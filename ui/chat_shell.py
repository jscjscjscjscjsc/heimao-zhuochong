"""Compact floating chat shell. Runtime and voice remain owned by AgentChatWindow."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QFrame,QHBoxLayout,QVBoxLayout,QLabel,QPushButton,
    QTreeWidget,QTextBrowser,QPlainTextEdit,QSplitter,QCheckBox,QMenu,QSizeGrip,QComboBox)


def build(panel):
    p=panel
    p.setWindowTitle('咕嘎 · 对话')
    p.setWindowFlags(Qt.Window|Qt.FramelessWindowHint|Qt.WindowStaysOnTopHint)
    p.setAttribute(Qt.WA_TranslucentBackground)
    p.resize(720,680);p.setMinimumSize(520,490)
    p.setFont(QFont('Microsoft YaHei UI',10));p.setObjectName('AgentWindow')
    p.setStyleSheet('''
        QWidget { color:#252933; font-family:"Microsoft YaHei UI"; }
        QDialog#AgentWindow {background:transparent;}
        QFrame#ChatShell {background:#fff;border:1px solid #e3e5e9;border-radius:18px;}
        QFrame#Sidebar {background:#f7f8fa;border:0;border-radius:12px;}
        QLabel#Title {font-size:14px;font-weight:600;}
        QLabel#Muted {color:#8b909c;font-size:11px;}
        QLabel#Badge {color:#8b909c;font-size:11px;padding:1px 4px;}
        QPushButton {background:transparent;border:0;border-radius:9px;padding:7px 9px;font-size:12px;}
        QPushButton:hover {background:#f0f2f6;}
        QPushButton:checked {background:#eaf0ff;color:#4168d8;}
        QPushButton:disabled {color:#b9bdc5;}
        QPushButton#Primary {background:#476bec;color:white;border-radius:11px;min-width:40px;}
        QPushButton#Primary:hover {background:#385bd2;}
        QFrame#Composer {background:#f7f8fa;border:1px solid #eceef2;border-radius:14px;}
        QPlainTextEdit {background:transparent;border:0;padding:4px;font-size:13px;selection-background-color:#cfdbff;}
        QTextBrowser {background:white;border:0;font-size:13px;selection-background-color:#dbe5ff;}
        QTreeWidget {background:transparent;border:0;outline:0;font-size:12px;}
        QTreeWidget::item {padding:7px 2px;border-radius:7px;}
        QTreeWidget::item:selected {background:#e7ecf6;color:#344f93;}
        QCheckBox {font-size:11px;color:#8b909c;}
        QComboBox {border:1px solid #e5e8ee;border-radius:7px;padding:4px 8px;background:white;font-size:11px;}
        QMenu {background:white;border:1px solid #e2e5eb;padding:6px;}
        QMenu::item {padding:7px 16px;}
        QMenu::item:selected {background:#eef2fa;}
        QScrollBar:vertical {width:5px;background:transparent;margin:0;}
        QScrollBar::handle:vertical {background:#d8dce3;border-radius:2px;min-height:28px;}
        QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
    ''')
    root=QVBoxLayout(p);root.setContentsMargins(5,5,5,5)
    shell=QFrame();shell.setObjectName('ChatShell');root.addWidget(shell)
    main=QVBoxLayout(shell);main.setContentsMargins(10,10,10,5);main.setSpacing(2)
    header=QFrame();p._drag_header=header;header.installEventFilter(p)
    bar=QHBoxLayout(header);bar.setContentsMargins(2,0,2,4);bar.setSpacing(2)
    def button(text,tip,callback=None):
        b=QPushButton(text);b.setToolTip(tip)
        if callback:b.clicked.connect(callback)
        return b
    p.history_button=button('≡','展开历史对话',p._toggle_sidebar);bar.addWidget(p.history_button)
    p.title=QLabel('咕嘎');p.title.setObjectName('Title');p.title.installEventFilter(p);bar.addWidget(p.title,1)
    p.header_new=button('＋','新对话',p.new_conversation);bar.addWidget(p.header_new)
    p.daily_plan_button=button('每日计划','查看、勾选和修改每天重复的清单',p.pet._show_daily_plan);bar.addWidget(p.daily_plan_button)
    p.console_button=button('管理台','设置驱动大模型、语音输出和长语音',p.pet._open_desktop_console);bar.addWidget(p.console_button)
    p.hide_pet_button=button('隐藏桌宠','隐藏桌宠并保留悬浮入口',lambda:p.pet.set_pet_hidden(True));bar.addWidget(p.hide_pet_button)
    p.pin=button('置顶','置顶');p.pin.setCheckable(True);p.pin.setChecked(True);p.pin.toggled.connect(p._pin);bar.addWidget(p.pin)
    p.more_button=button('···','更多');bar.addWidget(p.more_button)
    p.hide_button=button('—','收起到咕嘎，通话继续',p.hide_to_pet);bar.addWidget(p.hide_button)
    p.close_button=button('×','关闭窗口并结束通话',p.close);bar.addWidget(p.close_button)
    main.addWidget(header)
    p.splitter=QSplitter();main.addWidget(p.splitter,1)
    p.sidebar=QFrame();p.sidebar.setObjectName('Sidebar');p.sidebar.setMinimumWidth(190)
    side=QVBoxLayout(p.sidebar);side.setContentsMargins(10,12,10,8)
    p.new_button=button('＋ 新对话','新对话',p.new_conversation);side.addWidget(p.new_button)
    p.project_button=button('新建项目','新建项目',p.new_project);side.addWidget(p.project_button)
    p.tree=QTreeWidget();p.tree.setHeaderHidden(True);p.tree.setIndentation(12)
    p.tree.setContextMenuPolicy(Qt.CustomContextMenu);p.tree.customContextMenuRequested.connect(p._context_menu)
    p.tree.itemClicked.connect(p._selected);p.tree.itemDoubleClicked.connect(p._rename_item);side.addWidget(p.tree,1)
    p.archive_button=button('已归档对话','已归档对话',p._toggle_archive);side.addWidget(p.archive_button)
    p.splitter.addWidget(p.sidebar);p.sidebar.hide()
    content=QFrame();layout=QVBoxLayout(content);layout.setContentsMargins(10,5,10,0);layout.setSpacing(5)
    p.model_label=QLabel('和咕嘎聊聊，也能让它帮你做事');p.model_label.setObjectName('Muted');layout.addWidget(p.model_label)
    options=QHBoxLayout();options.setSpacing(6)
    p.route_combo=QComboBox()
    for label,value in (('自动分流','auto'),('只问答','chat'),('执行任务','task')):p.route_combo.addItem(label,value)
    p.route_combo.setToolTip('自动：普通问题直接回答，操作电脑才交给任务智能体；也可手动指定')
    p.effort_combo=QComboBox()
    for label,value in (('快速 · 不思考','quick'),('思考 · 轻度','low'),('思考 · 高','high'),('思考 · 极限','max')):p.effort_combo.addItem(label,value)
    p.effort_combo.setToolTip('仅控制普通问答和截图回答；执行任务保留独立策略')
    p.search_combo=QComboBox()
    for label,value in (('联网 · 自动','auto'),('联网 · 强制','always'),('联网 · 关闭','never')):
        p.search_combo.addItem(label,value)
    p.search_combo.setToolTip('自动：只有时效/事实类问题才联网；强制：本轮一定联网；关闭：不联网')
    for combo,key,default in ((p.route_combo,'chat/route','auto'),(p.effort_combo,'chat/effort','quick'),(p.search_combo,'chat/search','auto')):
        combo.setCurrentIndex(max(0,combo.findData(p.pet._config._s.value(key,default))))
        combo.currentIndexChanged.connect(lambda index,c=combo,k=key:p.pet._config._s.setValue(k,c.currentData()))
        options.addWidget(combo)
    options.addStretch();p.read_aloud=QCheckBox('朗读');options.addWidget(p.read_aloud);layout.addLayout(options)
    p.view=QTextBrowser();p.view.setOpenExternalLinks(False);p.view.anchorClicked.connect(p._log_link);layout.addWidget(p.view,1)
    p.status=QLabel('就绪');p.status.setObjectName('Badge');p.status.setMaximumHeight(26);layout.addWidget(p.status)
    p.attachment_bar=QFrame();attach=QHBoxLayout(p.attachment_bar);attach.setContentsMargins(5,2,5,2)
    p.attachment_label=QLabel('已附加截图 · 仅本轮使用');p.attachment_label.setObjectName('Muted');attach.addWidget(p.attachment_label,1)
    attach.addWidget(button('×','移除截图',p._clear_attachment));layout.addWidget(p.attachment_bar);p.attachment_bar.hide()
    composer=QFrame();composer.setObjectName('Composer');edit_layout=QVBoxLayout(composer);edit_layout.setContentsMargins(8,8,8,6);edit_layout.setSpacing(2)
    p.editor=QPlainTextEdit();p.editor.setPlaceholderText('问问咕嘎，或交给我一件事…');p.editor.setFixedHeight(66);p.editor.installEventFilter(p);edit_layout.addWidget(p.editor)
    from ui.voice_record_bar import VoiceRecordBar
    p.voice_record_bar=VoiceRecordBar(p)
    p.voice_record_bar.started.connect(p.start_bubble_short_voice)
    p.voice_record_bar.submitted.connect(lambda:p.finish_bubble_short_voice(False))
    p.voice_record_bar.cancelled.connect(lambda:p.finish_bubble_short_voice(True))
    edit_layout.addWidget(p.voice_record_bar);p.voice_record_bar.hide()
    controls=QHBoxLayout();controls.setSpacing(0)
    p.capture_button=button('截图','截取当前屏幕并直接提问 · Ctrl+Alt+A',lambda:p._screen_tool('ask'));controls.addWidget(p.capture_button)
    p.explain_button=button('划词','开启或关闭屏幕选词工具条');p.explain_button.setCheckable(True)
    p.explain_button.setChecked(p.pet._screen_tools.selection_enabled);p.explain_button.toggled.connect(p.pet._screen_tools.set_selection_enabled);controls.addWidget(p.explain_button)
    p.pet._screen_tools.selection_enabled_changed.connect(p.explain_button.setChecked)
    p.short_voice_button=button('说话','点击录音，在录音条点击 ↑ 发送，×取消；本地识别 + Edge TTS',p.toggle_short_voice);controls.addWidget(p.short_voice_button)
    p.voice_button=button('实时语音','端到端实时语音，使用单独的实时语音额度');p.voice_button.setCheckable(True);p.voice_button.toggled.connect(p.toggle_voice);controls.addWidget(p.voice_button)
    p.log_button=button('执行记录','展开操作摘要和工具记录');p.log_button.setCheckable(True);p.log_button.toggled.connect(p._toggle_logs);controls.addWidget(p.log_button)
    controls.addStretch()
    p.stop_button=button('停止','停止当前任务',p.stop_turn);p.stop_button.setEnabled(False);controls.addWidget(p.stop_button)
    p.send_button=button('↑','发送 · Enter',p.send);p.send_button.setObjectName('Primary');controls.addWidget(p.send_button)
    edit_layout.addLayout(controls);layout.addWidget(composer)
    p.voice_controls=QFrame();voice=QHBoxLayout(p.voice_controls);voice.setContentsMargins(4,0,4,0)
    p.mute_button=button('静音','静音麦克风');p.mute_button.setCheckable(True);p.mute_button.setEnabled(False)
    p.mute_button.toggled.connect(lambda on:p.voice.mute(on) if p.voice_active and p.voice else None);voice.addWidget(p.mute_button)
    p.interrupt_button=button('打断','打断咕嘎当前回答',lambda:p.voice.interrupt() if p.voice_active and p.voice else None);p.interrupt_button.setEnabled(False);voice.addWidget(p.interrupt_button)
    voice.addStretch();layout.addWidget(p.voice_controls);p.voice_controls.hide()
    p.splitter.addWidget(content);p.splitter.setSizes([210,460])
    footer=QHBoxLayout();footer.setContentsMargins(8,0,0,0)
    hint=QLabel('Enter 发送 · Shift+Enter 换行');hint.setObjectName('Muted');footer.addWidget(hint,1);footer.addWidget(QSizeGrip(p));main.addLayout(footer)
    p.voice_settings_button=button('语音设置','语音设置',p.open_voice_settings);p.voice_settings_button.hide()
    menu=QMenu(p);menu.addAction('桌面管理台 · 模型与语音设置',p.pet._open_desktop_console);menu.addAction('软件权限',p.open_permissions);menu.addAction('长语音模型设置',p.open_voice_settings)
    menu.addAction('朗读文字回复',p.read_aloud.toggle);menu.addAction('对话更名',p.rename_active)
    menu.addAction('咕嘎大小…',p.pet._show_size_dialog);menu.addAction('快捷键说明',p._shortcut_help)
    menu.addAction('框选截图（附到输入框）',lambda:p._screen_tool('region_ask'))
    menu.addAction('框选识字（图片文字）',lambda:p._screen_tool('explain'))
    p.more_button.setMenu(menu)
    for b in p.findChildren(QPushButton):b.setAutoDefault(False);b.setDefault(False)
