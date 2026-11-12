"""Browser capability for the reasoning agent, with actual post-action observation."""
from urllib.parse import urlsplit
import time


def execute(computer,args,cancel):
    if cancel.is_set():return dict(ok=False,error='任务已停止，未发送浏览器操作',action_sent=False)
    from core.browser_session import ACTIONS,BrowserSession
    if args.get('action') in ACTIONS:
        if not computer.access.enabled:return dict(ok=False,error='完整访问未开启',action_sent=False)
        session=getattr(computer,'browser_session',None)
        if session is None:session=computer.browser_session=BrowserSession()
        return session.execute(args,cancel)
    if args.get('action')=='close_tab':
        from core.browser_actions import bounded_close
        return bounded_close()
    if args.get('action')=='open':return open_page(computer,args['url'],cancel)
    raise ValueError('未知浏览器操作')

def validate_url(url):
    url=str(url).strip()
    parsed=urlsplit(url)
    if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('需要有效的 http/https 网址；网站名称先由模型解析或搜索')
    if len(url)>3000:raise ValueError('网址过长')
    return url

def open_page(computer,url,cancel):
    from core.browser_launch import launch,default_executable
    import psutil,win32process
    url=validate_url(url);executable=default_executable()
    receipt=launch(url)
    if cancel.wait(.6):return dict(ok=False,launch_requested=True,error='导航已提交，随后任务被停止，未复查')
    candidates=[]
    for window in computer.execute(dict(action='windows'),cancel)['windows']:
        try:
            pid=win32process.GetWindowThreadProcessId(window['hwnd'])[1]
            path=psutil.Process(pid).exe()
            if executable and path.casefold()==str(executable).casefold():candidates.append(window)
        except (OSError,psutil.Error):pass
    if not candidates:return dict(ok=False,launch_requested=True,error='浏览器已接受网址，但尚未定位窗口；使用 computer.windows 继续核对，不要重复启动')
    preferred=[w for w in candidates if '浏览器' in w['title']] or candidates
    window=preferred[0]
    try:
        computer.execute(dict(action='focus',hwnd=window['hwnd']),cancel)
        observation=computer.execute(dict(action='observe',hwnd=window['hwnd']),cancel)
        accessibility=computer.execute(dict(action='accessibility',hwnd=window['hwnd']),cancel)
    except (RuntimeError,ValueError,OSError) as exc:
        return dict(ok=False,launch_requested=True,error='网址已提交，但未完成页面验证：'+str(exc))
    return dict(ok=True,result=dict(observation,url_requested=url,launch_requested=True,
        page_verified=False,accessibility=accessibility,task_complete=True,
        message=('网址已经交给用户的默认浏览器打开，真实窗口也已观察到——'
                 '**这一步就算做完了**。不要再 open 一遍、不要换成专用浏览器再开一次、'
                 '也不要为了「确认是否打开」而重复尝试；需要看页面内容就直接用 '
                 'computer 的 observe 看当前这个窗口。')))
