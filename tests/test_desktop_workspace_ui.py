"""Peer windows, grouped actions and explicit statistical quantities."""
from datetime import date
import pytest
from PySide6.QtCore import Qt, QDate, QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QPushButton, QLabel, QDialog
from shiboken6 import isValid
from desktop.window import MainWindow
from desktop.store import Store
from desktop.credentials import CredentialStore
from desktop.theme import apply_theme
from desktop.learning_ui import StatisticsDialog, StructureDialog, StepsDialog
from desktop.dialogs import TaskDialog
from desktop.guide import open_guide, close_guide
from tests.test_desktop_store import create, edit


@pytest.fixture
def workspace(qtbot, qapp, tmp_path):
    apply_theme(qapp, 'light')
    store = Store(tmp_path)
    store.set_setting('reminders_paused', True)
    create(store)
    w = MainWindow(store, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(w)
    w.show()
    yield w
    close_guide()
    w.tools.close_all()
    w.exiting = True
    w.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize('operation,key', [
    ('open_statistics', 'statistics'), ('open_recurring', 'recurring'),
    ('resume_draft', 'drafts'), ('open_structure', 'structure'),
    ('undo_selected', 'history'), ('show_history', 'requirements'),
    ('add_manual_task', ('manual', None)), ('open_schedule', ('schedule', None, False, '补排'))])
def test_regular_tools_are_ownerless_nonmodal_reusable(workspace, operation, key):
    w = workspace
    if key in ('structure', 'history', 'requirements'):
        key = (key, w.selected_plan_id)
    getattr(w, operation)()
    d = w.tools.windows[key]
    QApplication.processEvents()
    assert d.isVisible() and not d.isModal() and d.parentWidget() is None
    assert d.windowHandle().transientParent() is None
    assert not d.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert w.isEnabled() and QApplication.activeModalWidget() is None
    d.showMinimized()
    getattr(w, operation)()
    assert w.tools.windows[key] is d and not d.isMinimized()
    d.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert key not in w.tools.windows
    getattr(w, operation)()
    assert w.tools.windows[key] is not d


def test_peer_help_remains_independent_and_reopens(workspace):
    w = workspace
    w.open_statistics()
    d = w.tools.windows['statistics']
    guide = open_guide(d, 'tools')
    assert guide.windowHandle().transientParent() is None
    guide.close()
    QApplication.processEvents()
    assert open_guide(d, 'tools') is not guide
    d.close()
    assert QApplication.instance()._learning_guide.isVisible()


def test_grouped_actions_keep_every_operation_and_delete_guard(workspace):
    w = workspace
    page = w.stack.widget(1)
    visible_groups = [b for b in page.findChildren(QPushButton) if hasattr(b, 'action_menu')]
    assert [b.text().strip() for b in visible_groups] == ['任务安排', '计划管理', 'AI 辅助', '历史与数据']
    labels = {a.text() for b in visible_groups for a, _ in b.action_menu.items}
    assert {'调整排期', '暂停计划', '重命名计划', '归档／恢复归档', '按此计划创建副本',
            '删除计划…', '任务分组与执行步骤', '添加任务到此计划', 'AI 调整计划',
            '生成本周复盘', '调整历史与回溯', '查看需求与历史', '导出 JSON', '继续草稿'} <= labels
    assert w.plan_more_menu.delete_button.objectName() == 'planDangerAction'


def test_task_editor_saves_captured_task_not_new_selection(workspace):
    w = workspace
    tid = w.store.tasks(w.selected_plan_id)[0]['id']
    w.edit_task_id(tid)
    dialog = w.tools.windows[('task', tid)]
    second = create(w.store)
    w.selected_plan_id = second
    dialog.note.setPlainText('仅写入最初选中的任务')
    dialog.accept()
    assert next(t for t in w.store.tasks() if t['id'] == tid)['note'] == '仅写入最初选中的任务'
    assert all(not t['note'] for t in w.store.tasks(second))


def test_peer_task_edit_conflict_preserves_new_record(workspace):
    w = workspace
    task = w.store.tasks()[0]
    d = TaskDialog(task, parent=w, store=w.store)
    w.tools.show(d, ('task', task['id']))
    edit(w.store, task, note='另一个窗口的新笔记')
    d.note.setPlainText('旧窗口内容')
    d.accept()
    assert d.isVisible() and '其他窗口' in d.save_error.text()
    assert next(t for t in w.store.tasks() if t['id'] == task['id'])['note'] == '另一个窗口的新笔记'


def test_new_focus_while_editor_open_not_overwritten(workspace):
    w = workspace
    task = w.store.tasks()[0]
    d = TaskDialog(task, store=w.store)
    w.tools.show(d, ('task', task['id']))
    w.store.record_focus('workspace-check', task['id'], 17, '2026-10-20T12:00:00')
    d.note.setPlainText('保留刚产生的专注')
    d.accept()
    saved = next(t for t in w.store.tasks() if t['id'] == task['id'])
    assert saved['focus_seconds'] == 17 and saved['status'] == '进行中'
    assert saved['note'] == '保留刚产生的专注'


@pytest.mark.parametrize('days', [7, 30])
def test_chart_keeps_daily_values_and_zero_meaning(workspace, days):
    w = workspace
    task = w.store.tasks()[0]
    w.store.record_focus('chart-check', task['id'], 90, '2026-10-20T12:00:00')
    d = StatisticsDialog(w.store)
    w.tools.show(d, 'statistics')
    d.start.setDate(QDate.fromString(task['due_date'], 'yyyy-MM-dd'))
    d.end.setDate(d.start.date().addDays(days-1))
    assert len(d.chart.days) == days == d.tabs.topLevelItemCount()
    assert d.chart.days[0]['actual_seconds'] == 90
    assert '已记录1.5分钟' in d.chart.accessibleDescription()
    assert d.output_tabs.tabText(0) == '每日用时对照'
    assert d.chart.height() > 76*days
    assert d.chart.days[1]['expected'] == 0
    assert not d.chart.grab().isNull()
    d.start.setDate(d.end.date().addDays(1))
    assert not d.chart.days and d.report is None and d.tabs.topLevelItemCount() == 0


def test_steps_show_example_and_selection_instruction(workspace):
    w = workspace
    d = StructureDialog(w.store, w.selected_plan_id)
    w.tools.show(d, ('structure', w.selected_plan_id))
    captions = '\n'.join(label.text() for label in d.findChildren(QLabel))
    assert '像文件夹一样' in captions and '阅读示例' in captions and '可选' in captions
    d.edit_steps()
    assert '先选中一项具体任务' in d.error.text()
    tid = w.store.tasks()[0]['id']
    step = StepsDialog(w.store, tid)
    w.tools.show(step, ('steps', tid))
    assert not step.data['steps']  # Examples are guidance, never silent new tasks.


def test_rename_keeps_original_plan_after_selection_changes(workspace):
    w = workspace
    pid = w.selected_plan_id
    w.rename_selected()
    d = w.tools.windows[('rename', pid)]
    other = create(w.store)
    old = w.store.get_plan(other)['title']
    w.selected_plan_id = other
    d.setTextValue('只修改原来的计划')
    d.accept()
    assert w.store.get_plan(pid)['title'] == '只修改原来的计划'
    assert w.store.get_plan(other)['title'] == old


def test_nested_steps_are_peers_and_survive_parent_close(workspace):
    w = workspace
    w.open_structure()
    parent = w.tools.windows[('structure', w.selected_plan_id)]
    item = parent.tasks.topLevelItem(0).child(0)
    item.setSelected(True)
    tid = item.data(0, Qt.ItemDataRole.UserRole)
    parent.edit_steps()
    child = w.tools.windows[('steps', tid)]
    assert child.isVisible() and child.parentWidget() is None and not child.isModal()
    parent.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    child.new_title.setText('父窗口关闭后仍可保存')
    child.add_step()
    child.accept()
    from desktop.learning_tools import task_details
    assert task_details(w.store, tid)[0]['steps'][0]['title'] == '父窗口关闭后仍可保存'


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_menus_keyboard_and_small_layout(workspace, qtbot, qapp, theme):
    w = workspace
    apply_theme(qapp, theme)
    w.resize(980, 680)
    w.navigate(1)
    w.plan_more_button.click()
    menu = w.plan_more_menu
    assert menu.isVisible() and menu.width() >= 240
    qtbot.keyClick(menu, Qt.Key.Key_Escape)
    assert not menu.isVisible()
    w.open_statistics()
    d = w.tools.windows['statistics']
    d.resize(640, 560)
    QApplication.processEvents()
    assert d.width() == 640 and not d.grab().isNull()
