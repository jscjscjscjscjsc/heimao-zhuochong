"""Explicit app grants, or discovered local apps when the user enables full access."""
import json
import os
import subprocess
import time
from itertools import islice
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_ALIASES = {
    '录入主人声音': ROOT/'录入主人声音.bat',
    '录入主人声音脚本': ROOT/'录入主人声音.bat',
    '录入主人声音的脚本': ROOT/'录入主人声音.bat',
    '首次配置': ROOT/'首次配置.bat',
}


class AppResolutionError(ValueError):
    code='app_not_found'


class AuthorizedApps:
    def __init__(self, path=None):
        self.path = Path(path or ROOT / 'runtime/chat/authorized_apps.json')
        from core.desktop_access import DesktopAccess
        self.access = DesktopAccess(self.path.parent/'local_access.json')

    def available(self, query=''):
        entries=dict(self.entries())
        for name,path in SCRIPT_ALIASES.items():
            if path.is_file():entries.setdefault(name,str(path))
        if self.access.enabled:
            roots=[Path(os.environ.get('APPDATA',''))/'Microsoft/Windows/Start Menu/Programs',
                   Path(os.environ.get('PROGRAMDATA','C:/ProgramData'))/'Microsoft/Windows/Start Menu/Programs',
                   Path.home()/'Desktop']
            for index,root in enumerate(roots):
                if not root.is_dir():continue
                # Desktop contains user projects; never recursively walk those.
                paths=root.glob('*.lnk') if index==2 else root.rglob('*.lnk')
                for path in islice(paths,1000):
                    name=path.stem
                    if name in entries and entries[name]!=str(path):name+=' ['+path.parent.name+']'
                    entries.setdefault(name,str(path))
                    if len(entries)>=1000:break
            import winreg
            for hive in (winreg.HKEY_CURRENT_USER,winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(hive,r'SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths') as registry:
                        for i in range(min(500,winreg.QueryInfoKey(registry)[0])):
                            name=winreg.EnumKey(registry,i)
                            try:
                                with winreg.OpenKey(registry,name) as key:
                                    target=os.path.expandvars(winreg.QueryValue(key,None)).strip('"')
                                    if Path(target).is_file():entries.setdefault(Path(name).stem,target)
                            except OSError:pass
                except OSError:pass
        query=str(query).strip().casefold()
        return {name:path for name,path in entries.items() if not query or query in name.casefold()}

    def entries(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}

    def _save(self, entries):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def grant(self, name, path):
        name = name.strip()
        target = Path(path).resolve(strict=True)
        if not name or len(name) > 80:
            raise ValueError('软件名称需要 1—80 个字符')
        if target.suffix.lower() not in ('.exe', '.lnk') or not target.is_file():
            raise ValueError('请选择软件 .exe 或快捷方式 .lnk')
        entries = self.entries()
        entries[name] = str(target)
        self._save(entries)

    def revoke(self, name):
        entries = self.entries()
        entries.pop(name, None)
        self._save(entries)

    def open(self, name):
        # Reuse existing windows even when their title is a document/chat title.
        if self.access.enabled:
            from core.window_inventory import list_windows
            windows=list_windows(name)
            if len(windows)==1:
                from core.computer_control import ComputerControl
                result=ComputerControl(self.access).execute(dict(action='focus',hwnd=windows[0]['hwnd']))
                return dict(name=name,**result,hwnd=windows[0]['hwnd'],reused_existing=True,
                    verified_running=True,launch_requested=False,
                    message='已找到并聚焦现有窗口；继续observe/accessibility核对，不需要重新启动')
            if len(windows)>1:
                return dict(name=name,reused_existing=False,launch_requested=False,selection_required=True,
                    windows=windows,message='找到多个运行中的窗口；按当前任务选择hwnd并focus，不要重复启动')
        from core.fast_commands import BROWSER_NAMES, _open_app
        if name in BROWSER_NAMES:
            return dict(name=name,**_open_app(name))
        entries = self.available()
        canonical=next((key for key in entries if key.casefold()==str(name).casefold()),None)
        if canonical is not None:name=canonical
        if name not in entries:
            target=Path(name).expanduser()
            # Accept the user's natural aliases for the owner enrollment script.
            normalized=str(name).replace(' ','').removesuffix('.bat')
            alias=next((value for key,value in SCRIPT_ALIASES.items() if normalized in (key,key+'脚本',key+'的脚本')),None)
            if alias and alias.is_file():target=alias
            elif not self.access.enabled or not target.is_absolute() or target.suffix.lower() not in ('.exe','.lnk','.bat','.cmd','.ps1'):
                # 报"没找到"就结束，模型只会卡在这里重复试。这里把可选项和正确出路一起给它。
                head=str(name)[:2]
                nearby=[key for key in entries if head and head in key][:6] or list(entries)[:6]
                raise AppResolutionError(
                    '未找到名为“%s”的软件。当前可用的软件有：%s。'
                    '如果用户要打开的是**网站/网页**（B站、豆包、腾讯会议网页版等），请改用 browser 工具，不要用 applications；'
                    '如果是中文别名启动，用 desktop_command；'
                    '只有确定是本机已安装的软件才用 applications。' % (
                        str(name)[:80], '、'.join(nearby) or '（没有已授权软件）'))
        else:
            target = Path(entries[name])
        if not target.is_file():
            raise ValueError('已授权的软件路径已失效，请重新选择')
        suffix=target.suffix.lower()
        flags=subprocess.CREATE_NEW_CONSOLE if os.name=='nt' else 0
        if suffix in ('.bat','.cmd'):
            proc=subprocess.Popen(['cmd.exe','/k','call',str(target)],cwd=str(target.parent),creationflags=flags)
        elif suffix=='.ps1':
            proc=subprocess.Popen(['powershell.exe','-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File',str(target)],
                cwd=str(target.parent),creationflags=flags)
        else:
            os.startfile(str(target));proc=None
        if proc is not None:
            time.sleep(1.2)
            code=proc.poll()
            if code is not None:
                raise RuntimeError(f'脚本启动后立即退出，退出码 {code}；请检查脚本或依赖安装')
            return dict(name=name,path=str(target),launch_requested=True,verified_running=True,pid=proc.pid,
                        message='脚本控制台已启动并在运行')
        return dict(name=name,path=str(target),launch_requested=True,verified_running=False,
                    message='Windows 已接受软件启动请求；仍需观察窗口确认')
