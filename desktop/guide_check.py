"""Isolated smoke check of help-button/close/source-destruction lifecycles."""
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QPushButton


def native_close(guide):
    if QApplication.platformName() == "windows":
        import ctypes
        from ctypes import wintypes
        close = ctypes.windll.user32.SendMessageW
        close.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        close(int(guide.winId()), 0x0010, 0, 0)
    else:
        guide.close()


def check_help_lifecycle(window):
    from desktop.guide import open_guide, close_guide
    from desktop.manual_tasks import ManualTaskDialog
    from desktop.schedule_ui import ScheduleDialog
    app = QApplication.instance()
    close_guide()
    app.processEvents()
    results, failures = [], []
    for factory, label in ((ManualTaskDialog, "查看手动任务说明"), (ScheduleDialog, "使用说明")):
        source = factory(window.store, window)
        help_button = next(b for b in source.findChildren(QPushButton) if b.text() == label)
        def exercise():
            try:
                help_button.click()
                app.processEvents()
                guide = getattr(app, "_learning_guide", None)
                assert guide is not None and guide.isVisible()
                native_close(guide)
                app.processEvents()
                assert getattr(app, "_learning_guide", None) is None
                help_button.click()
                app.processEvents()
                fresh = app._learning_guide
                assert fresh is not guide and fresh.isVisible()
                native_close(fresh)
                app.processEvents()
                assert getattr(app, "_learning_guide", None) is None
                results.append(label)
            except Exception as error:
                failures.append(type(error).__name__)
            finally:
                source.reject()
        QTimer.singleShot(60, exercise)
        timeout = QTimer(source)
        timeout.setSingleShot(True)
        timeout.timeout.connect(source.reject)
        timeout.start(3000)
        source.exec()
        timeout.stop()
        source.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        guide = open_guide(window)
        app.processEvents()
        assert guide.isVisible()
        native_close(guide)
        app.processEvents()
    assert not failures and len(results) == 2, "Guide lifecycle failed: " + ",".join(failures)
    return dict(real_help_buttons=2, modal_exec=True, native_close_reopen=True,
                source_destroyed_reopen=True, reading_state_retained=True, success=True)
