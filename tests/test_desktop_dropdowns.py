"""Actual popup painting and input, isolated from APIs and personal data."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QComboBox, QVBoxLayout, QWidget
from desktop.controls import DropdownDelegate, SoftComboBox
from desktop.theme import DARK, LIGHT, apply_theme


@pytest.fixture
def menu(qtbot):
    host = QWidget()
    host.resize(360, 260)
    layout = QVBoxLayout(host)
    combo = SoftComboBox()
    combo.addItems(["全部状态", "未开始", "进行中", "延期", "已完成"])
    layout.addWidget(combo)
    layout.addStretch()
    qtbot.addWidget(host)
    host.show()
    QApplication.processEvents()
    yield host, combo
    combo.hidePopup()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_popup_has_painted_surface_round_corners_and_distinct_hover(menu, qtbot, theme):
    _host, combo = menu
    app = QApplication.instance()
    apply_theme(app, theme)
    combo.showPopup()
    app.processEvents()
    view, popup = combo.view(), combo.view().window()
    colors = LIGHT if theme == "light" else DARK
    image = popup.grab().toImage()
    assert image.pixelColor(popup.width() // 2, 3).name() == colors["card"].lower()
    assert image.pixelColor(0, 0).alpha() == 0
    first = view.visualRect(combo.model().index(0, 0))
    third = view.visualRect(combo.model().index(2, 0))
    qtbot.mouseMove(view.viewport(), first.center())
    app.processEvents()
    normal = view.viewport().grab().toImage().pixelColor(8, third.center().y()).name()
    qtbot.mouseMove(view.viewport(), third.center())
    app.processEvents()
    hovered = view.viewport().grab().toImage()
    assert hovered.pixelColor(8, third.center().y()).name() != normal
    assert hovered.pixelColor(8, first.center().y()).name() == colors["selected"].lower()
    assert combo.currentIndex() == 0
    assert not view.verticalScrollBar().isVisible()
    assert view.viewport().rect().contains(view.visualRect(combo.model().index(4, 0)))
    assert isinstance(combo.itemDelegate(), DropdownDelegate)


def test_mouse_click_commits_once_and_keeps_item_data(menu, qtbot):
    _host, combo = menu
    apply_theme(QApplication.instance(), "light")
    combo.setItemData(2, "stable-status-id")
    spy = QSignalSpy(combo.currentIndexChanged)
    combo.showPopup()
    QApplication.processEvents()
    rect = combo.view().visualRect(combo.model().index(2, 0))
    qtbot.mouseClick(combo.view().viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
    assert combo.currentIndex() == 2 and combo.currentData() == "stable-status-id"
    assert spy.count() == 1 and not combo.view().window().isVisible()


def test_escape_cancels_and_keyboard_enter_accepts(menu, qtbot):
    _host, combo = menu
    apply_theme(QApplication.instance(), "light")
    combo.showPopup()
    qtbot.keyClick(combo.view(), Qt.Key.Key_Down)
    qtbot.keyClick(combo.view(), Qt.Key.Key_Escape)
    assert combo.currentIndex() == 0
    combo.showPopup()
    qtbot.keyClick(combo.view(), Qt.Key.Key_Down)
    qtbot.keyClick(combo.view(), Qt.Key.Key_Return)
    assert combo.currentIndex() == 1


def test_long_list_scroll_editable_text_and_screen_bounds(menu, qtbot):
    host, combo = menu
    app = QApplication.instance()
    apply_theme(app, "dark")
    combo.clear()
    combo.addItems([f"模型 {i} · 很长的自定义名称" for i in range(40)])
    combo.setCurrentIndex(35)
    available = combo.screen().availableGeometry()
    host.move(available.right() - host.width(), available.bottom() - host.height())
    combo.showPopup()
    app.processEvents()
    assert combo.view().verticalScrollBar().isVisible()
    assert combo.view().viewport().rect().intersects(combo.view().visualRect(combo.model().index(35, 0)))
    assert available.contains(combo.view().window().geometry())
    combo.hidePopup()
    combo.setEditable(True)
    combo.setEditText("自己的学习科目")
    assert combo.currentText() == "自己的学习科目"


def test_disabled_option_is_skipped_without_changing_committed_selection(menu, qtbot):
    _host, combo = menu
    apply_theme(QApplication.instance(), "light")
    combo.model().item(1).setEnabled(False)
    combo.showPopup()
    qtbot.keyClick(combo.view(), Qt.Key.Key_Down)
    assert combo.currentIndex() == 0
    qtbot.keyClick(combo.view(), Qt.Key.Key_Return)
    assert combo.currentIndex() == 2


def test_all_app_combo_boxes_use_shared_control(qtbot, tmp_path):
    from desktop.credentials import CredentialStore
    from desktop.dialogs import PlanDialog
    from desktop.store import Store
    from desktop.window import MainWindow
    app = QApplication.instance()
    apply_theme(app, "light")
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    dialog = PlanDialog([])
    qtbot.addWidget(dialog)
    assert all(isinstance(c, SoftComboBox) for parent in (window, dialog) for c in parent.findChildren(QComboBox))
    window.exiting = True
    window.close()
    dialog.close()
