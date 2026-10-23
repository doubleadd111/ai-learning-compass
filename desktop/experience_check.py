"""Frozen, isolated checks of the five experience improvements."""
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from desktop.backup_manager import live_hash
from desktop.task_resources import item,save,load
from desktop.backup_cleanup import save_note,read_note,file_hash
from datetime import date,timedelta


def check_experience(host):
    tid=host.store.tasks()[0]['id'];before=live_hash(host.store)
    detail=host.open_task_detail(tid);assert detail.tabs.count()==4
    for i in range(4):detail.tabs.setCurrentIndex(i)
    search=host.open_global_search();search.query.query.setText(host.store.tasks(task_id=tid)[0]['task'].title);search.populate()
    assert any(r['kind']=='task' and r['id']==tid for r in search.results)
    QApplication.processEvents()
    assert not detail.isModal() and detail.parentWidget() is None and search.parentWidget() is None
    assert live_hash(host.store)==before
    detail.reject();search.reject()
    today=date.today();monday=today-timedelta(days=today.weekday());day=monday if today!=monday else monday+timedelta(days=1)
    host.select_today_day(day);assert host.today_selected_day==day and not host.back_to_today.isHidden()
    assert all(host.store.tasks(task_id=host.today_tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole))[0]['due_date']==day.isoformat() for i in range(host.today_tree.topLevelItemCount()))
    host.select_today_day(today);assert host.back_to_today.isHidden() and live_hash(host.store)==before
    existing=load(host.store,tid);values=[item('冻结隔离教材','https://example.invalid/learning')]
    save(host.store,tid,values,existing);assert load(host.store,tid)==values
    associated_hash=live_hash(host.store);search=host.open_global_search();search.query.scope.setCurrentIndex(1);search.query.query.setText('example.invalid');search.populate()
    result=next(i for i,r in enumerate(search.results) if r['kind']=='resource' and r['task_id']==tid)
    search.tree.setCurrentItem(search.tree.topLevelItem(result));search.open_selected();detail=host.tools.windows[('detail',tid)]
    assert detail.tabs.currentIndex()==3 and detail.selected_resource()['id']==values[0]['id']
    search.query.query.setText('番茄钟');search.populate();assert any(r['kind']=='feature' and r['id']=='focus' for r in search.results)
    assert live_hash(host.store)==associated_hash
    detail.reject();search.reject()
    save(host.store,tid,existing,values)
    backup=host.store.manual_backup();save_note(host.store,backup,'冻结隔离备份',file_hash(backup),'')
    assert read_note(backup)=='冻结隔离备份'
    return dict(success=True,read_only_detail=True,global_search=True,peer_windows=True,resource_persistence=True,backup_note=True,explicit_edit=True,select_weekday=True,feature_search=True,resource_search=True,resource_result_locates_only=True)
