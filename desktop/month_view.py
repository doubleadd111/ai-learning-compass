"""Month overview with drag proposals; a drop never writes task dates."""
from datetime import date, timedelta
import json

from PySide6.QtCore import Qt, QDate, QMimeData, QPoint, QRectF, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QDrag, QPainter, QColor, QPen
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout,
    QPushButton, QScrollArea, QTextBrowser, QTreeWidgetItem, QVBoxLayout, QWidget, QCheckBox)

from desktop.learning_ui import ToolDialog, text, action, row, tree
from desktop.controls import SoftComboBox
from desktop.dialogs import date_edit
from desktop.organizer_ui import token
from desktop import organizer
from desktop.scheduling import preview
from desktop.schedule_ui import budget_action
from desktop.tool_windows import open_tool
from shiboken6 import isValid
from desktop.task_context import bind_task_menu
from desktop.month_motion import DragOpacity,drag_preview,motion_enabled
from desktop.controls import dropdown_colors

MIME='application/x-learning-compass-task'


class MovePreviewDialog(ToolDialog):
    def __init__(self,store,task_id,target,parent=None):
        super().__init__(store,'确认拖动改期','拖动只生成预览。确认前不改日期，也不移动其他任务或改变学习记录。',parent,chapter='schedule')
        self.task_id=task_id;self.proposal=None
        self.due=date_edit(QDate(target.year,target.month,target.day));self.box.addWidget(row(text('新的安排日期'),self.due))
        self.overload=QCheckBox('允许跨计划总预算超载（应用时仍需二次确认）');self.box.addWidget(self.overload)
        self.output=QTextBrowser();self.box.addWidget(self.output,1);self.box.addWidget(self.error)
        self.apply=action('确认改期',self.accept,True)
        self.box.addWidget(row(action('取消，保留原日期',self.reject),action('重新预览',self.refresh),self.apply))
        self.due.dateChanged.connect(self.refresh);self.overload.toggled.connect(self.refresh);self.refresh()

    def refresh(self,*_):
        self.proposal=None;self.apply.setEnabled(False);self.output.clear()
        def run():
            p=preview(self.store,[self.task_id],self.due.date().toPython(),exact_date=True,manual_override=True,allow_overload=self.overload.isChecked())
            self.proposal=p
            lines=[f"{r['title']}\n{r['before']} → {r['after']} · {r['minutes']} 分钟" for r in p.changes]
            lines += [f"保留：{r['title']} · {r['reason']}" for r in p.protected]
            lines += p.conflicts+p.warnings
            lines += [f"{r['date']} 全部活动任务：{r['total']} / {r['budget'] if r['budget'] is not None else '未设置'} 分钟预算" for r in p.loads]
            lines += [f'计划日历排期延长 {n} 天；学习内容周数不变' for n in p.extensions.values() if n]
            self.output.setPlainText('\n\n'.join(lines) or '日期相同，无需改期。')
            self.apply.setEnabled(bool(p.changes) and not p.conflicts and not p.protected)
        self.attempt(run)

    def accept(self):
        if self.proposal is None or not self.apply.isEnabled():return
        def apply():
            self.store.apply_schedule(self.proposal)
            return True
        if self.attempt(lambda:budget_action(self,self.store,apply)):super().accept()


class DragTaskButton(QPushButton):
    def __init__(self,task,calendar):
        super().__init__(task['task'].title[:10]+('…' if len(task['task'].title)>10 else ''))
        self.task=task;self.calendar=calendar;self.origin=None
        self.task_id=task['id']
        self.drag_opacity=None
        self.setObjectName('monthTask')
        self.setAccessibleName('拖动改期或双击编辑：'+task['task'].title)
        self.clicked.connect(lambda _:calendar.select_day(date.fromisoformat(task['due_date'])))
        bind_task_menu(self,calendar)

    def select(self,tid):
        self.calendar.select_day(date.fromisoformat(self.task['due_date']))

    def mousePressEvent(self,event):
        self.origin=event.position().toPoint() if event.button()==Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.origin is not None and event.buttons() & Qt.MouseButton.LeftButton and (event.position().toPoint()-self.origin).manhattanLength()>=QApplication.startDragDistance():
            self.origin=None
            data=QMimeData();data.setData(MIME,json.dumps(dict(id=self.task['id'],token=token(self.calendar.store))).encode())
            drag=QDrag(self);drag.setMimeData(data)
            pixmap,hotspot=drag_preview(self);drag.setPixmap(pixmap);drag.setHotSpot(hotspot)
            self.setDown(False)
            if self.drag_opacity is None:self.drag_opacity=DragOpacity(self)
            self.drag_opacity.set_dragging(True);self.calendar.drag_source=self
            try:drag.exec(Qt.DropAction.MoveAction)
            finally:
                if isValid(self.calendar):self.calendar.clear_drag_feedback()
                if isValid(self):
                    self.setDown(False);self.drag_opacity.set_dragging(False)
                if isValid(drag):drag.deleteLater()
        else:super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self.calendar.edit_task(self.task['id'])
        event.accept()


class DropDay(QFrame):
    def __init__(self,day,calendar):
        super().__init__();self.day=day;self.calendar=calendar
        self.setObjectName('weekDay');self.setProperty('currentDay',day==date.today())
        self.setMinimumSize(106,108);self.setAcceptDrops(True)
        self.drop_progress=0.0;self.drop_active=False
        self.drop_animation=QVariantAnimation(self);self.drop_animation.setDuration(110)
        self.drop_animation.setEasingCurve(QEasingCurve.Type.OutCubic);self.drop_animation.valueChanged.connect(self.set_drop_progress)

    def set_drop_progress(self,value):
        self.drop_progress=float(value);self.update()

    def set_drop_active(self,active,*,immediate=False):
        self.drop_active=active;self.drop_animation.stop();target=1.0 if active else 0.0
        if immediate or not motion_enabled():self.set_drop_progress(target);return
        self.drop_animation.setStartValue(self.drop_progress);self.drop_animation.setEndValue(target);self.drop_animation.start()

    def paintEvent(self,event):
        super().paintEvent(event)
        if self.drop_progress<=0:return
        colors=dropdown_colors();p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill=QColor(colors['selected']);fill.setAlpha(round(150*self.drop_progress))
        border=QColor(colors['accent']);border.setAlpha(round(255*self.drop_progress))
        p.setBrush(fill);p.setPen(QPen(border,2));p.drawRoundedRect(QRectF(self.rect()).adjusted(2,2,-2,-2),9,9);p.end()

    def accepts_drag(self,event):
        source=event.source()
        return event.mimeData().hasFormat(MIME) and isinstance(source,DragTaskButton) and source.calendar.store is self.calendar.store

    def dragEnterEvent(self,event):
        if self.accepts_drag(event):
            self.set_drop_active(True);event.acceptProposedAction()
        else:event.ignore()

    def dragMoveEvent(self,event):
        if self.accepts_drag(event):
            if not self.drop_active:self.set_drop_active(True)
            event.acceptProposedAction()
        else:self.set_drop_active(False);event.ignore()

    def dragLeaveEvent(self,event):
        self.set_drop_active(False);event.accept()

    def dropEvent(self,event):
        self.set_drop_active(False,immediate=True)
        if not self.accepts_drag(event):return event.ignore()
        try:
            raw=bytes(event.mimeData().data(MIME))
            if len(raw)>4096:raise ValueError('拖动资料无效，请重新拖动')
            data=json.loads(raw)
            if data['id']!=event.source().task_id:raise ValueError('拖动任务已变化，请重新拖动')
            self.calendar.clear_drag_feedback()
            self.calendar.request_move(data['id'],self.day,data['token'])
            event.acceptProposedAction()
        except (ValueError,KeyError,TypeError):event.ignore()


class MonthDialog(ToolDialog):
    def __init__(self,store,edit_task,parent=None):
        super().__init__(store,'月视图','拖动任务到日期会先预览；双击任务打开详情。负荷始终统计全部活动任务，不受计划筛选影响。',parent,chapter='schedule')
        self.resize(1060,850);self.edit_task=edit_task;self.month=date.today().replace(day=1);self.selected_day=date.today()
        self.drag_source=None
        self.heading=text('','brand')
        self.plan=SoftComboBox();self.plan.addItem('全部计划',None);self.plan.addItem('独立任务','__standalone__')
        for p in store.list_plans():self.plan.addItem(p['title'],p['id'])
        self.box.addWidget(row(action('上个月',lambda:self.change_month(-1)),self.heading,action('下个月',lambda:self.change_month(1))))
        self.box.addWidget(row(action('本月',lambda:self.change_month(0)),self.plan))
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame);self.box.addWidget(self.scroll,3)
        self.day_caption=text('','confirmationSection');self.box.addWidget(self.day_caption)
        self.details=tree(['任务','所属计划','预计','状态']);self.details.setMinimumHeight(110);self.box.addWidget(self.details,1)
        bind_task_menu(self.details,self)
        self.details.itemDoubleClicked.connect(lambda item,*_:self.edit_task(item.data(0,Qt.ItemDataRole.UserRole)))
        self.box.addWidget(self.error);self.box.addWidget(row(action('刷新',self.refresh),action('关闭',self.close)))
        self.plan.currentIndexChanged.connect(self.refresh);self.refresh()

    def change_month(self,delta):
        if not delta:self.month=date.today().replace(day=1)
        else:self.month=QDate(self.month.year,self.month.month,1).addMonths(delta).toPython()
        self.refresh()

    def refresh(self,*_):
        self.clear_drag_feedback(cancel=True)
        self.rows=[r for r in self.store.tasks(self.plan.currentData()) if not r['archived']]
        self.heading.setText(f'{self.month:%Y年%m月}')
        self.loads=self.store.capacity();limits=self.store.setting('daily_budget',[None]*7)
        grid_widget=QWidget();grid=QGridLayout(grid_widget);grid.setContentsMargins(0,0,0,0);grid.setSpacing(6)
        for i,caption in enumerate('一二三四五六日'):grid.addWidget(text('周'+caption),0,i)
        first=self.month-timedelta(days=self.month.weekday());self.cells=[]
        for i in range(42):
            day=first+timedelta(days=i);cell=DropDay(day,self);inner=QVBoxLayout(cell);inner.setContentsMargins(6,6,6,6);inner.setSpacing(4)
            choose=QPushButton(f'{day.month}/{day.day}' if day.month!=self.month.month else str(day.day));choose.setAccessibleName(day.isoformat())
            choose.setObjectName('monthDate')
            choose.clicked.connect(lambda _=False,d=day:self.select_day(d));inner.addWidget(choose)
            load=self.loads.get(day.isoformat(),{});limit=limits[day.weekday()]
            caption=f"{load.get('total',0)}分"+(f' / {limit}分' if limit is not None else '')
            if load.get('over'):caption+=' 超预算'
            inner.addWidget(text(caption,'budgetWarning' if load.get('over') else 'muted'))
            daily=organizer.ordered([r for r in self.rows if r['due_date']==day.isoformat()])
            for task in daily[:2]:inner.addWidget(DragTaskButton(task,self))
            if len(daily)>2:
                more=QPushButton(f'另有 {len(daily)-2} 项');more.clicked.connect(lambda _=False,d=day:self.select_day(d));inner.addWidget(more)
            inner.addStretch();grid.addWidget(cell,1+i//7,i%7);self.cells.append(cell)
        old=self.scroll.takeWidget()
        if old:old.deleteLater()
        self.scroll.setWidget(grid_widget);self.select_day(self.selected_day)

    def clear_drag_feedback(self,*,cancel=False):
        source=self.drag_source;self.drag_source=None
        if source is not None and isValid(source):
            if cancel:QDrag.cancel()
            if source.drag_opacity is not None:source.drag_opacity.reset()
            source.setDown(False);source.origin=None
        for cell in getattr(self,'cells',[]):
            if isValid(cell):cell.set_drop_active(False,immediate=True)

    def hideEvent(self,event):
        self.clear_drag_feedback(cancel=True);super().hideEvent(event)

    def select_day(self,day):
        self.selected_day=day;self.details.clear()
        rows=organizer.ordered([r for r in self.rows if r['due_date']==day.isoformat()])
        self.day_caption.setText(f'{day} · {len(rows)} 项任务 · 双击查看详情')
        for r in rows:
            item=QTreeWidgetItem(self.details,[r['task'].title,r['plan_title'],f"{r['task'].duration_minutes} 分钟",('已暂停 · ' if r.get('paused') else '')+r['status']])
            item.setData(0,Qt.ItemDataRole.UserRole,r['id'])

    def request_move(self,tid,day,source_token):
        def run():
            if source_token!=token(self.store):raise ValueError('拖动期间任务、记录或预算已有变化，请刷新后重新拖动')
            if not any(r['id']==tid for r in self.rows):raise ValueError('该任务不在当前筛选结果中，请刷新')
            def changed(result):
                if not isValid(self):return
                if result==QDialog.DialogCode.Accepted:self.selected_day=day
                self.refresh()
            from PySide6.QtWidgets import QDialog
            return open_tool(self,MovePreviewDialog(self.store,tid,day,self),('month_move',tid),changed)
        return self.attempt(run)
