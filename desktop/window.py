from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from time import monotonic

from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPushButton, QProgressBar, QScrollArea, QSpinBox, QStackedWidget,
    QSystemTrayIcon, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QLineEdit,
    QSizePolicy)

from demo_sample import sample_learning_plan
from desktop import DISPLAY_VERSION
from desktop.credentials import CredentialStore
from desktop.dialogs import ConnectionDialog, PlanDialog, PreviewDialog, TaskDialog, spin
from desktop.focus import FocusTimer
from desktop.gateway import GatewayError, ModelGateway
from desktop.models import DesktopProfile
from desktop.network import TEST_TIMEOUT, MODELS_TIMEOUT, GENERATE_TIMEOUT
from desktop.review import prepare_review
from desktop.system import set_autostart
from desktop.theme import apply_theme, compass_icon
from desktop.ui import button_icon
from desktop.workers import Worker
from desktop.guide import open_guide
from desktop.planning import PlanningDraft, modification_context
from desktop.planning_ui import PlanningWindow, ModificationDialog, DraftsDialog, HistoryWindow


def label(text, *, name=None, wrap=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    if name:
        widget.setObjectName(name)
    return widget


def button(text, handler, primary=False):
    widget = QPushButton(text)
    widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    if primary:
        widget.setObjectName("primary")
    if text == "删除":
        widget.setObjectName("danger")
    widget.clicked.connect(lambda _checked=False: handler())
    return widget


def horizontal(*widgets):
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    for widget in widgets:
        layout.addWidget(widget)
    if widgets and all(isinstance(widget, QPushButton) for widget in widgets):
        layout.addStretch()
    return row


def page_layout():
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(16)
    return page, layout


def task_tree():
    tree = QTreeWidget()
    tree.setHeaderLabels(["任务", "日期", "预计", "状态"])
    tree.setRootIsDecorated(False)
    tree.setAlternatingRowColors(True)
    tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
    for column in (1, 2, 3):
        tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    return tree


class StatusLabel(QLabel):
    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))

    def clear(self):
        self.setText("")


class MainWindow(QMainWindow):
    def __init__(self, store, *, credentials=None, enable_tray=True):
        super().__init__()
        self.store = store
        self.credentials = credentials or CredentialStore()
        self.focus = FocusTimer(store)
        self.worker = None
        self.active_gateway = None
        self.request_timer = QTimer(self)
        self.request_timer.setSingleShot(True)
        self.request_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.request_timer.timeout.connect(self.request_timed_out)
        self.request_status_timer = QTimer(self)
        self.request_status_timer.setInterval(100)
        self.request_status_timer.timeout.connect(self.update_request_status)
        self.selected_plan_id = None
        self.planning_window = None
        self.history_windows = []
        self.exiting = False
        self.enable_tray = enable_tray
        self.setWindowTitle(f"学习罗盘 {DISPLAY_VERSION}")
        self.setWindowIcon(compass_icon())
        self.resize(1220, 820)
        self.setMinimumSize(980, 680)
        self.tray = None
        self._build()
        self.guide_shortcut = QShortcut(QKeySequence("F1"), self)
        self.guide_shortcut.activated.connect(lambda: open_guide(self))
        self._build_tray()
        self.refresh()
        self.clock_timer = QTimer(self)
        self.clock_timer.timeout.connect(self.tick)
        self.clock_timer.start(1000)
        self.reminder_timer = QTimer(self)
        self.reminder_timer.timeout.connect(self.check_reminders)
        self.reminder_timer.start(30000)
        QTimer.singleShot(1500, self.check_reminders)

    def _build(self):
        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(208)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(16, 26, 16, 22)
        side.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(compass_icon().pixmap(34, 34))
        icon.setFixedSize(34, 34)
        side.addWidget(horizontal(icon, label("学习罗盘", name="brand")))
        side.addSpacing(7)
        side.addWidget(label("LEARNING COMPASS", name="eyebrow"))
        side.addSpacing(30)
        side.addWidget(label("我的学习空间", name="eyebrow"))
        self.nav = []
        self.stack = QStackedWidget()
        for index, text in enumerate(["今日", "计划库", "周视图", "专注", "设置"]):
            if index == 4:
                side.addStretch()
            item = QPushButton(text)
            item.setObjectName("nav")
            button_icon(item, ["today", "library", "week", "focus", "settings"][index])
            item.setCheckable(True)
            item.clicked.connect(lambda _checked=False, i=index: self.navigate(i))
            self.nav.append(item)
            side.addWidget(item)
        side.addSpacing(18)
        side.addWidget(button("使用指南  ·  F1", lambda: open_guide(self)))
        side.addWidget(label("本机保存 · 由你掌控", name="muted"))
        side.addWidget(label(f"VERSION  {DISPLAY_VERSION}", name="eyebrow"))
        outer.addWidget(sidebar)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(32, 26, 32, 24)
        body_layout.setSpacing(18)
        header = QHBoxLayout()
        header.setSpacing(12)
        title_group = QVBoxLayout()
        title_group.setSpacing(4)
        self.page_title = label("今日", name="heading")
        self.page_subtitle = label("", name="muted")
        title_group.addWidget(self.page_title)
        title_group.addWidget(self.page_subtitle)
        header.addLayout(title_group)
        header.addStretch()
        self.model_picker = QComboBox()
        self.model_picker.setMinimumWidth(190)
        self.model_picker.setMaximumWidth(260)
        self.model_picker.setAccessibleName("当前生成模型")
        self.model_picker.currentIndexChanged.connect(self.select_model)
        header.addWidget(self.model_picker)
        self.new_button = button("创建计划", self.create_plan, True)
        button_icon(self.new_button, "plus")
        header.addWidget(self.new_button)
        body_layout.addLayout(header)
        self.notice = StatusLabel()
        self.notice.setObjectName("notice")
        self.notice.setWordWrap(True)
        self.notice.hide()
        body_layout.addWidget(self.notice)
        busy_row = QHBoxLayout()
        self.busy = StatusLabel()
        self.busy.setWordWrap(True)
        self.busy.hide()
        busy_row.addWidget(self.busy, 1)
        self.request_countdown = label("", name="requestCountdown")
        self.request_countdown.setAccessibleName("请求剩余时间")
        self.request_countdown.hide()
        busy_row.addWidget(self.request_countdown)
        self.cancel_button = button("取消当前请求", self.cancel_request)
        self.cancel_button.hide()
        busy_row.addWidget(self.cancel_button)
        body_layout.addLayout(busy_row)
        body_layout.addWidget(self.stack, 1)
        outer.addWidget(body, 1)
        self._today_page()
        self._library_page()
        self._week_page()
        self._focus_page()
        self._settings_page()
        self.navigate(0)

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for i, item in enumerate(self.nav):
            item.setChecked(i == index)
        self.page_title.setText(["今日", "计划库", "周视图", "专注", "设置"][index])
        self.page_subtitle.setText(["让每一小步，都算数。", "收藏目标，也记录走过的路。",
                                   "把时间留给真正重要的事。", "专注当下，慢慢抵达。",
                                   "让工具适合你的习惯。"][index])

    def _today_page(self):
        page, layout = page_layout()
        layout.addSpacing(8)
        layout.addWidget(label("这一周，你的学习轨迹", name="brand"))
        self.today_summary = label("", name="muted")
        layout.addWidget(self.today_summary)
        self.trail = QHBoxLayout()
        self.trail.setSpacing(10)
        self.trail_days = []
        for i in range(7):
            card = QFrame()
            card.setObjectName("card")
            inner = QVBoxLayout(card)
            inner.setContentsMargins(10, 16, 10, 16)
            inner.setSpacing(12)
            day_label, count = label(""), label("", name="muted")
            bar = QProgressBar()
            bar.setRange(0, 100)
            bar.setTextVisible(False)
            inner.addWidget(day_label)
            inner.addWidget(count)
            inner.addWidget(bar)
            self.trail.addWidget(card)
            self.trail_days.append((day_label, count, bar))
        layout.addLayout(self.trail)
        self.welcome = label("先到设置添加模型连接，或载入样例体验。已有旧版计划可以在“设置 → 数据与备份”导入。", wrap=True)
        layout.addWidget(self.welcome)
        self.sample_button = button("载入两周 Python 体验样例（无需 API）", self.load_sample)
        layout.addWidget(self.sample_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(label("今天与未完成的任务 · 双击填写记录", name="muted"))
        self.today_tree = task_tree()
        self.today_tree.itemDoubleClicked.connect(self.edit_tree_task)
        layout.addWidget(self.today_tree, 1)
        layout.addWidget(button("记录／编辑选中任务", lambda: self.edit_tree_task(self.today_tree.currentItem())), 0, Qt.AlignmentFlag.AlignLeft)
        self.stack.addWidget(page)

    def _library_page(self):
        page, layout = page_layout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索计划名称、科目或目标")
        self.search.textChanged.connect(self.refresh_library)
        self.archived = QCheckBox("查看已归档")
        self.archived.toggled.connect(self.refresh_library)
        layout.addWidget(horizontal(self.search, self.archived))
        layout.addWidget(horizontal(button("AI 调整计划", self.modify_plan, True),
                                    button("按此计划创建副本", self.fork_plan),
                                    button("查看需求与历史", self.show_history), button("继续草稿", self.resume_draft)))
        columns = QHBoxLayout()
        self.plan_list = QListWidget()
        self.plan_list.setMinimumWidth(215)
        self.plan_list.setMaximumWidth(255)
        self.plan_list.currentItemChanged.connect(self.plan_selected)
        columns.addWidget(self.plan_list)
        right = QVBoxLayout()
        self.plan_title = label("", name="brand", wrap=True)
        right.addWidget(self.plan_title)
        self.plan_summary = label("选择一份计划，继续上次的学习。", name="muted", wrap=True)
        right.addWidget(self.plan_summary)
        right.addWidget(horizontal(button("重命名", self.rename_selected), button("归档／恢复", self.archive_selected),
                                   button("导出 JSON", self.export_selected), button("删除", self.delete_selected)))
        self.plan_progress = QProgressBar()
        self.plan_progress.setTextVisible(False)
        right.addWidget(self.plan_progress)
        self.plan_tree = task_tree()
        self.plan_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.plan_tree.itemDoubleClicked.connect(self.edit_tree_task)
        right.addWidget(self.plan_tree, 1)
        self.review_text = label("", name="muted", wrap=True)
        right.addWidget(self.review_text)
        right.addWidget(horizontal(button("记录／编辑任务", lambda: self.edit_tree_task(self.plan_tree.currentItem())),
                                   button("生成本周复盘", self.review_selected, True), button("撤销最近调整", self.undo_selected)))
        columns.addLayout(right, 1)
        layout.addLayout(columns, 1)
        self.stack.addWidget(page)

    def _week_page(self):
        page, layout = page_layout()
        self.week_offset = 0
        self.week_heading = label("", name="brand")
        previous = button("上一周", lambda: self.change_week(-1))
        following = button("下一周", lambda: self.change_week(1))
        button_icon(previous, "left")
        button_icon(following, "right")
        self.week_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(horizontal(previous, self.week_heading,
                                   button("本周", lambda: self.change_week(0)), following))
        layout.addWidget(label("按真实日期查看所有进行中的计划；双击任务可改期和填写记录。", name="muted", wrap=True))
        self.week_tree = task_tree()
        self.week_tree.itemDoubleClicked.connect(self.edit_tree_task)
        layout.addWidget(self.week_tree, 1)
        self.stack.addWidget(page)

    def _focus_page(self):
        page, layout = page_layout()
        layout.addStretch()
        card = QFrame()
        card.setObjectName("focusCard")
        card.setMaximumWidth(610)
        card.setMinimumWidth(530)
        content = QVBoxLayout(card)
        content.setContentsMargins(36, 34, 36, 34)
        content.setSpacing(22)
        lead = label("一次只推进一件事", name="brand")
        lead.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(lead)
        self.focus_label = label("专注 · 已暂停", name="muted")
        self.focus_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(self.focus_label)
        self.timer_label = label("25:00", name="timer")
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        content.addWidget(self.timer_label)
        self.focus_task = QComboBox()
        self.focus_task.setAccessibleName("专注关联任务")
        content.addWidget(self.focus_task)
        self.focus_minutes = spin(1, 180, self.focus.focus_minutes)
        self.break_minutes = spin(1, 60, self.focus.break_minutes)
        content.addWidget(horizontal(label("专注分钟"), self.focus_minutes, label("休息分钟"), self.break_minutes))
        content.addWidget(horizontal(button("开始／继续", self.start_focus, True), button("暂停", self.pause_focus), button("结束本次", self.stop_focus)))
        content.addWidget(label("专注用时会自动记下。先完成这一小步，再去学习记录中确认任务。休眠或退出时自动暂停。", name="muted", wrap=True))
        layout.addWidget(card, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        self.stack.addWidget(page)

    def _settings_page(self):
        page, layout = page_layout()
        tabs = QTabWidget()
        tabs.setDocumentMode(True)
        tabs.tabBar().setDrawBase(False)
        model_page, model_layout = page_layout()
        model_layout.setContentsMargins(0, 28, 0, 0)
        model_layout.addWidget(label("用你喜欢的模型，规划下一步", name="brand"))
        model_layout.addWidget(label("每套连接独立保存，随时切换。密钥保存在 Windows 凭据管理器，也可以仅本次使用。", name="muted", wrap=True))
        model_layout.addWidget(horizontal(button("添加连接", self.add_connection, True), button("编辑", self.edit_connection),
                                         button("删除", self.remove_connection)))
        self.connection_list = QListWidget()
        self.connection_list.setMinimumHeight(110)
        self.connection_list.itemDoubleClicked.connect(lambda _item: self.edit_connection())
        model_layout.addWidget(self.connection_list)
        model_layout.addWidget(horizontal(button("测试连接（使用额度）", self.test_connection), button("获取并选择模型", self.fetch_models)))
        model_layout.addWidget(label("连接测试会发出小额真实请求，最长等待 30 秒。你可以随时取消；离线不会影响已保存的学习记录。", name="muted", wrap=True))
        model_layout.addStretch()
        tabs.addTab(model_page, "模型连接")
        general, general_layout = page_layout()
        general_layout.setContentsMargins(0, 28, 0, 0)
        general_layout.addWidget(label("外观与运行", name="brand"))
        self.theme_picker = QComboBox()
        for text, value in (("跟随系统", "system"), ("浅色", "light"), ("深色", "dark")):
            self.theme_picker.addItem(text, value)
        self.theme_picker.setCurrentIndex(self.theme_picker.findData(self.store.setting("theme", "system")))
        self.theme_picker.currentIndexChanged.connect(self.change_theme)
        self.theme_picker.setMaximumWidth(240)
        general_layout.addWidget(horizontal(label("界面主题"), self.theme_picker))
        self.tray_close = QCheckBox("关闭窗口后收进托盘，继续提醒和计时")
        self.tray_close.setChecked(self.store.setting("close_to_tray", True))
        self.tray_close.toggled.connect(lambda value: self.store.set_setting("close_to_tray", value))
        general_layout.addWidget(self.tray_close)
        self.autostart = QCheckBox("登录 Windows 时启动到托盘")
        self.autostart.setChecked(self.store.setting("autostart", False))
        self.autostart.toggled.connect(self.change_autostart)
        general_layout.addWidget(self.autostart)
        self.reminders_paused = QCheckBox("暂停任务提醒")
        self.reminders_paused.setChecked(self.store.setting("reminders_paused", False))
        self.reminders_paused.toggled.connect(lambda value: self.store.set_setting("reminders_paused", value))
        general_layout.addWidget(self.reminders_paused)
        general_layout.addStretch()
        general_layout.addWidget(label(f"学习罗盘 {DISPLAY_VERSION}\n模型请求仅发送到你配置的服务商。离线时仍可管理计划、记录进度和专注。", name="muted", wrap=True))
        tabs.addTab(general, "外观与提醒")
        data, data_layout = page_layout()
        data_layout.setContentsMargins(0, 28, 0, 0)
        data_layout.addWidget(label("把学习记录留在自己手里", name="brand"))
        data_layout.addWidget(label(f"数据目录：{self.store.directory}\n每天首次修改前自动备份，保留最近 30 个每日备份。软件升级和正常卸载会保留此目录。", name="muted", wrap=True))
        data_layout.addWidget(horizontal(button("立即备份", self.backup_now, True), button("恢复整库备份", self.restore_backup),
                                        button("打开数据目录", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.directory))))))
        data_layout.addWidget(label("旧版迁移与计划交换", name="brand"))
        data_layout.addWidget(horizontal(button("导入计划 JSON", self.import_json), button("导入旧版 SQLite", self.import_legacy)))
        data_layout.addWidget(label("单份 JSON 导入为新计划；整库恢复会替换当前资料，并先创建恢复点。备份不包含 API Key，换电脑后需要重新填写密钥。旧版源文件保持不变。", name="muted", wrap=True))
        data_layout.addStretch()
        tabs.addTab(data, "数据与备份")
        layout.addWidget(tabs)
        self.stack.addWidget(page)

    def _build_tray(self):
        if not self.enable_tray or not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(compass_icon(), self)
        self.tray.setToolTip(f"学习罗盘 {DISPLAY_VERSION}")
        menu = QMenu(self)
        show_action = QAction("打开学习罗盘", self)
        show_action.triggered.connect(self.reveal)
        menu.addAction(show_action)
        pause_action = QAction("暂停／恢复任务提醒", self)
        pause_action.triggered.connect(lambda: self.reminders_paused.setChecked(not self.reminders_paused.isChecked()))
        menu.addAction(pause_action)
        menu.addSeparator()
        quit_action = QAction("完全退出", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self.reveal() if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.messageClicked.connect(self.reveal)
        self.tray.show()

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def notify(self, title, text):
        self.notice.setText(text)
        if self.tray:
            self.tray.showMessage(title, text, QSystemTrayIcon.MessageIcon.Information, 8000)

    def safe(self, operation):
        try:
            return operation()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "操作未完成", str(error))
        except Exception:
            QMessageBox.warning(self, "操作未完成", "无法完成这项操作，请检查文件和数据目录。")

    def refresh(self):
        configs = self.store.connections()
        selected = self.store.setting("active_connection")
        self.model_picker.blockSignals(True)
        self.model_picker.clear()
        for config in configs:
            self.model_picker.addItem(f"{config.name} · {config.model}", config.id)
        if not configs:
            self.model_picker.addItem("请到设置添加模型", None)
        position = self.model_picker.findData(selected)
        if position >= 0:
            self.model_picker.setCurrentIndex(position)
        self.model_picker.blockSignals(False)
        self.connection_list.clear()
        self.connection_list.setMaximumHeight(max(110, min(360, len(configs) * 86 + 16)))
        self.connection_list.setToolTip("双击连接可编辑；密钥不会显示在此列表中")
        for config in configs:
            item = QListWidgetItem(f"{config.name}  ·  {config.model}\n{config.base_url}  |  {config.api_type}")
            item.setData(Qt.ItemDataRole.UserRole, config.id)
            self.connection_list.addItem(item)
        if self.connection_list.count():
            self.connection_list.setCurrentRow(0)
        self.refresh_today()
        self.refresh_library()
        self.refresh_week()
        self.refresh_focus_tasks()

    def refresh_today(self):
        today = date.today()
        rows = [t for t in self.store.tasks() if not t["archived"]]
        today_rows = [t for t in rows if t["due_date"] == today.isoformat()]
        completed = sum(t["status"] == "已完成" for t in today_rows)
        overdue = sum(t["due_date"] < today.isoformat() and t["status"] != "已完成" for t in rows)
        self.today_summary.setText(f"{today:%Y年%m月%d日}  ·  今日 {len(today_rows)} 项 / 已完成 {completed} 项  ·  待安排的逾期任务 {overdue} 项")
        monday = today - timedelta(days=today.weekday())
        for i, (heading, count, bar) in enumerate(self.trail_days):
            day = monday + timedelta(days=i)
            card = heading.parentWidget()
            if card.property("currentDay") != (day == today):
                card.setProperty("currentDay", day == today)
                card.style().unpolish(card)
                card.style().polish(card)
            daily = [t for t in rows if t["due_date"] == day.isoformat()]
            done = sum(t["status"] == "已完成" for t in daily)
            heading.setText(f"{'今天' if day == today else '周' + '一二三四五六日'[i]}  {day:%m.%d}")
            count.setText(f"{done}/{len(daily)} 项完成" if daily else "留一点空白")
            bar.setValue(round(done / len(daily) * 100) if daily else 0)
        empty = not self.store.list_plans() and not self.store.list_plans(archived=True)
        self.welcome.setVisible(empty)
        self.sample_button.setVisible(empty)
        self.fill_tasks(self.today_tree, [t for t in rows if t["due_date"] <= today.isoformat() and t["status"] != "已完成"])

    def fill_tasks(self, tree, rows):
        tree.clear()
        for task in rows:
            item = QTreeWidgetItem([task["task"].title, task["due_date"], f"{task['task'].duration_minutes} 分钟", task["status"]])
            item.setData(0, Qt.ItemDataRole.UserRole, task["id"])
            item.setToolTip(0, f"{task['plan_title']} · 第{task['week']}周第{task['day']}天\n{task['task'].description}\n产出：{task['task'].deliverable}")
            tree.addTopLevelItem(item)

    def refresh_library(self, *_args):
        previous = self.selected_plan_id
        self.plan_list.blockSignals(True)
        self.plan_list.clear()
        chosen = 0
        for i, plan in enumerate(self.store.list_plans(self.search.text(), self.archived.isChecked())):
            profile = DesktopProfile.model_validate_json(plan["profile_json"])
            item = QListWidgetItem(f"{plan['title']}\n{profile.subject} · {plan['completed']}/{plan['total']} 项完成")
            item.setData(Qt.ItemDataRole.UserRole, plan["id"])
            self.plan_list.addItem(item)
            if plan["id"] == previous:
                chosen = i
        self.plan_list.blockSignals(False)
        if self.plan_list.count():
            self.plan_list.setCurrentRow(chosen)
            self.plan_selected(self.plan_list.currentItem())
        else:
            self.selected_plan_id = None
            self.plan_title.clear()
            self.plan_tree.clear()
            self.plan_summary.setText("没有符合条件的计划。创建一份计划，或调整搜索条件。")
            self.review_text.clear()
            self.plan_progress.setValue(0)

    def plan_selected(self, item, *_args):
        if not item:
            return
        self.selected_plan_id = item.data(Qt.ItemDataRole.UserRole)
        saved = self.store.get_plan(self.selected_plan_id)
        profile = saved["profile"]
        self.plan_title.setText(saved["title"])
        self.plan_summary.setText(f"{profile.subject} · {profile.plan_weeks} 周 · 单日上限 {profile.minutes_per_day} 分钟\n{saved['overview']}")
        done = sum(t["status"] == "已完成" for t in saved["tasks"])
        self.plan_progress.setValue(round(done / len(saved["tasks"]) * 100))
        self.plan_progress.setFormat(f"已完成 {done}/{len(saved['tasks'])} 项")
        self.fill_tasks(self.plan_tree, sorted(saved["tasks"], key=lambda t: (t["week"], t["day"])))
        reviews = self.store.reviews(self.selected_plan_id)
        self.review_text.setText("\n".join(reviews.values()) if reviews else "每周把任务标记为已完成或延期后，生成复盘并调整后续安排。")

    def change_week(self, direction):
        self.week_offset = self.week_offset + direction if direction else 0
        self.refresh_week()

    def refresh_week(self):
        today = date.today()
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=self.week_offset)
        end = start + timedelta(days=6)
        self.week_heading.setText(f"{start:%m月%d日} — {end:%m月%d日}")
        rows = [t for t in self.store.tasks() if not t["archived"] and start.isoformat() <= t["due_date"] <= end.isoformat()]
        self.fill_tasks(self.week_tree, rows)

    def select_model(self, *_args):
        if self.model_picker.currentData():
            self.store.set_setting("active_connection", self.model_picker.currentData())

    def connection(self, connection_id=None):
        connection_id = connection_id or self.model_picker.currentData()
        configs = self.store.connections()
        config = next((c for c in configs if c.id == connection_id), None)
        if not config:
            raise ValueError("请先在设置中添加一套模型连接。")
        return config

    def gateway(self, connection_id=None):
        config = self.connection(connection_id)
        return ModelGateway(config, self.credentials.get(config.id))

    def start_work(self, text, function, done, gateway=None, *, timeout_seconds=GENERATE_TIMEOUT):
        if self.worker:
            QMessageBox.information(self, "请求正在进行", "请等待当前模型请求结束，或先取消它。")
            return
        worker = Worker(function, gateway.close if gateway else None)
        self.worker = worker
        self.active_gateway = gateway
        # 身份检查阻止已取消任务的排队信号污染新任务，即使信号已进入 Qt 事件队列。
        worker.signals.done.connect(lambda result: self.safe(lambda: done(result)) if self.worker is worker and not worker.cancelled.is_set() else None)
        worker.signals.failed.connect(lambda message: self.request_failed(worker, message))
        worker.signals.finished.connect(lambda: self.work_finished(worker))
        self.request_timeout_seconds = timeout_seconds
        self.request_deadline = monotonic() + timeout_seconds
        self.request_message = text
        self.request_timer.start(max(1, int(timeout_seconds * 1000)))
        self.request_status_timer.start()
        self.notice.clear()
        self.busy.setText(text)
        self.cancel_button.show()
        self.request_countdown.show()
        self.update_request_status()
        QThreadPool.globalInstance().start(worker)

    def update_request_status(self):
        if not self.worker:
            return
        remaining = max(0, math.ceil(self.request_deadline - monotonic()))
        self.request_countdown.setText(f"剩余 {remaining}s")
        self.request_countdown.setAccessibleDescription(f"请求剩余 {remaining} 秒")
        stage = self.active_gateway.control.stage if self.active_gateway else ""
        self.busy.setText(f"{self.request_message} · {stage}" if stage else self.request_message)
        if remaining == 0:
            self.request_timed_out()

    def cancel_request(self):
        if self.worker:
            self.worker.cancelled.set()
            if self.active_gateway:
                self.active_gateway.cancel()
            self.work_finished(self.worker)
            self.notice.setText("已取消，可以发起新请求。未完成结果不会保存；服务端已收到的请求仍可能计费。")

    def request_timed_out(self):
        if self.worker:
            seconds = self.request_timeout_seconds
            status = self.active_gateway.control.response_status if self.active_gateway else None
            self.cancel_request()
            detail = f"服务已返回 HTTP {status}，但未在时限内完成响应。" if status is not None else "未在时限内收到服务响应。"
            self.notice.setText(f"请求已超过 {seconds:g} 秒，已停止等待。{detail}可检查网络与代理或稍后重试；旧结果不会保存。")

    def request_failed(self, worker, message):
        if self.worker is worker and not worker.cancelled.is_set():
            self.work_finished(worker)
            self.notice.setText(message)
            QMessageBox.warning(self, "模型操作未完成", message)

    def work_finished(self, worker=None):
        if worker is not None and self.worker is not worker:
            return
        self.request_timer.stop()
        self.request_status_timer.stop()
        self.request_countdown.clear()
        self.request_countdown.hide()
        self.worker = None
        self.active_gateway = None
        self.busy.clear()
        self.cancel_button.hide()

    def create_plan(self):
        config = next((c for c in self.store.connections() if c.id == self.model_picker.currentData()), None)
        draft = PlanningDraft(profile=DesktopProfile(subject="Python", goal="请填写希望实现的学习目标", current_level="零基础",
                              plan_weeks=4, days_per_week=5, minutes_per_day=45),
                              start_date=date.today(), weekdays=[0, 1, 2, 3, 4], connection=config,
                              base_inputs={"goal": ""})
        self.open_planning(draft)

    def open_planning(self, draft):
        if self.planning_window and not self.planning_window.closed:
            if self.planning_window.draft.id == draft.id:
                self.planning_window.show()
                self.planning_window.raise_()
                return
            if not self.planning_window.close():
                return
        self.planning_window = PlanningWindow(self, draft)
        self.planning_window.show()

    def resume_draft(self):
        dialog = DraftsDialog(self.store, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected:
            self.open_planning(dialog.selected)

    def fork_plan(self):
        if not self.selected_plan_id:
            self.notice.setText("先选择一份计划，再创建副本。")
            return
        self.pause_focus()
        saved = self.store.get_plan(self.selected_plan_id)
        config = next((c for c in self.store.connections() if c.id == self.model_picker.currentData()), None)
        learned = "；".join(t["task"].title for t in saved["tasks"] if t["status"] == "已完成")[:1500]
        draft = PlanningDraft(profile=saved["profile"], start_date=date.today(), weekdays=json.loads(saved["weekdays_json"]),
                              connection=config, mastered=learned, parent_plan_id=saved["id"], parent_revision=saved["revision"])
        self.open_planning(draft)

    def modify_plan(self):
        if not self.selected_plan_id:
            self.notice.setText("先选择计划和需要调整的范围。")
            return
        self.pause_focus()
        saved = self.store.get_plan(self.selected_plan_id)
        selected = [item.data(0, Qt.ItemDataRole.UserRole) for item in self.plan_tree.selectedItems()]
        dialog = ModificationDialog(saved, selected, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        def begin():
            context = modification_context(saved, dialog.target_ids(saved, selected), self.store.reviews(saved["id"]))
            config = next((c for c in self.store.connections() if c.id == self.model_picker.currentData()), None)
            draft = PlanningDraft(kind="modify", profile=saved["profile"], start_date=date.fromisoformat(saved["start_date"]),
                weekdays=json.loads(saved["weekdays_json"]), connection=config,
                source_plan_id=saved["id"], source_revision=saved["revision"], target_ids=context["target_ids"],
                source_context={**context, "confirmed_requirements": self.store.brief(saved["id"])},
                request=dialog.intent.currentText() + "；" + dialog.text.toPlainText())
            self.open_planning(draft)
        self.safe(begin)

    def show_history(self):
        if self.selected_plan_id:
            dialog = HistoryWindow(self.store, self.selected_plan_id, self)
            self.history_windows.append(dialog)
            dialog.show()

    def load_sample(self):
        profile, plan = sample_learning_plan()
        profile = DesktopProfile.model_validate(profile.model_dump())
        days = [(date.today().weekday() + i) % 7 for i in range(profile.days_per_week)]
        self.selected_plan_id = self.store.save_plan(profile, plan, date.today(), days)
        self.refresh()
        self.navigate(1)

    def edit_tree_task(self, item, *_args):
        if item is None:
            return
        task_id = item.data(0, Qt.ItemDataRole.UserRole)
        row = next((t for t in self.store.tasks() if t["id"] == task_id), None)
        if not row:
            return
        dialog = TaskDialog(row, locked=row["week"] in self.store.reviews(row["plan_id"]), parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            def save():
                self.store.update_task(**dialog.values())
                self.refresh()
                self.notice.setText("任务与学习记录已保存。")
            self.safe(save)

    def rename_selected(self):
        if not self.selected_plan_id:
            return
        old = self.store.get_plan(self.selected_plan_id)["title"]
        text, ok = QInputDialog.getText(self, "重命名计划", "计划名称", text=old)
        if ok:
            self.safe(lambda: self.store.rename_plan(self.selected_plan_id, text))
            self.refresh()

    def archive_selected(self):
        if self.selected_plan_id:
            self.store.archive(self.selected_plan_id, not self.archived.isChecked())
            self.refresh()

    def delete_selected(self):
        if not self.selected_plan_id:
            return
        if QMessageBox.question(self, "删除计划", "删除这份计划及其学习记录？已生成的每日备份仍会保留。") != QMessageBox.StandardButton.Yes:
            return
        if self.focus.task_id in {t["id"] for t in self.store.tasks(self.selected_plan_id)}:
            self.focus.stop()
        self.store.delete_plan(self.selected_plan_id)
        self.selected_plan_id = None
        self.refresh()

    def review_selected(self):
        if not self.selected_plan_id:
            return
        plan_id = self.selected_plan_id
        def begin():
            self.pause_focus()
            saved = self.store.get_plan(plan_id)
            reviews = self.store.reviews(plan_id)
            final = len(reviews) == saved["profile"].plan_weeks - 1
            gateway = None if final else self.gateway()
            def done(preview):
                if PreviewDialog(preview, saved["plan"], self).exec() == QDialog.DialogCode.Accepted:
                    self.store.apply_review(plan_id, preview["week"], preview["summary"], preview["adjusted"], preview["revision"], preview["provenance"])
                    self.refresh()
                    self.notice.setText("复盘已保存，后续任务已更新。")
            self.start_work("正在整理复盘与调整预览…", lambda cancel: prepare_review(self.store, plan_id, gateway, cancelled=cancel), done, gateway)
        self.safe(begin)

    def undo_selected(self):
        if self.selected_plan_id:
            self.safe(lambda: self.store.undo_latest(self.selected_plan_id))
            self.refresh()

    def export_selected(self):
        if not self.selected_plan_id:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出计划与记录", "学习计划.json", "计划文件 (*.json)")
        if path:
            self.safe(lambda: Path(path).write_bytes(self.store.export_plan(self.selected_plan_id)))
            self.notice.setText("计划已导出，文件不包含 API Key。")

    def import_start(self):
        text, ok = QInputDialog.getText(self, "导入学习计划", "旧版计划从哪一天开始安排？新版文件会保留原有日期（yyyy-MM-dd）", text=date.today().isoformat())
        return date.fromisoformat(text) if ok else None

    def import_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入计划", "", "计划文件 (*.json)")
        if not path:
            return
        def action():
            start = self.import_start()
            if start:
                self.selected_plan_id = self.store.import_plan(Path(path).read_bytes(), start)
                self.archived.setChecked(False)
                self.search.clear()
                self.refresh()
                self.navigate(1)
        self.safe(action)

    def import_legacy(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择旧版 plans.sqlite3", str(Path(__file__).parents[1] / ".learning_compass"), "SQLite (*.sqlite3 *.db)")
        if not path:
            return
        def action():
            start = self.import_start()
            if start:
                ids = self.store.import_legacy_database(Path(path), start)
                self.selected_plan_id = ids[0] if ids else None
                self.refresh()
                self.notice.setText(f"已导入 {len(ids)} 份旧版计划；源文件保持不变。")
        self.safe(action)

    def backup_now(self):
        def action():
            path = self.store.manual_backup()
            self.notice.setText(f"备份已保存：{path.name}")
        self.safe(action)

    def restore_backup(self):
        path, _ = QFileDialog.getOpenFileName(self, "恢复整库备份", str(self.store.backups), "SQLite 备份 (*.sqlite3)")
        if not path:
            return
        def action():
            info = self.store.inspect_backup(Path(path))
            if QMessageBox.question(self, "恢复备份", f"备份包含 {info['plans']} 份计划、{info['tasks']} 项任务、{info['connections']} 套模型配置。\n将替换当前资料，恢复前会创建恢复点。是否继续？") != QMessageBox.StandardButton.Yes:
                return
            if self.worker:
                raise ValueError("请先结束当前模型请求再恢复数据。")
            self.focus.pause()
            self.store.restore(Path(path))
            self.focus = FocusTimer(self.store)
            self.selected_plan_id = None
            self.refresh()
            self.theme_picker.setCurrentIndex(self.theme_picker.findData(self.store.setting("theme", "system")))
            for box, key, default in ((self.tray_close, "close_to_tray", True), (self.autostart, "autostart", False), (self.reminders_paused, "reminders_paused", False)):
                box.blockSignals(True)
                box.setChecked(self.store.setting(key, default))
                box.blockSignals(False)
            self.notice.setText("备份已恢复。换电脑后，请重新填写模型密钥。")
        self.safe(action)

    def selected_connection(self):
        item = self.connection_list.currentItem()
        return self.connection(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def add_connection(self):
        self.edit_connection(new=True)

    def edit_connection(self, new=False):
        config = None if new else self.selected_connection()
        dialog = ConnectionDialog(config, bool(config and self.credentials.get(config.id)), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            def action():
                updated = dialog.config()
                key = dialog.key.text().strip() or (self.credentials.get(config.id) if config else "")
                self.store.save_connection(updated)
                if key:
                    remembered = self.credentials.set(updated.id, key, dialog.remember.isChecked())
                    if dialog.remember.isChecked() and not remembered:
                        QMessageBox.information(self, "仅本次使用", "系统凭据管理器不可用。密钥已临时保存在内存中，下次启动需要重新填写。")
                self.store.set_setting("active_connection", updated.id)
                self.refresh()
            self.safe(action)

    def remove_connection(self):
        config = self.selected_connection()
        if config and QMessageBox.question(self, "删除模型连接", "删除这套配置及保存的密钥？已有计划会保留。") == QMessageBox.StandardButton.Yes:
            self.credentials.delete(config.id)
            self.store.delete_connection(config.id)
            self.refresh()

    def test_connection(self):
        config = self.selected_connection()
        if not config:
            return
        if QMessageBox.question(self, "测试模型连接", "将向这套配置的服务商发送一次小型 JSON 请求，并使用 API 额度。是否测试？") != QMessageBox.StandardButton.Yes:
            return
        def action():
            gateway = self.gateway(config.id)
            self.start_work(f"正在测试 JSON 连接（最多 {TEST_TIMEOUT} 秒）…", lambda cancel: gateway.test(cancelled=cancel),
                            lambda result: self.notice.setText(f"连接测试通过，模型能返回有效 JSON。输入／输出 token：{result[0] if result[0] is not None else '未知'}／{result[1] if result[1] is not None else '未知'}。"), gateway, timeout_seconds=TEST_TIMEOUT)
        self.safe(action)

    def fetch_models(self):
        config = self.selected_connection()
        if not config:
            return
        def action():
            gateway = self.gateway(config.id)
            def done(models):
                if not models:
                    raise ValueError("服务未提供模型列表，请编辑配置并手动填写模型 ID。")
                name, ok = QInputDialog.getItem(self, "选择模型", "模型 ID（也可手动输入）", models, 0, True)
                if ok and name.strip():
                    updated = config.model_copy(update={"model": name.strip()})
                    self.store.save_connection(updated)
                    self.refresh()
            self.start_work("正在获取服务商模型列表…", lambda cancel: gateway.list_models(cancelled=cancel), done, gateway, timeout_seconds=MODELS_TIMEOUT)
        self.safe(action)

    def refresh_focus_tasks(self):
        selected = self.focus.task_id or self.focus_task.currentData()
        self.focus_task.clear()
        self.focus_task.addItem("不关联任务，只记录专注", None)
        for row in self.store.tasks():
            if not row["archived"] and (row["status"] != "已完成" or row["id"] == selected):
                self.focus_task.addItem(f"{row['task'].title} · {row['plan_title']}", row["id"])
        index = self.focus_task.findData(selected)
        self.focus_task.setCurrentIndex(max(0, index))

    def start_focus(self):
        def action():
            self.focus.configure(self.focus_minutes.value(), self.break_minutes.value())
            self.focus.start(self.focus_task.currentData())
            self.tick()
        self.safe(action)

    def pause_focus(self):
        self.focus.pause()
        self.tick()

    def stop_focus(self):
        self.focus.stop()
        self.tick()
        self.refresh_today()

    def tick(self):
        event = self.focus.tick()
        seconds = max(0, math.ceil(self.focus.remaining))
        self.timer_label.setText(f"{seconds // 60:02d}:{seconds % 60:02d}")
        self.focus_label.setText(("专注" if self.focus.phase == "focus" else "休息") + (" · 进行中" if self.focus.running else " · 已暂停"))
        self.focus_task.setEnabled(not self.focus.session_id)
        self.focus_minutes.setEnabled(not self.focus.running)
        self.break_minutes.setEnabled(not self.focus.running)
        if event == "sleep":
            self.notify("专注已暂停", "检测到电脑休眠或长时间未响应，专注已暂停，未将这段时间计入学习。")
        elif event in {"focus", "break"}:
            self.notify("专注完成" if event == "focus" else "休息结束", "这一轮已记录。开始休息吧。" if event == "focus" else "准备好了就开始下一轮。")

    def check_reminders(self):
        if self.store.setting("reminders_paused", False):
            return
        due = self.store.due_reminders(datetime.now())
        if due:
            text = f"有 {len(due)} 项学习任务等待你，打开今日页面查看。" if len(due) > 1 else f"该学一点了：{due[0]['task'].title}"
            self.notify("学习提醒", text)

    def change_theme(self, *_args):
        value = self.theme_picker.currentData()
        self.store.set_setting("theme", value)
        apply_theme(QApplication.instance(), value)

    def change_autostart(self, value):
        try:
            set_autostart(value)
            self.store.set_setting("autostart", value)
        except OSError:
            self.autostart.blockSignals(True)
            self.autostart.setChecked(not value)
            self.autostart.blockSignals(False)
            QMessageBox.warning(self, "未能设置", "无法修改当前用户的开机启动设置。")

    def quit_app(self):
        if self.planning_window and not self.planning_window.closed:
            if not self.planning_window.close():
                return
        self.focus.pause()
        self.cancel_request()
        self.exiting = True
        if self.tray:
            self.tray.hide()
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, event):
        if not self.exiting and self.tray and self.store.setting("close_to_tray", True):
            event.ignore()
            self.hide()
            if not self.store.setting("tray_explained", False):
                self.tray.showMessage("学习罗盘仍在运行", "提醒和计时会继续。从右下角托盘菜单选择“完全退出”可停止软件。")
                self.store.set_setting("tray_explained", True)
        else:
            if self.planning_window and not self.planning_window.closed:
                if not self.planning_window.close():
                    event.ignore()
                    return
            self.focus.pause()
            self.cancel_request()
            event.accept()
