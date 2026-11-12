# -*- coding: utf-8 -*-
"""真机验收：跨源 iframe 内的元素也能"看见 + 点中"。

做法：外层页面用 file://，内层页面从另一个 http 端口加载 → 天然跨源。
跨源 iframe 里主文档的 JS 进不去，只能靠 `Page.createIsolatedWorld(frameId)` +
`Runtime.evaluate(contextId=...)`，这正是本模块实现的路径。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent import cdp  # noqa: E402

EDGE_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]

INNER = """<!doctype html><html><head><title>inner</title></head><body>
<h2>内层页面（另一个源）</h2>
<button id="inner" onclick="document.getElementById('out').textContent='已点击';document.title='inner-clicked';">内层按钮</button>
<div id="out"></div>
</body></html>"""

OUTER = """<!doctype html><html><head><title>outer</title></head><body>
<h1>外层页面</h1>
<button id="outer">外层按钮</button>
<iframe id="frame" src="__INNER_URL__" width="600" height="220"></iframe>
</body></html>"""


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def wait_port(port, timeout=25.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.3):
                return True
        except OSError:
            time.sleep(0.25)
    return False


class InnerHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        body = INNER.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    executable = next((path for path in EDGE_CANDIDATES if Path(path).is_file()), '')
    if not executable:
        print('SKIP 本机没有 Edge')
        return 3
    inner = ThreadingHTTPServer(('127.0.0.1', 0), InnerHandler)
    inner_port = inner.server_address[1]
    threading.Thread(target=inner.serve_forever, daemon=True).start()

    profile = tempfile.mkdtemp(prefix='guga-frame-')
    port = free_port()
    process = subprocess.Popen(
        [executable, '--headless=new', '--disable-gpu', '--no-first-run',
         '--no-default-browser-check', '--remote-debugging-port=%d' % port,
         '--user-data-dir=' + profile, 'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures = []
    try:
        if not wait_port(port):
            print('FAIL 浏览器没起来')
            return 2
        outer_file = Path(profile) / 'outer.html'
        outer_file.write_text(
            OUTER.replace('__INNER_URL__', 'http://127.0.0.1:%d/' % inner_port), encoding='utf-8')
        browser = cdp.Browser(port=port)
        session = cdp.PageSession(browser.page()['webSocketDebuggerUrl'])
        try:
            cdp.act(session, 'navigate', url=outer_file.as_uri())
            time.sleep(0.8)          # 等 iframe 加载
            started = time.perf_counter()
            state = cdp.observe(session)
            observe_ms = (time.perf_counter() - started) * 1000
            print('OBSERVE %.0fms count=%d frame_count=%d' % (
                observe_ms, state['count'], state['frame_count']))
            for element in state['elements']:
                print('  [%d] %-8s %-10s frame=%s' % (
                    element['i'], element['tag'], str(element['text'])[:10],
                    (element.get('frame_id') or 'main')[:12]))

            inner_elements = [element for element in state['elements']
                              if 'child' in str(element.get('frame_id') or '')
                              or '127.0.0.1' in str(element.get('frame_url') or '')]
            if not inner_elements:
                failures.append('没看见跨源 iframe 里的元素')
                target = None
            else:
                target = inner_elements[0]
                print('FRAME_URL=%s' % target.get('frame_url'))

            if target is not None:
                started = time.perf_counter()
                result = cdp.act(session, 'click', index=target['i'])
                click_ms = (time.perf_counter() - started) * 1000
                print('CLICK %.0fms ok=%s cross_frame=%s' % (
                    click_ms, result.get('ok'), result.get('cross_frame')))
                if not result.get('ok') or not result.get('cross_frame'):
                    failures.append('跨源 iframe 点击没成功')

                # 在跨源 iframe 的会话里读回结果，确认真的生效（OOPIF 是独立 target）
                targets = cdp.iframe_targets(session)
                inner_target = next((row for row in targets
                                     if '127.0.0.1' in str(row.get('url') or '')), None)
                if not inner_target:
                    failures.append('拿不到跨源 iframe 的 target 会话')
                else:
                    text = session.evaluate(
                        "document.getElementById('out').textContent",
                        session_id=inner_target['session_id'])
                    title = session.evaluate('document.title',
                                             session_id=inner_target['session_id'])
                    print('INNER url=%s' % inner_target.get('url'))
                    print('INNER_OUT=%r  title=%r' % (text, title))
                    if str(text) != '已点击':
                        failures.append('内层按钮的回执没出现：%r' % text)
        finally:
            session.close()
            cdp._FRAME_CONTEXTS.clear()
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        inner.shutdown()
        inner.server_close()
        shutil.rmtree(profile, ignore_errors=True)
        print('CLEANUP 已清理')

    print()
    if failures:
        print('FAIL')
        for item in failures:
            print('  - ' + item)
        return 2
    print('PASS 跨源 iframe：能看见、能点中、回执可读')
    return 0


if __name__ == '__main__':
    sys.exit(main())
