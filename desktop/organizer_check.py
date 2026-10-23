"""Frozen synthetic feature checks; never touch the running profile or models."""
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile

from PySide6.QtWidgets import QApplication,QDialogButtonBox
from PySide6.QtCore import QDate
from desktop.dialogs import TaskDialog
from desktop.store import Store, SCHEMA_VERSION
from desktop import organizer as svc
from desktop.focus import FocusTimer
from desktop.organizer_ui import QuickAddDialog, CaptureInboxDialog, ActualStatisticsDialog, token
from desktop.month_view import MonthDialog, MovePreviewDialog, DragTaskButton
from desktop.month_motion import drag_preview,DragOpacity
from models import DailyTask


def check_organizer():
    with tempfile.TemporaryDirectory(prefix='compass-organizer-smoke-') as directory:
        store=Store(Path(directory));today=date.today()
        payload=DailyTask(day=1,title='隔离快速录入任务',description='只用于离线检查',deliverable='不写真实资料',duration_minutes=20)
        task=svc.save_capture(store,payload,priority=3,pinned=True)
        assert not store.tasks() and not store.capacity()
        svc.schedule_capture(store,task,today.isoformat(),token=token(store))
        row=store.tasks()[0]
        assert row['id']==task and row['priority']==3 and row['pinned'] and not svc.captures(store)
        before=store.read('SELECT * FROM tasks')
        tools=[]
        try:
            month=MonthDialog(store,lambda _:None);tools.append(month)
            source=month.findChildren(DragTaskButton)[0];pixmap,hotspot=drag_preview(source)
            assert not pixmap.isNull() and hotspot.x()==22
            source.drag_opacity=DragOpacity(source);month.drag_source=source
            month.cells[2].set_drop_active(True,immediate=True);assert month.cells[2].drop_progress==1
            month.clear_drag_feedback();assert source.drag_opacity.effect.opacity()==1 and not month.cells[2].drop_active
            assert store.read('SELECT * FROM tasks')==before
            proposal=MovePreviewDialog(store,task,today+timedelta(days=1));tools.append(proposal)
            assert proposal.apply.isEnabled() and store.read('SELECT * FROM tasks')==before
            proposal.reject()
            proposal=MovePreviewDialog(store,task,today+timedelta(days=1));tools.append(proposal)
            proposal.accept()
            assert store.tasks()[0]['due_date']==(today+timedelta(days=1)).isoformat()
            now=[0.0];wall=[datetime.now().astimezone()]
            focus=FocusTimer(store,lambda:now[0],lambda:wall[0]);focus.start(task)
            now[0]+=2;wall[0]+=timedelta(seconds=2);focus.tick();focus.pause()
            svc.log_manual(store,task,today,3)
            report=svc.statistics(store,today,today)
            assert report['actual_seconds']==182 and report['active_days']==1 and report['unknown_seconds']==0
            for form in (QuickAddDialog(store),CaptureInboxDialog(store),ActualStatisticsDialog(store)):
                tools.append(form);form.show();QApplication.processEvents()
                assert not form.grab().isNull()
                if isinstance(form,ActualStatisticsDialog):
                    day=QDate.currentDate();rect=form.calendar._cells[day.toJulianDay()]
                    number,marker,caption=form.calendar.cell_geometry(rect,day)
                    assert not number.intersects(caption) and not marker.intersects(caption)
            editor=TaskDialog(store.tasks()[0],store=store);tools.append(editor)
            editor.resize(640,520);editor.show();QApplication.processEvents()
            assert editor.description.height()>=editor.description.fontMetrics().lineSpacing()*4+20
            assert editor.findChild(QDialogButtonBox).isVisible()
            assert editor.fields.verticalScrollBar().maximum()>0
            reopened=Store(Path(directory));assert reopened.tasks()[0]['focus_seconds']==2
            assert len(reopened.read('SELECT * FROM study_events'))==2
            Store.inspect_backup(reopened.path)
        finally:
            for form in tools:form.close();form.deleteLater()
        return dict(success=True,schema=SCHEMA_VERSION,capture_stable_id=True,unscheduled_not_load=True,
            drag_preview_cancel_readonly=True,drag_commit=True,actual_dates=True,attention_display_only=True,
            readable_task_form=True,calendar_disjoint=True,restart_preserved=True,drag_visual_readonly=True,drag_feedback_cleanup=True)
