"""Compact, width-aware plan cards; full content stays in the model and tooltip."""
from html import escape

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem

TITLE_ROLE = Qt.ItemDataRole.UserRole + 41
SUBJECT_ROLE = Qt.ItemDataRole.UserRole + 42
PROGRESS_ROLE = Qt.ItemDataRole.UserRole + 43


def populate_plan_item(item, plan, subject):
    title = plan["title"]
    progress = f"{plan['completed']}/{plan['total']} 项完成"
    # Keep full plain text for accessibility, selection, and legacy callers.
    item.setText(f"{title}\n{subject} · {progress}")
    item.setData(Qt.ItemDataRole.UserRole, plan["id"])
    for role, text in ((TITLE_ROLE, title), (SUBJECT_ROLE, subject), (PROGRESS_ROLE, progress)):
        item.setData(role, text)
    item.setData(Qt.ItemDataRole.AccessibleTextRole, item.text())
    item.setToolTip("<qt>" + "<br>".join(escape(s) for s in (title, subject, progress)) + "</qt>")


class PlanListDelegate(QStyledItemDelegate):
    """Three quiet lines: name, subject, then always-visible completion count."""

    @staticmethod
    def lines(index, font, width):
        title_font, small_font = QFont(font), QFont(font)
        title_font.setBold(True)
        small_font.setPixelSize(max(11, font.pixelSize() - 1))
        return [(QFontMetrics(f).elidedText(str(index.data(role) or "").replace("\n", " "),
                                           Qt.TextElideMode.ElideRight, max(0, width)), f)
                for role, f in ((TITLE_ROLE, title_font), (SUBJECT_ROLE, small_font),
                                (PROGRESS_ROLE, small_font))]

    def sizeHint(self, option, index):
        # A long name must never request a wider QListWidget viewport.
        heights = sum(QFontMetrics(f).height() for _, f in self.lines(index, option.font, 1))
        return QSize(0, heights + 40)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        colors = QApplication.instance().property("dropdownColors") or {}
        rect = option.rect.adjusted(17, 14, -17, -12)
        painter.save()
        painter.setClipRect(rect)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        top = rect.top()
        for number, (text, font) in enumerate(self.lines(index, opt.font, rect.width())):
            painter.setFont(font)
            painter.setPen(QColor(colors.get(("ink", "muted", "accent")[number], "#202938")))
            height = QFontMetrics(font).height()
            painter.drawText(QRect(rect.left(), top, rect.width(), height),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
            top += height + 6
        painter.restore()
