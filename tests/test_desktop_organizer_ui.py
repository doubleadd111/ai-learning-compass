from datetime import date,timedelta
import pytest
from PySide6.QtCore import Qt,QDate
from PySide6.QtWidgets import QApplication,QDialog
from desktop.store import Store
from desktop.credentials import CredentialStore
from desktop.window import MainWindow
from desktop.theme import apply_theme
from desktop import organizer as svc
from desktop.organizer_ui import QuickAddDialog,ActualStatisticsDialog,token,AttentionDialog
from desktop.month_view import MonthDialog,MovePreviewDialog
from desktop.capture_hotkey import parse_shortcut,CaptureHotkey
from tests.test_desktop_manual_tasks import payload
from tests.test_desktop_store import create


@pytest.fixture
def host(qtbot,tmp_path):
    apply_theme(QApplication.instance(),'light');s=Store(tmp_path);create(s)
    s.set_setting('reminders_paused',True)
    w=MainWindow(s,credentials=CredentialStore(object()),enable_tray=False)
    qtbot.addWidget(w);w.show()
    yield w
    w.exiting=True;w.tools.close_all();w.close()


def test_quick_add_pending_and_selected_date(host,qtbot):
    before=len(host.store.tasks())
    d=host.quick_capture();d.title.setText('需要稍后安排');d.accept()
    assert len(svc.captures(host.store))==1 and len(host.store.tasks())==before
    d=host.quick_capture();d.title.setText('明确日期的独立任务');d.scheduled.setChecked(True)
    d.accept()
    assert len(host.store.tasks())==before+1
    assert any(r['task'].title=='明确日期的独立任务' for r in host.store.tasks('__standalone__'))


def test_quick_add_cancel_does_not_write(host):
    d=host.quick_capture();d.title.setText('取消不写入');d.reject()
    assert not svc.captures(host.store)


def test_inbox_search_and_schedule_preserves_id(host):
    tid=svc.save_capture(host.store,payload().model_copy(update={'title':'查询目标事项'}))
    svc.save_capture(host.store,payload().model_copy(update={'title':'其他事项'}))
    d=host.open_capture_inbox();d.search.setText('查询目标')
    assert d.items.topLevelItemCount()==1
    d.items.setCurrentItem(d.items.topLevelItem(0));d.schedule()
    editor=host.tools.windows[('capture_schedule',tid)]
    editor.accept();assert not any(r['id']==tid for r in svc.captures(host.store))
    assert any(r['id']==tid for r in host.store.tasks())


def test_month_drop_opens_preview_cancel_no_write_and_stale_rejected(host,qtbot):
    s=host.store;tid=s.add_manual_task(payload(),date.today().isoformat());m=host.open_month()
    before=s.read('SELECT * FROM tasks ORDER BY id')
    p=m.request_move(tid,date.today()+timedelta(days=1),token(s))
    assert p.proposal and p.apply.isEnabled() and s.read('SELECT * FROM tasks ORDER BY id')==before
    assert p.parentWidget() is None and not p.isModal()
    p.reject();assert s.read('SELECT * FROM tasks ORDER BY id')==before
    old=token(s);svc.set_attention(s,tid,priority=3)
    assert m.request_move(tid,date.today()+timedelta(days=2),old) is None
    assert '变化' in m.error.text()


def test_month_preview_commit_and_protected_completed(host):
    s=host.store;tid=s.add_manual_task(payload(),date.today().isoformat());m=host.open_month()
    target=date.today()+timedelta(days=2)
    p=m.request_move(tid,target,token(s));p.accept()
    assert next(r for r in s.tasks() if r['id']==tid)['due_date']==target.isoformat()
    with s.write() as c:c.execute("UPDATE tasks SET status='已完成' WHERE id=?",(tid,))
    d=MovePreviewDialog(s,tid,target+timedelta(days=1));m.tools_unused=d
    assert not d.apply.isEnabled() and '保留' in d.output.toPlainText()
    d.deleteLater()


def test_month_filter_does_not_change_global_budget(host):
    s=host.store;svc.save_capture(s,payload(),due=date.today().isoformat())
    m=host.open_month();loads=m.loads
    m.plan.setCurrentIndex(m.plan.findData('__standalone__'))
    assert m.loads==loads and all(r['plan_id'] is None for r in m.rows)
    assert len(m.cells)==42


def test_priority_today_filter_preserves_records_and_ignores_expired(host):
    s=host.store;tid=s.add_manual_task(payload(),date.today().isoformat())
    svc.set_attention(s,tid,priority=3,pinned=True,focus_date=date.today().isoformat());host.refresh()
    host.today_important.setChecked(True)
    assert host.today_tree.topLevelItemCount()==1
    assert '今天重点' in host.today_tree.topLevelItem(0).text(0)
    item=host.today_tree.topLevelItem(0);d=host.open_attention(item)
    d.priority.setCurrentIndex(1);d.accept()
    r=next(t for t in s.tasks() if t['id']==tid)
    assert r['status']=='未开始' and r['manual_seconds']==r['focus_seconds']==0 and r['due_date']==date.today().isoformat()


def test_actual_statistics_uses_record_date_and_weekly_totals(host):
    s=host.store;r=s.tasks()[0];svc.log_manual(s,r['id'],date.today(),13)
    d=host.open_actual_statistics()
    assert d.report['actual_seconds']==780 and d.report['active_days']==1
    assert d.calendar.minutes[date.today().isoformat()]==13
    assert d.daily.topLevelItemCount() in (28,29,30,31) and d.weekly.topLevelItemCount() in (4,5,6)
    assert d.parentWidget() is None and not d.isModal()
    d.close();assert host.open_actual_statistics() is not d


@pytest.mark.parametrize('theme',['light','dark'])
@pytest.mark.parametrize('kind',['quick','month','actual','attention'])
def test_new_tools_small_window_layout_and_palette(host,qtbot,theme,kind):
    apply_theme(QApplication.instance(),theme)
    if kind=='quick':d=host.quick_capture()
    elif kind=='month':d=host.open_month()
    elif kind=='actual':d=host.open_actual_statistics()
    else:d=host.tools.open('attention_test',lambda:AttentionDialog(host.store,host.store.tasks()[0]['id'],host))
    d.resize(640,520);qtbot.wait(15)
    assert not d.grab().isNull() and d.width()<=660
    assert d.box.contentsMargins().left()==24


@pytest.mark.parametrize('text,expected',[('Ctrl+Alt+N',(3,78)),('Alt+F8',(1,119)),('Win+Shift+1',(12,49))])
def test_shortcut_parse(text,expected):assert parse_shortcut(text)==expected


@pytest.mark.parametrize('text',['N','Shift+N','Ctrl+Ctrl+N','Ctrl+Alt','Ctrl+F25','Ctrl+Alt+汉','Ctrl+Alt+'])
def test_shortcut_invalid(text):
    with pytest.raises(ValueError):parse_shortcut(text)


def test_shortcut_conflict_keeps_previous_without_real_registration(qapp):
    class Fake:
        taken=False
        def RegisterHotKey(self,hwnd,ident,mod,vk):return not self.taken or vk==78
        def UnregisterHotKey(self,*args):return True
    api=Fake();key=CaptureHotkey(lambda:None,api)
    key.configure(True,'Ctrl+Alt+N');old=key.active;api.taken=True
    with pytest.raises(ValueError,match='占用'):key.configure(True,'Ctrl+Alt+M')
    assert key.active==old
    key.configure(False,'');assert key.active is None;key.close()


def test_runtime_refresh_defers_week_until_navigation(qtbot,tmp_path):
    s=Store(tmp_path);create(s);s.set_setting('reminders_paused',True)
    w=MainWindow(s,credentials=CredentialStore(object()),enable_tray=False,defer_hidden=True)
    qtbot.addWidget(w)
    assert 2 in w._dirty_pages and not hasattr(w.week_panel,'all_rows')
    w.navigate(2);assert 2 not in w._dirty_pages and hasattr(w.week_panel,'all_rows')
    w.navigate(0);w.refresh();assert 2 in w._dirty_pages
    w.exiting=True;w.close()


@pytest.mark.parametrize('operation',['attention','study_log'])
def test_new_context_actions_reuse_correct_task_peer_without_saving(host,operation):
    tid=host.store.tasks()[1]['id'];before=host.store.read('SELECT * FROM tasks ORDER BY id')
    host.run_task_action(tid,operation)
    d=host.tools.windows[(operation,tid)]
    assert d.task_id==tid and d.parentWidget() is None and not d.isModal()
    host.run_task_action(tid,operation);assert host.tools.windows[(operation,tid)] is d
    d.reject();assert host.store.read('SELECT * FROM tasks ORDER BY id')==before


def test_frozen_context_check_with_deferred_hidden_pages(host):
    from desktop.task_context_check import check_task_context
    host.defer_hidden=True;host.navigate(0)
    report=check_task_context(host)
    assert report['success'] and report['actions']==12


def test_manual_event_remove_requires_confirmation_and_focus_is_disabled(host,monkeypatch):
    from desktop.organizer_ui import StudyLogDialog
    from desktop.confirmations import ConfirmationDialog
    tid=host.store.tasks()[0]['id'];svc.log_manual(host.store,tid,date.today(),5)
    host.store.record_focus('ui-focus',tid,60,'2026-10-21T10:00:00',day_seconds={date.today().isoformat():60})
    d=host.tools.open(('study_log',tid),lambda:StudyLogDialog(host.store,tid,host))
    for i in range(d.records.topLevelItemCount()):
        item=d.records.topLevelItem(i);d.records.setCurrentItem(item)
        assert d.remove_button.isEnabled()==(item.text(1)=='人工补记')
        if item.text(1)=='人工补记':manual=item
    d.records.setCurrentItem(manual)
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda _:QDialog.DialogCode.Rejected)
    before=host.store.read('SELECT * FROM study_events ORDER BY id');d.remove_entry()
    assert host.store.read('SELECT * FROM study_events ORDER BY id')==before
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda _:QDialog.DialogCode.Accepted)
    d.remove_entry();assert host.store.tasks()[0]['manual_seconds']==0 and host.store.tasks()[0]['focus_seconds']==60
