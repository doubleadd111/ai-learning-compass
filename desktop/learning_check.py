"""Frozen offline regression of the new learning tools, isolated data only."""
from datetime import date
from contextlib import closing
from PySide6.QtWidgets import QDialog
from desktop.learning_tools import preview_series, apply_series, task_details, save_details, stop_series
from desktop.learning_ui import StatisticsDialog, StepsDialog, StructureDialog, RecurringDialog, SeriesListDialog
from desktop.scheduling import fingerprint
from models import DailyTask


def check_learning_tools(store):
    from desktop.store import Store
    import tempfile
    # Never add tasks to the outer smoke sample or change its fixed task count.
    with tempfile.TemporaryDirectory(prefix='learning-tools-check-') as directory:
        scratch = Store(directory)
        rule = dict(kind='daily', start='2027-01-04', end='2027-01-06', weekdays=[], intervals=[])
        task = DailyTask(day=1, title='离线周期检查', description='只用于本机隔离验收', deliverable='检查保存与恢复', duration_minutes=20)
        proposal = preview_series(scratch, task, rule)
        assert not scratch.tasks()
        apply_series(scratch, proposal)
        tid = proposal.task_ids[0]
        detail, token = task_details(scratch, tid)
        detail['steps'] = [dict(id='qa-step', title='完成隔离步骤', done=True)]
        save_details(scratch, tid, detail, token)
        assert scratch.tasks()[0]['status'] == '进行中'
        backup = scratch.manual_backup()
        scratch.restore(backup)
        assert task_details(scratch, tid)[0]['steps'][0]['done']
        windows = [StatisticsDialog(scratch), StepsDialog(scratch, tid), RecurringDialog(scratch), SeriesListDialog(scratch),
                   StructureDialog(store, store.list_plans()[0]['id'])]
        try:
            for w in windows:
                w.show()
                assert not w.grab().isNull()
        finally:
            for w in windows:
                w.close()
                w.deleteLater()
        return dict(finite_preview_save=True, step_progress=True, backup_restore=True, windows_rendered=True, success=True)
