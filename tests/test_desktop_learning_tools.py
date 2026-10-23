from contextlib import closing
from datetime import date, timedelta
import json
import sqlite3

import pytest

from desktop.store import Store, SCHEMA_VERSION
from desktop import learning_tools as tools, plan_history
from desktop.scheduling import fingerprint, BudgetExceeded, PlanCapacityExceeded
from tests.test_desktop_store import create, edit
from tests.test_desktop_manual_tasks import payload


def rule(kind='daily', start='2027-01-04', end='2027-01-06', weekdays=None, intervals=None):
    return dict(kind=kind, start=start, end=end, weekdays=[0, 2, 4] if weekdays is None else weekdays,
                intervals=[] if intervals is None else intervals)


def data(stage='基础', done=False):
    return dict(stage=stage, steps=[dict(id='step-one', title='完成一个小步骤', done=done)])


def token(s):
    with closing(s.connect()) as c:
        return fingerprint(c)


def test_statistics_cohort_counts_exact_seconds_not_session_start(tmp_path):
    s = Store(tmp_path)
    tid = s.add_manual_task(payload(), '2027-01-04')
    r = s.tasks()[0]
    edit(s, r, status='已完成', manual_minutes=3)
    s.record_focus('stats', tid, 95, '2027-02-15T13:00:00')
    result = tools.statistics(s, date(2027, 1, 4), date(2027, 1, 5))
    assert result['total'] == result['done'] == 1
    assert result['actual_seconds'] == 275
    assert result['days'][1]['actual_seconds'] == 0
    assert '不代表' in result['summary'] and '未记录' in result['summary']
    assert tools.statistics(s, date(2027, 2, 15), date(2027, 2, 15))['actual_seconds'] == 0


def test_statistics_scopes_archived_paused_and_empty(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    s.add_manual_task(payload(), '2027-01-04')
    start, end = date(2026, 1, 1), date(2027, 1, 1)
    expected = tools.statistics(s, start, end, pid)['total']
    assert expected
    s.archive(pid)
    assert tools.statistics(s, start, end, pid)['total'] == 0
    assert tools.statistics(s, start, end, pid, True)['total'] == expected
    assert tools.statistics(s, date(2027, 1, 4), date(2027, 1, 4), '__standalone__')['total'] == 1
    with pytest.raises(ValueError):
        tools.statistics(s, end, start)


def test_steps_progress_no_extra_minutes_and_no_parent_autocomplete(tmp_path):
    s = Store(tmp_path)
    tid = s.add_manual_task(payload(), '2027-01-04')
    _, t = tools.task_details(s, tid)
    tools.save_details(s, tid, data(done=True), t)
    row = s.tasks()[0]
    assert row['status'] == '进行中' and row['manual_seconds'] == row['focus_seconds'] == 0
    tools.save_details(s, tid, data(done=False), token(s))
    assert s.tasks()[0]['status'] == '进行中'


def test_details_content_history_checkin_not_spammed_and_stale(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    tid = s.tasks(pid)[0]['id']
    initial = len(s.versions(pid))
    tools.save_details(s, tid, data(), token(s))
    assert len(s.versions(pid)) == initial + 1
    stale = token(s)
    tools.save_details(s, tid, data(done=True), stale)
    assert len(s.versions(pid)) == initial + 1
    with pytest.raises(ValueError, match='变化'):
        tools.save_details(s, tid, data(), stale)
    tools.assign_stage(s, [tid], '新的阶段', token(s))
    assert tools.task_details(s, tid)[0]['steps'][0]['done']


@pytest.mark.parametrize('invalid', [dict(stage='x'*41, steps=[]), dict(stage='', steps=[dict(id='a', title='', done=False)]),
    dict(stage='', steps=[dict(id='a', title='有步骤', done=1)]), dict(stage='', steps=[dict(id='a', title='一', done=False)]*2)])
def test_invalid_steps_rejected(invalid):
    with pytest.raises(ValueError):
        tools.validate_details(invalid)


def test_history_steps_replay_preserves_done_and_recorded_extras(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    tid = s.tasks(pid)[0]['id']
    base = s.versions(pid)[0]['id']
    tools.save_details(s, tid, data(), token(s))
    entry = s.versions(pid)[0]['id']
    tools.save_details(s, tid, data(done=True), token(s))
    p = plan_history.preview(s, pid, base)
    plan_history.apply(s, p)
    assert tools.task_details(s, tid)[0]['steps'][0]['done']
    assert tools.task_details(s, tid)[0]['stage'] == ''
    back = plan_history.preview(s, pid, entry)
    plan_history.apply(s, back)
    assert tools.task_details(s, tid)[0] == data(done=True)


@pytest.mark.parametrize('kind,expected', [('daily', ['2027-01-04','2027-01-05','2027-01-06']), ('weekly', ['2027-01-04','2027-01-06']), ('review', ['2027-01-04','2027-01-05'])])
def test_recurrence_dates(kind, expected):
    assert tools.recurrence_dates(rule(kind, intervals=[0,1,7])) == expected


@pytest.mark.parametrize('r', [rule(end='2028-01-05'), rule(end='2027-01-01'), rule('weekly', weekdays=[]), rule('review', intervals=[1,1]), rule('review', intervals=[-1]), rule('review', intervals=[True])])
def test_invalid_recurrence(r):
    with pytest.raises(ValueError):
        tools.recurrence_dates(r)


def test_cross_year_leap_day_and_weekdays():
    assert tools.recurrence_dates(rule(start='2028-02-28', end='2028-03-01')) == ['2028-02-28','2028-02-29','2028-03-01']
    assert tools.recurrence_dates(rule('weekly', start='2026-12-31', end='2027-01-04', weekdays=[0])) == ['2027-01-04']


def test_preview_readonly_apply_idempotence_and_data_conflict(tmp_path):
    s = Store(tmp_path)
    p = tools.preview_series(s, payload(), rule(), remind_at='20:00')
    assert not s.tasks() and not tools.series_entries(s)
    tools.apply_series(s, p)
    assert len(s.tasks()) == 3 and {r['id'] for r in s.tasks()} == set(p.task_ids)
    assert all(r['remind_at'] == '20:00' for r in s.tasks())
    with pytest.raises(ValueError):
        tools.apply_series(s, p)
    q = tools.preview_series(s, payload(), rule())
    s.set_setting('daily_budget', [100]*7)
    with pytest.raises(ValueError, match='变化'):
        tools.apply_series(s, q)


def test_series_budget_cancel_rollback_and_explicit_approval(tmp_path):
    s = Store(tmp_path)
    s.set_setting('daily_budget', [0]*7)
    p = tools.preview_series(s, payload(), rule())
    with pytest.raises(BudgetExceeded) as exc:
        tools.apply_series(s, p)
    assert not s.tasks() and not tools.series_entries(s)
    with s.accept_budget(exc.value.token):
        tools.apply_series(s, p)
    assert len(s.tasks()) == 3


def test_series_plan_confirmation_and_history_export_roundtrip(tmp_path):
    s = Store(tmp_path)
    pid = create(s)
    p = tools.preview_series(s, payload(), rule(), plan_id=pid)
    try:
        tools.apply_series(s, p)
    except PlanCapacityExceeded as error:
        with s.accept_plan_capacity(error.token):
            tools.apply_series(s, p)
    tools.save_details(s, p.task_ids[0], data(done=True), token(s))
    exported = s.export_plan(pid)
    assert json.loads(exported)['version'] ==9
    copy = s.import_plan(exported, date(2026,1,1), confirm_limits=True)
    assert len(s.tasks(copy)) == len(s.tasks(pid))
    assert len(tools.series_entries(s)) == 2
    Store.inspect_backup(s.path)


def test_stop_keeps_recorded_past_and_focus_and_is_atomic(tmp_path):
    s = Store(tmp_path)
    start = date.today() - timedelta(days=1)
    p = tools.preview_series(s, payload(), rule(start=start.isoformat(), end=(start+timedelta(days=4)).isoformat()))
    tools.apply_series(s, p)
    s.record_focus('protected', p.task_ids[1], 25, start.isoformat())
    s.set_setting('focus_state', dict(task_id=p.task_ids[2], session_id='active', phase='focus'))
    count, kept = tools.stop_series(s, p.series_id, token(s))
    assert (count, kept) == (2,2)
    assert {r['id'] for r in s.tasks()} == set(p.task_ids[:3])
    assert tools.series_entries(s)[0]['stopped']
    assert len(s.read('SELECT * FROM series_tasks')) == 5  # Tombstones never regenerate.


def test_schema5_staging_migration_and_backup_restore(tmp_path):
    s = Store(tmp_path)
    tid = s.add_manual_task(payload(), '2027-01-04')
    with closing(s.connect()) as c, c:
        for table in ('series_tasks', 'task_series', 'task_details'):
            c.execute('DROP TABLE ' + table)
        c.execute('PRAGMA user_version=5')
    before = s.read('SELECT * FROM tasks')
    reopened = Store(s.directory)
    assert reopened.tasks()[0]['id'] == tid
    old = next(reopened.backups.glob('before-schema-10-*.sqlite3'))
    with closing(sqlite3.connect(old)) as backup:
        backup.row_factory = sqlite3.Row
        assert [dict(r) for r in backup.execute('SELECT * FROM tasks')] == before
        assert backup.execute('PRAGMA user_version').fetchone()[0] == 5
    target = Store(tmp_path / 'restored')
    target.restore(old)
    assert target.read('PRAGMA user_version')[0]['user_version'] == SCHEMA_VERSION == 10
    assert target.tasks()[0]['id'] == tid


def test_schema6_backup_independent_series_steps_survive_restart(tmp_path):
    s = Store(tmp_path)
    p = tools.preview_series(s, payload(), rule())
    tools.apply_series(s, p)
    tools.save_details(s, p.task_ids[0], data(done=True), token(s))
    backup = s.manual_backup()
    target = Store(tmp_path / 'copy')
    target.restore(backup)
    assert tools.task_details(target, p.task_ids[0])[0] == data(done=True)
    assert tools.series_entries(target)[0]['id'] == p.series_id
    assert len(target.tasks()) == 3
