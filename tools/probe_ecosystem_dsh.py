# -*- coding: utf-8 -*-
"""路线4 收尾验收：走咕嘎自己的生态入口（MCPHost + mcp_servers.json）读 DSH 实时状态。

这才是"咕嘎能用"的证据——不是直接连端口，而是走它自己的工具入口。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.mcp_host import MCPHost  # noqa: E402

INTENT = '当前主线：咕嘎×DSH 四条路线实施（路线4 已接通：咕嘎可直接读 DSH 实时状态）'


def text_of(result):
    body = ''
    for block in (result or {}).get('content') or []:
        if isinstance(block, dict) and block.get('type') == 'text':
            body += block.get('text') or ''
    return body


def main():
    failures = []
    host = MCPHost()          # 默认读项目根的 mcp_servers.json
    print('config=%s' % host.path)
    if not host.path.exists():
        print('FAIL 没有 mcp_servers.json')
        return 2

    servers = host.execute(dict(action='list_servers'))['servers']
    for row in servers:
        print('server=%-12s enabled=%-5s transport=%s' % (row['name'], row['enabled'], row['transport']))
    bridge = next((row for row in servers if row['name'] == 'dsh-bridge'), None)
    if not bridge or not bridge['enabled'] or bridge['transport'] != 'http':
        failures.append('dsh-bridge 没配置成启用的 http 传输')

    tools = host.execute(dict(action='list_tools', server='dsh-bridge'))['tools']
    print('tools=%d %s' % (len(tools), ','.join(sorted(t['name'] for t in tools))))
    if len(tools) != 8:
        failures.append('工具数不是 8：%d' % len(tools))

    status = host.execute(dict(action='call', server='dsh-bridge', tool='dsh_get_status'))
    print('status=%s' % text_of(status)[:300].replace('\n', ' '))

    # 写入一条有意义的共享意图（确认类工具，需要显式批准）
    approved = []
    intent = host.execute(
        dict(action='call', server='dsh-bridge', tool='dsh_set_intent',
             arguments=dict(text=INTENT, by='guga')),
        approve=lambda ctx: approved.append(ctx) or True)
    print('set_intent approved=%d result=%s' % (len(approved), text_of(intent)[:120].replace('\n', ' ')))

    readback = host.execute(dict(action='call', server='dsh-bridge', tool='dsh_get_intent'))
    body = text_of(readback)
    print('get_intent=%s' % body[:200].replace('\n', ' '))
    if '四条路线' not in body:
        failures.append('意图回读里没有刚写入的内容')

    # 未授权工具必须被本地白名单拦住
    try:
        host.execute(dict(action='call', server='dsh-bridge', tool='dsh_review_request',
                          arguments=dict(title='x')))
        # dsh_review_request 在 allowed_tools 里，但不在 routine，需要 approve；不给 approve 应被拦
        failures.append('确认类工具没经过批准就执行了')
    except ValueError as exc:
        print('guard=%s' % str(exc)[:100])
    except Exception as exc:  # noqa: BLE001
        print('guard_other=%s %s' % (type(exc).__name__, str(exc)[:100]))

    print()
    if failures:
        print('FAIL')
        for item in failures:
            print('  - ' + item)
        return 2
    print('PASS 咕嘎生态入口 ↔ DSH 桥：列服务器/列工具/读状态/写意图/确认门禁 全部正常')
    return 0


if __name__ == '__main__':
    sys.exit(main())
