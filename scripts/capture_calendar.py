"""Calendar popup snapshots using an isolated date field, never user data."""
import os
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from desktop.dialogs import date_edit
from desktop.theme import apply_theme

app = QApplication([])
if sys.platform == "win32":
    for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename))
output = ROOT / ".learning_compass/qa/calendar-2026.10.7"
output.mkdir(parents=True, exist_ok=True)
prefix = "native-" if os.environ.get("QT_QPA_PLATFORM") == "windows" else ""
for theme in ("light", "dark"):
    apply_theme(app, theme)
    host = QWidget()
    host.resize(400, 140)
    layout = QVBoxLayout(host)
    field = date_edit(QDate(2026, 11, 15))
    layout.addWidget(field)
    host.show()
    app.processEvents()
    QTest.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(field.width()-12, field.height()//2))
    app.processEvents()
    calendar = field.calendarWidget()
    assert calendar.window().isVisible() and len(calendar._cells) == 42
    calendar.window().grab().save(str(output / f"{prefix}{theme}.png"))
    calendar.window().hide()
    host.close()
    host.deleteLater()
    app.processEvents()
print(output)
