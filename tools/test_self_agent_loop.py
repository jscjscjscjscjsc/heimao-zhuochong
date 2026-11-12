# -*- coding: utf-8 -*-
"""B3 集成测试：用假模型把 SelfAgent 的主循环跑通。

验的是真循环里的粘合（不是我另写一段逻辑）：
计划 → 工具回执 → **按验收条件自动推进** → 收尾。
没有真模型、没有真桌面：`_request` 和 bridge 都是假的。
"""
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 不建 QApplication：这台机器上创建 QApplication（offscreen 或默认平台）会挂住。
# 我们测的是主循环的胶水，不测 Qt 事件循环，所以信号用桩代替。
from core import self_agent as sa  # noqa: E402


class _Signal:
    """替代 pyqtSignal：只记录 emit 了什么。"""

    def __init__(self):
        self.emitted = []

    def emit(self, *args):
        self.emitted.append(args)


class FakeBridge:
    """任何工具都返回同一条"成功 + 带验收关键词"的回执。"""

    def __init__(self, payload=None):
        self.payload = payload or dict(ok=True, result=dict(ok=True, message='网络设置已打开'))
        self.calls = []

    def call(self, name, arguments, cancel):
        self.calls.append((name, arguments))
        return dict(self.payload)


class FakeJournal:
    def __init__(self):
        import tempfile
        self.data = {'workspace': tempfile.mkdtemp(prefix='sa-loop-')}
        self.updates = []

    def start(self, session_id, prompt):
        self.data.update(session=session_id, prompt=prompt)
        return 'task-1'

    def update(self, **kwargs):
        self.updates.append(kwargs)
        self.data.update(kwargs)

    def latest(self, session_id):
        return None

    def event(self, *args, **kwargs):
        return None


def make_agent(replies, bridge=None):
    agent = sa.SelfAgent.__new__(sa.SelfAgent)
    # 不调用 QObject.__init__：不建 QApplication 也能测循环，信号用桩
    agent.progress = _Signal()
    agent.activity = _Signal()
    agent.completed = _Signal()
    agent.bridge = bridge or FakeBridge()
    agent.config = dict(task_budget_seconds=60, api_key='fake')
    agent.cancel_event = sa.threading.Event()
    agent.worker = None
    agent.steering = sa.queue.Queue(maxsize=4)
    agent.journal = FakeJournal()
    agent.computer = type('C', (), {'execute': lambda self, a, c: {}})()
    agent.wechat = type('W', (), {'execute': lambda self, a, c: {'ok': True}})()
    agent.mcp = None
    agent._routine_request = False
    agent._guided_local = False
    scripted = list(replies)

    def fake_request(messages, session_id):
        agent.sent_messages.append([dict(m) for m in messages])
        if not scripted:
            raise AssertionError('假模型被问了超过脚本次数（循环没收住）')
        return scripted.pop(0)

    agent.sent_messages = []
    agent._request = fake_request
    return agent


def finish(agent, conv, prompt):
    """跑一轮 _run，并从 completed 信号里取结果（_run 自己不返回）。"""
    agent._run(conv, [], prompt, agent.cancel_event)
    if agent.completed.emitted:
        return agent.completed.emitted[-1][0] or {}
    return {}


class LoopIntegrationTests(unittest.TestCase):
    def setUp(self):
        os.environ.pop('GUGA_HARNESS', None)

    def test_plan_then_tool_auto_advances_then_finishes(self):
        agent = make_agent([
            {'role': 'assistant', 'content': '1. 打开设置页｜验收：网络设置'},
            {'role': 'assistant', 'tool_calls': [
                {'id': 'c1', 'function': {'name': 'workspace',
                                          'arguments': json.dumps({'action': 'list_archived'})}}]},
            {'role': 'assistant', 'content': '好了，设置页已经打开。'},
        ])
        result = finish(agent, 'conv-1', '帮我打开网络设置')
        self.assertTrue(result.get('ok'), result)
        # 第二轮就收到了"计划块"，说明计划真的注入了
        second = '\n'.join(str(m.get('content')) for m in agent.sent_messages[1])
        self.assertIn('当前任务计划', second)
        # 工具调完之后，第三步的消息里出现了"自动判定完成"
        third = '\n'.join(str(m.get('content')) for m in agent.sent_messages[2])
        self.assertIn('自动判定完成', third)
        self.assertIn('网络设置', third)

    def test_failed_tool_blocks_and_stops_pretending(self):
        agent = make_agent([
            {'role': 'assistant', 'content': '1. 打开设置页｜验收：窗口标题是网络设置'},
            {'role': 'assistant', 'tool_calls': [
                {'id': 'c1', 'function': {'name': 'workspace',
                                          'arguments': json.dumps({'action': 'list_archived'})}}]},
            {'role': 'assistant', 'content': '打不开，卡在权限上。'},
        ], bridge=FakeBridge(dict(ok=False, error='窗口没找到')))
        result = finish(agent, 'conv-2', '帮我打开网络设置')
        # 失败要如实报，不能自己宣布完成
        self.assertFalse(result.get('ok'))
        blob = json.dumps(result, ensure_ascii=False)
        self.assertTrue('窗口没找到' in blob or '没有通过完成校验' in blob, blob[:400])

    def test_harness_off_restores_old_behaviour(self):
        os.environ['GUGA_HARNESS'] = 'off'
        try:
            agent = make_agent([
                {'role': 'assistant', 'content': '直接回答了，没有计划。'},
            ])
            agent._run('conv-3', [], '你好', agent.cancel_event)
            first = '\n'.join(str(m.get('content')) for m in agent.sent_messages[0])
            self.assertNotIn('动手前先给计划', first)
        finally:
            os.environ.pop('GUGA_HARNESS', None)


if __name__ == '__main__':
    unittest.main()
