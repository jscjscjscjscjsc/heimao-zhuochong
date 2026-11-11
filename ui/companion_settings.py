"""Editable identities, existing layered memory and reusable long workflows."""
import threading
from PyQt5.QtCore import Qt,pyqtSignal
from PyQt5.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QTabWidget,QWidget,
    QPlainTextEdit,QLineEdit,QPushButton,QComboBox,QListWidget,QListWidgetItem,QSplitter)
from ui.companion_widgets import member_icon,SETTINGS_STYLE


def label(text,muted=False):
    result=QLabel(text);result.setWordWrap(True);result.setObjectName('Muted' if muted else 'SectionTitle')
    return result


class CompanionSettings(QDialog):
    lingshu_ready=pyqtSignal(object)

    def __init__(self,panel,agent_id=None,tab=0):
        super().__init__(panel,Qt.Window|Qt.WindowCloseButtonHint)
        self.panel=panel;self.agent_id=str(agent_id or panel.active_agent_id)
        current=panel.store.conversation(panel.active_id)
        self.conversation_id=panel.active_id if current.get('agent_id')==self.agent_id else None
        self.profile=panel.memory.profile(self.agent_id);self._flow_id=None;self._ling_busy=False
        self.setWindowTitle(self.profile['display_name']+' · 角色与工作流')
        self.setWindowIcon(member_icon(self.agent_id));self.resize(800,700);self.setMinimumSize(660,560)
        self.setStyleSheet(SETTINGS_STYLE)
        root=QVBoxLayout(self);root.setContentsMargins(26,22,26,18);root.setSpacing(14)
        header=QHBoxLayout();portrait=QLabel();portrait.setPixmap(member_icon(self.agent_id).pixmap(64,64));header.addWidget(portrait)
        titles=QVBoxLayout();title=label(self.profile['display_name']+'的工作间');title.setObjectName('PageTitle');titles.addWidget(title)
        titles.addWidget(label('人设、习惯与记忆各自保存，常用工作可以写成可复用的流程。',True));header.addLayout(titles,1);root.addLayout(header)
        self.tabs=QTabWidget();root.addWidget(self.tabs,1)
        self._identity_tab();self._memory_tab();self._workflow_tab();self.tabs.setCurrentIndex(tab)
        self.notice=label('修改后点击对应的保存按钮。',True);root.addWidget(self.notice)
        self.lingshu_ready.connect(self._lingshu_received)

    def page(self,title):
        widget=QWidget();layout=QVBoxLayout(widget);layout.setContentsMargins(20,18,20,18);layout.setSpacing(10)
        self.tabs.addTab(widget,title);return layout

    def button(self,text,callback,primary=False):
        b=QPushButton(text);b.setAutoDefault(False);b.clicked.connect(callback)
        if primary:b.setObjectName('Primary')
        return b

    def guard(self,callback):
        try:return callback()
        except Exception as exc:self.notice.setText(str(exc));return None

    def _identity_tab(self):
        box=self.page('人设与习惯')
        row=QHBoxLayout();row.addWidget(label('如何称呼你'));self.address=QComboBox();self.address.setEditable(True)
        self.address.addItems(['饲养员','主人']);self.address.setCurrentText(self.profile.get('user_address') or '饲养员');row.addWidget(self.address);row.addStretch();box.addLayout(row)
        box.addWidget(label('角色人设'))
        self.personality=QPlainTextEdit(self.profile.get('personality') or '');self.personality.setPlaceholderText('这个角色如何说话、怎样做事…');box.addWidget(self.personality,1)
        box.addWidget(label('用户习惯 · 附加系统提示词'))
        self.instructions=QPlainTextEdit(self.profile.get('user_instructions') or '')
        self.instructions.setPlaceholderText('例如：先用大白话给结论；我问技术问题时给一个例子；后台任务遇到重要变化再告诉我。')
        box.addWidget(self.instructions,1)
        box.addWidget(label('保存后用于这个角色接下来的文字问答、任务执行与新语音会话；保留已有工具与真实性规则。',True))
        row=QHBoxLayout();row.addStretch();row.addWidget(self.button('保存人设与习惯',lambda:self.guard(self.save_identity),True));box.addLayout(row)

    def save_identity(self):
        personality=self.personality.toPlainText().strip()
        if not personality or len(personality)>8000:raise ValueError('角色人设需要 1—8000 字')
        self.profile=self.panel.memory.update_profile(self.agent_id,personality=personality,
            user_instructions=self.instructions.toPlainText(),user_address=self.address.currentText())
        if self.panel.active_agent_id==self.agent_id:self.panel.active_profile=self.profile
        self.panel.refresh_companion_header();self.notice.setText('已保存，下一轮对话立即使用；正在进行的语音会话结束后生效。')

    def _memory_tab(self):
        box=self.page('长期记忆')
        box.addWidget(label('这个角色的记忆'))
        box.addWidget(label('沿用现有用户 / 项目 / 对话分层。选择一条可编辑；保存不会改动其他角色的记忆。',True))
        self.memories=QListWidget();self.memories.currentItemChanged.connect(self._select_memory);box.addWidget(self.memories,1)
        self.memory_text=QPlainTextEdit();self.memory_text.setMaximumHeight(100);self.memory_text.setPlaceholderText('写下希望这个角色记住的偏好或事实…');box.addWidget(self.memory_text)
        row=QHBoxLayout();self.scope=QComboBox()
        for text,value in [('用户长期偏好','user'),('角色记忆','agent'),('当前项目','project'),('当前对话','conversation')]:self.scope.addItem(text,value)
        if not self.conversation_id:
            for index in (2,3):self.scope.model().item(index).setEnabled(False)
        elif not self._location().get('project_id'):self.scope.model().item(2).setEnabled(False)
        row.addWidget(self.scope);row.addStretch()
        row.addWidget(self.button('新增',lambda:self.guard(lambda:self.save_memory(False))))
        row.addWidget(self.button('更新所选',lambda:self.guard(lambda:self.save_memory(True)),True))
        row.addWidget(self.button('忘记所选',lambda:self.guard(self.forget_memory)));box.addLayout(row)
        box.addWidget(label('咕嘎的灵枢记忆'))
        self.ling_status=label('保留现有灵枢连接；仅主动查询时读取，不影响聊天响应。',True);box.addWidget(self.ling_status)
        row=QHBoxLayout();self.ling_query=QLineEdit();self.ling_query.setPlaceholderText('查询灵枢里的用户偏好或工作经验');row.addWidget(self.ling_query)
        self.ling_button=self.button('查询灵枢',self.query_lingshu);row.addWidget(self.ling_button);box.addLayout(row)
        self.ling_save=self.button('将上方内容记入灵枢',lambda:self.guard(self.save_lingshu));row.addWidget(self.ling_save)
        self.ling_results=QPlainTextEdit();self.ling_results.setReadOnly(True);self.ling_results.setMaximumHeight(110);box.addWidget(self.ling_results)
        if self.agent_id!='001':
            self.ling_button.setEnabled(False);self.ling_save.setEnabled(False);self.ling_status.setText('灵枢仍由咕嘎使用；这个角色使用上方独立的分层记忆，避免混入咕嘎的经历。')
        self.refresh_memories()

    def _location(self):
        row=self.panel.store.conversation(self.conversation_id) if self.conversation_id else {}
        return dict(conversation_id=self.conversation_id,project_id=row.get('project_id'))

    def refresh_memories(self):
        self.memories.clear()
        names={'user':'用户','agent':'角色','project':'项目','conversation':'对话'}
        for fact in self.panel.memory.facts(self.agent_id,100,**self._location()):
            item=QListWidgetItem('['+names.get(fact['scope'],fact['scope'])+'] '+fact['content'])
            item.setData(Qt.UserRole,fact);self.memories.addItem(item)

    def _select_memory(self,item,previous=None):
        if item:self.memory_text.setPlainText(item.data(Qt.UserRole)['content'])

    def save_memory(self,update=False):
        args=dict(action='remember',scope=self.scope.currentData(),content=self.memory_text.toPlainText())
        if update:
            item=self.memories.currentItem()
            if not item:raise ValueError('请先选择一条记忆')
            args.update(action='update',id=item.data(Qt.UserRole)['id'])
        self.panel.runtime.companion_memory.execute(self.agent_id,self.conversation_id,args)
        self.refresh_memories();self.notice.setText('记忆已保存，后续对话按当前范围召回。')

    def forget_memory(self):
        item=self.memories.currentItem()
        if not item:raise ValueError('请先选择一条记忆')
        self.panel.runtime.companion_memory.execute(self.agent_id,self.conversation_id,dict(action='forget',id=item.data(Qt.UserRole)['id']))
        self.refresh_memories();self.memory_text.clear();self.notice.setText('已忘记所选记忆。')

    def query_lingshu(self):
        query=self.ling_query.text().strip()
        if not query:self.notice.setText('请填写想查询的内容');return
        if self._ling_busy:return
        self._ling_busy=True;self.ling_button.setEnabled(False);self.ling_status.setText('正在查询现有灵枢记忆…')
        def work():
            from core.chat_memory import recall_context
            try:result=recall_context(query,agent_id='001',wait_seconds=6)
            except Exception:result=dict(state='unavailable',items=[])
            self.lingshu_ready.emit(result)
        threading.Thread(target=work,daemon=True,name='guga-memory-settings').start()

    def save_lingshu(self):
        from core.agent_memory import safe_text
        text=self.memory_text.toPlainText().strip()
        if not text or len(text)>4000 or safe_text(text)!=text:raise ValueError('请输入 1—4000 字的偏好或事实，不能包含密钥')
        if self.agent_id!='001' or self._ling_busy:return
        self._ling_busy=True;self.ling_button.setEnabled(False);self.ling_save.setEnabled(False)
        self.ling_status.setText('正在通过现有灵枢连接写入…')
        def work():
            from core.super_agent.lingshu_bridge import LingshuBridge,default_token
            bridge=None
            try:
                bridge=LingshuBridge(token=default_token(),timeout=8,startup_timeout=8)
                bridge.start()
                result=bridge.remember(text,tags=['guga','user-explicit'],ccg=dict(
                    title='用户明确保存的长期偏好',precondition='用户在咕嘎记忆设置中主动保存',
                    invocation='按用户明确填写的事实辅助后续任务，用户更正优先',
                    postcondition='灵枢写入接口返回保存凭据',body=text))
                # A rejected or queued write must never be presented as stored.
                state=str((result or {}).get('status') or (result or {}).get('state') or '').lower()
                verdict=str(((result or {}).get('verdict') or {}).get('state') or '').lower()
                if (result or {}).get('ok') is not True or state in ('rejected','reject','denied','failed','error','queued') or verdict in ('rejected','reject','denied','failed'):
                    raise ValueError('灵枢没有确认保存，请检查记忆服务状态')
                if not (result or {}).get('id') and not (result or {}).get('node_id') and not (result or {}).get('memory_id'):
                    raise ValueError('灵枢没有返回可核对的保存编号，本次不标为已保存')
                from core.chat_memory import invalidate
                invalidate()
                self.lingshu_ready.emit(dict(state='saved',items=[text]))
            except Exception:
                self.lingshu_ready.emit(dict(state='write_failed',items=[]))
            finally:
                if bridge is not None:bridge.close()
        threading.Thread(target=work,daemon=True,name='guga-memory-save').start()

    def _lingshu_received(self,result):
        self._ling_busy=False;self.ling_button.setEnabled(self.agent_id=='001');self.ling_save.setEnabled(self.agent_id=='001')
        self.ling_results.setPlainText('\n\n'.join(result.get('items') or []))
        self.ling_status.setText({'saved':'已记入现有灵枢；后续可按相关内容召回。','write_failed':'灵枢未确认保存；本地记忆不受影响，可检查服务后重试。','ok':'已从现有灵枢召回。','empty':'灵枢中没有相关记忆。','disabled':'当前灵枢已关闭。','busy':'灵枢正在处理其他查询，稍后可重试。'}.get(result.get('state'),'本次没有取得灵枢结果；本地记忆和聊天仍可使用。'))

    def _workflow_tab(self):
        box=self.page('工作流编排')
        box.addWidget(label('把常做的工作，写成下一次可以复用的步骤'))
        box.addWidget(label('支持 32000 字长流程。先写输入、执行步骤、完成标准与停止条件；重复成功的流程会保留为待确认候选。',True))
        split=QSplitter();self.flows=QListWidget();self.flows.setMinimumWidth(170);self.flows.setWordWrap(True);self.flows.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff);self.flows.currentItemChanged.connect(self._select_flow);split.addWidget(self.flows)
        editor=QWidget();form=QVBoxLayout(editor);form.setContentsMargins(12,0,0,0)
        self.flow_title=QLineEdit();self.flow_title.setPlaceholderText('流程名称');form.addWidget(self.flow_title)
        self.flow_trigger=QLineEdit();self.flow_trigger.setPlaceholderText('触发词，例如：投稿本地视频');form.addWidget(self.flow_trigger)
        self.flow_steps=QPlainTextEdit();self.flow_steps.setPlaceholderText('目标：\n输入资料：\n执行步骤：\n验收标准：\n需要确认的动作：\n失败时停止条件：');form.addWidget(self.flow_steps,1)
        self.flow_meta=label('保存不会立即执行。',True);form.addWidget(self.flow_meta);split.addWidget(editor);split.setSizes([190,440]);box.addWidget(split,1)
        row=QHBoxLayout();row.addWidget(self.button('新建流程',self.new_flow))
        row.addWidget(self.button('启用 / 停用',lambda:self.guard(self.toggle_flow)));row.addStretch()
        row.addWidget(self.button('放入对话',lambda:self.guard(self.use_flow)))
        row.addWidget(self.button('保存流程',lambda:self.guard(self.save_flow),True));box.addLayout(row)
        self.refresh_flows()

    def refresh_flows(self,select=None):
        self.flows.clear()
        for flow in self.panel.runtime.workflows.list():
            state='内置' if flow.get('builtin') else {'active':'已启用','candidate':'待确认','disabled':'已停用'}.get(flow.get('state'),'待确认')
            item=QListWidgetItem(flow['title']+'\n'+state);item.setData(Qt.UserRole,flow);self.flows.addItem(item)
            if flow['id']==select:self.flows.setCurrentItem(item)

    def _select_flow(self,item,previous=None):
        if not item:return
        flow=item.data(Qt.UserRole);self._flow_id=None if flow.get('builtin') else flow['id']
        self.flow_title.setText(flow['title']);self.flow_trigger.setText(flow.get('trigger') or flow['title'])
        self.flow_steps.setPlainText(flow['instructions'])
        self.flow_meta.setText(('内置流程，修改后保存为你的副本。' if flow.get('builtin') else
            '成功 %s 次 · 失败 %s 次 · %s'%(flow.get('success',0),flow.get('failure',0),flow.get('state',''))))

    def new_flow(self):
        self.flows.clearSelection();self._flow_id=None;self.flow_title.clear();self.flow_trigger.clear();self.flow_steps.clear();self.flow_meta.setText('新流程保存后启用；这里不会自动执行。')

    def save_flow(self):
        catalog=self.panel.runtime.workflows
        args=dict(trigger=self.flow_trigger.text(),instructions=self.flow_steps.toPlainText(),title=self.flow_title.text())
        flow=catalog.update(self._flow_id,**args) if self._flow_id else catalog.define(**args)
        self._flow_id=flow['id'];self.refresh_flows(flow['id']);self.notice.setText('工作流已保存，可通过触发词在对话中调用。')

    def toggle_flow(self):
        if not self._flow_id:raise ValueError('请先保存一个自定义流程；内置流程保留不变')
        catalog=self.panel.runtime.workflows;flow=catalog.get(self._flow_id)
        catalog.disable(self._flow_id) if flow['state']=='active' else catalog.activate(self._flow_id)
        self.refresh_flows(self._flow_id);self.notice.setText('已更新流程状态。')

    def use_flow(self):
        self.save_flow()
        if self.panel.runtime.workflows.get(self._flow_id)['state']!='active':raise ValueError('请先确认并启用这个流程，再放入对话')
        if not self.panel.activate_agent(self.agent_id):raise ValueError('请先结束当前任务或语音再切换角色')
        self.panel.editor.setPlainText('请执行工作流：'+self.flow_trigger.text().strip())
        self.panel.show();self.panel.raise_();self.close();self.panel.editor.setFocus()
