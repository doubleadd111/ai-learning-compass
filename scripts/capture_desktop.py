"""在隔离数据目录内生成桌面界面 QA 图，不调用模型。"""
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
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
        for page in range(5):
            window.navigate(page)
            window.show()
            app.processEvents()
            window.grab().save(str(output / f"{theme}-{page}.png"))
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
