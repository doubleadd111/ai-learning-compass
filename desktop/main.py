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
from desktop.store import Store
from desktop.theme import apply_theme, compass_icon
from desktop.window import MainWindow


def main():
    parser = argparse.ArgumentParser(description="学习罗盘桌面版")
    parser.add_argument("--data-dir", type=Path, help="使用单独的数据目录")
    parser.add_argument("--minimized", action="store_true")
    parser.add_argument("--smoke-test", action="store_true", help="在指定临时目录中执行无 API 启动检查")
    parser.add_argument("--screenshot", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.smoke_test and args.data_dir is None:
        parser.error("--smoke-test 必须指定独立的 --data-dir")
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
    try:
        store = Store(args.data_dir)
    except (ValueError, OSError) as error:
        QMessageBox.critical(None, "无法打开学习罗盘", str(error))
        return 1
    lock = QLockFile(str(store.directory / "instance.lock"))
    server_name = "LearningCompass-" + hashlib.sha256(str(store.directory.resolve()).encode()).hexdigest()[:20]
    if not args.smoke_test and not lock.tryLock(200):
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
    window = MainWindow(store, enable_tray=not args.smoke_test)
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
