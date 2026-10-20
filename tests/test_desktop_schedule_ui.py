import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from datetime import date, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton, QMessageBox, QDialog
from desktop.confirmations import CapacityConfirmDialog

from desktop.store import Store
from desktop.window import MainWindow
from desktop.credentials import CredentialStore
from desktop.schedule_ui import ScheduleDialog, BudgetSettings, budget_action
from desktop.scheduling import preview, paused
from desktop.theme import apply_theme
from desktop.controls import ArrowWheelSpinBox, ArrowWheelDateEdit, SoftComboBox
from tests.test_desktop_scheduling import create


@pytest.fixture
def window(qtbot, tmp_path):
    w = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.exiting = True
    w.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_schedule_preview_cancel_commit_and_guarded_inputs(window, qtbot, theme):
    apply_theme(QApplication.instance(), theme)
    s = window.store
    pid = create(s)
    before = s.export_plan(pid)
    d = ScheduleDialog(s, window, plan_id=pid)
    qtbot.addWidget(d)
    d.resize(720, 620)
    d.show()
    assert isinstance(d.plans, SoftComboBox) and isinstance(d.start, ArrowWheelDateEdit)
    d.mode.setCurrentIndex(1)
    d.select_tasks(True)
    d.make_preview()
    assert d.apply.isEnabled() and "日期变化" in d.output.toPlainText()
    assert s.export_plan(pid) == before
    d.overload.setChecked(True)
    assert not d.apply.isEnabled() and d.proposal is None
    d.make_preview()
    d.commit()
    assert s.export_plan(pid) != before
    assert d.result() == d.DialogCode.Accepted


def test_budget_settings_unset_zero_and_display_global_with_filtered_tasks(window, qtbot):
    s = window.store
    a, b = create(s, date.today()), create(s, date.today(), "另一份计划")
    panel = window.budget_settings
    assert all(not e.isChecked() for e, _ in panel.fields)
    for e, minutes in panel.fields:
        assert isinstance(minutes, ArrowWheelSpinBox)
        e.setChecked(True)
        minutes.setValue(0)
    panel.save()
    assert s.setting("daily_budget") == [0] * 7
    window.today_plan_filter.setCurrentIndex(window.today_plan_filter.findData(a))
    assert "全部活动计划" in window.today_budget.text()
    assert "任务" in window.week_panel.budget_summary.text() or "全部活动计划" in window.week_panel.budget_summary.text()
    assert s.capacity()


def test_budget_confirmation_defaults_cancel_and_does_not_write(window, monkeypatch):
    s = window.store
    s.set_setting("daily_budget", [0] * 7)
    checked = []
    def cancel(box):
        checked.append(box.cancel_button.isDefault() and not box.confirm_button.isDefault())
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(CapacityConfirmDialog, "exec", cancel)
    assert budget_action(window, s, lambda: create(s, date.today())) is None
    assert checked == [True] and not s.list_plans()
    monkeypatch.setattr(CapacityConfirmDialog, "exec", lambda box: QDialog.DialogCode.Accepted)
    assert budget_action(window, s, lambda: create(s, date.today()))


def test_week_empty_reason_and_jump_heading_synchronization(window):
    s = window.store
    start = date.today() + timedelta(days=90)
    pid = create(s, start)
    window.refresh()
    panel = window.week_panel
    panel.plan_filter.setCurrentIndex(panel.plan_filter.findData(pid))
    assert "不在任务排期内" in panel.context.text()
    panel.first_week()
    assert panel.rows
    assert panel.start.isoformat()[:4] in window.week_heading.text()
    panel.status_filter.setCurrentText("已完成")
    assert not panel.rows and "筛选隐藏" in panel.context.text()
    panel.clear_filters()
    assert panel.rows
    panel.next_task()
    assert panel.selected_id is None


def test_paused_tasks_remain_in_week_but_focus_disabled_and_today_hidden(window):
    s = window.store
    pid = create(s)
    s.apply_schedule(preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=date.today() + timedelta(days=5)))
    window.refresh()
    assert window.today_tree.topLevelItemCount() == 0
    panel = window.week_panel
    panel.plan_filter.setCurrentIndex(panel.plan_filter.findData(pid))
    panel.first_week()
    assert "计划已暂停" in panel.context.text()
    assert panel.cards
    assert all(not b.isEnabled() for c in panel.cards for b in c.findChildren(QPushButton))


def test_new_buttons_pause_focus_confirmation_and_cancel(window, monkeypatch):
    s = window.store
    pid = create(s)
    window.refresh()
    row = s.tasks(pid)[0]
    window.focus.start(row["id"])
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    window.open_schedule(plan_id=pid, kind="暂停")
    assert window.focus.running and not paused(s.schedule(pid))
    names = [b.text() for b in window.findChildren(QPushButton)]
    assert "安排逾期任务" in names and "调整排期" in names and "暂停计划" in names
    window.focus.stop()
