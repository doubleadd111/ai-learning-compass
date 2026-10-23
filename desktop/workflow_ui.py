"""Task utilities using the application's existing rounded cards and controls."""
from datetime import date
import json
import sqlite3

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut,QKeySequence
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QTreeWidget,
    QTreeWidgetItem,QLineEdit,QAbstractItemView,QHeaderView)
from desktop.controls import SoftComboBox
from desktop.dialogs import date_edit
from desktop.confirmations import ConfirmationDialog,text_label
from desktop.schedule_ui import budget_action
from desktop.task_search import TaskQuery,search_tasks
from desktop import workflow


def task_list(headers):
    tree=QTreeWidget();tree.setHeaderLabels(headers);tree.setRootIsDecorated(False)
    tree.setAlternatingRowColors(False);tree.setUniformRowHeights(True)
    tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    tree.header().setStretchLastSection(False)
    tree.header().setSectionResizeMode(0,QHeaderView.ResizeMode.Stretch)
    for i in range(1,len(headers)):tree.header().setSectionResizeMode(i,QHeaderView.ResizeMode.ResizeToContents)
    return tree


def action(text,slot,primary=False):
    b=QPushButton(text);b.setAutoDefault(False)
    if primary:b.setObjectName('primary')
    b.clicked.connect(slot);return b


class UtilityDialog(QDialog):
    def __init__(self,host,title,description):
        super().__init__(host);self.host=host;self.store=host.store
        self.setWindowTitle(title+' · 学习罗盘');self.resize(900,680);self.setMinimumSize(540,440)
        self.box=QVBoxLayout(self);self.box.setContentsMargins(24,24,24,24);self.box.setSpacing(12)
        from desktop.guide import open_guide
        title_row=QHBoxLayout();title_row.addWidget(text_label(title,'brand'));title_row.addStretch()
        title_row.addWidget(action('使用说明',lambda:open_guide(self,'tools')));self.box.addLayout(title_row)
        self.help_shortcut=QShortcut(QKeySequence('F1'),self);self.help_shortcut.activated.connect(lambda:open_guide(self,'tools'))
        self.box.addWidget(text_label(description,'muted'))
        self.error=text_label('','confirmationNote');self.error.hide()

    def run(self,fn):
        try:
            result=budget_action(self,self.store,fn);self.error.hide();return result
        except (ValueError,OSError) as e:
            self.error.setText(str(e));self.error.show();return None
        except sqlite3.Error:
            self.error.setText('数据暂时无法保存，请关闭其他写入操作，刷新后重试。');self.error.show();return None


class BatchDialog(UtilityDialog):
    def __init__(self,host,ids=(),plan_id=None):
        super().__init__(host,'批量管理任务','勾选任务，选择动作，再查看变更。整批保存或整批取消；不会增加学习时长。')
        self.plan_id=plan_id;self.initial=set(ids);self.proposal=None
        self.search=TaskQuery();self.box.addWidget(self.search)
        self.tree=task_list(['选择任务','所属计划','日期','状态']);self.box.addWidget(self.tree,1)
        row=QHBoxLayout();self.operation=SoftComboBox()
        for title,key in [('修改安排日期','date'),('设置任务分组','group'),('标记已完成','complete'),('移入回收站（仅手动任务）','trash')]:self.operation.addItem(title,key)
        self.day=date_edit();self.day.setAccessibleName('批量安排日期')
        self.group=QLineEdit();self.group.setMaxLength(40);self.group.setPlaceholderText('分组名称；留空归为未分组');self.group.hide()
        row.addWidget(self.operation);row.addWidget(self.day);row.addWidget(self.group,1);self.box.addLayout(row)
        self.summary=text_label('未预览。勾选和修改选项后，旧预览会失效。','muted');self.box.addWidget(self.summary)
        self.box.addWidget(self.error)
        footer=QHBoxLayout()
        footer.addWidget(action('勾选搜索结果',lambda:self.select_visible(True)));footer.addWidget(action('清空选择',lambda:self.select_visible(False)))
        footer.addStretch();footer.addWidget(action('查看变更预览',self.preview))
        self.apply_button=action('确认应用',self.apply,True);self.apply_button.setEnabled(False)
        footer.addWidget(self.apply_button);footer.addWidget(action('关闭',self.reject));self.box.addLayout(footer)
        self.search.changed.connect(self.populate);self.operation.currentIndexChanged.connect(self.invalidate)
        self.day.dateChanged.connect(self.invalidate);self.group.textChanged.connect(self.invalidate)
        self.tree.itemChanged.connect(self.invalidate);self.populate()

    def invalidate(self,*_):
        self.proposal=None;self.apply_button.setEnabled(False)
        self.day.setVisible(self.operation.currentData()=='date');self.group.setVisible(self.operation.currentData()=='group')
        self.summary.setText('尚未应用；请查看最新变更预览。')

    def populate(self):
        selected={self.tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole) for i in range(self.tree.topLevelItemCount())
                  if self.tree.topLevelItem(i).checkState(0)==Qt.CheckState.Checked} or self.initial
        self.initial=set();self.tree.blockSignals(True);self.tree.clear()
        rows=search_tasks(self.store.tasks(self.plan_id),self.search.query.text(),scope=self.search.scope.currentData())
        for r in rows:
            item=QTreeWidgetItem([r['task'].title,r['plan_title'],r['due_date'],r['status']])
            item.setData(0,Qt.ItemDataRole.UserRole,r['id']);item.setCheckState(0,Qt.CheckState.Checked if r['id'] in selected else Qt.CheckState.Unchecked)
            self.tree.addTopLevelItem(item)
        self.tree.blockSignals(False);self.invalidate()

    def select_visible(self,value):
        for i in range(self.tree.topLevelItemCount()):self.tree.topLevelItem(i).setCheckState(0,Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)

    def preview(self):
        self.invalidate()
        ids=[self.tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole) for i in range(self.tree.topLevelItemCount()) if self.tree.topLevelItem(i).checkState(0)==Qt.CheckState.Checked]
        p=self.run(lambda:workflow.preview_batch(self.store,ids,self.operation.currentData(),self.day.date().toString('yyyy-MM-dd') if self.operation.currentData()=='date' else self.group.text() if self.operation.currentData()=='group' else ''))
        if not p:return
        box=ConfirmationDialog('核对批量变更',f'共{len(p.ids)}项。此时尚未保存，返回可继续修改。',self,confirm='使用这份预览')
        for line in p.changes:box.content.addWidget(text_label(line))
        for line in p.warnings:box.content.addWidget(text_label(line,'confirmationNote'))
        box.content.addStretch()
        if box.exec()==QDialog.DialogCode.Accepted:
            self.proposal=p;self.apply_button.setEnabled(True);self.summary.setText(f'已预览{len(p.ids)}项；确认应用时仍会检查资料变化及预算。')

    def apply(self):
        p=self.proposal
        if not p:return
        if p.operation=='trash':
            box=ConfirmationDialog('将选中任务移入回收站？',f'共{len(p.ids)}项，30天内可恢复。依赖这些任务的关系将移除。',self,confirm='移入回收站')
            if box.exec()!=QDialog.DialogCode.Accepted:return
        def save():workflow.apply_batch(self.store,p);return True
        if self.run(save):self.host.refresh();self.accept()
        else:self.invalidate()


class DependenciesDialog(UtilityDialog):
    def __init__(self,host,tid):
        rows=host.store.tasks();self.current=next((r for r in rows if r['id']==tid),None)
        if not self.current:raise ValueError('任务已不存在')
        super().__init__(host,'设置前置任务','前置任务是建议先完成的任务，不是执行步骤。只提示，不自动改期或完成；本轮支持同一计划／独立任务箱内关联。')
        self.tid=tid;self.expected=workflow.token(self.store)
        self.box.addWidget(text_label('当前任务：'+self.current['task'].title,'brand'))
        self.search=QLineEdit();self.search.setPlaceholderText('搜索前置任务名称或日期');self.search.setClearButtonEnabled(True);self.box.addWidget(self.search)
        self.tree=task_list(['先完成哪些任务','日期','当前状态']);self.box.addWidget(self.tree,1)
        selected={r['id'] for r in workflow.prerequisites(self.store,tid)}
        for r in rows:
            if r['id']==tid or r['plan_id']!=self.current['plan_id']:continue
            item=QTreeWidgetItem([r['task'].title,r['due_date'],r['status']]);item.setData(0,Qt.ItemDataRole.UserRole,r['id'])
            item.setCheckState(0,Qt.CheckState.Checked if r['id'] in selected else Qt.CheckState.Unchecked);self.tree.addTopLevelItem(item)
        self.search.textChanged.connect(self.filter);self.box.addWidget(self.error)
        footer=QHBoxLayout();footer.addStretch();footer.addWidget(action('取消',self.reject));footer.addWidget(action('保存前置关系',self.save,True));self.box.addLayout(footer)

    def filter(self,text):
        for i in range(self.tree.topLevelItemCount()):
            item=self.tree.topLevelItem(i);item.setHidden(text.casefold() not in (item.text(0)+' '+item.text(1)).casefold())

    def save(self):
        ids=[self.tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole) for i in range(self.tree.topLevelItemCount()) if self.tree.topLevelItem(i).checkState(0)==Qt.CheckState.Checked]
        def save():workflow.set_dependencies(self.store,self.tid,ids,self.expected);return True
        if self.run(save):self.host.refresh();self.accept()


class ReminderDialog(UtilityDialog):
    def __init__(self,host):
        super().__init__(host,'提醒中心','集中查看今日待办、逾期及错过的提醒。稍后提醒不修改任务日期；暂停计划不在这里催办。')
        self.query=TaskQuery();self.box.addWidget(self.query)
        self.tree=task_list(['任务','安排日期','提醒状态']);self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.box.addWidget(self.tree,1)
        self.box.addWidget(text_label('双击任务查看详情。已完成任务请到计划库或独立任务箱查看。','muted'))
        self.box.addWidget(self.error);footer=QHBoxLayout()
        for title,key in [('15分钟后提醒','snooze'),('今天不再提醒','mute'),('恢复正常提醒','clear')]:footer.addWidget(action(title,lambda _=False,k=key:self.control(k)))
        footer.addStretch();footer.addWidget(action('刷新',self.refresh));footer.addWidget(action('关闭',self.reject));self.box.addLayout(footer)
        self.query.changed.connect(self.refresh);self.tree.itemDoubleClicked.connect(self.open_task);self.refresh()

    def refresh(self):
        self.tree.clear();self.expected=workflow.token(self.store)
        for r in search_tasks(workflow.reminder_rows(self.store),self.query.query.text(),scope=self.query.scope.currentData()):
            item=QTreeWidgetItem([r['task'].title,r['due_date'],r['reminder_state']]);item.setData(0,Qt.ItemDataRole.UserRole,r['id']);self.tree.addTopLevelItem(item)

    def open_task(self,item,*_):self.host.open_task_detail(item.data(0,Qt.ItemDataRole.UserRole))

    def control(self,key):
        item=self.tree.currentItem()
        if not item:self.error.setText('请先选择一项任务');self.error.show();return
        def save():workflow.control_reminder(self.store,item.data(0,Qt.ItemDataRole.UserRole),key,self.expected);return True
        if self.run(save):self.refresh()
