import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QPoint, QPointF, QTime, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (QApplication, QScrollArea, QStyle, QStyleOptionSpinBox,
                              QVBoxLayout, QWidget)
from desktop.controls import ArrowWheelSpinBox, ArrowWheelTimeEdit
from desktop.dialogs import spin
from desktop.planning_widgets import OutlineCard, PlanningSteps, assumptions_card
from desktop.theme import apply_theme
from tests.test_desktop_planning import outline


def wheel(widget, position, delta=120):
    event = QWheelEvent(QPointF(position), QPointF(widget.mapToGlobal(position)), QPoint(),
                        QPoint(0, delta), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                        Qt.ScrollPhase.NoScrollPhase, False)
    QApplication.sendEvent(widget, event)
    return event


def arrow_rect(field, subcontrol):
    option = QStyleOptionSpinBox()
    field.initStyleOption(option)
    return field.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option, subcontrol, field)


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('kind', ['number', 'time'])
def test_only_real_arrow_area_allows_wheel(qtbot, theme, kind):
    apply_theme(QApplication.instance(), theme)
    field = spin(1, 100, 40) if kind == 'number' else ArrowWheelTimeEdit(QTime(20, 30))
    qtbot.addWidget(field)
    field.resize(360, 48)
    field.show()
    QApplication.processEvents()
    value = field.value if kind == 'number' else field.time
    initial = value()
    field.setFocus()
    assert not wheel(field, QPoint(25, 24)).isAccepted()
    assert value() == initial
    # Clicking/focusing the text does not expand the wheel hit area.
    wheel(field.lineEdit(), field.lineEdit().rect().center(), -120)
    assert value() == initial
    up = arrow_rect(field, QStyle.SubControl.SC_SpinBoxUp)
    down = arrow_rect(field, QStyle.SubControl.SC_SpinBoxDown)
    assert up.width() > 0 and down.width() > 0
    wheel(field, QPoint(up.left() - 1, up.center().y()))
    assert value() == initial
    wheel(field, up.center())
    assert value() != initial
    wheel(field, down.center(), -120)
    assert value() == initial
    field.clearFocus()
    wheel(field, up.center())
    assert value() != initial  # Hovering an arrow is sufficient; no text focus needed.


def test_number_keyboard_click_limits_and_text_remain_available(qtbot):
    field = spin(1, 8, 4)
    assert isinstance(field, ArrowWheelSpinBox)
    qtbot.addWidget(field)
    field.resize(320, 48)
    field.show()
    qtbot.keyClick(field, Qt.Key.Key_Up)
    assert field.value() == 5
    qtbot.mouseClick(field, Qt.MouseButton.LeftButton,
                     pos=arrow_rect(field, QStyle.SubControl.SC_SpinBoxDown).center())
    assert field.value() == 4
    field.lineEdit().selectAll()
    qtbot.keyClicks(field.lineEdit(), '7')
    field.interpretText()
    assert field.value() == 7
    field.setValue(8)
    wheel(field, arrow_rect(field, QStyle.SubControl.SC_SpinBoxUp).center())
    assert field.value() == 8


@pytest.mark.parametrize('theme', ['light', 'dark'])
@pytest.mark.parametrize('width', [620, 980])
def test_outline_card_wraps_and_groups_actions_without_network(qtbot, theme, width):
    apply_theme(QApplication.instance(), theme)
    changed = []
    point = outline().points[0]
    card = OutlineCard(1, point, changed.append)
    qtbot.addWidget(card)
    card.resize(width, card.sizeHint().height())
    card.show()
    QApplication.processEvents()
    assert card.summary.wordWrap() and card.summary.width() > width - 180
    assert card.edit.parentWidget() is card and not card.details.isVisible()
    qtbot.keyClick(card.toggle, Qt.Key.Key_Space)
    assert card.details.isVisible() and card.property('expanded')
    assert card.details.width() <= card.width()
    qtbot.mouseClick(card.edit, Qt.MouseButton.LeftButton)
    assert changed == [point.id]
    qtbot.keyClick(card.toggle, Qt.Key.Key_Space)
    assert not card.details.isVisible()


def test_steps_show_current_stage_and_assumptions_are_plain_text(qtbot):
    steps = PlanningSteps()
    qtbot.addWidget(steps)
    steps.set_stage(2)
    assert [p.property('stageState') for p, *_ in steps.items] == ['done', 'done', 'current', 'future', 'future']
    assert steps.items[2][1].text() == '3'
    card = assumptions_card(['仅工作日学习', '<script>文字不是HTML</script>'])
    qtbot.addWidget(card)
    from PySide6.QtWidgets import QLabel
    assert all(p.textFormat() == Qt.TextFormat.PlainText for p in card.findChildren(QLabel))


def test_ignored_wheel_keeps_parent_form_scrollable(qtbot):
    host = QScrollArea()
    content = QWidget()
    layout = QVBoxLayout(content)
    field = spin(1, 100, 45)
    layout.addWidget(field)
    spacer = QWidget()
    spacer.setFixedHeight(1000)
    layout.addWidget(spacer)
    host.setWidget(content)
    host.setWidgetResizable(True)
    host.resize(400, 250)
    qtbot.addWidget(host)
    host.show()
    QApplication.processEvents()
    position = host.mapFromGlobal(field.mapToGlobal(QPoint(20, field.height() // 2)))
    QTest.wheelEvent(host.windowHandle(), position, QPoint(0, -120))
    assert field.value() == 45
    assert host.verticalScrollBar().value() > 0


@pytest.mark.parametrize('theme', ['light', 'dark'])
def test_card_actions_have_visible_hover_feedback(qtbot, theme):
    app = QApplication.instance()
    apply_theme(app, theme)
    card = OutlineCard(1, outline().points[0], lambda *_: None)
    qtbot.addWidget(card)
    card.resize(660, 150)
    card.show()
    for button in (card.edit, card.toggle):
        qtbot.mouseMove(card, QPoint(2, 2))
        app.processEvents()
        before = button.grab().toImage().pixelColor(5, button.height() // 2)
        qtbot.mouseMove(button, button.rect().center())
        app.processEvents()
        after = button.grab().toImage().pixelColor(5, button.height() // 2)
        assert before != after
