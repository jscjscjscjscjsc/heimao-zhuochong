# -*- coding: utf-8 -*-
"""工具调用型工作流：steps 里直接写 `tool + args`。

为什么要有它：`action_workflow` 那套录制器记的是 UIA 动作（focus/act/type/key/scroll），
适合"窗口里点按钮"。但「腾讯会议下载录制 → 改名 → B站投稿前」这种长任务，主体是
browser 的 session/upload + local_computer 的 run + media_workflow 的 submission，
用录音机存会录成一个跑不起来的假流程，所以单独给一类格式。

纪律：**只做静态校验**（工具名是否存在、必填参数是否齐、确认闸有没有标），
不执行任何动作。真正的执行仍由模型按步骤调用工具。
"""
import json
import re
import time
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[2] / 'runtime' / 'tool-workflows'
SCHEMA_VERSION = 1
SLUG_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}')

# 无人值守时先挡住这些：删东西、格式化、关机、强推。命中就停，不执行。
DANGEROUS_PATTERNS = (
    (re.compile(r'remove-item\s+.*(-recurse|-force)', re.I), '递归/强制删除'),
    (re.compile(r'\b(del|erase|rmdir|rd)\b', re.I), '删除命令'),
    (re.compile(r'\bformat(-volume)?\b', re.I), '格式化'),
    (re.compile(r'shutdown|restart-computer|stop-computer', re.I), '关机/重启'),
    (re.compile(r'git\s+push\s+.*(--force|-f)\b', re.I), '强推'),
    (re.compile(r'Remove-Item.*C:\\\\Windows', re.I), '删系统目录'),
)


def unsafe_reason(step):
    """这一步看起来会不可逆地动东西？返回原因，否则 None。"""
    if str(step.get('do')) != 'local_computer':
        return None
    args = step.get('args') or {}
    if str(args.get('action')) != 'run':
        return None
    command = str(args.get('command') or '')
    for pattern, label in DANGEROUS_PATTERNS:
        if pattern.search(command):
            return '%s（命令里出现：%s）' % (label, command[:60])
    return None


class ToolWorkflowError(ValueError):
    """工作流本身不合法（不是执行失败）。"""


def known_tools():
    """当前咕嘎暴露给模型的工具名集合。"""
    from core.self_agent import TOOLS
    return {t['function']['name'] for t in TOOLS}


def tool_schema(tool):
    """取某个工具的参数定义（用于校验必填项）。"""
    from core.self_agent import TOOLS
    for item in TOOLS:
        fn = item['function']
        if fn['name'] == tool:
            params = fn.get('parameters') or {}
            return dict(properties=params.get('properties') or {},
                        required=list(params.get('required') or []))
    return None


def validate(spec, tools=None):
    """校验并规范化；不合法就抛 ToolWorkflowError，绝不"当作能用"。"""
    if not isinstance(spec, dict):
        raise ToolWorkflowError('工作流必须是一个对象')
    name = str(spec.get('name') or '').strip()
    slug = str(spec.get('slug') or '').strip()
    raw_steps = spec.get('steps')
    if not name:
        raise ToolWorkflowError('缺少 name')
    if not SLUG_RE.fullmatch(slug):
        raise ToolWorkflowError('slug 不合法：%r' % slug)
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ToolWorkflowError('steps 必须是非空数组')

    available = set(tools) if tools is not None else known_tools()
    steps = []
    for index, raw in enumerate(raw_steps, 1):
        if not isinstance(raw, dict):
            raise ToolWorkflowError('第 %d 步不是对象' % index)
        tool = str(raw.get('do') or '').strip()
        if not tool:
            raise ToolWorkflowError('第 %d 步缺少 do（要调用的工具名）' % index)
        if tool not in available:
            raise ToolWorkflowError('第 %d 步用了不存在的工具：%s' % (index, tool))
        args = raw.get('args') or {}
        if not isinstance(args, dict):
            raise ToolWorkflowError('第 %d 步 args 必须是对象' % index)
        schema = tool_schema(tool) or {}
        missing = [key for key in schema.get('required', []) if key not in args]
        if missing:
            raise ToolWorkflowError('第 %d 步 %s 缺必填参数：%s'
                                    % (index, tool, ','.join(missing)))
        steps.append(dict(index=index, do=tool, args=dict(args),
                          note=str(raw.get('note') or ''),
                          confirm=bool(raw.get('confirm')),
                          skippable=bool(raw.get('skippable', True))))

    return dict(schema=SCHEMA_VERSION, slug=slug, name=name,
                triggers=[str(t) for t in (spec.get('triggers') or [])],
                goal=str(spec.get('goal') or ''),
                steps=steps)


def load(slug, root=None):
    path = Path(root or DEFAULT_DIR) / (slug + '.json')
    if not path.is_file():
        raise ToolWorkflowError('没有找到工具型工作流 %r（%s）' % (slug, path))
    try:
        spec = json.loads(path.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise ToolWorkflowError('工作流 JSON 解析失败：%s' % exc) from exc
    return validate(spec)


def save(spec, root=None):
    workflow = validate(spec)
    folder = Path(root or DEFAULT_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (workflow['slug'] + '.json')).write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2), encoding='utf-8')
    return workflow


def dry_run(spec, tools=None):
    """只走校验，不执行。返回每一步的准备情况 + 有几个需要人工确认的闸门。"""
    workflow = validate(spec, tools=tools)
    gates = [step['index'] for step in workflow['steps'] if step['confirm']]
    return dict(ok=True, slug=workflow['slug'], name=workflow['name'],
                steps=workflow['steps'], total_steps=len(workflow['steps']),
                confirm_gates=gates, tools_used=sorted({s['do'] for s in workflow['steps']}),
                note='只做了静态校验，没有任何动作被执行')


def last_runs(ledger=None):
    """读回执台账，给出每条工作流最后一次跑成什么样。没有就返回空 dict。"""
    path = Path(ledger or (DEFAULT_DIR / 'receipts.jsonl'))
    if not path.is_file():
        return {}
    summary = {}
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return {}
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        slug = str(row.get('slug') or '')
        if not slug:
            continue
        summary[slug] = dict(at=row.get('at'), stopped=row.get('stopped'),
                             executed=row.get('executed'), passed=row.get('passed'),
                             started_at_step=row.get('started_at_step'))
    return summary


def run(spec, execute, *, tools=None, allow_confirm=False, stop_on_error=True,
        max_steps=None, start_at=1, ledger=None, guard_dangerous=True,
        allow_dangerous=False):
    """按步骤执行。

    ``execute(tool, args) -> dict``：真正干活的回调（由调用方注入，便于测试与权限控制）。
    ``start_at``：从第几步开始（人工越过闸门后接着跑，不用从头再来）。
    ``ledger``：给一个 jsonl 路径，就把每一步回执追加进去，事后可追溯。
    纪律：
      - ``confirm=True`` 的步默认**不执行**，返回 ``stopped='confirm_required'``，除非显式 allow_confirm；
      - 任何一步 ``ok=False`` 立刻停（``stopped='step_failed'``），不继续往下猜；
      - 每一步都留回执，报告里能看出走到第几步、每步耗时/错误。
    """
    workflow = validate(spec, tools=tools)
    if not callable(execute):
        raise ToolWorkflowError('run 需要一个 execute(tool, args) 回调')
    first = max(1, int(start_at or 1))
    pending = [step for step in workflow['steps'] if step['index'] >= first]
    if max_steps:
        pending = pending[:int(max_steps)]
    receipts = []
    stopped = 'completed'
    for step in pending:
        if step['confirm'] and not allow_confirm:
            stopped = 'confirm_required'
            receipts.append(dict(index=step['index'], do=step['do'], ok=None,
                                 skipped=True, reason='这一步需要人工确认，没有执行'))
            break
        if guard_dangerous and not allow_dangerous:
            reason = unsafe_reason(step)
            if reason:
                stopped = 'guarded'
                receipts.append(dict(index=step['index'], do=step['do'], ok=None,
                                     skipped=True, reason='被安全护栏拦下：' + reason))
                break
        started = time.monotonic()
        try:
            result = execute(step['do'], dict(step['args']))
        except Exception as exc:  # noqa: BLE001
            result = dict(ok=False, error='%s: %s' % (type(exc).__name__, str(exc)[:240]))
        elapsed = round((time.monotonic() - started) * 1000, 1)
        result = result if isinstance(result, dict) else dict(ok=bool(result))
        receipts.append(dict(index=step['index'], do=step['do'], ok=bool(result.get('ok')),
                             elapsed_ms=elapsed, note=step['note'],
                             error=str(result.get('error') or '')[:200]))
        if not result.get('ok') and stop_on_error:
            stopped = 'step_failed'
            break
    executed = [row for row in receipts if row['ok'] is not None]
    report = dict(ok=stopped in ('completed', 'confirm_required'), stopped=stopped,
                  slug=workflow['slug'], total_steps=len(workflow['steps']),
                  started_at_step=first,
                  executed_steps=len(executed),
                  passed_steps=len([row for row in receipts if row['ok']]),
                  total_ms=round(sum(row.get('elapsed_ms') or 0 for row in receipts), 1),
                  receipts=receipts)
    if ledger:
        try:
            path = Path(ledger)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(dict(at=time.strftime('%Y-%m-%d %H:%M:%S'),
                                             slug=report['slug'], stopped=stopped,
                                             started_at_step=first,
                                             executed=report['executed_steps'],
                                             passed=report['passed_steps'],
                                             receipts=receipts), ensure_ascii=False) + '\n')
        except OSError:
            pass
    return report
