# -*- coding: utf-8 -*-
"""路线4 真协议验收：咕嘎 StreamableHTTPMCP ↔ dsh-as-mcp-server（真实现，非桩）。

用法：
  python -X utf8 tools/probe_mcp_bridge_live.py [--url http://127.0.0.1:51999/mcp]

不需要装进 DSH：可以先起插件自带的演示服务 `node dsh/mcp.js 51999` 再跑本探针。
"""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.mcp_host import MCPHost, StreamableHTTPMCP  # noqa: E402


def timed(label, fn):
    started = time.perf_counter()
    result = fn()
    ms = (time.perf_counter() - started) * 1000
    print('%-22s %7.1fms' % (label, ms))
    return result, ms


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:51999/mcp')
    parser.add_argument('--skip-lock', action='store_true',
                        help='对真实 DSH 端口运行时必须加：持锁会让 DSH 自己的 write/edit/git 被拒')
    args = parser.parse_args(argv)
    failures = []

    client = StreamableHTTPMCP(dict(url=args.url, timeout=20))
    info, _ = timed('initialize', client.initialize)
    server_name = str((info.get('serverInfo') or {}).get('name') or '')
    print('  server=%s protocol=%s session=%s' % (
        server_name, client.protocol, client.session_id or '<无>'))
    if 'mcp' not in server_name.lower():
        failures.append('initialize 返回的 serverInfo 不像这个桥：%r' % server_name)

    listing, _ = timed('tools/list', lambda: client.request('tools/list', {}))
    tools = [str(tool.get('name')) for tool in (listing.get('tools') or [])]
    print('  tools=%d %s' % (len(tools), ','.join(tools)))
    expected = {'dsh_get_status', 'dsh_get_intent', 'dsh_set_intent', 'dsh_lock_acquire',
                'dsh_lock_release', 'dsh_lock_status', 'dsh_inject_alert', 'dsh_review_request'}
    missing = expected - set(tools)
    if missing:
        failures.append('缺工具：%s' % ','.join(sorted(missing)))

    def call(name, arguments=None):
        result = client.request('tools/call', dict(name=name, arguments=arguments or {}))
        text = ''
        for block in result.get('content') or []:
            if isinstance(block, dict) and block.get('type') == 'text':
                text += block.get('text') or ''
        if result.get('isError'):
            raise RuntimeError(text[:200] or '工具返回错误')
        return text

    status, _ = timed('dsh_get_status', lambda: call('dsh_get_status'))
    print('  status=%s' % status[:160])

    timed('dsh_set_intent', lambda: call('dsh_set_intent',
                                         dict(text='咕嘎自检：读一下状态', by='guga')))
    intent, _ = timed('dsh_get_intent', lambda: call('dsh_get_intent'))
    print('  intent=%s' % intent[:160])
    if '咕嘎自检' not in intent:
        failures.append('意图回读不一致：%r' % intent[:120])

    if args.skip_lock:
        print('dsh_lock_acquire      SKIP（对真实 DSH 端口跳过：持锁会挡住 DSH 自己的写入）')
    else:
        timed('dsh_lock_acquire', lambda: call('dsh_lock_acquire', dict(owner='guga', ttlMs=60_000)))
        lock, _ = timed('dsh_lock_status', lambda: call('dsh_lock_status'))
        print('  lock=%s' % lock[:160])
        timed('dsh_lock_release', lambda: call('dsh_lock_release', dict(owner='guga')))
        after, _ = timed('dsh_lock_status', lambda: call('dsh_lock_status'))
        print('  lock_after=%s' % after[:160])

    # MCPHost 走配置文件的真实入口（含传输选择与工具白名单）
    config = {'mcpServers': {'dsh-bridge': {
        'url': args.url, 'enabled': True,
        'allowed_tools': ['dsh_get_status', 'dsh_get_intent', 'dsh_lock_status'],
        'routine_tools': ['dsh_get_status'],
        'confirmation_tools': ['dsh_set_intent']}}}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'mcp_servers.json'
        path.write_text(json.dumps(config, ensure_ascii=False), encoding='utf-8')
        host = MCPHost(path)
        listed = host.execute(dict(action='list_servers'))
        print('HOST transport=%s' % listed['servers'][0]['transport'])
        try:
            result = host.execute(dict(action='call', server='dsh-bridge', tool='dsh_get_status'))
            ok = not result.get('isError')
        except Exception as exc:  # noqa: BLE001
            ok = False
            print('HOST call 失败：%s' % str(exc)[:160])
        print('HOST dsh_get_status ok=%s' % ok)
        if not ok:
            failures.append('MCPHost 入口调用失败')
        try:
            host.execute(dict(action='call', server='dsh-bridge', tool='dsh_set_intent',
                              arguments=dict(intent='x')))
            blocked = False
        except ValueError as exc:
            blocked = 'allowed_tools' in str(exc)
        print('HOST 未授权工具被拦=%s' % blocked)
        if not blocked:
            failures.append('白名单没拦住未授权工具')

    client.close()
    print()
    if failures:
        print('FAIL')
        for item in failures:
            print('  - ' + item)
        return 2
    print('PASS 咕嘎 StreamableHTTPMCP ↔ 真实 dsh-as-mcp-server 协议互通')
    return 0


if __name__ == '__main__':
    sys.exit(main())
