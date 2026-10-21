"""Offline regression for explicit scheduling and both kinds of manual task."""
from contextlib import closing
from datetime import date, timedelta, datetime
import json
import sqlite3

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication

from models import DailyTask, validate_plan_for_profile
from desktop.store import Store
from desktop.scheduling import preview, paused, BudgetExceeded
from desktop.focus import FocusTimer
from desktop.planning import protection_reason
from desktop.task_style import focus_tooltip
from desktop.manual_tasks import ManualTaskDialog
from desktop.schedule_ui import ScheduleDialog
from desktop.window import MainWindow
from desktop.credentials import CredentialStore
from desktop.theme import apply_theme
from tests.test_desktop_scheduling import create
from tests.test_desktop_store import edit


def payload(minutes=15):
    return DailyTask(day=1, title="自己添加的任务", description="阅读并记录要点", deliverable="一份简短笔记", duration_minutes=minutes)


def row(store, task_id):
    return next(r for r in store.tasks() if r["id"] == task_id)


@pytest.mark.parametrize("status", ["未开始", "进行中", "延期", "已完成"])
def test_focus_status_and_idempotent_seconds(tmp_path, status):
    s = Store(tmp_path)
    tid = s.add_manual_task(payload(), date.today().isoformat())
    edit(s, row(s, tid), status=status)
    s.record_focus("session", tid, 0, "2026-10-19T12:00:00")
    assert row(s, tid)["status"] == status
    s.record_focus("session", tid, 65, "2026-10-19T12:00:00")
    s.record_focus("session", tid, 65, "2026-10-19T12:00:00")
    r = row(s, tid)
    assert r["status"] == ("进行中" if status == "未开始" else status)
    assert r["focus_seconds"] == 65
    assert "1 分 5 秒" in focus_tooltip(r)
    edit(s, r, status="未开始")
    assert row(s, tid)["status"] == "进行中"


@pytest.mark.parametrize("attached", [False, True])
def test_manual_task_restart_backup_focus_reminder_edit_delete(tmp_path, attached):
    s = Store(tmp_path / "source")
    pid = create(s) if attached else None
    day = (date.today() + timedelta(days=31)).isoformat()
    tid = s.add_manual_task(payload(), day, plan_id=pid, remind_at="20:00")
    r = row(s, tid)
    assert r["manual"] == 1 and r["plan_id"] == pid and r["day"] is None
    assert "手动任务" in protection_reason(r)
    s.record_focus("manual-session", tid, 61, day + "T10:00:00")
    edit(s, row(s, tid), note="测试笔记", manual_minutes=2)
    target = Store(tmp_path / "target")
    backup = s.manual_backup()
    target.restore(backup)
    target = Store(target.directory)
    assert row(target, tid)["note"] == "测试笔记"
    assert row(target, tid)["focus_seconds"] == 61
    assert row(target, tid)["manual_seconds"] == 120
    due = target.due_reminders(datetime.fromisoformat(day + "T21:00:00"))
    assert tid in {r["id"] for r in due}
    assert not target.due_reminders(datetime.fromisoformat(day + "T21:00:00"))
    target.delete_manual_task(tid)
    assert not target.read("SELECT * FROM focus_sessions WHERE task_id=?", (tid,))
    assert not target.read("SELECT * FROM notifications WHERE task_id=?", (tid,))


def test_attached_manual_json_preserves_generated_shape_and_new_ids(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    tid = s.add_manual_task(payload(), (date.today() + timedelta(days=31)).isoformat(), plan_id=pid)
    saved = s.get_plan(pid)
    validate_plan_for_profile(saved["plan"], saved["profile"])
    assert len(saved["tasks"]) == 5 and len(s.progress(pid)) == 4
    imported = s.import_plan(s.export_plan(pid), date.today())
    assert len(s.tasks(imported)) == 5
    assert tid not in {r["id"] for r in s.tasks(imported)}
    validate_plan_for_profile(s.get_plan(imported)["plan"], saved["profile"])


def test_both_manual_types_obey_global_budget_and_plan_hard_limit(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    day = (date.today() + timedelta(days=30)).isoformat()
    s.set_setting("daily_budget", [20] * 7)
    s.add_manual_task(payload(), day)
    with pytest.raises(BudgetExceeded) as err:
        s.add_manual_task(payload(), day, plan_id=pid)
    assert len(s.tasks("__standalone__")) == 1 and not any(r["manual"] for r in s.tasks(pid))
    with s.accept_budget(err.value.token):
        s.add_manual_task(payload(), day, plan_id=pid)
    assert s.capacity()[day]["total"] == 30
    with pytest.raises(ValueError, match="上限"):
        s.add_manual_task(payload(45), day, plan_id=pid)
    assert s.capacity()[day]["total"] == 30


def test_manual_exact_batch_preserves_records_lock_and_other_tasks(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    a, b, untouched, completed = s.tasks(pid)
    edit(s, a, status="进行中", note="已经学过", manual_minutes=5)
    edit(s, b, payload=b["task"].model_copy(update={"duration_minutes": 5}))
    edit(s, completed, status="已完成")
    target = date.today() + timedelta(days=30)
    # Combined 35 min fits a 45 min plan day, not its original weekday.
    p = preview(s, [a["id"], b["id"], completed["id"]], target, exact_date=True)
    assert not p.conflicts and len(p.changes) == 2 and len(p.protected) == 1
    s.apply_schedule(p)
    assert row(s, a["id"])["note"] == "已经学过"
    assert row(s, b["id"])["ai_locked"] == 1
    assert row(s, untouched["id"])["due_date"] == untouched["due_date"]
    assert all(row(s, t["id"])["due_date"] == target.isoformat() for t in (a, b))


def test_exact_date_conflicts_explain_and_cancel_is_readonly(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    before = s.export_plan(pid)
    p = preview(s, [r["id"] for r in s.tasks(pid)], date.today() + timedelta(days=30), exact_date=True)
    assert p.conflicts and "当天" in "".join(p.conflicts)
    with pytest.raises(ValueError):
        s.apply_schedule(p)
    assert before == s.export_plan(pid)


def test_pause_and_resume_only_change_availability_not_dates(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    dates = {r["id"]: r["due_date"] for r in s.tasks(pid)}
    until = date.today() + timedelta(days=7)
    p = preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=until, keep_dates=True)
    assert not p.changes
    s.apply_schedule(p)
    assert paused(s.schedule(pid)) and not s.tasks(due=date.today().isoformat())
    assert {r["id"]: r["due_date"] for r in s.tasks(pid)} == dates
    p = preview(s, [], date.today(), kind="恢复", plan_id=pid, keep_dates=True)
    s.apply_schedule(p)
    assert not paused(s.schedule(pid))
    assert {r["id"]: r["due_date"] for r in s.tasks(pid)} == dates


@pytest.mark.parametrize("mutation", ["add", "edit", "focus", "delete"])
def test_standalone_mutation_invalidates_other_plan_preview(tmp_path, mutation):
    s = Store(tmp_path)
    pid = create(s)
    tid = s.add_manual_task(payload(), date.today().isoformat())
    p = preview(s, [s.tasks(pid)[0]["id"]], date.today() + timedelta(days=30), exact_date=True)
    if mutation == "add":
        s.add_manual_task(payload(), date.today().isoformat())
    elif mutation == "edit":
        edit(s, row(s, tid), note="新的笔记")
    elif mutation == "focus":
        s.record_focus("session", tid, 5, "2026-10-19T12:00:00")
    else:
        s.delete_manual_task(tid)
    with pytest.raises(ValueError, match="已有变化"):
        s.apply_schedule(p)


def test_standalone_exact_reschedule_and_focus(tmp_path):
    s = Store(tmp_path)
    tid = s.add_manual_task(payload(), date.today().isoformat())
    p = preview(s, [tid], date.today() + timedelta(days=2), exact_date=True)
    assert len(p.changes) == 1 and not p.schedules
    s.apply_schedule(p)
    assert row(s, tid)["due_date"] == (date.today() + timedelta(days=2)).isoformat()
    clock = [0.0]
    f = FocusTimer(s, clock=lambda: clock[0])
    f.start(tid)
    clock[0] = 5.0
    f.tick()
    assert row(s, tid)["status"] == "进行中"
    f.stop()
    assert row(s, tid)["focus_seconds"] == 5


def test_pause_interval_blocks_exact_move_but_resume_day_allows_it(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    tid = s.tasks(pid)[0]["id"]
    until = date.today() + timedelta(days=7)
    s.apply_schedule(preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=until))
    p = preview(s, [tid], date.today(), exact_date=True)
    assert p.conflicts and "暂停中" in "".join(p.conflicts)
    p = preview(s, [tid], until, exact_date=True)
    assert not p.conflicts
    s.apply_schedule(p)
    assert row(s, tid)["due_date"] == until.isoformat() and paused(s.schedule(pid))


@pytest.mark.parametrize("reason", ["reviewed", "completed", "active", "archived"])
def test_manual_move_protected_reasons_are_visible(tmp_path, reason):
    from desktop.scheduling import manual_move_reason
    s = Store(tmp_path)
    pid = create(s)
    r = s.tasks(pid)[0]
    if reason == "reviewed":
        reviewed = {1: "复盘完成"}
    else:
        reviewed = {}
    if reason == "completed":
        r["status"] = "已完成"
    if reason == "active":
        r["focus_active"] = True
    if reason == "archived":
        r["archived"] = True
    assert manual_move_reason(r, reviewed)


def test_weekly_review_does_not_rewrite_manual_task(tmp_path):
    from types import SimpleNamespace
    from desktop.review import prepare_review
    from tests.test_progress_store import sample_plan
    s = Store(tmp_path)
    pid = create(s)
    tid = s.add_manual_task(payload(), (date.today() + timedelta(days=60)).isoformat(), plan_id=pid)
    for r in s.tasks(pid):
        if r["week"] == 1:
            edit(s, r, status="已完成")
    before = row(s, tid)
    def generate(profile, **kwargs):
        return sample_plan().model_copy(update={"weekly_plans": sample_plan().weekly_plans[:1]}), None
    p = prepare_review(s, pid, SimpleNamespace(generate=generate))
    s.apply_review(pid, p["week"], p["summary"], p["adjusted"], p["revision"])
    assert before == row(s, tid)


def test_schema_five_failed_migration_leaves_original_untouched(tmp_path, monkeypatch):
    s = Store(tmp_path)
    create(s)
    # Simulate a previous library for the failure boundary only. The separate
    # real-schema test exercises the old canonical task definition and all FKs.
    with closing(sqlite3.connect(s.path)) as c, c:
        c.execute("PRAGMA user_version=4")
    before = s.path.read_bytes()
    def fail(c):
        c.execute("CREATE TABLE incomplete(id)")
        raise ValueError("模拟失败")
    monkeypatch.setattr(Store, "_upgrade_v5", staticmethod(fail))
    with pytest.raises(ValueError, match="模拟失败"):
        Store(s.directory)
    assert s.path.read_bytes() == before
    assert not list(s.directory.glob("upgrade-*.sqlite3"))


def test_real_schema_four_migration_preserves_children_and_backfills_status(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    task = s.tasks(pid)[0]
    s.record_focus("session", task["id"], 60, "2026-10-19T12:00:00")
    with closing(sqlite3.connect(s.path)) as c, c:
        c.execute("""CREATE TABLE tasks_old (id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
            week INTEGER NOT NULL, day INTEGER NOT NULL,payload_json TEXT NOT NULL,
            due_date TEXT NOT NULL,remind_at TEXT,status TEXT NOT NULL DEFAULT '未开始',
            manual_seconds INTEGER NOT NULL DEFAULT 0,focus_seconds INTEGER NOT NULL DEFAULT 0,
            difficulty INTEGER,note TEXT NOT NULL DEFAULT '',user_modified INTEGER NOT NULL DEFAULT 0,
            ai_locked INTEGER NOT NULL DEFAULT 0,UNIQUE(plan_id,week,day))""")
        columns = ','.join(r[1] for r in c.execute("PRAGMA table_info(tasks_old)"))
        c.execute(f"INSERT INTO tasks_old SELECT {columns} FROM tasks")
        c.execute("DROP TABLE tasks")
        c.execute("ALTER TABLE tasks_old RENAME TO tasks")
        c.execute("CREATE INDEX tasks_due ON tasks(due_date,status)")
        c.execute("UPDATE tasks SET status='未开始' WHERE id=?", (task["id"],))
        c.execute("INSERT INTO notifications VALUES (?,'fingerprint','2026-10-19')", (task["id"],))
        c.execute("PRAGMA user_version=4")
    original = s.path.read_bytes()
    reopened = Store(s.directory)
    assert row(reopened, task["id"])["status"] == "进行中"
    assert row(reopened, task["id"])["focus_seconds"] == 60
    assert reopened.read("SELECT * FROM focus_sessions")[0]["task_id"] == task["id"]
    assert reopened.read("SELECT * FROM notifications")[0]["task_id"] == task["id"]
    backup = list(reopened.backups.glob("before-schema-7-*.sqlite3"))[0]
    with closing(sqlite3.connect(backup)) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 4
        assert c.execute("SELECT status,focus_seconds FROM tasks WHERE id=?", (task["id"],)).fetchone() == ("未开始", 60)
    assert reopened.read("PRAGMA foreign_key_check") == []
    assert reopened.read("PRAGMA user_version")[0]["user_version"] == 7


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_ui_manual_tasks_filters_tooltips_and_pause_words(qtbot, tmp_path, theme):
    apply_theme(QApplication.instance(), theme)
    s = Store(tmp_path)
    pid = create(s)
    w = MainWindow(s, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(w)
    try:
        d = ManualTaskDialog(s, w)
        qtbot.addWidget(d)
        d.title.setText("独立学习测试")
        d.accept()
        assert d.task_id and row(s, d.task_id)["plan_id"] is None
        d2 = ManualTaskDialog(s, w, plan_id=pid)
        qtbot.addWidget(d2)
        d2.title.setText("计划内学习测试")
        d2.due.setDate(QDate.currentDate().addDays(30))
        d2.accept()
        assert d2.task_id and row(s, d2.task_id)["plan_id"] == pid
        s.record_focus("session", d.task_id, 125, "2026-10-19T12:00:00")
        w.refresh()
        w.today_plan_filter.setCurrentIndex(w.today_plan_filter.findData("__standalone__"))
        assert w.today_tree.topLevelItemCount() == 1
        assert "2 分 5 秒" in w.today_tree.topLevelItem(0).toolTip(3)
        panel = w.week_panel
        panel.plan_filter.setCurrentIndex(panel.plan_filter.findData("__standalone__"))
        assert len(panel.rows) == 1 and "2 分 5 秒" in panel.cards[0].status.toolTip()
        item = w.today_tree.topLevelItem(0)
        w.today_tree.setCurrentItem(item)
        s.record_focus("session", d.task_id, 130, "2026-10-19T12:00:00")
        w.refresh_focus_status()
        assert w.today_tree.currentItem() is item
        assert "2 分 10 秒" in item.toolTip(3) and "2 分 10 秒" in panel.cards[0].status.toolTip()
        pause = ScheduleDialog(s, w, plan_id=pid, kind="暂停")
        qtbot.addWidget(pause)
        assert "当天恢复" in pause.pause_hint.text() and "任务不会自动改期" in pause.pause_hint.text()
        pause.make_preview()
        assert pause.apply.isEnabled() and not pause.proposal.changes
    finally:
        w.exiting = True
        w.close()
