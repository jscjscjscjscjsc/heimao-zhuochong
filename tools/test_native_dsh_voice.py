"""Native DSH task facts and voice context reductions without live accounts."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock,patch
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
# 测试只验证汇报文案与语音上下文，关闭超级智能体的屏幕/记忆通道，避免真实采集与外部记忆写入。
os.environ.setdefault('GUGA_SUPER_AGENT','off')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication
from core.dsh_link import DshLink
from core.realtime_agent import AgentRealtimeVoice
from core.realtime_voice import DEFAULTS
APP=QApplication.instance() or QApplication([])


class NativeDshTests(unittest.TestCase):
    def setUp(self):
        self.now=100.
        self.panel=SimpleNamespace(voice=None,voice_active=False,busy=False,active_id='chat',_say_active_reply=MagicMock())
        self.link=DshLink(self.panel,clock=lambda:self.now)

    def tearDown(self):self.link.timer.stop()

    def event(self,session='session-1',kind='turn/start',state='running',**kw):
        return self.link.receive(dict(session_id=session,kind=kind,state=state,event_id=str(self.now)+kind,**kw))

    def test_host_presence_does_not_create_a_task(self):
        self.link.receive(dict(session_id='_host_',kind='host/heartbeat'))
        self.assertTrue(self.link.status()['connected']);self.assertEqual(self.link.status()['sessions'],[])

    def test_progress_is_narrated_once_per_minute(self):
        # 主人指定（2026-11-11）：运行中每 60 秒组织一次自然语言进度，心跳再密也不多报。
        self.event(goal='通过咕嘎打开浏览器',turn=1,summary='正在调用 applications')
        self.link.tick()                                            # 刚开始，未到 60 秒
        self.assertEqual(self.panel._say_active_reply.call_count,0)
        # 到 60 秒：本测试关掉了超级智能体，直接说确定性文案。
        self.now+=61;self.event(kind='heartbeat');self.link.tick()
        self.assertEqual(self.panel._say_active_reply.call_count,1)
        self.assertIn('打开浏览器',self.panel._say_active_reply.call_args.args[0])
        self.assertNotIn('当前目标',self.panel._say_active_reply.call_args.args[0])
        # 同一个节拍内，心跳再密也不该再打扰。
        for _ in range(3):
            self.now+=15;self.event(kind='heartbeat');self.link.tick()
        self.assertEqual(self.panel._say_active_reply.call_count,1)
        # 再过一个完整周期 → 再报一次。
        self.now+=20;self.event(kind='heartbeat');self.link.tick()
        self.assertEqual(self.panel._say_active_reply.call_count,2)

    def test_stall_does_not_add_reports_between_beats(self):
        # 长时间没有新进展也不额外插话：节奏只由 60 秒决定。
        self.event(goal='跑一个长任务',summary='正在调用 applications')
        self.now+=61;self.event(kind='heartbeat');self.link.tick()
        self.now+=1;self.event(kind='heartbeat');self.link.tick()
        self.assertEqual(self.panel._say_active_reply.call_count,1)
        for _ in range(2):
            self.now+=20;self.event(kind='heartbeat');self.link.tick()
        self.assertEqual(self.panel._say_active_reply.call_count,1)

    def test_finished_task_speaks_verdict_and_next_step_as_one_line(self):
        # 任务跑完：先验收、再组织下一步，合并成一条说，不连发两条。
        with patch('core.super_agent.finish_review.review',
                   return_value=dict(ok=True,verdict='unverified',
                                     reasons=['没有声明检查命令'],
                                     next_step='这轮跑完了，但我还没有能独立验证的检查项。')):
            self.event(turn=1)
            self.event(kind='turn/end',state='finished',turn=1,summary='本轮结束')
            for _ in range(100):
                if self.link.sessions['session-1'].get('review_result'):break
                time.sleep(0.02)
            self.now+=1;self.link.tick()
            self.assertEqual(self.panel._say_active_reply.call_count,1)
            self.assertIn('检查项',self.panel._say_active_reply.call_args.args[0])
            # 同一条结论不会重复播报。
            self.now+=1;self.link.tick()
            self.assertEqual(self.panel._say_active_reply.call_count,1)

    def test_focus_stays_put_instead_of_hopping_between_sessions(self):
        self.event(kind='user/message',goal='任务A')
        self.event(session='session-2',kind='user/message',goal='任务B')
        self.assertEqual(self.link.focused_session,'session-1')
        self.assertEqual(len(self.link.status()['sessions']),2)
        self.link.tick()
        self.assertEqual(self.panel._say_active_reply.call_count,0)

    def test_long_silence_does_not_claim_completion(self):
        # 长时间没动静只按 60 秒节拍汇报，绝不谎称任务已结束。
        self.event(goal='跑一个长任务',summary='正在调用 applications')
        self.now+=61;self.event(kind='heartbeat');self.link.tick()
        self.now+=1;self.event(kind='heartbeat');self.link.tick()
        self.assertEqual(self.panel._say_active_reply.call_count,1)
        self.assertNotIn('干完啦',self.panel._say_active_reply.call_args.args[0])
        self.assertEqual(self.link.status()['sessions'][0]['state'],'running')

    def test_missing_heartbeat_never_claims_completion(self):
        self.event(summary='DSH 正在处理')
        self.now+=46;self.link.tick();line=self.panel._say_active_reply.call_args.args[0]
        self.assertIn('还不能判断',line)
        self.assertFalse(self.link.status()['connected'])
        self.now+=31;self.link.tick();self.assertEqual(self.panel._say_active_reply.call_count,1)

    def test_terminal_event_announces_once(self):
        self.event(turn=1)
        self.event(kind='turn/end',state='failed',turn=1,summary='模型返回错误，本轮失败')
        self.link.tick();self.link.tick()
        self.assertEqual(self.panel._say_active_reply.call_count,1)
        self.assertIn('失败',self.panel._say_active_reply.call_args.args[0])

    def test_old_turn_cannot_overwrite_new_turn(self):
        self.event(turn=3,summary='新任务')
        old=self.event(kind='turn/end',state='finished',turn=2,summary='旧任务结束')
        self.assertTrue(old['stale_event_ignored'])
        self.assertEqual(self.link.status()['sessions'][0]['summary'],'新任务')

    def test_active_voice_gets_context_and_narration_without_new_connection(self):
        self.panel.voice=MagicMock();self.panel.voice_active=True
        self.event(goal='打开浏览器',summary='正在执行')
        self.now+=61;self.event(kind='heartbeat');self.link.tick()   # 到点组织
        self.now+=1;self.event(kind='heartbeat');self.link.tick()    # 60 秒节拍播报
        self.panel.voice.set_task_context.assert_called()
        self.panel.voice.notify_battleground.assert_called_once()
        self.panel._say_active_reply.assert_not_called()

    def test_busy_frontend_is_not_interrupted(self):
        self.panel.companion_chat_allowed=lambda:False
        self.event();self.link.tick();self.panel._say_active_reply.assert_not_called()

    def test_child_agent_does_not_steal_parent_focus(self):
        self.event(kind='user/message',goal='主会话任务')
        self.event(session='child',kind='user/message',goal='子任务',parent_session_id='session-1')
        self.assertEqual(self.link.focused_session,'session-1')

    def test_manual_focus_uses_real_ids_and_does_not_claim_stop(self):
        self.event();self.event(session='session-2')
        self.link.execute(dict(action='focus',session_id='session-2'))
        self.assertEqual(self.link.focused_session,'session-2')
        with self.assertRaises(ValueError):self.link.execute(dict(action='focus',session_id='guessed'))
        with self.assertRaisesRegex(ValueError,'暂停、停止请在 DSH'):
            self.link.execute(dict(action='stop'))

    def test_new_voice_only_inherits_a_current_running_task(self):
        self.event(goal='当前DSH任务')
        self.assertEqual(self.link.active_context()['goal'],'当前DSH任务')
        self.now+=46;self.assertIsNone(self.link.active_context())
        self.event(kind='turn/end',state='finished');self.assertIsNone(self.link.active_context())


class VoiceContextTests(unittest.TestCase):
    def voice(self):
        v=AgentRealtimeVoice();v._cfg=dict(DEFAULTS)
        v.context_history=[('饲养员','KEEP_RECONNECT_REFERENCE')]
        v._history.extend([('饲养员','SERVER_ALREADY_HAS_THIS_'*80),('咕嘎','OLD_AUDIO_TRANSCRIPT_'*80)]*12)
        v.long_term_memory=['主人喜欢阅读']
        v.set_task_context(dict(running=True,goal='完成当前测试',summary='正在执行第2步'))
        v.screen_context_provider=lambda:dict(active=True,fresh=True,context='当前页面显示每日计划')
        return v

    def test_dynamic_updates_omit_only_server_resident_history(self):
        v=self.voice();initial=v._session_settings();update=v._session_settings(include_history=False)
        self.assertIn('SERVER_ALREADY_HAS_THIS',initial['instructions'])
        self.assertNotIn('SERVER_ALREADY_HAS_THIS',update['instructions'])
        for fact in ('KEEP_RECONNECT_REFERENCE','主人喜欢阅读','完成当前测试','第2步','当前页面显示每日计划'):
            self.assertIn(fact,update['instructions'])
        self.assertEqual(initial['tools'],update['tools'])
        self.assertEqual(update['max_history_turns'],16)
        self.assertEqual(initial['turn_detection'],update['turn_detection'])
        self.assertLess(len(update['instructions']),len(initial['instructions'])-4000)

    def test_screen_sync_uses_slim_settings_without_extra_response(self):
        v=self.voice();v._ws=object();v._screen_configured=True
        with patch.object(v,'_send') as send:v._sync_screen_context(force=True)
        event=send.call_args.args[0]
        self.assertEqual(event['type'],'session.update')
        self.assertNotIn('SERVER_ALREADY_HAS_THIS',event['session']['instructions'])
        self.assertGreater(v.screen_bridge_status()['context_transport']['history_bytes_avoided'],0)

    def test_usage_from_tests_cannot_overwrite_production_meter(self):
        import core.realtime_voice as module
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);real=root/'realtime_usage.log';real.write_text('REAL_METER',encoding='utf-8')
            v=AgentRealtimeVoice()
            with patch.object(module,'CONFIG_PATH',root/'ai_config.json'),patch.object(module,'USAGE_PATH',real),patch.object(module,'VOICE_EVENTS_DIR',root/'events'):
                v._record_usage(dict(id='test',usage=dict(total_tokens=5,input_tokens=3,output_tokens=2)))
            self.assertEqual(real.read_text(),'REAL_METER')
            self.assertTrue(list((root/'events').glob('usage-test-*.json')))


if __name__=='__main__':unittest.main()
