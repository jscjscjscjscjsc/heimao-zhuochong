# -*- coding: utf-8 -*-
"""路线3 最小闭环真机验收：新建会话 → 投递指令 → 观察它跑完并读回结论。

用法（项目根）：
  python -X utf8 tools/probe_session_create.py                 # 新建一个会话再投递
  python -X utf8 tools/probe_session_create.py --reuse <id>    # 往已有会话投一轮，不新增会话

结论以 turn_outline 里的真实 response 为准；running=false 且已有 response 才算完成。
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent import dsh_gateway as gw  # noqa: E402

INSTRUCTION = '这是咕嘎托管链路的一次自检：请只回复 reply=ok，不要调用任何工具，立即结束。'


def describe(view):
    view = view if isinstance(view, dict) else {}
    running = bool(view.get('running'))
    outline = view.get('turn_outline') or []
    last = outline[-1] if isinstance(outline, list) and outline else {}
    last = last if isinstance(last, dict) else {}
    return dict(running=running, turns=len(outline) if isinstance(outline, list) else 0,
                response=str(last.get('response') or ''), title=str(view.get('title') or ''),
                tokens=(view.get('token_usage') or {}))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--reuse', default='', help='往这个已有会话投递，不新建')
    parser.add_argument('--wait', type=float, default=90.0)
    args = parser.parse_args(argv)
    client = gw.DshGateway(timeout=12.0)

    if args.reuse:
        session_id = args.reuse
        print('REUSE session_id=%s' % session_id)
    else:
        started = time.perf_counter()
        created = client.create_session(conversation_id='selfcheck')
        print('CREATE ok=%s session_id=%s preset=%s %.0fms' % (
            created['ok'], created['session_id'], created.get('agent_preset'),
            (time.perf_counter() - started) * 1000))
        session_id = created['session_id']

    baseline_turns = describe(client.session_view(session_id))['turns']
    started = time.perf_counter()
    client.prompt(session_id, INSTRUCTION, mode='queue')
    print('PROMPT ok=True %.0fms baseline_turns=%d' % (
        (time.perf_counter() - started) * 1000, baseline_turns))

    deadline = time.monotonic() + args.wait
    observed_running = False
    while time.monotonic() < deadline:
        time.sleep(1.0)
        info = describe(client.session_view(session_id))
        if info['running']:
            observed_running = True
        if info['turns'] > baseline_turns and info['response'] and not info['running']:
            print('DONE title=%s turns=%d observed_running=%s response=%r' % (
                info['title'], info['turns'], observed_running, info['response'][:120]))
            print('TOKENS', info['tokens'])
            print('OWNED=%s OWNER=%s' % (session_id in gw.owned_sessions(), gw.owner_of(session_id)))
            return 0

    print('TIMEOUT %.0fs 内没有观察到新的一轮完成；不假装完成' % args.wait)
    return 3


if __name__ == '__main__':
    sys.exit(main())
