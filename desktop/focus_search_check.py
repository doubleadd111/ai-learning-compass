"""Exercise task-sized focus and group search in the isolated smoke store only."""
from datetime import date
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from desktop.learning_ui import StructureDialog
from models import DailyTask


def check_focus_search(window):
    store = window.store
    original_ids = {t['id'] for t in store.tasks()}
    focus_minutes, break_minutes = window.focus.focus_minutes, window.focus.break_minutes
    tid = store.add_manual_task(DailyTask(day=1, title='隔离专注时长检索', description='独有检索说明',
        deliverable='仅用于本机模拟验收', duration_minutes=240), date.today().isoformat())
    try:
        window.start_focus_for_task(tid)
        assert window.focus.task_id == tid and window.focus.running
        assert window.focus_minutes.value() == window.focus.focus_minutes == 240
        assert 14399 <= window.focus.remaining <= 14400
        window.pause_focus()
        session, remaining = window.focus.session_id, window.focus.remaining
        window.start_focus_for_task(tid)
        assert window.focus.session_id == session and window.focus.remaining <= remaining
        window.stop_focus()
        dialog = StructureDialog(store, None)
        window.tools.show(dialog, ('structure','focus-search-check'))
        dialog.search_scope.setCurrentText('全部内容')
        dialog.search.setText('独有检索说明')
        assert dialog.tasks.topLevelItemCount() == 1
        item = dialog.tasks.topLevelItem(0).child(0)
        assert item.data(0,Qt.ItemDataRole.UserRole) == tid
        item.setSelected(True)
        dialog.refresh()
        assert dialog.selected() == [tid]
        dialog.search.setText('没有匹配检查')
        assert not dialog.selected() and not dialog.tasks.topLevelItemCount()
        dialog.search.clear()
        assert dialog.tasks.topLevelItemCount() and not dialog.selected()
        return dict(task_minutes=240, full_duration=True, same_session_preserved=True,
                    group_search=True, selection_preserved=True, hidden_selection_cleared=True, success=True)
    finally:
        window.tools.close_all()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        window.focus.stop()
        window.focus.configure(focus_minutes,break_minutes)
        window.focus_minutes.setValue(focus_minutes)
        window.break_minutes.setValue(break_minutes)
        store.delete_manual_task(tid)
        window.refresh()
        assert {t['id'] for t in store.tasks()} == original_ids
