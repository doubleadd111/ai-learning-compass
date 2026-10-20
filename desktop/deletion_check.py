"""Exercise actual menu/modal/delete/backup/restore only in isolated smoke QA."""
from contextlib import closing
from datetime import date
import json
import sqlite3

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from desktop.confirmations import DeletePlanDialog
from desktop.recovery import restore_missing_plan


def check_plan_deletion(window):
    app, store = QApplication.instance(), window.store
    sample = store.get_plan(store.list_plans()[0]["id"])
    original_ids = {p["id"] for p in store.list_plans()}
    pid = store.save_plan(sample["profile"], sample["plan"].model_copy(update={"title": "离线删除检查样例"}),
                          date.fromisoformat(sample["start_date"]), json.loads(sample["weekdays_json"]))
    before = store.export_plan(pid)
    failures = []
    for approve in (False, True):
        window.selected_plan_id = pid
        window.refresh()
        def exercise():
            dialog = app.activeModalWidget()
            try:
                assert isinstance(dialog, DeletePlanDialog)
                dialog.phrase.setText("确认删除 ")
                assert not dialog.confirm_button.isEnabled()
                dialog.accept()
                assert not dialog.confirmed and dialog.isVisible()
                dialog.phrase.setText("确认删除")
                assert dialog.confirm_button.isEnabled()
                if approve:
                    dialog.confirm_button.click()
                else:
                    dialog.cancel_button.click()
            except Exception as error:
                failures.append(type(error).__name__)
                if dialog:
                    dialog.reject()
        timeout = QTimer(window)
        timeout.setSingleShot(True)
        def abort():
            failures.append("ModalTimeout")
            if app.activeModalWidget():
                app.activeModalWidget().reject()
        timeout.timeout.connect(abort)
        timeout.start(5000)
        QTimer.singleShot(60, exercise)
        window.delete_plan_action.trigger()
        timeout.stop()
        timeout.deleteLater()
        assert not failures, "Deletion check failed: " + ",".join(failures)
        exists = bool(store.read("SELECT 1 FROM plans WHERE id=?", (pid,)))
        assert exists != approve
        if not approve:
            assert store.export_plan(pid) == before
    backup = max(store.backups.glob("before-delete-plan-*.sqlite3"), key=lambda p: p.stat().st_mtime_ns)
    with closing(sqlite3.connect(backup)) as source, closing(store.connect()) as target:
        source.row_factory = sqlite3.Row
        with target:
            target.execute("BEGIN IMMEDIATE")
            restore_missing_plan(target, source, pid)
    assert store.export_plan(pid) == before
    store.delete_plan(pid, expected_revision=store.get_plan(pid)["revision"])
    assert {p["id"] for p in store.list_plans()} == original_ids
    window.selected_plan_id = sample["id"]
    window.refresh()
    return dict(menu_entry=True, modal_exec=True, exact_phrase=True, cancel_preserves=True,
                latest_backup=True, selective_restore=True, other_plans_preserved=True, success=True)
