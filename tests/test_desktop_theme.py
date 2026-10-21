import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton, QScrollArea, QVBoxLayout, QWidget
from desktop.credentials import CredentialStore
from desktop.dialogs import PlanDialog
from desktop.store import Store
from desktop.theme import DARK, LIGHT, apply_theme
from desktop.window import MainWindow


def surface(widget):
    # Sample away from the border and text, so this checks visible fill, not a
    # property that could be correct while the stylesheet has no visual effect.
    return widget.grab().toImage().pixelColor(12, widget.height() // 2).name()


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("role", ["secondary", "primary", "danger", "nav"])
def test_buttons_have_visible_hover_pressed_focus_and_disabled_states(qtbot, theme, role):
    app = QApplication.instance()
    apply_theme(app, theme)
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    control = QPushButton("测试按钮")
    control.setFixedSize(180, 48)
    control.setObjectName(role)
    layout.addWidget(control)
    host.show()
    app.processEvents()
    app.sendEvent(control, QEvent(QEvent.Type.Leave))
    normal = surface(control)
    qtbot.mouseMove(control, QPoint(80, 20))
    app.sendEvent(control, QEvent(QEvent.Type.Enter))
    app.processEvents()
    hovered = surface(control)
    assert control.property("hovered") is True
    assert control.cursor().shape() == Qt.CursorShape.PointingHandCursor
    assert hovered != normal
    qtbot.mousePress(control, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert surface(control) != hovered
    control.setDown(False)
    app.sendEvent(control, QEvent(QEvent.Type.Leave))
    assert surface(control) == normal
    control.setFocus(Qt.FocusReason.TabFocusReason)
    assert control.hasFocus()
    control.setEnabled(False)
    app.sendEvent(control, QEvent(QEvent.Type.Enter))
    assert control.property("hovered") is False
    assert control.cursor().shape() == Qt.CursorShape.ArrowCursor
    host.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_standard_dialog_buttons_share_hover_feedback(qtbot, theme):
    app = QApplication.instance()
    apply_theme(app, theme)
    dialog = PlanDialog([])
    qtbot.addWidget(dialog)
    dialog.show()
    app.processEvents()
    control = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save)
    app.sendEvent(control, QEvent(QEvent.Type.Leave))
    normal = surface(control)
    app.sendEvent(control, QEvent(QEvent.Type.Enter))
    assert surface(control) != normal
    assert control.cursor().shape() == Qt.CursorShape.PointingHandCursor
    dialog.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("size", [(980, 680), (1220, 820)])
def test_pages_fit_supported_sizes_and_theme_switch_keeps_navigation(qtbot, tmp_path, theme, size):
    app = QApplication.instance()
    apply_theme(app, theme)
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.load_sample()
    window.resize(*size)
    window.show()
    app.processEvents()
    assert (window.width(), window.height()) == size
    for index in range(5):
        window.navigate(index)
        app.processEvents()
        assert sum(button.isChecked() for button in window.nav) == 1
        assert window.nav[index].isChecked()
        assert not window.nav[index].icon().isNull()
        for control in window.stack.currentWidget().findChildren(QPushButton):
            if control.isVisible():
                # The seven-day view intentionally scrolls in a compact window.
                # isVisible() includes children clipped outside its viewport.
                parent = control.parentWidget()
                while parent is not None and not isinstance(parent, QScrollArea):
                    parent = parent.parentWidget()
                if parent is not None and parent.widget().isAncestorOf(control):
                    parent.ensureWidgetVisible(control, 0, 0)
                    app.processEvents()
                # Index-widget buttons are visible Qt children even when their
                # table rows are clipped. Verify reachability after real scrolling.
                from PySide6.QtWidgets import QTreeWidget
                table = control.parentWidget()
                while table is not None and not isinstance(table, QTreeWidget):
                    table = table.parentWidget()
                if table is not None:
                    for row in range(table.topLevelItemCount()):
                        item = table.topLevelItem(row)
                        if any(table.itemWidget(item, col) is control for col in range(table.columnCount())):
                            table.scrollToItem(item)
                            # Compact task tables keep the task title readable;
                            # the action column is reachable by horizontal scroll.
                            table.horizontalScrollBar().setValue(table.horizontalScrollBar().maximum())
                            app.processEvents()
                corner = control.mapTo(window, control.rect().bottomRight())
                assert 0 <= corner.x() < window.width(), control.text()
                assert 0 <= corner.y() < window.height(), control.text()
    colors = DARK if theme == "dark" else LIGHT
    assert app.palette().window().color().name() == colors["bg"].lower()
    window.exiting = True
    window.close()


def test_theme_assets_are_packaged_and_included_in_source_digest(monkeypatch):
    from pathlib import Path
    from scripts import build_desktop
    root = Path(__file__).resolve().parents[1]
    assert "desktop/assets" in (root / "packaging/desktop.spec").read_text(encoding="utf-8")
    for filename in ("check.svg", "chevron-light.svg", "chevron-dark.svg", "up-light.svg", "up-dark.svg"):
        assert (root / "desktop/assets" / filename).is_file()
    original = Path.read_bytes
    before = build_desktop.source_digest()
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + (b"\n" if path.name == "check.svg" else b""))
    assert build_desktop.source_digest() != before


def test_keyboard_navigation_and_runtime_theme_switch_preserve_state(qtbot, tmp_path):
    app = QApplication.instance()
    apply_theme(app, "light")
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.load_sample()
    window.show()
    app.processEvents()
    window.nav[3].setFocus(Qt.FocusReason.TabFocusReason)
    qtbot.keyClick(window.nav[3], Qt.Key.Key_Space)
    assert window.stack.currentIndex() == 3 and window.nav[3].isChecked()
    selected_plan = window.selected_plan_id
    icon_key = window.nav[3].icon().cacheKey()
    apply_theme(app, "dark")
    assert window.nav[3].icon().cacheKey() != icon_key
    assert window.nav[3].isChecked() and window.stack.currentIndex() == 3
    assert window.selected_plan_id == selected_plan
    assert len(window.store.tasks()) == 6
    apply_theme(app, "light")
    window.exiting = True
    window.close()
