# -*- coding: utf-8 -*-
"""最小 agent harness：计划 → 逐步执行 → 每步验收 → 失败不假装。

为什么单独一个模块：`SelfAgent.run()` 已经有循环、预算、检查点、steering，
缺的是"这一步算不算做完了"的判据。这里只做判据，不碰 Qt、不发请求，
所以可以拿纯逻辑单测把它钉住。

纪律（和 ds-verify-loop 一致）：
- 一步只有拿到**回执证据**才算完成，模型自己说"好了"不算；
- 工具回执 ok=False / 空 → 记卡点，**不推进**，不进入下一步；
- 关掉开关（GUGA_HARNESS=off）就完全退回旧行为。
"""
import json
import os
import re

PLAN_HEAD_RE = re.compile(r'^\s*(?:步骤\s*)?(\d+)\s*[.、)）:：]\s*(.+)$')
CHECK_SPLIT = re.compile(r'[|｜]\s*(?:验收|验证|检查|check|acceptance)\s*[:：]?\s*', re.I)
ACTION_PREFIX = re.compile(r'^\s*(?:我(?:会|先|要)?|先|然后|接着)\s*')

# 验收条件里这些词太泛，命中也不算证据
STEP_STOPWORDS = frozenset((
    '验收', '检查', '确认', '完成', '成功', '结果', '得到', '出现', '可以', '应该',
    '是否', '然后', '接着', '上面', '下面', '一下', '这一步', '这一步的', '有回执',
))


def evidence_matches(step, payload):
    """这一步的验收条件，是否被工具回执里的真实内容命中。

    判据是硬的：把验收条件里的实词，拿去和回执的实际内容比对。
    模型自己说"做完了"**不参与判定**——这就是"验收"和"自述"的区别。
    返回 True/False；None 表示"这条验收条件没法判定"（没写或全是虚词）。
    """
    check = str((step or {}).get('check') or '')
    if not check.strip():
        return None
    try:
        blob = json.dumps(payload, ensure_ascii=False, default=str).lower()
    except (TypeError, ValueError):
        blob = str(payload).lower()
    if not blob.strip():
        return False
    tokens = [t.lower() for t in re.findall(r'[\u4e00-\u9fa5]{2,}|[A-Za-z_]{3,}', check)]
    tokens = [t for t in tokens if t not in STEP_STOPWORDS]
    if not tokens:
        return None
    return any(token in blob for token in tokens)



def enabled():
    """逃生开关：GUGA_HARNESS=off 时退回旧行为。"""
    return str(os.environ.get('GUGA_HARNESS', '') or '').strip().lower() not in ('off', '0', 'false', 'no')


def parse_plan(text):
    """从模型回复里抽编号计划。容忍 1. / 1） / 步骤1： / - 1)；解析不出就返回空列表。"""
    steps = []
    for raw in str(text or '').splitlines():
        line = raw.strip().lstrip('-*+ ').strip()
        match = PLAN_HEAD_RE.match(line)
        if not match:
            continue
        body = match.group(2).strip()
        if len(body) < 2:
            continue
        parts = CHECK_SPLIT.split(body, maxsplit=1)
        action = ACTION_PREFIX.sub('', parts[0].strip()).rstrip('。；;')
        check = parts[1].strip() if len(parts) > 1 else ''
        if not action:
            continue
        steps.append(dict(index=int(match.group(1)), action=action, check=check))
    # 编号可能乱序或重复：按出现顺序重排成 1..n，保留原始文本
    for position, step in enumerate(steps, 1):
        step['index'] = position
    return steps


class Plan:
    """一个计划：步骤 + 完成证据 + 卡点。"""

    def __init__(self, goal='', steps=None):
        self.goal = str(goal or '')
        self.steps = [dict(step, done=False, evidence='') for step in (steps or [])]
        self.stuck_at = None
        self.stuck_reason = ''
        self.parse_failed = not self.steps

    # -- 状态 -------------------------------------------------------- #
    def current(self):
        if self.stuck_at is not None:
            return None
        for step in self.steps:
            if not step['done']:
                return step
        return None

    def current_index(self):
        step = self.current()
        return step['index'] if step else None

    def completed(self):
        return [step['index'] for step in self.steps if step['done']]

    def finished(self):
        return bool(self.steps) and all(step['done'] for step in self.steps)

    # -- 推进 -------------------------------------------------------- #
    def mark_done(self, index, evidence):
        evidence = str(evidence or '').strip()
        if not evidence:
            raise ValueError('没有证据不能标记完成')
        for step in self.steps:
            if step['index'] == int(index):
                step['done'] = True
                step['evidence'] = evidence[:400]
                return step
        raise KeyError('没有第 %s 步' % index)

    def stuck(self, index, reason):
        self.stuck_at = int(index) if index is not None else None
        self.stuck_reason = str(reason or '')[:300]

    # -- 注入 -------------------------------------------------------- #
    def to_prompt(self):
        if not self.steps:
            return ''
        lines = ['当前任务计划（每步都要有验收，没证据不算完成）：']
        if self.goal:
            lines.append('目标：' + self.goal[:300])
        for step in self.steps:
            mark = '✓' if step['done'] else ('✗' if self.stuck_at == step['index'] else '·')
            check = ('｜验收：' + step['check']) if step['check'] else ''
            lines.append('%s %d. %s%s' % (mark, step['index'], step['action'][:180], check))
            if step['done'] and step['evidence']:
                lines.append('    证据：' + step['evidence'][:160])
        nxt = self.current()
        if nxt:
            lines.append('下一步只做第 %d 步。做完必须给出证据（工具回执/读回的真实变化）。' % nxt['index'])
        elif self.stuck_at is not None:
            lines.append('第 %d 步卡住了：%s。不要跳过它假装完成。' % (self.stuck_at, self.stuck_reason))
        return '\n'.join(lines)

    def status(self):
        return dict(goal=self.goal[:200], total=len(self.steps),
                    completed=self.completed(), current=self.current_index(),
                    finished=self.finished(), stuck_at=self.stuck_at,
                    stuck_reason=self.stuck_reason,
                    parse_failed=self.parse_failed)


class Harness:
    """把计划、工具回执和卡点串起来。SelfAgent 只需要三处调用：begin/record/status。"""

    def __init__(self, goal=''):
        self.plan = Plan(goal=goal)
        self.trace = []
        self.tool_calls = 0
        self.failures = 0
        self.auto_advanced = []   # 哪些步是靠硬判据自动推进的（不是模型自述）
        self.recovered = []       # 卡住之后靠"真实成功回执"恢复的步

    def begin(self, plan_text, goal=''):
        steps = parse_plan(plan_text)
        self.plan = Plan(goal=goal or self.plan.goal, steps=steps)
        return self.plan

    def record(self, tool, result):
        """记一次工具回执；返回是否算"这个工具成功了"。失败会标卡点，命中验收条件会自动推进。"""
        self.tool_calls += 1
        result = result if isinstance(result, dict) else {'ok': bool(result)}
        ok = bool(result.get('ok'))
        inner = result.get('result')
        if ok and isinstance(inner, dict) and inner.get('ok') is False:
            ok = False  # Bridge 包了一层 {ok:True,result:{ok:False}} 的情况
        entry = dict(tool=str(tool), ok=ok,
                     detail=str(result.get('error') or (inner if isinstance(inner, str) else ''))[:200])
        self.trace.append(entry)
        if not ok:
            self.failures += 1
            current = self.plan.current_index()
            if current is not None:
                self.plan.stuck(current, '%s 失败：%s' % (entry['tool'], entry['detail'] or '没有回执'))
            return ok
        # 卡住之后又拿到成功回执：允许恢复，但要留痕（不是假装没失败过）
        if self.plan.stuck_at is not None:
            self.recovered.append(self.plan.stuck_at)
            self.plan.stuck_at = None
            self.plan.stuck_reason = ''
        # 成功了还要看"验收条件被命中没有"——命中才算这一步做完
        current = self.plan.current()
        if current is not None and evidence_matches(current, result) is True:
            self.auto_advanced.append(current['index'])
            self.plan.mark_done(current['index'],
                                '自动核对：%s 的回执命中验收条件「%s」'
                                % (tool, str(current.get('check') or '')[:60]))
        return ok

    def acknowledge(self, evidence):
        """当前步拿到证据后调它推进计划。没有证据会被拒绝。"""
        current = self.plan.current_index()
        if current is None:
            return None
        return self.plan.mark_done(current, evidence)

    def prompt_block(self):
        return self.plan.to_prompt()

    def status(self):
        report = self.plan.status()
        report.update(tool_calls=self.tool_calls, failures=self.failures,
                      auto_advanced=list(self.auto_advanced),
                      recovered=list(self.recovered),
                      last_tool=(self.trace[-1]['tool'] if self.trace else ''))
        return report
