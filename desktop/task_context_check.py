"""Frozen context-menu smoke checks on invented tasks only, never user data."""
from datetime import date
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from desktop.learning_ui import StructureDialog
from models import DailyTask


def check_task_context(window):
    store=window.store
    created=[]
    original={r['id'] for r in store.tasks()}
    try:
        for title in ('右键检查任务甲','右键检查任务乙'):
            created.append(store.add_manual_task(DailyTask(day=1,title=title,description='仅用隔离虚构资料检查菜单',
                deliverable='菜单不直接保存内容',duration_minutes=15),date.today().isoformat()))
        window.refresh()
        before=store.read('SELECT * FROM tasks ORDER BY id')
        def find(tree,tid):
            for i in range(tree.topLevelItemCount()):
                root=tree.topLevelItem(i)
                for item in ([root] if not root.childCount() else [root.child(j) for j in range(root.childCount())]):
                    if item.data(0,Qt.ItemDataRole.UserRole)==tid:
                        return item
            raise AssertionError('隔离任务未显示')
        for page,tree in ((0,window.today_tree),(1,window.plan_tree),(2,window.week_tree)):
            # Follow real navigation: hidden pages intentionally stay dirty.
            window.navigate(page)
            window.refresh()
            if page==0:
                window.today_plan_filter.setCurrentIndex(window.today_plan_filter.findData('__standalone__'))
            elif page==1:
                window.plan_list.setCurrentRow(0)
            tree.setCurrentItem(find(tree,created[0]))
            target=find(tree,created[1])
            point=tree.viewport().mapToGlobal(tree.visualItemRect(target).center())
            tree.setCurrentItem(target)
            assert tree.task_context.open(point,keyboard=True)
            menu=tree.task_context.menu
            assert menu.task_id==created[1] and len(menu.items)==12
            entry={a.data():(a,b) for a,b in menu.items}
            assert entry['edit'][0].isEnabled() and not entry['ai_modify'][0].isEnabled()
            entry['note'][1].click()
            editor=window.tools.windows[('task',created[1])]
            assert editor.row['id']==created[1] and editor.focusWidget() is editor.note
            editor.reject()
        dialog=StructureDialog(store,None)
        window.tools.show(dialog,('structure','context-check'))
        target=find(dialog.tasks,created[1])
        dialog.tasks.setCurrentItem(target)
        assert dialog.tasks.task_context.open(dialog.tasks.viewport().mapToGlobal(dialog.tasks.visualItemRect(target).center()),keyboard=True)
        dialog.tasks.task_context.menu.hide()
        card=next(c for c in window.week_panel.cards if c.task_id==created[1])
        assert card.task_context.open(card.mapToGlobal(card.rect().center()),keyboard=True)
        card.task_context.menu.hide()
        assert store.read('SELECT * FROM tasks ORDER BY id')==before
        return dict(five_entry_points=True,actions=12,stable_target=True,guarded_original_editor=True,
                    manual_ai_disabled=True,open_cancel_readonly=True,success=True)
    finally:
        window.tools.close_all()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        for tid in created:
            store.delete_manual_task(tid)
        window.today_plan_filter.setCurrentIndex(0)
        window.refresh()
        assert {r['id'] for r in store.tasks()}==original
