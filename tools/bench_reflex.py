"""Measure reflex.match() parse latency; no window, UIA or input is touched.

Run: python -X utf8 tools/bench_reflex.py
"""
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.reflex import match

SAMPLES = ('点这里', '点这块', '点一下这个', '点第2个', '点最后一个', '双击这里',
           '在这里输入hello', '输入你好', '往下滚', '滚到底', '按回车', '按Esc',
           '保存', '粘贴', '最大化', '关闭这个窗口', '我想点个外卖', '输入法怎么切换')
BUDGET_MS = 5.0


def run(iterations=1000):
    for text in SAMPLES:
        match(text)
    values = []
    started = time.perf_counter()
    for index in range(iterations):
        text = SAMPLES[index % len(SAMPLES)]
        at = time.perf_counter()
        command = match(text)
        values.append((time.perf_counter() - at) * 1000)
        if not command.get('matched') and text not in ('我想点个外卖', '输入法怎么切换'):
            raise SystemExit('bench sample stopped matching: ' + text)
    total_ms = (time.perf_counter() - started) * 1000
    values.sort()
    p50 = statistics.median(values)
    p95 = values[int(len(values) * 0.95)]
    print('reflex.match(): %d calls, total %.1f ms' % (iterations, total_ms))
    print('P50 %.4f ms  P95 %.4f ms  max %.4f ms' % (p50, p95, values[-1]))
    print('budget <%.1f ms: %s' % (BUDGET_MS, 'PASS' if p95 < BUDGET_MS else 'FAIL'))
    return 0 if p95 < BUDGET_MS else 1


if __name__ == '__main__':
    raise SystemExit(run())
