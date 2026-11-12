# -*- coding: utf-8 -*-
"""dsh_gateway 新建会话 / 投递 / 归属映射 的契约测试（不连真机，用假 RPC）。"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent import dsh_gateway as gw  # noqa: E402


class FakeGateway(gw.DshGateway):
    """把 rpc 换成可编排的假实现，其余逻辑照跑。"""

    def __init__(self, create_result=None, create_error=None, prompt_error=None, prompt_result=None):
        super().__init__(timeout=1.0)
        self.calls = []
        self.create_result = create_result if create_result is not None else dict(
            sessionId='session-abc', agentPreset='standard')
        self.create_error = create_error
        self.prompt_error = prompt_error
        self.prompt_result = prompt_result if prompt_result is not None else dict(accepted=True)

    def rpc(self, method, args=None, timeout=None, _retry=True):
        self.calls.append((method, dict(args or {})))
        if method == 'session/create':
            if self.create_error:
                raise self.create_error
            return dict(self.create_result)
        if method == 'session/prompt':
            if self.prompt_error:
                raise self.prompt_error
            return dict(self.prompt_result)
        raise AssertionError('没预料到的 RPC：' + method)

    def resolve_session_id(self, session_id):
        return str(session_id)

    def ensure_full_access(self,session_id):
        self.permission_checked=session_id
        return dict(ok=True,currentValue='full-access')


class GatewayToolSurfaceTests(unittest.TestCase):
    """"在 DSH 里开个新对话"必须是模型看得见的工具动作。

    以前 dsh_gateway 只有 status/session/prompt，模型没有建会话的手段，
    只好退回 computer 去 DSH 窗口里点按钮，结果卡在"窗口没获得焦点"。
    """

    def test_self_agent_schema_exposes_create_and_start(self):
        from core.self_agent import TOOLS
        spec = next(t for t in TOOLS if t['function']['name'] == 'dsh_gateway')
        actions = spec['function']['parameters']['properties']['action']['enum']
        for action in ('create', 'start', 'prompt'):
            self.assertIn(action, actions)
        # 描述里要明确劝退桌面点击这条路
        self.assertIn('computer', spec['function']['description'])

    def test_mcp_contract_schema_exposes_create_and_start(self):
        from core.mcp_contract import self_tools
        spec = next(t for t in self_tools() if t['name'] == 'dsh_gateway')
        actions = spec['inputSchema']['properties']['action']['enum']
        for action in ('create', 'start', 'prompt'):
            self.assertIn(action, actions)


class SessionCreateTests(unittest.TestCase):
    def setUp(self):
        gw._OWNED.clear()
        gw._OWNERS.clear()

    def tearDown(self):
        # 归属是模块级状态；不清理会污染其它测试（dsh_link 会以为归属在别处）。
        gw._OWNED.clear()
        gw._OWNERS.clear()

    def test_create_session_returns_id_and_claims_ownership(self):
        client = FakeGateway()
        result = client.create_session(conversation_id='conv-1')
        self.assertTrue(result['ok'])
        self.assertEqual(result['session_id'], 'session-abc')
        self.assertEqual(result['agent_preset'], 'standard')
        self.assertEqual(client.permission_checked,'session-abc')
        self.assertIn('session-abc', gw.owned_sessions())
        self.assertEqual(gw.owner_of('session-abc'), 'conv-1')
        self.assertEqual(client.calls[0][0], 'session/create')
        self.assertEqual(client.calls[0][1]['request']['clientTimeZone'], 'Asia/Shanghai')

    def test_missing_session_id_is_an_error_not_a_fake_success(self):
        client = FakeGateway(create_result=dict(agentPreset='standard'))
        with self.assertRaisesRegex(gw.GatewayError, '没有返回会话编号'):
            client.create_session()

    def test_create_error_propagates(self):
        client = FakeGateway(create_error=gw.GatewayError('连接已断开'))
        with self.assertRaisesRegex(gw.GatewayError, '连接已断开'):
            client.create_session()

    def test_start_task_creates_then_prompts(self):
        client = FakeGateway()
        result = client.start_task('把首页按钮改成蓝色', conversation_id='conv-2')
        self.assertTrue(result['ok'])
        self.assertEqual([row[0] for row in client.calls], ['session/create', 'session/prompt'])
        payload = client.calls[1][1]['request']
        self.assertEqual(payload['sessionId'], 'session-abc')
        self.assertEqual(payload['content'][0]['text'], '把首页按钮改成蓝色')
        self.assertEqual(payload['mode'], 'queue')
        self.assertEqual(gw.owner_of('session-abc'), 'conv-2')

    def test_start_task_reports_honestly_when_prompt_fails(self):
        client = FakeGateway(prompt_error=RuntimeError('目标会话不在列表里'))
        result = client.start_task('干点活')
        self.assertFalse(result['ok'])
        self.assertFalse(result['accepted'])
        self.assertEqual(result['session_id'], 'session-abc')   # 会话确实建了，编号要带回去
        self.assertIn('没有投递成功', result['error'])
        # 建了会话就算归属，仍然不该被别人抢走
        self.assertIn('session-abc', gw.owned_sessions())

    def test_prompt_claims_ownership_only_after_success(self):
        client = FakeGateway()
        client.prompt('session-xyz', '继续')
        self.assertIn('session-xyz', gw.owned_sessions())

        gw._OWNED.clear()
        failing = FakeGateway(prompt_error=RuntimeError('boom'))
        with self.assertRaises(RuntimeError):
            failing.prompt('session-bad', '继续')
        self.assertNotIn('session-bad', gw.owned_sessions())


if __name__ == '__main__':
    unittest.main()
