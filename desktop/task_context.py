"""Single-task context menus. Hit-test first, capture a stable ID, never write here."""
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication, QTreeWidget, QWidget
from shiboken6 import isValid
from desktop.action_menu import ActionMenu
from desktop.planning import protection_reason


def action_reason(store, row, action, host=None):
    reviewed = row['week'] in store.reviews(row['plan_id'])
    if action=='focus':
        return ('请从主窗口打开任务' if host is None else '计划已归档' if row['archived'] else
                '计划已暂停' if row.get('paused') else '任务已完成' if row['status']=='已完成' else '')
    if action in ('due','status') and reviewed and not row.get('manual'):
        return '本周已复盘'
    if action=='ai_modify':
        return ('计划已归档' if row['archived'] else '计划已暂停' if row.get('paused') else
                '请先结束当前模型请求' if host is not None and host.worker else
                protection_reason(row,store.reviews(row['plan_id'])))
    if action=='ai_locked':
        return '手动任务已受保护' if row.get('manual') else protection_reason({**row,'ai_locked':0},store.reviews(row['plan_id']))
    if action=='delete':
        if not row.get('manual'):
            return '仅支持手动任务'
        if row.get('focus_active') or store.setting('focus_state',{}).get('task_id')==row['id']:
            return '请先结束关联专注'
        if host is not None and any((w:=host.tools.windows.get((kind,row['id']))) is not None and isValid(w) for kind in ('task','steps')):
            return '请先关闭编辑窗口'
        return ''
    return ''


class TaskContext(QObject):
    def __init__(self, view, source):
        super().__init__(view)
        self.view, self.source, self.menu = view, source, None
        view.installEventFilter(self)
        if isinstance(view,QTreeWidget):
            view.viewport().installEventFilter(self)
        else:
            for child in view.findChildren(QWidget):
                child.installEventFilter(self)

    def host(self):
        hub=getattr(QApplication.instance(),'_learning_tool_windows',None)
        host=hub.host if hub else None
        return host if host is not None and isValid(host) and not host.exiting and host.store is self.source.store else None

    def open(self, global_pos, *, keyboard=False):
        if self.menu is not None and isValid(self.menu):
            self.menu.hide()
            self.menu.deleteLater()
            self.menu=None
        if isinstance(self.view,QTreeWidget):
            item=self.view.currentItem() if keyboard else self.view.itemAt(self.view.viewport().mapFromGlobal(global_pos))
            tid=item.data(0,Qt.ItemDataRole.UserRole) if item else None
            if not tid:
                return False
            self.view.clearSelection()
            self.view.setCurrentItem(item)
            item.setSelected(True)
        else:
            tid=self.view.task_id
            self.view.select(tid)
        row=next((r for r in self.source.store.tasks() if r['id']==tid),None)
        if row is None:
            return False
        from desktop.focus_tip import focus_tips
        if focus_tips().popup is not None:
            focus_tips().popup.hide()
        host=self.host()
        menu=self.menu=ActionMenu(self.view,scrollable=True)
        menu.task_id=tid
        menu.setAccessibleName('任务操作：'+row['task'].title)
        menu.section('当前任务：'+row['task'].title[:48]+('…' if len(row['task'].title)>48 else ''))
        menu.section('内容和学习')
        for key,title in (('view','查看任务详情…'),('edit','编辑任务／填写记录…'),('due','修改安排日期…'),('status','修改完成状态…'),
                          ('note','填写笔记／补记用时…'),('focus','开始专注')):
            self.add(menu,host,row,key,title)
        menu.section('提醒和整理')
        for key,title in (('reminder','设置任务提醒…'),('steps','任务分组与执行步骤…'),
                          ('dependencies','设置前置任务…'),
                          ('attention','优先级与今天重点…'),('study_log','补记实际学习日期…'),('ai_locked','设置 AI 修改保护…')):
            self.add(menu,host,row,key,title)
        self.add(menu,host,row,'ai_modify','让 AI 调整这项任务…')
        menu.section('删除 · 仍需确认')
        self.add(menu,host,row,'delete','删除这项任务…',danger=True)
        menu.popup(global_pos)
        return True

    def add(self, menu, host, row, key, title, **options):
        reason=action_reason(self.source.store,row,key,host) or ('' if host else '请从主窗口打开任务')
        label=title+('\n'+reason if reason else '')
        details=self.source.store.read('SELECT stage,steps_json FROM task_details WHERE task_id=?',(row['id'],)) if key=='delete' else None
        action=menu.add_action(label,lambda _=False: host.run_task_action(row['id'],key,source=self.source,expected=row,expected_details=details) if host and isValid(host) and not host.exiting else None,**options)
        action.setData(key)
        action.setEnabled(not reason)
        action.setStatusTip(reason)
        button=menu.items[-1][1]
        button.setAccessibleDescription(reason or '只操作当前右键的这一项任务；编辑后仍需保存。')

    def eventFilter(self, watched, event):
        if event.type()==QEvent.Type.ContextMenu:
            keyboard=event.reason()==QContextMenuEvent.Reason.Keyboard
            point=event.globalPos()
            if keyboard:
                point=self.keyboard_position()
            self.open(point,keyboard=keyboard)
            return True
        if event.type()==QEvent.Type.KeyPress and (event.key()==Qt.Key.Key_Menu or
                event.key()==Qt.Key.Key_F10 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self.open(self.keyboard_position(),keyboard=True)
            return True
        if event.type() in (QEvent.Type.Hide,QEvent.Type.Close) and watched is self.view and self.menu is not None and isValid(self.menu):
            self.menu.hide()
        return False

    def keyboard_position(self):
        if isinstance(self.view,QTreeWidget):
            item=self.view.currentItem()
            return self.view.viewport().mapToGlobal(self.view.visualItemRect(item).center()) if item else self.view.mapToGlobal(QPoint())
        return self.view.mapToGlobal(self.view.rect().center())


def bind_task_menu(view, source):
    view.task_context=TaskContext(view,source)
    return view.task_context
