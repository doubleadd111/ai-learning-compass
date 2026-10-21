"""Ownerless workspace windows; confirm dialogs remain short, scoped operations."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog
from shiboken6 import isValid


class ToolWindows:
    def __init__(self, host):
        self.host = host
        self.windows = {}

    def existing(self, key):
        window = self.windows.get(key)
        if window is not None and isValid(window):
            if window.isMinimized():
                window.showNormal()
            window.show()
            window.raise_()
            window.activateWindow()
            return window
        return None

    def show(self, dialog, key, finished=None):
        if old := self.existing(key):
            dialog.deleteLater()
            return old
        # Dialog parentage in Qt creates a native Windows owner, which imposes
        # z-order even when the window is not modal. Keep only Python references.
        dialog.setParent(None, Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
        dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.setWindowIcon(self.host.windowIcon())
        self.windows[key] = dialog
        def done(result):
            if self.windows.get(key) is dialog:
                self.windows.pop(key, None)
            if finished and not self.host.exiting:
                self.host.safe(lambda: finished(result))
        if isinstance(dialog, QDialog):
            dialog.finished.connect(done)
        dialog.destroyed.connect(lambda *_: self.windows.pop(key, None) if self.windows.get(key) is dialog else None)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
        return dialog

    def open(self, key, factory, finished=None):
        return self.existing(key) or self.show(factory(), key, finished)

    def close_all(self):
        for window in list(self.windows.values()):
            if isValid(window):
                window.close()
                if window.isVisible():
                    window.showNormal()
                    window.raise_()
                    window.activateWindow()
                    return False
        return True


def open_tool(source, dialog, key, finished=None):
    """Resolve the shared app controller without giving windows native owners."""
    app = QApplication.instance()
    hub = getattr(app, '_learning_tool_windows', None)
    if hub is not None and isValid(hub.host) and not hub.host.exiting and getattr(source, 'store', None) is hub.host.store:
        return hub.show(dialog, key, finished)
    # Standalone component/QA windows have no app host, but remain nonmodal.
    dialog.setParent(None, Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
    dialog.setWindowModality(Qt.WindowModality.NonModal)
    dialog.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
    dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
    if not hasattr(source, '_child_tools'):
        source._child_tools = []
    source._child_tools.append(dialog)
    dialog.finished.connect(lambda *_: source._child_tools.remove(dialog) if dialog in source._child_tools else None)
    if finished:
        dialog.finished.connect(finished)
    dialog.show()
    return dialog
