"""Windows observation/input tools. No per-application allowlist in full access mode."""
import base64
import ctypes
from ctypes import wintypes
import io
import threading
import time
import uuid

class _KI(ctypes.Structure):
    _fields_=[('vk',wintypes.WORD),('scan',wintypes.WORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
class _MI(ctypes.Structure):
    _fields_=[('dx',wintypes.LONG),('dy',wintypes.LONG),('data',wintypes.DWORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
class _U(ctypes.Union):_fields_=[('ki',_KI),('mi',_MI)]
class _INPUT(ctypes.Structure):_fields_=[('type',wintypes.DWORD),('value',_U)]

def _attach_focus(hwnd):
    """Windows foreground handoff, bounded to the requested existing window."""
    import win32gui,win32process,win32api
    user=ctypes.windll.user32;attached=[]
    try:
        target_thread=win32process.GetWindowThreadProcessId(hwnd)[0]
        foreground=win32gui.GetForegroundWindow()
        foreground_thread=win32process.GetWindowThreadProcessId(foreground)[0] if foreground else 0
        current=win32api.GetCurrentThreadId()
        if not target_thread:return False
        for thread in set((target_thread,foreground_thread))-{0,current}:
            if user.AttachThreadInput(current,thread,True):attached.append(thread)
        win32gui.BringWindowToTop(hwnd);win32gui.SetForegroundWindow(hwnd)
        return win32gui.GetForegroundWindow()==hwnd
    except Exception:return False
    finally:
        for thread in reversed(attached):user.AttachThreadInput(current,thread,False)

def _send_keys(values):
    user=ctypes.windll.user32
    def send(code,up=False):
        extended=code in (33,34,35,36,37,38,39,40,45,46,91,92)
        packet=_INPUT(1,_U(ki=_KI(code,user.MapVirtualKeyW(code,0),int(extended)|(2 if up else 0),0,0)))
        if user.SendInput(1,ctypes.byref(packet),ctypes.sizeof(_INPUT))!=1:raise RuntimeError('Windows拒绝快捷键输入')
    try:
        for code in values:send(code)
    finally:
        for code in reversed(values):send(code,True)


class ComputerControl:
    def __init__(self, access=None):
        from core.desktop_access import DesktopAccess
        self.access=access or DesktopAccess()
        self.observation=None
        self._uia_backend=None

    def _uia(self):
        if self._uia_backend is None:
            from core.uia_service import shared_service
            self._uia_backend=shared_service()
        return self._uia_backend

    def execute(self,args,cancel=None):
        # Match screenshot and input coordinates on mixed-DPI monitors without
        # changing Qt's UI-thread coordinate system.
        user=ctypes.windll.user32
        setter=getattr(user,'SetThreadDpiAwarenessContext',None)
        previous=None
        if setter:
            setter.restype=ctypes.c_void_p;setter.argtypes=[ctypes.c_void_p]
            previous=setter(ctypes.c_void_p(-4))
        try:return self._execute(args,cancel)
        finally:
            if setter and previous:setter(previous)

    def _execute(self,args,cancel=None):
        if not self.access.enabled:raise ValueError('屏幕与输入需要开启完整访问')
        cancel=cancel or threading.Event()
        if cancel.is_set():raise RuntimeError('已停止屏幕操作')
        import win32gui
        action=args.get('action')
        if action=='editor_context':
            import json,os
            from pathlib import Path
            from urllib.parse import urlparse,unquote
            query=str(args.get('query','')).replace(' ','').casefold()
            entries=[]
            for product in ('Trae CN','Trae','Code','Cursor','Code - Insiders'):
                folder=Path(os.getenv('APPDATA',''))/product/'User/workspaceStorage'
                if not folder.is_dir():continue
                files=sorted(folder.glob('*/workspace.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:80]
                for path in files:
                    try:
                        data=json.loads(path.read_text(encoding='utf-8-sig'))
                        uri=data.get('folder') or data.get('workspace','')
                        parsed=urlparse(uri)
                        if parsed.scheme!='file':continue
                        target=unquote(parsed.path).lstrip('/')
                        if query and query not in target.replace(' ','').casefold():continue
                        entries.append(dict(editor=product,path=target,exists=Path(target).exists(),source='workspace.json',updated=path.stat().st_mtime))
                    except (OSError,ValueError,TypeError):continue
            return dict(workspaces=sorted(entries,key=lambda e:e['updated'],reverse=True)[:30],
                note='编辑器最近工作区元数据；结合窗口标题核对，不能只凭最近记录判断当前文件。')
        if action=='windows':
            from core.window_inventory import list_windows
            return dict(windows=list_windows(args.get('query','')),
                note='这是已经运行的窗口，包含最小化窗口；按进程和标题共同识别。选定后focus恢复，再observe/accessibility。')
        if action=='focus':
            hwnd=int(args['hwnd'])
            if not win32gui.IsWindow(hwnd):raise ValueError('窗口已关闭，请重新列出')
            win32gui.ShowWindow(hwnd,9)
            if win32gui.GetForegroundWindow()!=hwnd:
                import pywintypes
                try:win32gui.SetForegroundWindow(hwnd)
                except pywintypes.error:
                    if win32gui.GetForegroundWindow()!=hwnd and not _attach_focus(hwnd):
                        raise RuntimeError('Windows未允许切换到目标窗口；尚未发送输入，请检查窗口状态或请用户聚焦目标窗口') from None
            self.observation=None
            if win32gui.GetForegroundWindow()!=hwnd:raise RuntimeError('目标窗口尚未获得焦点；未发送输入')
            return dict(focused=True,needs_observe=True)
        if action=='observe':
            from PIL import ImageGrab
            hwnd=int(args.get('hwnd') or win32gui.GetForegroundWindow())
            if hwnd!=win32gui.GetForegroundWindow():raise ValueError('先 focus 目标窗口再观察，避免遮挡误认')
            rect=win32gui.GetWindowRect(hwnd)
            if rect[2]<=rect[0] or rect[3]<=rect[1]:raise ValueError('窗口没有可见区域')
            frame=ImageGrab.grab(bbox=rect,all_screens=True).convert('RGB')
            # 默认压到 1024 长边：满屏截图每张要多花四成 token，而多数点击只需要认出按钮在哪；
            # 要看清小字再显式传 max_side=1600。坐标仍按 image_size 的相对像素换算。
            side=max(640,min(1600,int(args.get('max_side') or 1024)))
            frame.thumbnail((side,int(side*0.75)))
            output=io.BytesIO();frame.save(output,format='JPEG',quality=72)
            token=uuid.uuid4().hex
            self.observation=dict(id=token,hwnd=hwnd,rect=rect,image_size=frame.size,time=time.monotonic())
            return dict(observation_id=token,hwnd=hwnd,title=win32gui.GetWindowText(hwnd),
                desktop_rect=rect,image_size=frame.size,
                coordinates='默认传截图内的相对像素坐标，与image_size一致；执行器自动转换到桌面。仅coordinate_space=desktop时传桌面物理坐标。',
                _image_url='data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode())
        if action=='accessibility':
            hwnd=int(args.get('hwnd') or win32gui.GetForegroundWindow())
            if not win32gui.IsWindow(hwnd):raise ValueError('窗口已关闭')
            from core.uia_backend import UIAUnavailable
            try:return self._uia().snapshot(hwnd,args.get('limit',240))
            except UIAUnavailable:pass
            script='''Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$rootElement=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HWND_VALUE)
$nodes=$rootElement.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
$items=for($i=0;$i -lt [Math]::Min($nodes.Count,150);$i++) {
  try {$c=$nodes.Item($i).Current; if($c.Name -or $c.AutomationId) {
    [pscustomobject]@{name=$c.Name;id=$c.AutomationId;type=$c.ControlType.ProgrammaticName;enabled=$c.IsEnabled;bounds=$c.BoundingRectangle.ToString()}
  }} catch {}
}
ConvertTo-Json -InputObject @($items) -Compress -Depth 3'''.replace('HWND_VALUE',str(hwnd))
            from core.desktop_access import ROOT
            output=self.access.run(script,str(ROOT),6,cancel)
            if output.get('exit_code')==0:
                import json
                try:items=json.loads(output.get('stdout') or '[]')
                except ValueError:items=None
                if isinstance(items,dict):items=[items]
                if isinstance(items,list):
                    return dict(hwnd=hwnd,elements=items[:150],
                        hint='优先按 name/id/type 调用 computer(action=uia)，操作后重新读取验证')
            return output
        if action=='uia':
            hwnd=int(args.get('hwnd') or win32gui.GetForegroundWindow())
            if not win32gui.IsWindow(hwnd):raise ValueError('窗口已关闭')
            if win32gui.GetForegroundWindow()!=hwnd:raise ValueError('先 focus 目标窗口再执行控件操作')
            name=str(args.get('uia_name',''))[:200]
            automation_id=str(args.get('uia_id',''))[:200]
            control_type=str(args.get('uia_type',''))[:80]
            value=str(args.get('uia_value',''))
            operation=str(args.get('uia_operation',''))
            if not any((name,automation_id,control_type,args.get('uia_ref'))) or operation not in ('invoke','set_value','focus','click'):
                raise ValueError('uia 需要 ref/Name/AutomationId/ControlType 之一，以及合法操作')
            try:
                result=self._uia().act(hwnd,operation,ref=args.get('uia_ref'),name=name,
                    automation_id=automation_id,control_type=control_type,value=value)
                if result.pop('fallback',False):
                    rect=result.pop('rect',None)
                    if not rect:raise RuntimeError('UIA控件没有可点击区域')
                    user.SetCursorPos(int((rect['left']+rect['right'])/2),int((rect['top']+rect['bottom'])/2))
                    user.mouse_event(2,0,0,0,0);user.mouse_event(4,0,0,0,0)
                    result['method']='MouseFallback';result['input_sent']=True
                else:result.pop('rect',None)
                self.observation=None
                return result
            except ValueError:raise
            except Exception as backend_error:
                if '进程内 UIAutomation 不可用' not in str(backend_error):raise
            def pstr(value):return "'"+value.replace("'","''")+"'"
            if len(value)>4000:raise ValueError('uia_value 最多4000字')
            script='''Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$root=[System.Windows.Automation.AutomationElement]::FromHandle([IntPtr]HWND_VALUE)
$nodes=$root.FindAll([System.Windows.Automation.TreeScope]::Descendants,[System.Windows.Automation.Condition]::TrueCondition)
$target=$null;$match=@{};$name=NAME_VALUE;$id=ID_VALUE;$type=TYPE_VALUE
for($i=0;$i -lt $nodes.Count;$i++){try{$c=$nodes.Item($i).Current
  if(($name -eq '' -or $c.Name -eq $name) -and ($id -eq '' -or $c.AutomationId -eq $id) -and ($type -eq '' -or $c.ControlType.ProgrammaticName -eq ('ControlType.'+$type))){$target=$nodes.Item($i);break}}catch{}}
if($null -eq $target){throw 'UIA target not found'}
$c=$target.Current;$performed=$false;$method=''
if(OPERATION_VALUE -eq 'invoke'){
  $p=$null;if($target.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$p)){$p.Invoke();$performed=$true;$method='InvokePattern'}
  elseif($target.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern,[ref]$p)){$p.Select();$performed=$true;$method='SelectionItemPattern'}
}elseif(OPERATION_VALUE -eq 'set_value'){
  $p=$null;if($target.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern,[ref]$p)){$p.SetValue(VALUE_VALUE);$performed=$true;$method='ValuePattern'}
}elseif(OPERATION_VALUE -eq 'focus'){
  $target.SetFocus();$performed=$true;$method='SetFocus'
}
$r=$c.BoundingRectangle
[pscustomobject]@{name=$c.Name;id=$c.AutomationId;type=$c.ControlType.ProgrammaticName;enabled=$c.IsEnabled;
  left=$r.Left;top=$r.Top;right=$r.Right;bottom=$r.Bottom;performed=$performed;method=$method} | ConvertTo-Json -Compress'''
            script=script.replace('HWND_VALUE',str(hwnd)).replace('NAME_VALUE',pstr(name)).replace('ID_VALUE',pstr(automation_id))
            script=script.replace('TYPE_VALUE',pstr(control_type)).replace('OPERATION_VALUE',pstr(operation)).replace('VALUE_VALUE',pstr(value))
            from core.desktop_access import ROOT
            output=self.access.run(script,str(ROOT),8,cancel)
            if output.get('exit_code')!=0:raise RuntimeError((output.get('stderr') or 'UIA控件操作失败').strip()[:300])
            import json
            try:result=json.loads(output.get('stdout') or '{}')
            except ValueError:raise RuntimeError('UIA返回格式无效；未发送鼠标输入') from None
            if not result.get('enabled',False):raise RuntimeError('UIA目标控件不可用')
            method=str(result.get('method',''))
            if operation=='click' or not result.get('performed'):
                left,top,right,bottom=(float(result.get(k,0)) for k in ('left','top','right','bottom'))
                if right<=left or bottom<=top:raise RuntimeError('UIA目标没有可点击区域')
                user.SetCursorPos(int((left+right)/2),int((top+bottom)/2))
                user.mouse_event(2,0,0,0,0);user.mouse_event(4,0,0,0,0)
                method='MouseFallback'
            self.observation=None
            return dict(input_sent=True,action='uia',needs_observe=True,method=method,
                control=dict(name=result.get('name'),id=result.get('id'),type=result.get('type')),
                message='UIA操作已执行；仍需 accessibility 或 observe 验证结果')
        if action=='uia_read':
            hwnd=int(args.get('hwnd') or win32gui.GetForegroundWindow())
            return self._uia().read(hwnd,ref=args.get('uia_ref'),name=args.get('uia_name',''),
                automation_id=args.get('uia_id',''),control_type=args.get('uia_type',''))
        if action=='uia_wait':
            hwnd=int(args.get('hwnd') or win32gui.GetForegroundWindow())
            if not win32gui.IsWindow(hwnd):raise ValueError('窗口已关闭')
            name=str(args.get('uia_name',''))[:200];automation_id=str(args.get('uia_id',''))[:200]
            control_type=str(args.get('uia_type',''))[:80];ref=args.get('uia_ref')
            if not any((name,automation_id,control_type,ref)):raise ValueError('uia_wait 需要控件条件')
            state=str(args.get('uia_state') or args.get('uia_wait') or 'exists')
            if state not in ('exists','enabled','offscreen'):raise ValueError('未知等待状态')
            return self._uia().wait(hwnd,args.get('uia_timeout_ms',5000)/1000,ref=ref,name=name,
                automation_id=automation_id,control_type=control_type,state=state,
                expected=args.get('uia_expected',True))
        observed=self.observation
        if not observed or args.get('observation_id')!=observed['id'] or time.monotonic()-observed['time']>30:
            raise ValueError('观察 ID 已消耗或过期；先 observe 取得新 ID。若刚点击过输入框，只需用新 ID 执行 type，不要再次点击；也可一次 fill 完成定位与输入')
        if win32gui.GetForegroundWindow()!=observed['hwnd'] or win32gui.GetWindowRect(observed['hwnd'])!=observed['rect']:
            self.observation=None
            raise ValueError('前台窗口或位置已改变，先重新观察')
        self.observation=None
        def mapped(x,y):
            left,top,right,bottom=observed['rect']
            if args.get('coordinate_space','screenshot')=='screenshot':
                width,height=observed.get('image_size',(right-left,bottom-top))
                if not 0<=x<width or not 0<=y<height:raise ValueError('坐标超出截图范围')
                x=left+x*(right-left)/width;y=top+y*(bottom-top)/height
            x,y=int(x),int(y)
            if not left<=x<right or not top<=y<bottom:raise ValueError('输入坐标超出刚才观察的窗口')
            return x,y
        def position(x,y):
            x,y=mapped(x,y)
            ctypes.windll.user32.SetCursorPos(x,y)
        user=ctypes.windll.user32
        requested_action=action
        if action=='fill':
            # One bounded transaction: there is no model round trip between
            # selecting the field and typing, and the observation is consumed once.
            text=str(args.get('text',''))
            if not text or len(text)>4000:raise ValueError('fill 需要1—4000字的 text')
            keys=str(args.get('keys','')).lower().replace(' ','')
            has_position='x' in args or 'y' in args
            if has_position and ('x' not in args or 'y' not in args):raise ValueError('fill 坐标需要同时提供 x、y')
            if keys and has_position:raise ValueError('fill 用坐标或快捷键定位，不能同时提供')
            if keys and keys not in ('ctrl+l','alt+d','ctrl+a'):raise ValueError('fill 仅支持 ctrl+l、alt+d、ctrl+a 定位')
            if has_position:
                position(args['x'],args['y'])
                user.mouse_event(2,0,0,0,0);user.mouse_event(4,0,0,0,0)
            elif keys:
                _send_keys({'ctrl+l':[17,76],'alt+d':[18,68],'ctrl+a':[17,65]}[keys])
            if cancel.is_set() or win32gui.GetForegroundWindow()!=observed['hwnd']:
                raise RuntimeError('输入已取消或前台窗口改变；未继续打字')
            action='type'
        if action in ('click','double_click','move','drag'):
            position(args['x'],args['y'])
            if action in ('click','double_click'):
                down,up=(8,16) if args.get('button')=='right' else (2,4)
                for _ in range(2 if action=='double_click' else 1):
                    user.mouse_event(down,0,0,0,0);user.mouse_event(up,0,0,0,0)
            elif action=='drag':
                # Validate destination before holding the button.
                end_x,end_y=mapped(args['end_x'],args['end_y'])
                start_x,start_y=mapped(args['x'],args['y'])
                user.mouse_event(2,0,0,0,0)
                try:
                    for step in range(1,13):
                        if cancel.wait(.02):raise RuntimeError('拖动已取消')
                        user.SetCursorPos(int(start_x+(end_x-start_x)*step/12),int(start_y+(end_y-start_y)*step/12))
                finally:user.mouse_event(4,0,0,0,0)
        elif action=='scroll':
            delta=int(args.get('delta',-3))
            if not -20<=delta<=20:raise ValueError('滚动量需要-20到20')
            user.mouse_event(0x800,0,0,delta*120,0)
        elif action=='hotkey':
            keys=str(args.get('keys','')).lower().split('+')
            aliases={'return':'enter','escape':'esc','control':'ctrl','control_l':'ctrl','control_r':'ctrl',
                'ctrl_l':'ctrl','alt_l':'alt','alt_r':'alt','shift_l':'shift','shift_r':'shift','super_l':'win','deleteforward':'delete'}
            keys=[aliases.get(k.strip(),k.strip()) for k in keys]
            codes={'ctrl':17,'alt':18,'shift':16,'win':91,'enter':13,'tab':9,'esc':27,'backspace':8,'delete':46,
                'up':38,'down':40,'left':37,'right':39,'home':36,'end':35,'space':32}
            values=[codes[k] if k in codes else ord(k.upper()) if len(k)==1 and k.isascii() else 111+int(k[1:]) if k.startswith('f') and k[1:].isdigit() and 1<=int(k[1:])<=12 else 0 for k in keys]
            if not values or len(values)>5 or 0 in values:raise ValueError('不支持的组合键；请用ctrl/alt/shift、enter、tab、esc、home、end及字母，例如ctrl+g、enter、ctrl+shift+end')
            _send_keys(values)
        elif action=='editor_select_tail':
            line=int(args.get('line',0))
            if not 1<=line<=200000:raise ValueError('需要有效的编辑器行号')
            if not any(name in win32gui.GetWindowText(observed['hwnd']).lower() for name in ('trae','code','cursor')):
                raise ValueError('仅在已观察的代码编辑器窗口使用')
            def chord(values):
                if cancel.is_set() or win32gui.GetForegroundWindow()!=observed['hwnd']:raise RuntimeError('选区操作已取消或窗口切换')
                _send_keys(values)
                cancel.wait(.15)
            chord([27]);chord([17,71]);chord([17,65])
            for char in str(line):chord([ord(char)])
            chord([13]);chord([36]);chord([17,16,35])
            return dict(input_sent=True,selection_requested=True,line=line,needs_observe=True,message='已请求选择此行至文件末尾；先observe核对选区后再输入，不能假定选择正确')
        elif action=='type':
            text=str(args.get('text',''))
            if len(text)>4000:raise ValueError('单次输入最多4000字')
            # replace=True：先全选，随后的输入会整体替换旧内容（搜索框里残留的脏字就是这么清掉的）。
            if args.get('replace'):
                _send_keys([17,65])
                cancel.wait(.15)
            # clear=True：再按 Delete 把框清空。微信搜索面板只在"内容发生变化"时弹出，
            # 如果框里已经是同样的字，直接重输不会触发搜索。
            if args.get('clear'):
                _send_keys([46])
                cancel.wait(.25)
            # 非 ASCII（日文假名/韩文/emoji/生僻字）不能走 SendInput：
            # 微信这类自绘输入框会把每个字符都替换成第一个（「おりがみ」被打成「おおおお」）。
            # 统一改用剪贴板粘贴，兼容性最好，且原剪贴板会恢复。
            if '\n' in text or any(ord(c)>0x7F for c in text):
                if self._paste_multiline(text,observed['hwnd'],cancel):
                    return dict(input_sent=True,action=action,needs_observe=True,
                        message='含非 ASCII 字符，已改用剪贴板粘贴；剪贴板已恢复，请 observe 核对')
            # KEYEVENTF_UNICODE; preserves the user's clipboard.
            class KI(ctypes.Structure):
                _fields_=[('vk',wintypes.WORD),('scan',wintypes.WORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
            class MI(ctypes.Structure):
                _fields_=[('dx',wintypes.LONG),('dy',wintypes.LONG),('data',wintypes.DWORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
            class U(ctypes.Union):_fields_=[('ki',KI),('mi',MI)]
            class INPUT(ctypes.Structure):_fields_=[('type',wintypes.DWORD),('value',U)]
            encoded=text.encode('utf-16-le')
            for offset in range(0,len(encoded),2):
                if cancel.is_set():raise RuntimeError('文字输入已取消')
                if win32gui.GetForegroundWindow()!=observed['hwnd']:
                    raise RuntimeError('打字期间前台窗口改变，已停止后续输入；部分文字可能已输入，请观察核对')
                scan=int.from_bytes(encoded[offset:offset+2],'little')
                inputs=(INPUT*2)(INPUT(1,U(ki=KI(0,scan,4,0,0))),INPUT(1,U(ki=KI(0,scan,6,0,0))))
                if user.SendInput(2,inputs,ctypes.sizeof(INPUT))!=2:raise RuntimeError('Windows 拒绝输入；检查实际进程权限')
        else:raise ValueError('未知屏幕操作')
        return dict(input_sent=True,action=requested_action,needs_observe=True,
            next_action='observe',message='输入已发送；只需重新 observe 验证效果，不要重复刚才的点击或输入')

    @staticmethod
    def _paste_multiline(text,hwnd,cancel):
        import win32clipboard as clip
        import win32con,win32gui
        saved=[]
        clip.OpenClipboard()
        try:
            fmt=0
            while True:
                fmt=clip.EnumClipboardFormats(fmt)
                if not fmt:break
                value=clip.GetClipboardData(fmt)
                if not isinstance(value,(bytes,str)) or len(value)>10_000_000:return False
                saved.append((fmt,value))
            clip.EmptyClipboard();clip.SetClipboardData(win32con.CF_UNICODETEXT,text)
        finally:clip.CloseClipboard()
        try:
            if cancel.is_set() or win32gui.GetForegroundWindow()!=hwnd:raise RuntimeError('窗口已切换，未粘贴')
            _send_keys([17,86])
            cancel.wait(.3)
            return True
        finally:
            clip.OpenClipboard()
            try:
                if clip.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT) and clip.GetClipboardData(win32con.CF_UNICODETEXT)==text:
                    clip.EmptyClipboard()
                    for fmt,value in saved:clip.SetClipboardData(fmt,value)
            finally:clip.CloseClipboard()
