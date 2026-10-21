"""Learning tools reuse the established palette, guarded inputs and confirmations."""
from contextlib import closing
from datetime import date, timedelta
from html import escape
import json

from PySide6.QtCore import Qt, QDate, QRectF, QEvent, QSignalBlocker
from PySide6.QtGui import QKeySequence, QShortcut, QPainter, QColor, QPen
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QFrame, QTreeWidget, QTreeWidgetItem,
    QTextBrowser, QVBoxLayout, QWidget, QHeaderView, QSizePolicy, QTabWidget)
from desktop.controls import SoftComboBox
from desktop.dialogs import date_edit, spin
from desktop.confirmations import ConfirmationDialog
from desktop.guide import open_guide
from desktop.schedule_ui import budget_action
from desktop.ui import readable_document, format_reading_blocks
from desktop.models import new_id
from desktop.scheduling import fingerprint
from desktop import learning_tools as service
from desktop.tool_windows import open_tool
from desktop.task_search import search_tasks, TaskQuery, annotate_match
from desktop.task_style import TaskItemDelegate
from models import DailyTask


def text(value, name='muted'):
    w = QLabel(value)
    w.setTextFormat(Qt.TextFormat.PlainText)
    w.setWordWrap(True)
    w.setObjectName(name)
    return w


def action(title, handler, primary=False):
    w = QPushButton(title)
    w.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    w.setAutoDefault(False)
    if primary:
        w.setObjectName('primary')
    w.clicked.connect(lambda _=False: handler())
    return w


def row(*widgets):
    w = QWidget()
    layout = QHBoxLayout(w)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    for index, widget in enumerate(widgets):
        if index == len(widgets)-1 and isinstance(widget, QPushButton) and (all(isinstance(w, QPushButton) for w in widgets) or isinstance(widgets[0], QLabel)):
            layout.addStretch()
        layout.addWidget(widget)
    return w


def tree(headers):
    w = QTreeWidget()
    w.setHeaderLabels(headers)
    w.setRootIsDecorated(False)
    w.setUniformRowHeights(False)
    w.header().setStretchLastSection(False)
    for i in range(len(headers)):
        w.header().setSectionResizeMode(i, QHeaderView.ResizeMode.Stretch if i == 0 else QHeaderView.ResizeMode.ResizeToContents)
    return w


class ToolDialog(QDialog):
    def __init__(self, store, title, subtitle, parent=None, chapter='tools'):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle(title + ' · 学习罗盘')
        self.resize(920, 720)
        self.setMinimumSize(600, 480)
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(24, 24, 24, 24)
        self.box.setSpacing(14)
        self.box.addWidget(row(text(title, 'brand'), action('使用说明', lambda: open_guide(self, chapter))))
        self.subtitle = text(subtitle)
        self.box.addWidget(self.subtitle)
        self.error = text('', 'budgetWarning')
        self.error.hide()
        self.shortcut = QShortcut(QKeySequence('F1'), self)
        self.shortcut.activated.connect(lambda: open_guide(self, chapter))

    def attempt(self, operation):
        self.error.hide()
        try:
            return operation()
        except (ValueError, TypeError, KeyError) as e:
            from pydantic import ValidationError
            self.error.setText('请检查任务名称（2–80字）、说明、日期和用时。' if isinstance(e, ValidationError) else str(e))
            self.error.show()
            return None

    def scope(self, independent=True):
        field = SoftComboBox()
        field.addItem('全部任务', None)
        if independent:
            field.addItem('仅独立任务', '__standalone__')
        for p in self.store.read('SELECT id,title FROM plans ORDER BY title'):
            field.addItem(p['title'], p['id'])
        return field


class Trend(QWidget):
    """One date per row, a shared minutes scale, and values beside both bars."""
    def __init__(self):
        super().__init__()
        self.days = []
        self.setMinimumWidth(480)
        self.setAccessibleName('每日任务用时对照：计划用时、已记录用时，单位分钟')

    def set_days(self, days):
        self.days = days
        self.setFixedHeight(max(120, 44 + 76 * len(days)))
        self.setAccessibleDescription('\n'.join(f"{d['date']}：{d['total']}项任务，计划{d['expected']}分钟，已记录{d['actual_seconds']/60:g}分钟" for d in days))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = QApplication.instance().property('dropdownColors')
        left, width = 178, max(100, self.width() - 300)
        maximum = max(10, max((max(d['expected'], d['actual_seconds']/60) for d in self.days), default=10))
        maximum = ((maximum + 9) // 10) * 10
        painter.setPen(QColor(colors['muted']))
        painter.drawText(14, 24, '安排日期 / 任务数')
        painter.drawText(left, 24, '0')
        painter.drawText(QRectF(left+width-90, 7, 90, 24), Qt.AlignmentFlag.AlignRight, f'{maximum:g} 分钟')
        for i, d in enumerate(self.days):
            y = 42 + i*76
            painter.fillRect(QRectF(0, y-5, self.width(), 73), QColor(colors['card'] if i%2 == 0 else colors['bg']))
            painter.setPen(QColor(colors['ink']))
            painter.drawText(14, y+18, d['date'][5:] + ' 周' + '一二三四五六日'[date.fromisoformat(d['date']).weekday()])
            painter.setPen(QColor(colors['muted']))
            painter.drawText(14, y+43, f"{d['done']}/{d['total']}项完成" if d['total'] else '没有任务')
            for offset, value, caption in [(0, d['expected'], '计划'), (27, d['actual_seconds']/60, '已记录')]:
                top = y + offset
                painter.setPen(QColor(colors['muted']))
                painter.drawText(121, top+17, caption)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(colors['track']))
                painter.drawRoundedRect(QRectF(left, top+5, width, 12), 6, 6)
                if value:
                    painter.setPen(QPen(QColor(colors['accent']), 1) if offset == 0 else QPen(Qt.PenStyle.NoPen))
                    painter.setBrush(QColor(colors['selected'] if offset == 0 else colors['progress_fill']))
                    painter.drawRoundedRect(QRectF(left, top+5, max(2, value/maximum*width), 12), 5, 5)
                painter.setPen(QColor(colors['ink'] if value else colors['muted']))
                value_text = f'{value:g} 分钟' if value else ('未记用时' if offset else '无安排')
                painter.drawText(QRectF(left+width+12, top, 106, 24), Qt.AlignmentFlag.AlignVCenter, value_text)


class StatisticsDialog(ToolDialog):
    def __init__(self, store, parent=None):
        super().__init__(store, '学习统计', '看看安排了多少任务、现在完成多少，以及这些任务已经记录了多久。', parent)
        self.box.setSpacing(8)
        self.start = date_edit(QDate.currentDate().addDays(-6))
        self.end = date_edit()
        self.plan = self.scope()
        self.archived = QCheckBox('包含归档计划')
        from_label, to_label = text('从'), text('至')
        from_label.setFixedWidth(20)
        to_label.setFixedWidth(20)
        self.box.addWidget(row(from_label, self.start, to_label, self.end, self.plan))
        self.range_picker = SoftComboBox()
        for title, days in [('近7天', 7), ('近30天', 30), ('自选日期', None)]:
            self.range_picker.addItem(title, days)
        self.range_picker.setAccessibleName('统计日期快捷选择')
        self.range_picker.currentIndexChanged.connect(lambda *_: self.period(self.range_picker.currentData()) if self.range_picker.currentData() else None)
        self.box.addWidget(row(self.range_picker, self.archived, action('刷新统计', self.refresh)))
        self.metrics = text('', 'confirmationSection')
        self.box.addWidget(self.metrics)
        self.chart = Trend()
        chart_page = QWidget()
        chart_box = QVBoxLayout(chart_page)
        chart_box.setContentsMargins(0, 12, 0, 0)
        self.chart_caption = text('计划 = 任务预计用时；已记录 = 专注计时 + 人工补记。条越长，用时越多。')
        chart_box.addWidget(self.chart_caption)
        chart_scroll = QScrollArea()
        chart_scroll.setWidgetResizable(True)
        chart_scroll.setFrameShape(QFrame.Shape.NoFrame)
        chart_scroll.setWidget(self.chart)
        chart_box.addWidget(chart_scroll, 1)
        self.chart_note = text('按任务的安排日期归类，不是实际学习发生日。完成但没记用时，也不等于只用了0分钟。向下滚动可看其余日期。')
        chart_box.addWidget(self.chart_note)
        self.tabs = tree(['安排日期', '完成 / 总数', '计划用时（分钟）', '已记录（分钟）'])
        self.summary = QTextBrowser()
        readable_document(self.summary)
        self.output_tabs = QTabWidget()
        self.output_tabs.addTab(chart_page, '每日用时对照')
        self.output_tabs.addTab(self.tabs, '每日明细')
        self.output_tabs.addTab(self.summary, '离线总结')
        self.box.addWidget(self.output_tabs, 1)
        self.box.addWidget(self.error)
        self.box.addWidget(row(action('复制离线总结', self.copy_summary), action('关闭', self.close)))
        self.plan.currentIndexChanged.connect(self.refresh)
        self.archived.toggled.connect(self.refresh)
        self.start.dateChanged.connect(self.refresh)
        self.end.dateChanged.connect(self.refresh)
        self.refresh()

    def period(self, days):
        self.start.blockSignals(True)
        self.end.blockSignals(True)
        self.end.setDate(QDate.currentDate())
        self.start.setDate(QDate.currentDate().addDays(1 - days))
        self.start.blockSignals(False)
        self.end.blockSignals(False)
        self.refresh()

    def changeEvent(self, event):
        super().changeEvent(event)
        if (event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and self.isVisible()
                and not getattr(self, '_closing', False) and self.store.path.exists() and hasattr(self, 'output_tabs')):
            self.refresh()

    def closeEvent(self, event):
        self._closing = True
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'chart_caption'):
            compact = self.height() < 650
            self.subtitle.setVisible(not compact)
            self.chart_caption.setText('计划：预计分钟  /  已记录：专注 + 补记' if compact else '计划 = 任务预计用时；已记录 = 专注计时 + 人工补记。条越长，用时越多。')
            self.chart_note.setText('按任务安排日归类，不是学习发生日。向下滚动查看更多。' if compact else '按任务的安排日期归类，不是实际学习发生日。完成但没记用时，也不等于只用了0分钟。向下滚动可看其余日期。')

    def refresh(self, *_):
        self.report = None
        if self.sender() in (self.start, self.end):
            self.range_picker.blockSignals(True)
            self.range_picker.setCurrentIndex(2)
            self.range_picker.blockSignals(False)
        self.chart.set_days([])
        self.tabs.clear()
        self.summary.clear()
        self.metrics.setText('')
        def run():
            self.report = service.statistics(self.store, self.start.date().toPython(), self.end.date().toPython(), self.plan.currentData(), self.archived.isChecked())
            r = self.report
            self.metrics.setText(f"完成 {r['done']}/{r['total']} 项  ·  逾期 {r['overdue']} 项  ·  计划 {r['expected']} 分钟  ·  已记录 {r['actual_seconds']/60:g} 分钟")
            self.tabs.clear()
            self.chart.set_days(r['days'])
            for d in r['days']:
                QTreeWidgetItem(self.tabs, [d['date'], f"{d['done']} / {d['total']}", str(d['expected']), f"{d['actual_seconds'] / 60:.1f}"])
            self.summary.setPlainText(r['summary'])
            format_reading_blocks(self.summary)
        self.attempt(run)

    def copy_summary(self):
        if self.report:
            QApplication.clipboard().setText(self.report['summary'])


class StepsDialog(ToolDialog):
    def __init__(self, store, task_id, parent=None):
        self.task_id = task_id
        super().__init__(store, '任务分组与执行步骤', '分组是任务的分类，步骤是这项任务内部的小清单。修改后只保存分组和步骤，不会把任务标为完成。', parent)
        self.box.setSpacing(8)
        self.data, self.token = service.task_details(store, task_id)
        task = next(t for t in store.tasks() if t['id'] == task_id)
        self.task_title = text('当前任务：' + task['task'].title, 'brand')
        self.box.addWidget(self.task_title)
        self.task_context = text(f"当前状态：{task['status']}  ·  安排日期：{task['due_date']}  ·  {task['plan_title']}", 'confirmationNote')
        self.box.addWidget(self.task_context)
        self.stage = SoftComboBox()
        self.stage.setEditable(True)
        self.stage.addItem('未分组（留空表示不分组）', '')
        stages = store.read('SELECT DISTINCT stage FROM task_details d JOIN tasks t ON t.id=d.task_id WHERE t.plan_id IS ? AND stage!=? ORDER BY stage', (task['plan_id'], ''))
        for stage in stages:
            self.stage.addItem(stage['stage'], stage['stage'])
        if self.data['stage']:
            self.stage.setCurrentIndex(self.stage.findData(self.data['stage']))
        self.stage.lineEdit().setMaxLength(40)
        self.stage.lineEdit().setPlaceholderText('例如：基础练习；留空表示不分组')
        stage_label = text('任务分组')
        stage_label.setFixedWidth(80)
        self.box.addWidget(row(stage_label, self.stage))
        self.example_hint = text('举例：“运行第一段代码”可拆成“打开示例 → 运行代码 → 修改输入再运行”。\n每步可勾选；共用原任务预计用时，不另外计时。保存不会将任务标为完成。', 'confirmationNote')
        self.box.addWidget(self.example_hint)
        self.steps = tree(['执行步骤（勾选表示做过，双击修改文字）'])
        self.steps.setObjectName('stepChecklist')
        for s in self.data['steps']:
            self.add_item(s)
        self.box.addWidget(self.steps, 1)
        self.new_title = QLineEdit()
        self.new_title.setMaxLength(120)
        self.new_title.setPlaceholderText('写一个能直接行动的小步骤')
        self.box.addWidget(row(self.new_title, action('添加步骤', self.add_step), action('移除选中步骤', self.remove_step)))
        self.box.addWidget(self.error)
        self.box.addWidget(row(action('取消', self.reject), action('保存分组和步骤', self.accept, True)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'example_hint'):
            compact = self.height() < 650
            self.subtitle.setVisible(not compact)
            self.example_hint.setVisible(not compact)
            self.task_context.setToolTip('本窗口只保存分组和步骤，不会把任务标为完成。详细用法请按F1。')

    def add_item(self, s):
        item = QTreeWidgetItem(self.steps, [s['title']])
        item.setData(0, Qt.ItemDataRole.UserRole, s['id'])
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
        item.setCheckState(0, Qt.CheckState.Checked if s['done'] else Qt.CheckState.Unchecked)

    def add_step(self):
        if self.steps.topLevelItemCount() >= 40:
            self.error.setText('每项任务最多40个执行步骤')
            self.error.show()
        elif self.new_title.text().strip():
            self.add_item(dict(id=new_id(), title=self.new_title.text().strip(), done=False))
            self.new_title.clear()

    def remove_step(self):
        item = self.steps.currentItem()
        if item is None:
            return
        box = ConfirmationDialog('移除这个步骤？', item.text(0), self, note='会移除这条步骤及其勾选状态，不影响任务的专注、笔记和完成记录。', confirm='移除步骤')
        if box.exec() == QDialog.DialogCode.Accepted:
            self.steps.takeTopLevelItem(self.steps.indexOfTopLevelItem(item))

    def accept(self):
        def save():
            group = self.stage.currentText().strip()
            if self.stage.currentIndex() == 0 and group == self.stage.itemText(0):
                group = ''
            data = dict(stage=group, steps=[dict(id=item.data(0, Qt.ItemDataRole.UserRole), title=item.text(0).strip(), done=item.checkState(0) == Qt.CheckState.Checked)
                for item in [self.steps.topLevelItem(i) for i in range(self.steps.topLevelItemCount())]])
            service.save_details(self.store, self.task_id, data, self.token)
            super(StepsDialog, self).accept()
        self.attempt(save)


class StructureDialog(ToolDialog):
    def __init__(self, store, plan_id, parent=None):
        self.plan_id = plan_id
        super().__init__(store, '任务分组与执行步骤', '像文件夹一样把多项任务归到同一组；再把某项任务拆成执行步骤。这是可选的整理工具，不会改变日期或增加用时。', parent)
        self.box.addWidget(text(store.get_plan(plan_id)['title'] if plan_id else '独立任务', 'brand'))
        self.example_hint = text('例如：把“运行代码、变量输入”归入“基础练习”，把“制作工具”归入“项目练习”。\n选中一项任务，点击“管理执行步骤”，可添加“阅读示例、运行代码、修改输入”等小步骤。', 'confirmationNote')
        self.box.addWidget(self.example_hint)
        query = TaskQuery()
        self.search, self.search_scope = query.query, query.scope
        self.search.setAccessibleName('搜索分组列表中的任务')
        self.search.setClearButtonEnabled(True)
        self.search.setMaxLength(240)
        self.search.setToolTip('多个关键词用空格分开，结果须同时匹配；支持2026-10-20、2026/10/20等日期格式。只筛选显示，不改任务。')
        self.box.addWidget(query)
        self.search_hint = text('')
        self.box.addWidget(self.search_hint)
        self.tasks = tree(['分组 / 任务', '日期', '任务状态', '步骤进度'])
        self.tasks.setItemDelegate(TaskItemDelegate(self.tasks, status_column=2))
        from desktop.task_context import bind_task_menu
        bind_task_menu(self.tasks,self)
        self.tasks.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tasks.setRootIsDecorated(True)
        self.tasks.itemDoubleClicked.connect(lambda *_: self.edit_steps())
        self.box.addWidget(self.tasks, 1)
        self.selection_hint = text('当前未选择任务；点击一项可选中，双击一项可管理执行步骤。')
        self.tasks.itemSelectionChanged.connect(self.show_selection)
        self.box.addWidget(self.selection_hint)
        self.stage = QLineEdit()
        self.stage.setMaxLength(40)
        self.stage.setPlaceholderText('输入分组名，例如：基础练习；留空可取消分组')
        self.box.addWidget(row(self.stage, action('分配给选中任务', self.assign)))
        self.box.addWidget(self.error)
        self.box.addWidget(row(action('管理执行步骤', self.edit_steps), action('刷新', self.refresh), action('关闭', self.close)))
        query.changed.connect(self.refresh)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'example_hint'):
            compact = self.height() < 650
            self.subtitle.setVisible(not compact)
            self.example_hint.setVisible(not compact)

    def refresh(self, *_):
        selected = set(self.selected())
        blocker = QSignalBlocker(self.tasks)
        self.tasks.clear()
        with closing(self.store.connect()) as c:
            c.execute('BEGIN')
            self.token = fingerprint(c)
            groups = {}
            rows = [dict(r) for r in c.execute('SELECT * FROM tasks WHERE plan_id IS ? ORDER BY due_date,week,day,id', (self.plan_id,))]
            plan = c.execute('SELECT title FROM plans WHERE id=?', (self.plan_id,)).fetchone() if self.plan_id else None
            plan_title = plan['title'] if plan else ('独立任务' if self.plan_id is None else '')
            candidates = [dict(t, task=DailyTask.model_validate_json(t['payload_json']), plan_title=plan_title) for t in rows]
            visible = search_tasks(candidates, self.search.text(), scope=self.search_scope.currentData())
            for t in visible:
                d = service.details_in(c, t['id'])
                groups.setdefault(d['stage'] or '未分组', []).append((t, d))
            for stage, values in groups.items():
                done = sum(t['status'] == '已完成' for t, _ in values)
                parent = QTreeWidgetItem(self.tasks, [f'{stage} · 任务 {done}/{len(values)} 项已完成'])
                parent.setFlags(parent.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                for t, d in values:
                    item = QTreeWidgetItem(parent, [json.loads(t['payload_json'])['title'], t['due_date'], t['status'], f"{sum(s['done'] for s in d['steps'])}/{len(d['steps'])} 步" if d['steps'] else '未添加步骤'])
                    item.setData(0, Qt.ItemDataRole.UserRole, t['id'])
                    annotate_match(item, t, self.search.text(), self.search_scope.currentData(), hint='双击任务管理执行步骤')
                    item.setSelected(t['id'] in selected)
                parent.setExpanded(True)
        blocker.unblock()
        self.show_selection()
        self.search_hint.setText(f'显示 {len(visible)}/{len(rows)} 项 · 双击任务管理执行步骤；搜索后只对可见的选中任务操作。'
                                 if visible else ('没有匹配的任务，请清除关键词或换个词。' if rows else '这里还没有任务。'))

    def show_selection(self):
        names = [item.text(0) for item in self.tasks.selectedItems() if item.data(0, Qt.ItemDataRole.UserRole)]
        selected = '、'.join(names[:2]) + (f' 等{len(names)}项' if len(names)>2 else '')
        self.selection_hint.setText('当前选择：' + (selected if names else '未选择具体任务')
                                    + ('（可批量分组；管理步骤需只选一项）' if len(names) > 1 else ''))
        self.selection_hint.setToolTip('\n'.join(names))

    def selected(self):
        return [item.data(0, Qt.ItemDataRole.UserRole) for item in self.tasks.selectedItems() if item.data(0, Qt.ItemDataRole.UserRole)]

    def assign(self):
        def run():
            service.assign_stage(self.store, self.selected(), self.stage.text().strip(), self.token)
            self.refresh()
        self.attempt(run)

    def edit_steps(self):
        ids = self.selected()
        if len(ids) == 1:
            from shiboken6 import isValid
            self.attempt(lambda: open_tool(self, StepsDialog(self.store, ids[0], self), ('steps', ids[0]), lambda _: self.refresh() if isValid(self) else None))
        else:
            self.error.setText('请先选中一项具体任务；分组标题不是任务。批量分组则可以同时选中多项任务。')
            self.error.show()


class RecurringDialog(ToolDialog):
    def __init__(self, store, parent=None):
        super().__init__(store, '周期任务与复习', '先限定日期，再预览并保存每一次任务。每次独立打卡；漏做不会自动堆到下一次，程序不在后台无限生成。', parent)
        self.proposal = None
        form = QFormLayout()
        self.plan = SoftComboBox()
        self.plan.addItem('独立任务', None)
        for p in store.list_plans():
            self.plan.addItem(p['title'], p['id'])
        self.title = QLineEdit()
        self.title.setMaxLength(80)
        self.title.setPlaceholderText('例如：复习本周英语生词')
        self.description = QLineEdit()
        self.description.setMaxLength(300)
        self.description.setPlaceholderText('可选：每次要做哪些步骤？')
        self.deliverable = QLineEdit()
        self.deliverable.setMaxLength(160)
        self.deliverable.setPlaceholderText('可选：怎样算完成？')
        self.duration = spin(5, 240, 20)
        self.duration.setSuffix(' 分钟')
        self.kind = SoftComboBox()
        for title, code in [('每天一次', 'daily'), ('每周选定的星期', 'weekly'), ('间隔复习（相对开始日期）', 'review')]:
            self.kind.addItem(title, code)
        self.start, self.end = date_edit(), date_edit(QDate.currentDate().addDays(27))
        self.weekdays = []
        days_box = QWidget()
        dl = QHBoxLayout(days_box)
        dl.setContentsMargins(0, 0, 0, 0)
        for i, title in enumerate('一二三四五六日'):
            check = QCheckBox(title)
            check.setChecked(i < 5)
            dl.addWidget(check)
            self.weekdays.append(check)
        self.intervals = QLineEdit('1,3,7,14')
        self.intervals.setMaxLength(180)
        self.intervals.setPlaceholderText('开始日期之后第几天；0表示开始当天，例如1,3,7,14')
        self.reminder = QCheckBox('每次安排日20:00提醒（保存后可逐项修改）')
        for label, w in [('放在哪里', self.plan), ('任务名称', self.title), ('具体说明', self.description), ('完成标准', self.deliverable), ('每次预计', self.duration), ('如何重复', self.kind),
                         ('开始日期', self.start), ('结束日期（包含当天）', self.end), ('星期（每周模式）', days_box), ('间隔天数（复习模式）', self.intervals), ('提醒', self.reminder)]:
            form.addRow(label, w)
        content = QWidget()
        content.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        self.box.addWidget(scroll, 2)
        self.preview = tree(['日期', '预计分钟', '全部活动任务 / 预算'])
        self.box.addWidget(self.preview, 1)
        self.preview_note = text('尚未预览。每条规则最长一年；保存后可单独编辑每次任务。')
        self.box.addWidget(self.preview_note)
        self.box.addWidget(self.error)
        self.save_button = action('保存这些任务', self.save, True)
        self.save_button.setEnabled(False)
        self.box.addWidget(row(action('已保存的周期', self.manage), action('查看预览', self.make_preview), self.save_button, action('关闭', self.close)))
        for field in (self.title, self.description, self.deliverable, self.intervals):
            field.textChanged.connect(self.invalidate)
        for field in (self.plan, self.kind):
            field.currentIndexChanged.connect(self.invalidate)
        for field in (self.start, self.end):
            field.dateChanged.connect(self.invalidate)
        self.duration.valueChanged.connect(self.invalidate)
        self.reminder.toggled.connect(self.invalidate)
        for check in self.weekdays:
            check.toggled.connect(self.invalidate)
        self.kind.currentIndexChanged.connect(lambda *_: self.mode())
        self.mode()

    def mode(self):
        for check in self.weekdays:
            check.setEnabled(self.kind.currentData() == 'weekly')
        self.intervals.setEnabled(self.kind.currentData() == 'review')

    def invalidate(self, *_):
        self.proposal = None
        self.save_button.setEnabled(False)
        self.preview.clear()
        self.preview_note.setText('资料已变化，请重新查看预览。')

    def make_preview(self):
        def run():
            try:
                offsets = [int(x.strip()) for x in self.intervals.text().replace('，', ',').split(',') if x.strip()] if self.kind.currentData() == 'review' else []
            except ValueError:
                raise ValueError('间隔天数请用逗号分隔的整数填写，例如1,3,7,14') from None
            rule = dict(kind=self.kind.currentData(), start=self.start.date().toString('yyyy-MM-dd'), end=self.end.date().toString('yyyy-MM-dd'),
                weekdays=[i for i, w in enumerate(self.weekdays) if w.isChecked()], intervals=offsets)
            title = self.title.text().strip()
            payload = DailyTask(day=1, title=title, description=self.description.text().strip() or '按本次目标完成：' + title,
                deliverable=self.deliverable.text().strip() or '完成后自行确认', duration_minutes=self.duration.value())
            p = service.preview_series(self.store, payload, rule, plan_id=self.plan.currentData(), remind_at='20:00' if self.reminder.isChecked() else None)
            self.preview.clear()
            loads = {r['date']: r for r in p.loads}
            for day in p.dates:
                r = loads.get(day)
                QTreeWidgetItem(self.preview, [day, str(payload.duration_minutes), f"{r['total']} / {r['budget'] if r['budget'] is not None else '未设置'}" if r else '暂停/归档计划不计活动负荷'])
            self.proposal = p
            self.preview_note.setText(f'将创建{len(p.dates)}次，共{len(p.dates) * payload.duration_minutes}分钟；容量超限会在保存时逐日/逐周二次确认。预览期间数据变化需重看。')
            self.save_button.setEnabled(True)
        self.attempt(run)

    def save(self):
        if self.proposal is None:
            return
        def run():
            result = budget_action(self, self.store, lambda: service.apply_series(self.store, self.proposal))
            if result:
                self.invalidate()
                self.preview_note.setText('已保存；每次任务在今日或周视图查看，不会再次重复生成。')
        self.attempt(run)

    def manage(self):
        from shiboken6 import isValid
        self.attempt(lambda: open_tool(self, SeriesListDialog(self.store, self), 'series-list', lambda _: self.invalidate() if isValid(self) else None))


class SeriesListDialog(ToolDialog):
    def __init__(self, store, parent=None):
        super().__init__(store, '已保存的周期', '修改规则时，先停止旧规则，再创建新的日期预览。已保存实例也可用调整排期批量改日。', parent)
        self.list = tree(['名称 / 所属计划', '日期范围', '状态'])
        self.box.addWidget(self.list, 1)
        self.box.addWidget(self.error)
        self.box.addWidget(row(action('停止后续安排…', self.stop), action('关闭', self.close)))
        self.refresh()

    def refresh(self):
        self.list.clear()
        for r in service.series_entries(self.store):
            rule = json.loads(r['rule_json'])
            item = QTreeWidgetItem(self.list, [json.loads(r['payload_json'])['title'] + ' · ' + r['plan_title'], rule['start'] + ' — ' + rule['end'], '已停止' if r['stopped'] else '已生成至结束日'])
            item.setData(0, Qt.ItemDataRole.UserRole, r['id'])

    def stop(self):
        item = self.list.currentItem()
        if not item:
            return
        def run():
            sid = item.data(0, Qt.ItemDataRole.UserRole)
            with closing(self.store.connect()) as c:
                c.execute('BEGIN')
                token = fingerprint(c)
                day = date.today().isoformat()
                remove, keep = service.stop_candidates(c, sid, day)
            box = ConfirmationDialog('停止后续安排？', item.text(0), self, note='只移除今天起未开始、没有学习记录且不在专注中的实例；过去任务和有记录的任务保留。', confirm='停止并移除可取消的实例')
            box.content.addWidget(text(f'将移除 {len(remove)} 项，保留 {len(keep)} 项已有记录或正在专注的任务。', 'brand'))
            for t in remove:
                box.content.addWidget(text(t['due_date'] + ' · ' + json.loads(t['payload_json'])['title']))
            for t in keep:
                box.content.addWidget(text('保留：' + t['due_date'] + ' · 已有记录/正在专注'))
            if box.exec() == QDialog.DialogCode.Accepted:
                service.stop_series(self.store, sid, token, day)
                self.refresh()
        self.attempt(run)
