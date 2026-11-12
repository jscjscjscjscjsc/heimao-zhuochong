"""One spoken instruction -> one action in the current browser, without a model."""
import re
import threading
import time


_WINDOW_WORDS=('浏览器','窗口','页面','网页','当前页面','当前窗口','这个页面','这个窗口')


def _field_name(value):
    """'在浏览器的输入框中输入X': 浏览器的 describes the window, not the field."""
    target=str(value or '').strip().strip('“”"\'')
    target=re.sub(r'^(?:浏览器|窗口|页面|网页|当前页面|当前窗口)(?:的|里的|中的)?','',target).strip()
    target=re.sub(r'(?:里的|中的|的)$','',target).strip()
    if not target or target in _WINDOW_WORDS:
        return '输入框'
    return target


def parse_step(value,source_text=''):
    common=dict(matched=True,action='browser_step',kind='browser',silent=True,priority='fast',source_text=source_text or value)
    shortcuts={
        '返回上一页':'back','回到上一页':'back','网页返回':'back','浏览器返回':'back','后退':'back',
        '前进一页':'forward','前进到下一页':'forward','刷新页面':'refresh','刷新网页':'refresh','刷新':'refresh',
        '切换到下一个标签页':'next_tab','下一个标签页':'next_tab','切换到上一个标签页':'previous_tab','上一个标签页':'previous_tab',
        '新建标签页':'new_tab','打开新标签页':'new_tab','聚焦地址栏':'address_bar','点击地址栏':'address_bar',
        '按回车':'enter','按一下回车':'enter','回车':'enter','全选输入内容':'select_all',
        '清空输入框':'clear','清空当前输入框':'clear',
        '向下滚动':'scroll_down','往下滚动':'scroll_down','往下翻':'scroll_down','下滑':'scroll_down',
        '向上滚动':'scroll_up','往上滚动':'scroll_up','往上翻':'scroll_up','上滑':'scroll_up',
    }
    if value in shortcuts:return dict(common,browser_action=shortcuts[value],target='')
    entered=re.fullmatch(r'进入(?:我的|这个页面的|当前页面的|网页上的|页面里的)(.+?)(?:好吗|吧)?',value)
    if entered:value='点击'+entered.group(1)
    click=re.fullmatch(r'(?:点击|点一下|点选|点开|按一下|点)(?:一下)?(?:当前网页上(?:的)?|网页上(?:的)?|页面上(?:的)?|这个页面的)?\s*(.+?)(?:这个按钮|按钮|这个链接|链接)?(?:一下|好吗|可以吗|吧)?',value)
    if click:
        target=click.group(1).strip().strip('“”"\'')
        generic=re.fullmatch(r'(?:(?:这个|那个|当前|上面的|页面里的|页面上的|页面的)\s*)+(输入框|搜索框|搜索输入框)',target)
        if generic:target=generic.group(1)
        elif not click.group(1).strip().startswith(('“','"',"'")):
            target=re.sub(r'^(?:这个|那个|上面的|页面里的|页面上的|页面的)+','',target)
        if target and len(target)<=120 and not re.search(r'然后|接着|并且|再帮|屏幕|桌面|窗口|[，,;；]',target):
            return dict(common,browser_action='click',target=target)
    named=re.fullmatch(r'(?:在|往|向)(.+?)(?:输入框|框)(?:里|内|中)?(?:输入|填写)[:：\s]*(.+)',value)
    if named:
        original=re.search(r'(?:在|往|向)(.+?)(?:输入框|框)(?:里|内|中)?(?:输入|填写)[:：\s]*(.+)',source_text or value,re.S)
        text=(original.group(2) if original else named.group(2)).strip()
        if len(text)>=2 and (text[0],text[-1]) in (('“','”'),('"','"'),("'","'")):text=text[1:-1]
        return dict(common,browser_action='fill_named',target=_field_name(named.group(1)),text=text)
    typed=re.fullmatch(r'(追加输入|输入|键入|填写)[:：\s]*(.+)',value)
    if value.startswith(('输入法','输入框')):typed=None
    if typed:
        # Keep literal punctuation and whitespace from the user's text. It is
        # field content, never shell, script or another interpreted command.
        original=re.search(r'(?:追加输入|输入|键入|填写)[:：\s]*(.+)',source_text or value,re.S)
        text=(original.group(1) if original else typed.group(2)).strip()
        if len(text)>=2 and (text[0],text[-1]) in (('“','”'),('"','"'),("'","'")):text=text[1:-1]
        if text and len(text)<=4000:return dict(common,browser_action='append' if typed.group(1)=='追加输入' else 'fill',target=text)
    return None


class BrowserInteraction:
    def __init__(self):
        from core.uia_service import shared_service
        self.uia=shared_service();self.lock=threading.Lock()

    def warmup(self):return self.uia.warmup()
    def prewarm(self):
        def run():
            try:self.warmup()
            except RuntimeError:pass
        threading.Thread(target=run,name='guga-uia-warmup',daemon=True).start()
    def close(self):self.uia.close()

    @staticmethod
    def _guard(hwnd=None):
        import win32gui,win32process,psutil
        active=win32gui.GetForegroundWindow()
        if not active or (hwnd and int(hwnd)!=active):raise RuntimeError('请先切到要操作的浏览器页面，本次未发送操作')
        pid=win32process.GetWindowThreadProcessId(active)[1]
        if psutil.Process(pid).name().lower() not in ('msedge.exe','chrome.exe','firefox.exe','doubao.exe','brave.exe'):
            raise RuntimeError('当前前台不是浏览器，请先切到目标网页，本次未发送操作')
        return active

    def execute(self,command,hwnd=None):
        started=time.perf_counter()
        if not self.lock.acquire(blocking=False):return dict(ok=False,action_sent=False,code='desktop_busy',error='上一项界面操作尚未结束，本次未发送操作')
        attempted=False
        try:
            from core.desktop_access import DesktopAccess
            if not DesktopAccess().enabled:raise RuntimeError('完整访问未开启，本次未发送操作')
            hwnd=self._guard(hwnd);action=command.get('browser_action');verified=False
            keys={'back':[18,37],'forward':[18,39],'refresh':[116],
                  'next_tab':[17,9],'previous_tab':[17,16,9],'new_tab':[17,84],
                  'address_bar':[17,76],'enter':[13],'select_all':[17,65]}
            if action in keys:
                from core.computer_control import _send_keys
                self._guard(hwnd);attempted=True;_send_keys(keys[action])
                message='已发送'+{'back':'返回','forward':'前进','refresh':'刷新','enter':'回车'}.get(action,'浏览器快捷键')
            elif action in ('fill','append','clear','fill_named'):
                if action=='fill_named':
                    if command['target'] in ('搜索','搜索框','搜索输入','搜索输入框','当前','这个','输入'):
                        item=self.uia.page_input(hwnd,search='搜索' in command['target'])['element']
                    else:item=self.uia.read(hwnd,name=command['target'],control_type='Edit')['element']
                    text=command['text']
                else:
                    item=self.uia.focused(hwnd)['element'];text='' if action=='clear' else command['target']
                    if action=='append':
                        if item.get('value_truncated'):raise RuntimeError('当前内容过长，未执行追加输入')
                        text=item.get('value','')+text
                if len(text)>4000:raise RuntimeError('输入内容超过4000字，本次未发送操作')
                self._guard(hwnd);attempted=True
                receipt=self.uia.act(hwnd,'set_value',ref=item['ref'],value=text)
                verified=receipt.get('verified',False)
                if not verified:raise RuntimeError('输入已尝试，但回读未匹配，请先核对输入框')
                message='已清空输入框' if action=='clear' else '已填写，并回读确认'
            elif action=='click':
                if command['target'] in ('输入框','当前输入框','搜索框','搜索输入框'):
                    item=self.uia.page_input(hwnd,search='搜索' in command['target'])['element']
                else:item=self.uia.read(hwnd,name=command['target'])['element']
                operation='focus' if item['type'] in ('Edit','ComboBox') else 'invoke'
                self._guard(hwnd);attempted=True
                receipt=self.uia.act(hwnd,operation,ref=item['ref'])
                if receipt.get('fallback'):
                    rect=receipt.get('rect')
                    if not rect:raise RuntimeError('控件没有可点击区域，本次未发送鼠标操作')
                    self._guard(hwnd)
                    import ctypes
                    user=ctypes.windll.user32
                    user.SetCursorPos(int((rect['left']+rect['right'])/2),int((rect['top']+rect['bottom'])/2))
                    user.mouse_event(2,0,0,0,0);user.mouse_event(4,0,0,0,0)
                verified=receipt.get('verified',False)
                message='已聚焦输入框' if operation=='focus' else '已点击'+command['target']
            elif action in ('scroll_down','scroll_up'):
                import ctypes,win32gui
                # Wheel delivery follows the pointer on Windows. Move it into
                # this browser before sending, rather than scrolling another app.
                rect=win32gui.GetWindowRect(hwnd);self._guard(hwnd);attempted=True
                user=ctypes.windll.user32
                user.SetCursorPos((rect[0]+rect[2])//2,(rect[1]+rect[3])//2)
                user.mouse_event(0x0800,0,0,-480 if action=='scroll_down' else 480,0)
                message='已向下滚动' if action=='scroll_down' else '已向上滚动'
            else:raise ValueError('不支持的单步浏览器操作')
            return dict(ok=True,action_sent=True,verified=verified,hwnd=hwnd,message=message,
                dispatch_ms=round((time.perf_counter()-started)*1000,2),route='local_browser_step')
        except Exception as exc:
            return dict(ok=False,code='browser_step_failed',action_sent=None if attempted else False,
                error=str(exc)[:350],dispatch_ms=round((time.perf_counter()-started)*1000,2),route='local_browser_step')
        finally:self.lock.release()
