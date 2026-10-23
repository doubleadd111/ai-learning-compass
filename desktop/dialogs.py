from __future__ import annotations

from desktop.controls import SoftComboBox as QComboBox
from desktop.controls import (ArrowWheelSpinBox as QSpinBox, ArrowWheelTimeEdit as QTimeEdit,
                              ArrowWheelDateEdit as QDateEdit)
from desktop.calendar import configure_calendar

from datetime import date
from html import escape

from PySide6.QtCore import QDate, QTime, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QTextBrowser, QVBoxLayout, QWidget)

from desktop.models import DesktopProfile, ModelConnection, PROVIDER_PRESETS
from desktop.readable_text import ReadableTextEdit, form_scroll
from models import DailyTask


def spin(low, high, value):
    widget = QSpinBox()
    widget.setRange(low, high)
    widget.setValue(value)
    return widget


def date_edit(value=None):
    widget = QDateEdit(value or QDate.currentDate())
    widget.setCalendarPopup(True)
    configure_calendar(widget)
    widget.setDisplayFormat("yyyy-MM-dd")
    return widget


def buttons(dialog, layout, label="保存"):
    layout.setContentsMargins(24, 24, 24, 24)
    layout.setSpacing(16)
    # Keep form rows readable while retaining standard tab/keyboard behaviour.
    for index in range(layout.count()):
        child = layout.itemAt(index).layout()
        if isinstance(child, QFormLayout):
            child.setVerticalSpacing(12)
            child.setHorizontalSpacing(18)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Save).setText(label)
    box.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
    box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    layout.addWidget(box)


class PlanDialog(QDialog):
    def __init__(self, connections, selected_id=None, parent=None, *, embedded=False):
        super().__init__(parent)
        self.setWindowTitle("创建学习计划")
        self.resize(600, 650)
        layout = QVBoxLayout(self)
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
            layout.setContentsMargins(0, 0, 0, 0)
        lead = QLabel("把目标拆成每天能完成的一步")
        lead.setObjectName("brand")
        layout.addWidget(lead)
        lead.setVisible(not embedded)
        form = QFormLayout()
        self.subject = QComboBox()
        self.subject.addItems(["Python", "英语", "考试复习", "其他学习目标"])
        self.subject.setEditable(True)
        self.goal = ReadableTextEdit(min_lines=3,max_lines=6)
        self.goal.setPlaceholderText("例如：准备英语口语面试，能独立完成三分钟自我介绍")
        self.level = QComboBox()
        self.level.addItems(["零基础", "入门", "已有基础"])
        self.outcome = QLineEdit()
        self.outcome.setPlaceholderText("例如：做出一个可运行的小工具／独立完成自我介绍")
        self.mastered = QLineEdit()
        self.mastered.setPlaceholderText("已经会什么？没有也可以留空")
        self.weeks = spin(1, 8, 4)
        self.start = date_edit()
        self.weekdays = []
        day_box = QWidget()
        day_layout = QHBoxLayout(day_box)
        day_layout.setContentsMargins(0, 0, 0, 0)
        for i, name in enumerate("一二三四五六日"):
            checkbox = QCheckBox(name)
            checkbox.setChecked(i < 5)
            self.weekdays.append(checkbox)
            day_layout.addWidget(checkbox)
        self.minutes = spin(15, 240, 45)
        self.preferences = ReadableTextEdit(min_lines=3,max_lines=7)
        self.preferences.setPlaceholderText("可用设备、偏好、已掌握的内容、需要避开的安排……")
        self.connection = QComboBox()
        self.connection.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.connection.setMinimumContentsLength(12)
        for config in connections:
            self.connection.addItem(f"{config.name} · {config.model}", config.id)
        if self.connection.findData(selected_id) >= 0:
            self.connection.setCurrentIndex(self.connection.findData(selected_id))
        for name, widget in (("学习科目", self.subject), ("想实现的目标", self.goal), ("当前基础", self.level),
                             ("已掌握的内容", self.mastered), ("预期产出", self.outcome),
                             ("计划周数", self.weeks), ("开始日期", self.start), ("每周学习日", day_box),
                             ("每日上限（分钟）", self.minutes), ("偏好和限制", self.preferences), ("使用模型", self.connection)):
            form.addRow(name, widget)
        if embedded:
            layout.addLayout(form)
        else:
            layout.addWidget(form_scroll(form),1)
        tip = QLabel("生成会使用你选择的模型服务并消耗其 API 额度。计划保存后可离线查看。")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        layout.addWidget(tip)
        if not embedded:
            buttons(self, layout, "生成概略")

    def spec(self):
        days = [i for i, box in enumerate(self.weekdays) if box.isChecked()]
        return (DesktopProfile(subject=self.subject.currentText().strip(), goal=self.goal.toPlainText().strip(),
                current_level=self.level.currentText(), plan_weeks=self.weeks.value(), days_per_week=len(days),
                minutes_per_day=self.minutes.value(), preferences=self.preferences.toPlainText().strip() or "无"),
                self.start.date().toPython(), days, self.connection.currentData())

    def accept(self):
        try:
            self.spec()
        except ValueError:
            QMessageBox.warning(self, "请补充资料", "目标请填写 4–240 个字，至少选择一个学习日，偏好不超过 500 字。")
            return
        super().accept()


class ModelSelectionDialog(QDialog):
    """Use the same guarded, editable dropdown as every other model field."""
    def __init__(self, models, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择模型")
        self.resize(480, 160)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("模型 ID（也可手动输入）"))
        self.model = QComboBox()
        self.model.setEditable(True)
        self.model.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model.setMinimumContentsLength(20)
        self.model.addItems(models)
        layout.addWidget(self.model)
        buttons(self, layout, "使用此模型")


def choose_model(models, parent=None):
    dialog = ModelSelectionDialog(models, parent)
    accepted = dialog.exec() == QDialog.DialogCode.Accepted
    return dialog.model.currentText(), accepted


class ConnectionDialog(QDialog):
    def __init__(self, existing=None, has_key=False, parent=None):
        super().__init__(parent)
        self.existing = existing
        self.setWindowTitle("模型连接设置")
        self.resize(590, 530)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItems(PROVIDER_PRESETS)
        self.name, self.url, self.model, self.key = (QLineEdit() for _ in range(4))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("已有密钥；留空保持" if has_key else "粘贴这家服务商的 API Key")
        self.remember = QCheckBox("使用 Windows 凭据管理器记住密钥")
        self.remember.setChecked(True)
        self.api = QComboBox()
        self.api.addItem("Responses", "responses")
        self.api.addItem("Chat Completions（兼容服务常用）", "chat")
        self.output = QComboBox()
        self.output.addItem("严格 JSON Schema", "json_schema")
        self.output.addItem("JSON 模式（兼容性更广）", "json_object")
        self.token_field = QComboBox()
        self.token_field.addItems(["max_tokens", "max_completion_tokens"])
        self.network_mode = QComboBox()
        self.network_mode.addItem("默认代理（保持原路由）", "system")
        self.network_mode.addItem("直连（不使用环境代理）", "direct")
        for label, widget in (("服务商", self.provider), ("配置名称", self.name), ("服务地址", self.url),
                              ("模型 ID", self.model), ("API Key", self.key), ("", self.remember),
                              ("接口类型", self.api), ("输出格式", self.output), ("Chat 输出上限字段", self.token_field),
                              ("高级：网络路由", self.network_mode)):
            form.addRow(label, widget)
        layout.addLayout(form)
        self.api.currentIndexChanged.connect(lambda _index: form.setRowVisible(self.token_field, self.api.currentData() == "chat"))
        tip = QLabel("模型 ID 以服务商提供的名称为准。自定义服务需要兼容所选接口。密钥不会写入计划或备份。")
        tip.setWordWrap(True)
        tip.setObjectName("muted")
        layout.addWidget(tip)
        self.provider.currentTextChanged.connect(self.preset)
        if existing:
            self.provider.setCurrentText(existing.provider if existing.provider in PROVIDER_PRESETS else "自定义")
            self.name.setText(existing.name)
            self.url.setText(existing.base_url)
            self.model.setText(existing.model)
            self.api.setCurrentIndex(self.api.findData(existing.api_type))
            self.output.setCurrentIndex(self.output.findData(existing.output_format))
            self.token_field.setCurrentText(existing.max_tokens_field)
            self.network_mode.setCurrentIndex(self.network_mode.findData(existing.network_mode))
        else:
            self.preset("DeepSeek")
        form.setRowVisible(self.token_field, self.api.currentData() == "chat")
        buttons(self, layout)

    def preset(self, provider):
        values = PROVIDER_PRESETS[provider]
        self.name.setText(provider)
        self.url.setText(values["base_url"])
        self.model.setText(values["model"])
        self.api.setCurrentIndex(self.api.findData(values["api_type"]))
        self.output.setCurrentIndex(self.output.findData(values["output_format"]))
        self.token_field.setCurrentText(values.get("max_tokens_field", "max_tokens"))

    def config(self):
        values = dict(name=self.name.text().strip(), provider=self.provider.currentText(), base_url=self.url.text(),
                      model=self.model.text().strip(), api_type=self.api.currentData(), output_format=self.output.currentData(),
                      max_tokens_field=self.token_field.currentText(), network_mode=self.network_mode.currentData())
        if self.existing:
            values["id"] = self.existing.id
        return ModelConnection(**values)

    def accept(self):
        try:
            self.config()
        except ValueError:
            QMessageBox.warning(self, "请检查配置", "请填写配置名称、完整的 HTTPS 服务地址和模型 ID；地址中不要包含密钥。")
            return
        super().accept()


class TaskDialog(QDialog):
    def __init__(self, row, *, locked=False, parent=None, store=None):
        super().__init__(parent)
        self.row = row
        self.store = store
        task = row["task"]
        self.setWindowTitle("学习任务与记录")
        self.resize(760, 800)
        self.setMinimumSize(600,480)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.title = QLineEdit(task.title)
        self.description = ReadableTextEdit(task.description)
        self.description.setAccessibleName('任务说明')
        self.deliverable = ReadableTextEdit(task.deliverable,min_lines=2,max_lines=5)
        self.deliverable.setAccessibleName('完成产出')
        self.duration = spin(5, 240, task.duration_minutes)
        self.due = date_edit(QDate.fromString(row["due_date"], "yyyy-MM-dd"))
        self.reminder = QCheckBox("提醒我")
        self.reminder.setChecked(bool(row["remind_at"]))
        self.reminder_time = QTimeEdit(QTime.fromString(row["remind_at"] or "20:00", "HH:mm"))
        self.reminder_time.setDisplayFormat("HH:mm")
        self.ai_locked = QCheckBox("禁止 AI 修改这项任务")
        self.ai_locked.setChecked(bool(row.get("ai_locked", row.get("user_modified", 0))))
        from desktop.planning import protection_reason
        self.ai_locked.setEnabled(not locked and not row.get("manual") and not protection_reason({**row, "ai_locked": 0}))
        reminder_box = QWidget()
        rlayout = QHBoxLayout(reminder_box)
        rlayout.setContentsMargins(0, 0, 0, 0)
        rlayout.addWidget(self.reminder)
        rlayout.addWidget(self.reminder_time)
        self.status = QComboBox()
        self.status.addItems(["未开始", "进行中", "已完成", "延期"])
        self.status.setCurrentText(row["status"])
        self.manual = spin(0, 1440, row["manual_seconds"] // 60)
        self.difficulty = QComboBox()
        self.difficulty.addItem("未评估", None)
        for value in range(1, 6):
            self.difficulty.addItem(str(value), value)
        self.difficulty.setCurrentIndex(self.difficulty.findData(row["difficulty"]))
        self.note = ReadableTextEdit(row['note'],min_lines=3,max_lines=8)
        self.note.setAccessibleName('收获与问题')
        for label, widget in (("任务名称", self.title), ("任务说明", self.description), ("完成产出", self.deliverable),
                              ("预计分钟", self.duration), ("安排日期", self.due), ("提醒", reminder_box),
                              ("完成情况", self.status), ("人工补记分钟", self.manual), ("难度 1–5", self.difficulty), ("收获与问题", self.note)):
            form.addRow(label, widget)
        form.addRow("AI 保护", self.ai_locked)
        self.fields=form_scroll(form)
        layout.addWidget(self.fields,1)
        tip = QLabel(f"已自动记录专注时间：{row['focus_seconds'] // 60} 分钟。实际用时 = 专注时间 + 人工补记。" + ("\n本周已复盘，可补记用时与笔记。" if locked else ""))
        tip.setObjectName("muted")
        tip.setWordWrap(True)
        form.addRow(tip)
        if store is not None:
            from PySide6.QtWidgets import QPushButton
            steps_button = QPushButton("任务分组与执行步骤…")
            def show_steps():
                from desktop.learning_ui import StepsDialog
                from desktop.tool_windows import open_tool
                from shiboken6 import isValid
                previous = self.row['status']
                def finished(result):
                    if not isValid(self) or result != QDialog.DialogCode.Accepted:
                        return
                    updated = next((t for t in store.tasks() if t['id'] == self.row['id']), None)
                    if updated:
                        # Steps only change these parent fields. Never adopt unrelated
                        # peer-window edits into this editor's conflict baseline.
                        if self.status.currentText() == previous:
                            self.status.setCurrentText(updated['status'])
                        for key in ('status', 'ai_locked', 'user_modified'):
                            self.row[key] = updated[key]
                        self.ai_locked.setChecked(bool(updated['ai_locked']))
                open_tool(self, StepsDialog(store, self.row['id'], self), ('steps', self.row['id']), finished)
            steps_button.clicked.connect(show_steps)
            form.addRow(steps_button)
        if row.get("manual"):
            from PySide6.QtWidgets import QPushButton
            remove = QPushButton("删除这项手动任务…")
            remove.clicked.connect(lambda: self.done(2))
            form.addRow(remove)
        if locked:
            self.description.setReadOnly(True)
            self.deliverable.setReadOnly(True)
            for widget in (self.title, self.duration, self.due, self.status):
                widget.setEnabled(False)
        self.save_error = QLabel('')
        self.save_error.setWordWrap(True)
        self.save_error.setObjectName('budgetWarning')
        self.save_error.hide()
        layout.addWidget(self.save_error)
        buttons(self, layout)

    def values(self):
        return dict(task_id=self.row["id"], payload=DailyTask(day=self.row["day"] or 1, title=self.title.text().strip(),
                    description=self.description.toPlainText().strip(), duration_minutes=self.duration.value(), deliverable=self.deliverable.text().strip()),
                    due_date=self.due.date().toString("yyyy-MM-dd"), remind_at=self.reminder_time.time().toString("HH:mm") if self.reminder.isChecked() else None,
                    status=self.status.currentText(), manual_minutes=self.manual.value(), difficulty=self.difficulty.currentData(), note=self.note.toPlainText().strip(),
                    ai_locked=self.ai_locked.isChecked() if self.ai_locked.isEnabled() else None)

    def accept(self):
        try:
            values = self.values()
            if len(values["note"]) > 500:
                raise ValueError()
        except ValueError:
            QMessageBox.warning(self, "请检查任务", "请填写完整的任务和产出，任务说明不超过 300 字，笔记不超过 500 字。")
            return
        if self.store is not None:
            from desktop.schedule_ui import budget_action
            if values['due_date'] != self.row['due_date']:
                from desktop.workflow import dependency_warnings
                from contextlib import closing
                from desktop.confirmations import ConfirmationDialog,text_label
                with closing(self.store.connect()) as c:
                    warnings=dependency_warnings(c,dates={self.row['id']:values['due_date']})
                if warnings:
                    box=ConfirmationDialog('任务顺序需要注意','本次改期可能早于前置任务。没有自动修改其他任务。',self,confirm='仍然使用此日期')
                    for warning in warnings:box.content.addWidget(text_label(warning))
                    box.content.addStretch()
                    if box.exec()!=QDialog.DialogCode.Accepted:return
            def save():
                current = next((t for t in self.store.tasks() if t['id'] == self.row['id']), None)
                if current is None:
                    raise ValueError('任务已被删除，未保存本窗口的修改。')
                fields = ('task', 'due_date', 'remind_at', 'manual_seconds', 'difficulty', 'note', 'ai_locked')
                auto_started = self.row['status'] == '未开始' and current['status'] == '进行中' and current['focus_seconds'] > self.row['focus_seconds']
                if any(current[k] != self.row[k] for k in fields) or (current['status'] != self.row['status'] and not auto_started):
                    raise ValueError('任务已在其他窗口修改或打卡。请关闭并重新打开本窗口，再保存，避免覆盖新记录。')
                self.store.update_task(**values, confirm_limits=True)
                return True
            try:
                if not budget_action(self, self.store, save):
                    return
            except ValueError as error:
                self.save_error.setText(str(error))
                self.save_error.show()
                return
        super().accept()


class PreviewDialog(QDialog):
    def __init__(self, preview, original, parent=None):
        super().__init__(parent)
        self.setWindowTitle("复盘与后续调整预览")
        self.resize(760, 650)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        text = f"<h2>本周复盘</h2><p>{escape(preview['summary'])}</p>"
        if preview["adjusted"]:
            text += "<h3>后续任务</h3><p>手动编辑和已开始的任务会保留。应用后可以撤销；若之后有新记录，需要保留这些记录。</p>"
            for week in preview["adjusted"].weekly_plans[preview["week"]:]:
                text += f"<h3>第 {week.week_number} 周 · {escape(week.theme)}</h3>"
                for task in week.tasks:
                    old = original.weekly_plans[week.week_number - 1].tasks[task.day - 1]
                    label = "保留" if task == old else "调整"
                    text += f"<p><b>第{task.day}天 · {escape(task.title)}（{task.duration_minutes}分钟，{label}）</b><br>{escape(task.description)}<br>产出：{escape(task.deliverable)}</p>"
        browser.setHtml(text)
        layout.addWidget(browser)
        buttons(self, layout, "应用复盘")
