# -*- coding: utf-8 -*-
"""浏览器反射：把「点这个按钮 / 在搜索框里输入 X / 这个页面上有什么」直接落到 CDP。

为什么单独一层：
- UIA 操作网页要靠坐标和控件树，慢且脆；CDP 能直接读 DOM、按语义定位，准得多；
- 但要保持"反射弧"的语义：只做单步、不推理、解析不到就退回模型，绝不盲点。

安全红线与反射弧一致：
- 危险控件（发送/删除/支付/购买/提交订单/注销/退出登录…）一律拒绝，交回模型；
- 解析不到唯一目标 → 不动作；
- 动作后必须重新观察核验。
"""
from __future__ import annotations

import time

MAX_MESSAGE = 240
_SESSION_CACHE = dict(port=0, session=None, at=0.0)
DANGEROUS_WORDS = ('发送', '删除', '卸载', '支付', '付款', '购买', '下单', '提交订单',
                   '注销', '退出登录', '确认转账', '清空', '格式化', '免密',
                   'delete', 'pay', 'purchase', 'unsubscribe')

# ---- 词表：刻意收窄，宁可漏判交给模型，也不要在网页上误点 -------------------
PHRASES_READ = ('这个页面上有什么', '这个页面上写了什么', '页面上有什么', '念一下这个页面',
                '读一下这个页面', '看看这个网页讲了什么', '这个网页有什么')
PHRASES_CLICK_BUTTON = ('点这个按钮', '点那个按钮', '点一下这个按钮', '点一下那个按钮')
PHRASES_CLICK_LINK = ('点这个链接', '点那个链接', '点一下这个链接', '点一下那个链接')
PHRASES_SCROLL_DOWN = ('页面往下滚', '页面往下', '网页往下滚', '往下翻页')
PHRASES_SCROLL_UP = ('页面往上滚', '页面往上', '网页往上滚', '往上翻页')


def _no(**extra):
    return dict(matched=False, action='none', **extra)


def _command(action, **fields):
    return dict(matched=True, action=action, silent=False, priority='fast',
                browser=True, **fields)


def _contains(text, phrases):
    return any(phrase in text for phrase in phrases)


def _ordinal(value):
    digits = str(value or '').strip()
    table = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
    if digits.isdigit():
        return max(1, int(digits))
    return table.get(digits, 0)


def match(text):
    """纯解析：这句话是不是浏览器反射动作。不碰网络、不连浏览器。"""
    import re
    raw = str(text or '').strip()
    if not raw or len(raw) > 200:
        return _no()
    value = re.sub(r'[。！？!?，,～~\s]+$', '', raw)
    if _contains(value, PHRASES_READ):
        return _command('browser_read')
    if _contains(value, PHRASES_SCROLL_DOWN):
        return _command('browser_scroll', delta=1)
    if _contains(value, PHRASES_SCROLL_UP):
        return _command('browser_scroll', delta=-1)
    if _contains(value, PHRASES_CLICK_BUTTON):
        return _command('browser_click', kind='button')
    if _contains(value, PHRASES_CLICK_LINK):
        return _command('browser_click', kind='link')
    # 只认"链接"的序号：桌面窗口里也有按钮，序号点按钮归 UIA 那条路，别在这抢。
    match_ordinal = re.fullmatch(r'(?:页面上|网页上)?点第\s*([0-9一二两三四五六七八九十]+)\s*(?:个)?\s*链接', value)
    if match_ordinal:
        index = _ordinal(match_ordinal.group(1))
        if index:
            return _command('browser_click', kind='link', ordinal=index)
    match_type = re.fullmatch(r'在(?:这个|那个)?(搜索框|输入框|框|输入栏)里?输入[：:\s]*(.{1,200})', value, re.S)
    if match_type:
        return _command('browser_type', field='search' if '搜索' in match_type.group(1) else 'input',
                        text=match_type.group(2).strip())
    match_find = re.fullmatch(r'点(?:一下)?(?:那个)?叫\s*[「"“]?(.{1,40}?)[」"”]?\s*的?(?:按钮|链接)', value)
    if match_find:
        return _command('browser_click', kind='any', name=match_find.group(1).strip())
    return _no()


# ------------------------------------------------------------------ 执行
class BrowserUnavailable(RuntimeError):
    pass


def close_session():
    session = _SESSION_CACHE.get('session')
    _SESSION_CACHE.update(port=0, session=None, at=0.0)
    if session is not None:
        try:
            session.close()
        except Exception:  # noqa: BLE001
            pass


def _session(port=0, hint=None):
    """复用同一个页面的 CDP 会话；端口变了就重建。"""
    import os
    from core.super_agent import cdp
    if not port:
        try:
            port = int(os.environ.get('GUGA_CDP_PORT') or 0)
        except ValueError:
            port = 0
    if port:
        info = cdp.discover(hint)
        if not cdp._port_alive(port):
            raise BrowserUnavailable('浏览器调试端口 %s 没有在监听' % port)
        page = cdp.Browser(port=port).page()
        target_port = int(port)
    else:
        info = cdp.discover(hint)
        if not info.get('alive'):
            raise BrowserUnavailable(info.get('reason') or '浏览器调试端口不可用')
        target_port = int(info['port'])
        page = cdp.Browser(port=target_port).page()
    cached = _SESSION_CACHE.get('session')
    if cached is not None and _SESSION_CACHE.get('port') == target_port:
        try:
            if cdp.PageSession.__name__ and cached.call('Runtime.evaluate',
                    dict(expression='1', returnByValue=True), timeout=4):
                return cached, page
        except Exception:  # noqa: BLE001
            close_session()
    session = cdp.PageSession(page['webSocketDebuggerUrl'])
    _SESSION_CACHE.update(port=target_port, session=session, at=time.time())
    return session, page


def _pick(elements, kind='any', name='', ordinal=0):
    def tag_ok(element):
        tag = str(element.get('tag') or '')
        if kind == 'link':
            return tag == 'a' or bool(element.get('href'))
        if kind == 'button':
            return tag == 'button' or str(element.get('type') or '') in ('button', 'submit')
        if kind == 'field':
            # 只有输入类控件才算"框"；否则按钮和链接会混进来，导致明明只有一个输入框却报歧义。
            return tag in ('input', 'textarea', 'select')
        return True

    candidates = [element for element in elements if tag_ok(element) and not element.get('disabled')]
    if name:
        needle = name.strip()
        candidates = [element for element in candidates if needle in str(element.get('text') or '')]
    if ordinal:
        if len(candidates) < ordinal:
            return None, '页面上只有 %d 个可选目标，没有第 %d 个' % (len(candidates), ordinal)
        element = candidates[ordinal - 1]
    else:
        if not candidates:
            return None, '这个页面上没有可点的目标'
        if len(candidates) > 1:
            # 多于一个时不猜：让模型看一眼再决定。
            return None, '这一页有 %d 个可选目标，我不确定你指的是哪个' % len(candidates)
        element = candidates[0]
    label = str(element.get('text') or element.get('id') or '')
    low = label.lower()
    if any(word in label or word in low for word in DANGEROUS_WORDS):
        return None, '这个是「%s」，属于不能替你点的操作，我先停手' % label[:40]
    return element, ''


def execute(command, *, port=0, hint=None):
    """执行一个浏览器反射动作，返回与反射弧一致的回执。"""
    from core.super_agent import cdp
    started = time.perf_counter()
    action = str((command or {}).get('action') or '')
    try:
        session, page = _session(port=port, hint=hint)
    except Exception as exc:  # noqa: BLE001
        return dict(ok=False, action_sent=False, verified=False, need_model=True,
                    browser=True, error=type(exc).__name__,
                    message='浏览器现在没有可用的调试端口：%s' % str(exc)[:160])

    def done(payload, verified=False, **extra):
        payload = dict(payload, browser=True, verified=verified,
                       elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
        payload.update(extra)
        return payload

    try:
        if action == 'browser_read':
            state = cdp.observe(session)
            body = str(state.get('text') or '').strip()
            text = ('这一页是「%s」，地址 %s。' % (state.get('title') or '未命名', state.get('url') or ''))[:120]
            if body:
                text += '页面上写着：' + body[:MAX_MESSAGE]
            return done(dict(ok=True, action_sent=False, message=text),
                        verified=True, title=state.get('title'), url=state.get('url'),
                        count=state.get('count'))
        if action == 'browser_scroll':
            delta = int((command or {}).get('delta') or 1)
            script = 'window.scrollBy(0,%d); window.scrollY' % (420 * delta)
            position = session.evaluate(script)
            return done(dict(ok=True, action_sent=True, message=('往下滚了一点' if delta > 0 else '往上滚了一点')),
                        verified=True, scroll_y=position)
        state = cdp.observe(session)
        element, why = _pick(state.get('elements') or [],
                             kind=('field' if action == 'browser_type'
                                   else ((command or {}).get('kind') or 'any')),
                             name=(command or {}).get('name') or '',
                             ordinal=int((command or {}).get('ordinal') or 0))
        if element is None:
            return done(dict(ok=False, action_sent=False, need_model=True, message=why))
        if action == 'browser_click':
            receipt = cdp.act(session, 'click', index=element['i'])
            after = cdp.observe(session)
            changed = (after.get('title') != state.get('title')) or (after.get('url') != state.get('url'))
            label = str(element.get('text') or element.get('id') or '目标')[:40]
            return done(dict(ok=bool(receipt.get('ok')),
                             action_sent=True,
                             message='点了「%s」%s' % (label, '，页面变了' if changed else '')),
                        verified=bool(receipt.get('ok')), target=label,
                        title=after.get('title'), url=after.get('url'), page_changed=changed)
        if action == 'browser_type':
            receipt = cdp.act(session, 'type', index=element['i'],
                              text=str((command or {}).get('text') or ''))
            return done(dict(ok=bool(receipt.get('ok')), action_sent=True,
                             message='填好了「%s」' % str(element.get('text') or element.get('id') or '输入框')[:30]),
                        verified=bool(receipt.get('ok')), value=receipt.get('value'))
        return done(dict(ok=False, action_sent=False, need_model=True,
                         message='这个浏览器动作我还不支持'))
    except Exception as exc:  # noqa: BLE001
        close_session()
        return dict(ok=False, action_sent=False, need_model=True, browser=True,
                    error=type(exc).__name__,
                    message='浏览器这边没做成（%s），这次没有继续操作' % type(exc).__name__,
                    elapsed_ms=round((time.perf_counter() - started) * 1000, 1))
