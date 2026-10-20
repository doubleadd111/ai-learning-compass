"""Real timeline/confirmation replay in isolated startup QA only."""
from datetime import date
import json

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QApplication
from desktop.confirmations import ConfirmationDialog


def check_plan_history(window):
    store, app = window.store, QApplication.instance()
    sample = store.get_plan(store.list_plans()[0]["id"])
    pid = store.save_plan(sample["profile"], sample["plan"].model_copy(update={"title": "离线历史检查"}),
                          date.fromisoformat(sample["start_date"]), json.loads(sample["weekdays_json"]))
    original = store.versions(pid)[0]["id"]
    store.rename_plan(pid, "离线历史检查修改后")
    task = store.tasks(pid)[0]
    store.record_focus("isolated-history-session", task["id"], 9, "2026-10-19T10:00:00")
    window.selected_plan_id = pid
    window.refresh()
    window.show_plan_menu()
    app.processEvents()
    assert window.plan_more_menu.isVisible() and window.plan_more_menu.delete_button.text() == "删除计划…"
    window.plan_more_menu.hide()
    window.undo_selected()
    flow = window.history_replay_window
    app.processEvents()
    errors = []
    for target, side, expected in ((original, "after", "离线历史检查"), (None, "before", "离线历史检查修改后")):
        target = target or store.versions(pid)[0]["id"]
        item = next(flow.list.topLevelItem(i) for i in range(flow.list.topLevelItemCount())
                    if flow.list.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == target)
        flow.list.setCurrentItem(item)
        flow.side.setCurrentIndex(flow.side.findData(side))
        assert flow.current_preview and flow.restore.isEnabled()
        def confirm():
            box = app.activeModalWidget()
            try:
                assert isinstance(box, ConfirmationDialog) and box.confirm_button.text() == "确认回溯"
                box.confirm_button.click()
            except Exception as error:
                errors.append(type(error).__name__)
                if box:
                    box.reject()
        timeout = QTimer(flow)
        timeout.setSingleShot(True)
        def abort():
            errors.append("HistoryModalTimeout")
            if app.activeModalWidget():
                app.activeModalWidget().reject()
        timeout.timeout.connect(abort)
        timeout.start(5000)
        QTimer.singleShot(60, confirm)
        flow.restore.click()
        timeout.stop()
        timeout.deleteLater()
        assert not errors, errors
        assert store.get_plan(pid)["title"] == expected
        assert store.tasks(pid)[0]["focus_seconds"] == 9 and store.tasks(pid)[0]["status"] == "进行中"
        assert store.versions(pid)[0]["kind"].startswith("回溯历史")
    assert len(store.versions(pid)) == 4
    flow.close()
    store.delete_plan(pid)
    window.selected_plan_id = sample["id"]
    window.refresh()
    return dict(real_timeline=True, confirmation=True, replay_and_return=True, append_only=True,
                focus_records_preserved=True, rounded_action_popup=True, success=True)
