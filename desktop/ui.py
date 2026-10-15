"""Code-drawn navigation icons and shared, keyboard-friendly button feedback."""
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QPushButton


def line_icon(name, color, accent="#356CF6"):
    icon = QIcon()
    for state, stroke in ((QIcon.State.Off, color), (QIcon.State.On, accent)):
        pixmap = QPixmap(40, 40)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(2, 2)
        painter.setPen(QPen(QColor(stroke), 1.5, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if name in ("today", "week"):
            painter.drawRoundedRect(QRectF(3, 4, 14, 13), 2, 2)
            for x in (6, 14):
                painter.drawLine(QPointF(x, 2.5), QPointF(x, 5.5))
            painter.drawLine(QPointF(3, 8), QPointF(17, 8))
            if name == "today":
                painter.setBrush(QColor(stroke))
                painter.drawEllipse(QPointF(7, 12), 1.2, 1.2)
            else:
                for x in (6, 10, 14):
                    painter.drawLine(QPointF(x, 11), QPointF(x, 14))
        elif name == "library":
            for x, height in ((3, 13), (8, 13), (13, 10)):
                painter.drawRoundedRect(QRectF(x, 17 - height, 3, height), .6, .6)
        elif name == "focus":
            painter.drawEllipse(QRectF(3, 3, 14, 14))
            painter.drawLine(QPointF(10, 6), QPointF(10, 10))
            painter.drawLine(QPointF(10, 10), QPointF(13, 12))
        elif name == "settings":
            for y, knob in ((5, 7), (10, 13), (15, 8)):
                painter.drawLine(QPointF(3, y), QPointF(17, y))
                painter.setBrush(QColor(stroke))
                painter.drawEllipse(QPointF(knob, y), 1.8, 1.8)
        elif name == "plus":
            painter.drawLine(QPointF(10, 4), QPointF(10, 16))
            painter.drawLine(QPointF(4, 10), QPointF(16, 10))
        elif name in ("left", "right"):
            x, end = (12, 7) if name == "left" else (8, 13)
            path = QPainterPath(QPointF(x, 5))
            path.lineTo(end, 10)
            path.lineTo(x, 15)
            painter.drawPath(path)
        painter.end()
        icon.addPixmap(pixmap, QIcon.Mode.Normal, state)
    return icon


def button_icon(widget, name):
    widget.setProperty("lineIcon", name)
    app = QApplication.instance()
    color = "#FFFFFF" if widget.objectName() == "primary" else (app.property("iconColor") or "#68758A")
    widget.setIcon(line_icon(name, color, app.property("accentColor") or "#356CF6"))
    widget.setIconSize(QSize(20, 20))


class ButtonFeedback(QObject):
    """Apply the same feedback to action, navigation and dialog buttons.

    A property also makes hover state deterministic for offscreen QA; native
    :pressed and :focus states retain standard Qt mouse/keyboard semantics.
    """
    def eventFilter(self, watched, event):
        if isinstance(watched, (QPushButton, QCheckBox, QComboBox)):
            kind = event.type()
            if kind in (QEvent.Type.Polish, QEvent.Type.EnabledChange):
                watched.setCursor(Qt.CursorShape.PointingHandCursor if watched.isEnabled()
                                  else Qt.CursorShape.ArrowCursor)
            if isinstance(watched, QPushButton) and kind in (
                    QEvent.Type.Enter, QEvent.Type.Leave, QEvent.Type.EnabledChange):
                hovered = kind == QEvent.Type.Enter and watched.isEnabled()
                if watched.property("hovered") != hovered:
                    watched.setProperty("hovered", hovered)
                    watched.style().unpolish(watched)
                    watched.style().polish(watched)
                    watched.update()
        return super().eventFilter(watched, event)
