"""Local schedule controls, sharing the app's guarded inputs and theme."""
from datetime import date, timedelta
from html import escape

from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFormLayout, QHBoxLayout, QHeaderView,
    QLabel, QMessageBox, QPushButton, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from desktop.controls import SoftComboBox
from desktop.dialogs import date_edit, spin
from desktop.scheduling import preview, protection_reason, manual_move_reason, BudgetExceeded, PlanCapacityExceeded, report_text
from desktop.confirmations import CapacityConfirmDialog


def note(text):
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setObjectName("muted")
    return label


def set_load_label(label, text, overload):
    label.setText(text)
    label.setObjectName("budgetWarning" if overload else "muted")
    label.style().unpolish(label)
    label.style().polish(label)


def load_text(row, day, limits):
    limit = limits[day.weekday()]
    total = row["total"] if row else 0
    text = f"全部活动计划与独立任务预计安排 {total} / {limit if limit is not None else '未设置'} 分钟"
    if row and row["over"]:
        text += f" · 超出 {row['over']} 分钟"
    if row:
        text += "；" + "、".join(f"{p['title']} {p['minutes']}分钟" for p in row["plans"].values())
    return text


def budget_action(parent, store, action):
    """The first transaction has rolled back before opening a confirmation."""
    try:
        return action()
    except (BudgetExceeded, PlanCapacityExceeded) as error:
        plan_limit = isinstance(error, PlanCapacityExceeded)
        box = CapacityConfirmDialog(error, parent)
        if box.exec() != QDialog.DialogCode.Accepted:
            return None
        with (store.accept_plan_capacity(error.token) if plan_limit else store.accept_budget(error.token)):
            return action()


class BudgetSettings(QWidget):
    def __init__(self, store, changed, parent=None):
        super().__init__(parent)
        self.store, self.changed = store, changed
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 20, 0, 0)
        title = QLabel("给每一天留出合适的学习时间")
        title.setObjectName("brand")
        layout.addWidget(title)
        layout.addWidget(note("这是全部活动计划和独立任务合计的预算。未设置表示不限制；0 分钟表示当天不预留学习时间。包含已完成任务的预计用时，不是实际用时。"))
        form = QFormLayout()
        self.fields = []
        values = store.setting("daily_budget", [None] * 7)
        for day, value in enumerate(values):
            enabled = QCheckBox("设置预算")
            enabled.setChecked(value is not None)
            minutes = spin(0, 1440, value if value is not None else 60)
            minutes.setSuffix(" 分钟")
            minutes.setEnabled(enabled.isChecked())
            minutes.setAccessibleName(f"周{'一二三四五六日'[day]}总预算")
            enabled.toggled.connect(minutes.setEnabled)
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.addWidget(enabled)
            line.addWidget(minutes)
            form.addRow(f"周{'一二三四五六日'[day]}", row)
            self.fields.append((enabled, minutes))
        layout.addLayout(form)
        save = QPushButton("保存时间预算")
        save.setObjectName("primary")
        save.clicked.connect(self.save)
        layout.addWidget(save, 0, Qt.AlignmentFlag.AlignLeft)
        self.status = note("")
        layout.addWidget(self.status)
        layout.addStretch()

    def reload(self):
        values = self.store.setting("daily_budget", [None] * 7)
        for value, (enabled, minutes) in zip(values, self.fields):
            enabled.setChecked(value is not None)
            minutes.setValue(value if value is not None else 60)
        self.status.setText("")

    def save(self):
        self.store.set_setting("daily_budget", [s.value() if e.isChecked() else None for e, s in self.fields])
        overload = [r for r in self.store.capacity().values() if r["over"]]
        self.status.setText("预算已保存，没有改动任务。" + (f"现有 {len(overload)} 天超限，请查看今日或周视图。" if overload else ""))
        self.changed()


class ScheduleDialog(QDialog):
    def __init__(self, store, parent=None, *, plan_id=None, overdue=False, kind="补排"):
        super().__init__(parent)
        self.store, self.kind, self.plan_id, self.overdue = store, kind, plan_id, overdue
        self.proposal = None
        self.setWindowTitle({"补排": "调整排期", "暂停": "暂停计划", "恢复": "提前恢复计划"}[kind] + " · 学习罗盘")
        self.resize(920, 720) if kind == "补排" else self.resize(650, 470)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        heading = QLabel("选择任务，再决定哪一天做" if kind == "补排" else "暂时休息，安排不丢失" if kind == "暂停" else "准备好继续学习了吗？")
        heading.setObjectName("brand")
        layout.addWidget(heading)
        layout.addWidget(note("只改你选中的任务日期，不改内容和学习记录。不会调用模型。" if kind == "补排" else
            "暂停只会关闭这份计划的提醒和新专注入口，不会自动更改任何任务日期。到所选日期当天自动恢复；也可提前恢复。" if kind == "暂停" else
            "恢复后重新开放提醒和新专注入口。任务日期不变；需要改期时，请在恢复后点击“调整排期”。"))
        form = QFormLayout()
        layout.addLayout(form)
        self.plans = SoftComboBox()
        self.plans.addItem("全部活动计划", None)
        self.plans.addItem("独立任务", "__standalone__")
        for p in store.list_plans():
            self.plans.addItem(p["title"], p["id"])
        if plan_id:
            self.plans.setCurrentIndex(self.plans.findData(plan_id))
        self.plans.setEnabled(kind == "补排")
        form.addRow("计划范围", self.plans)
        self.mode = SoftComboBox()
        self.mode.addItems(["指定日期：把选中任务改到同一天", "自动安排：从选定日期起寻找空闲学习日"])
        form.addRow("如何调整", self.mode)
        form.setRowVisible(self.mode, kind == "补排")
        self.start = date_edit(QDate.currentDate())
        self.start.setMinimumDate(QDate.currentDate())
        self.start.setMaximumDate(QDate.currentDate().addDays(366))
        self.resume = date_edit(QDate.currentDate().addDays(7))
        self.resume.setMinimumDate(QDate.currentDate().addDays(1))
        self.resume.setMaximumDate(QDate.currentDate().addDays(366))
        form.addRow("从这天继续学习" if kind == "暂停" else "目标日期 / 自动安排起始日", self.resume if kind == "暂停" else self.start)
        if kind != "暂停":
            form.setRowVisible(self.start, kind == "补排")
        shortcut_box = QWidget()
        shortcuts = QHBoxLayout(shortcut_box)
        shortcuts.setContentsMargins(0, 0, 0, 0)
        for title, offset in (("今天", 0), ("明天", 1), ("一周后", 7)):
            pick = QPushButton(title)
            pick.clicked.connect(lambda _=False, n=offset: self.start.setDate(QDate.currentDate().addDays(n)))
            shortcuts.addWidget(pick)
        shortcuts.addStretch()
        layout.addWidget(shortcut_box)
        shortcut_box.setVisible(kind == "补排")
        self.pause_hint = note("")
        layout.addWidget(self.pause_hint)
        self.pause_hint.setVisible(kind == "暂停")
        self.resume.dateChanged.connect(self.update_pause_hint)
        self.update_pause_hint()
        self.locked = QCheckBox("本次允许移动手动锁定的未开始任务（仅改日期，保留 AI 锁定）")
        self.future = QCheckBox("同时顺延所选计划后续可调整任务")
        self.overload = QCheckBox("允许跨计划总预算超载（应用时仍需二次确认）")
        for field in (self.locked, self.future, self.overload):
            layout.addWidget(field)
            field.toggled.connect(self.invalidate)
            field.setVisible(kind == "补排")
        choices = QHBoxLayout()
        layout.addLayout(choices)
        for caption, checked in (("全选可调整任务", True), ("清空选择", False)):
            select = QPushButton(caption, self)
            select.clicked.connect(lambda _=False, value=checked: self.select_tasks(value))
            select.setVisible(kind == "补排")
            choices.addWidget(select)
        self.selection_hint = note("仅勾选一项即可单独改期；勾选多项可批量调整。")
        choices.addWidget(self.selection_hint, 1)
        self.selection_hint.setVisible(kind == "补排")
        self.tasks = QTreeWidget()
        self.tasks.setRootIsDecorated(False)
        self.tasks.setHeaderLabels(["选择任务", "所属计划", "当前日期", "预计分钟", "不能选择的原因"])
        self.tasks.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tasks.itemChanged.connect(self.invalidate)
        layout.addWidget(self.tasks, 1)
        self.tasks.setVisible(kind == "补排")
        self.output = QTextBrowser()
        self.output.setMinimumHeight(160)
        layout.addWidget(self.output, 1)
        row = QHBoxLayout()
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        help_button = QPushButton("使用说明")
        from desktop.guide import open_guide
        help_button.clicked.connect(lambda: open_guide(self, "schedule"))
        generate = QPushButton("查看排期预览")
        generate.clicked.connect(self.make_preview)
        self.apply = QPushButton("确认应用" if kind == "补排" else "确认暂停" if kind == "暂停" else "恢复学习")
        self.apply.setObjectName("primary")
        self.apply.setEnabled(False)
        self.apply.clicked.connect(self.commit)
        row.addWidget(cancel)
        row.addWidget(help_button)
        row.addStretch()
        row.addWidget(generate)
        row.addWidget(self.apply)
        layout.addLayout(row)
        self.plans.currentIndexChanged.connect(self.fill)
        self.locked.toggled.connect(self.fill)
        self.mode.currentIndexChanged.connect(self.fill)
        self.start.dateChanged.connect(self.invalidate)
        self.resume.dateChanged.connect(self.invalidate)
        self.fill()

        if kind != "补排":
            self.make_preview()
            self.resume.dateChanged.connect(lambda *_: self.make_preview())
            generate.setVisible(False)

    def update_pause_hint(self, *_):
        d = self.resume.date().toPython()
        self.pause_hint.setText(f"今天起暂停，暂停到 {d - timedelta(days=1):%Y-%m-%d} 晚上；{d:%Y-%m-%d} 当天恢复学习。任务不会自动改期。")

    def select_tasks(self, checked):
        for i in range(self.tasks.topLevelItemCount()):
            item = self.tasks.topLevelItem(i)
            if item.flags() & Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(0, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)

    def invalidate(self, *_):
        self.proposal = None
        self.apply.setEnabled(False)
        self.output.setPlainText("选择任务后查看预览。更改选项后需重新预览，尚未修改原计划。")

    def fill(self, *_):
        preserve = self.sender() in (self.locked, self.mode)
        exact = self.mode.currentIndex() == 0
        self.locked.setVisible(self.kind == "补排" and not exact)
        self.future.setVisible(self.kind == "补排" and not exact)
        selected = {self.tasks.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.tasks.topLevelItemCount())
                    if self.tasks.topLevelItem(i).checkState(0) == Qt.CheckState.Checked}
        self.tasks.blockSignals(True)
        self.tasks.clear()
        for r in self.store.tasks(self.plans.currentData()):
            if r["archived"]:
                continue
            if self.overdue and r["due_date"] >= date.today().isoformat():
                continue
            reason = manual_move_reason(r, self.store.reviews(r["plan_id"])) if exact else protection_reason({**r, "manual": 0}, self.store.reviews(r["plan_id"]))
            if reason == "用户已锁定" and self.locked.isChecked():
                reason = ""
            if r.get("paused") and self.kind == "补排" and not exact:
                reason = "计划已暂停"
            item = QTreeWidgetItem([r["task"].title, r["plan_title"], r["due_date"], str(r["task"].duration_minutes), reason])
            item.setData(0, Qt.ItemDataRole.UserRole, r["id"])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            if not reason and self.kind == "补排":
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if r["id"] in selected or not preserve and self.overdue else Qt.CheckState.Unchecked)
            elif not reason:
                item.setText(4, "预览时重新安排")
            self.tasks.addTopLevelItem(item)
        self.tasks.blockSignals(False)
        self.invalidate()

    def make_preview(self):
        try:
            ids = [self.tasks.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.tasks.topLevelItemCount())
                   if self.tasks.topLevelItem(i).checkState(0) == Qt.CheckState.Checked]
            exact = self.mode.currentIndex() == 0
            p = preview(self.store, ids if self.kind == "补排" else [], self.start.date().toPython(), include_future=self.future.isChecked() and not exact,
                        include_locked=self.locked.isChecked(), allow_overload=self.overload.isChecked(),
                        kind=self.kind, plan_id=self.plan_id, resume_date=self.resume.date().toPython() if self.kind == "暂停" else None,
                        exact_date=exact and self.kind == "补排", keep_dates=self.kind != "补排",
                        manual_override=exact and self.kind == "补排")
            self.proposal = p
            if self.kind != "补排":
                html = "<h3>暂停后会怎样</h3>" if self.kind == "暂停" else "<h3>继续这份计划</h3>"
                html += f"<p>{escape(self.pause_hint.text())}</p>" if self.kind == "暂停" else "<p>从今天恢复提醒和新专注。所有任务日期保持原样。</p>"
                html += "<p>· 暂停期间仍可查看、编辑和记录任务。<br>· 不补发暂停期间错过的旧提醒。<br>· 如需移动任务，请另用“调整排期”，选择具体任务和日期。</p>"
                self.output.setHtml(html)
                self.apply.setEnabled(not p.conflicts)
                return
            html = "<h3>日期变化</h3>"
            if p.warnings:
                html = "<h3>超限提醒 · 应用时需再次确认</h3>" + "".join(f"<p>{escape(s)}</p>" for s in p.warnings) + html
            if self.kind == "暂停":
                html += f"<p>{escape(self.pause_hint.text())} 暂停期间不发任务提醒，不开始新专注。仍可查看、编辑和打卡。</p>"
            html += "<table cellspacing='8'><tr><th>计划 / 任务</th><th>原日期 → 新日期</th><th>预计</th></tr>"
            for r in p.changes:
                html += f"<tr><td>{escape(r['plan'])}<br><b>{escape(r['title'])}</b></td><td>{r['before']} → {r['after']}</td><td>{r['minutes']}分钟</td></tr>"
            html += "</table>"
            if not p.changes:
                html += "<p>没有任务需要改期。</p>"
            for pid, days in p.extensions.items():
                html += f"<p>{escape(self.store.get_plan(pid)['title'])}：结束日 {p.schedules[pid]['end_date']}，延长 {days} 天。</p>"
            if p.protected:
                html += "<h3>保持不动</h3>" + "".join(f"<p>{escape(r['title'])}：{escape(r['reason'])}</p>" for r in p.protected)
            html += "<h3>变更后每日总负荷</h3>" + "".join(f"<p>{r['date']}：{r['total']} / {r['budget'] if r['budget'] is not None else '未设置'} 分钟" + (f" · 超出 {r['over']}分钟" if r['over'] else "") + "</p>" for r in p.loads)
            if p.conflicts:
                html += "<h3>尚不能应用</h3>" + "".join(f"<p>{escape(s)}</p>" for s in p.conflicts)
            self.output.setHtml(html)
            self.apply.setEnabled(not p.conflicts and bool(p.schedules or p.changes))
        except (ValueError, OSError) as error:
            self.invalidate()
            self.output.setPlainText(str(error))

    def commit(self):
        if self.proposal is None:
            return
        try:
            applied = budget_action(self, self.store, lambda: (self.store.apply_schedule(self.proposal), True)[1])
            if applied:
                self.accept()
        except (ValueError, OSError) as error:
            self.invalidate()
            self.output.setPlainText(str(error))
