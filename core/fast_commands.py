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
}
APP_ALIASES={
    '记事本':'notepad.exe','计算器':'calc.exe','画图':'mspaint.exe','资源管理器':'explorer.exe',
    '设置':'ms-settings:','终端':'wt.exe','命令提示符':'cmd.exe','任务管理器':'taskmgr.exe',
}
BROWSER_NAMES=('浏览器','默认浏览器','网页浏览器')
STOP_PHRASES=('停止对话','结束对话','停止通话','结束通话','停止语音','结束语音','别说了','不用聊了',
    '闭嘴','闭嘴吧','安静','安静点','别说话','别出声','不要说话','不用说了')


def normalize(text):
    text=unicodedata.normalize('NFKC',str(text or '')).strip().strip('。！？!?，, ')
    return re.sub(r'\s+',' ',text)


def command_text(text):
    value=normalize(text)
    value=re.sub(r'^(?:(?:不[，,\s]*){1,4}[，,\s]*|不是[，,\s]*)?(?:我的意思是|我是说|我说的是)(?:让你|要你)?[，,\s]*','',value)
    value=re.sub(r'(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])','',value)
    value=re.sub(r'^(?:小?咕嘎)[，,\s]*','',value)
    value=re.sub(r'^(?:(?:你能不能|你能|你可以|你帮我|你给我|请你?|麻烦你?|能不能|可以请你|可以|帮我|给我|替我)[，,\s]*)+','',value)
    value=re.sub(r'^((?:打开|启动|访问|前往)(?:一下)?)[，,:：\s]+',r'\1',value)
    return value


def command_key(command):
    if command.get('action')=='browser_step':
        return ('browser_step',command.get('browser_action'),str(command.get('target','')),str(command.get('text','')))
    target=str(command.get('target','')).casefold()
    if target in BROWSER_NAMES:target='浏览器'
    return (command.get('action'),command.get('kind'),target)


def _open_app(target):
    target=target.strip().strip('“”"\' ')
    if target in BROWSER_NAMES:
        from core.browser_launch import default_executable
        browser=default_executable() or _browser_executable()
        if browser:
            # A real browser window, independent of third-party HTTP handlers
            # that may accept a URL without showing it to the user.
            # 打开"新建标签页"而不是 about:blank：后者是一张白纸，用户还得点后退才能回到起始页；
            # 不带任何参数启动时 Edge 会走"已有实例"分支直接退出，所以必须给一个能落地的 URL。
            subprocess.Popen([str(browser), 'about:newtab'])
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


def match(text):
    """Parse a high-confidence local action without performing it."""
    value=command_text(text)
    prefix=dict(source_text=normalize(text))
    from core.browser_interaction import parse_step
    browser_step=parse_step(value,str(text or ''))
    if browser_step:return browser_step
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
        '比个心':'idle_heart','比一下心':'idle_heart','给你比心':'idle_heart','比心啦':'idle_heart'}
    if value in gestures:return dict(prefix,matched=True,action='performance',kind='gesture',target=gestures[value],silent=True)
    if value in ('停止唱歌','停歌','别唱了','停止音乐'):
        return dict(prefix,matched=True,action='performance',kind='music_stop',target='',silent=True)
    singing=re.fullmatch(r'(?:唱(?:一首|首|一下)?|来一首|播放翻唱)\s*《?(.+?)》?',value)
    if singing and len(singing.group(1))<80:
        return dict(prefix,matched=True,action='performance',kind='sing',target=singing.group(1).strip('《》'),silent=True)
    if value in STOP_PHRASES:
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
    close=re.fullmatch(r'(?:把)?(默认浏览器|网页浏览器|浏览器|豆包浏览器|edge|chrome|firefox)(?:关掉|关闭|退出)(?:吧|一下)?|(?:关闭|关掉|退出)(?:一下)?\s*(默认浏览器|网页浏览器|浏览器|豆包浏览器|edge|chrome|firefox)(?:吧|一下)?',value,re.I)
    if close:
        return dict(prefix,matched=True,action='close_browser',kind='close_browser',target=(close.group(1) or close.group(2)).lower(),silent=True,priority='fast')
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
        if target in BROWSER_NAMES:
            return dict(prefix,matched=True,action='open_app',silent=True,priority='fast',kind='app',target='浏览器')
        if target and len(target)<=80 and not re.search(
                r'然后|接着|并且|再帮|并|不要|如果|输入|打字|填写|写入|写一|写个|撰写|保存|发送|搜索|查找|找到|编辑|点击|登录|录入|修复|安装|下载|网站|网页|网址|官网|是什么|什么意思|为什么|怎么|如何|会怎样|会怎么样|[，,。;；：:]|[?？]',target):
            # Resolve installed apps on the existing launch worker, never scan
            # the Start Menu on Qt/voice threads or require a duplicate grant.
            return dict(prefix,matched=True,action='open_app',silent=True,priority='fast',kind='app',target=target)
    return dict(matched=False,action='none',silent=False)


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
    if values.get('kind')=='url':
        return dict(ok=True,**_open_url(values['target']))
    if values.get('kind')=='app':
        return dict(ok=True,**_open_app(values['target']))
    return dict(ok=False,error='未知快速动作')


def route(text):
    """Compatibility helper for scripts and tests."""
    parsed=match(text)
    return dict(parsed,execution=execute(parsed)) if parsed.get('matched') else parsed
