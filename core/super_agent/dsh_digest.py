# -*- coding: utf-8 -*-
"""让咕嘎自己的模型把 DSH 的原始事实翻成一句人话，而不是复述 DSH 的原文。

节奏约定（2026-11-11 起）
------------------------
主人要求：**聚焦 DSH 正在执行的那个任务，每隔 1 分钟用自然语言汇报一次当前情况。**
所以这里不再按「里程碑」触发，而是由 `core/dsh_link.py` 每 60 秒调用一次；
每次调用给一个递增的 `variant`，同一批事实也会换一种说法，避免每分钟念同一句。

原则：
- 只做「翻译 + 判断重要性」，不新增事实，不猜进度；
- 调用在后台线程，主线程只取缓存，绝不阻塞桌宠；
- 模型不可用/超时就返回空字符串，由调用方退回确定性文案。
"""
from __future__ import annotations

import threading

# 每分钟一次的常态汇报
_INSTRUCTION = (
    '你是咕嘎，一只小企鹅管家，正在替主人盯着一个外部 AI 工程师（DSH）干活。\n'
    '下面给你它此刻的最新事实。请用一到两句话向主人汇报（总长不超过 70 字）：\n'
    '1) 它现在在做什么、做到哪一步；\n'
    '2) 相比上一次有没有推进；\n'
    '3) 如果它卡住了或在等主人决定，就点明这一点。\n'
    '要求：不念工具名，不念英文原文，不复述它的原话，不提问，不客套，'
    '不说「我还在盯着」这类空话。只输出汇报正文，不要任何前缀。'
)

# 任务结束后的「下一步」组织
_NEXT_STEP_INSTRUCTION = (
    '你是咕嘎，一只小企鹅管家。主人交办的这轮 DSH 任务刚刚结束，你已经在自己的电脑上'
    '看过实际情况（下面给你验收结论）。请用一到两句话（总长不超过 70 字）告诉主人：\n'
    '1) 这一轮到底成没成，依据是什么；\n'
    '2) 你接下来准备做什么（下一步动作）。\n'
    '如果验收结论不是通过，必须说清缺什么、你要补什么，不要粉饰。\n'
    '不念工具名，不念英文，不客套。只输出正文。'
)

_CACHE = {}
_CACHE_LOCK = threading.Lock()
_INFLIGHT = set()
_MAX_CACHE = 60


def fingerprint(row, variant=0):
    """同一份 DSH 事实 + 同一个汇报序号只总结一次；心跳重复送来的旧事实不会重复消耗模型。"""
    return '|'.join(str(row.get(key) if row.get(key) is not None else '')
                    for key in ('tool', 'summary', 'last_answer', 'step', 'state')) + '|v' + str(int(variant or 0))


def _facts(row, elapsed):
    lines = []
    goal = str(row.get('goal') or '').strip()
    if goal:
        lines.append('主人交代的目标：' + goal[:160])
    tool = str(row.get('tool') or '').strip()
    if tool:
        lines.append('它正在调用的工具：' + tool[:80])
    summary = str(row.get('summary') or '').strip()
    if summary:
        lines.append('它最新的动作说明：' + summary[:200])
    answer = str(row.get('last_answer') or '').strip()
    if answer:
        lines.append('它刚才对主人说的话：' + answer[:300])
    step = row.get('step')
    if step is not None:
        lines.append('已经走到第 %s 步' % step)
    if elapsed:
        lines.append('这一轮已经跑了 %d 秒' % int(elapsed))
    return '\n'.join(lines)


def _post(cfg, messages, timeout, max_tokens):
    """一次非流式补全；失败一律返回空串。"""
    try:
        import requests
        from core.chat_answer import request_payload
        key = cfg.get('api_key')
        base = str(cfg.get('base_url') or '').rstrip('/')
        if not key or not base:
            return ''
        payload = request_payload(cfg, messages, 'quick')
        payload.update(stream=False, max_tokens=max_tokens, temperature=0.4)
        payload.pop('stream_options', None)
        headers = {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                   'User-Agent': 'GugaDesktopPet/1.0'}
        # opencode 网关强制要求 x-opencode-session，缺了直接 400 MissingSessionID：
        # 以前这里没带这个头，所以「模型组织的那句话」从来没成功过，一直退回固定文案。
        if 'opencode.ai' in base:
            headers['x-opencode-session'] = 'guga-dsh-digest'
        response = requests.post(
            base + '/chat/completions',
            headers=headers,
            json=payload, timeout=(3, timeout))
        try:
            if response.status_code != 200:
                return ''
            text = (response.json().get('choices') or [{}])[0].get('message', {}).get('content', '')
        finally:
            response.close()
        return ' '.join(str(text or '').split())[:200]
    except Exception:
        return ''


def _config():
    try:
        from core.model_settings import load_config
        return load_config()
    except Exception:
        return {}


def summarize(row, elapsed=0, timeout=12.0, variant=0):
    """同步调用（只在后台线程里用）。失败一律返回空串。"""
    try:
        instruction = _INSTRUCTION
        if variant:
            instruction += '\n（这是本任务的第 %d 次汇报，换一种说法，不要和上一次重复。）' % int(variant)
        return _post(_config(), [{'role': 'user',
                                  'content': instruction + '\n\n【事实】\n' + _facts(row, elapsed)}],
                     timeout, 180)
    except Exception:
        return ''


def next_step(row, verdict='', reasons=(), timeout=8.0):
    """任务结束后组织「下一步」。verdict 只能是 pass / fail / unverified。"""
    try:
        facts = [_facts(row, 0)]
        facts.append('独立验收结论：' + str(verdict or 'unverified'))
        for reason in list(reasons or [])[:4]:
            facts.append('验收说明：' + str(reason)[:160])
        return _post(_config(), [{'role': 'user',
                                  'content': _NEXT_STEP_INSTRUCTION + '\n\n【事实】\n' + '\n'.join(facts)}],
                     timeout, 220)
    except Exception:
        return ''


def cached(key):
    with _CACHE_LOCK:
        return _CACHE.get(key, '')


def request_async(row, elapsed=0, on_ready=None, variant=0):
    """后台生成一条总结；已有缓存或已在飞就不再重复请求。"""
    import os
    # 测试/现场关闭超级智能体时不做任何模型调用，汇报退回确定性文案。
    if os.environ.get('GUGA_SUPER_AGENT', 'on').strip().lower() in ('off', '0', 'false', 'no'):
        return ''
    key = fingerprint(row, variant)
    with _CACHE_LOCK:
        if key in _CACHE or key in _INFLIGHT:
            return ''
        _INFLIGHT.add(key)
    snapshot = dict(row)

    def work():
        line = summarize(snapshot, elapsed, variant=variant)
        with _CACHE_LOCK:
            _INFLIGHT.discard(key)
            if line:
                _CACHE[key] = line
                while len(_CACHE) > _MAX_CACHE:
                    _CACHE.pop(next(iter(_CACHE)))
        if line and callable(on_ready):
            try:
                on_ready(key, line)
            except Exception:
                pass

    threading.Thread(target=work, name='guga-dsh-digest', daemon=True).start()
    return key
