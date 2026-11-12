"""Zero-LLM command router for latency-sensitive, low-ambiguity voice actions."""
import os
import re
import urllib.parse
import webbrowser
import time
import unicodedata
import subprocess
from pathlib import Path


SITE_ALIASES={
    '百度':'https://www.baidu.com','必应':'https://www.bing.com','谷歌':'https://www.google.com',
    'google':'https://www.google.com','b站':'https://www.bilibili.com','哔哩哔哩':'https://www.bilibili.com',
    'bilibili':'https://www.bilibili.com','github':'https://github.com','知乎':'https://www.zhihu.com',
    'youtube':'https://www.youtube.com','微博':'https://weibo.com',
    # 2026-11-12：原来只有上面 9 个站。其余网站（京东、淘宝…）不在表里，
    # 就会被下面的「打开X」当成软件名交给 applications，报
    # 「未找到名为X的软件」。用户说「打开京东」失败、说「打开京东官网」
    # 反而成功（后者含"官网"被排除词挡下，交给了智能体用 browser 打开）。
    # 这里补齐只有网站形态、基本不会被当本机软件的常见站点。
    '京东':'https://www.jd.com','淘宝':'https://www.taobao.com','天猫':'https://www.tmall.com',
    '豆瓣':'https://www.douban.com','掘金':'https://juejin.cn','csdn':'https://www.csdn.net',
    '12306':'https://www.12306.cn','小红书':'https://www.xiaohongshu.com',
    'mdn':'https://developer.mozilla.org','抖音':'https://www.douyin.com','快手':'https://www.kuaishou.com',
    '贴吧':'https://tieba.baidu.com','百度贴吧':'https://tieba.baidu.com','百科':'https://baike.baidu.com',
    '腾讯视频':'https://v.qq.com','爱奇艺':'https://www.iqiyi.com','优酷':'https://www.youku.com',
    'pixiv':'https://www.pixiv.net','twitter':'https://x.com','推特':'https://x.com',
    'stackoverflow':'https://stackoverflow.com','pypi':'https://pypi.org','菜鸟教程':'https://www.runoob.com',
}
APP_ALIASES={
    '记事本':'notepad.exe','计算器':'calc.exe','画图':'mspaint.exe','资源管理器':'explorer.exe',
    '设置':'ms-settings:','终端':'wt.exe','命令提示符':'cmd.exe','任务管理器':'taskmgr.exe',
}
BROWSER_NAMES=('浏览器','默认浏览器','网页浏览器')
STOP_PHRASES=('停止对话','结束对话','停止通话','结束通话','停止语音','结束语音','别说了','不用聊了',
    '闭嘴','闭嘴吧','安静','安静点','别说话','别出声','不要说话','不用说了',
    '退下','退下了','退下吧','你退下吧','你可以退下了','可以先退下了','去休息吧','你可以去休息了',
    '不用陪我了','先聊到这里','今天就聊到这里')


def normalize(text):
    text=unicodedata.normalize('NFKC',str(text or '')).strip().strip('。！？!?，, ')
    return re.sub(r'\s+',' ',text)


def command_text(text):
    value=normalize(text)
    # ASR keeps conversational fillers. Remove only a standalone leading filler,
    # never a negation or a clause describing what somebody else said.
    value=re.sub(r'^(?:(?:嗯+|呃+|额+|哦+|啊+|好的|好吧|好|行)[，,\s]+)+','',value)
    # An explicit correction cancels the first launch and selects the second.
    correction=re.fullmatch(r'(?:先)?(?:不|别|不用|不要)(?:打开|启动)[^，,；;]{1,30}[，,；;]\s*((?:打开|启动).+)',value)
    if correction:value=correction.group(1)
    value=re.sub(r'^(?:(?:不[，,\s]*){1,4}[，,\s]*|不是[，,\s]*)?(?:我的意思是|我是说|我说的是)(?:让你|要你)?[，,\s]*','',value)
    value=re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])','',value)
    value=re.sub(r'^(?:小?咕嘎)[，,\s]*','',value)
    value=re.sub(r'^(?:(?:你能不能|你能|你可以|你帮我|你给我|请你?|麻烦你?|能不能|可以请你|可以|帮我|给我|替我)[，,\s]*)+','',value)
    value=re.sub(r'^(?:(?:你)?现在)?(?:重新|再)?(?=打开|启动)','',value)
    value=re.sub(r'^((?:打开|启动|访问|前往)(?:一下)?)[，,:：\s]+',r'\1',value)
    return value


def command_key(command):
    if command.get('action') in ('browser_step','desktop_step'):
        return ('desktop_step',command.get('step_action') or command.get('browser_action'),str(command.get('target','')),str(command.get('text','')),command.get('index'))
    target=str(command.get('target','')).casefold()
    if target in BROWSER_NAMES:target='浏览器'
    return (command.get('action'),command.get('kind'),target)


def _open_app(target):
    target=target.strip().strip('“”"\' ')
    if target in BROWSER_NAMES:
        from core.browser_launch import default_executable,launch_arguments
        browser=default_executable() or _browser_executable()
        if browser:
            # A real browser window, independent of third-party HTTP handlers
            # that may accept a URL without showing it to the user.
            subprocess.Popen(launch_arguments(browser,'about:blank'))
            return dict(kind='app',target='浏览器',launch_requested=True,verified_running=False,
                message='已请求浏览器打开新窗口')
        return _open_url('https://www.baidu.com')
    alias=APP_ALIASES.get(target)
    if alias:
        os.startfile(alias)
        return dict(kind='app',target=target,message='Windows 已接受启动请求')
    from core.authorized_apps import AuthorizedApps
    result=AuthorizedApps().open(target)
    if result.get('selection_required'):
        raise RuntimeError('找到多个已运行窗口，需要交给内部智能体按当前任务选择；尚未切换窗口')
    return dict(result,kind='app',target=target)


def _browser_executable():
    if os.name!='nt':return None
    # Check fixed install locations only; never walk the user's folders.
    for root in (os.getenv('PROGRAMFILES(X86)'),os.getenv('PROGRAMFILES'),os.getenv('LOCALAPPDATA')):
        if not root:continue
        for relative in ('Microsoft/Edge/Application/msedge.exe','Google/Chrome/Application/chrome.exe'):
            path=Path(root)/relative
            if path.is_file():return path
    return None


def _open_url(url):
    from core.browser_launch import launch
    return launch(url)


_TAIL_ONLY=re.compile(r'^(?:(?:一下|吧|好吗|可以吗|行吗|谢谢|呢|啊|哦|呀|然后|接着|再|请)(?:呢|吧|啊|哦|呀)?)?[。！？!?，,\s]*$')


def _needs_agent(remaining):
    """Whether a compound sentence's second clause is real work.

    The fast path may only claim a sentence it can finish. A tail that still
    names an action ("在输入框里输入X", "找到小军") belongs to the agent loop.
    """
    tail=str(remaining or '').strip().strip('。！？!?，, ')
    return bool(tail) and not _TAIL_ONLY.match(tail)


def _is_known_local_app(target):
    """目标是否确实是一个已知的本地软件。

    只读 runtime/chat/authorized_apps.json（几行 JSON），不扫开始菜单或注册表，
    所以可以在路由阶段安全调用。
    """
    value=str(target or '').strip()
    if not value:return False
    if value in APP_ALIASES:return True
    try:
        from core.authorized_apps import SCRIPT_ALIASES
        if value in SCRIPT_ALIASES:return True
    except Exception:
        pass
    try:
        from core.authorized_apps import AuthorizedApps
        entries=AuthorizedApps().entries()
    except Exception:
        return False
    folded=value.casefold()
    return any(str(key).casefold()==folded for key in entries)


# 像网站名：2-8 个中文/字母/数字字符，允许间隔号与连字符。
SITE_LIKE=re.compile(r'^[\u4e00-\u9fffA-Za-z0-9·\-]{2,8}$')


def _unresolved_site_candidate(target):
    """看着像网站名、但不是任何已知本地软件的短词。

    2026-11-12：fast path 只该拦它确定能完成的事。原来「打开京东」会被直接判成
    open_app，applications 找不到就报「未找到名为京东的软件」收场——而智能体本来
    能用 browser/web_search 打开 jd.com。用户说「打开京东官网」反而成功，正是因为
    含"官网"被排除词挡下、交给了智能体。
    这里把这类词交还给智能体，让 fast path 不再抢它做不到的活。
    """
    value=str(target or '').strip()
    if not value or len(value)>8:return False
    if not SITE_LIKE.match(value):return False
    if value in SITE_ALIASES or value.casefold() in SITE_ALIASES:return False
    if _is_known_local_app(value):return False
    return True


def match(text):
    """Parse a high-confidence local action without performing it."""
    value=command_text(text)
    if re.fullmatch(r'(?:查看)?\s*DSH\s*(?:进度|任务状态|现在在干什么|正在做什么)[。？！?!]*',str(text).strip(),re.I):
        return dict(matched=True,action='dsh_link',arguments=dict(action='status'),source_text=str(text),silent=False)
    follow=re.fullmatch(r'关注\s*DSH\s*会话\s+([a-zA-Z0-9_-]+)',str(text).strip(),re.I)
    if follow:return dict(matched=True,action='dsh_link',arguments=dict(action='focus',session_id=follow[1]),source_text=str(text),silent=False)
    from core.daily_plan import parse_command
    daily = parse_command(text)
    if daily:
        return dict(matched=True,action='daily_plan',arguments=daily,source_text=str(text),silent=False)
    prefix=dict(source_text=normalize(text))
    from core.desktop_interaction import _input_method_question
    if _input_method_question(value):
        # This asks about the input method; it is not text to enter in a field.
        return dict(matched=False,action='none',silent=False)
    from core.browser_interaction import parse_step
    browser_step=parse_step(value,str(text or ''))
    from core.desktop_interaction import parse_desktop_step
    desktop_step=parse_desktop_step(value,str(text or ''))
    # Numbered targets and added keyboard phrases take precedence over the
    # legacy parser's generic "click <name>" result.
    if desktop_step and desktop_step.get('step_action') in ('list_targets','click_index','unsupported'):
        return desktop_step
    if browser_step:
        return dict(desktop_step,action='browser_step',kind='browser') if desktop_step else browser_step
    if desktop_step:return desktop_step
    # A search/open prefix must not swallow the rest of a multi-step job.
    if re.search(r'(?:[，,。；;]|然后|接着|并且|并)(?:再|请|帮我|将|把)?[^，,。；;]{0,20}(?:写入|写成|保存|发送|创建|整理成|下载|安装)',value):
        return dict(matched=False,action='none',silent=False)
    summon=re.fullmatch(r'(?:召唤|叫出|叫来)\s*(?:咕嘎)?\s*(002|003)',value)
    if summon:return dict(prefix,matched=True,action='multiagent',kind='summon',target=summon.group(1),silent=True)
    named_summon=re.fullmatch(r'(?:召唤|叫出|叫来)\s*(大肥鱼|dora|朵拉)',value,re.I)
    if named_summon:
        target='002' if named_summon.group(1).lower() in ('大肥鱼') else '003'
        return dict(prefix,matched=True,action='multiagent',kind='summon',target=target,silent=True)
    if value in ('召唤两个咕嘎','叫出两个咕嘎','召唤002和003','召唤两个同伴'):
        return dict(prefix,matched=True,action='multiagent',kind='summon_pair',target='002,003',silent=True)
    team_discuss=re.fullmatch(r'让\s*(001|002|003)\s*(?:和|与|、)\s*(001|002|003)\s*(?:一起)?(?:讨论|聊聊|商量)(?:一下)?\s*(.*)',value)
    if team_discuss:
        topic=team_discuss.group(3).strip(' ：:，,。')
        if topic:return dict(prefix,matched=True,action='multiagent',kind='discuss',target=team_discuss.group(1)+','+team_discuss.group(2),topic=topic,silent=True)
        return dict(prefix,matched=True,action='multiagent',kind='discuss_ask',target=team_discuss.group(1)+','+team_discuss.group(2),silent=True)
    singing_request=re.sub(r'^(?:你能不能|你能|能不能|能|我想听你|我想听|想听你|想听)','',value)
    singing_request=re.sub(r'(?:给我听|给我|听听|听|吧|好吗|可以吗)[？?。！!]*$','',singing_request)
    if singing_request in ('唱歌','唱首歌','唱一首歌','来首歌','来一首歌','唱一下歌'):
        return dict(prefix,matched=True,action='performance',kind='sing',target='晴天',silent=True)
    if value in ('暂停唱歌','暂停音乐','暂停一下','继续唱歌','继续音乐'):
        return dict(prefix,matched=True,action='performance',kind='music_pause' if '暂停' in value else 'music_resume',target='',silent=True)
    search=re.fullmatch(r'(?:打开|用|在)(?:默认|豆包)?浏览器[，,\s]*(?:帮我|然后|并|再|输入|搜索|查询|搜一下|查一下|进行搜索|进行查询|一下|[，,\s])+(.+)',value)
    if search and any(word in value for word in ('搜索','查询','搜一下','查一下')):
        query=re.sub(r'(?:[，,]?并?汇报(?:一下)?结果|[，,]?告诉我结果|[，,]?进行搜索查询|[，,]?进行搜索)$','',search.group(1)).strip('，, 。')
        if query:return dict(prefix,matched=True,action='web_search',silent=True,priority='fast',kind='url',target='https://www.baidu.com/s?wd='+urllib.parse.quote(query),report_query=query if any(w in value for w in ('汇报','告诉我结果')) else '')
    gestures={'戴墨镜':'sunglasses','戴上墨镜':'sunglasses','摘墨镜':'remove_sunglasses','摘下墨镜':'remove_sunglasses',
        '跳舞':'dance','摇摆':'dance','晃动身体':'dance','停止跳舞':'stop_dance','停止摇摆':'stop_dance','暗中观察':'sneak_peek',
        '打盹':'idle_doze','困倦打盹':'idle_doze','比心':'idle_heart','双手比心':'idle_heart',
        '比个心':'idle_heart','比一下心':'idle_heart','给你比心':'idle_heart','比心啦':'idle_heart',
        '嘟嘴':'idle_pout','鼓脸':'idle_pout','生闷气':'idle_pout','抱臂嘟嘴':'idle_pout',
        '大笑':'idle_laugh','仰头笑':'idle_laugh','哈哈笑':'idle_laugh','开怀大笑':'idle_laugh'}
    if value in gestures:return dict(prefix,matched=True,action='performance',kind='gesture',target=gestures[value],silent=True)
    if value in ('停止唱歌','停歌','别唱了','停止音乐'):
        return dict(prefix,matched=True,action='performance',kind='music_stop',target='',silent=True)
    singing=re.fullmatch(r'(?:唱(?:一首|首|一下)?|来一首|播放翻唱)\s*《?(.+?)》?',value)
    if singing and len(singing.group(1))<80:
        return dict(prefix,matched=True,action='performance',kind='sing',target=singing.group(1).strip('《》'),silent=True)
    if value in STOP_PHRASES or re.fullmatch(r'(?:先)?(?:退下|去休息|休息去吧|走吧|走了)(?:了|吧|啦|啊)?',value):
        return dict(prefix,matched=True,action='stop_call',silent=True,priority='critical',
                    message='结束实时语音通话')
    share=re.fullmatch(r'(?:开始|打开)?(?:共享|分享)(?:一下)?(?:我的|整个|当前)?屏幕(?:[，,]?每秒([2-5两三四五])帧)?(?:吧|一下)?',value)
    if share:
        raw=share.group(1);fps={'两':2,'二':2,'三':3,'四':4,'五':5}[raw] if raw in ('两','二','三','四','五') else int(raw) if raw else 3
        return dict(prefix,matched=True,action='screen_share_start',kind='screen_share',fps=fps,silent=False,priority='fast')
    if re.fullmatch(r'(?:停止|关闭|结束)(?:一下)?(?:共享|分享)(?:屏幕)?(?:吧|一下)?',value):
        return dict(prefix,matched=True,action='screen_share_stop',kind='screen_share',silent=False,priority='fast')
    close_page=re.fullmatch(r'(?:(?:关闭|关掉|关上)(?:一下)?(?:这个|当前|现在|正在看|活动|前台)?(?:的)?(?:浏览器)?(?:页面|网页|标签页|标签)|(?:把)?(?:这个|当前|现在|活动|前台)?(?:的)?(?:浏览器)?(?:页面|网页|标签页|标签)(?:给)?(?:关掉|关闭|关上))(?:吧|一下|好吗|可以吗)?',value)
    if close_page:
        return dict(prefix,matched=True,action='close_page',kind='close_page',target='current_tab',silent=True,priority='fast')
    close=re.fullmatch(r'(?:把)?(默认浏览器|网页浏览器|浏览器|豆包浏览器|edge|chrome|firefox)(?:窗口)?(?:关掉|关闭|退出)(?:吧|一下)?|(?:关闭|关掉|退出)(?:一下)?\s*(默认浏览器|网页浏览器|浏览器|豆包浏览器|edge|chrome|firefox)(?:窗口)?(?:吧|一下)?',value,re.I)
    if close:
        return dict(prefix,matched=True,action='close_browser',kind='close_browser',target=(close.group(1) or close.group(2)).lower(),silent=True,priority='fast')
    window_kind=_window_control_intent(value)
    if window_kind:
        return dict(prefix,matched=True,action='window_control',kind=window_kind['kind'],
                    target=('浏览器' if window_kind['browser'] else window_kind['kind']),silent=True,priority='fast')
    # "打开微信，找到小军" is one task with two steps. The fast path may only
    # claim what it can finish: a real second clause hands the whole sentence to
    # the agent loop instead of opening the app and reporting the rest as undone.
    compound=re.match(r'^(?:帮我|请)?(?:把|将)?(?:打开|启动)(?:一下)?\s*([^，,。；;？?]{1,20})[，,]\s*(.+)$',value)
    if compound:
        target=compound.group(1).strip().strip('“”"\' ')
        if target in BROWSER_NAMES:target='浏览器'
        if (target and len(target)<=20 and target not in SITE_ALIASES
                and not re.search(r'然后|接着|搜索|网页|网站|http|是什么|为什么|怎么',target)):
            remaining=str(compound.group(2)).strip()[:200]
            if _needs_agent(remaining):
                return dict(prefix,matched=False,action='none',silent=False)
            return dict(prefix,matched=True,action='open_app',silent=True,priority='fast',kind='app',
                        target=target)
    inverted=re.fullmatch(r'(?:把|将)?\s*(.+?)\s*(?:给)?(?:打开|启动|开一下)(?:一下|吧)?',value)
    if inverted:
        target=re.sub(r'^(?:这个|那个|我的)','',inverted.group(1).strip()).strip('“”"\' ')
        if target in BROWSER_NAMES:target='浏览器'
        if (target and len(target)<=20 and target not in SITE_ALIASES
                and not re.search(r'[，,。;；：:]|[?？]|然后|接着|找到|搜索|点击|写入|保存|关闭',target)):
            return dict(prefix,matched=True,action='open_app',silent=True,priority='fast',kind='app',target=target)
    match=re.fullmatch(r'(?:(?:在|用)(?:默认)?浏览器)?(?:打开|启动|访问|前往)(?:一下)?(?:一个)?(?:网页|网站|页面)?\s*(.+)',value)
    if match:
        target=match.group(1).strip().strip('“”"\' ')
        candidate=re.sub(r'(?:一下|好吗|可以吗|吗|吧)$','',target).strip()
        site_candidate=re.sub(r'(?:这个|那个|的)?(?:网站|官网|网页|主页)$','',candidate).strip()
        if site_candidate.casefold() in SITE_ALIASES:candidate=site_candidate.casefold()
        if candidate in SITE_ALIASES or candidate in APP_ALIASES or candidate in BROWSER_NAMES:target=candidate
        elif re.fullmatch(r'(?:https?://)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?(?:/[^\s\u3400-\u9fff]*)?',candidate):target=candidate
        target=target.casefold() if target.casefold() in SITE_ALIASES else target
        if target in SITE_ALIASES:
            url=SITE_ALIASES[target]
        elif re.fullmatch(r'[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?::\d+)?(?:/[^\s\u3400-\u9fff]*)?',target):
            url='https://'+target
        elif re.fullmatch(r'https?://[A-Za-z0-9.-]+(?::\d+)?(?:/[^\s\u3400-\u9fff]*)?',target):
            url=target
        else:
            url=None
        if url:
            return dict(prefix,matched=True,action='open_url',silent=True,priority='fast',kind='url',target=url)
        if target in BROWSER_NAMES or target in APP_ALIASES:
            return dict(prefix,matched=True,action='open_app',silent=True,priority='fast',kind='app',target=target)
    match=re.fullmatch(r'(?:帮我|请)?(?:搜索|搜一下|查一下|查查)\s*(.+)',value)
    if match:
        query=match.group(1).strip()
        if query and len(query)<=300:
            url='https://www.baidu.com/s?wd='+urllib.parse.quote(query)
            return dict(prefix,matched=True,action='web_search',silent=True,priority='fast',kind='url',target=url)
    match=re.fullmatch(r'(?:打开|启动)(?:一下)?\s*(.+)',value)
    if match:
        target=re.sub(r'(?:一下|好吗|可以吗|吗|吧)$','',match.group(1).strip()).strip()
        # "打开我的b站"——口语里的"我的"不是软件名的一部分，先剥掉再按站点认
        bare=re.sub(r'^(?:我的|我那个|那个|咱的)', '', target).strip()
        if bare and bare.casefold() in SITE_ALIASES:
            return dict(prefix,matched=True,action='open_url',silent=True,priority='fast',
                        kind='url',target=SITE_ALIASES[bare.casefold()])
        if target in BROWSER_NAMES:
            return dict(prefix,matched=True,action='open_app',silent=True,priority='fast',kind='app',target='浏览器')
        if target and len(target)<=20 and not re.search(
                r'然后|接着|并且|再帮|并|不要|如果|输入|打字|填写|写入|写一|写个|撰写|保存|发送|搜索|查找|找到|编辑|点击|登录|录入|修复|安装|下载|网站|网页|网址|官网|是什么|什么意思|为什么|怎么|如何|会怎样|会怎么样'
                r'|在|后|以后|之后|里面|里|中|新建|创建|建一个|对话|会话|消息|发给|说|问|告诉|帮我|请|一下'
                r'|[，,。;；：:]|[?？]',target):
            # Resolve installed apps on the existing launch worker, never scan
            # the Start Menu on Qt/voice threads or require a duplicate grant.
            if _unresolved_site_candidate(target):
                # 不是已知本地软件、又长得像网站名：不要抢着当软件启动，
                # 交给智能体（browser + web_search 能处理任意网站）。
                return dict(matched=False,action='none',silent=False)
            return dict(prefix,matched=True,action='open_app',silent=True,priority='fast',kind='app',target=target)
    return dict(matched=False,action='none',silent=False)


WINDOW_CONTROLS=(
    ('maximize',('最大化','窗口最大化','最大化窗口','这个窗口最大化','当前窗口最大化','放到最大','铺满屏幕',
                 '放大窗口','浏览器最大化','最大化浏览器')),
    ('minimize',('最小化','窗口最小化','最小化窗口','这个窗口最小化','当前窗口最小化','收到任务栏','缩到任务栏',
                 '最小化到任务栏','浏览器最小化','最小化浏览器')),
    ('restore',('还原窗口','窗口还原','恢复窗口','还原大小','恢复窗口大小','恢复正常大小','取消最大化',
                '恢复原来大小','恢复原大小','浏览器还原')),
    ('close',('关闭窗口','关掉窗口','关这个窗口','这个窗口关掉','关掉当前窗口','关闭当前窗口','关掉这个',
              '关闭这个','关掉这个窗口')),
)
BROWSER_PROCESSES=('msedge.exe','chrome.exe','firefox.exe','doubao.exe','brave.exe')


def _window_control_intent(value):
    """Recognise window-level verbs that need no accessibility tree at all."""
    text=re.sub(r'^(?:请|帮我|麻烦|把|将|给我|你)+','',value)
    text=re.sub(r'(?:一下|吧|好吗|可以吗|谢谢)$','',text).strip()
    text=text.replace('的','')
    if len(text)>10:return None
    for kind,phrases in WINDOW_CONTROLS:
        if text in phrases:return dict(kind=kind,browser=('浏览器' in text))
    return None


_WINDOW_MEMORY=dict(hwnd=0,at=0.,title='',kind='')


def _window_target(win32gui,win32process):
    """Foreground window when it is a real app; otherwise the window we just touched."""
    now=time.monotonic()
    hwnd=int(win32gui.GetForegroundWindow() or 0)
    if hwnd:
        try:desktop_class=win32gui.GetClassName(hwnd)
        except Exception:desktop_class=''
        if (hwnd==int(win32gui.GetDesktopWindow()) or desktop_class in
                ('Progman','WorkerW','Shell_TrayWnd','#32769','SysListView32')):
            return 0,now,'desktop'
    if hwnd and win32process.GetWindowThreadProcessId(hwnd)[1]==os.getpid():
        last=_WINDOW_MEMORY
        if last['hwnd'] and now-last['at']<10 and win32gui.IsWindow(last['hwnd']):
            return int(last['hwnd']),now,'memory'
        return 0,now,'own'
    return hwnd,now,'foreground'


def _window_state(hwnd,win32con):
    import win32gui
    show=int(win32gui.GetWindowPlacement(hwnd)[1])
    return show==win32con.SW_SHOWMAXIMIZED,show==win32con.SW_SHOWMINIMIZED


def _window_control(kind,browser_only=False):
    """Window state is a system property, never a button to hunt for in UIA."""
    import win32con
    import win32gui
    import win32process
    from core.desktop_access import DesktopAccess
    if not DesktopAccess().enabled:
        return dict(ok=False,code='access_disabled',action_sent=False,error='完整访问未开启；尚未操作窗口')
    hwnd,now,source=_window_target(win32gui,win32process)
    if source=='desktop':
        return dict(ok=False,code='system_window',action_sent=False,error='当前对着的是桌面或任务栏，没有可操作的窗口')
    if source=='own':
        return dict(ok=False,code='own_window',action_sent=False,error='当前前台是咕嘎自己，先切到要操作的窗口再说一次')
    if not hwnd or not win32gui.IsWindow(hwnd):
        return dict(ok=False,code='target_missing',action_sent=False,error='没有取到可操作的窗口；尚未操作')
    if kind=='restore':
        last=_WINDOW_MEMORY
        if now-last['at']<10 and last['hwnd'] and int(last['hwnd'])!=hwnd and win32gui.IsWindow(last['hwnd']):
            # A window minimised a moment ago is what "还原" means, even after focus moved on.
            if _window_state(int(last['hwnd']),win32con)[1]:hwnd=int(last['hwnd'])
    pid=win32process.GetWindowThreadProcessId(hwnd)[1]
    if browser_only:
        try:
            import psutil
            process=psutil.Process(pid).name().lower()
        except Exception:process=''
        if process not in BROWSER_PROCESSES:
            return dict(ok=False,code='not_browser',action_sent=False,error='当前前台不是浏览器，先切到浏览器窗口再说一次')
    title=win32gui.GetWindowText(hwnd)[:40] or '当前窗口'
    _WINDOW_MEMORY.update(hwnd=hwnd,at=now,title=title,kind=kind)
    zoomed,iconic=_window_state(hwnd,win32con)
    if kind=='maximize' and zoomed:
        return dict(ok=True,verified=True,action_sent=False,code='already_maximized',evidence='is_zoomed',message='「'+title+'」已经是最大化了')
    if kind=='minimize' and iconic:
        return dict(ok=True,verified=True,action_sent=False,code='already_minimized',evidence='is_iconic',message='「'+title+'」已经最小化了')
    if kind=='restore' and not zoomed and not iconic:
        return dict(ok=True,verified=True,action_sent=False,code='already_normal',evidence='is_normal',message='「'+title+'」本来就是正常大小')
    try:
        if kind=='maximize':win32gui.ShowWindow(hwnd,win32con.SW_MAXIMIZE)
        elif kind=='minimize':win32gui.ShowWindow(hwnd,win32con.SW_MINIMIZE)
        elif kind=='restore':win32gui.ShowWindow(hwnd,win32con.SW_RESTORE)
        elif kind=='close':win32gui.PostMessage(hwnd,win32con.WM_CLOSE,0,0)
        else:raise ValueError('未知窗口操作')
    except ValueError:raise
    except Exception as exc:
        return dict(ok=False,code='window_action_failed',action_sent=False,error='窗口操作没有发出去：'+type(exc).__name__)
    deadline=time.monotonic()+1.5;verified=False
    while time.monotonic()<deadline:
        zoomed,iconic=_window_state(hwnd,win32con)
        if kind=='maximize' and zoomed:verified=True;break
        if kind=='minimize' and iconic:verified=True;break
        if kind=='restore' and not zoomed and not iconic:verified=True;break
        if kind=='close' and not win32gui.IsWindow(hwnd):verified=True;break
        time.sleep(.03)
    if kind=='close' and not verified:verified=not win32gui.IsWindow(hwnd)
    evidence=dict(maximize='is_zoomed',minimize='is_iconic',restore='is_normal',close='window_gone')[kind]
    if verified:
        return dict(ok=True,verified=True,action_sent=True,code='window_'+kind,evidence=evidence,
            message='「'+title+'」'+(dict(maximize='已最大化',minimize='已最小化',restore='已还原',close='已关闭')[kind]))
    if kind=='close':
        return dict(ok=False,verified=False,action_sent=True,code='close_unverified',evidence=evidence,
            error='已发出关闭请求，但窗口还在；可能有未保存内容或确认提示，请自己看一眼，不会重复关')
    return dict(ok=False,verified=False,action_sent=True,code='window_'+kind+'_unverified',evidence=evidence,
        error='已发出'+dict(maximize='最大化',minimize='最小化',restore='还原')[kind]+'请求，但状态没变；可能被应用自己锁定了窗口大小')


def _close_browser(target):
    import win32gui, win32con
    from core.desktop_access import DesktopAccess
    if not DesktopAccess().enabled:return dict(ok=False,code='access_disabled',action_sent=False,error='完整访问未开启；尚未发送关闭操作')
    from core.window_inventory import list_windows
    from core.browser_launch import default_executable
    names={'edge':'msedge.exe','chrome':'chrome.exe','firefox':'firefox.exe','豆包浏览器':'doubao.exe'}
    windows=list_windows('浏览器',200)
    wanted=names.get(target)
    if wanted:
        windows=[w for w in windows if w.get('process_name','').lower()==wanted]
    if not windows:return dict(ok=False,code='target_missing',error='没有检测到正在运行的目标浏览器；尚未发送关闭操作')
    selected=next((w for w in windows if w.get('foreground')),None)
    if not selected and not wanted:
        default=default_executable()
        exe=Path(str(default)).name.lower() if default else ''
        selected=next((w for w in windows if w.get('process_name','').lower()==exe),None)
    if not selected:
        selected=windows[0] if wanted or len(windows)==1 else None
    if not selected:return dict(ok=False,error='检测到多个浏览器，请指定要关闭哪一个')
    hwnd=selected['hwnd']
    win32gui.PostMessage(hwnd,win32con.WM_CLOSE,0,0)
    for _ in range(15):
        time.sleep(.1)
        if not win32gui.IsWindow(hwnd):return dict(ok=True,verified=True,action_sent=True,evidence='target_window_gone',message='浏览器窗口已关闭')
    return dict(ok=False,code='close_unverified',action_sent=True,error='已请求关闭浏览器，但窗口仍存在，请检查是否有未保存内容或确认提示；本次不会自动重复关闭')


def execute(result):
    started=time.perf_counter()
    try:
        answer=_execute(result)
    except Exception as exc:
        answer=dict(ok=False,error=str(exc),code=getattr(exc,'code',None))
    answer['dispatch_ms']=round((time.perf_counter()-started)*1000,2)
    if isinstance(result,dict) and isinstance(result.get('recognized_at'),(int,float)):
        answer['transcript_to_result_ms']=round((time.monotonic()-result['recognized_at'])*1000,2)
    return answer


def _execute(result):
    values=dict(result or {})
    if not values.get('matched'):return dict(ok=False,error='没有匹配的快速命令')
    if values.get('action')=='stop_call':return dict(ok=True,message='结束实时语音通话')
    if values.get('action')=='close_page':
        from core.browser_actions import bounded_close
        return bounded_close()
    if values.get('action') in ('screen_share_start','screen_share_stop'):
        return dict(ok=False,code='host_ui_required',error='共享屏幕需要由咕嘎界面控制')
    if values.get('action')=='close_browser':return _close_browser(values['target'])
    if values.get('action')=='window_control':
        return _window_control(values['kind'],browser_only=str(values.get('target',''))=='浏览器')
    if values.get('kind')=='url':
        return dict(ok=True,**_open_url(values['target']))
    if values.get('kind')=='app':
        return dict(ok=True,**_open_app(values['target']))
    return dict(ok=False,error='未知快速动作')


def route(text):
    """Compatibility helper for scripts and tests."""
    parsed=match(text)
    return dict(parsed,execution=execute(parsed)) if parsed.get('matched') else parsed
