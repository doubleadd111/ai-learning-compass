"""Recovery uses the same ownerless tools, cards and Chinese confirmations."""
from datetime import datetime
import json
from PySide6.QtCore import Qt, QDate, QSignalBlocker
from PySide6.QtWidgets import QCheckBox, QDialog, QLineEdit, QTreeWidgetItem, QTextBrowser
from desktop.learning_ui import ToolDialog, action, row, text, tree
from desktop.confirmations import ConfirmationDialog, text_label
from desktop.schedule_ui import budget_action
from desktop.scheduling import report_text, plan_limit_text
from desktop.ui import readable_document
from desktop.controls import SoftComboBox
from desktop.dialogs import date_edit
from desktop.tool_windows import open_tool
from desktop import trash


class PurgeDialog(ConfirmationDialog):
    def __init__(self, title, parent=None):
        super().__init__('彻底删除此项目？', title, parent,
            note='这会从回收站清除此项目及所含记录，不能在这里撤销。已有整库备份不会一并删除。',
            confirm='彻底删除', cancel='取消，继续保留')
        self.content.addWidget(text_label('请输入“彻底删除”四个字，再点击按钮。'))
        self.phrase = QLineEdit()
        self.phrase.setAccessibleName('彻底删除确认文字')
        self.phrase.setPlaceholderText('彻底删除')
        self.content.addWidget(self.phrase)
        self.content.addStretch()
        self.confirm_button.setObjectName('danger')
        self.confirm_button.setEnabled(False)
        self.phrase.textChanged.connect(lambda value: self.confirm_button.setEnabled(value == '彻底删除'))

    def accept(self):
        if self.phrase.text() == '彻底删除':
            super().accept()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.reject()
        else:
            super().keyPressEvent(event)


class TrashDialog(ToolDialog):
    def __init__(self, store, parent=None, changed=None):
        super().__init__(store, '回收站',
            '误删项目可在30天内恢复。单项任务可选择恢复后的安排日期；双击查看删除时的详细信息。',
            parent, chapter='trash')
        self.changed = changed or (lambda: None)
        self.proposal = None
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索已删除的计划或任务名称')
        self.search.setAccessibleName('搜索回收站')
        self.expired = QCheckBox('查看已过期')
        self.box.addWidget(row(self.search, self.expired, action('刷新', self.refresh)))
        self.hint = text('')
        self.box.addWidget(self.hint)
        self.items = tree(['名称 / 类型', '删除时间', '恢复截止'])
        self.items.setAccessibleName('已删除项目')
        self.box.addWidget(self.items, 1)
        self.date_mode = SoftComboBox()
        self.date_mode.addItems(['按原日期恢复', '指定安排日期'])
        self.date_mode.setAccessibleName('恢复日期方式')
        self.restore_date = date_edit()
        self.restore_date.setAccessibleName('恢复后的任务安排日期')
        self.date_controls = row(text('恢复后安排在'), self.date_mode, self.restore_date)
        self.box.addWidget(self.date_controls)
        self.date_hint = text('')
        self.box.addWidget(self.date_hint)
        self.details = QTextBrowser()
        self.details.setAccessibleName('恢复预览及冲突说明')
        readable_document(self.details)
        self.details.setMinimumHeight(110)
        self.box.addWidget(self.details, 1)
        self.box.addWidget(self.error)
        self.restore_button = action('恢复选中项目', self.restore, True)
        self.purge_button = action('彻底删除…', self.purge)
        self.purge_button.setObjectName('danger')
        self.box.addWidget(row(self.purge_button, action('查看详情', self.show_detail), action('查看恢复预览', self.preview),
                               self.restore_button, action('关闭', self.close)))
        self.search.textChanged.connect(self.refresh)
        self.expired.toggled.connect(self.refresh)
        self.items.currentItemChanged.connect(lambda *_: self.selection_changed())
        self.items.itemDoubleClicked.connect(lambda item, _: self.show_detail(item.data(0, Qt.ItemDataRole.UserRole)))
        self.date_mode.currentIndexChanged.connect(self.preview)
        self.restore_date.dateChanged.connect(self.preview)
        self.refresh()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'date_hint'):
            # Keep the original/new dates and confirmation reachable on a short
            # window; the same explanations remain in the preview and guide.
            compact = self.height() < 640
            self.subtitle.setVisible(not compact)
            self.date_hint.setVisible(not compact)

    def selected_id(self):
        item = self.items.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def refresh(self, *_):
        selected = self.selected_id()
        self.items.blockSignals(True)
        self.items.clear()
        rows = trash.entries(self.store)
        keyword = self.search.text().strip().casefold()
        shown = []
        for r in rows:
            expired = trash.clock() >= datetime.fromisoformat(r['expires_at'])
            if (expired and not self.expired.isChecked()) or keyword not in r['title'].casefold():
                continue
            def stamp(value):
                return datetime.fromisoformat(value).astimezone().strftime('%Y-%m-%d %H:%M')
            item = QTreeWidgetItem([r['title'] + (' · 计划' if r['kind']=='plan' else ' · 手动任务'),
                stamp(r['deleted_at']), ('已过期 · ' if expired else '') + stamp(r['expires_at'])])
            item.setData(0, Qt.ItemDataRole.UserRole, r['id'])
            self.items.addTopLevelItem(item)
            shown.append(item)
            if r['id'] == selected:
                self.items.setCurrentItem(item)
        if not self.items.currentItem() and shown:
            self.items.setCurrentItem(shown[0])
        self.items.blockSignals(False)
        self.hint.setText(f'显示 {len(shown)}/{len(rows)} 项 · 双击查看详情。“恢复截止”是找回期限，不是任务安排日期。')
        self.selection_changed(reset=self.selected_id() != selected)

    def selection_changed(self, *_args, reset=True):
        data = self.attempt(lambda: trash.detail(self.store, self.selected_id())) if self.selected_id() else None
        single = bool(data and data['entry']['kind']=='task')
        self._single_task = single
        self.date_controls.setVisible(single)
        if single and reset:
            with QSignalBlocker(self.date_mode), QSignalBlocker(self.restore_date):
                self.date_mode.setCurrentIndex(0)
                self.restore_date.setDate(QDate.fromString(data['rows']['tasks'][0]['due_date'], 'yyyy-MM-dd'))
        self.date_hint.setText('指定新日期只更改安排日，状态和学习记录保留；恢复前会重新检查时间占用。' if single
            else '整份计划按原排期恢复；如需批量改期，请恢复后使用“调整排期”。' if data else '')
        self.preview()

    def show_detail(self, entry_id=None):
        entry_id = entry_id or self.selected_id()
        if entry_id:
            self.attempt(lambda: open_tool(self, TrashDetailDialog(self.store, entry_id, self), ('trash-detail', entry_id)))

    def preview(self, *_):
        self.proposal = None
        self.error.hide()
        self.restore_button.setEnabled(False)
        self.purge_button.setEnabled(bool(self.selected_id()))
        self.details.setPlainText('回收站为空。删除计划或手动任务后会出现在这里。' if not trash.entries(self.store)
            else '请选择一项已删除的项目，查看恢复内容。')
        if not self.selected_id():
            self.restore_date.setEnabled(False)
            return
        target = self.restore_date.date().toString('yyyy-MM-dd') if getattr(self, '_single_task', False) and self.date_mode.currentIndex()==1 else None
        self.restore_date.setEnabled(target is not None)
        proposal = self.attempt(lambda: trash.preview(self.store, self.selected_id(), target_date=target))
        if proposal is None:
            return
        self.proposal = proposal
        content = proposal.summary
        if proposal.conflicts:
            content = '暂时不能恢复\n' + '\n'.join(proposal.conflicts)
        else:
            if proposal.plan_rows:
                content += '\n\n恢复后超过计划时间上限（提交时还需确认）\n' + plan_limit_text(proposal.plan_rows)
            if proposal.budget_rows:
                content += '\n\n恢复后超过跨计划总预算（提交时还需确认）\n' + report_text(proposal.budget_rows)
        self.details.setPlainText(content)
        self.restore_button.setEnabled(not proposal.conflicts)

    def restore(self):
        self.restore_date.interpretText()
        proposal = self.proposal
        if not proposal or proposal.conflicts:
            return
        box = ConfirmationDialog('恢复选中项目？', proposal.title, self,
            note='按预览中的日期恢复，保留原状态和学习记录，不会覆盖当前任务。默认取消；时间超限还需另外确认。',
            confirm='确认恢复', cancel='取消')
        box.content.addWidget(text_label(proposal.summary, 'muted'))
        box.content.addStretch()
        if box.exec() != QDialog.DialogCode.Accepted:
            return
        result = self.attempt(lambda: budget_action(self, self.store, lambda: trash.apply(self.store, proposal)))
        if result:
            self.changed()
            self.refresh()
            self.details.setPlainText('已按预览中的日期恢复，原状态和学习记录保留。可在计划库、独立任务箱或对应日期的周视图中查找。')
        elif result is None and self.error.isVisible():
            self.proposal = None
            self.restore_button.setEnabled(False)

    def purge(self):
        proposal = self.proposal
        if not proposal:
            return
        box = PurgeDialog(proposal.title, self)
        if box.exec() != QDialog.DialogCode.Accepted:
            return
        result = self.attempt(lambda: (trash.purge(self.store, proposal.entry_id, proposal.token, box.phrase.text()), True)[1])
        if result:
            self.refresh()
            self.details.setPlainText('已从回收站彻底删除，不能在这里撤销。已有整库备份未删除。')


class TrashDetailDialog(ToolDialog):
    """A deleted snapshot is read-only, including after expiry or ID reuse."""
    def __init__(self, store, entry_id, parent=None):
        data = trash.detail(store, entry_id)
        entry, rows = data['entry'], data['rows']
        super().__init__(store, '已删除项目详情', '这是删除时的只读快照，不会恢复任务，也不代表当前计划库的内容。',
                         parent, chapter='trash')
        self.entry_id = entry_id
        self.body = QTextBrowser()
        self.body.setAccessibleName('删除时的任务详细信息，只读')
        readable_document(self.body)
        self.box.addWidget(self.body, 1)
        def stamp(value):
            return datetime.fromisoformat(value).astimezone().strftime('%Y-%m-%d %H:%M:%S')
        lines = [entry['title'], f"所属：{data['owner']}", f"删除时间：{stamp(entry['deleted_at'])}",
                 f"可恢复截止：{stamp(entry['expires_at'])}"]
        if trash.clock() >= datetime.fromisoformat(entry['expires_at']):
            lines.append('已过期：可以查看，但不能在回收站恢复。')
        if entry['kind']=='plan':
            plan = rows['plans'][0]
            lines.extend(['', '计划说明', plan['overview'], f"原计划开始日：{plan['start_date']}"])
        details = {r['task_id']: r for r in rows['task_details']}
        def duration(seconds):
            return f'{seconds//3600} 小时 {seconds//60%60} 分 {seconds%60} 秒'
        for t in sorted(rows['tasks'], key=lambda t: (t['due_date'], t['id'])):
            payload = json.loads(t['payload_json'])
            lines.extend(['', '任务：' + payload['title'], f"原安排日期：{t['due_date']} · 状态：{t['status']}",
                f"预计用时：{payload['duration_minutes']} 分钟", '', '具体说明', payload['description'] or '未填写',
                '', '完成标准', payload['deliverable'] or '未填写', '', '学习记录',
                '累计专注：' + duration(t['focus_seconds']), '人工补记：' + duration(t['manual_seconds']),
                '实际总用时：' + duration(t['focus_seconds']+t['manual_seconds']),
                '难度：' + (str(t['difficulty']) if t['difficulty'] is not None else '未填写'),
                '笔记：' + (t['note'] or '未填写'), '提醒时间：' + (t['remind_at'] or '未设置')])
            d = details.get(t['id'])
            lines.extend(['', '任务分组：' + (d['stage'] if d and d['stage'] else '未分组'), '执行步骤'])
            steps = json.loads(d['steps_json']) if d else []
            lines.extend([f"{i+1}. {'已做' if step['done'] else '未做'} · {step['title']}" for i, step in enumerate(steps)] or ['未添加步骤'])
        self.body.setPlainText('\n'.join(lines))
        self.box.addWidget(row(action('关闭', self.close)))
