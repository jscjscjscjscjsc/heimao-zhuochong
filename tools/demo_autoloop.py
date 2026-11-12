# -*- coding: utf-8 -*-
"""真机演示：让托管循环在真实 DSH 会话上跑完整闭环。

  python -X utf8 tools/demo_autoloop.py --mode pass
  python -X utf8 tools/demo_autoloop.py --mode fail --session <已有会话编号>

pass 模式：检查命令真实通过 → 循环应第 1 轮就停并判定 passed。
fail 模式：检查命令故意失败 → 循环应追问一次、同一原因再现后停手（no_progress）。
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent.acceptance import AcceptanceLedger  # noqa: E402
from core.super_agent.autoloop import AutoLoop  # noqa: E402
from core.super_agent.dsh_gateway import DshGateway  # noqa: E402

PY = sys.executable
PROMPT = '这是咕嘎托管循环的一次自检：请只回复 reply=ok，不要调用任何工具，立即结束。'


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('pass', 'fail'), default='pass')
    parser.add_argument('--session', default='', help='复用已有会话，避免再建新的')
    parser.add_argument('--max-rounds', type=int, default=3)
    parser.add_argument('--wait', type=float, default=120.0, help='每轮等待上限秒数')
    args = parser.parse_args(argv)

    if args.mode == 'pass':
        checks = [dict(label='验收台账契约测试',
                       command=[PY, '-X', 'utf8', '-m', 'pytest', 'tools/test_acceptance.py',
                                '-q', '--no-header'])]
    else:
        checks = [dict(label='故意失败的检查',
                       command=[PY, '-c', 'raise SystemExit(1)'])]

    gateway = DshGateway(timeout=12.0)
    # 失败模式故意用「一句话就结束」的追问，让状态机可判定；
    # 真实使用时用默认 FOLLOW_UP_TEMPLATE（会要求 DSH 真去修，轮次会长）。
    follow_up = None
    if args.mode == 'fail':
        follow_up = ('上一轮验收没过（检查退出码 1）。请只回复 reply=still-failing，'
                     '不要调用任何工具，立即结束。')
    loop = AutoLoop(ledger=AcceptanceLedger(), gateway=gateway,
                    max_rounds=args.max_rounds, max_same_reason=2,
                    poll_seconds=1.2, wait_seconds=args.wait, follow_up=follow_up)
    report = loop.run(goal='托管循环自检（%s 模式）' % args.mode, prompt=PROMPT,
                      cwd=str(ROOT), checks=checks,
                      conversation_id='selfcheck', session_id=args.session)

    print('STOPPED  %s' % report['stopped'])
    print('PASSED   %s' % report['passed'])
    print('DETAIL   %s' % report['detail'])
    for row in report['rounds']:
        print('ROUND %d verdict=%s changed=%s record=%s' % (
            row.get('index'), row.get('verdict'), row.get('changed'), row.get('record')))
        for reason in (row.get('reasons') or [])[:4]:
            print('        - %s' % reason)
    expected = 'passed' if args.mode == 'pass' else 'no_progress'
    print('EXPECT   %s' % expected)
    return 0 if report['stopped'] == expected else 2


if __name__ == '__main__':
    sys.exit(main())
