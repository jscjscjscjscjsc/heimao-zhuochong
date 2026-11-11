# -*- coding: utf-8 -*-
"""DSH 当次任务结束后的「先验收、再决定下一步」。

主人 2026-11-11 的要求：
> 在 dsh 执行完当次任务以后，咕嘎具有操作我电脑的权限，当然可以自己在对话框中
> （先验收实际情况），然后自己组织自然语言下一步干什么，然后执行发送。

本模块只做三件事，顺序不能变：
1. **看真实情况**：优先读该任务已有的验收台账（`core/super_agent/acceptance.py`）；
   没有台账就抓工作区实际改动与会话终态。**不看模型自述。**
2. **给结论**：`pass` / `fail` / `unverified`。没有检查命令时恒为 `unverified`，
   绝不因为「看起来做完了」而放行。
3. **组织下一步**：用咕嘎自己的模型把结论 + 下一步写成一句人话；模型不可用就退回
   确定性文案。返回给调用方去播报/发送。

设计约束：
- 全程不修改任何文件，不代替用户做不可逆动作；
- 任何依赖缺失（台账/票据/模型）都降级，不抛异常到 UI 线程。
"""
from __future__ import annotations

PASS = 'pass'
FAIL = 'fail'
UNVERIFIED = 'unverified'


def _ledger_record(acceptance_id):
    """按 id 取验收台账记录；取不到返回 None。"""
    if not acceptance_id:
        return None
    try:
        from core.super_agent.acceptance import AcceptanceLedger
        return AcceptanceLedger().load(acceptance_id)
    except Exception:
        return None


def _ticket_for_session(session_id):
    """按 DSH 会话反查票据；依赖 task-store 的 by_session，缺失就返回 None。"""
    if not session_id:
        return None
    try:
        from core.personal_harness import PersonalHarness
        archive = PersonalHarness()
        if hasattr(archive, 'by_session'):
            return archive.by_session(session_id)
    except Exception:
        return None
    return None


def _workspace_facts(cwd):
    """工作区实际改动（只读 git）。"""
    try:
        from core.super_agent.acceptance import AcceptanceLedger
        return AcceptanceLedger()._workspace_facts(cwd or '')
    except Exception:
        return dict(available=False, reason='无法读取工作区')


def _fallback_next_step(verdict, reasons, workspace):
    """模型不可用时的确定性下一步。"""
    if verdict == PASS:
        return '这轮验收通过了，我把产物整理好交给你；你要是想继续加东西，跟我说一声就行。'
    if verdict == FAIL:
        detail = '；'.join(str(x) for x in list(reasons or [])[:2]) or '检查没通过'
        return '这轮没通过：' + detail[:90] + '。我接着在原来的会话里让它改，改完再验一遍。'
    if workspace.get('available') and workspace.get('changed_files'):
        return '这轮改了 %d 个文件，但我还没有能独立验证的检查项，所以先不算通过。你告诉我该跑什么检查，我就去验。' % workspace['changed_files']
    return '这轮结束了，但我拿不到能独立验证的证据，所以先不算通过。我需要你给一条检查命令，或者我先把实际情况翻给你看。'


def review(session_row, timeout=8.0):
    """DSH 任务结束后调用。

    参数 session_row：dsh_link 里那条会话状态 dict（至少有 session_id/state/summary/goal）。
    返回：
        {ok, session_id, ticket_id, acceptance_id, verdict, reasons, workspace,
         next_step, composed_by}
    """
    row = dict(session_row or {})
    session_id = str(row.get('session_id') or '')
    ticket = _ticket_for_session(session_id) or {}
    acceptance_id = str(ticket.get('acceptance_id') or '')
    cwd = str(ticket.get('cwd') or '')

    record = _ledger_record(acceptance_id)
    verdict = UNVERIFIED
    reasons = []

    if record:
        verdict = str(record.get('verdict') or UNVERIFIED)
        reasons = list(record.get('reasons') or [])
        if verdict == UNVERIFIED and not reasons:
            reasons = ['验收台账还没有判定结论']
        workspace = dict(record.get('workspace') or {})
    else:
        # 没有台账：只用环境事实，且明确标注"未验证"。
        workspace = _workspace_facts(cwd)
        reasons = ['这个任务没有可复用的验收台账：不把模型自述当作通过依据']
        if not cwd:
            reasons.append('没有登记工作目录，无法核对改动')

    # 会话还在跑就谈不上验收。
    if row.get('state') == 'running':
        verdict = UNVERIFIED
        reasons.append('会话还在跑，先不判定')

    composed = ''
    try:
        from core.super_agent import dsh_digest
        composed = dsh_digest.next_step(row, verdict=verdict, reasons=reasons, timeout=timeout)
    except Exception:
        composed = ''

    if composed:
        next_step, composed_by = composed, 'model'
    else:
        next_step, composed_by = _fallback_next_step(verdict, reasons, workspace), 'fallback'

    return dict(ok=True, session_id=session_id,
                ticket_id=str(ticket.get('id') or ''),
                acceptance_id=acceptance_id,
                verdict=verdict, reasons=reasons,
                workspace=workspace,
                next_step=next_step, composed_by=composed_by)
