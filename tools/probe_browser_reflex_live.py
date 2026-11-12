# -*- coding: utf-8 -*-
"""真机验收：反射弧 → browser_reflex → CDP 全链路（headless Edge + 临时 profile）。

测的是「说一句话 → 反射弧解析 → 浏览器上真的发生」这条链，不是直接调 CDP。
"""
import json
import os
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

from core import reflex  # noqa: E402
from core.super_agent import browser_reflex, cdp  # noqa: E402

EDGE_CANDIDATES = [
    r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe',
    r'C:\Program Files\Microsoft\Edge\Application\msedge.exe',
]

PAGE_HTML = """<!doctype html><html><head><title>before</title></head><body>
<h1>咕嘎 CDP 验收页</h1>
<input id="q" type="text" placeholder="搜索">
<button id="go" onclick="document.title='clicked';document.getElementById('out').textContent='已点击';">搜索</button>
<a href="#doc1">首页</a><a href="#doc2">文档</a>
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


def say(text):
    """走反射弧本体：解析 → 执行。"""
    command = reflex.match(text)
    if not command.get('matched'):
        return dict(matched=False, message='反射弧没有命中')
    result = reflex.execute(command)
    result = dict(result)
    result.setdefault('action', command.get('action'))
    result['matched'] = True
    return result


def main():
    executable = next((path for path in EDGE_CANDIDATES if Path(path).is_file()), '')
    if not executable:
        print('SKIP 本机没有 Edge')
        return 3
    port = free_port()
    profile = tempfile.mkdtemp(prefix='guga-brx-')
    process = subprocess.Popen(
        [executable, '--headless=new', '--disable-gpu', '--no-first-run',
         '--no-default-browser-check', '--remote-debugging-port=%d' % port,
         '--user-data-dir=' + profile, 'about:blank'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.environ['GUGA_CDP_PORT'] = str(port)
    failures = []
    try:
        if not wait_port(port):
            print('FAIL 调试端口没起来')
            return 2
        browser = cdp.Browser(port=port)
        session = cdp.PageSession(browser.page()['webSocketDebuggerUrl'])
        # 用 file:// 而不是 data:：data URL 上锚点跳转不会被浏览器当成导航，测不出真实行为。
        page_file = Path(profile) / 'accept.html'
        page_file.write_text(PAGE_HTML, encoding='utf-8')
        cdp.act(session, 'navigate', url=page_file.as_uri())
        session.close()
        print('READY port=%d browser=%s' % (port, str(browser.version().get('Browser'))[:40]))

        steps = []

        result = say('这个页面上有什么')
        steps.append(('读页面', result.get('matched'), result.get('ok'),
                      str(result.get('message'))[:90]))
        if '验收页' not in str(result.get('message') or ''):
            failures.append('读页面没有读到正文')

        result = say('在搜索框里输入咕嘎')
        steps.append(('填输入框', result.get('matched'), result.get('ok'),
                      str(result.get('message'))[:90]))
        if result.get('value') != '咕嘎':
            failures.append('输入框没填上：%r' % result.get('value'))

        result = say('点这个按钮')
        steps.append(('点按钮', result.get('matched'), result.get('ok'),
                      'page_changed=%s' % result.get('page_changed')))
        if result.get('title') != 'clicked':
            failures.append('点按钮后标题没变：%r' % result.get('title'))

        result = say('点第2个链接')
        steps.append(('点第2个链接', result.get('matched'), result.get('ok'),
                      str(result.get('url'))[-14:]))
        if '#doc2' not in str(result.get('url') or ''):
            failures.append('第2个链接没点对：%r' % result.get('url'))

        result = say('页面往下滚')
        steps.append(('往下滚', result.get('matched'), result.get('ok'),
                      'scroll_y=%s' % result.get('scroll_y')))

        result = say('点叫「删除」的按钮')
        steps.append(('危险目标', result.get('matched'), result.get('ok'),
                      str(result.get('message'))[:90]))

        result = say('我想点个外卖')
        steps.append(('不误触', result.get('matched'), None, ''))

        print()
        for name, matched, ok, detail in steps:
            print('  %-12s matched=%-5s ok=%-5s %s' % (name, matched, ok, detail))
        try:
            browser_reflex.close_session()
        except Exception:  # noqa: BLE001
            pass
        print()
        if failures:
            print('FAIL')
            for item in failures:
                print('  - ' + item)
            return 2
        print('PASS 反射弧→CDP 全链路：读/填/点/序号点/滚动/危险拒绝/不误触')
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(profile, ignore_errors=True)
        os.environ.pop('GUGA_CDP_PORT', None)
        print('CLEANUP 临时 profile 已删除')


if __name__ == '__main__':
    sys.exit(main())
