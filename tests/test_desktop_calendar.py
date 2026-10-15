import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate, QPoint, Qt
from PySide6.QtTest import QSignalSpy
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget
from desktop.calendar import StudyCalendar
from desktop.dialogs import date_edit
from desktop.theme import apply_theme


@pytest.fixture
def field(qtbot):
    host = QWidget()
    host.resize(400, 140)
    layout = QVBoxLayout(host)
    editor = date_edit(QDate(2026, 11, 15))
    layout.addWidget(editor)
    qtbot.addWidget(host)
    host.show()
    QApplication.processEvents()
    yield editor
    editor.calendarWidget().window().hide()
    host.close()


def open_calendar(field, qtbot):
    qtbot.mouseClick(field, Qt.MouseButton.LeftButton, pos=QPoint(field.width()-12, field.height()//2))
    QApplication.processEvents()
    calendar = field.calendarWidget()
    assert isinstance(calendar, StudyCalendar) and calendar.window().isVisible()
    return calendar


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_calendar_readable_header_selection_hover_and_round_popup(field, qtbot, theme):
    app = QApplication.instance()
    apply_theme(app, theme)
    c = open_calendar(field, qtbot)
    assert c.firstDayOfWeek() == Qt.DayOfWeek.Monday
    assert c.year.text() == "2026" and c.month.currentText() == "11月"
    assert c.year.contentsRect().width() >= 60 and c.previous.height() <= 34
    assert c.width() <= 330 and len(c._cells) == 42
    selected = c._cells[QDate(2026, 11, 15).toJulianDay()]
    target = c._cells[QDate(2026, 11, 16).toJulianDay()]
    qtbot.mouseMove(c._viewport, selected.center())
    app.processEvents()
    before = c._viewport.grab().toImage().pixelColor(target.left()+5, target.center().y())
    qtbot.mouseMove(c._viewport, target.center())
    app.processEvents()
    image = c._viewport.grab().toImage()
    assert image.pixelColor(target.left()+5, target.center().y()) != before
    assert image.pixelColor(selected.left()+5, selected.center().y()).name() == "#356cf6"
    assert c.window().grab().toImage().pixelColor(0, 0).alpha() == 0
    assert field.date() == QDate(2026, 11, 15)


def test_calendar_navigation_crosses_year_and_accepts_exact_day(field, qtbot):
    apply_theme(QApplication.instance(), "light")
    c = open_calendar(field, qtbot)
    qtbot.mouseClick(c.next, Qt.MouseButton.LeftButton)
    qtbot.mouseClick(c.next, Qt.MouseButton.LeftButton)
    assert (c.yearShown(), c.monthShown()) == (2027, 1)
    assert field.date() == QDate(2026, 11, 15)
    c.month.setCurrentIndex(2)
    c.year.setText("2028")
    c.year.editingFinished.emit()
    QApplication.processEvents()
    assert (c.yearShown(), c.monthShown()) == (2028, 3)
    target = QDate(2028, 3, 14)
    qtbot.mouseClick(c._viewport, Qt.MouseButton.LeftButton, pos=c._cells[target.toJulianDay()].center())
    assert field.date() == target and not c.window().isVisible()


def test_keyboard_escape_and_enter_keep_native_date_semantics(field, qtbot):
    apply_theme(QApplication.instance(), "light")
    c = open_calendar(field, qtbot)
    qtbot.keyClick(c._view, Qt.Key.Key_Right)
    qtbot.keyClick(c._view, Qt.Key.Key_Escape)
    assert field.date() == QDate(2026, 11, 15) and not c.window().isVisible()
    c = open_calendar(field, qtbot)
    qtbot.keyClick(c._view, Qt.Key.Key_Right)
    qtbot.keyClick(c._view, Qt.Key.Key_Return)
    assert field.date() == QDate(2026, 11, 16) and not c.window().isVisible()


def test_today_range_limits_and_invalid_year_are_safe(field, qtbot):
    apply_theme(QApplication.instance(), "light")
    field.setDateRange(QDate(2026, 11, 10), QDate(2026, 11, 20))
    c = open_calendar(field, qtbot)
    assert not c.previous.isEnabled() and not c.next.isEnabled()
    c.year.setText("1")
    c.year.editingFinished.emit()
    assert c.year.text() == "2026"
    assert c.today.isEnabled() == (field.minimumDate() <= QDate.currentDate() <= field.maximumDate())
    c.window().hide()
    field.setDateRange(QDate(1752, 9, 14), QDate(9999, 12, 31))
    c = open_calendar(field, qtbot)
    qtbot.mouseClick(c.today, Qt.MouseButton.LeftButton)
    assert field.date() == QDate.currentDate() and not c.window().isVisible()
