"""Persistence regressions for long reusable workflows and review candidates."""
import tempfile,unittest
from pathlib import Path
from core.workflow_catalog import WorkflowCatalog


class WorkflowPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.path=Path(self.folder.name)/'flows.sqlite3';self.catalog=WorkflowCatalog(self.path)

    def tearDown(self):self.folder.cleanup()

    def test_long_steps_survive_restart_and_edit_without_losing_outcomes(self):
        text='先检查输入，再执行，再核对真实结果。\n'*1000
        f=self.catalog.define('整理文档',text)
        self.catalog.record_run(f,True,'已读回');self.catalog.record_run(f,False,'缺少文件')
        reopened=WorkflowCatalog(self.path);self.assertEqual(reopened.get(f['id'])['instructions'],text.strip())
        edited=reopened.update(f['id'],'整理我的文档',text+'\n失败时停止。','整理资料')
        self.assertEqual((edited['success'],edited['failure']),(1,1))
        self.assertIsNone(reopened.match('整理文档'));self.assertEqual(reopened.match('整理我的文档')['id'],f['id'])

    def test_long_definition_is_rejected_instead_of_silently_truncated(self):
        with self.assertRaises(ValueError):self.catalog.define('过长','a'*32001)
        self.assertEqual(self.catalog.list(False),[])

    def test_edit_does_not_activate_unreviewed_candidate(self):
        c=self.catalog.define('常做工作','先核对输入',state='candidate')
        self.catalog.update(c['id'],'常做工作','核对输入，再核对输出')
        self.assertIsNone(self.catalog.match('常做工作'))
        self.catalog.activate(c['id']);self.assertIsNotNone(self.catalog.match('常做工作'))

    def test_edit_cannot_replace_builtin_definitions(self):
        with self.assertRaises(ValueError):self.catalog.update('meeting-to-bilibili','投稿','随便发布')
        builtin=next(f for f in self.catalog.list() if f['id']=='meeting-to-bilibili')
        self.assertIn('再次明确确认',builtin['instructions'])


if __name__=='__main__':unittest.main()
