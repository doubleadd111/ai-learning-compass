from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from desktop import VERSION
from desktop.theme import apply_theme, compass_icon


def main():
    parser = argparse.ArgumentParser(description="学习罗盘桌面版")
    parser.add_argument("--data-dir", type=Path, help="使用单独的数据目录")
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help="在指定临时目录中执行无 API 启动检查")
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--network-self-test", help=argparse.SUPPRESS)
    parser.add_argument("--anonymous-network-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--diagnostic-route", choices=["system", "direct"], default="system", help=argparse.SUPPRESS)
    parser.add_argument("--guide-window-check", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--input-scope-check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.anonymous_network_check:
        if args.data_dir is None:
            parser.error("匿名网络检查必须指定隔离目录")
        from desktop.network_check import run_anonymous_check
        return run_anonymous_check(args.data_dir, args.diagnostic_route)
    if args.network_self_test:
        if args.data_dir is None:
            parser.error("网络自检必须指定隔离数据目录")
        from desktop.network_check import run_network_check
        return run_network_check(args.network_self_test, args.data_dir)
    if args.smoke_test and args.data_dir is None:
        parser.error("--smoke-test 必须指定独立的 --data-dir")
    if args.guide_window_check and not args.smoke_test:
        parser.error("指南窗口自检必须在隔离smoke-test中运行")
    if args.input_scope_check and not args.smoke_test:
        parser.error("输入范围自检必须在隔离smoke-test中运行")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("LearningCompass")
    app.setApplicationDisplayName("学习罗盘")
    app.setApplicationVersion(VERSION)
    app.setOrganizationName("LearningCompass")
    app.setWindowIcon(compass_icon())
    app.setStyle("Fusion")
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("LearningCompass.Desktop")
    # Paint feedback before importing the application and constructing its pages.
    # This cannot eliminate Windows/antivirus time before Python starts.
    from desktop.startup import StartupWindow
    startup = None
    if not (args.minimized or args.smoke_test):
        apply_theme(app,'system')
        startup=StartupWindow();startup.show();app.processEvents()
    from desktop.store import Store
    try:
        store = Store(args.data_dir)
    except (ValueError, OSError) as error:
        if startup:startup.close()
        QMessageBox.critical(None, "无法打开学习罗盘", str(error))
        return 1
    lock = QLockFile(str(store.directory / "instance.lock"))
    server_name = "LearningCompass-" + hashlib.sha256(str(store.directory.resolve()).encode()).hexdigest()[:20]
    if not args.smoke_test and not lock.tryLock(200):
        if startup:startup.close()
        socket = QLocalSocket()
        socket.connectToServer(server_name)
        if socket.waitForConnected(1000):
            socket.write(b"show")
            socket.waitForBytesWritten(1000)
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(server_name)
    server.listen(server_name)
    apply_theme(app, store.setting("theme", "system"))
    if startup:
        startup.message.setText('正在准备任务和学习记录…');app.processEvents()
    from desktop.window import MainWindow
    window = MainWindow(store, enable_tray=not args.smoke_test, defer_hidden=True)
    if startup:startup.close()
    def reveal():
        socket = server.nextPendingConnection()
        if socket:
            socket.close()
            socket.deleteLater()
        window.reveal()
    server.newConnection.connect(reveal)
    app.styleHints().colorSchemeChanged.connect(lambda _scheme: apply_theme(app, "system") if store.setting("theme", "system") == "system" else None)
    app.aboutToQuit.connect(window.focus.pause)
    if not args.minimized or not window.tray:
        window.show()
    if args.smoke_test:
        def smoke():
            try:
                if not store.list_plans():
                    window.load_sample()
                window.navigate(0)
                window.refresh()
                if args.screenshot:
                    args.screenshot.parent.mkdir(parents=True, exist_ok=True)
                    window.grab().save(str(args.screenshot))
                report = dict(success=True, version=VERSION, pages=window.stack.count(),
                              plans=len(store.list_plans()), tasks=len(store.tasks()), executable=sys.executable)
                if args.input_scope_check:
                    from desktop.input_check import check_input_controls
                    report['input_scope'] = check_input_controls(window)
                from desktop.deletion_check import check_plan_deletion
                report['plan_deletion'] = check_plan_deletion(window)
                from desktop.history_check import check_plan_history
                report['plan_history'] = check_plan_history(window)
                from desktop.learning_check import check_learning_tools
                report['learning_tools'] = check_learning_tools(store)
                from desktop.workspace_check import check_workspace_windows
                report['workspace_windows'] = check_workspace_windows(window)
                from desktop.discovery_check import check_task_discovery
                report['task_discovery'] = check_task_discovery(window)
                from desktop.focus_search_check import check_focus_search
                report['focus_search'] = check_focus_search(window)
                from desktop.search_scope_check import check_search_scope
                report['search_scope'] = check_search_scope(window)
                from desktop.task_context_check import check_task_context
                report['task_context'] = check_task_context(window)
                from desktop.workflow_check import check_workflow
                report['daily_workflow'] = check_workflow(window)
                from desktop.experience_check import check_experience
                report['experience'] = check_experience(window)
                from desktop.trash_check import check_trash
                report['trash'] = check_trash(window)
                from desktop.organizer_check import check_organizer
                report['organizer'] = check_organizer()
                if args.guide_window_check:
                    from desktop.guide import open_guide
                    guide = open_guide(window, "modify")
                    app.processEvents()
                    guide.showMinimized()
                    app.processEvents()
                    same = open_guide(window)
                    app.processEvents()
                    assert same is guide and not guide.isMinimized() and guide.parentWidget() is None
                    import ctypes
                    user32 = ctypes.windll.user32
                    from ctypes import wintypes
                    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
                    user32.GetWindow.restype = wintypes.HWND
                    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
                    user32.GetWindowLongW.restype = ctypes.c_long
                    hwnd = int(guide.winId())
                    assert hwnd != int(window.winId()) and not user32.GetWindow(hwnd, 4)
                    assert not (user32.GetWindowLongW(hwnd, -20) & 0x80)  # Not WS_EX_TOOLWINDOW.
                    report["native_guide_window"] = dict(ownerless=True, separate_hwnd=True, taskbar_eligible=True, restored=True)
                    from PySide6.QtWidgets import QDialog
                    from PySide6.QtCore import Qt
                    source_dialog = QDialog(window)
                    source_dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
                    source_dialog.show()
                    app.processEvents()
                    assert open_guide(source_dialog, "modify") is guide
                    app.processEvents()
                    assert guide.isActiveWindow() and guide.windowHandle().transientParent() is source_dialog.windowHandle()
                    assert user32.GetWindow(int(guide.winId()), 4) == int(source_dialog.winId())
                    source_dialog.reject()
                    app.processEvents()
                    assert guide.windowHandle().transientParent() is None and guide.isVisible()
                    report["modal_guide"] = dict(above_source=True, interactive=True, detached_after_close=True, singleton=True)
                    from desktop.guide_check import check_help_lifecycle
                    report["guide_lifecycle"] = check_help_lifecycle(window)
                from desktop.credentials import CredentialStore
                from desktop.models import new_id
                temporary_credential = "smoke-" + new_id()
                try:
                    remembered = window.credentials.set(temporary_credential, "temporary-smoke-placeholder", True)
                    report["credential_store"] = "available" if remembered and CredentialStore().get(temporary_credential) == "temporary-smoke-placeholder" else "session-only"
                finally:
                    window.credentials.delete(temporary_credential)
                (store.directory / "smoke-result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            finally:
                window.quit_app()
        QTimer.singleShot(800, smoke)
    result = app.exec()
    server.close()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
