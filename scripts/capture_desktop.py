"""在隔离数据目录内生成桌面界面 QA 图，不调用模型。"""
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget
from PySide6.QtGui import QFontDatabase
from desktop.dialogs import ConnectionDialog, PlanDialog, TaskDialog
from desktop.models import ModelConnection
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.window import MainWindow


app = QApplication([])
if sys.platform == "win32":
    for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf"):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename))
app.setStyle("Fusion")
output = ROOT / ".learning_compass" / "qa" / "screenshots"
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory))
    config = ModelConnection(name="我的 DeepSeek", provider="DeepSeek", base_url="https://api.deepseek.com", model="deepseek-flash", api_type="responses", output_format="json_schema")
    store.save_connection(config)
    window = MainWindow(store, enable_tray=False)
    window.load_sample()
    for theme in ("light", "dark"):
        apply_theme(app, theme)
        window.notice.clear()
        for page in range(5):
            window.navigate(page)
            window.show()
            app.processEvents()
            if app.focusWidget():
                app.focusWidget().clearFocus()
            window.grab().save(str(output / f"{theme}-{page}.png"))
        window.navigate(4)
        hover_button = next(button for button in window.findChildren(QPushButton) if button.text() == "测试连接（使用额度）")
        QApplication.sendEvent(hover_button, QEvent(QEvent.Type.Enter))
        app.processEvents()
        window.grab().save(str(output / f"{theme}-hover.png"))
        QTest.mousePress(hover_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        window.grab().save(str(output / f"{theme}-pressed.png"))
        # Cancel the synthetic press without emitting clicked or calling the API.
        hover_button.setDown(False)
        QApplication.sendEvent(hover_button, QEvent(QEvent.Type.Leave))
        window.resize(980, 680)
        window.navigate(0)
        app.processEvents()
        window.grab().save(str(output / f"{theme}-compact.png"))
        window.resize(1220, 820)
        window.navigate(4)
        tabs = window.findChild(QTabWidget)
        for index, name in ((1, "appearance"), (2, "data")):
            tabs.setCurrentIndex(index)
            app.processEvents()
            window.grab().save(str(output / f"{theme}-{name}.png"))
        tabs.setCurrentIndex(0)
        for name, dialog in (("connection", ConnectionDialog(config, parent=window)),
                             ("create", PlanDialog([config], parent=window)),
                             ("task", TaskDialog(store.tasks()[0], parent=window))):
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(output / f"{theme}-{name}.png"))
            dialog.close()
        gate = threading.Event()
        window.navigate(4)
        window.start_work("正在测试 JSON 连接…", lambda cancel: gate.wait(10), lambda result: None, timeout_seconds=30)
        app.processEvents()
        window.grab().save(str(output / f"{theme}-countdown.png"))
        window.cancel_request()
        gate.set()
    window.exiting = True
    window.close()
print(output)
