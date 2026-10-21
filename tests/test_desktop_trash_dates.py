from contextlib import closing
from datetime import datetime
import json
import pytest
from desktop import trash
from desktop.store import Store
from desktop.scheduling import BudgetExceeded, PlanCapacityExceeded
from tests.test_desktop_trash import deleted
from tests.test_desktop_store import create
from tests.test_desktop_manual_tasks import payload


@pytest.mark.parametrize('attached', [False, True])
@pytest.mark.parametrize('target', ['2026-12-03', '2027-01-01', '2028-02-29'])
def test_chosen_date_only_changes_arrangement_preserves_records(tmp_path, attached, target):
    s = Store(tmp_path)
    tid, pid, original, entry = deleted(s, attached)
    snapshot = s.read('SELECT * FROM trash')[0]
    before = s.path.read_bytes()
    p = trash.preview(s, entry, target_date=target)
    assert not p.conflicts and p.target_date==target
    assert f'2026-12-01 → {target}' in p.summary
    assert s.path.read_bytes()==before and not s.read('SELECT * FROM tasks WHERE id=?',(tid,))
    assert trash.apply(s,p)
    assert s.read('SELECT * FROM tasks WHERE id=?',(tid,))[0]=={**original,'due_date':target}
    assert s.read('SELECT * FROM notifications WHERE task_id=?',(tid,))[0]['fingerprint']=='sent'
    assert s.read('SELECT * FROM focus_sessions WHERE task_id=?',(tid,))[0]['seconds']==122
    assert s.read('SELECT * FROM task_details WHERE task_id=?',(tid,))[0]['stage']=='阅读'


@pytest.mark.parametrize('target', ['2026-02-29','20261201','not-date',1])
def test_invalid_date_rejected_without_writes(tmp_path,target):
    s=Store(tmp_path)
    tid,_,_,entry=deleted(s)
    before=s.path.read_bytes()
    p=trash.preview(s,entry,target_date=target)
    assert p.conflicts and s.path.read_bytes()==before


def test_parent_start_and_pause_destination_explained(tmp_path):
    s=Store(tmp_path)
    _,pid,_,entry=deleted(s,True)
    assert '早于' in trash.preview(s,entry,target_date='2026-01-01').conflicts[0]
    with s.write() as c:
        c.execute('UPDATE plan_schedules SET pause_start=?,resume_date=? WHERE plan_id=?',('2026-12-01','2026-12-08',pid))
    assert '暂停期间' in trash.preview(s,entry,target_date='2026-12-03').conflicts[0]
    assert not trash.preview(s,entry,target_date='2026-12-08').conflicts
    assert not trash.preview(s,entry).conflicts  # Original recovery is not a move.


@pytest.mark.parametrize('global_budget',[False,True])
def test_selected_day_budget_uses_new_date_and_confirms(tmp_path,global_budget):
    s=Store(tmp_path)
    tid,pid,_,entry=deleted(s,not global_budget)
    if global_budget:
        s.set_setting('daily_budget',[0]*7)
    else:
        s.add_manual_task(payload(40),'2026-12-03',plan_id=pid)
    p=trash.preview(s,entry,target_date='2026-12-03')
    error_type=BudgetExceeded if global_budget else PlanCapacityExceeded
    with pytest.raises(error_type) as err:
        trash.apply(s,p)
    assert len(trash.entries(s))==1 and not s.read('SELECT * FROM tasks WHERE id=?',(tid,))
    with (s.accept_budget(err.value.token) if global_budget else s.accept_plan_capacity(err.value.token)):
        assert trash.apply(s,p)
    assert s.read('SELECT due_date FROM tasks WHERE id=?',(tid,))[0]['due_date']=='2026-12-03'


def test_plan_date_cannot_collapse_all_tasks(tmp_path):
    s=Store(tmp_path)
    pid=create(s)
    s.delete_plan(pid)
    entry=trash.entries(s)[0]['id']
    p=trash.preview(s,entry,target_date='2026-12-03')
    assert '仅适用于单项任务' in p.conflicts[0]
    assert not s.tasks()


def test_detail_uses_deleted_snapshot_not_live_task_and_no_writes(tmp_path):
    s=Store(tmp_path)
    tid,_,original,entry=deleted(s)
    with s.write() as c:
        live={**original,'note':'当前同ID的新笔记'}
        c.execute(f'INSERT INTO tasks ({",".join(live)}) VALUES ({",".join("?" for _ in live)})',list(live.values()))
    before=s.path.read_bytes()
    detail=trash.detail(s,entry)
    assert detail['rows']['tasks'][0]['note']=='保留学习笔记'
    assert s.path.read_bytes()==before


def test_expired_detail_can_view_but_no_recovery_or_extended_deadline(tmp_path,monkeypatch):
    s=Store(tmp_path)
    _,_,_,entry=deleted(s)
    end=trash.entries(s)[0]['expires_at']
    monkeypatch.setattr(trash,'clock',lambda:datetime.fromisoformat(end))
    before=s.path.read_bytes()
    assert trash.detail(s,entry)['entry']['expires_at']==end
    assert trash.preview(s,entry,target_date='2026-12-03').conflicts
    assert s.path.read_bytes()==before


def test_changed_budget_invalidates_selected_date_preview(tmp_path):
    s=Store(tmp_path)
    _,_,_,entry=deleted(s)
    p=trash.preview(s,entry,target_date='2026-12-03')
    s.set_setting('daily_budget',[60]*7)
    with pytest.raises(ValueError,match='变化'):
        trash.apply(s,p)


def test_recurring_origin_does_not_change_when_instance_restored_elsewhere(tmp_path):
    from desktop import learning_tools as tools
    from tests.test_desktop_learning_tools import rule
    s=Store(tmp_path)
    p=tools.preview_series(s,payload(),rule())
    tools.apply_series(s,p)
    tid=p.task_ids[0]
    s.delete_manual_task(tid)
    entry=trash.entries(s)[0]['id']
    trash.apply(s,trash.preview(s,entry,target_date='2027-02-01'))
    r=s.read('SELECT * FROM series_tasks WHERE task_id=?',(tid,))[0]
    assert r['scheduled_date']==p.dates[0]
    assert s.read('SELECT due_date FROM tasks WHERE id=?',(tid,))[0]['due_date']=='2027-02-01'


def test_new_date_reminder_is_due_once_without_removing_old_history(tmp_path):
    s=Store(tmp_path)
    tid,_,_,entry=deleted(s)
    trash.apply(s,trash.preview(s,entry,target_date='2026-12-03'))
    due=s.due_reminders(datetime.fromisoformat('2026-12-03T21:00:00'))
    assert tid in {r['id'] for r in due}
    assert not s.due_reminders(datetime.fromisoformat('2026-12-03T21:00:00'))
    assert s.read('SELECT * FROM notifications WHERE task_id=? AND fingerprint=?',(tid,'sent'))
