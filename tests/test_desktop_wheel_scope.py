"""Cover closed input fields and popup browsing with real Qt wheel dispatch."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, Qt, QEvent
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest, QSignalSpy
from PySide6.QtWidgets import QApplication, QScrollArea, QStyle, QStyleOptionComboBox, QVBoxLayout, QWidget
from desktop.controls import SoftComboBox
from desktop.dialogs import date_edit
from desktop.theme import apply_theme


def arrow(field):
    option = QStyleOptionComboBox()
    option.initFrom(field)
    option.rect = field.rect()
    option.editable = True
    return field.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                       QStyle.SubControl.SC_ComboBoxArrow, field)


def wheel_at(field, position, delta=-120):
    window = field.window()
    position = window.mapFromGlobal(field.mapToGlobal(position))
    # QTest's QWindow wheel path takes native pixels; QWidget hit regions are
    # logical coordinates. Without this conversion Windows 200% halves them.
    native_position = QPointF(position) * window.devicePixelRatioF()
    QTest.wheelEvent(window.windowHandle(), native_position, QPoint(0, delta))
    QTest.qWait(30)
    QApplication.processEvents()


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('kind', ['combo', 'editable', 'date'])
def test_closed_fields_only_step_over_actual_right_arrow(qtbot, theme, kind):
    apply_theme(QApplication.instance(), theme)
    field = date_edit(QDate(2026, 10, 15)) if kind == 'date' else SoftComboBox()
    if kind != 'date':
        field.addItems(['零基础', '已有基础', '进阶'])
        field.setCurrentIndex(1)
        field.setEditable(kind == 'editable')
    host = QWidget()
    host.resize(1000, 200)
    layout = QVBoxLayout(host)
    layout.addWidget(field)
    layout.addStretch()
    qtbot.addWidget(host)
    host.show()
    QApplication.processEvents()
    value = field.date if kind == 'date' else field.currentText
    initial = value()
    field.setFocus()
    area = arrow(field)
    assert area.width() < field.width() // 5 and area.right() >= field.width() - 3
    for position in (QPoint(20, field.height() // 2), field.rect().center(),
                     QPoint(area.left() - 1, area.center().y())):
        wheel_at(field, position)
        assert value() == initial
    wheel_at(field, area.center())
    assert value() != initial
    wheel_at(field, area.center(), 120)
    assert value() == initial
    field.clearFocus()
    wheel_at(field, area.center())
    assert value() != initial


@pytest.mark.parametrize('kind', ['combo', 'editable', 'date'])
def test_text_region_wheel_scrolls_form_without_changing_value(qtbot, kind):
    apply_theme(QApplication.instance(), 'light')
    host = QScrollArea()
    content = QWidget()
    layout = QVBoxLayout(content)
    field = date_edit(QDate(2026, 10, 15)) if kind == 'date' else SoftComboBox()
    if kind != 'date':
        field.addItems(['第一项', '第二项', '第三项'])
        field.setCurrentIndex(1)
        field.setEditable(kind == 'editable')
    layout.addWidget(field)
    spacer = QWidget()
    spacer.setFixedHeight(1200)
    layout.addWidget(spacer)
    host.setWidget(content)
    host.setWidgetResizable(True)
    host.resize(430, 260)
    qtbot.addWidget(host)
    host.show()
    QApplication.processEvents()
    initial = field.date() if kind == 'date' else field.currentText()
    wheel_at(field, QPoint(30, field.height() // 2))
    assert (field.date() if kind == 'date' else field.currentText()) == initial
    assert host.verticalScrollBar().value() > 0


def test_popup_wheel_browses_without_committing_and_click_still_selects(qtbot):
    apply_theme(QApplication.instance(), 'dark')
    field = SoftComboBox()
    field.addItems([f'选项{i}' for i in range(30)])
    qtbot.addWidget(field)
    field.resize(360, 48)
    field.show()
    field.showPopup()
    QApplication.processEvents()
    view = field.view()
    before = field.currentIndex()
    spy = QSignalSpy(field.currentIndexChanged)
    wheel_at(view.viewport(), view.viewport().rect().center(), -480)
    assert view.verticalScrollBar().value() > 0
    assert field.currentIndex() == before and spy.count() == 0
    point = view.viewport().rect().center()
    QTest.qWait(300)  # Let the native popup's opening-click guard expire.
    index = view.indexAt(point)
    # Deliver logical QWidget coordinates directly: the native QTest mouse
    # helper moves the real cursor and can auto-scroll a grabbed popup at 200%.
    for kind, buttons in ((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                          (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
        event = QMouseEvent(kind, QPointF(point), QPointF(view.viewport().mapToGlobal(point)),
                            Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(view.viewport(), event)
    assert field.currentIndex() == index.row() and spy.count() == 1


def test_every_app_form_uses_guarded_controls(qtbot, tmp_path):
    from desktop.credentials import CredentialStore
    from desktop.input_check import check_input_controls
    from desktop.store import Store
    from desktop.window import MainWindow
    from tests.test_desktop_store import create
    store = Store(tmp_path)
    create(store)
    window = MainWindow(store, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    try:
        report = check_input_controls(window)
        assert report['success'] and report['sample_checks'] == 5
    finally:
        window.exiting = True
        window.close()


def test_calendar_month_text_wheel_does_not_change_page_or_selected_date(qtbot):
    field = date_edit(QDate(2026, 10, 15))
    qtbot.addWidget(field)
    field.resize(360, 48)
    field.show()
    qtbot.mouseClick(field, Qt.MouseButton.LeftButton, pos=arrow(field).center())
    QApplication.processEvents()
    calendar = field.calendarWidget()
    month = calendar.month
    before = (calendar.yearShown(), calendar.monthShown())
    wheel_at(month, QPoint(10, month.height() // 2))
    assert (calendar.yearShown(), calendar.monthShown()) == before
    assert field.date() == QDate(2026, 10, 15)
    wheel_at(month, arrow(month).center())
    assert calendar.monthShown() != before[1]
    field.calendarWidget().window().hide()


def test_closed_combo_keyboard_limits_disabled_options_and_empty_list(qtbot):
    field = SoftComboBox()
    field.addItems(['one', 'disabled', 'three'])
    field.model().item(1).setEnabled(False)
    qtbot.addWidget(field)
    field.resize(350, 48)
    field.show()
    QApplication.processEvents()
    qtbot.keyClick(field, Qt.Key.Key_Down)
    assert field.currentIndex() == 2
    wheel_at(field, QPoint(20, 20), 120)
    assert field.currentIndex() == 2
    wheel_at(field, arrow(field).center(), 120)
    assert field.currentIndex() == 0
    field.clear()
    wheel_at(field, arrow(field).center())
    assert field.currentIndex() == -1
    field.addItems(['one', 'two'])
    field.setEnabled(False)
    wheel_at(field, arrow(field).center())
    assert field.currentIndex() == 0


@pytest.mark.parametrize('accept', [True, False])
def test_fetched_model_dialog_uses_guard_and_only_applies_on_confirmation(qtbot, tmp_path, monkeypatch, accept):
    from PySide6.QtCore import QTimer
    from desktop.credentials import CredentialStore
    from desktop.dialogs import ModelSelectionDialog
    from desktop.models import ModelConnection
    from desktop.store import Store
    from desktop.window import MainWindow
    store = Store(tmp_path)
    config = ModelConnection(name='mock', base_url='https://example.invalid', model='original')
    store.save_connection(config)
    window = MainWindow(store, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, 'gateway', lambda *_: object())
    monkeypatch.setattr(window, 'start_work', lambda text, work, done, gateway, **kwargs: done(['mock-one', 'mock-two']))
    def interact():
        dialog = QApplication.activeModalWidget()
        assert isinstance(dialog, ModelSelectionDialog) and isinstance(dialog.model, SoftComboBox)
        dialog.model.setEditText('my-own-model')
        dialog.accept() if accept else dialog.reject()
    QTimer.singleShot(0, interact)
    try:
        window.fetch_models()
        saved = store.connections()[0]
        assert saved.model == ('my-own-model' if accept else 'original')
        assert saved.base_url == config.base_url and saved.api_type == config.api_type
    finally:
        window.exiting = True
        window.close()
