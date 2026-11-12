# -*- coding: utf-8 -*-
"""端到端验收：让咕嘎的编码通道在本机 DSH 上真的改一个小项目并跑通测试。

不伪造任何一步：项目是现建的、DSH 是真跑的、测试是真执行的。
"""
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.coding_agent import CodingAgent  # noqa: E402

PROJECT_FILES = {
    'mathx.py': 'def add(a, b):\n    raise NotImplementedError("还没实现")\n',
    'test_mathx.py': 'from mathx import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n',
    'pytest.ini': '[pytest]\nquiet_mode = true\n',
}


def main():
    folder = Path(tempfile.mkdtemp(prefix='guga-code-accept-'))
    for name, body in PROJECT_FILES.items():
        (folder / name).write_text(body, encoding='utf-8')
    print('项目目录: %s' % folder)
    before = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'pytest', '-q', '--no-header'],
                            cwd=str(folder), capture_output=True, text=True, timeout=120)
    print('改动前 pytest exit=%s（应为非 0，因为 add 还没实现）' % before.returncode)

    task = ('mathx.py 里的 add(a, b) 现在抛 NotImplementedError。'
            '请把它实现成返回 a+b，然后运行 pytest 确认 test_add 通过。')
    started = time.time()
    report = CodingAgent(timeout=900, poll_seconds=5).run(task, str(folder))
    elapsed = round(time.time() - started, 1)

    after = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'pytest', '-q', '--no-header'],
                           cwd=str(folder), capture_output=True, text=True, timeout=120)
    print('改动后 pytest exit=%s' % after.returncode)
    print('输出尾部: %s' % (after.stdout or after.stderr).strip().splitlines()[-1:])
    print('DSH 报告: stopped=%s ok=%s elapsed=%ss polls=%s' % (
        report.get('stopped'), report.get('ok'), elapsed, report.get('polls')))
    print('会话: %s' % report.get('session_id'))
    print('回答尾部: %s' % str(report.get('answer') or '')[-300:].replace('\n', ' '))

    verdict = dict(
        project=str(folder),
        before_exit=before.returncode,
        after_exit=after.returncode,
        agent_stopped=report.get('stopped'),
        agent_ok=report.get('ok'),
        session_id=report.get('session_id'),
        elapsed=elapsed,
        passed=(before.returncode != 0 and after.returncode == 0),
    )
    print()
    print('@@RESULT@@' + json.dumps(verdict, ensure_ascii=False))
    shutil.rmtree(folder, ignore_errors=True)
    return 0 if verdict['passed'] else 2


if __name__ == '__main__':
    sys.exit(main())
