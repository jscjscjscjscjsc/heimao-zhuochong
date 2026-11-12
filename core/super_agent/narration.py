# -*- coding: utf-8 -*-
"""把 DSH 进度 + 屏幕感知 + 灵枢记忆 组装成咕嘎的大白话。

原则：任何一路失败都静默跳过，绝不让汇报模块把桌宠主循环拖垮。
"""
from __future__ import annotations

import os
import threading
import time

MAX_CHARS = 460
_GW = {'at': 0.0, 'text': '', 'refreshing': False, 'error': ''}
_GW_LOCK = threading.Lock()
# 已经说过的附注，重复的就不再拼进去（否则 30 秒汇报会整段复读）。
_SEEN = {'memory': '', 'dsh': ''}


def _enabled() -> bool:
    return os.environ.get('GUGA_SUPER_AGENT', 'on').strip().lower() not in ('off', '0', 'false', 'no')


def _screen_line() -> str:
    try:
        from core.super_agent import screen_context
        line = screen_context.summary_line()
        if line:
            return line
        # 首帧兜底：汇报时观察线程可能刚启动，立即采样一次再取摘要。
        screen_context.probe()
        return screen_context.summary_line()
    except Exception:
        return ''


def _memory_line(query: str = '') -> str:
    try:
        from core.super_agent.lingshu_memory import recall_sentence
        return recall_sentence(query) or ''
    except Exception:
        return ''


def _refresh_dsh_async():
    """后台刷新 DSH 会话状态（网关 HTTP 不放在桌宠主线程）。"""
    with _GW_LOCK:
        if _GW['refreshing']:
            return
        _GW['refreshing'] = True

    def work():
        text = ''
        error = ''
        try:
            from core.super_agent.dsh_gateway import DshGateway, session_title
            gateway = DshGateway(timeout=4.0)
            items = gateway.session_summaries()
            running = [item for item in items if item.get('running')]
            if running:
                title = session_title(running[0]) or '当前会话'
                text = 'DSH 那边现在有 %d 个会话在跑，我盯着「%s」呢。' % (len(running), title[:36])
            elif items:
                text = 'DSH 那边现在没有会话在跑，随时可以开工。'
        except Exception as exc:  # noqa: BLE001
            error = type(exc).__name__
        finally:
            with _GW_LOCK:
                _GW.update(at=time.time(), text=text, error=error, refreshing=False)

    threading.Thread(target=work, name='guga-dsh-status', daemon=True).start()


def _dsh_line() -> str:
    if not _enabled():
        return ''
    with _GW_LOCK:
        text = _GW['text']
        at = _GW['at']
    if time.time() - at > 90:
        _refresh_dsh_async()
    return text


def compose(base_text: str, with_screen: bool = True, with_memory: bool = False,
            memory_query: str = '') -> str:
    """拼出一句自然、口语化的 30 秒汇报；同一句附注只出现一次，不跟着复读。"""
    if not _enabled():
        return str(base_text or '')[:MAX_CHARS]
    parts = [str(base_text or '').strip()]
    if with_screen:
        parts.append(_screen_line())
    if with_memory:
        line = _memory_line(memory_query)
        if line and line != _SEEN['memory']:
            _SEEN['memory'] = line
            parts.append(line)
    line = _dsh_line()
    if line and line != _SEEN['dsh']:
        _SEEN['dsh'] = line
        parts.append(line)
    text = '  '.join(part for part in parts if part)
    return text[:MAX_CHARS]


def log_report(text: str, state: str = ''):
    """把咕嘎真正说出口的汇报写进夜间回放日志（内存之外唯一留痕，便于早晨回看）。"""
    if not _enabled() or not str(text or '').strip():
        return
    try:
        import json
        from pathlib import Path
        path = Path(__file__).resolve().parents[2] / 'runtime' / 'super_agent' / 'reports.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(dict(at=round(time.time(), 3), state=state,
                                         text=str(text)[:800]), ensure_ascii=False) + '\n')
    except Exception:
        pass
