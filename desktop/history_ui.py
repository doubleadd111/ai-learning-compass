"""A local seven-day timeline with explicit before/after replay previews."""
import sqlite3
from pydantic import ValidationError
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel,
    QPushButton, QScrollArea, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from desktop.controls import SoftComboBox
from desktop.confirmations import ConfirmationDialog, text_label
from desktop.guide import open_guide
from desktop import plan_history


class PlanHistoryWindow(QDialog):
    def __init__(self, owner, plan_id):
        super().__init__(owner, Qt.WindowType.Window)
        self.owner, self.store, self.plan_id = owner, owner.store, plan_id
        self.current_preview = None
        self.setWindowTitle("调整历史与回溯 · 学习罗盘")
        self.resize(1080, 740)
        self.setMinimumSize(760, 540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        layout.setSpacing(14)
        heading = QHBoxLayout()
        heading.addWidget(text_label("找到想回去的那一步", "brand"), 1)
        help_button = QPushButton("使用说明")
        help_button.clicked.connect(lambda: open_guide(self, "modify"))
        self.help_shortcut = QShortcut(QKeySequence("F1"), self)
        self.help_shortcut.activated.connect(lambda: open_guide(self, "modify"))
        heading.addWidget(help_button)
        layout.addLayout(heading)
        self.plan_name = text_label("", "confirmationSection")
        layout.addWidget(self.plan_name)
        layout.addWidget(text_label("最近7天的安排调整。回溯只改内容、日期和计划设置，保留当前完成状态、笔记、复盘与用时。", "muted"))
        self.splitter = QSplitter()
        self.list = QTreeWidget()
        self.list.setRootIsDecorated(False)
        self.list.setHeaderLabels(["保存时间", "调整"])
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.list.setMinimumWidth(270)
        self.list.setWordWrap(True)
        self.list.currentItemChanged.connect(lambda *_: self.show_preview())
        self.splitter.addWidget(self.list)
        right = QWidget()
        box = QVBoxLayout(right)
        box.setContentsMargins(8, 0, 0, 0)
        box.setSpacing(12)
        self.target = text_label("请选择一条调整记录", "confirmationSection")
        box.addWidget(self.target)
        self.side = SoftComboBox()
        self.side.addItem("回到这次调整之后", "after")
        self.side.addItem("回到这次调整之前", "before")
        self.side.currentIndexChanged.connect(lambda *_: self.show_preview())
        box.addWidget(self.side)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        box.addWidget(self.scroll, 1)
        self.splitter.addWidget(right)
        self.splitter.setSizes([330, 700])
        layout.addWidget(self.splitter, 1)
        self.status = text_label("", "muted")
        layout.addWidget(self.status)
        layout.addWidget(text_label("回溯前的当前状态会保存为新历史，可再次选择。旧版未记录的操作无法补造；不会删除更晚的历史，也不会回退全局预算。", "confirmationNote"))
        row = QHBoxLayout()
        refresh = QPushButton("刷新历史与预览")
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        row.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.close)
        row.addWidget(close)
        self.restore = QPushButton("回溯到所选状态")
        self.restore.setObjectName("primary")
        self.restore.setAutoDefault(False)
        self.restore.clicked.connect(self.apply)
        row.addWidget(self.restore)
        layout.addLayout(row)
        self.refresh()

    def refresh(self):
        try:
            self.plan_name.setText("计划：" + self.store.get_plan(self.plan_id)["title"])
        except ValueError:
            self.plan_name.setText("这份计划已不存在，请关闭此窗口。")
        selected = self.list.currentItem().data(0, Qt.ItemDataRole.UserRole) if self.list.currentItem() else None
        self.list.blockSignals(True)
        self.list.clear()
        for entry in plan_history.recent_versions(self.store, self.plan_id):
            when = plan_history.stamp(entry["created_at"]).astimezone().strftime("%Y-%m-%d\n%H:%M:%S")
            item = QTreeWidgetItem([when, entry["kind"]])
            item.setData(0, Qt.ItemDataRole.UserRole, entry["id"])
            item.setToolTip(1, entry["kind"])
            self.list.addTopLevelItem(item)
            if entry["id"] == selected:
                self.list.setCurrentItem(item)
        if not self.list.currentItem() and self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0))
        self.list.blockSignals(False)
        self.show_preview()

    def show_preview(self):
        self.current_preview = None
        self.restore.setEnabled(False)
        contents = QWidget()
        box = QVBoxLayout(contents)
        box.setContentsMargins(0, 0, 12, 0)
        box.setSpacing(12)
        item = self.list.currentItem()
        try:
            if not item:
                self.target.setText("最近7天没有已保存的调整")
                box.addWidget(text_label("从这次更新开始，重命名、归档、任务编辑、增删、排期、暂停、AI调整和回溯都会记录在这里。普通打卡和计时不产生安排版本。", "muted"))
                self.status.setText("其他旧历史仍保留在本机，不因打开窗口而删除。")
            else:
                self.target.setText(item.text(1) + " · " + item.text(0).replace("\n", " "))
                p = plan_history.preview(self.store, self.plan_id, item.data(0, Qt.ItemDataRole.UserRole), self.side.currentData())
                self.current_preview = p
                for change in p.changes:
                    card = QFrame()
                    card.setObjectName("confirmationCard")
                    columns = QVBoxLayout(card)
                    columns.setContentsMargins(16, 14, 16, 14)
                    columns.setSpacing(8)
                    columns.addWidget(text_label(change["name"], "confirmationSection"))
                    columns.addWidget(text_label("当前", "muted"))
                    columns.addWidget(text_label(change["before"]))
                    columns.addWidget(text_label("回溯后", "muted"))
                    columns.addWidget(text_label(change["after"]))
                    box.addWidget(card)
                for message in p.notices:
                    box.addWidget(text_label(message, "confirmationNote"))
                if p.overloads:
                    box.addWidget(text_label("回溯后的安排中存在超限；新增超限会再次询问，默认返回调整。已有超限不重复确认。", "budgetWarning"))
                    for r in p.overloads:
                        if r["source"] == "计划上限":
                            text = f"{r['title']} · {r['period']}（按{r['kind']}统计）：{r['total']} / {r['limit']} 分钟，超出 {r['over']} 分钟"
                        else:
                            text = f"{r['date']} · 每日总预算：{r['total']} / {r['budget']} 分钟，超出 {r['over']} 分钟\n" + "、".join(f"{p['title']} {p['minutes']}分钟" for p in r["plans"].values())
                        box.addWidget(text_label(text, "confirmationNote"))
                self.status.setText(f"{len(p.changes)} 处变化 · {len(p.notices)} 项保留说明" if p.changes else
                    "差异已按保护规则保留，没有可应用的变化。" if p.notices else "当前安排与所选状态相同，无需回溯。")
                self.restore.setEnabled(bool(p.changes))
        except ValidationError:
            self.status.setText("这条历史的任务资料未通过本地校验，请选择其他历史或检查备份副本。")
        except sqlite3.Error:
            self.status.setText("暂时无法读取或预览历史，请稍后刷新；当前安排没有改变。")
        except (ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
            self.status.setText(str(error) if isinstance(error, ValueError) else "这条历史不完整，请选择其他历史或使用备份副本检查。")
        box.addStretch()
        old = self.scroll.takeWidget()
        if old:
            old.hide()
            old.deleteLater()
        self.scroll.setWidget(contents)

    def apply(self):
        p = self.current_preview
        if not p:
            return
        if self.owner.worker:
            self.status.setText("请先完成或取消当前模型请求，再重新预览回溯。")
            return
        dialog = ConfirmationDialog("回溯这份计划？", f"将应用预览中的 {len(p.changes)} 处安排变化。当前学习记录保持不变。", self,
            note="当前状态会另存为新历史并创建本机备份。回溯后仍可切回，不会删除其他历史或其他计划。",
            confirm="确认回溯", cancel="取消，保留当前安排")
        dialog.content.addWidget(text_label(self.plan_name.text(), "confirmationSection"))
        dialog.content.addWidget(text_label(self.target.text()))
        dialog.content.addWidget(text_label(self.side.currentText()))
        dialog.content.addStretch()
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        from desktop.schedule_ui import budget_action
        try:
            applied = budget_action(self, self.store, lambda: plan_history.apply(self.store, p))
        except (ValueError, OSError) as error:
            self.status.setText(str(error))
            self.current_preview = None
            self.restore.setEnabled(False)
            return
        except sqlite3.Error:
            self.status.setText("无法保存回溯，已取消本次更改。请检查数据目录或稍后重新预览。")
            self.current_preview = None
            self.restore.setEnabled(False)
            return
        if applied:
            self.owner.refresh()
            self.refresh()
            self.status.setText("已回溯。回溯前的状态已保存，选最新“回溯历史”的调整前可切回。")
        else:
            self.status.setText("已取消超限确认，当前安排未改变。")
