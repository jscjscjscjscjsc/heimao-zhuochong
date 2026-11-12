# -*- coding: utf-8 -*-
"""工具调用型工作流：只验静态结构，不执行任何动作。"""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent import tool_workflow as tw  # noqa: E402


def spec_one(tool='browser', args=None, **extra):
    body = dict(slug='demo-flow', name='演示流程',
                steps=[dict(do=tool, args=args if args is not None else {'action': 'session_start'})])
    body.update(extra)
    return body


class ValidateTests(unittest.TestCase):
    def test_accepts_a_valid_flow(self):
        flow = tw.validate(spec_one())
        self.assertEqual(flow['slug'], 'demo-flow')
        self.assertEqual(flow['steps'][0]['do'], 'browser')
        self.assertEqual(flow['steps'][0]['index'], 1)

    def test_unknown_tool_is_rejected_not_ignored(self):
        with self.assertRaises(tw.ToolWorkflowError):
            tw.validate(spec_one(tool='不存在的工具'))

    def test_missing_required_argument_is_rejected(self):
        with self.assertRaises(tw.ToolWorkflowError):
            tw.validate(spec_one(args={}))

    def test_empty_steps_is_rejected(self):
        with self.assertRaises(tw.ToolWorkflowError):
            tw.validate(dict(slug='x', name='x', steps=[]))

    def test_bad_slug_is_rejected(self):
        with self.assertRaises(tw.ToolWorkflowError):
            tw.validate(dict(slug='../etc', name='x', steps=[dict(do='browser', args={'action': 'session_start'})]))

    def test_confirm_flag_is_preserved(self):
        flow = tw.validate(dict(slug='gate', name='闸门流程',
                                steps=[dict(do='pet_say', args={'text': '停'},
                                            confirm=True)]))
        self.assertTrue(flow['steps'][0]['confirm'])


class DryRunTests(unittest.TestCase):
    def test_dry_run_reports_gates_and_tools(self):
        report = tw.dry_run(dict(slug='gate', name='闸门流程', steps=[
            dict(do='browser', args={'action': 'session_start'}),
            dict(do='pet_say', args={'text': '停'}, confirm=True),
        ]))
        self.assertTrue(report['ok'])
        self.assertEqual(report['total_steps'], 2)
        self.assertEqual(report['confirm_gates'], [2])
        self.assertIn('browser', report['tools_used'])
        self.assertIn('没有任何动作被执行', report['note'])

    def test_dry_run_never_writes_or_executes(self):
        before = set(tw.DEFAULT_DIR.glob('*.json')) if tw.DEFAULT_DIR.is_dir() else set()
        tw.dry_run(spec_one())
        after = set(tw.DEFAULT_DIR.glob('*.json')) if tw.DEFAULT_DIR.is_dir() else set()
        self.assertEqual(before, after)

    def test_save_and_load_round_trip(self):
        tmp = Path(tempfile.mkdtemp(prefix='tw-'))
        tw.save(spec_one(), root=tmp)
        loaded = tw.load('demo-flow', root=tmp)
        self.assertEqual(loaded['name'], '演示流程')


class RunTests(unittest.TestCase):
    """执行骨架：确认闸必须停、失败必须停、每步都要留回执。"""

    def flow(self, steps):
        return dict(slug='run-demo', name='执行演示', steps=steps)

    def test_runs_every_step_and_records_receipts(self):
        calls = []

        def execute(tool, args):
            calls.append((tool, args))
            return dict(ok=True, tool=tool)

        report = tw.run(self.flow([
            dict(do='browser', args={'action': 'session_start'}),
            dict(do='pet_say', args={'text': '第一步完成'}),
        ]), execute)
        self.assertTrue(report['ok'])
        self.assertEqual(report['stopped'], 'completed')
        self.assertEqual(report['executed_steps'], 2)
        self.assertEqual(report['passed_steps'], 2)
        self.assertEqual(len(calls), 2)
        self.assertEqual(report['receipts'][0]['index'], 1)

    def test_confirm_gate_stops_before_executing(self):
        calls = []

        def execute(tool, args):
            calls.append(tool)
            return dict(ok=True)

        report = tw.run(self.flow([
            dict(do='browser', args={'action': 'session_start'}),
            dict(do='pet_say', args={'text': '发布前停下'}, confirm=True),
            dict(do='pet_say', args={'text': '不该执行到这里'}),
        ]), execute)
        self.assertEqual(report['stopped'], 'confirm_required')
        self.assertEqual(calls, ['browser'])
        self.assertTrue(report['receipts'][-1]['skipped'])
        self.assertEqual(report['executed_steps'], 1)

    def test_confirm_gate_can_be_overridden_explicitly(self):
        calls = []

        def execute(tool, args):
            calls.append(tool)
            return dict(ok=True)

        report = tw.run(self.flow([
            dict(do='pet_say', args={'text': '确认过就走'}, confirm=True),
        ]), execute, allow_confirm=True)
        self.assertEqual(report['stopped'], 'completed')
        self.assertEqual(calls, ['pet_say'])

    def test_failure_stops_instead_of_guessing(self):
        calls = []

        def execute(tool, args):
            calls.append(tool)
            if len(calls) == 2:
                return dict(ok=False, error='窗口没找到')
            return dict(ok=True)

        report = tw.run(self.flow([
            dict(do='browser', args={'action': 'session_start'}),
            dict(do='browser', args={'action': 'snapshot'}),
            dict(do='pet_say', args={'text': '不该到这一步'}),
        ]), execute)
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'step_failed')
        self.assertEqual(len(calls), 2)
        self.assertEqual(report['receipts'][-1]['index'], 2)
        self.assertIn('窗口没找到', report['receipts'][-1]['error'])

    def test_executor_exception_is_a_receipt_not_a_crash(self):
        def execute(tool, args):
            raise RuntimeError('工具炸了')

        report = tw.run(self.flow([dict(do='browser', args={'action': 'session_start'})]),
                        execute)
        self.assertFalse(report['ok'])
        self.assertEqual(report['stopped'], 'step_failed')
        self.assertIn('工具炸了', report['receipts'][0]['error'])

    def test_max_steps_limits_the_walk(self):
        calls = []

        def execute(tool, args):
            calls.append(tool)
            return dict(ok=True)

        tw.run(self.flow([
            dict(do='browser', args={'action': 'session_start'}),
            dict(do='pet_say', args={'text': 'a'}),
            dict(do='pet_say', args={'text': 'b'}),
        ]), execute, max_steps=2)
        self.assertEqual(len(calls), 2)

    def test_start_at_resumes_after_a_gate_instead_of_restarting(self):
        calls = []

        def execute(tool, args):
            calls.append(tool)
            return dict(ok=True)

        report = tw.run(self.flow([
            dict(do='browser', args={'action': 'session_start'}),
            dict(do='pet_say', args={'text': '闸门前'}),
            dict(do='pet_say', args={'text': '闸门后'}, confirm=True),
        ]), execute, start_at=3, allow_confirm=True)
        self.assertEqual(report['started_at_step'], 3)
        self.assertEqual(calls, ['pet_say'])
        self.assertEqual(report['executed_steps'], 1)
        self.assertEqual(report['stopped'], 'completed')

    def test_ledger_appends_one_json_line_per_run(self):
        tmp = Path(tempfile.mkdtemp(prefix='tw-ledger-'))
        ledger = tmp / 'receipts.jsonl'

        def execute(tool, args):
            return dict(ok=True)

        tw.run(self.flow([dict(do='browser', args={'action': 'session_start'})]),
               execute, ledger=ledger)
        tw.run(self.flow([dict(do='browser', args={'action': 'session_start'})]),
               execute, ledger=ledger)
        lines = [line for line in ledger.read_text(encoding='utf-8').splitlines() if line.strip()]
        self.assertEqual(len(lines), 2)
        import json as _json
        row = _json.loads(lines[0])
        self.assertEqual(row['slug'], 'run-demo')
        self.assertEqual(row['executed'], 1)
        self.assertTrue(row['receipts'][0]['do'] == 'browser')


class GuardrailTests(unittest.TestCase):
    """无人值守时先挡住不可逆命令。"""

    def flow(self, command):
        return dict(slug='guard-demo', name='护栏演示', steps=[
            dict(do='local_computer', args={'action': 'run', 'command': command}),
        ])

    def test_destructive_command_is_blocked_before_execution(self):
        calls = []

        def execute(tool, args):
            calls.append((tool, args))
            return dict(ok=True)

        report = tw.run(self.flow('Remove-Item -Recurse -Force C:\\Users\\x'), execute)
        self.assertEqual(report['stopped'], 'guarded')
        self.assertEqual(calls, [], '被拦的步骤绝不能真的执行')
        self.assertIn('安全护栏', report['receipts'][0]['reason'])

    def test_safe_command_still_runs(self):
        calls = []

        def execute(tool, args):
            calls.append(args['command'])
            return dict(ok=True)

        report = tw.run(self.flow("Rename-Item -LiteralPath 'a.mp4' -NewName 'b.mp4'"), execute)
        self.assertEqual(report['stopped'], 'completed')
        self.assertEqual(len(calls), 1)

    def test_guard_can_be_overridden_explicitly(self):
        calls = []

        def execute(tool, args):
            calls.append(args['command'])
            return dict(ok=True)

        report = tw.run(self.flow('shutdown /s'), execute, allow_dangerous=True)
        self.assertEqual(report['stopped'], 'completed')
        self.assertEqual(calls, ['shutdown /s'])

    def test_unsafe_reason_ignores_non_run_actions(self):
        self.assertIsNone(tw.unsafe_reason(dict(do='local_computer',
                                                args={'action': 'list', 'path': 'C:\\'})))
        self.assertIsNone(tw.unsafe_reason(dict(do='browser', args={'action': 'navigate'})))


class RealFlowTests(unittest.TestCase):
    """跑真实的「腾讯会议→改名→B站投稿前」那份流程：结构必须立得住。"""

    def test_tencent_to_bilibili_flow_is_structurally_valid(self):
        flow = tw.load('tencent-meeting-to-bilibili')
        self.assertGreaterEqual(len(flow['steps']), 8)
        tools = {step['do'] for step in flow['steps']}
        for expected in ('browser', 'local_computer', 'media_workflow'):
            self.assertIn(expected, tools)

    def test_last_step_is_a_human_gate_before_publishing(self):
        flow = tw.load('tencent-meeting-to-bilibili')
        self.assertTrue(flow['steps'][-1]['confirm'],
                        '发布是不可逆动作，最后一步必须是人工确认闸门')

    def test_dry_run_of_real_flow_lists_every_tool(self):
        report = tw.dry_run(json_of_real := tw.load('tencent-meeting-to-bilibili'))
        self.assertTrue(report['ok'])
        self.assertEqual(report['total_steps'], len(json_of_real['steps']))
        self.assertEqual(report['confirm_gates'], [len(json_of_real['steps'])])


if __name__ == '__main__':
    unittest.main()
