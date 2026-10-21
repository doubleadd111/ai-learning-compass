"""Context actions target the hit task, reuse guarded editors and never auto-save."""
from datetime import date
import pytest
from PySide6.QtCore import Qt, QPoint, QEvent, QRect
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QLabel
from desktop.task_context import action_reason
from desktop.confirmations import ConfirmationDialog
from desktop.learning_ui import StructureDialog
from desktop.theme import apply_theme
from desktop import learning_tools
from tests.test_desktop_workspace_ui import workspace
from tests.test_desktop_manual_tasks import payload, row
from tests.test_desktop_store import edit


def setup_tasks(w):
    ids=[w.store.add_manual_task(payload().model_copy(update={'title':name}),date.today().isoformat())
         for name in ('任务甲','任务乙')]
    w.refresh()
    w.today_plan_filter.setCurrentIndex(w.today_plan_filter.findData('__standalone__'))
    w.plan_list.setCurrentRow(0)
    return ids


def find(tree,tid):
    def walk(item):
        if item.data(0,Qt.ItemDataRole.UserRole)==tid:
            return item
        for i in range(item.childCount()):
            if value:=walk(item.child(i)):
                return value
    return next(value for i in range(tree.topLevelItemCount()) if (value:=walk(tree.topLevelItem(i))))


def show_menu(tree,item):
    tree.scrollToItem(item)
    QApplication.processEvents()
    point=tree.visualItemRect(item).center()
    event=QContextMenuEvent(QContextMenuEvent.Reason.Mouse,point,tree.viewport().mapToGlobal(point))
    QApplication.sendEvent(tree.viewport(),event)
    return tree.task_context.menu


def buttons(menu):
    return {a.data(): (a,b) for a,b in menu.items}


@pytest.mark.parametrize('entry',['today','library','week-table','week-card','structure'])
def test_hit_task_replaces_old_and_multi_selection_readonly(workspace,entry):
    w=workspace
    a,b=setup_tasks(w)
    before=w.store.read('SELECT * FROM tasks ORDER BY id')
    library_plan=w.selected_plan_id
    if entry=='structure':
        d=StructureDialog(w.store,None)
        w.tools.show(d,('structure',None))
        tree=d.tasks
    elif entry.startswith('week'):
        w.navigate(2)
        w.week_panel.mode.setCurrentIndex(0 if entry=='week-card' else 1)
        tree=w.week_tree
    else:
        w.navigate(0 if entry=='today' else 1)
        tree=w.today_tree if entry=='today' else w.plan_tree
    tree.setCurrentItem(find(tree,a))
    if entry=='week-card':
        card=next(c for c in w.week_panel.cards if c.task_id==b)
        child=card.findChildren(QLabel)[0]
        point=child.rect().center()
        QApplication.sendEvent(child,QContextMenuEvent(QContextMenuEvent.Reason.Mouse,point,child.mapToGlobal(point)))
        menu=card.task_context.menu
        assert w.week_panel.selected_id==b
    else:
        find(tree,b).setSelected(True)
        menu=show_menu(tree,find(tree,b))
        assert [i.data(0,Qt.ItemDataRole.UserRole) for i in tree.selectedItems()]==[b]
        assert tree.currentItem().data(0,Qt.ItemDataRole.UserRole)==b
    assert menu.isVisible() and menu.task_id==b
    assert set(buttons(menu))=={'edit','due','status','note','focus','reminder','steps','attention','study_log','ai_locked','ai_modify','delete'}
    assert buttons(menu)['delete'][0].isEnabled()
    assert not buttons(menu)['ai_modify'][0].isEnabled()
    assert w.selected_plan_id==library_plan
    assert before==w.store.read('SELECT * FROM tasks ORDER BY id')
    menu.hide()


@pytest.mark.parametrize('key,modifiers',[(Qt.Key.Key_Menu,Qt.KeyboardModifier.NoModifier),(Qt.Key.Key_F10,Qt.KeyboardModifier.ShiftModifier)])
def test_keyboard_context_menu_uses_current_task(workspace,qtbot,key,modifiers):
    w=workspace
    a,b=setup_tasks(w)
    w.navigate(0)
    tree=w.today_tree
    tree.setCurrentItem(find(tree,b))
    qtbot.keyClick(tree,key,modifiers)
    menu=tree.task_context.menu
    assert menu.task_id==b and menu.isVisible()
    qtbot.keyClick(menu,Qt.Key.Key_Escape)
    assert not menu.isVisible()


def test_blank_area_and_group_heading_never_reuse_old_task(workspace):
    w=workspace
    a,b=setup_tasks(w)
    w.navigate(0)
    menu=show_menu(w.today_tree,find(w.today_tree,b))
    point=w.today_tree.viewport().mapToGlobal(QPoint(5,w.today_tree.viewport().height()-5))
    assert not w.today_tree.task_context.open(point) and not menu.isVisible()
    d=StructureDialog(w.store,None)
    w.tools.show(d,('structure',None))
    assert not d.tasks.task_context.open(d.tasks.viewport().mapToGlobal(d.tasks.visualItemRect(d.tasks.topLevelItem(0)).center()))
    assert d.tasks.task_context.menu is None


def test_context_event_over_row_focus_button_still_targets_that_task(workspace):
    w=workspace
    a,b=setup_tasks(w)
    w.navigate(0)
    tree=w.today_tree
    button=tree.itemWidget(find(tree,b),4)
    point=button.rect().center()
    QApplication.sendEvent(button,QContextMenuEvent(QContextMenuEvent.Reason.Mouse,point,button.mapToGlobal(point)))
    assert tree.task_context.menu.isVisible() and tree.task_context.menu.task_id==b
    assert not w.focus.running


@pytest.mark.parametrize('field',['edit','due','status','note','reminder'])
def test_edit_entries_open_correct_original_editor_and_cancel_readonly(workspace,field):
    w=workspace
    a,b=setup_tasks(w)
    w.navigate(0)
    before=w.store.read('SELECT * FROM tasks ORDER BY id')
    menu=show_menu(w.today_tree,find(w.today_tree,b))
    buttons(menu)[field][1].click()
    editor=w.tools.windows[('task',b)]
    assert editor.row['id']==b and not menu.isVisible()
    if field!='edit':
        assert editor.focusWidget() is getattr(editor,field)
    editor.reject()
    assert before==w.store.read('SELECT * FROM tasks ORDER BY id')


def test_reusing_editor_preserves_unsaved_fields(workspace):
    w=workspace
    a,b=setup_tasks(w)
    w.run_task_action(b,'edit')
    editor=w.tools.windows[('task',b)]
    editor.title.setText('未保存的新标题')
    w.run_task_action(b,'note')
    assert w.tools.windows[('task',b)] is editor
    assert editor.title.text()=='未保存的新标题' and row(w.store,b)['task'].title=='任务乙'
    editor.reject()


def test_note_save_changes_only_hit_task(workspace):
    w=workspace
    a,b=setup_tasks(w)
    before=row(w.store,a)
    menu=show_menu(w.today_tree,find(w.today_tree,b))
    buttons(menu)['note'][1].click()
    editor=w.tools.windows[('task',b)]
    editor.note.setPlainText('右键任务的笔记')
    editor.accept()
    assert row(w.store,b)['note']=='右键任务的笔记'
    assert row(w.store,a)==before


def test_steps_target_and_refresh_structure_without_completing_task(workspace):
    w=workspace
    a,b=setup_tasks(w)
    d=StructureDialog(w.store,None)
    w.tools.show(d,('structure',None))
    menu=show_menu(d.tasks,find(d.tasks,b))
    buttons(menu)['steps'][1].click()
    editor=w.tools.windows[('steps',b)]
    editor.new_title.setText('执行一步')
    editor.add_step()
    editor.accept()
    assert row(w.store,b)['status']=='未开始'
    assert find(d.tasks,b).text(3)=='0/1 步'
    assert not learning_tools.task_details(w.store,a)[0]['steps']


def test_ai_entry_uses_explicit_task_and_does_not_send_request(workspace,monkeypatch):
    w=workspace
    pid=w.selected_plan_id
    target=w.store.tasks(pid)[1]
    before=w.store.read('SELECT * FROM tasks ORDER BY id')
    called=[]
    w.plan_list.setCurrentRow(0)
    assert w.selected_plan_id is None
    monkeypatch.setattr(w,'start_work',lambda *a,**k: called.append(True))
    w.run_task_action(target['id'],'ai_modify')
    dialog=w.tools.windows[('modify-task',pid,(target['id'],))]
    assert dialog.scope.currentText()=='选中任务'
    assert dialog.target_ids(w.store.get_plan(pid),[target['id']])==[target['id']]
    assert not called and w.selected_plan_id is None
    dialog.reject()
    assert before==w.store.read('SELECT * FROM tasks ORDER BY id')


def test_reviewed_task_protection_and_record_access(workspace):
    w=workspace
    pid=w.selected_plan_id
    target=w.store.tasks(pid)[0]
    with w.store.write() as c:
        c.execute('INSERT INTO reviews VALUES (?,?,?)',(pid,target['week'],'复盘测试'))
    w.refresh()
    w.navigate(1)
    menu=show_menu(w.plan_tree,find(w.plan_tree,target['id']))
    for key in ('due','status','ai_locked','ai_modify','delete'):
        assert not buttons(menu)[key][0].isEnabled()
        assert '\n' in buttons(menu)[key][0].text()
    for key in ('edit','note','reminder','steps'):
        assert buttons(menu)[key][0].isEnabled()
    buttons(menu)['note'][1].click()
    editor=w.tools.windows[('task',target['id'])]
    assert not editor.title.isEnabled() and editor.note.isEnabled()
    editor.reject()


@pytest.mark.parametrize('state',['completed','paused','archived'])
def test_focus_disabled_reason_rechecks_current_row(workspace,state):
    w=workspace
    pid=w.selected_plan_id
    target=w.store.tasks(pid)[0]
    if state=='completed':
        edit(w.store,target,status='已完成')
    elif state=='archived':
        w.store.archive(pid)
    else:
        with w.store.write() as c:
            c.execute("UPDATE plan_schedules SET pause_start=?,resume_date=? WHERE plan_id=?",(date.today().isoformat(),'2099-01-01',pid))
    fresh=row(w.store,target['id'])
    assert action_reason(w.store,fresh,'focus',w)


def test_menu_delete_requires_confirmation_and_default_cancel(workspace,monkeypatch):
    w=workspace
    a,b=setup_tasks(w)
    before=w.store.read('SELECT * FROM tasks ORDER BY id')
    def cancel(box):
        assert box.cancel_button.isDefault() and not box.confirm_button.isDefault()
        assert '任务乙' in box.summary.text()
        return QDialog.DialogCode.Rejected
    monkeypatch.setattr(ConfirmationDialog,'exec',cancel)
    menu=show_menu(w.today_tree,find(w.today_tree,b))
    buttons(menu)['delete'][1].click()
    assert before==w.store.read('SELECT * FROM tasks ORDER BY id')


def test_confirmed_delete_only_removes_target_and_refreshes_group(workspace,monkeypatch):
    w=workspace
    a,b=setup_tasks(w)
    old=row(w.store,a)
    d=StructureDialog(w.store,None)
    w.tools.show(d,('structure',None))
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda box:QDialog.DialogCode.Accepted)
    menu=show_menu(d.tasks,find(d.tasks,b))
    buttons(menu)['delete'][1].click()
    assert b not in {r['id'] for r in w.store.tasks()}
    assert row(w.store,a)==old and d.tasks.topLevelItem(0).childCount()==1


@pytest.mark.parametrize('change',['note','steps'])
def test_changes_during_delete_confirmation_block_deletion(workspace,monkeypatch,change):
    w=workspace
    a,b=setup_tasks(w)
    errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:errors.append(a[-1]))
    def confirm(box):
        if change=='note':
            edit(w.store,row(w.store,b),note='新的学习记录')
        else:
            data,token=learning_tools.task_details(w.store,b)
            data['stage']='新分组'
            learning_tools.save_details(w.store,b,data,token)
        return QDialog.DialogCode.Accepted
    monkeypatch.setattr(ConfirmationDialog,'exec',confirm)
    menu=show_menu(w.today_tree,find(w.today_tree,b))
    buttons(menu)['delete'][1].click()
    assert row(w.store,b) and errors and '未删除' in errors[-1]


def test_deleted_task_menu_does_not_fall_back_to_other_selection(workspace,monkeypatch):
    w=workspace
    a,b=setup_tasks(w)
    errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:errors.append(a[-1]))
    menu=show_menu(w.today_tree,find(w.today_tree,b))
    w.store.delete_manual_task(b)
    w.today_tree.setCurrentItem(find(w.today_tree,a))
    buttons(menu)['edit'][1].click()
    assert errors and not w.tools.windows.get(('task',a))


def test_active_focus_disables_delete_and_does_not_reset_timer(workspace):
    w=workspace
    a,b=setup_tasks(w)
    w.start_focus_for_task(b)
    try:
        session=w.focus.session_id
        w.navigate(0)
        menu=show_menu(w.today_tree,find(w.today_tree,b))
        assert not buttons(menu)['delete'][0].isEnabled()
        assert w.focus.session_id==session and w.focus.running
        menu.hide()
    finally:
        w.focus.stop()


def test_open_editor_blocks_delete_without_discarding_unsaved_values(workspace,monkeypatch):
    w=workspace
    a,b=setup_tasks(w)
    w.run_task_action(b,'edit')
    editor=w.tools.windows[('task',b)]
    editor.note.setPlainText('未保存的笔记')
    errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:errors.append(a[-1]))
    w.run_task_action(b,'delete')
    assert errors and '先关闭' in errors[-1] and row(w.store,b)
    assert editor.note.toPlainText()=='未保存的笔记'
    editor.reject()


def test_changes_after_opening_menu_block_delete_before_confirmation(workspace,monkeypatch):
    w=workspace
    a,b=setup_tasks(w)
    menu=show_menu(w.today_tree,find(w.today_tree,b))
    data,token=learning_tools.task_details(w.store,b)
    data['stage']='刚保存的新分组'
    learning_tools.save_details(w.store,b,data,token)
    errors=[]
    monkeypatch.setattr(QMessageBox,'warning',lambda *a:errors.append(a[-1]))
    called=[]
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda *a:called.append(True))
    buttons(menu)['delete'][1].click()
    assert errors and '新变化' in errors[-1] and not called and row(w.store,b)


def test_idle_but_still_linked_focus_state_explains_delete_block(workspace):
    w=workspace
    a,b=setup_tasks(w)
    w.store.set_setting('focus_state',dict(task_id=b,session_id=None,phase='focus'))
    menu=show_menu(w.today_tree,find(w.today_tree,b))
    assert not w.focus.running
    assert not buttons(menu)['delete'][0].isEnabled()
    assert '关联专注' in buttons(menu)['delete'][0].text()
    assert row(w.store,b)


def test_menu_explains_open_editor_delete_block(workspace):
    w=workspace
    a,b=setup_tasks(w)
    w.run_task_action(b,'edit')
    editor=w.tools.windows[('task',b)]
    menu=show_menu(w.today_tree,find(w.today_tree,b))
    assert not buttons(menu)['delete'][0].isEnabled()
    assert '关闭编辑窗口' in buttons(menu)['delete'][0].text()
    menu.hide()
    editor.reject()


@pytest.mark.parametrize('theme',['light','dark'])
def test_bounded_scrolling_menu_keyboard_reaches_last_item(workspace,monkeypatch,qtbot,theme):
    w=workspace
    a,b=setup_tasks(w)
    apply_theme(QApplication.instance(),theme)
    class Screen:
        def availableGeometry(self):
            return QRect(0,0,800,360)
    monkeypatch.setattr(QApplication,'screenAt',lambda _:Screen())
    menu=show_menu(w.today_tree,find(w.today_tree,b))
    assert QRect(0,0,800,360).contains(menu.geometry())
    assert menu.scroll.verticalScrollBar().maximum()>0
    assert menu.active_index==-1
    for _ in range(sum(a.isEnabled() for a,_ in menu.items)):
        qtbot.keyClick(menu,Qt.Key.Key_Down)
    button=buttons(menu)['delete'][1]
    assert button.property('menuActive')
    assert menu.scroll.viewport().rect().contains(QRect(button.mapTo(menu.scroll.viewport(),QPoint()),button.size()))
    qtbot.keyClick(menu,Qt.Key.Key_Escape)
    assert not menu.isVisible()
