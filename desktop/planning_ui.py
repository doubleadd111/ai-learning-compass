"""Non-modal planning workspace: explicit paid actions, autosaved drafts."""
from __future__ import annotations

from desktop.controls import SoftComboBox as QComboBox

import json
from datetime import date, datetime
from html import escape

from PySide6.QtCore import QDate, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout,
    QInputDialog, QLabel, QLineEdit, QListWidget, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QStackedWidget, QTextBrowser, QToolButton, QTreeWidget, QTreeWidgetItem, QHeaderView, QVBoxLayout, QWidget)
from shiboken6 import isValid

from desktop.dialogs import PlanDialog, spin
from desktop.gateway import GatewayError, ModelGateway
from desktop.guide import open_guide
from desktop.models import DesktopProfile, schedule_dates
from desktop.planning import PlanningDraft, validate_proposal
from desktop.ui import readable_document
from models import validate_plan_for_profile


def action(text, callback, primary=False):
    result = QPushButton(text)
    if primary:
        result.setObjectName("primary")
    result.clicked.connect(lambda _checked=False: callback())
    return result


def note(text, brand=False):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(True)
    result.setObjectName("brand" if brand else "muted")
    return result


def scroll_page():
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    contents = QWidget()
    layout = QVBoxLayout(contents)
    layout.setContentsMargins(6, 6, 14, 6)
    layout.setSpacing(14)
    scroll.setWidget(contents)
    return scroll, layout


class PlanningWindow(QDialog):
    def __init__(self, owner, draft):
        super().__init__(owner, Qt.WindowType.Window)
        self.owner = owner
        self.store = owner.store
        self.draft = draft.model_copy(deep=True)
        self.owned_worker = None
        self.closed = False
        self.setWindowTitle("需求与计划 · 学习罗盘")
        self.resize(860, 740)
        self.setMinimumSize(700, 540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        top = QHBoxLayout()
        top.addWidget(note("调整你的学习安排" if draft.kind == "modify" else "先确认方向，再迈出第一步", True), 1)
        top.addWidget(action("查看本步骤说明", self.help))
        layout.addLayout(top)
        self.shortcut = QShortcut(QKeySequence("F1"), self)
        self.shortcut.activated.connect(self.help)
        self.steps = note("")
        layout.addWidget(self.steps)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        self._base_page()
        self.questions_page, self.questions_layout = scroll_page()
        self.pages.addWidget(self.questions_page)
        self.outline_page, self.outline_layout = scroll_page()
        self.pages.addWidget(self.outline_page)
        self.preview = QTextBrowser()
        readable_document(self.preview)
        self.pages.addWidget(self.preview)
        self.status = note("")
        layout.addWidget(self.status)
        self.cost = note("每次发送可能使用模型额度；展开概略、查看指南和保存到本机不调用模型。")
        layout.addWidget(self.cost)
        bottom = QHBoxLayout()
        self.back = action("返回资料", self.return_to_base)
        self.switch = action("换用顶部模型", self.switch_model)
        self.cancel = action("取消请求", self.cancel_work)
        self.close_button = action("保存草稿并关闭", self.close)
        self.next = action("发送资料，生成概略", self.advance, True)
        for item in (self.back, self.switch, self.cancel):
            bottom.addWidget(item)
        bottom.addStretch()
        bottom.addWidget(self.close_button)
        bottom.addWidget(self.next)
        layout.addLayout(bottom)
        self.autosave = QTimer(self)
        self.autosave.setSingleShot(True)
        self.autosave.setInterval(400)
        self.autosave.timeout.connect(lambda: self.owner.safe(self.save_inputs))
        self.watch_inputs()
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self.update_busy)
        self.ui_timer.start(120)
        self.render()
        self.store.save_draft(self.draft)

    def help(self):
        open_guide(self, "modify" if self.draft.kind == "modify" else "create")

    def _base_page(self):
        page, box = scroll_page()
        self.form = PlanDialog(self.store.connections(), self.draft.connection.id if self.draft.connection else None, self, embedded=True)
        box.addWidget(self.form)
        self.quick = QCheckBox("快速生成：跳过追问，仍先确认概略")
        self.quick.setChecked(self.draft.quick)
        box.addWidget(self.quick)
        self.request = QPlainTextEdit(self.draft.request)
        self.request.setPlaceholderText("例如：只把第二周的练习变简单，日期和其他任务不变")
        self.request.setMaximumHeight(100)
        self.request.setVisible(self.draft.kind == "modify")
        if self.draft.kind == "modify":
            box.addWidget(note(f"已选 {len(self.draft.target_ids)} 项可编辑任务；受保护任务不会交给模型改写。"))
            box.addWidget(self.request)
        self.archive = QCheckBox("保存新计划时，同时归档旧计划（保留记录）")
        self.archive.setChecked(self.draft.archive_parent)
        self.archive.setVisible(bool(self.draft.parent_plan_id))
        box.addWidget(self.archive)
        box.addStretch()
        self.pages.addWidget(page)
        self.fill_form()
        if self.draft.kind == "modify":
            # Local edits cannot silently change any hard constraints.
            self.form.setEnabled(False)
            self.quick.setEnabled(False)

    def fill_form(self):
        d, f = self.draft, self.form
        values = dict(subject=d.profile.subject, goal=d.profile.goal, level=d.profile.current_level,
                      weeks=d.profile.plan_weeks, start=d.start_date.isoformat(), weekdays=d.weekdays,
                      minutes=d.profile.minutes_per_day, preferences=d.profile.preferences,
                      outcome=d.outcome, mastered=d.mastered)
        values.update(d.base_inputs)
        f.subject.setCurrentText(values["subject"])
        f.goal.setPlainText(values["goal"])
        f.level.setCurrentText(values["level"])
        f.weeks.setValue(values["weeks"])
        f.start.setDate(QDate.fromString(values["start"], "yyyy-MM-dd"))
        for i, checkbox in enumerate(f.weekdays):
            checkbox.setChecked(i in values["weekdays"])
        f.minutes.setValue(values["minutes"])
        f.preferences.setPlainText(values["preferences"])
        f.outcome.setText(values["outcome"])
        f.mastered.setText(values["mastered"])

    def watch_inputs(self):
        for edit in self.form.findChildren(QLineEdit):
            edit.textChanged.connect(lambda *_: self.autosave.start())
        for edit in (self.form.goal, self.form.preferences, self.request):
            edit.textChanged.connect(lambda: self.autosave.start())
        for checkbox in self.form.weekdays + [self.quick, self.archive]:
            checkbox.toggled.connect(lambda *_: self.autosave.start())
        for widget in (self.form.weeks, self.form.minutes):
            widget.valueChanged.connect(lambda *_: self.autosave.start())
        self.form.start.dateChanged.connect(lambda *_: self.autosave.start())
        self.form.level.currentIndexChanged.connect(lambda *_: self.autosave.start())
        self.form.subject.currentTextChanged.connect(lambda *_: self.autosave.start())
        self.form.connection.currentIndexChanged.connect(self.bind_form_model)

    def bind_form_model(self):
        if self.draft.state == "base":
            found = next((c for c in self.store.connections() if c.id == self.form.connection.currentData()), None)
            if found:
                self.draft.connection = found.model_copy(deep=True)
            self.autosave.start()

    def save_inputs(self):
        if self.closed:
            return
        if self.draft.state == "base":
            f = self.form
            self.draft.base_inputs = dict(subject=f.subject.currentText(), goal=f.goal.toPlainText(),
                level=f.level.currentText(), weeks=f.weeks.value(), start=f.start.date().toString("yyyy-MM-dd"),
                weekdays=[i for i, cb in enumerate(f.weekdays) if cb.isChecked()], minutes=f.minutes.value(),
                preferences=f.preferences.toPlainText(), outcome=f.outcome.text(), mastered=f.mastered.text())
            self.draft.quick = self.quick.isChecked()
            self.draft.archive_parent = self.archive.isChecked()
            self.draft.request = self.request.toPlainText()[:2000]
        if self.draft.state == "questions":
            self.draft.pending_answers = {key: self.answer_text(combo, edit) for key, (combo, edit) in self.answer_widgets.items()}
        self.draft.revision += 1
        self.store.save_draft(self.draft)

    @staticmethod
    def answer_text(combo, edit):
        return edit.toPlainText().strip() or (combo.currentText() if combo.currentIndex() > 0 else "")

    def update_busy(self):
        working = self.owned_worker is not None and self.owner.worker is self.owned_worker
        self.next.setEnabled(not working)
        self.back.setEnabled(not working)
        self.switch.setEnabled(not working)
        self.cancel.setVisible(working)
        # Freeze input during a request. Its immutable snapshot cannot be silently
        # contradicted by an autosave while the background model is responding.
        self.pages.setEnabled(not working)
        if working:
            self.status.setText(self.owner.busy.text() + "  " + self.owner.request_countdown.text())
        elif self.owned_worker:
            self.owned_worker = None
            self.status.setText(self.owner.notice.text() or "请求结束，草稿仍保留。")

    def run_stage(self, stage, function, done):
        if self.owner.worker:
            raise ValueError("已有模型请求，请先等待或取消")
        if not self.draft.connection:
            raise ValueError("请先在设置添加模型，并在此窗口选择连接")
        self.autosave.stop()
        self.save_inputs()
        if self.draft.kind == "modify":
            self.current_source()
        snapshot = self.draft.model_copy(deep=True)
        current = next((config for config in self.store.connections() if config.id == snapshot.connection.id), None)
        if current is None or current != snapshot.connection:
            raise ValueError("该连接已删除或设置已变化。请明确“换用顶部模型”后继续，避免把新密钥发送到旧地址。")
        gateway = ModelGateway(snapshot.connection, self.owner.credentials.get(snapshot.connection.id))
        revision = self.draft.revision
        def work(cancel):
            try:
                result, provenance = function(gateway, snapshot, cancel)
            except GatewayError as error:
                if error.provenance:
                    self.store.planning_event(snapshot.id, stage, error.provenance)
                raise
            else:
                safe = self.store.planning_event(snapshot.id, stage, provenance)
                return result, safe
        def finish(result):
            if self.closed or not isValid(self) or self.draft.revision != revision:
                return
            payload, provenance = result
            self.draft.events.append(provenance)
            done(payload)
            self.store.save_draft(self.draft)
            self.status.setText("结果已保存为草稿；正式计划尚未改变。")
            self.render()
        self.owner.start_work(f"正在{stage}…", work, finish, gateway)
        self.owned_worker = self.owner.worker
        self.update_busy()

    def advance(self):
        self.owner.safe(self._advance)

    def _advance(self):
        if self.draft.state == "base":
            profile, start, weekdays, _connection = self.form.spec()
            if self.draft.kind == "create":
                self.draft.profile, self.draft.start_date, self.draft.weekdays = profile, start, weekdays
            self.draft.outcome, self.draft.mastered = self.form.outcome.text(), self.form.mastered.text()
            self.save_inputs()
            if self.draft.kind == "modify" and not self.draft.request.strip():
                raise ValueError("请描述希望怎样修改计划")
            self.run_stage("澄清需求与概略", lambda gateway, draft, cancel: gateway.clarify(draft, cancelled=cancel), self.draft.receive_turn)
        elif self.draft.state == "questions":
            self.save_inputs()
            self.draft.answer_questions(self.draft.pending_answers)
            self.draft.pending_answers = {}
            self.store.save_draft(self.draft)
            self.render()
            self.run_stage("补充需求与概略", lambda gateway, draft, cancel: gateway.clarify(draft, cancelled=cancel), self.draft.receive_turn)
        elif self.draft.state == "outline":
            if self.draft.kind == "create":
                context = json.dumps(dict(requirements=self.draft.context(), confirmed_outline=self.draft.outline.model_dump()), ensure_ascii=False)
                self.run_stage("生成每日任务", lambda gateway, draft, cancel: gateway.generate(draft.profile, context=context, cancelled=cancel), self.receive_candidate)
            else:
                saved = self.current_source()
                reviewed = self.store.reviews(saved["id"])
                self.run_stage("生成修改预览", lambda gateway, draft, cancel: gateway.propose_revision(draft, saved, reviewed, cancelled=cancel), self.receive_proposal)
        elif self.draft.state == "preview":
            self.save_result()

    def current_source(self):
        saved = self.store.get_plan(self.draft.source_plan_id)
        if saved["revision"] != self.draft.source_revision:
            raise ValueError("计划已有变化，请关闭此草稿并重新选择范围生成预览")
        return saved

    def receive_candidate(self, plan):
        validate_plan_for_profile(plan, self.draft.profile)
        self.draft.candidate = plan
        self.draft.state = "preview"
        self.draft.revision += 1

    def receive_proposal(self, proposal):
        saved = self.current_source()
        validate_proposal(saved, proposal, self.draft.target_ids, self.store.reviews(saved["id"]))
        self.draft.proposal = proposal
        self.draft.state = "preview"
        self.draft.revision += 1

    def save_result(self):
        d = self.draft
        if d.kind == "create":
            brief = dict(requirements=d.context(), outline=d.outline.model_dump(), events=d.events)
            pid = self.store.save_plan(d.profile, d.candidate, d.start_date, d.weekdays,
                provenance=d.events[-1] if d.events else None, brief=brief,
                parent_plan_id=d.parent_plan_id, parent_revision=d.parent_revision,
                archive_parent=d.archive_parent, draft_id=d.id)
        else:
            pid = d.source_plan_id
            self.store.apply_proposal(pid, d.proposal, d.target_ids, d.source_revision,
                                      provenance=d.events[-1] if d.events else None, draft_id=d.id,
                                      brief_update=dict(request=d.request, answers=[a.model_dump() for a in d.answers],
                                                        outline=d.outline.model_dump(), feedback=d.feedback,
                                                        target_ids=d.target_ids, events=d.events))
        d.state = "saved"
        self.owner.selected_plan_id = pid
        self.owner.archived.setChecked(False)
        self.owner.search.clear()
        self.owner.refresh()
        self.owner.navigate(1)
        self.owner.notice.setText("修改已应用，任务记录与历史保留。" if d.kind == "modify" else "计划已保存在本机。")
        self.close()

    def return_to_base(self):
        if self.draft.state == "base":
            return
        if QMessageBox.question(self, "返回资料", "返回后需要重新确认概略，当前详细预览会清除。继续吗？") != QMessageBox.StandardButton.Yes:
            return
        self.draft.state = "base"
        self.draft.rounds = 0
        self.draft.answers = []
        self.draft.questions = []
        self.draft.pending_answers = {}
        self.draft.outline = self.draft.candidate = self.draft.proposal = None
        self.draft.revision += 1
        self.store.save_draft(self.draft)
        self.render()

    def switch_model(self):
        config = next((c for c in self.store.connections() if c.id == self.owner.model_picker.currentData()), None)
        if not config:
            self.status.setText("请先在主窗口顶部选择模型；未保存密钥的配置需要到设置填写。")
            return
        self.draft.connection = config.model_copy(deep=True)
        self.draft.revision += 1
        self.store.save_draft(self.draft)
        self.status.setText(f"下一次发送使用 {config.name} · {config.model}；已完成结果不会重新请求。")

    def cancel_work(self):
        if self.owned_worker and self.owner.worker is self.owned_worker:
            self.owner.cancel_request()
        self.update_busy()

    def render(self):
        state = self.draft.state
        index = {"base": 0, "questions": 1, "outline": 2, "preview": 3}.get(state, 0)
        self.pages.setCurrentIndex(index)
        self.steps.setText("资料  →  关键追问  →  概略确认  →  详细预览  →  保存    ·    当前：" + ["基础资料", "关键追问", "概略确认", "详细预览"][index])
        labels = ["发送资料，生成概略", "提交回答，继续", "确认概略，生成每日任务" if self.draft.kind == "create" else "确认概略，生成修改预览", "保存计划" if self.draft.kind == "create" else "应用全部修改"]
        self.next.setText(labels[index])
        self.back.setVisible(index != 0)
        if state == "questions":
            self.render_questions()
        elif state == "outline":
            self.render_outline()
        elif state == "preview":
            self.render_preview()
        self.update_busy()

    @staticmethod
    def clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def render_questions(self):
        self.clear_layout(self.questions_layout)
        self.answer_widgets = {}
        self.questions_layout.addWidget(note(f"补充关键资料 · 第 {self.draft.rounds}/2 轮", True))
        for question in self.draft.questions:
            self.questions_layout.addWidget(note(question.question, True))
            self.questions_layout.addWidget(note(question.purpose))
            combo = QComboBox()
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(14)
            combo.addItems(["请选择，也可直接在下面填写", *question.options, "不确定", "跳过"])
            edit = QPlainTextEdit()
            edit.setMaximumHeight(80)
            edit.setPlaceholderText("选项都不合适？在这里写你的想法；自填内容优先。")
            edit.setPlainText(self.draft.pending_answers.get(question.id, ""))
            self.answer_widgets[question.id] = (combo, edit)
            combo.currentIndexChanged.connect(lambda *_: self.autosave.start())
            edit.textChanged.connect(lambda: self.autosave.start())
            self.questions_layout.addWidget(combo)
            self.questions_layout.addWidget(edit)
        self.questions_layout.addStretch()

    def render_outline(self):
        self.clear_layout(self.outline_layout)
        self.outline_layout.addWidget(note("先看看，这是不是你想走的方向？", True))
        for point in self.draft.outline.points:
            toggle = QToolButton()
            toggle.setText(point.summary)
            toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            toggle.setArrowType(Qt.ArrowType.RightArrow)
            toggle.setCheckable(True)
            toggle.setSizePolicy(toggle.sizePolicy().horizontalPolicy(), toggle.sizePolicy().verticalPolicy())
            detail = note(f"阶段目标：{point.objective}\n主要内容：{point.content}\n预期产出：{point.deliverable}\n安排依据：{point.rationale}")
            detail.hide()
            def expand(checked, target=detail, control=toggle):
                target.setVisible(checked)
                control.setArrowType(Qt.ArrowType.DownArrow if checked else Qt.ArrowType.RightArrow)
            toggle.toggled.connect(expand)
            self.outline_layout.addWidget(toggle)
            self.outline_layout.addWidget(detail)
            self.outline_layout.addWidget(action("修改这一点", lambda pid=point.id: self.modify_point(pid)), 0, Qt.AlignmentFlag.AlignLeft)
        if self.draft.outline.assumptions:
            self.outline_layout.addWidget(note("需要你确认的假设：\n" + "\n".join("• " + s for s in self.draft.outline.assumptions)))
        self.outline_layout.addStretch()

    def modify_point(self, point_id):
        text, ok = QInputDialog.getMultiLineText(self, "修改概略", "希望这一点怎样改变？其余方向默认保留。")
        if ok and text.strip():
            self.draft.revise_outline(point_id, text)
            self.store.save_draft(self.draft)
            self.render()
            self.advance()

    def render_preview(self):
        d = self.draft
        html = "<h2>确认后才会保存</h2>"
        if d.kind == "create":
            dates = schedule_dates(d.start_date, d.weekdays, d.profile.plan_weeks)
            html += f"<h3>{escape(d.candidate.title)}</h3><p>{escape(d.candidate.overview)}</p>"
            for week in d.candidate.weekly_plans:
                html += f"<h3>第{week.week_number}周 · {escape(week.theme)}</h3><p>{escape(week.milestone)}</p>"
                for task in week.tasks:
                    html += f"<p><b>{dates[(week.week_number, task.day)]} · 第{task.day}天 · {escape(task.title)} / {task.duration_minutes}分钟</b><br>{escape(task.description)}<br>产出：{escape(task.deliverable)}</p>"
        else:
            rows = {row["id"]: row for row in self.store.tasks(d.source_plan_id)}
            html += f"<p>{escape(d.proposal.summary)}</p>"
            for change in d.proposal.changes:
                old = rows.get(change.task_id)
                if not old:
                    continue
                html += f"<h3>{escape(old['task'].title)}</h3><table border='0' cellpadding='8'><tr><th>修改前</th><th>修改后</th></tr><tr>"
                for task, due in ((old["task"], old["due_date"]), (change.task, change.due_date.isoformat())):
                    html += f"<td><b>{escape(task.title)}</b><br>{escape(due)} · {task.duration_minutes}分钟<br>{escape(task.description)}<br>产出：{escape(task.deliverable)}</td>"
                html += f"</tr></table><p>修改理由：{escape(change.reason)}</p>"
        self.preview.setHtml(html)

    def closeEvent(self, event):
        self.cancel_work()
        self.autosave.stop()
        if self.draft.state != "saved":
            try:
                self.save_inputs()
            except Exception:
                self.status.setText("草稿保存失败，请检查数据目录空间与权限后重试；窗口保留，尚未退出。")
                event.ignore()
                return
        self.closed = True
        super().closeEvent(event)


class ModificationDialog(QDialog):
    def __init__(self, saved, selected_ids, parent=None):
        super().__init__(parent)
        self.setWindowTitle("选择调整范围")
        self.resize(530, 410)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.addWidget(note("只改需要改变的部分", True))
        self.scope = QComboBox()
        self.scope.addItems(["选中任务", "当前计划周", "剩余未完成部分"])
        self.scope.setCurrentIndex(0 if selected_ids else 2)
        self.week = spin(1, saved["profile"].plan_weeks, max(1, min(saved["profile"].plan_weeks, (date.today() - date.fromisoformat(saved["start_date"])).days // 7 + 1)))
        form = QFormLayout()
        form.addRow("调整范围", self.scope)
        form.addRow("计划周", self.week)
        self.intent = QComboBox()
        self.intent.addItems(["更具体", "降低难度", "增加练习", "缩短任务用时", "重新安排日期", "自定义"])
        form.addRow("修改方向", self.intent)
        layout.addLayout(form)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("补充希望改变什么、保留什么。例如只减少理论，不改变每日时长。")
        layout.addWidget(self.text)
        layout.addWidget(note("已完成、已有记录及锁定任务自动排除；若全部受保护，将提示先解锁或创建副本。"))
        row = QHBoxLayout()
        row.addWidget(action("查看本步骤说明", lambda: open_guide(self, "modify")))
        row.addStretch()
        row.addWidget(action("取消", self.reject))
        row.addWidget(action("继续明确需求", self.accept, True))
        layout.addLayout(row)

    def target_ids(self, saved, selected):
        if self.scope.currentIndex() == 0:
            return selected
        return [row["id"] for row in saved["tasks"] if (row["week"] == self.week.value() if self.scope.currentIndex() == 1 else row["status"] != "已完成")]


class DraftsDialog(QDialog):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.selected = None
        self.setWindowTitle("继续草稿")
        self.resize(850, 460)
        layout = QVBoxLayout(self)
        self.list = QTreeWidget()
        self.list.setRootIsDecorated(False)
        self.list.setHeaderLabels(["草稿与阶段", "创建时间", "最后修改时间"])
        self.list.header().setStretchLastSection(False)
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.list.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.list.itemDoubleClicked.connect(lambda *_: self.choose())
        layout.addWidget(self.list)
        row = QHBoxLayout()
        row.addWidget(action("删除选中草稿", self.remove))
        row.addStretch()
        row.addWidget(action("继续", self.choose, True))
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        entries = self.store.draft_entries()
        self.items = [entry["draft"] for entry in entries]
        self.list.clear()
        for entry in entries:
            draft = entry["draft"]
            state = {"base": "基础资料", "questions": "关键追问", "outline": "待确认概略", "preview": "待保存预览", "saved": "已保存"}[draft.state]
            goal = draft.base_inputs.get("goal", "") or draft.profile.goal
            def stamp(value):
                return datetime.fromisoformat(value).astimezone().strftime("%Y-%m-%d %H:%M") if value else "旧版未记录"
            item = QTreeWidgetItem([f"{'调整' if draft.kind == 'modify' else '创建'} · {draft.profile.subject} · {goal[:45]}\n{state}", stamp(entry["created_at"]), stamp(entry["updated_at"])])
            item.setToolTip(0, goal)
            self.list.addTopLevelItem(item)
        if self.items:
            self.list.setCurrentItem(self.list.topLevelItem(0))

    def choose(self):
        row = self.list.indexOfTopLevelItem(self.list.currentItem())
        if row >= 0:
            self.selected = self.items[row]
            self.accept()

    def remove(self):
        row = self.list.indexOfTopLevelItem(self.list.currentItem())
        if row >= 0 and QMessageBox.question(self, "删除草稿", "删除这份草稿及其本机问答？正式计划不会改变。") == QMessageBox.StandardButton.Yes:
            self.store.delete_draft(self.items[row].id)
            self.refresh()


class HistoryWindow(QDialog):
    def __init__(self, store, plan_id, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("需求与历史")
        self.resize(820, 640)
        layout = QVBoxLayout(self)
        browser = QTextBrowser()
        readable_document(browser)
        brief = store.brief(plan_id)
        html = "<h2>创建需求与概略</h2>"
        if brief:
            requirements = brief.get("requirements", {})
            profile = requirements.get("profile", {})
            html += f"<p><b>学习目标：</b>{escape(str(profile.get('goal', '未记录')))}<br><b>预期产出：</b>{escape(str(requirements.get('desired_outcome', '未填写')))}<br><b>已掌握：</b>{escape(str(requirements.get('already_mastered', '未填写')))}</p>"
            for answer in requirements.get("answers", []):
                html += f"<p><b>{escape(answer['question'])}</b><br>{escape(answer['answer'])}</p>"
            for point in brief.get("outline", {}).get("points", []):
                html += f"<h3>{escape(point['summary'])}</h3><p>{escape(point['content'])}<br>产出：{escape(point['deliverable'])}<br>依据：{escape(point['rationale'])}</p>"
            for event in brief.get("events", []):
                metrics = event.get("metrics", {})
                html += f"<p>模型：{escape(str(event.get('connection_name', '')))} · {escape(str(event.get('model', '')))} / {metrics.get('latency_ms', '未知')}毫秒 / 输入{metrics.get('input_tokens', '未知')}、输出{metrics.get('output_tokens', '未知')} token</p>"
            for adjustment in brief.get("adjustments", []):
                html += f"<h3>后续修改意见</h3><p>{escape(adjustment.get('request', ''))}</p>"
                for answer in adjustment.get("answers", []):
                    html += f"<p>{escape(answer['question'])}<br>{escape(answer['answer'])}</p>"
        else:
            html += "<p>旧版计划没有创建问答，不会编造补齐。原目标、记录仍然有效。</p>"
        for version in store.versions(plan_id):
            html += f"<h3>{escape(version['kind'])} · {escape(version['created_at'])}</h3>"
            after = json.loads(version["after_json"])
            for row in after.get("tasks", []):
                task = json.loads(row["payload_json"])
                html += f"<p>{escape(row['due_date'])} · {escape(task['title'])} / {task['duration_minutes']}分钟<br>{escape(task['description'])}</p>"
        browser.setHtml(html)
        layout.addWidget(browser)
        layout.addWidget(note("历史只读；有新记录后不能直接覆盖恢复。返回计划库可撤销最近一次未过期的AI调整。"))
