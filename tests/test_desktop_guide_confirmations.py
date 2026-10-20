"""Close/reopen regression and the shared Chinese confirmation visual contract."""
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt, QLocale
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QPushButton, QWidget
from shiboken6 import isValid

from desktop.theme import apply_theme
from desktop.guide import open_guide, close_guide
from desktop.guide_check import native_close, check_help_lifecycle
from desktop.confirmations import CapacityConfirmDialog
from desktop.scheduling import PlanCapacityExceeded, BudgetExceeded
from desktop.store import Store
from desktop.window import MainWindow
from desktop.credentials import CredentialStore


@pytest.fixture(autouse=True)
def themed(qapp):
    apply_theme(qapp, "light")
    yield
    close_guide()
    qapp.processEvents()


def test_actual_help_buttons_close_source_destroy_then_reopen(qtbot, tmp_path):
    store = Store(tmp_path)
    store.set_setting("reminders_paused", True)
    window = MainWindow(store, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.show()
    try:
        assert check_help_lifecycle(window)["success"]
    finally:
        window.exiting = True
        window.close()


def test_main_guide_close_reopens_reading_state_and_clears_alias(qtbot):
    host = QWidget()
    qtbot.addWidget(host)
    host.show()
    first = open_guide(host, "modify")
    first.search.setText("修改")
    chapter = first.toc.currentItem().text()
    native_close(first)
    QApplication.processEvents()
    assert not getattr(host, "_guide_window", None)
    assert not getattr(QApplication.instance(), "_learning_guide", None)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not isValid(first)
    second = open_guide(host)
    assert second is not first and second.isVisible()
    assert second.search.text() == "修改" and second.toc.currentItem().text() == chapter
    assert open_guide(host) is second


def example_error(combined=False):
    row = dict(plan_id="fake", title="两周 Python 起步样例", kind="周", period="2026-10-19", total=150, limit=135, over=15)
    day = dict(row, kind="日", period="2026-10-20", total=65, limit=45, over=20)
    global_row = dict(date="2026-10-20", total=90, budget=60, over=30,
        plans={"fake": dict(title="两周 Python 起步样例", minutes=65), "other": dict(title="英语练习", minutes=25)})
    return PlanCapacityExceeded("fake", [row, day], [global_row] if combined else [])


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("combined", [False, True])
def test_confirmation_cards_chinese_and_small_window(qtbot, theme, combined):
    apply_theme(QApplication.instance(), theme)
    d = CapacityConfirmDialog(example_error(combined))
    qtbot.addWidget(d)
    d.resize(500, 450)
    d.show()
    QApplication.processEvents()
    assert len(d.cards) == (3 if combined else 2)
    assert d.cancel_button.text() == "返回调整" and d.confirm_button.text() == "仍然保留安排"
    assert d.cancel_button.isDefault() and not d.confirm_button.isDefault()
    all_text = "\n".join(w.text() for w in d.findChildren(QLabel))
    assert "150 分钟" in all_text and "135 分钟" in all_text and "20 分钟" in all_text
    assert "2026-10-19 至 2026-10-25" in all_text
    assert not any("Cancel" in b.text() or "Details" in b.text() for b in d.findChildren(QPushButton))
    for b in (d.cancel_button, d.confirm_button):
        assert d.rect().contains(b.mapTo(d, b.rect().bottomRight()))
    if combined:
        assert "英语练习：25 分钟" in all_text


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Escape])
def test_confirmation_default_keyboard_does_not_approve(qtbot, key):
    d = CapacityConfirmDialog(example_error())
    qtbot.addWidget(d)
    d.show()
    with qtbot.waitSignal(d.rejected):
        qtbot.keyClick(d.cancel_button, key)
    assert d.result() == QDialog.DialogCode.Rejected


def test_global_budget_only_confirmation(qtbot):
    error = BudgetExceeded("fake", [dict(date="2026-10-20", total=50, budget=20, over=30,
        plans={"fake": dict(title="练习计划", minutes=50)})])
    d = CapacityConfirmDialog(error)
    qtbot.addWidget(d)
    assert len(d.cards) == 1
    assert "练习计划：50 分钟" in "\n".join(w.text() for w in d.findChildren(QLabel))


def test_qt_standard_buttons_and_detail_toggle_are_chinese(qtbot):
    previous = QLocale()
    QLocale.setDefault(QLocale(QLocale.Language.English))
    try:
        d = QMessageBox()
        qtbot.addWidget(d)
        d.setText("测试")
        d.setDetailedText("详情")
        d.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes)
        d.show()
        QApplication.processEvents()
        labels = {b.text() for b in d.findChildren(QPushButton)}
        assert {"确定", "取消", "是", "展开详情"} <= labels
        button = next(b for b in d.findChildren(QPushButton) if b.text() == "展开详情")
        qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
        assert button.text() == "收起详情"
    finally:
        QLocale.setDefault(previous)
