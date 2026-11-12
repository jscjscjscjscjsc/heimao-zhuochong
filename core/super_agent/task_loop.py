# -*- coding: utf-8 -*-
"""咕嘎 × DSH 闭环：派单 → 独立验收 → 有限返工 → 交付。

为什么要有它
------------
DSH 会说自己"完成了"，但自述不是证据。这一层把闭环钉死在三件硬事实上：
① 声明式检查命令的真实退出码 ② DSH 会话是否真的结束 ③ 工作区是否真的改动。
判定只来自 `core/super_agent/acceptance.py` 的 AcceptanceLedger；模型自述只写进
台账留档，不参与放行。

三条铁律（改代码前先读）
------------------------
1. 返工只投回**原 DSH 会话**（``gateway.prompt(session_id, ..., mode='queue')``），
   绝不新建会话——否则上下文断掉，返工就变成了重做。
2. ``attempts >= max_attempts`` 或预算耗尽 → ``state='blocked'`` 并给出 reasons，
   不无限循环。
3. ``unverified`` 不算通过；没有任何 checks 时不得放行。

分区：cwd 默认 ``runtime/dsh-workspace/<partition>/<ticket_id>/``，
票据 ``partition='guga'``，会话标题前缀 ``[咕嘎]``。

依赖降级
--------
``dsh_binding`` / ``capability_catalog`` / ``personal_harness`` 的新方法由其他工作流
并行开发，可能尚未落地：这里全部按"可选增强"处理，缺了就降级，绝不因此卡住。
本地 JSON 台账是权威，personal_harness 只是可选的持久镜像。
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / 'runtime' / 'task-loop'
WORKSPACE_ROOT = ROOT / 'runtime' / 'dsh-workspace'

AGENT = 'guga'
DEFAULT_PARTITION = 'guga'
TITLE_PREFIX = '[咕嘎]'
PROMPT_LIMIT = 7900  # dsh_gateway.prompt 的上限是 8000，留一点余量

STATE_DISPATCHED = 'dispatched'
STATE_RUNNING = 'running'
STATE_AWAITING_REVIEW = 'awaiting_review'
STATE_REWORK = 'rework'
STATE_UNVERIFIED = 'unverified'
STATE_BLOCKED = 'blocked'
STATE_DELIVERED = 'delivered'
STATE_FAILED = 'failed'

PASS = 'pass'
FAIL = 'fail'
UNVERIFIED = 'unverified'


def _now(clock=None):
    return round(float((clock or time.time)()), 3)


class TaskLoop:
    """一张票据 = 一个目标 + 一个专属 DSH 会话 + 一条独立验收链。"""

    def __init__(self, gateway=None, ledger=None, harness=None, binding=None,
                 catalog=None, clock=None):
        self.clock = clock or time.time
        self.gateway = gateway if gateway is not None else self._default_gateway()
        self.ledger = ledger if ledger is not None else self._default_ledger()
        self.harness = harness if harness is not None else self._default_harness()
        self.binding = binding if binding is not None else self._default_binding()
        self.catalog = catalog if catalog is not None else self._default_catalog()
        self.root = RUNTIME
        self.root.mkdir(parents=True, exist_ok=True)
        # 0 = 不限预算；可用环境变量或实例属性覆盖。
        self.budget_seconds = float(os.environ.get('GUGA_TASK_LOOP_BUDGET_SECONDS') or 0)
        # None = 自动（自建分区目录/用户给的 git 仓库才检查工作区改动）。
        self.expect_changes = None
        self._lock = threading.RLock()

    # ------------------------------------------------------------ 可选依赖
    @staticmethod
    def _default_gateway():
        try:
            from core.super_agent.dsh_gateway import DshGateway
            return DshGateway()
        except Exception:
            return None

    @staticmethod
    def _default_ledger():
        try:
            from core.super_agent.acceptance import AcceptanceLedger
            return AcceptanceLedger()
        except Exception:
            return None

    @staticmethod
    def _default_harness():
        try:
            from core.personal_harness import PersonalHarness
            return PersonalHarness()
        except Exception:
            return None

    @staticmethod
    def _default_binding():
        try:
            from core.super_agent import dsh_binding
            return dsh_binding
        except Exception:
            return None

    @staticmethod
    def _default_catalog():
        try:
            from core.super_agent import capability_catalog
            return capability_catalog
        except Exception:
            return None

    # ------------------------------------------------------------ 本地台账
    def _path(self, ticket_id):
        return self.root / ('%s.json' % str(ticket_id))

    def _load(self, ticket_id):
        path = self._path(ticket_id)
        if not path.exists():
            raise KeyError('没有这张票据：%s' % str(ticket_id)[:80])
        with self._lock:
            return json.loads(path.read_text(encoding='utf-8-sig'))

    def _save(self, record):
        record['updated_at'] = _now(self.clock)
        path = self._path(record['ticket_id'])
        with self._lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.pending')
            temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str),
                                 encoding='utf-8')
            os.replace(temporary, path)
        return record

    def _note(self, record, action, detail=''):
        record.setdefault('history', []).append(
            dict(at=_now(self.clock), action=str(action)[:60], detail=str(detail)[:300]))
        record['history'] = record['history'][-60:]

    # ------------------------------------------------------------ 派单
    def dispatch(self, goal, conversation_id='', cwd='', checks=(), partition='guga',
                 max_attempts=3):
        """建票据 → 认准实例 → 在 DSH 建专属会话 → 投首条指令。"""
        goal = str(goal or '').strip()
        if not goal:
            return dict(ok=False, error='dispatch 需要一个明确的目标')
        partition = str(partition or DEFAULT_PARTITION).strip() or DEFAULT_PARTITION
        try:
            max_attempts = max(1, int(max_attempts or 3))
        except (TypeError, ValueError):
            return dict(ok=False, error='max_attempts 必须是正整数')
        checks = self._checks_list(checks)

        ticket_id = 'tk-' + uuid.uuid4().hex[:12]
        given_cwd = str(cwd or '').strip()
        workdir = (Path(given_cwd).expanduser().resolve() if given_cwd
                   else (WORKSPACE_ROOT / partition / ticket_id).resolve())
        try:
            self._prepare_workspace(workdir, given=bool(given_cwd))
        except OSError as exc:
            return dict(ok=False, error='无法准备工作目录：%s: %s' % (
                type(exc).__name__, str(exc)[:160]))

        record = dict(
            ticket_id=ticket_id, goal=goal[:4000],
            conversation_id=str(conversation_id or ''), partition=partition,
            cwd=str(workdir), checks=checks, max_attempts=max_attempts,
            session_id='', state=STATE_DISPATCHED, attempts=0, verdict='', reasons=[],
            acceptance_id='', claim='', unverified_reworks=0, rework_log=[], history=[],
            created_at=_now(self.clock), updated_at=_now(self.clock),
            title_prefix=TITLE_PREFIX, delivered=False, warnings=[],
            harness_ticket='', binding={},
        )

        # 票据编号先定（cwd 默认路径依赖它）；personal_harness 是可选的持久镜像。
        harness_ticket = self._harness_create(goal, conversation_id, str(workdir), partition)
        record['harness_ticket'] = str((harness_ticket or {}).get('id') or '')
        if (harness_ticket or {}).get('harness_error'):
            record['warnings'].append('personal_harness 未就绪，已降级本地台账：' +
                                      str(harness_ticket['harness_error'])[:160])
        record['binding'] = self._binding_facts()

        gateway = self.gateway
        if gateway is None or not callable(getattr(gateway, 'create_session', None)):
            return self._fail(record, 'DSH 网关不可用：拿不到 create_session')
        try:
            created = gateway.create_session(conversation_id=record['conversation_id']) or {}
        except Exception as exc:  # noqa: BLE001
            return self._fail(record, '新建 DSH 会话失败：%s: %s' % (
                type(exc).__name__, str(exc)[:200]))
        session_id = str(created.get('session_id') or created.get('sessionId') or '').strip()
        if not session_id:
            return self._fail(record, 'DSH 受理了新建会话但没有返回会话编号；不假装已经建好')
        record['session_id'] = session_id
        # 权限闸：咕嘎派出去的会话必须是完全权限（danger-full-access + approval:never）。
        # 切不过去就当场失败，绝不硬派——否则任务跑到一半卡在「等人点同意」，
        # 那比直接报错更难查，也更违背"人只跟咕嘎打交道"的前提。
        permission = created.get('permission') or {}
        if created.get('full_access') is False or (permission and not permission.get('ok')):
            record['state'] = STATE_FAILED
            record['reasons'] = ['会话权限不是完全权限，拒绝派单：%s' % (
                permission.get('error') or '未知原因')]
            self._note(record, 'dispatch_permission_failed', str(permission)[:400])
            self._save(record)
            return dict(ok=False, ticket_id=ticket_id, session_id=session_id,
                        error='会话权限不是完全权限，已停止派单', permission=permission)
        if record['harness_ticket']:
            self._harness_sync('bind_dsh', record['harness_ticket'], session_id)
            self._harness_sync('set_partition', record['harness_ticket'], partition)

        text = self._first_prompt(goal, checks, self._catalog_text())
        try:
            gateway.prompt(session_id, text, mode='queue')
        except Exception as exc:  # noqa: BLE001
            record['state'] = STATE_FAILED
            record['reasons'] = ['会话已建立，但首条指令没有投递成功：%s: %s' % (
                type(exc).__name__, str(exc)[:200])]
            self._note(record, 'dispatch_prompt_failed', str(exc))
            self._save(record)
            return dict(ok=False, ticket_id=ticket_id, session_id=session_id,
                        partition=partition, cwd=str(workdir), checks=checks,
                        max_attempts=max_attempts, error=record['reasons'][0])

        self._note(record, 'dispatch', 'session=%s' % session_id)
        self._save(record)
        return dict(ok=True, ticket_id=ticket_id, session_id=session_id, partition=partition,
                    cwd=str(workdir), checks=checks, max_attempts=max_attempts,
                    harness_ticket=record['harness_ticket'], title_prefix=TITLE_PREFIX,
                    binding=record['binding'], warnings=record['warnings'])

    def _fail(self, record, error):
        record['state'] = STATE_FAILED
        record['reasons'] = [str(error)[:400]]
        self._note(record, 'failed', error)
        self._save(record)
        return dict(ok=False, ticket_id=record['ticket_id'],
                    session_id=record.get('session_id') or '',
                    partition=record.get('partition'), cwd=record.get('cwd'),
                    checks=record.get('checks') or [], max_attempts=record.get('max_attempts'),
                    error=str(error)[:400])

    def attach(self, ticket_id, goal='', session_id='', cwd='', checks=(), partition='guga',
               max_attempts=3, harness_ticket='', claim=''):
        """把**外部已经建好的** DSH 会话挂进闭环（headless worker 用）。

        只登记事实：不新建会话、不投递任何消息。随后用 ``accept_view`` 做独立验收。
        """
        ticket_id = str(ticket_id or '').strip()
        session_id = str(session_id or '').strip()
        if not ticket_id:
            return dict(ok=False, error='attach 需要 ticket_id')
        if not session_id:
            return dict(ok=False, error='attach 需要已存在的 session_id')
        try:
            record = self._load(ticket_id)
        except KeyError:
            record = dict(
                ticket_id=ticket_id, goal=str(goal or '')[:4000], conversation_id='',
                partition=str(partition or DEFAULT_PARTITION), cwd=str(cwd or ''),
                checks=self._checks_list(checks), max_attempts=max(1, int(max_attempts or 3)),
                session_id=session_id, state=STATE_DISPATCHED, attempts=0, verdict='',
                reasons=[], acceptance_id='', claim=str(claim or '')[:4000],
                unverified_reworks=0, rework_log=[], history=[],
                created_at=_now(self.clock), updated_at=_now(self.clock),
                title_prefix=TITLE_PREFIX, delivered=False, warnings=[],
                harness_ticket=str(harness_ticket or ''), binding={},
            )
            self._note(record, 'attach', 'session=%s' % session_id)
            self._save(record)
        return dict(ok=True, ticket_id=ticket_id, session_id=session_id,
                    partition=record.get('partition'), cwd=record.get('cwd'),
                    checks=record.get('checks'), max_attempts=record.get('max_attempts'))

    def accept_view(self, ticket_id, view):
        """用调用方给的环境事实（如 headless worker 的 final 事件）做独立验收。"""
        return self.accept(ticket_id, _view=view)

    # ------------------------------------------------------------ 状态
    def status(self, ticket_id):
        try:
            record = self._load(ticket_id)
        except KeyError as exc:
            return dict(ok=False, error=str(exc))
        view, view_error = self._session_view(record)
        title = str((view or {}).get('title') or '')
        attempt = int(record.get('attempts') or 0)
        max_attempts = int(record.get('max_attempts') or 3)
        return dict(ok=True, ticket_id=record['ticket_id'], state=record.get('state') or '',
                    verdict=record.get('verdict') or '',
                    reasons=record.get('reasons') or [], attempts=attempt,
                    max_attempts=max_attempts, exhausted=attempt >= max_attempts,
                    session_id=record.get('session_id') or '',
                    running=None if view is None else bool(view.get('running')),
                    title=title, title_ok=(title.startswith(TITLE_PREFIX) if title else None),
                    cwd=record.get('cwd') or '', partition=record.get('partition'),
                    checks=record.get('checks') or [],
                    acceptance_id=record.get('acceptance_id') or '',
                    delivered=bool(record.get('delivered')),
                    claim=record.get('claim') or '', view_error=view_error,
                    history=record.get('history') or [])

    # ------------------------------------------------------------ 独立验收
    def accept(self, ticket_id, _view=None):
        """跑 checks + 会话终态 + 工作区改动，写台账；结论只认 pass/fail/unverified。"""
        try:
            record = self._load(ticket_id)
        except KeyError as exc:
            return dict(ok=False, error=str(exc))
        session_id = str(record.get('session_id') or '')
        if not session_id:
            return dict(ok=False, ticket_id=record['ticket_id'], verdict=UNVERIFIED,
                        reasons=['没有 DSH 会话可验收：先 dispatch'], passed=False)
        ledger = self.ledger
        if ledger is None or not callable(getattr(ledger, 'open', None)):
            return dict(ok=False, ticket_id=record['ticket_id'], verdict=UNVERIFIED,
                        reasons=['验收台账不可用（core/super_agent/acceptance.py 未加载）'],
                        passed=False)

        if _view is not None:
            view, view_error = _view, ''
        else:
            view, view_error = self._session_view(record)
        claim = self._claim_text(view)
        try:
            opened = ledger.open(goal=record.get('goal', ''), session_id=session_id,
                                 cwd=record.get('cwd', ''),
                                 checks=record.get('checks') or [],
                                 expect_changes=self._expect_changes(record))
            acc_id = opened['id']
            if claim:
                ledger.claim(acc_id, claim)  # 只留档，不参与判定
            ledger.observe(acc_id, view)
            ledger.run_checks(acc_id)
            judged = ledger.judge(acc_id)
        except Exception as exc:  # noqa: BLE001
            return dict(ok=False, ticket_id=record['ticket_id'], verdict=UNVERIFIED,
                        reasons=['验收台账执行失败：%s: %s' % (
                            type(exc).__name__, str(exc)[:200])],
                        acceptance_id=record.get('acceptance_id') or '', passed=False)

        verdict = str(judged.get('verdict') or UNVERIFIED)
        if verdict not in (PASS, FAIL, UNVERIFIED):
            verdict = UNVERIFIED
        reasons = [str(item)[:400] for item in (judged.get('reasons') or [])][:12]
        record.update(verdict=verdict, reasons=reasons, acceptance_id=acc_id, claim=claim,
                      state=(STATE_AWAITING_REVIEW if verdict == PASS
                             else STATE_REWORK if verdict == FAIL else STATE_UNVERIFIED))
        if record.get('harness_ticket'):
            self._harness_sync('link_acceptance', record['harness_ticket'], acc_id, verdict)
        self._note(record, 'accept', '%s %s' % (verdict, acc_id))
        self._save(record)
        return dict(ok=True, ticket_id=record['ticket_id'], verdict=verdict,
                    passed=(verdict == PASS), reasons=reasons, acceptance_id=acc_id,
                    session_id=session_id, claim=claim, view_error=view_error,
                    state=record['state'])

    # ------------------------------------------------------------ 推进一拍
    def step(self, ticket_id):
        """观察环境事实 → 判定 → 必要时返工；不通过且用尽次数就 blocked。"""
        try:
            record = self._load(ticket_id)
        except KeyError as exc:
            return dict(ok=False, error=str(exc))
        attempt = int(record.get('attempts') or 0)
        max_attempts = int(record.get('max_attempts') or 3)
        if record.get('delivered'):
            return dict(ok=True, ticket_id=record['ticket_id'], state=STATE_DELIVERED,
                        verdict=record.get('verdict') or '',
                        reasons=record.get('reasons') or [], attempt=attempt,
                        max_attempts=max_attempts, exhausted=attempt >= max_attempts,
                        delivered=True)

        budget_reason = self._budget_reason(record)
        if budget_reason:
            return self._block(record, [budget_reason])

        view, _view_error = self._session_view(record)
        if view is not None and view.get('running'):
            record['state'] = STATE_RUNNING
            self._note(record, 'step', '会话仍在跑，不判定')
            self._save(record)
            return dict(ok=True, ticket_id=record['ticket_id'], state=STATE_RUNNING,
                        verdict='', reasons=['会话仍在跑，等它结束再验收'],
                        attempt=attempt, max_attempts=max_attempts,
                        exhausted=attempt >= max_attempts, delivered=False)

        accepted = self.accept(ticket_id, _view=view)
        if not accepted.get('ok'):
            return dict(ok=False, ticket_id=record['ticket_id'], state=record.get('state'),
                        verdict=accepted.get('verdict') or UNVERIFIED,
                        reasons=accepted.get('reasons') or [], attempt=attempt,
                        max_attempts=max_attempts, exhausted=attempt >= max_attempts,
                        delivered=False, error=accepted.get('error') or '')

        record = self._load(ticket_id)
        verdict = accepted.get('verdict') or UNVERIFIED
        reasons = accepted.get('reasons') or []
        attempt = int(record.get('attempts') or 0)
        max_attempts = int(record.get('max_attempts') or 3)

        if verdict == PASS:
            record['state'] = STATE_AWAITING_REVIEW
            self._save(record)
            return dict(ok=True, ticket_id=record['ticket_id'], state=STATE_AWAITING_REVIEW,
                        verdict=PASS, reasons=reasons, attempt=attempt,
                        max_attempts=max_attempts, exhausted=attempt >= max_attempts,
                        delivered=False)

        if attempt >= max_attempts:
            return self._block(record, [
                '已达最大返工次数（%d/%d）：不再自动返工' % (attempt, max_attempts)] + reasons)

        if verdict == FAIL:
            reworked = self.rework(ticket_id, reasons, _kind='fail')
            return self._step_result(reworked, verdict, reasons)

        # unverified：不得当通过，也最多自动补证一次（先补 checks）。
        if int(record.get('unverified_reworks') or 0) >= 1:
            return self._block(record, [
                '验收结论是 unverified，且已自动补证一次仍无法独立验证；不放行'] + reasons)
        reworked = self.rework(ticket_id, reasons, _kind='unverified')
        return self._step_result(reworked, verdict, reasons)

    @staticmethod
    def _step_result(reworked, verdict, reasons):
        result = dict(ok=bool(reworked.get('ok')), ticket_id=reworked.get('ticket_id'),
                      state=reworked.get('state') or STATE_REWORK, verdict=verdict,
                      reasons=list(reasons or [])[:12], attempt=reworked.get('attempt', 0),
                      max_attempts=reworked.get('max_attempts', 3),
                      exhausted=bool(reworked.get('exhausted')), delivered=False)
        if reworked.get('error'):
            result['error'] = reworked['error']
        return result

    # ------------------------------------------------------------ 有限返工
    def rework(self, ticket_id, reasons, _kind='fail'):
        """向**原会话**投递具体修改要求；同一 session_id，绝不新建。"""
        try:
            record = self._load(ticket_id)
        except KeyError as exc:
            return dict(ok=False, error=str(exc))
        session_id = str(record.get('session_id') or '')
        if not session_id:
            return dict(ok=False, ticket_id=ticket_id, state=record.get('state'),
                        error='没有原 DSH 会话可返工：先 dispatch')
        attempt = int(record.get('attempts') or 0)
        max_attempts = int(record.get('max_attempts') or 3)
        if attempt >= max_attempts:
            blocked = self._block(record, [
                '已达最大返工次数（%d/%d）：不再自动返工' % (attempt, max_attempts)])
            return dict(ok=False, ticket_id=ticket_id, state=STATE_BLOCKED,
                        attempt=attempt, max_attempts=max_attempts, exhausted=True,
                        reasons=blocked.get('reasons') or [], error='已达最大返工次数')

        gateway = self.gateway
        if gateway is None or not callable(getattr(gateway, 'prompt', None)):
            return dict(ok=False, ticket_id=ticket_id, error='DSH 网关不可用：无法投递返工')

        text = self._rework_prompt(reasons, attempt + 1, max_attempts, _kind)
        try:
            gateway.prompt(session_id, text, mode='queue')  # 铁律：原会话，queue
        except Exception as exc:  # noqa: BLE001
            return dict(ok=False, ticket_id=ticket_id, session_id=session_id,
                        error='返工投递失败：%s: %s' % (type(exc).__name__, str(exc)[:200]))

        attempt = self._bump_attempt(record, attempt, max_attempts)
        record['state'] = STATE_REWORK
        if _kind == 'unverified':
            record['unverified_reworks'] = int(record.get('unverified_reworks') or 0) + 1
        record.setdefault('rework_log', []).append(
            dict(at=_now(self.clock), attempt=attempt, kind=_kind,
                 reasons=[str(item)[:300] for item in (reasons or [])][:10]))
        self._note(record, 'rework', 'attempt %d/%d kind=%s' % (attempt, max_attempts, _kind))
        self._save(record)
        return dict(ok=True, ticket_id=ticket_id, session_id=session_id, attempt=attempt,
                    max_attempts=max_attempts, exhausted=attempt >= max_attempts,
                    state=STATE_REWORK, reasons=list(reasons or [])[:12])

    def _bump_attempt(self, record, attempt, max_attempts):
        """优先让 personal_harness 记账（重启后可续跑），缺方法就本地递增。"""
        harness_ticket = str(record.get('harness_ticket') or '')
        if harness_ticket:
            synced = self._harness_sync('attempt', harness_ticket, max_attempts=max_attempts)
            value = synced.get('value') if synced.get('available') else None
            if isinstance(value, dict) and value.get('attempts') is not None:
                record['attempts'] = int(value.get('attempts') or 0)
                if value.get('max_attempts') is not None:
                    record['max_attempts'] = int(value['max_attempts'])
                return record['attempts']
        record['attempts'] = attempt + 1
        return record['attempts']

    # ------------------------------------------------------------ 交付
    def deliver(self, ticket_id):
        """只有独立验收 pass 才交付；自述永远不参与这个判断。"""
        try:
            record = self._load(ticket_id)
        except KeyError as exc:
            return dict(ok=False, error=str(exc))
        verdict = str(record.get('verdict') or '')
        if verdict != PASS:
            return dict(ok=False, ticket_id=ticket_id, delivered=False,
                        verdict=verdict or UNVERIFIED,
                        reasons=['未通过独立验收，拒绝交付'] +
                                [str(item)[:300] for item in (record.get('reasons') or [])][:10])
        report_path = ''
        try:
            folder = self.root / 'deliveries'
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / ('%s.md' % ticket_id)
            path.write_text(self._delivery_report(record), encoding='utf-8')
            report_path = str(path)
        except OSError:
            report_path = ''
        record['state'] = STATE_DELIVERED
        record['delivered'] = True
        record['delivered_at'] = _now(self.clock)
        record['delivery_report'] = report_path
        self._note(record, 'deliver', report_path or 'inline')
        self._save(record)
        return dict(ok=True, ticket_id=ticket_id, delivered=True, verdict=PASS,
                    session_id=record.get('session_id') or '',
                    cwd=record.get('cwd') or '',
                    acceptance_id=record.get('acceptance_id') or '',
                    report_path=report_path,
                    claim=(record.get('claim') or '')[:1200],
                    reasons=record.get('reasons') or [])

    # ------------------------------------------------------------ 内部工具
    def _block(self, record, reasons):
        record['state'] = STATE_BLOCKED
        record['reasons'] = [str(item)[:400] for item in (reasons or [])][:12]
        record['blocked_at'] = _now(self.clock)
        self._note(record, 'blocked', '; '.join(record['reasons'])[:300])
        self._save(record)
        return dict(ok=False, ticket_id=record['ticket_id'], state=STATE_BLOCKED,
                    verdict=record.get('verdict') or '', reasons=record['reasons'],
                    attempt=record.get('attempts', 0),
                    max_attempts=record.get('max_attempts', 3),
                    exhausted=True, delivered=False)

    def _budget_reason(self, record):
        if not self.budget_seconds:
            return ''
        used = _now(self.clock) - float(record.get('created_at') or 0)
        if used <= self.budget_seconds:
            return ''
        return '预算耗尽：已用 %.0f 秒，上限 %.0f 秒' % (used, self.budget_seconds)

    def _session_view(self, record):
        session_id = str(record.get('session_id') or '')
        if not session_id:
            return None, '没有 DSH 会话'
        gateway = self.gateway
        if gateway is None or not callable(getattr(gateway, 'session_view', None)):
            return None, 'DSH 网关不可用'
        try:
            return gateway.session_view(session_id), ''
        except Exception as exc:  # noqa: BLE001
            return None, '%s: %s' % (type(exc).__name__, str(exc)[:200])

    @staticmethod
    def _claim_text(view):
        """只取 DSH 最后一条答复作留档；它不参与任何判定。"""
        if not isinstance(view, dict):
            return ''
        outline = view.get('turn_outline') or []
        if not isinstance(outline, list) or not outline:
            return ''
        last = outline[-1] if isinstance(outline[-1], dict) else {}
        return str(last.get('response') or '')[:4000]

    def _expect_changes(self, record):
        if self.expect_changes is not None:
            return bool(self.expect_changes)
        return self._is_git(record.get('cwd') or '')

    @staticmethod
    def _is_git(cwd):
        if not cwd or not Path(cwd).is_dir():
            return False
        try:
            completed = subprocess.run(['git', 'rev-parse', '--is-inside-work-tree'],
                                       cwd=cwd, capture_output=True, text=True,
                                       encoding='utf-8', errors='replace', timeout=20)
        except (OSError, subprocess.SubprocessError):
            return False
        return completed.returncode == 0 and 'true' in (completed.stdout or '').lower()

    @staticmethod
    def _prepare_workspace(workdir, given=False):
        workdir.mkdir(parents=True, exist_ok=True)
        if given:
            return
        # 自建分区目录初始化成 git 仓库，验收才能用 git status 取证"真的改了东西"。
        try:
            subprocess.run(['git', 'init', '-q'], cwd=str(workdir), capture_output=True,
                           text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            pass

    @staticmethod
    def _checks_list(checks):
        if checks is None:
            return []
        if isinstance(checks, (str, dict)):
            return [checks]
        return [check for check in checks]

    @staticmethod
    def _check_text(check):
        if isinstance(check, str):
            return check
        if isinstance(check, (list, tuple)):
            return ' '.join(str(part) for part in check)
        if isinstance(check, dict):
            command = check.get('command')
            if isinstance(command, (list, tuple)):
                return ' '.join(str(part) for part in command)
            return str(command or check.get('label') or '')
        return str(check)

    def _first_prompt(self, goal, checks, catalog_text):
        lines = ['%s 任务：%s' % (TITLE_PREFIX, goal), '',
                 '【独立验收标准（由外部执行，不以你的自述为准）】']
        if checks:
            for index, check in enumerate(checks, 1):
                lines.append('%d. `%s`' % (index, self._check_text(check)))
        else:
            lines.append('（本次没有声明自动检查；完成后必须给出可复现的验证命令和真实输出，'
                         '否则不会被放行。）')
        lines += ['', '【交付要求】',
                  '- 只在上面这个工作目录内改动，不要写到用户其他目录；',
                  '- 改完先自己跑一遍验收命令，贴真实退出码/输出；',
                  '- 不确定就写"未验证"，禁止把"看起来完成"写成完成；',
                  '- 最后用一段话说明：改了什么、怎么验证的、真实结果是什么。']
        if catalog_text:
            lines += ['', '【本机真实能力摘要（来自 dsh --dump-config）】', catalog_text]
        return '\n'.join(lines)[:PROMPT_LIMIT]

    def _rework_prompt(self, reasons, attempt, max_attempts, kind):
        if kind == 'unverified':
            head = ('%s 返工第 %d/%d 轮：验收结论是 unverified（无法独立确认）。'
                    % (TITLE_PREFIX, attempt, max_attempts))
        else:
            head = ('%s 返工第 %d/%d 轮：独立验收未通过。'
                    % (TITLE_PREFIX, attempt, max_attempts))
        lines = [head, '', '【未通过的具体原因】']
        for reason in (reasons or [])[:10]:
            lines.append('- %s' % str(reason)[:300])
        lines += ['', '【这一轮的要求】',
                  '- 只针对上面列出的原因修改，不要重做无关部分；',
                  '- 不要新建任务、不要换工作目录，继续在这个会话里做；',
                  '- 改完必须自己跑验收命令并贴真实输出；']
        if kind == 'unverified':
            lines.append('- 如果你认为已经完成，请给出可复现的验证命令和真实输出，'
                         '让外部能独立确认。')
        return '\n'.join(lines)[:PROMPT_LIMIT]

    def _delivery_report(self, record):
        lines = ['# 交付报告 %s' % record.get('ticket_id', ''), '',
                 '- 目标：%s' % record.get('goal', ''),
                 '- 分区：%s' % record.get('partition', ''),
                 '- 工作目录：%s' % record.get('cwd', ''),
                 '- DSH 会话：%s' % (record.get('session_id') or '（未关联）'),
                 '- 验收台账：%s' % (record.get('acceptance_id') or '（无）'),
                 '- 独立验收结论：**%s**' % (record.get('verdict') or '未判定'),
                 '- 返工次数：%s/%s' % (record.get('attempts', 0),
                                       record.get('max_attempts', 3)),
                 '']
        if record.get('reasons'):
            lines += ['## 验收理由', '']
            lines += ['- %s' % reason for reason in (record.get('reasons') or [])[:20]]
            lines.append('')
        claim = (record.get('claim') or '').strip()
        if claim:
            lines += ['## 模型自述（仅留档，不参与判定）', '', claim[:3000], '']
        lines += ['## 返工记录', '']
        log = record.get('rework_log') or []
        if not log:
            lines.append('- 无')
        for item in log:
            lines.append('- 第 %s 轮（%s）：%s' % (
                item.get('attempt'), item.get('kind'),
                '；'.join(str(reason) for reason in (item.get('reasons') or []))[:400]))
        lines.append('')
        return '\n'.join(lines)

    # ------------------------------------------------------------ 可选增强
    def _binding_facts(self):
        if not self.binding:
            return dict(available=False, reason='dsh_binding 未就绪（并行开发中，已降级）')
        facts = dict(available=True)
        for name in ('current', 'discover'):
            method = getattr(self.binding, name, None)
            if not callable(method):
                continue
            try:
                facts[name] = method()
            except Exception as exc:  # noqa: BLE001
                facts[name] = dict(error='%s: %s' % (type(exc).__name__, str(exc)[:160]))
        return facts

    def _catalog_text(self):
        if not self.catalog:
            return ''
        method = getattr(self.catalog, 'summary_text', None)
        if not callable(method):
            return ''
        try:
            return str(method(40) or '')[:3000]
        except Exception:  # noqa: BLE001
            return ''

    def _harness_create(self, goal, conversation_id, cwd, partition):
        if not self.harness:
            return {}
        create = getattr(self.harness, 'create', None)
        if not callable(create):
            return {}
        try:
            try:
                return create(AGENT, conversation_id, goal, cwd, partition=partition) or {}
            except TypeError:
                # 队友 C 的新签名还没落地：退回旧 create(agent, conversation, goal, cwd)。
                return create(AGENT, conversation_id, goal, cwd) or {}
        except Exception as exc:  # noqa: BLE001
            return dict(harness_error='%s: %s' % (type(exc).__name__, str(exc)[:160]))

    def _harness_sync(self, name, *args, **kwargs):
        method = getattr(self.harness, name, None) if self.harness else None
        if not callable(method):
            return dict(available=False)
        try:
            return dict(available=True, value=method(*args, **kwargs))
        except Exception as exc:  # noqa: BLE001
            return dict(available=True, error='%s: %s' % (type(exc).__name__, str(exc)[:160]))
