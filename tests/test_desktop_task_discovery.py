"""Search/completed-task discoverability and unambiguous task organization."""
from datetime import date
import pytest
from PySide6.QtCore import Qt, QEvent, QDate
from PySide6.QtWidgets import QApplication, QPushButton
from desktop.action_menu import ActionMenu
from desktop.store import Store
from desktop.credentials import CredentialStore
from desktop.theme import apply_theme
from desktop.window import MainWindow
from desktop.learning_ui import StepsDialog, StructureDialog
from desktop.learning_tools import task_details, assign_stage
from desktop.task_search import search_tasks
from tests.test_desktop_manual_tasks import payload
from tests.test_desktop_store import create, edit


@pytest.fixture
def workspace(qtbot, qapp, tmp_path):
    apply_theme(qapp, 'light')
    s = Store(tmp_path)
    s.set_setting('reminders_paused', True)
    create(s)
    w = MainWindow(s, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.exiting = True
    w.close()


def choose_inbox(w):
    item = next(w.plan_list.item(i) for i in range(w.plan_list.count()) if w.plan_list.item(i).data(Qt.ItemDataRole.UserRole) == '__standalone__')
    w.plan_list.setCurrentItem(item)


def ids(tree):
    return {tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(tree.topLevelItemCount())}


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_menu_no_stuck_first_item_and_single_visual_highlight(workspace, qapp, qtbot, theme):
    apply_theme(qapp, theme)
    menu = workspace.plan_more_menu
    menu.popup(workspace.plan_more_button.mapToGlobal(workspace.plan_more_button.rect().bottomLeft()))
    assert menu.active_index == -1
    assert not any(button.property('menuActive') or button.hasFocus() for _, button in menu.items)
    first, third = menu.items[0][1], menu.items[2][1]
    QApplication.sendEvent(first, QEvent(QEvent.Type.Enter))
    first_active = first.grab().toImage().pixelColor(10, 10)
    QApplication.sendEvent(first, QEvent(QEvent.Type.Leave))
    QApplication.sendEvent(third, QEvent(QEvent.Type.Enter))
    assert sum(bool(b.property('menuActive')) for _, b in menu.items) == 1
    assert third.property('menuActive') and not first.property('menuActive')
    assert first.grab().toImage().pixelColor(10, 10) != first_active
    assert third.grab().toImage().pixelColor(10, 10) == first_active
    qtbot.keyClick(menu, Qt.Key.Key_Escape)
    menu.popup(workspace.plan_more_button.mapToGlobal(workspace.plan_more_button.rect().bottomLeft()))
    assert menu.active_index == -1


def test_menu_keyboard_skips_disabled_and_confirm_only(qtbot):
    menu = ActionMenu()
    qtbot.addWidget(menu)
    called = []
    menu.add_action('第一项', lambda *_: called.append(1))
    menu.add_action('不可用', lambda *_: called.append(2)).setEnabled(False)
    menu.add_action('第三项', lambda *_: called.append(3))
    from PySide6.QtCore import QPoint
    menu.popup(QPoint(20, 20))
    qtbot.keyClick(menu, Qt.Key.Key_Return)
    assert not called
    qtbot.keyClick(menu, Qt.Key.Key_Down)
    assert menu.active_index == 0
    qtbot.keyClick(menu, Qt.Key.Key_Down)
    assert menu.active_index == 2
    qtbot.keyClick(menu, Qt.Key.Key_Return)
    assert called == [3] and not menu.isVisible()


@pytest.mark.parametrize('status', ['未开始', '进行中', '延期', '已完成'])
@pytest.mark.parametrize('standalone', [False, True])
def test_group_and_unchecked_steps_never_complete_or_reset_task(workspace, status, standalone):
    w = workspace
    tid = w.store.add_manual_task(payload(), date.today().isoformat()) if standalone else w.store.tasks()[0]['id']
    task = next(t for t in w.store.tasks() if t['id'] == tid)
    edit(w.store, task, status=status)
    d = StepsDialog(w.store, tid)
    w.tools.show(d, ('steps', tid))
    assert '未分组' in d.stage.itemText(0) and d.stage.itemData(0) == ''
    assert task['task'].title in d.task_title.text() and status in d.task_context.text()
    d.stage.setEditText('项目练习')
    d.new_title.setText('先阅读示例')
    d.add_step()
    d.accept()
    assert next(t for t in w.store.tasks() if t['id'] == tid)['status'] == status
    details, token = task_details(w.store, tid)
    assign_stage(w.store, [tid], '另一组', token)
    assert next(t for t in w.store.tasks() if t['id'] == tid)['status'] == status
    assert not details['steps'][0]['done']


def test_ungroup_option_removes_group_without_saving_label(workspace):
    w = workspace
    tid = w.store.tasks()[0]['id']
    _, token = task_details(w.store, tid)
    assign_stage(w.store, [tid], '原分组', token)
    d = StepsDialog(w.store, tid)
    w.tools.show(d, ('steps', tid))
    d.stage.setCurrentIndex(0)
    d.accept()
    assert task_details(w.store, tid)[0]['stage'] == ''


def test_today_completed_checkbox_keeps_only_today_and_global_metrics(workspace):
    w = workspace
    today = date.today()
    from datetime import timedelta
    days = [today-timedelta(days=1), today, today+timedelta(days=1)]
    completed = []
    for day in days:
        tid = w.store.add_manual_task(payload(), day.isoformat())
        edit(w.store, next(t for t in w.store.tasks() if t['id'] == tid), status='已完成')
        completed.append(tid)
    w.refresh()
    summary = w.today_summary.text()
    assert not ids(w.today_tree) & set(completed)
    before = w.store.read('SELECT * FROM tasks ORDER BY id')
    w.today_completed.setChecked(True)
    assert ids(w.today_tree) & set(completed) == {completed[1]}
    assert w.today_summary.text() == summary
    w.today_completed.setChecked(False)
    assert not ids(w.today_tree) & set(completed)
    assert before == w.store.read('SELECT * FROM tasks ORDER BY id')


@pytest.mark.parametrize('query', ['独有名称', '测试具体说明', '验证完成标准', '2026/10/22', '2026年10月22日', '独有名称 具体说明'])
def test_search_title_description_deliverable_date_and_multiple_terms(workspace, query):
    w = workspace
    p = payload().model_copy(update=dict(title='独有名称', description='测试具体说明', deliverable='验证完成标准'))
    tid = w.store.add_manual_task(p, '2026-10-22')
    rows = search_tasks(w.store.tasks(), query)
    assert {r['id'] for r in rows} == {tid}


def test_library_inbox_complete_tasks_all_dates_and_not_a_real_plan(workspace):
    w = workspace
    pid = w.selected_plan_id
    tid = w.store.add_manual_task(payload(), '2026-01-01')
    edit(w.store, next(t for t in w.store.tasks() if t['id'] == tid), status='已完成')
    plans_before = w.store.read('SELECT * FROM plans ORDER BY id')
    w.refresh()
    assert w.plan_list.item(0).data(Qt.ItemDataRole.UserRole) == '__standalone__'
    choose_inbox(w)
    assert tid in ids(w.plan_tree) and w.selected_plan_id is None and w.library_standalone
    assert not w.plan_more_button.isEnabled() and not w.delete_plan_action.isEnabled()
    w.delete_selected()
    w.archive_selected()
    assert w.store.read('SELECT * FROM plans ORDER BY id') == plans_before
    w.library_task_search.status.setCurrentText('未完成')
    assert tid not in ids(w.plan_tree)
    w.library_task_search.status.setCurrentText('已完成')
    assert tid in ids(w.plan_tree)
    w.search.setText('不存在的计划')
    assert w.plan_list.count() == 1 and w.plan_title.text() == '独立任务'
    assert w.store.get_plan(pid)


def test_query_date_range_and_no_results_are_visible_without_writes(workspace):
    w = workspace
    tid = w.store.add_manual_task(payload(), '2026-10-22')
    w.refresh()
    choose_inbox(w)
    before = w.store.read('SELECT * FROM tasks ORDER BY id')
    search = w.library_task_search
    search.limit_dates.setChecked(True)
    search.start.setDate(QDate(2026,10,22))
    search.end.setDate(QDate(2026,10,22))
    assert ids(w.plan_tree) == {tid}
    search.end.setDate(QDate(2026,10,21))
    assert not ids(w.plan_tree) and '不能早于' in w.library_task_hint.text()
    search.limit_dates.setChecked(False)
    search.query.setText('不匹配的关键词')
    assert not ids(w.plan_tree) and '没有匹配' in w.library_task_hint.text()
    assert before == w.store.read('SELECT * FROM tasks ORDER BY id')


def test_structure_selection_is_named_and_standalone_supported(workspace):
    w = workspace
    tid = w.store.add_manual_task(payload(), date.today().isoformat())
    d = StructureDialog(w.store, None)
    w.tools.show(d, ('structure',None))
    item = d.tasks.topLevelItem(0).child(0)
    item.setSelected(True)
    assert item.text(0) in d.selection_hint.text()
    assert item.data(0, Qt.ItemDataRole.UserRole) == tid


def test_new_plan_can_be_selected_after_inbox(workspace):
    w = workspace
    choose_inbox(w)
    w.load_sample()
    assert w.selected_plan_id and not w.library_standalone
    assert w.plan_list.currentItem().data(Qt.ItemDataRole.UserRole) == w.selected_plan_id


def test_inbox_still_exposes_global_draft_resume(workspace):
    choose_inbox(workspace)
    actions = {action.text(): action.isEnabled() for action, _ in workspace.history_menu_button.action_menu.items}
    assert workspace.history_menu_button.isEnabled() and actions['继续草稿']
    assert not actions['导出 JSON'] and not actions['调整历史与回溯']
