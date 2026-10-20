"""Manual tasks share the normal task ID, focus, records and reminder pipeline."""
from PySide6.QtCore import QDate, QTime
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QLabel, QLineEdit,
    QPlainTextEdit, QScrollArea, QFrame, QVBoxLayout, QWidget)

from desktop.controls import SoftComboBox, ArrowWheelTimeEdit
from desktop.dialogs import buttons, date_edit, spin
from desktop.schedule_ui import budget_action
from models import DailyTask


class ManualTaskDialog(QDialog):
    def __init__(self, store, parent=None, *, plan_id=None):
        super().__init__(parent)
        self.store, self.task_id = store, None
        self.setWindowTitle("添加自己的任务 · 学习罗盘")
        self.resize(650, 680)
        box = QVBoxLayout(self)
        heading = QLabel("把想做的事，放到合适的一天")
        heading.setObjectName("brand")
        box.addWidget(heading)
        tip = QLabel("无需模型。可以加入已有计划，也可以作为独立任务保存。加入计划后会计入该计划的时间上限，但不改写 AI 生成的学习周次。")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        box.addWidget(tip)
        from PySide6.QtWidgets import QPushButton
        from desktop.guide import open_guide
        help_button = QPushButton("查看手动任务说明")
        help_button.clicked.connect(lambda: open_guide(self, "modify"))
        box.addWidget(help_button)
        form = QFormLayout()
        self.plan = SoftComboBox()
        self.plan.addItem("独立任务（不属于任何计划）", None)
        for p in store.list_plans():
            self.plan.addItem(p["title"], p["id"])
        self.plan.setCurrentIndex(max(0, self.plan.findData(plan_id)))
        self.title = QLineEdit()
        self.title.setMaxLength(80)
        self.title.setPlaceholderText("例如：复习今天的英语生词")
        self.description = QPlainTextEdit()
        self.description.setMinimumHeight(80)
        self.description.setMaximumHeight(110)
        self.description.setPlaceholderText("可选：写下具体步骤")
        self.deliverable = QLineEdit()
        self.deliverable.setMaxLength(160)
        self.deliverable.setPlaceholderText("可选：怎样算完成？")
        self.due = date_edit()
        self.duration = spin(5, 240, 25)
        self.duration.setSuffix(" 分钟")
        self.reminder = QCheckBox("在安排日期提醒我")
        self.time = ArrowWheelTimeEdit(QTime(20, 0))
        self.time.setDisplayFormat("HH:mm")
        self.time.setEnabled(False)
        self.reminder.toggled.connect(self.time.setEnabled)
        for title, field in (("放在哪里", self.plan), ("任务名称", self.title), ("具体说明", self.description),
                ("完成标准", self.deliverable), ("安排日期", self.due), ("预计用时", self.duration), ("提醒", self.reminder), ("提醒时间", self.time)):
            form.addRow(title, field)
        fields = QWidget()
        fields.setLayout(form)
        form.setContentsMargins(0, 0, 12, 0)
        form.setVerticalSpacing(12)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(fields)
        box.addWidget(scroll, 1)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setObjectName("budgetWarning")
        self.error.setVisible(False)
        box.addWidget(self.error)
        buttons(self, box, "添加任务")

    def accept(self):
        self.error.setVisible(False)
        try:
            title = self.title.text().strip()
            payload = DailyTask(day=1, title=title,
                description=self.description.toPlainText().strip() or f"完成任务：{title}",
                deliverable=self.deliverable.text().strip() or "完成后自行确认",
                duration_minutes=self.duration.value())
            task_id = budget_action(self, self.store, lambda: self.store.add_manual_task(payload,
                self.due.date().toString("yyyy-MM-dd"), plan_id=self.plan.currentData(),
                remind_at=self.time.time().toString("HH:mm") if self.reminder.isChecked() else None, confirm_limits=True))
            if task_id:
                self.task_id = task_id
                super().accept()
        except ValueError as error:
            self.error.setVisible(True)
            self.error.setText("请检查名称（2–80字）、说明（最多300字）、完成标准（最多160字）和日期。\n" + str(error))
