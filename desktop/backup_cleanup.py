"""Explicit, previewed cleanup of local automatic backups; never current DB."""
from dataclasses import dataclass
from datetime import datetime,timedelta
import json,os
from pathlib import Path
from uuid import uuid4
from desktop.backup_metadata import file_hash


def local_path(store,path):
    p=Path(path)
    if p.is_symlink() or p.parent.resolve()!=store.backups.resolve() or p.suffix!='.sqlite3' or not p.is_file():
        raise ValueError('只能管理本机备份目录中的普通SQLite备份文件')
    return p.resolve()


def note_path(path):return Path(path).with_suffix('.note.json')


def read_note(path):
    p=note_path(path)
    try:
        if p.stat().st_size>8192:return ''
        value=json.loads(p.read_text(encoding='utf-8'))
        if value['sha256']==file_hash(path) and isinstance(value['note'],str) and len(value['note'])<=200:return value['note']
    except (OSError,ValueError,KeyError,TypeError):pass
    return ''


def save_note(store,path,note,expected_hash,expected_note=None):
    path=local_path(store,path)
    if not isinstance(note,str) or len(note)>200:raise ValueError('备份备注最多200字')
    if file_hash(path)!=expected_hash:raise ValueError('备份已有变化，请刷新后重新备注')
    if expected_note is not None and read_note(path)!=expected_note:raise ValueError('备份备注已被其他窗口修改，请刷新后重试')
    p=note_path(path)
    if p.is_symlink():raise ValueError('备注路径不安全，未保存')
    temporary=p.with_name(p.name+'.'+uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps(dict(sha256=expected_hash,note=note.strip()),ensure_ascii=False),encoding='utf-8')
        if file_hash(path)!=expected_hash:raise ValueError('备份已有变化，未保存备注')
        os.replace(temporary,p)
    finally:temporary.unlink(missing_ok=True)


def size_text(size):
    if size<1024:return f'{size} B'
    if size<1024**2:return f'{size/1024:.1f} KB'
    return f'{size/1024**2:.1f} MB'


def inventory_token(store):
    return tuple((p.name,p.stat().st_size,p.stat().st_mtime_ns,p.stat().st_ctime_ns)
                 for p in sorted(store.backups.iterdir()) if p.is_file() and p.suffix in ('.sqlite3','.json'))


@dataclass(frozen=True)
class CleanupPreview:
    token: tuple
    days: int
    files: tuple
    bytes: int
    protected: tuple


def preview(store,days=30):
    if type(days) is not int or not 7<=days<=3650:raise ValueError('至少保留最近7天，最多3650天')
    from desktop.backup_manager import catalog
    token=inventory_token(store);rows=catalog(store);protected=[];latest=set();cutoff=datetime.now().astimezone()-timedelta(days=days)
    # Keep the newest genuinely restorable point of each recognized type.
    for row in rows:
        if row['kind'] not in latest:
            try:store.inspect_backup(row['path'])
            except (ValueError,OSError):continue
            latest.add(row['kind']);protected.append(row['path'].name)
    files=[]
    for row in rows:
        p=local_path(store,row['path'])
        if row['category'] not in ('daily','guard') or p.name in protected:continue
        stamp=datetime.fromisoformat(row['created_at']) if row.get('created_at') else datetime.fromtimestamp(p.stat().st_mtime).astimezone()
        if stamp>=cutoff:continue
        group=[]
        for target in (p,p.with_suffix('.meta.json'),note_path(p)):
            if target.exists():
                if target.is_symlink():raise ValueError('备份关联文件路径不安全，未生成清理预览')
                group.append((target.name,file_hash(target),target.stat().st_size))
        files.append((p.name,tuple(group)))
    if inventory_token(store)!=token:raise ValueError('备份在检查期间发生变化，请重新预览')
    return CleanupPreview(token,days,tuple(files),sum(r[2] for _,group in files for r in group),tuple(protected))


def apply(store,p):
    if not isinstance(p,CleanupPreview) or inventory_token(store)!=p.token:raise ValueError('备份列表已变化，请重新预览清理')
    # Recompute policy and contents after confirmation; never trust UI-supplied paths.
    fresh=preview(store,p.days)
    if fresh!=p:raise ValueError('备份内容或保留规则已变化，请重新预览清理')
    for name,group in p.files:
        local_path(store,store.backups/name)
        for filename,digest,size in group:
            target=store.backups/filename
            if target.is_symlink() or target.parent.resolve()!=store.backups.resolve() or file_hash(target)!=digest:raise ValueError('备份已变化，未开始清理')
    removed=0
    try:
        for _,group in p.files:
            for filename,_,size in group:(store.backups/filename).unlink();removed+=size
    except OSError as e:raise ValueError(f'清理未全部完成，已移除{size_text(removed)}。其余文件保留，请刷新检查。') from e
    return removed
