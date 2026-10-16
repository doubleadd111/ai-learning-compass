"""Today plan filters are presentation-only and retain the existing due scope."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date as real_date
from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtWidgets import QApplication

import desktop.window as window_module
from desktop.controls import SoftComboBox
from desktop.credentials import CredentialStore
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.window import MainWindow
from tests.test_desktop_store import create, edit
from tests.test_desktop_wheel_scope import arrow, wheel_at


@pytest.fixture
def ui(qtbot, tmp_path, monkeypatch):
    class FixedDate(real_date):
        @classmethod
        def today(cls):
            return cls(2026, 10, 16)
    monkeypatch.setattr(window_module, "date", FixedDate)
    original_font = QFont(QApplication.instance().font())
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "msyh.ttc"
    font_id = (QFontDatabase.addApplicationFont(str(font))
               if font.exists() and "Microsoft YaHei UI" not in QFontDatabase.families() else -1)
    apply_theme(QApplication.instance(), "light")
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.show()
    yield window
    window.exiting = True
    window.close()
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)
    QApplication.instance().setFont(original_font)


def ids(ui):
    return [ui.today_tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)
            for i in range(ui.today_tree.topLevelItemCount())]


def select(ui, plan_id):
    index = ui.today_plan_filter.findData(plan_id)
    assert index >= 0
    ui.today_plan_filter.setCurrentIndex(index)


def test_all_and_each_plan_keep_due_scope_and_global_stats(ui):
    first, second, archived = [create(ui.store) for _ in range(3)]
    ui.store.rename_plan(first, "Python 入门")
    ui.store.rename_plan(second, "英语练习")
    ui.store.archive(archived)
    edit(ui.store, ui.store.tasks(first)[0], status="已完成")
    ui.refresh()
    summary = ui.today_summary.text()
    trail = [count.text() for _, count, _ in ui.trail_days]
    expected = {t["id"] for t in ui.store.tasks(due="2026-10-16")}
    assert ui.today_plan_filter.currentText() == "全部任务"
    assert set(ids(ui)) == expected and len(expected) == 3
    assert ui.today_plan_filter.findData(archived) == -1
    for pid in (first, second):
        select(ui, pid)
        assert ids(ui) == [t["id"] for t in ui.store.tasks(pid, due="2026-10-16")]
        assert ui.today_summary.text() == summary
        assert [count.text() for _, count, _ in ui.trail_days] == trail
    select(ui, None)
    assert set(ids(ui)) == expected


def test_same_named_plans_filter_by_id_without_writing_data(ui):
    first, second = create(ui.store), create(ui.store)
    ui.refresh()
    before = ui.store.read("SELECT * FROM plans ORDER BY id"), ui.store.read("SELECT * FROM tasks ORDER BY id")
    assert ui.today_plan_filter.itemText(1) == ui.today_plan_filter.itemText(2)
    select(ui, second)
    assert set(ids(ui)) == {t["id"] for t in ui.store.tasks(second, due="2026-10-16")}
    assert not set(ids(ui)) & {t["id"] for t in ui.store.tasks(first)}
    assert before == (ui.store.read("SELECT * FROM plans ORDER BY id"), ui.store.read("SELECT * FROM tasks ORDER BY id"))


def test_refresh_rename_and_new_plan_preserve_filter_and_task_selection(ui):
    pid = create(ui.store)
    ui.refresh()
    select(ui, pid)
    item = ui.today_tree.topLevelItem(0)
    task_id = item.data(0, Qt.ItemDataRole.UserRole)
    ui.today_tree.setCurrentItem(item)
    ui.store.rename_plan(pid, "重命名后的学习计划")
    create(ui.store)
    ui.refresh()
    assert ui.today_plan_filter.currentData() == pid
    assert ui.today_plan_filter.currentText() == "重命名后的学习计划"
    assert ui.today_tree.currentItem().data(0, Qt.ItemDataRole.UserRole) == task_id


@pytest.mark.parametrize("operation", ["archive", "delete"])
def test_missing_plan_falls_back_to_all(ui, operation):
    removed, kept = create(ui.store), create(ui.store)
    ui.refresh()
    select(ui, removed)
    if operation == "archive":
        ui.store.archive(removed)
    else:
        ui.store.delete_plan(removed)
    ui.refresh()
    assert ui.today_plan_filter.currentData() is None
    assert ui.today_plan_filter.findData(removed) == -1
    assert ids(ui) == [t["id"] for t in ui.store.tasks(kept, due="2026-10-16")]


def test_empty_plan_message_and_restored_archived_option(ui):
    pid = create(ui.store)
    for task in ui.store.tasks(pid, due="2026-10-16"):
        edit(ui.store, task, status="已完成")
    ui.refresh()
    select(ui, pid)
    assert not ids(ui) and ui.today_empty.isVisible()
    assert "该计划没有今日或逾期" in ui.today_empty.text()
    ui.store.archive(pid)
    ui.refresh()
    ui.store.archive(pid, False)
    ui.refresh()
    assert ui.today_plan_filter.findData(pid) > 0
    assert ui.today_plan_filter.currentData() is None


def test_filtered_focus_and_edit_receive_correct_task_id(ui, qtbot, monkeypatch):
    create(ui.store)
    pid = create(ui.store)
    ui.refresh()
    select(ui, pid)
    first = ui.today_tree.topLevelItem(0)
    expected = first.data(0, Qt.ItemDataRole.UserRole)
    captured = []
    monkeypatch.setattr(ui, "start_focus_for_task", captured.append)
    ui.refresh_today_tasks()
    first = ui.today_tree.topLevelItem(0)
    qtbot.mouseClick(ui.today_tree.itemWidget(first, 4), Qt.MouseButton.LeftButton)
    assert captured == [expected]
    def complete():
        dialog = QApplication.activeModalWidget()
        dialog.status.setCurrentText("已完成")
        dialog.note.setPlainText("从筛选列表填写记录")
        dialog.accept()
    QTimer.singleShot(0, complete)
    ui.edit_tree_task(first)
    assert ui.today_plan_filter.currentData() == pid and expected not in ids(ui)
    record = next(t for t in ui.store.tasks(pid) if t["id"] == expected)
    assert record["status"] == "已完成" and record["note"] == "从筛选列表填写记录"


def test_no_plans_default_and_new_plan_added_without_restart(ui):
    assert ui.today_plan_filter.count() == 1
    assert ui.today_empty.isVisible() and ui.sample_button.isVisible()
    pid = create(ui.store)
    ui.refresh()
    assert ui.today_plan_filter.count() == 2
    assert ui.today_plan_filter.findData(pid) > 0
    assert ids(ui) and not ui.today_empty.isVisible()


def test_keyboard_can_select_plan_and_return_all(ui, qtbot):
    from PySide6.QtTest import QTest
    create(ui.store)
    ui.refresh()
    field = ui.today_plan_filter
    field.setFocus()
    QTest.keyClick(field, Qt.Key.Key_Down)
    assert field.currentData() is not None and ids(ui)
    QTest.keyClick(field, Qt.Key.Key_Home)
    assert field.currentData() is None


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_filter_layout_and_arrow_only_wheel(ui, qtbot, theme, tmp_path):
    create(ui.store)
    pid = create(ui.store)
    ui.store.rename_plan(pid, "这是一份名称很长但不应该挤出小窗口的学习计划" * 3)
    ui.refresh()
    apply_theme(QApplication.instance(), theme)
    ui.resize(980, 680)
    qtbot.wait(50)
    field = ui.today_plan_filter
    assert isinstance(field, SoftComboBox)
    assert ui.rect().contains(field.mapTo(ui, field.rect().topRight()))
    assert ui.rect().contains(field.mapTo(ui, field.rect().bottomLeft()))
    wheel_at(field, field.rect().center())
    assert field.currentData() is None
    wheel_at(field, arrow(field).center())
    assert field.currentData() is not None
    folder = tmp_path / f"today-filter-{theme}.png"
    assert ui.grab().save(str(folder))
