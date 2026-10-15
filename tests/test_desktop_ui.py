import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import QTimer
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from desktop.credentials import CredentialStore
from desktop.models import ModelConnection
from desktop.store import Store
from desktop.window import MainWindow


def test_desktop_sample_navigation_record_and_model_selection(qtbot, tmp_path):
    store = Store(tmp_path)
    configs = [ModelConnection(name=f"测试连接{i}", base_url="https://example.invalid", model=f"model-{i}") for i in (1, 2)]
    for config in configs:
        store.save_connection(config)
    window = MainWindow(store, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.show()
    window.load_sample()
    assert window.stack.count() == 5
    assert window.plan_tree.topLevelItemCount() == 6
    window.model_picker.setCurrentIndex(1)
    assert store.setting("active_connection") == configs[1].id
    def fill_dialog():
        dialog = QApplication.activeModalWidget()
        dialog.status.setCurrentText("已完成")
        dialog.note.setPlainText("桌面界面记录成功")
        dialog.accept()
    QTimer.singleShot(0, fill_dialog)
    window.edit_tree_task(window.plan_tree.topLevelItem(0))
    assert store.progress(window.selected_plan_id)[(1, 1)].status == "已完成"
    for index in range(5):
        window.navigate(index)
        assert window.stack.currentIndex() == index
    window.focus.stop()
    window.exiting = True
    window.close()


def test_countdown_completion_and_http_response_timeout_hint(qtbot, tmp_path, monkeypatch):
    import threading
    from types import SimpleNamespace
    gate = threading.Event()
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.start_work("快速成功", lambda cancel: True, lambda result: None)
    qtbot.waitUntil(lambda: window.worker is None)
    assert not window.request_status_timer.isActive() and window.request_countdown.text() == ""
    fake_gateway = SimpleNamespace(control=SimpleNamespace(stage="等待服务返回结果", response_status=200),
                                   cancel=lambda: None, close=lambda: None)
    window.start_work("等待结果", lambda cancel: gate.wait(2), lambda result: None, fake_gateway, timeout_seconds=30)
    assert "等待服务返回结果" in window.busy.text()
    window.request_timed_out()
    assert "HTTP 200" in window.notice.text() and "未在时限内完成" in window.notice.text()
    assert not window.request_countdown.isVisible()
    gate.set()
    window.exiting = True
    window.close()


def test_background_result_returns_to_ui_and_cancel_discards_late_result(qtbot, tmp_path):
    import threading
    store = Store(tmp_path)
    window = MainWindow(store, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    results = []
    window.start_work("后台检查", lambda cancel: "完成", lambda result: results.append((result, QThread.currentThread())))
    qtbot.waitUntil(lambda: window.worker is None)
    assert results[0][0] == "完成"
    assert results[0][1] == QApplication.instance().thread()
    gate = threading.Event()
    window.start_work("取消检查", lambda cancel: (gate.wait(1), "迟到结果")[1], results.append)
    window.cancel_request()
    gate.set()
    qtbot.waitUntil(lambda: window.worker is None)
    assert len(results) == 1
    window.exiting = True
    window.close()


def test_cancel_immediately_allows_new_request_and_old_finish_cannot_clear_it(qtbot, tmp_path):
    import threading
    from time import monotonic
    old_gate, new_gate = threading.Event(), threading.Event()
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    results = []
    window.start_work("旧请求", lambda cancel: (old_gate.wait(2), "旧结果")[1], results.append)
    old_worker = window.worker
    started = monotonic()
    window.cancel_request()
    assert window.worker is None and monotonic() - started < .1
    window.start_work("新请求", lambda cancel: (new_gate.wait(2), "新结果")[1], results.append)
    new_worker = window.worker
    old_gate.set()
    qtbot.wait(80)
    assert window.worker is new_worker and not results
    # 模拟取消前已排入主线程队列的迟到信号。
    old_worker.signals.done.emit("迟到结果")
    old_worker.signals.finished.emit()
    assert window.worker is new_worker and not results
    new_gate.set()
    qtbot.waitUntil(lambda: window.worker is None)
    assert results == ["新结果"]
    window.exiting = True
    window.close()


def test_ui_deadline_and_failure_restore_controls(qtbot, tmp_path, monkeypatch):
    import threading
    from PySide6.QtWidgets import QMessageBox
    gate = threading.Event()
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    results = []
    window.start_work("连接测试", lambda cancel: (gate.wait(1), "旧结果")[1], results.append, timeout_seconds=.05)
    qtbot.waitUntil(lambda: window.worker is None)
    assert "停止等待" in window.notice.text() and not window.cancel_button.isVisible()
    gate.set()
    def fail(cancel):
        raise ValueError("测试失败")
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *_args: messages.append(window.worker))
    window.start_work("失败测试", fail, results.append)
    qtbot.waitUntil(lambda: bool(messages))
    assert messages == [None] and window.worker is None
    assert "测试失败" in window.notice.text() and not results
    window.exiting = True
    window.close()


def test_countdown_decreases_by_monotonic_time_and_cancel_resets_it(qtbot, tmp_path, monkeypatch):
    import threading
    clock = [100.0]
    monkeypatch.setattr("desktop.window.monotonic", lambda: clock[0])
    gate = threading.Event()
    window = MainWindow(Store(tmp_path), credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.show()
    window.start_work("测试连接", lambda cancel: gate.wait(2), lambda result: None, timeout_seconds=30)
    assert window.request_countdown.text() == "剩余 30s"
    assert window.request_countdown.isVisible()
    clock[0] += 7.2
    window.update_request_status()
    assert window.request_countdown.text() == "剩余 23s"
    window.cancel_request()
    assert not window.request_countdown.isVisible() and not window.request_status_timer.isActive()
    window.start_work("再次测试", lambda cancel: gate.wait(2), lambda result: None, timeout_seconds=30)
    assert window.request_countdown.text() == "剩余 30s"
    clock[0] += 30
    window.update_request_status()
    assert window.worker is None and "停止等待" in window.notice.text()
    assert not window.request_countdown.isVisible()
    gate.set()
    window.exiting = True
    window.close()
