"""Task-sized focus and presentation-only search of grouped tasks."""
from datetime import date
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from desktop.credentials import CredentialStore
from desktop.focus import FocusTimer
from desktop.learning_ui import StructureDialog
from desktop.learning_tools import task_details, assign_stage
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.window import MainWindow
from tests.test_desktop_manual_tasks import payload
from tests.test_desktop_store import create, edit


@pytest.fixture
def workspace(qtbot, tmp_path):
    apply_theme(QApplication.instance(), 'light')
    store = Store(tmp_path)
    store.set_setting('reminders_paused', True)
    create(store)
    w = MainWindow(store, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.exiting = True
    w.close()


@pytest.mark.parametrize('minutes', [5, 30, 45, 240])
def test_new_focus_uses_full_task_duration_not_25_or_old_usage(workspace, minutes):
    w = workspace
    tid = w.store.add_manual_task(payload().model_copy(update={'duration_minutes': minutes}), date.today().isoformat())
    w.store.record_focus('older-session', tid, 60, '2026-10-20T12:00:00')
    w.focus_minutes.setValue(25)
    w.break_minutes.setValue(7)
    w.start_focus_for_task(tid)
    assert w.focus.task_id == tid and w.focus.running
    assert w.focus.focus_minutes == w.focus_minutes.value() == minutes
    assert minutes*60-1 <= w.focus.remaining <= minutes*60
    assert w.focus.break_minutes == 7
    assert w.store.tasks('__standalone__')[0]['focus_seconds'] == 60
    assert w.store.tasks('__standalone__')[0]['status'] == '进行中'


def test_same_task_pause_and_restart_resume_without_reset(workspace):
    w = workspace
    tid = w.store.tasks()[0]['id']
    w.start_focus_for_task(tid)
    w.focus.remaining -= 90
    w.focus.elapsed = 90
    w.pause_focus()
    remaining, session = w.focus.remaining, w.focus.session_id
    w.focus = FocusTimer(w.store)
    w.start_focus_for_task(tid)
    assert w.focus.running and w.focus.session_id == session
    assert remaining-1 <= w.focus.remaining <= remaining
    w.start_focus_for_task(tid)
    w.pause_focus()
    assert next(t for t in w.store.tasks() if t['id']==tid)['focus_seconds'] == 90


def test_switch_cancel_keeps_session_and_confirm_uses_new_duration(workspace, monkeypatch):
    w = workspace
    first, second = w.store.tasks()[:2]
    w.start_focus_for_task(first['id'])
    w.focus.elapsed = 80
    session, minutes = w.focus.session_id, w.focus_minutes.value()
    seen = []
    def cancel(*args):
        seen.append(args[-1])
        return QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, 'question', cancel)
    w.start_focus_for_task(second['id'])
    assert seen == [QMessageBox.StandardButton.Cancel]
    assert w.focus.session_id == session and w.focus_minutes.value() == minutes
    monkeypatch.setattr(QMessageBox, 'question', lambda *_: QMessageBox.StandardButton.Yes)
    w.start_focus_for_task(second['id'])
    assert w.focus.task_id == second['id'] and w.focus.focus_minutes == second['task'].duration_minutes
    assert w.store.tasks()[0]['focus_seconds'] == 80


def test_focus_page_selected_task_and_unassociated_manual_duration(workspace):
    w = workspace
    task = w.store.tasks()[0]
    w.focus_task.setCurrentIndex(w.focus_task.findData(task['id']))
    w.focus_minutes.setValue(25)
    w.start_focus()
    assert w.focus.focus_minutes == task['task'].duration_minutes
    w.stop_focus()
    w.focus_task.setCurrentIndex(0)
    w.focus_minutes.setValue(19)
    w.start_focus()
    assert w.focus.task_id is None and w.focus.focus_minutes == 19


@pytest.mark.parametrize('entry', ['today', 'library', 'week'])
def test_task_buttons_all_route_to_task_sized_focus(workspace, qtbot, monkeypatch, entry):
    w = workspace
    task = w.store.tasks()[0]
    if entry=='today':
        # A fixed empty sample day avoids colliding with another task as the
        # real calendar advances; this case tests routing, not overbooking.
        class Today(date):
            @classmethod
            def today(cls):
                return cls(2026, 10, 13)
        monkeypatch.setattr('desktop.window.date', Today)
        edit(w.store,task,due_date=Today.today().isoformat())
    w.refresh()
    w.focus_minutes.setValue(25)
    if entry=='week':
        w.navigate(2)
        panel=w.week_panel
        day=date.fromisoformat(task['due_date'])
        from datetime import timedelta
        panel.refresh(day-timedelta(days=day.weekday()))
        panel.mode.setCurrentIndex(1)
        table,col=panel.table,6
    else:
        w.navigate(0 if entry=='today' else 1)
        table,col=(w.today_tree if entry=='today' else w.plan_tree),4
    item=next(table.topLevelItem(i) for i in range(table.topLevelItemCount()) if table.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)==task['id'])
    table.itemWidget(item,col).click()
    assert w.focus.task_id==task['id'] and w.focus.focus_minutes==task['task'].duration_minutes


@pytest.mark.parametrize('standalone', [False, True])
def test_structure_search_fields_date_selection_and_clear_are_readonly(workspace, qtbot, standalone):
    w=workspace
    pid=None if standalone else w.selected_plan_id
    if standalone:
        tid=w.store.add_manual_task(payload(), '2026-10-22')
        rows=w.store.tasks('__standalone__')
    else:
        rows=w.store.tasks(pid)
        tid=rows[0]['id']
    task=rows[0]
    edit(w.store,task,payload=task['task'].model_copy(update=dict(title='可搜索名称',description='独有具体说明',deliverable='验证完成标准')),due_date='2026-10-22')
    d=StructureDialog(w.store,pid)
    qtbot.addWidget(d)
    d.show()
    d.search_scope.setCurrentText('全部内容')
    before=w.store.read('SELECT * FROM tasks ORDER BY id')
    for query in ('可搜索名称','独有具体说明','验证完成标准','2026/10/22','2026年10月22日','可搜索名称 具体说明'):
        d.search.setText(query)
        item=d.tasks.topLevelItem(0).child(0)
        assert item.data(0,Qt.ItemDataRole.UserRole)==tid
        assert d.tasks.topLevelItemCount()==1 and '显示 1/' in d.search_hint.text()
    item.setSelected(True)
    d.refresh()
    assert d.selected()==[tid] and '可搜索名称' in d.selection_hint.text()
    d.search.setText('没有这个任务')
    assert not d.selected() and d.tasks.topLevelItemCount()==0 and '没有匹配' in d.search_hint.text()
    assert '未选择具体任务' in d.selection_hint.text()
    d.search.clear()
    assert not d.selected() and d.tasks.topLevelItemCount()>0
    assert before==w.store.read('SELECT * FROM tasks ORDER BY id')


def test_filtered_batch_assign_changes_only_visible_selected(workspace, qtbot):
    w=workspace
    d=StructureDialog(w.store,w.selected_plan_id)
    qtbot.addWidget(d)
    first,second=w.store.tasks(w.selected_plan_id)[:2]
    for i in (0,1):
        d.tasks.topLevelItem(0).child(i).setSelected(True)
    d.search.setText(first['task'].title)
    assert d.selected()==[first['id']]
    d.stage.setText('仅分配匹配任务')
    d.assign()
    assert task_details(w.store,first['id'])[0]['stage']=='仅分配匹配任务'
    assert task_details(w.store,second['id'])[0]['stage']==''
    assert d.selected()==[first['id']]
