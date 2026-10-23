"""Date cards and feature/resource search are presentation-only."""
from datetime import date,timedelta
import json
import pytest
from PySide6.QtCore import Qt,QEvent,QCoreApplication
from PySide6.QtWidgets import QApplication
from desktop import global_search,task_resources,backup_manager
from desktop.feature_search import FEATURES,open_feature
from desktop.global_search_ui import GlobalSearchDialog
from desktop.store import Store
from desktop.theme import apply_theme
from tests.test_desktop_workspace_ui import workspace
from tests.test_desktop_today_filter import ui
from tests.test_desktop_workflow import task
from tests.test_desktop_store import create,edit


def visible_ids(w):return [w.today_tree.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole) for i in range(w.today_tree.topLevelItemCount())]


@pytest.mark.parametrize('offset',range(7))
def test_choose_each_weekday_changes_only_view(ui,qtbot,offset):
    w=ui;today=w.display_date;monday=today-timedelta(days=today.weekday());target=monday+timedelta(days=offset)
    ids=[task(w.store,'任务'+str(i),(monday+timedelta(days=i)).isoformat()) for i in range(7)]
    w.refresh();before=backup_manager.live_hash(w.store)
    card=w.trail_days[offset][0].parentWidget();qtbot.mouseClick(card,Qt.MouseButton.LeftButton)
    assert w.today_selected_day==target and card.property('dateSelected') and '已选中' in card.accessibleName()
    expected=set(ids[:offset+1]) if target==today else {ids[offset]}
    assert set(visible_ids(w))==expected
    assert target.strftime('%m月%d日') in w.today_budget.text()
    assert backup_manager.live_hash(w.store)==before
    qtbot.mouseClick(w.back_to_today,Qt.MouseButton.LeftButton) if target!=today else w.select_today_day(today)
    assert w.today_selected_day==today and set(visible_ids(w))==set(ids[:today.weekday()+1])


def test_past_completed_and_focus_date_follow_selection(ui):
    w=ui;today=w.display_date;day=today-timedelta(days=1)
    tid=task(w.store,'昨天已完成',day.isoformat());edit(w.store,w.store.tasks(task_id=tid)[0],status='已完成')
    # Write only the fictional task's existing presentation metadata.
    with w.store.write() as c:c.execute('INSERT INTO task_attention VALUES (?,3,0,?)',(tid,day.isoformat()))
    w.refresh();before=backup_manager.live_hash(w.store);w.select_today_day(day)
    assert tid not in visible_ids(w)
    w.today_completed.setChecked(True);assert visible_ids(w)==[tid]
    w.today_important.setChecked(True);assert visible_ids(w)==[tid] and '所选日' in w.today_important.text()
    assert '已完成 1 项' in w.today_summary.text() and backup_manager.live_hash(w.store)==before


def test_card_keyboard_and_date_filter_reset(ui,qtbot):
    w=ui;w.today_task_search.limit_dates.setChecked(True)
    card=w.trail_days[0][0].parentWidget();card.setFocus();qtbot.keyClick(card,Qt.Key.Key_Space)
    assert w.today_selected_day==card.day and not w.today_task_search.limit_dates.isChecked()
    qtbot.keyClick(card,Qt.Key.Key_Right);next_card=w.trail_days[1][0].parentWidget();assert QApplication.focusWidget() is next_card
    qtbot.keyClick(next_card,Qt.Key.Key_Return);assert w.today_selected_day==next_card.day


@pytest.mark.parametrize('today_text,target_text',[('2026-12-31','2027-01-03'),('2028-02-29','2028-03-05'),('2026-10-26','2026-10-26')])
def test_week_across_year_leap_and_rollover(ui,monkeypatch,today_text,target_text):
    import desktop.window as module
    class FixedDate(date):
        @classmethod
        def today(cls):return cls.fromisoformat(today_text)
    monkeypatch.setattr(module,'date',FixedDate)
    ui.refresh();ui.select_today_day(date.fromisoformat(target_text));assert ui.today_selected_day.isoformat()==target_text
    ui.select_today_day(date.fromisoformat('2000-01-01'));assert ui.today_selected_day.isoformat()==target_text


@pytest.mark.parametrize('query,expected',[('备份','backups'),('番茄钟','focus'),('深色','appearance'),('api key','models'),('回滚','history'),('删除','trash')])
def test_features_search_aliases_never_execute(query,expected):
    rows=global_search.search([],[],query,features=True)
    assert any(r['kind']=='feature' and r['id']==expected for r in rows)
    assert all(r['kind']=='feature' for r in rows)


def test_resources_search_filename_path_title_filters_and_no_disk_read(tmp_path,monkeypatch):
    s=Store(tmp_path);pid=create(s);r=s.tasks(pid)[0]
    item=task_resources.item('阅读资料',r'C:\not-present\Python\lesson-one.pdf','file');task_resources.save(s,r['id'],[item],[])
    resources=[dict(item,task_id=r['id'])];tasks=s.tasks();plans=s.list_plans();before=backup_manager.live_hash(s)
    assert global_search.search(tasks,plans,'lesson-one',resources=resources,entity='resource')
    assert not global_search.search(tasks,plans,'not-present',resources=resources,entity='resource')
    found=global_search.search(tasks,plans,'not-present',scope='all',resources=resources,entity='resource')
    assert len(found)==1 and '文件路径' in found[0]['match']
    assert not global_search.search(tasks,plans,'lesson-one',resources=resources,entity='resource',status='已完成')
    assert not global_search.search(tasks,plans,'lesson-one',resources=resources,entity='resource',start='2099-01-01')
    assert backup_manager.live_hash(s)==before


def test_open_resource_result_selects_association_not_external_app(workspace,monkeypatch):
    from PySide6.QtGui import QDesktopServices
    w=workspace;tid=w.store.tasks()[0]['id'];item=task_resources.item('唯一教材',r'C:\fictional\unique-paper.pdf','file')
    task_resources.save(w.store,tid,[item],[]);before=backup_manager.live_hash(w.store)
    monkeypatch.setattr(QDesktopServices,'openUrl',lambda *_:pytest.fail('search must not open external links'))
    d=w.open_global_search();d.query.query.setText('unique-paper');d.populate();assert len(d.results)==1
    d.tree.setCurrentItem(d.tree.topLevelItem(0));d.open_selected();detail=w.tools.windows[('detail',tid)]
    assert detail.tabs.currentIndex()==3 and detail.selected_resource()['id']==item['id']
    assert backup_manager.live_hash(w.store)==before
    task_resources.save(w.store,tid,[],[item]);d.open_selected();assert '移除' in detail.error.text()


@pytest.mark.parametrize('key',['backups','cleanup','statistics','reminders','budget','appearance','models','data','history','resources','pause','week','focus','guide'])
def test_feature_routes_only_open_views_forms(workspace,key):
    w=workspace;before=backup_manager.live_hash(w.store);open_feature(w,key);QApplication.processEvents()
    assert backup_manager.live_hash(w.store)==before
    with pytest.raises(ValueError):open_feature(w,'delete_all_now()')


@pytest.mark.parametrize('theme',['light','dark'])
def test_expanded_search_small_peer_and_function_filters(workspace,theme):
    w=workspace;apply_theme(QApplication.instance(),theme);d=w.open_global_search();d.resize(640,560);QApplication.processEvents()
    assert (d.width(),d.height())==(640,560) and not d.isModal() and d.parentWidget() is None
    d.plan.setCurrentIndex(1);d.query.status.setCurrentText('已完成');d.entity.setCurrentIndex(d.entity.findData('feature'))
    d.query.query.setText('备份');d.populate();assert d.results and all(r['kind']=='feature' for r in d.results)
    d.tree.setCurrentItem(d.tree.topLevelItem(0));assert not d.locate_button.isEnabled() and not d.plan.isEnabled()
    assert d.query.scope.itemText(0)=='名称／文件名'


@pytest.mark.parametrize('theme',['light','dark'])
def test_today_small_window_scroll_not_clipped(ui,theme):
    apply_theme(QApplication.instance(),theme);task(ui.store,'本周阅读',ui.display_date.isoformat())
    ui.refresh();ui.resize(980,680);ui.select_today_day(ui.display_date-timedelta(days=1));QApplication.processEvents()
    area=ui.today_scroll;assert area.widget().width()<=area.viewport().width()
    assert ui.today_tree.height()>=220 and all(h.parentWidget().height()>=104 for h,_,_ in ui.trail_days)
    area.ensureWidgetVisible(ui.back_to_today);QApplication.processEvents()
    assert area.viewport().rect().contains(ui.back_to_today.mapTo(area.viewport(),ui.back_to_today.rect().center()))
    area.ensureWidgetVisible(ui.today_tree);QApplication.processEvents()
    assert area.verticalScrollBar().maximum()>0 and not area.horizontalScrollBar().isVisible()


def test_archived_resource_requires_opt_in_and_filters_use_parent(tmp_path):
    s=Store(tmp_path);pid=create(s);r=s.tasks(pid)[0];resource=task_resources.item('归档教材','https://example.invalid/lesson')
    task_resources.save(s,r['id'],[resource],[]);s.archive(pid);data=[dict(resource,task_id=r['id'])]
    assert not global_search.search(s.tasks(),s.list_plans(archived=True),'归档教材',resources=data,entity='resource')
    assert global_search.search(s.tasks(),s.list_plans(archived=True),'归档教材',resources=data,entity='resource',include_archived=True,plan_id=pid)
    assert not global_search.search(s.tasks(),s.list_plans(archived=True),'归档教材',resources=data,entity='resource',include_archived=True,plan_id='__standalone__')


def test_review_feature_is_only_guidance_and_invalid_association_is_skipped(workspace,monkeypatch):
    w=workspace;monkeypatch.setattr(w,'generate_review',lambda:pytest.fail('search must not invoke model'),raising=False)
    before=backup_manager.live_hash(w.store);open_feature(w,'review');assert '不发出请求' in w.notice.text()
    assert backup_manager.live_hash(w.store)==before
    tid=w.store.tasks()[0]['id']
    with w.store.write() as c:c.execute('INSERT INTO task_resources VALUES (?,?)',(tid,'invalid-json'))
    d=w.open_global_search();assert d.resource_errors==1 and '关联格式异常' in d.count.text()


def test_tick_tracks_today_but_does_not_shift_another_selection(ui,monkeypatch):
    import desktop.window as module
    class Tomorrow(date):
        @classmethod
        def today(cls):return cls(2026,10,17)
    ui.today_selected_day=ui.display_date
    monkeypatch.setattr(module,'date',Tomorrow);ui.tick()
    assert ui.today_selected_day.isoformat()=='2026-10-17'
    ui.today_selected_day=date(2026,10,14);ui.display_date=date(2026,10,16);ui.tick()
    assert ui.today_selected_day==date(2026,10,14)


def test_selected_day_budget_remains_global_under_plan_filter(ui):
    w=ui;first=create(w.store);create(w.store);day=date.fromisoformat(w.store.tasks(first)[0]['due_date'])
    rows=[r for r in w.store.tasks() if r['due_date']==day.isoformat()]
    assert rows
    w.store.set_setting('daily_budget',[0]*7);w.refresh();w.select_today_day(day)
    text=w.today_budget.text();w.today_plan_filter.setCurrentIndex(w.today_plan_filter.findData(first))
    assert w.today_budget.text()==text and str(sum(r['task'].duration_minutes for r in rows)) in text and '超出' in text
