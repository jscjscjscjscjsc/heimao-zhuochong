import os
import sys
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core.fast_commands import match,execute,_window_control,_window_control_intent
from core.desktop_access import DesktopAccess


class WindowPhraseTests(unittest.TestCase):
    def test_window_verbs_are_recognised_without_a_model(self):
        cases={'最大化':'maximize','窗口最大化':'maximize','把窗口最大化':'maximize',
               '帮我把这个窗口最大化':'maximize','最大化一下':'maximize',
               '最小化':'minimize','把窗口最小化吧':'minimize','最小化窗口':'minimize',
               '还原窗口':'restore','恢复窗口大小':'restore','恢复正常大小':'restore',
               '关闭窗口':'close','把这个窗口关掉':'close','关掉当前窗口':'close'}
        for text,kind in cases.items():
            with self.subTest(text=text):
                command=match(text)
                self.assertTrue(command['matched'])
                self.assertEqual(command['action'],'window_control')
                self.assertEqual(command['kind'],kind)
                self.assertTrue(command['silent'])

    def test_browser_window_verbs_keep_the_browser_identity(self):
        command=match('最大化浏览器')
        self.assertEqual(command['action'],'window_control')
        self.assertEqual(command['kind'],'maximize')
        self.assertEqual(command['target'],'浏览器')

    def test_existing_browser_routes_are_not_swallowed(self):
        self.assertEqual(match('关闭浏览器')['action'],'close_browser')
        self.assertEqual(match('关闭浏览器窗口')['action'],'close_browser')
        self.assertEqual(match('关闭页面')['action'],'close_page')
        self.assertEqual(match('关闭标签页')['action'],'close_page')
        self.assertEqual(match('打开浏览器')['action'],'open_app')
        self.assertEqual(match('打开微信')['action'],'open_app')

    def test_vague_verbs_stay_with_the_model(self):
        for text in ('全屏','放大','缩小','这个按钮是干什么的','窗口是什么','屏幕最大化'):
            with self.subTest(text=text):
                self.assertIsNone(_window_control_intent(match(text).get('source_text') or text))

    def test_inverted_and_compound_app_requests_stay_local(self):
        for text,target in (('把浏览器打开','浏览器'),('浏览器打开','浏览器'),
                            ('把微信打开','微信'),('微信打开一下','微信')):
            with self.subTest(text=text):
                command=match(text)
                self.assertEqual(command['action'],'open_app')
                self.assertEqual(command['target'],target)
        # 有真实第二动作的复合句现在整句交给 agent，不再只做一半。
        self.assertFalse(match('打开微信，找到小军')['matched'])

    def test_typing_verbs_are_not_treated_as_window_commands(self):
        command=match('在浏览器里搜索最大化功率')
        self.assertNotEqual(command.get('action'),'window_control')


class WindowControlTests(unittest.TestCase):
    def test_access_disabled_refuses_before_any_window_call(self):
        with patch.object(DesktopAccess,'enabled',new_callable=PropertyMock,return_value=False):
            result=_window_control('maximize')
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'],'access_disabled')
        self.assertFalse(result['action_sent'])

    def test_desktop_is_not_an_operable_window(self):
        import win32gui
        with patch.object(DesktopAccess,'enabled',new_callable=PropertyMock,return_value=True), \
             patch('win32gui.GetForegroundWindow',return_value=win32gui.GetDesktopWindow()):
            result=_window_control('maximize')
        self.assertFalse(result['ok'])
        self.assertIn(result['code'],('system_window','target_missing'))
        self.assertFalse(result['action_sent'])

    def test_own_window_is_refused_instead_of_rearranging_the_pet(self):
        import time
        from core import fast_commands as module
        previous=dict(module._WINDOW_MEMORY)
        module._WINDOW_MEMORY.update(hwnd=0,at=0.,title='',kind='')
        try:
            with patch.object(DesktopAccess,'enabled',new_callable=PropertyMock,return_value=True), \
                 patch('core.fast_commands._window_target',return_value=(4242,time.monotonic(),'own')):
                result=_window_control('maximize')
        finally:
            module._WINDOW_MEMORY.update(previous)
        self.assertFalse(result['ok'])
        self.assertEqual(result['code'],'own_window')
        self.assertFalse(result['action_sent'])


if __name__=='__main__':
    unittest.main()
