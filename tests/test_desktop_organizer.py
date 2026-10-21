"""Dated evidence, capture conversion and attention never overwrite learning."""
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
import json
import pytest

from desktop.store import Store, SCHEMA_VERSION
from desktop import organizer as svc
from desktop.focus import FocusTimer
from desktop.scheduling import fingerprint, BudgetExceeded
from desktop import trash
from tests.test_desktop_manual_tasks import payload
from tests.test_desktop_store import create, edit


def token(s):
    with closing(s.connect()) as c:return fingerprint(c)


def test_attention_history_replays_display_only_and_old_default_is_noop(tmp_path):
    from desktop import plan_history as h
    s=Store(tmp_path);pid=create(s);tid=s.tasks(pid)[0]['id']
    initial=s.versions(pid)[0]
    assert not h.preview(s,pid,initial['id'],'after').changes
    svc.set_attention(s,tid,priority=3,pinned=True,focus_date=date.today().isoformat())
    entry=s.versions(pid)[0]
    assert entry['kind']=='调整优先级与重点'
    svc.log_manual(s,tid,date.today(),7)
    records=s.read('SELECT * FROM study_events');tasks=s.read('SELECT * FROM tasks ORDER BY id')
    h.apply(s,h.preview(s,pid,entry['id'],'before'))
    assert s.tasks(pid)[0]['priority']==0 and s.tasks(pid)[0]['pinned']==0
    assert s.read('SELECT * FROM study_events')==records
    assert s.read('SELECT * FROM tasks ORDER BY id')==tasks
    assert not h.preview(s,pid,entry['id'],'before').changes


def test_remove_dated_manual_entry_preserves_status_and_other_evidence(tmp_path):
    s=Store(tmp_path);pid=create(s);tid=s.tasks(pid)[0]['id']
    first=svc.log_manual(s,tid,date.today(),8);svc.log_manual(s,tid,date.today(),3)
    s.record_focus('dated',tid,60,datetime.now().astimezone().isoformat(),day_seconds={date.today().isoformat():60})
    stale=token(s);s.set_setting('daily_budget',[None]*7)
    with pytest.raises(ValueError,match='变化'):svc.delete_manual_event(s,first,stale)
    svc.delete_manual_event(s,first,token(s))
    row=s.tasks(pid)[0]
    assert row['manual_seconds']==180 and row['focus_seconds']==60 and row['status']=='进行中'
    focus=next(r for r in s.read('SELECT * FROM study_events') if r['source']=='focus')
    with pytest.raises(ValueError,match='专注'):svc.delete_manual_event(s,focus['id'],token(s))
    Store.inspect_backup(s.path)


def test_scoped_reads_are_consistent_and_never_leak_after_scope(tmp_path):
    s=Store(tmp_path);create(s)
    with s.read_scope():
        rows=s.read('SELECT * FROM tasks ORDER BY id');rows[0]['note']='not stored'
        assert s.read('SELECT * FROM tasks ORDER BY id')[0]['note']!='not stored'
        with s.read_scope():assert s.tasks()
        with pytest.raises(ValueError,match='只读'):s.set_setting('scoped_write',True)
    s.set_setting('scoped_write',True);assert s.setting('scoped_write') is True


@pytest.mark.parametrize('schema',range(1,8))
def test_all_legacy_schemas_migrate_preserving_old_columns(tmp_path,schema):
    s=Store(tmp_path);pid=create(s)
    old=s.read('SELECT * FROM tasks ORDER BY id')
    with s.write(check_budget=False) as c:
        c.execute('DROP TABLE study_events');c.execute('DROP TABLE task_attention');c.execute('DROP TABLE captures')
        if schema<4:
            c.execute('DROP TABLE plan_schedules');c.execute('DROP TABLE pause_history')
        if schema<3:c.execute('ALTER TABLE planning_drafts DROP COLUMN created_at')
        if schema==1:
            for table in ('planning_drafts','planning_events','plan_briefs','plan_versions'):c.execute(f'DROP TABLE {table}')
            c.execute('ALTER TABLE tasks DROP COLUMN ai_locked')
        c.execute(f'PRAGMA user_version={schema}')
    migrated=Store(tmp_path)
    assert migrated.read('SELECT * FROM tasks ORDER BY id')==old
    assert migrated.read('PRAGMA user_version')[0]['user_version']==8
    Store.inspect_backup(migrated.path)


def test_old_trash_snapshot_migration_does_not_rewrite_original(tmp_path):
    s=Store(tmp_path);pid=create(s)
    s.delete_plan(pid)
    with s.write(check_budget=False) as c:
        for entry in c.execute('SELECT id,snapshot_json FROM trash').fetchall():
            rows=json.loads(entry[1])
            for table in svc.TASK_TABLES:rows.pop(table,None)
            c.execute('UPDATE trash SET snapshot_json=? WHERE id=?',(json.dumps(rows,ensure_ascii=False),entry[0]))
        for table in svc.TASK_TABLES+('captures',):c.execute(f'DROP TABLE {table}')
        c.execute('PRAGMA user_version=7')
    old=s.read('SELECT * FROM trash')
    Store.inspect_backup(s.path)
    s=Store(tmp_path)
    assert s.read('SELECT * FROM trash')==old
    proposal=trash.preview(s,old[0]['id']);trash.apply(s,proposal)
    assert len(s.tasks(pid))==4


def test_capture_not_a_task_or_load_and_conversion_stable_id(tmp_path):
    s=Store(tmp_path);pid=create(s)
    before=s.read('SELECT * FROM tasks ORDER BY id');loads=s.capacity()
    tid=svc.save_capture(s,payload(),plan_id=pid,priority=3,pinned=True)
    assert s.read('SELECT * FROM tasks ORDER BY id')==before and s.capacity()==loads
    assert svc.captures(s)[0]['id']==tid
    svc.schedule_capture(s,tid,'2026-11-02',plan_id=pid,token=token(s))
    r=next(r for r in s.tasks() if r['id']==tid)
    assert r['due_date']=='2026-11-02' and r['priority']==3 and r['pinned'] and r['status']=='未开始'
    assert not svc.captures(s)


def test_capture_stale_and_budget_cancel_leave_original(tmp_path):
    s=Store(tmp_path);tid=svc.save_capture(s,payload());old=token(s)
    s.set_setting('daily_budget',[0]*7)
    with pytest.raises(ValueError,match='变化'):svc.schedule_capture(s,tid,date.today().isoformat(),token=old)
    fresh=token(s)
    with pytest.raises(BudgetExceeded) as e:svc.schedule_capture(s,tid,date.today().isoformat(),token=fresh)
    assert svc.captures(s)[0]['id']==tid and not s.tasks()
    with s.accept_budget(e.value.token):svc.schedule_capture(s,tid,date.today().isoformat(),token=fresh)
    assert s.tasks()[0]['id']==tid and not svc.captures(s)


@pytest.mark.parametrize('priority,pinned,important',[(0,False,False),(3,True,True),(1,False,True)])
def test_attention_does_not_change_task_or_budget(tmp_path,priority,pinned,important):
    s=Store(tmp_path);create(s);r=s.tasks()[0]
    before=s.read('SELECT * FROM tasks ORDER BY id');loads=s.capacity()
    svc.set_attention(s,r['id'],priority=priority,pinned=pinned,focus_date=date.today().isoformat() if important else None)
    assert s.read('SELECT * FROM tasks ORDER BY id')==before and s.capacity()==loads
    updated=next(t for t in s.tasks() if t['id']==r['id'])
    assert updated['priority']==priority and bool(updated['pinned'])==pinned


def test_ordering_and_expired_focus_date(tmp_path):
    s=Store(tmp_path);create(s);a,b,c=s.tasks()[:3]
    svc.set_attention(s,c['id'],pinned=True)
    svc.set_attention(s,b['id'],priority=3,focus_date=date.today().isoformat())
    assert [r['id'] for r in svc.ordered(s.tasks())][:2]==[c['id'],b['id']]
    b=next(r for r in s.tasks() if r['id']==b['id'])
    assert '今天重点' in svc.label(b)
    assert '今天重点' not in svc.label(b,date.today()+timedelta(days=1))


def test_manual_event_actual_date_unknown_and_no_double_submission(tmp_path):
    s=Store(tmp_path);create(s);r=s.tasks()[0]
    edit(s,r,manual_minutes=5)
    s.record_focus('old',r['id'],60,'2026-10-12T08:00:00')
    yesterday=date.today()-timedelta(days=1)
    eid=svc.log_manual(s,r['id'],yesterday,20,entry_id='one-entry')
    report=svc.statistics(s,yesterday,date.today())
    assert report['actual_seconds']==1200 and report['unknown_seconds']==360
    assert report['active_days']==1 and report['longest_streak']==1
    assert report['days'][0]['manual_seconds']==1200
    r=next(t for t in s.tasks() if t['id']==r['id'])
    assert r['manual_seconds']==1500 and r['status']=='进行中'
    with pytest.raises(ValueError):svc.log_manual(s,r['id'],yesterday,20,entry_id=eid)
    with pytest.raises(ValueError,match='累计补记'):edit(s,r,manual_minutes=1)
    assert len(s.read('SELECT * FROM study_events'))==1


def test_focus_observed_seconds_cross_midnight_and_pause_gap(tmp_path):
    s=Store(tmp_path);tid=s.add_manual_task(payload(),date.today().isoformat())
    mono=[0.0];wall=[datetime(2026,10,20,23,59,57,tzinfo=timezone(timedelta(hours=8)))]
    f=FocusTimer(s,clock=lambda:mono[0],wall_clock=lambda:wall[0]);f.start(tid)
    mono[0]+=2;wall[0]+=timedelta(seconds=2);f.tick();f.pause()
    mono[0]+=86400;wall[0]+=timedelta(days=1);f.start(tid)
    mono[0]+=5;wall[0]+=timedelta(seconds=5);f.tick();f.pause()
    events=s.read('SELECT occurred_on,SUM(seconds) AS total FROM study_events GROUP BY occurred_on ORDER BY occurred_on')
    assert events==[{'occurred_on':'2026-10-20','total':2},{'occurred_on':'2026-10-21','total':1},{'occurred_on':'2026-10-22','total':4}]
    assert s.tasks()[0]['focus_seconds']==7
    f._commit();assert sum(r['seconds'] for r in s.read('SELECT * FROM study_events'))==7


def test_focus_sleep_not_recorded_as_learning(tmp_path):
    s=Store(tmp_path);mono=[0.0];f=FocusTimer(s,clock=lambda:mono[0]);f.start()
    mono[0]=300;assert f.tick()=='sleep'
    assert not s.read('SELECT * FROM study_events')


def test_dated_checkpoint_retry_is_idempotent(tmp_path):
    s=Store(tmp_path);pid=create(s);tid=s.tasks(pid)[0]['id'];day=date.today().isoformat()
    for _ in range(2):s.record_focus('retry',tid,60,datetime.now().isoformat(),day_seconds={day:60})
    assert s.tasks(pid)[0]['focus_seconds']==60 and len(s.read('SELECT * FROM study_events'))==1


def test_actual_dates_not_changed_by_rescheduling(tmp_path):
    s=Store(tmp_path);create(s);r=s.tasks()[0]
    svc.log_manual(s,r['id'],date.today(),10)
    before=s.read('SELECT * FROM study_events')
    edit(s,next(t for t in s.tasks() if t['id']==r['id']),due_date='2027-01-04')
    assert s.read('SELECT * FROM study_events')==before
    assert svc.statistics(s,date.today(),date.today())['actual_seconds']==600


def test_export_import_trash_and_full_backup_preserve_new_data(tmp_path):
    s=Store(tmp_path/'source');pid=create(s)
    tid=s.add_manual_task(payload(),'2026-11-02',plan_id=pid)
    svc.set_attention(s,tid,priority=2,pinned=True);svc.log_manual(s,tid,date.today(),7)
    capture=svc.save_capture(s,payload())
    exported=s.export_plan(pid)
    target=Store(tmp_path/'imported');newpid=target.import_plan(exported,date.today())
    assert svc.statistics(target,date.today(),date.today(),newpid)['actual_seconds']==420
    assert any(r['priority']==2 for r in target.tasks(newpid))
    s.delete_manual_task(tid)
    entry=next(e for e in trash.entries(s) if e['entity_id']==tid)
    trash.apply(s,trash.preview(s,entry['id']))
    assert next(r for r in s.tasks() if r['id']==tid)['manual_seconds']==420
    assert s.read('SELECT * FROM study_events WHERE task_id=?',(tid,))
    backup=s.manual_backup();s.restore(backup)
    assert svc.captures(s)[0]['id']==capture
    assert SCHEMA_VERSION==8 and s.read('PRAGMA user_version')[0]['user_version']==8


def test_migration_schema7_preserves_unknown_totals(tmp_path):
    s=Store(tmp_path);pid=create(s);r=s.tasks()[0];edit(s,r,manual_minutes=5)
    with closing(s.connect()) as c,c:
        for table in ('study_events','task_attention','captures'):c.execute('DROP TABLE '+table)
        c.execute('PRAGMA user_version=7')
    s=Store(tmp_path)
    assert list(s.backups.glob('before-schema-8-*.sqlite3'))
    assert svc.statistics(s,date.today(),date.today())['unknown_seconds']==300
    assert not s.read('SELECT * FROM study_events') and s.get_plan(pid)['tasks'][0]['id']==r['id']


def test_import_and_trash_reject_inflated_dated_evidence(tmp_path):
    s=Store(tmp_path);pid=create(s);tid=s.tasks()[0]['id'];svc.log_manual(s,tid,date.today(),2)
    data=json.loads(s.export_plan(pid));data['study_events'][0]['seconds']=999999
    with pytest.raises(ValueError,match='无法导入'):s.import_plan(json.dumps(data).encode(),date.today())
    assert len(s.list_plans())==1
    s.delete_plan(pid);entry=trash.entries(s)[0]
    with s.write(check_budget=False) as c:
        raw=json.loads(c.execute('SELECT snapshot_json FROM trash WHERE id=?',(entry['id'],)).fetchone()[0])
        raw['study_events'][0]['seconds']=999999
        c.execute('UPDATE trash SET snapshot_json=? WHERE id=?',(json.dumps(raw),entry['id']))
    p=trash.preview(s,entry['id']);assert p.conflicts and not s.tasks()
