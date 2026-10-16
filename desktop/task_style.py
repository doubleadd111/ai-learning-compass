"""Accessible overdue badges shared by the task lists and weekly table."""
from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QBrush, QColor, QPalette, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate

from desktop.task_status import overdue_days

OVERDUE_ROLE = Qt.ItemDataRole.UserRole + 31


def mark_overdue(item, task, columns, today):
    days = overdue_days(task, today)
    for column in range(columns):
        item.setData(column, OVERDUE_ROLE, days)
    if days:
        item.setToolTip(0, item.toolTip(0) + f"\n逾期 {days} 天 · 原状态：{task['status']}")


class TaskItemDelegate(QStyledItemDelegate):
    def __init__(self, parent=None, status_column=3):
        super().__init__(parent)
        self.status_column = status_column

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        app = QApplication.instance()
        colours = app.property("overdueColors") or {}
        days = index.data(OVERDUE_ROLE) or 0
        if days and colours:
            option.backgroundBrush = QBrush(QColor(colours["background"]))
            if index.column() in (0, 1, self.status_column):
                option.palette.setColor(QPalette.ColorRole.Text, QColor(colours["ink"]))
                option.palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colours["ink"]))
            if index.column() == 1:
                option.font.setBold(True)
        elif index.column() == self.status_column:
            colour = (app.property("statusColors") or {}).get(index.data())
            if colour:
                option.palette.setColor(QPalette.ColorRole.Text, QColor(colour))
                option.palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colour))

    def paint(self, painter, option, index):
        days = index.data(OVERDUE_ROLE) or 0
        colours = QApplication.instance().property("overdueColors") or {}
        if index.column() != 0 or not days or not colours:
            return super().paint(painter, option, index)
        from PySide6.QtWidgets import QStyleOptionViewItem
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        title, opt.text = opt.text, ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        # Keep the badge visible even when long titles are elided in a narrow row.
        font = opt.font
        font.setPixelSize(11)
        font.setBold(True)
        from PySide6.QtGui import QFontMetrics
        metrics = QFontMetrics(font)
        badge = f"逾期 {days} 天"
        width = metrics.horizontalAdvance(badge) + 14
        pill = QRect(rect.left(), rect.center().y() - 10, width, 20)
        painter.save()
        painter.setClipRect(option.rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(colours["ink"]))
        painter.drawRoundedRect(pill, 5, 5)
        painter.setPen(QColor(colours["badge_text"]))
        painter.setFont(font)
        painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, badge)
        painter.setFont(opt.font)
        painter.setPen(QColor(colours["ink"]))
        title_rect = rect.adjusted(width + 8, 0, 0, 0)
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         opt.fontMetrics.elidedText(title, Qt.TextElideMode.ElideRight, max(0, title_rect.width())))
        painter.restore()
