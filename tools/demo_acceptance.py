# -*- coding: utf-8 -*-
"""真机演示：拿本轮自己的验收跑一遍零模型台账。

用法（项目根）：
  python -X utf8 tools/demo_acceptance.py [--session <dsh session id>]
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent.acceptance import AcceptanceLedger  # noqa: E402

PY = sys.executable


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--session', default='', help='关联一个真实的 DSH 会话编号')
    parser.add_argument('--out', default='')
    args = parser.parse_args(argv)

    ledger = AcceptanceLedger()
    record = ledger.open(
        goal='本轮新增：零模型验收台账 core/super_agent/acceptance.py + 10 个契约测试，并跑通全量回归',
        session_id=args.session,
        cwd=str(ROOT),
        checks=[dict(label='验收台账自身的契约测试',
                     command=[PY, '-X', 'utf8', '-m', 'pytest', 'tools/test_acceptance.py', '-q', '--no-header'])],
    )
    print('OPEN     id=%s' % record['id'])

    view = None
    if args.session:
        try:
            from core.super_agent.dsh_gateway import DshGateway
            view = DshGateway(timeout=10.0).session_view(args.session)
            print('VIEW     ok session=%s running=%s turns=%s' % (
                args.session, view.get('running'), len(view.get('turn_outline') or [])))
        except Exception as exc:  # noqa: BLE001
            print('VIEW     failed：%s %s' % (type(exc).__name__, str(exc)[:160]))
    else:
        print('VIEW     未关联会话（本次只验证检查命令 + 工作区改动）')

    ledger.claim(record['id'], '我（DSH）认为这一轮已经完成——但这句话只留档，不参与判定。')
    ledger.observe(record['id'], view)
    print('OBSERVE  workspace_available=%s changed_files=%s' % (
        (ledger.load(record['id'])['workspace'] or {}).get('available'),
        (ledger.load(record['id'])['workspace'] or {}).get('changed_files')))

    ledger.run_checks(record['id'])
    for result in ledger.load(record['id'])['check_results']:
        print('CHECK    ok=%s exit=%s %.0fms %s' % (
            result.get('ok'), result.get('exit'), result.get('elapsed_ms') or 0,
            result.get('label')))
    verdict = ledger.judge(record['id'])
    print('VERDICT  %s' % verdict['verdict'])
    for reason in verdict['reasons']:
        print('  - %s' % reason)

    target = args.out or str(ROOT / 'runtime' / 'acceptance' / ('%s.md' % record['id']))
    exported = ledger.export(record['id'], target)
    print('EXPORT   %s' % exported.get('path'))
    return 0 if verdict['verdict'] == 'pass' else 1


if __name__ == '__main__':
    sys.exit(main())
