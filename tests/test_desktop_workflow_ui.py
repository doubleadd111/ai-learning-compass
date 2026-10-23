from datetime import date
import pytest
from PySide6.QtCore import Qt,QEvent,QCoreApplication
from PySide6.QtWidgets import QApplication,QDialog
from desktop.workflow_ui import BatchDialog,ReminderDialog,DependenciesDialog
from desktop.backup_ui import BackupDialog
from desktop.confirmations import ConfirmationDialog
from desktop import workflow as svc,backup_manager
from desktop.theme import apply_theme
from tests.test_desktop_workspace_ui import workspace
from tests.test_desktop_workflow import task


@pytest.mark.parametrize('kind',['batch','reminders','dependencies','backups'])
@pytest.mark.parametrize('theme',['light','dark'])
def test_new_windows_peer_readonly_small_and_reopen(workspace,kind,theme):
    w=workspace;tid=task(w.store,day=date.today().isoformat());w.store.manual_backup()
    apply_theme(QApplication.instance(),theme);before=backup_manager.live_hash(w.store)
    calls={'batch':lambda:w.open_batch(w.today_tree),'reminders':w.open_reminders,
           'dependencies':lambda:w.open_dependencies(tid),'backups':w.open_backups}
    calls[kind]();key=('dependencies',tid) if kind=='dependencies' else kind
    d=w.tools.windows[key];d.resize(640,520);QApplication.processEvents()
    assert not d.isModal() and d.parentWidget() is None and d.windowHandle().transientParent() is None
    assert not d.windowFlags() & Qt.WindowType.WindowStaysOnTopHint and w.isEnabled() and d.isVisible()
    d.reject();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    assert key not in w.tools.windows and backup_manager.live_hash(w.store)==before
    calls[kind]();assert w.tools.windows[key] is not d


def test_batch_preview_cancel_edit_and_visible_scope(workspace,monkeypatch):
    w=workspace;a=task(w.store,'第一项');z=task(w.store,'另一项')
    d=BatchDialog(w,[a,z]);before=backup_manager.live_hash(w.store)
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda self:QDialog.DialogCode.Rejected)
    d.preview();assert d.proposal is None and not d.apply_button.isEnabled()
    assert backup_manager.live_hash(w.store)==before
    d.search.query.setText('第一项');assert d.tree.topLevelItemCount()==1
    d.select_visible(True);d.operation.setCurrentIndex(1)
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda self:QDialog.DialogCode.Accepted)
    d.group.setText('练习');d.preview();assert d.apply_button.isEnabled()
    d.group.setText('其他');assert d.proposal is None and not d.apply_button.isEnabled();d.reject()


def test_reminder_double_click_and_mute_preserve_date(workspace,monkeypatch):
    w=workspace;a=task(w.store,day=date.today().isoformat());d=ReminderDialog(w)
    item=next(d.tree.topLevelItem(i) for i in range(d.tree.topLevelItemCount()) if d.tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)==a)
    opened=[];monkeypatch.setattr(w,'open_task_detail',opened.append);d.open_task(item);assert opened==[a]
    d.tree.setCurrentItem(item);d.control('mute')
    assert w.store.read('SELECT muted_on FROM reminder_controls WHERE task_id=?',(a,))[0]['muted_on']==date.today().isoformat()
    assert next(r for r in w.store.tasks() if r['id']==a)['due_date']==date.today().isoformat();d.reject()


def test_dependency_save_and_focus_default_cancel(workspace,monkeypatch):
    w=workspace;a=task(w.store,'前置任务');z=task(w.store,'后续任务');before=w.store.read('SELECT * FROM tasks ORDER BY id')
    d=DependenciesDialog(w,z)
    for i in range(d.tree.topLevelItemCount()):
        item=d.tree.topLevelItem(i)
        if item.data(0,Qt.ItemDataRole.UserRole)==a:item.setCheckState(0,Qt.CheckState.Checked)
    d.save();assert w.store.read('SELECT * FROM tasks ORDER BY id')==before
    assert svc.prerequisites(w.store,z)[0]['id']==a
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda self:QDialog.DialogCode.Rejected)
    w.start_focus_for_task(z);assert not w.focus.session_id and not w.focus.running


def test_backup_cancel_does_not_restore(workspace,monkeypatch):
    w=workspace;w.store.manual_backup();d=BackupDialog(w);d.tree.setCurrentItem(d.tree.topLevelItem(0))
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda self:QDialog.DialogCode.Rejected)
    before=backup_manager.live_hash(w.store);d.preview()
    assert not d.restore_button.isEnabled() and backup_manager.live_hash(w.store)==before;d.reject()
