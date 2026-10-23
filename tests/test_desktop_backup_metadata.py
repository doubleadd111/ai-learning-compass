"""Backup labels and provenance; all files and records are fictional."""
from contextlib import closing
from datetime import date,timedelta
import json
import os
import shutil
import sqlite3
import pytest

from desktop import VERSION
from desktop.store import Store
from desktop import backup_metadata as meta,backup_manager as manager
from tests.test_desktop_store import create


@pytest.mark.parametrize('prefix,kind',[
    ('manual','manual'),('before-restore','before_restore'),
    ('before-history-replay','before_history'),('before-delete-plan','before_delete_plan'),
    ('custom-snapshot','other')])
def test_named_backups_record_type_version_actual_schema_and_hash(tmp_path,prefix,kind):
    s=Store(tmp_path);create(s);p=s.manual_backup(prefix)
    info=json.loads(p.with_suffix('.meta.json').read_text(encoding='utf-8'))
    assert info['kind']==kind and info['app_version']==VERSION and info['schema']==10
    assert info['sha256']==meta.file_hash(p) and info['created_at']
    assert not any(word in json.dumps(info).lower() for word in ('api_key','payload','note','credential'))
    listed=next(r for r in manager.catalog(s) if r['path']==p)
    assert listed['kind']==kind and listed['version']==VERSION and listed['kind_source']=='metadata'


def test_daily_backup_records_metadata_once_not_rewritten(tmp_path):
    s=Store(tmp_path);create(s);p=s.backups/f'daily-{date.today().isoformat()}.sqlite3'
    assert p.exists();data=p.read_bytes();description=p.with_suffix('.meta.json').read_bytes()
    s.set_setting('test_key','not_a_key')
    assert p.read_bytes()==data and p.with_suffix('.meta.json').read_bytes()==description
    r=next(r for r in manager.catalog(s) if r['path']==p)
    assert r['category']=='daily' and r['version']==VERSION and '自动备份' in r['kind_label']


def test_migration_uses_old_real_schema_and_no_staging_sidecar(tmp_path):
    s=Store(tmp_path);pid=create(s);old=s.read('SELECT * FROM tasks ORDER BY id')
    with closing(s.connect()) as c,c:
        c.execute('DROP TABLE task_dependencies');c.execute('DROP TABLE reminder_controls');c.execute('PRAGMA user_version=8')
    s=Store(tmp_path)
    p=next(s.backups.glob('before-schema-10-*.sqlite3'));r=meta.description(p,8)
    assert r['version']==VERSION and r['kind']=='before_schema' and r['target_version']==VERSION
    assert json.loads(p.with_suffix('.meta.json').read_text())['schema']==8
    assert s.read('SELECT * FROM tasks ORDER BY id')==old
    assert not list(s.directory.glob('upgrade-*.meta.json'))


@pytest.mark.parametrize('name,kind',[
    ('daily-2026-10-22.sqlite3','automatic_daily'),
    ('manual-20261022-100000-abcd.sqlite3','manual'),
    ('pre-update-2026.10.99-20261022-100000.sqlite3','before_update'),
    ('before-schema-8-abcd.sqlite3','before_schema'),
    ('before-restore-20261022-100000-abcd.sqlite3','before_restore'),
    ('before-history-replay-20261022-100000-abcd.sqlite3','before_history'),
    ('before-delete-plan-20261022-100000-abcd.sqlite3','before_delete_plan'),
    ('a-file.sqlite3','other')])
def test_old_backup_type_is_only_a_label_never_infers_app_version(tmp_path,name,kind):
    s=Store(tmp_path);p=s.backups/name;s.backup_to(p,kind=None)
    before=p.read_bytes();r=meta.description(p,9)
    assert r['kind']==kind and r['version']=='未记录'
    assert r['kind_source'] in ('filename','unknown') and not r['target_version']
    assert p.read_bytes()==before and not p.with_suffix('.meta.json').exists()


@pytest.mark.parametrize('bad',['json','hash','schema','kind','timestamp','version','oversized'])
def test_bad_sidecar_does_not_mark_sqlite_contents_corrupt(tmp_path,bad):
    s=Store(tmp_path);create(s);p=s.manual_backup();side=p.with_suffix('.meta.json')
    info=json.loads(side.read_text());text=None
    if bad=='json':text='not-json'
    elif bad=='hash':info['sha256']='0'*64
    elif bad=='schema':info['schema']=123
    elif bad=='kind':info['kind']='unrecognized'
    elif bad=='timestamp':info['created_at']='2026-10-22T10:00:00'
    elif bad=='version':info['app_version']='fake'
    else:text=' '*66000
    side.write_text(text if text is not None else json.dumps(info),encoding='utf-8')
    r=next(r for r in manager.catalog(s) if r['path']==p)
    assert r['version']=='未记录' and r['metadata_warning']
    assert '无法读取' not in r['summary'] and manager.preview(s,p).summary['plans']==1


def test_recorded_kind_wins_over_renamed_filename(tmp_path):
    s=Store(tmp_path);p=s.manual_backup();renamed=s.backups/'daily-2000-01-01.sqlite3'
    shutil.copy2(p,renamed);shutil.copy2(p.with_suffix('.meta.json'),renamed.with_suffix('.meta.json'))
    r=meta.description(renamed,10);assert r['kind']=='manual' and r['kind_source']=='metadata'


def test_old_manual_sidecar_still_supported_without_rewriting(tmp_path):
    s=Store(tmp_path);p=s.manual_backup();side=p.with_suffix('.meta.json')
    old=dict(app_version='2026.10.13.1',schema=10,sha256=meta.file_hash(p),created_at='2026-10-22T10:00:00+08:00')
    side.write_text(json.dumps(old),encoding='utf-8');before=side.read_bytes()
    r=meta.description(p,10);assert r['version']=='2026.10.13.1' and r['kind_source']=='filename'
    assert side.read_bytes()==before


def test_retention_cleans_paired_sidecars_not_manual_files(tmp_path):
    s=Store(tmp_path);create(s);manual=s.manual_backup()
    today=date.today()
    for i in range(1,34):
        s.backup_to(s.backups/f'daily-{(today-timedelta(days=i)).isoformat()}.sqlite3',kind='automatic_daily')
    s._backed_up_date='';s._daily_backup()
    assert len(list(s.backups.glob('daily-*.sqlite3')))==30
    assert len(list(s.backups.glob('daily-*.meta.json')))==30
    assert manual.exists() and manual.with_suffix('.meta.json').exists()


def test_metadata_failure_aborts_restore_and_keeps_current_records(tmp_path,monkeypatch):
    s=Store(tmp_path);pid=create(s);p=s.manual_backup();s.rename_plan(pid,'保留当前名称')
    before=s.read('SELECT * FROM plans')
    def fail(*args,**kwargs):raise OSError('description unavailable')
    monkeypatch.setattr(meta.os,'replace',fail)
    with pytest.raises(OSError):s.restore(p)
    assert s.read('SELECT * FROM plans')==before and not list(s.backups.glob('*.tmp'))


def test_update_script_records_type_and_target_not_filename_source_version():
    from pathlib import Path
    code=(Path(__file__).resolve().parents[1]/'scripts/install_local_test.py').read_text(encoding='utf-8')
    assert "record(backup, 'before_update', app_version=VERSION, target_version=VERSION)" in code
