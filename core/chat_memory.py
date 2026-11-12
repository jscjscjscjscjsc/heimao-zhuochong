"""Bounded read-only LingShu recall for companion answers, independent of DSH UI."""
import atexit
import hashlib
import os
import threading
import time
from core.agent_memory import safe_text

_LOCK=threading.Lock()
_CACHE={}
_INFLIGHT=None
_READER=None


def invalidate():
    """A confirmed explicit write invalidates recall without cancelling live reads."""
    with _LOCK:_CACHE.clear()


def close():
    global _READER
    reader,_READER=_READER,None
    if reader is not None:reader.close()


atexit.register(close)


def _read(query):
    global _READER
    from core.super_agent.lingshu_bridge import LingshuBridge
    try:
        if _READER is None:
            _READER=LingshuBridge(timeout=6,startup_timeout=8)
            _READER.start()
        result=_READER.recall(query)
        rows=[]
        for node in (result.get('pack') or [])[:4]:
            text=str(node.get('content','')).strip()
            if text and safe_text(text)==text:rows.append(text[:900])
        return dict(state='ok' if rows else 'empty',items=rows)
    except Exception:
        close()
        raise


def recall_context(query,agent_id='001',wait_seconds=5.0):
    """Wait only in the answer worker; never reuse another query's cached result."""
    global _INFLIGHT
    query=str(query or '').strip()
    if str(agent_id)!='001' or not query or safe_text(query)!=query:
        return dict(state='skipped',items=[])
    if os.environ.get('GUGA_LINGSHU','on').lower() in ('off','0','false','no'):
        return dict(state='disabled',items=[])
    key=hashlib.sha256(query.encode()).hexdigest()
    with _LOCK:
        cached=_CACHE.get(key)
        if cached and time.monotonic()-cached['at']<90:return dict(cached['result'])
        if _INFLIGHT and _INFLIGHT[0]!=key:return dict(state='busy',items=[])
        if _INFLIGHT:event=_INFLIGHT[1]
        else:
            event=threading.Event();_INFLIGHT=(key,event)
            def work():
                global _INFLIGHT
                try:result=_read(query)
                except Exception as exc:result=dict(state='unavailable',items=[],error_type=type(exc).__name__)
                with _LOCK:
                    if len(_CACHE)>=32:_CACHE.pop(next(iter(_CACHE)))
                    _CACHE[key]=dict(at=time.monotonic(),result=result)
                    _INFLIGHT=None
                event.set()
            threading.Thread(target=work,daemon=True,name='guga-chat-memory').start()
    if not event.wait(max(0,min(float(wait_seconds),6))):return dict(state='pending',items=[])
    with _LOCK:return dict(_CACHE[key]['result'])
