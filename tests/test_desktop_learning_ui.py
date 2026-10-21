from datetime import date
import json

import pytest
from PySide6.QtCore import Qt, QDate
from PySide6.QtWidgets import QDialog, QPushButton, QTreeWidgetItem

from desktop.store import Store
from desktop.theme import apply_theme
from desktop.learning_ui import StatisticsDialog, StepsDialog, StructureDialog, RecurringDialog, SeriesListDialog
from desktop.learning_tools import task_details, save_details, preview_series, apply_series
from desktop.dialogs import TaskDialog
from desktop.guide import guide_sections, close_guide
from desktop.controls import SoftComboBox, ArrowWheelDateEdit, ArrowWheelSpinBox
from desktop.scheduling import fingerprint
from tests.test_desktop_store import create
from tests.test_desktop_manual_tasks import payload


@pytest.fixture
def learning(qapp, tmp_path):
    apply_theme(qapp, 'light')
    s = Store(tmp_path)
    pid = create(s)
    yield s, pid
    close_guide()


def test_new_guide_section_available_offline():
    assert 'tools' in guide_sections()
    assert '不是当天实际发生的学习时长' in guide_sections()['tools'][1]


def test_steps_save_and_parent_not_auto_done(qtbot, learning):
    s, pid = learning
    tid = s.tasks(pid)[0]['id']
    dialog = StepsDialog(s, tid)
    qtbot.addWidget(dialog)
    dialog.stage.setCurrentText('基础阶段')
    dialog.new_title.setText('写一个测试')
    dialog.add_step()
    dialog.steps.topLevelItem(0).setCheckState(0, Qt.CheckState.Checked)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert task_details(s, tid)[0]['stage'] == '基础阶段'
    assert s.tasks(pid)[0]['status'] == '进行中'


def test_steps_cancel_does_not_write(qtbot, learning):
    s, pid = learning
    tid = s.tasks(pid)[0]['id']
    dialog = StepsDialog(s, tid)
    qtbot.addWidget(dialog)
    dialog.new_title.setText('不要写入')
    dialog.add_step()
    dialog.reject()
    assert task_details(s, tid)[0]['steps'] == []


def test_statistics_filters_summary_and_invalid_dates(qtbot, learning):
    s, pid = learning
    dialog = StatisticsDialog(s)
    qtbot.addWidget(dialog)
    dialog.start.setDate(QDate(2026, 10, 1))
    dialog.end.setDate(QDate(2026, 10, 31))
    dialog.plan.setCurrentIndex(dialog.plan.findData(pid))
    assert dialog.report['total'] == 4
    dialog.copy_summary()
    from PySide6.QtWidgets import QApplication
    assert QApplication.clipboard().text() == dialog.report['summary']
    dialog.start.setDate(QDate(2027, 1, 1))
    dialog.refresh()
    assert not dialog.error.isHidden()


def test_recurring_preview_explicit_save_and_input_invalidates(qtbot, learning):
    s, _ = learning
    dialog = RecurringDialog(s)
    qtbot.addWidget(dialog)
    dialog.title.setText('复习词汇')
    dialog.start.setDate(QDate(2027, 1, 1))
    dialog.end.setDate(QDate(2027, 1, 3))
    dialog.make_preview()
    assert dialog.proposal and dialog.preview.topLevelItemCount() == 3
    assert not s.tasks('__standalone__')
    dialog.title.setText('复习口语')
    assert dialog.proposal is None and not dialog.save_button.isEnabled()
    dialog.make_preview()
    dialog.save()
    assert len(s.tasks('__standalone__')) == 3 and dialog.proposal is None
    dialog.save()
    assert len(s.tasks('__standalone__')) == 3


def test_recurring_review_weekly_controls_and_invalid_input(qtbot, learning):
    s, _ = learning
    dialog = RecurringDialog(s)
    qtbot.addWidget(dialog)
    dialog.kind.setCurrentIndex(1)
    assert all(w.isEnabled() for w in dialog.weekdays) and not dialog.intervals.isEnabled()
    dialog.kind.setCurrentIndex(2)
    assert dialog.intervals.isEnabled() and not dialog.weekdays[0].isEnabled()
    dialog.intervals.setText('abc')
    dialog.make_preview()
    assert dialog.proposal is None and not dialog.error.isHidden()


def test_structure_single_and_batch_selection(qtbot, learning):
    s, pid = learning
    dialog = StructureDialog(s, pid)
    qtbot.addWidget(dialog)
    group = dialog.tasks.topLevelItem(0)
    group.child(0).setSelected(True)
    group.child(1).setSelected(True)
    dialog.stage.setText('基础')
    ids = dialog.selected()
    dialog.assign()
    assert all(task_details(s, tid)[0]['stage'] == '基础' for tid in ids)
    assert dialog.tasks.topLevelItemCount() == 2


@pytest.mark.parametrize('theme', ['light','dark'])
@pytest.mark.parametrize('cls', [StatisticsDialog, StepsDialog, StructureDialog, RecurringDialog, SeriesListDialog])
def test_tools_render_small_theme_and_guarded_inputs(qtbot, learning, theme, cls):
    from PySide6.QtWidgets import QApplication, QComboBox, QDateEdit, QSpinBox
    s, pid = learning
    apply_theme(QApplication.instance(), theme)
    args = (s, s.tasks(pid)[0]['id']) if cls == StepsDialog else (s, pid) if cls == StructureDialog else (s,)
    dialog = cls(*args)
    qtbot.addWidget(dialog)
    dialog.resize(640, 560)
    dialog.show()
    qtbot.wait(20)
    assert not dialog.grab().isNull()
    assert dialog.width() <= 650
    for widget in dialog.findChildren(QComboBox):
        assert isinstance(widget, SoftComboBox)
    for widget in dialog.findChildren(QDateEdit):
        assert isinstance(widget, ArrowWheelDateEdit)
    for widget in dialog.findChildren(QSpinBox):
        if widget.objectName() == 'qt_calendar_yearedit' and not widget.isVisible():
            continue  # Unused hidden native calendar; the guarded custom calendar replaces it.
        assert isinstance(widget, ArrowWheelSpinBox)
    close = [b for b in dialog.findChildren(QPushButton) if b.text() in {'关闭','取消','保存分组和步骤'}]
    assert close and any(b.mapTo(dialog, b.rect().center()).y() < dialog.height() for b in close)
