# -*- coding: utf-8 -*-
"""从磁盘确认咕嘎重启后会加载的代码：code_task 在不在、harness 开没开、运行时有没有分支。"""
import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import agent_harness  # noqa: E402
from core.self_agent import TOOLS  # noqa: E402
import ui.agent_runtime as runtime  # noqa: E402

names = [item['function']['name'] for item in TOOLS]
source = inspect.getsource(runtime)

checks = [
    ('code_task 在工具面', 'code_task' in names),
    ('工具面数量 == 28', len(names) == 28),
    ('harness 默认开启', agent_harness.enabled()),
    ('运行时 dispatch 有 code_task 分支', "name=='code_task'" in source),
    ('运行时把 check_cmd 透传下去', 'check_cmd=check_cmd' in source),
    ('运行时后台线程名字存在', 'guga-code-task' in source),
]
bad = 0
for label, ok in checks:
    print('[%s] %s' % ('PASS' if ok else 'FAIL', label))
    bad += 0 if ok else 1
print('工具面：%s' % '、'.join(sorted(names)))
sys.exit(1 if bad else 0)
