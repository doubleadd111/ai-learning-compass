"""Capture the actual Qt popup surface with synthetic data; zero API calls."""
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPoint
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget
from desktop.controls import SoftComboBox
from desktop.theme import apply_theme

app = QApplication([])
if sys.platform == "win32":
    for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename))
output = ROOT / ".learning_compass/qa/dropdowns-2026.10.6"
output.mkdir(parents=True, exist_ok=True)
prefix = "native-" if os.environ.get("QT_QPA_PLATFORM") == "windows" else ""
for theme in ("light", "dark"):
    apply_theme(app, theme)
    host = QWidget()
    host.resize(400, 280)
    layout = QVBoxLayout(host)
    layout.addWidget(QLabel("状态"))
    combo = SoftComboBox()
    combo.addItems(["全部状态", "未开始", "进行中", "延期", "已完成"])
    layout.addWidget(combo)
    layout.addStretch()
    host.show()
    app.processEvents()
    combo.showPopup()
    view = combo.view()
    rect = view.visualRect(combo.model().index(2, 0))
    QTest.mouseMove(view.viewport(), rect.center())
    app.processEvents()
    view.window().grab().save(str(output / f"{prefix}{theme}-status.png"))
    combo.hidePopup()
    combo.clear()
    combo.addItems(["我的 DeepSeek · deepseek-flash"] + [f"模型连接 {i} · 自定义长名称用于检查列表滚动与文字省略" for i in range(1, 21)])
    combo.setCurrentIndex(12)
    combo.showPopup()
    app.processEvents()
    view.window().grab().save(str(output / f"{prefix}{theme}-models.png"))
    combo.hidePopup()
    host.close()
    host.deleteLater()
    app.processEvents()
print(output)
