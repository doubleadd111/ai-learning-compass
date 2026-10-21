import pytest
from PySide6.QtCore import QDate,Qt,QCoreApplication,QEvent
from PySide6.QtWidgets import QDialog
from desktop.trash_ui import TrashDialog,TrashDetailDialog
from desktop.confirmations import ConfirmationDialog
from desktop.controls import ArrowWheelDateEdit,SoftComboBox
from desktop.store import Store
from desktop.theme import apply_theme
from desktop import trash
from tests.test_desktop_trash import deleted
from tests.test_desktop_store import create


@pytest.fixture
def setup(qtbot,qapp,tmp_path):
    apply_theme(qapp,'light')
    s=Store(tmp_path)
    tid,_,_,entry=deleted(s)
    d=TrashDialog(s)
    qtbot.addWidget(d)
    d.show()
    yield s,d,tid,entry
    for w in list(getattr(d,'_child_tools',[])):
        w.close()


def test_select_date_preview_cancel_then_confirm(setup,monkeypatch):
    s,d,tid,entry=setup
    assert isinstance(d.date_mode,SoftComboBox) and isinstance(d.restore_date,ArrowWheelDateEdit)
    assert d.date_mode.currentIndex()==0 and not d.restore_date.isEnabled()
    d.date_mode.setCurrentIndex(1)
    d.restore_date.setDate(QDate(2026,12,3))
    assert d.proposal.target_date=='2026-12-03'
    assert '2026-12-01 → 2026-12-03' in d.details.toPlainText()
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda self:QDialog.DialogCode.Rejected)
    d.restore()
    assert len(trash.entries(s))==1 and not s.tasks()
    monkeypatch.setattr(ConfirmationDialog,'exec',lambda self:QDialog.DialogCode.Accepted)
    d.restore()
    assert s.read('SELECT due_date FROM tasks WHERE id=?',(tid,))[0]['due_date']=='2026-12-03'
    assert '按预览中的日期恢复' in d.details.toPlainText()


def test_double_click_readonly_detail_not_restore(setup,qtbot,qapp):
    s,d,tid,entry=setup
    before=s.path.read_bytes()
    item=d.items.topLevelItem(0)
    pt=d.items.visualItemRect(item).center()
    qtbot.mouseClick(d.items.viewport(),Qt.MouseButton.LeftButton,pos=pt)
    qtbot.mouseDClick(d.items.viewport(),Qt.MouseButton.LeftButton,pos=pt)
    qapp.processEvents()
    detail=next(w for w in d._child_tools if isinstance(w,TrashDetailDialog))
    assert detail.parentWidget() is None and not detail.isModal()
    assert not detail.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert detail.body.isReadOnly()
    content=detail.body.toPlainText()
    for expected in ('阅读并记录要点','一份简短笔记','保留学习笔记','0 小时 2 分 2 秒','记录要点','已做'):
        assert expected in content
    assert s.path.read_bytes()==before and len(trash.entries(s))==1
    detail.close()
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    d.show_detail()
    assert d._child_tools[-1].isVisible()


def test_switch_to_plan_resets_and_hides_task_date(setup):
    s,d,_,_=setup
    d.date_mode.setCurrentIndex(1)
    pid=create(s)
    s.delete_plan(pid)
    d.refresh()
    for i in range(d.items.topLevelItemCount()):
        item=d.items.topLevelItem(i)
        if '计划' in item.text(0):
            d.items.setCurrentItem(item)
            break
    assert not d.date_controls.isVisible() and d.proposal.target_date is None
    assert '整份计划' in d.date_hint.text()


@pytest.mark.parametrize('theme',['light','dark'])
@pytest.mark.parametrize('size',[(920,720),(640,520)])
def test_date_and_detail_layout(setup,qtbot,qapp,theme,size):
    s,d,_,entry=setup
    apply_theme(qapp,theme)
    d.resize(*size)
    d.date_mode.setCurrentIndex(1)
    qapp.processEvents()
    for field in (d.date_mode,d.restore_date,d.restore_button):
        assert d.rect().contains(field.mapTo(d,field.rect().center()))
    detail=TrashDetailDialog(s,entry)
    qtbot.addWidget(detail)
    detail.resize(*size)
    detail.show()
    qapp.processEvents()
    assert detail.body.isVisible() and not detail.grab().isNull()


@pytest.mark.parametrize('kind',['mode','date'])
def test_restore_inputs_wheel_only_over_right_arrow(setup,qtbot,qapp,kind):
    from tests.test_desktop_wheel_scope import arrow,wheel_at
    from PySide6.QtCore import QPoint
    s,d,_,_=setup
    d.date_mode.setCurrentIndex(1)
    field=d.date_mode if kind=='mode' else d.restore_date
    value=field.currentIndex if kind=='mode' else field.date
    field.setFocus()
    qapp.processEvents()
    initial=value()
    area=arrow(field)
    for position in (QPoint(15,field.height()//2),field.rect().center(),QPoint(area.left()-1,area.center().y())):
        wheel_at(field,position)
        assert value()==initial
    wheel_at(field,area.center(),120 if kind=='mode' else -120)
    assert value()!=initial
    assert len(trash.entries(s))==1 and not s.tasks()


def test_smoke_can_repeat_with_same_name_deleted_history(qtbot,qapp,tmp_path):
    from desktop.window import MainWindow
    from desktop.credentials import CredentialStore
    from desktop.trash_check import check_trash
    s=Store(tmp_path)
    create(s)
    s.set_setting('reminders_paused',True)
    w=MainWindow(s,credentials=CredentialStore(object()),enable_tray=False)
    qtbot.addWidget(w)
    w.show()
    try:
        assert check_trash(w)['explicit_task_date']
        assert check_trash(w)['readonly_detail']
        assert len([e for e in trash.entries(s) if e['title']=='回收站手动任务'])==2
    finally:
        w.tools.close_all()
        w.exiting=True
        w.close()
