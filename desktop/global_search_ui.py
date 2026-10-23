from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QHBoxLayout,QCheckBox,QTreeWidgetItem,QHeaderView
import json
from desktop.workflow_ui import UtilityDialog,task_list,action
from desktop.controls import SoftComboBox
from desktop.task_search import TaskSearch
from desktop.confirmations import text_label
from desktop import global_search


class GlobalSearchDialog(UtilityDialog):
    PAGE_SIZE=200
    def __init__(self,host):
        super().__init__(host,'全局搜索','查找任务、计划、功能入口，以及已关联的资料和成果。不会扫描电脑文件正文，也不会自动打开文件或执行操作。')
        self.query=TaskSearch();self.query.query.setAccessibleName('全局搜索内容');self.box.addWidget(self.query)
        row=QHBoxLayout();self.plan=SoftComboBox();self.plan.setAccessibleName('全局搜索所属计划')
        self.entity=SoftComboBox();self.entity.setAccessibleName('全局搜索对象')
        for title,key in [('全部类型','all'),('仅任务','task'),('仅计划','plan'),('资料与成果','resource'),('仅功能','feature')]:self.entity.addItem(title,key)
        self.archived=QCheckBox('包含归档计划');row.addWidget(self.plan,1);row.addWidget(self.entity);row.addWidget(self.archived);self.box.addLayout(row)
        self.count=text_label('','muted');self.box.addWidget(self.count)
        self.tree=task_list(['名称／匹配位置','类型／状态','所属／日期']);self.tree.setAccessibleName('全局搜索结果');self.box.addWidget(self.tree,1)
        self.tree.header().setSectionResizeMode(2,QHeaderView.ResizeMode.Fixed);self.tree.setColumnWidth(2,160)
        self.box.addWidget(text_label('计划、日期和状态筛选限制任务、计划与关联资料，不限制功能入口。资料结果双击定位关联，外部打开仍需确认。路径或网址请选“名称及内容”。','muted'));self.box.addWidget(self.error)
        row=QHBoxLayout();self.more=action('再显示200项',self.show_more);row.addWidget(self.more);row.addWidget(action('打开选中',self.open_selected,True));self.locate_button=action('在计划库定位',self.locate);row.addWidget(self.locate_button);row.addStretch();row.addWidget(action('刷新资料',self.refresh));row.addWidget(action('关闭',self.reject));self.box.addLayout(row)
        self.delay=QTimer(self);self.delay.setSingleShot(True);self.delay.setInterval(180);self.delay.timeout.connect(self.populate)
        self.query.changed.connect(self.delay.start);self.plan.currentIndexChanged.connect(self.populate);self.entity.currentIndexChanged.connect(self.populate);self.archived.toggled.connect(self.populate)
        self.tree.itemDoubleClicked.connect(lambda *_:self.open_selected());self.tree.itemSelectionChanged.connect(self.selection_changed)
        self.query.scope.currentIndexChanged.connect(self.search_labels);self.search_labels();self.limit=self.PAGE_SIZE;self.refresh()

    def search_labels(self,*_):
        self.query.scope.setItemText(0,'名称／文件名');self.query.scope.setItemText(1,'名称及内容')
        self.query.scope.setToolTip('名称模式匹配任务/计划/关联名称、本地文件名；功能还匹配常用关键词。内容模式另匹配说明、文件路径和网址。')
        self.query.query.setPlaceholderText('搜索任务、功能或已关联文件名' if self.query.scope.currentData()=='title' else '搜索名称、说明、路径、网址或功能关键词')

    def refresh(self):
        with self.store.read_scope():
            self.tasks=self.store.tasks();self.plans=self.store.list_plans()+self.store.list_plans(archived=True)
            from desktop.task_resources import validate
            self.resources=[];self.resource_errors=0
            for r in self.store.read('SELECT * FROM task_resources'):
                try:self.resources.extend(dict(x,task_id=r['task_id']) for x in validate(json.loads(r['items_json'])))
                except (ValueError,TypeError,KeyError):self.resource_errors+=1
        old=self.plan.currentData();self.plan.blockSignals(True);self.plan.clear();self.plan.addItem('全部计划与独立任务',None);self.plan.addItem('独立任务','__standalone__')
        for p in self.plans:self.plan.addItem(p['title'],p['id'])
        self.plan.setCurrentIndex(max(0,self.plan.findData(old)));self.plan.blockSignals(False);self.populate()

    def populate(self,*_):
        self.delay.stop();self.limit=self.PAGE_SIZE
        only_functions=self.entity.currentData()=='feature'
        for field in (self.plan,self.archived,self.query.status,self.query.limit_dates,self.query.dates):field.setEnabled(not only_functions)
        if self.query.invalid_range() and not only_functions:self.results=[];self.error.setText('开始日期不能晚于结束日期；只查功能可切换“仅功能”。');self.error.show()
        else:
            self.error.hide();self.results=global_search.search(self.tasks,self.plans,self.query.query.text(),scope=self.query.scope.currentData(),status=self.query.status.currentText(),start=self.query.start.date().toString('yyyy-MM-dd') if self.query.limit_dates.isChecked() else None,end=self.query.end.date().toString('yyyy-MM-dd') if self.query.limit_dates.isChecked() else None,plan_id=self.plan.currentData(),include_archived=self.archived.isChecked(),entity=self.entity.currentData(),resources=self.resources,features=True)
        self.render()

    def render(self):
        item=self.tree.currentItem();old=item.data(0,Qt.ItemDataRole.UserRole) if item else None
        self.tree.clear()
        for r in self.results[:self.limit]:
            kind={'task':'任务','plan':'计划','feature':'功能','resource':'成果' if r.get('role')=='result' else '资料'}[r['kind']]
            item=QTreeWidgetItem([r['title']+('\n'+r['match'] if r['match'] else ''),kind+('\n'+r['status'] if r['kind'] in ('task','resource') else ''),r['plan']+'\n'+r['date']]);item.setData(0,Qt.ItemDataRole.UserRole,(r['kind'],r['id']));self.tree.addTopLevelItem(item)
            if item.data(0,Qt.ItemDataRole.UserRole)==old:self.tree.setCurrentItem(item)
        count=self.tree.topLevelItemCount();self.count.setText(f'找到{len(self.results)}项 · 已显示{count}项'+('；请修改关键词或筛选条件。' if not count else '')+(f'；{self.resource_errors}项关联格式异常，未列出。' if self.resource_errors else ''))
        self.more.setVisible(count<len(self.results))
        self.selection_changed()

    def show_more(self):self.limit+=self.PAGE_SIZE;self.render()

    def selected(self):
        item=self.tree.currentItem();return item.data(0,Qt.ItemDataRole.UserRole) if item else None

    def open_selected(self):
        if r:=self.selected_result():
            if r['kind']=='task':self.host.open_task_detail(r['id'])
            elif r['kind']=='plan':self.host.locate_plan(r['id'])
            elif r['kind']=='feature':
                from desktop.feature_search import open_feature
                self.run(lambda:open_feature(self.host,r['id']))
            elif r['kind']=='resource':
                d=self.host.open_task_detail(r['task_id'])
                if d:d.focus_resource(r['resource_id'])

    def selected_result(self):
        key=self.selected()
        return next((r for r in self.results if key and [r['kind'],r['id']]==list(key)),None)

    def selection_changed(self):
        r=self.selected_result();self.locate_button.setEnabled(bool(r and r['kind']!='feature'))

    def locate(self):
        if r:=self.selected_result():
            if r['kind']=='task':self.host.locate_task(r['id'])
            elif r['kind']=='resource':self.host.locate_task(r['task_id'])
            elif r['kind']=='plan':self.host.locate_plan(r['id'])
