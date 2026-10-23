import pytest
from PySide6.QtCore import Qt,QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication,QDialog,QLabel
from desktop.theme import apply_theme
from desktop.confirmations import ConfirmationDialog
from desktop.global_search_ui import GlobalSearchDialog
from desktop.task_detail_ui import TaskDetailDialog,ResourceEditor
from desktop.backup_space_ui import CleanupDialog
from desktop.startup import StartupWindow
from desktop import backup_manager,task_resources
from tests.test_desktop_workspace_ui import workspace
from tests.test_desktop_workflow import task


@pytest.mark.parametrize('theme',['light','dark'])
@pytest.mark.parametrize('kind',['detail','search','cleanup'])
def test_workspace_tools_small_readonly_peer_reopen(workspace,theme,kind):
    w=workspace;tid=w.store.tasks()[0]['id'];apply_theme(QApplication.instance(),theme);before=backup_manager.live_hash(w.store)
    key=('detail',tid) if kind=='detail' else 'global_search' if kind=='search' else 'backup_cleanup'
    def show():
        return w.open_task_detail(tid) if kind=='detail' else w.open_global_search() if kind=='search' else w.tools.open(key,lambda:CleanupDialog(w))
    d=show();d.resize(640,560);QApplication.processEvents()
    assert (d.width(),d.height())==(640,560) and not d.isModal() and d.parentWidget() is None
    assert d.windowHandle().transientParent() is None and not d.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert backup_manager.live_hash(w.store)==before
    d.reject();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert show() is not d


def test_detail_readonly_all_sections_and_explicit_edit(workspace):
    w=workspace;tid=w.store.tasks()[0]['id'];before=backup_manager.live_hash(w.store)
    d=w.open_task_detail(tid);assert d.tabs.count()==4
    assert w.store.tasks(task_id=tid)[0]['task'].description in [label.text() for label in d.findChildren(QLabel)]
    for i in range(4):d.tabs.setCurrentIndex(i)
    assert backup_manager.live_hash(w.store)==before
    d.edit();editor=w.tools.windows[('task',tid)];assert editor.row['id']==tid
    editor.reject();assert backup_manager.live_hash(w.store)==before


def test_global_search_active_and_archived_plans_done_tasks_locate(workspace):
    w=workspace;tid=task(w.store,'全局已完成独立任务','2026-12-01')
    r=w.store.tasks(task_id=tid)[0]
    from tests.test_desktop_store import edit
    edit(w.store,r,status='已完成')
    d=w.open_global_search();d.query.query.setText('全局已完成');d.populate();assert len(d.results)==1
    d.tree.setCurrentItem(d.tree.topLevelItem(0));d.open_selected();assert ('detail',tid) in w.tools.windows
    d.locate();assert w.library_standalone and w.plan_tree.currentItem().data(0,Qt.ItemDataRole.UserRole)==tid
    d.query.query.setText('找不到');d.populate();assert not d.tree.currentItem() and not d.results


def test_global_search_paging_and_query_refresh_dont_write(workspace):
    w=workspace;d=GlobalSearchDialog(w);before=backup_manager.live_hash(w.store)
    d.results=[dict(kind='task',id=str(i),title='虚构任务'+str(i),plan='任务箱',date='2026-10-23',status='未开始',match='') for i in range(450)]
    d.limit=200;d.render();assert d.tree.topLevelItemCount()==200
    d.show_more();assert d.tree.topLevelItemCount()==400
    d.show_more();assert d.tree.topLevelItemCount()==450
    assert backup_manager.live_hash(w.store)==before;d.reject()


def test_reference_editor_validation_and_removal_cancel(workspace,monkeypatch):
    w=workspace;tid=w.store.tasks()[0]['id'];d=ResourceEditor(w)
    d.name.setText('教材');d.target.setText('javascript:bad');d.accept();assert d.result()!=QDialog.DialogCode.Accepted and d.error.text()
    d.target.setText('https://example.invalid/course');d.accept();assert d.result()==QDialog.DialogCode.Accepted
    task_resources.save(w.store,tid,[d.value],[]);detail=w.open_task_detail(tid);detail.resources.setCurrentItem(detail.resources.topLevelItem(0))
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda _:QDialog.DialogCode.Rejected)
    before=backup_manager.live_hash(w.store);detail.remove_resource();detail.open_resource();assert backup_manager.live_hash(w.store)==before


def test_deleted_task_detail_cannot_edit_or_open_resources(workspace):
    w=workspace;tid=task(w.store,'会删除的任务');d=w.open_task_detail(tid);w.store.delete_manual_task(tid);d.refresh()
    assert not d.tabs.isEnabled() and '删除' in d.error.text()


def test_cleanup_cancel_and_input_invalidates_preview(workspace,monkeypatch):
    w=workspace;w.store.manual_backup();d=CleanupDialog(w)
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda _:QDialog.DialogCode.Rejected)
    before=backup_manager.live_hash(w.store);d.preview();assert d.proposal is None and backup_manager.live_hash(w.store)==before
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda _:QDialog.DialogCode.Accepted)
    d.preview();assert d.proposal is not None and not d.apply_button.isEnabled()
    d.days.setValue(31);assert d.proposal is None;d.reject()


def test_startup_feedback_contains_no_database_operations(qapp):
    d=StartupWindow();d.show();QApplication.processEvents();assert d.isVisible() and '打开' in d.message.text();d.close()
