"""Blue-grey peer tools for capture, priority and actual learning dates."""
from contextlib import closing
from datetime import date
import json

from PySide6.QtCore import QDate, Qt, QRect
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QLineEdit,
    QPlainTextEdit, QScrollArea, QFrame, QTabWidget, QTreeWidgetItem, QWidget, QVBoxLayout)

from desktop.learning_ui import ToolDialog, text, action, row, tree
from desktop.dialogs import date_edit, spin
from desktop.controls import SoftComboBox, dropdown_colors
from desktop.calendar import StudyCalendar
from desktop.confirmations import ConfirmationDialog
from desktop.schedule_ui import budget_action
from desktop.tool_windows import open_tool
from desktop.scheduling import fingerprint
from desktop.models import new_id
from desktop import organizer as service
from shiboken6 import isValid
from models import DailyTask
from desktop.readable_text import ReadableTextEdit


def token(store):
    with closing(store.connect()) as c:
        return fingerprint(c)


def plan_field(store, selected=None):
    field=SoftComboBox()
    field.addItem('独立任务', None)
    for p in store.list_plans(): field.addItem(p['title'],p['id'])
    field.setCurrentIndex(max(0,field.findData(selected)))
    field.setAccessibleName('任务所属计划')
    return field


class QuickAddDialog(ToolDialog):
    def __init__(self, store, parent=None):
        super().__init__(store,'快速记下一件事','先记下来，稍后再安排。未选日期的事项不进入周历、提醒或时间预算。',parent)
        self.resize(700,630)
        self.saved_id=None
        self.title=QLineEdit();self.title.setMaxLength(80);self.title.setPlaceholderText('例如：复习列表推导式')
        self.title.setAccessibleName('快速录入任务名称')
        self.description=ReadableTextEdit();self.description.setPlaceholderText('可选：写下具体说明')
        self.minutes=spin(5,240,25);self.minutes.setSuffix(' 分钟')
        self.plan=plan_field(store)
        self.priority=SoftComboBox();self.priority.addItems(service.PRIORITIES)
        self.pinned=QCheckBox('置顶，方便稍后找到')
        self.scheduled=QCheckBox('现在安排日期');self.due=date_edit();self.due.setEnabled(False)
        self.scheduled.toggled.connect(self.due.setEnabled)
        form=QFormLayout()
        for title,field in [('任务名称',self.title),('具体说明',self.description),('预计用时',self.minutes),('所属计划',self.plan),('优先级',self.priority),('',self.pinned),('',self.scheduled),('安排日期',self.due)]: form.addRow(title,field)
        content=QWidget();content.setLayout(form)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame);scroll.setWidget(content)
        self.box.addWidget(scroll,1)
        self.box.addWidget(self.error)
        self.save_button=action('保存到收集箱',self.accept,True)
        self.scheduled.toggled.connect(lambda checked:self.save_button.setText('保存并安排任务' if checked else '保存到收集箱'))
        self.box.addWidget(row(action('取消',self.reject),self.save_button))
        self.title.setFocus()

    def accept(self):
        if self.saved_id is not None: return
        def save():
            payload=DailyTask(day=1,title=self.title.text().strip(),description=self.description.toPlainText().strip() or '按任务名称完成这项学习。',duration_minutes=self.minutes.value(),deliverable='完成后记录学习结果')
            return service.save_capture(self.store,payload,plan_id=self.plan.currentData(),priority=self.priority.currentIndex(),pinned=self.pinned.isChecked(),due=self.due.date().toString('yyyy-MM-dd') if self.scheduled.isChecked() else None)
        self.saved_id=self.attempt(lambda:budget_action(self,self.store,save))
        if self.saved_id: super().accept()


class CaptureScheduleDialog(ToolDialog):
    def __init__(self,store,capture,parent=None):
        super().__init__(store,'把这件事安排到哪一天？','确认后移出待安排收集箱，成为可记录、专注的任务；超限仍需另行确认。',parent)
        self.capture=capture;self.token=token(store);self.saved_id=None
        self.box.addWidget(text(DailyTask.model_validate_json(capture['payload_json']).title,'brand'))
        self.plan=plan_field(store,capture['plan_id']);self.due=date_edit()
        form=QFormLayout();form.addRow('所属计划',self.plan);form.addRow('安排日期',self.due)
        self.box.addLayout(form);self.box.addStretch();self.box.addWidget(self.error)
        self.box.addWidget(row(action('取消',self.reject),action('确认安排',self.accept,True)))

    def accept(self):
        self.saved_id=self.attempt(lambda:budget_action(self,self.store,lambda:service.schedule_capture(self.store,self.capture['id'],self.due.date().toString('yyyy-MM-dd'),plan_id=self.plan.currentData(),token=self.token)))
        if self.saved_id: super().accept()


class CaptureInboxDialog(ToolDialog):
    def __init__(self,store,parent=None):
        super().__init__(store,'待安排收集箱','这里只存还没有日期的事项。安排后可在计划库或固定独立任务箱找到，不会丢失。',parent)
        self.search=QLineEdit();self.search.setPlaceholderText('搜索待安排事项名称或说明');self.search.setClearButtonEnabled(True)
        self.box.addWidget(self.search)
        self.items=tree(['事项','所属','预计','优先级']);self.box.addWidget(self.items,1)
        self.detail=text('选择事项查看说明。双击可选择安排日期。');self.box.addWidget(self.detail)
        self.items.itemSelectionChanged.connect(self.select)
        self.items.itemDoubleClicked.connect(lambda *_:self.schedule())
        self.hint=text('');self.box.addWidget(self.hint);self.box.addWidget(self.error)
        self.box.addWidget(row(action('再记一件',self.quick_add),action('安排选中事项',self.schedule,True),action('刷新',self.refresh),action('关闭',self.close)))
        self.search.textChanged.connect(self.refresh);self.refresh()

    def refresh(self,*_):
        selected=self.items.currentItem();tid=selected.data(0,Qt.ItemDataRole.UserRole) if selected else None
        self.rows=service.captures(self.store);self.items.clear()
        query=self.search.text().strip().casefold()
        for r in self.rows:
            p=DailyTask.model_validate_json(r['payload_json'])
            if query and query not in (p.title+' '+p.description).casefold():continue
            item=QTreeWidgetItem(self.items,[('置顶 · ' if r['pinned'] else '')+p.title,r['plan_title'] or '独立任务',f'{p.duration_minutes} 分钟',service.PRIORITIES[r['priority']]])
            item.setData(0,Qt.ItemDataRole.UserRole,r['id'])
            if r['id']==tid:self.items.setCurrentItem(item)
        self.hint.setText(f'显示 {self.items.topLevelItemCount()} / {len(self.rows)} 件待安排事项；不计入今日预算。')
        self.select()

    def selected(self):
        item=self.items.currentItem()
        return next((r for r in self.rows if item and r['id']==item.data(0,Qt.ItemDataRole.UserRole)),None)

    def select(self):
        r=self.selected()
        self.detail.setText(DailyTask.model_validate_json(r['payload_json']).description if r else '选择事项查看说明。双击可选择安排日期。')

    def schedule(self):
        r=self.selected()
        if r:open_tool(self,CaptureScheduleDialog(self.store,r,self),('capture_schedule',r['id']),lambda _:self.refresh() if isValid(self) else None)

    def quick_add(self):
        open_tool(self,QuickAddDialog(self.store,self),'quick_add',lambda _:self.refresh() if isValid(self) else None)


class AttentionDialog(ToolDialog):
    def __init__(self,store,task_id,parent=None):
        super().__init__(store,'优先级与今天重点','只调整展示顺序，不改日期、时间预算或完成状态。今天重点过了当天便不再显示。',parent)
        self.task_id=task_id;self.token=token(store)
        task=next(r for r in store.tasks() if r['id']==task_id)
        self.box.addWidget(text('当前任务：'+task['task'].title,'brand'))
        self.priority=SoftComboBox();self.priority.addItems(service.PRIORITIES);self.priority.setCurrentIndex(task['priority'])
        self.pinned=QCheckBox('置顶');self.pinned.setChecked(bool(task['pinned']))
        self.important=QCheckBox('标为今天重点');self.important.setChecked(task['focus_date']==date.today().isoformat())
        self.box.addWidget(row(text('优先级'),self.priority,self.pinned,self.important));self.box.addStretch()
        self.box.addWidget(self.error);self.box.addWidget(row(action('取消',self.reject),action('保存展示设置',self.accept,True)))

    def accept(self):
        def save():
            service.set_attention(self.store,self.task_id,priority=self.priority.currentIndex(),pinned=self.pinned.isChecked(),focus_date=date.today().isoformat() if self.important.isChecked() else None,token=self.token)
            return True
        if self.attempt(save):super().accept()


class StudyLogDialog(ToolDialog):
    def __init__(self,store,task_id,parent=None):
        super().__init__(store,'补记实际学习日期','补记的是已经学习的用时，不改变预计分钟或安排日期；旧累计用时不推算发生日期。',parent)
        self.task_id=task_id;self.token=token(store);self.entry_id=new_id()
        task=next(r for r in store.tasks() if r['id']==task_id)
        self.box.addWidget(text('当前任务：'+task['task'].title,'brand'))
        self.day=date_edit();self.day.setMinimumDate(QDate(1900,1,1));self.day.setMaximumDate(QDate.currentDate())
        self.minutes=spin(1,1440,25);self.minutes.setSuffix(' 分钟')
        self.box.addWidget(row(text('学习日期'),self.day,text('本次补记'),self.minutes))
        self.records=tree(['学习日期','来源','分钟']);self.box.addWidget(self.records,1)
        self.remove_button=action('撤销选中补记',self.remove_entry)
        self.records.itemSelectionChanged.connect(self.select_entry)
        self.refresh();self.box.addWidget(self.error)
        self.box.addWidget(row(self.remove_button,action('取消',self.reject),action('保存这次补记',self.accept,True)))

    def refresh(self):
        self.records.clear()
        for r in self.store.read('SELECT * FROM study_events WHERE task_id=? ORDER BY occurred_on DESC,id',(self.task_id,)):
            item=QTreeWidgetItem(self.records,[r['occurred_on'],'专注计时' if r['source']=='focus' else '人工补记',f"{r['seconds']/60:g}"])
            item.setData(0,Qt.ItemDataRole.UserRole,r['id'])
            item.setData(0,Qt.ItemDataRole.UserRole+1,r['source'])
        self.select_entry()

    def select_entry(self):
        item=self.records.currentItem()
        self.remove_button.setEnabled(bool(item and item.data(0,Qt.ItemDataRole.UserRole+1)=='manual'))

    def remove_entry(self):
        item=self.records.currentItem()
        if not item:return
        eid=item.data(0,Qt.ItemDataRole.UserRole)
        box=ConfirmationDialog('撤销这次人工补记？','只扣除选中的这条按日期人工补记，不改预计用时、日期、完成状态或其他学习记录。',self,confirm='确认撤销补记')
        if box.exec()!=QDialog.DialogCode.Accepted:return
        def run():
            service.delete_manual_event(self.store,eid,self.token)
            self.token=token(self.store);self.refresh();return True
        self.attempt(run)

    def accept(self):
        if self.attempt(lambda:service.log_manual(self.store,self.task_id,self.day.date().toPython(),self.minutes.value(),token=self.token,entry_id=self.entry_id)):
            super().accept()


class StudyLogCalendar(StudyCalendar):
    def __init__(self,parent=None):
        self.minutes={}
        super().__init__(parent)
        self.setMinimumHeight(380)

    def has_annotation_row(self):
        return True

    def cell_annotation(self,day):
        amount=self.minutes.get(day.toString('yyyy-MM-dd'),0)
        return f'{amount:.1f}分' if amount else ''


class ActualStatisticsDialog(ToolDialog):
    def __init__(self,store,parent=None):
        super().__init__(store,'实际学习日历','按真正发生的学习日期统计新记录；未知日期的旧累计用时单独列出，不混入日历或趋势。',parent)
        self.resize(920,800)
        self.plan=self.scope();self.archived=QCheckBox('包含归档计划')
        self.box.addWidget(row(self.plan,self.archived,action('刷新',self.refresh)))
        self.metrics=text('','confirmationSection');self.box.addWidget(self.metrics)
        self.calendar=StudyLogCalendar()
        calendar_page=QWidget();calendar_box=QVBoxLayout(calendar_page);calendar_box.addWidget(self.calendar,1)
        calendar_box.addWidget(text('日期下方为实际分钟简写（保留1位小数）；精确分秒见“每日记录”。切换月份可查看历史。'))
        calendar_scroll=QScrollArea();calendar_scroll.setWidgetResizable(True);calendar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        calendar_scroll.setWidget(calendar_page)
        self.daily=tree(['实际学习日期','专注用时','补记用时','实际合计'])
        self.weekly=tree(['本月内的周区间','实际用时','学习天数'])
        tabs=QTabWidget();tabs.addTab(calendar_scroll,'学习日历');tabs.addTab(self.daily,'每日记录');tabs.addTab(self.weekly,'每周趋势')
        self.box.addWidget(tabs,1)
        self.unknown=text('');self.box.addWidget(self.unknown);self.box.addWidget(self.error)
        self.box.addWidget(row(action('复制本月总结',self.copy_summary),action('关闭',self.close)))
        self.plan.currentIndexChanged.connect(self.refresh);self.archived.toggled.connect(self.refresh)
        self.calendar.currentPageChanged.connect(self.refresh);self.refresh()

    def refresh(self,*_):
        self.report=None
        def run():
            start=QDate(self.calendar.yearShown(),self.calendar.monthShown(),1)
            end=start.addMonths(1).addDays(-1)
            r=self.report=service.statistics(self.store,start.toPython(),end.toPython(),self.plan.currentData(),self.archived.isChecked())
            self.metrics.setText(f"本月记录 {service.duration_text(r['actual_seconds'])} · 学习 {r['active_days']} 天 · 月内最长连续 {r['longest_streak']} 天")
            self.unknown.setText(f"另有 {service.duration_text(r['unknown_seconds'])}未确定发生日期（所选范围全部任务的历史累计），不计入本月。")
            self.calendar.minutes={d['date']:d['actual_seconds']/60 for d in r['days']};self.calendar.updateCells()
            self.daily.clear();self.weekly.clear();weeks={}
            for d in r['days']:
                QTreeWidgetItem(self.daily,[d['date'],service.duration_text(d['focus_seconds']),service.duration_text(d['manual_seconds']),service.duration_text(d['actual_seconds'])])
                key=date.fromisoformat(d['date']).isocalendar()[:2]
                weeks.setdefault(key,[]).append(d)
            for group in weeks.values():
                QTreeWidgetItem(self.weekly,[group[0]['date']+' — '+group[-1]['date'],service.duration_text(sum(d['actual_seconds'] for d in group)),str(sum(d['actual_seconds']>0 for d in group))])
        self.attempt(run)

    def copy_summary(self):
        if self.report:
            from PySide6.QtWidgets import QApplication
            QApplication.clipboard().setText(self.report['summary'])
