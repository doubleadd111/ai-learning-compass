"""Search explanations exercised only by the isolated smoke-test entry point."""
from datetime import date
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from desktop.learning_ui import StructureDialog
from desktop.task_style import SEARCH_MATCH_ROLE
from desktop.task_tip import TaskTip, TASK_TIP_ROLE
from desktop.focus_tip import focus_tips
from PySide6.QtGui import QHelpEvent
from PySide6.QtWidgets import QStyleOptionViewItem
from models import DailyTask


def check_search_scope(window):
    store=window.store
    original={r['id'] for r in store.tasks()}
    created=[]
    query='编写匹配检查'
    try:
        for title,description in ((query+'名称','按名称检查'),('跨周匹配检查','为切换行为'+query)):
            created.append(store.add_manual_task(DailyTask(day=1,title=title,description=description,
                deliverable='仅用于隔离搜索检查',duration_minutes=15),date.today().isoformat()))
        window.refresh()
        window.today_plan_filter.setCurrentIndex(window.today_plan_filter.findData('__standalone__'))
        window.plan_list.setCurrentRow(0)
        for control,table in ((window.today_task_search,window.today_tree),(window.library_task_search,window.plan_tree)):
            assert control.scope.currentData()=='title'
            control.query.setText(query)
            assert table.topLevelItemCount()==1 and table.topLevelItem(0).data(0,Qt.ItemDataRole.UserRole)==created[0]
            control.scope.setCurrentText('全部内容')
            assert table.topLevelItemCount()==2
            item=next(table.topLevelItem(i) for i in range(table.topLevelItemCount()) if table.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)==created[1])
            assert '说明命中：' in item.data(0,SEARCH_MATCH_ROLE) and '【'+query+'】' in item.data(0,SEARCH_MATCH_ROLE)
            option=QStyleOptionViewItem()
            index=table.indexFromItem(item)
            option.rect=table.visualRect(index)
            point=option.rect.center()
            event=QHelpEvent(QEvent.Type.ToolTip,point,table.viewport().mapToGlobal(point))
            assert table.itemDelegate().helpEvent(event,table,option,index)
            tip=focus_tips().popup
            assert isinstance(tip,TaskTip) and tip.isVisible()
            assert tip.matches.text()=='匹配位置：说明' and '【'+query+'】' in tip.description.text()
            assert tip.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            tip.hide()
            control.query.clear()
            assert not table.topLevelItem(0).data(0,SEARCH_MATCH_ROLE)
        dialog=StructureDialog(store,None)
        window.tools.show(dialog,('structure','search-scope-check'))
        dialog.search.setText(query)
        assert dialog.tasks.topLevelItem(0).childCount()==1
        dialog.search_scope.setCurrentText('全部内容')
        assert dialog.tasks.topLevelItem(0).childCount()==2
        item=next(dialog.tasks.topLevelItem(0).child(i) for i in range(2) if dialog.tasks.topLevelItem(0).child(i).data(0,Qt.ItemDataRole.UserRole)==created[1])
        assert '说明命中：' in item.data(0,SEARCH_MATCH_ROLE)
        assert item.data(0,TASK_TIP_ROLE)['hint']=='双击任务管理执行步骤'
        dialog.search.setText('独立任务')
        assert '所属计划命中：' in dialog.tasks.topLevelItem(0).child(0).data(0,SEARCH_MATCH_ROLE)
        return dict(default_title_only=True, three_entry_points=True, all_content_opt_in=True,
                    visible_match_excerpt=True, clear_removes_excerpt=True, plan_scope_match=True,
                    rounded_task_preview=True, no_duplicate_description=True, success=True)
    finally:
        window.tools.close_all()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        for control in (window.today_task_search,window.library_task_search):
            control.query.clear()
            control.scope.setCurrentText('仅任务名称')
        window.today_plan_filter.setCurrentIndex(0)
        for tid in created:
            store.delete_manual_task(tid)
        window.refresh()
        assert {r['id'] for r in store.tasks()}==original
