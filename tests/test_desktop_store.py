import hashlib
import json
from datetime import date, datetime, timedelta

import pytest

from desktop.models import DesktopProfile, ModelConnection, schedule_dates
from desktop.review import prepare_review
from desktop.store import Store
from plan_transfer import export_bundle
from progress_store import StudyRepository, TaskProgress
from tests.test_progress_store import sample_plan, sample_profile


def create(store, *, provenance=None):
    return store.save_plan(DesktopProfile.model_validate(sample_profile().model_dump()), sample_plan(),
                           date(2026, 10, 12), [0, 2], provenance=provenance)


def edit(store, row, **changes):
    values = dict(task_id=row["id"], payload=row["task"], due_date=row["due_date"], remind_at=row["remind_at"],
                  status=row["status"], manual_minutes=row["manual_seconds"] // 60,
                  difficulty=row["difficulty"], note=row["note"])
    values.update(changes)
    store.update_task(**values)


def test_schedule_uses_selected_weekdays_and_full_seven_day_windows():
    dates = schedule_dates(date(2026, 10, 15), [0, 3], 2)
    assert list(dates.values()) == ["2026-10-15", "2026-10-19", "2026-10-22", "2026-10-26"]


def test_restart_export_import_backup_restore_and_no_secrets(tmp_path):
    store = Store(tmp_path / "first")
    pid = create(store)
    row = store.tasks(pid)[0]
    edit(store, row, status="已完成", manual_minutes=5, note="完成练习")
    store.record_focus("session-one", row["id"], 60, "2026-10-12T10:00:00")
    store.record_focus("session-one", row["id"], 60, "2026-10-12T10:00:00")
    store.save_connection(ModelConnection(name="测试", base_url="https://example.invalid/v1", model="test"))
    reopened = Store(store.directory)
    assert reopened.progress(pid)[(1, 1)].actual_minutes == 6
    assert reopened.tasks(pid)[0]["id"] == row["id"]
    backup = reopened.manual_backup()
    reopened.rename_plan(pid, "修改后的计划")
    reopened.restore(backup)
    assert reopened.get_plan(pid)["title"] == sample_plan().title
    assert len(reopened.read("SELECT * FROM focus_sessions")) == 1
    exported = reopened.export_plan(pid)
    target = Store(tmp_path / "second")
    imported = target.import_plan(exported, date.today())
    assert imported != pid
    assert target.progress(imported)[(1, 1)].note == "完成练习"
    assert target.progress(imported)[(1, 1)].actual_minutes == 6
    assert target.tasks(imported)[0]["id"] != row["id"]
    assert b"api_key" not in exported
    assert list(reopened.backups.glob("daily-*.sqlite3"))
    assert list(reopened.backups.glob("before-restore-*.sqlite3"))


def test_import_bad_metadata_rolls_back_entire_plan(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    payload = json.loads(store.export_plan(pid))
    payload["tasks"][0]["remind_at"] = "not-a-time"
    with pytest.raises(ValueError, match="无法导入"):
        store.import_plan(json.dumps(payload).encode(), date.today())
    assert len(store.list_plans()) == 1


def test_legacy_database_import_is_readonly_and_preserves_records(tmp_path):
    legacy_path = tmp_path / "old.sqlite3"
    legacy = StudyRepository(legacy_path)
    pid = legacy.save_plan(sample_profile(), sample_plan())
    legacy.save_progress(pid, 1, 1, TaskProgress(status="已完成", actual_minutes=37, note="旧记录"))
    before = hashlib.sha256(legacy_path.read_bytes()).digest()
    store = Store(tmp_path / "new")
    imported = store.import_legacy_database(legacy_path, date(2026, 10, 12))[0]
    assert hashlib.sha256(legacy_path.read_bytes()).digest() == before
    assert store.get_plan(imported)["profile"].subject == "Python"
    assert store.progress(imported)[(1, 1)].actual_minutes == 37
    assert store.progress(imported)[(1, 1)].note == "旧记录"


def test_reminders_persist_and_reschedule_has_a_new_notification(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    moment = datetime(2026, 10, 12, 20, 1)
    assert len(store.due_reminders(moment)) == 1
    assert Store(tmp_path).due_reminders(moment) == []
    row = store.tasks(pid)[0]
    edit(store, row, remind_at="20:30")
    assert store.due_reminders(moment) == []
    assert len(store.due_reminders(moment.replace(hour=21))) == 1


def test_manual_rescheduling_respects_daily_and_weekly_limits(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    rows = store.tasks(pid)
    with pytest.raises(ValueError, match="单日时间"):
        edit(store, rows[1], due_date=rows[0]["due_date"])
    edit(store, rows[2], due_date="2026-10-16")
    with pytest.raises(ValueError, match="每周时间"):
        edit(store, rows[3], due_date="2026-10-17")


class FakeGateway:
    def generate(self, profile, **kwargs):
        plan = sample_plan().model_copy(update={"weekly_plans": [sample_plan().weekly_plans[0]]})
        return plan, {"connection_name": "测试连接", "model": "mock", "metrics": {"attempts": 1}}


def test_review_protects_edited_task_preserves_history_and_undo(tmp_path):
    store = Store(tmp_path)
    pid = create(store, provenance={"model": "mock", "metrics": {}})
    rows = store.tasks(pid)
    edit(store, rows[0], status="已完成", difficulty=4)
    edit(store, rows[1], status="延期", difficulty=4)
    protected = rows[2]["task"].model_copy(update={"title": "我亲自修改的任务"})
    edit(store, rows[2], payload=protected)
    store.record_focus("past-focus", rows[0]["id"], 20, "2026-10-12T10:00:00")
    preview = prepare_review(store, pid, FakeGateway())
    assert preview["adjusted"].weekly_plans[1].tasks[0] == protected
    store.apply_review(pid, preview["week"], preview["summary"], preview["adjusted"], preview["revision"], preview["provenance"])
    after = store.tasks(pid)
    assert after[2]["id"] == rows[2]["id"]
    assert after[3]["id"] == rows[3]["id"]
    assert store.reviews(pid)
    store.undo_review(pid)
    assert store.reviews(pid) == {}
    assert [t["id"] for t in store.tasks(pid)] == [t["id"] for t in rows]
    assert store.tasks(pid)[0]["focus_seconds"] == 20
    assert len(store.read("SELECT * FROM generation_events WHERE plan_id=?", (pid,))) == 1


def test_stale_preview_and_undo_cannot_erase_new_records(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    rows = store.tasks(pid)
    for row in rows[:2]:
        edit(store, row, status="已完成")
    preview = prepare_review(store, pid, FakeGateway())
    store.record_focus("new", rows[0]["id"], 5, "2026-10-12T10:00:00")
    with pytest.raises(ValueError, match="已有变化"):
        store.apply_review(pid, preview["week"], preview["summary"], preview["adjusted"], preview["revision"])
    preview = prepare_review(store, pid, FakeGateway())
    store.apply_review(pid, preview["week"], preview["summary"], preview["adjusted"], preview["revision"])
    edit(store, store.tasks(pid)[2], note="新的学习记录")
    with pytest.raises(ValueError, match="新记录"):
        store.undo_review(pid)


def test_broken_backup_does_not_replace_current_data(tmp_path):
    store = Store(tmp_path / "data")
    pid = create(store)
    invalid = tmp_path / "invalid.sqlite3"
    invalid.write_bytes(b"invalid")
    with pytest.raises(ValueError):
        store.restore(invalid)
    assert store.get_plan(pid)["title"] == sample_plan().title


def test_semantically_corrupt_backup_is_rejected_before_replace(tmp_path):
    import sqlite3
    store = Store(tmp_path / "data")
    pid = create(store)
    backup = store.manual_backup()
    with sqlite3.connect(backup) as connection:
        connection.execute("UPDATE tasks SET remind_at='invalid'")
    with pytest.raises(ValueError):
        store.restore(backup)
    assert store.tasks(pid)[0]["remind_at"] == "20:00"
