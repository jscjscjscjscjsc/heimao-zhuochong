# -*- coding: utf-8 -*-
"""浏览器 attach：把用户**已登录**的 Chromium 系浏览器接成可编程通道。

为什么需要它
------------
咕嘎已有 Playwright 级能力（`core/super_agent/cdp.py` 直讲 CDP、ego-browser 语义操作），
缺的从来不是"会不会点网页"，而是**驱动哪个浏览器身份**：
ego 驱动的是它自己的干净 profile，而用户的登录态在豆包/Chrome 的 profile 里。

做法
----
Chromium 只能在**启动时**打开调试端口，所以本模块负责：
  1. 端口已在监听 -> 直接复用；
  2. 没在监听 -> 优雅关闭该浏览器（让 cookie/草稿写盘），再带
     `--remote-debugging-port` 用**同一个 user-data-dir** 重新拉起；
  3. 校验 `/json/version` 与 `/json/list`，返回可 attach 的页面列表。

用法
----
    from tools.browser_attach import ensure, pages, pick
    info = ensure()                       # 默认豆包浏览器 + 9222
    page = pick('member.bilibili.com')    # 找到目标标签
    # 之后用 core/super_agent/cdp.py 或原始 websocket 驱动

安全约束
--------
- 不读取、不导出、不落盘任何 cookie/token；只借浏览器自己的登录态发请求。
- 关闭浏览器前用 CloseMainWindow 优雅退出，失败才 taskkill。
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request

DOUBAO_EXE = r'G:\Doubao\智能体\Doubao\app\Doubao.exe'
DOUBAO_USER_DATA = r'C:\Users\Admin（无密码）\AppData\Local\Doubao\User Data'
DEFAULT_PORT = 9222


def _alive(port: int, timeout: float = 1.5) -> bool:
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/json/version' % port, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def version(port: int = DEFAULT_PORT) -> dict:
    with urllib.request.urlopen('http://127.0.0.1:%d/json/version' % port, timeout=5) as r:
        return json.loads(r.read().decode('utf-8'))


def pages(port: int = DEFAULT_PORT) -> list:
    with urllib.request.urlopen('http://127.0.0.1:%d/json/list' % port, timeout=5) as r:
        return [p for p in json.loads(r.read().decode('utf-8')) if p.get('type') == 'page']


def pick(url_contains: str, port: int = DEFAULT_PORT) -> dict | None:
    """按 URL 片段挑一个已打开的标签。"""
    for page in pages(port):
        if url_contains in str(page.get('url') or ''):
            return page
    return None


def new_tab(url: str, port: int = DEFAULT_PORT) -> dict:
    import urllib.parse
    req = urllib.request.Request(
        'http://127.0.0.1:%d/json/new?%s' % (port, urllib.parse.quote(url, safe='')), method='PUT')
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode('utf-8'))


def _ps(command: str) -> str:
    completed = subprocess.run(['powershell.exe', '-NoProfile', '-Command', command],
                               capture_output=True, text=True, encoding='utf-8', errors='replace')
    return (completed.stdout or '') + (completed.stderr or '')


def _close_browser(process_name: str) -> None:
    _ps(("$p = Get-Process -Name '%s' -ErrorAction SilentlyContinue | "
         "Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1; "
         "if ($p) { $p.CloseMainWindow() | Out-Null; Start-Sleep -Seconds 4 }; "
         "$still = Get-Process -Name '%s' -ErrorAction SilentlyContinue; "
         "if ($still) { $still | Stop-Process -Force }") % (process_name, process_name))


def ensure(exe: str = DOUBAO_EXE, user_data: str = DOUBAO_USER_DATA,
           port: int = DEFAULT_PORT, process_name: str = 'Doubao',
           timeout: float = 40.0) -> dict:
    """确保有一个带调试端口的已登录浏览器在跑；返回可 attach 的信息。"""
    if _alive(port):
        return dict(ok=True, reused=True, port=port, version=version(port), pages=len(pages(port)))

    _close_browser(process_name)
    time.sleep(2)
    subprocess.Popen([exe,
                      '--remote-debugging-port=%d' % port,
                      '--remote-allow-origins=*',
                      '--user-data-dir=%s' % user_data])
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _alive(port):
            return dict(ok=True, reused=False, port=port, version=version(port), pages=len(pages(port)))
        time.sleep(1.5)
    return dict(ok=False, port=port, error='调试端口没起来：可能该浏览器禁用了 remote debugging')


if __name__ == '__main__':
    print(json.dumps(ensure(), ensure_ascii=False, indent=1))
