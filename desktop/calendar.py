"""Compact, date-only calendar; native date editing and selection are retained."""
from PySide6.QtCore import QDate, QEvent, QLocale, QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QIntValidator, QPainter, QTextCharFormat
from PySide6.QtWidgets import (QCalendarWidget, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                              QTableView, QToolButton, QWidget)

from desktop.controls import PopupSurface, SoftComboBox, dropdown_colors
from desktop.ui import button_icon


class StudyCalendar(QCalendarWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("studyCalendar")
        self.setLocale(QLocale(QLocale.Language.Chinese, QLocale.Country.China))
        self.setFirstDayOfWeek(Qt.DayOfWeek.Monday)
        self.setNavigationBarVisible(False)
        self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader)
        self.setHorizontalHeaderFormat(QCalendarWidget.HorizontalHeaderFormat.ShortDayNames)
        self.setGridVisible(False)
        self.setMinimumSize(316, 300)
        self._cells = {}
        self._hover = None
        nav = QWidget()
        nav.setObjectName("calendarNavigation")
        row = QHBoxLayout(nav)
        row.setContentsMargins(10, 8, 10, 6)
        row.setSpacing(6)
        self.previous = QToolButton()
        self.previous.setObjectName("calendarArrow")
        self.previous.setAccessibleName("上个月")
        button_icon(self.previous, "left")
        self.next = QToolButton()
        self.next.setObjectName("calendarArrow")
        self.next.setAccessibleName("下个月")
        button_icon(self.next, "right")
        self.year = QLineEdit()
        self.year.setObjectName("calendarYear")
        self.year.setAccessibleName("日历年份")
        self.year.setValidator(QIntValidator(1, 9999, self.year))
        self.year.setMaxLength(4)
        self.year.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.year.setFixedWidth(64)
        self.month = SoftComboBox()
        self.month.setObjectName("calendarMonth")
        self.month.setAccessibleName("日历月份")
        self.month.addItems([f"{i}月" for i in range(1, 13)])
        self.month.setFixedWidth(84)
        row.addWidget(self.previous)
        row.addStretch()
        row.addWidget(self.year)
        row.addWidget(QLabel("年"))
        row.addWidget(self.month)
        row.addStretch()
        row.addWidget(self.next)
        self.layout().insertWidget(0, nav)
        footer = QWidget()
        footer.setObjectName("calendarFooter")
        line = QHBoxLayout(footer)
        line.setContentsMargins(12, 4, 12, 10)
        self.caption = QLabel()
        self.caption.setObjectName("muted")
        self.today = QPushButton("今天")
        self.today.setObjectName("calendarToday")
        self.today.setAccessibleName("选择今天")
        line.addWidget(self.caption, 1)
        line.addWidget(self.today)
        self.layout().addWidget(footer)
        self.previous.clicked.connect(self.showPreviousMonth)
        self.next.clicked.connect(self.showNextMonth)
        self.year.editingFinished.connect(self.commit_year)
        self.month.currentIndexChanged.connect(lambda month: self.setCurrentPage(self.yearShown(), month + 1))
        self.currentPageChanged.connect(self.sync_page)
        self.selectionChanged.connect(self.sync_selected)
        self.today.clicked.connect(self.choose_today)
        self._view = self.findChild(QTableView)
        self._view.setMouseTracking(True)
        self._viewport = self._view.viewport()
        self._viewport.installEventFilter(self)
        self._view.installEventFilter(self)
        self.year.installEventFilter(self)
        self.sync_page(self.yearShown(), self.monthShown())
        self.sync_selected()

    def sizeHint(self):
        return QSize(316, 300)

    def sync_page(self, year, month):
        self.year.blockSignals(True)
        self.month.blockSignals(True)
        self.year.validator().setRange(self.minimumDate().year(), self.maximumDate().year())
        self.year.setText(str(year))
        self.month.setCurrentIndex(month - 1)
        self.year.blockSignals(False)
        self.month.blockSignals(False)
        self._cells.clear()
        self._hover = None
        self.previous.setEnabled(QDate(year, month, 1) > QDate(self.minimumDate().year(), self.minimumDate().month(), 1))
        self.next.setEnabled(QDate(year, month, 1) < QDate(self.maximumDate().year(), self.maximumDate().month(), 1))
        self.today.setEnabled(self.minimumDate() <= QDate.currentDate() <= self.maximumDate())

    def commit_year(self):
        if self.year.hasAcceptableInput():
            self.setCurrentPage(int(self.year.text()), self.monthShown())
        self.year.setText(str(self.yearShown()))

    def sync_selected(self):
        self.caption.setText(self.selectedDate().toString("yyyy-MM-dd"))
        self.updateCells()

    def choose_today(self):
        today = QDate.currentDate()
        if self.minimumDate() <= today <= self.maximumDate():
            self.setSelectedDate(today)
            self.setCurrentPage(today.year(), today.month())
            self.clicked.emit(today)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            if hasattr(self, "_opening_date"):
                self.setSelectedDate(self._opening_date)
            self.window().hide()
            return True
        if watched is getattr(self, "_viewport", None):
            hovered = self._hover
            if event.type() == QEvent.Type.MouseMove:
                point = event.position().toPoint()
                hovered = next((day for day, rect in self._cells.items() if rect.contains(point)), None)
            elif event.type() == QEvent.Type.Leave:
                hovered = None
            if hovered != self._hover:
                self._hover = hovered
                self.updateCells()
        return super().eventFilter(watched, event)

    def paintCell(self, painter, rect, date):
        colors = dropdown_colors()
        if not hasattr(self, "_cells"):
            return
        self._cells[date.toJulianDay()] = QRect(rect)
        selected = date == self.selectedDate()
        enabled = self.minimumDate() <= date <= self.maximumDate()
        in_month = date.month() == self.monthShown() and date.year() == self.yearShown()
        cell = QRectF(rect).adjusted(3, 2, -3, -2)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if enabled and (selected or self._hover == date.toJulianDay()):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#356CF6" if selected else colors["hover"]))
            painter.drawRoundedRect(cell, 7, 7)
        ink = QColor("#FFFFFF" if selected and enabled else colors["muted"] if date.dayOfWeek() >= 6 or not in_month else colors["ink"])
        if not enabled or not in_month:
            ink.setAlphaF(.45 if not enabled else .6)
        painter.setPen(ink)
        font = self.font()
        font.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Normal)
        painter.setFont(font)
        painter.drawText(cell, Qt.AlignmentFlag.AlignCenter, str(date.day()))
        if date == QDate.currentDate():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#FFFFFF" if selected else colors["accent"]))
            painter.drawEllipse(QRectF(cell.center().x() - 1.5, cell.bottom() - 5, 3, 3))
        painter.restore()

    def showEvent(self, event):
        self._opening_date = self.selectedDate()
        colors = dropdown_colors()
        for day in (Qt.DayOfWeek.Saturday, Qt.DayOfWeek.Sunday):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colors["muted"]))
            self.setWeekdayTextFormat(day, fmt)
        self.sync_page(self.yearShown(), self.monthShown())
        super().showEvent(event)


def configure_calendar(date_edit):
    calendar = StudyCalendar()
    date_edit.setCalendarWidget(calendar)
    popup = calendar.window()
    popup.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
    popup.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    calendar._popup_surface = PopupSurface(popup)
    popup.installEventFilter(calendar._popup_surface)
    popup.installEventFilter(calendar)
    return calendar
