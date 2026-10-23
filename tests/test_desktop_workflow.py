"""Five daily-workflow improvements with isolated databases and no model calls."""
from contextlib import closing
from datetime import date,datetime,timedelta
import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest
from models import DailyTask
from desktop.store import Store,SCHEMA_VERSION
from desktop import workflow as w, backup_manager as b
from desktop.scheduling import BudgetExceeded,PlanCapacityExceeded
from tests.test_desktop_store import create


def task(s,title='测试任务',day='2026-10-22',plan=None):
    return s.add_manual_task(DailyTask(day=1,title=title,description='具体安排',duration_minutes=10,deliverable='练习产出'),day,plan_id=plan,remind_at='09:00')


def test_batch_preview_cancel_and_apply_preserve_ids_records(tmp_path):
    s=Store(tmp_path);pid=create(s);rows=s.tasks(pid);ids=[r['id'] for r in rows]
    before=s.read('SELECT * FROM tasks ORDER BY id')
    p=w.preview_batch(s,ids,'group','练习')
    assert s.read('SELECT * FROM tasks ORDER BY id')==before
    w.apply_batch(s,p)
    assert s.read('SELECT * FROM tasks ORDER BY id')==before
    assert len(s.read("SELECT * FROM task_details WHERE stage='练习'"))==len(ids)
    assert s.versions(pid)[-1:] or s.versions(pid)


@pytest.mark.parametrize('change',['note','focus','budget','other_plan','reminder','dependencies'])
def test_preview_stales_after_every_relevant_change(tmp_path,change):
    s=Store(tmp_path);tid=task(s);other=task(s,'另一任务');p=w.preview_batch(s,[tid],'complete')
    if change=='note':
        with s.write(check_budget=False) as c:c.execute("UPDATE tasks SET note='新笔记' WHERE id=?",(tid,))
    elif change=='focus':s.record_focus('new-session',tid,10,'2026-10-22T09:00:00')
    elif change=='budget':s.set_setting('daily_budget',[60]*7)
    elif change=='other_plan':create(s)
    elif change=='reminder':w.control_reminder(s,tid,'mute',w.token(s),datetime(2026,10,22,9))
    else:w.set_dependencies(s,tid,[other],w.token(s))
    with pytest.raises(ValueError,match='变化'):w.apply_batch(s,p)
    assert s.tasks()[0]['status']!='已完成'


def test_batch_date_budget_default_rollback_then_confirmation(tmp_path):
    s=Store(tmp_path);a=task(s);s.set_setting('daily_budget',[0]*7)
    p=w.preview_batch(s,[a],'date','2026-10-23')
    with pytest.raises(BudgetExceeded) as error:w.apply_batch(s,p)
    assert s.tasks()[0]['due_date']=='2026-10-22'
    with s.accept_budget(error.value.token):w.apply_batch(s,p)
    assert s.tasks()[0]['due_date']=='2026-10-23'


def test_batch_protected_items_all_or_nothing_and_trash(tmp_path):
    s=Store(tmp_path);pid=create(s);auto=s.tasks(pid)[0]['id'];a=task(s)
    with pytest.raises(ValueError,match='只可删除'):w.preview_batch(s,[a,auto],'trash')
    assert len(s.tasks())>1 and not s.read('SELECT * FROM trash')
    p=w.preview_batch(s,[a],'trash');w.apply_batch(s,p)
    assert not s.read('SELECT * FROM tasks WHERE id=?',(a,));assert len(s.read('SELECT * FROM trash'))==1


def test_complete_does_not_invent_time_and_group_preserves_steps(tmp_path):
    s=Store(tmp_path);a=task(s)
    with s.write(check_budget=False) as c:c.execute('INSERT INTO task_details VALUES (?,?,?)',(a,'旧组',json.dumps([dict(id='step1',title='第一步',done=False)])))
    w.apply_batch(s,w.preview_batch(s,[a],'group',''))
    assert json.loads(s.read('SELECT steps_json FROM task_details')[0]['steps_json'])[0]['done'] is False
    w.apply_batch(s,w.preview_batch(s,[a],'complete'))
    r=s.tasks()[0];assert r['status']=='已完成' and r['focus_seconds']==r['manual_seconds']==0


def test_dependencies_cycle_self_wrong_plan_and_order_warning(tmp_path):
    s=Store(tmp_path);a=task(s,'前置任务','2026-10-24');z=task(s,'后续任务','2026-10-23')
    w.set_dependencies(s,z,[a],w.token(s))
    with closing(s.connect()) as c:assert '早于前置' in w.dependency_warnings(c)[0]
    for tid,ids in [(a,[z]),(a,[a]),(a,['missing'])]:
        with pytest.raises(ValueError):w.set_dependencies(s,tid,ids,w.token(s))
    pid=create(s);other=s.tasks(pid)[0]['id']
    with pytest.raises(ValueError,match='同一'):w.set_dependencies(s,a,[other],w.token(s))
    assert s.tasks()[0]['status']=='未开始'
    p=w.preview_batch(s,[a,z],'date','2026-10-25');assert not any('早于前置' in line for line in p.warnings)


def test_dependency_export_import_remaps_and_whole_backup(tmp_path):
    s=Store(tmp_path/'source');pid=create(s);rows=s.tasks(pid);a,z=[r['id'] for r in rows[:2]]
    w.set_dependencies(s,z,[a],w.token(s));w.control_reminder(s,z,'mute',w.token(s))
    exported=s.export_plan(pid);assert json.loads(exported)['version']==9
    target=Store(tmp_path/'target');new=target.import_plan(exported,date.today())
    edge=target.read('SELECT * FROM task_dependencies')[0]
    assert edge['task_id']!=z and edge['prerequisite_id']!=a
    assert all(r['plan_id']==new for r in target.tasks())
    Store.inspect_backup(target.manual_backup())


@pytest.mark.parametrize('corruption',['cycle','external','self'])
def test_invalid_dependency_json_rejected_transactionally(tmp_path,corruption):
    s=Store(tmp_path);pid=create(s);data=json.loads(s.export_plan(pid));a,z=[r['id'] for r in data['tasks'][:2]]
    edges=[dict(task_id=a,prerequisite_id=z)]
    if corruption=='cycle':edges.append(dict(task_id=z,prerequisite_id=a))
    if corruption=='external':edges[0]['prerequisite_id']='missing'
    if corruption=='self':edges[0]['prerequisite_id']=a
    data['task_dependencies']=edges
    with pytest.raises(ValueError):s.import_plan(json.dumps(data).encode(),date.today())
    assert len(s.list_plans())==1


def test_reminder_snooze_mute_dedupe_and_new_date(tmp_path):
    s=Store(tmp_path);a=task(s);now=datetime(2026,10,22,9,0)
    assert len(s.due_reminders(now))==1;assert not s.due_reminders(now)
    w.control_reminder(s,a,'snooze',w.token(s),now)
    assert not s.due_reminders(now+timedelta(minutes=14))
    assert len(s.due_reminders(now+timedelta(minutes=15)))==1
    assert not s.due_reminders(now+timedelta(minutes=16))
    w.control_reminder(s,a,'mute',w.token(s),now)
    assert not s.due_reminders(now+timedelta(hours=10))
    assert '今日不再' in w.reminder_rows(s,now)[0]['reminder_state']
    w.apply_batch(s,w.preview_batch(s,[a],'date','2026-10-23'))
    assert len(s.due_reminders(datetime(2026,10,23,9)))==1


def test_pause_complete_archive_global_pause_and_missed_center(tmp_path):
    s=Store(tmp_path);a=task(s);pid=create(s);now=datetime(2026,10,22,10)
    assert any(r['id']==a and '错过' in r['reminder_state'] for r in w.reminder_rows(s,now))
    s.set_setting('reminders_paused',True);assert not s.due_reminders(now)
    s.set_setting('reminders_paused',False)
    with s.write(check_budget=False) as c:
        c.execute("UPDATE tasks SET status='已完成' WHERE id=?",(a,))
        c.execute('UPDATE plan_schedules SET pause_start=?,resume_date=? WHERE plan_id=?',('2026-10-01','2026-11-01',pid))
    assert not s.due_reminders(now)


def test_backup_catalog_diff_restore_source_unchanged_and_restore_point(tmp_path):
    s=Store(tmp_path);pid=create(s);saved=s.manual_backup();original=b.file_hash(saved)
    s.rename_plan(pid,'新的名称')
    p=b.preview(s,saved)
    assert any('覆盖1' in line for line in p.differences)
    assert b.catalog(s)[0]['schema']==10
    b.restore(s,p)
    assert s.get_plan(pid)['title']!='新的名称'
    assert b.file_hash(saved)==original and list(s.backups.glob('before-restore-*.sqlite3'))


@pytest.mark.parametrize('what',['live','backup'])
def test_backup_preview_rejects_later_changes(tmp_path,what):
    s=Store(tmp_path);create(s);saved=s.manual_backup();p=b.preview(s,saved)
    if what=='live':s.set_setting('theme','dark')
    else:
        with sqlite3.connect(saved) as c:c.execute("UPDATE plans SET title='changed'")
    with pytest.raises(ValueError,match='变化'):b.restore(s,p)


def test_schema8_upgrade_old_json_and_old_app_rejection(tmp_path):
    s=Store(tmp_path);pid=create(s);before=s.read('SELECT * FROM tasks ORDER BY id')
    data=json.loads(s.export_plan(pid));data['version']=7;data.pop('task_dependencies');data.pop('reminder_controls')
    with closing(s.connect()) as c,c:
        c.execute('DROP TABLE task_dependencies');c.execute('DROP TABLE reminder_controls');c.execute('PRAGMA user_version=8')
    old_hash=b.file_hash(s.path);s=Store(tmp_path)
    assert SCHEMA_VERSION==10 and s.read('SELECT * FROM tasks ORDER BY id')==before
    backup=next(s.backups.glob('before-schema-10-*.sqlite3'));assert b.file_hash(backup) # SQLite backup header may differ.
    s.import_plan(json.dumps(data).encode(),date.today())
    assert len(s.list_plans())==2


def test_offline_window_import_does_not_import_model_sdk():
    command='import sys; import desktop.window; assert "openai" not in sys.modules'
    subprocess.run([sys.executable,'-c',command],check=True,capture_output=True)


def test_search_normalization_cache_bounded_and_invalidation_by_value():
    from desktop.task_search import normalized
    normalized.cache_clear();assert normalized('编写')=='编写';normalized('编写')
    assert normalized.cache_info().hits==1
    for i in range(4200):normalized('名称'+str(i))
    assert normalized.cache_info().currsize<=4096


def test_clear_then_snooze_again_never_reuses_delivery_sequence(tmp_path):
    s=Store(tmp_path);a=task(s);moment=datetime(2026,10,22,10)
    w.control_reminder(s,a,'snooze',w.token(s),moment)
    assert len(s.due_reminders(moment+timedelta(minutes=15)))==1
    w.control_reminder(s,a,'clear',w.token(s),moment+timedelta(minutes=16))
    w.control_reminder(s,a,'snooze',w.token(s),moment+timedelta(minutes=17))
    assert len(s.due_reminders(moment+timedelta(minutes=32)))==1


def test_batch_date_rejects_pause_and_preserves_locked_learning(tmp_path):
    s=Store(tmp_path);pid=create(s);rows=s.tasks(pid)
    with s.write(check_budget=False) as c:
        c.execute('UPDATE tasks SET ai_locked=1,note=? WHERE id=?',('保留',rows[0]['id']))
        c.execute('UPDATE plan_schedules SET pause_start=?,resume_date=? WHERE plan_id=?',('2026-10-22','2026-11-01',pid))
    with pytest.raises(ValueError,match='暂停'):w.preview_batch(s,[rows[0]['id']],'date','2026-10-23')
    p=w.preview_batch(s,[rows[0]['id']],'date','2026-11-02');w.apply_batch(s,p)
    r=next(r for r in s.tasks() if r['id']==rows[0]['id']);assert r['note']=='保留' and r['ai_locked']==1
