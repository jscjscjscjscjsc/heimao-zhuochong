# -*- coding: utf-8 -*-
"""CDP 执行器的契约测试：起一个假 CDP 端点（HTTP + WebSocket），不依赖真浏览器。

覆盖：陈旧端口识别、页面发现、observe 元素清单、click/type/navigate、
verify 比对、脚本报错不吞。
"""
import json
import socket
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent import cdp  # noqa: E402

ELEMENTS = [
    dict(i=0, tag='button', type='', text='搜索', id='go', name='', href='', disabled=False, x=10, y=20),
    dict(i=1, tag='input', type='text', text='', id='q', name='q', href='', disabled=False, x=30, y=20),
]
PAGE_INFO = dict(title='测试页', url='https://example.test/home', ready='complete',
                 frames=0, text='欢迎来到测试页 搜索框')


FRAME_ELEMENTS = [
    dict(i=0, tag='button', type='', text='接受', id='accept', name='', href='', disabled=False, x=5, y=5),
]


def fake_evaluate(expression, context_id=None):
    text = str(expression)
    inside_frame = int(context_id or 0) == 77
    if 'document.title' in text and 'innerText' in text:
        return PAGE_INFO
    if 'querySelectorAll' in text and 'window.__gugaCdps' in text:
        return FRAME_ELEMENTS if inside_frame else ELEMENTS
    if 'el.click()' in text:
        return dict(ok=True, tag='button', text='接受' if inside_frame else '搜索', frame=inside_frame)
    if 'setter.set.call' in text or 'el.textContent=text' in text:
        return dict(ok=True, value='已填写')
    if 'throw new Error' in text:
        return None
    return dict(ok=True)


class FakeWs:
    """极简 CDP 语义：Runtime.evaluate / Page.enable / Page.navigate / 帧相关。"""

    def __init__(self):
        from websockets.sync.server import serve
        self.frames = False          # 默认没有子 frame，保持既有用例不变
        self.oopif = False           # 跨源 iframe（独立 target）
        self.contexts = []
        self.evals = []
        self.attached = []
        self.server = serve(self._handler, '127.0.0.1', 0)
        self.port = self.server.socket.getsockname()[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _handler(self, ws):
        for raw in ws:
            try:
                request = json.loads(raw)
            except ValueError:
                continue
            method = request.get('method')
            ident = request.get('id')
            params = request.get('params') or {}
            session_id = request.get('sessionId')
            if method == 'Runtime.evaluate':
                context_id = params.get('contextId')
                self.evals.append((session_id, context_id, str(params.get('expression'))[:40]))
                value = (fake_evaluate(params.get('expression', ''), 77)
                         if session_id else fake_evaluate(params.get('expression', ''), context_id))
                payload = dict(jsonrpc='2.0', id=ident, result=dict(result=dict(value=value)))
            elif method == 'Target.setDiscoverTargets':
                payload = dict(jsonrpc='2.0', id=ident, result=dict())
            elif method == 'Target.getTargets':
                rows = [dict(type='page', targetId='page-1', url='https://example.test/home')]
                if self.oopif:
                    rows.append(dict(type='iframe', targetId='oopif-1', url='https://child.test/'))
                payload = dict(jsonrpc='2.0', id=ident, result=dict(targetInfos=rows))
            elif method == 'Target.attachToTarget':
                self.attached.append(params.get('targetId'))
                payload = dict(jsonrpc='2.0', id=ident, result=dict(sessionId='sess-oopif'))
            elif method == 'Page.getFrameTree':
                if self.frames:
                    tree = dict(
                        frame=dict(id='frame-main', url='https://example.test/home'),
                        childFrames=[dict(frame=dict(id='frame-child', url='https://child.test/',
                                                    parentId='frame-main'))])
                else:
                    tree = dict(frame=dict(id='frame-main', url='https://example.test/home'))
                payload = dict(jsonrpc='2.0', id=ident, result=dict(frameTree=tree))
            elif method == 'Page.createIsolatedWorld':
                self.contexts.append((params.get('frameId'), params.get('worldName')))
                payload = dict(jsonrpc='2.0', id=ident, result=dict(executionContextId=77))
            elif method == 'Page.navigate':
                payload = dict(jsonrpc='2.0', id=ident, result=dict())
                ws.send(json.dumps(payload))
                ws.send(json.dumps(dict(method='Page.loadEventFired', params=dict(timestamp=1))))
                continue
            else:
                payload = dict(jsonrpc='2.0', id=ident, result=dict())
            ws.send(json.dumps(payload))

    def stop(self):
        self.server.shutdown()


class _Handler(BaseHTTPRequestHandler):
    ws_port = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.startswith('/json/version'):
            body = dict(Browser='FakeChrome/1.0', webSocketDebuggerUrl='ws://127.0.0.1:%d/x' % _Handler.ws_port)
        elif self.path.startswith('/json/list'):
            body = [dict(type='page', id='page-1', title='测试页', url='https://example.test/home',
                         webSocketDebuggerUrl='ws://127.0.0.1:%d/devtools/page/page-1' % _Handler.ws_port),
                    dict(type='page', id='page-2', title='别的页', url='https://other.test/',
                         webSocketDebuggerUrl='ws://127.0.0.1:%d/devtools/page/page-2' % _Handler.ws_port)]
        else:
            self.send_response(404)
            self.end_headers()
            return
        raw = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class CDPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = FakeWs()
        _Handler.ws_port = cls.ws.port
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        cls.port = cls.http.server_address[1]
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()
        cls.hint_dir = tempfile.TemporaryDirectory()
        cls.hint = Path(cls.hint_dir.name) / 'DevToolsActivePort'
        cls.hint.write_text('%d\n/devtools/browser/fake\n' % cls.port, encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.ws.stop()
        cls.hint_dir.cleanup()

    def test_stale_hint_file_is_detected(self):
        dead = Path(self.hint_dir.name) / 'dead' / 'DevToolsActivePort'
        dead.parent.mkdir(parents=True, exist_ok=True)
        dead.write_text('%d\n/devtools/browser/x\n' % free_port(), encoding='utf-8')
        info = cdp.discover(dead)
        self.assertFalse(info['ok'])
        self.assertFalse(info['alive'])
        self.assertIn('没有在监听', info['reason'])

    def test_missing_hint_file_is_reported(self):
        info = cdp.discover(Path(self.hint_dir.name) / 'nope' / 'DevToolsActivePort')
        self.assertFalse(info['ok'])
        self.assertIn('找不到', info['reason'])

    def test_live_hint_file_is_accepted(self):
        info = cdp.discover(self.hint)
        self.assertTrue(info['ok'])
        self.assertEqual(info['port'], self.port)

    def test_browser_lists_pages_and_picks_by_url(self):
        browser = cdp.Browser(port=self.port)
        self.assertEqual(len(browser.pages()), 2)
        page = browser.page(url_contains='other.test')
        self.assertEqual(page['id'], 'page-2')
        with self.assertRaises(cdp.CDPError):
            browser.page(url_contains='没有这个')

    def test_observe_returns_elements_and_page_info(self):
        _, _, session = cdp.open_session(port=self.port, target_id='page-1')
        try:
            state = cdp.observe(session)
            self.assertEqual(state['title'], '测试页')
            self.assertIn('example.test', state['url'])
            self.assertEqual(state['count'], 2)
            self.assertEqual(state['elements'][0]['text'], '搜索')
        finally:
            session.close()

    def test_click_and_type_carry_receipts(self):
        _, _, session = cdp.open_session(port=self.port, target_id='page-1')
        try:
            clicked = cdp.act(session, 'click', index=0)
            self.assertTrue(clicked['ok'])
            self.assertEqual(clicked['action'], 'click')
            typed = cdp.act(session, 'type', index=1, text='咕嘎')
            self.assertTrue(typed['ok'])
            self.assertEqual(typed['value'], '已填写')
        finally:
            session.close()

    def test_navigate_waits_for_load_event(self):
        _, _, session = cdp.open_session(port=self.port, target_id='page-1')
        try:
            result = cdp.act(session, 'navigate', url='https://example.test/next')
            self.assertTrue(result['ok'])
        finally:
            session.close()

    def test_verify_checks_text_and_url(self):
        _, _, session = cdp.open_session(port=self.port, target_id='page-1')
        try:
            good = cdp.verify(session, expect_text='欢迎来到', expect_url_contains='example.test')
            self.assertTrue(good['ok'])
            bad = cdp.verify(session, expect_text='这句话不存在')
            self.assertFalse(bad['ok'])
            self.assertEqual(len(bad['checks']), 1)
        finally:
            session.close()

    def test_unknown_action_is_rejected(self):
        _, _, session = cdp.open_session(port=self.port, target_id='page-1')
        try:
            with self.assertRaises(ValueError):
                cdp.act(session, 'drag', index=0)
        finally:
            session.close()


class IframeTests(unittest.TestCase):
    """子 frame 里的元素也要能看见、能点——这是"整个网页内全局操作"的关键。"""

    @classmethod
    def setUpClass(cls):
        cls.ws = FakeWs()
        cls.ws.frames = True
        _Handler.ws_port = cls.ws.port
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        cls.port = cls.http.server_address[1]
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.ws.stop()

    def setUp(self):
        cdp._FRAME_CONTEXTS.clear()
        self.ws.contexts = []
        self.ws.evals = []

    def test_observe_collects_elements_from_child_frame(self):
        _, _, session = cdp.open_session(port=self.port, target_id='page-1')
        try:
            state = cdp.observe(session)
            self.assertEqual(state['count'], 3)            # 主文档 2 + iframe 1
            self.assertEqual(state['frame_count'], 2)
            frames = {element['frame_id'] for element in state['elements']}
            self.assertIn('frame-child', frames)
            tail = state['elements'][2]
            self.assertEqual(tail['text'], '接受')
            self.assertEqual(tail['frame_url'], 'https://child.test/')
            self.assertIn(('frame-child', 'guga'), self.ws.contexts)
        finally:
            session.close()

    def test_click_inside_iframe_uses_its_context(self):
        _, _, session = cdp.open_session(port=self.port, target_id='page-1')
        try:
            cdp.observe(session)                            # 先建立 isolated world
            result = cdp.act(session, 'click', index=2)     # 第 3 个元素在 iframe 里
            self.assertTrue(result['ok'])
            self.assertTrue(result['cross_frame'])
            self.assertEqual(result['frame_id'], 'frame-child')
            self.assertTrue(any(row[1] == 77 for row in self.ws.evals),
                            '没有用 frame 的 executionContextId 执行点击')
        finally:
            session.close()

    def test_out_of_range_index_is_reported_not_guessed(self):
        _, _, session = cdp.open_session(port=self.port, target_id='page-1')
        try:
            result = cdp.act(session, 'click', index=99)
            self.assertFalse(result['ok'])
            self.assertIn('重新观察', result['error'])
        finally:
            session.close()


class OopifTests(unittest.TestCase):
    """跨源 iframe 是独立 target：要先 attach 拿 sessionId，再把命令按 session 路由。"""

    @classmethod
    def setUpClass(cls):
        cls.ws = FakeWs()
        cls.ws.oopif = True
        _Handler.ws_port = cls.ws.port
        cls.http = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        cls.port = cls.http.server_address[1]
        threading.Thread(target=cls.http.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.http.shutdown()
        cls.http.server_close()
        cls.ws.stop()

    def setUp(self):
        cdp._FRAME_CONTEXTS.clear()
        cdp._IFRAME_SESSIONS.clear()
        self.ws.evals = []
        self.ws.attached = []

    def test_observe_reaches_cross_origin_iframe(self):
        _, _, session = cdp.open_session(port=self.port, target_id='page-1')
        try:
            state = cdp.observe(session)
            self.assertEqual(state['count'], 3)          # 主文档 2 + OOPIF 1
            kinds = [element['frame_kind'] for element in state['elements']]
            self.assertIn('oopif', kinds)
            self.assertEqual(state['elements'][2]['text'], '接受')
            self.assertIn('oopif-1', self.ws.attached)   # 真的 attach 了那个 target
        finally:
            session.close()

    def test_click_in_cross_origin_iframe_routes_by_session(self):
        _, _, session = cdp.open_session(port=self.port, target_id='page-1')
        try:
            cdp.observe(session)
            result = cdp.act(session, 'click', index=2)
            self.assertTrue(result['ok'])
            self.assertEqual(result['frame_kind'], 'oopif')
            self.assertTrue(any(session_id == 'sess-oopif' for session_id, _, _ in self.ws.evals),
                            '没有按 OOPIF 的 sessionId 路由命令')
        finally:
            session.close()


if __name__ == '__main__':
    unittest.main()
