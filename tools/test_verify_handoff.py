"""Independent adversarial acceptance tests for the fast-route handoff change.

Written by the verifier, not the implementer. Each case states the contract it
protects. Covers:
- which boundary sentences stay on the fast path vs. hand off to the agent loop;
- a handed-off sentence is still recognised as work and routes to the local agent;
- "在浏览器的输入框中输入X" normalises the window word out of the field name;
- short multi-step sentences get the full agent budget, and the read timeout
  differs between routine and non-routine model requests;
- DesktopInteraction._activate_browser only moves focus when Guga itself is
  foreground, never when the user is working in another application;
- execute() actually calls _activate_browser and passes its hwnd to _guard.
"""
import os
import sys
import types
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.browser_interaction import parse_step
from core.desktop_interaction import DesktopInteraction
from core.execution_contract import requires_execution
from core.fast_commands import match
from core.self_agent import SelfAgent, needs_full_budget
from core.task_router import choose_task_route


class HandoffRoutingTests(unittest.TestCase):
    def test_browser_then_fill_hands_whole_sentence_to_agent(self):
        text = '打开浏览器，在输入框中输入 咕咕嘎嘎'
        parsed = match(text)
        self.assertFalse(parsed['matched'])
        self.assertEqual(parsed['action'], 'none')
        # The new contract never reports half of the sentence as done.
        self.assertNotIn('remaining', parsed)
        self.assertTrue(requires_execution(text))
        route = choose_task_route(text)
        self.assertEqual(route['executor'], 'local_agent')
        self.assertEqual(route['category'], 'local_task')

    def test_filler_tail_still_uses_the_fast_path(self):
        for text in ('打开记事本，好吗', '打开浏览器，谢谢'):
            with self.subTest(text=text):
                parsed = match(text)
                self.assertTrue(parsed['matched'])
                self.assertEqual(parsed['action'], 'open_app')
        self.assertEqual(match('打开记事本，好吗')['target'], '记事本')

    def test_wechat_find_contact_hands_whole_sentence_to_agent(self):
        text = '打开微信，找到小军'
        self.assertFalse(match(text)['matched'])
        self.assertTrue(requires_execution(text))
        self.assertEqual(choose_task_route(text)['executor'], 'local_agent')

    def test_plain_open_browser_stays_fast(self):
        parsed = match('帮我打开浏览器')
        self.assertTrue(parsed['matched'])
        self.assertEqual(parsed['action'], 'open_app')
        self.assertEqual(parsed['target'], '浏览器')

    def test_second_action_variants_are_not_half_done(self):
        for text in ('打开微信，帮我发消息给小军',
                     '打开浏览器，在输入框中输入咕咕嘎嘎',
                     '打开记事本，写一段文章'):
            with self.subTest(text=text):
                parsed = match(text)
                self.assertFalse(parsed['matched'])
                self.assertTrue(requires_execution(text))


class BrowserFieldNameTests(unittest.TestCase):
    def test_window_word_is_not_a_field_name(self):
        step = parse_step('在浏览器的输入框中输入 咕咕嘎嘎')
        self.assertEqual(step['browser_action'], 'fill_named')
        self.assertEqual(step['target'], '输入框')
        self.assertEqual(step['text'], '咕咕嘎嘎')

    def test_window_word_is_not_a_field_name_without_a_space(self):
        step = parse_step('在浏览器的输入框中输入咕咕嘎嘎')
        self.assertEqual(step['target'], '输入框')
        self.assertEqual(step['text'], '咕咕嘎嘎')

    def test_window_word_is_stripped_from_a_search_box(self):
        # "浏览器的搜索框" describes the window; the field is the search box.
        step = parse_step('在浏览器的搜索框中输入 咕嘎')
        self.assertEqual(step['browser_action'], 'fill_named')
        self.assertEqual(step['target'], '搜索')
        self.assertEqual(step['text'], '咕嘎')

    def test_explicit_field_names_are_preserved(self):
        for text, target in (('在用户名输入框中输入 咕嘎', '用户名'),
                             ('在密码框中填写 abc123', '密码')):
            with self.subTest(text=text):
                step = parse_step(text)
                self.assertEqual(step['browser_action'], 'fill_named')
                self.assertEqual(step['target'], target)


class _FakeResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def close(self):
        pass


class SelfAgentBudgetTests(unittest.TestCase):
    def test_short_multi_step_sentence_needs_full_budget(self):
        self.assertTrue(needs_full_budget('打开浏览器，在输入框中输入 咕咕嘎嘎'))
        self.assertTrue(needs_full_budget('打开微信，找到小军'))

    def test_message_sending_sentence_needs_full_budget(self):
        # "发消息" is the same class of two-step work as "发送"/"输入".
        self.assertTrue(needs_full_budget('打开微信，帮我发消息给小军'))

    def test_writing_sentence_needs_full_budget(self):
        # "写一段/写一篇" is a second action, not a one-shot routine tail.
        self.assertTrue(needs_full_budget('打开记事本，写一段文章'))

    def test_plain_open_sentence_does_not_need_full_budget(self):
        self.assertFalse(needs_full_budget('帮我打开浏览器'))

    def _timeout_for(self, routine):
        # A plain stub is enough: _request only reads config and sets diagnostics.
        agent = SimpleNamespace(config=dict(base_url='https://api.example.com',
                                            api_key='test-key', model='test-model'),
                                _routine_request=routine)
        response = _FakeResponse({'choices': [{'message': {'role': 'assistant',
                                                          'content': 'ok'}}]})
        with patch('core.self_agent.requests.post', return_value=response) as post:
            SelfAgent._request(agent, [{'role': 'user', 'content': 'hi'}], 'session-1')
        self.assertEqual(post.call_count, 1)
        return post.call_args.kwargs.get('timeout')

    def test_non_routine_request_uses_long_read_timeout(self):
        self.assertEqual(self._timeout_for(False), (6, 60))

    def test_routine_request_uses_shorter_read_timeout(self):
        self.assertEqual(self._timeout_for(True), (6, 30))


SW_RESTORE = 9


def _fake_pywin32(foreground=0, foreground_pid=None, iconic=False,
                  focus_lands=True, set_foreground_error=None):
    """Minimal stand-ins for the three pywin32 modules _activate_browser imports."""
    calls = []
    state = {'foreground': foreground}
    pid = os.getpid() if foreground_pid is None else foreground_pid

    def get_foreground_window():
        return state['foreground']

    def get_window_thread_process_id(hwnd):
        return (1, pid)

    def is_iconic(hwnd):
        return iconic

    def show_window(hwnd, command):
        calls.append(('ShowWindow', hwnd, command))

    def set_foreground_window(hwnd):
        calls.append(('SetForegroundWindow', hwnd))
        if set_foreground_error is not None:
            raise set_foreground_error
        if focus_lands:
            state['foreground'] = hwnd

    gui = types.ModuleType('win32gui')
    gui.GetForegroundWindow = get_foreground_window
    gui.IsIconic = is_iconic
    gui.ShowWindow = show_window
    gui.SetForegroundWindow = set_foreground_window
    process = types.ModuleType('win32process')
    process.GetWindowThreadProcessId = get_window_thread_process_id
    con = types.ModuleType('win32con')
    con.SW_RESTORE = SW_RESTORE
    return {'win32gui': gui, 'win32process': process, 'win32con': con}, calls


class ActivateBrowserTests(unittest.TestCase):
    def _activate(self, foreground, windows, foreground_pid=None, iconic=False,
                  focus_lands=True, set_foreground_error=None,
                  monotonic=None, sleep=False):
        modules, calls = _fake_pywin32(foreground=foreground,
                                       foreground_pid=foreground_pid,
                                       iconic=iconic,
                                       focus_lands=focus_lands,
                                       set_foreground_error=set_foreground_error)
        with ExitStack() as stack:
            stack.enter_context(patch.dict(sys.modules, modules))
            list_windows = stack.enter_context(
                patch('core.window_inventory.list_windows', return_value=windows))
            if monotonic is not None:
                stack.enter_context(patch('core.desktop_interaction.time.monotonic',
                                          side_effect=monotonic))
            if sleep:
                stack.enter_context(patch('core.desktop_interaction.time.sleep'))
            interaction = DesktopInteraction(uia=SimpleNamespace())
            result = interaction._activate_browser()
        return result, calls, list_windows

    def test_own_foreground_restores_and_focuses_browser(self):
        windows = [dict(hwnd=222, pid=4444, foreground=False, title='Chrome')]
        result, calls, _ = self._activate(111, windows, foreground_pid=os.getpid(),
                                          iconic=True)
        self.assertEqual(result, 222)
        self.assertIn(('ShowWindow', 222, SW_RESTORE), calls)
        self.assertIn(('SetForegroundWindow', 222), calls)

    def test_other_app_foreground_never_steals_focus(self):
        windows = [dict(hwnd=222, pid=4444, foreground=False, title='Chrome')]
        result, calls, list_windows = self._activate(111, windows,
                                                     foreground_pid=999999)
        self.assertIsNone(result)
        self.assertEqual(calls, [])
        list_windows.assert_not_called()

    def test_no_browser_window_means_no_focus_change(self):
        result, calls, list_windows = self._activate(111, [],
                                                     foreground_pid=os.getpid())
        self.assertIsNone(result)
        self.assertFalse([call for call in calls if call[0] == 'SetForegroundWindow'])
        list_windows.assert_called_once()

    def test_own_pid_window_is_not_an_activation_target(self):
        windows = [dict(hwnd=333, pid=os.getpid(), foreground=False, title='Guga')]
        result, calls, _ = self._activate(111, windows, foreground_pid=os.getpid())
        self.assertIsNone(result)
        self.assertFalse([call for call in calls if call[0] == 'SetForegroundWindow'])

    def test_zero_foreground_returns_none(self):
        result, calls, list_windows = self._activate(0, [dict(hwnd=222, pid=4444)])
        self.assertIsNone(result)
        self.assertEqual(calls, [])
        list_windows.assert_not_called()

    def test_denied_set_foreground_returns_none_without_crashing(self):
        windows = [dict(hwnd=222, pid=4444, foreground=False)]
        result, calls, _ = self._activate(111, windows, foreground_pid=os.getpid(),
                                          set_foreground_error=RuntimeError('denied'))
        self.assertIsNone(result)
        self.assertIn(('SetForegroundWindow', 222), calls)

    def test_focus_that_never_lands_returns_none_after_bounded_poll(self):
        windows = [dict(hwnd=222, pid=4444, foreground=False)]
        result, calls, _ = self._activate(111, windows, foreground_pid=os.getpid(),
                                          focus_lands=False,
                                          monotonic=[0.0, 0.1, 2.0], sleep=True)
        self.assertIsNone(result)
        self.assertIn(('SetForegroundWindow', 222), calls)


class ExecuteWiringTests(unittest.TestCase):
    def test_execute_activates_browser_and_passes_hwnd_to_guard(self):
        interaction = DesktopInteraction(uia=SimpleNamespace())
        seen = {}

        def guard(hwnd=None):
            seen['hwnd'] = hwnd
            raise RuntimeError('stop-after-guard')

        command = dict(kind='browser', browser_action='fill_named',
                       target='输入框', text='咕咕嘎嘎',
                       source_text='在输入框中输入 咕咕嘎嘎')
        with patch('core.desktop_access.DesktopAccess.enabled',
                   new_callable=PropertyMock, return_value=True), \
                patch.object(DesktopInteraction, '_activate_browser',
                             return_value=4242) as activate, \
                patch.object(DesktopInteraction, '_guard', side_effect=guard):
            result = interaction.execute(command)
        activate.assert_called_once_with()
        self.assertEqual(seen.get('hwnd'), 4242)
        self.assertFalse(result['ok'])


if __name__ == '__main__':
    unittest.main()
