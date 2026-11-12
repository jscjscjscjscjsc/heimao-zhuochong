# -*- coding: utf-8 -*-
"""控制面板 POST 真机自测：起面板 → 写意图 → 发提醒 → 读状态 → 关面板。

用 requests 按 UTF-8 发 JSON（浏览器就是这个行为）。PowerShell 的 Invoke-RestMethod
在 5.1 下会把中文 body 编成 '?'，不能用来验中文链路。
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import requests  # noqa: E402

PORT = 51703
BASE = 'http://127.0.0.1:%d' % PORT
PY = sys.executable


def wait_up(timeout=25.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(BASE + '/api/status', timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:  # noqa: BLE001
            time.sleep(0.3)
    return False


def main():
    process = subprocess.Popen([PY, '-X', 'utf8', str(ROOT / 'tools' / 'control_panel.py'),
                                '--port', str(PORT)],
                               cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures = []
    try:
        if not wait_up():
            print('FAIL 面板没起来')
            return 2
        page = requests.get(BASE + '/', timeout=10)
        print('GET  /            %d bytes 标题=%s' % (len(page.content), '咕嘎控制面板' in page.text))
        if '咕嘎控制面板' not in page.text:
            failures.append('首页没有中文标题（编码有问题）')

        intent_text = '控制面板自检：意图链路已通（面板自检 write）'
        write = requests.post(BASE + '/api/intent', json=dict(text=intent_text), timeout=25).json()
        print('POST /api/intent  ok=%s' % write.get('ok'))
        body = json.loads(write.get('result') or '{}')
        echoed = ((body.get('intent') or {}).get('text') or '')
        print('     回读=%r' % echoed[:60])
        if intent_text not in echoed:
            failures.append('面板写入的中文被破坏：%r' % echoed[:40])

        alert_text = '【控制面板自检】这条来自咕嘎本地控制面板，链路验证用，不需要任何动作。'
        alert = requests.post(BASE + '/api/alert', json=dict(message=alert_text, severity='low'),
                              timeout=25).json()
        print('POST /api/alert   ok=%s' % alert.get('ok'))
        payload = json.loads(alert.get('result') or '{}')
        message = ((payload.get('alert') or {}).get('message') or '')
        print('     回读=%r' % message[:60])
        if alert_text not in message:
            failures.append('面板注入的中文被破坏：%r' % message[:40])

        status = requests.get(BASE + '/api/status', timeout=20).json()
        live = status.get('status') or {}
        print('GET  /api/status  ok=%s status=%s turn=%s queue=%s' % (
            status.get('ok'), live.get('status'), live.get('turn'), live.get('alertQueue')))
        if not status.get('ok'):
            failures.append('状态读取失败')

        # 空值必须被拒
        empty = requests.post(BASE + '/api/intent', json=dict(text='  '), timeout=15)
        print('POST /api/intent 空值 -> HTTP %s' % empty.status_code)
        if empty.status_code != 400:
            failures.append('空意图没有被拒绝')

        print()
        if failures:
            print('FAIL')
            for item in failures:
                print('  - ' + item)
            return 2
        print('PASS 控制面板：页面/读状态/写意图/中途插话/空值拒绝，中文全程无损坏')
        return 0
    finally:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        print('面板已关闭')


if __name__ == '__main__':
    sys.exit(main())
