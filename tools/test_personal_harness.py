import json,os,sys,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch,Mock,PropertyMock
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from core.personal_harness import PersonalHarness,use_dsh


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=PersonalHarness(self.root/'tickets.db')
    def tearDown(self):self.temp.cleanup()
    def ticket(self):return self.store.create('001','chat','写入并验证文件',self.root)
    def test_recovery_never_replays_and_retains_session(self):
        row=self.ticket();self.store.update(row['id'],session='resume-me',summary='文件已写，尚未回读')
        other=PersonalHarness(self.root/'tickets.db');self.assertEqual(other.recover(),1)
        self.assertEqual(other.get(row['id'])['state'],'interrupted')
        self.assertEqual(other.get(row['id'])['session'],'resume-me')
    def test_experience_requires_human_review_and_receipt(self):
        row=self.ticket();lesson=self.store.propose('001',row['id'],'写入后回读，未确认不要重发')
        self.assertFalse(self.store.lessons('001',active=True))
        self.store.receipt(row['id'],'local_computer',{'action':'read'},{'ok':True})
        self.store.update(row['id'],state='awaiting_review')
        self.store.review('001',row['id'],True)
        self.assertEqual(self.store.lessons('001',active=True)[0]['id'],lesson['id'])
        self.store.disable_lesson('001',lesson['id']);self.assertFalse(self.store.lessons('001',active=True))
    def test_failed_or_receiptless_tasks_cannot_activate_experience(self):
        row=self.ticket();self.store.propose('001',row['id'],'候选')
        self.store.update(row['id'],state='failed');self.store.review('001',row['id'],True)
        self.assertFalse(self.store.lessons('001',active=True))
    def test_member_isolation_and_secret_redaction(self):
        row=self.ticket();self.assertFalse(self.store.recent('002'))
        with self.assertRaises(ValueError):self.store.propose('002',row['id'],'不属于此成员')
        self.store.update(row['id'],summary='sk-'+'A'*32)
        self.assertNotIn('A'*32,self.store.get(row['id'])['summary'])
    def test_followups_persist_and_are_consumed_once(self):
        row=self.ticket();self.store.add_input(row['id'],'再保存一份摘要')
        other=PersonalHarness(self.root/'tickets.db')
        self.assertIn('摘要',other.take_inputs(row['id']));self.assertEqual(other.take_inputs(row['id']),'')
    def test_backend_is_local_first_and_dsh_only_for_engineering(self):
        self.assertFalse(use_dsh('给微信小军发一段介绍并发送'))
        self.assertFalse(use_dsh('打开腾讯会议找到最新录制并上传B站'))
        self.assertTrue(use_dsh('重构这个Python项目并修复多文件测试'))
        self.assertFalse(use_dsh('分析当前截图',image=True))
        self.assertFalse(use_dsh('旧执行器',backend='self'))


from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QSettings,QTimer
APP=QApplication.instance() or QApplication([])


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.root=Path(cls.temp.name)
        cls.pipe='guga-self-test-'+uuid.uuid4().hex
        cls.patches=[patch.dict(os.environ,{'GUGA_MCP_PIPE':cls.pipe}),
                     patch('ui.agent_chat.FAST_DIAGNOSTICS_PATH',cls.root/'fast-diagnostics.json'),
                     patch('core.keyboard_hook.KeyboardHook.start'),patch('ui.tts_engine.TTSEngine.speak')]
        for p in cls.patches:p.start()
        QSettings.setDefaultFormat(QSettings.IniFormat);QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,cls.temp.name)
        from ui.pet_window import PetWindow
        from core.chat_workspace import ChatWorkspace
        from ui.agent_chat import AgentChatWindow
        cls.pet=PetWindow()
        for timer in cls.pet.findChildren(QTimer):timer.stop()
        cls.panel=AgentChatWindow(cls.pet,ChatWorkspace(cls.root/'chat.db'));cls.pet._agent_panel=cls.panel
    @classmethod
    def tearDownClass(cls):
        cls.panel.shutdown();cls.pet._self_mcp.close();cls.pet._screen_tools.stop();cls.pet._tray.hide();cls.pet.hide();cls.panel.hide()
        for p in reversed(cls.patches):p.stop()
        cls.temp.cleanup()
    def setUp(self):
        self.panel.busy=False;self.panel._run_kind=None;self.panel.agent.cancel_event.clear()
        self.panel.open_conversation(self.panel.store.create_conversation())
    def pump(self,predicate,seconds=6):
        end=time.monotonic()+seconds
        while not predicate() and time.monotonic()<end:APP.processEvents();time.sleep(.01)
        self.assertTrue(predicate())
    def test_typed_qa_while_dsh_is_busy_does_not_steer_or_clear_the_task(self):
        p=self.panel;p.busy=True;p._run_kind='dsh';reports=p._dsh_reports
        try:
            with patch.object(p.dialogue,'start') as qa,patch.object(p,'_steer_execution') as steer,patch.object(p.runtime.dsh,'stop') as stop:
                self.assertTrue(p.send('为什么月亮看起来是圆的？'))
                qa.assert_called_once();steer.assert_not_called();stop.assert_not_called()
                p._dialogue_completed(dict(generation=p.dialogue.generation,ok=True,text='月球本身近似球体。'))
                self.assertTrue(p.busy);self.assertEqual(p._run_kind,'dsh');self.assertIs(p._dsh_reports,reports)
                self.assertIn('月球本身',p.store.messages(p.active_id)[-1]['content'])
        finally:p._dialogue_context=None;p.busy=False;p._run_kind=None

    def test_task_correction_still_goes_to_the_executor_instead_of_chat(self):
        p=self.panel;p.busy=True;p._run_kind='dsh'
        try:
            with patch.object(p.dialogue,'start') as qa,patch.object(p,'_steer_execution') as steer:
                self.assertTrue(p.send('改成中文版本'))
                qa.assert_not_called();steer.assert_called_once_with('改成中文版本')
        finally:p.busy=False;p._run_kind=None
    def test_daily_task_uses_local_agent_while_fast_commands_stay_without_llm(self):
        p=self.panel
        with patch.object(p.runtime.dsh,'start',return_value={'accepted':True}) as dsh,patch.object(p.agent,'start') as old:
            self.assertTrue(p.send('搜索三个来源，写入文件并核对内容'))
            self.assertEqual(p._run_kind,'task');old.assert_called_once();dsh.assert_not_called()
        p.busy=False;p._run_kind=None
        with patch.object(p.runtime.dsh,'start',return_value={'accepted':True,'job_id':'code-job'}) as dsh,patch.object(p.agent,'start') as old:
            self.assertTrue(p.send('重构这个Python项目的多个代码文件并修复测试'))
            self.assertEqual(p._run_kind,'dsh');dsh.assert_called_once();old.assert_not_called()
        p.busy=False;p._run_kind=None
        with patch.object(p,'_fast_command',return_value={'ok':True}) as quick,patch.object(p.runtime.dsh,'start') as dsh:
            p.send('打开浏览器');quick.assert_called_once();dsh.assert_not_called()

    def test_complex_voice_task_requires_one_confirmation_then_starts(self):
        p=self.panel;voice=Mock();p.voice=voice;p.voice_active=True;p.busy=False;p._pending_execution=None
        try:
            with patch.object(p,'sender',return_value=voice),patch.object(p,'_show_active_bubble'),patch.object(p,'send',return_value=True) as send:
                p._voice_request('c1','把最新会议录制上传到B站并准备投稿')
                self.assertFalse(send.called)
                self.assertIsNotNone(p._pending_execution)
                self.assertIn('确认执行',voice.resolve_task.call_args.args[1]['answer'])
                p._voice_request('c2','确认执行')
                send.assert_called_once()
                self.assertIsNone(p._pending_execution)
        finally:
            p.busy=False;p.voice_active=False;p._pending_execution=None

    def test_message_question_and_asr_commas_reach_computer_tools_instead_of_qa(self):
        p=self.panel;old=p.voice;voice=Mock();p.voice=voice;p.voice_active=True;p._pending_execution=None
        request='打开，微信，找到验收联系人，发送，最近怎么样，忙完了吗'
        try:
            with patch.object(p,'sender',return_value=voice),patch.object(p.agent,'start') as agent, \
                 patch.object(p.answer,'start') as answer,patch.object(p.dialogue,'start') as dialogue, \
                 patch.object(p.runtime.dsh,'start') as dsh,patch.object(p,'_show_active_bubble'):
                p._voice_request('fixture-send',request)
                agent.assert_called_once();self.assertEqual(agent.call_args.args[2],request)
                self.assertEqual(p._run_kind,'task');answer.assert_not_called();dialogue.assert_not_called();dsh.assert_not_called()
        finally:
            p.voice=old;p.voice_active=False;p.busy=False;p._run_kind=None;p._voice_task=None;p._task_heartbeat_timer.stop()

    def test_typed_installed_app_request_bypasses_models_and_runs_on_worker(self):
        p=self.panel;release=threading.Event();entered=threading.Event()
        def launch(name):
            self.assertEqual(name,'微信');entered.set();release.wait(2)
            return dict(name=name,launch_requested=True,verified_running=False,message='验收启动请求')
        try:
            with patch('core.authorized_apps.AuthorizedApps.open',side_effect=launch), \
                 patch.object(p.agent,'start') as agent,patch.object(p.answer,'start') as answer:
                self.assertTrue(p.send('打开，微信'));self.assertTrue(entered.wait(.5))
                self.assertTrue(p._last_fast_result['execution_pending']);release.set()
                self.pump(lambda:getattr(p,'_fast_launch_count',0)==0,1)
                self.assertTrue(p._last_fast_result['ok']);agent.assert_not_called();answer.assert_not_called()
        finally:release.set()

    def test_slow_browser_provider_keeps_qt_responsive_and_never_starts_task_model(self):
        from core.fast_commands import match
        p=self.panel;release=threading.Event();entered=threading.Event();ticks=[]
        old=getattr(p,'_browser_interaction',None)
        def slow(command,hwnd=None):
            entered.set();release.wait(2)
            return dict(ok=False,error='provider timeout',action_sent=None)
        p._browser_interaction=Mock();p._browser_interaction.execute.side_effect=slow
        try:
            with patch.object(p.agent,'start') as agent,patch.object(p.runtime.dsh,'start') as dsh,patch.object(p,'_show_active_bubble'):
                start=time.monotonic();receipt=p._fast_command(match('点击测试按钮'))
                self.assertLess(time.monotonic()-start,.2);self.assertTrue(receipt['execution_pending'])
                self.assertTrue(entered.wait(.5));QTimer.singleShot(0,lambda:ticks.append(True))
                self.pump(lambda:bool(ticks),.5)
                self.assertTrue(p._fast_inflight)
                release.set();self.pump(lambda:not p._fast_inflight,1)
                agent.assert_not_called();dsh.assert_not_called()
                self.assertFalse(p._last_fast_result['ok'])
        finally:
            release.set();p._browser_interaction=old;p._direct_control_until=0
    def test_close_page_voice_bypasses_confirmation_and_dsh(self):
        p=self.panel;voice=Mock();old=p.voice;p.voice=voice;p.voice_active=True;p.busy=False
        p._pending_execution=dict(text='旧的复杂任务',created=time.monotonic())
        try:
            with patch.object(p,'sender',return_value=voice),patch.object(p.runtime.dsh,'start') as dsh,patch.object(p.agent,'start') as agent:
                p._voice_request('close-page','不不不，我的意思是让你关闭页面。')
                self.assertIsNone(p._pending_execution)
                command=voice._dispatch_fast.call_args.args[0]
                self.assertEqual(command['action'],'close_page');dsh.assert_not_called();agent.assert_not_called()
        finally:p.voice=old;p.voice_active=False;p._pending_execution=None

    def test_voice_session_local_control_checks_free_guard_and_status_without_connecting(self):
        from unittest.mock import Mock
        p=self.panel;runtime=p.runtime;old=p.voice;flag=p.voice_active;p.voice=Mock();p.voice.active=False
        try:
            with patch('core.realtime_voice.load_settings',return_value=dict(provider='qianwen',model='qwen-audio-3.1-realtime-plus',
                 free_only=True,free_quota_confirmed_provider='other',free_quota_confirmed_model='qwen-audio-3.1-realtime-plus')), \
                 patch('core.realtime_voice.connection_url',return_value='wss://unit.test'):
                with self.assertRaisesRegex(RuntimeError,'免费额度保护未匹配'):runtime.execute('voice_session',{'action':'start'})
            p.voice_active=False
            p.voice._usage_rounds=3;p.voice._usage_total=37;p.voice._usage_missing=1
            with patch('core.realtime_voice.load_settings',return_value=dict(provider='qianwen',model='qwen-audio-3.1-realtime-plus',
                 free_only=True,free_quota_confirmed_provider='qianwen',free_quota_confirmed_model='qwen-audio-3.1-realtime-plus')):
                status=runtime.execute('voice_session',{'action':'status'})
            self.assertFalse(status['active']);self.assertTrue(status['quota_confirmation_matches'])
            self.assertEqual(status['usage']['reported_total_tokens'],37)
            self.assertFalse(p.voice.active)
        finally:p.voice=old;p.voice_active=flag

    def test_long_call_owns_screen_sharing_without_visible_preview(self):
        p=self.panel
        shared=dict(started=True,fps=3,active=True,context='当前是记事本，输入区有测试文字。',context_age_seconds=1,fresh=True)
        try:
            with patch.dict(os.environ,{'QT_QPA_PLATFORM':''}), \
                 patch.object(p.runtime.screen_share,'status',side_effect=[dict(active=False),shared,shared,shared]), \
                 patch.object(p.runtime.screen_share,'start',return_value=shared) as start, \
                 patch.object(p.runtime.screen_share,'stop',return_value=dict(active=False)) as stop:
                p._start_call_screen_share()
                start.assert_called_once_with(reason='call');self.assertTrue(p._screen_share_call_owned)
                self.assertFalse(hasattr(p.runtime,'screen_preview'))
                p._stop_call_screen_share();stop.assert_called_once();self.assertFalse(p._screen_share_call_owned)
        finally:
            p._screen_share_call_owned=False

    def test_screen_share_watchdog_recovers_once_but_respects_manual_stop(self):
        p=self.panel;flag=p.voice_active
        try:
            p.voice_active=True;p._screen_share_call_owned=True;p._screen_share_manual_stop=False;p._screen_share_retry_at=0
            with patch.object(p.runtime.screen_share,'status',return_value=dict(active=False)), \
                 patch.object(p.runtime.screen_share,'start',return_value=dict(started=True,active=True)) as start:
                p._screen_share_watchdog();start.assert_called_once_with(reason='call_recovery')
            p._screen_share_manual_stop=True;p._screen_share_retry_at=0
            with patch.object(p.runtime.screen_share,'status',return_value=dict(active=False)), \
                 patch.object(p.runtime.screen_share,'start') as start:
                p._screen_share_watchdog();start.assert_not_called()
        finally:p.voice_active=flag;p._screen_share_call_owned=False;p._screen_share_manual_stop=False

    def test_first_share_start_failure_is_diagnosed_and_recoverable(self):
        p=self.panel;flag=p.voice_active;p.voice_active=True
        try:
            with patch.dict(os.environ,{'QT_QPA_PLATFORM':''}), \
                 patch('ui.agent_chat.SCREEN_SHARE_DIAGNOSTICS_PATH',self.root/'share-lifecycle.json'), \
                 patch.object(p.runtime.screen_share,'status',return_value=dict(active=False)), \
                 patch.object(p.runtime.screen_share,'start',side_effect=[RuntimeError('temporary capture failure'),dict(active=True)]) as start:
                p._start_call_screen_share()
                self.assertTrue(p._screen_share_call_owned);self.assertTrue(p._screen_share_watchdog_timer.isActive())
                self.assertIn('temporary capture',p._screen_share_start_error)
                self.assertTrue((self.root/'share-lifecycle.json').is_file())
                p._screen_share_watchdog();self.assertEqual(start.call_count,2)
                self.assertEqual(p._screen_share_start_error,'')
        finally:
            p._screen_share_watchdog_timer.stop();p.voice_active=flag;p._screen_share_call_owned=False

    def test_legacy_long_call_entry_uses_agent_and_screen_path(self):
        with patch.object(self.pet,'_open_agent_voice') as start:
            self.pet._open_realtime_voice();start.assert_called_once_with()

    def test_long_call_wires_screen_provider_before_voice_connection(self):
        from core.realtime_voice import DEFAULTS
        p=self.panel;old=p.voice;flag=p.voice_active;p.voice=None;p.voice_active=False
        shared=dict(active=True,fresh=True,context='真实测试控件',foreground=dict(hwnd=42),sequence=3)
        try:
            with patch('core.realtime_voice.load_settings',return_value=dict(DEFAULTS)), \
                 patch('core.realtime_voice.connection_url',return_value='wss://unit.test'), \
                 patch.object(p.pet,'_realtime_active',return_value=False), \
                 patch.object(p,'_start_call_screen_share') as share, \
                 patch.object(p.runtime.screen_share,'assist_context',return_value=shared), \
                 patch('core.realtime_agent.AgentRealtimeVoice.start') as start:
                p.toggle_voice(True)
                # The bridge is wired before connecting, but capture is deferred
                # to the connected callback so it never delays the handshake.
                share.assert_not_called();start.assert_called_once()
                self.assertTrue(p._screen_share_pending)
                self.assertTrue(p.voice.screen_bridge_status()['attached'])
                p._voice_connected_changed(True)
                share.assert_called_once();self.assertFalse(p._screen_share_pending)
                p.voice._cfg=dict(DEFAULTS)
                self.assertIn('真实测试控件',p.voice._session_settings()['instructions'])
        finally:
            p.toggle_voice(False);p.voice=old;p.voice_active=flag

    def test_read_screen_uses_actual_frame_path_without_starting_desktop_task(self):
        p=self.panel;old=p.voice;flag=p.voice_active
        from core.realtime_agent import AgentRealtimeVoice
        voice=p.voice=AgentRealtimeVoice();p.voice_active=True
        voice._tasks['fixture-read']=None
        try:
            with patch.object(p.runtime.screen_share,'status',return_value=dict(active=True)), \
                 patch.object(p.runtime.screen_share,'snapshot',return_value=dict(ok=True,
                     _image_url='data:image/jpeg;base64,AA==',frame_age_seconds=.1,sequence=8)), \
                 patch('core.screen_visual.observe_frame',return_value=dict(ok=True,text='蓝色圆形和验收文字')) as answer, \
                 patch.object(voice,'resolve_task') as resolve,patch.object(p.agent,'start') as task:
                p._voice_screen_question('fixture-read','当前有什么颜色？')
                self.pump(lambda:resolve.called)
                self.assertIn('蓝色圆形',resolve.call_args.args[1]['observation'])
                self.assertEqual(answer.call_args.args[0],'data:image/jpeg;base64,AA==')
                task.assert_not_called()
        finally:p.voice=old;p.voice_active=flag

    def test_general_screen_question_uses_cached_facts_without_new_visual_request(self):
        from core.realtime_agent import AgentRealtimeVoice
        p=self.panel;old=p.voice;flag=p.voice_active;p.voice=AgentRealtimeVoice();p.voice_active=True
        try:
            with patch.object(p.runtime.screen_share,'status',return_value=dict(active=True)), \
                 patch.object(p.runtime.screen_share,'assist_context',return_value=dict(active=True,fresh=True,context='蓝色圆形')), \
                 patch('core.screen_visual.observe_frame') as perception, \
                 patch.object(p.voice,'resolve_task') as resolve:
                p._voice_screen_question('cached','能看到我的屏幕吗？')
                perception.assert_not_called();self.assertEqual(resolve.call_args.args[1]['observation'],'蓝色圆形')
                self.assertNotIn('answer',resolve.call_args.args[1])
        finally:p.voice=old;p.voice_active=flag

    def test_visible_text_question_uses_local_controls_without_cloud_vision(self):
        from core.realtime_agent import AgentRealtimeVoice
        p=self.panel;old=p.voice;flag=p.voice_active;p.voice=AgentRealtimeVoice();p.voice_active=True
        try:
            with patch.object(p.runtime.screen_share,'status',return_value=dict(active=True)), \
                 patch.object(p.runtime.screen_share,'local_context',return_value=dict(fresh=True,age_seconds=.1,context='Edit：今天想看什么；Button：收藏空间')), \
                 patch('core.screen_visual.observe_frame') as perception,patch.object(p.voice,'resolve_task') as resolve:
                p._voice_screen_question('local-read','当前网页有哪些按钮和输入框？')
                perception.assert_not_called();value=resolve.call_args.args[1]
                self.assertTrue(value['local_only']);self.assertFalse(value['visual_content']);self.assertIn('收藏空间',value['observation'])
        finally:p.voice=old;p.voice_active=flag

    def test_call_screen_context_is_injected_into_task_handoff(self):
        p=self.panel;flag=p.voice_active;p.voice_active=True
        assist=dict(active=True,fresh=True,context='当前是编辑器，目标文件已打开。',
            foreground=dict(hwnd=42,title='编辑器',process='editor.exe'),sequence=8,
            context_matches_foreground=True,frame_age_seconds=.1,context_age_seconds=1)
        try:
            with patch.object(p.runtime.screen_share,'status',return_value=dict(active=True)), \
                 patch.object(p.runtime.screen_share,'assist_context',return_value=assist), \
                 patch.object(p.agent,'start') as agent:
                self.assertTrue(p.send('看看当前编辑器里的目标',force_agent=True))
            handoff=agent.call_args.kwargs['handoff']['_screen_context']
            self.assertTrue(handoff['fresh']);self.assertEqual(handoff['foreground']['hwnd'],42)
            self.assertIn('目标文件',handoff['context'])
        finally:p.voice_active=flag;p.busy=False;p._run_kind=None

    def test_close_worker_returns_receipt_without_blocking_qt(self):
        p=self.panel;p._fast_inflight=False
        from core.fast_commands import match
        finished=threading.Event()
        def execute(command):
            finished.wait(.1)
            return dict(ok=False,code='target_ambiguous',error='有多个浏览器，尚未发送操作，请先切到目标页面')
        with patch('core.fast_commands.execute',side_effect=execute):
            accepted=p._fast_command(match('关闭当前页面'))
            self.assertTrue(accepted['execution_pending']);self.assertTrue(p._fast_inflight)
            self.assertFalse(p.send('读取一个文件',force_agent=True))
            finished.set();self.pump(lambda:not p._fast_inflight)
        self.assertIn('有多个浏览器',p._last_fast_result['error'])
        self.assertIn('有多个浏览器',p.store.messages(p.active_id)[-1]['content'])

    def test_website_launch_does_not_block_voice_event_loop(self):
        p=self.panel
        from core.fast_commands import match
        release=threading.Event();started=threading.Event();ticks=[]
        def execute(command):
            started.set();release.wait(2)
            return dict(ok=True,launch_requested=True,message='已提交测试网址')
        with patch('core.fast_commands.execute',side_effect=execute):
            before=time.monotonic()
            accepted=p._fast_command(match('你能帮我打开 GitHub 官网吗'))
            self.assertLess(time.monotonic()-before,.1)
            self.assertTrue(accepted['execution_pending'])
            try:
                self.assertTrue(started.wait(1))
                QTimer.singleShot(0,lambda:ticks.append(True));APP.processEvents()
                self.assertTrue(ticks)
            finally:
                release.set();self.pump(lambda:not getattr(p,'_fast_launch_count',0))
        self.assertTrue(p._last_fast_result['launch_requested'])

    def test_failure_can_be_explained_locally_with_model_unavailable(self):
        p=self.panel;p._last_fast_failure=dict(error='未完成：读取标签失败；尚未发送操作。请切到目标页面',at=time.time()+1,conversation_id=p.active_id)
        with patch.object(p.agent,'start') as agent,patch.object(p.runtime.dsh,'start') as dsh:
            p.send('刚才为什么失败')
            self.assertIn('尚未发送操作',p.store.messages(p.active_id)[-1]['content'])
            agent.assert_not_called();dsh.assert_not_called()
    def test_local_personal_memory_survives_new_conversation(self):
        p=self.panel;p.send('记住：我喜欢简洁的项目报告')
        p.open_conversation(p.store.create_conversation());p.send('查看我的记忆')
        self.assertIn('简洁的项目报告',p.store.messages(p.active_id)[-1]['content'])
        self.assertFalse(p.busy)
    def test_dsh_final_releases_ui_and_remains_awaiting_review(self):
        from tools.test_dsh_worker import FakeProc
        p=self.panel;mgr=p.runtime.dsh
        lines=['{"type":"session","sessionId":"ui-snapshot"}\n',
               '{"type":"status","phase":"step_start"}\n',
               '{"type":"final","text":"这是执行器的交付，需要核对。"}\n']
        with patch('core.dsh_worker.subprocess.Popen',return_value=FakeProc(lines)):
            self.assertTrue(p.send('重构这个Python项目的源码并运行单元测试'))
            self.pump(lambda:not p.busy)
            mgr.thread.join(2)
        self.assertEqual(mgr.status()['ticket_state'],'awaiting_review')
        self.assertIn('尚未独立核验',p.store.messages(p.active_id)[-1]['content'])
        self.assertTrue(p.new_button.isEnabled())
        self.assertNotIn('dsh:'+mgr.state['job_id'],p.pet._anim2d._task_motion.jobs)
        self.assertNotIn('turn:'+p.active_id,p.pet._anim2d._task_motion.jobs)
    def test_important_reports_speak_through_edge_without_a_call(self):
        from core.task_speech import TaskSpeechQueue
        p=self.panel;p.voice_active=False;p._task_speech=TaskSpeechQueue()
        with patch.object(type(p.pet._tts),'is_speaking',new_callable=PropertyMock,return_value=False),patch.object(p.pet._tts,'speak') as speak:
            p._dsh_event(dict(type='dsh_event',job_id='edge-proof',conversation_id=p.active_id,
                             event=dict(type='report',state='progress',milestone=True,summary='录制视频已经下载，接下来处理字幕。')))
            speak.assert_called_once();self.assertIn('字幕',speak.call_args.args[0])
        p._dsh_reports.clear()
    def test_needs_input_is_persisted_as_a_question_instead_of_success(self):
        from tools.test_dsh_worker import FakeProc
        p=self.panel;mgr=p.runtime.dsh
        def launch(*args,**kwargs):
            mgr.state.update(report_state='needs_input',report_summary='成片已准备好，是否发布？')
            return FakeProc(['{"type":"session","sessionId":"needs-input-session"}\n',
                             '{"type":"final","text":"等待用户决定。"}\n'])
        with patch('core.dsh_worker.subprocess.Popen',side_effect=launch):
            self.assertTrue(p.send('修复这个Python项目的源码和单元测试'));self.pump(lambda:not p.busy);mgr.thread.join(2)
        self.assertEqual(mgr.status()['ticket_state'],'needs_input')
        self.assertIn('等待你的决定',p.store.messages(p.active_id)[-1]['content'])
    def test_important_reports_use_the_live_call_without_edge_overlap(self):
        from core.task_speech import TaskSpeechQueue
        p=self.panel;old=p.voice;p._task_speech=TaskSpeechQueue()
        from types import SimpleNamespace
        # 这条测的是「通话中走实时语音通道」。_flush_task_speech 用 voice.connected 判断是否
        # 真的在通话里；假 voice 不给 connected=True 就永远走本机朗读，测不到本意。
        notify=Mock();p.voice=SimpleNamespace(is_speaking=False,connected=True,
            notify_battleground=notify,set_task_context=Mock());p.voice_active=True
        try:
            with patch.object(p.pet._tts,'speak') as edge:
                p._dsh_event(dict(type='dsh_event',job_id='voice-proof',conversation_id=p.active_id,
                                 event=dict(type='report',state='needs_input',summary='成片已经准备好，是否发布？')))
                notify.assert_called_once();self.assertTrue(notify.call_args.kwargs['during_task']);edge.assert_not_called()
        finally:p.voice=old;p.voice_active=False;p._dsh_reports.clear()
    def test_stop_confirmation_cancels_dsh(self):
        p=self.panel;p.busy=True;p._run_kind='dsh'
        with patch.object(p.runtime.dsh,'stop') as stop:
            p.send('停止任务');stop.assert_not_called()
            p.send('停止');stop.assert_called_once()
    def test_real_stdio_mcp_to_qt_thread_to_existing_file_executor(self):
        p=self.panel;mgr=p.runtime.dsh
        row=mgr.archive.create('001',p.active_id,'读取测试文件',self.root)
        mgr.state=dict(running=True,job_id='test-job',ticket_id=row['id'],agent_id='001',conversation_id=p.active_id,cwd=str(self.root))
        mgr.stop_event=threading.Event()
        from core.dsh_tools import DshTools
        mgr.tool_executor=DshTools(p,mgr)
        target=self.root/'proof.txt';target.write_text('BRIDGE_PROOF',encoding='utf-8')
        results=[]
        def run():
            from core.mcp_host import StdioMCP
            cli=StdioMCP(dict(command=sys.executable,args=[str(ROOT/'tools/guga_mcp_server.py'),'--dsh-tools'],
                              env={'GUGA_DSH_JOB':'test-job','GUGA_MCP_PIPE':self.pipe}))
            try:
                cli.initialize();tools=cli.request('tools/list')
                results.append(tools)
                results.append(cli.request('tools/call',dict(name='local_computer',arguments=dict(action='read',path=str(target)))))
            except Exception as exc:results.append({'error':str(exc)})
            finally:cli.close()
        thread=threading.Thread(target=run);thread.start();self.pump(lambda:not thread.is_alive(),12)
        self.assertEqual(len(results),2,results)
        self.assertIn('personal_memory',[x['name'] for x in results[0]['tools']])
        self.assertIn('BRIDGE_PROOF',json.dumps(results[1]));self.assertFalse(results[1].get('isError'))
        self.assertEqual(mgr.archive.receipt_count(row['id']),1)
        mgr.stop_event.set();self.assertFalse(mgr.execute_tool('test-job','local_computer',{'action':'read','path':str(target)})['ok'])
        mgr.state['running']=False


if __name__=='__main__':unittest.main()
