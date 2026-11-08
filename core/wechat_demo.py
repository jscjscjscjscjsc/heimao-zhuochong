"""Explicit-recipient WeChat capability; uncertain delivery is never retried."""
import base64,json,os,subprocess,tempfile,time,re
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def is_self_message_request(prompt):
    from core.fast_commands import command_text
    text=command_text(prompt)
    if any(word in text for word in ('不要','别发','为什么','失败','错误','上次','之前','如何','怎么')):return False
    return bool(re.match(r'^(?:在微信中?|用微信|打开微信[，,]?|微信)?(?:给|向)?(?:我自己的?|我的)?文件传输助手.*(?:发一段|发送|发一句|写一段|组织语言)',text))

def compact(value):return re.sub(r'[\W_]+','',str(value))

def phrase_boxes(ocr,phrase):
    """Windows Chinese OCR often returns one character per word."""
    result=[]
    for line in ocr.get('lines',[]):
        words=line.get('words',[])
        for start in range(len(words)):
            value='';selected=[]
            for word in words[start:]:
                value+=compact(word['text']);selected.append(word)
                if value==phrase:
                    x=min(w['x'] for w in selected);y=min(w['y'] for w in selected)
                    result.append(dict(text=phrase,x=x,y=y,width=max(w['x']+w['width'] for w in selected)-x,height=max(w['y']+w['height'] for w in selected)-y));break
                if not phrase.startswith(value):break
    return result

def read_frame(observation):
    folder=ROOT/'runtime/chat/ocr';folder.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix='.jpg',dir=folder,delete=False) as handle:
        path=Path(handle.name);handle.write(base64.b64decode(observation['_image_url'].split(',',1)[1]))
    try:
        try:
            result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'tools/windows_ocr.ps1'),str(path)],
                capture_output=True,timeout=12,creationflags=0x08000000)
        except subprocess.TimeoutExpired:raise RuntimeError('读取微信界面超时；未确认发送，交给智能体检查当前窗口') from None
        if result.returncode:raise RuntimeError('Windows OCR 未完成；未继续发送，请检查中文 OCR 语言组件')
        return json.loads(result.stdout.decode('utf-8-sig',errors='replace'))
    finally:path.unlink(missing_ok=True)

def send_message(computer,recipient,text,cancel,prepare_only=False,chat=None):
    recipient=str(recipient).strip()
    if not recipient or len(recipient)>80 or any(c in recipient for c in "\n\r"):raise ValueError("需要用户指定的精确聊天对象名称")
    text=str(text).strip()
    if not prepare_only and (not 1<=len(text)<=1000 or '\n' in text or '\r' in text):raise ValueError('演示消息需要1—1000字单段文本')
    import win32process,psutil
    matches=[]
    for window in computer.execute(dict(action='windows'),cancel)['windows']:
        try:
            pid=win32process.GetWindowThreadProcessId(window['hwnd'])[1]
            if psutil.Process(pid).name().lower() in ('weixin.exe','wechat.exe') and window['title']=='微信':matches.append(window)
        except (OSError,psutil.Error):pass
    if len(matches)!=1:raise RuntimeError('没有找到唯一的微信主窗口，请打开微信主窗口')
    hwnd=matches[0]['hwnd'];computer.execute(dict(action='focus',hwnd=hwnd),cancel)
    def observe():
        obs=computer.execute(dict(action='observe',hwnd=hwnd),cancel)
        return obs,read_frame(obs)
    obs,ocr=observe();width,height=obs['image_size']
    keyboard_picked=False
    if not chat:
        # 优先用微信内置快捷键聚焦搜索框，不依赖 OCR 认出"搜索"两个字
        # （微信新版搜索框只有放大镜图标，OCR 经常漏掉）。
        typed=False
        try:
            computer.execute(dict(action='hotkey',observation_id=obs['observation_id'],keys='ctrl+f'),cancel)
            cancel.wait(.35);obs,ocr=observe()
            computer.execute(dict(action='type',observation_id=obs['observation_id'],text=recipient,replace=True,clear=True),cancel)
            typed=True
        except Exception:
            typed=False
        if not typed:
            search_words=[w for w in phrase_boxes(ocr,'搜索') if w['x']<width*.35 and w['y']<height*.25]
            if not search_words:raise RuntimeError('未定位微信顶部搜索框；未发送')
            search=search_words[0]
            computer.execute(dict(action='fill',observation_id=obs['observation_id'],x=int(search['x']+search['width']/2),y=int(search['y']+search['height']/2),text=recipient),cancel)
        cancel.wait(.7);obs,ocr=observe();width,height=obs['image_size']
        contacts=[box for box in phrase_boxes(ocr,compact(recipient)) if box['x']<width*.35 and box['y']>height*.13]
        keyboard_picked=False
        if not contacts:
            # OCR 只装了中文/英文模型，认不出日文假名、韩文、emoji 这类名字。
            # 这种情况下退回键盘路径：搜索框里输入后按 ↓ 选中第一个结果、回车进入。
            if not any(ord(c)>0x7F for c in recipient):
                raise RuntimeError('搜索结果未找到匹配聊天对象；未输入或发送（若聊天输入框里留下了草稿，请手动清掉）')
            # 用点击而不是 ↓+Enter：微信搜索结果里按 ↓ 会选中"群聊"那一条，直接进错会话。
            # 搜索面板布局固定：「联系人」分组在最上面，第一条就在 height*0.18 附近。
            grp=[w for w in phrase_boxes(ocr,'联系人') if w['x']<width*.35 and w['y']<height*.25]
            ty=int(grp[0]['y']+max(38,height*.055)) if grp else int(height*.18)
            try:computer.execute(dict(action='click',observation_id=obs['observation_id'],x=int(width*.22),y=ty),cancel)
            except Exception:pass
            cancel.wait(.5);obs,ocr=observe();width,height=obs['image_size']
            keyboard_picked=True
        else:
            contact=min(contacts,key=lambda box:box['y'])
            computer.execute(dict(action='click',observation_id=obs['observation_id'],x=int(contact['x']+contact['width']/2),y=int(contact['y']+contact['height']/2)),cancel)
            cancel.wait(.2);obs,ocr=observe();width,height=obs['image_size']
    # Require the actual conversation header, not a matching sidebar contact.
    header=any(box['x']>width*.2 and box['y']<height*.23 for box in phrase_boxes(ocr,compact(recipient)))
    if not header and not keyboard_picked:raise RuntimeError('顶部会话名未验证为指定对象，未输入或发送')
    if not header and keyboard_picked:
        # 键盘选中结果、但标题全是 OCR 认不出的字符：不谎报"已核对"，交给人看截图。
        return dict(ok=True,verified=False,recipient=recipient,hwnd=hwnd,_image_url=obs['_image_url'],
            message='已用键盘选中第一个搜索结果并进入会话，但标题含 OCR 认不出的字符，未能逐字核对；请核对截图确认是不是对的人，再调用 send')
    if prepare_only:return dict(ok=True,ready_for_message=True,recipient=recipient,hwnd=hwnd,_image_url=obs['_image_url'],message='已进入首个匹配会话并核对标题；现在根据用户要求组织正文，再调用send')
    input_words=[w for w in phrase_boxes(ocr,'发送') if w['y']>height*.6]
    if not input_words:raise RuntimeError('未识别微信发送按钮；未继续输入')
    button=max(input_words,key=lambda w:w['x']);x=int(width*.55);y=int(button['y']-max(28,height*.06))
    bottom=''.join(w['text'] for l in ocr.get('lines',[]) for w in l.get('words',[]) if w['x']>width*.28 and button['y']-max(45,height*.12)<w['y']<button['y']-22)
    draft=compact(bottom).replace('长按鼠标语音输入','')
    if draft:raise RuntimeError('输入区域存在未处理草稿；保留草稿，未继续发送')
    computer.execute(dict(action='fill',observation_id=obs['observation_id'],x=x,y=y,text=text),cancel)
    obs,ocr=observe()
    if not any(b['x']>width*.2 and b['y']<height*.23 for b in phrase_boxes(ocr,compact(recipient))):
        return dict(ok=False,error='输入后会话对象发生变化；未点击发送',sent=False)
    # Send only text that actually appeared in the composer.
    if compact(text) not in compact(''.join(l['text'] for l in ocr.get('lines',[]) if l.get('words') and min(w['y'] for w in l['words'])>height*.65)):
        return dict(ok=False,error='输入内容未通过回读；未点击发送，请检查草稿',sent=False)
    buttons=[w for w in phrase_boxes(ocr,'发送') if w['y']>height*.6]
    if not buttons:raise RuntimeError('发送按钮已变化；未发送')
    button=max(buttons,key=lambda w:w['x'])
    computer.execute(dict(action='click',observation_id=obs['observation_id'],x=int(button['x']+button['width']/2),y=int(button['y']+button['height']/2)),cancel)
    cancel.wait(.7);obs,ocr=observe()
    transcript=compact(''.join(l['text'] for l in ocr.get('lines',[]) if l.get('words') and min(w['y'] for w in l['words'])<button['y']-40))
    found=compact(text) in transcript
    return dict(ok=found,recipient=recipient,send_clicked=True,verified_message_visible=found,
        delivery_verified=False,message='消息已出现在指定聊天区；不代表服务器送达确认' if found else '已点击发送，但未回读到消息；请检查，不会自动重发')


def send_to_self(computer,text,cancel):
    return send_message(computer,"文件传输助手",text,cancel)


class WeChatAgent:
    def __init__(self,computer):self.computer=computer;self.prepared=None
    def execute(self,args,cancel):
        import uuid
        action=args.get('action');recipient=str(args.get('recipient','')).strip()
        if action=='open':
            result=send_message(self.computer,recipient,'',cancel,prepare_only=True)
            if result.get('ok'):
                token=uuid.uuid4().hex;self.prepared=dict(token=token,recipient=recipient,hwnd=result['hwnd'],created=time.monotonic())
                result['chat_token']=token
            return result
        if action!='send':raise ValueError('微信操作仅支持open/send')
        token=args.get('chat_token');ready=self.prepared
        if token and (not ready or token!=ready['token'] or recipient!=ready['recipient'] or time.monotonic()-ready['created']>120):
            raise ValueError('会话凭据已失效，请重新open；未发送')
        result=send_message(self.computer,recipient,args.get('text',''),cancel,chat=ready if token else None)
        if result.get('send_clicked'):self.prepared=None
        return result
