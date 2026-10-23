"""Read-only backup discovery and stale-safe whole-database restore preview."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from functools import lru_cache
from desktop.backup_metadata import file_hash, description


def live_hash(store):
    with closing(store.connect()) as c:
        c.execute('BEGIN')
        return hashlib.sha256(c.serialize()).hexdigest()


def _catalog_entry(path):
    p=Path(path)
    info=dict(path=p,name=p.name,time='未记录',size=0,version='未记录',schema='未知',summary='',
              kind='other',kind_label='其他备份 · 来源未记录',category='other',kind_note='',metadata_warning='')
    try:
        stat=p.stat();info.update(time=datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S'),size=stat.st_size)
        with closing(sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)) as c:
            from desktop.store import APP_ID
            if c.execute('PRAGMA application_id').fetchone()[0]!=APP_ID:raise ValueError('不是学习罗盘备份')
            info['schema']=c.execute('PRAGMA user_version').fetchone()[0]
            counts=[c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('plans','tasks')]
            info['summary']=f'{counts[0]}份计划 · {counts[1]}项任务'
        info.update(description(p,info['schema']))
        from desktop.backup_cleanup import read_note
        info['note']=read_note(p)
        if info.get('created_at'):
            info['time']=datetime.fromisoformat(info['created_at']).astimezone().strftime('%Y-%m-%d %H:%M:%S')
            info['time_note']='记录的创建时间'
        else:info['time_note']='文件修改时间（无创建时间记录）'
    except (sqlite3.Error,ValueError,OSError,TypeError,AttributeError):info['summary']='无法读取；不会恢复此文件'
    return info


@lru_cache(maxsize=256)
def _cached_entry(path,signature):
    return _catalog_entry(path)


def catalog_entry(path):
    p=Path(path)
    def stat(q):
        try:
            s=q.stat();return (s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        except OSError:return None
    signature=tuple(stat(q) for q in (p,p.with_suffix('.meta.json'),p.with_suffix('.note.json')))
    return dict(_cached_entry(str(p),signature))


def catalog(store):
    return [catalog_entry(p) for p in sorted(store.backups.glob('*.sqlite3'),key=lambda p:p.stat().st_mtime,reverse=True)]


@dataclass(frozen=True)
class BackupPreview:
    path: Path
    source_hash: str
    current_hash: str
    summary: dict
    differences: tuple


def preview(store,path):
    path=Path(path).resolve()
    if path==store.path.resolve(): raise ValueError('不能选择当前正在使用的数据文件')
    before=file_hash(path)
    summary=store.inspect_backup(path)
    differences=[]
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as old, closing(store.connect()) as current:
        old.row_factory=current.row_factory=sqlite3.Row
        old.execute('BEGIN');current.execute('BEGIN')
        for table,title in (('plans','计划'),('tasks','任务'),('focus_sessions','专注记录'),('settings','设置'),('connections','模型配置'),('study_events','按日期学习记录'),('task_dependencies','前置关系'),('reminder_controls','提醒处理状态'),('task_resources','资料与成果关联')):
            def rows(c):
                if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():return {}
                return {r[0]:dict(r) for r in c.execute(f'SELECT * FROM {table}')}
            a,b=rows(current),rows(old)
            common=set(a)&set(b)
            # Never put row contents (goals, notes or connection details) into logs/UI.
            changed=sum(any(a[i].get(k)!=v for k,v in b[i].items()) for i in common)
            differences.append(f'{title}：当前{len(a)} → 备份{len(b)}；增加{len(set(b)-set(a))}，移除{len(set(a)-set(b))}，覆盖{changed}')
        version=old.execute('PRAGMA user_version').fetchone()[0]
        differences.append(f'备份数据结构：schema {version}；恢复时只迁移副本，不改备份原件。')
        current_hash=hashlib.sha256(current.serialize()).hexdigest()
    if before!=file_hash(path):raise ValueError('备份文件在检查期间发生变化，请重新预览')
    return BackupPreview(path,before,current_hash,summary,tuple(differences))


def restore(store,p):
    if file_hash(p.path)!=p.source_hash or live_hash(store)!=p.current_hash:
        raise ValueError('当前资料或备份已变化，请重新查看恢复差异')
    state=store.setting('focus_state',{})
    if state.get('session_id') or state.get('phase')=='break':raise ValueError('请先结束专注或休息并保存记录，再重新预览')
    # Store.restore validates twice, protects current state, migrates a staging
    # copy, then atomically replaces. No keyring access or source-file writes.
    return store.restore(p.path)
