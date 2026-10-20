"""Deletion is explicit, backed up, version checked and selectively recoverable."""
from contextlib import closing
from datetime import date
import hashlib
import sqlite3

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton

from desktop.confirmations import DeletePlanDialog
from desktop.credentials import CredentialStore
from desktop.focus import FocusTimer
from desktop.recovery import restore_missing_plan, TABLES
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.window import MainWindow
from tests.test_desktop_store import create, edit
from tests.test_desktop_manual_tasks import payload


def snapshot(store):
    with closing(store.connect()) as c:
        names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        return {name: sorted((tuple(r) for r in c.execute(f'SELECT * FROM "{name}"')), key=repr) for name in names}


@pytest.fixture
def window(qtbot, qapp, tmp_path):
    apply_theme(qapp, "light")
    store = Store(tmp_path)
    store.set_setting("reminders_paused", True)
    w = MainWindow(store, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.exiting = True
    w.close()


@pytest.mark.parametrize("text", ["", "删除", "确认", "确认删除 ", " 确认删除", "确认删除其他", "确认删 除"])
def test_phrase_exact_match_no_programmatic_bypass(qtbot, text):
    d = DeletePlanDialog("<完整计划名称>", 40, 1)
    qtbot.addWidget(d)
    d.phrase.setText(text)
    assert not d.confirm_button.isEnabled()
    d.accept()
    assert not d.confirmed and d.result() == QDialog.DialogCode.Rejected


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape])
def test_typed_phrase_enter_escape_still_cancel(qtbot, key):
    d = DeletePlanDialog("完整名称", 40, 1)
    qtbot.addWidget(d)
    d.show()
    d.phrase.setText("确认删除")
    with qtbot.waitSignal(d.rejected):
        qtbot.keyClick(d.phrase, key)
    assert not d.confirmed


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_delete_dialog_layout_close_and_click(qtbot, qapp, theme):
    apply_theme(qapp, theme)
    d = DeletePlanDialog("完整名称" * 20, 40, 1)
    qtbot.addWidget(d)
    d.resize(480, 420)
    d.show()
    qapp.processEvents()
    assert d.cancel_button.isDefault() and not d.confirm_button.isDefault()
    assert d.confirm_button.objectName() == "danger"
    texts = "\n".join(w.text() for w in d.findChildren(QLabel))
    assert "完整名称" * 20 in texts and "已完成 1 项" in texts and "归档" in texts
    assert not any("Cancel" in b.text() for b in d.findChildren(QPushButton))
    for b in (d.cancel_button, d.confirm_button):
        assert d.rect().contains(b.mapTo(d, b.rect().bottomRight()))
    d.close()
    assert not d.confirmed
    d.show()
    d.phrase.setText("确认删除")
    qtbot.mouseClick(d.confirm_button, Qt.MouseButton.LeftButton)
    assert d.confirmed and d.result() == QDialog.DialogCode.Accepted


def test_blocked_dialog_cannot_accept(qtbot):
    d = DeletePlanDialog("计划", 2, 0, blocking_reason="请先结束专注")
    qtbot.addWidget(d)
    d.phrase.setText("确认删除")
    d.accept()
    assert not d.confirm_button.isEnabled() and not d.confirmed


def test_store_backs_up_latest_data_and_deletes_only_target(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    other = create(store)
    independent = store.add_manual_task(payload(), date.today().isoformat())
    row = store.tasks(pid)[0]
    edit(store, row, status="已完成", note="每日备份之后的新记录")
    store.record_focus("finished-session", row["id"], 65, "2026-10-19T12:00:00")
    original = snapshot(store)
    backup = store.delete_plan(pid, expected_revision=store.get_plan(pid)["revision"])
    assert backup.name.startswith("before-delete-plan-")
    with closing(sqlite3.connect(backup)) as c:
        for name, records in original.items():
            assert sorted(c.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr) == records
    assert store.get_plan(other) and any(r["id"] == independent for r in store.tasks())
    assert not store.read("SELECT * FROM plans WHERE id=?", (pid,))
    assert not store.read("SELECT * FROM focus_sessions WHERE id='finished-session'")
    with closing(store.connect()) as c:
        assert not c.execute("PRAGMA foreign_key_check").fetchall()


def test_backup_failure_never_deletes(tmp_path, monkeypatch):
    store = Store(tmp_path)
    pid = create(store)
    original = snapshot(store)
    def fail(*args):
        raise OSError("备份目录不可写")
    monkeypatch.setattr(store, "manual_backup", fail)
    with pytest.raises(OSError):
        store.delete_plan(pid)
    assert snapshot(store) == original


def test_revision_change_and_missing_plan_fail_closed(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    revision = store.get_plan(pid)["revision"]
    edit(store, store.tasks(pid)[0], note="确认期间的新记录")
    original = snapshot(store)
    with pytest.raises(ValueError, match="已有变化"):
        store.delete_plan(pid, expected_revision=revision)
    with pytest.raises(ValueError, match="找不到"):
        store.delete_plan("does-not-exist")
    assert snapshot(store) == original
    assert not list(store.backups.glob("before-delete-plan-*"))


@pytest.mark.parametrize("mode", ["running", "paused", "break"])
def test_own_focus_and_break_block_deletion(tmp_path, mode):
    store = Store(tmp_path)
    pid = create(store)
    focus = FocusTimer(store)
    focus.start(store.tasks(pid)[0]["id"])
    if mode == "paused":
        focus.pause()
    elif mode == "break":
        focus.phase, focus.session_id = "break", None
        focus._commit()
    before = snapshot(store)
    with pytest.raises(ValueError, match="专注或休息"):
        store.delete_plan(pid)
    assert snapshot(store) == before


@pytest.mark.parametrize("accept", [False, True])
def test_window_more_menu_and_captured_plan_id(window, monkeypatch, accept):
    store = window.store
    pid, other = create(store), create(store)
    window.refresh()
    window.selected_plan_id = pid
    assert not any(b.text() == "删除" for b in window.stack.widget(1).findChildren(QPushButton))
    assert window.plan_more_button.text() == "更多操作"
    assert window.delete_plan_action.text() == "删除计划…"
    def respond(d):
        assert store.get_plan(pid)["title"] in "\n".join(w.text() for w in d.findChildren(QLabel))
        window.selected_plan_id = other
        d.phrase.setText("确认删除")
        if accept:
            d.confirm_button.click()
        else:
            d.reject()
        return d.result()
    monkeypatch.setattr(DeletePlanDialog, "exec", respond)
    window.delete_selected()
    assert bool(store.read("SELECT id FROM plans WHERE id=?", (pid,))) is not accept
    assert store.get_plan(other) and window.selected_plan_id == other


def test_selection_missing_or_fake_accept_no_delete(window, monkeypatch):
    window.selected_plan_id = None
    window.delete_selected()
    pid = create(window.store)
    window.selected_plan_id = pid
    monkeypatch.setattr(DeletePlanDialog, "exec", lambda d: QDialog.DialogCode.Accepted)
    window.delete_selected()
    assert window.store.get_plan(pid)


def test_real_menu_modal_backup_restore_smoke(window):
    from desktop.deletion_check import check_plan_deletion
    create(window.store)
    assert check_plan_deletion(window)["success"]


def test_selective_restore_preserves_ids_records_and_unrelated_data(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    other = create(store)
    row = store.tasks(pid)[0]
    edit(store, row, status="已完成", manual_minutes=5, note="要恢复的笔记")
    store.record_focus("old-focus", row["id"], 72, "2026-10-19T12:00:00")
    wanted = store.get_plan(pid)
    backup = store.delete_plan(pid)
    edit(store, store.tasks(other)[0], note="删除后其他计划的新笔记")
    store.set_setting("keep-current-setting", "new")
    original = snapshot(store)
    source_digest = hashlib.sha256(backup.read_bytes()).digest()
    with closing(sqlite3.connect(backup)) as source, closing(store.connect()) as target:
        source.row_factory = sqlite3.Row
        with target:
            target.execute("BEGIN IMMEDIATE")
            counts = restore_missing_plan(target, source, pid)
    restored = store.get_plan(pid)
    assert restored["tasks"] == wanted["tasks"] and restored["revision"] == wanted["revision"]
    assert counts["tasks"] == len(wanted["tasks"]) and counts["focus_sessions"] == 1
    assert store.setting("keep-current-setting") == "new"
    assert hashlib.sha256(backup.read_bytes()).digest() == source_digest
    after = snapshot(store)
    for name, records in original.items():
        assert all(row in after[name] for row in records)
        if name not in TABLES:
            assert after[name] == records


@pytest.mark.parametrize("problem", ["existing", "version", "foreign_keys", "collision"])
def test_restore_refuses_unsafe_or_conflicting_input(tmp_path, problem):
    store = Store(tmp_path)
    pid = create(store)
    backup = store.manual_backup()
    if problem != "existing":
        store.delete_plan(pid)
    with closing(sqlite3.connect(backup)) as source, closing(store.connect()) as target:
        source.row_factory = sqlite3.Row
        if problem == "version":
            source.execute("PRAGMA user_version=4")
        if problem == "foreign_keys":
            target.execute("PRAGMA foreign_keys=OFF")
        if problem == "collision":
            # One unrelated task occupies an old ID. Never replace that row.
            tid = store.add_manual_task(payload(), date.today().isoformat())
            old_id = source.execute("SELECT id FROM tasks WHERE plan_id=? LIMIT 1", (pid,)).fetchone()[0]
            target.execute("UPDATE tasks SET id=? WHERE id=?", (old_id, tid))
            target.commit()
        before = snapshot(store)
        with pytest.raises((ValueError, sqlite3.IntegrityError)):
            with target:
                target.execute("BEGIN IMMEDIATE")
                restore_missing_plan(target, source, pid)
        assert snapshot(store) == before
