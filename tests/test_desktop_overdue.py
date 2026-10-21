"""Overdue is a date-derived display/filter, not a persisted task status."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import date as RealDate, timedelta

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QApplication, QLabel

import desktop.window as main_module
import desktop.week_view as week_module
from desktop.credentials import CredentialStore
from desktop.store import Store
from desktop.task_status import overdue_days, display_status, matches_status
from desktop.task_style import OVERDUE_ROLE, TaskItemDelegate
from desktop.theme import DARK, LIGHT, apply_theme
from desktop.window import MainWindow
from tests.test_desktop_store import create, edit


@pytest.mark.parametrize("status", ["未开始", "进行中", "延期", "已完成"])
@pytest.mark.parametrize("offset", [-3, 0, 1])
def test_overdue_date_boundary_and_original_status(status, offset):
    today = RealDate(2026, 10, 16)
    row = dict(status=status, due_date=(today + timedelta(days=offset)).isoformat(), archived=0)
    expected = 3 if offset == -3 and status != "已完成" else 0
    assert overdue_days(row, today) == expected
    assert matches_status(row, "逾期", today) == bool(expected)
    assert matches_status(row, status, today)
    assert display_status(row, today) == (f"逾期 · {status}" if expected else status)
    row["archived"] = 1
    assert overdue_days(row, today) == 0


def test_cross_year_and_leap_day():
    row = dict(status="未开始", due_date="2024-02-28")
    assert overdue_days(row, RealDate(2024, 3, 1)) == 2
    row["due_date"] = "2025-12-31"
    assert overdue_days(row, RealDate(2026, 1, 1)) == 1


@pytest.fixture
def ui(qtbot, tmp_path, monkeypatch):
    clock = [RealDate(2026, 10, 16)]
    class FixedDate(RealDate):
        @classmethod
        def today(cls):
            return cls(clock[0].year, clock[0].month, clock[0].day)
    monkeypatch.setattr(main_module, "date", FixedDate)
    monkeypatch.setattr(week_module, "date", FixedDate)
    apply_theme(QApplication.instance(), "light")
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.show()
    pid = create(window.store)
    rows = window.store.tasks(pid)
    # Two past dates, one today, one future; preserve the original 2-day budget.
    for row, day in zip(rows, (12, 14, 16, 19)):
        edit(window.store, row, due_date=f"2026-10-{day}",
             payload=row["task"].model_copy(update={"duration_minutes": 15}))
    window.selected_plan_id = pid  # Select the plan, not the new fixed inbox.
    window.refresh()
    yield window, pid, clock
    window.exiting = True
    window.close()


def table_items(tree):
    return [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]


def test_today_library_and_week_share_badges_and_do_not_write_status(ui):
    window, pid, _ = ui
    before = window.store.read("SELECT * FROM tasks ORDER BY id")
    window.refresh()
    assert [item.data(0, OVERDUE_ROLE) for item in table_items(window.today_tree)] == [4, 2, 0]
    assert [item.data(0, OVERDUE_ROLE) for item in table_items(window.plan_tree)] == [4, 2, 0, 0]
    assert window.today_tree.topLevelItem(0).text(3) == "逾期 · 未开始"
    assert isinstance(window.today_tree.itemDelegate(), TaskItemDelegate)
    panel = window.week_panel
    panel.refresh(RealDate(2026, 10, 12))
    assert [item.data(0, OVERDUE_ROLE) for item in table_items(panel.table)] == [4, 2, 0]
    assert [card.property("overdue") for card in panel.cards] == [True, True, False]
    assert "逾期 2 项" in panel.summary.text()
    assert window.store.read("SELECT * FROM tasks ORDER BY id") == before


def test_overdue_filter_intersection_both_modes_and_original_filters(ui):
    window, pid, _ = ui
    other = create(window.store)
    panel = window.week_panel
    panel.refresh(RealDate(2026, 10, 12))
    panel.plan_filter.setCurrentIndex(panel.plan_filter.findData(pid))
    panel.status_filter.setCurrentText("逾期")
    assert len(panel.rows) == 2 and all(r["plan_id"] == pid for r in panel.rows)
    ids = {r["id"] for r in panel.rows}
    panel.select_task(panel.rows[0]["id"])
    panel.mode.setCurrentIndex(1)
    assert {item.data(0, Qt.ItemDataRole.UserRole) for item in table_items(panel.table)} == ids
    assert {card.task_id for card in panel.cards} == ids
    assert "逾期 4 天" in panel.detail.toPlainText()
    panel.status_filter.setCurrentText("未开始")
    assert len(panel.rows) == 3  # Unchanged base status still includes overdue.
    panel.status_filter.setCurrentText("逾期")
    panel.refresh(RealDate(2026, 11, 2))
    assert not panel.rows  # Overdue filter is scoped to the displayed week.
    assert panel.status_filter.currentText() == "逾期"
    window.store.archive(other)
    panel.refresh(RealDate(2026, 10, 12))
    assert {r["id"] for r in panel.rows} == ids


def test_complete_or_reschedule_clears_badge_without_clearing_notes(ui):
    window, pid, _ = ui
    first, second = window.store.tasks(pid)[:2]
    edit(window.store, first, status="已完成", manual_minutes=4, note="保留学习记录")
    edit(window.store, second, due_date="2026-10-17")
    window.refresh()
    panel = window.week_panel
    panel.status_filter.setCurrentText("逾期")
    assert not panel.rows
    assert not any(item.data(0, OVERDUE_ROLE) for item in table_items(window.today_tree))
    rows = window.store.tasks(pid)
    assert rows[0]["id"] == first["id"] and rows[0]["note"] == "保留学习记录"
    assert rows[0]["manual_seconds"] == 240 and rows[0]["status"] == "已完成"


def test_midnight_refresh_adds_overdue_and_keeps_filters(ui):
    window, pid, clock = ui
    panel = window.week_panel
    panel.plan_filter.setCurrentIndex(panel.plan_filter.findData(pid))
    panel.status_filter.setCurrentText("逾期")
    assert len(panel.rows) == 2
    clock[0] = RealDate(2026, 10, 17)
    window.tick()
    assert len(panel.rows) == 3
    assert panel.plan_filter.currentData() == pid and panel.status_filter.currentText() == "逾期"
    assert [item.data(0, OVERDUE_ROLE) for item in table_items(window.today_tree)] == [5, 3, 1]


def test_filtered_task_focus_and_edit_still_use_stable_id(ui, monkeypatch, qtbot):
    window, pid, _ = ui
    panel = window.week_panel
    panel.status_filter.setCurrentText("逾期")
    task_id = panel.rows[0]["id"]
    called = []
    panel.start_focus = called.append
    panel.render()
    qtbot.mouseClick(panel.table.itemWidget(panel.table.topLevelItem(0), 6), Qt.MouseButton.LeftButton)
    assert called == [task_id]
    window.edit_task_id(task_id)
    dialog = window.tools.windows[('task', task_id)]
    assert "逾期" not in [dialog.status.itemText(i) for i in range(dialog.status.count())]
    assert not dialog.isModal()
    dialog.status.setCurrentText("已完成")
    dialog.accept()
    assert task_id not in {r["id"] for r in panel.rows}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_overdue_badge_paints_in_selected_rows_and_calendar(ui, theme, tmp_path):
    window, pid, _ = ui
    app = QApplication.instance()
    apply_theme(app, theme)
    colours = DARK if theme == "dark" else LIGHT
    window.resize(980, 680)
    window.navigate(0)
    item = window.today_tree.topLevelItem(0)
    window.today_tree.setCurrentItem(item)
    app.processEvents()
    rect = window.today_tree.visualItemRect(item)
    image = window.today_tree.viewport().grab(rect).toImage()
    # Real rendered badge ink survives selection; not only a correct property.
    assert any(image.pixelColor(x, y).name() == colours["overdue"].lower()
               for x in range(min(image.width(), 180)) for y in range(image.height()))
    assert window.grab().save(str(tmp_path / f"overdue-today-{theme}.png"))
    window.navigate(2)
    panel = window.week_panel
    panel.status_filter.setCurrentText("逾期")
    assert panel.cards[0].findChild(QLabel, "overdueBadge").text() == "逾期 4 天"
    # The compact calendar scrolls by design. Inspect a full readable card too.
    panel.calendar.ensureWidgetVisible(panel.cards[0], 0, 0)
    window.resize(1220, 900)
    app.processEvents()
    assert window.grab().save(str(tmp_path / f"overdue-week-{theme}.png"))
    panel.mode.setCurrentIndex(1)
    app.processEvents()
    assert window.grab().save(str(tmp_path / f"overdue-table-{theme}.png"))
