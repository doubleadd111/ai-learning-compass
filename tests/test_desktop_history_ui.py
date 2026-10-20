from datetime import date

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel

from desktop.confirmations import ConfirmationDialog
from desktop.controls import SoftComboBox
from desktop.credentials import CredentialStore
from desktop.history_ui import PlanHistoryWindow
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.window import MainWindow
from tests.test_desktop_store import create
from tests.test_desktop_delete_safety import snapshot


@pytest.fixture
def owner(qtbot, qapp, tmp_path):
    apply_theme(qapp, "light")
    s = Store(tmp_path)
    s.set_setting("reminders_paused", True)
    w = MainWindow(s, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.exiting = True
    w.close()


def test_real_history_flow_and_round_popup(owner):
    from desktop.history_check import check_plan_history
    create(owner.store)
    assert check_plan_history(owner)["success"]


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_popup_no_shadow_hover_escape_and_disabled_action(owner, qtbot, qapp, theme):
    apply_theme(qapp, theme)
    owner.selected_plan_id = create(owner.store)
    owner.refresh()
    owner.show_plan_menu()
    popup = owner.plan_more_menu
    assert popup.isVisible() and popup.windowFlags() & Qt.WindowType.NoDropShadowWindowHint
    assert popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert popup.delete_button.objectName() == "planDangerAction"
    qtbot.keyClick(popup.delete_button, Qt.Key.Key_Escape)
    assert not popup.isVisible()
    owner.selected_plan_id = None
    owner.show_plan_menu()
    assert not popup.delete_button.isEnabled()
    popup.hide()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_history_small_window_layout_local_time_and_no_plain_json(owner, qtbot, qapp, theme):
    apply_theme(qapp, theme)
    pid = create(owner.store)
    owner.store.rename_plan(pid, "一个容易辨认的计划名称")
    d = PlanHistoryWindow(owner, pid)
    qtbot.addWidget(d)
    d.resize(760, 540)
    d.show()
    qapp.processEvents()
    assert "一个容易辨认的计划名称" in d.plan_name.text()
    assert d.list.topLevelItemCount() == 2 and isinstance(d.side, SoftComboBox)
    assert len(d.list.topLevelItem(0).text(0).replace("\n", " ")) == 19
    assert not d.restore.isEnabled()
    d.side.setCurrentIndex(d.side.findData("before"))
    assert d.restore.isEnabled() and d.current_preview.changes
    texts = "\n".join(w.text() for w in d.findChildren(QLabel) if w.isVisible())
    assert "当前" in texts and "回溯后" in texts and "一个容易辨认的计划名称" in texts
    for button in (d.restore,):
        assert d.rect().contains(button.mapTo(d, button.rect().bottomRight()))


@pytest.mark.parametrize("approve", [False, True])
def test_history_confirmation_default_cancel_no_write_until_click(owner, qtbot, monkeypatch, approve):
    pid = create(owner.store)
    old_title = owner.store.get_plan(pid)["title"]
    owner.store.rename_plan(pid, "调整后的名称")
    d = PlanHistoryWindow(owner, pid)
    qtbot.addWidget(d)
    d.side.setCurrentIndex(d.side.findData("before"))
    before = snapshot(owner.store)
    def respond(box):
        assert box.cancel_button.isDefault() and box.confirm_button.text() == "确认回溯"
        assert "调整后的名称" in "\n".join(label.text() for label in box.findChildren(QLabel))
        assert snapshot(owner.store) == before
        return QDialog.DialogCode.Accepted if approve else QDialog.DialogCode.Rejected
    monkeypatch.setattr(ConfirmationDialog, "exec", respond)
    d.apply()
    if approve:
        assert owner.store.get_plan(pid)["title"] == old_title
        assert owner.store.versions(pid)[0]["kind"].startswith("回溯历史")
    else:
        assert snapshot(owner.store) == before


def test_stale_preview_and_worker_do_not_save(owner, qtbot, monkeypatch):
    pid = create(owner.store)
    owner.store.rename_plan(pid, "新的计划名称")
    d = PlanHistoryWindow(owner, pid)
    qtbot.addWidget(d)
    d.side.setCurrentIndex(d.side.findData("before"))
    owner.worker = object()
    d.apply()
    assert "模型请求" in d.status.text()
    owner.worker = None
    owner.store.rename_plan(pid, "预览期间又修改了名称")
    before = snapshot(owner.store)
    monkeypatch.setattr(ConfirmationDialog, "exec", lambda *_: QDialog.DialogCode.Accepted)
    d.apply()
    assert "已有变化" in d.status.text() and snapshot(owner.store) == before
    assert not d.restore.isEnabled()


def test_main_entry_reuses_open_window_and_new_button_label(owner):
    pid = create(owner.store)
    owner.selected_plan_id = pid
    owner.undo_selected()
    first = owner.history_replay_window
    assert first.isVisible()
    owner.undo_selected()
    assert owner.history_replay_window is first
    first.close()
    owner.undo_selected()
    assert owner.history_replay_window is not first
    owner.history_replay_window.close()
