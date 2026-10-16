"""Quiet, responsive presentation of planning stages. No model or persistence."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QFormLayout, QHBoxLayout, QLabel,
                              QPushButton, QSizePolicy, QToolButton, QVBoxLayout)


def label(text, name):
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    return widget


class PlanningSteps(QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("planningSteps")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 8)
        layout.setSpacing(6)
        self.items = []
        for i, text in enumerate(("基础资料", "关键追问", "概略确认", "详细预览", "保存")):
            step = QFrame()
            step.setObjectName("planningStep")
            row = QHBoxLayout(step)
            row.setContentsMargins(8, 7, 8, 7)
            row.setSpacing(7)
            marker = label(str(i + 1), "stepMarker")
            marker.setFixedSize(22, 22)
            marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title = label(text, "stepTitle")
            row.addWidget(marker)
            row.addWidget(title, 1)
            layout.addWidget(step, 1)
            self.items.append((step, marker, title))

    def set_stage(self, index):
        for i, (step, marker, title) in enumerate(self.items):
            state = "current" if i == index else "done" if i < index else "future"
            step.setProperty("stageState", state)
            marker.setText(str(i + 1))
            step.setAccessibleName(f"{title.text()} · {'当前步骤' if i == index else '已通过' if i < index else '待进行'}")
            for widget in (step, marker, title):
                widget.style().unpolish(widget)
                widget.style().polish(widget)
                widget.update()


class OutlineCard(QFrame):
    def __init__(self, number, point, modify):
        super().__init__()
        self.setObjectName("outlineCard")
        self.setProperty("pointId", point.id)
        self.setProperty("expanded", False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(12)
        row = QHBoxLayout()
        row.setSpacing(12)
        marker = label(f"{number:02}", "outlineNumber")
        marker.setAlignment(Qt.AlignmentFlag.AlignCenter)
        marker.setFixedSize(30, 30)
        row.addWidget(marker, 0, Qt.AlignmentFlag.AlignTop)
        self.summary = label(point.summary, "outlineSummary")
        self.summary.setMinimumWidth(0)
        row.addWidget(self.summary, 1)
        self.edit = QPushButton("修改")
        self.edit.setObjectName("outlineEdit")
        self.edit.setAccessibleName(f"修改阶段{number}：{point.summary}")
        self.edit.clicked.connect(lambda _checked=False: modify(point.id))
        row.addWidget(self.edit, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)
        self.toggle = QToolButton()
        self.toggle.setObjectName("outlineToggle")
        self.toggle.setCheckable(True)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.toggle.setText("查看安排与产出")
        self.toggle.setAccessibleName(f"展开阶段{number}的安排与产出")
        layout.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.details = QFrame()
        self.details.setObjectName("outlineDetails")
        form = QFormLayout(self.details)
        form.setContentsMargins(14, 12, 14, 12)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for name, text in (("阶段目标", point.objective), ("主要内容", point.content),
                           ("预期产出", point.deliverable), ("安排依据", point.rationale)):
            form.addRow(label(name, "outlineDetailLabel"), label(text, "outlineDetailText"))
        layout.addWidget(self.details)
        self.details.hide()
        self.toggle.toggled.connect(self.expand)

    def expand(self, checked):
        self.details.setVisible(checked)
        self.toggle.setText("收起详情" if checked else "查看安排与产出")
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
        self.setProperty("expanded", checked)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()


def assumptions_card(assumptions):
    card = QFrame()
    card.setObjectName("outlineAssumptions")
    layout = QVBoxLayout(card)
    layout.setContentsMargins(18, 14, 18, 14)
    layout.setSpacing(8)
    layout.addWidget(label("还需要你核对的前提", "outlineAssumptionTitle"))
    for text in assumptions:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(label("•", "outlineDetailLabel"), 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(label(text, "outlineAssumptionText"), 1)
        layout.addLayout(row)
    return card
