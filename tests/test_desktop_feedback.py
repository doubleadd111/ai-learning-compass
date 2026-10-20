"""Regression for the user's screenshots: modality, flashes and limit prompts."""
from contextlib import closing
from datetime import date, timedelta

import pytest
from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer
from PySide6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QStyleOptionViewItem, QWidget
from PySide6.QtGui import QHelpEvent

from desktop.store import Store
from desktop.scheduling import preview, PlanCapacityExceeded, plan_loads
from desktop.schedule_ui import ScheduleDialog, budget_action
from desktop.confirmations import CapacityConfirmDialog
from desktop.manual_tasks import ManualTaskDialog
from desktop.guide import open_guide, close_guide
from desktop.focus_tip import focus_tips, short_duration, set_focus_hint
from desktop.task_style import TaskItemDelegate, FOCUS_ROLE
from desktop.theme import apply_theme
from tests.test_desktop_scheduling import create
from tests.test_desktop_manual_tasks import payload, row
from tests.test_desktop_store import edit


@pytest.fixture(autouse=True)
def styled_app(qapp):
    # Match production: set the Fusion proxy before constructing calendar widgets.
    apply_theme(qapp, "light")


@pytest.mark.parametrize("approve", [False, True])
def test_manual_add_capacity_prompt_default_cancel_and_atomic_write(qtbot, tmp_path, monkeypatch, approve):
    s = Store(tmp_path)
    pid = create(s)
    original = s.tasks(pid)[0]
    before = s.export_plan(pid)
    calls = []
    def confirm(box):
        calls.append(box)
        assert box.cancel_button.isDefault() and not box.confirm_button.isDefault()
        assert "上限" in box.windowTitle() and original["due_date"] in box.details
        assert s.export_plan(pid) == before  # No tentative task/version leaks.
        return QDialog.DialogCode.Accepted if approve else QDialog.DialogCode.Rejected
    monkeypatch.setattr(CapacityConfirmDialog, "exec", confirm)
    tid = budget_action(None, s, lambda: s.add_manual_task(payload(25), original["due_date"], plan_id=pid, confirm_limits=True))
    assert len(calls) == 1
    assert bool(tid) == approve
    if approve:
        assert len(s.tasks(pid)) == 5
        assert s.get_plan(pid)["profile"].minutes_per_day == 45
        assert row(s, tid)["task"].duration_minutes == 25
    else:
        assert s.export_plan(pid) == before


def test_both_plan_and_global_limits_appear_in_one_confirmation(qtbot, tmp_path, monkeypatch):
    s = Store(tmp_path)
    pid = create(s)
    due = s.tasks(pid)[0]["due_date"]
    s.set_setting("daily_budget", [30] * 7)
    details = []
    def approve(box):
        details.append(box.details)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(CapacityConfirmDialog, "exec", approve)
    budget_action(None, s, lambda: s.add_manual_task(payload(25), due, plan_id=pid, confirm_limits=True))
    assert len(details) == 1 and "每日预算" in details[0] and "上限" in details[0]
    assert s.capacity()[due]["total"] == 55


def test_exact_weekly_overflow_is_preview_warning_then_explicit_approval(qtbot, tmp_path, monkeypatch):
    s = Store(tmp_path)
    pid = create(s)
    target = date.today() + timedelta(days=30)
    rows = s.tasks(pid)
    # All four generated tasks total 120 minutes; profile weekly cap is 90.
    ids = [r["id"] for r in rows]
    p = preview(s, ids, target, exact_date=True, manual_override=True, allow_overload=True)
    assert p.warnings and not p.conflicts and len(p.changes) == 4
    before = s.export_plan(pid)
    with pytest.raises(PlanCapacityExceeded) as err:
        s.apply_schedule(p)
    assert s.export_plan(pid) == before and "本周" in err.value.details
    with s.accept_plan_capacity(err.value.token):
        s.apply_schedule(p)
    assert {r["id"] for r in s.tasks(pid)} == set(ids)
    assert all(r["due_date"] == target.isoformat() for r in s.tasks(pid))


def test_schedule_ui_limit_warning_does_not_disable_apply(qtbot, tmp_path, monkeypatch):
    s = Store(tmp_path)
    pid = create(s)
    d = ScheduleDialog(s, plan_id=pid)
    qtbot.addWidget(d)
    d.select_tasks(True)
    d.overload.setChecked(True)
    d.make_preview()
    assert d.proposal.warnings and d.apply.isEnabled()
    monkeypatch.setattr(CapacityConfirmDialog, "exec", lambda box: QDialog.DialogCode.Rejected)
    before = s.export_plan(pid)
    d.commit()
    assert s.export_plan(pid) == before and d.proposal is not None and d.apply.isEnabled()
    monkeypatch.setattr(CapacityConfirmDialog, "exec", lambda box: QDialog.DialogCode.Accepted)
    d.commit()
    assert d.result() == QDialog.DialogCode.Accepted


def test_manual_dialog_overflow_not_misreported_as_bad_title(qtbot, tmp_path, monkeypatch):
    s = Store(tmp_path)
    pid = create(s)
    d = ManualTaskDialog(s, plan_id=pid)
    qtbot.addWidget(d)
    from PySide6.QtCore import QDate
    d.due.setDate(QDate.fromString(s.tasks(pid)[0]["due_date"], "yyyy-MM-dd"))
    d.title.setText("测试")
    monkeypatch.setattr(CapacityConfirmDialog, "exec", lambda box: QDialog.DialogCode.Rejected)
    d.accept()
    assert not d.task_id and not d.error.isVisible()
    monkeypatch.setattr(CapacityConfirmDialog, "exec", lambda box: QDialog.DialogCode.Accepted)
    d.accept()
    assert d.task_id and d.result() == QDialog.DialogCode.Accepted


def test_confirmation_is_single_use_and_stale_after_another_task_change(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    due = s.tasks(pid)[0]["due_date"]
    action = lambda: s.add_manual_task(payload(25), due, plan_id=pid, confirm_limits=True)
    with pytest.raises(PlanCapacityExceeded) as err:
        action()
    edit(s, s.tasks(pid)[0], note="确认前新增的笔记")
    with s.accept_plan_capacity(err.value.token), pytest.raises(PlanCapacityExceeded):
        action()
    assert len(s.tasks(pid)) == 4


def test_existing_approved_overflow_can_be_recorded_and_exported_imported(qtbot, tmp_path, monkeypatch):
    s = Store(tmp_path)
    pid = create(s)
    due = s.tasks(pid)[0]["due_date"]
    monkeypatch.setattr(CapacityConfirmDialog, "exec", lambda box: QDialog.DialogCode.Accepted)
    tid = budget_action(None, s, lambda: s.add_manual_task(payload(25), due, plan_id=pid, confirm_limits=True))
    def no_prompt(box):
        pytest.fail("Unchanged existing overflow must not ask again")
    monkeypatch.setattr(CapacityConfirmDialog, "exec", no_prompt)
    budget_action(None, s, lambda: edit(s, row(s, tid), status="已完成", note="学习记录", confirm_limits=True))
    assert row(s, tid)["status"] == "已完成"
    monkeypatch.setattr(CapacityConfirmDialog, "exec", lambda box: QDialog.DialogCode.Accepted)
    imported = budget_action(None, s, lambda: s.import_plan(s.export_plan(pid), date.today(), confirm_limits=True))
    assert len(s.tasks(imported)) == 5


def test_automatic_schedule_and_ai_still_cannot_bypass_plan_capacity(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    p = preview(s, [r["id"] for r in s.tasks(pid)], date.today(), exact_date=False, manual_override=True, allow_overload=True)
    assert not p.conflicts
    s.apply_schedule(p)
    with closing(s.connect()) as c:
        s._check_plan_capacity(c, pid)
    # Strict backend creation remains strict unless the explicit manual guard is used.
    due = s.tasks(pid)[0]["due_date"]
    with pytest.raises(ValueError):
        s.add_manual_task(payload(45), due, plan_id=pid)


class ShowAudit(QObject):
    def __init__(self):
        super().__init__()
        self.windows = []

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and hasattr(watched, "isWindow") and watched.isWindow():
            self.windows.append(type(watched).__name__)
        return False


@pytest.mark.parametrize("kind", ["补排", "暂停"])
def test_schedule_constructor_shows_no_temporary_top_level_widgets(qtbot, tmp_path, kind):
    s = Store(tmp_path)
    pid = create(s)
    audit = ShowAudit()
    app = QApplication.instance()
    app.installEventFilter(audit)
    try:
        d = ScheduleDialog(s, plan_id=pid, kind=kind)
        qtbot.addWidget(d)
        app.processEvents()
        assert not audit.windows
        d.show()
        assert audit.windows == ["ScheduleDialog"]
    finally:
        app.removeEventFilter(audit)


@pytest.mark.parametrize("modal", [False, True])
def test_guide_above_source_window_and_independent_after_close(qtbot, modal):
    host = QWidget()
    qtbot.addWidget(host)
    host.show()
    # Reuse a guide that was already shown from the main window and minimized.
    # QDialog used to silently reset the native owner on this show path.
    guide = open_guide(host)
    guide.showMinimized()
    QApplication.processEvents()
    source = QDialog(host)
    source.setWindowTitle("测试说明来源")
    source.setWindowModality(Qt.WindowModality.ApplicationModal if modal else Qt.WindowModality.NonModal)
    qtbot.addWidget(source)
    source.show()
    app = QApplication.instance()
    app.processEvents()
    assert open_guide(source, "modify") is guide
    try:
        app.processEvents()
        assert guide.parentWidget() is None
        assert guide.windowHandle().transientParent() is source.windowHandle()
        assert guide.isVisible()
        if app.platformName() == "windows":
            qtbot.waitUntil(guide.isActiveWindow, timeout=3000)
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
            user32.GetWindow.restype = wintypes.HWND
            assert user32.GetWindow(int(guide.winId()), 4) == int(source.winId())
        guide.search.setFocus()
        qtbot.keyClicks(guide.search, "focus")
        assert guide.search.text() == "focus"
        assert open_guide(source, "schedule") is guide
        source.accept()
        app.processEvents()
        assert guide.isVisible() and guide.windowHandle().transientParent() is None
    finally:
        close_guide()
        source.close()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_focus_tip_rounding_short_time_no_activation_and_dismissal(qtbot, theme):
    apply_theme(QApplication.instance(), theme)
    anchor = QLabel("进行中")
    qtbot.addWidget(anchor)
    anchor.resize(160, 44)
    anchor.show()
    set_focus_hint(anchor, {"focus_seconds": 162})
    event = QHelpEvent(QEvent.Type.ToolTip, QPoint(10, 10), anchor.mapToGlobal(QPoint(10, 10)))
    QApplication.sendEvent(anchor, event)
    tip = focus_tips().popup
    assert tip.isVisible() and tip.value.text() == "2 分 42 秒"
    assert tip.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert tip.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert tip.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
    image = tip.grab().toImage()
    assert image.pixelColor(0, 0).alpha() == 0
    assert short_duration(3605) == "1 小时 0 分 5 秒"
    QApplication.sendEvent(anchor, QEvent(QEvent.Type.ApplicationDeactivate))
    assert not tip.isVisible()
