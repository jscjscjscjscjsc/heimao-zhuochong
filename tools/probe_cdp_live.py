# -*- coding: utf-8 -*-
"""真机 CDP 验收：用 headless Edge + 临时 profile 起一个隔离浏览器，
跑通 observe → click → type → verify，然后关掉。

不碰用户正在用的浏览器（独立 profile、headless、随机端口）。
"""
import json
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent import cdp  # noqa: E402

EDGE_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]

PAGE_HTML = """<!doctype html><html><head><title>before</title></head><body>
<h1>咕嘎 CDP 验收页</h1>
<input id="q" type="text" placeholder="搜索">
<button id="go" onclick="document.title='clicked';document.getElementById('out').textContent='已点击';">搜索</button>
<div id="out"></div>
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


def main():
    executable = next((path for path in EDGE_CANDIDATES if Path(path).is_file()), '')
    if not executable:
        print('SKIP 本机没有 Edge')
        return 3
    port = free_port()
    profile = tempfile.mkdtemp(prefix='guga-cdp-')
    process = subprocess.Popen(
        [executable, '--headless=new', '--disable-gpu', '--no-first-run',
         '--no-default-browser-check', '--remote-debugging-port=%d' % port,
         '--user-data-dir=' + profile, 'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_port(port):
            print('FAIL 浏览器调试端口 %.0fs 内没起来' % 25)
            return 2
        print('LAUNCH port=%d %.0fms' % (port, 0))
        browser = cdp.Browser(port=port)
        version = browser.version()
        print('VERSION %s' % str(version.get('Browser'))[:60])
        pages = browser.pages()
        print('PAGES %d' % len(pages))
        page = browser.page()
        session = cdp.PageSession(page['webSocketDebuggerUrl'])
        try:
            data_url = 'data:text/html;charset=utf-8,' + urllib.parse.quote(PAGE_HTML)
            started = time.perf_counter()
            result = cdp.act(session, 'navigate', url=data_url)
            print('NAVIGATE ok=%s %.0fms' % (result.get('ok'), (time.perf_counter() - started) * 1000))

            started = time.perf_counter()
            state = cdp.observe(session)
            observe_ms = (time.perf_counter() - started) * 1000
            print('OBSERVE title=%r count=%d frames=%s %.0fms' % (
                state['title'], state['count'], state['frames'], observe_ms))
            kinds = [(element['tag'], element['text'] or element.get('id')) for element in state['elements']]
            print('ELEMENTS %s' % json.dumps(kinds, ensure_ascii=False))

            button = next((element for element in state['elements']
                           if element['tag'] == 'button' or element.get('id') == 'go'), None)
            field = next((element for element in state['elements']
                          if element['tag'] == 'input' or element.get('id') == 'q'), None)
            if not button or not field:
                print('FAIL 没在页面里找到按钮或输入框')
                return 2

            started = time.perf_counter()
            clicked = cdp.act(session, 'click', index=button['i'])
            click_ms = (time.perf_counter() - started) * 1000
            print('CLICK ok=%s %.0fms receipt=%s' % (
                clicked.get('ok'), click_ms, json.dumps(clicked, ensure_ascii=False)[:160]))

            typed = cdp.act(session, 'type', index=field['i'], text='咕嘎')
            print('TYPE ok=%s value=%r' % (typed.get('ok'), typed.get('value')))

            checked = cdp.verify(session, expect_text='')
            print('VERIFY ok=%s title=%r url=%s' % (
                checked['ok'], checked['state']['title'], str(checked['state']['url'])[:60]))
            after = cdp.observe(session)
            title_ok = after['title'] == 'clicked'
            text_ok = '已点击' in (after.get('text') or '')
            print('RESULT title_changed=%s onclick_text_present=%s' % (title_ok, text_ok))
            return 0 if (title_ok and text_ok) else 2
        finally:
            session.close()
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)
        print('CLEANUP 临时 profile 已删除')


if __name__ == '__main__':
    sys.exit(main())
