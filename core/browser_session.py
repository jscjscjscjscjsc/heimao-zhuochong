"""Owned Playwright browser in a bounded worker; never attaches to personal tabs."""
import atexit
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
ACTIONS=('session_start','session_status','session_stop','tabs','new_tab','snapshot','navigate',
         'fill_form','click','press','scroll','wait','upload','download')


class BrowserSession:
    def __init__(self,profile_dir=None):
        self.process=None;self.responses=queue.Queue();self.lock=threading.Lock()
        self.profile_dir=profile_dir
        atexit.register(self.close)

    def close(self):
        process=self.process;self.process=None
        if process and process.poll() is None:
            try:
                process.stdin.close();process.wait(timeout=3)
            except (OSError,subprocess.TimeoutExpired):
                # Kill only this worker and its owned browser children.
                import psutil
                try:
                    parent=psutil.Process(process.pid);children=parent.children(recursive=True)
                    for child in reversed(children):
                        try:child.kill()
                        except psutil.Error:pass
                    parent.kill();process.wait(timeout=2)
                except (psutil.Error,subprocess.TimeoutExpired):pass
        if process:
            for stream in (process.stdin,process.stdout):
                try:stream.close()
                except OSError:pass

    def _launch(self):
        executable=Path(sys.executable).with_name('python.exe') if os.name=='nt' else Path(sys.executable)
        self.responses=queue.Queue();responses=self.responses
        environment=dict(os.environ,PYTHONIOENCODING='utf-8')
        if self.profile_dir:environment['GUGA_BROWSER_PROFILE_DIR']=str(self.profile_dir)
        self.process=subprocess.Popen([str(executable),'-m','core.browser_session','--worker',str(os.getpid())],
            cwd=str(ROOT),env=environment,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',bufsize=1,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def read(process):
            try:
                for line in process.stdout:
                    try:responses.put(json.loads(line))
                    except ValueError:pass
            except (OSError,ValueError):pass
            finally:responses.put(None)
        threading.Thread(target=read,args=(self.process,),daemon=True,name='guga-browser-replies').start()

    def execute(self,args,cancel):
        if cancel.is_set():return dict(ok=False,action_sent=False,error='任务已停止')
        if not self.lock.acquire(blocking=False):return dict(ok=False,action_sent=False,error='浏览器正在执行另一个步骤')
        try:
            if args['action']=='session_stop':
                self.close();return dict(ok=True,verified=True,active=False)
            active=bool(self.process and self.process.poll() is None)
            if args['action']=='session_status':return dict(ok=True,active=active,owned_browser=True,idle_timeout_seconds=300)
            if not active:
                if args['action']!='session_start':return dict(ok=False,action_sent=False,error='专用浏览器会话未启动或已超时；先session_start')
                self.close();self._launch()
            request=uuid.uuid4().hex
            self.process.stdin.write(json.dumps(dict(id=request,args=args),ensure_ascii=False)+'\n');self.process.stdin.flush()
            deadline=time.monotonic()+35
            while time.monotonic()<deadline and not cancel.is_set():
                try:response=self.responses.get(timeout=.1)
                except queue.Empty:continue
                if response is None:break
                if response.get('id')==request:return response['result']
            self.close()
            return dict(ok=False,action_sent=None,code='browser_worker_stopped',
                error='浏览器步骤取消、超时或异常退出，已清理专用浏览器。副作用结果未知，禁止自动重放提交或发送')
        except (OSError,ValueError) as exc:
            self.close();return dict(ok=False,action_sent=None,error='专用浏览器通信失败：'+type(exc).__name__+'；先核对外部结果，不要重放提交')
        finally:self.lock.release()


class BrowserWorker:
    def __init__(self):
        self.playwright=None;self.context=None;self.pages={};self.refs={};self.observation=None
        self.receipts={};self.sent=False

    def close(self):
        if self.context:self.context.close()
        if self.playwright:self.playwright.stop()
        self.context=None;self.playwright=None

    def page_list(self):
        current=self.context.pages
        self.pages={key:page for key,page in self.pages.items() if page in current and not page.is_closed()}
        for page in current:
            if page not in self.pages.values():self.pages[uuid.uuid4().hex[:12]]=page
        return [dict(page_id=key,url=page.url,title=page.title()[:180]) for key,page in self.pages.items()]

    def page(self,args):
        self.page_list()
        if args.get('page_id') not in self.pages:raise ValueError('page_id不匹配；先tabs选择目标，不猜当前标签')
        return self.pages[args['page_id']]

    def snapshot(self,page):
        self.page_list();page_id=next(key for key,p in self.pages.items() if p is page)
        token=uuid.uuid4().hex;self.refs={};controls=[]
        for frame_index,frame in enumerate(page.frames[:12]):
            for element in frame.query_selector_all('input:not([type=hidden]),textarea,select,button,a[href],[role=button],[role=checkbox],[role=combobox],[contenteditable=true]')[:220]:
                if len(controls)>=180:break
                if not element.is_visible():continue
                metadata=element.evaluate('''el => ({tag:el.tagName.toLowerCase(),type:el.type||'',role:el.getAttribute('role')||'',
                    label:el.getAttribute('aria-label')||(el.labels&&Array.from(el.labels).map(x=>{const copy=x.cloneNode(true);copy.querySelectorAll('input,select,textarea,button').forEach(n=>n.remove());return copy.textContent.trim()}).join(' '))||el.getAttribute('placeholder')||el.innerText||el.name||'',
                    value:el.type==='password'?'[redacted]':(el.value||''),checked:!!el.checked,
                    options:el.tagName==='SELECT'?Array.from(el.options).map(o=>({label:o.label,value:o.value})):undefined})''')
                ref='e'+str(len(controls)+1);self.refs[ref]=element
                metadata['label']=metadata['label'][:180];metadata['value']=metadata['value'][:400]
                if metadata.get('options'):metadata['options']=metadata['options'][:80]
                controls.append(dict(ref=ref,frame=frame_index,**metadata))
        text=page.locator('body').inner_text(timeout=3000)[:10000]
        self.observation=dict(id=token,page_id=page_id,url=page.url,time=time.monotonic())
        return dict(ok=True,page_id=page_id,url=page.url,title=page.title(),snapshot_id=token,
            controls=controls,text=text,frames=[dict(index=i,url=f.url) for i,f in enumerate(page.frames[:12])],
            untrusted_page_data=True,owned_browser=True)

    def targets(self,page,args,refs):
        observed=self.observation or {}
        if (args.get('snapshot_id')!=observed.get('id') or args.get('page_id')!=observed.get('page_id') or
            page.url!=observed.get('url') or time.monotonic()-observed.get('time',0)>60):
            raise ValueError('页面观察已消耗、过期或标签不匹配；先snapshot，不发送输入')
        result=[]
        for ref in refs:
            element=self.refs.get(ref)
            if element is None or not element.evaluate('el=>el.isConnected') or not element.is_visible():
                raise ValueError('控件已变化或ref未知；先snapshot，不发送输入')
            result.append(element)
        self.observation=None
        return result

    def execute(self,args):
        from core.browser_agent import validate_url
        self.sent=False;action=args['action'];key=args.get('operation_id');signature=None;completed=[]
        if action in ('click','press','upload','download'):
            if not isinstance(key,str) or not 1<=len(key)<=100:raise ValueError('此操作需要唯一operation_id；重试沿用同一ID防止重复提交')
            signature=hashlib.sha256(json.dumps({k:v for k,v in args.items() if k!='snapshot_id'},sort_keys=True).encode()).hexdigest()
            if key in self.receipts:
                old_signature,result=self.receipts[key]
                if signature!=old_signature:raise ValueError('operation_id已用于不同操作')
                return dict(result,duplicate_suppressed=True)
            if len(self.receipts)>=500:raise ValueError('会话操作记录达到500条上限；结束会话后再开始新任务')
        try:
            if action=='session_start':
                if self.context:return dict(ok=True,already_active=True,tabs=self.page_list())
                from playwright.sync_api import sync_playwright
                self.playwright=sync_playwright().start()
                profile=Path(os.environ.get('GUGA_BROWSER_PROFILE_DIR',str(ROOT/'runtime/browser-profile')))
                profile.mkdir(parents=True,exist_ok=True)
                launch_options=dict(headless=bool(args.get('headless',False)),accept_downloads=True,
                    viewport=dict(width=1280,height=900),timeout=15000)
                try:
                    self.context=self.playwright.chromium.launch_persistent_context(str(profile),**launch_options)
                except Exception as chromium_error:
                    # Public installs may not be able to download Playwright's
                    # bundled Chromium. Windows ships Edge with the Chromium
                    # DevTools protocol, so keep the owned-browser feature usable.
                    if os.name!='nt':raise
                    try:
                        self.context=self.playwright.chromium.launch_persistent_context(
                            str(profile),channel='msedge',**launch_options)
                    except Exception as edge_error:
                        raise RuntimeError(
                            'Playwright Chromium不可用，Microsoft Edge回退也失败；'
                            '请确认Edge已安装并可正常启动') from edge_error
                self.context.set_default_timeout(4000);self.context.set_default_navigation_timeout(12000)
                self.context.on('dialog',lambda dialog:dialog.dismiss())
                page=self.context.pages[0] if self.context.pages else self.context.new_page()
                if args.get('url'):page.goto(validate_url(args['url']),wait_until='domcontentloaded')
                return self.snapshot(page)
            if not self.context:raise ValueError('专用浏览器未启动')
            if action=='tabs':return dict(ok=True,tabs=self.page_list())
            if action=='new_tab':
                url=validate_url(args['url']);page=self.context.new_page();page.goto(url,wait_until='domcontentloaded')
                return self.snapshot(page)
            page=self.page(args)
            if action=='snapshot':return self.snapshot(page)
            if action=='navigate':
                self.observation=None;self.sent=True;page.goto(validate_url(args['url']),wait_until='domcontentloaded')
                return self.snapshot(page)
            if action=='wait':
                page.wait_for_timeout(max(0,min(3000,int(args.get('milliseconds',500)))))
                return self.snapshot(page)
            fields=args.get('fields',[])
            if action=='fill_form':
                if not isinstance(fields,list) or not 1<=len(fields)<=30:raise ValueError('fields需要1到30个字段')
                for field in fields:
                    if field.get('kind') not in ('text','select','check'):raise ValueError('字段kind应为text/select/check')
                    if field['kind']=='check' and type(field.get('checked')) is not bool:raise ValueError('check字段需要checked布尔值')
                    if field['kind']!='check' and not isinstance(field.get('value'),str):raise ValueError('字段需要字符串value')
                if len({f.get('ref') for f in fields})!=len(fields):raise ValueError('同批字段ref不能重复')
                targets=self.targets(page,args,[f.get('ref') for f in fields]);completed=[]
                for field,element in zip(fields,targets):
                    self.sent=True;kind=field['kind']
                    if kind=='text':
                        element.fill(field['value'])
                        actual=element.input_value() if element.evaluate('el=>"value" in el') else element.inner_text()
                        if actual!=field['value']:raise RuntimeError('文字字段回读不一致')
                    elif kind=='select':
                        element.select_option(label=field['value'])
                        if element.eval_on_selector('option:checked','el=>el.label')!=field['value']:raise RuntimeError('下拉框回读不一致')
                    else:
                        element.set_checked(field['checked'])
                        if element.is_checked()!=field['checked']:raise RuntimeError('勾选状态回读不一致')
                    completed.append(dict(ref=field['ref'],kind=kind,verified=True))
                return dict(self.snapshot(page),verified=True,action_sent=True,fields_verified=completed)
            if action=='scroll':
                self.targets(page,args,[]);self.sent=True
                page.mouse.wheel(0,max(-2000,min(2000,int(args.get('delta',600)))))
                return dict(self.snapshot(page),action_sent=True)
            element=self.targets(page,args,[args.get('ref')])[0]
            if action=='click':self.sent=True;element.click()
            elif action=='press':
                keys=str(args.get('keys',''))
                if len(keys)>60:raise ValueError('按键过长')
                self.sent=True;element.press(keys)
            elif action=='upload':
                path=Path(args['path']).resolve()
                if not path.is_file():raise ValueError('上传文件不存在，尚未发送')
                self.sent=True;element.set_input_files(str(path))
            elif action=='download':
                self.sent=True
                with page.expect_download(timeout=10000) as download:element.click()
                item=download.value;directory=ROOT/'runtime/browser-downloads';directory.mkdir(parents=True,exist_ok=True)
                path=directory/(uuid.uuid4().hex[:8]+'-'+Path(item.suggested_filename).name)
                item.save_as(str(path))
                result=dict(self.snapshot(page),action_sent=True,verified=True,download_path=str(path),bytes=path.stat().st_size)
                if signature:self.receipts[key]=(signature,result)
                return result
            else:raise ValueError('未知专用浏览器操作')
            result=dict(self.snapshot(page),action_sent=True,verified=False,
                verification='操作后已观察页面；点击/按键不代表业务提交成功，按页面文本与字段继续核验')
        except Exception as exc:
            from core.desktop_access import redact
            result=dict(ok=False,action_sent=self.sent,verified=False,error=redact(type(exc).__name__+': '+str(exc))[:500],
                completed_fields=completed,
                next_action='snapshot',retry_policy='observe_before_retry_never_replay_submission')
        if signature and self.sent:self.receipts[key]=(signature,result)
        return result


def serve(parent_pid):
    import psutil
    requests=queue.Queue();worker=BrowserWorker()
    def read():
        for line in sys.stdin:
            try:requests.put(json.loads(line))
            except ValueError:pass
        requests.put(None)
    threading.Thread(target=read,daemon=True).start();last=time.monotonic()
    try:
        while psutil.pid_exists(parent_pid) and time.monotonic()-last<300:
            try:request=requests.get(timeout=1)
            except queue.Empty:continue
            if request is None:break
            last=time.monotonic()
            try:result=worker.execute(request['args'])
            except Exception as exc:result=dict(ok=False,action_sent=False,error=type(exc).__name__+': '+str(exc)[:240])
            print(json.dumps(dict(id=request['id'],result=result),ensure_ascii=False),flush=True)
    finally:worker.close()


if __name__=='__main__':serve(int(sys.argv[2]))
