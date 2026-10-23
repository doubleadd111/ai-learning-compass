from PySide6.QtWidgets import QHBoxLayout,QLineEdit,QDialog
from desktop.workflow_ui import UtilityDialog,action
from desktop.dialogs import spin
from desktop.confirmations import ConfirmationDialog,text_label
from desktop import backup_cleanup


def edit_note(parent,path):
    store=parent.store
    try:
        path=backup_cleanup.local_path(store,path);digest=backup_cleanup.file_hash(path)
    except (ValueError,OSError) as e:parent.error.setText(str(e));parent.error.show();return
    box=ConfirmationDialog('给备份添加备注',path.name,parent,confirm='保存备注')
    previous=backup_cleanup.read_note(path)
    field=QLineEdit(previous);field.setMaxLength(200);field.setAccessibleName('备份备注');field.setPlaceholderText('例如：开始新课程前，包含本周复盘')
    box.content.addWidget(field);box.content.addStretch()
    if box.exec()==QDialog.DialogCode.Accepted:
        def save():backup_cleanup.save_note(store,path,field.text(),digest,previous);return True
        if parent.run(save):parent.refresh()


class CleanupDialog(UtilityDialog):
    def __init__(self,host):
        super().__init__(host,'清理旧备份','只清理自动备份。手动备份和来源不明的文件保留；每类自动备份至少保留最近一份可恢复的备份。')
        row=QHBoxLayout();row.addWidget(text_label('保留最近天数'));self.days=spin(7,3650,30);self.days.setAccessibleName('清理时保留最近天数');row.addWidget(self.days);row.addStretch();self.box.addLayout(row)
        self.summary=text_label('先查看清理预览。此窗口不会自动清理。','muted');self.box.addWidget(self.summary,1);self.box.addWidget(self.error)
        self.box.addWidget(text_label('日常规则：每日备份自动保留最近30份；手动备份不自动清理；操作前备份由你预览后清理。这里的天数只用于本次清理。','muted'))
        row=QHBoxLayout();row.addStretch();row.addWidget(action('查看清理预览',self.preview));self.apply_button=action('清理所列备份…',self.apply);self.apply_button.setObjectName('danger');self.apply_button.setEnabled(False);row.addWidget(self.apply_button);row.addWidget(action('关闭',self.reject));self.box.addLayout(row)
        self.proposal=None;self.days.valueChanged.connect(self.invalidate)

    def invalidate(self,*_):self.proposal=None;self.apply_button.setEnabled(False);self.summary.setText('选项已变化，请重新查看清理预览。')

    def preview(self):
        self.invalidate();p=self.run(lambda:backup_cleanup.preview(self.store,self.days.value()))
        if not p:return
        box=ConfirmationDialog('核对清理清单',f'拟永久删除{len(p.files)}份旧自动备份，释放约{backup_cleanup.size_text(p.bytes)}。此时尚未清理。',self,confirm='使用这份预览')
        for name,group in p.files:box.content.addWidget(text_label(name+' · '+backup_cleanup.size_text(sum(x[2] for x in group))))
        if not p.files:box.content.addWidget(text_label('没有符合条件的旧备份。无需清理。'))
        box.content.addWidget(text_label('保留的最近恢复点：\n'+'\n'.join(p.protected),'muted'));box.content.addStretch()
        if box.exec()==QDialog.DialogCode.Accepted:
            self.proposal=p;self.summary.setText(f'已预览{len(p.files)}份 · {backup_cleanup.size_text(p.bytes)}；手动备份保持不变。');self.apply_button.setEnabled(bool(p.files))

    def apply(self):
        if not self.proposal:return
        box=ConfirmationDialog('永久删除这些旧自动备份？',f'共{len(self.proposal.files)}份，约{backup_cleanup.size_text(self.proposal.bytes)}。',self,note='删除后不能从软件回收站恢复。当前数据库、手动备份和保留的恢复点不受影响。默认取消。',confirm='永久清理这些备份')
        if box.exec()!=QDialog.DialogCode.Accepted:return
        p=self.proposal;result=self.run(lambda:backup_cleanup.apply(self.store,p));self.invalidate()
        if result is not None:self.summary.setText('清理完成，释放约'+backup_cleanup.size_text(result)+'。');self.host.refresh()
