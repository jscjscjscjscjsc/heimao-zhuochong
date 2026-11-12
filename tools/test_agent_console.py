"""对话台开源内核（smolagents）的离线检查：转发器、工具目录、开关。"""
import json
import threading
import unittest

from core import agent_console


class _Bridge:
    def __init__(self):
        self.calls = []

    def call(self, name, args, cancel):
        self.calls.append((name, args))
        return {'ok': True, 'result': {'seen': name, 'args': args}}


class ConsoleToolTests(unittest.TestCase):
    def setUp(self):
        self.bridge = _Bridge()
        self.tool = agent_console.GugaTool.build(self.bridge, threading.Event())

    def test_forward_calls_the_same_bridge_as_self_agent(self):
        payload = json.loads(self.tool.forward('app_state', '{"query": "x"}'))
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['result']['seen'], 'app_state')
        self.assertEqual(self.bridge.calls, [('app_state', {'query': 'x'})])

    def test_forward_rejects_invalid_json(self):
        payload = json.loads(self.tool.forward('app_state', 'not-json'))
        self.assertFalse(payload['ok'])
        self.assertEqual(self.bridge.calls, [])

    def test_forward_rejects_non_object_arguments(self):
        payload = json.loads(self.tool.forward('app_state', '[1, 2]'))
        self.assertFalse(payload['ok'])
        self.assertEqual(self.bridge.calls, [])

    def test_forward_reports_bridge_exception_without_raising(self):
        class Boom:
            def call(self, name, args, cancel):
                raise RuntimeError('bridge down')

        tool = agent_console.GugaTool.build(Boom(), threading.Event())
        payload = json.loads(tool.forward('app_state', '{}'))
        self.assertFalse(payload['ok'])
        self.assertIn('bridge down', payload['error'])

    def test_tool_catalog_lists_real_tools(self):
        catalog = agent_console.tool_catalog()
        self.assertIn('app_state', catalog)
        self.assertIn('computer', catalog)
        self.assertIn('参数：', catalog)

    def test_forward_survives_circular_bridge_result(self):
        class Loop:
            def call(self, name, args, cancel):
                payload = {'ok': True, 'name': name}
                payload['loop'] = payload
                return payload

        tool = agent_console.GugaTool.build(Loop(), threading.Event())
        text = tool.forward('app_state', '{}')
        self.assertIn('"ok": true', text)
        self.assertIn('app_state', text)

    def test_backend_is_off_by_default(self):
        self.assertFalse(agent_console.enabled({}))
        self.assertTrue(agent_console.enabled({'console_backend': 'smolagents'}))

    def test_build_context_carries_profile_memory_and_screen(self):
        context = agent_console.build_context({
            '_agent_profile': dict(display_name='咕嘎', agent_id='001', role='管家', specialty='电脑事务'),
            '_memory_context': '用户喜欢简洁回答',
            '_screen_context': {'foreground': '微信'},
            '_workflow': dict(title='打开浏览器', instructions='先开浏览器再输入'),
        })
        self.assertIn('咕嘎', context)
        self.assertIn('用户喜欢简洁回答', context)
        self.assertIn('微信', context)
        self.assertIn('打开浏览器', context)

    def test_build_context_tolerates_empty_handoff(self):
        self.assertEqual(agent_console.build_context(None), '')
        self.assertEqual(agent_console.build_context({}), '')

    def test_cancelled_agent_does_not_start_a_model_request(self):
        import threading
        cancel = threading.Event()
        cancel.set()
        agent = agent_console.ConsoleAgent(None, cancel_event=cancel)
        result = agent.run('hi')
        self.assertFalse(result['ok'])
        self.assertIn('取消', result['error'])
        self.assertEqual(result['steps'], 0)


class ConsoleWorkerTests(unittest.TestCase):
    def _run(self, runner, prompt='hi'):
        from PyQt5.QtCore import Qt
        worker = agent_console.ConsoleWorker(None, runner=runner)
        got = []
        worker.completed.connect(got.append, Qt.DirectConnection)
        worker.start('conv-1', [], prompt)
        worker._thread.join(10)
        return got

    def test_start_emits_self_agent_compatible_result(self):
        got = self._run(lambda prompt: dict(ok=True, text='ok', steps=2))
        self.assertEqual(len(got), 1)
        payload = got[0]
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['text'], 'ok')
        self.assertEqual(payload['conversation_id'], 'conv-1')
        self.assertEqual(payload['kind'], 'local')
        self.assertEqual(payload['backend'], 'smolagents')
        self.assertFalse(payload['needs_input'])

    def test_runner_failure_is_reported_not_raised(self):
        def boom(prompt):
            raise RuntimeError('model down')

        got = self._run(boom)
        self.assertEqual(len(got), 1)
        self.assertFalse(got[0]['ok'])
        self.assertIn('model down', got[0]['error'])

    def test_worker_exposes_self_agent_surface(self):
        worker = agent_console.ConsoleWorker(None, runner=lambda prompt: dict(ok=True, text='x'))
        self.assertTrue(hasattr(worker, 'cancel_event'))
        self.assertIsNone(worker.journal.latest('conv-1'))
        self.assertEqual(worker.journal.data['events'], [])
        worker.cancel()
        self.assertTrue(worker.cancel_event.is_set())

    def test_events_are_published_to_journal(self):
        worker = agent_console.ConsoleWorker(None, runner=lambda prompt: dict(ok=True, text='x'))
        worker._conversation_id = 'conv-1'
        worker._publish_tool_call('app_state', {'query': 'x'}, {'ok': True, 'result': {}})
        event = worker.journal.data['events'][0]
        self.assertEqual(event['name'], 'app_state')
        self.assertEqual(event['arguments'], {'query': 'x'})
        self.assertTrue(event['result']['ok'])

    def test_watchdog_returns_before_a_hung_run(self):
        import time
        from PyQt5.QtCore import Qt

        def slow(prompt):
            time.sleep(3)
            return dict(ok=True, text='late')

        worker = agent_console.ConsoleWorker(None, runner=slow, timeout_seconds=0.2)
        got = []
        worker.completed.connect(got.append, Qt.DirectConnection)
        worker.start('conv-1', [], 'hi')
        time.sleep(0.8)
        self.assertEqual(len(got), 1)
        self.assertFalse(got[0]['ok'])
        self.assertIn('超过', got[0]['error'])


if __name__ == '__main__':
    unittest.main()
