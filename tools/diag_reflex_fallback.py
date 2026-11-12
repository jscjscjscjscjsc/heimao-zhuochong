# -*- coding: utf-8 -*-
"""临时诊断：need_model 回退为什么没走到 answer.start。"""
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

from test_reflex import ReflexWiringTests as T  # noqa: E402

T.setUpClass()
try:
    inst = T('test_typed_chat_ambiguous_target_falls_back_to_the_model')
    inst.setUp()
    panel = inst.panel
    ambiguous = dict(ok=False, action_sent=False, verified=False, need_model=True,
                     message='我没看清你指的是哪一块')
    reached = []
    original = panel._reflex_fallback

    def spy(command, result):
        reached.append((dict(command), dict(result)))
        return original(command, result)

    panel._reflex_fallback = spy
    sends = []
    original_send = panel.send

    def send_spy(text, **kwargs):
        value = original_send(text, **kwargs)
        sends.append((text, kwargs, value))
        return value

    panel.send = send_spy
    with patch('core.reflex.execute', return_value=ambiguous), \
            patch.object(panel.answer, 'start') as answer:
        ok = panel.send('点这里')
        print('outer send 之后 busy =', panel.busy, 'inflight =', panel._fast_inflight)
        inst.pump(lambda: len(reached) > 0, seconds=3)
        print('fallback 之后 busy  =', panel.busy, 'inflight =', panel._fast_inflight)
        print('send 返回          =', ok)
        print('_reflex_fallback   =', 'called' if reached else 'NOT called')
        if reached:
            cmd, res = reached[0]
            print('  command.source_text =', repr(cmd.get('source_text'))[:80])
            print('  command._reflex     =', cmd.get('_reflex'))
            print('  result.need_model   =', res.get('need_model'))
        # 继续泵事件循环，看 singleShot 里的 send 是否会来（不 assert，避免半路跳出）
        import time as _t
        end = _t.monotonic() + 3
        while not answer.called and _t.monotonic() < end:
            panel.app.processEvents() if hasattr(panel, 'app') else None
            from PyQt5.QtWidgets import QApplication
            QApplication.processEvents()
            _t.sleep(0.01)
        print('fallback 内部 send  =', sends)
        print('busy=', panel.busy, 'inflight=', panel._fast_inflight,
              'personal_command=', panel._personal_command('点这里', False),
              'run_kind=', getattr(panel, '_run_kind', None))
        try:
            from core.chat_answer import uses_task
            print('uses_task(点这里) =', uses_task('点这里', 'auto'),
                  ' active_route=', getattr(panel, '_active_route', None),
                  ' active_task_text=', repr(getattr(panel, '_active_task_text', ''))[:40])
        except Exception as exc:  # noqa: BLE001
            print('routing probe 失败', exc)
        print('answer.start.called =', answer.called)
        print('_fast_inflight      =', panel._fast_inflight)
        # 直接试一下气泡：如果它抛异常，就会挡住后面的 singleShot
        try:
            panel._show_active_bubble('诊断气泡')
            print('_show_active_bubble = ok')
        except Exception as exc:  # noqa: BLE001
            print('_show_active_bubble 抛异常 =', type(exc).__name__, str(exc)[:160])
finally:
    T.tearDownClass()
