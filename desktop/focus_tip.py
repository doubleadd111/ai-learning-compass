"""Quiet, rounded focus-time popover. Never activates or intercepts clicks."""
from PySide6.QtCore import QEvent, QObject, QRect, QRectF, QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout
from shiboken6 import isValid

from desktop.controls import dropdown_colors


def short_duration(seconds):
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return (f"{hours} 小时 " if hours else "") + (f"{minutes} 分 " if minutes or hours else "") + f"{seconds} 秒"


class FocusTip(QFrame):
    def __init__(self):
        super().__init__(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setObjectName("focusTimePopover")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(5)
        self.caption = QLabel("累计专注")
        self.value = QLabel("")
        self.hint = QLabel("仅计专注 · 不含手动补记")
        for label in (self.caption, self.value, self.hint):
            box.addWidget(label)
        self.anchor = QRect()
        self.expiry = QTimer(self)
        self.expiry.setSingleShot(True)
        self.expiry.timeout.connect(self.hide)

    def paintEvent(self, event):
        colors = dropdown_colors()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(colors["line"]), 1))
        painter.setBrush(QColor(colors["card"]))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 12, 12)

    def present(self, seconds, position, anchor):
        colors = dropdown_colors()
        for label in (self.caption, self.hint):
            label.setStyleSheet(f"background:transparent;border:0;padding:0;color:{colors['muted']};font-size:11px;")
        self.value.setStyleSheet(f"background:transparent;border:0;padding:0;color:{colors['ink']};font-size:20px;font-weight:600;")
        self.value.setText(short_duration(seconds))
        self.adjustSize()
        self.anchor = anchor
        screen = QApplication.screenAt(position) or QApplication.primaryScreen()
        bounds = screen.availableGeometry()
        x = min(max(bounds.left() + 8, position.x() + 12), bounds.right() - self.width() - 8)
        y = position.y() + 18
        if y + self.height() > bounds.bottom() - 8:
            y = position.y() - self.height() - 10
        self.move(x, max(bounds.top() + 8, y))
        self.show()
        self.expiry.start(7000)


class FocusTipController(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.popup = None
        app.aboutToQuit.connect(self.shutdown)

    def shutdown(self):
        QApplication.instance().removeEventFilter(self)
        if self.popup is not None and isValid(self.popup):
            self.popup.close()
        self.popup = None

    def show(self, seconds, position, anchor):
        if self.popup is None or not isValid(self.popup):
            self.popup = FocusTip()
        self.popup.present(seconds, position, anchor)

    def eventFilter(self, watched, event):
        if QApplication.closingDown():
            return False
        if self.popup is not None and not isValid(self.popup):
            self.popup = None
        if event.type() == QEvent.Type.ToolTip and watched.property("focusSeconds"):
            self.show(watched.property("focusSeconds"), event.globalPos(), QRect(watched.mapToGlobal(QPoint()), watched.size()))
            return True
        if self.popup and self.popup.isVisible():
            if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.Wheel, QEvent.Type.ApplicationDeactivate):
                self.popup.hide()
            elif event.type() == QEvent.Type.MouseMove and not self.popup.anchor.contains(event.globalPosition().toPoint()):
                self.popup.hide()
        return False


def focus_tips():
    app = QApplication.instance()
    if not getattr(app, "_focus_tips", None):
        app._focus_tips = FocusTipController(app)
        app.installEventFilter(app._focus_tips)
    return app._focus_tips


def set_focus_hint(label, task):
    from desktop.task_style import focus_tooltip
    label.setToolTip(focus_tooltip(task))
    label.setProperty("focusSeconds", task.get("focus_seconds", 0))
    focus_tips()
