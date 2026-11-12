"""重复运行 smolagents 对话台内核，量化稳定性（真实模型，假 bridge）。

用法：python tools/probe_console_stability.py --runs 5
每次运行都是一次真实的模型请求 + 一次真实工具转发，不做 mock。
"""
import argparse
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent_console import ConsoleAgent


class Bridge:
    def __init__(self):
        self.calls = []

    def call(self, name, args, cancel):
        self.calls.append(name)
        if name == 'app_state':
            return {'ok': True, 'result': {'pet': {'visible': True}}}
        return {'ok': False, 'error': 'unknown tool ' + str(name)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=5)
    parser.add_argument('--prompt', default='调用 guga_tool 读取 app_state，然后只回复“可见”或“不可见”')
    args = parser.parse_args()
    results = []
    for index in range(args.runs):
        bridge = Bridge()
        agent = ConsoleAgent(bridge)
        started = time.monotonic()
        outcome = agent.run(args.prompt)
        elapsed = round((time.monotonic() - started) * 1000)
        item = dict(run=index + 1, ok=bool(outcome.get('ok')), steps=outcome.get('steps'),
                    ms=elapsed, tool_calls=bridge.calls, events=len(agent.events),
                    error=str(outcome.get('error') or '')[:160])
        results.append(item)
        print(json.dumps(item, ensure_ascii=False), flush=True)
    ok = sum(1 for item in results if item['ok'])
    summary = dict(runs=len(results), ok=ok, fail=len(results) - ok,
                   success_rate=round(ok / len(results), 3),
                   avg_ms=round(statistics.mean(item['ms'] for item in results)),
                   tool_called=sum(1 for item in results if item['tool_calls']))
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if ok == len(results) else 1


if __name__ == '__main__':
    sys.exit(main())
