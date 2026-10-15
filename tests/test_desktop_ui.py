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
