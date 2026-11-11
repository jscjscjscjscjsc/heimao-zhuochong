"""Independent model, plain-language prompting and read-only memory regressions."""
import json,os,threading,time,unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
from tools import test_personal_harness as harness
from core.companion_dialogue import needs_web_search
from core import chat_memory


class MemoryRecallTests(unittest.TestCase):
    def setUp(self):
        environment=patch.dict(os.environ,{'GUGA_LINGSHU':'on'})
        environment.start();self.addCleanup(environment.stop)
        chat_memory._CACHE.clear();chat_memory._INFLIGHT=None

    def test_cache_is_query_specific_and_other_member_does_not_receive_shared_memory(self):
        with patch.object(chat_memory,'_read',side_effect=lambda q:dict(state='ok',items=[q])):
            self.assertEqual(chat_memory.recall_context('fixture a')['items'],['fixture a'])
            self.assertEqual(chat_memory.recall_context('fixture b')['items'],['fixture b'])
            self.assertEqual(chat_memory.recall_context('fixture a','003')['items'],[])

    def test_slow_memory_does_not_block_reply_or_supply_unrelated_cached_text(self):
        release=threading.Event()
        def read(q):release.wait(1);return dict(state='ok',items=['fixture'])
        with patch.object(chat_memory,'_read',side_effect=read):
            try:
                started=time.monotonic();r=chat_memory.recall_context('slow fixture',wait_seconds=.01)
                self.assertEqual(r['state'],'pending');self.assertLess(time.monotonic()-started,.3)
                self.assertEqual(chat_memory.recall_context('different fixture')['items'],[])
            finally:
                release.set();chat_memory._INFLIGHT[1].wait(1) if chat_memory._INFLIGHT else None

    def test_memory_failure_does_not_look_like_success(self):
        with patch.object(chat_memory,'_read',side_effect=RuntimeError('offline')):
            self.assertEqual(chat_memory.recall_context('failed fixture')['state'],'unavailable')

    def test_ordinary_questions_do_not_require_web_but_current_information_does(self):
        self.assertFalse(needs_web_search('解释一下什么叫大模型，用生活里的例子'))
        self.assertFalse(needs_web_search('你能听见我说话吗'))
        self.assertTrue(needs_web_search('查一下今天的天气'))


class IndependentChatTests(unittest.TestCase):
    setUpClass=harness.IntegrationTests.__dict__['setUpClass']
    tearDownClass=harness.IntegrationTests.__dict__['tearDownClass']
    setUp=harness.IntegrationTests.setUp
    pump=harness.IntegrationTests.pump

    def test_inline_text_uses_companion_chat_instead_of_old_game_model(self):
        with patch.object(self.panel,'send',return_value=True) as send,patch.object(self.pet._ai,'ask') as legacy:
            self.pet._ask_with_ctx('给我讲个通俗的比喻')
            send.assert_called_once_with('给我讲个通俗的比喻');legacy.assert_not_called()

    def test_real_chat_dispatch_includes_prompt_and_memories_without_dsh(self):
        p=self.panel;captured=[]
        response=Mock(status_code=200)
        response.iter_lines.return_value=[('data: '+json.dumps({'choices':[{'delta':{'content':'大模型像个读过很多书的助手。'}}]},ensure_ascii=False)).encode(),'data: [DONE]']
        def post(*args,**kwargs):captured.append(kwargs['json']);return response
        cfg=dict(api_key='fixture',base_url='https://example.test/v1',model='fixture-model')
        p.send('记住：我喜欢大白话解释')
        try:
            with patch('core.model_settings.load_config',return_value=cfg),patch('core.chat_memory.recall_context',return_value=dict(state='ok',items=['共享工作结论 fixture-memory'])),patch('core.chat_answer.requests.post',side_effect=post),patch.object(p.runtime.dsh,'start') as dsh:
                self.assertTrue(p.send('解释大模型是什么'))
                self.pump(lambda:not p.busy)
                dsh.assert_not_called()
            system=captured[0]['messages'][0]['content']
            self.assertIn('大白话',system);self.assertIn('用户长期信息',system)
            self.assertIn('fixture-memory',system)
            self.assertEqual(p.store.messages(p.active_id)[-1]['content'],'大模型像个读过很多书的助手。')
            self.assertEqual(p.answer.last_receipt['state'],'completed')
        finally:p.busy=False;p._run_kind=None


if __name__=='__main__':unittest.main()
