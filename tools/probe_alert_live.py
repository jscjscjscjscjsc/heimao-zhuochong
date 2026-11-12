# -*- coding: utf-8 -*-
"""路线4 最有意思的一环：外部在 DSH 跑到一半时插话（dsh_inject_alert）。

走咕嘎的生态入口（MCPHost），验证：注入送达 → alertQueue / 中途注入的行为 → 状态可读。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.mcp_host import MCPHost  # noqa: E402

MESSAGE = ('【咕嘎桥自检】这条提醒由咕嘎通过 dsh_inject_alert 注入，'
           '用来验证「外部可以在 DSH 跑到一半时插话」。收到后不需要执行任何动作。')


def text_of(result):
    return ''.join(block.get('text', '') for block in (result or {}).get('content') or []
                   if isinstance(block, dict) and block.get('type') == 'text')


def main():
    host = MCPHost()
    approved = []
    started = time.perf_counter()
    result = host.execute(
        dict(action='call', server='dsh-bridge', tool='dsh_inject_alert',
             arguments=dict(message=MESSAGE, severity='low')),
        approve=lambda ctx: (approved.append(ctx), True)[1])
    elapsed = (time.perf_counter() - started) * 1000
    print('inject %.1fms approved=%d' % (elapsed, len(approved)))
    print('result=%s' % text_of(result)[:300].replace('\n', ' '))

    time.sleep(1.5)
    status = host.execute(dict(action='call', server='dsh-bridge', tool='dsh_get_status'))
    payload = json.loads(text_of(status))
    print('after  turn=%s status=%s' % (payload.get('turn'), payload.get('status')))
    print('       alertQueue=%s' % json.dumps(payload.get('alertQueue'), ensure_ascii=False)[:240])
    return 0


if __name__ == '__main__':
    sys.exit(main())
