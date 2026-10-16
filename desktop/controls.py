"""Shared dropdown surface; retain Qt models, signals and keyboard semantics."""
from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QListView,
                              QProxyStyle, QStyle, QStyledItemDelegate,
                              QSpinBox, QTimeEdit, QStyleOptionSpinBox)


class ArrowWheelOnly:
    """Allow wheel stepping only over the style's real increment controls."""
    def wheelEvent(self, event):
        option = QStyleOptionSpinBox()
        self.initStyleOption(option)
        hit = self.style().hitTestComplexControl(QStyle.ComplexControl.CC_SpinBox,
                                                option, event.position().toPoint(), self)
        if hit in (QStyle.SubControl.SC_SpinBoxUp, QStyle.SubControl.SC_SpinBoxDown):
            super().wheelEvent(event)
        else:
            event.ignore()


class ArrowWheelSpinBox(ArrowWheelOnly, QSpinBox):
    pass


class ArrowWheelTimeEdit(ArrowWheelOnly, QTimeEdit):
    pass


def dropdown_colors():
    app = QApplication.instance()
    if colors := app.property("dropdownColors"):
        return colors
    palette = app.palette()
    return {"card": palette.base().color().name(), "line": palette.mid().color().name(),
            "ink": palette.text().color().name(), "muted": palette.placeholderText().color().name(),
            "accent": palette.link().color().name(), "selected": palette.highlight().color().name(),
            "hover": palette.alternateBase().color().name()}


class PopupSurface(QObject):
    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            colors = dropdown_colors()
            painter = QPainter(watched)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor(colors["line"]), 1))
            painter.setBrush(QColor(colors["card"]))
            painter.drawRoundedRect(QRectF(watched.rect()).adjusted(.5, .5, -.5, -.5), 12, 12)
            painter.end()
            return True
        return super().eventFilter(watched, event)


class DropdownStyle(QProxyStyle):
    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_ComboBox_Popup:
            # Avoid Fusion's native menu delegate/container and focus rectangle.
            return 0
        return super().styleHint(hint, option, widget, returnData)


class DropdownDelegate(QStyledItemDelegate):
    def __init__(self, combo):
        super().__init__(combo)
        self.combo = combo

    def sizeHint(self, option, index):
        return QSize(option.fontMetrics.horizontalAdvance(str(index.data() or "")) + 64, 42)

    def paint(self, painter, option, index):
        colors = dropdown_colors()
        enabled = bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
        committed = index.row() == self.combo.currentIndex()
        highlighted = bool(option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver))
        rect = QRectF(option.rect).adjusted(4, 2, -4, -2)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if enabled and (committed or highlighted):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors["selected"] if committed else colors["hover"]))
            painter.drawRoundedRect(rect, 7, 7)
        font = self.combo.font()
        font.setWeight(font.Weight.DemiBold if committed else font.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(colors["muted"] if not enabled else colors["accent"] if committed else colors["ink"]))
        text_rect = rect.adjusted(12, 0, -32, 0)
        metrics = painter.fontMetrics()
        label = metrics.elidedText(str(index.data() or ""), Qt.TextElideMode.ElideRight, max(0, int(text_rect.width())))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)
        if committed:
            x, y = rect.right() - 17, rect.center().y()
            painter.setPen(QPen(QColor(colors["accent"] if enabled else colors["muted"]), 1.8,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawLine(QPointF(x - 4, y), QPointF(x - 1, y + 3))
            painter.drawLine(QPointF(x - 1, y + 3), QPointF(x + 5, y - 4))
        painter.restore()


class SoftComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        view = QListView()
        view.setObjectName("softComboList")
        view.setMouseTracking(True)
        view.setUniformItemSizes(True)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        view.setTextElideMode(Qt.TextElideMode.ElideRight)
        view.entered.connect(lambda index: view.viewport().setToolTip(
            str(index.data(Qt.ItemDataRole.ToolTipRole) or index.data() or "")))
        self.setView(view)
        self.setItemDelegate(DropdownDelegate(self))
        self.setMaxVisibleItems(8)
        popup = view.window()
        popup.setObjectName("softComboPopup")
        popup.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint |
                             Qt.WindowType.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        popup.setFrameShape(QFrame.Shape.NoFrame)
        self._popup_surface = PopupSurface(popup)
        popup.installEventFilter(self._popup_surface)
        if popup.layout():
            popup.layout().setContentsMargins(6, 6, 6, 6)

    def showPopup(self):
        # Closed field may have grown since creation (model names, form layout).
        self.view().setMinimumWidth(max(140, self.width() - 12))
        super().showPopup()
        if not self.count():
            return
        # Qt sizes its private container without the breathing room added above.
        # Account for it explicitly so a short list doesn't need a scrollbar.
        popup = self.view().window()
        screen = self.screen().availableGeometry()
        height = min(min(self.count(), self.maxVisibleItems()) * 42 + 12, screen.height() - 16)
        width = min(max(152, self.width()), screen.width() - 16)
        below = self.mapToGlobal(QPoint(0, self.height() + 4))
        above = self.mapToGlobal(QPoint(0, 0)).y() - height - 4
        y = below.y() if below.y() + height <= screen.bottom() - 8 else above
        x = min(max(screen.left() + 8, below.x()), screen.right() - width - 8)
        y = min(max(screen.top() + 8, y), screen.bottom() - height - 8)
        popup.resize(width, height)
        popup.move(x, y)
        current = self.model().index(self.currentIndex(), self.modelColumn(), self.rootModelIndex())
        self.view().setCurrentIndex(current)
        self.view().scrollTo(current)
