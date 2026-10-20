"""Shared calm confirmation layout; content scrolls while actions stay visible."""
from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                              QPushButton, QScrollArea, QVBoxLayout, QWidget)


def text_label(text, name=None):
    label = QLabel(str(text))
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    if name:
        label.setObjectName(name)
    return label


class ConfirmationDialog(QDialog):
    def __init__(self, title, description, parent=None, *, note="", confirm="确认继续", cancel="取消"):
        super().__init__(parent)
        self.setWindowTitle(title + " · 学习罗盘")
        self.resize(650, 590)
        self.setMinimumSize(480, 420)
        box = QVBoxLayout(self)
        box.setContentsMargins(24, 24, 24, 24)
        box.setSpacing(16)
        box.addWidget(text_label(title, "brand"))
        self.summary = text_label(description, "muted")
        box.addWidget(self.summary)
        self.scroll = QScrollArea()
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self.content = QVBoxLayout(content)
        self.content.setContentsMargins(0, 0, 10, 0)
        self.content.setSpacing(12)
        self.scroll.setWidget(content)
        box.addWidget(self.scroll, 1)
        if note:
            box.addWidget(text_label(note, "confirmationNote"))
        actions = QHBoxLayout()
        actions.setSpacing(10)
        actions.addStretch()
        self.cancel_button = QPushButton(cancel)
        self.confirm_button = QPushButton(confirm)
        self.confirm_button.setObjectName("primary")
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button.clicked.connect(self.accept)
        self.confirm_button.setAutoDefault(False)
        self.cancel_button.setDefault(True)
        self.cancel_button.setFocus()
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.confirm_button)
        box.addLayout(actions)


class DeletePlanDialog(ConfirmationDialog):
    PHRASE = "确认删除"

    def __init__(self, title, task_count, completed_count, parent=None, *, blocking_reason=""):
        super().__init__("删除这份计划？", "这会删除该计划、全部任务，以及关联的学习记录、提醒和历史。", parent,
            note="只是暂时不学？建议返回选择“归档”，记录会保留。删除前会另存一份本机备份；备份失败时不会删除。",
            confirm="删除这份计划", cancel="取消，保留计划")
        self.resize(620, 550)
        self.confirmed = False
        self.blocking_reason = blocking_reason
        card = QFrame()
        card.setObjectName("confirmationCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(12)
        box.addWidget(text_label(title, "confirmationSection"))
        box.addWidget(text_label(f"共 {task_count} 项任务 · 已完成 {completed_count} 项", "muted"))
        box.addWidget(text_label("不会删除其他计划、独立任务或模型配置。", "muted"))
        self.content.addWidget(card)
        prompt = text_label("请输入“确认删除”四个字，再点击下方删除按钮。")
        self.content.addWidget(prompt)
        self.phrase = QLineEdit()
        self.phrase.setPlaceholderText(self.PHRASE)
        self.phrase.setAccessibleName("删除确认文字")
        prompt.setBuddy(self.phrase)
        self.content.addWidget(self.phrase)
        if blocking_reason:
            self.content.addWidget(text_label(blocking_reason, "confirmationNote"))
        self.content.addStretch()
        self.confirm_button.setObjectName("danger")
        self.confirm_button.setEnabled(False)
        self.phrase.textChanged.connect(lambda text: self.confirm_button.setEnabled(
            text == self.PHRASE and not self.blocking_reason))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.reject()
            return
        super().keyPressEvent(event)

    def accept(self):
        if self.phrase.text() != self.PHRASE or self.blocking_reason:
            return
        self.confirmed = True
        super().accept()


class CapacityConfirmDialog(ConfirmationDialog):
    def __init__(self, error, parent=None):
        from desktop.scheduling import PlanCapacityExceeded
        plan_rows = error.rows if isinstance(error, PlanCapacityExceeded) else []
        global_rows = error.global_rows if plan_rows else error.rows
        count = len(plan_rows) + len(global_rows)
        super().__init__("这次安排超出时间上限", f"有 {count} 处超限。本次更改尚未保存，请核对后决定是否保留。", parent,
            note="只保留这次安排，不修改原时间上限。AI 和自动排期仍按原上限安排。",
            confirm="仍然保留安排", cancel="返回调整")
        self.details = error.details
        self.cards = []
        if plan_rows:
            self.content.addWidget(text_label("这份计划的时间上限", "confirmationSection"))
            for row in sorted(plan_rows, key=lambda r: (r["period"], r["kind"] != "日", r["title"])):
                period = row["period"]
                if row["kind"] == "周":
                    period += " 至 " + (date.fromisoformat(period) + timedelta(days=6)).isoformat()
                self.add_card(row["title"], period + (" · 每周" if row["kind"] == "周" else " · 每日"),
                              row["total"], row["limit"], row["over"])
        if global_rows:
            self.content.addWidget(text_label("全部活动计划与独立任务的每日总预算", "confirmationSection"))
            for row in global_rows:
                allocation = "\n".join(f"{p['title']}：{p['minutes']} 分钟" for p in row["plans"].values())
                self.add_card("每日总预算", row["date"], row["total"], row["budget"], row["over"], allocation)
        self.content.addStretch()

    def add_card(self, plan, period, total, limit, over, allocation=""):
        card = QFrame()
        card.setObjectName("confirmationCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 14, 16, 14)
        box.setSpacing(10)
        box.addWidget(text_label(plan, "confirmationSection"))
        box.addWidget(text_label(period, "muted"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(3)
        for column, (name, value) in enumerate((("预计安排", total), ("时间上限", limit), ("超出", over))):
            grid.addWidget(text_label(name, "muted"), 0, column)
            grid.addWidget(text_label(f"{value} 分钟", "confirmationOver" if column == 2 else "confirmationValue"), 1, column)
            grid.setColumnStretch(column, 1)
        box.addLayout(grid)
        if allocation:
            box.addWidget(text_label(allocation, "muted"))
        self.content.addWidget(card)
        self.cards.append(card)
