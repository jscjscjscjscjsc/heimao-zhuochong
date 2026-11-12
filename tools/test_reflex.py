"""Offline contract tests for the model-free reflex arc; never touch a real desktop.

Run: python -X utf8 -m pytest tools/test_reflex.py -q
"""
import copy
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import reflex
from core.reflex import execute, match


def element(ref='ok', name='确定', kind='Button', rect=None, **fields):
    item = dict(ref=ref, name=name, id='', type=kind,
                rect=dict(rect or dict(left=100, top=100, right=200, bottom=140)),
                enabled=True, offscreen=False, password=False, focused=False,
                patterns=['InvokePattern'] if kind in ('Button', 'Hyperlink', 'MenuItem') else [])
    item.update(fields)
    return item


class FakeWindow:
    """Window facts only; activation is recorded instead of performed."""

    def __init__(self):
        self.windows = {}
        self.foreground_hwnd = 0
        self.root_hwnd = 0
        self.cursor_point = (150, 120)
        self.activated = []
        self.activate_result = True

    def add(self, hwnd, **fields):
        info = dict(hwnd=int(hwnd), pid=567, title='测试窗口', process='notepad.exe',
                    class_name='Notepad', rect=[0, 0, 1000, 800], own_app=False, shell=False,
                    minimized=False)
        info.update(fields)
        info['hwnd'] = int(hwnd)
        self.windows[int(hwnd)] = info
        return info

    def foreground(self):
        info = self.windows.get(int(self.foreground_hwnd or 0))
        return dict(info) if info else {}

    def describe(self, hwnd):
        info = self.windows.get(int(hwnd or 0))
        return dict(info) if info else {}

    def cursor(self):
        return self.cursor_point

    def root_at(self, x, y):
        return int(self.root_hwnd or 0)

    def activate(self, hwnd):
        if not self.activate_result:
            return False
        self.activated.append(int(hwnd))
        self.foreground_hwnd = int(hwnd)
        self.root_hwnd = int(hwnd)
        return True

    def is_window(self, hwnd):
        return int(hwnd) in self.windows


class FakeBackend:
    """UIA surface as seen by reflex.execute; records every action."""

    def __init__(self):
        self.items = []
        self.by_ref = {}
        self.point = None
        self.focus = None
        self.actions = []
        self.read_fail = False
        self.snapshot_error = None
        self.act_errors = {}
        self.verified_operations = {'focus', 'set_value'}
        self.remove_after_act = False

    def set_items(self, *items):
        self.items = [copy.deepcopy(item) for item in items]
        self.by_ref = {item['ref']: copy.deepcopy(item) for item in items}

    def set_point(self, item, chain=None):
        self.point = dict(hwnd=item.get('hwnd') or 0, element=copy.deepcopy(item),
                          chain=[copy.deepcopy(entry) for entry in (chain or [])])
        self.by_ref.setdefault(item['ref'], copy.deepcopy(item))

    def snapshot(self, hwnd, limit=240, time_budget=1.5):
        if self.snapshot_error:
            raise RuntimeError(self.snapshot_error)
        return dict(hwnd=int(hwnd), elements=[copy.deepcopy(item) for item in self.items],
                    truncated=False)

    def read(self, hwnd, **selector):
        ref = selector.get('ref')
        if self.read_fail or ref not in self.by_ref:
            raise RuntimeError('UIA引用已过期或失效')
        return dict(hwnd=int(hwnd), element=copy.deepcopy(self.by_ref[ref]), verified=True)

    def focused(self, hwnd):
        if not self.focus:
            raise RuntimeError('当前焦点不是可填写的普通输入框')
        return dict(hwnd=int(hwnd), element=copy.deepcopy(self.focus))

    def element_from_point(self, x, y):
        if not self.point:
            raise RuntimeError('鼠标位置没有可读取的控件')
        return dict(hwnd=self.point.get('hwnd') or 0, point=dict(x=x, y=y),
                    element=copy.deepcopy(self.point['element']),
                    chain=[copy.deepcopy(entry) for entry in self.point.get('chain') or []])

    def act(self, hwnd, operation, ref=None, value='', **kwargs):
        self.actions.append(dict(hwnd=int(hwnd), operation=operation, ref=ref, value=value))
        if self.act_errors.get(operation):
            raise RuntimeError(self.act_errors[operation])
        if self.remove_after_act and operation == 'invoke':
            self.by_ref.pop(ref, None)
            self.items = [item for item in self.items if item.get('ref') != ref]
        verified = operation in self.verified_operations
        return dict(hwnd=int(hwnd), input_sent=True, action='uia', needs_observe=not verified,
                    verified=verified, verification=dict(kind='fake', matched=verified),
                    method='Fake', fallback=False, control=copy.deepcopy(self.by_ref.get(ref) or {}),
                    rect=None, message='fake')


class FakeInput:
    def __init__(self):
        self.events = []

    def click(self, x, y, expect_hwnd, count=1, guard=None):
        if callable(guard):
            guard()
        self.events.append(('click', int(x), int(y), int(expect_hwnd), int(count)))

    def scroll(self, x, y, delta, expect_hwnd, guard=None):
        if callable(guard):
            guard()
        self.events.append(('scroll', int(x), int(y), int(delta), int(expect_hwnd)))

    def keys(self, values):
        self.events.append(('keys', tuple(values)))


class FakeDesktop:
    """The existing desktop-step executor's numbered-option cache."""

    OPTION_TTL = 30.0

    def __init__(self, hwnd, items):
        import time
        self._options = dict(identity=dict(hwnd=int(hwnd)), items=items, at=time.monotonic())


class ReflexMatchTests(unittest.TestCase):
    def assertAction(self, utterance, action, **fields):
        command = match(utterance)
        self.assertTrue(command.get('matched'), utterance)
        self.assertEqual(command['action'], action, utterance)
        for key, value in fields.items():
            self.assertEqual(command.get(key), value, utterance + ' ' + key)

    def test_click_referents(self):
        for text, target in (('点这里', '这里'), ('点这块', '这块'), ('点这个', '这个'),
                             ('点那个', '那个'), ('点它', '它'), ('点一下这个', '这个'),
                             ('点击这里', '这里')):
            with self.subTest(text=text):
                self.assertAction(text, 'click_here', target=target)
        self.assertAction('点一下', 'click_here', target='cursor')

    def test_ordinal_and_double_click(self):
        self.assertAction('点第2个', 'click_ordinal', ordinal='index', index=2)
        self.assertAction('点第二个', 'click_ordinal', index=2)
        self.assertAction('点第2个按钮', 'click_ordinal', index=2)
        self.assertAction('点中间那个', 'click_ordinal', ordinal='middle')
        self.assertAction('点最后一个', 'click_ordinal', ordinal='last')
        for text in ('双击这里', '双击这个'):
            self.assertAction(text, 'double_click_here')

    def test_input_text_keeps_literal_content(self):
        self.assertAction('在这里输入hello', 'input_text', text='hello')
        self.assertAction('在这里输入 hello world', 'input_text', text='hello world')
        self.assertAction('输入：你好', 'input_text', text='你好')
        self.assertAction('输入hello', 'input_text', text='hello')

    def test_scroll_and_keys_and_window_state(self):
        self.assertAction('往下滚', 'scroll', direction='down', screens=0)
        self.assertAction('往上滚', 'scroll', direction='up', screens=0)
        self.assertAction('滚3屏', 'scroll', direction='down', screens=3)
        self.assertAction('滚到底', 'scroll', direction='bottom')
        self.assertAction('滚到顶', 'scroll', direction='top')
        for text in ('按回车', '回车'):
            self.assertAction(text, 'key', key='enter')
        self.assertAction('按Esc', 'key', key='esc')
        self.assertAction('按Tab', 'key', key='tab')
        self.assertAction('按退格', 'key', key='backspace')
        self.assertAction('保存', 'key', key='save')
        self.assertAction('复制', 'key', key='copy')
        self.assertAction('粘贴', 'key', key='paste')
        self.assertAction('最大化', 'window_state', kind='maximize')
        self.assertAction('最小化', 'window_state', kind='minimize')
        self.assertAction('还原窗口', 'window_state', kind='restore')
        self.assertAction('关闭这个窗口', 'window_state', kind='close')

    def test_ordinary_sentences_stay_with_the_model(self):
        for text in ('我想点个外卖', '点开设置里的账号', '输入法怎么切换', '这个文件在哪',
                     '点这里可以吗', '不要点这里', '双击打开文件', '输入', '点击',
                     '滚动一下', '帮我看看这个按钮在哪', '今天什么天气', '关闭浏览器',
                     '在这里输入hello然后点保存', '你能点这里吗'):
            with self.subTest(text=text):
                self.assertFalse(match(text).get('matched'), text)

    def test_match_is_pure_and_never_touches_uia(self):
        with patch('core.uia_service.shared_service', side_effect=AssertionError('UIA used in match')):
            self.assertTrue(match('点这里')['matched'])
            self.assertTrue(match('在这里输入hello')['matched'])


class ReflexExecuteTests(unittest.TestCase):
    def setUp(self):
        reflex._WINDOW_MEMORY.update(hwnd=0, at=0.0)
        reflex._LAST_TARGET.update(hwnd=0, at=0.0, ref='', name='', type='', rect=None)
        self.window = FakeWindow()
        self.window.add(123)
        self.window.foreground_hwnd = 123
        self.window.root_hwnd = 123
        self.backend = FakeBackend()
        self.driver = FakeInput()

    def run_command(self, text, desktop=None):
        command = match(text)
        self.assertTrue(command.get('matched'), text)
        return execute(command, backend=self.backend, window=self.window,
                       input_driver=self.driver, desktop=desktop)

    def test_plain_text_with_invoke_pattern_is_not_clickable(self):
        # Qt/Chromium report InvokePattern on Text and Group objects; a paragraph
        # must never be treated as a button.
        paragraph = element('text', '一段普通文字', 'Text', patterns=['InvokePattern'])
        self.backend.set_point(paragraph)
        self.backend.focus = None
        result = self.run_command('点这里')
        self.assertTrue(result['need_model'])
        self.assertEqual(self.backend.actions, [])

    def test_numbered_click_reuses_the_list_the_user_already_saw(self):
        cached_first = element('cached-1', '一')
        cached_second = element('cached-2', '二')
        self.backend.set_items(element('snap-1', '甲'), element('snap-2', '乙'))
        for item in (cached_first, cached_second):
            self.backend.by_ref[item['ref']] = copy.deepcopy(item)
        self.backend.verified_operations.add('invoke')
        desktop = FakeDesktop(123, [cached_first, cached_second])
        result = self.run_command('点第2个', desktop=desktop)
        self.assertTrue(result['ok'], result)
        self.assertEqual([action['ref'] for action in self.backend.actions], ['cached-2'])
        self.assertEqual(result['source'], 'cached_list_2')

    def test_unique_cursor_target_is_invoked_and_verified(self):
        button = element('ok', '确定')
        self.backend.set_point(button)
        self.backend.verified_operations.add('invoke')
        result = self.run_command('点这里')
        self.assertTrue(result['ok'])
        self.assertTrue(result['verified'])
        self.assertEqual(result['evidence'], 'uia_confirmed')
        self.assertEqual([(action['operation'], action['ref']) for action in self.backend.actions],
                         [('invoke', 'ok')])
        self.assertEqual(self.driver.events, [], 'UIA invoke must not move the mouse')

    def test_click_whose_target_disappears_is_a_real_verification(self):
        button = element('ok', '确定')
        self.backend.set_point(button)
        self.backend.remove_after_act = True
        result = self.run_command('点这里')
        self.assertTrue(result['ok'])
        self.assertTrue(result['verified'])
        self.assertEqual(result['evidence'], 'target_gone')

    def test_unverified_click_is_reported_honestly(self):
        button = element('ok', '确定')
        self.backend.set_point(button)
        result = self.run_command('点这里')
        self.assertTrue(result['ok'])
        self.assertFalse(result['verified'])
        self.assertEqual(result['evidence'], 'target_unchanged')
        self.assertIn('没有回读', result['message'])

    def test_focus_an_edit_with_verified_readback(self):
        edit = element('edit', '内容', 'Edit', patterns=[], value='原文')
        self.backend.set_point(edit)
        self.backend.verified_operations.add('focus')
        result = self.run_command('点一下')
        self.assertTrue(result['ok'])
        self.assertTrue(result['verified'])
        self.assertEqual(self.backend.actions[0]['operation'], 'focus')

    def test_ambiguous_target_never_acts(self):
        self.backend.point = None
        self.backend.focus = None
        result = self.run_command('点这里')
        self.assertFalse(result['ok'])
        self.assertTrue(result['need_model'])
        self.assertEqual(self.backend.actions, [])
        self.assertEqual(self.driver.events, [])

    def test_offscreen_or_disabled_target_never_acts(self):
        for fields in (dict(offscreen=True), dict(enabled=False)):
            with self.subTest(fields=fields):
                self.backend.actions = []
                self.driver.events = []
                self.backend.set_point(element('ok', '确定', **fields))
                result = self.run_command('点这里')
                self.assertTrue(result['need_model'])
                self.assertEqual(self.backend.actions, [])
                self.assertEqual(self.driver.events, [])

    def test_dangerous_control_is_refused(self):
        for name, control_id in (('发送', ''), ('删除', ''), ('确定', 'btn_delete'),
                                 ('立即支付', ''), ('确认转账', '')):
            with self.subTest(name=name, id=control_id):
                self.backend.actions = []
                self.backend.set_point(element('ok', name, id=control_id))
                result = self.run_command('点这里')
                self.assertFalse(result['ok'])
                self.assertTrue(result['need_model'])
                self.assertEqual(self.backend.actions, [])

    def test_ordinal_click_uses_snapshot_order_and_acts_once(self):
        first = element('a', '一')
        second = element('b', '二')
        third = element('c', '三')
        self.backend.set_items(first, second, third)
        self.backend.verified_operations.add('invoke')
        result = self.run_command('点第2个')
        self.assertTrue(result['ok'])
        self.assertEqual([action['ref'] for action in self.backend.actions], ['b'])
        self.assertEqual(result['source'], 'ordinal_2')

    def test_ordinal_out_of_range_is_a_model_hand_off(self):
        self.backend.set_items(element('a', '一'))
        result = self.run_command('点第5个')
        self.assertTrue(result['need_model'])
        self.assertEqual(self.backend.actions, [])

    def test_input_text_sets_value_and_verifies_readback(self):
        edit = element('edit', '内容', 'Edit', patterns=[], value='')
        self.backend.set_point(edit)
        result = self.run_command('在这里输入hello')
        self.assertTrue(result['ok'])
        self.assertTrue(result['verified'])
        self.assertEqual(result['evidence'], 'value_equals')
        self.assertEqual([(action['operation'], action['value']) for action in self.backend.actions],
                         [('set_value', 'hello')])

    def test_input_text_without_any_field_is_ambiguous(self):
        result = self.run_command('在这里输入hello')
        self.assertTrue(result['need_model'])
        self.assertEqual(self.backend.actions, [])

    def test_input_text_into_unique_field_of_remembered_window(self):
        # Typed chat: Guga owns the foreground, the user means the app just used.
        own = self.window.add(555, title='咕嘎对话', process='python.exe', own_app=True)
        self.window.foreground_hwnd = 555
        self.window.root_hwnd = 555
        reflex.remember_window(self.window.describe(123))
        edit = element('edit', '内容', 'Edit', patterns=[], value='')
        self.backend.set_items(edit)
        self.backend.focus = None
        result = self.run_command('在这里输入hello')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.window.activated, [123])
        self.assertEqual(self.backend.actions[0]['operation'], 'set_value')

    def test_key_press_targets_the_foreground_app(self):
        result = self.run_command('按回车')
        self.assertTrue(result['ok'])
        self.assertEqual(self.driver.events, [('keys', (13,))])
        self.assertFalse(result['verified'])
        self.assertIn('回车', result['message'])

    def test_enter_is_refused_in_a_chat_app(self):
        self.window.add(123, process='wechat.exe', title='微信')
        result = self.run_command('按回车')
        self.assertTrue(result['need_model'])
        self.assertEqual(self.driver.events, [])

    def test_scroll_and_scroll_to_bottom(self):
        result = self.run_command('往下滚')
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.driver.events), 1)
        kind, x, y, delta, expect = self.driver.events[0]
        self.assertEqual((kind, expect), ('scroll', 123))
        self.assertLess(delta, 0)
        self.assertEqual(self.run_command('滚到底')['ok'], True)
        self.assertEqual(self.driver.events[-1], ('keys', (17, 35)))

    def test_key_in_remembered_window_activates_it_first(self):
        own = self.window.add(555, title='咕嘎对话', process='python.exe', own_app=True)
        self.window.foreground_hwnd = 555
        self.window.root_hwnd = 555
        reflex.remember_window(self.window.describe(123))
        result = self.run_command('按Tab')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.window.activated, [123])
        self.assertEqual(self.driver.events, [('keys', (9,))])

    def test_minimized_or_shell_window_is_never_operated(self):
        for fields in (dict(minimized=True), dict(shell=True), dict(own_app=True)):
            with self.subTest(fields=fields):
                self.window.windows[123].update(fields)
                self.backend.set_point(element('ok', '确定'))
                result = self.run_command('点这里')
                self.assertTrue(result['need_model'])
                self.assertEqual(self.backend.actions, [])
                self.window.windows[123].update(dict(minimized=False, shell=False, own_app=False))

    def test_no_reliable_window_is_a_model_hand_off(self):
        self.window.foreground_hwnd = 555
        self.window.root_hwnd = 555
        self.window.add(555, title='咕嘎对话', process='python.exe', own_app=True)
        self.window.cursor_point = (5000, 5000)
        result = self.run_command('按回车')
        self.assertTrue(result['need_model'])
        self.assertEqual(self.driver.events, [])

    def test_window_state_delegates_to_the_existing_verified_executor(self):
        with patch('core.fast_commands.execute',
                   return_value=dict(ok=True, verified=True, action_sent=True,
                                     evidence='is_zoomed', message='「记事本」已最大化')):
            result = self.run_command('最大化')
        self.assertTrue(result['ok'])
        self.assertTrue(result['verified'])
        self.assertIn('最大化', result['message'])

    def test_uia_failure_never_raises_and_returns_need_model(self):
        self.backend.set_items(element('a', '一'))
        self.backend.snapshot_error = 'UIA provider crashed'
        result = self.run_command('点第2个')
        self.assertFalse(result['ok'])
        self.assertTrue(result['need_model'])
        self.assertIn('need_model', str(result))


# --------------------------------------------------------------------------
# Wiring: the three input paths must reach the reflex layer before the model.
# A real AgentChatWindow is used once; every desktop action is mocked out.
# --------------------------------------------------------------------------
import tempfile
import time
import uuid

from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtCore import QSettings, QTimer
from PyQt5.QtWidgets import QApplication

APP = QApplication.instance() or QApplication([])
REFLEX_OK = dict(ok=True, verified=True, action_sent=True, evidence='uia_confirmed',
                 message='已点击「确定」')


class ReflexWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.patches = [
            patch.dict(os.environ, {'GUGA_MCP_PIPE': 'reflex-test-' + uuid.uuid4().hex}),
            patch('ui.agent_chat.FAST_DIAGNOSTICS_PATH', cls.root / 'fast-diagnostics.json'),
            patch('core.keyboard_hook.KeyboardHook.start'),
            patch('ui.tts_engine.TTSEngine.speak'),
        ]
        for patcher in cls.patches:
            patcher.start()
        QSettings.setDefaultFormat(QSettings.IniFormat)
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, cls.temp.name)
        from ui.pet_window import PetWindow
        from core.chat_workspace import ChatWorkspace
        from ui.agent_chat import AgentChatWindow
        cls.pet = PetWindow()
        for timer in cls.pet.findChildren(QTimer):
            timer.stop()
        cls.panel = AgentChatWindow(cls.pet, ChatWorkspace(cls.root / 'chat.db'))
        cls.pet._agent_panel = cls.panel

    @classmethod
    def tearDownClass(cls):
        cls.panel.shutdown()
        cls.pet._self_mcp.close()
        cls.pet._screen_tools.stop()
        cls.pet._tray.hide()
        cls.pet.hide()
        cls.panel.hide()
        for patcher in reversed(cls.patches):
            patcher.stop()
        cls.temp.cleanup()

    def setUp(self):
        panel = self.panel
        panel.busy = False
        panel._run_kind = None
        panel._fast_inflight = False
        panel.open_conversation(panel.store.create_conversation())

    def pump(self, predicate, seconds=4):
        end = time.monotonic() + seconds
        while not predicate() and time.monotonic() < end:
            APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(predicate())

    def test_typed_chat_runs_the_reflex_without_the_model(self):
        panel = self.panel
        with patch('core.reflex.execute', return_value=REFLEX_OK) as run, \
                patch.object(panel.answer, 'start') as answer:
            self.assertTrue(panel.send('点这里'))
            self.pump(lambda: panel._fast_inflight is False)
        answer.assert_not_called()
        run.assert_called_once()
        self.assertEqual(run.call_args.args[0]['action'], 'click_here')
        self.assertFalse(panel._fast_inflight)
        self.assertEqual(panel.store.messages(panel.active_id)[-1]['role'], 'assistant')

    def test_typed_chat_ambiguous_target_falls_back_to_the_model(self):
        """指代不清时必须交回模型，不能自己在本地瞎点。

        交回之后的**路由**可能判成"本地任务"走 `panel.agent.start`，
        也可能直接回答走 `panel.answer.start`——两条都是模型路径。
        所以断言"至少启动了一条模型路径"，而不是死盯 answer
        （实测此时 run_kind=task / category=local_task，走的是 agent）。
        """
        panel = self.panel
        ambiguous = dict(ok=False, action_sent=False, verified=False, need_model=True,
                         message='我没看清你指的是哪一块')
        with patch('core.reflex.execute', return_value=ambiguous), \
                patch.object(panel.answer, 'start') as answer, \
                patch.object(panel.agent, 'start') as agent:
            self.assertTrue(panel.send('点这里'))
            self.pump(lambda: answer.called or agent.called)
        self.assertTrue(answer.called or agent.called,
                        '既没交给回答也没交给本地智能体，等于没交回模型')
        self.assertFalse(panel._fast_inflight)

    def test_recording_voice_reaches_reflex_through_send(self):
        panel = self.panel
        with patch('core.reflex.execute', return_value=REFLEX_OK) as run, \
                patch.object(panel.answer, 'start') as answer:
            panel._short_transcript('点这里')
            self.pump(lambda: panel._fast_inflight is False)
        answer.assert_not_called()
        run.assert_called_once()

    def test_long_voice_transcript_dispatches_reflex(self):
        from core.realtime_agent import AgentRealtimeVoice, local_action_match
        command = local_action_match('点这里')
        self.assertTrue(command.get('reflex'))
        self.assertEqual(command.get('action'), 'click_here')
        self.assertFalse(local_action_match('点开设置里的账号').get('reflex'))
        voice = AgentRealtimeVoice()
        fast, tasks = [], []
        voice.fast_command.connect(fast.append)
        voice.task_requested.connect(lambda *args: tasks.append(args))
        voice._handle_event(dict(type='input_audio_buffer.speech_started'))
        voice._handle_event(dict(type='conversation.item.input_audio_transcription.completed',
                                 transcript='点这块'))
        self.assertEqual(len(fast), 1)
        self.assertTrue(fast[0].get('reflex'))
        self.assertFalse(tasks)

    def test_long_voice_request_handler_dispatches_reflex(self):
        panel = self.panel
        voice = Mock()
        panel.voice = voice
        panel.voice_active = True
        try:
            with patch.object(panel, 'sender', return_value=voice):
                panel._voice_request('call-1', '往下滚')
        finally:
            panel.voice = None
            panel.voice_active = False
        voice._dispatch_fast.assert_called_once()
        command = voice._dispatch_fast.call_args.args[0]
        self.assertTrue(command.get('reflex'))
        self.assertEqual(command.get('action'), 'scroll')


if __name__ == '__main__':
    unittest.main()
