"""Early themed feedback; no data or network access."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog,QVBoxLayout,QProgressBar
from desktop.confirmations import text_label


class StartupWindow(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('正在打开学习罗盘');self.setFixedSize(380,160)
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.CustomizeWindowHint | Qt.WindowType.WindowTitleHint)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose,False)
        box=QVBoxLayout(self);box.setContentsMargins(24,20,24,20)
        box.addWidget(text_label('学习罗盘','brand'))
        self.message=text_label('正在打开本机资料，请稍候…','muted');box.addWidget(self.message)
        bar=QProgressBar();bar.setRange(0,0);bar.setTextVisible(False);box.addWidget(bar)
