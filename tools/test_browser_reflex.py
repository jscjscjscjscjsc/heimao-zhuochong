# -*- coding: utf-8 -*-
"""浏览器反射的契约测试：解析不误触、动作不盲点、危险控件不让点。"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent import browser_reflex as br  # noqa: E402
from core.super_agent import cdp  # noqa: E402


class FakeSession:
    def __init__(self):
        self.calls = []

    def call(self, method, params=None, timeout=None):
        self.calls.append(method)
        return {}

    def evaluate(self, expression, await_promise=False):
        self.calls.append('eval:' + str(expression)[:40])
        return 400

    def close(self):
        pass


def element(index, tag='button', text='搜索', href='', disabled=False, element_id=''):
    return dict(i=index, tag=tag, type='', text=text, id=element_id, name='', href=href,
                disabled=disabled, x=10, y=10)


class BrowserMatchTests(unittest.TestCase):
    def test_read_page(self):
        self.assertEqual(br.match('这个页面上有什么')['action'], 'browser_read')
        self.assertEqual(br.match('念一下这个页面')['action'], 'browser_read')

    def test_click_button_and_link(self):
        self.assertEqual(br.match('点这个按钮')['action'], 'browser_click')
        self.assertEqual(br.match('点这个按钮')['kind'], 'button')
        self.assertEqual(br.match('点那个链接')['kind'], 'link')

    def test_ordinal_click(self):
        command = br.match('点第2个链接')
        self.assertEqual(command['action'], 'browser_click')
        self.assertEqual(command['ordinal'], 2)
        self.assertEqual(command['kind'], 'link')

    def test_type_into_search(self):
        command = br.match('在搜索框里输入咕嘎')
        self.assertEqual(command['action'], 'browser_type')
        self.assertEqual(command['text'], '咕嘎')
        self.assertEqual(command['field'], 'search')

    def test_click_by_name(self):
        command = br.match('点叫「登录」的按钮')
        self.assertEqual(command['action'], 'browser_click')
        self.assertEqual(command['name'], '登录')

    def test_scroll(self):
        self.assertEqual(br.match('页面往下滚')['delta'], 1)
        self.assertEqual(br.match('页面往上滚')['delta'], -1)

    def test_non_browser_sentences_do_not_match(self):
        for text in ('我想点个外卖', '这个页面怎么点', '点这里', '', '打开浏览器'):
            self.assertFalse(br.match(text).get('matched'), text)


class BrowserExecuteTests(unittest.TestCase):
    def setUp(self):
        br._SESSION_CACHE.update(port=0, session=None, at=0.0)
        self.session = FakeSession()
        self.patcher_session = patch.object(br, '_session', return_value=(self.session, dict(id='p')))
        self.patcher_session.start()

    def tearDown(self):
        self.patcher_session.stop()

    def run_read(self, elements, text='正文内容'):
        state = dict(ok=True, title='测试页', url='https://x.test/', text=text,
                     elements=elements, count=len(elements))
        with patch.object(cdp, 'observe', return_value=state):
            return br.execute(dict(matched=True, action='browser_read'))

    def test_read_returns_page_summary(self):
        result = self.run_read([], text='这里是一段正文')
        self.assertTrue(result['ok'])
        self.assertIn('测试页', result['message'])
        self.assertIn('这里是一段正文', result['message'])

    def test_single_button_is_clicked_and_verified(self):
        elements = [element(0, text='搜索')]
        state = dict(ok=True, title='t', url='u', text='', elements=elements, count=1)
        after = dict(ok=True, title='t2', url='u', text='', elements=elements, count=1)
        with patch.object(cdp, 'observe', side_effect=[state, after]), \
             patch.object(cdp, 'act', return_value=dict(ok=True, tag='button')) as act:
            result = br.execute(dict(matched=True, action='browser_click', kind='button'))
        self.assertTrue(result['ok'])
        self.assertTrue(result['action_sent'])
        self.assertTrue(result['page_changed'])   # 标题变了
        act.assert_called_once()
        self.assertEqual(act.call_args.kwargs['index'], 0)

    def test_multiple_targets_are_not_guessed(self):
        elements = [element(0, text='搜索'), element(1, text='登录')]
        state = dict(ok=True, title='t', url='u', text='', elements=elements, count=2)
        with patch.object(cdp, 'observe', return_value=state), \
             patch.object(cdp, 'act') as act:
            result = br.execute(dict(matched=True, action='browser_click', kind='button'))
        self.assertFalse(result['ok'])
        self.assertTrue(result['need_model'])
        self.assertFalse(result['action_sent'])
        act.assert_not_called()
        self.assertIn('不确定', result['message'])

    def test_ordinal_picks_the_right_one(self):
        elements = [element(0, tag='a', text='首页', href='https://x/'), element(1, tag='a', text='文档', href='https://x/d')]
        state = dict(ok=True, title='t', url='u', text='', elements=elements, count=2)
        after = dict(ok=True, title='t', url='https://x/d', text='', elements=elements, count=2)
        with patch.object(cdp, 'observe', side_effect=[state, after]), \
             patch.object(cdp, 'act', return_value=dict(ok=True)) as act:
            result = br.execute(dict(matched=True, action='browser_click', kind='link', ordinal=2))
        self.assertTrue(result['ok'])
        self.assertEqual(act.call_args.kwargs['index'], 1)

    def test_dangerous_target_is_refused(self):
        elements = [element(0, text='删除这条记录')]
        state = dict(ok=True, title='t', url='u', text='', elements=elements, count=1)
        with patch.object(cdp, 'observe', return_value=state), \
             patch.object(cdp, 'act') as act:
            result = br.execute(dict(matched=True, action='browser_click', kind='button'))
        self.assertFalse(result['ok'])
        self.assertTrue(result['need_model'])
        act.assert_not_called()
        self.assertIn('不能替你点', result['message'])

    def test_type_fills_the_field(self):
        elements = [element(0, tag='input', text='', element_id='q')]
        state = dict(ok=True, title='t', url='u', text='', elements=elements, count=1)
        with patch.object(cdp, 'observe', return_value=state), \
             patch.object(cdp, 'act', return_value=dict(ok=True, value='咕嘎')) as act:
            result = br.execute(dict(matched=True, action='browser_type', field='search', text='咕嘎'))
        self.assertTrue(result['ok'])
        self.assertEqual(act.call_args.kwargs['text'], '咕嘎')
        self.assertEqual(result['value'], '咕嘎')

    def test_scroll_reports_position(self):
        with patch.object(cdp, 'observe', return_value=dict(ok=True, elements=[])):
            result = br.execute(dict(matched=True, action='browser_scroll', delta=1))
        self.assertTrue(result['ok'])
        self.assertEqual(result['scroll_y'], 400)

    def test_browser_unavailable_hands_back_to_model(self):
        self.patcher_session.stop()
        with patch.object(br, '_session', side_effect=br.BrowserUnavailable('端口没在监听')):
            result = br.execute(dict(matched=True, action='browser_read'))
        self.patcher_session.start()
        self.assertFalse(result['ok'])
        self.assertTrue(result['need_model'])
        self.assertIn('没有可用的调试端口', result['message'])


if __name__ == '__main__':
    unittest.main()
