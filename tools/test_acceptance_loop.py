"""DSH 任务独立验收：检查命令的退出码说了算，模型自述只留档。"""
import sys
import tempfile
import unittest
from pathlib import Path

from core.self_agent import TOOLS
from core.super_agent import task_loop


class AcceptanceLoopTests(unittest.TestCase):
    def setUp(self):
        self._old_root = task_loop.RUNTIME
        self._tmp = tempfile.TemporaryDirectory()
        task_loop.RUNTIME = Path(self._tmp.name)
        self.addCleanup(self._restore)

    def _restore(self):
        task_loop.RUNTIME = self._old_root
        self._tmp.cleanup()

    def _judge(self, check):
        loop = task_loop.TaskLoop(gateway=None)
        ticket = 'test-' + Path(self._tmp.name).name
        loop.attach(ticket_id=ticket, goal='写文件', session_id='sess-' + ticket,
                    cwd=self._tmp.name, checks=[check], partition='guga',
                    harness_ticket='', claim='自述完成')
        view = dict(running=False, title='[咕嘎] test',
                    turn_outline=[dict(prompt='x', response='自述完成')])
        return loop.accept_view(ticket, view)

    def test_failing_check_is_not_a_pass(self):
        result = self._judge([sys.executable, '-c', 'import sys; sys.exit(1)'])
        self.assertEqual(result['verdict'], 'fail')
        self.assertFalse(result['passed'])
        self.assertTrue(any('退出码' in str(reason) for reason in result['reasons']))

    def test_passing_check_is_a_pass(self):
        result = self._judge([sys.executable, '-c', 'print(1)'])
        self.assertEqual(result['verdict'], 'pass')
        self.assertTrue(result['passed'])

    def test_dsh_task_tool_accepts_check_cmd(self):
        tool = next(item for item in TOOLS if item['function']['name'] == 'dsh_task')
        self.assertIn('check_cmd', tool['function']['parameters']['properties'])


if __name__ == '__main__':
    unittest.main()
