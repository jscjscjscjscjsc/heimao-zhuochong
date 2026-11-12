"""Bounded offline checks for profile memory, grounded Q&A and task isolation."""
import json
import os
from pathlib import Path
import tempfile
import time
from datetime import datetime,timezone,timedelta
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication,QWidget
from core.agent_memory import AgentMemoryStore,profile_facts
from core.chat_answer import AnswerWorker
from core.companion_dialogue import plain_answer,speech_answer,needs_web_search,question_opener,current_time_instructions,is_repetitive
from core.proactive_conversation import ProactiveConversation
from core.realtime_agent import AgentRealtimeVoice
from core.realtime_voice import DEFAULTS
from core.task_speech import TaskSpeechQueue
from ui.agent_chat import AgentChatWindow
from ui.speech_bubble import PetSpeechBubble
from ui.pet_window import PetWindow

APP=QApplication.instance() or QApplication([])


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'memory.db'
        self.memory=AgentMemoryStore(self.path)
    def tearDown(self):self.temp.cleanup()

    def test_explicit_facts_persist_and_corrections_replace_old_profile(self):
        self.memory.add_dialogue('001','user','我是软件工程师，我喜欢喝咖啡。')
        self.memory.add_dialogue('001','user','我现在的工作是产品经理，我不喜欢咖啡。')
        rows=AgentMemoryStore(self.path).facts('001')
        self.assertEqual(len(rows),2)
        self.assertIn('产品经理',str(rows));self.assertNotIn('软件工程师',str(rows))
        self.assertIn('不喜欢',str(rows));self.assertEqual(self.memory.facts('002'),[])

    def test_terse_answer_to_proactive_question_becomes_a_fact(self):
        self.memory.add_dialogue('001','assistant','你平时喜欢喝什么？')
        self.memory.add_dialogue('001','user','乌龙茶')
        self.assertIn('乌龙茶',str(self.memory.facts('001')))

    def test_assistant_fiction_questions_and_secrets_never_become_profile_facts(self):
        self.memory.add_dialogue('001','assistant','我是软件工程师，我喜欢咖啡。')
        for text in ('假如我是工程师','我喜欢咖啡吗？','我喜欢什么','我是医生还是程序员','写故事：“我是医生”','我喜欢这个建议',
                     '我的工作是保管密码','我喜欢 sk-'+'A'*32):
            self.memory.add_dialogue('001','user',text)
        self.assertEqual(self.memory.facts('001'),[])


class PresentationTests(unittest.TestCase):
    def test_hidden_partial_blocks_and_markdown_are_filtered(self):
        self.assertEqual(plain_answer('<think>PRIVATE'), '')
        self.assertEqual(plain_answer('<think>PRIVATE</think>## 建议\n**先喝水**。'), '建议\n先喝水。')
        self.assertNotIn('https',speech_answer('先喝水[1]。\n参考来源：\n1. 标题\nhttps://example.com'))
        self.assertEqual(speech_answer('先喝水[1]。\n参考来源：\nurl'), '先喝水。')

    def test_display_keeps_markdown_while_speech_strips_it(self):
        from core.ai_chat import parse_reply
        from core.companion_dialogue import display_answer
        raw='|happy|none|## 建议\n**先喝水**。\n```python\nprint(1)\n```'
        shown=parse_reply(display_answer(raw))[2]
        self.assertIn('## 建议',shown);self.assertIn('```python',shown)
        spoken=speech_answer(shown)
        self.assertNotIn('##',spoken);self.assertNotIn('**',spoken);self.assertNotIn('```',spoken)

    def test_search_policy_and_identity_appropriate_openers(self):
        self.assertTrue(needs_web_search('今天上海的天气如何？'))
        self.assertFalse(needs_web_search('我喜欢喝茶'))
        self.assertFalse(needs_web_search('你喜欢吃什么？'))
        self.assertNotEqual(question_opener(0),question_opener(1))
        self.assertNotIn('本鹅',question_opener(0,dict(agent_id='002')))

    def test_real_date_is_injected_and_repeat_guard_compares_phrases(self):
        fixed=datetime(2026,11,4,14,20,tzinfo=timezone(timedelta(hours=8)))
        value=current_time_instructions(lambda:fixed)
        self.assertIn('2026年11月4日',value);self.assertIn('星期三',value);self.assertIn('14:20',value)
        self.assertTrue(is_repetitive('今天想聊聊你最近喜欢的乌龙茶吗？',['你最近是不是很喜欢乌龙茶？']))
        self.assertFalse(is_repetitive('今天想聊聊乌龙茶吗？',['周末去公园看到的银杏叶颜色很漂亮。']))


class AnswerTests(unittest.TestCase):
    def run_answer(self,receipt):
        worker=AnswerWorker(config=dict(api_key='offline',base_url='https://example.com/v1'))
        results=[];drafts=[];worker.completed.connect(results.append)
        worker.partial.connect(lambda _,__,t:drafts.append(t))
        response=MagicMock(status_code=200)
        response.iter_lines.return_value=[('data: '+json.dumps(dict(choices=[dict(delta=x)]))).encode() for x in
            (dict(reasoning_content='PRIVATE'),dict(content='<think>PRIVATE'),dict(content='</think>|happy|none|## 简短解释\n**可以这样理解。**[1]'))]+[b'data: [DONE]']
        with patch('core.chat_memory.recall_context',return_value=dict(state='empty',items=[])), \
                patch('core.search_results.search',return_value=receipt) as search, \
                patch('core.chat_answer.requests.post',return_value=response) as post:
            worker.start('qa',[],'列表和元组有什么区别？',memory_context='用户喜欢乌龙茶',search_mode='always')
            end=time.monotonic()+4
            while not results and time.monotonic()<end:APP.processEvents();time.sleep(.005)
            self.assertTrue(results)
        self.assertNotIn('PRIVATE',str(results)+str(drafts))
        self.assertNotIn('|',results[0].get('text',''))
        self.assertNotIn('tools',post.call_args.kwargs['json'])
        self.assertNotIn('乌龙茶',search.call_args.args[0])
        return results[0],post.call_args.kwargs['json']

    def test_sources_are_real_search_receipts_and_not_spoken(self):
        result,payload=self.run_answer(dict(ok=True,results=[dict(title='文档',url='https://example.com/docs',snippet='测试摘要')]))
        self.assertTrue(result['ok']);self.assertEqual(result['search_state'],'ok')
        self.assertIn('[文档](https://example.com/docs)',result['text'])
        self.assertIn('## 简短解释',result['text']);self.assertIn('**可以这样理解。**',result['text'])
        self.assertNotIn('example.com',speech_answer(result['text']))
        self.assertIn('不可信资料',payload['messages'][0]['content'])

    def test_empty_search_is_explicit_and_no_sources_are_invented(self):
        result,_=self.run_answer(dict(ok=False,results=[]))
        self.assertTrue(result['ok']);self.assertEqual(result['sources'],[])
        self.assertIn('联网没能取得',result['text']);self.assertNotIn('参考来源：',result['text'])


class InitiativeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.memory=AgentMemoryStore(Path(self.temp.name)/'memory.db')
        self.panel=SimpleNamespace(memory=self.memory,pet=None,busy=False,voice_active=False,active_id='a',
            active_agent_id='001',active_profile=self.memory.profile('001'),_last_user_activity=0,
            runtime=SimpleNamespace(music=SimpleNamespace(playing=False),dsh=SimpleNamespace(status=lambda:dict(running=False))))
        self.p=ProactiveConversation(self.panel,self.memory);self.p.started_at=0
    def tearDown(self):self.p.stop();self.temp.cleanup()

    def test_default_is_thirty_seconds_and_busy_defers(self):
        self.assertEqual(self.p.interval_seconds,30);self.assertEqual(self.p.cooldown_seconds,30)
        self.assertTrue(self.p.enabled)
        self.panel.busy=True
        with patch.object(self.p,'_compose_offline') as compose:
            self.assertIsNone(self.p.tick());compose.assert_not_called()

    def test_late_generation_cannot_interrupt_or_leak_into_another_chat(self):
        self.p._pending_context=dict(event='p1',conversation_id='a',agent_id='001',last_activity=0)
        self.p.last_result=dict(event='p1')
        self.panel.active_id='b';self.panel._show_active_bubble=MagicMock()
        self.p._deliver_offline('p1','旧话题')
        self.assertEqual(self.p.last_result['state'],'superseded')
        self.panel._show_active_bubble.assert_not_called()

    def test_delivery_uses_one_reply_bubble_and_never_legacy_paint_bubble(self):
        self.panel._say_active_reply=MagicMock();self.panel.store=MagicMock();self.panel.memory=MagicMock()
        self.panel.pet=SimpleNamespace(_say_with_expression=MagicMock())
        event='single-bubble';self.p.last_result=dict(event=event)
        self.p._pending_context=dict(event=event,conversation_id='a',agent_id='001',last_activity=0)
        self.p._deliver_offline(event,'只显示这一条')
        self.panel._say_active_reply.assert_called_once()
        self.panel.pet._say_with_expression.assert_not_called()

    def test_real_panel_can_chat_while_a_long_task_keeps_running(self):
        self.panel.busy=True;self.panel._run_kind='dsh';self.panel.companion_chat_allowed=lambda:True
        self.p._topic_cursor=1;self.p.started_at=0;self.p.last_at=0
        with patch.object(self.p,'_compose_offline') as compose,patch('core.proactive_conversation.threading.Thread') as thread:
            result=self.p.tick()
        self.assertEqual(result['mode'],'offline');thread.assert_called_once();compose.assert_not_called()

    def test_latest_news_is_searched_cached_and_injected_without_claiming_more(self):
        now=[100.0];calls=[]
        def provider(query):
            calls.append(query)
            return dict(results=[dict(title='真实新闻标题',snippet='摘要只说一项可核对内容',url='https://example.com/news')])
        p=ProactiveConversation(self.panel,self.memory,news_provider=provider,clock=lambda:now[0])
        entries=p._news_for_prompt(refresh=True)
        self.assertEqual(entries[0]['title'],'真实新闻标题');self.assertIn('2026年',calls[0])
        prompt=p._prompt(self.panel.active_profile,[],entries,0)
        self.assertIn('当前现实时间',prompt);self.assertIn('真实新闻标题',prompt);self.assertIn('不能补造细节',prompt)
        self.assertEqual(p._news_for_prompt(refresh=True)[0]['title'],'真实新闻标题');self.assertEqual(len(calls),1)
        now[0]+=p.news_refresh_seconds;p._news_for_prompt(refresh=True);self.assertEqual(len(calls),2)
        p.stop()


class BubbleTests(unittest.TestCase):
    def test_hold_to_talk_has_one_dock_and_cancel_release_signals(self):
        host=QWidget();host.resize(200,200);bubble=PetSpeechBubble(host);bubble.talk_dock.show()
        started=[];stopped=[];bubble.talk_started.connect(lambda:started.append(True));bubble.talk_stopped.connect(stopped.append)
        bubble._begin_talk();APP.processEvents()
        self.assertTrue(started);self.assertTrue(bubble.talk_dock.property('recording'));self.assertEqual(bubble.talk_hint.text(),'松开发送')
        bubble._finish_talk();APP.processEvents()
        self.assertEqual(stopped,[False]);self.assertEqual(bubble.talk_hint.text(),'正在识别')
        bubble.set_voice_state('idle');APP.processEvents()
        self.assertFalse(bubble.talk_dock.property('recording'));self.assertEqual(bubble.talk_hint.text(),'按住说话')
        bubble._begin_talk();bubble.cancel_talk();self.assertEqual(stopped[-1],True)
        bubble.deleteLater();host.deleteLater()

    def test_agent_reply_uses_the_agent_bubble_only(self):
        target=SimpleNamespace(_show_agent_bubble=MagicMock(),_anim=MagicMock(),_perform_gesture=MagicMock(),
            _apply_agent_style=MagicMock(),_realtime_active=lambda:False,_tts=MagicMock())
        PetWindow._say_agent_reply(target,'这是唯一的气泡','happy','nod',True,'chat-id')
        target._show_agent_bubble.assert_called_once_with('这是唯一的气泡','chat-id')
        target._apply_agent_style.assert_called_once_with('happy','nod');target._tts.speak.assert_called_once_with('这是唯一的气泡')

    def test_panel_reply_does_not_duplicate_bubble_rendering(self):
        target=SimpleNamespace(active_agent_id='001',active_id='chat',voice_active=False,
            active_profile={},pet=SimpleNamespace(_multi_agent=None,_say_agent_reply=MagicMock(),_tts=MagicMock()))
        AgentChatWindow._say_active_reply(target,'显示一次','happy','none',False,'chat',spoken_text='朗读一次')
        target.pet._say_agent_reply.assert_called_once_with('显示一次','happy','none',False,'chat')


class IsolationTests(unittest.TestCase):
    def test_bubble_voice_can_answer_while_background_task_is_running(self):
        short=MagicMock(active=False);pet=SimpleNamespace(_realtime_active=lambda:False,_tts=MagicMock())
        panel=SimpleNamespace(voice_active=False,pet=pet,short_voice=short,busy=True,_run_kind='dsh',status_error=MagicMock())
        self.assertTrue(AgentChatWindow.start_bubble_short_voice(panel));short.start.assert_called_once();pet._tts.stop.assert_called_once()
        panel.busy=True;panel._run_kind='answer';self.assertFalse(AgentChatWindow.start_bubble_short_voice(panel));panel.status_error.assert_called_once()

    def test_long_call_fallback_mutes_while_local_asr_then_submits_text(self):
        voice=MagicMock(active=True);voice.submit_user_text.return_value=True
        short=MagicMock(active=False);tts=MagicMock(is_speaking=False)
        panel=SimpleNamespace(voice_active=True,voice=voice,pet=SimpleNamespace(_realtime_active=lambda:True,_tts=tts),
            short_voice=short,busy=False,_run_kind=None,status_error=MagicMock())
        self.assertTrue(AgentChatWindow.start_bubble_short_voice(panel));voice.mute.assert_called_once_with(True);short.start.assert_called_once()
        panel.store=MagicMock();panel.memory=MagicMock();panel.active_id='chat';panel.active_agent_id='001'
        panel.editor=MagicMock();panel.pet._multi_agent=None;panel._last_user_activity=0;panel.status=MagicMock();panel.render=MagicMock()
        AgentChatWindow._short_transcript(panel,'长对话听不清时按住说')
        voice.mute.assert_called_with(False);voice.submit_user_text.assert_called_once_with('长对话听不清时按住说')

    def test_answer_completion_preserves_running_job_and_its_progress(self):
        voice=MagicMock();reports={'job':object()}
        panel=SimpleNamespace(_dialogue_context=dict(conversation_id='chat',agent_id='001',profile={},voice=voice,call_id='qa',read_aloud=False),
            dialogue=SimpleNamespace(generation=4),store=MagicMock(),memory=MagicMock(),voice=voice,voice_active=True,
            active_id='chat',active_agent_id='001',busy=True,_run_kind='dsh',_dsh_reports=reports,
            _show_active_bubble=MagicMock(),render=MagicMock(),_voice_readback=False)
        AgentChatWindow._dialogue_completed(panel,dict(generation=4,ok=True,text='回答正文\n参考来源：\nhttps://example.com'))
        self.assertTrue(panel.busy);self.assertEqual(panel._run_kind,'dsh');self.assertIs(panel._dsh_reports,reports)
        voice.resolve_task.assert_called_once()
        self.assertEqual(voice.resolve_task.call_args.args[1]['answer'],'回答正文')
        voice.set_task_context.assert_not_called()

    def test_knowledge_question_is_not_steered_into_busy_executor(self):
        voice=MagicMock()
        panel=SimpleNamespace(voice_active=True,sender=lambda:voice,voice=voice,busy=True,
            _pending_image=None,_pending_execution=None,_start_dialogue=MagicMock(return_value=True),_steer_execution=MagicMock())
        AgentChatWindow._voice_request(panel,'qa','为什么月亮看起来是圆的？')
        panel._start_dialogue.assert_called_once_with('为什么月亮看起来是圆的？',call_id='qa')
        panel._steer_execution.assert_not_called()

    def test_voice_persona_keeps_default_share_and_grounded_qa(self):
        voice=AgentRealtimeVoice();voice._cfg=dict(DEFAULTS)
        voice.screen_context_provider=lambda:dict(active=True,fresh=False)
        instructions=voice._session_settings()['instructions']
        for phrase in ('每30秒','独立通道','让本鹅想一想','active=true时绝不能让用户再次开启共享'):
            self.assertIn(phrase,instructions)

    def test_ordinary_progress_speech_is_available_at_thirty_seconds(self):
        now=[0.];q=TaskSpeechQueue(clock=lambda:now[0])
        q.offer('start','开始');self.assertIsNotNone(q.next())
        q.offer('beat','还在处理');now[0]=29;self.assertIsNone(q.next())
        now[0]=30;self.assertIsNotNone(q.next())


if __name__=='__main__':unittest.main()
