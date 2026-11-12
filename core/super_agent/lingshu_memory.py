# -*- coding: utf-8 -*-
"""咕嘎侧灵枢记忆适配器（懒加载、后台线程、失败静默）。

- 读：后台线程用灵枢桥召回相关记忆，30s 汇报里带一句「灵枢记忆里还留着…」；
- 写：DSH 任务完成时把一条 CCG 合规的记忆写回同一份认知图；
- 全部非阻塞：桥进程首次使用时才拉起，任何失败只写审计日志，不影响桌宠。
"""
from __future__ import annotations

import atexit
import json
import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOG = ROOT / 'runtime' / 'super_agent' / 'lingshu.jsonl'
CACHE_SECONDS = 90.0
WRITE_THROTTLE_SECONDS = 120.0

_LOCK = threading.RLock()
_BRIDGE = None
_REFRESH_THREAD = None
_STATE = dict(items=[], at=0.0, refreshing=False, error='', writes=0, last_write=0.0)


def _enabled() -> bool:
    """两把总开关任一关闭就不碰记忆。

    `GUGA_SUPER_AGENT=off` 是「整套超级智能体关闭」（测试/现场静默），
    以前只认 `GUGA_LINGSHU`，导致测试跑一遍就把假文案写进主人的真实认知图。
    """
    for name in ('GUGA_SUPER_AGENT', 'GUGA_LINGSHU'):
        if os.environ.get(name, 'on').strip().lower() in ('off', '0', 'false', 'no'):
            return False
    return True


def _log(event: str, payload: dict):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(dict(at=round(time.time(), 3), event=event, payload=payload),
                                    ensure_ascii=False) + '\n')
    except Exception:
        pass


def _bridge():
    global _BRIDGE
    with _LOCK:
        if _BRIDGE is None:
            from core.super_agent.lingshu_bridge import LingshuBridge, default_token
            bridge = LingshuBridge(token=default_token(), timeout=120.0)
            bridge.start()
            _BRIDGE = bridge
        return _BRIDGE


def _close_bridge():
    global _BRIDGE
    with _LOCK:
        bridge, _BRIDGE = _BRIDGE, None
    if bridge is not None:
        try:
            bridge.close()
        except Exception:
            pass


atexit.register(_close_bridge)


def _plain_text(content: str) -> str:
    lines = [line.strip() for line in str(content or '').splitlines() if line.strip()]
    body = [line for line in lines if not line.startswith('#')]
    text = ' '.join(body)[:220]
    return text or ' '.join(lines)[:220]


def _extract_items(pack) -> list:
    items = []
    for node in (pack or [])[:4]:
        text = _plain_text(node.get('content', ''))
        if text:
            items.append(dict(id=str(node.get('id', '')), text=text))
    return items


def refresh_async(query: str = ''):
    """后台召回，不阻塞调用方。"""
    global _REFRESH_THREAD
    if not _enabled():
        return
    with _LOCK:
        if _STATE['refreshing']:
            return
        _STATE['refreshing'] = True

    def work():
        try:
            data = _bridge().recall(query or '咕嘎 DSH 记忆桥 任务 进展 屏幕')
            items = _extract_items(data.get('pack'))
            with _LOCK:
                _STATE.update(items=items, at=time.time(), error='')
            _log('recall', dict(query=query, items=len(items)))
        except Exception as exc:  # noqa: BLE001
            with _LOCK:
                _STATE['error'] = type(exc).__name__ + ': ' + str(exc)[:200]
            _log('recall_error', dict(query=query, error=str(exc)[:300]))
        finally:
            with _LOCK:
                _STATE['refreshing'] = False

    _REFRESH_THREAD = threading.Thread(target=work, name='guga-lingshu-recall', daemon=True)
    _REFRESH_THREAD.start()


def recall_sentence(query: str = '') -> str:
    """给 30s 汇报用的一句记忆摘要；缓存为空时触发后台召回并返回空串。"""
    if not _enabled():
        return ''
    with _LOCK:
        items = list(_STATE['items'])
        at = _STATE['at']
    if not items:
        refresh_async(query)
        return ''
    if time.time() - at > CACHE_SECONDS * 2:
        refresh_async(query)
    text = items[0].get('text', '')
    if not text:
        return ''
    return '对了，灵枢记忆里还留着一条：' + text[:130]


def remember_async(title: str, body: str, layer: str = 'contextual'):
    """后台写回一条 CCG 合规记忆；两分钟内最多一条，避免刷图。"""
    if not _enabled():
        return
    now = time.time()
    with _LOCK:
        if now - _STATE.get('last_write', 0.0) < WRITE_THROTTLE_SECONDS:
            return
        _STATE['last_write'] = now

    def work():
        try:
            result = _bridge().remember(
                '', layer=layer, tags=['super-agent', 'dsh-task'], importance=0.55,
                ccg=dict(title=str(title)[:80], body=str(body)[:600]))
            ok = bool(result.get('ok'))
            node = str(result.get('id', ''))
            verdict = (result.get('verdict') or {}).get('state', '')
            with _LOCK:
                if ok:
                    _STATE['writes'] = _STATE.get('writes', 0) + 1
            _log('write', dict(title=title, ok=ok, node=node, verdict=verdict))
        except Exception as exc:  # noqa: BLE001
            _log('write_error', dict(title=title, error=str(exc)[:300]))

    threading.Thread(target=work, name='guga-lingshu-write', daemon=True).start()


def status() -> dict:
    with _LOCK:
        return dict(enabled=_enabled(), cached=len(_STATE['items']),
                    cache_age_seconds=round(time.time() - _STATE['at'], 1) if _STATE['at'] else None,
                    refreshing=_STATE['refreshing'], writes=_STATE.get('writes', 0),
                    error=_STATE.get('error', ''), log=str(LOG))


def handle(action: str = 'status', query: str = '', title: str = '', content: str = '') -> dict:
    action = str(action or 'status').strip().lower()
    if action == 'status':
        return dict(ok=True, **status())
    if action == 'recall':
        refresh_async(query)
        return dict(ok=True, **status(), hint='召回在后台运行；稍后看 status 或汇报里的记忆句')
    if action in ('remember', 'write'):
        remember_async(title or '咕嘎记录', content or query)
        return dict(ok=True, **status(), hint='写入在后台运行；结果看审计日志')
    return dict(ok=False, error='支持的 action：status / recall / remember')
