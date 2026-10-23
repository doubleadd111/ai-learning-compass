"""Offline QA of real app controls, called only with an isolated smoke-test store."""
import json
from datetime import date

from PySide6.QtCore import QDate, QPoint, QPointF, QTime, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (QApplication, QComboBox, QDateEdit, QSpinBox, QTimeEdit,
                              QStyle, QStyleOptionSpinBox, QVBoxLayout, QWidget)
from desktop.controls import (SoftComboBox, ArrowWheelDateEdit, ArrowWheelSpinBox,
                              ArrowWheelTimeEdit, dropdown_arrow_rect)
from desktop.dialogs import ConnectionDialog, PlanDialog, TaskDialog, ModelSelectionDialog, spin, date_edit
from desktop.models import ModelConnection
from desktop.planning import ClarificationQuestion, PlanningDraft, PlanningTurn
from desktop.planning_ui import ModificationDialog, PlanningWindow
from desktop.schedule_ui import ScheduleDialog
from desktop.manual_tasks import ManualTaskDialog
from desktop.history_ui import PlanHistoryWindow


def check_input_controls(window):
    config = ModelConnection(name='离线控件检查', base_url='https://example.invalid', model='placeholder')
    saved = window.store.get_plan(window.store.list_plans()[0]['id'])
    forms = [window, PlanDialog([config], parent=window), ConnectionDialog(parent=window),
             TaskDialog(saved['tasks'][0], parent=window),
             ModificationDialog(saved, [saved['tasks'][0]['id']], window),
             ModelSelectionDialog(['mock-one', 'mock-two'], window)]
    forms.append(ScheduleDialog(window.store, window, plan_id=saved['id']))
    forms.append(ScheduleDialog(window.store, window, plan_id=saved['id'], kind='暂停'))
    forms.append(ManualTaskDialog(window.store, window))
    forms.append(PlanHistoryWindow(window, saved['id']))
    from desktop.task_detail_ui import TaskDetailDialog,ResourceEditor
    from desktop.global_search_ui import GlobalSearchDialog
    from desktop.backup_ui import BackupDialog
    from desktop.backup_space_ui import CleanupDialog
    forms.extend([TaskDetailDialog(window,saved['tasks'][0]['id']),ResourceEditor(window),
                  GlobalSearchDialog(window),BackupDialog(window),CleanupDialog(window)])
    from desktop.organizer_ui import QuickAddDialog, AttentionDialog, StudyLogDialog, ActualStatisticsDialog
    from desktop.month_view import MonthDialog, MovePreviewDialog
    tid=saved['tasks'][0]['id']
    forms.extend([QuickAddDialog(window.store,window), AttentionDialog(window.store,tid,window),
        StudyLogDialog(window.store,tid,window), ActualStatisticsDialog(window.store,window),
        MonthDialog(window.store,window.edit_task_id,window),
        MovePreviewDialog(window.store,tid,date.today(),window)])
    from desktop.trash_ui import TrashDialog, PurgeDialog
    forms.extend([TrashDialog(window.store, window), PurgeDialog('离线清除确认检查', window)])
    from desktop.learning_ui import StatisticsDialog, StepsDialog, StructureDialog, RecurringDialog, SeriesListDialog
    forms.extend([StatisticsDialog(window.store, window), StepsDialog(window.store, saved['tasks'][0]['id'], window),
        StructureDialog(window.store, saved['id'], window), RecurringDialog(window.store, window), SeriesListDialog(window.store, window)])
    draft = PlanningDraft(profile=saved['profile'], start_date=date.fromisoformat(saved['start_date']),
                          weekdays=json.loads(saved['weekdays_json']), connection=config)
    flow = PlanningWindow(window, draft)
    flow.draft.receive_turn(PlanningTurn(decision='ask', outline=None, questions=[
        ClarificationQuestion(id='qa', question='优先练习哪一部分？', purpose='检查追问选择框',
                              options=['基础练习', '小型项目'])]))
    flow.render()
    forms.append(flow)
    counts = dict(combo=0, number=0, date=0, time=0)
    seen = set()
    try:
        for form in forms:
            for field in form.findChildren(QWidget):
                if not isinstance(field, (QComboBox, QSpinBox, QDateEdit, QTimeEdit)):
                    continue
                if id(field) in seen:
                    continue
                seen.add(id(field))
                # Qt's hidden built-in calendar navigation is replaced by StudyCalendar.
                if field.objectName() == 'qt_calendar_yearedit' and not field.isVisible():
                    continue
                if isinstance(field, QComboBox):
                    kind, expected = 'combo', SoftComboBox
                elif isinstance(field, QDateEdit):
                    kind, expected = 'date', ArrowWheelDateEdit
                elif isinstance(field, QTimeEdit):
                    kind, expected = 'time', ArrowWheelTimeEdit
                else:
                    kind, expected = 'number', ArrowWheelSpinBox
                assert isinstance(field, expected), f'Unguarded {kind}: {type(field).__name__}'
                counts[kind] += 1
        assert counts['combo'] >= 20 and counts['number'] >= 6 and counts['date'] >= 2 and counts['time'] >= 1
        host = QWidget()
        layout = QVBoxLayout(host)
        combo, editable = SoftComboBox(), SoftComboBox()
        for field in (combo, editable):
            field.addItems(['first', 'second', 'third'])
            field.setCurrentIndex(1)
        editable.setEditable(True)
        samples = [combo, editable, spin(1, 8, 4), date_edit(QDate(2026, 10, 15)), ArrowWheelTimeEdit(QTime(20, 30))]
        for field in samples:
            layout.addWidget(field)
        host.resize(600, 340)
        host.show()
        QApplication.processEvents()
        try:
            for field in samples:
                value = field.currentText if isinstance(field, QComboBox) else field.date if isinstance(field, QDateEdit) else field.time if isinstance(field, QTimeEdit) else field.value
                initial = value()
                if isinstance(field, (QComboBox, QDateEdit)):
                    area = dropdown_arrow_rect(field)
                else:
                    option = QStyleOptionSpinBox()
                    field.initStyleOption(option)
                    area = field.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                                                        QStyle.SubControl.SC_SpinBoxUp, field)
                for position, changes in ((QPoint(20, field.height() // 2), False), (area.center(), True)):
                    event = QWheelEvent(QPointF(position), QPointF(field.mapToGlobal(position)), QPoint(), QPoint(0, -120),
                                        Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
                    QApplication.sendEvent(field, event)
                    assert (value() != initial) == changes, f'Wheel scope failed: {type(field).__name__}'
        finally:
            host.close()
            host.deleteLater()
        return dict(controls=counts, sample_checks=len(samples), success=True)
    finally:
        for form in forms[1:]:
            form.close()
            form.deleteLater()
