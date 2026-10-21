"""Frozen recovery smoke check; called only for isolated --smoke-test data."""
from datetime import date, timedelta
import json
from PySide6.QtCore import QTimer, Qt, QDate
from PySide6.QtWidgets import QApplication
from desktop import trash
from desktop.confirmations import ConfirmationDialog
from desktop.models import new_id
from models import DailyTask


def check_trash(window):
    store = window.store
    sample = store.get_plan(store.list_plans()[0]['id'])
    original_ids = {r['id'] for r in store.tasks()}
    pid = store.save_plan(sample['profile'], sample['plan'].model_copy(update={'title':'回收站隔离检查'}),
        date.today()+timedelta(days=370), json.loads(sample['weekdays_json']))
    ids = {r['id'] for r in store.tasks(pid)}
    store.delete_plan(pid)
    entry = next(r for r in trash.entries(store) if r['entity_id']==pid)
    proposal = trash.preview(store, entry['id'])
    assert not proposal.conflicts and not {r['id'] for r in store.tasks()} & ids
    assert trash.apply(store, proposal)
    assert {r['id'] for r in store.tasks(pid)} == ids
    task = DailyTask(day=1, title='回收站手动任务', description='隔离恢复测试', deliverable='保留学习记录', duration_minutes=5)
    tid = store.add_manual_task(task, (date.today()+timedelta(days=400)).isoformat())
    store.record_focus('trash-isolated-focus-'+new_id(), tid, 61, date.today().isoformat()+'T10:00:00')
    store.delete_manual_task(tid)
    entry = next(r for r in trash.entries(store) if r['entity_id']==tid)
    window.open_trash()
    dialog = window.tools.windows['trash']
    assert dialog.parentWidget() is None and not dialog.isModal()
    assert not dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    dialog.search.setText('回收站手动任务')
    # Repeated smoke runs deliberately leave recoverable history. Same names
    # are valid: choose the stable entry ID, never rely on result count/order.
    selected = next(dialog.items.topLevelItem(i) for i in range(dialog.items.topLevelItemCount())
        if dialog.items.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)==entry['id'])
    dialog.items.setCurrentItem(selected)
    assert dialog.proposal.entry_id==entry['id']
    detail = dialog.show_detail(entry['id'])
    detail_window = window.tools.windows[('trash-detail',entry['id'])]
    assert '保留学习记录' in detail_window.body.toPlainText() and '0 小时 1 分 1 秒' in detail_window.body.toPlainText()
    assert not store.read('SELECT 1 FROM tasks WHERE id=?', (tid,))
    detail_window.close()
    target = date.today()+timedelta(days=401)
    dialog.date_mode.setCurrentIndex(1)
    dialog.restore_date.setDate(QDate(target.year,target.month,target.day))
    assert dialog.proposal.target_date==target.isoformat()
    app = QApplication.instance()
    errors = []
    for accept in (False, True):
        def click(accept=accept):
            box = app.activeModalWidget()
            try:
                assert isinstance(box, ConfirmationDialog)
                assert box.cancel_button.isDefault()
                (box.confirm_button if accept else box.cancel_button).click()
            except Exception as e:
                errors.append(type(e).__name__)
                if box:
                    box.reject()
        timer = QTimer(dialog)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: app.activeModalWidget().reject() if app.activeModalWidget() else None)
        timer.start(5000)
        QTimer.singleShot(50, click)
        dialog.restore_button.click()
        timer.stop()
        timer.deleteLater()
        assert not errors
        assert bool(store.read('SELECT 1 FROM tasks WHERE id=?', (tid,))) == accept
    assert store.read('SELECT focus_seconds,status FROM tasks WHERE id=?', (tid,))[0] == dict(focus_seconds=61,status='进行中')
    assert store.read('SELECT due_date FROM tasks WHERE id=?',(tid,))[0]['due_date']==target.isoformat()
    dialog.close()
    store.delete_manual_task(tid)
    store.delete_plan(pid)
    assert {r['id'] for r in store.tasks()}==original_ids
    window.refresh()
    return dict(success=True, schema=7, plan_stable_ids=True, task_focus_preserved=True,
        preview_readonly=True, cancel_preserves=True, real_confirmation=True, ownerless=True, search=True,
        explicit_task_date=True, readonly_detail=True)
