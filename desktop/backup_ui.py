"""Backup catalog and explicit whole-database recovery."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout,QTreeWidgetItem,QFileDialog,QDialog
from desktop.workflow_ui import UtilityDialog,task_list,action
from desktop.confirmations import ConfirmationDialog,text_label
from desktop import backup_manager
from desktop.controls import SoftComboBox


class BackupDialog(UtilityDialog):
    def __init__(self,host):
        super().__init__(host,'备份管理','整库恢复会覆盖全部当前资料，包括学习记录和设置。先查看差异，确认后自动备份当前状态；API Key不在备份中。')
        self.proposal=None
        filters=QHBoxLayout();filters.addWidget(text_label('备份类型'))
        self.kind_filter=SoftComboBox();self.kind_filter.setAccessibleName('按备份类型筛选')
        for title,key in [('全部备份','all'),('自动备份：每日','daily'),('手动备份','manual'),('自动备份：操作前恢复点','guard'),('其他／来源未记录','other')]:
            self.kind_filter.addItem(title,key)
        filters.addWidget(self.kind_filter);filters.addStretch();self.box.addLayout(filters)
        self.space=text_label('','muted');self.box.addWidget(self.space)
        self.tree=task_list(['备份类型／时间','创建程序／数据结构','内容']);self.box.addWidget(self.tree,1)
        self.box.addWidget(text_label('创建程序是生成备份的版本，不是升级前的旧版。旧版本缺失显示“未记录”，不按文件名猜测。双击查看恢复差异。','muted'))
        self.summary=text_label('请选择备份，再查看差异。','muted');self.box.addWidget(self.summary);self.box.addWidget(self.error)
        row=QHBoxLayout();row.addWidget(action('立即备份',self.create));row.addWidget(action('选择其他备份…',self.choose))
        from desktop.action_menu import menu_button
        row.addWidget(menu_button('备份管理', [('备注选中备份…',self.note),('空间与清理…',self.cleanup),('刷新列表',self.refresh)]));row.addStretch();self.box.addLayout(row)
        row=QHBoxLayout();row.addStretch()
        row.addWidget(action('查看恢复差异',self.preview));self.restore_button=action('恢复此备份',self.restore,True);self.restore_button.setEnabled(False);row.addWidget(self.restore_button);row.addWidget(action('关闭',self.reject));self.box.addLayout(row)
        self.tree.itemSelectionChanged.connect(self.selected);self.tree.itemDoubleClicked.connect(lambda *_:self.preview())
        self.kind_filter.currentIndexChanged.connect(self.refresh);self.refresh()

    def invalidate(self):self.proposal=None;self.restore_button.setEnabled(False)

    def refresh(self):
        self.invalidate();self.tree.clear()
        rows=backup_manager.catalog(self.store)
        from desktop.backup_cleanup import size_text
        self.space.setText(f'共{len(rows)}份备份 · 数据文件{size_text(sum(r["size"] for r in rows))}；手动备份不自动清理。')
        for r in rows:
            if self.kind_filter.currentData() not in ('all',r['category']):continue
            self.add_entry(r)
        self.summary.setText(f'显示{self.tree.topLevelItemCount()}份备份；选择一份可查看文件名和创建依据。')

    def add_entry(self,r):
        from desktop.backup_cleanup import size_text
        item=QTreeWidgetItem([r['kind_label']+'\n'+r['time'],f"{r['version']} ／ schema {r['schema']}",r['summary']+'\n'+size_text(r['size'])])
        item.setData(0,Qt.ItemDataRole.UserRole,str(r['path']));item.setData(0,Qt.ItemDataRole.UserRole+1,r)
        self.tree.addTopLevelItem(item)
        return item

    def selected(self):
        self.invalidate();item=self.tree.currentItem()
        if not item:return
        r=item.data(0,Qt.ItemDataRole.UserRole+1) or {}
        lines=['文件：'+Path(item.data(0,Qt.ItemDataRole.UserRole)).name]
        if r.get('kind_note'):lines.append(r['kind_note']+'；'+r.get('time_note',''))
        if r.get('target_version'):lines.append('为升级到 '+r['target_version']+' 创建；不代表源软件版本。')
        if r.get('metadata_warning'):lines.append(r['metadata_warning'])
        if r.get('note'):lines.append('备注：'+r['note'])
        self.summary.setText('\n'.join(lines))

    def note(self):
        item=self.tree.currentItem()
        if not item:self.summary.setText('请先选择要备注的本机备份。');return
        from desktop.backup_space_ui import edit_note
        edit_note(self,item.data(0,Qt.ItemDataRole.UserRole))

    def cleanup(self):
        from desktop.backup_space_ui import CleanupDialog
        self.host.tools.open('backup_cleanup',lambda:CleanupDialog(self.host),lambda _:self.refresh())

    def create(self):
        path=self.run(lambda:self.store.manual_backup())
        if path:
            self.kind_filter.setCurrentIndex(0);self.refresh()
            for i in range(self.tree.topLevelItemCount()):
                item=self.tree.topLevelItem(i)
                if item.data(0,Qt.ItemDataRole.UserRole)==str(path):self.tree.setCurrentItem(item);break

    def choose(self):
        path,_=QFileDialog.getOpenFileName(self,'选择整库备份',str(self.store.backups),'SQLite备份 (*.sqlite3)')
        if not path:return
        self.kind_filter.setCurrentIndex(0)
        item=self.add_entry(backup_manager.catalog_entry(path));self.tree.setCurrentItem(item);self.preview()

    def preview(self):
        self.invalidate();item=self.tree.currentItem()
        if not item:self.summary.setText('请先选择一个备份文件。');return
        p=self.run(lambda:backup_manager.preview(self.store,item.data(0,Qt.ItemDataRole.UserRole)))
        if not p:return
        box=ConfirmationDialog('恢复差异（尚未恢复）',p.path.name,self,confirm='使用这份预览')
        for line in p.differences:box.content.addWidget(text_label(line))
        box.content.addWidget(text_label('移除和覆盖包含当前学习记录。只找回误删任务时，请优先使用回收站。','confirmationNote'));box.content.addStretch()
        if box.exec()==QDialog.DialogCode.Accepted:self.proposal=p;self.restore_button.setEnabled(True);self.summary.setText('差异已检查，后续保存或打卡会使预览失效。')

    def restore(self):
        p=self.proposal
        if not p:return
        box=ConfirmationDialog('用此备份替换当前全部资料？','这是整库覆盖，不是合并。请先检查增加、移除和覆盖数量。',self,confirm='备份当前状态并恢复')
        box.content.addWidget(text_label(p.path.name));box.content.addStretch()
        if box.exec()!=QDialog.DialogCode.Accepted:return
        def save():self.host.apply_backup_preview(p);return True
        if self.run(save):self.accept()
        else:self.invalidate()
