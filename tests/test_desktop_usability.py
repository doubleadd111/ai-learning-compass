import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from contextlib import closing
from datetime import date, datetime, timedelta
import hashlib
import json
import socket
import sqlite3
import ssl

import httpx
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextBlockFormat
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from desktop.credentials import CredentialStore
from desktop.diagnostics import ConnectionDiagnostic, classify
from desktop.gateway import GatewayError, ModelGateway
from desktop.guide import open_guide
from desktop.models import ModelConnection
from desktop.network import CancellableTransport, RequestControl
from desktop.planning_ui import DraftsDialog
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.window import MainWindow
from tests.test_desktop_planning import draft
from tests.test_desktop_store import create, edit


@pytest.fixture
def ui(qtbot, tmp_path):
    apply_theme(QApplication.instance(), "light")
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.show()
    yield window
    window.exiting = True
    window.close()


def test_global_guide_ownerless_minimize_restore_and_preserve_chapter(ui, qtbot):
    guide = open_guide(ui, "modify")
    assert guide.parentWidget() is None and not guide.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose)
    other_owner = DraftsDialog(ui.store, ui)
    qtbot.addWidget(other_owner)
    assert open_guide(other_owner) is guide
    assert guide.toc.currentItem().text() == "修改与保护计划"
    guide.showMinimized()
    QApplication.processEvents()
    assert guide.isMinimized()
    assert open_guide(ui) is guide and not guide.isMinimized()
    other_owner.close()
    assert guide.isVisible()
    guide.close()
    assert ui.isVisible()


def test_guide_actual_spacing_indentation_and_code_preserved(ui):
    guide = open_guide(ui, "modify")
    block = guide.browser.document().begin().next()
    while block.isValid() and (block.blockFormat().headingLevel() or block.textList() or not block.text().strip()):
        block = block.next()
    fmt = block.blockFormat()
    assert fmt.lineHeight() == 150
    assert fmt.lineHeightType() == QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
    assert fmt.bottomMargin() == 12 and fmt.textIndent() == 28
    guide.go("build")
    assert "python.exe" in guide.browser.toPlainText()


def test_focus_buttons_bind_ids_continue_and_keep_settings(ui, qtbot):
    pid = create(ui.store)
    ui.refresh()
    rows = ui.store.tasks(pid)
    ui.focus_minutes.setValue(17)
    ui.navigate(1)
    QApplication.processEvents()
    first = ui.plan_tree.topLevelItem(0)
    button = ui.plan_tree.itemWidget(first, 4)
    assert button.isVisible()
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    assert ui.stack.currentIndex() == 3 and ui.focus.task_id == rows[0]["id"]
    assert ui.focus.focus_minutes == 17 and ui.focus.running
    session = ui.focus.session_id
    ui.focus.remaining -= 30
    remaining = ui.focus.remaining
    ui.start_focus_for_task(rows[0]["id"])
    assert ui.focus.session_id == session and ui.focus.remaining <= remaining
    ui.pause_focus()
    ui.start_focus_for_task(rows[0]["id"])
    assert ui.focus.running and ui.focus.session_id == session
    recorded = ui.store.tasks(pid)[0]
    assert recorded["status"] == ("进行中" if recorded["focus_seconds"] else "未开始")


def test_focus_switch_default_cancel_then_commit_once(ui, monkeypatch):
    pid = create(ui.store)
    ui.refresh()
    first, second = ui.store.tasks(pid)[:2]
    ui.start_focus_for_task(first["id"])
    ui.focus.elapsed = 90
    answers = []
    def cancel(*args):
        answers.append(args[-1])
        return QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, "question", cancel)
    ui.start_focus_for_task(second["id"])
    assert ui.focus.task_id == first["id"] and answers == [QMessageBox.StandardButton.Cancel]
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    ui.start_focus_for_task(second["id"])
    assert ui.focus.task_id == second["id"] and ui.focus.running
    assert ui.store.tasks(pid)[0]["focus_seconds"] == 90
    ui.pause_focus()
    assert ui.store.tasks(pid)[0]["focus_seconds"] == 90


def test_focus_from_break_starts_focus_not_break(ui, monkeypatch):
    pid = create(ui.store)
    ui.refresh()
    ui.focus.phase, ui.focus.remaining = "break", 45
    monkeypatch.setattr(QMessageBox, "question", lambda *_: QMessageBox.StandardButton.Yes)
    ui.start_focus_for_task(ui.store.tasks(pid)[0]["id"])
    assert ui.focus.phase == "focus" and ui.focus.running


def test_completed_and_archived_focus_buttons_disabled(ui):
    pid = create(ui.store)
    row = ui.store.tasks(pid)[0]
    edit(ui.store, row, status="已完成")
    ui.refresh()
    assert not ui.plan_tree.itemWidget(ui.plan_tree.topLevelItem(0), 4).isEnabled()
    ui.store.archive(pid)
    ui.archived.setChecked(True)
    ui.refresh()
    assert all(not ui.plan_tree.itemWidget(ui.plan_tree.topLevelItem(i), 4).isEnabled() for i in range(ui.plan_tree.topLevelItemCount()))


def test_week_table_focus_button_is_not_clipped_or_status_duplicated(ui):
    create(ui.store)
    ui.refresh()
    ui.navigate(2)
    panel = ui.week_panel
    panel.refresh(date(2026, 10, 12))
    # Place the fixture's first Monday inside the displayed week.
    if not panel.rows:
        first_date = date.fromisoformat(ui.store.tasks()[0]["due_date"])
        panel.refresh(first_date - timedelta(days=first_date.weekday()))
    panel.mode.setCurrentIndex(1)
    QApplication.processEvents()
    item = panel.table.topLevelItem(0)
    button = panel.table.itemWidget(item, 6)
    assert button.width() >= button.fontMetrics().horizontalAdvance(button.text()) + 16
    assert panel.table.columnWidth(6) >= 112
    assert panel.table.itemWidget(item, 5) is None


@pytest.mark.parametrize("start", [date(2026, 10, 26), date(2026, 12, 28)])
def test_week_grid_and_table_same_tasks_across_month_year(ui, start):
    pid = create(ui.store)
    rows = ui.store.tasks(pid)
    for i, row in enumerate(rows):
        edit(ui.store, row, payload=row["task"].model_copy(update={"duration_minutes": 15}), due_date=(start + timedelta(days=i*2)).isoformat())
    ui.refresh()
    panel = ui.week_panel
    panel.refresh(start)
    assert panel.start.weekday() == 0
    ids = {r["id"] for r in panel.rows}
    assert ids == {card.task_id for card in panel.cards}
    assert ids == {panel.table.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(panel.table.topLevelItemCount())}
    panel.select_task(rows[0]["id"])
    panel.mode.setCurrentIndex(1)
    assert panel.selected_id == rows[0]["id"]
    assert rows[0]["task"].deliverable in panel.detail.toPlainText()
    panel.mode.setCurrentIndex(0)
    assert panel.selected_id == rows[0]["id"]


def test_week_filters_actual_time_and_empty_week(ui):
    pid, other = create(ui.store), create(ui.store)
    row = ui.store.tasks(pid)[0]
    edit(ui.store, row, status="已完成", manual_minutes=7)
    ui.store.record_focus("test-focus", row["id"], 120, "2026-10-01T10:00:00+08:00")
    panel = ui.week_panel
    panel.refresh(date.fromisoformat(row["due_date"]) - timedelta(days=date.fromisoformat(row["due_date"]).weekday()))
    panel.plan_filter.setCurrentIndex(panel.plan_filter.findData(pid))
    panel.status_filter.setCurrentText("已完成")
    assert len(panel.rows) == 1 and "累计实际 9 分钟" in panel.summary.text()
    assert "按本周排期归属" in panel.summary.text()
    panel.subject_filter.setCurrentIndex(0)
    panel.refresh(date(2030, 1, 7))
    assert panel.rows == [] and panel.table.topLevelItemCount() == 0 and panel.cards == []


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_week_small_window_scroll_and_draft_columns(ui, theme):
    apply_theme(QApplication.instance(), theme)
    create(ui.store)
    ui.refresh()
    ui.navigate(2)
    ui.resize(980, 680)
    QApplication.processEvents()
    assert ui.week_panel.calendar.widget().minimumSizeHint().width() > 700
    assert ui.week_panel.edit_button.mapTo(ui, ui.week_panel.edit_button.rect().bottomRight()).y() < ui.height()
    d = draft()
    ui.store.save_draft(d)
    dialog = DraftsDialog(ui.store, ui)
    dialog.show()
    QApplication.processEvents()
    item = dialog.list.topLevelItem(0)
    assert len(item.text(1)) == 16 and len(item.text(2)) == 16
    dialog.close()


def test_draft_creation_and_modified_only_on_semantic_change(tmp_path, monkeypatch):
    store = Store(tmp_path)
    clock = ["2026-10-15T10:00:00+08:00"]
    monkeypatch.setattr("desktop.store.now", lambda: clock[0])
    d = draft()
    store.save_draft(d)
    clock[0] = "2026-10-15T10:01:00+08:00"
    d.revision += 1
    store.save_draft(d)
    entry = store.draft_entries()[0]
    assert entry["created_at"] == entry["updated_at"] == "2026-10-15T10:00:00+08:00"
    d.pending_answers = {"question": "我的答案"}
    store.save_draft(d)
    entry = store.draft_entries()[0]
    assert entry["created_at"].startswith("2026-10-15T10:00") and entry["updated_at"] == clock[0]
    assert Store(tmp_path).drafts()[0].pending_answers == d.pending_answers


def make_schema_two(store):
    with closing(sqlite3.connect(store.path)) as c, c:
        c.execute("DROP TABLE pause_history")
        c.execute("DROP TABLE plan_schedules")
        c.execute("ALTER TABLE planning_drafts DROP COLUMN created_at")
        c.execute("PRAGMA user_version=2")


def test_schema_two_migration_restore_unknown_created_time_and_source_preserved(tmp_path):
    old = Store(tmp_path / "old")
    create(old)
    old.save_draft(draft())
    stamp = old.draft_entries()[0]["updated_at"]
    make_schema_two(old)
    backup = old.manual_backup()
    digest = hashlib.sha256(backup.read_bytes()).digest()
    migrated = Store(old.directory)
    assert migrated.draft_entries()[0]["created_at"] is None
    assert migrated.draft_entries()[0]["updated_at"] == stamp
    target = Store(tmp_path / "target")
    target.restore(backup)
    assert target.draft_entries()[0]["created_at"] is None
    assert hashlib.sha256(backup.read_bytes()).digest() == digest


def test_failed_schema_three_migration_keeps_source(tmp_path, monkeypatch):
    old = Store(tmp_path)
    old.save_draft(draft())
    make_schema_two(old)
    before = old.path.read_bytes()
    original = Store._upgrade_v3
    def fail(c):
        original(c)
        raise ValueError("模拟迁移失败")
    monkeypatch.setattr(Store, "_upgrade_v3", staticmethod(fail))
    with pytest.raises(ValueError):
        Store(tmp_path)
    assert old.path.read_bytes() == before


def test_old_connection_defaults_do_not_change_draft_modified_time(tmp_path):
    store = Store(tmp_path)
    d = draft(connection=ModelConnection(name="旧配置", base_url="https://example.invalid", model="mock"))
    store.save_draft(d)
    stamp = store.draft_entries()[0]["updated_at"]
    data = json.loads(d.model_dump_json())
    data["connection"].pop("network_mode")
    with store.write() as c:
        c.execute("UPDATE planning_drafts SET data_json=? WHERE id=?", (json.dumps(data), d.id))
    resumed = store.drafts()[0]
    resumed.revision += 1
    store.save_draft(resumed)
    assert store.draft_entries()[0]["updated_at"] == stamp


@pytest.mark.parametrize("error,category", [(socket.gaierror("private"), "dns"),
    (ssl.SSLCertVerificationError("private"), "tls"), (httpx.ProxyError("private"), "proxy"),
    (ModuleNotFoundError("private"), "runtime_dependency"), (httpx.ReadTimeout("private"), "timeout")])
def test_diagnostic_classifies_chained_errors_without_echoing_secrets(error, category):
    outer = httpx.ConnectError("Bearer private-key-must-not-leak")
    outer.__cause__ = error
    found, chain = classify(outer)
    diagnostic = ConnectionDiagnostic(operation="connection_test", stage="连接服务", elapsed_ms=1, category=found, exception_types=chain)
    assert found == category and "private" not in diagnostic.summary() and "Bearer" not in diagnostic.model_dump_json()


def test_route_snapshot_and_copy_summary_has_no_credentials(ui, monkeypatch):
    config = ModelConnection(name="本机测试", base_url="https://example.invalid", model="mock", network_mode="direct")
    gateway = ModelGateway(config, "placeholder")
    config.network_mode = "system"
    assert gateway.connection.network_mode == "direct"
    assert gateway.client._client._transport.network_mode == "direct"
    gateway.close()
    ui.last_diagnostic = ConnectionDiagnostic(operation="models", stage="连接服务", elapsed_ms=3, category="proxy", exception_types=["ProxyError"])
    ui.copy_diagnostic()
    assert "proxy" in QApplication.clipboard().text() and "密钥" in ui.notice.text()


def test_gateway_records_underlying_tls_cause_and_keeps_public_message_safe():
    def fail(_request):
        error = httpx.ConnectError("private-proxy-credential")
        error.__cause__ = ssl.SSLCertVerificationError("private-cert-path")
        raise error
    from openai import OpenAI
    config = ModelConnection(name="mock", base_url="https://example.invalid", model="mock")
    with httpx.Client(transport=httpx.MockTransport(fail)) as http:
        with OpenAI(api_key="placeholder", base_url=config.base_url, max_retries=0, http_client=http) as client:
            gateway = ModelGateway(config, "placeholder", client=client)
            with pytest.raises(GatewayError, match="证书") as caught:
                gateway.test()
            assert "private" not in str(caught.value)
            assert gateway.last_diagnostic.category == "tls"
