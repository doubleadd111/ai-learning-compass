"""Drag visuals cannot commit a date or bypass the existing preview."""
from datetime import date,timedelta
import json
import pytest
from PySide6.QtCore import Qt,QPoint,QPointF,QMimeData,QEvent
from PySide6.QtGui import QDrag,QMouseEvent,QDragLeaveEvent
from PySide6.QtWidgets import QApplication
from desktop.month_view import MonthDialog,DragTaskButton,MIME
from desktop.month_motion import drag_preview,DragOpacity
from desktop.organizer_ui import token
from desktop import backup_manager
from desktop.theme import apply_theme
from tests.test_desktop_workflow import task


@pytest.fixture
def month(qtbot,tmp_path):
    from desktop.store import Store
    app=QApplication.instance();apply_theme(app,'light');old=QApplication.isEffectEnabled(Qt.UIEffect.UI_General)
    QApplication.setEffectEnabled(Qt.UIEffect.UI_General,True)
    s=Store(tmp_path);task(s,'拖动这项任务，不写入学习记录',date.today().isoformat())
    m=MonthDialog(s,lambda _:None);qtbot.addWidget(m);m.show();qtbot.wait(20)
    yield m
    m.close();QApplication.setEffectEnabled(Qt.UIEffect.UI_General,old)


def source(month):return month.findChildren(DragTaskButton)[0]


class DragEvent:
    def __init__(self,button,payload=None,mime=True):
        self.button=button;self.mime=QMimeData();self.accepted=False
        if mime:self.mime.setData(MIME,json.dumps(payload or dict(id=button.task_id,token=token(button.calendar.store))).encode())
    def source(self):return self.button
    def mimeData(self):return self.mime
    def acceptProposedAction(self):self.accepted=True
    def ignore(self):self.accepted=False


@pytest.mark.parametrize('theme',['light','dark'])
def test_target_fades_and_leave_clears_without_writes(month,qtbot,theme):
    apply_theme(QApplication.instance(),theme);cell=month.cells[12];event=DragEvent(source(month));before=backup_manager.live_hash(month.store)
    cell.dragEnterEvent(event);assert event.accepted and cell.drop_active
    qtbot.waitUntil(lambda:cell.drop_progress>.1);assert cell.drop_progress<=1
    cell.dragMoveEvent(event);assert event.accepted
    qtbot.waitUntil(lambda:cell.drop_progress==1)
    cell.dragLeaveEvent(QDragLeaveEvent());assert not cell.drop_active
    qtbot.waitUntil(lambda:cell.drop_progress==0)
    assert backup_manager.live_hash(month.store)==before


def test_no_animation_setting_switches_immediately(month):
    QApplication.setEffectEnabled(Qt.UIEffect.UI_General,False)
    cell=month.cells[10];cell.set_drop_active(True);assert cell.drop_progress==1
    cell.set_drop_active(False);assert cell.drop_progress==0
    opacity=DragOpacity(source(month));opacity.set_dragging(True);assert opacity.effect.opacity()==.45
    opacity.set_dragging(False);assert opacity.effect.opacity()==1


@pytest.mark.parametrize('theme',['light','dark'])
def test_drag_pixmap_theme_and_high_dpi_dimensions(month,theme):
    apply_theme(QApplication.instance(),theme);pixmap,hotspot=drag_preview(source(month))
    ratio=pixmap.devicePixelRatio();assert not pixmap.isNull() and ratio==source(month).devicePixelRatioF()
    assert abs(pixmap.width()/ratio-244)<1 and abs(pixmap.height()/ratio-78)<1
    assert hotspot==QPoint(22,18) and pixmap.toImage().pixelColor(0,0).alpha()==0
    colors=QApplication.instance().property('dropdownColors')
    assert pixmap.toImage().pixelColor(round(8*ratio),round(30*ratio)).name()==colors['card'].lower()


@pytest.mark.parametrize('failure',[False,True])
def test_drag_sets_preview_and_cleans_on_cancel_or_error(month,monkeypatch,qtbot,failure):
    button=source(month);before=backup_manager.live_hash(month.store);seen=[]
    def execute(drag,*_):
        assert not drag.pixmap().isNull() and drag.hotSpot()==QPoint(22,18)
        assert month.drag_source is button and not button.isDown()
        month.cells[2].set_drop_active(True);seen.append(True)
        if failure:raise RuntimeError('synthetic drag cancellation')
        return Qt.DropAction.IgnoreAction
    monkeypatch.setattr(QDrag,'exec',execute)
    button.origin=QPoint(0,0)
    event=QMouseEvent(QEvent.Type.MouseMove,QPointF(40,10),QPointF(40,10),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    if failure:
        with pytest.raises(RuntimeError):button.mouseMoveEvent(event)
    else:button.mouseMoveEvent(event)
    assert seen and month.drag_source is None and button.origin is None and not button.isDown()
    qtbot.waitUntil(lambda:button.drag_opacity.effect.opacity()==1)
    assert all(not c.drop_active and c.drop_progress==0 for c in month.cells) and backup_manager.live_hash(month.store)==before


def test_small_mouse_move_is_not_a_drag(month,monkeypatch):
    button=source(month);button.origin=QPoint(10,10)
    monkeypatch.setattr(QDrag,'exec',lambda *_:pytest.fail('click must not start drag'))
    event=QMouseEvent(QEvent.Type.MouseMove,QPointF(11,10),QPointF(11,10),Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
    button.mouseMoveEvent(event);assert button.drag_opacity is None


def test_drop_opens_preview_only_then_cancel_preserves(month):
    button=source(month);target=next(c for c in month.cells if c.day==date.today()+timedelta(days=1))
    before=backup_manager.live_hash(month.store);target.dragEnterEvent(DragEvent(button));event=DragEvent(button)
    target.dropEvent(event);assert event.accepted and target.drop_progress==0
    tools=month._tool_windows if hasattr(month,'_tool_windows') else None
    from desktop.month_view import MovePreviewDialog
    preview=next(w for w in QApplication.topLevelWidgets() if isinstance(w,MovePreviewDialog) and w.isVisible())
    assert preview.proposal and preview.due.date().toPython()==target.day and backup_manager.live_hash(month.store)==before
    preview.reject();assert backup_manager.live_hash(month.store)==before


@pytest.mark.parametrize('kind',['missing_mime','other_store','wrong_id','stale'])
def test_invalid_drop_never_writes_or_leaves_highlight(month,qtbot,tmp_path,kind):
    button=source(month);cell=month.cells[3];payload=dict(id=button.task_id,token=token(month.store))
    if kind=='wrong_id':payload['id']='different-task'
    if kind=='stale':payload['token']='stale-token'
    if kind=='other_store':
        from desktop.store import Store
        other=MonthDialog(Store(tmp_path/'other'),lambda _:None);qtbot.addWidget(other)
        button=DragTaskButton(button.task,other)
    event=DragEvent(button,payload,mime=kind!='missing_mime');before=backup_manager.live_hash(month.store)
    cell.set_drop_active(True);cell.dropEvent(event)
    assert cell.drop_progress==0 and backup_manager.live_hash(month.store)==before
    if kind!='stale':assert not event.accepted
    else:assert '变化' in month.error.text()


@pytest.mark.parametrize('operation',['refresh','close'])
def test_refresh_or_close_resets_feedback(month,monkeypatch,operation):
    button=source(month);button.drag_opacity=DragOpacity(button);button.drag_opacity.set_dragging(True)
    month.drag_source=button;month.cells[4].set_drop_active(True);called=[]
    monkeypatch.setattr(QDrag,'cancel',lambda:called.append(True));before=backup_manager.live_hash(month.store)
    getattr(month,operation)();assert called and month.drag_source is None
    assert button.drag_opacity.effect.opacity()==1 and backup_manager.live_hash(month.store)==before
