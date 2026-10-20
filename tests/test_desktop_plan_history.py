"""Seven-day append-only arrangement replay, never rewind learning evidence."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json

import pytest

from desktop import plan_history as history
from desktop.focus import FocusTimer
from desktop.scheduling import preview as schedule_preview, PlanCapacityExceeded, BudgetExceeded
from desktop.store import Store
from tests.test_desktop_store import create, edit
from tests.test_desktop_manual_tasks import payload
from tests.test_desktop_delete_safety import snapshot


def versions(store, pid):
    return store.versions(pid)


def test_manual_edit_name_archive_lock_and_no_progress_spam(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    assert len(versions(s, pid)) == 1
    row = s.tasks(pid)[0]
    edit(s, row, note="新笔记", status="进行中", manual_minutes=3)
    s.record_focus("session", row["id"], 65, "2026-10-19T10:00:00")
    assert len(versions(s, pid)) == 1
    edit(s, s.tasks(pid)[1], payload=s.tasks(pid)[1]["task"].model_copy(update={"title": "新的任务说明"}))
    assert versions(s, pid)[0]["kind"] == "编辑任务安排"
    s.rename_plan(pid, "简短的新计划名称")
    s.rename_plan(pid, "简短的新计划名称")
    s.archive(pid)
    s.archive(pid)
    s.archive(pid, False)
    assert [r["kind"] for r in versions(s, pid)][:3] == ["恢复归档计划", "归档计划", "重命名计划"]
    s.set_task_lock(s.tasks(pid)[2]["id"], True)
    assert versions(s, pid)[0]["kind"] == "修改AI保护锁"


def test_rolling_168_hours_and_timezone_equivalence(tmp_path, monkeypatch):
    s = Store(tmp_path)
    pid = create(s)
    now = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(history, "utc_now", lambda: now)
    row = versions(s, pid)[0]
    for offset, expected in ((timedelta(days=7), True), (timedelta(days=7, microseconds=1), False),
                              (timedelta(seconds=-1), False), (timedelta(), True)):
        value = (now - offset).astimezone(timezone(timedelta(hours=8))).isoformat()
        with s.write() as c:
            c.execute("UPDATE plan_versions SET created_at=? WHERE id=?", (value, row["id"]))
        assert bool(history.recent_versions(s, pid)) == expected
    # Viewing never purges older versions.
    assert len(s.versions(pid)) == 1


def test_preview_read_only_apply_and_replay_back_preserve_learning(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    origin = versions(s, pid)[0]
    row = s.tasks(pid)[0]
    s.rename_plan(pid, "修改后的名称")
    edit(s, row, payload=row["task"].model_copy(update={"title": "修改后的任务标题"}))
    edit(s, s.tasks(pid)[0], status="已完成", manual_minutes=8, difficulty=3, note="回溯也必须保留")
    s.record_focus("learn", row["id"], 72, "2026-10-19T11:00:00")
    before = snapshot(s)
    p = history.preview(s, pid, origin["id"])
    assert p.changes and snapshot(s) == before
    assert history.apply(s, p) is True
    task = s.tasks(pid)[0]
    assert task["status"] == "已完成" and task["manual_seconds"] == 480 and task["focus_seconds"] == 72
    assert task["note"] == "回溯也必须保留" and task["difficulty"] == 3
    for table in ("focus_sessions", "notifications", "reviews"):
        assert snapshot(s)[table] == before[table]
    latest = versions(s, pid)[0]
    assert latest["kind"].startswith("回溯历史")
    assert len(versions(s, pid)) == 4
    assert json.loads(latest["before_json"])["plans"][0]["title"] == "修改后的名称"
    assert len(list(s.backups.glob("before-history-replay-*"))) == 1
    history.apply(s, history.preview(s, pid, latest["id"], "before"))
    assert s.get_plan(pid)["title"] == "修改后的名称"
    assert s.tasks(pid)[0]["task"].title == "修改后的任务标题"
    assert s.tasks(pid)[0]["focus_seconds"] == 72
    assert len(versions(s, pid)) == 5  # No discarded branch or destructive reset.


@pytest.mark.parametrize("change", ["note", "focus", "other_plan", "budget", "new_task", "lock"])
def test_any_new_input_invalidates_preview(tmp_path, change):
    s = Store(tmp_path)
    pid, other = create(s), create(s)
    target = versions(s, pid)[0]["id"]
    s.rename_plan(pid, "改变名称以产生差异")
    p = history.preview(s, pid, target)
    if change == "note":
        edit(s, s.tasks(pid)[0], note="预览后的笔记")
    elif change == "focus":
        s.record_focus("new-focus", s.tasks(pid)[0]["id"], 20, "2026-10-19T11:00:00")
    elif change == "other_plan":
        s.rename_plan(other, "另一个计划的新名称")
    elif change == "budget":
        s.set_setting("daily_budget", [200] * 7)
    elif change == "lock":
        s.set_task_lock(s.tasks(pid)[0]["id"], True)
    else:
        s.add_manual_task(payload(), "2027-01-01")
    before = snapshot(s)
    with pytest.raises(ValueError, match="已有变化"):
        history.apply(s, p)
    assert snapshot(s) == before


@pytest.mark.parametrize("phase", ["focus", "paused", "break"])
def test_active_focus_or_break_blocks_replay(tmp_path, phase):
    s = Store(tmp_path)
    pid = create(s)
    target = versions(s, pid)[0]["id"]
    s.rename_plan(pid, "正在专注的计划")
    timer = FocusTimer(s)
    timer.start(s.tasks(pid)[0]["id"])
    if phase == "paused":
        timer.pause()
    if phase == "break":
        timer.phase, timer.session_id = "break", None
        timer._commit()
    before = snapshot(s)
    with pytest.raises(ValueError, match="结束"):
        history.preview(s, pid, target)
    assert snapshot(s) == before


def test_manual_tasks_resurrect_content_only_and_keep_recorded_additions(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    initial = versions(s, pid)[0]["id"]
    tid = s.add_manual_task(payload(), "2027-01-01", plan_id=pid)
    added = versions(s, pid)[0]["id"]
    edit(s, next(t for t in s.tasks(pid) if t["id"] == tid), manual_minutes=2, note="需要保留")
    p = history.preview(s, pid, initial)
    assert p.notices and not p.changes  # Cannot silently remove its evidence.
    assert any(t["id"] == tid for t in s.tasks(pid))
    s.delete_manual_task(tid)
    history.apply(s, history.preview(s, pid, added))
    t = next(t for t in s.tasks(pid) if t["id"] == tid)
    assert t["status"] == "未开始" and t["note"] == "" and t["manual_seconds"] == 0
    assert not s.read("SELECT * FROM focus_sessions WHERE task_id=?", (tid,))
    # An unstarted addition can be removed by returning to its before state.
    history.apply(s, history.preview(s, pid, initial))
    assert not any(t["id"] == tid for t in s.tasks(pid))


def test_reviewed_tasks_and_reviews_stay_intact(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    original = versions(s, pid)[0]["id"]
    edit(s, s.tasks(pid)[0], payload=s.tasks(pid)[0]["task"].model_copy(update={"title": "复盘后的内容"}))
    with s.write() as c:
        c.execute("INSERT INTO reviews VALUES (?,?,?)", (pid, 1, "历史复盘记录"))
        s._touch(c, pid)
    s.rename_plan(pid, "另一个名称")
    p = history.preview(s, pid, original)
    assert any("已复盘" in n for n in p.notices)
    history.apply(s, p)
    assert s.tasks(pid)[0]["task"].title == "复盘后的内容"
    assert s.reviews(pid)[1] == "历史复盘记录"


def test_backup_failure_and_expired_target_leave_everything_unchanged(tmp_path, monkeypatch):
    s = Store(tmp_path)
    pid = create(s)
    target = versions(s, pid)[0]["id"]
    s.rename_plan(pid, "新的名称")
    p = history.preview(s, pid, target)
    before = snapshot(s)
    monkeypatch.setattr(s, "manual_backup", lambda *args: (_ for _ in ()).throw(OSError("备份失败")))
    with pytest.raises(OSError):
        history.apply(s, p)
    assert snapshot(s) == before
    monkeypatch.setattr(history, "utc_now", lambda: datetime.now(timezone.utc) + timedelta(days=8))
    with pytest.raises(ValueError, match="最近7天"):
        history.apply(s, p)
    assert snapshot(s) == before


def test_noop_missing_before_cross_plan_invalid_side(tmp_path):
    s = Store(tmp_path)
    pid, other = create(s), create(s)
    target = versions(s, pid)[0]["id"]
    p = history.preview(s, pid, target)
    assert not p.changes
    with pytest.raises(ValueError, match="相同"):
        history.apply(s, p)
    for plan, side in ((pid, "before"), (pid, "invalid"), (other, "after")):
        with pytest.raises(ValueError):
            history.preview(s, plan, target, side)


def test_budget_confirmation_is_required_and_does_not_change_limits(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    target = versions(s, pid)[0]["id"]
    first = s.tasks(pid)[0]
    # Move one task out, lower that weekday's global budget, then rewind.
    edit(s, first, due_date="2027-01-01")
    limits = [None] * 7
    limits[date.fromisoformat(first["due_date"]).weekday()] = 0
    s.set_setting("daily_budget", limits)
    p = history.preview(s, pid, target)
    before = snapshot(s)
    with pytest.raises(BudgetExceeded) as error:
        history.apply(s, p)
    assert snapshot(s) == before
    with s.accept_budget(error.value.token):
        history.apply(s, p)
    assert s.setting("daily_budget") == limits


def test_plan_capacity_confirmation_after_preserving_recorded_task(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    tid = s.add_manual_task(payload(15), "2027-01-01", plan_id=pid)
    target = versions(s, pid)[0]["id"]
    s.delete_manual_task(tid)
    keep = s.add_manual_task(payload(15), "2027-01-01", plan_id=pid)
    edit(s, next(t for t in s.tasks(pid) if t["id"] == keep), note="不能移除")
    # 30 <= original daily 45; create an additional retained task to exceed it.
    extra = s.add_manual_task(payload(25), "2027-01-01", plan_id=pid)
    edit(s, next(t for t in s.tasks(pid) if t["id"] == extra), note="也要保留")
    p = history.preview(s, pid, target)
    assert p.overloads
    before = snapshot(s)
    with pytest.raises(PlanCapacityExceeded) as error:
        history.apply(s, p)
    assert snapshot(s) == before
    with s.accept_plan_capacity(error.value.token):
        history.apply(s, p)
    assert len([t for t in s.tasks(pid) if t["due_date"] == "2027-01-01"]) == 3


def test_restart_backup_export_legacy_preserve_history(tmp_path):
    s = Store(tmp_path / "source")
    pid = create(s)
    s.rename_plan(pid, "有历史的计划")
    before = s.versions(pid)
    assert Store(s.directory).versions(pid) == before
    backup = s.manual_backup()
    target = Store(tmp_path / "restored")
    target.restore(backup)
    assert target.versions(pid) == before
    imported = target.import_plan(s.export_plan(pid), date.today())
    assert target.versions(imported)[0]["kind"] == "导入计划"
    assert target.versions(imported)[0]["before_json"] == "{}"


def test_pause_resume_archive_and_expired_pause_are_replayable(tmp_path, monkeypatch):
    s = Store(tmp_path)
    pid = create(s)
    initial = versions(s, pid)[0]["id"]
    resume = date.today() + timedelta(days=4)
    s.apply_schedule(schedule_preview(s, [], date.today(), plan_id=pid, kind="暂停", resume_date=resume))
    paused = versions(s, pid)[0]["id"]
    history.apply(s, history.preview(s, pid, initial))
    assert not s.schedule(pid)["pause_start"]
    history.apply(s, history.preview(s, pid, paused))
    assert s.schedule(pid)["resume_date"] == resume.isoformat()
    assert s.read("SELECT COUNT(*) AS n FROM pause_history WHERE plan_id=?", (pid,))[0]["n"] == 2
    s.expire_pauses(resume)
    # Simulate selecting a pause whose resume day has already passed.
    row = s.versions(pid)[0]
    with s.write() as c:
        target = json.loads(row["after_json"])
        target["plan_schedules"][0].update(pause_start="2020-01-01", resume_date="2020-01-02")
        c.execute("UPDATE plan_versions SET after_json=? WHERE id=?", (json.dumps(target), row["id"]))
    p = history.preview(s, pid, row["id"])
    assert any("已到恢复日期" in n for n in p.notices)
    assert not p.changes
    s.archive(pid)
    history.apply(s, history.preview(s, pid, initial))
    assert not s.get_plan(pid)["archived"]


def test_manual_task_edit_and_old_snapshot_normalization(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    target = versions(s, pid)[0]
    old = json.loads(target["after_json"])
    old["version"] = 2
    for name in ("plan_briefs", "plan_schedules", "pause_history"):
        old.pop(name)
    for row in old["tasks"]:
        row.pop("manual")
        row.pop("ai_locked")
    with s.write() as c:
        c.execute("UPDATE plan_versions SET after_json=? WHERE id=?", (json.dumps(old), target["id"]))
    s.rename_plan(pid, "旧历史仍能正常查看")
    assert history.preview(s, pid, target["id"]).changes
    tid = s.add_manual_task(payload(), "2027-01-01", plan_id=pid)
    row = next(t for t in s.tasks(pid) if t["id"] == tid)
    edit(s, row, payload=row["task"].model_copy(update={"title": "修改手动任务内容"}))
    assert s.versions(pid)[0]["kind"] == "编辑手动任务"
