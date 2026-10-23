"""One read-only task overview, with explicit routes to existing editors."""
from pathlib import Path
import json
from PySide6.QtCore import Qt,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QTabWidget,QScrollArea,QFrame,
    QTreeWidgetItem,QLineEdit,QFileDialog,QFormLayout,QDialog)
from desktop.workflow_ui import UtilityDialog,task_list,action
from desktop.confirmations import text_label,ConfirmationDialog
from desktop.controls import SoftComboBox
from desktop.organizer import duration_text
from desktop import task_resources


def readable_page():
    scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    page=QWidget();layout=QVBoxLayout(page);layout.setContentsMargins(8,12,12,12);layout.setSpacing(14)
    scroll.setWidget(page);return scroll,layout


def section(layout,title,text):
    layout.addWidget(text_label(title,'eyebrow'))
    label=text_label(text or '未填写');label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse|Qt.TextInteractionFlag.TextSelectableByKeyboard)
    layout.addWidget(label);return label


class ResourceEditor(ConfirmationDialog):
    def __init__(self,parent):
        super().__init__('关联资料或成果','只保存地址，不复制、上传或删除原文件。',parent,confirm='保存关联')
        form=QFormLayout();self.content.addLayout(form)
        self.name=QLineEdit();self.name.setMaxLength(120);self.name.setAccessibleName('资料名称')
        self.target=QLineEdit();self.target.setMaxLength(2048);self.target.setAccessibleName('资料地址或文件路径')
        self.kind=SoftComboBox();self.kind.addItem('网页地址','url');self.kind.addItem('本地文件','file');self.kind.setAccessibleName('资料地址类型')
        self.role=SoftComboBox();self.role.addItem('学习资料','material');self.role.addItem('完成成果','result');self.role.setAccessibleName('资料用途')
        for name,field in [('名称',self.name),('用途',self.role),('地址类型',self.kind),('地址／路径',self.target)]:form.addRow(name,field)
        self.content.addWidget(action('选择本地文件…',self.choose_file))
        self.error=text_label('','confirmationNote');self.content.addWidget(self.error);self.content.addStretch();self.value=None

    def choose_file(self):
        path,_=QFileDialog.getOpenFileName(self,'选择要关联的本地文件')
        if path:
            self.kind.setCurrentIndex(self.kind.findData('file'));self.target.setText(path)
            if not self.name.text():self.name.setText(Path(path).name)

    def accept(self):
        try:self.value=task_resources.item(self.name.text(),self.target.text(),self.kind.currentData(),self.role.currentData())
        except ValueError as e:self.error.setText(str(e));return
        super().accept()


class TaskDetailDialog(UtilityDialog):
    def __init__(self,host,tid):
        super().__init__(host,'任务详情','这里查看任务和学习证据；只有明确编辑、保存或开始专注才会改变资料。')
        self.tid=tid;self.tabs=QTabWidget();self.box.addWidget(self.tabs,1)
        self.overview,self.overview_box=readable_page();self.tabs.addTab(self.overview,'任务说明')
        self.steps=task_list(['执行步骤','进度']);self.tabs.addTab(self.steps,'步骤与前置')
        self.records,self.records_box=readable_page();self.tabs.addTab(self.records,'学习记录')
        materials=QWidget();area=QVBoxLayout(materials);area.setContentsMargins(0,8,0,0)
        self.resources=task_list(['资料／成果名称','类型']);self.resources.setAccessibleName('任务资料与成果')
        area.addWidget(self.resources,1);self.resource_hint=text_label('选中可查看地址；打开前会询问。','muted');area.addWidget(self.resource_hint)
        row=QHBoxLayout();row.addWidget(action('添加关联…',self.add_resource));row.addWidget(action('打开选中…',self.open_resource));row.addWidget(action('移除关联…',self.remove_resource));row.addStretch();area.addLayout(row)
        self.tabs.addTab(materials,'资料与成果');self.resources.itemSelectionChanged.connect(self.resource_selected)
        self.resources.itemDoubleClicked.connect(lambda *_:self.open_resource())
        self.box.addWidget(self.error)
        row=QHBoxLayout();row.addWidget(action('编辑任务／填写记录',self.edit,True));row.addWidget(action('开始专注',lambda:self.host.run_task_action(self.tid,'focus',source=self)))
        from desktop.action_menu import menu_button
        row.addWidget(menu_button('整理任务',[(title,lambda k=key:self.host.run_task_action(self.tid,k,source=self)) for title,key in
            [('分组与执行步骤','steps'),('设置前置任务','dependencies'),('补记实际学习日期','study_log'),('优先级与今天重点','attention')]]))
        row.addStretch();row.addWidget(action('刷新',self.refresh));row.addWidget(action('关闭',self.reject));self.box.addLayout(row)
        self.refresh()

    @staticmethod
    def clear(layout):
        while layout.count():
            item=layout.takeAt(0)
            if item.widget():item.widget().deleteLater()

    def refresh(self):
        rows=self.store.tasks(task_id=self.tid)
        if not rows:
            self.error.setText('任务已被删除。请关闭此页，或到回收站查看。');self.error.show();self.tabs.setEnabled(False);return
        self.tabs.setEnabled(True);self.error.hide();r=rows[0];self.row=r;t=r['task']
        self.setWindowTitle(t.title+' · 任务详情 · 学习罗盘')
        self.clear(self.overview_box)
        section(self.overview_box,'当前任务',t.title)
        section(self.overview_box,'安排与状态',f"{r['plan_title']} · {r['due_date']} · {r['status']}"+(' · 已暂停' if r.get('paused') else '')+(' · 已归档' if r['archived'] else ''))
        section(self.overview_box,'预计用时',duration_text(t.duration_minutes*60))
        section(self.overview_box,'任务说明',t.description);section(self.overview_box,'完成标准',t.deliverable);self.overview_box.addStretch()
        self.steps.clear()
        from desktop.learning_tools import task_details
        details,_=task_details(self.store,self.tid)
        self.steps.addTopLevelItem(QTreeWidgetItem(['分组：'+(details['stage'] or '未分组'),'']))
        for step in details['steps']:self.steps.addTopLevelItem(QTreeWidgetItem([step['title'],'已做过' if step['done'] else '未做']))
        if not details['steps']:self.steps.addTopLevelItem(QTreeWidgetItem(['尚未添加执行步骤','']))
        from desktop.workflow import prerequisites
        for p in prerequisites(self.store,self.tid):self.steps.addTopLevelItem(QTreeWidgetItem(['前置任务：'+json.loads(p['payload_json'])['title'],p['status']]))
        self.clear(self.records_box)
        section(self.records_box,'累计学习用时',f"专注：{duration_text(r['focus_seconds'])}；人工补记：{duration_text(r['manual_seconds'])}")
        section(self.records_box,'收获与问题',r['note'])
        events=self.store.read('SELECT occurred_on,seconds,source FROM study_events WHERE task_id=? ORDER BY occurred_on DESC,created_at DESC',(self.tid,))
        section(self.records_box,'按实际发生日期记录','\n'.join(f"{e['occurred_on']} · {'专注' if e['source']=='focus' else '补记'} · {duration_text(e['seconds'])}" for e in events) or '暂无按日期记录；早期累计用时没有可靠日期时，不分配到某一天。')
        self.records_box.addStretch();self.items=task_resources.load(self.store,self.tid);self.resources.clear()
        for resource in self.items:
            item=QTreeWidgetItem([resource['title'],('成果' if resource['role']=='result' else '资料')+' · '+('网页' if resource['kind']=='url' else '文件')]);item.setData(0,Qt.ItemDataRole.UserRole,resource['id']);self.resources.addTopLevelItem(item)
        self.resource_hint.setText('选中可查看地址；打开前会询问。原文件不会被复制或删除。')

    def edit(self):self.host.run_task_action(self.tid,'edit',source=self)

    def focus_resource(self,resource_id):
        self.refresh()
        if not self.tabs.isEnabled():return False
        self.tabs.setCurrentIndex(3)
        for i in range(self.resources.topLevelItemCount()):
            item=self.resources.topLevelItem(i)
            if item.data(0,Qt.ItemDataRole.UserRole)==resource_id:
                self.resources.setCurrentItem(item);self.resources.scrollToItem(item);return True
        self.error.setText('这个关联已被移除或修改，请刷新全局搜索后重试。');self.error.show();return False

    def selected_resource(self):
        item=self.resources.currentItem()
        return next((r for r in self.items if item and r['id']==item.data(0,Qt.ItemDataRole.UserRole)),None)

    def resource_selected(self):
        if r:=self.selected_resource():self.resource_hint.setText(r['target'])

    def add_resource(self):
        expected=list(self.items);d=ResourceEditor(self)
        if d.exec()==QDialog.DialogCode.Accepted:
            def save():task_resources.save(self.store,self.tid,[*expected,d.value],expected);return True
            if self.run(save):self.refresh();self.host.refresh()

    def remove_resource(self):
        r=self.selected_resource()
        if not r:return
        box=ConfirmationDialog('移除这个关联？',r['title'],self,note='只移除地址，不会删除原文件或网页。',confirm='移除关联')
        if box.exec()!=QDialog.DialogCode.Accepted:return
        def save():task_resources.save(self.store,self.tid,[x for x in self.items if x['id']!=r['id']],self.items);return True
        if self.run(save):self.refresh();self.host.refresh()

    def open_resource(self):
        r=self.selected_resource()
        if not r:return
        def check():
            if r not in task_resources.load(self.store,self.tid):raise ValueError('关联已有变化，请刷新后重试')
            task_resources.validate([r])
            if r['kind']=='file':
                path=Path(r['target'])
                if not path.is_file():raise ValueError('原文件不在这个路径，请检查文件是否已移动或删除')
                if path.suffix.lower() not in ('.txt','.md','.pdf','.png','.jpg','.jpeg','.gif','.webp','.docx','.xlsx','.pptx','.json','.csv'):
                    raise ValueError('不直接运行程序或脚本。请到文件管理器核对文件后自行打开。')
            box=ConfirmationDialog('打开外部资料？',r['target'],self,note='将交给系统默认应用打开。请确认来源可信；网页可能产生网络请求。',confirm='打开资料')
            if box.exec()!=QDialog.DialogCode.Accepted:return
            if not QDesktopServices.openUrl(QUrl(r['target']) if r['kind']=='url' else QUrl.fromLocalFile(r['target'])):raise ValueError('系统未能打开，请复制地址到相应应用')
        self.run(check)
