# -*- coding: utf-8 -*-
"""真机一轮：在真实（非临时）项目上跑咕嘎的编码通道，并留下验收台账。

跑法：python -u -X utf8 tools/probe_code_round.py "<项目目录>" "<任务>" "<check_cmd>"
"""
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.coding_agent import CodingAgent, write_receipt  # noqa: E402


def changed_files(cwd):
    """这一轮到底动了哪些文件：git 说了算，不是 agent 说了算。"""
    try:
        out = subprocess.run(['git', 'status', '--porcelain'], cwd=cwd,
                             capture_output=True, text=True, timeout=60)
        if out.returncode != 0:
            return []
        names = []
        for line in (out.stdout or '').splitlines():
            name = line[3:].strip().strip('"')
            if name and name not in ('DONE.json', 'FAILED.json'):
                names.append(name)
        return names
    except (OSError, subprocess.SubprocessError):
        return []


def main(argv):
    cwd = argv[1] if len(argv) > 1 else r'C:\Users\Admin（无密码）\Desktop\Vibe oding\demo-code-task'
    task = argv[2] if len(argv) > 2 else (
        'slug.py 里的 slugify(text) 现在抛 NotImplementedError。'
        '请实现它：小写、去掉标点、连续空白压成一个连字符、首尾不留连字符。'
        '例子：slugify("Hello World!") == "hello-world"，slugify("  A  B  ") == "a-b"。'
        '实现完运行 pytest 确认两个测试都过。')
    check = argv[3] if len(argv) > 3 else 'pytest -q'

    print('项目：%s' % cwd, flush=True)
    started = time.time()
    report = CodingAgent(timeout=1200, poll_seconds=5).run(task, cwd, check_cmd=check)
    report.setdefault('task', task[:200])
    report['wall_seconds'] = round(time.time() - started, 1)
    report['changed_files'] = changed_files(cwd)
    row = write_receipt(report)
    print('台账行：%s' % json.dumps(row, ensure_ascii=False)[:400], flush=True)

    after = subprocess.run([sys.executable, '-X', 'utf8', '-m'] + check.split(),
                           cwd=cwd, capture_output=True, text=True, timeout=300)
    tail = ((after.stdout or '') + (after.stderr or '')).strip().splitlines()[-2:]
    print('独立复跑 %s → exit=%s  %s' % (check, after.returncode, ' | '.join(tail)), flush=True)
    print('@@RESULT@@' + json.dumps(dict(ok=bool(report.get('ok')),
                                        stopped=report.get('stopped'),
                                        check_exit=report.get('check_exit'),
                                        independent_exit=after.returncode,
                                        session_id=report.get('session_id'),
                                        wall_seconds=report['wall_seconds']),
                                   ensure_ascii=False), flush=True)
    return 0 if after.returncode == 0 else 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
