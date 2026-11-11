# -*- coding: utf-8 -*-
"""咕嘎 × DSH 闭环 · 能力目录（契约第 3 节）。

为什么需要它
------------
咕嘎派单前要让执行器知道自己「到底有什么」：哪些插件真的加载了、有哪些工具、
每个工具吃哪些参数、模型走哪条 provider 路由。凭印象回答等于编造，所以本模块只做
一件事：**把 `dsh --profile <p> --dump-config` 打印出来的真实组合树解析成目录**。

解析口径（全部来自实测，不是猜）
--------------------------------
1. 组合树是**嵌套**的：顶层是 app 插件；`@deepseek-ai/dsh-agent-preset` 的
   `config.plugins` 才是某个会话实际加载的工具树；`cordis:group` 的 `config` 是
   子列表。默认 preset 取自 `agent-preset-registry.config.default`。
2. `disabled:` 有四种写法：缺省（启用）、`true`/`false`、`!!js <表达式>`。
   对 `process.platform ===|!== 'win32'|'darwin'|'linux'` 这类**本机可判定**的条件
   按真实平台求值；其余 `!!js` 表达式一律标 `conditional`，不当成已加载。
3. 工具名/参数来自该插件**真实安装的包源码**里的 `ctx.tools.register(defineTool({...}))`：
   `name` 取字面量，或 `config.toolName`，或 schema 默认值（`z.string().default("x")`）。
   解析不到就留空并记一条 warning —— **不编造**。
4. MCP 服务只认组合树里真实出现的 `mcpServers`/`mcp_servers` 段；没有就是空列表。
5. `mcp_bridge` 是桌面实例另挂的 MCP 桥（插件 dsh-as-mcp-server）：端口/工具名读
   `mcp_servers.json` 的 `dsh-bridge.allowed_tools`（已配置的权威列表），监听 pid 实测；
   与 DSH 主端口同 pid 才算「同一个实例的另一个口」。

约束：`--dump-config` 失败就 `ok=False` + `error`，绝不返回想象出来的能力。
结果缓存到 `runtime/dsh-binding/capabilities.json`（TTL 1 小时）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

DEFAULT_PROFILE = 'web'
# 桌面实例另挂的 MCP 桥（插件 dsh-as-mcp-server / id dsh-mcp-bridge）：
# 它与 Typert RPC 是同一个进程的另一个口，端口/工具清单见 mcp_servers.json。
MCP_BRIDGE_PORT = 51630
DEFAULT_DSH_PORT = 3080
DSH_HOME = Path.home() / '.dsh'
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MCP_SERVERS_FILE = PROJECT_ROOT / 'mcp_servers.json'
RUNTIME_DIR = PROJECT_ROOT / 'runtime' / 'dsh-binding'
CACHE_FILE = RUNTIME_DIR / 'capabilities.json'
CACHE_TTL_SECONDS = 3600
DUMP_TIMEOUT_SECONDS = 120

_STRING_DELIMS = ('"', "'", '`')


class JsExpression(str):
    """YAML 里的 `!!js <表达式>`：只作为条件占位，绝不当真值。"""


# --------------------------------------------------------------------------- 通用
def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def _platform() -> str:
    """与 Node 的 `process.platform` 对齐。"""
    if sys.platform.startswith('win'):
        return 'win32'
    if sys.platform == 'darwin':
        return 'darwin'
    return 'linux'


def _load_yaml(text: str):
    """载入带 `!!js` 自定义标签的 YAML。"""
    try:
        import yaml
    except Exception:  # pragma: no cover - 没有 PyYAML
        return None

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_constructor('tag:yaml.org,2002:js', lambda loader, node: JsExpression(node.value))
    try:
        return yaml.load(text, Loader=_Loader)
    except Exception:  # noqa: BLE001 - 组合树不合法时按「取不到」处理
        return None


def _walk_dicts(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_dicts(value)


# --------------------------------------------------------------- 组合树 → 插件节点
def _resolve_enabled(entry: dict) -> tuple:
    """返回 (enabled, reason)。enabled 为 None 表示「条件未判定」。"""
    if 'disabled' not in entry:
        return True, 'enabled'
    value = entry.get('disabled')
    if isinstance(value, JsExpression):
        return _eval_condition(str(value))
    return (not bool(value)), ('enabled' if not bool(value) else 'disabled')


def _eval_condition(expression: str) -> tuple:
    """只对「本机可判定」的平台比较求值，其余标条件未定。"""
    text = str(expression or '').strip()
    match = re.match(r"^process\.platform\s*(===|!==|==|!=)\s*['\"]([a-z0-9]+)['\"]$", text)
    if match:
        operator, wanted = match.group(1), match.group(2)
        equal = _platform() == wanted
        # `disabled: !!js <表达式>` —— 表达式为真表示「禁用」，所以 enabled 取反。
        if operator in ('===', '=='):
            return (not equal), 'platform:%s' % wanted
        return equal, 'platform:!%s' % wanted
    return None, 'conditional: ' + text[:60]


def _classify(name: str, entry_id: str) -> str:
    """给插件分个粗类，便于摘要里分组（纯命名规则，不猜能力）。"""
    low_name = str(name or '').lower()
    low_id = str(entry_id or '').lower()
    if 'mcp' in low_name or 'mcp' in low_id:
        return 'mcp'
    if 'client-ui' in low_name or low_id.startswith('ui-'):
        return 'ui'
    # 只认真正的工具包 `dsh-tool-*`；`dsh-compaction-tool-result-pruner` 这类不是模型工具。
    if 'dsh-tool-' in low_name:
        return 'tool'
    if 'llm' in low_name or low_id.startswith('llm'):
        return 'llm'
    if 'session' in low_name or low_id.startswith('session'):
        return 'session'
    if 'skill' in low_name:
        return 'skill'
    if 'agent-preset' in low_name:
        return 'preset'
    return 'plugin'


def _flatten(tree: list) -> list:
    """把嵌套组合树拍平成节点列表，保留 layer/path 以便溯源。"""
    nodes = []

    def walk(entries, layer, path):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            entry_id = str(entry.get('id') or '')
            name = str(entry.get('name') or '')
            node_path = (path + '/' + entry_id) if path else entry_id
            config = entry.get('config')
            config_dict = config if isinstance(config, dict) else {}
            enabled, reason = _resolve_enabled(entry)
            nodes.append(dict(
                id=entry_id, name=name, kind=_classify(name, entry_id),
                layer=layer, path=node_path, enabled=enabled, reason=reason,
                config=config_dict,
            ))
            children = None
            if isinstance(config, list):
                children = config
            elif isinstance(config, dict) and isinstance(config.get('plugins'), list):
                children = config['plugins']
            if children:
                walk(children, layer, node_path)

    for entry in tree:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('name') or '')
        config = entry.get('config')
        if name.endswith('dsh-agent-preset') and isinstance(config, dict):
            preset_id = str(config.get('id') or entry.get('id') or '')
            plugins = config.get('plugins') or []
            walk(plugins, 'preset:' + preset_id, 'preset:' + preset_id)
        else:
            walk([entry], 'app', '')
    return nodes


def _select_preset(tree: list) -> tuple:
    """返回 (默认 preset id, 全部 preset 摘要)。"""
    default_id = ''
    presets = []
    for entry in tree:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get('name') or '')
        config = entry.get('config')
        if not isinstance(config, dict):
            continue
        if name.endswith('dsh-agent-preset-registry'):
            default_id = str(config.get('default') or default_id)
        elif name.endswith('dsh-agent-preset'):
            presets.append(dict(id=str(config.get('id') or entry.get('id') or ''),
                                order=config.get('order'),
                                plugins=len(config.get('plugins') or [])))
    presets.sort(key=lambda item: (item.get('order') is None, item.get('order') or 0))
    if not default_id and presets:
        default_id = presets[0]['id']
    return default_id, presets


def _effective_nodes(nodes: list, preset_id: str) -> list:
    """某个会话真正会加载的节点：app 层 + 默认 preset 层，且 enabled 为真。"""
    wanted = {'app', 'preset:' + str(preset_id or '')}
    return [node for node in nodes if node.get('layer') in wanted and node.get('enabled') is True]


def _public_plugins(nodes: list) -> list:
    """对外插件列表（按 id 去重，保留首个真实来源）。"""
    seen = set()
    out = []
    for node in nodes:
        key = (node.get('id'), node.get('name'))
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(id=node.get('id'), name=node.get('name'),
                        kind=node.get('kind'), layer=node.get('layer'),
                        path=node.get('path')))
    return out


# ------------------------------------------------------------------- providers/MCP
def _collect_providers(tree: list) -> list:
    """组合树里 `llm-pi-ai.config.providers` 的真实路由与模型。"""
    providers = {}
    for item in _walk_dicts(tree):
        mapping = item.get('providers')
        if not isinstance(mapping, dict):
            continue
        for route, config in mapping.items():
            if not isinstance(config, dict):
                continue
            if not ({'baseURL', 'baseUrl', 'apiKeyEnv', 'models'} & set(config)):
                continue
            models = []
            for model in config.get('models') or []:
                if isinstance(model, dict) and model.get('id'):
                    models.append(str(model.get('id')))
            providers[str(route)] = dict(
                route=str(route),
                base_url=str(config.get('baseURL') or config.get('baseUrl') or ''),
                api_key_env=str(config.get('apiKeyEnv') or ''),
                api=str(config.get('api') or ''),
                models=models,
            )
    return [providers[key] for key in sorted(providers)]


def _collect_mcp(tree: list) -> list:
    """组合树里真实出现的 MCP 服务段；没有就是空列表。"""
    servers = {}
    for item in _walk_dicts(tree):
        for key in ('mcpServers', 'mcp_servers'):
            mapping = item.get(key)
            if not isinstance(mapping, dict):
                continue
            for name, config in mapping.items():
                if not isinstance(config, dict):
                    continue
                transport = str(config.get('transport') or config.get('type') or '')
                if not transport:
                    if config.get('command'):
                        transport = 'stdio'
                    elif config.get('url'):
                        transport = 'http'
                hint = config.get('tools') or config.get('allowedTools') or config.get('toolsHint') or []
                servers[str(name)] = dict(name=str(name), transport=transport,
                                          tools_hint=list(hint) if isinstance(hint, list) else [],
                                          source=key)
    return [servers[key] for key in sorted(servers)]


def _listen_pid(port: int) -> int:
    """端口监听 pid；复用 dsh_binding 的实现，拿不到就返回 0（不抛异常）。"""
    try:
        from .dsh_binding import _listen_pid as binding_listen_pid
        pid, _error = binding_listen_pid(int(port))
        return int(pid or 0)
    except Exception:  # noqa: BLE001 - 目录模块不因进程探测失败而崩
        return 0


def _mcp_bridge() -> dict:
    """MCP 桥的真实状态：配置（mcp_servers.json）+ 端口监听 pid + 是否与 DSH 同进程。

    工具名只取 `dsh-bridge.allowed_tools`（已配置的权威列表）；读不到就留空。
    """
    state = dict(present=False, configured=False, port=MCP_BRIDGE_PORT, pid=0,
                 same_process_as_dsh=None, tools=[], url='', error='')
    try:
        if not MCP_SERVERS_FILE.is_file():
            state['error'] = 'mcp_servers.json 不存在'
            return state
        data = json.loads(MCP_SERVERS_FILE.read_text(encoding='utf-8'))
        servers = data.get('mcpServers') if isinstance(data, dict) else None
        bridge = servers.get('dsh-bridge') if isinstance(servers, dict) else None
        if not isinstance(bridge, dict):
            state['error'] = 'mcp_servers.json 里没有 dsh-bridge 配置'
            return state

        state['configured'] = True
        url = str(bridge.get('url') or '')
        state['url'] = url
        match = re.search(r':(\d{2,5})(?:/|$)', url)
        port = int(match.group(1)) if match else MCP_BRIDGE_PORT
        state['port'] = port

        allowed = bridge.get('allowed_tools')
        if isinstance(allowed, list):
            state['tools'] = [str(tool) for tool in allowed if str(tool).strip()]

        bridge_pid = _listen_pid(port)
        dsh_pid = _listen_pid(DEFAULT_DSH_PORT)
        state['pid'] = bridge_pid
        state['present'] = bool(bridge_pid)
        if bridge_pid and dsh_pid:
            state['same_process_as_dsh'] = bridge_pid == dsh_pid
        if not bridge_pid:
            state['error'] = '端口 %d 没有监听进程（MCP 桥未挂载）' % port
        return state
    except Exception as exc:  # noqa: BLE001
        state['error'] = str(exc)[:160]
        return state


# ---------------------------------------------------------------- 包源码 → 工具定义
def _skip_quoted(text: str, index: int) -> int:
    quote = text[index]
    i = index + 1
    length = len(text)
    while i < length:
        if text[i] == '\\':
            i += 2
            continue
        if text[i] == quote:
            return i + 1
        i += 1
    return length


def _match_brace(text: str, index: int) -> tuple:
    """index 指向 '{'；返回 (内部文本, 结束下标)。"""
    depth = 0
    i = index
    length = len(text)
    while i < length:
        char = text[i]
        if char in _STRING_DELIMS:
            i = _skip_quoted(text, i)
            continue
        if char == '/' and i + 1 < length and text[i + 1] == '/':
            newline = text.find('\n', i)
            i = length if newline < 0 else newline + 1
            continue
        if char == '/' and i + 1 < length and text[i + 1] == '*':
            closing = text.find('*/', i + 2)
            i = length if closing < 0 else closing + 2
            continue
        if char == '{':
            depth += 1
        elif char == '}':
            depth -= 1
            if depth == 0:
                return text[index + 1:i], i
        i += 1
    return text[index + 1:], length


def _top_level_keys(text: str):
    """遍历对象体里 depth==0 的 key，产出 (key, value_start)。"""
    depth = 0
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char in _STRING_DELIMS:
            i = _skip_quoted(text, i)
            continue
        if char == '/' and i + 1 < length and text[i + 1] == '/':
            newline = text.find('\n', i)
            i = length if newline < 0 else newline + 1
            continue
        if char == '/' and i + 1 < length and text[i + 1] == '*':
            closing = text.find('*/', i + 2)
            i = length if closing < 0 else closing + 2
            continue
        if char in '{[(':
            depth += 1
            i += 1
            continue
        if char in '}])':
            depth -= 1
            i += 1
            continue
        if depth == 0 and (char.isalpha() or char in '_$'):
            end = i
            while end < length and (text[end].isalnum() or text[end] in '_$'):
                end += 1
            key = text[i:end]
            cursor = end
            while cursor < length and text[cursor] in ' \t\r\n':
                cursor += 1
            if cursor < length and text[cursor] == ':':
                yield key, cursor + 1
            i = end
            continue
        i += 1


def _read_value(text: str, start: int) -> tuple:
    """读取一个值：('literal'|'object'|'ident'|'bool'|'other', value, end)。"""
    i = start
    length = len(text)
    while i < length and text[i] in ' \t\r\n':
        i += 1
    if i >= length:
        return 'other', '', length
    char = text[i]
    if char in _STRING_DELIMS:
        end = _skip_quoted(text, i)
        raw = text[i + 1:end - 1] if end > i + 1 else ''
        return 'literal', raw, end
    if char == '{':
        inner, end = _match_brace(text, i)
        return 'object', inner, end
    end = i
    while end < length and (text[end].isalnum() or text[end] in '_$.'):
        end += 1
    token = text[i:end]
    if token in ('true', 'false'):
        return 'bool', token == 'true', end
    if token:
        return 'ident', token, end
    return 'other', '', i + 1


def _const_literal(source: str, identifier: str) -> str:
    """解析 `const NAME = "字面量"` 这类同文件常量。"""
    pattern = re.compile(
        r'(?:const|let|var)\s+' + re.escape(identifier) + r'\s*=\s*(["\'])((?:\\.|(?!\1).)*)\1',
        re.S)
    match = pattern.search(source)
    if not match:
        return ''
    value = match.group(2)
    if '${' in value:
        return ''
    return value.replace('\\n', '\n').replace("\\'", "'").replace('\\"', '"')


def _schema_default(source: str, key: str) -> str:
    """解析 `toolName: z.string().default("workflow")` 这类配置默认值。"""
    pattern = re.compile(
        re.escape(key) + r"\s*:\s*z\s*\.\s*string\s*\(\s*\)\s*\.\s*default\s*\(\s*([\"'])([^\"']+)\1\s*\)")
    match = pattern.search(source)
    return match.group(2) if match else ''


def _resolve_text(field: tuple, source: str, config: dict, key: str) -> str:
    if not field:
        return ''
    kind, value = field
    if kind == 'literal':
        text = str(value)
        return '' if '${' in text else text
    if kind == 'ident':
        identifier = str(value)
        if identifier == 'toolName':
            return str(config.get('toolName') or _schema_default(source, 'toolName') or '')
        return _const_literal(source, identifier)
    return ''


def _parse_parameters(inner: str) -> dict:
    """把 `parameters: { key: { type, required, description } }` 解析成 schema。"""
    schema = {}
    for key, value_start in _top_level_keys(inner):
        kind, value, _end = _read_value(inner, value_start)
        entry = {'type': '', 'required': False, 'description': ''}
        if kind == 'object':
            details = {}
            for sub_key, sub_start in _top_level_keys(value):
                sub_kind, sub_value, _sub_end = _read_value(value, sub_start)
                if sub_kind in ('literal', 'ident'):
                    details[sub_key] = sub_value
                elif sub_kind == 'bool':
                    details[sub_key] = sub_value
            entry['type'] = str(details.get('type') or '')
            entry['required'] = details.get('required') is True
            entry['description'] = str(details.get('description') or '')
        schema[key] = entry
    return schema


def _extract_define_tools(source: str, config: dict) -> list:
    """从插件源码里抓 `ctx.tools.register(defineTool({...}))` 的真实工具定义。"""
    tools = []
    cursor = 0
    while True:
        index = source.find('defineTool(', cursor)
        if index < 0:
            break
        cursor = index + len('defineTool(')
        brace = source.find('{', cursor)
        if brace < 0:
            break
        inner, end = _match_brace(source, brace)
        cursor = max(cursor, end)

        fields = {}
        for key, value_start in _top_level_keys(inner):
            if key in fields:
                continue
            kind, value, _value_end = _read_value(inner, value_start)
            fields[key] = (kind, value)

        name = _resolve_text(fields.get('name'), source, config, 'name')
        if not name or not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name):
            continue
        description = _resolve_text(fields.get('description'), source, config, 'description')
        params = {}
        parameters = fields.get('parameters')
        if parameters and parameters[0] == 'object':
            params = _parse_parameters(str(parameters[1]))
        tools.append(dict(name=name, description=description, params=params))
    return tools


def _package_roots(profile: str) -> list:
    return [
        DSH_HOME / 'profiles' / profile / 'node_modules',
        DSH_HOME / 'profiles' / 'node_modules',
        Path.home() / '.local' / 'bin' / 'node_modules' / '@deepseek-ai' / 'dsh' / 'node_modules',
        Path.home() / '.local' / 'bin' / 'node_modules',
    ]


def _read_package_json(package_dir: Path) -> dict:
    try:
        data = json.loads((package_dir / 'package.json').read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _exports_target(package_dir: Path, data: dict, sub_path: str) -> Path | None:
    exports = data.get('exports')
    if not isinstance(exports, dict):
        return None
    entry = exports.get(sub_path)
    target = None
    if isinstance(entry, str):
        target = entry
    elif isinstance(entry, dict):
        target = entry.get('default') or entry.get('require') or entry.get('import')
    if not isinstance(target, str):
        return None
    candidate = (package_dir / target).resolve()
    return candidate if candidate.is_file() else None


def _resolve_entry_file(spec: str, profile: str) -> tuple:
    """把插件 spec 解析成真实 JS 入口文件。返回 (path|None, error)。"""
    spec = str(spec or '').strip()
    if not spec:
        return None, '插件 spec 为空'
    if spec.startswith('file://'):
        parsed = urllib.parse.urlparse(spec)
        raw = urllib.parse.unquote(parsed.path or '')
        if re.match(r'^/[A-Za-z]:', raw):  # Windows: /C:/...
            raw = raw[1:]
        candidate = Path(raw)
        return (candidate, '') if candidate.is_file() else (None, 'file:// 指向的文件不存在')

    parts = spec.split('/')
    if spec.startswith('@') and len(parts) >= 2:
        package_name, sub_path = '/'.join(parts[:2]), '/'.join(parts[2:])
    else:
        package_name, sub_path = parts[0], '/'.join(parts[1:])

    for root in _package_roots(profile):
        package_dir = root / package_name
        if not package_dir.is_dir():
            continue
        data = _read_package_json(package_dir)
        if sub_path:
            target = _exports_target(package_dir, data, './' + sub_path)
            if target:
                return target, ''
            for candidate in (package_dir / 'lib' / 'types' / (sub_path + '.js'),
                              package_dir / 'lib' / (sub_path + '.js'),
                              package_dir / (sub_path + '.js')):
                if candidate.is_file():
                    return candidate, ''
            return None, '子路径 %s 没有可读入口' % sub_path
        main = data.get('main')
        if isinstance(main, str):
            candidate = (package_dir / main).resolve()
            if candidate.is_file():
                return candidate, ''
        target = _exports_target(package_dir, data, '.')
        if target:
            return target, ''
        for candidate in (package_dir / 'lib' / 'index.js', package_dir / 'index.js'):
            if candidate.is_file():
                return candidate, ''
        return None, '包目录存在但没有 lib/index.js'
    return None, '在已知 node_modules 里找不到这个包'


def _extract_tools(nodes: list, profile: str) -> tuple:
    """从生效插件里提取真实工具定义。返回 (tools, warnings)。

    每条工具都带 `layer`（app / preset:<id>）与 `source`，便于溯源；
    运行时是否真正暴露给会话由 DSH 的模式与预设决定，这里不做猜测。
    """
    tools = []
    warnings = []
    seen = set()
    for node in nodes:
        # 不只扫 `dsh-tool-*`：第三方插件（see-now / ego-browser / 记忆桥）同样用
        # `ctx.tools.register(defineTool({...}))` 注册工具，扫它们才能不遗漏。
        spec = str(node.get('name') or '')
        entry_id = str(node.get('id') or '')
        if not spec:
            continue
        entry_file, error = _resolve_entry_file(spec, profile)
        if not entry_file:
            if entry_id.startswith('tool-'):
                warnings.append('%s（%s）：%s' % (entry_id, spec, error))
            continue
        try:
            source = entry_file.read_text(encoding='utf-8', errors='ignore')
        except OSError as exc:
            warnings.append('%s：读取源码失败 %s' % (entry_id, str(exc)[:120]))
            continue
        definitions = _extract_define_tools(source, node.get('config') or {})
        if not definitions:
            # 只在「确实是工具包」时才告警，避免把策略类插件算成解析失败。
            if entry_id.startswith('tool-'):
                warnings.append('%s（%s）：源码里没有可静态解析的 defineTool 注册' % (entry_id, spec))
            continue
        for definition in definitions:
            name = definition['name']
            if name in seen:
                continue
            seen.add(name)
            tools.append(dict(
                name=name,
                plugin=str(node.get('id') or ''),
                plugin_name=spec,
                layer=str(node.get('layer') or ''),
                description=definition['description'],
                params_schema=definition['params'],
                source=str(entry_file),
            ))
    return tools, warnings


# --------------------------------------------------------------------------- 抓取
def _dsh_argv(profile: str) -> list:
    """优先直接调 node + bin.js，绕开 .cmd/.ps1 的引号与执行策略问题。"""
    bin_js = Path.home() / '.local' / 'bin' / 'node_modules' / '@deepseek-ai' / 'dsh' / 'lib' / 'bin.js'
    node = shutil.which('node')
    if node and bin_js.is_file():
        return [node, str(bin_js), '--profile', profile, '--dump-config']
    exe = shutil.which('dsh') or 'dsh'
    lowered = str(exe).lower()
    if lowered.endswith('.ps1'):
        return ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', exe,
                '--profile', profile, '--dump-config']
    if lowered.endswith(('.cmd', '.bat')):
        return ['cmd', '/c', exe, '--profile', profile, '--dump-config']
    return [exe, '--profile', profile, '--dump-config']


def _run_dump(profile: str) -> tuple:
    """执行 `dsh --profile <p> --dump-config`，返回 (stdout, error)。"""
    argv = _dsh_argv(profile)
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=DUMP_TIMEOUT_SECONDS,
                              text=True, encoding='utf-8', errors='replace')
    except Exception as exc:  # noqa: BLE001
        return '', '执行 `dsh --profile %s --dump-config` 失败：%s' % (profile, str(exc)[:200])
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or '').strip()[:300]
        return '', '`dsh --dump-config` 退出码 %s：%s' % (proc.returncode, detail)
    text = proc.stdout or ''
    if not text.strip():
        return '', '`dsh --dump-config` 没有输出'
    return text, ''


def _fingerprint(payload: dict) -> str:
    material = {
        'profile': payload.get('profile'),
        'plugins': sorted('%s|%s' % (p.get('id'), p.get('name')) for p in payload.get('plugins') or []),
        'tools': sorted(t.get('name') for t in payload.get('tools') or []),
        'providers': sorted('%s|%s' % (p.get('route'), ','.join(p.get('models') or []))
                            for p in payload.get('providers') or []),
        'mcp': sorted(m.get('name') for m in payload.get('mcp_servers') or []),
    }
    return hashlib.sha1(json.dumps(material, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


# ------------------------------------------------------------------- 对外契约 API
def build(profile: str = DEFAULT_PROFILE, refresh: bool = True) -> dict:
    """从 `dsh --profile <p> --dump-config` 解析真实组合后的插件树。"""
    profile = str(profile or DEFAULT_PROFILE)
    result = dict(ok=False, profile=profile, generated_at=_now(), fingerprint='',
                  preset='', presets=[], plugins=[], mcp_servers=[], tools=[],
                  providers=[], warnings=[], stats={}, mcp_bridge={}, error='',
                  caveat='工具来自各插件真实源码里的 defineTool 注册；'
                         'app 层插件注册的工具是否暴露给会话，取决于 DSH 的运行时模式与预设。',
                  source='dsh --profile %s --dump-config' % profile)
    try:
        text, error = _run_dump(profile)
        if error:
            result['error'] = error
            return result
        tree = _load_yaml(text)
        if not isinstance(tree, list):
            result['error'] = '`--dump-config` 输出不是 YAML 列表，无法解析'
            return result

        nodes = _flatten(tree)
        preset_id, presets = _select_preset(tree)
        effective = _effective_nodes(nodes, preset_id)
        tools, warnings = _extract_tools(effective, profile)

        result.update(
            preset=preset_id,
            presets=presets,
            plugins=_public_plugins(effective),
            mcp_servers=_collect_mcp(tree),
            tools=tools,
            providers=_collect_providers(tree),
            warnings=warnings,
            mcp_bridge=_mcp_bridge(),
            providers_note='只列组合配置里用 `providers:` 段显式声明的路由；'
                           '由内置 LLM 插件直接注册、没有配置段的路由不在其中（不猜）。',
        )
        conditional = [node for node in nodes
                       if node.get('enabled') is None
                       and node.get('layer') in ('app', 'preset:' + preset_id)]
        result['stats'] = dict(
            nodes=len(nodes),
            effective_nodes=len(effective),
            plugins=len(result['plugins']),
            tools=len(result['tools']),
            mcp_servers=len(result['mcp_servers']),
            providers=len(result['providers']),
            conditional=len(conditional),
            conditional_plugins=[node.get('path') for node in conditional][:20],
        )
        result['fingerprint'] = _fingerprint(result)
        result['ok'] = True

        cache_payload = dict(result)
        cache_payload['_cached_at'] = time.time()
        try:
            _atomic_write_json(CACHE_FILE, cache_payload)
        except OSError as exc:
            result['warnings'].append('缓存写入失败：%s' % str(exc)[:120])
        return result
    except Exception as exc:  # noqa: BLE001 - 任何异常都转成 ok=False
        result['error'] = '构建能力目录失败：%s' % str(exc)[:240]
        return result


def get(refresh: bool = False) -> dict:
    """读缓存；缓存不存在或过期(>1h)则重建。"""
    try:
        cached = None
        if CACHE_FILE.is_file():
            try:
                cached = json.loads(CACHE_FILE.read_text(encoding='utf-8'))
            except Exception:  # noqa: BLE001
                cached = None
        if not refresh and isinstance(cached, dict) and cached.get('ok'):
            age = max(0.0, time.time() - float(cached.get('_cached_at') or 0))
            if age <= CACHE_TTL_SECONDS:
                payload = dict(cached)
                payload['cached'] = True
                payload['age_seconds'] = round(age, 1)
                return payload

        built = build(refresh=True)
        if not built.get('ok') and isinstance(cached, dict) and cached.get('ok'):
            built['last_good'] = dict(profile=cached.get('profile'),
                                      generated_at=cached.get('generated_at'),
                                      stats=cached.get('stats'),
                                      fingerprint=cached.get('fingerprint'))
        return built
    except Exception as exc:  # noqa: BLE001
        return dict(ok=False, error='读取能力目录失败：%s' % str(exc)[:240],
                    profile=DEFAULT_PROFILE, generated_at=_now(), plugins=[], tools=[],
                    mcp_servers=[], providers=[], warnings=[],
                    mcp_bridge={}, preset='', presets=[], stats={}, fingerprint='')


def _find_tool(catalog: dict, tool_name: str) -> dict | None:
    wanted = str(tool_name or '').strip()
    if not wanted:
        return None
    for tool in catalog.get('tools') or []:
        if tool.get('name') == wanted:
            return tool
    return None


def _example_for(tool: dict) -> str:
    """按必填参数机械拼一个调用示例（只是形状示例，不是能力承诺）。"""
    schema = tool.get('params_schema') or {}
    sample = {}
    for key, meta in schema.items():
        if isinstance(meta, dict) and meta.get('required'):
            sample[key] = '<%s>' % (meta.get('type') or 'value')
    return json.dumps(sample, ensure_ascii=False)


def describe(tool_name: str) -> dict:
    """单个能力：{found, name, plugin, description, params_schema, example}"""
    empty = dict(found=False, name=str(tool_name or ''), plugin='', description='',
                 params_schema={}, example='', error='')
    try:
        if not str(tool_name or '').strip():
            empty['error'] = '需要工具名'
            return empty
        catalog = get()
        if not catalog.get('ok'):
            empty['error'] = catalog.get('error') or '能力目录不可用'
            return empty
        tool = _find_tool(catalog, tool_name)
        if not tool:
            empty['error'] = '能力目录里没有这个工具（不编造）'
            return empty
        return dict(found=True, name=tool.get('name'), plugin=tool.get('plugin'),
                    description=tool.get('description'),
                    params_schema=tool.get('params_schema') or {},
                    example=_example_for(tool), error='')
    except Exception as exc:  # noqa: BLE001
        empty['error'] = str(exc)[:200]
        return empty


def summary_text(limit: int = 40) -> str:
    """给咕嘎/模型看的中文能力摘要（一行一个能力，含关键参数名）。"""
    try:
        catalog = get()
    except Exception as exc:  # noqa: BLE001
        return '能力目录不可用：%s' % str(exc)[:200]
    if not catalog.get('ok'):
        return '能力目录不可用：%s' % (catalog.get('error') or '未知错误')

    try:
        limit = max(1, int(limit or 40))
    except (TypeError, ValueError):
        limit = 40

    tools = catalog.get('tools') or []
    providers = catalog.get('providers') or []
    servers = catalog.get('mcp_servers') or []
    lines = ['DSH 能力目录（profile=%s，生成于 %s）'
             % (catalog.get('profile'), catalog.get('generated_at'))]
    lines.append('插件 %d 个｜工具 %d 个｜MCP 服务 %d 个｜模型路由 %d 条'
                 % (len(catalog.get('plugins') or []), len(tools), len(servers), len(providers)))

    for tool in tools[:limit]:
        params = tool.get('params_schema') or {}
        names = []
        for key, meta in params.items():
            names.append(key + ('(必填)' if isinstance(meta, dict) and meta.get('required') else ''))
        line = '- %s（插件 %s）：%s' % (tool.get('name'), tool.get('plugin'),
                                       tool.get('description') or '无描述')
        if names:
            line += '；参数：' + '、'.join(names[:8])
            if len(names) > 8:
                line += ' 等 %d 个' % len(names)
        lines.append(line)
    if len(tools) > limit:
        lines.append('……另有 %d 个工具未列出' % (len(tools) - limit))

    for provider in providers:
        models = provider.get('models') or []
        lines.append('- 模型路由 %s：%s；密钥环境变量 %s；模型 %d 个（%s）'
                     % (provider.get('route'), provider.get('base_url') or '未配置地址',
                        provider.get('api_key_env') or '未声明',
                        len(models), '、'.join(models[:6]) or '未列出'))
    for server in servers:
        lines.append('- MCP 服务 %s：transport=%s' % (server.get('name'), server.get('transport') or '未知'))
    bridge = catalog.get('mcp_bridge') or {}
    if bridge.get('present'):
        same = bridge.get('same_process_as_dsh')
        if same is True:
            verdict = '与 DSH 桌面实例同一进程（pid %s）' % bridge.get('pid')
        elif same is False:
            verdict = '**与 DSH 桌面实例不是同一进程**（pid %s）' % bridge.get('pid')
        else:
            verdict = 'pid %s（未比对到 DSH 桌面实例）' % bridge.get('pid')
        lines.append('- MCP 桥 dsh-bridge：%s，%s；工具 %d 个（%s）'
                     % (bridge.get('url') or ('端口 %s' % bridge.get('port')), verdict,
                        len(bridge.get('tools') or []),
                        '、'.join((bridge.get('tools') or [])[:8]) or '未列出'))
    elif bridge.get('configured'):
        lines.append('- MCP 桥 dsh-bridge：已配置但当前未监听（%s）'
                     % (bridge.get('error') or '端口无响应'))
    if catalog.get('warnings'):
        lines.append('（有 %d 条解析告警，见 capabilities.json 的 warnings）'
                     % len(catalog.get('warnings') or []))
    return '\n'.join(lines)


# --------------------------------------------------------------------------- 自检
def _self_check() -> int:
    built = build(refresh=True)
    print('== build() ==')
    print(json.dumps({key: built.get(key) for key in
                      ('ok', 'profile', 'generated_at', 'fingerprint', 'preset', 'presets',
                       'stats', 'error')}, ensure_ascii=False, indent=2))
    print('plugins 数量 =', len(built.get('plugins') or []))
    print('mcp_servers =', json.dumps(built.get('mcp_servers') or [], ensure_ascii=False))
    print('tools 数量 =', len(built.get('tools') or []))
    print('provider routes =', [p.get('route') for p in built.get('providers') or []])
    print('warnings =', json.dumps(built.get('warnings') or [], ensure_ascii=False)[:800])

    print('\n== get(refresh=False) ==')
    cached = get(refresh=False)
    print(json.dumps({key: cached.get(key) for key in ('ok', 'cached', 'age_seconds', 'fingerprint')},
                     ensure_ascii=False))

    print('\n== describe("read") ==')
    print(json.dumps(describe('read'), ensure_ascii=False, indent=2))

    print('\n== summary_text(limit=40) 前 10 行 ==')
    text = summary_text(40)
    for line in text.splitlines()[:10]:
        print(line)
    return 0 if built.get('ok') else 1


if __name__ == '__main__':
    raise SystemExit(_self_check())
