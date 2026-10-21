"""Accessible overdue badges shared by the task lists and weekly table."""
from PySide6.QtCore import QRect, Qt, QSize
from PySide6.QtGui import QBrush, QColor, QPalette, QPainter, QFont
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate

from desktop.task_status import overdue_days

OVERDUE_ROLE = Qt.ItemDataRole.UserRole + 31
FOCUS_ROLE = Qt.ItemDataRole.UserRole + 32
SEARCH_MATCH_ROLE = Qt.ItemDataRole.UserRole + 33


def focus_tooltip(task):
    seconds = max(0, int(task.get("focus_seconds", 0)))
    return f"已专注 {seconds // 3600} 小时 {seconds // 60 % 60} 分 {seconds % 60} 秒\n仅统计专注计时，不含手动补记。" if seconds else "尚未记录专注时长。"


def mark_overdue(item, task, columns, today):
    days = overdue_days(task, today)
    for column in range(columns):
        item.setData(column, OVERDUE_ROLE, days)
    status_column = 5 if columns == 7 else 3
    item.setToolTip(status_column, focus_tooltip(task))
    item.setData(status_column, FOCUS_ROLE, task.get("focus_seconds", 0))
    if days:
        item.setToolTip(0, item.toolTip(0) + f"\n逾期 {days} 天 · 原状态：{task['status']}")


class TaskItemDelegate(QStyledItemDelegate):
    def __init__(self, parent=None, status_column=3):
        super().__init__(parent)
        self.status_column = status_column

    def helpEvent(self, event, view, option, index):
        seconds = index.data(FOCUS_ROLE) or 0
        if index.column() == self.status_column and seconds:
            from desktop.focus_tip import focus_tips
            anchor = QRect(view.viewport().mapToGlobal(option.rect.topLeft()), option.rect.size())
            focus_tips().show(seconds, event.globalPos(), anchor, view.viewport())
            return True
        from desktop.task_tip import TASK_TIP_ROLE
        if index.column() == 0 and (payload := index.data(TASK_TIP_ROLE)):
            from desktop.focus_tip import focus_tips
            anchor = QRect(view.viewport().mapToGlobal(option.rect.topLeft()), option.rect.size())
            focus_tips().show_task(payload, event.globalPos(), anchor, view.viewport())
            return True
        return super().helpEvent(event, view, option, index)

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
        summary = index.data(SEARCH_MATCH_ROLE) if index.column() == 0 else None
        if not summary:
            return self.paint_content(painter, option, index)
        from PySide6.QtWidgets import QStyleOptionViewItem
        from PySide6.QtGui import QFont, QFontMetrics
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ''
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        font = QFont(opt.font)
        font.setPixelSize(12)
        font.setBold(False)
        metrics = QFontMetrics(font)
        extra = metrics.height() + 6
        top = QStyleOptionViewItem(option)
        top.rect.setHeight(max(24, option.rect.height()-extra))
        self.paint_content(painter, top, index)
        rect = style.subElementRect(QStyle.SubElement.SE_ItemViewItemText, opt, opt.widget)
        rect.setTop(top.rect.bottom()+2)
        rect.setBottom(option.rect.bottom()-4)
        painter.save()
        painter.setClipRect(option.rect)
        painter.setFont(font)
        colours = QApplication.instance().property('dropdownColors') or {}
        painter.setPen(QColor(colours.get('muted', '#68758A')))
        painter.drawText(rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            metrics.elidedText(summary, Qt.TextElideMode.ElideRight, max(0, rect.width())))
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if index.column() == 0 and index.data(SEARCH_MATCH_ROLE):
            from PySide6.QtGui import QFont, QFontMetrics
            font = QFont(option.font)
            font.setPixelSize(12)
            return QSize(size.width(), size.height()+QFontMetrics(font).height()+6)
        return size

    def paint_content(self, painter, option, index):
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
        font = QFont(opt.font)
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
