# -*- coding: utf-8 -*-
"""CLI：干跑/回放一个已保存的可执行工作流，打印逐步耗时与核验结果。

用法（项目根目录）：
  python -X utf8 tools/play_action_workflow.py doubao-browser-blank-tab --dry-run
  python -X utf8 tools/play_action_workflow.py doubao-browser-blank-tab
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.action_workflow import Player, WorkflowError, WorkflowStore


def main(argv=None):
    parser = argparse.ArgumentParser(description='回放一个可执行桌面工作流')
    parser.add_argument('slug', help='runtime/workflows/<slug>.json 里的 slug')
    parser.add_argument('--dry-run', action='store_true',
                        help='只解析窗口/选择器，不发送任何输入')
    parser.add_argument('--json', action='store_true', help='只输出 JSON 报告')
    args = parser.parse_args(argv)
    store = WorkflowStore()
    try:
        report = Player(store=store).play(args.slug, dry_run=args.dry_run)
    except WorkflowError as exc:
        print('PLAY_FAILED', str(exc)[:500])
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
        return 0 if report.get('ok') else 1
    mode = 'DRY_RUN' if report['dry_run'] else 'PLAY'
    print('%s slug=%s ok=%s total=%.0fms first_step=%sms '
          'verified=%d/%d rate=%.0f%%' % (
              mode, report['slug'], report['ok'], report.get('total_ms') or 0,
              report.get('first_step_ms'), report.get('verified_steps'),
              report['total_steps'], (report.get('success_rate') or 0) * 100))
    for step in report.get('steps') or []:
        print('  %02d %-7s %6.0fms %s %s' % (
            step.get('index'), step.get('op'), step.get('elapsed_ms') or 0,
            'OK' if step.get('verified', step.get('resolved')) else 'NO',
            step.get('title')))
    if report.get('stuck_at'):
        print('STUCK_AT', report['stuck_at'], 'ERROR', report.get('error'))
    if report.get('last_ok_at'):
        print('LAST_OK_AT', report['last_ok_at'])
    return 0 if report.get('ok') else 1


if __name__ == '__main__':
    sys.exit(main())
