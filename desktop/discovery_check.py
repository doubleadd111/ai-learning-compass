"""Offline frozen check of the actual new task-discovery entry points."""
from datetime import date, timedelta
from PySide6.QtCore import Qt, QEvent, QCoreApplication
from PySide6.QtWidgets import QApplication
from desktop.learning_ui import StepsDialog
from models import DailyTask


def check_task_discovery(window):
    store = window.store
    plan_id = window.selected_plan_id
    original = {r['id'] for r in store.tasks()}
    created = []
    try:
        for day, title in ((date.today()-timedelta(days=40), '历史独立任务检查'), (date.today(), '今日完成检查')):
            tid = store.add_manual_task(DailyTask(day=1,title=title,description='离线检索说明',deliverable='保留完成记录',duration_minutes=15), day.isoformat())
            created.append(tid)
            r = next(t for t in store.tasks() if t['id'] == tid)
            store.update_task(tid,r['task'],r['due_date'],None,'已完成',0,None,'')
        window.refresh()
        window.plan_list.setCurrentRow(0)
        assert window.library_standalone and window.selected_plan_id is None
        assert not window.delete_plan_action.isEnabled()
        window.library_task_search.query.setText('历史独立')
        assert window.plan_tree.topLevelItemCount() == 1
        assert window.plan_tree.topLevelItem(0).data(0,Qt.ItemDataRole.UserRole) == created[0]
        window.library_task_search.status.setCurrentText('未完成')
        assert window.plan_tree.topLevelItemCount() == 0
        window.library_task_search.status.setCurrentText('已完成')
        assert window.plan_tree.topLevelItemCount() == 1
        window.today_completed.setChecked(True)
        displayed = {window.today_tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole) for i in range(window.today_tree.topLevelItemCount())}
        assert created[1] in displayed and created[0] not in displayed
        d = StepsDialog(store,created[0],window)
        window.tools.show(d,('steps',created[0]))
        assert d.stage.itemText(0) and '已完成' in d.task_context.text()
        d.stage.setEditText('离线分组')
        d.new_title.setText('添加未勾选步骤不改变原状态')
        d.add_step()
        d.accept()
        assert next(t for t in store.tasks() if t['id'] == created[0])['status'] == '已完成'
        menu = window.tasks_menu_button.action_menu
        menu.popup(window.tasks_menu_button.mapToGlobal(window.tasks_menu_button.rect().bottomLeft()))
        assert menu.active_index == -1
        QApplication.sendEvent(menu.items[0][1],QEvent(QEvent.Type.Enter))
        QApplication.sendEvent(menu.items[0][1],QEvent(QEvent.Type.Leave))
        QApplication.sendEvent(menu.items[3][1],QEvent(QEvent.Type.Enter))
        assert menu.active_index == 3 and sum(bool(b.property('menuActive')) for _,b in menu.items) == 1
        menu.hide()
        return dict(fixed_inbox=True, completed_all_dates=True, search_status=True, today_completed=True,
                    explicit_ungroup=True, saved_steps_preserve_status=True, single_menu_highlight=True, success=True)
    finally:
        window.tools.close_all()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        for tid in created:
            store.delete_manual_task(tid)
        window.today_completed.setChecked(False)
        window.library_task_search.query.clear()
        window.library_task_search.status.setCurrentText('全部状态')
        window.selected_plan_id=plan_id
        window.library_standalone=False
        window.refresh()
        assert {r['id'] for r in store.tasks()} == original
