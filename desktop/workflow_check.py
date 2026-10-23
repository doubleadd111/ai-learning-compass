"""Frozen checks use only the smoke test's invented database."""
from datetime import date
from PySide6.QtCore import QCoreApplication,QEvent
from PySide6.QtWidgets import QApplication
from desktop import workflow,backup_manager
from models import DailyTask


def check_workflow(window):
    s=window.store;ids=[]
    try:
        for title in ('批量检查甲','前置检查乙'):
            ids.append(s.add_manual_task(DailyTask(day=1,title=title,description='隔离检查',duration_minutes=5,deliverable='无真实资料'),date.today().isoformat()))
        rows=s.read('SELECT * FROM tasks ORDER BY id')
        p=workflow.preview_batch(s,ids,'group','检查分组')
        assert s.read('SELECT * FROM tasks ORDER BY id')==rows
        workflow.apply_batch(s,p);workflow.set_dependencies(s,ids[1],[ids[0]],workflow.token(s))
        assert workflow.prerequisites(s,ids[1])[0]['id']==ids[0]
        workflow.control_reminder(s,ids[0],'mute',workflow.token(s))
        assert s.read('SELECT * FROM tasks ORDER BY id')==rows
        path=s.manual_backup();assert backup_manager.preview(s,path).differences
        from desktop import VERSION
        rows=backup_manager.catalog(s)
        assert any(r['kind']=='automatic_daily' and r['version']==VERSION for r in rows)
        assert any(r['path']==path and r['kind']=='manual' and r['version']==VERSION for r in rows)
        window.open_reminders();window.open_dependencies(ids[1]);window.open_backups();window.open_batch(window.today_tree)
        QApplication.processEvents()
        for key in ('reminders',('dependencies',ids[1]),'backups','batch'):
            d=window.tools.windows[key];assert d.isVisible() and not d.isModal() and d.parentWidget() is None
        return dict(batch_preview=True,dependencies=True,reminders=True,backup_differences=True,backup_types_recorded=True,peer_windows=4,success=True)
    finally:
        window.tools.close_all();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        for tid in ids:s.delete_manual_task(tid)
        window.refresh()
