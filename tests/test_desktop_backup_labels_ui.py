from pathlib import Path
from datetime import date
import json
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication,QDialog,QFileDialog
from desktop.backup_ui import BackupDialog
from desktop.confirmations import ConfirmationDialog
from desktop import backup_manager
from desktop import VERSION
from desktop.theme import apply_theme
from tests.test_desktop_workspace_ui import workspace


@pytest.mark.parametrize('theme',['light','dark'])
def test_explicit_types_filters_details_and_read_only_selection(workspace,theme):
    w=workspace;apply_theme(QApplication.instance(),theme)
    manual=w.store.manual_backup();w.store.manual_backup('before-restore')
    old=w.store.backups/'pre-update-2026.10.99-20261022-100000.sqlite3';w.store.backup_to(old,kind=None)
    before=backup_manager.live_hash(w.store);d=BackupDialog(w);d.resize(640,560);d.show();QApplication.processEvents()
    assert d.width()==640 and d.height()==560
    assert d.tree.headerItem().text(0)=='备份类型／时间'
    d.kind_filter.setCurrentIndex(d.kind_filter.findData('manual'))
    assert d.tree.topLevelItemCount()==1
    item=d.tree.topLevelItem(0);assert '手动备份' in item.text(0) and manual.name not in item.text(0)
    d.tree.setCurrentItem(item);assert manual.name in d.summary.text()
    assert not d.restore_button.isEnabled()
    d.kind_filter.setCurrentIndex(d.kind_filter.findData('daily'))
    assert d.tree.topLevelItemCount()==1 and '自动备份' in d.tree.topLevelItem(0).text(0)
    d.kind_filter.setCurrentIndex(d.kind_filter.findData('guard'))
    assert d.tree.topLevelItemCount()==2
    item=next(d.tree.topLevelItem(i) for i in range(2) if d.tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)==str(old))
    d.tree.setCurrentItem(item);assert '未记录' in item.text(1) and '按文件名识别' in d.summary.text()
    assert backup_manager.live_hash(w.store)==before;d.reject()


def test_manual_button_shows_new_type_and_recorded_version(workspace):
    d=BackupDialog(workspace);before=backup_manager.live_hash(workspace.store)
    d.kind_filter.setCurrentIndex(d.kind_filter.findData('daily'));d.create()
    item=d.tree.currentItem();assert item and '手动备份' in item.text(0)
    assert VERSION in item.text(1) and 'manual-' in d.summary.text()
    assert d.kind_filter.currentData()=='all' and backup_manager.live_hash(workspace.store)==before;d.reject()


def test_broken_description_still_readable_and_no_automatic_restore(workspace,monkeypatch):
    s=workspace.store;p=s.manual_backup();p.with_suffix('.meta.json').write_text('invalid',encoding='utf-8')
    d=BackupDialog(workspace);item=next(d.tree.topLevelItem(i) for i in range(d.tree.topLevelItemCount()) if d.tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)==str(p))
    d.tree.setCurrentItem(item)
    assert '未记录' in item.text(1) and '说明缺失有效信息' in d.summary.text()
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda self:QDialog.DialogCode.Rejected)
    before=backup_manager.live_hash(s);d.preview()
    assert not d.proposal and backup_manager.live_hash(s)==before;d.reject()


def test_external_backup_uses_same_metadata_labels(workspace,monkeypatch,tmp_path):
    s=workspace.store;external=tmp_path/'external-name.sqlite3';s.backup_to(external,kind='automatic_daily')
    d=BackupDialog(workspace)
    monkeypatch.setattr(QFileDialog,'getOpenFileName',lambda *a: (str(external),''))
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda self:QDialog.DialogCode.Rejected)
    d.choose();item=d.tree.currentItem()
    assert item and '自动备份' in item.text(0) and VERSION in item.text(1)
    assert external.name in d.summary.text() and not d.restore_button.isEnabled();d.reject()
