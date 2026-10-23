"""Fictional data only: references, global search, cleanup and schema10."""
from contextlib import closing
from datetime import date,datetime,timedelta
import json,os,sqlite3
import pytest
from desktop.store import Store,SCHEMA_VERSION
from desktop import task_resources as resources,backup_cleanup as cleanup,backup_manager as backups,global_search
from desktop import trash,plan_history
from tests.test_desktop_store import create,edit


def test_reference_save_read_export_import_trash_restore_and_records(tmp_path):
    s=Store(tmp_path/'a');pid=create(s);r=s.tasks(pid)[0];tid=r['id'];edit(s,r,note='原笔记',manual_minutes=3)
    before=s.tasks(task_id=tid)[0];items=[resources.item('教材','https://example.invalid/course'),resources.item('成果',r'C:\fictional\result.md','file','result')]
    resources.save(s,tid,items,[]);assert resources.load(Store(s.directory),tid)==items
    assert s.tasks(task_id=tid)[0]==before
    target=Store(tmp_path/'b');new=target.import_plan(s.export_plan(pid),date.today());nt=target.tasks(new)[0]
    assert nt['id']!=tid and resources.load(target,nt['id'])==items
    assert nt['note']=='原笔记' and nt['manual_seconds']==180
    p=s.manual_backup();s.rename_plan(pid,'另一名称');s.restore(p);assert resources.load(s,tid)==items
    s.delete_plan(pid);entry=trash.entries(s)[0];proposal=trash.preview(s,entry['id']);trash.apply(s,proposal)
    assert resources.load(s,tid)==items


@pytest.mark.parametrize('kind,target',[
    ('url','javascript:alert(1)'),('url','file:///C:/a.exe'),('url','https://user:pass@example.invalid'),
    ('url','https://'),('url','https://example.invalid:bad'),('file','relative.txt'),
    ('file',r'\\server\share\file'),('file',r'\\?\C:\a'),('file','C:/bad\n.txt')])
def test_unsafe_reference_rejected(kind,target):
    with pytest.raises(ValueError):resources.item('资料',target,kind)


def test_references_conflicts_missing_task_and_replay_preserve_learning(tmp_path):
    s=Store(tmp_path);pid=create(s);r=s.tasks(pid)[0];tid=r['id']
    a=[resources.item('资料','https://example.invalid/a')];resources.save(s,tid,a,[])
    version=s.versions(pid)[0]
    with pytest.raises(ValueError):resources.save(s,tid,[],[])
    with pytest.raises(ValueError):resources.save(s,'missing',a,[])
    edit(s,s.tasks(task_id=tid)[0],status='已完成',note='新记录')
    proposal=plan_history.preview(s,pid,version['id'],'before');plan_history.apply(s,proposal)
    assert resources.load(s,tid)==[] and s.tasks(task_id=tid)[0]['status']=='已完成' and s.tasks(task_id=tid)[0]['note']=='新记录'


def test_schema9_upgrade_and_old_json_keep_old_rows(tmp_path):
    s=Store(tmp_path);pid=create(s);snapshot=json.loads(s.export_plan(pid));snapshot['version']=8;snapshot.pop('task_resources')
    old=s.read('SELECT * FROM tasks ORDER BY id')
    with closing(s.connect()) as c,c:c.execute('DROP TABLE task_resources');c.execute('PRAGMA user_version=9')
    reopened=Store(tmp_path);assert reopened.read('SELECT * FROM tasks ORDER BY id')==old
    point=next(s.backups.glob('before-schema-10-*.sqlite3'));assert Store.inspect_backup(point)['tasks']==len(old)
    assert reopened.read('PRAGMA user_version')[0]['user_version']==SCHEMA_VERSION==10
    reopened.import_plan(json.dumps(snapshot).encode(),date.today());assert reopened.read('SELECT * FROM task_resources')==[]


def test_reference_import_tamper_is_atomic(tmp_path):
    s=Store(tmp_path);pid=create(s);blob=json.loads(s.export_plan(pid))
    blob['task_resources']=[dict(task_id=blob['tasks'][0]['id'],items_json=json.dumps([dict(id='a',kind='url',title='a',role='material',target='javascript:bad')]))]
    before=backups.live_hash(s)
    with pytest.raises(ValueError):s.import_plan(json.dumps(blob).encode(),date.today())
    assert backups.live_hash(s)==before


def test_global_search_scope_status_date_archive_and_standalone(tmp_path):
    from tests.test_desktop_workflow import task
    s=Store(tmp_path);pid=create(s);rows=s.tasks(pid);r=rows[0]
    edit(s,r,status='已完成');standalone=task(s,'唯一独立任务','2026-12-01')
    plans=s.list_plans()+s.list_plans(archived=True);tasks=s.tasks()
    found=global_search.search(tasks,plans,r['task'].title,scope='title');assert any(x['id']==r['id'] for x in found)
    assert not global_search.search(tasks,plans,'不存在的词')
    found=global_search.search(tasks,plans,'唯一独立',plan_id='__standalone__');assert len(found)==1
    assert not global_search.search(tasks,plans,'唯一独立',start='2026-12-02')
    assert all(x['id']!=r['id'] for x in global_search.search(tasks,plans,r['task'].title,status='未完成'))
    s.archive(pid,True);tasks=s.tasks();plans=s.list_plans()+s.list_plans(archived=True)
    assert not global_search.search(tasks,plans,r['task'].title)
    assert global_search.search(tasks,plans,r['task'].title,include_archived=True)
    found=global_search.search(tasks,plans,plans[0]['title'],entity='plan',include_archived=True)
    assert found and found[0]['kind']=='plan'


def age(path,days):
    stamp=datetime.now().astimezone()-timedelta(days=days)
    meta=path.with_suffix('.meta.json');info=json.loads(meta.read_text());info['created_at']=stamp.isoformat();meta.write_text(json.dumps(info),encoding='utf-8')
    os.utime(path,(stamp.timestamp(),stamp.timestamp()))


def test_cleanup_retains_manual_latest_and_recent_cleans_paired_files(tmp_path):
    s=Store(tmp_path);create(s)
    old=s.manual_backup('before-restore');age(old,90)
    manual=s.manual_backup();age(manual,90)
    latest=s.manual_backup('before-restore');cleanup.save_note(s,old,'可清理',cleanup.file_hash(old))
    proposal=cleanup.preview(s,30);assert old.name in [r[0] for r in proposal.files] and manual.name not in [r[0] for r in proposal.files]
    before=backups.live_hash(s);assert old.exists();removed=cleanup.apply(s,proposal)
    assert removed>0 and not old.exists() and not old.with_suffix('.meta.json').exists() and not old.with_suffix('.note.json').exists()
    assert manual.exists() and latest.exists() and backups.live_hash(s)==before


@pytest.mark.parametrize('change',['new','bytes','note'])
def test_cleanup_stale_previews_do_not_delete(tmp_path,change):
    s=Store(tmp_path);create(s);old=s.manual_backup('before-restore');age(old,90);latest=s.manual_backup('before-restore')
    p=cleanup.preview(s)
    if change=='new':s.manual_backup()
    elif change=='bytes':
        with sqlite3.connect(old) as c:c.execute("UPDATE plans SET title='changed'")
    else:cleanup.save_note(s,old,'变化',cleanup.file_hash(old))
    with pytest.raises(ValueError):cleanup.apply(s,p)
    assert old.exists() and latest.exists()


def test_backup_note_cache_and_stale_edit(tmp_path):
    s=Store(tmp_path);create(s);p=s.manual_backup();raw=p.read_bytes();digest=cleanup.file_hash(p)
    first=backups.catalog_entry(p);assert not first.get('note')
    cleanup.save_note(s,p,'课程开始前',digest,'');assert backups.catalog_entry(p)['note']=='课程开始前'
    with pytest.raises(ValueError):cleanup.save_note(s,p,'覆盖他人',digest,'')
    assert p.read_bytes()==raw
    with pytest.raises(ValueError):cleanup.save_note(s,s.path,'不允许',cleanup.file_hash(s.path))


def test_cached_task_payload_never_shares_mutable_objects(tmp_path):
    s=Store(tmp_path);pid=create(s);one=s.tasks(pid)[0];original=one['task'].title;one['task'].title='只改内存'
    assert s.tasks(task_id=one['id'])[0]['task'].title==original


def test_schema10_failed_staging_never_replaces_original(tmp_path,monkeypatch):
    s=Store(tmp_path);create(s)
    with closing(s.connect()) as c,c:c.execute('DROP TABLE task_resources');c.execute('PRAGMA user_version=9')
    original=s.path.read_bytes()
    def fail(_):raise ValueError('模拟迁移失败')
    monkeypatch.setattr(Store,'_upgrade_v10',staticmethod(fail))
    with pytest.raises(ValueError):Store(tmp_path)
    assert s.path.read_bytes()==original and list(s.backups.glob('before-schema-10-*.sqlite3'))
    assert not list(s.directory.glob('upgrade-*.sqlite3'))


def test_backup_with_invalid_resource_is_not_restored(tmp_path):
    s=Store(tmp_path);pid=create(s);tid=s.tasks(pid)[0]['id'];p=s.manual_backup()
    with sqlite3.connect(p) as c:
        c.execute('INSERT INTO task_resources VALUES (?,?)',(tid,json.dumps([dict(id='a',kind='url',title='bad',role='material',target='file:///C:/bad.exe')])))
    before=backups.live_hash(s)
    with pytest.raises(ValueError):s.restore(p)
    assert backups.live_hash(s)==before


def test_forged_cleanup_path_never_deletes_anything(tmp_path):
    from dataclasses import replace
    s=Store(tmp_path);create(s);p=cleanup.preview(s)
    forged=replace(p,files=(('../learning-compass.sqlite3',(('learning-compass.sqlite3',cleanup.file_hash(s.path),s.path.stat().st_size),)),))
    before=backups.live_hash(s)
    with pytest.raises(ValueError):cleanup.apply(s,forged)
    assert backups.live_hash(s)==before
