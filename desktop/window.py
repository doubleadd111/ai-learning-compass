from __future__ import annotations

import json
import math
from html import escape
from datetime import date, datetime, timedelta
from pathlib import Path
from time import monotonic

from PySide6.QtCore import Qt, QThreadPool, QTimer, QUrl, QEvent
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QFrame,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPushButton, QProgressBar, QScrollArea, QStackedWidget,
    QSystemTrayIcon, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QLineEdit,
    QSizePolicy)

from demo_sample import sample_learning_plan
from desktop import DISPLAY_VERSION, build_label
from desktop.credentials import CredentialStore
from desktop.dialogs import ConnectionDialog, PlanDialog, PreviewDialog, TaskDialog, ModelSelectionDialog, spin
from desktop.focus import FocusTimer
from desktop.gateway import GatewayError, ModelGateway
from desktop.models import DesktopProfile
from desktop.network import TEST_TIMEOUT, MODELS_TIMEOUT, GENERATE_TIMEOUT
from desktop.review import prepare_review
from desktop.system import set_autostart
from desktop.theme import apply_theme, compass_icon
from desktop.ui import button_icon
from desktop.controls import SoftComboBox as QComboBox
from desktop.workers import Worker
from desktop.guide import open_guide, close_guide
from desktop.planning import PlanningDraft, modification_context
from desktop.planning_ui import PlanningWindow, ModificationDialog, DraftsDialog, HistoryWindow
from desktop.week_view import WeekPanel, attach_focus_button
from desktop.diagnostics import ConnectionDiagnostic
from desktop.task_status import display_status
from desktop.task_style import TaskItemDelegate, mark_overdue, focus_tooltip, FOCUS_ROLE
from desktop.task_tip import set_task_tip
from desktop.plan_list import PlanListDelegate, populate_plan_item
from desktop.schedule_ui import ScheduleDialog, BudgetSettings, budget_action
from desktop.scheduling import paused
from desktop.confirmations import DeletePlanDialog, ConfirmationDialog
from desktop.action_menu import menu_button
from desktop.tool_windows import ToolWindows
from desktop.history_ui import PlanHistoryWindow
from desktop.task_search import TaskSearch


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


class ReadableTaskTree(QTreeWidget):
    def fit_title(self, *_):
        if self.columnCount() < 2:
            return
        others = sum(self.columnWidth(i) for i in range(1,self.columnCount()))
        width = max(210,self.viewport().width()-others)
        if self.columnWidth(0) != width:
            self.setColumnWidth(0,width)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_title()


def task_tree():
    tree = ReadableTaskTree()
    tree.setItemDelegate(TaskItemDelegate(tree))
    tree.setHeaderLabels(["任务", "日期", "预计", "状态", "操作"])
    tree.setRootIsDecorated(False)
    tree.setAlternatingRowColors(True)
    tree.header().setStretchLastSection(False)
    tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
    for column in (1, 2, 3, 4):
        tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
    tree.header().sectionResized.connect(tree.fit_title)
    tree.fit_title()
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
        self.store.expire_pauses()
        self.credentials = credentials or CredentialStore()
        self.focus = FocusTimer(store)
        self.worker = None
        self.active_gateway = None
        self.last_diagnostic = None
        self.request_timer = QTimer(self)
        self.request_timer.setSingleShot(True)
        self.request_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.request_timer.timeout.connect(self.request_timed_out)
        self.request_status_timer = QTimer(self)
        self.request_status_timer.setInterval(100)
        self.request_status_timer.timeout.connect(self.update_request_status)
        self.selected_plan_id = None
        self.library_standalone = False
        self.display_date = date.today()
        self.planning_window = None
        self.history_windows = []
        self.exiting = False
        self.enable_tray = enable_tray
        self.setWindowTitle(f"学习罗盘 {DISPLAY_VERSION}")
        self.setWindowIcon(compass_icon())
        self.resize(1220, 820)
        self.setMinimumSize(980, 680)
        self.tray = None
        self.tools = ToolWindows(self)
        QApplication.instance()._learning_tool_windows = self.tools
        self._build()
        from desktop.task_context import bind_task_menu
        for tree in (self.today_tree,self.plan_tree,self.week_tree):
            bind_task_menu(tree,self)
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
        self.diagnostic_button = button("复制连接诊断", self.copy_diagnostic)
        self.diagnostic_button.hide()
        body_layout.addWidget(self.diagnostic_button, 0, Qt.AlignmentFlag.AlignLeft)
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

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow() and hasattr(self, 'week_panel'):
            # A peer tool may have saved while the main workspace was behind it.
            self.refresh()

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
        task_filters = QHBoxLayout()
        task_filters.addWidget(label("今天与未完成的任务 · 双击填写记录", name="muted"), 1)
        plan_label = label("按计划", name="muted")
        task_filters.addWidget(plan_label)
        self.today_plan_filter = QComboBox()
        self.today_plan_filter.setAccessibleName("今日任务按计划筛选")
        self.today_plan_filter.setMinimumWidth(180)
        self.today_plan_filter.setMaximumWidth(300)
        self.today_plan_filter.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.today_plan_filter.setMinimumContentsLength(10)
        self.today_plan_filter.addItem("全部任务", None)
        self.today_plan_filter.setToolTip("筛选今日与逾期未完成任务；不改变上方全局周统计。")
        plan_label.setBuddy(self.today_plan_filter)
        self.today_plan_filter.currentIndexChanged.connect(self.refresh_today_tasks)
        task_filters.addWidget(self.today_plan_filter)
        self.today_completed = QCheckBox('查看已完成')
        self.today_completed.setToolTip('同时显示安排在今天的已完成任务；更早或未来的已完成任务请到计划库或独立任务箱查看。')
        self.today_completed.toggled.connect(self.refresh_today_tasks)
        task_filters.addWidget(self.today_completed)
        layout.addLayout(task_filters)
        self.today_budget = label("", name="muted", wrap=True)
        layout.addWidget(self.today_budget)
        layout.addWidget(horizontal(menu_button("任务管理", [
            ("添加自己的任务", self.add_manual_task),
            ("安排逾期任务", lambda: self.open_schedule(overdue=True)),
            ("记录／编辑选中任务", lambda: self.edit_tree_task(self.today_tree.currentItem()))]),
            menu_button("学习工具", [("学习统计", self.open_statistics), ("周期任务与复习", self.open_recurring),
                ("选中任务的执行步骤", lambda: self.open_steps(self.today_tree.currentItem())),
                ("使用指南", lambda: open_guide(self, "tools"))])))
        self.today_task_search = TaskSearch(status=False)
        self.today_task_search.changed.connect(self.refresh_today_tasks)
        layout.addWidget(self.today_task_search)
        layout.addWidget(label('双击任务可打开详情、填写笔记或修改状态。查看已完成只显示安排在今天的完成任务。', name='muted', wrap=True))
        self.today_empty = label("", name="muted", wrap=True)
        layout.addWidget(self.today_empty)
        self.today_tree = task_tree()
        self.today_tree.itemDoubleClicked.connect(self.edit_tree_task)
        layout.addWidget(self.today_tree, 1)
        self.stack.addWidget(page)

    def _library_page(self):
        page, layout = page_layout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索计划名称、科目或目标")
        self.search.textChanged.connect(self.refresh_library)
        self.archived = QCheckBox("查看已归档")
        self.archived.toggled.connect(self.refresh_library)
        layout.addWidget(horizontal(self.search, self.archived))
        columns = QHBoxLayout()
        self.plan_list = QListWidget()
        self.plan_list.setObjectName("planLibraryList")
        self.plan_list.setItemDelegate(PlanListDelegate(self.plan_list))
        self.plan_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.plan_list.setUniformItemSizes(True)
        self.plan_list.setMinimumWidth(215)
        self.plan_list.setMaximumWidth(255)
        self.plan_list.currentItemChanged.connect(self.plan_selected)
        columns.addWidget(self.plan_list)
        right = QVBoxLayout()
        self.plan_title = label("", name="brand", wrap=True)
        right.addWidget(self.plan_title)
        self.plan_summary = label("选择一份计划，继续上次的学习。", name="muted", wrap=True)
        right.addWidget(self.plan_summary)
        self.plan_more_button = menu_button("计划管理", [("重命名计划", self.rename_selected),
            ("归档／恢复归档", self.archive_selected), ("按此计划创建副本", self.fork_plan)])
        self.plan_more_menu = self.plan_more_button.action_menu
        self.plan_more_menu.section("危险操作 · 需要输入确认文字")
        self.delete_plan_action = self.plan_more_menu.add_action("删除计划…", self.delete_selected, danger=True)
        # Kept as a stable safety-check entry, not a separate delete button.
        self.plan_more_menu.delete_action = self.delete_plan_action
        self.plan_more_menu.delete_button = self.plan_more_menu.items[-1][1]
        tasks_menu = menu_button("任务安排", [
            ("添加任务到此计划", lambda: self.add_manual_task(self.selected_plan_id)),
            ("记录／编辑选中任务", lambda: self.edit_tree_task(self.plan_tree.currentItem())),
            ("调整排期", lambda: self.open_schedule(plan_id=self.selected_plan_id)),
            ("任务分组与执行步骤", self.open_structure)])
        self.pause_plan_button = tasks_menu.action_menu.add_action("暂停计划", self.pause_selected)
        self.tasks_menu_button = tasks_menu
        self.ai_menu_button = menu_button("AI 辅助", [("AI 调整计划", self.modify_plan), ("生成本周复盘", self.review_selected)])
        self.history_menu_button = menu_button("历史与数据", [("调整历史与回溯", self.undo_selected), ("查看需求与历史", self.show_history),
                ("继续草稿", self.resume_draft), ("导出 JSON", self.export_selected), ("回收站", self.open_trash)])
        right.addWidget(horizontal(tasks_menu, self.plan_more_button, self.ai_menu_button, self.history_menu_button))
        self.plan_progress = QProgressBar()
        self.plan_progress.setTextVisible(False)
        right.addWidget(self.plan_progress)
        self.library_task_search = TaskSearch()
        self.library_task_search.changed.connect(self.refresh_library_tasks)
        right.addWidget(self.library_task_search)
        self.library_task_hint = label('双击任务打开详情；可搜索名称、说明、完成标准或日期。', name='muted', wrap=True)
        right.addWidget(self.library_task_hint)
        self.plan_tree = task_tree()
        self.plan_tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.plan_tree.itemDoubleClicked.connect(self.edit_tree_task)
        right.addWidget(self.plan_tree, 1)
        self.review_text = label("", name="muted", wrap=True)
        right.addWidget(self.review_text)
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
                                   button("本周", lambda: self.change_week(0)), following, button("添加任务", self.add_manual_task)))
        self.week_panel = WeekPanel(self.store, self.start_focus_for_task, self.edit_task_id, jump=self.jump_week)
        self.week_tree = self.week_panel.table
        layout.addWidget(self.week_panel, 1)
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
        self.focus_minutes = spin(1, 240, self.focus.focus_minutes)
        self.break_minutes = spin(1, 60, self.focus.break_minutes)
        content.addWidget(horizontal(label("专注分钟"), self.focus_minutes, label("休息分钟"), self.break_minutes))
        content.addWidget(label('新任务按预计分钟开始；继续同一任务保留剩余时间。不关联任务时可自行设置。', name='muted', wrap=True))
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
        general_layout.addWidget(label(f"学习罗盘 {DISPLAY_VERSION} · {build_label()}\n模型请求仅发送到你配置的服务商。离线时仍可管理计划、记录进度和专注。", name="muted", wrap=True))
        tabs.addTab(general, "外观与提醒")
        self.budget_settings = BudgetSettings(self.store, self.refresh, self)
        budget_scroll = QScrollArea()
        budget_scroll.setWidgetResizable(True)
        budget_scroll.setFrameShape(QFrame.Shape.NoFrame)
        budget_scroll.setWidget(self.budget_settings)
        tabs.addTab(budget_scroll, "学习时间预算")
        data, data_layout = page_layout()
        data_layout.setContentsMargins(0, 28, 0, 0)
        data_layout.addWidget(label("把学习记录留在自己手里", name="brand"))
        data_layout.addWidget(label(f"数据目录：{self.store.directory}\n每天首次修改前自动备份，保留最近 30 个每日备份。软件升级和正常卸载会保留此目录。", name="muted", wrap=True))
        data_layout.addWidget(button('回收站：找回误删的计划和手动任务', self.open_trash))
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
            return budget_action(self, self.store, operation)
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
        rows = [t for t in self.store.tasks() if not t["archived"] and not t.get("paused")]
        today_rows = [t for t in rows if t["due_date"] == today.isoformat()]
        completed = sum(t["status"] == "已完成" for t in today_rows)
        overdue = sum(t["due_date"] < today.isoformat() and t["status"] != "已完成" for t in rows)
        self.today_summary.setText(f"{today:%Y年%m月%d日}  ·  今日 {len(today_rows)} 项 / 已完成 {completed} 项  ·  待安排的逾期任务 {overdue} 项")
        from desktop.schedule_ui import load_text, set_load_label
        load = self.store.capacity().get(today.isoformat())
        limits = self.store.setting("daily_budget", [None] * 7)
        budget_text = load_text(load, today, limits)
        set_load_label(self.today_budget, budget_text.partition("；")[0], bool(load and load["over"]))
        self.today_budget.setToolTip(budget_text)
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
        previous = self.today_plan_filter.currentData()
        self.today_plan_filter.blockSignals(True)
        self.today_plan_filter.clear()
        self.today_plan_filter.addItem("全部任务", None)
        if self.store.tasks("__standalone__"):
            self.today_plan_filter.addItem("独立任务", "__standalone__")
        for plan in self.store.list_plans():
            self.today_plan_filter.addItem(plan["title"], plan["id"])
            self.today_plan_filter.setItemData(self.today_plan_filter.count() - 1,
                                               plan["title"], Qt.ItemDataRole.ToolTipRole)
        self.today_plan_filter.setCurrentIndex(max(0, self.today_plan_filter.findData(previous)))
        self.today_plan_filter.blockSignals(False)
        self.refresh_today_tasks()

    def refresh_today_tasks(self, *_args):
        """Filter the existing Today scope without changing data or global statistics."""
        selected = self.today_tree.currentItem()
        selected_id = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        today = date.today().isoformat()
        rows = [r for r in self.store.tasks(self.today_plan_filter.currentData()) if not r['archived'] and not r.get('paused')
                and r['due_date'] <= today and (r['status'] != '已完成' or self.today_completed.isChecked() and r['due_date'] == today)]
        rows = self.today_task_search.apply(rows, date.today())
        self.fill_tasks(self.today_tree, rows, self.today_task_search)
        for i in range(self.today_tree.topLevelItemCount()):
            item = self.today_tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == selected_id:
                self.today_tree.setCurrentItem(item)
                break
        self.today_empty.setText("该计划没有今日或逾期的未完成任务。未来任务可在计划库或周视图查看。"
                                 if self.today_plan_filter.currentData() else
                                 "没有今日或逾期的未完成任务。未来任务可在计划库或周视图查看。")
        self.today_empty.setVisible(not rows)
        if not rows and (self.today_task_search.query.text() or self.today_task_search.limit_dates.isChecked()):
            self.today_empty.setText('没有匹配的任务。可清除搜索或调整日期；今日页仍只查今日和逾期范围。')
        if self.today_task_search.invalid_range():
            self.today_empty.setText('结束日期不能早于开始日期，请调整日期范围。')

    def fill_tasks(self, tree, rows, search=None):
        tree.clear()
        for task in rows:
            item = QTreeWidgetItem([task["task"].title, task["due_date"], f"{task['task'].duration_minutes} 分钟", display_status(task, date.today()), ""])
            item.setData(0, Qt.ItemDataRole.UserRole, task["id"])
            set_task_tip(item, task)
            mark_overdue(item, task, 5, date.today())
            if search:
                search.annotate(item, task)
            tree.addTopLevelItem(item)
            attach_focus_button(tree, item, 4, task, self.start_focus_for_task)

    def refresh_library(self, *_args):
        previous = self.selected_plan_id or ('__standalone__' if self.library_standalone else None)
        self.plan_list.blockSignals(True)
        self.plan_list.clear()
        chosen = -1
        for i, plan in enumerate(self.store.list_plans(self.search.text(), self.archived.isChecked())):
            profile = DesktopProfile.model_validate_json(plan["profile_json"])
            item = QListWidgetItem()
            populate_plan_item(item, plan, profile.subject)
            self.plan_list.addItem(item)
            if plan["id"] == previous:
                chosen = self.plan_list.count() - 1
        rows = self.store.tasks('__standalone__')
        independent = QListWidgetItem()
        populate_plan_item(independent, dict(id='__standalone__', title='独立任务', total=len(rows),
            completed=sum(t['status'] == '已完成' for t in rows)), '固定任务箱 · 全部日期')
        independent.setToolTip('不属于任何计划的任务。包含未完成和已完成任务，可搜索全部日期；此入口不能删除或归档。')
        self.plan_list.insertItem(0, independent)
        chosen = 0 if previous == '__standalone__' or chosen == -1 and self.plan_list.count() == 1 else (chosen + 1 if chosen >= 0 else 1)
        if self.plan_list.count():
            self.plan_list.setCurrentRow(chosen)
            self.plan_list.blockSignals(False)
            self.plan_selected(self.plan_list.currentItem())
        else:
            self.plan_list.blockSignals(False)
            self.selected_plan_id = None
            self.plan_title.clear()
            self.plan_tree.clear()
            self.plan_summary.setText("没有符合条件的计划。创建一份计划，或调整搜索条件。")
            self.review_text.clear()
            self.plan_progress.setValue(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'plan_summary') and hasattr(self, 'review_text'):
            compact = self.height() < 750
            self.plan_summary.setVisible(not compact)
            self.review_text.setVisible(not compact)

    def plan_selected(self, item, *_args):
        if not item:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        self.library_standalone = entry == '__standalone__'
        self.selected_plan_id = None if self.library_standalone else entry
        self.set_library_actions()
        if self.library_standalone:
            rows = self.store.tasks('__standalone__')
            done = sum(t['status'] == '已完成' for t in rows)
            self.plan_title.setText('独立任务')
            self.plan_summary.setText('不属于任何计划的任务都在这里。初始显示全部日期和全部状态；完成后不会消失。\n这是固定任务箱，不是学习计划，不能删除或归档入口；完整记录可在设置中做整库备份。')
            self.plan_title.setToolTip('<p>' + escape(self.plan_summary.text()).replace('\n','<br>') + '</p>')
            self.plan_progress.setValue(round(done / len(rows) * 100) if rows else 0)
            self.review_text.setText('完成任务仍可搜索和查看记录。添加任务时选择“独立任务”；修改日期可双击任务进入详情。')
            self.refresh_library_tasks()
            return
        saved = self.store.get_plan(self.selected_plan_id)
        self.pause_plan_button.setText("恢复学习" if paused(saved["schedule"]) else "暂停计划")
        profile = saved["profile"]
        self.plan_title.setText(saved["title"])
        self.plan_summary.setText(f"{profile.subject} · {profile.plan_weeks} 周 · 单日上限 {profile.minutes_per_day} 分钟\n{saved['overview']}")
        if paused(saved["schedule"]):
            self.plan_summary.setText(self.plan_summary.text() + f"\n已暂停 · {saved['schedule']['resume_date']} 当天恢复")
        self.plan_summary.setText(self.plan_summary.text() + f"\n实际排期：{saved['start_date']} — {saved['schedule']['end_date']}（学习内容周次不变）")
        self.plan_title.setToolTip('<p>' + escape(self.plan_summary.text()).replace('\n','<br>') + '</p>')
        done = sum(t["status"] == "已完成" for t in saved["tasks"])
        self.plan_progress.setValue(round(done / len(saved["tasks"]) * 100) if saved['tasks'] else 0)
        self.plan_progress.setFormat(f"已完成 {done}/{len(saved['tasks'])} 项")
        self.refresh_library_tasks()
        reviews = self.store.reviews(self.selected_plan_id)
        self.review_text.setText("\n".join(reviews.values()) if reviews else "每周把任务标记为已完成或延期后，生成复盘并调整后续安排。")

    def set_library_actions(self):
        regular = bool(self.selected_plan_id)
        self.plan_more_button.setEnabled(regular)
        self.ai_menu_button.setEnabled(regular)
        # Drafts exist before the first plan: never make resume depend on a plan.
        self.history_menu_button.setEnabled(True)
        for action, _ in self.history_menu_button.action_menu.items:
            action.setEnabled(regular or action.text() in ('继续草稿', '回收站'))
        for action, _ in self.plan_more_menu.items:
            action.setEnabled(regular)
        for action, _ in self.tasks_menu_button.action_menu.items:
            if action is self.tasks_menu_button.action_menu.items[0][0]:
                action.setText('添加独立任务' if self.library_standalone else '添加任务到此计划')
            if action is self.pause_plan_button or action.text() == '调整排期':
                action.setEnabled(regular)

    def refresh_library_tasks(self, *_):
        selected = self.plan_tree.currentItem()
        task_id = selected.data(0, Qt.ItemDataRole.UserRole) if selected else None
        source = '__standalone__' if self.library_standalone else self.selected_plan_id
        rows = self.store.tasks(source) if source else []
        filtered = self.library_task_search.apply(rows, date.today())
        self.fill_tasks(self.plan_tree, filtered, self.library_task_search)
        for i in range(self.plan_tree.topLevelItemCount()):
            item = self.plan_tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) == task_id:
                self.plan_tree.setCurrentItem(item)
                break
        self.library_task_hint.setText(f'显示 {len(filtered)}/{len(rows)} 项 · 双击任务打开详情、查看说明或修改记录。'
            if filtered else '没有匹配的任务。请清除关键词、选择“全部状态”或调整日期范围。')
        if self.library_task_search.invalid_range():
            self.library_task_hint.setText('结束日期不能早于开始日期，请调整日期范围。')

    def change_week(self, direction):
        self.week_offset = self.week_offset + direction if direction else 0
        self.refresh_week()

    def jump_week(self, day):
        today = date.today()
        self.week_offset = ((day - timedelta(days=day.weekday())) - (today - timedelta(days=today.weekday()))).days // 7
        self.refresh_week()

    def open_schedule(self, *, plan_id=None, overdue=False, kind="补排"):
        def action():
            key = ("schedule", plan_id, overdue, kind)
            if self.tools.existing(key):
                return
            if self.worker:
                raise ValueError("请先等待或取消当前模型请求，再调整排期")
            if kind == "暂停" and self.focus.task_id in {r["id"] for r in self.store.tasks(plan_id)} and (self.focus.session_id or self.focus.phase == "break"):
                answer = QMessageBox.question(self, "暂停计划前保存专注", "结束关联任务的专注或休息并保存用时，再查看暂停预览？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
                if answer != QMessageBox.StandardButton.Yes:
                    return
                self.focus.stop()
            dialog = ScheduleDialog(self.store, self, plan_id=plan_id, overdue=overdue, kind=kind)
            def finished(result):
                if result == QDialog.DialogCode.Accepted:
                    self.refresh()
                    self.notice.setText("排期已保存，任务内容和学习记录保持不变。可在需求与历史中查看。")
            self.tools.show(dialog, key, finished)
        self.safe(action)

    def pause_selected(self):
        if self.selected_plan_id:
            self.open_schedule(plan_id=self.selected_plan_id, kind="恢复" if paused(self.store.schedule(self.selected_plan_id)) else "暂停")

    def refresh_week(self):
        today = date.today()
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=self.week_offset)
        end = start + timedelta(days=6)
        self.week_heading.setText(f"{start:%Y年%m月%d日} — {end:%m月%d日}")
        self.week_panel.refresh(start)

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
        self.last_diagnostic = None
        self.diagnostic_button.hide()
        # 身份检查阻止已取消任务的排队信号污染新任务，即使信号已进入 Qt 事件队列。
        worker.signals.done.connect(lambda result: self.safe(lambda: done(result)) if self.worker is worker and not worker.cancelled.is_set() else None)
        worker.signals.failed.connect(lambda message: self.request_failed(worker, message))
        worker.signals.finished.connect(lambda: self.work_finished(worker))
        self.request_timeout_seconds = timeout_seconds
        self.request_started = monotonic()
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

    def cancel_request(self, category="cancelled"):
        if self.worker:
            diagnostic = None
            self.worker.cancelled.set()
            if self.active_gateway:
                control = self.active_gateway.control
                connection = getattr(self.active_gateway, "connection", None)
                diagnostic = ConnectionDiagnostic(operation=getattr(control, "operation", "request"), stage=control.stage,
                    elapsed_ms=round((monotonic() - self.request_started) * 1000),
                    http_status=control.response_status, category=category, exception_types=[],
                    network_mode=getattr(connection, "network_mode", "system"))
                self.active_gateway.cancel()
            self.work_finished(self.worker)
            if diagnostic:
                self.last_diagnostic = diagnostic
                self.diagnostic_button.show()
            self.notice.setText("已取消，可以发起新请求。未完成结果不会保存；服务端已收到的请求仍可能计费。")

    def request_timed_out(self):
        if self.worker:
            seconds = self.request_timeout_seconds
            status = self.active_gateway.control.response_status if self.active_gateway else None
            self.cancel_request("timeout")
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
        diagnostic = getattr(self.active_gateway, "last_diagnostic", None)
        if diagnostic:
            self.last_diagnostic = diagnostic
            self.diagnostic_button.show()
        self.request_timer.stop()
        self.request_status_timer.stop()
        self.request_countdown.clear()
        self.request_countdown.hide()
        self.worker = None
        self.active_gateway = None
        self.busy.clear()
        self.cancel_button.hide()

    def copy_diagnostic(self):
        if self.last_diagnostic:
            QApplication.clipboard().setText(self.last_diagnostic.summary())
            self.notice.setText("已复制脱敏连接诊断；不含密钥、认证头或学习内容。")

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
        self.planning_window = self.tools.open("planning", lambda: PlanningWindow(self, draft))

    def resume_draft(self):
        if self.tools.existing("drafts"):
            return
        dialog = DraftsDialog(self.store, self)
        self.tools.show(dialog, "drafts", lambda result: self.open_planning(dialog.selected)
                        if result == QDialog.DialogCode.Accepted and dialog.selected else None)

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

    def modify_plan(self, *, plan_id=None, task_ids=None):
        pid=plan_id if task_ids is not None else self.selected_plan_id
        if not pid:
            self.notice.setText("先选择计划和需要调整的范围。")
            return
        self.pause_focus()
        saved = self.store.get_plan(pid)
        selected = list(task_ids) if task_ids is not None else [item.data(0, Qt.ItemDataRole.UserRole) for item in self.plan_tree.selectedItems()]
        key=('modify-task',pid,tuple(selected)) if task_ids is not None else ('modify',pid)
        if self.tools.existing(key):
            return
        dialog = ModificationDialog(saved, selected, self)
        def begin():
            if self.store.get_plan(saved['id'])['revision'] != saved['revision']:
                raise ValueError('计划已有变化，请关闭调整范围窗口并重新选择，避免使用旧任务。')
            context = modification_context(saved, dialog.target_ids(saved, selected), self.store.reviews(saved["id"]))
            config = next((c for c in self.store.connections() if c.id == self.model_picker.currentData()), None)
            draft = PlanningDraft(kind="modify", profile=saved["profile"], start_date=date.fromisoformat(saved["start_date"]),
                weekdays=json.loads(saved["weekdays_json"]), connection=config,
                source_plan_id=saved["id"], source_revision=saved["revision"], target_ids=context["target_ids"],
                source_context={**context, "confirmed_requirements": self.store.brief(saved["id"])},
                request=dialog.intent.currentText() + "；" + dialog.text.toPlainText())
            self.open_planning(draft)
        self.tools.show(dialog, key, lambda result: begin() if result == QDialog.DialogCode.Accepted else None)

    def show_history(self):
        if self.selected_plan_id:
            pid = self.selected_plan_id
            self.tools.open(("requirements", pid), lambda: HistoryWindow(self.store, pid, self))

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
        self.edit_task_id(task_id)

    def edit_task_id(self, task_id, *, field=None):
        def focus_field(dialog):
            target=getattr(dialog,field,None) if field else None
            if target is not None and target.isEnabled():
                target.setFocus()
            return dialog
        if old := self.tools.existing(("task", task_id)):
            return focus_field(old)
        row = next((t for t in self.store.tasks() if t["id"] == task_id), None)
        if not row:
            return
        dialog = TaskDialog(row, locked=row["week"] in self.store.reviews(row["plan_id"]), parent=self, store=self.store)
        def finished(result):
            self.refresh()
            if result == 2 and row.get("manual"):
                self.delete_task_id(task_id,expected=row)
            elif result == QDialog.DialogCode.Accepted:
                self.notice.setText("任务与学习记录已保存。")
        self.tools.show(dialog, ("task", task_id), finished)
        return focus_field(dialog)

    def run_task_action(self, task_id, operation, *, source=None, expected=None, expected_details=None):
        from desktop.task_context import action_reason
        from desktop.learning_ui import StepsDialog
        from shiboken6 import isValid
        def run():
            row=next((r for r in self.store.tasks() if r['id']==task_id),None)
            if row is None:
                raise ValueError('任务已被删除，请刷新列表后重新选择。')
            if reason := action_reason(self.store,row,operation,self):
                raise ValueError(reason)
            if operation=='focus':
                self.start_focus_for_task(task_id)
            elif operation=='delete':
                self.delete_task_id(task_id,expected=expected,expected_details=expected_details)
                if source is not None and source is not self and isValid(source) and hasattr(source,'refresh'):
                    source.refresh()
            elif operation=='ai_modify':
                self.modify_plan(plan_id=row['plan_id'],task_ids=[task_id])
            elif operation=='steps':
                def changed(_):
                    self.refresh()
                    if source is not None and source is not self and isValid(source) and hasattr(source,'refresh'):
                        source.refresh()
                self.tools.open(('steps',task_id),lambda: StepsDialog(self.store,task_id,self),changed)
            else:
                if operation not in ('edit','due','status','note','reminder','ai_locked'):
                    raise ValueError('无法识别这项任务操作，请重新打开菜单。')
                dialog=self.edit_task_id(task_id,field=None if operation=='edit' else operation)
                if dialog is not None and source is not None and source is not self and isValid(source) and hasattr(source,'refresh'):
                    # Only one refresh hook per source/editor; reusing a window keeps unsaved edits.
                    sources=getattr(dialog,'_context_sources',set())
                    if id(source) not in sources:
                        sources.add(id(source))
                        dialog._context_sources=sources
                        dialog.finished.connect(lambda _: source.refresh() if isValid(source) else None)
        self.safe(run)

    def delete_task_id(self, task_id, *, expected=None, expected_details=None):
        from shiboken6 import isValid
        row=next((r for r in self.store.tasks() if r['id']==task_id),None)
        if row is None or not row.get('manual'):
            raise ValueError('此入口只支持删除仍存在的手动任务。')
        if expected is not None and row!=expected:
            raise ValueError('任务已有新变化，请重新打开菜单核对后再删除。')
        details=self.store.read('SELECT stage,steps_json FROM task_details WHERE task_id=?',(task_id,))
        if expected_details is not None and details!=expected_details:
            raise ValueError('任务分组或步骤已有新变化，请重新打开菜单核对后再删除。')
        if row.get('focus_active') or self.store.setting('focus_state',{}).get('task_id')==task_id:
            raise ValueError('请先结束关联的专注，再删除任务。')
        if any((w:=self.tools.windows.get((kind,task_id))) is not None and isValid(w) for kind in ('task','steps')):
            raise ValueError('请先关闭这项任务的编辑或步骤窗口，再删除，避免丢失未保存内容。')
        box=ConfirmationDialog('删除这项手动任务？',row['task'].title,self,
            note='任务及其专注、笔记、执行步骤和提醒记录会移入回收站，可在30天内恢复。其他任务不变。默认取消；删除前的每日备份仍保留。',
            confirm='删除这项任务',cancel='取消，保留任务')
        if box.exec()!=QDialog.DialogCode.Accepted:
            return
        current=next((r for r in self.store.tasks() if r['id']==task_id),None)
        if current!=row or details!=self.store.read('SELECT stage,steps_json FROM task_details WHERE task_id=?',(task_id,)):
            raise ValueError('确认期间任务已有新记录或修改，未删除。请刷新后重试。')
        self.store.delete_manual_task(task_id)
        self.refresh()
        self.notice.setText('手动任务已移入回收站，可在30天内恢复。入口：计划库 → 历史与数据 → 回收站。')

    def open_trash(self):
        from desktop.trash_ui import TrashDialog
        self.tools.open('trash', lambda: TrashDialog(self.store, self, changed=self.refresh))

    def add_manual_task(self, plan_id=None):
        from desktop.manual_tasks import ManualTaskDialog
        def finished(result):
            self.refresh()
            if result == QDialog.DialogCode.Accepted:
                self.notice.setText("任务已添加。今日页显示今天和逾期任务；未来任务可在周视图查看。")
        self.tools.open(("manual", plan_id), lambda: ManualTaskDialog(self.store, self, plan_id=plan_id), finished)

    def open_statistics(self):
        from desktop.learning_ui import StatisticsDialog
        self.tools.open("statistics", lambda: StatisticsDialog(self.store, self))

    def open_recurring(self):
        from desktop.learning_ui import RecurringDialog
        self.tools.open("recurring", lambda: RecurringDialog(self.store, self), lambda _: self.refresh())

    def open_structure(self):
        from desktop.learning_ui import StructureDialog
        if self.selected_plan_id or self.library_standalone:
            pid = self.selected_plan_id
            self.tools.open(("structure", pid), lambda: StructureDialog(self.store, pid, self), lambda _: self.refresh())

    def open_steps(self, item):
        from desktop.learning_ui import StepsDialog
        if item is None:
            self.notice.setText("先在任务列表选择一项任务，再管理它的执行步骤。")
            return
        tid = item.data(0, Qt.ItemDataRole.UserRole)
        self.tools.open(("steps", tid), lambda: StepsDialog(self.store, tid, self), lambda _: self.refresh())

    def refresh_focus_status(self):
        """Update live labels without rebuilding tables or losing selections."""
        rows = {r["id"]: r for r in self.store.tasks()}
        for tree, column in ((self.today_tree, 3), (self.plan_tree, 3), (self.week_tree, 5)):
            for i in range(tree.topLevelItemCount()):
                item = tree.topLevelItem(i)
                row = rows.get(item.data(0, Qt.ItemDataRole.UserRole))
                if row:
                    item.setText(column, display_status(row, date.today()))
                    item.setToolTip(column, focus_tooltip(row))
                    item.setData(column, FOCUS_ROLE, row["focus_seconds"])
        for card in self.week_panel.cards:
            row = rows.get(card.task_id)
            if row:
                card.status.setText(display_status(row, date.today()))
                from desktop.focus_tip import set_focus_hint
                set_focus_hint(card.status, row)
                card.status.setProperty("taskState", card.status.text())
                card.status.style().unpolish(card.status)
                card.status.style().polish(card.status)

    def rename_selected(self):
        if not self.selected_plan_id:
            return
        pid = self.selected_plan_id
        if self.tools.existing(('rename', pid)):
            return
        saved = self.store.get_plan(pid)
        dialog = QInputDialog()
        dialog.setWindowTitle('重命名计划 · 学习罗盘')
        dialog.setLabelText('计划名称')
        dialog.setTextValue(saved['title'])
        dialog.setOkButtonText('保存名称')
        dialog.setCancelButtonText('取消')
        dialog.resize(520, 180)
        def finished(result):
            if result == QDialog.DialogCode.Accepted:
                if self.store.get_plan(pid)['revision'] != saved['revision']:
                    raise ValueError('计划已有变化，请重新打开重命名窗口；未覆盖新内容。')
                self.store.rename_plan(pid, dialog.textValue())
                self.refresh()
        self.tools.show(dialog, ('rename', pid), finished)

    def archive_selected(self):
        if self.selected_plan_id:
            self.safe(lambda: self.store.archive(self.selected_plan_id, not self.archived.isChecked()))
            self.refresh()

    def show_plan_menu(self):
        self.delete_plan_action.setEnabled(bool(self.selected_plan_id))
        self.plan_more_menu.popup(self.plan_more_button.mapToGlobal(self.plan_more_button.rect().bottomLeft()))

    def delete_selected(self):
        if not self.selected_plan_id:
            return
        plan_id = self.selected_plan_id
        def remove():
            saved = self.store.get_plan(plan_id)
            reason = ""
            if self.worker:
                reason = "请先完成或取消当前模型请求，再删除计划。"
            elif self.focus.task_id in {t["id"] for t in saved["tasks"]} and (self.focus.session_id or self.focus.phase == "break"):
                reason = "请先到专注页结束这份计划关联的专注或休息，再删除计划。"
            dialog = DeletePlanDialog(saved["title"], len(saved["tasks"]),
                sum(t["status"] == "已完成" for t in saved["tasks"]), self, blocking_reason=reason)
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.confirmed:
                return
            backup = self.store.delete_plan(plan_id, expected_revision=saved["revision"])
            if self.selected_plan_id == plan_id:
                self.selected_plan_id = None
            self.refresh()
            self.notice.setText(f"计划已移入回收站，可在30天内恢复。入口：历史与数据 → 回收站。删除前备份：{backup.name}")
        self.safe(remove)

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
                dialog = PreviewDialog(preview, saved["plan"], self)
                def apply(result):
                    if result == QDialog.DialogCode.Accepted:
                        self.store.apply_review(plan_id, preview["week"], preview["summary"], preview["adjusted"], preview["revision"], preview["provenance"])
                        self.refresh()
                        self.notice.setText("复盘已保存，后续任务已更新。")
                self.tools.show(dialog, ("review", plan_id), apply)
            self.start_work("正在整理复盘与调整预览…", lambda cancel: prepare_review(self.store, plan_id, gateway, cancelled=cancel), done, gateway)
        self.safe(begin)

    def undo_selected(self):
        if self.selected_plan_id:
            def show():
                pid = self.selected_plan_id
                self.history_replay_window = self.tools.open(("history", pid), lambda: PlanHistoryWindow(self, pid))
            self.safe(show)

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
        start = self.import_start()
        def action():
            if start:
                self.selected_plan_id = self.store.import_plan(Path(path).read_bytes(), start, confirm_limits=True)
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
            self.budget_settings.reload()
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
        key_id = ("connection", config.id if config else None)
        if self.tools.existing(key_id):
            return
        dialog = ConnectionDialog(config, bool(config and self.credentials.get(config.id)), self)
        def finished(result):
            if result != QDialog.DialogCode.Accepted:
                return
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
        self.tools.show(dialog, key_id, finished)

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
                dialog = ModelSelectionDialog(models, self)
                def selected(result):
                    if result == QDialog.DialogCode.Accepted and dialog.model.currentText().strip():
                        current = next((c for c in self.store.connections() if c.id == config.id), None)
                        if current != config:
                            raise ValueError('连接配置已改变，请重新获取模型列表。')
                        updated = config.model_copy(update={"model": dialog.model.currentText().strip()})
                        self.store.save_connection(updated)
                        self.refresh()
                self.tools.show(dialog, ('models', config.id), selected)
            self.start_work("正在获取服务商模型列表…", lambda cancel: gateway.list_models(cancelled=cancel), done, gateway, timeout_seconds=MODELS_TIMEOUT)
        self.safe(action)

    def refresh_focus_tasks(self):
        selected = self.focus.task_id or self.focus_task.currentData()
        self.focus_task.clear()
        self.focus_task.addItem("不关联任务，只记录专注", None)
        for row in self.store.tasks():
            if not row["archived"] and not row.get("paused") and (row["status"] != "已完成" or row["id"] == selected):
                self.focus_task.addItem(f"{row['task'].title} · {row['plan_title']}", row["id"])
        index = self.focus_task.findData(selected)
        self.focus_task.setCurrentIndex(max(0, index))

    def start_focus(self):
        def action():
            if self.focus_task.currentData() and self.focus.phase == 'focus' and not self.focus.session_id:
                self.start_focus_for_task(self.focus_task.currentData())
                return
            self.focus.configure(self.focus_minutes.value(), self.break_minutes.value())
            self.focus.start(self.focus_task.currentData())
            self.tick()
        self.safe(action)

    def start_focus_for_task(self, task_id):
        def action():
            row = next((t for t in self.store.tasks() if t["id"] == task_id), None)
            if not row or row["archived"] or row.get("paused") or row["status"] == "已完成":
                raise ValueError("这项任务已完成、归档或删除，请刷新任务列表。")
            same = self.focus.phase == "focus" and self.focus.task_id == task_id and self.focus.session_id is not None
            if not same and (self.focus.session_id or self.focus.phase == "break"):
                answer = QMessageBox.question(self, "切换专注任务", "结束当前专注或休息，保存已有用时，并开始这项任务？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Cancel)
                if answer != QMessageBox.StandardButton.Yes:
                    return
                self.focus.stop()
            self.refresh_focus_tasks()
            self.focus_task.setCurrentIndex(self.focus_task.findData(task_id))
            if not same:
                minutes = row['task'].duration_minutes
                self.focus_minutes.setValue(minutes)
                self.focus.configure(minutes, self.break_minutes.value())
            self.focus.start(task_id)
            self.navigate(3)
            self.tick()
        self.safe(action)

    def pause_focus(self):
        self.focus.pause()
        self.tick()
        self.refresh_focus_status()

    def stop_focus(self):
        self.focus.stop()
        self.tick()
        self.refresh_today()
        self.refresh_focus_status()

    def tick(self):
        if self.display_date != date.today():
            self.display_date = date.today()
            self.store.expire_pauses()
            self.refresh_today()
            self.refresh_library()
            self.refresh_week()
        committed = self.focus.last_commit
        event = self.focus.tick()
        if committed != self.focus.last_commit:
            self.refresh_focus_status()
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
        if not self.tools.close_all():
            return
        if self.planning_window and not self.planning_window.closed:
            if not self.planning_window.close():
                return
        self.focus.pause()
        self.cancel_request()
        close_guide()
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
            if not self.tools.close_all():
                event.ignore()
                return
            if self.planning_window and not self.planning_window.closed:
                if not self.planning_window.close():
                    event.ignore()
                    return
            self.focus.pause()
            self.cancel_request()
            close_guide()
            event.accept()
