# -*- coding: utf-8 -*-
"""agent harness 的判据：计划解析 / 推进 / 卡点 / 开关。纯逻辑，不碰 Qt 和网络。"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import agent_harness as ah  # noqa: E402


class RecoveryTests(unittest.TestCase):
    """卡住之后允许用"新的真实成功回执"恢复，但要留痕，不能假装没失败过。"""

    def make(self):
        harness = ah.Harness(goal='恢复测试')
        harness.begin('1. 打开设置页｜验收：网络设置\n2. 收尾｜验收：网络设置')
        return harness

    def test_success_after_failure_recovers_and_records_it(self):
        harness = self.make()
        harness.record('browser', {'ok': False, 'error': '窗口没找到'})
        self.assertEqual(harness.status()['stuck_at'], 1)
        self.assertIsNone(harness.plan.current_index())   # 卡住时不给下一步
        harness.record('browser', {'ok': True, 'title': '网络设置 - 控制面板'})
        status = harness.status()
        self.assertIsNone(status['stuck_at'])
        self.assertEqual(status['recovered'], [1])
        self.assertEqual(status['completed'], [1])        # 恢复后按证据推进

    def test_success_without_evidence_recovers_but_does_not_advance(self):
        harness = self.make()
        harness.record('browser', {'ok': False, 'error': '超时'})
        harness.record('browser', {'ok': True, 'title': '随便什么页面'})
        status = harness.status()
        self.assertIsNone(status['stuck_at'])
        self.assertEqual(status['recovered'], [1])
        self.assertEqual(status['completed'], [])
        self.assertEqual(status['current'], 1)


class EvidenceMatchTests(unittest.TestCase):
    """验收判据是硬的：验收条件里的实词必须在回执里出现，模型自述不算。"""

    def test_matching_evidence_advances_the_step(self):
        harness = ah.Harness(goal='打开并确认')
        harness.begin('1. 打开设置页｜验收：页面出现"网络设置"\n2. 改代理｜验收：代理已保存')
        ok = harness.record('browser', {'ok': True, 'title': '网络设置 - 控制面板'})
        self.assertTrue(ok)
        self.assertEqual(harness.plan.completed(), [1])
        self.assertEqual(harness.auto_advanced, [1])
        self.assertEqual(harness.plan.current_index(), 2)

    def test_success_without_matching_evidence_does_not_advance(self):
        harness = ah.Harness(goal='打开并确认')
        harness.begin('1. 打开设置页｜验收：页面出现"网络设置"')
        harness.record('browser', {'ok': True, 'title': '随便什么页面'})
        self.assertEqual(harness.plan.completed(), [])
        self.assertEqual(harness.plan.current_index(), 1)

    def test_step_without_check_never_auto_advances(self):
        harness = ah.Harness()
        harness.begin('1. 做点什么\n2. 再做点什么')
        harness.record('browser', {'ok': True, 'title': '随便'})
        self.assertEqual(harness.plan.completed(), [])

    def test_vague_check_words_do_not_count_as_evidence(self):
        # "完成"/"成功"这类虚词不许当证据
        harness = ah.Harness()
        harness.begin('1. 做点什么｜验收：完成')
        harness.record('browser', {'ok': True, 'message': '完成'})
        self.assertEqual(harness.plan.completed(), [])

    def test_auto_advance_is_visible_in_status(self):
        harness = ah.Harness()
        harness.begin('1. 看页面｜验收：出现"登录"')
        harness.record('browser', {'ok': True, 'title': '登录 - 知乎'})
        status = harness.status()
        self.assertIn(1, status['auto_advanced'])
        self.assertEqual(status['current'], None)
        self.assertTrue(status['finished'])


class ParsePlanTests(unittest.TestCase):
    def test_standard_numbered_plan_with_checks(self):
        text = ("PLAN:\n"
                "1. 打开腾讯会议并进入我的录制｜验收：页面出现过录制列表\n"
                "2. 下载最新一条录制｜验收：下载目录多出一个 mp4\n"
                "3. 改名成 ai 全栈开发23｜验收：文件列表里能看到新名字\n")
        steps = ah.parse_plan(text)
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[0]['action'], '打开腾讯会议并进入我的录制')
        self.assertIn('录制列表', steps[0]['check'])
        self.assertEqual([s['index'] for s in steps], [1, 2, 3])

    def test_tolerates_other_numbering_styles(self):
        text = ("步骤1：打开浏览器\n"
                "2）搜索 bilibili\n"
                "- 3) 点第一个结果\n")
        steps = ah.parse_plan(text)
        self.assertEqual(len(steps), 3)
        self.assertEqual(steps[1]['action'], '搜索 bilibili')
        self.assertEqual(steps[2]['action'], '点第一个结果')

    def test_garbage_yields_empty_plan_not_exception(self):
        self.assertEqual(ah.parse_plan('今天天气不错，我们聊聊吧'), [])
        self.assertEqual(ah.parse_plan(''), [])
        self.assertEqual(ah.parse_plan(None), [])

    def test_plan_without_checks_is_still_valid(self):
        steps = ah.parse_plan('1. 先看看窗口\n2. 再点按钮\n')
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]['check'], '')


class PlanStateTests(unittest.TestCase):
    def make(self):
        plan = ah.Plan(goal='演示目标')
        plan.steps = [
            dict(index=1, action='第一步', check='有回执', done=False, evidence=''),
            dict(index=2, action='第二步', check='有回执', done=False, evidence=''),
        ]
        return plan

    def test_marking_done_with_evidence_advances(self):
        plan = self.make()
        self.assertEqual(plan.current_index(), 1)
        plan.mark_done(1, 'bridge.call 返回 ok')
        self.assertEqual(plan.current_index(), 2)
        self.assertEqual(plan.completed(), [1])
        self.assertFalse(plan.finished())
        plan.mark_done(2, '文件已改名')
        self.assertTrue(plan.finished())
        self.assertIsNone(plan.current_index())

    def test_no_evidence_is_refused(self):
        plan = self.make()
        with self.assertRaises(ValueError):
            plan.mark_done(1, '   ')

    def test_prompt_block_marks_done_and_next(self):
        plan = self.make()
        plan.mark_done(1, '看得到页面标题')
        block = plan.to_prompt()
        self.assertIn('✓ 1.', block)
        self.assertIn('下一步只做第 2 步', block)
        self.assertIn('看得到页面标题', block)

    def test_stuck_step_is_reported_and_blocks_current(self):
        plan = self.make()
        plan.stuck(1, '窗口没找到')
        self.assertIsNone(plan.current_index())
        block = plan.to_prompt()
        self.assertIn('✗ 1.', block)
        self.assertIn('不要跳过它假装完成', block)


class HarnessTests(unittest.TestCase):
    def make(self):
        harness = ah.Harness(goal='把录制传上 B 站')
        harness.begin('1. 下载录制｜验收：多一个 mp4\n2. 上传｜验收：投稿页出现文件名')
        return harness

    def test_successful_tool_result_does_not_auto_advance(self):
        harness = self.make()
        ok = harness.record('browser', {'ok': True, 'result': {'ok': True}})
        self.assertTrue(ok)
        # 工具成功 ≠ 这一步完成：还要有证据去 acknowledge
        self.assertEqual(harness.plan.current_index(), 1)

    def test_failed_tool_result_marks_stuck_and_blocks_next_step(self):
        harness = self.make()
        harness.acknowledge('下载目录多了一个 mp4')
        self.assertEqual(harness.plan.current_index(), 2)
        ok = harness.record('browser', {'ok': False, 'error': '窗口没找到'})
        self.assertFalse(ok)
        status = harness.status()
        self.assertEqual(status['stuck_at'], 2)
        self.assertIsNone(status['current'])
        self.assertIn('窗口没找到', status['stuck_reason'])

    def test_nested_failure_is_treated_as_failure(self):
        # ToolBridge 的形态是 {ok:True, result:{ok:False,...}}，不能当成功
        harness = self.make()
        ok = harness.record('applications', {'ok': True, 'result': {'ok': False, 'error': '未找到软件'}})
        self.assertFalse(ok)
        self.assertEqual(harness.status()['failures'], 1)

    def test_status_reports_progress_for_the_ui(self):
        harness = self.make()
        harness.record('browser', {'ok': True})
        harness.acknowledge('页面打开了')
        status = harness.status()
        self.assertEqual(status['total'], 2)
        self.assertEqual(status['completed'], [1])
        self.assertEqual(status['current'], 2)
        self.assertEqual(status['tool_calls'], 1)
        self.assertEqual(status['last_tool'], 'browser')


class SwitchTests(unittest.TestCase):
    def test_enabled_by_default(self):
        saved = os.environ.pop('GUGA_HARNESS', None)
        try:
            self.assertTrue(ah.enabled())
        finally:
            if saved is not None:
                os.environ['GUGA_HARNESS'] = saved

    def test_can_be_turned_off_for_rollback(self):
        saved = os.environ.get('GUGA_HARNESS')
        os.environ['GUGA_HARNESS'] = 'off'
        try:
            self.assertFalse(ah.enabled())
        finally:
            if saved is None:
                os.environ.pop('GUGA_HARNESS', None)
            else:
                os.environ['GUGA_HARNESS'] = saved


if __name__ == '__main__':
    unittest.main()
