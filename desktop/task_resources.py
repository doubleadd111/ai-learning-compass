"""Explicit task associations. Never copy, upload, execute or delete targets."""
from contextlib import closing
import json
from pathlib import PureWindowsPath
from urllib.parse import urlsplit
from desktop.models import new_id


def upgrade(c):
    c.execute('CREATE TABLE IF NOT EXISTS task_resources (task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE, items_json TEXT NOT NULL)')
    c.execute('PRAGMA user_version=10')


def validate(items):
    if not isinstance(items,list) or len(items)>40:raise ValueError('每项任务最多关联40份资料或成果')
    ids=set()
    for r in items:
        if (not isinstance(r,dict) or set(r)!={'id','kind','title','target','role'} or
            not isinstance(r['id'],str) or not 1<=len(r['id'])<=80 or r['id'] in ids or
            r['kind'] not in ('url','file') or r['role'] not in ('material','result') or
            not isinstance(r['title'],str) or not 1<=len(r['title'].strip())<=120 or
            not isinstance(r['target'],str) or not 1<=len(r['target'])<=2048 or
            any(ord(ch)<32 for ch in r['target'])):raise ValueError('资料需要名称、有效地址和唯一标识')
        target=r['target']
        if r['kind']=='url':
            try:
                u=urlsplit(target)
                if u.scheme not in ('https','http') or not u.hostname or u.username or u.password:raise ValueError()
                _=u.port
            except ValueError:raise ValueError('网址须为http或https，不能包含账号或密码') from None
        else:
            path=PureWindowsPath(target)
            if not path.is_absolute() or target.startswith(('\\\\','//','\\?','\\.')) or path.drive.endswith(':') is False:
                raise ValueError('请选择本机绝对文件路径；不支持网络共享或设备路径')
        ids.add(r['id'])
    return items


def validate_rows(rows,tids):
    seen=set()
    for row in rows:
        if set(row)!={'task_id','items_json'} or row['task_id'] not in tids or row['task_id'] in seen:raise ValueError('资料所属任务不一致')
        validate(json.loads(row['items_json']));seen.add(row['task_id'])


def load(store,tid):
    with closing(store.connect()) as c:
        if not c.execute('SELECT 1 FROM tasks WHERE id=?',(tid,)).fetchone():raise ValueError('任务已不存在')
        row=c.execute('SELECT items_json FROM task_resources WHERE task_id=?',(tid,)).fetchone()
        return json.loads(row[0]) if row else []


def save(store,tid,items,expected):
    items=validate(items)
    with store.write() as c:
        task=c.execute('SELECT plan_id FROM tasks WHERE id=?',(tid,)).fetchone()
        if not task:raise ValueError('任务已不存在，未保存关联')
        row=c.execute('SELECT items_json FROM task_resources WHERE task_id=?',(tid,)).fetchone()
        current=json.loads(row[0]) if row else []
        if current!=expected:raise ValueError('资料关联已被其他窗口修改，请刷新后重试')
        if current==items:return
        before=store._snapshot_in_transaction(c,task['plan_id']) if task['plan_id'] else None
        if items:c.execute('INSERT INTO task_resources VALUES (?,?) ON CONFLICT(task_id) DO UPDATE SET items_json=excluded.items_json',(tid,json.dumps(items,ensure_ascii=False)))
        else:c.execute('DELETE FROM task_resources WHERE task_id=?',(tid,))
        if task['plan_id']:
            store._touch(c,task['plan_id']);store._version(c,task['plan_id'],'修改资料与成果关联',before)


def item(title,target,kind='url',role='material'):
    result=dict(id=new_id(),title=title.strip(),target=target.strip(),kind=kind,role=role)
    validate([result]);return result
