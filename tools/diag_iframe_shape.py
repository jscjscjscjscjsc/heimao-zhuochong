# -*- coding: utf-8 -*-
"""诊断：跨源 iframe 在 CDP 里以什么形态出现（frame tree 里没有？Target 列表里？）"""
import json
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

EDGE = r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe'
INNER = '<!doctype html><html><head><title>inner</title></head><body><button id="b">内层按钮</button></body></html>'
OUTER = '<!doctype html><html><head><title>outer</title></head><body><iframe src="__URL__" width="500" height="200"></iframe></body></html>'


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class H(BaseHTTPRequestHandler):
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
    inner = ThreadingHTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=inner.serve_forever, daemon=True).start()
    profile = tempfile.mkdtemp(prefix='guga-diag-')
    port = free_port()
    proc = subprocess.Popen([EDGE, '--headless=new', '--disable-gpu', '--no-first-run',
                             '--remote-debugging-port=%d' % port, '--user-data-dir=' + profile,
                             'about:blank'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                with socket.create_connection(('127.0.0.1', port), timeout=0.3):
                    break
            except OSError:
                time.sleep(0.2)
        outer = Path(profile) / 'o.html'
        outer.write_text(OUTER.replace('__URL__', 'http://127.0.0.1:%d/' % inner.server_address[1]),
                         encoding='utf-8')
        browser = cdp.Browser(port=port)
        session = cdp.PageSession(browser.page()['webSocketDebuggerUrl'])
        session.navigate(outer.as_uri())
        # 给 iframe 一点加载时间
        for _ in range(20):
            tree = session.call('Page.getFrameTree', {})
            def count(node):
                total = 1
                for child in (node or {}).get('childFrames') or []:
                    total += count(child)
                return total
            n = count(tree.get('frameTree'))
            if n > 1:
                break
            time.sleep(0.25)
        print('frame_tree_count=%d' % n)
        print('frame_tree=%s' % json.dumps(tree, ensure_ascii=False)[:500])
        targets = session.call('Target.getTargets', {})
        rows = (targets or {}).get('targetInfos') or []
        print('targets=%d' % len(rows))
        for row in rows:
            print('  type=%-8s id=%s url=%s' % (
                row.get('type'), str(row.get('targetId'))[:16], str(row.get('url'))[:60]))
        session.close()
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        inner.shutdown()
        inner.server_close()


if __name__ == '__main__':
    sys.exit(main())
