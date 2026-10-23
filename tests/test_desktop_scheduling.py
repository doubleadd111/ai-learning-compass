from contextlib import closing
from datetime import date, datetime, timedelta
import json
import sqlite3

import pytest

from desktop.models import DesktopProfile
from desktop.scheduling import preview, paused, BudgetExceeded
from desktop.store import Store
from desktop.focus import FocusTimer
from desktop.planning import RevisionProposal, TaskChange, validate_proposal
from tests.test_progress_store import sample_profile, sample_plan
from tests.test_desktop_store import edit


def create(store, start=None, title="本地排期测试"):
    return store.save_plan(DesktopProfile.model_validate(sample_profile().model_dump()),
                          sample_plan().model_copy(update={"title": title}),
                          start or date.today() - timedelta(days=50), [0, 2])


def test_preview_readonly_apply_stable_ids_and_conditional_undo(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    original = s.export_plan(pid)
    ids = [r["id"] for r in s.tasks(pid)]
    p = preview(s, ids, date.today())
    assert p.changes and not p.conflicts
    assert s.export_plan(pid) == original
    s.apply_schedule(p)
    assert [r["id"] for r in s.tasks(pid)] == ids
    assert s.get_plan(pid)["profile"].plan_weeks == 2
    assert all(r["due_date"] >= date.today().isoformat() for r in s.tasks(pid))
    assert s.schedule(pid)["end_date"] >= s.tasks(pid)[-1]["due_date"]
    s.undo_latest(pid)
    assert [r["due_date"] for r in s.tasks(pid)] == [r["due_date"] for r in json.loads(original)["tasks"]]
    assert len(s.versions(pid)) == 3
    with pytest.raises(ValueError):
        s.undo_latest(pid)


@pytest.mark.parametrize("change", ["record", "lock", "started", "done", "reviewed"])
def test_protected_tasks_never_move(tmp_path, change):
    s = Store(tmp_path)
    pid = create(s)
    row = s.tasks(pid)[0]
    if change == "record":
        edit(s, row, note="记录保留", manual_minutes=5)
    elif change == "lock":
        s.set_task_lock(row["id"], True)
    elif change in {"started", "done"}:
        edit(s, row, status="进行中" if change == "started" else "已完成")
    else:
        with s.write() as c:
            c.execute("INSERT INTO reviews VALUES (?,1,'已经复盘')", (pid,))
    p = preview(s, [row["id"]], date.today())
    assert not p.changes and p.protected
    assert s.tasks(pid)[0]["due_date"] == row["due_date"]
    if change == "lock":
        p = preview(s, [row["id"]], date.today(), include_locked=True)
        s.apply_schedule(p)
        assert next(r for r in s.tasks(pid) if r["id"] == row["id"])["ai_locked"]


@pytest.mark.parametrize("mutation", ["other_plan", "budget", "record"])
def test_stale_preview_rejected_across_plans_and_policy(tmp_path, mutation):
    s = Store(tmp_path)
    a, b = create(s), create(s)
    p = preview(s, [r["id"] for r in s.tasks(a)], date.today())
    if mutation == "other_plan":
        s.rename_plan(b, "修改其他计划")
    elif mutation == "budget":
        s.set_setting("daily_budget", [60] * 7)
    else:
        edit(s, s.tasks(a)[0], note="预览后补记")
    before = s.export_plan(a)
    with pytest.raises(ValueError, match="已有变化"):
        s.apply_schedule(p)
    assert s.export_plan(a) == before


def test_budget_zero_unset_guard_rollback_and_explicit_confirmation(tmp_path):
    s = Store(tmp_path)
    s.set_setting("daily_budget", [0] * 7)
    with pytest.raises(BudgetExceeded) as error:
        create(s, date.today())
    assert not s.list_plans()
    with s.accept_budget(error.value.token):
        pid = create(s, date.today())
    assert all(r["over"] == r["total"] for r in s.capacity().values())
    # A record-only update does not demand repeated overload approvals.
    edit(s, s.tasks(pid)[0], status="已完成")
    assert any(r["total"] == 30 for r in s.capacity().values())
    s.set_setting("daily_budget", [None] * 7)
    create(s, date.today())
    assert not any(r["over"] for r in s.capacity().values())


@pytest.mark.parametrize("value", [[True] * 7, [-1] * 7, [1441] * 7, [60], "60"])
def test_invalid_budget_is_not_saved(tmp_path, value):
    s = Store(tmp_path)
    with pytest.raises(ValueError):
        s.set_setting("daily_budget", value)
    assert s.setting("daily_budget") is None


def test_budget_conflict_bounded_search_and_explicit_overload(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    s.set_setting("daily_budget", [0] * 7)
    ids = [r["id"] for r in s.tasks(pid)]
    original = s.export_plan(pid)
    p = preview(s, ids, date.today())
    assert len(p.conflicts) == 4
    with pytest.raises(ValueError):
        s.apply_schedule(p)
    assert s.export_plan(pid) == original
    p = preview(s, ids, date.today(), allow_overload=True)
    assert not p.conflicts and any(r["over"] for r in p.loads)
    with pytest.raises(BudgetExceeded) as error:
        s.apply_schedule(p)
    with s.accept_budget(error.value.token):
        s.apply_schedule(p)


def test_hard_plan_limits_still_apply_when_budget_overload_allowed(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    p = preview(s, [r["id"] for r in s.tasks(pid)], date.today(), allow_overload=True)
    s.apply_schedule(p)
    with closing(s.connect()) as c:
        s._check_plan_capacity(c, pid)
    due = s.tasks(pid)[0]["due_date"]
    with pytest.raises(ValueError, match="上限"):
        edit(s, s.tasks(pid)[1], due_date=due)


def test_pause_persists_skips_today_focus_reminders_and_restores(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    original = [r["id"] for r in s.tasks(pid)]
    original_dates = [r["due_date"] for r in s.tasks(pid)]
    until = date.today() + timedelta(days=7)
    p = preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=until)
    s.apply_schedule(p)
    assert paused(s.schedule(pid))
    assert [r["due_date"] for r in s.tasks(pid)] == original_dates
    assert s.tasks(due=(until + timedelta(days=30)).isoformat()) == []
    assert s.capacity() == {}
    with pytest.raises(ValueError, match="暂停"):
        FocusTimer(s).start(original[0])
    assert Store(tmp_path).due_reminders(datetime.now() + timedelta(days=1)) == []
    assert s.expire_pauses(until) == [pid]
    assert not paused(s.schedule(pid), until)
    assert s.expire_pauses(until) == []
    assert s.capacity()
    assert [r["id"] for r in s.tasks(pid)] == original


def test_early_resume_preview_is_readonly_and_undo_pause_keeps_records(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    until = date.today() + timedelta(days=14)
    s.apply_schedule(preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=until))
    before = s.export_plan(pid)
    p = preview(s, [], date.today(), kind="恢复", plan_id=pid)
    assert s.export_plan(pid) == before
    s.apply_schedule(p)
    assert not paused(s.schedule(pid))
    s.undo_latest(pid)
    assert paused(s.schedule(pid))
    edit(s, s.tasks(pid)[0], note="新学习记录")
    with pytest.raises(ValueError):
        s.undo_latest(pid)


@pytest.mark.parametrize("schema", [1, 2, 3])
def test_old_database_and_backup_migrate_without_mutation(tmp_path, schema):
    s = Store(tmp_path / "old")
    pid = create(s)
    ids = [r["id"] for r in s.tasks(pid)]
    with closing(sqlite3.connect(s.path)) as c, c:
        c.execute("DROP TABLE plan_schedules")
        c.execute("DROP TABLE pause_history")
        if schema < 3:
            c.execute("ALTER TABLE planning_drafts DROP COLUMN created_at")
        if schema == 1:
            for table in ("planning_drafts", "planning_events", "plan_briefs", "plan_versions"):
                c.execute(f"DROP TABLE {table}")
            c.execute("ALTER TABLE tasks DROP COLUMN ai_locked")
        c.execute(f"PRAGMA user_version={schema}")
    backup = tmp_path / "old.sqlite3"
    backup.write_bytes(s.path.read_bytes())
    before = backup.read_bytes()
    migrated = Store(s.directory)
    assert [r["id"] for r in migrated.tasks(pid)] == ids
    assert migrated.schedule(pid)["pause_start"] is None
    target = Store(tmp_path / "restored")
    target.restore(backup)
    assert target.export_plan(pid) == migrated.export_plan(pid)
    assert backup.read_bytes() == before
    assert list(migrated.backups.glob("before-schema-10-*.sqlite3"))


def test_schema_four_failure_preserves_database(tmp_path, monkeypatch):
    s = Store(tmp_path)
    create(s)
    with closing(sqlite3.connect(s.path)) as c, c:
        c.execute("DROP TABLE pause_history")
        c.execute("DROP TABLE plan_schedules")
        c.execute("PRAGMA user_version=3")
    original = s.path.read_bytes()
    def fail(c):
        c.execute("CREATE TABLE should_not_survive(id)")
        raise ValueError("模拟迁移失败")
    monkeypatch.setattr(Store, "_upgrade_v4", staticmethod(fail))
    with pytest.raises(ValueError):
        Store(tmp_path)
    assert s.path.read_bytes() == original


def test_new_json_roundtrip_pause_and_old_json_import(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    s.apply_schedule(preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=date.today() + timedelta(days=5)))
    payload = s.export_plan(pid)
    target = Store(tmp_path / "other")
    new_id = target.import_plan(payload, date.today())
    assert paused(target.schedule(new_id))
    assert target.read("SELECT * FROM pause_history")[0]["plan_id"] == new_id
    assert not {r["id"] for r in s.tasks(pid)} & {r["id"] for r in target.tasks(new_id)}
    old = json.loads(payload)
    old["version"] = 3
    del old["plan_schedules"], old["pause_history"]
    old_id = target.import_plan(json.dumps(old).encode(), date.today())
    assert target.schedule(old_id)["pause_start"] is None


def test_ai_date_validation_uses_extended_calendar_not_content_weeks(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    s.apply_schedule(preview(s, [r["id"] for r in s.tasks(pid)], date.today() + timedelta(days=28)))
    saved = s.get_plan(pid)
    row = saved["tasks"][0]
    proposal = RevisionProposal(summary="细化本地已有任务", changes=[TaskChange(task_id=row["id"], task=row["task"],
        due_date=row["due_date"], reason="保持已有日期")])
    validate_proposal(saved, proposal, [row["id"]], [])


def test_cross_plan_budget_includes_completed_excludes_archived_and_paused(tmp_path):
    s = Store(tmp_path)
    a, b = create(s, date.today()), create(s, date.today())
    r = s.tasks(a)[0]
    edit(s, r, status="已完成")
    assert s.capacity()[r["due_date"]]["total"] == 60
    s.archive(b)
    assert s.capacity()[r["due_date"]]["total"] == 30


def test_selected_only_does_not_move_future_and_cancel_is_noop(tmp_path):
    s = Store(tmp_path)
    pid = create(s, date.today() - timedelta(days=7))
    rows = s.tasks(pid)
    p = preview(s, [rows[0]["id"]], date.today())
    assert {r["id"] for r in p.changes} <= {rows[0]["id"]}
    before = s.export_plan(pid)
    assert before == s.export_plan(pid)
    assert all(date.fromisoformat(r["after"]).weekday() in (0, 2) for r in p.changes)


@pytest.mark.parametrize("anchor", [date(2026, 12, 28), date(2027, 2, 25), date(2028, 2, 25)])
def test_calendar_cross_year_month_and_leap_day(tmp_path, monkeypatch, anchor):
    import desktop.scheduling as scheduling
    class Calendar(date):
        @classmethod
        def today(cls):
            return anchor
    monkeypatch.setattr(scheduling, "date", Calendar)
    s = Store(tmp_path)
    pid = create(s, anchor - timedelta(days=50))
    p = preview(s, [r["id"] for r in s.tasks(pid)], anchor)
    assert not p.conflicts
    s.apply_schedule(p)
    assert all(date.fromisoformat(r["due_date"]).weekday() in (0, 2) for r in s.tasks(pid))
    with closing(s.connect()) as c:
        s._check_plan_capacity(c, pid)


def test_pause_does_not_redeliver_missed_reminders_and_new_dates_dedupe(tmp_path):
    s = Store(tmp_path)
    pid = create(s, date.today())
    row = s.tasks(pid)[0]
    edit(s, row, note="这个日期保留")
    until = date.today() + timedelta(days=7)
    s.apply_schedule(preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=until))
    s.expire_pauses(until)
    moment = datetime.combine(until + timedelta(days=30), datetime.min.time()).replace(hour=21)
    first = s.due_reminders(moment)
    assert row["id"] not in {r["id"] for r in first}
    assert first and s.due_reminders(moment) == []


def test_budget_confirmation_stale_when_other_plan_changes(tmp_path):
    s = Store(tmp_path)
    pid = create(s, date.today())
    s.set_setting("daily_budget", [30] * 7)
    with pytest.raises(BudgetExceeded) as error:
        create(s, date.today())
    s.rename_plan(pid, "别处已经改变")
    with s.accept_budget(error.value.token), pytest.raises(BudgetExceeded):
        create(s, date.today())
    assert len(s.list_plans()) == 1


def test_auto_resume_exposes_but_does_not_block_existing_overload(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    until = date.today() + timedelta(days=7)
    s.apply_schedule(preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=until))
    s.set_setting("daily_budget", [0] * 7)
    assert s.capacity() == {}
    s.expire_pauses(until)
    assert any(r["over"] for r in s.capacity().values())


def test_extended_schedule_still_supports_logical_weekly_review(tmp_path):
    from desktop.review import prepare_review
    from types import SimpleNamespace
    s = Store(tmp_path)
    pid = create(s)
    s.apply_schedule(preview(s, [r["id"] for r in s.tasks(pid)], date.today()))
    for row in s.tasks(pid):
        if row["week"] == 1:
            edit(s, row, status="已完成")
    calls = []
    def generate(profile, **kwargs):
        calls.append(profile)
        p = sample_plan().model_copy(update={"weekly_plans": sample_plan().weekly_plans[:1]})
        return p, None
    p = prepare_review(s, pid, SimpleNamespace(generate=generate))
    ids = {r["id"] for r in s.tasks(pid)}
    s.apply_review(pid, p["week"], p["summary"], p["adjusted"], p["revision"])
    assert calls and p["week"] == 1
    assert {r["id"] for r in s.tasks(pid)} == ids


def test_new_schedule_backup_roundtrip_and_corrupt_metadata_rejected(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    s.apply_schedule(preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=date.today() + timedelta(days=9)))
    s.set_setting("daily_budget", [45] * 7)
    backup = s.manual_backup()
    target = Store(tmp_path / "restore")
    target.restore(backup)
    assert target.export_plan(pid) == s.export_plan(pid)
    assert target.versions(pid) == s.versions(pid)
    assert target.setting("daily_budget") == [45] * 7
    with closing(sqlite3.connect(backup)) as c, c:
        c.execute("UPDATE plan_schedules SET resume_date='not-a-date'")
    before = target.export_plan(pid)
    with pytest.raises(ValueError):
        target.restore(backup)
    assert target.export_plan(pid) == before


@pytest.mark.parametrize("operation", ["import", "manual", "ai", "review", "unarchive", "undo", "early_resume"])
def test_all_schedule_write_paths_require_budget_confirmation(tmp_path, operation):
    from desktop.review import prepare_review
    from types import SimpleNamespace
    s = Store(tmp_path)
    pid = create(s, date.today())
    row = s.tasks(pid)[0]
    other = None
    if operation in {"manual", "ai", "review"}:
        other = create(s, date.today(), "同日保留任务")
    if operation == "unarchive":
        s.archive(pid)
    elif operation == "undo":
        s.apply_schedule(preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=date.today() + timedelta(days=7)))
    elif operation == "early_resume":
        s.apply_schedule(preview(s, [], date.today(), kind="暂停", plan_id=pid, resume_date=date.today() + timedelta(days=7)))
    elif operation == "review":
        for r in s.tasks(pid):
            if r["week"] == 1:
                edit(s, r, status="已完成")
    s.set_setting("daily_budget", [60] * 7 if other else [0] * 7)
    if operation == "import":
        action = lambda: s.import_plan(s.export_plan(pid), date.today())
    elif operation == "manual":
        action = lambda: edit(s, row, payload=row["task"].model_copy(update={"duration_minutes": 40}))
    elif operation == "ai":
        p = RevisionProposal(summary="增加练习时间", changes=[TaskChange(task_id=row["id"], due_date=row["due_date"],
            task=row["task"].model_copy(update={"duration_minutes": 40}), reason="增加练习")])
        action = lambda: s.apply_proposal(pid, p, [row["id"]], s.get_plan(pid)["revision"])
    elif operation == "review":
        def generate(profile, **kwargs):
            p = sample_plan().weekly_plans[0].model_copy(update={"tasks": [t.model_copy(update={"duration_minutes": 40}) for t in sample_plan().weekly_plans[0].tasks]})
            return sample_plan().model_copy(update={"weekly_plans": [p]}), None
        p = prepare_review(s, pid, SimpleNamespace(generate=generate))
        action = lambda: s.apply_review(pid, p["week"], p["summary"], p["adjusted"], p["revision"])
    elif operation == "unarchive":
        action = lambda: s.archive(pid, False)
    elif operation == "undo":
        action = lambda: s.undo_latest(pid)
    else:
        p = preview(s, [], date.today(), kind="恢复", plan_id=pid, allow_overload=True)
        action = lambda: s.apply_schedule(p)
    before = s.export_plan(pid)
    with pytest.raises(BudgetExceeded):
        action()
    assert s.export_plan(pid) == before


def test_just_started_focus_is_protected_before_first_second_commit(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    row = s.tasks(pid)[0]
    old = preview(s, [row["id"]], date.today())
    focus = FocusTimer(s, clock=lambda: 0)
    focus.start(row["id"])
    p = preview(s, [row["id"]], date.today())
    assert not p.changes and p.protected
    with pytest.raises(ValueError):
        s.apply_schedule(old)
    focus.stop()
