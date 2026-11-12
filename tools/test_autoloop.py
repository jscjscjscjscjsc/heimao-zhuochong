# -*- coding: utf-8 -*-
"""托管循环的硬约束测试：失败才继续、通过就停、同一原因不空转、预算与轮数有上限。"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent.acceptance import AcceptanceLedger  # noqa: E402
from core.super_agent.autoloop import AutoLoop  # noqa: E402

PY = sys.executable


def git(cwd, *args):
    return subprocess.run(
        ['git', '-c', 'user.email=t@l', '-c', 'user.name=t'] + list(args),
        cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')


class FakeGateway:
    """按脚本返回会话视图；不发任何真实请求。默认每查一次就多一轮，模拟真实推进。"""

    def __init__(self, views=None, never_finish=False):
        self.views = list(views or [])
        self.never_finish = never_finish
        self.turns = 0
        self.prompts = []
        self.created = []

    def start_task(self, text, conversation_id='', mode='queue'):
        self.created.append(text)
        return dict(ok=True, session_id='session-1', accepted=True)

    def prompt(self, session_id, text, mode='queue'):
        self.prompts.append(text)
        return dict(accepted=True)

    def session_view(self, session_id):
        if self.never_finish:
            return dict(running=True, turn_outline=[])
        if self.views:
            return self.views.pop(0)
        self.turns += 1
        return finished(self.turns)


def finished(turns=1):
    return dict(running=False, title='t',
                turn_outline=[dict(turn=i + 1, prompt='p', response='r') for i in range(turns)])


class AutoLoopTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init')
        (self.repo / 'a.txt').write_text('one\n', encoding='utf-8')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'init')
        self.ledger = AcceptanceLedger(root=self.root / 'ledger')

    def tearDown(self):
        self.folder.cleanup()

    def loop(self, gateway, **kwargs):
        params = dict(poll_seconds=0.001, wait_seconds=0.2)
        params.update(kwargs)
        return AutoLoop(ledger=self.ledger, gateway=gateway, **params)

    def test_passes_on_first_round_and_stops(self):
        (self.repo / 'a.txt').write_text('two\n', encoding='utf-8')
        gateway = FakeGateway([finished()])
        report = self.loop(gateway).run(
            goal='改 a.txt', prompt='改成 two', cwd=str(self.repo),
            checks=[[PY, '-c', 'print(1)']])
        self.assertTrue(report['passed'])
        self.assertEqual(report['stopped'], 'passed')
        self.assertEqual(len(report['rounds']), 1)
        self.assertEqual(len(gateway.created), 1)

    def test_fails_once_then_passes(self):
        (self.repo / 'a.txt').write_text('two\n', encoding='utf-8')
        gateway = FakeGateway([finished(1), finished(2)])
        report = self.loop(gateway).run(
            goal='改 a.txt', prompt='改成 two', cwd=str(self.repo),
            checks=[[PY, '-c', 'raise SystemExit(1)']])
        # 第 1 轮检查失败；第 2 轮同样失败 → 同一原因 → 停手
        self.assertFalse(report['passed'])
        self.assertEqual(report['stopped'], 'no_progress')
        self.assertEqual(len(gateway.prompts), 1)      # 只追问过一次
        self.assertIn('具体原因', gateway.prompts[0])

    def test_same_reason_stops_instead_of_spinning(self):
        (self.repo / 'a.txt').write_text('two\n', encoding='utf-8')
        gateway = FakeGateway([finished(1), finished(2), finished(3)])
        report = self.loop(gateway, max_rounds=5).run(
            goal='x', prompt='p', cwd=str(self.repo),
            checks=[[PY, '-c', 'raise SystemExit(1)']])
        self.assertEqual(report['stopped'], 'no_progress')
        self.assertLessEqual(len(report['rounds']), 2)

    def test_rounds_exhausted_is_reported(self):
        gateway = FakeGateway([finished(1), finished(2), finished(3)])
        report = self.loop(gateway, max_rounds=1, max_same_reason=99).run(
            goal='x', prompt='p', cwd=str(self.repo),
            checks=[[PY, '-c', 'raise SystemExit(1)']])
        self.assertEqual(report['stopped'], 'rounds_exhausted')
        self.assertFalse(report['passed'])

    def test_unverified_stops_and_asks_a_human(self):
        gateway = FakeGateway()
        report = self.loop(gateway).run(goal='x', prompt='p', cwd=str(self.repo), checks=[])
        self.assertEqual(report['stopped'], 'unverified')
        self.assertFalse(report['passed'])

    def test_turn_that_never_finishes_is_not_called_done(self):
        gateway = FakeGateway(never_finish=True)
        report = self.loop(gateway).run(
            goal='x', prompt='p', cwd=str(self.repo),
            checks=[[PY, '-c', 'print(1)']])
        self.assertEqual(report['stopped'], 'turn_not_finished')
        self.assertFalse(report['passed'])

    def test_busy_session_is_never_prompted(self):
        gateway = FakeGateway(never_finish=True)
        report = self.loop(gateway, max_rounds=1).run(
            goal='x', prompt='p', cwd=str(self.repo),
            checks=[[PY, '-c', 'print(1)']], session_id='session-busy')
        self.assertEqual(report['stopped'], 'session_busy')
        self.assertEqual(gateway.prompts, [])   # 一条都没投进去

    def test_custom_follow_up_callable_is_used(self):
        (self.repo / 'a.txt').write_text('two\n', encoding='utf-8')
        seen = []

        def follow(verdict):
            seen.append(verdict.get('verdict'))
            return '继续修'

        gateway = FakeGateway()
        report = self.loop(gateway, max_rounds=2, max_same_reason=2, follow_up=follow).run(
            goal='x', prompt='p', cwd=str(self.repo),
            checks=[[PY, '-c', 'raise SystemExit(1)']], session_id='session-1')
        self.assertTrue(seen)
        self.assertEqual(gateway.prompts[0], 'p')          # 第一轮用原始指令
        self.assertEqual(gateway.prompts[-1], '继续修')     # 追问用自定义模板
        self.assertEqual(report['stopped'], 'no_progress')

    def test_budget_exhaustion_is_reported(self):
        gateway = FakeGateway([finished(1), finished(2), finished(3)])
        report = self.loop(gateway, max_rounds=5, max_seconds=0.0).run(
            goal='x', prompt='p', cwd=str(self.repo),
            checks=[[PY, '-c', 'print(1)']])
        self.assertEqual(report['stopped'], 'budget_exhausted')
        self.assertFalse(report['passed'])


if __name__ == '__main__':
    unittest.main()
