"""Opt-in local stdio MCP host (2025-11-25 tools subset), bounded process lifetime."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')


class StdioMCP:
    def __init__(self, config, cancel=None):
        command = config.get('command')
        args = config.get('args', [])
        if not isinstance(command, str) or not command or not isinstance(args, list):
            raise ValueError('MCP 需要 command 字符串和 args 数组')
        env = dict(os.environ, **config.get('env', {}))
        self.cancel = cancel or threading.Event()
        self.proc = subprocess.Popen([command] + args, cwd=config.get('cwd') or str(ROOT),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding='utf-8', text=True, env=env, bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        self.lines = queue.Queue(maxsize=64)
        self.sequence = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            while self.proc.stdout:
                line = self.proc.stdout.readline(2_000_001)
                if not line:
                    break
                if len(line) > 2_000_000:
                    break
                self.lines.put(line, timeout=2)
        except (OSError, ValueError, queue.Full):
            pass

    def send(self, value):
        self.proc.stdin.write(json.dumps(value, ensure_ascii=False) + '\n')
        self.proc.stdin.flush()

    def request(self, method, params=None):
        self.sequence += 1
        ident = self.sequence
        self.send(dict(jsonrpc='2.0', id=ident, method=method, params=params or {}))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not self.cancel.is_set():
            try:
                line = self.lines.get(timeout=.1)
            except queue.Empty:
                if self.proc.poll() is not None:
                    raise RuntimeError('MCP 软件进程已退出')
                continue
            message = json.loads(line)
            if message.get('method'):
                if 'id' in message:
                    if message['method'] == 'ping':
                        self.send(dict(jsonrpc='2.0', id=message['id'], result={}))
                    else:
                        self.send(dict(jsonrpc='2.0', id=message['id'], error=dict(code=-32601, message='Unsupported client capability')))
                continue
            if message.get('id') == ident:
                if message.get('error'):
                    raise RuntimeError('MCP 请求失败：' + str(message['error'].get('code')))
                return message.get('result', {})
        raise RuntimeError('MCP 操作已取消或超过 20 秒')

    def initialize(self):
        result = self.request('initialize', dict(protocolVersion=VERSIONS[0], capabilities={},
            clientInfo=dict(name='guga-desktop', version='2.0.0')))
        if result.get('protocolVersion') not in VERSIONS:
            raise RuntimeError('暂不支持该 MCP 协议版本，请使用兼容 2025-11-25 的服务器')
        if 'tools' not in result.get('capabilities', {}):
            raise RuntimeError('此 MCP 软件未提供 tools 能力')
        self.send(dict(jsonrpc='2.0', method='notifications/initialized'))

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=.5)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.proc.terminate()
            try:
                self.proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=1)
        if self.proc.stdout:
            self.proc.stdout.close()


class StreamableHTTPMCP:
    """按 MCP Streamable HTTP 传输访问一个 http(s) 端点（不引入任何第三方 SDK）。

    只实现本项目需要的最小面：initialize / notifications/initialized /
    tools/list / tools/call。响应既可能是 application/json，也可能是
    text/event-stream（SSE），两种都解析；会话号通过 Mcp-Session-Id 回带。
    """

    def __init__(self, config, cancel=None, timeout=20):
        url = str(config.get('url') or '').strip()
        if not url.startswith(('http://', 'https://')):
            raise ValueError('MCP http 传输需要一个 http:// 或 https:// 的 url')
        self.url = url
        self.headers = dict(config.get('headers') or {})
        self.timeout = float(config.get('timeout') or timeout)
        self.cancel = cancel or threading.Event()
        self.session_id = ''
        self.protocol = ''
        self.sequence = 0

    # ---- 传输 -------------------------------------------------------------
    def _headers(self):
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream',
                   'User-Agent': 'GugaDesktopPet/2.0'}
        headers.update({str(k): str(v) for k, v in self.headers.items()})
        if self.session_id:
            headers['Mcp-Session-Id'] = self.session_id
        return headers

    def _post(self, payload, expect_reply=True):
        import requests
        if self.cancel.is_set():
            raise RuntimeError('MCP 操作已取消')
        deadline = time.monotonic() + self.timeout
        response = requests.post(self.url, json=payload, headers=self._headers(),
                                 stream=True, timeout=(5, self.timeout))
        try:
            session = response.headers.get('Mcp-Session-Id') or response.headers.get('mcp-session-id')
            if session:
                self.session_id = session
            if response.status_code in (401, 403):
                raise RuntimeError('MCP 端点拒绝访问（HTTP %s），请检查 token' % response.status_code)
            if not expect_reply:
                # notifications/initialized 之类只要求受理：202/204 都算成功。
                if response.status_code >= 400:
                    raise RuntimeError('MCP 端点返回 HTTP %s' % response.status_code)
                return {}
            if response.status_code != 200:
                raise RuntimeError('MCP 端点返回 HTTP %s' % response.status_code)
            ctype = (response.headers.get('Content-Type') or '').lower()
            if 'text/event-stream' in ctype:
                return self._read_sse(response, payload.get('id'), deadline)
            # 显式按 UTF-8 解码：服务端 Content-Type 常不带 charset，交给 requests 猜会乱码。
            body = response.content or b''
            try:
                return json.loads(body.decode('utf-8', 'replace') or '{}')
            except ValueError:
                return {}
        finally:
            response.close()

    def _read_sse(self, response, ident, deadline):
        """按字节流读 SSE，只按 \\n 切行。

        不能用 requests 的 iter_lines(decode_unicode=True)：它会先按 latin-1 解码，
        再用 str.splitlines() 切行；中文 UTF-8 里出现的 0x85/0x0b 等字节会被当成换行，
        把一行 JSON 拦腰截断（真机上 dsh-as-mcp-server 就是这么失败的）。
        """
        buffer = b''
        while True:
            if self.cancel.is_set():
                raise RuntimeError('MCP 操作已取消')
            if time.monotonic() > deadline:
                break
            chunk = response.raw.read(2048, decode_content=True) if response.raw else b''
            if not chunk:
                break
            buffer += chunk
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                message = self._sse_line(line, ident)
                if message is not None:
                    return message
        message = self._sse_line(buffer, ident)
        if message is not None:
            return message
        raise RuntimeError('MCP 端点的 SSE 流里没有匹配的响应')

    @staticmethod
    def _sse_line(line, ident):
        if isinstance(line, (bytes, bytearray)):
            text = bytes(line).decode('utf-8', 'replace').strip()
        else:
            text = str(line or '').strip()
        if not text or not text.startswith('data:'):
            return None
        data = text[5:].strip()
        if not data or data == '[DONE]':
            return None
        try:
            message = json.loads(data)
        except ValueError:
            return None
        if isinstance(message, dict) and message.get('id') == ident:
            return message
        return None

    def request(self, method, params=None):
        self.sequence += 1
        ident = self.sequence
        message = self._post(dict(jsonrpc='2.0', id=ident, method=method, params=params or {}))
        if message.get('error'):
            detail = message['error']
            if isinstance(detail, dict):
                raise RuntimeError('MCP 请求失败：%s %s' % (detail.get('code'), str(detail.get('message') or '')[:160]))
            raise RuntimeError('MCP 请求失败：' + str(detail)[:160])
        return message.get('result', {})

    def initialize(self):
        result = self.request('initialize', dict(protocolVersion=VERSIONS[0], capabilities={},
            clientInfo=dict(name='guga-desktop', version='2.0.0')))
        version = result.get('protocolVersion')
        if version not in VERSIONS:
            raise RuntimeError('暂不支持该 MCP 协议版本，请使用兼容 2025-11-25 的服务器')
        self.protocol = version
        if 'tools' not in (result.get('capabilities') or {}):
            raise RuntimeError('此 MCP 软件未提供 tools 能力')
        self._post(dict(jsonrpc='2.0', method='notifications/initialized'), expect_reply=False)
        return result

    def close(self):
        self.session_id = ''


class MCPHost:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / 'mcp_servers.json')

    def execute(self, args, cancel=None, approve=None):
        configs = json.loads(self.path.read_text(encoding='utf-8-sig')).get('mcpServers', {}) if self.path.exists() else {}
        action = args.get('action')
        if action == 'list_servers':
            return {'servers': [dict(name=name, enabled=bool(cfg.get('enabled', False)),
                                     transport='http' if str(cfg.get('url') or '').strip() else 'stdio',
                                     allowed_tools=cfg.get('allowed_tools', []), routine_tools=cfg.get('routine_tools', [])) for name, cfg in configs.items()]}
        server = args.get('server', '')
        cfg = configs.get(server)
        if not cfg or not cfg.get('enabled', False):
            raise ValueError('该软件未配置或尚未授权启用')
        if action not in ('list_tools', 'call'):
            raise ValueError('未知生态操作')
        if action == 'call' and args.get('tool') not in cfg.get('allowed_tools', []):
            raise ValueError('工具不在你授予此软件的 allowed_tools 列表中')
        if action == 'call' and (args.get('tool') not in cfg.get('routine_tools', []) or
                                 args.get('tool') in cfg.get('confirmation_tools', [])):
            if not approve or not approve(dict(server=server, tool=args['tool'], arguments=args.get('arguments', {}))):
                raise ValueError('此操作未获得本次确认，未执行')
        if cancel and cancel.is_set():
            raise RuntimeError('操作已取消')
        client = StreamableHTTPMCP(cfg, cancel) if str(cfg.get('url') or '').strip() else StdioMCP(cfg, cancel)
        try:
            client.initialize()
            if action == 'list_tools':
                result = client.request('tools/list')
                return {'tools': result.get('tools', []), 'nextCursor': result.get('nextCursor'),
                        'allowed_tools': cfg.get('allowed_tools', [])}
            result = client.request('tools/call', dict(name=args['tool'], arguments=args.get('arguments', {})))
            return result
        finally:
            client.close()
