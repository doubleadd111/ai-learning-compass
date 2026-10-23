"""Local task operations. Previews use a scratch database; applying is atomic."""
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import sqlite3

from desktop.scheduling import fingerprint, capacity, plan_loads, manual_move_reason

TABLES = ('task_dependencies', 'reminder_controls')


def upgrade(c):
    c.execute('''CREATE TABLE IF NOT EXISTS task_dependencies (
        task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        prerequisite_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
        PRIMARY KEY(task_id, prerequisite_id), CHECK(task_id!=prerequisite_id))''')
    c.execute('''CREATE TABLE IF NOT EXISTS reminder_controls (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
        schedule_key TEXT NOT NULL, snooze_until TEXT, muted_on TEXT,
        sequence INTEGER NOT NULL DEFAULT 0 CHECK(sequence>=0))''')
    c.execute('CREATE INDEX IF NOT EXISTS dependencies_predecessor ON task_dependencies(prerequisite_id)')
    c.execute('PRAGMA user_version=9')


def validate(c):
    graph = {}
    for r in c.execute('''SELECT d.*,t.plan_id,p.plan_id AS parent_plan FROM task_dependencies d
            JOIN tasks t ON t.id=d.task_id JOIN tasks p ON p.id=d.prerequisite_id'''):
        if r['plan_id'] != r['parent_plan']:
            raise ValueError('前置任务必须来自同一计划或同一独立任务箱')
        graph.setdefault(r['task_id'], []).append(r['prerequisite_id'])
    # Iterative topological check avoids recursion limits on large task boxes.
    nodes = set(graph) | {i for values in graph.values() for i in values}
    incoming = {n: 0 for n in nodes}
    for values in graph.values():
        for n in values: incoming[n] += 1
    pending = [n for n in nodes if incoming[n] == 0]
    visited = 0
    while pending:
        n = pending.pop(); visited += 1
        for p in graph.get(n, []):
            incoming[p] -= 1
            if incoming[p] == 0: pending.append(p)
    if visited != len(nodes):
        raise ValueError('前置任务形成循环，请取消相互依赖')
    for r in c.execute('SELECT * FROM reminder_controls'):
        if r['snooze_until']: datetime.fromisoformat(r['snooze_until'])
        if r['muted_on']: date.fromisoformat(r['muted_on'])
        datetime.fromisoformat(r['schedule_key'])
        if type(r['sequence']) is not int or r['sequence'] < 0:
            raise ValueError('提醒处理状态无效')


def token(store):
    with closing(store.connect()) as c: return fingerprint(c)


def prerequisites(store, task_id):
    return store.read('''SELECT t.* FROM tasks t JOIN task_dependencies d ON d.prerequisite_id=t.id
        WHERE d.task_id=? ORDER BY t.due_date,t.id''', (task_id,))


def dependency_warnings(c, task_ids=None, dates=None):
    selected = set(task_ids) if task_ids is not None else None
    result = []
    for r in c.execute('''SELECT t.id,t.payload_json,t.due_date,p.payload_json AS parent,
            p.due_date AS parent_date,p.id AS parent_id,p.status FROM task_dependencies d
            JOIN tasks t ON t.id=d.task_id JOIN tasks p ON p.id=d.prerequisite_id'''):
        if selected is not None and r['id'] not in selected: continue
        # Both ends may move in one batch. Check the final effective dates.
        due=(dates or {}).get(r['id'],r['due_date'])
        parent_date=(dates or {}).get(r['parent_id'],r['parent_date'])
        if parent_date > due:
            result.append(f"{json.loads(r['payload_json'])['title']}：安排早于前置任务“{json.loads(r['parent'])['title']}”（{parent_date}）")
    return result


def set_dependencies(store, tid, ids, expected):
    ids = tuple(ids)
    if len(set(ids)) != len(ids) or tid in ids: raise ValueError('不能重复选择或依赖自己')
    with store.write(check_budget=False) as c:
        if fingerprint(c) != expected: raise ValueError('任务或记录已有变化，请重新打开前置任务')
        row = c.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
        if not row: raise ValueError('任务已不存在')
        if c.execute('SELECT 1 FROM reviews WHERE plan_id=? AND week=?',(row['plan_id'],row['week'])).fetchone():
            raise ValueError('该周已复盘，保留原来的任务关系')
        before = store._snapshot_in_transaction(c,row['plan_id']) if row['plan_id'] else None
        for pid in ids:
            p = c.execute('SELECT plan_id FROM tasks WHERE id=?',(pid,)).fetchone()
            if not p or p[0] != row['plan_id']: raise ValueError('前置任务必须来自同一计划或独立任务箱')
        c.execute('DELETE FROM task_dependencies WHERE task_id=?',(tid,))
        c.executemany('INSERT INTO task_dependencies VALUES (?,?)',[(tid,p) for p in ids])
        validate(c)
        store._touch(c,row['plan_id'])
        if before: store._version(c,row['plan_id'],'设置前置任务',before)


def _batch(store, c, ids, operation, value):
    if not ids or len(ids) > 2000 or len(set(ids)) != len(ids): raise ValueError('请选择1–2000项不同任务')
    if operation not in ('date','group','complete','trash'): raise ValueError('未知批量操作')
    if operation == 'date': date.fromisoformat(value)
    if operation == 'group' and (not isinstance(value,str) or len(value)>40): raise ValueError('分组最多40字')
    focus = c.execute("SELECT value_json FROM settings WHERE key='focus_state'").fetchone()
    active = json.loads(focus[0]) if focus else {}
    rows, blocked = [], []
    for tid in ids:
        r = c.execute('''SELECT t.*,COALESCE(p.archived,0) AS archived FROM tasks t
            LEFT JOIN plans p ON p.id=t.plan_id WHERE t.id=?''',(tid,)).fetchone()
        if not r: raise ValueError('有任务已不存在，请重新选择')
        r = dict(r)
        r['focus_active'] = active.get('task_id')==tid and bool(active.get('session_id') or active.get('phase')=='break')
        reviewed = [w[0] for w in c.execute('SELECT week FROM reviews WHERE plan_id=?',(r['plan_id'],))]
        reason = '请先结束关联专注或休息' if r['focus_active'] else '计划已归档' if r['archived'] else ''
        if not reason and operation == 'date': reason = manual_move_reason(r,reviewed)
        if not reason and operation in ('group','complete') and not r['manual'] and r['week'] in reviewed: reason='本周已复盘'
        if not reason and operation == 'trash' and not r['manual']: reason='只可删除手动任务；学习计划任务请保留或调整'
        if reason: blocked.append((json.loads(r['payload_json'])['title'],reason))
        rows.append(r)
    if blocked: raise ValueError('以下任务不能调整；本批次没有保存：\n'+'\n'.join(f'{t}：{r}' for t,r in blocked))
    plans = {r['plan_id'] for r in rows if r['plan_id']}
    before = {p: store._snapshot_in_transaction(c,p) for p in plans}
    changes = []
    for r in rows:
        tid,title = r['id'],json.loads(r['payload_json'])['title']
        if operation=='date':
            start = c.execute('SELECT start_date FROM plans WHERE id=?',(r['plan_id'],)).fetchone()
            if start and value < start[0]: raise ValueError(f'{title}：不能早于计划开始日{start[0]}')
            pause=c.execute('SELECT pause_start,resume_date FROM plan_schedules WHERE plan_id=?',(r['plan_id'],)).fetchone()
            if pause and pause['pause_start'] and pause['pause_start']<=value<pause['resume_date']:
                raise ValueError(f'{title}：所选日期在暂停期间，请选择{pause["resume_date"]}当天或之后')
            c.execute('UPDATE tasks SET due_date=? WHERE id=?',(value,tid))
            c.execute('UPDATE plan_schedules SET end_date=MAX(end_date,?) WHERE plan_id=?',(value,r['plan_id']))
            changes.append(f'{title}：{r["due_date"]} → {value}')
        elif operation=='group':
            from desktop.learning_tools import details_in
            d = details_in(c,tid)
            c.execute('''INSERT INTO task_details VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET stage=excluded.stage''',
                      (tid,value.strip(),json.dumps(d['steps'],ensure_ascii=False)))
            changes.append(f'{title}：{d["stage"] or "未分组"} → {value.strip() or "未分组"}')
        elif operation=='complete':
            c.execute("UPDATE tasks SET status='已完成' WHERE id=?",(tid,))
            changes.append(f'{title}：{r["status"]} → 已完成（不增加学习时长）')
        else:
            from desktop.trash import capture_task
            capture_task(c,tid)
            c.execute('DELETE FROM tasks WHERE id=?',(tid,))
            changes.append(f'{title}：移入回收站，30天内可恢复；相关前置关系将移除')
    for pid in plans:
        store._touch(c,pid)
        store._version(c,pid,'批量'+dict(date='改期',group='分组',complete='完成',trash='移入回收站')[operation],before[pid])
    return changes


@dataclass(frozen=True)
class BatchPreview:
    token: str
    ids: tuple
    operation: str
    value: str
    changes: tuple
    warnings: tuple
    loads: tuple


def preview_batch(store, ids, operation, value=''):
    with closing(store.connect()) as source, closing(sqlite3.connect(':memory:')) as scratch:
        source.execute('BEGIN')
        expected = fingerprint(source)
        scratch.row_factory=sqlite3.Row
        scratch.deserialize(source.serialize()); scratch.execute('PRAGMA foreign_keys=ON')
        changes = _batch(store,scratch,tuple(ids),operation,value)
        warnings = dependency_warnings(scratch)
        from desktop.scheduling import report_text, plan_limit_text
        warnings += [plan_limit_text([r]) for r in plan_loads(scratch).values() if r['over']]
        loads = tuple(capacity(scratch).values())
        warnings += [report_text([r]) for r in loads if r['over']]
        return BatchPreview(expected,tuple(ids),operation,value,tuple(changes),tuple(warnings),loads)


def apply_batch(store, p):
    # A new transaction revalidates selected rows and budget, not the UI summary.
    with store.write(confirm_plan_limits=True) as c:
        if fingerprint(c) != p.token: raise ValueError('任务、记录、专注或预算已有变化，请重新预览')
        changes = _batch(store,c,p.ids,p.operation,p.value)
        if tuple(changes)!=p.changes: raise ValueError('批量预览不一致，请重新预览')


def schedule_key(row): return f"{row['due_date']}T{row['remind_at'] or '00:00'}:00"


def reminder_rows(store, moment=None):
    moment = (moment or datetime.now()).replace(tzinfo=None)
    controls = {r['task_id']:r for r in store.read('SELECT * FROM reminder_controls')}
    delivered = {(r['task_id'],r['fingerprint']) for r in store.read('SELECT * FROM notifications')}
    result=[]
    for r in store.tasks(due=moment.date().isoformat()):
        scheduled=datetime.fromisoformat(schedule_key(r))
        ctrl=controls.get(r['id'],{})
        if ctrl.get('schedule_key')!=schedule_key(r): ctrl={}
        if r['due_date']<moment.date().isoformat(): state='逾期'
        elif r['remind_at'] and scheduled<=moment: state='已提醒' if (r['id'],schedule_key(r)) in delivered else '错过提醒'
        else: state='今日待办'
        if ctrl.get('muted_on')==moment.date().isoformat(): state+=' · 今日不再提醒'
        elif ctrl.get('snooze_until'): state+=' · 稍后 '+ctrl['snooze_until'][11:16]
        result.append({**r,'reminder_state':state})
    return result


def control_reminder(store, tid, action, expected, moment=None, minutes=15):
    moment=(moment or datetime.now()).replace(tzinfo=None)
    if action not in ('snooze','mute','clear') or type(minutes) is not int or not 1<=minutes<=1440: raise ValueError('提醒操作无效')
    with store.write(check_budget=False) as c:
        if fingerprint(c)!=expected: raise ValueError('任务或提醒已有变化，请刷新后重试')
        r=c.execute('SELECT * FROM tasks WHERE id=?',(tid,)).fetchone()
        if not r: raise ValueError('任务已不存在')
        old=c.execute('SELECT sequence FROM reminder_controls WHERE task_id=?',(tid,)).fetchone()
        c.execute('INSERT OR REPLACE INTO reminder_controls VALUES (?,?,?,?,?)',(tid,schedule_key(r),
            (moment+timedelta(minutes=minutes)).isoformat() if action=='snooze' else None,
            moment.date().isoformat() if action=='mute' else None,(old[0] if old else 0)+1))


def due_reminders(store, moment):
    moment=moment.replace(tzinfo=None)
    if store.setting('reminders_paused',False): return []
    due=[]
    # Read and delivery stamps share a write transaction to prevent double delivery.
    with store.write(check_budget=False) as c:
        from desktop.scheduling import paused
        rows=c.execute('''SELECT t.*,s.pause_start,s.resume_date,COALESCE(p.archived,0) AS archived FROM tasks t
            LEFT JOIN plans p ON p.id=t.plan_id LEFT JOIN plan_schedules s ON s.plan_id=p.id
            WHERE t.status!='已完成' AND COALESCE(p.archived,0)=0 AND t.due_date<=?''',(moment.date().isoformat(),)).fetchall()
        for raw in rows:
            r=dict(raw)
            if paused(r,moment.date()): continue
            key=schedule_key(r)
            ctrl=c.execute('SELECT * FROM reminder_controls WHERE task_id=? AND schedule_key=?',(r['id'],key)).fetchone()
            if ctrl and ctrl['muted_on']==moment.date().isoformat(): continue
            if ctrl and ctrl['snooze_until']:
                if datetime.fromisoformat(ctrl['snooze_until'])>moment: continue
                key+=':snooze:'+str(ctrl['sequence'])
            else:
                if not r['remind_at'] or datetime.fromisoformat(key)>moment: continue
                if c.execute('''SELECT 1 FROM pause_history WHERE plan_id=? AND start_date<=?
                        AND ?<COALESCE(ended_date,resume_date)''',(r['plan_id'],r['due_date'],r['due_date'])).fetchone(): continue
            if c.execute('SELECT 1 FROM notifications WHERE task_id=? AND fingerprint=?',(r['id'],key)).fetchone(): continue
            c.execute('INSERT INTO notifications VALUES (?,?,?)',(r['id'],key,moment.isoformat()))
            from models import DailyTask
            r['task']=DailyTask.model_validate_json(r['payload_json']); due.append(r)
    return due
