"""Check painted progress colours, including all production progress widgets."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

import pytest
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication, QProgressBar, QVBoxLayout, QWidget

from desktop.credentials import CredentialStore
from desktop.store import Store
from desktop.theme import DARK, LIGHT, apply_theme
from desktop.window import MainWindow


def colour_at(widget, fraction):
    image = widget.grab().toImage()
    return image.pixelColor(int(image.width() * fraction), image.height() // 2).name()


@pytest.fixture
def screenshot_fonts(qapp):
    """Don't leak temporary screenshot fonts into unrelated layout tests."""
    original_font = QFont(qapp.font())
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
    font_id = (QFontDatabase.addApplicationFont(str(font))
               if font.exists() and "Microsoft YaHei UI" not in QFontDatabase.families() else -1)
    yield
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)
    qapp.setFont(original_font)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("value", [0, 50, 100])
def test_progress_painted_fill_and_track(qtbot, theme, value, tmp_path):
    app = QApplication.instance()
    apply_theme(app, theme)
    colours = DARK if theme == "dark" else LIGHT
    bar = QProgressBar()
    qtbot.addWidget(bar)
    bar.setTextVisible(False)
    bar.setRange(0, 100)
    bar.setValue(value)
    bar.resize(320, 6)
    bar.show()
    app.processEvents()
    assert bar.value() == value and bar.height() == 6
    assert colour_at(bar, .25) == colours["progress_fill" if value else "progress_track"].lower()
    assert colour_at(bar, .75) == colours["progress_fill" if value == 100 else "progress_track"].lower()
    assert colours["progress_fill"] != colours["good"]
    assert bar.grab().save(str(tmp_path / f"progress-{theme}-{value}.png"))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_all_eight_app_bars_share_colour_without_progress_changes(qtbot, theme, tmp_path, screenshot_fonts):
    app = QApplication.instance()
    apply_theme(app, theme)
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.load_sample()
    window.show()
    app.processEvents()
    bars = window.findChildren(QProgressBar)
    assert len(bars) == 8
    before = window.store.read("SELECT * FROM tasks ORDER BY id")
    for page, controls in ((0, [row[2] for row in window.trail_days]), (1, [window.plan_progress])):
        window.navigate(page)
        for bar in controls:
            bar.setValue(50)  # Presentation fixture, never modifies task data.
        app.processEvents()
        for bar in controls:
            assert bar.isVisible() and bar.height() == 6
            assert colour_at(bar, .25) == (DARK if theme == "dark" else LIGHT)["progress_fill"].lower()
        assert window.grab().save(str(tmp_path / f"progress-page-{page}-{theme}.png"))
    assert window.store.read("SELECT * FROM tasks ORDER BY id") == before
    window.exiting = True
    window.close()


def test_runtime_theme_switch_preserves_values_and_status_colour(qtbot):
    app = QApplication.instance()
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    bars = [QProgressBar() for _ in range(3)]
    for bar, value in zip(bars, (0, 50, 100)):
        bar.setRange(0, 100)
        bar.setValue(value)
        bar.setTextVisible(False)
        layout.addWidget(bar)
    host.resize(320, 100)
    host.show()
    for theme, colours in (("light", LIGHT), ("dark", DARK), ("light", LIGHT)):
        apply_theme(app, theme)
        app.processEvents()
        assert [bar.value() for bar in bars] == [0, 50, 100]
        assert colour_at(bars[1], .25) == colours["progress_fill"].lower()
        assert colour_at(bars[1], .75) == colours["progress_track"].lower()
        assert app.property("statusColors")["已完成"] == colours["good"]


def test_progress_palette_has_visible_contrast():
    def luminance(code):
        values = [int(code[i:i+2], 16) / 255 for i in (1, 3, 5)]
        linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in values]
        return sum(v * weight for v, weight in zip(linear, (.2126, .7152, .0722)))
    for colours in (LIGHT, DARK):
        values = sorted([luminance(colours["progress_fill"]), luminance(colours["progress_track"])])
        assert (values[1] + .05) / (values[0] + .05) >= 3
