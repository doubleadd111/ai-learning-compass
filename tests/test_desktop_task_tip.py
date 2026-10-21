"""Task hover presentation is bounded, shared, passive and read-only."""
import pytest
from PySide6.QtCore import QEvent, QPoint, QRect, Qt
from PySide6.QtGui import QHelpEvent, QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem
from desktop.focus_tip import RoundedTip, FocusTip, focus_tips
from desktop.task_tip import TaskTip, TASK_TIP_ROLE, preview_text
from desktop.task_style import TaskItemDelegate, FOCUS_ROLE
from desktop.task_search import annotate_match
from desktop.theme import apply_theme
from tests.test_desktop_search_scope import rows, workspace


@pytest.fixture
def surface(qtbot):
    app=QApplication.instance()
    apply_theme(app,'light')
    tree=QTreeWidget()
    qtbot.addWidget(tree)
    tree.setColumnCount(4)
    tree.setHeaderLabels(['任务','日期','预计','状态'])
    tree.setItemDelegate(TaskItemDelegate(tree))
    item=QTreeWidgetItem(tree,['跨周切换测试','2026-10-22','25 分钟','进行中'])
    annotate_match(item,rows()[1],'编写')
    tree.resize(720,360)
    tree.show()
    app.processEvents()
    yield tree,item
    if focus_tips().popup:
        focus_tips().popup.hide()


def hover(tree,item,column=0):
    index=tree.indexFromItem(item,column)
    option=QStyleOptionViewItem()
    option.rect=tree.visualRect(index)
    point=option.rect.center()
    event=QHelpEvent(QEvent.Type.ToolTip,point,tree.viewport().mapToGlobal(point))
    assert tree.itemDelegate().helpEvent(event,tree,option,index)
    return focus_tips().popup


@pytest.mark.parametrize('theme',['light','dark'])
def test_task_help_uses_shared_rounded_plaintext_card(surface,theme):
    tree,item=surface
    apply_theme(QApplication.instance(),theme)
    tip=hover(tree,item)
    assert isinstance(tip,TaskTip) and isinstance(tip,RoundedTip)
    assert tip.title.text()=='跨周切换测试'
    assert tip.matches.text()=='匹配位置：说明'
    assert '【编写】' in tip.description.text()
    assert tip.description.text().count('编写')==1
    assert '说明' not in tip.deliverable.text()
    assert tip.hint.text()=='双击任务查看完整内容'
    assert all(label.textFormat()==Qt.TextFormat.PlainText for label in tip.findChildren(QLabel))
    assert tip.grab().toImage().pixelColor(0,0).alpha()==0
    assert tip.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert tip.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    assert tip.windowFlags() & Qt.WindowType.NoDropShadowWindowHint


@pytest.mark.parametrize('corner',['top-left','bottom-right'])
def test_long_content_stays_inside_available_screen_and_line_budget(surface,corner):
    tree,item=surface
    data=item.data(0,TASK_TIP_ROLE)
    data.update(title='很长的任务名称'*80,description='很长的任务说明'*300,deliverable='完成标准'*200,plan='所属计划'*100,matches=[])
    bounds=QApplication.primaryScreen().availableGeometry()
    point=bounds.topLeft() if corner=='top-left' else bounds.bottomRight()
    focus_tips().show_task(data,point,QRect(point,tree.size()),tree.viewport())
    tip=focus_tips().popup
    assert bounds.contains(tip.geometry())
    assert tip.width()<=380 and tip.height()<400
    assert tip.title.text().endswith('…') and tip.description.text().endswith('…')
    assert len(tip.description.text())<200
    assert tip.description.height()<=tip.description.fontMetrics().lineSpacing()*3+3


@pytest.mark.parametrize('kind',[QEvent.Type.MouseButtonPress,QEvent.Type.Wheel,QEvent.Type.KeyPress,QEvent.Type.ApplicationDeactivate])
def test_interaction_dismisses_without_consuming_event(surface,kind):
    tree,item=surface
    tip=hover(tree,item)
    assert tip.isVisible()
    assert focus_tips().eventFilter(tree.viewport(),QEvent(kind)) is False
    assert not tip.isVisible()


def test_move_outside_anchor_and_source_close_dismiss(surface):
    tree,item=surface
    tip=hover(tree,item)
    event=QMouseEvent(QEvent.Type.MouseMove,QPoint(0,0),QPoint(-10000,-10000),Qt.MouseButton.NoButton,Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier)
    assert focus_tips().eventFilter(tree.viewport(),event) is False
    assert not tip.isVisible()
    tip=hover(tree,item)
    tree.close()
    assert not tip.isVisible()


def test_focus_and_task_share_one_popup_and_reopen_after_close(surface):
    tree,item=surface
    task=hover(tree,item)
    item.setData(3,FOCUS_ROLE,162)
    focus=hover(tree,item,3)
    assert isinstance(focus,FocusTip) and not task.isVisible()
    assert focus.value.text()=='2 分 42 秒'
    task=hover(tree,item)
    assert isinstance(task,TaskTip) and not focus.isVisible()
    task.close()
    assert hover(tree,item).isVisible()


def test_plain_markup_and_clear_query_remove_match_metadata(surface):
    tree,item=surface
    row=rows()[1]
    row['task']=row['task'].model_copy(update={'description':'<img src="https://invalid.test/x">编写 & <b>内容</b>'})
    annotate_match(item,row,'编写')
    tip=hover(tree,item)
    assert '<b>' in tip.description.text() and '【编写】' in tip.description.text()
    annotate_match(item,row,'')
    tip=hover(tree,item)
    assert '<img' in tip.description.text() and tip.description.textFormat()==Qt.TextFormat.PlainText
    assert not tip.matches.isVisible() and not item.data(0,TASK_TIP_ROLE)['matches']
    assert item.toolTip(0)==row['task'].title


def test_long_unbroken_tokens_are_bounded(surface):
    tree,item=surface
    tip=hover(tree,item)
    value=preview_text('X'*10000,tip.description.font(),160,2)
    assert value.endswith('…') and len(value)<100


def test_real_viewport_help_event_routes_to_custom_card(surface):
    tree,item=surface
    point=tree.visualRect(tree.indexFromItem(item)).center()
    event=QHelpEvent(QEvent.Type.ToolTip,point,tree.viewport().mapToGlobal(point))
    QApplication.sendEvent(tree.viewport(),event)
    assert isinstance(focus_tips().popup,TaskTip) and focus_tips().popup.isVisible()
    assert focus_tips().popup.title.text()==item.text(0)


def test_week_detail_uses_same_preview_without_changing_saved_tasks(workspace):
    w=workspace
    before=w.store.read('SELECT * FROM tasks ORDER BY id')
    w.navigate(2)
    w.week_panel.mode.setCurrentIndex(1)
    QApplication.processEvents()
    tree=w.week_tree
    item=next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount()) if tree.topLevelItem(i).text(0)=='跨周切换测试')
    tip=hover(tree,item)
    assert isinstance(tip,TaskTip) and tip.description.text()=='为上一周、下一周编写测试'
    assert not tip.matches.isVisible()
    assert w.store.read('SELECT * FROM tasks ORDER BY id')==before
