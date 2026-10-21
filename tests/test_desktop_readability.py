"""Long task text stays readable; calendar evidence never covers date markers."""
from datetime import date
import pytest
from PySide6.QtCore import QDate,QRect,Qt,QPoint
from PySide6.QtWidgets import QApplication,QDialogButtonBox
from desktop.readable_text import ReadableTextEdit
from desktop.dialogs import TaskDialog,PlanDialog
from desktop.manual_tasks import ManualTaskDialog
from desktop.learning_ui import RecurringDialog
from desktop.organizer_ui import QuickAddDialog,StudyLogCalendar,ActualStatisticsDialog
from desktop.organizer import duration_text
from desktop.store import Store
from desktop.theme import apply_theme
from tests.test_desktop_store import create


@pytest.fixture
def data(tmp_path):
    s=Store(tmp_path);pid=create(s);r=s.tasks(pid)[0]
    r['task']=r['task'].model_copy(update={'description':('核对测试范围，记录问题并说明如何复现。\n'*8).strip(),
        'deliverable':'更新后的测试清单\n记录具体复现步骤和检查结果。'})
    r['note']='目前的收获与问题。\n'*12
    return s,r


@pytest.mark.parametrize('theme',['light','dark'])
@pytest.mark.parametrize('width,height',[(640,520),(820,800)])
def test_task_form_wraps_scrolls_and_keeps_footer(qtbot,data,theme,width,height):
    apply_theme(QApplication.instance(),theme);s,r=data
    before=s.read('SELECT * FROM tasks ORDER BY id')
    d=TaskDialog(r,store=s);qtbot.addWidget(d);d.resize(width,height);d.show();qtbot.wait(25)
    assert d.description.height()>=d.description.fontMetrics().lineSpacing()*4+20
    assert d.description.lineWrapMode()==ReadableTextEdit.LineWrapMode.WidgetWidth
    assert d.description.horizontalScrollBarPolicy()==Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert d.description.toPlainText()==r['task'].description
    assert d.deliverable.toPlainText()==r['task'].deliverable
    assert d.fields.verticalScrollBar().maximum()>0
    footer=d.findChild(QDialogButtonBox)
    assert footer.isVisible() and footer.mapTo(d,QPoint()).y()+footer.height()<=d.height()
    d.fields.ensureWidgetVisible(d.note);qtbot.wait(5)
    assert d.note.isVisible() and footer.isVisible()
    d.reject();assert s.read('SELECT * FROM tasks ORDER BY id')==before


def test_locked_task_text_can_be_selected_but_not_typed(qtbot,data):
    s,r=data;d=TaskDialog(r,store=s,locked=True);qtbot.addWidget(d);d.show()
    assert d.description.isEnabled() and d.description.isReadOnly() and d.deliverable.isReadOnly()
    d.description.setFocus();old=d.description.toPlainText();qtbot.keyClicks(d.description,'new text')
    assert d.description.toPlainText()==old and d.note.isEnabled() and not d.note.isReadOnly()
    d.reject()


@pytest.mark.parametrize('kind',['manual','quick','recurring','plan'])
def test_other_long_form_fields_share_readable_plain_text(qtbot,data,kind):
    s,r=data;d={'manual':lambda:ManualTaskDialog(s),'quick':lambda:QuickAddDialog(s),
        'recurring':lambda:RecurringDialog(s),'plan':lambda:PlanDialog([])}[kind]()
    qtbot.addWidget(d);d.resize(640,520);d.show()
    field=d.goal if kind=='plan' else d.description
    assert isinstance(field,ReadableTextEdit)
    value='<b>原样保留</b>\n'+('需要完整阅读的具体说明。'*20)
    field.setText(value);qtbot.wait(20)
    assert field.toPlainText()==value and field.text()==value
    assert field.height()>=field.fontMetrics().lineSpacing()*field.min_lines+20
    assert field.height()<=max(field.fontMetrics().lineSpacing(),field.fontMetrics().height())*field.max_lines+40
    assert field.tabChangesFocus()
    d.reject()


@pytest.mark.parametrize('theme',['light','dark'])
@pytest.mark.parametrize('selected',[True,False])
@pytest.mark.parametrize('seconds',[1,272,3600])
def test_calendar_date_today_dot_and_time_have_disjoint_bounds(qtbot,theme,selected,seconds):
    apply_theme(QApplication.instance(),theme);c=StudyLogCalendar();qtbot.addWidget(c)
    today=QDate.currentDate();c.minutes[today.toString('yyyy-MM-dd')]=seconds/60
    c.setSelectedDate(today if selected else today.addDays(1));c.resize(640,400);c.show();qtbot.wait(15)
    assert len(c._cells)==42
    rect=c._cells[today.toJulianDay()];number,marker,caption=c.cell_geometry(rect,today)
    assert number.height()>=c.fontMetrics().height()-2
    assert not number.intersects(caption) and not marker.intersects(caption)
    assert marker.right()<number.center().x()
    assert caption.bottom()<=rect.bottom()+1
    assert c.cell_annotation(today)==f'{seconds/60:.1f}分'
    assert not c.grab().isNull()


@pytest.mark.parametrize('seconds,text',[(0,'0 分钟'),(90,'1 分 30 秒'),(272,'4 分 32 秒'),(3600,'60 分钟')])
def test_duration_format_is_exact_and_readable(seconds,text):assert duration_text(seconds)==text


def test_actual_calendar_summary_and_detail_do_not_show_long_decimal(qtbot,data):
    s,r=data;s.record_focus('fractional',r['id'],272,date.today().isoformat()+'T10:00:00',day_seconds={date.today().isoformat():272})
    d=ActualStatisticsDialog(s);qtbot.addWidget(d);d.show()
    assert '4 分 32 秒' in d.metrics.text() and '4.5333' not in d.metrics.text()
    item=next(d.daily.topLevelItem(i) for i in range(d.daily.topLevelItemCount()) if d.daily.topLevelItem(i).text(0)==date.today().isoformat())
    assert item.text(3)=='4 分 32 秒' and d.report['actual_seconds']==272
    assert '4 分 32 秒' in d.report['summary']
    d.reject();assert s.tasks()[0]['focus_seconds']==272
