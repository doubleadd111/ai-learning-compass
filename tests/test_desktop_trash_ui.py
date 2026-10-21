from datetime import datetime, timedelta
import pytest
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtWidgets import QDialog, QPushButton
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.credentials import CredentialStore
from desktop.window import MainWindow
from desktop.confirmations import ConfirmationDialog
from desktop.trash_ui import TrashDialog, PurgeDialog
from desktop import trash
from tests.test_desktop_manual_tasks import payload


@pytest.fixture
def setup(qtbot, qapp, tmp_path):
    apply_theme(qapp, 'light')
    s = Store(tmp_path)
    s.set_setting('reminders_paused', True)
    tid = s.add_manual_task(payload(), '2026-12-01')
    s.delete_manual_task(tid)
    d = TrashDialog(s)
    qtbot.addWidget(d)
    d.show()
    return s, d


@pytest.mark.parametrize('theme', ['light','dark'])
@pytest.mark.parametrize('size', [(920,720),(640,520)])
def test_dialog_style_small_keyboard_and_plain_text(setup, qtbot, qapp, theme, size):
    s, d = setup
    apply_theme(qapp, theme)
    d.resize(*size)
    qapp.processEvents()
    assert d.restore_button.isEnabled() and d.items.topLevelItemCount()==1
    assert '原日期' in d.details.toPlainText()
    for b in (d.restore_button, d.purge_button):
        assert d.rect().contains(b.mapTo(d, b.rect().center()))
    d.search.setText('没有匹配')
    assert d.items.topLevelItemCount()==0 and not d.restore_button.isEnabled()
    d.search.clear()
    assert d.items.topLevelItemCount()==1
    qtbot.keyClick(d.search, Qt.Key.Key_Escape)
    assert len(trash.entries(s))==1 and not s.tasks()


def test_restore_cancel_then_confirm(setup, qtbot, monkeypatch):
    s, d = setup
    monkeypatch.setattr(ConfirmationDialog, 'exec', lambda self: QDialog.DialogCode.Rejected)
    d.restore()
    assert len(trash.entries(s))==1 and not s.tasks()
    monkeypatch.setattr(ConfirmationDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    d.restore()
    assert not trash.entries(s) and len(s.tasks())==1
    assert '已按预览中的日期恢复' in d.details.toPlainText()


def test_stale_preview_ui_does_not_overwrite(setup, monkeypatch):
    s, d = setup
    s.add_manual_task(payload(), '2026-12-02')
    monkeypatch.setattr(ConfirmationDialog, 'exec', lambda self: QDialog.DialogCode.Accepted)
    d.restore()
    assert d.error.isVisible() and '变化' in d.error.text()
    assert len(trash.entries(s))==1 and len(s.tasks())==1
    assert not d.restore_button.isEnabled()


@pytest.mark.parametrize('text', ['', '彻底删除 ', '彻底删除'])
def test_purge_phrase_and_enter_always_cancel(qtbot, text):
    d = PurgeDialog('需要保留的资料')
    qtbot.addWidget(d)
    d.show()
    d.phrase.setText(text)
    assert d.confirm_button.isEnabled() == (text=='彻底删除')
    assert d.cancel_button.isDefault()
    qtbot.keyClick(d.phrase, Qt.Key.Key_Return)
    assert d.result()==QDialog.DialogCode.Rejected


def test_purge_click_requires_exact_phrase(setup, monkeypatch):
    s, d = setup
    def confirm(box):
        box.phrase.setText('彻底删除')
        box.confirm_button.click()
        return box.result()
    monkeypatch.setattr(PurgeDialog, 'exec', confirm)
    d.purge()
    assert not trash.entries(s) and not s.tasks()
    assert '彻底删除' in d.details.toPlainText()


def test_expired_hidden_then_explained(setup, monkeypatch):
    s, d = setup
    stamp = datetime.fromisoformat(trash.entries(s)[0]['expires_at'])
    monkeypatch.setattr(trash, 'clock', lambda: stamp)
    d.refresh()
    assert d.items.topLevelItemCount()==0
    d.expired.setChecked(True)
    assert d.items.topLevelItemCount()==1 and not d.restore_button.isEnabled()
    assert '超过30天' in d.details.toPlainText() and d.purge_button.isEnabled()


def test_library_entry_enabled_without_plans_and_ownerless(qtbot, qapp, tmp_path):
    s = Store(tmp_path)
    s.set_setting('reminders_paused', True)
    w = MainWindow(s, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(w)
    w.show()
    w.navigate(1)
    action = next(a for a,_ in w.history_menu_button.action_menu.items if a.text()=='回收站')
    assert action.isEnabled()
    action.trigger()
    qapp.processEvents()
    d = w.tools.windows['trash']
    assert d.parentWidget() is None and not d.isModal()
    assert not d.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert not d.restore_button.isEnabled()
    d.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    w.open_trash()
    assert w.tools.windows['trash'].isVisible()
    w.tools.close_all()
    w.exiting = True
    w.close()
