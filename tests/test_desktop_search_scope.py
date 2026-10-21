"""Search scope and visible, safe reasons for otherwise unexpected results."""
from datetime import date
import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from desktop.task_search import search_tasks, match_details, match_excerpt, annotate_match, TaskQuery
from desktop.task_style import SEARCH_MATCH_ROLE
from desktop.task_tip import TASK_TIP_ROLE
from desktop.store import Store
from desktop.window import MainWindow
from desktop.learning_ui import StructureDialog
from desktop.credentials import CredentialStore
from desktop.theme import apply_theme
from tests.test_desktop_manual_tasks import payload
from tests.test_desktop_store import create


def rows():
    return [dict(id='title',task=payload().model_copy(update={'title':'编写周视图测试','description':'检查日期'}),due_date='2026-10-20',plan_title='测试计划',status='未开始'),
            dict(id='detail',task=payload().model_copy(update={'title':'跨周切换测试','description':'为上一周、下一周编写测试','deliverable':'完成验证'}),due_date='2026-10-22',plan_title='测试计划',status='未开始')]


@pytest.mark.parametrize('scope,expected', [('title',{'title'}),('all',{'title','detail'})])
def test_scope_excludes_description_only_until_all_selected(scope, expected):
    assert {r['id'] for r in search_tasks(rows(),'编写',scope=scope)}==expected
    assert rows()[1]['task'].title=='跨周切换测试'


def test_multiple_terms_can_match_different_fields_and_date_is_all_only():
    assert {r['id'] for r in search_tasks(rows(),'切换 编写',scope='all')}=={'detail'}
    assert not search_tasks(rows(),'切换 编写',scope='title')
    assert not search_tasks(rows(),'2026/10/22',scope='title')
    assert [r['id'] for r in search_tasks(rows(),'2026年10月22日',scope='all')]==['detail']


def test_explanation_reports_actual_field_and_original_excerpt():
    details=match_details(rows()[1],'编写')
    assert details==[('说明','为上一周、下一周编写测试',['编写'])]
    assert '【编写】' in match_excerpt(details[0][1],details[0][2])
    assert not match_details(rows()[1],'编写','title')
    assert not match_details(rows()[1],'不存在')
    assert not match_details(rows()[1],'   ')


def test_unicode_original_case_and_long_context_snippets():
    value='前'*150+'ＰｙＴｈＯｎ'+'后'*150
    excerpt=match_excerpt(value,['python'])
    assert '【ＰｙＴｈＯｎ】' in excerpt and excerpt.startswith('…') and excerpt.endswith('…')
    assert len(excerpt)<85
    assert match_excerpt('Straße', ['strasse'])=='【Straße】'


@pytest.fixture
def workspace(qtbot,tmp_path):
    apply_theme(QApplication.instance(),'light')
    s=Store(tmp_path)
    create(s)
    s.set_setting('reminders_paused',True)
    for row in rows():
        s.add_manual_task(row['task'],date.today().isoformat())
    w=MainWindow(s,credentials=CredentialStore(object()),enable_tray=False)
    qtbot.addWidget(w)
    w.show()
    yield w
    w.exiting=True
    w.close()


@pytest.mark.parametrize('entry',['today','library','structure'])
def test_three_ui_entries_default_title_and_explain_all_readonly(workspace,qtbot,entry):
    w=workspace
    before=w.store.read('SELECT * FROM tasks ORDER BY id')
    if entry=='structure':
        d=StructureDialog(w.store,None)
        qtbot.addWidget(d)
        d.show()
        query,scope,table=d.search,d.search_scope,d.tasks
        def items():
            return [table.topLevelItem(i).child(j) for i in range(table.topLevelItemCount()) for j in range(table.topLevelItem(i).childCount())]
    else:
        if entry=='library':
            w.plan_list.setCurrentRow(0)
        else:
            w.today_plan_filter.setCurrentIndex(w.today_plan_filter.findData('__standalone__'))
        control=w.today_task_search if entry=='today' else w.library_task_search
        query,scope=control.query,control.scope
        table=w.today_tree if entry=='today' else w.plan_tree
        def items():
            return [table.topLevelItem(i) for i in range(table.topLevelItemCount())]
    assert scope.currentData()=='title'
    query.setText('编写')
    assert [i.text(0) for i in items()]==['编写周视图测试']
    scope.setCurrentText('全部内容')
    target=next(i for i in items() if i.text(0)=='跨周切换测试')
    assert '说明命中：' in target.data(0,SEARCH_MATCH_ROLE) and '【编写】' in target.data(0,SEARCH_MATCH_ROLE)
    assert target.data(0,TASK_TIP_ROLE)['matches']==[('说明','为上一周、下一周编写测试',['编写'])]
    assert len(items())==2
    scope.setCurrentText('仅任务名称')
    assert len(items())==1
    query.clear()
    assert all(not i.data(0,SEARCH_MATCH_ROLE) for i in items())
    assert before==w.store.read('SELECT * FROM tasks ORDER BY id')


def test_snippets_and_preview_data_keep_markup_as_plain_text(qtbot):
    from PySide6.QtWidgets import QTreeWidget,QTreeWidgetItem
    tree=QTreeWidget()
    qtbot.addWidget(tree)
    tree.setColumnCount(1)
    item=QTreeWidgetItem(tree,['跨周切换测试'])
    row=rows()[1]
    row['task']=row['task'].model_copy(update={'description':'<b>编写</b> & 测试'})
    annotate_match(item,row,'编写')
    assert '<b>【编写】</b>' in item.data(0,SEARCH_MATCH_ROLE)
    assert item.data(0,TASK_TIP_ROLE)['description']=='<b>编写</b> & 测试'
    assert item.toolTip(0)=='跨周切换测试'


@pytest.mark.parametrize('standalone',[False,True])
def test_structure_all_content_includes_its_plan_scope(workspace,qtbot,standalone):
    w=workspace
    pid=None if standalone else w.selected_plan_id
    d=StructureDialog(w.store,pid)
    qtbot.addWidget(d)
    query='独立任务' if standalone else w.store.get_plan(pid)['title']
    d.search.setText(query)
    assert d.tasks.topLevelItemCount()==0
    d.search_scope.setCurrentText('全部内容')
    item=d.tasks.topLevelItem(0).child(0)
    assert '所属计划命中：' in item.data(0,SEARCH_MATCH_ROLE)


@pytest.mark.parametrize('theme',['light','dark'])
def test_two_line_delegate_keeps_title_overdue_badge_and_scope_arrow(workspace,theme):
    from desktop.task_style import OVERDUE_ROLE
    from desktop.controls import SoftComboBox
    from PySide6.QtCore import QEvent
    w=workspace
    apply_theme(QApplication.instance(),theme)
    w.navigate(0)
    search=w.today_task_search
    assert isinstance(search.scope,SoftComboBox)
    search.scope.setCurrentText('全部内容')
    search.query.setText('编写')
    item=next(w.today_tree.topLevelItem(i) for i in range(w.today_tree.topLevelItemCount()) if w.today_tree.topLevelItem(i).text(0)=='跨周切换测试')
    item.setData(0,OVERDUE_ROLE,2)
    w.today_tree.setCurrentItem(item)
    QApplication.processEvents()
    assert not w.today_tree.grab().isNull()
    assert w.today_tree.visualItemRect(item).height()>=45
    assert item.data(0,Qt.ItemDataRole.UserRole) and item.text(0)=='跨周切换测试'
    w.navigate(1)
    w.plan_list.setCurrentRow(0)
    w.resize(980,680)
    QApplication.processEvents()
    assert w.plan_tree.columnWidth(0)>=210
