# -*- coding: utf-8 -*-
"""零模型验收台账的契约测试：判定只吃硬信号，模型自述不参与。"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.super_agent.acceptance import AcceptanceLedger, FAIL, PASS, UNVERIFIED  # noqa: E402

PY = sys.executable


def git(cwd, *args):
    return subprocess.run(
        ['git', '-c', 'user.email=test@local', '-c', 'user.name=test'] + list(args),
        cwd=cwd, capture_output=True, text=True, encoding='utf-8', errors='replace')


def finished_session(response='done', turns=1):
    return dict(running=False, title='测试任务', turn_outline=[
        dict(turn=index + 1, prompt='p%d' % index, response=response) for index in range(turns)])


class AcceptanceLedgerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        git(self.repo, 'init')
        (self.repo / 'a.txt').write_text('one\n', encoding='utf-8')
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', 'init')
        self.ledger = AcceptanceLedger(root=self.root / 'ledger')

    def tearDown(self):
        self.folder.cleanup()

    def test_no_checks_is_never_a_pass(self):
        record = self.ledger.open(goal='改点东西', cwd=str(self.repo))
        self.ledger.observe(record['id'], finished_session())
        verdict = self.ledger.judge(record['id'])
        self.assertEqual(verdict['verdict'], UNVERIFIED)
        self.assertTrue(any('没有声明任何检查命令' in reason for reason in verdict['reasons']))

    def test_all_green_with_real_changes_passes(self):
        (self.repo / 'a.txt').write_text('two\n', encoding='utf-8')
        record = self.ledger.open(goal='把 a.txt 改成 two', cwd=str(self.repo),
                                  checks=[[PY, '-c', 'print(1)']])
        self.ledger.observe(record['id'], finished_session())
        self.ledger.run_checks(record['id'])
        verdict = self.ledger.judge(record['id'])
        self.assertEqual(verdict['verdict'], PASS, verdict['reasons'])
        self.assertEqual(verdict['workspace']['changed_files'], 1)
        self.assertEqual(verdict['check_results'][0]['exit'], 0)

    def test_failing_check_fails(self):
        (self.repo / 'a.txt').write_text('two\n', encoding='utf-8')
        record = self.ledger.open(goal='假装做完了', cwd=str(self.repo),
                                  checks=[[PY, '-c', 'raise SystemExit(3)']])
        self.ledger.observe(record['id'], finished_session())
        self.ledger.run_checks(record['id'])
        verdict = self.ledger.judge(record['id'])
        self.assertEqual(verdict['verdict'], FAIL)
        self.assertEqual(verdict['check_results'][0]['exit'], 3)

    def test_model_claim_never_rescues_a_failure(self):
        record = self.ledger.open(goal='x', cwd=str(self.repo),
                                  checks=[[PY, '-c', 'raise SystemExit(1)']])
        self.ledger.claim(record['id'], '我全部完成了，测试全部通过')
        self.ledger.observe(record['id'], finished_session())
        self.ledger.run_checks(record['id'])
        verdict = self.ledger.judge(record['id'])
        self.assertEqual(verdict['verdict'], FAIL)
        self.assertTrue(any('模型自述仅留档' in reason for reason in verdict['reasons']))

    def test_still_running_is_unverified(self):
        (self.repo / 'a.txt').write_text('two\n', encoding='utf-8')
        record = self.ledger.open(goal='x', cwd=str(self.repo),
                                  checks=[[PY, '-c', 'print(1)']])
        self.ledger.observe(record['id'], dict(running=True, turn_outline=[]))
        self.ledger.run_checks(record['id'])
        verdict = self.ledger.judge(record['id'])
        self.assertEqual(verdict['verdict'], UNVERIFIED)
        self.assertTrue(any('还在跑' in reason for reason in verdict['reasons']))

    def test_no_workspace_change_is_a_failure_when_changes_expected(self):
        record = self.ledger.open(goal='应该改文件但没改', cwd=str(self.repo),
                                  checks=[[PY, '-c', 'print(1)']])
        self.ledger.observe(record['id'], finished_session())
        self.ledger.run_checks(record['id'])
        verdict = self.ledger.judge(record['id'])
        self.assertEqual(verdict['verdict'], FAIL)
        self.assertTrue(any('没有任何改动' in reason for reason in verdict['reasons']))

    def test_checks_not_run_yet_is_unverified(self):
        record = self.ledger.open(goal='x', cwd=str(self.repo),
                                  checks=[[PY, '-c', 'print(1)']])
        verdict = self.ledger.judge(record['id'])
        self.assertEqual(verdict['verdict'], UNVERIFIED)

    def test_timeout_is_recorded_as_failure(self):
        record = self.ledger.open(goal='x', cwd=str(self.repo),
                                  checks=[dict(command=[PY, '-c', 'import time; time.sleep(5)'],
                                               timeout=0.5, label='慢命令')])
        self.ledger.observe(record['id'], finished_session())
        self.ledger.run_checks(record['id'])
        record = self.ledger.load(record['id'])
        self.assertFalse(record['check_results'][0]['ok'])
        self.assertEqual(record['check_results'][0]['error'], '超时')

    def test_export_markdown_contains_verdict_and_evidence(self):
        (self.repo / 'a.txt').write_text('two\n', encoding='utf-8')
        record = self.ledger.open(goal='导出测试', cwd=str(self.repo),
                                  checks=[[PY, '-c', 'print(1)']])
        self.ledger.observe(record['id'], finished_session())
        self.ledger.run_checks(record['id'])
        self.ledger.judge(record['id'])
        out = self.root / 'out.md'
        self.ledger.export(record['id'], str(out))
        text = out.read_text(encoding='utf-8')
        self.assertIn('结论：**pass**', text)
        self.assertIn('## 检查命令', text)
        self.assertIn('a.txt', text)

    def test_list_returns_recent_records(self):
        for index in range(3):
            self.ledger.open(goal='任务%d' % index, cwd=str(self.repo), checks=[])
        rows = self.ledger.list()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[-1]['goal'], '任务2')


if __name__ == '__main__':
    unittest.main()
