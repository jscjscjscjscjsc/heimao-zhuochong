# -*- coding: utf-8 -*-
"""咕嘎控制面板（路线1 最后一块）：一个本地网页，用来看和干预 DSH。

用法（项目根）：
    python -X utf8 tools/control_panel.py            # 默认 127.0.0.1:51700
    python -X utf8 tools/control_panel.py --port 51700 --open

设计约束：
- **只监听 127.0.0.1**，不对外暴露；
- 读状态不需要确认；写意图 / 中途插话属于动作，必须在页面上显式点"确认发送"；
- 不引入任何第三方依赖（stdlib http.server），页面自带样式，不加载外部资源；
- 面板只是遥控器：真正的"手"是 CDP 与 UIA，真正的"脑"是 DSH。
"""
import argparse
import json
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.mcp_host import MCPHost  # noqa: E402

DEFAULT_PORT = 51700
SERVER = 'dsh-bridge'

PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>咕嘎控制面板</title>
<style>
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;font:14px/1.6 system-ui,"Segoe UI","Microsoft YaHei",sans-serif;
     background:#0f1115;color:#e6e8ee;padding:24px;max-width:920px;margin:0 auto}
h1{font-size:18px;margin:0 0 4px}
.sub{color:#8b93a7;font-size:12px;margin-bottom:20px}
.card{background:#171a21;border:1px solid #242936;border-radius:10px;padding:16px;margin-bottom:14px}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.k{color:#8b93a7;min-width:96px}
.v{font-family:ui-monospace,Consolas,monospace}
pre{background:#0c0e13;border:1px solid #242936;border-radius:8px;padding:10px;
    overflow:auto;max-height:220px;font-size:12px;margin:8px 0 0}
input,textarea{background:#0c0e13;border:1px solid #2c323f;color:#e6e8ee;
    border-radius:8px;padding:8px 10px;font:inherit;width:100%}
textarea{min-height:64px;resize:vertical}
button{background:#2a6df4;border:0;color:#fff;border-radius:8px;padding:8px 14px;
    font:inherit;cursor:pointer}
button.ghost{background:#242936;color:#c9d1e3}
button:disabled{opacity:.5;cursor:not-allowed}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:6px}
.ok{background:#35c46a}.bad{background:#e05555}.idle{background:#8b93a7}
</style></head><body>
<h1>咕嘎控制面板</h1>
<div class="sub">本页面只监听 127.0.0.1。读状态随时可点；写意图与中途插话需要你点"确认发送"。</div>

<div class="card">
  <div class="row"><span class="k">连接</span><span id="conn"><span class="dot idle"></span>检查中…</span>
    <button class="ghost" onclick="refresh()">刷新</button></div>
  <div class="row"><span class="k">状态</span><span class="v" id="st">-</span></div>
  <div class="row"><span class="k">轮次</span><span class="v" id="turn">-</span></div>
  <div class="row"><span class="k">最近工具</span><span class="v" id="tool">-</span></div>
  <div class="row"><span class="k">共享意图</span><span class="v" id="intent">-</span></div>
  <div class="row"><span class="k">待处理提醒</span><span class="v" id="queue">-</span></div>
  <div class="row"><span class="k">地板锁</span><span class="v" id="lock">-</span></div>
</div>

<div class="card">
  <div class="row"><span class="k">共享意图</span></div>
  <textarea id="intentText" placeholder="用一句话说清当前主线，双方每一轮都会读到"></textarea>
  <pre id="intentOut" hidden></pre>
  <div class="row" style="margin-top:10px">
    <button onclick="sendIntent()">确认写入意图</button></div>
</div>

<div class="card">
  <div class="row"><span class="k">中途插话</span></div>
  <textarea id="alertText" placeholder="DSH 跑到一半时也能收到这条提醒"></textarea>
  <pre id="alertOut" hidden></pre>
  <div class="row" style="margin-top:10px">
    <button onclick="sendAlert()">确认发送提醒</button></div>
</div>

<script>
function $(id){return document.getElementById(id)}
function show(id,obj){const el=$(id);el.hidden=false;el.textContent=JSON.stringify(obj,null,2)}
async function refresh(){
  try{
    const r=await fetch('/api/status');const d=await r.json();
    if(!d.ok){throw new Error(d.error||'读取失败')}
    const s=d.status||{};
    $('conn').innerHTML='<span class="dot ok"></span>已连接 '+(d.server||'');
    $('st').textContent=s.status||'-';
    $('turn').textContent=(s.turn===undefined?'-':s.turn);
    $('tool').textContent=(s.lastTool&&s.lastTool.name)?s.lastTool.name:'-';
    $('intent').textContent=(s.intent&&s.intent.text)?s.intent.text:'（未设置）';
    const q=s.alertQueue; $('queue').textContent=(q===undefined?'-':q);
    $('lock').textContent=s.lock?(s.lock.owner+' 持锁'):'未持锁';
  }catch(e){
    $('conn').innerHTML='<span class="dot bad"></span>'+e.message;
  }
}
async function post(path,body){
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)});
  return await r.json();
}
async function sendIntent(){
  const text=$('intentText').value.trim();
  if(!text){alert('先写一句意图');return}
  if(!confirm('确认把这条意图写入 DSH 的共享议程？\\n\\n'+text))return;
  show('intentOut',await post('/api/intent',{text}));
  refresh();
}
async function sendAlert(){
  const message=$('alertText').value.trim();
  if(!message){alert('先写提醒内容');return}
  if(!confirm('确认把这条提醒注入 DSH 的当前轮？\\n\\n'+message))return;
  show('alertOut',await post('/api/alert',{message}));
  refresh();
}
refresh();setInterval(refresh,5000);
</script></body></html>"""


def _text(result):
    return ''.join(block.get('text', '') for block in (result or {}).get('content') or []
                   if isinstance(block, dict) and block.get('type') == 'text')


class Handler(BaseHTTPRequestHandler):
    server_version = 'GugaControlPanel/1.0'

    def log_message(self, *args):
        pass

    def _send(self, payload, status=200, content_type='application/json'):
        body = payload if isinstance(payload, bytes) else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            return self._send(PAGE.encode('utf-8'), content_type='text/html; charset=utf-8')
        if self.path.startswith('/api/status'):
            try:
                host = MCPHost()
                result = host.execute(dict(action='call', server=SERVER, tool='dsh_get_status'))
                payload = json.loads(_text(result) or '{}')
                return self._send(dict(ok=True, server=SERVER, status=payload))
            except Exception as exc:  # noqa: BLE001
                return self._send(dict(ok=False, error='%s: %s' % (type(exc).__name__, str(exc)[:200])), 502)
        return self._send(dict(ok=False, error='not found'), 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(length) or b'{}')
        except ValueError:
            return self._send(dict(ok=False, error='请求体不是 JSON'), 400)
        host = MCPHost()
        try:
            if self.path.startswith('/api/intent'):
                text = str(body.get('text') or '').strip()
                if not text:
                    return self._send(dict(ok=False, error='text 不能为空'), 400)
                result = host.execute(
                    dict(action='call', server=SERVER, tool='dsh_set_intent',
                         arguments=dict(text=text[:2000], by='guga-panel')),
                    approve=lambda ctx: True)
                return self._send(dict(ok=True, result=_text(result)[:400]))
            if self.path.startswith('/api/alert'):
                message = str(body.get('message') or '').strip()
                if not message:
                    return self._send(dict(ok=False, error='message 不能为空'), 400)
                result = host.execute(
                    dict(action='call', server=SERVER, tool='dsh_inject_alert',
                         arguments=dict(message=message[:2000], severity=str(body.get('severity') or 'low'))),
                    approve=lambda ctx: True)
                return self._send(dict(ok=True, result=_text(result)[:400]))
        except Exception as exc:  # noqa: BLE001
            return self._send(dict(ok=False, error='%s: %s' % (type(exc).__name__, str(exc)[:200])), 502)
        return self._send(dict(ok=False, error='not found'), 404)


def make_server(port=DEFAULT_PORT):
    server = ThreadingHTTPServer(('127.0.0.1', int(port)), Handler)
    return server


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=DEFAULT_PORT)
    parser.add_argument('--open', action='store_true', help='启动后打开浏览器')
    parser.add_argument('--selftest', action='store_true', help='起服务自测一轮后退出')
    args = parser.parse_args(argv)
    server = make_server(args.port)
    url = 'http://127.0.0.1:%d/' % server.server_address[1]
    print('控制面板: %s' % url)
    if args.selftest:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        import urllib.request
        with urllib.request.urlopen(url, timeout=10) as response:
            page = response.read().decode('utf-8')
        print('GET / -> %d bytes, 含标题=%s' % (len(page), '咕嘎控制面板' in page))
        with urllib.request.urlopen(url + 'api/status', timeout=15) as response:
            data = json.loads(response.read().decode('utf-8'))
        print('GET /api/status -> ok=%s status=%s turn=%s' % (
            data.get('ok'), (data.get('status') or {}).get('status'),
            (data.get('status') or {}).get('turn')))
        server.shutdown()
        server.server_close()
        return 0 if data.get('ok') else 2
    if args.open:
        threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
