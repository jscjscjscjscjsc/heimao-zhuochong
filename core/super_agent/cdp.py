# -*- coding: utf-8 -*-
"""CDP 执行器：直接驱动浏览器的 Chromium 调试端口来"看"和"做"网页。

为什么单独做这一层（路线1 的判断依据）：
- 自制网页/小程序读不到**跨源 DOM**（同源策略 + 沙箱），所以"在网页里全局操作别的网页"必须由浏览器协议来做；
- CDP 能跨页面、读 DOM、执行 JS，比 UIA 定位网页更准也更稳。

本模块只管三件事，语义与反射弧对齐：`observe` / `act` / `verify`。
不引入新依赖：`websocket-client` 已是本机现成的。
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.request
from pathlib import Path

DEFAULT_TIMEOUT = 20.0
MAX_ELEMENTS = 240

# 可交互元素的收集脚本；返回带序号的清单，并挂到 window 上供 act 复用。
_COLLECT_JS = r"""(function(){
  var sel='a[href],button,input,textarea,select,[role="button"],[role="link"],[role="tab"],[contenteditable="true"]';
  var out=[];var nodes=document.querySelectorAll(sel);
  for(var i=0;i<nodes.length && out.length<__LIMIT__;i++){
    var el=nodes[i];var r=el.getBoundingClientRect();
    if(r.width<2||r.height<2)continue;
    var st=window.getComputedStyle(el);
    if(!st||st.visibility==='hidden'||st.display==='none'||st.opacity==='0')continue;
    var text=(el.innerText||el.value||el.getAttribute('aria-label')||el.getAttribute('title')||'').trim();
    out.push({i:out.length,tag:el.tagName.toLowerCase(),type:(el.type||''),
      text:text.slice(0,120),id:el.id||'',name:el.name||'',
      href:(el.href||'').slice(0,200),disabled:!!el.disabled,
      x:Math.round(r.left+r.width/2),y:Math.round(r.top+r.height/2)});
  }
  window.__gugaCdps=Array.prototype.slice.call(nodes);
  return out;
})()"""

_CLICK_JS = r"""(function(i){
  var nodes=document.querySelectorAll('a[href],button,input,textarea,select,[role="button"],[role="link"],[role="tab"],[contenteditable="true"]');
  var live=[];for(var k=0;k<nodes.length;k++){var el=nodes[k];var r=el.getBoundingClientRect();
    if(r.width<2||r.height<2)continue;var st=window.getComputedStyle(el);
    if(!st||st.visibility==='hidden'||st.display==='none'||st.opacity==='0')continue;live.push(el);}
  var el=live[i];if(!el)return {ok:false,error:'元素已不存在，请重新观察'};
  el.scrollIntoView({block:'center'});
  if(el.focus)el.focus();
  el.click();
  return {ok:true,tag:el.tagName.toLowerCase(),text:(el.innerText||el.value||'').slice(0,80)};
})(__INDEX__)"""

_TYPE_JS = r"""(function(i,text){
  var nodes=document.querySelectorAll('a[href],button,input,textarea,select,[role="button"],[role="link"],[role="tab"],[contenteditable="true"]');
  var live=[];for(var k=0;k<nodes.length;k++){var el=nodes[k];var r=el.getBoundingClientRect();
    if(r.width<2||r.height<2)continue;var st=window.getComputedStyle(el);
    if(!st||st.visibility==='hidden'||st.display==='none'||st.opacity==='0')continue;live.push(el);}
  var el=live[i];if(!el)return {ok:false,error:'元素已不存在，请重新观察'};
  el.scrollIntoView({block:'center'});el.focus();
  var ok=false;
  if('value' in el){
    var proto=el.tagName==='TEXTAREA'?window.HTMLTextAreaElement.prototype:window.HTMLInputElement.prototype;
    var setter=Object.getOwnPropertyDescriptor(proto,'value');
    if(setter&&setter.set){setter.set.call(el,text);ok=true;}else{el.value=text;ok=true;}
    el.dispatchEvent(new Event('input',{bubbles:true}));
    el.dispatchEvent(new Event('change',{bubbles:true}));
  }else if(el.isContentEditable){
    el.textContent=text;ok=true;
    el.dispatchEvent(new Event('input',{bubbles:true}));
  }
  return {ok:ok,value:('value' in el?String(el.value):String(el.textContent||'')).slice(0,200)};
})(__INDEX__,__TEXT__)"""


class CDPError(RuntimeError):
    pass


# ---------------------------------------------------------------- 发现
def default_hint():
    candidates = []
    override = os.environ.get('GUGA_CDP_HINT', '')
    if override:
        candidates.append(Path(override))
    local = os.environ.get('LOCALAPPDATA', '')
    if local:
        for name in ('ego-lite-linux', 'ego-browser'):
            candidates.append(Path(local) / name / 'profile' / 'DevToolsActivePort')
    home = Path.home()
    candidates.extend([home / '.ego-lite-linux' / 'profile' / 'DevToolsActivePort',
                       home / '.ego-browser' / 'profile' / 'DevToolsActivePort'])
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0] if candidates else Path('DevToolsActivePort')


def _port_alive(port, timeout=0.4):
    try:
        with socket.create_connection(('127.0.0.1', int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def discover(hint=None):
    """读 DevToolsActivePort 并验证端口真的在监听。

    这个文件是**上次**启动时写的；浏览器关掉后文件仍在、端口已死。
    所以必须探活，绝不能只看文件就宣称"浏览器可用"。
    """
    path = Path(hint) if hint else default_hint()
    if not path.is_file():
        return dict(ok=False, alive=False, port=0, hint=str(path),
                    reason='找不到 DevToolsActivePort，浏览器未以调试模式启动过')
    try:
        lines = [line.strip() for line in path.read_text(encoding='utf-8', errors='replace').splitlines() if line.strip()]
    except OSError as exc:
        return dict(ok=False, alive=False, port=0, hint=str(path), reason='读不到端口文件：' + type(exc).__name__)
    if not lines:
        return dict(ok=False, alive=False, port=0, hint=str(path), reason='端口文件是空的')
    try:
        port = int(lines[0])
    except ValueError:
        return dict(ok=False, alive=False, port=0, hint=str(path), reason='端口文件第一行不是端口号')
    alive = _port_alive(port)
    return dict(ok=alive, alive=alive, port=port, hint=str(path),
                browser_path=lines[1] if len(lines) > 1 else '',
                reason='' if alive else '端口 %d 没有在监听：这个文件是上一次启动留下的陈旧记录' % port)


# ---------------------------------------------------------------- HTTP 面
def http_json(port, path, timeout=5.0):
    url = 'http://127.0.0.1:%d%s' % (int(port), path)
    request = urllib.request.Request(url, headers={'User-Agent': 'GugaDesktopPet/2.0'})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode('utf-8', 'replace')
    return json.loads(body) if body.strip() else {}


class Browser:
    """一个 Chromium 调试端口上的浏览器。"""

    def __init__(self, port=None, hint=None):
        info = discover(hint)
        if port:
            info = dict(info, port=int(port), alive=_port_alive(port))
        if not info.get('alive'):
            raise CDPError(info.get('reason') or '浏览器调试端口不可用')
        self.info = info
        self.port = int(info['port'])

    @property
    def port_number(self):
        return self.port

    def version(self):
        return http_json(self.port, '/json/version')

    def pages(self):
        rows = http_json(self.port, '/json/list')
        return [row for row in rows if isinstance(row, dict) and row.get('type') == 'page']

    def page(self, target_id='', url_contains=''):
        pages = self.pages()
        if target_id:
            for row in pages:
                if row.get('id') == target_id:
                    return row
            raise CDPError('没有这个页面 target：' + str(target_id)[:80])
        if url_contains:
            for row in pages:
                if url_contains in str(row.get('url') or ''):
                    return row
            raise CDPError('没有 URL 包含 %s 的页面' % url_contains[:60])
        if not pages:
            raise CDPError('浏览器里没有可用的页面；先开一个标签页')
        return pages[0]


# ---------------------------------------------------------------- WS 会话
class PageSession:
    """一个页面上的 CDP 会话：call() 发命令并按 id 收结果，事件自动跳过。"""

    def __init__(self, ws_url, timeout=DEFAULT_TIMEOUT):
        import websocket
        self.timeout = float(timeout)
        self._ws = websocket.create_connection(ws_url, timeout=self.timeout)
        self._id = 0
        self.events = []

    def call(self, method, params=None, timeout=None, session_id=None):
        self._id += 1
        ident = self._id
        message = dict(id=ident, method=method, params=params or {})
        if session_id:
            message['sessionId'] = session_id
        self._ws.send(json.dumps(message))
        deadline = time.monotonic() + float(timeout or self.timeout)
        while time.monotonic() < deadline:
            raw = self._ws.recv()
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8', 'replace')
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            if 'id' not in message:
                self.events.append(message)
                while len(self.events) > 200:
                    self.events.pop(0)
                continue
            if message.get('id') != ident:
                continue
            if message.get('error'):
                error = message['error']
                raise CDPError('CDP 调用失败：%s %s' % (
                    (error or {}).get('code'), str((error or {}).get('message') or '')[:160]))
            return message.get('result', {}) or {}
        raise CDPError('CDP 调用超时：' + method)

    def evaluate(self, expression, await_promise=False, context_id=None, session_id=None):
        params = dict(expression=expression, returnByValue=True,
                      awaitPromise=bool(await_promise))
        if context_id:
            params['contextId'] = int(context_id)
        result = self.call('Runtime.evaluate', params, session_id=session_id)
        if result.get('exceptionDetails'):
            detail = result['exceptionDetails']
            raise CDPError('页面脚本报错：' + str(detail.get('text') or '')[:160])
        return (result.get('result') or {}).get('value')

    def navigate(self, url, wait=True, timeout=None):
        self.call('Page.enable')
        self.call('Page.navigate', dict(url=url))
        if not wait:
            return dict(ok=True, url=url)
        deadline = time.monotonic() + float(timeout or self.timeout)
        while time.monotonic() < deadline:
            raw = self._ws.recv()
            if isinstance(raw, bytes):
                raw = raw.decode('utf-8', 'replace')
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get('method') == 'Page.loadEventFired':
                return dict(ok=True, url=url)
        return dict(ok=False, url=url, error='等页面加载超时')

    def close(self):
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001
            pass


_FRAME_CONTEXTS = {}


def frame_contexts(session, refresh=False):
    """列出所有 frame，并为非主文档 frame 建一个 isolated world 拿 executionContextId。

    这是跨 iframe（含跨源）读写 DOM 的正确做法：主文档的 Runtime.evaluate 进不去别人的
    frame，但 `Page.createIsolatedWorld(frameId)` 能在那个 frame 里开一个独立上下文。
    """
    cache = _FRAME_CONTEXTS.setdefault(id(session), {})
    tree = session.call('Page.getFrameTree', {}) or {}
    frames = []

    def walk(node):
        frame = (node or {}).get('frame') or {}
        frame_id = frame.get('id')
        if frame_id:
            frames.append(dict(frame_id=frame_id, url=frame.get('url') or '',
                               is_main=not frame.get('parentId'),
                               context_id=0))
        for child in (node or {}).get('childFrames') or []:
            walk(child)

    walk((tree or {}).get('frameTree'))
    for row in frames:
        if row['is_main']:
            continue
        cached = cache.get(row['frame_id'])
        if cached and not refresh:
            row['context_id'] = cached
            continue
        try:
            created = session.call('Page.createIsolatedWorld',
                                   dict(frameId=row['frame_id'], worldName='guga'))
            row['context_id'] = int((created or {}).get('executionContextId') or 0)
        except Exception:  # noqa: BLE001
            row['context_id'] = 0
        cache[row['frame_id']] = row['context_id']
    return frames


_IFRAME_SESSIONS = {}


def iframe_targets(session, refresh=False):
    """跨源 iframe 是**独立 target**（OOPIF），父页面的 frame tree 里看不到它。

    必须 `Target.attachToTarget(flatten=True)` 拿一个 sessionId，之后把命令按 sessionId 路由，
    才能真正在那个跨源文档里执行 JS。
    """
    cache = _IFRAME_SESSIONS.setdefault(id(session), {})
    if not cache.get('__discovered') or refresh:
        try:
            session.call('Target.setDiscoverTargets', dict(discover=True))
        except Exception:  # noqa: BLE001
            pass
        cache['__discovered'] = True
    try:
        rows = (session.call('Target.getTargets', {}) or {}).get('targetInfos') or []
    except Exception:  # noqa: BLE001
        return []
    result = []
    for row in rows:
        if str(row.get('type')) != 'iframe':
            continue
        target_id = str(row.get('targetId') or '')
        if not target_id:
            continue
        session_id = cache.get(target_id)
        if not session_id or refresh:
            try:
                attached = session.call('Target.attachToTarget',
                                        dict(targetId=target_id, flatten=True)) or {}
                session_id = str(attached.get('sessionId') or '')
            except Exception:  # noqa: BLE001
                session_id = ''
            cache[target_id] = session_id
        if session_id:
            result.append(dict(target_id=target_id, session_id=session_id,
                               url=str(row.get('url') or '')))
    return result


def _collect(session, context_id=0, limit=MAX_ELEMENTS, session_id=None):
    """在指定上下文里收集可交互元素，返回带 local_index 的清单。"""
    script = _COLLECT_JS.replace('__LIMIT__', str(int(limit)))
    rows = session.evaluate(script, context_id=context_id, session_id=session_id) or []
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row = dict(row)
        row['local_index'] = int(row.get('i') or len(result))
        result.append(row)
    return result


def _sources(session, include_frames=True):
    """按固定顺序给出所有可执行上下文：主文档 → 同进程子 frame → 跨源 iframe target。"""
    sources = [dict(kind='main', context_id=0, frame_id='', url='', session_id=None)]
    if not include_frames:
        return sources
    for row in frame_contexts(session):
        if row.get('is_main'):
            continue
        sources.append(dict(kind='frame', context_id=row.get('context_id') or 0,
                            frame_id=row.get('frame_id') or '', url=row.get('url') or '',
                            session_id=None))
    for row in iframe_targets(session):
        sources.append(dict(kind='oopif', context_id=0,
                            frame_id=row.get('target_id') or '', url=row.get('url') or '',
                            session_id=row.get('session_id') or ''))
    return sources


# ---------------------------------------------------------------- 语义动作
def observe(session, limit=MAX_ELEMENTS, include_frames=True):
    """看页面：标题、地址、可交互元素清单（默认含同进程 frame 与跨源 iframe 的元素）。"""
    info = session.evaluate(
        "(function(){return {title:document.title||'',url:location.href||'',"
        "ready:document.readyState||'',frames:window.frames.length,"
        "text:(document.body?document.body.innerText:'').slice(0,4000)};})()") or {}
    sources = _sources(session, include_frames)
    elements = []
    for source in sources:
        try:
            chunk = _collect(session, source['context_id'], limit, source['session_id'])
        except Exception:  # noqa: BLE001
            chunk = []
        for element in chunk:
            element = dict(element)
            element['frame_id'] = source['frame_id']
            element['frame_url'] = source['url']
            element['frame_kind'] = source['kind']
            element['i'] = len(elements)
            elements.append(element)
        if len(elements) >= limit:
            break
    elements = elements[:limit]
    return dict(ok=True, title=info.get('title', ''), url=info.get('url', ''),
                ready=info.get('ready', ''), frames=info.get('frames', 0),
                frame_count=len([s for s in sources if s['kind'] != 'main']) + 1,
                text=info.get('text', ''), elements=elements, count=len(elements))


def _context_for(text):
    return json.dumps(str(text), ensure_ascii=False)


def act(session, action, index=None, text='', url=''):
    """做一件事：click / type / navigate。返回是否送达 + 页面回执。"""
    if action == 'navigate':
        if not url:
            raise ValueError('navigate 需要 url')
        return dict(session.navigate(url), action='navigate')
    if action not in ('click', 'type'):
        raise ValueError('不支持的浏览器动作：' + str(action))
    position = int(index or 0)
    sources = _sources(session)
    gathered = []
    for source in sources:
        try:
            chunk = _collect(session, source['context_id'], MAX_ELEMENTS, source['session_id'])
        except Exception:  # noqa: BLE001
            chunk = []
        for element in chunk:
            element = dict(element)
            element['context_id'] = source['context_id']
            element['session_id'] = source['session_id']
            element['frame_id'] = source['frame_id']
            element['frame_kind'] = source['kind']
            element['i'] = len(gathered)
            gathered.append(element)
    if position >= len(gathered):
        return dict(ok=False, error='元素已不存在，请重新观察', action=action, index=position)
    target = gathered[position]
    context_id = target.get('context_id') or 0
    session_id = target.get('session_id') or None
    if action == 'click':
        script = _CLICK_JS.replace('__INDEX__', str(target['local_index']))
        result = session.evaluate(script, context_id=context_id, session_id=session_id) or {}
        return dict(result, action='click', index=position,
                    frame_id=target.get('frame_id', ''), frame_kind=target.get('frame_kind'),
                    cross_frame=target.get('frame_kind') != 'main')
    script = _TYPE_JS.replace('__INDEX__', str(target['local_index'])).replace(
        '__TEXT__', _context_for(str(text)))
    result = session.evaluate(script, context_id=context_id, session_id=session_id) or {}
    return dict(result, action='type', index=position,
                frame_id=target.get('frame_id', ''), frame_kind=target.get('frame_kind'),
                cross_frame=target.get('frame_kind') != 'main')


def verify(session, expect_text='', expect_url_contains=''):
    """动作后核验：重新看一遍，按期望比对。"""
    state = observe(session)
    checks = []
    if expect_url_contains:
        checks.append(('url_contains', expect_url_contains in (state.get('url') or '')))
    if expect_text:
        blob = (state.get('title') or '') + '\n' + (state.get('text') or '')
        checks.append(('text_present', expect_text in blob))
    passed = all(ok for _, ok in checks) if checks else True
    return dict(ok=passed, state=dict(title=state.get('title'), url=state.get('url'),
                                      count=state.get('count')),
                checks=[dict(name=name, ok=ok) for name, ok in checks])


def open_session(port=None, hint=None, target_id='', url_contains=''):
    """一次性拿到 (browser, page, session)，调用方负责 session.close()。"""
    browser = Browser(port=port, hint=hint)
    page = browser.page(target_id=target_id, url_contains=url_contains)
    ws_url = str(page.get('webSocketDebuggerUrl') or '')
    if not ws_url:
        raise CDPError('这个页面没有 webSocketDebuggerUrl，无法建立 CDP 会话')
    return browser, page, PageSession(ws_url)
