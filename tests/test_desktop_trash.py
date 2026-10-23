"""Trash recovery never replaces another entity or discards learning records."""
from contextlib import closing
from dataclasses import replace
from datetime import date, timedelta
import json
import sqlite3

import pytest

from desktop.store import Store, SCHEMA_VERSION
from desktop import trash
from desktop.learning_tools import save_details
from desktop.scheduling import fingerprint, BudgetExceeded, PlanCapacityExceeded
from tests.test_desktop_store import create, edit
from tests.test_desktop_manual_tasks import payload


def deleted(store, attached=False):
    pid = create(store) if attached else None
    tid = store.add_manual_task(payload(), '2026-12-01', plan_id=pid, remind_at='20:00')
    store.record_focus('kept-focus', tid, 122, '2026-12-01T12:00:00')
    edit(store, next(r for r in store.tasks() if r['id']==tid), note='保留学习笔记', manual_minutes=3)
    with closing(store.connect()) as c:
        token = fingerprint(c)
    save_details(store, tid, dict(stage='阅读', steps=[dict(id='step', title='记录要点', done=True)]), token)
    with store.write() as c:
        c.execute('INSERT INTO notifications VALUES (?,?,?)', (tid, 'sent', '2026-12-01T20:00:00'))
    original = store.read('SELECT * FROM tasks WHERE id=?', (tid,))[0]
    store.delete_manual_task(tid)
    return tid, pid, original, trash.entries(store)[0]['id']


@pytest.mark.parametrize('attached', [False, True])
def test_task_restore_roundtrip_records_and_preview_no_writes(tmp_path, attached):
    s = Store(tmp_path)
    tid, pid, original, entry = deleted(s, attached)
    before = s.path.read_bytes()
    p = trash.preview(s, entry)
    assert not p.conflicts and '专注 2 分钟' in p.summary
    assert s.path.read_bytes() == before and not s.read('SELECT * FROM tasks WHERE id=?', (tid,))
    assert trash.apply(s, p)
    assert s.read('SELECT * FROM tasks WHERE id=?', (tid,))[0] == original
    assert s.read('SELECT * FROM task_details WHERE task_id=?', (tid,))[0]['stage'] == '阅读'
    assert s.read('SELECT * FROM notifications WHERE task_id=?', (tid,))[0]['fingerprint'] == 'sent'
    assert s.read('SELECT * FROM focus_sessions WHERE task_id=?', (tid,))[0]['seconds'] == 122
    assert not trash.entries(s)
    assert Store.inspect_backup(s.manual_backup())['tasks'] >= 1


def test_plan_restore_all_relations_and_other_plan_unchanged(tmp_path):
    s = Store(tmp_path)
    pid, other = create(s), create(s)
    tid = s.tasks(pid)[0]['id']
    s.record_focus('focus', tid, 45, '2026-10-12T10:00:00')
    edit(s, s.tasks(pid)[0], status='已完成', note='保留记录')
    original = s.read('SELECT * FROM tasks WHERE plan_id=? ORDER BY id', (pid,))
    other_before = s.export_plan(other)
    backup = s.delete_plan(pid)
    entry = trash.entries(s)[0]['id']
    assert backup.exists() and s.get_plan(other)
    p = trash.preview(s, entry)
    assert not p.conflicts, p.conflicts
    assert trash.apply(s, p)
    assert s.read('SELECT * FROM tasks WHERE plan_id=? ORDER BY id', (pid,)) == original
    assert s.export_plan(other) == other_before
    assert s.read('SELECT seconds FROM focus_sessions WHERE id=?', ('focus',))[0]['seconds'] == 45
    assert s.read('SELECT * FROM plan_versions WHERE plan_id=? AND kind=?', (pid, '从回收站恢复计划'))
    assert Store.inspect_backup(s.manual_backup())['plans'] == 2


@pytest.mark.parametrize('change', ['task', 'budget', 'focus', 'steps', 'other-plan', 'reminder'])
def test_changes_invalidate_preview(tmp_path, change):
    s = Store(tmp_path)
    _, _, _, entry = deleted(s)
    tid = s.add_manual_task(payload(), '2026-12-02')
    p = trash.preview(s, entry)
    if change == 'task':
        edit(s, next(r for r in s.tasks() if r['id']==tid), note='新笔记')
    elif change == 'budget':
        s.set_setting('daily_budget', [99]*7)
    elif change == 'focus':
        s.record_focus('new-session', tid, 1, '2026-12-02T12:00:00')
    elif change == 'steps':
        with closing(s.connect()) as c:
            token = fingerprint(c)
        save_details(s, tid, dict(stage='新分组', steps=[]), token)
    elif change == 'other-plan':
        create(s)
    elif change == 'reminder':
        with s.write() as c:
            c.execute('INSERT INTO notifications VALUES (?,?,?)', (tid, 'new', '2026-12-02'))
    with pytest.raises(ValueError, match='变化'):
        trash.apply(s, p)
    assert len(trash.entries(s)) == 1


def test_parent_missing_then_restore_parent_and_task(tmp_path):
    s = Store(tmp_path)
    tid, pid, _, task_entry = deleted(s, True)
    s.delete_plan(pid)
    p = trash.preview(s, task_entry)
    assert '先从回收站恢复该计划' in p.conflicts[0]
    plan_entry = next(r['id'] for r in trash.entries(s) if r['kind']=='plan')
    assert trash.apply(s, trash.preview(s, plan_entry))
    assert trash.apply(s, trash.preview(s, task_entry))
    assert next(r for r in s.tasks(pid) if r['id']==tid)['note'] == '保留学习笔记'


def test_existing_id_never_overwritten(tmp_path):
    s = Store(tmp_path)
    tid, _, original, entry = deleted(s)
    with s.write() as c:
        names = list(original)
        original['note'] = '后来恢复的新笔记'
        c.execute(f'INSERT INTO tasks ({",".join(names)}) VALUES ({",".join("?" for _ in names)})', list(original.values()))
    p = trash.preview(s, entry)
    assert '同一任务已存在' in p.conflicts[0]
    with pytest.raises(ValueError):
        trash.apply(s, p)
    assert s.read('SELECT note FROM tasks WHERE id=?', (tid,))[0]['note'] == '后来恢复的新笔记'


def test_expiry_boundary_and_typed_purge(tmp_path, monkeypatch):
    s = Store(tmp_path)
    _, _, _, entry = deleted(s)
    until = trash.clock() + timedelta(days=30)
    from datetime import datetime
    until = datetime.fromisoformat(trash.entries(s)[0]['expires_at'])
    monkeypatch.setattr(trash, 'clock', lambda: until-timedelta(microseconds=1))
    assert not trash.preview(s, entry).conflicts
    monkeypatch.setattr(trash, 'clock', lambda: until)
    p = trash.preview(s, entry)
    assert '超过30天' in p.conflicts[0]
    for phrase in ('', '删除', '彻底删除 '):
        with pytest.raises(ValueError):
            trash.purge(s, entry, p.token, phrase)
    trash.purge(s, entry, p.token, '彻底删除')
    assert not trash.entries(s)


def test_budget_restore_cancel_and_confirm(tmp_path):
    s = Store(tmp_path)
    _, _, _, entry = deleted(s)
    s.set_setting('daily_budget', [0]*7)
    p = trash.preview(s, entry)
    assert p.budget_rows and not p.conflicts
    with pytest.raises(BudgetExceeded) as error:
        trash.apply(s, p)
    assert not s.tasks() and len(trash.entries(s)) == 1
    with s.accept_budget(error.value.token):
        assert trash.apply(s, p)


def test_attached_plan_limit_restore_requires_confirmation(tmp_path):
    s = Store(tmp_path)
    tid, pid, _, entry = deleted(s, True)
    # Fill the original day with a different task without moving current tasks.
    s.add_manual_task(payload(40), '2026-12-01', plan_id=pid)
    p = trash.preview(s, entry)
    assert p.plan_rows and not p.conflicts
    with pytest.raises(PlanCapacityExceeded) as error:
        trash.apply(s, p)
    assert not s.read('SELECT * FROM tasks WHERE id=?', (tid,))
    with s.accept_plan_capacity(error.value.token):
        assert trash.apply(s, p)


def test_snapshot_whitelist_and_atomic_failure(tmp_path):
    s = Store(tmp_path)
    _, _, _, entry = deleted(s)
    row = s.read('SELECT * FROM trash WHERE id=?', (entry,))[0]
    body = json.loads(row['snapshot_json'])
    assert not {'settings','connections'} & body.keys()
    body['settings'] = []
    with s.write() as c:
        c.execute('UPDATE trash SET snapshot_json=? WHERE id=?', (json.dumps(body), entry))
    p = trash.preview(s, entry)
    assert p.conflicts and not s.tasks()
    with pytest.raises(ValueError):
        Store.inspect_backup(s.manual_backup())


def test_schema6_migration_and_trash_backup_restart(tmp_path):
    s = Store(tmp_path / 'source')
    pid = create(s)
    original = s.export_plan(pid)
    with closing(s.connect()) as c, c:
        c.execute('DROP TABLE trash')
        c.execute('PRAGMA user_version=6')
    s = Store(s.directory)
    assert s.export_plan(pid) == original
    assert s.read('PRAGMA user_version')[0]['user_version'] == SCHEMA_VERSION == 10
    assert list(s.backups.glob('before-schema-10-*.sqlite3'))
    s.delete_plan(pid)
    backup = s.manual_backup()
    target = Store(tmp_path / 'target')
    target.restore(backup)
    target = Store(target.directory)
    p = trash.preview(target, trash.entries(target)[0]['id'])
    assert trash.apply(target, p)
    assert target.get_plan(pid)['title']


def test_migration_failure_preserves_original(tmp_path, monkeypatch):
    s = Store(tmp_path)
    create(s)
    with closing(s.connect()) as c, c:
        c.execute('DROP TABLE trash')
        c.execute('PRAGMA user_version=6')
    before = s.path.read_bytes()
    def fail(c):
        c.execute('CREATE TABLE trash (id TEXT)')
        raise ValueError('迁移失败')
    monkeypatch.setattr(Store, '_upgrade_v7', staticmethod(fail))
    with pytest.raises(ValueError, match='迁移失败'):
        Store(tmp_path)
    assert s.path.read_bytes() == before and not list(tmp_path.glob('upgrade-*.sqlite3'))


def test_deletion_capture_failure_rolls_back_every_record(tmp_path, monkeypatch):
    s = Store(tmp_path)
    pid = create(s)
    original = s.export_plan(pid)
    def fail(*args):
        raise ValueError('捕获失败')
    monkeypatch.setattr(trash, 'validate_entry', fail)
    with pytest.raises(ValueError, match='捕获失败'):
        s.delete_plan(pid)
    assert s.export_plan(pid)==original and not trash.entries(s)


def test_focus_checkpoint_alone_invalidates_preview(tmp_path):
    s = Store(tmp_path)
    _, _, _, entry = deleted(s)
    live = s.add_manual_task(payload(), '2026-12-02')
    p = trash.preview(s, entry)
    # No task/revision change: the checkpoint table still invalidates recovery.
    with s.write() as c:
        c.execute('INSERT INTO focus_sessions VALUES (?,?,?,?,?)', ('checkpoint', live, 1, '2026-12-02', '2026-12-02'))
    with pytest.raises(ValueError, match='变化'):
        trash.apply(s, p)


def test_restore_checks_expiry_again_at_commit(tmp_path, monkeypatch):
    s = Store(tmp_path)
    _, _, _, entry = deleted(s)
    p = trash.preview(s, entry)
    from datetime import datetime
    monkeypatch.setattr(trash, 'clock', lambda: datetime.fromisoformat(trash.entries(s)[0]['expires_at']))
    with pytest.raises(ValueError, match='超过30天'):
        trash.apply(s, p)
    assert not s.tasks() and trash.entries(s)


@pytest.mark.parametrize('attached', [False, True])
def test_recurring_task_restores_one_occurrence_without_restarting_rule(tmp_path, attached):
    from desktop import learning_tools as tools
    s = Store(tmp_path)
    pid = create(s) if attached else None
    # Use the public rule shape already covered by recurring-task regressions.
    from tests.test_desktop_learning_tools import rule as sample_rule
    p = tools.preview_series(s, payload(), sample_rule(), plan_id=pid)
    tools.apply_series(s, p)
    tid = p.task_ids[0]
    s.delete_manual_task(tid)
    with s.write() as c:
        c.execute('UPDATE task_series SET stopped=1 WHERE id=?', (p.series_id,))
    entry = trash.entries(s)[0]['id']
    assert trash.apply(s, trash.preview(s, entry))
    assert s.read('SELECT stopped FROM task_series WHERE id=?', (p.series_id,))[0]['stopped']==1
    assert s.read('SELECT task_id FROM series_tasks WHERE series_id=? ORDER BY scheduled_date', (p.series_id,))[0]['task_id']==tid


def test_stale_purge_cannot_remove_new_snapshot(tmp_path):
    s = Store(tmp_path)
    _, _, _, entry = deleted(s)
    p = trash.preview(s, entry)
    s.add_manual_task(payload(), '2026-12-02')
    with pytest.raises(ValueError, match='变化'):
        trash.purge(s, entry, p.token, '彻底删除')
    assert trash.entries(s)


def test_restored_plan_pause_and_notification_history_survive(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    tid = s.tasks(pid)[0]['id']
    with s.write() as c:
        c.execute('UPDATE plan_schedules SET pause_start=?,resume_date=? WHERE plan_id=?',
                  ('2026-10-20', '2026-10-28', pid))
        c.execute('INSERT INTO pause_history VALUES (?,?,?,?,?)', ('pause', pid, '2026-10-20', '2026-10-28', None))
        c.execute('INSERT INTO notifications VALUES (?,?,?)', (tid, 'already-sent', '2026-10-19T20:00:00'))
    schedule = s.schedule(pid)
    s.delete_plan(pid)
    p = trash.preview(s, trash.entries(s)[0]['id'])
    assert trash.apply(s, p)
    assert s.schedule(pid) == schedule
    assert s.read('SELECT * FROM pause_history')[0]['id']=='pause'
    assert s.read('SELECT * FROM notifications')[0]['fingerprint']=='already-sent'


def test_rule_position_conflict_rolls_back_task_and_records(tmp_path):
    from desktop import learning_tools as tools
    from tests.test_desktop_learning_tools import rule
    s = Store(tmp_path)
    p = tools.preview_series(s, payload(), rule())
    tools.apply_series(s, p)
    tid = p.task_ids[0]
    s.record_focus('old-focus', tid, 62, '2027-01-04T10:00:00')
    s.delete_manual_task(tid)
    entry = trash.entries(s)[0]['id']
    other = s.add_manual_task(payload(), '2027-01-04')
    with s.write() as c:
        c.execute('UPDATE series_tasks SET task_id=? WHERE series_id=? AND scheduled_date=?', (other, p.series_id, p.dates[0]))
    restored = trash.preview(s, entry)
    assert '原周期位置已有新任务' in restored.conflicts[0]
    assert not s.read('SELECT * FROM tasks WHERE id=?', (tid,))
    assert not s.read('SELECT * FROM focus_sessions WHERE task_id=?', (tid,))
    assert s.read('SELECT task_id FROM series_tasks WHERE series_id=? AND scheduled_date=?', (p.series_id, p.dates[0]))[0]['task_id']==other
