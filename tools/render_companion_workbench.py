"""Render the real Qt workbench with temporary demo data and no network requests."""
import os
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QT_SCALE_FACTOR']='1'
os.environ['GUGA_SUPER_AGENT']='off'
os.environ['GUGA_LINGSHU']='off'
import sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from launcher import _prepare_qt
_prepare_qt()
from PyQt5.QtCore import QSettings,QTimer
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFontDatabase
from core.chat_workspace import ChatWorkspace
from ui.pet_window import PetWindow
from ui.agent_chat import AgentChatWindow
from ui.companion_settings import CompanionSettings


def main():
    app=QApplication.instance() or QApplication([])
    # Windows' offscreen backend starts with an empty font database. Reuse the
    # OS fonts for faithful previews; never redistribute the font files.
    for name in ('msyh.ttc','msyhbd.ttc','segoeui.ttf'):
        font=Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name
        if font.is_file():QFontDatabase.addApplicationFont(str(font))
    output=ROOT/'runtime/workbench-preview';output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='guga-visual-') as folder:
        QSettings.setDefaultFormat(QSettings.IniFormat);QSettings.setPath(QSettings.IniFormat,QSettings.UserScope,folder)
        with patch('core.keyboard_hook.KeyboardHook.start'),patch('core.reflex.start_foreground_tracker'),patch('ui.tts_engine.TTSEngine.speak'):
            pet=PetWindow();panel=AgentChatWindow(pet,ChatWorkspace(Path(folder)/'chat.db'));pet._agent_panel=panel
            for obj in (pet,panel):
                for timer in obj.findChildren(QTimer):timer.stop()
            pet._anim.stop();panel.proactive.stop();pet.hide()
            panel.store.append(panel.active_id,'user','咕嘎，把常做的本地视频投稿整理成工作流，下次就能直接用了。')
            panel.store.append(panel.active_id,'assistant','好的，饲养员。我会先确认视频、标题和投稿账号，再依照保存的流程处理。\n\n### 这次的流程\n1. 检查文件与视频信息\n2. 准备标题、简介和封面\n3. 上传并核对页面内容\n4. 将最终发布摘要交给你确认\n\n你可以在左侧的 **工作流编排** 中随时修改步骤。')
            panel.render();panel.show();app.processEvents();panel.grab().save(str(output/'chat.png'))
            for ident in ('001','002','003'):
                dialog=CompanionSettings(panel,ident);dialog.show();app.processEvents();dialog.grab().save(str(output/('settings-'+ident+'.png')));dialog.close()
            dialog=CompanionSettings(panel,tab=2);dialog.show();dialog.flows.setCurrentRow(0);app.processEvents();dialog.grab().save(str(output/'workflows.png'));dialog.close()
            panel.shutdown();panel.hide();pet._screen_tools.stop();pet._self_mcp.close();pet._tray.hide()
    print(output)


if __name__=='__main__':main()
