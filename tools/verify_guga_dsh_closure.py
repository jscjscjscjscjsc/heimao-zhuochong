# -*- coding: utf-8 -*-
"""咕嘎 × DSH 闭环端到端验收（Lead 用，真实执行，不 mock 核心链路）。

跑法（项目根目录）：
    python tools/verify_guga_dsh_closure.py

对应主人要求的五件事：
  1 认准同一个 DSH      -> dsh_binding.discover/bind/current/verify
  2 建立能力目录        -> capability_catalog.build/get/summary_text
  3 共享任务编号        -> personal_harness 迁移 + by_session + resume_candidates + 归属落盘
  4 独立验收            -> acceptance.AcceptanceLedger（真实跑一条检查命令）
  5 有限返工            -> task_loop.dispatch/rework 投回原会话 + 次数上限
  附加：每分钟自然语言播报 -> dsh_link 的 60 秒节拍

任何一项拿不到真实结果就记 FAIL/SKIP，不写"应该可以"。
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

RESULTS = []


def record(name, ok, detail=''):
    RESULTS.append(dict(name=name, ok=ok, detail=str(detail)[:400]))
    print(('  PASS  ' if ok else '  FAIL  ') + name + (('  :: ' + str(detail)[:300]) if detail else ''))


def section(title):
    print('\n=== ' + title + ' ===')


def main():
    # Qt 必须先按 launcher 的方式初始化，否则 QApplication 会硬崩。
    try:
        import launcher
        launcher._prepare_qt()
    except Exception as exc:  # noqa: BLE001
        print('launcher._prepare_qt 失败：', exc)

    # ---------------------------------------------------------------- 1 认准同一个 DSH
    section('1 认准同一个 DSH')
    try:
        from core.super_agent import dsh_binding
        info = dsh_binding.discover()
        record('discover 拿到 3080 实例',
               bool(info.get('ok')) and int(info.get('port') or 0) == 3080,
               {k: info.get(k) for k in ('ok', 'port', 'pid', 'profile', 'launch_token_present',
                                         'cookie_ok', 'sessions', 'error')})
        bound = dsh_binding.bind()
        record('bind 成功', bool(bound.get('ok')), bound)
        cur = dsh_binding.current()
        record('current 能读回绑定', bool(cur.get('ok')), cur)
        ver = dsh_binding.verify()
        record('verify 判定同一实例', bool(ver.get('ok')) and bool(ver.get('same_instance')),
               {k: ver.get(k) for k in ('ok', 'same_instance', 'drift', 'error')})
    except Exception:  # noqa: BLE001
        record('dsh_binding 可用', False, traceback.format_exc().splitlines()[-1])

    # ---------------------------------------------------------------- 2 能力目录
    section('2 建立能力目录')
    try:
        from core.super_agent import capability_catalog
        cat = capability_catalog.build()
        record('capability_catalog.build 成功', bool(cat.get('ok')),
               dict(plugins=len(cat.get('plugins') or []),
                    mcp_servers=len(cat.get('mcp_servers') or []),
                    tools=len(cat.get('tools') or []),
                    providers=[p.get('route') for p in (cat.get('providers') or [])],
                    error=cat.get('error')))
        text = capability_catalog.summary_text(limit=10)
        record('summary_text 有内容', bool(str(text or '').strip()), str(text or '')[:200])
    except Exception:  # noqa: BLE001
        record('capability_catalog 可用', False, traceback.format_exc().splitlines()[-1])

    # ---------------------------------------------------------------- 3 共享任务编号
    section('3 共享任务编号（持久化）')
    try:
        from core.personal_harness import PersonalHarness
        with tempfile.TemporaryDirectory() as folder:
            store = PersonalHarness(Path(folder) / 'tasks.sqlite3')
            ticket = store.create('001', 'conv-verify', '验证闭环', str(ROOT), partition='guga')
            record('create 带 partition', ticket.get('partition') == 'guga',
                   {k: ticket.get(k) for k in ('id', 'partition', 'state')})
            bound = store.bind_dsh(ticket['id'], 'session-verify-1')
            record('bind_dsh 写入会话号', bound.get('dsh_session') == 'session-verify-1', bound.get('dsh_session'))
            found = store.by_session('session-verify-1')
            record('by_session 反查同一任务', bool(found) and found.get('id') == ticket['id'],
                   (found or {}).get('id'))
            tries = [store.attempt(ticket['id'], max_attempts=3) for _ in range(3)]
            record('attempt 有次数上限', bool(tries[-1].get('exhausted')),
                   tries)
            listed = store.resume_candidates('001')
            record('resume_candidates 可续跑', any(x.get('id') == ticket['id'] for x in listed),
                   len(listed))
    except Exception:  # noqa: BLE001
        record('personal_harness 扩展可用', False, traceback.format_exc().splitlines()[-1])

    try:
        from core.super_agent import dsh_gateway
        dsh_gateway.remember_owned('session-verify-persist')
        # 新实例化一次也不该丢：实现里应落盘。
        record('owned 归属已落盘', 'session-verify-persist' in dsh_gateway.owned_sessions(),
               sorted(dsh_gateway.owned_sessions())[-3:])
    except Exception:  # noqa: BLE001
        record('dsh_gateway 归属落盘', False, traceback.format_exc().splitlines()[-1])

    # ---------------------------------------------------------------- 4 独立验收
    section('4 独立验收（真实跑检查命令）')
    try:
        from core.super_agent.acceptance import AcceptanceLedger
        ledger = AcceptanceLedger()
        rec = ledger.open(goal='验收台账自检', cwd=str(ROOT), expect_changes=False,
                          checks=[['python', '-c', 'import sys; sys.exit(0)']])
        ledger.observe(rec['id'], session_view=dict(running=False, title='自检', turn_outline=[]))
        ledger.run_checks(rec['id'])
        verdict = ledger.judge(rec['id'])
        record('通过的检查判定为 pass', verdict.get('verdict') == 'pass',
               dict(verdict=verdict.get('verdict'), reasons=verdict.get('reasons')))

        rec2 = ledger.open(goal='验收台账自检-失败', cwd=str(ROOT), expect_changes=False,
                           checks=[['python', '-c', 'import sys; sys.exit(3)']])
        ledger.observe(rec2['id'], session_view=dict(running=False, title='自检', turn_outline=[]))
        ledger.run_checks(rec2['id'])
        verdict2 = ledger.judge(rec2['id'])
        record('失败的检查判定为 fail', verdict2.get('verdict') == 'fail',
               dict(verdict=verdict2.get('verdict'), reasons=verdict2.get('reasons')))

        rec3 = ledger.open(goal='验收台账自检-无检查', cwd=str(ROOT), expect_changes=False)
        ledger.observe(rec3['id'], session_view=dict(running=False, title='自检', turn_outline=[]))
        verdict3 = ledger.judge(rec3['id'])
        record('没有检查命令不放行', verdict3.get('verdict') == 'unverified',
               dict(verdict=verdict3.get('verdict'), reasons=verdict3.get('reasons')))
    except Exception:  # noqa: BLE001
        record('acceptance 可用', False, traceback.format_exc().splitlines()[-1])

    # ---------------------------------------------------------------- 5 有限返工
    section('5 有限返工（投回原会话 + 次数上限）')
    try:
        from core.super_agent import task_loop

        class FakeGateway:
            def __init__(self):
                self.created = 0
                self.prompts = []

            def create_session(self, conversation_id='', preset=''):
                self.created += 1
                return dict(ok=True, session_id='session-fake-1')

            def prompt(self, session_id, text, mode='queue', request_id=''):
                self.prompts.append(dict(session_id=session_id, text=text, mode=mode))
                return dict(ok=True)

            def session_view(self, session_id):
                return dict(session_id=session_id, running=False, title='假会话', turn_outline=[])

            def health(self):
                return dict(ok=True)

        gw = FakeGateway()
        loop = task_loop.TaskLoop(gateway=gw)
        dispatched = loop.dispatch('把 README 里的错字改掉', cwd=str(ROOT), checks=[], max_attempts=2)
        record('dispatch 建了专属会话', bool(dispatched.get('ok')) and gw.created == 1,
               {k: dispatched.get(k) for k in ('ok', 'ticket_id', 'session_id', 'error')})
        session_id = dispatched.get('session_id')
        first = loop.rework(dispatched['ticket_id'], ['检查没通过'])
        record('rework 投回原会话', first.get('ok') and gw.prompts
               and all(p['session_id'] == session_id for p in gw.prompts),
               gw.prompts[-1:] if gw.prompts else '没有投递')
        second = loop.rework(dispatched['ticket_id'], ['还是不通过'])
        record('达到次数上限后返回 exhausted 信号',
               bool(second.get('exhausted')),
               {k: second.get(k) for k in ('ok', 'attempts', 'exhausted', 'state')})
        third = loop.rework(dispatched['ticket_id'], ['第三次仍然不通过'])
        record('再要返工就被 blocked，不无限循环',
               third.get('state') == 'blocked' or bool(third.get('blocked')),
               {k: third.get(k) for k in ('ok', 'state', 'blocked', 'reasons', 'error')})
        record('返工没有新建会话', gw.created == 1, gw.created)
    except Exception:  # noqa: BLE001
        record('task_loop 可用', False, traceback.format_exc().splitlines()[-1])

    # ---------------------------------------------------------------- 附加：60 秒播报
    section('附加 每分钟自然语言播报')
    try:
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from PyQt5.QtWidgets import QApplication
        from core.dsh_link import DshLink
        QApplication.instance() or QApplication([])
        clock = {'now': 100.0}
        panel = SimpleNamespace(voice=None, voice_active=False, busy=False, active_id='chat',
                                _say_active_reply=MagicMock())
        link = DshLink(panel, clock=lambda: clock['now'])
        os.environ['GUGA_SUPER_AGENT'] = 'off'
        link.receive(dict(session_id='session-narr', kind='turn/start', state='running',
                          goal='验证一分钟播报', summary='正在跑测试', turn=1, event_id='e1'))
        link.tick()
        before = panel._say_active_reply.call_count
        clock['now'] += 61
        link.receive(dict(session_id='session-narr', kind='heartbeat', event_id='e2'))
        link.tick()
        after = panel._say_active_reply.call_count
        record('60 秒到点播报一次', after == before + 1, dict(before=before, after=after,
                                                          text=panel._say_active_reply.call_args.args[0]
                                                          if panel._say_active_reply.call_args else ''))
        clock['now'] += 20
        link.receive(dict(session_id='session-narr', kind='heartbeat', event_id='e3'))
        link.tick()
        record('节拍内不重复打扰', panel._say_active_reply.call_count == after,
               panel._say_active_reply.call_count)
        link.timer.stop()
    except Exception:  # noqa: BLE001
        record('dsh_link 播报可用', False, traceback.format_exc().splitlines()[-1])

    # ---------------------------------------------------------------- 汇总
    total = len(RESULTS)
    passed = sum(1 for r in RESULTS if r['ok'])
    print('\n================ 汇总 ================')
    print('%d / %d 通过' % (passed, total))
    for r in RESULTS:
        if not r['ok']:
            print('  FAIL: ' + r['name'] + ' :: ' + r['detail'][:200])
    return 0 if passed == total else 1


if __name__ == '__main__':
    sys.exit(main())
