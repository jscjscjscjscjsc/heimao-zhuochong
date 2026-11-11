# -*- coding: utf-8 -*-
"""编码通道：拉起→轮询→终态。用假 gateway，不花模型调用。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import coding_agent as ca  # noqa: E402


class FakeGateway:
    def __init__(self, session_id='session-fake', views=None, create_result=None, on_view=None):
        self.session_id = session_id
        self.views = list(views or [])
        self.create_result = create_result
        self.prompts = []
        self.created = 0
        self.viewed = 0
        self.on_view = on_view

    def create_session(self):
        self.created += 1
        if self.create_result is not None:
            return self.create_result
        return dict(ok=True, session_id=self.session_id)

    def prompt(self, session_id, text, mode='queue'):
        self.prompts.append((session_id, text, mode))
        return dict(ok=True)

    def session_view(self, session_id):
        self.viewed += 1
        if self.on_view:
            self.on_view(self.viewed)
        if self.views:
            return self.views.pop(0)
        return dict(running=False, last_answer='做完了')


class CompletionSignalTests(unittest.TestCase):
    """会话状态不可靠（任务做完了仍 running=True），所以用完成文件当判据。"""

    def _agent(self, folder=None, signal=None, payload=None, check_cmd=None):
        """跑一轮；完成文件在**轮内**写出来（上一轮留下的旧文件不算数）。"""
        import json as _json
        def write_signal(n):
            if signal and n == 1:
                (Path(folder) / signal).write_text(
                    _json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        ticks = iter(range(0, 1000000, 1))
        gateway = FakeGateway(views=[dict(running=True)] * 200, on_view=write_signal)
        return ca.CodingAgent(gateway=gateway, sleep=lambda _s: None,
                              timeout=30, clock=lambda: next(ticks))

    def test_done_json_finishes_the_run_with_evidence(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-done-'))
        agent = self._agent(folder, 'DONE.json',
                            dict(summary='把 add 实现了', ok=True,
                                 checks=[dict(cmd='pytest', exit=0)]))
        report = agent.run('实现 add', str(folder))
        self.assertTrue(report['ok'])
        self.assertEqual(report['stopped'], 'finished')
        self.assertEqual(report['signal'], 'DONE.json')
        self.assertIn('add', report['answer'])

    def test_failed_json_reports_failure_not_success(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-failed-'))
        agent = self._agent(folder, 'FAILED.json',
                            dict(reason='测试跑不过', tried=['改了两次']))
        report = agent.run('实现 add', str(folder))
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'failed')
        self.assertIn('测试跑不过', report['reason'])

    def test_done_json_still_has_to_pass_the_check(self):
        """完成文件说做完了，但检查没过 → 按没做完算，不能信文件。"""
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-done-fail-'))
        (folder / 'test_bad.py').write_text('def test_no():\n    assert False\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        agent = self._agent(folder, 'DONE.json', dict(summary='我说做完了', ok=True))
        report = agent.run('实现 add', str(folder), check_cmd='pytest -q')
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'check_failed_after_done')
        self.assertNotEqual(report['check_exit'], 0)
        self.assertIn('按没做完算', report['error'])

    def test_done_json_plus_passing_check_is_trusted(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-done-pass-'))
        (folder / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        agent = self._agent(folder, 'DONE.json', dict(summary='做完了', ok=True))
        report = agent.run('实现 add', str(folder), check_cmd='pytest -q')
        self.assertTrue(report['ok'])
        self.assertEqual(report['stopped'], 'finished')
        self.assertEqual(report['check_exit'], 0)

    def test_stale_done_json_from_last_round_is_ignored(self):
        """上一轮留下的 DONE.json 不能骗过这一轮。"""
        import json as _json
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-stale-'))
        (folder / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        folder.joinpath('DONE.json').write_text(_json.dumps(
            dict(summary='上一轮的', ok=True), ensure_ascii=False), encoding='utf-8')
        ticks = iter(range(0, 1000000, 1))
        gateway = FakeGateway(views=[dict(running=True, as_of_seq=1)] * 200)  # 从不推进
        agent = ca.CodingAgent(gateway=gateway, sleep=lambda _s: None, timeout=9999,
                               clock=lambda: next(ticks), seq_polls=2, stall_polls=5,
                               idle_polls=3)
        report = agent.run('这一轮什么也不做', str(folder), check_cmd='pytest -q')
        self.assertNotEqual(report.get('signal'), 'DONE.json')   # 没吃旧文件
        self.assertFalse(report['ok'])

    def test_half_written_done_json_is_ignored(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-half-'))
        folder.joinpath('DONE.json').write_text('{"summary": "还没写完', encoding='utf-8')
        self.assertIsNone(self._agent()._completion(str(folder)))


class CheckCommandTests(unittest.TestCase):
    """结论只认咕嘎自己跑出来的退出码，不认 DSH 的自述。"""

    def _agent(self):
        ticks = iter(range(0, 1000000, 10))
        gateway = FakeGateway(views=[dict(running=True)] * 500)   # 永远说"在跑"
        # idle_polls 调大，专门测"等不到终态 → 超时"这条路（提前收工另有测试）
        return ca.CodingAgent(gateway=gateway, sleep=lambda _s: None,
                              timeout=30, clock=lambda: next(ticks), idle_polls=99999)

    def test_timeout_but_check_passes_is_reported_as_verified(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-check-pass-'))
        (folder / 'test_one.py').write_text('def test_ok():\n    assert 1 + 1 == 2\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        report = self._agent().run('随便改点', str(folder), check_cmd='pytest -q')
        self.assertTrue(report['ok'])
        self.assertEqual(report['stopped'], 'timeout_but_verified')
        self.assertEqual(report['check_exit'], 0)
        self.assertIn('passed', report['check_tail'] + ' 1 passed')

    def test_timeout_without_check_is_not_claimed_as_done(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-check-none-'))
        report = self._agent().run('随便改点', str(folder))
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'timeout')


class ReceiptAndCancelTests(unittest.TestCase):
    """台账可核对、任务可中止——两件都必须在没有 GUI 的情况下能验。"""

    def test_receipt_row_has_everything_a_human_needs(self):
        import json as _json
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-receipt-'))
        path = folder / 'receipts.jsonl'
        row = ca.write_receipt(dict(task='实现 add', cwd=r'D:\proj', ok=True,
                                    stopped='finished', check_cmd='pytest -q',
                                    check_exit=0, session_id='session-1', elapsed=12.5,
                                    answer='改好了'), path=path)
        self.assertEqual(row['check_exit'], 0)
        line = _json.loads(path.read_text(encoding='utf-8').strip())
        for key in ('ts', 'task', 'cwd', 'ok', 'stopped', 'check_cmd',
                    'check_exit', 'session_id', 'elapsed', 'summary'):
            self.assertIn(key, line)
        # 追加而不是覆盖
        ca.write_receipt(dict(task='第二次', cwd=r'D:\proj', ok=False), path=path)
        self.assertEqual(len(path.read_text(encoding='utf-8').strip().splitlines()), 2)

    def test_cancel_stops_waiting_without_claiming_success(self):
        import threading
        ticks = iter(range(0, 1000000, 1))
        cancel = threading.Event()
        agent = ca.CodingAgent(gateway=FakeGateway(views=[dict(running=True)] * 100),
                               sleep=lambda _s: cancel.set(), timeout=60,
                               clock=lambda: next(ticks))
        report = agent.run('改点东西', r'D:\proj', cancel=cancel)
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'cancelled')
        self.assertIn('不会自动回滚', report['error'])


class EarlyFinishTests(unittest.TestCase):
    """会话永远 running 时，靠"文件不再变 + 检查通过"提前收工，不白等到超时。"""

    def test_idle_project_with_passing_check_finishes_early(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-idle-'))
        (folder / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        # 先让它推进两轮（证明真的动过），再停住
        views = ([dict(running=True, as_of_seq=n) for n in range(1, 3)]
                 + [dict(running=True, as_of_seq=3)] * 200)
        ticks = iter(range(0, 1000000, 1))
        def edit(n):
            if n == 2:
                (folder / 'words.py').write_text('x = 1\n', encoding='utf-8')
        agent = ca.CodingAgent(gateway=FakeGateway(views=views, on_view=edit),
                               sleep=lambda _s: None, timeout=600,
                               clock=lambda: next(ticks), idle_polls=3, seq_polls=999)
        report = agent.run('随便改点', str(folder), check_cmd='pytest -q')
        self.assertTrue(report['ok'])
        self.assertEqual(report['stopped'], 'finished_by_check')
        self.assertEqual(report['check_exit'], 0)
        self.assertLess(report['polls'], 12)   # 没白等到超时

    def test_without_check_cmd_it_still_waits(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-idle2-'))
        (folder / 'x.py').write_text('x = 1\n', encoding='utf-8')
        ticks = iter(range(0, 1000000, 1))
        agent = ca.CodingAgent(gateway=FakeGateway(views=[dict(running=True)] * 200),
                               sleep=lambda _s: None, timeout=5,
                               clock=lambda: next(ticks), idle_polls=2)
        report = agent.run('改点东西', str(folder))
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'timeout')   # 没证据就不许提前收工


    def test_stalled_project_is_reported_not_waited_out(self):
        """文件一直不动、检查也不过：报 stalled，别干等到 20 分钟。"""
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-stall-'))
        (folder / 'test_bad.py').write_text('def test_no():\n    assert False\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        ticks = iter(range(0, 1000000, 1))
        agent = ca.CodingAgent(gateway=FakeGateway(views=[dict(running=True)] * 500),
                               sleep=lambda _s: None, timeout=9999,
                               clock=lambda: next(ticks), idle_polls=3, stall_polls=5)
        report = agent.run('改点东西', str(folder), check_cmd='pytest -q')
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'stalled')
        self.assertIn('没变化', report['error'])
        self.assertNotEqual(report['check_exit'], 0)


class SessionReuseTests(unittest.TestCase):
    """"再改"同一个项目：复用会话，不在 DSH 里堆一串永远不结束的会话。"""

    def test_reusing_a_session_does_not_create_a_new_one(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-reuse-'))
        (folder / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        gateway = FakeGateway(views=[dict(running=False, last_answer='改好了')])
        ticks = iter(range(0, 1000000, 1))
        agent = ca.CodingAgent(gateway=gateway, sleep=lambda _s: None, timeout=60,
                               clock=lambda: next(ticks))
        report = agent.run('再改一处', str(folder), check_cmd='pytest -q',
                           session_id='session-existing')
        self.assertEqual(gateway.created, 0)          # 没有再建会话
        self.assertEqual(report['session_id'], 'session-existing')
        self.assertTrue(report['reused'])
        self.assertEqual(gateway.prompts[0][0], 'session-existing')

    def test_fresh_run_creates_a_session_and_marks_not_reused(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-fresh-'))
        (folder / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        gateway = FakeGateway(views=[dict(running=False)])
        agent = ca.CodingAgent(gateway=gateway, sleep=lambda _s: None, timeout=60)
        report = agent.run('改一处', str(folder), check_cmd='pytest -q')
        self.assertEqual(gateway.created, 1)
        self.assertFalse(report['reused'])


class RetryTests(unittest.TestCase):
    """会话卡住（stalled）时换新会话重试一次，不把一次卡死当成任务失败。"""

    class TwoPhaseGateway(FakeGateway):
        """第一次尝试永远不动（→stalled），第二次立刻给出成品。"""

        def __init__(self, folder):
            super().__init__()
            self.folder = Path(folder)
            self.phase = 0

        def create_session(self):
            self.created += 1
            self.phase += 1
            if self.phase == 2:
                (self.folder / 'test_ok.py').write_text(
                    'def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
            return dict(ok=True, session_id='session-%d' % self.phase)

        def session_view(self, session_id):
            if session_id == 'session-1':
                return dict(running=True)          # 第一次：卡住
            return dict(running=False, last_answer='第二次做完了')

    def test_stalled_first_attempt_retries_with_a_new_session(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-retry-'))
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        gateway = self.TwoPhaseGateway(folder)
        ticks = iter(range(0, 1000000, 1))
        agent = ca.CodingAgent(gateway=gateway, sleep=lambda _s: None, timeout=9999,
                               clock=lambda: next(ticks), idle_polls=2, stall_polls=4)
        report = agent.run('改点东西', str(folder), check_cmd='pytest -q', retries=1)
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['attempts'], 2)
        self.assertEqual(gateway.created, 2)
        self.assertFalse(report['reused'])

    def test_retries_zero_keeps_the_stalled_verdict(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-retry0-'))
        (folder / 'x.py').write_text('x = 1\n', encoding='utf-8')
        ticks = iter(range(0, 1000000, 1))
        agent = ca.CodingAgent(gateway=FakeGateway(views=[dict(running=True)] * 500),
                               sleep=lambda _s: None, timeout=9999,
                               clock=lambda: next(ticks), idle_polls=2, stall_polls=4)
        report = agent.run('改点东西', str(folder), retries=0)
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'stalled')
        self.assertEqual(report['attempts'], 1)


class SessionSeqTests(unittest.TestCase):
    """running 字段不可信（做完也一直 true）；as_of_seq 不涨才是"不再推进"。"""

    def test_growing_then_static_with_check_finishes_quickly(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-seq-'))
        (folder / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        views = ([dict(running=True, as_of_seq=n) for n in range(1, 4)]
                 + [dict(running=True, as_of_seq=4)] * 100)
        ticks = iter(range(0, 1000000, 1))
        def edit(n):
            if n == 2:
                (folder / 'words.py').write_text('x = 1\n', encoding='utf-8')
        agent = ca.CodingAgent(gateway=FakeGateway(views=views, on_view=edit),
                               sleep=lambda _s: None, timeout=9999,
                               clock=lambda: next(ticks), seq_polls=3,
                               stall_polls=999, idle_polls=999)
        report = agent.run('改点东西', str(folder), check_cmd='pytest -q')
        self.assertTrue(report['ok'])
        self.assertEqual(report['stopped'], 'finished_by_check')
        self.assertLessEqual(report['polls'], 10)

    def test_growing_seq_keeps_waiting(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-seq2-'))
        (folder / 'x.py').write_text('x = 1\n', encoding='utf-8')
        views = [dict(running=True, as_of_seq=n) for n in range(1, 400)]
        ticks = iter(range(0, 1000000, 10))
        agent = ca.CodingAgent(gateway=FakeGateway(views=views), sleep=lambda _s: None,
                               timeout=30, clock=lambda: next(ticks), seq_polls=2)
        report = agent.run('改点东西', str(folder))
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'timeout')   # 一直在推进，就不能提前判完

    def test_static_seq_without_check_refuses_to_claim_success(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-seq3-'))
        (folder / 'x.py').write_text('x = 1\n', encoding='utf-8')
        views = [dict(running=True, as_of_seq=3)] * 50
        ticks = iter(range(0, 1000000, 1))
        agent = ca.CodingAgent(gateway=FakeGateway(views=views), sleep=lambda _s: None,
                               timeout=9999, clock=lambda: next(ticks), seq_polls=2,
                               stall_polls=4, idle_polls=999)
        report = agent.run('改点东西', str(folder))
        self.assertFalse(report['ok'])          # 什么都没发生，不许说做完了
        self.assertIn(report['stopped'], ('stalled', 'idle_no_check', 'timeout'))


    def test_static_seq_without_any_progress_never_claims_success(self):
        """刚建好的会话 seq 本来就不涨：不许把它当成"已经做完"。"""
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-seq4-'))
        (folder / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        views = [dict(running=True, as_of_seq=7)] * 100   # 从不推进
        ticks = iter(range(0, 1000000, 1))
        agent = ca.CodingAgent(gateway=FakeGateway(views=views), sleep=lambda _s: None,
                               timeout=9999, clock=lambda: next(ticks), seq_polls=2,
                               idle_polls=2, stall_polls=6)
        report = agent.run('改点东西', str(folder), check_cmd='pytest -q')
        # 检查是过的（老测试本来就能过），但一轮里什么都没发生 → 绝不能报成功
        self.assertFalse(report['ok'], report)
        self.assertIn(report['stopped'], ('stalled', 'idle_no_check', 'timeout'))

    def test_growing_seq_then_static_finishes(self):
        """先看到它推进过，再停下来 → 可以判完成。"""
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-seq5-'))
        (folder / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        views = ([dict(running=True, as_of_seq=n) for n in range(1, 5)]
                 + [dict(running=True, as_of_seq=5)] * 50)
        ticks = iter(range(0, 1000000, 1))
        def edit(n):
            if n == 2:
                (folder / 'words.py').write_text('x = 1\n', encoding='utf-8')
        agent = ca.CodingAgent(gateway=FakeGateway(views=views, on_view=edit),
                               sleep=lambda _s: None,
                               timeout=9999, clock=lambda: next(ticks), seq_polls=3,
                               idle_polls=999, stall_polls=999)
        report = agent.run('改点东西', str(folder), check_cmd='pytest -q')
        self.assertTrue(report['ok'], report)
        self.assertEqual(report['stopped'], 'finished_by_check')


class CheckCommandFormTests(unittest.TestCase):
    """check_cmd 不只支持 python -m：脚本路径和外部命令也要能跑。"""

    def _agent(self):
        return ca.CodingAgent(gateway=FakeGateway())

    def test_python_module_form(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-cmd1-'))
        (folder / 'test_ok.py').write_text('def test_ok():\n    assert 1 == 1\n', encoding='utf-8')
        (folder / 'pytest.ini').write_text('[pytest]\n', encoding='utf-8')
        out = self._agent()._run_check('pytest -q', str(folder))
        self.assertTrue(out['check_ok'], out)
        self.assertEqual(out['check_exit'], 0)

    def test_script_path_form(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-cmd2-'))
        (folder / 'check.py').write_text('import sys\nsys.exit(0)\n', encoding='utf-8')
        out = self._agent()._run_check('check.py', str(folder))
        self.assertTrue(out['check_ok'], out)
        self.assertEqual(out['check_exit'], 0)

    def test_script_failure_is_reported(self):
        import tempfile
        folder = Path(tempfile.mkdtemp(prefix='ca-cmd3-'))
        (folder / 'check.py').write_text('import sys\nsys.exit(3)\n', encoding='utf-8')
        out = self._agent()._run_check('check.py', str(folder))
        self.assertFalse(out['check_ok'])
        self.assertEqual(out['check_exit'], 3)

    def test_empty_command_is_an_error_not_a_pass(self):
        out = self._agent()._run_check('   ', '.')
        self.assertIsNone(out['check_ok'])


class InstructionTests(unittest.TestCase):
    def test_instruction_pins_the_working_directory(self):
        text = ca.CodingAgent(gateway=FakeGateway()).instruction('加一个测试', r'D:\proj')
        self.assertIn(r'D:\proj', text)
        self.assertIn('只在这个目录里读写', text)
        self.assertIn('加一个测试', text)

    def test_instruction_demands_real_verification(self):
        text = ca.CodingAgent(gateway=FakeGateway()).instruction('修 bug', '/tmp/x')
        self.assertIn('运行该项目自己的检查', text)
        self.assertIn('没跑通就说没跑通', text)
        self.assertIn('改了什么', text)


class RunTests(unittest.TestCase):
    def make(self, views=None, **kwargs):
        gateway = FakeGateway(views=views)
        agent = ca.CodingAgent(gateway=gateway, sleep=lambda _s: None, **kwargs)
        return agent, gateway

    def test_happy_path_waits_for_terminal_state(self):
        agent, gateway = self.make(views=[
            dict(running=True, last_answer='正在读代码'),
            dict(running=True, last_answer='改好了，正在跑测试'),
            dict(running=False, last_answer='改了什么：加了 x；验证结果：3 passed'),
        ])
        report = agent.run('给项目加一个 hello 函数', r'D:\proj')
        self.assertTrue(report['ok'])
        self.assertEqual(report['stopped'], 'finished')
        self.assertEqual(report['session_id'], 'session-fake')
        self.assertEqual(len(gateway.prompts), 1)
        self.assertIn('改了什么', report['answer'])
        self.assertEqual(gateway.viewed, 3)

    def test_never_reports_success_before_terminal(self):
        # 一直 running → 命中超时，绝不能返回 ok=True
        ticks = iter(range(0, 10000, 10))
        agent, gateway = self.make(views=[dict(running=True)] * 50,
                                   timeout=5, poll_seconds=0,
                                   clock=lambda: next(ticks))
        report = agent.run('改点东西', r'D:\proj')
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'timeout')
        self.assertIn('仍未收到完成信号', report['error'])

    def test_missing_session_id_is_an_error_not_a_success(self):
        gateway = FakeGateway(create_result=dict(ok=True))
        agent = ca.CodingAgent(gateway=gateway, sleep=lambda _s: None)
        with self.assertRaises(RuntimeError):
            agent.run('x', r'D:\proj')
        self.assertEqual(gateway.prompts, [])

    def test_empty_task_or_cwd_is_refused(self):
        agent, _ = self.make()
        with self.assertRaises(ValueError):
            agent.run('', r'D:\proj')
        with self.assertRaises(ValueError):
            agent.run('做点事', '')

    def test_prompt_carries_instruction_not_raw_task(self):
        agent, gateway = self.make(views=[dict(running=False, last_answer='ok')])
        agent.run('把 README 补一行', r'D:\proj')
        session_id, text, mode = gateway.prompts[0]
        self.assertEqual(session_id, 'session-fake')
        self.assertIn('工作目录：D:\\proj', text)
        self.assertIn('把 README 补一行', text)
        self.assertEqual(mode, 'queue')


if __name__ == '__main__':
    unittest.main()
