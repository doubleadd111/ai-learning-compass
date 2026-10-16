"""Date-only weekly planner: no invented start times or cross-plan capacity."""
from datetime import date, timedelta
from html import escape

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QHeaderView,
    QLabel, QPushButton, QScrollArea, QSizePolicy, QStackedWidget, QTextBrowser,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from desktop.models import DesktopProfile
from desktop.controls import SoftComboBox as QComboBox
from desktop.task_status import overdue_days, display_status, matches_status
from desktop.task_style import TaskItemDelegate, mark_overdue


def text(value, muted=False):
    label = QLabel(value)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if muted:
        label.setObjectName("muted")
    return label


def focus_button(row, callback):
    button = QPushButton("已完成" if row["status"] == "已完成" else "开始专注")
    button.setObjectName("taskFocus")
    button.setMaximumWidth(104)
    button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    button.setAccessibleName(f"开始专注：{row['task'].title}")
    button.setEnabled(not row["archived"] and row["status"] != "已完成")
    button.clicked.connect(lambda _checked=False, task_id=row["id"]: callback(task_id))
    return button


def status_label(value):
    label = text(value)
    label.setObjectName("taskStatus")
    label.setProperty("taskState", value)
    return label


def attach_focus_button(tree, item, column, row, callback):
    # QTreeWidget doesn't reliably include an index widget in ResizeToContents.
    item.setSizeHint(column, QSize(112, 44))
    tree.setItemWidget(item, column, focus_button(row, callback))


class TaskCard(QFrame):
    def __init__(self, row, select, edit, focus, parent=None, today=None):
        super().__init__(parent)
        self.task_id = row["id"]
        self.select, self.edit = select, edit
        self.setObjectName("weekTask")
        days = overdue_days(row, today)
        self.setProperty("overdue", bool(days))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        title = row["task"].title
        if days:
            badge = text(f"逾期 {days} 天")
            badge.setObjectName("overdueBadge")
            badge.setWordWrap(False)
            layout.addWidget(badge, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(text(title[:32] + ("…" if len(title) > 32 else "")))
        plan_title = row["plan_title"]
        layout.addWidget(text(plan_title[:20] + ("…" if len(plan_title) > 20 else ""), True))
        layout.addWidget(text(f"{row['task'].duration_minutes} 分钟", True))
        layout.addWidget(status_label(row["status"]))
        layout.addWidget(focus_button(row, focus))
        self.setToolTip(f"{title}\n{row['task'].description}\n产出：{row['task'].deliverable}")

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.select(self.task_id)
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.edit(self.task_id)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.select(self.task_id)
            event.accept()
        else:
            super().keyPressEvent(event)


class WeekPanel(QWidget):
    def __init__(self, store, start_focus, edit_task, parent=None):
        super().__init__(parent)
        self.store, self.start_focus, self.edit_task = store, start_focus, edit_task
        self.start = date.today() - timedelta(days=date.today().weekday())
        self.selected_id = None
        self.rows, self.cards = [], []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        filters = QGridLayout()
        self.plan_filter, self.subject_filter, self.status_filter = (QComboBox() for _ in range(3))
        self.plan_filter.setAccessibleName("按计划筛选周任务")
        self.subject_filter.setAccessibleName("按科目筛选周任务")
        self.status_filter.setAccessibleName("按状态筛选周任务")
        self.status_filter.addItems(["全部状态", "未开始", "进行中", "延期", "逾期", "已完成"])
        self.status_filter.setToolTip("逾期按今天自动判断：日期早于今天且尚未完成。原完成状态仍保留；只筛选当前显示周。")
        for column, (title, widget) in enumerate((("计划", self.plan_filter), ("科目", self.subject_filter), ("状态", self.status_filter))):
            filters.addWidget(text(title, True), 0, column)
            filters.addWidget(widget, 1, column)
            widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            widget.setMinimumContentsLength(8)
            widget.currentIndexChanged.connect(self.render)
        self.mode = QComboBox()
        self.mode.addItems(["七日周历", "明细表"])
        self.mode.setCurrentIndex(store.setting("week_layout", 0))
        filters.addWidget(text("视图", True), 0, 3)
        filters.addWidget(self.mode, 1, 3)
        layout.addLayout(filters)
        self.summary = text("", True)
        layout.addWidget(self.summary)
        self.views = QStackedWidget()
        self.calendar = QScrollArea()
        self.calendar.setWidgetResizable(True)
        self.calendar.setFrameShape(QFrame.Shape.NoFrame)
        self.views.addWidget(self.calendar)
        self.table = QTreeWidget()
        self.table.setRootIsDecorated(False)
        self.table.setAlternatingRowColors(True)
        self.table.setHeaderLabels(["任务", "日期", "所属计划", "预计", "累计实际", "状态", "操作"])
        self.table.setItemDelegate(TaskItemDelegate(self.table, status_column=5))
        self.table.header().setStretchLastSection(False)
        self.table.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 7):
            self.table.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self.select_table)
        self.table.itemDoubleClicked.connect(lambda item, *_: edit_task(item.data(0, Qt.ItemDataRole.UserRole)))
        self.views.addWidget(self.table)
        self.views.setCurrentIndex(self.mode.currentIndex())
        self.mode.currentIndexChanged.connect(self.change_mode)
        layout.addWidget(self.views, 1)
        self.detail = QTextBrowser()
        self.detail.setMinimumHeight(92)
        self.detail.setMaximumHeight(130)
        layout.addWidget(self.detail)
        self.edit_button = QPushButton("记录／编辑选中任务")
        self.edit_button.clicked.connect(lambda: edit_task(self.selected_id) if self.selected_id else None)
        layout.addWidget(self.edit_button, 0, Qt.AlignmentFlag.AlignLeft)

    def change_mode(self, index):
        self.views.setCurrentIndex(index)
        self.store.set_setting("week_layout", index)

    @staticmethod
    def fill_filter(combo, items, all_title):
        selected = combo.currentData()
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(all_title, None)
        for title, value in items:
            combo.addItem(title, value)
        combo.setCurrentIndex(max(0, combo.findData(selected)))
        combo.blockSignals(False)

    def refresh(self, start=None):
        if start is not None:
            self.start = start
        plans = self.store.list_plans()
        self.subjects = {p["id"]: DesktopProfile.model_validate_json(p["profile_json"]).subject for p in plans}
        self.fill_filter(self.plan_filter, [(p["title"], p["id"]) for p in plans], "全部计划")
        self.fill_filter(self.subject_filter, [(s, s) for s in sorted(set(self.subjects.values()))], "全部科目")
        self.all_rows = [t for t in self.store.tasks() if not t["archived"] and self.start.isoformat() <= t["due_date"] <= (self.start + timedelta(days=6)).isoformat()]
        self.render()

    def render(self, *_):
        if not hasattr(self, "all_rows"):
            return
        plan, subject, status = self.plan_filter.currentData(), self.subject_filter.currentData(), self.status_filter.currentText()
        today = date.today()
        self.rows = [r for r in self.all_rows if (not plan or r["plan_id"] == plan) and
                     (not subject or self.subjects[r["plan_id"]] == subject) and matches_status(r, status, today)]
        done = sum(r["status"] == "已完成" for r in self.rows)
        expected = sum(r["task"].duration_minutes for r in self.rows)
        actual = sum(r["manual_seconds"] + r["focus_seconds"] for r in self.rows) // 60
        late = sum(overdue_days(r, today) > 0 for r in self.rows)
        self.summary.setText(f"筛选后：{len(self.rows)} 项 · 已完成 {done} 项 · 逾期 {late} 项 · 预计 {expected} 分钟 · 任务累计实际 {actual} 分钟（按本周排期归属）")
        self.table.blockSignals(True)
        self.table.clear()
        for row in self.rows:
            item = QTreeWidgetItem([row["task"].title, row["due_date"], row["plan_title"], f"{row['task'].duration_minutes} 分钟",
                                    f"{(row['manual_seconds'] + row['focus_seconds']) // 60} 分钟", display_status(row, today), ""])
            item.setData(0, Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(0, row["task"].description)
            mark_overdue(item, row, 7, today)
            self.table.addTopLevelItem(item)
            attach_focus_button(self.table, item, 6, row, self.start_focus)
            if row["id"] == self.selected_id:
                self.table.setCurrentItem(item)
        self.table.blockSignals(False)
        contents = QWidget()
        box = QHBoxLayout(contents)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        self.cards = []
        for offset in range(7):
            day = self.start + timedelta(days=offset)
            column = QFrame()
            column.setObjectName("weekDay")
            column.setProperty("currentDay", day == date.today())
            column.setMinimumWidth(118)
            inner = QVBoxLayout(column)
            inner.setContentsMargins(8, 12, 8, 10)
            inner.setSpacing(10)
            daily = [r for r in self.rows if r["due_date"] == day.isoformat()]
            inner.addWidget(text(f"{'今天 · ' if day == date.today() else ''}周{'一二三四五六日'[offset]}\n{day:%Y-%m-%d}"))
            inner.addWidget(text(f"{sum(r['status']=='已完成' for r in daily)}/{len(daily)}项完成\n预计 {sum(r['task'].duration_minutes for r in daily)} 分钟", True))
            for row in daily:
                card = TaskCard(row, self.select_task, self.edit_task, self.start_focus, today=today)
                self.cards.append(card)
                inner.addWidget(card)
            if not daily:
                inner.addWidget(text("无安排", True))
            inner.addStretch()
            box.addWidget(column, 1)
        old = self.calendar.takeWidget()
        if old:
            old.hide()
            old.deleteLater()
        self.calendar.setWidget(contents)
        if self.selected_id not in {r["id"] for r in self.rows}:
            self.selected_id = None
        self.select_task(self.selected_id)

    def select_table(self):
        item = self.table.currentItem()
        self.select_task(item.data(0, Qt.ItemDataRole.UserRole) if item else None)

    def select_task(self, task_id):
        self.selected_id = task_id
        row = next((r for r in self.rows if r["id"] == task_id), None)
        for card in self.cards:
            card.setProperty("selected", card.task_id == task_id)
            card.style().unpolish(card)
            card.style().polish(card)
        self.edit_button.setEnabled(row is not None)
        if row:
            task = row["task"]
            days = overdue_days(row, date.today())
            late = f" · 逾期 {days} 天" if days else ""
            self.detail.setHtml(f"<b>{escape(task.title)}</b> · {escape(row['due_date'])}{late}<br>{escape(task.description).replace(chr(10), '<br>')}<br><b>产出：</b>{escape(task.deliverable)}")
            self.table.blockSignals(True)
            for index in range(self.table.topLevelItemCount()):
                item = self.table.topLevelItem(index)
                if item.data(0, Qt.ItemDataRole.UserRole) == task_id:
                    self.table.setCurrentItem(item)
            self.table.blockSignals(False)
        else:
            self.detail.setPlainText("选择任务查看具体做法与产出；双击任务可编辑。没有任务时可切换周次或调整筛选。")
