"""Shared artwork and visual tokens for the desktop companion workbench."""
from pathlib import Path
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon,QPixmap,QPainter,QColor
from PyQt5.QtWidgets import QLabel,QSizePolicy

ROOT=Path(__file__).resolve().parents[1]
ARTWORK={
    '001':ROOT/'resources/expressions/happy_transparent.png',
    '002':ROOT/'resources/multiagent/dafish/v2/normal.png',
    '003':ROOT/'resources/multiagent/dora/v2/happy.png',
}
ACCENTS={'001':'#e9efff','002':'#e5f4fa','003':'#fff0df'}


class ElidedLabel(QLabel):
    def __init__(self,text='',parent=None):
        super().__init__(text,parent);self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)

    def paintEvent(self,event):
        painter=QPainter(self);painter.setFont(self.font());painter.setPen(self.palette().windowText().color())
        painter.drawText(self.contentsRect(),Qt.AlignLeft|Qt.AlignVCenter,
                         self.fontMetrics().elidedText(self.text(),Qt.ElideRight,self.contentsRect().width()))


def member_icon(agent_id,size=64):
    pix=QPixmap(size*2,size*2);pix.fill(Qt.transparent)
    painter=QPainter(pix);painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen);painter.setBrush(QColor(ACCENTS.get(str(agent_id),'#edf0f8')))
    painter.drawRoundedRect(0,0,size*2,size*2,round(size*.48),round(size*.48))
    art=QPixmap(str(ARTWORK.get(str(agent_id),ARTWORK['001'])))
    if not art.isNull():
        art=art.scaled(int(size*1.8),int(size*1.8),Qt.KeepAspectRatio,Qt.SmoothTransformation)
        painter.drawPixmap((size*2-art.width())//2,(size*2-art.height())//2,art)
    painter.end();pix.setDevicePixelRatio(2)
    return QIcon(pix)


SETTINGS_STYLE='''
QDialog {background:#f7f8fc;color:#25314c;font:13px "Microsoft YaHei UI";}
QLabel#PageTitle {font-size:22px;font-weight:700;color:#202b45;}
QLabel#SectionTitle {font-size:14px;font-weight:600;color:#293650;}
QLabel#Muted {color:#7a8599;font-size:12px;}
QTabWidget::pane {border:1px solid #e4e9f3;border-radius:12px;background:white;}
QTabBar::tab {padding:12px 18px;color:#7a8599;border:0;}
QTabBar::tab:selected {color:#4866d9;border-bottom:2px solid #5673e8;}
QPlainTextEdit,QLineEdit,QListWidget,QComboBox {background:white;border:1px solid #e0e6f0;border-radius:8px;padding:8px;color:#2d3850;selection-background-color:#e6ecff;}
QPlainTextEdit:focus,QLineEdit:focus {border:1px solid #8fa3f2;}
QPushButton {background:#edf1fa;color:#435373;border:0;border-radius:8px;padding:9px 14px;}
QPushButton:hover {background:#e1e8fa;}
QPushButton#Primary {background:#506ce4;color:white;}
QPushButton#Primary:hover {background:#425ed1;}
QPushButton:disabled {color:#a3aaba;background:#f1f3f7;}
QListWidget::item {padding:9px;border-radius:6px;}
QListWidget::item:selected {background:#edf1ff;color:#435dcc;}
QScrollBar:vertical {width:6px;background:transparent;}
QScrollBar::handle:vertical {background:#d8deeb;border-radius:3px;min-height:24px;}
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical {height:0;}
'''
