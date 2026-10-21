"""Local organization and dated learning evidence, separate from scheduling."""
from contextlib import closing
from datetime import date, datetime, timedelta
import json

from models import DailyTask
from desktop.models import new_id
from desktop.scheduling import fingerprint

TASK_TABLES = ('task_attention', 'study_events')
PRIORITIES = ('普通', '低', '中', '高')


def duration_text(seconds):
    minutes,remainder=divmod(max(0,int(seconds)),60)
    return f'{minutes} 分 {remainder} 秒' if remainder else f'{minutes} 分钟'


def upgrade(c):
    c.execute('''CREATE TABLE IF NOT EXISTS task_attention (
        task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
        priority INTEGER NOT NULL DEFAULT 0 CHECK(priority BETWEEN 0 AND 3),
        pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)), focus_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS study_events (
        id TEXT PRIMARY KEY, task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
        occurred_on TEXT NOT NULL, seconds INTEGER NOT NULL CHECK(seconds>0),
        source TEXT NOT NULL CHECK(source IN ('focus','manual')), created_at TEXT NOT NULL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS captures (
        id TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
        plan_id TEXT REFERENCES plans(id) ON DELETE SET NULL,
        priority INTEGER NOT NULL DEFAULT 0 CHECK(priority BETWEEN 0 AND 3),
        pinned INTEGER NOT NULL DEFAULT 0 CHECK(pinned IN (0,1)),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
    c.execute('CREATE INDEX IF NOT EXISTS study_events_day ON study_events(occurred_on,task_id)')
    c.execute('PRAGMA user_version=8')


def validate_rows(rows, task_ids=None):
    for r in rows.get('task_attention', []):
        if type(r['priority']) is not int or r['priority'] not in range(4) or type(r['pinned']) is not int or r['pinned'] not in (0, 1):
            raise ValueError('任务优先级或置顶标记无效')
        if r['focus_date']:
            date.fromisoformat(r['focus_date'])
        if task_ids is not None and r['task_id'] not in task_ids:
            raise ValueError('任务整理资料所属任务不一致')
    for r in rows.get('study_events', []):
        date.fromisoformat(r['occurred_on'])
        datetime.fromisoformat(r['created_at'])
        if type(r['seconds']) is not int or r['seconds'] <= 0 or r['source'] not in ('focus', 'manual'):
            raise ValueError('实际学习日期记录无效')
        if task_ids is not None and r['task_id'] not in task_ids:
            raise ValueError('学习日期记录所属任务不一致')
    if 'tasks' in rows:
        totals={}
        for r in rows.get('study_events',[]):
            key=(r['task_id'],r['source']);totals[key]=totals.get(key,0)+r['seconds']
        for task in rows['tasks']:
            if any(totals.get((task['id'],source),0)>task[column] for source,column in
                   (('manual','manual_seconds'),('focus','focus_seconds'))):
                raise ValueError('按日期记录的用时不能超过任务累计用时')


def validate_database(c):
    validate_rows({t: [dict(r) for r in c.execute(f'SELECT * FROM {t}')] for t in TASK_TABLES})
    for r in c.execute('SELECT * FROM captures'):
        DailyTask.model_validate_json(r['payload_json'])
        if r['priority'] not in range(4) or r['pinned'] not in (0, 1):
            raise ValueError('收集箱资料无效')
        datetime.fromisoformat(r['created_at'])
        datetime.fromisoformat(r['updated_at'])
    for source, column in (('manual', 'manual_seconds'), ('focus', 'focus_seconds')):
        if c.execute(f'''SELECT 1 FROM tasks t WHERE {column} <
            (SELECT COALESCE(SUM(seconds),0) FROM study_events e WHERE e.task_id=t.id AND e.source=?)''', (source,)).fetchone():
            raise ValueError('按日期记录的用时不能超过任务累计用时')


def attention_in(c, task_id, *, priority=0, pinned=False, focus_date=None):
    if type(priority) is not int or priority not in range(4) or pinned not in (False, True):
        raise ValueError('任务优先级或置顶标记无效')
    row = dict(task_id=task_id, priority=int(priority), pinned=int(bool(pinned)), focus_date=focus_date)
    validate_rows({'task_attention': [row]})
    c.execute('''INSERT INTO task_attention VALUES (?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET
        priority=excluded.priority,pinned=excluded.pinned,focus_date=excluded.focus_date''', tuple(row.values()))


def set_attention(store, task_id, *, priority=0, pinned=False, focus_date=None, token=None):
    with store.write(check_budget=False) as c:
        if token is not None and token != fingerprint(c):
            raise ValueError('任务或记录已有变化，请刷新后重新设置')
        row = c.execute('SELECT plan_id FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row:
            raise ValueError('任务已不存在')
        before=store._snapshot_in_transaction(c,row[0]) if row[0] else None
        attention_in(c, task_id, priority=priority, pinned=pinned, focus_date=focus_date)
        store._touch(c, row[0])
        if row[0]:store._version(c,row[0],'调整优先级与重点',before)


def ordered(rows, today=None):
    today = (today or date.today()).isoformat()
    return sorted(rows, key=lambda r: (not r.get('pinned'), r.get('focus_date') != today,
        -r.get('priority', 0), r['due_date'], r['week'], r['day'] or 0))


def label(row, today=None):
    tags = []
    if row.get('pinned'): tags.append('置顶')
    if row.get('focus_date') == (today or date.today()).isoformat(): tags.append('今天重点')
    if row.get('priority'): tags.append(PRIORITIES[row['priority']] + '优先级')
    return ' · '.join(tags)


def _check_manual_balance(c, task_id, seconds):
    dated = c.execute("SELECT COALESCE(SUM(seconds),0) FROM study_events WHERE task_id=? AND source='manual'", (task_id,)).fetchone()[0]
    if seconds < dated:
        raise ValueError('累计补记少于已有按日记录。请先核对实际学习记录，不能覆盖已记录的用时')


def log_manual(store, task_id, day, minutes, *, token=None, entry_id=None):
    if isinstance(day, str): day = date.fromisoformat(day)
    if not date(1900, 1, 1) <= day <= date.today() or type(minutes) is not int or not 1 <= minutes <= 1440:
        raise ValueError('请选择已经发生的学习日期及1—1440分钟')
    entry_id = entry_id or new_id()
    with store.write(check_budget=False) as c:
        if token is not None and token != fingerprint(c):
            raise ValueError('学习记录已有变化，请重新打开后补记')
        if c.execute('SELECT 1 FROM study_events WHERE id=?', (entry_id,)).fetchone():
            raise ValueError('这条补记已保存，请勿重复提交')
        row = c.execute('SELECT plan_id,manual_seconds FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not row: raise ValueError('任务已不存在')
        if row[1] + minutes * 60 > 1440 * 60:
            raise ValueError('该任务累计人工补记不能超过1440分钟')
        stamp = datetime.now().astimezone().isoformat()
        c.execute('INSERT INTO study_events VALUES (?,?,?,?,?,?)', (entry_id, task_id, day.isoformat(), minutes*60, 'manual', stamp))
        c.execute("UPDATE tasks SET manual_seconds=manual_seconds+?,status=CASE WHEN status='未开始' THEN '进行中' ELSE status END WHERE id=?", (minutes*60, task_id))
        store._touch(c, row[0])
    return entry_id


def statistics(store, start, end, plan_id=None, include_archived=False):
    if not start <= end <= start + timedelta(days=365):
        raise ValueError('请选择有效的日期范围，最长一年')
    tasks = {r['id']: r for r in store.tasks(plan_id) if include_archived or not r['archived']}
    events = [r for r in store.read('SELECT * FROM study_events ORDER BY occurred_on,id')
        if r['task_id'] in tasks or plan_id is None and r['task_id'] is None]
    days = { (start+timedelta(days=i)).isoformat(): dict(date=(start+timedelta(days=i)).isoformat(), actual_seconds=0, focus_seconds=0, manual_seconds=0)
        for i in range((end-start).days+1)}
    for r in events:
        if r['occurred_on'] in days:
            days[r['occurred_on']]['actual_seconds'] += r['seconds']
            days[r['occurred_on']][r['source']+'_seconds'] += r['seconds']
    known = dict.fromkeys(tasks,0)
    for r in events:
        if r['task_id'] in known:known[r['task_id']]+=r['seconds']
    unknown = sum(max(0,r['focus_seconds']+r['manual_seconds']-known[tid]) for tid,r in tasks.items())
    if plan_id is None:
        unattached = sum(r['seconds'] for r in store.read('SELECT seconds FROM focus_sessions WHERE task_id IS NULL'))
        unknown += max(0, unattached-sum(r['seconds'] for r in events if r['task_id'] is None))
    active = {d for d,v in days.items() if v['actual_seconds']>0}
    longest = run = 0
    for d in days:
        run = run+1 if d in active else 0
        longest = max(longest,run)
    total = sum(d['actual_seconds'] for d in days.values())
    return dict(days=list(days.values()), actual_seconds=total, unknown_seconds=unknown,
        active_days=len(active), longest_streak=longest,
        summary=f'{start} 至 {end}：按实际学习日期记录 {duration_text(total)}，学习 {len(active)} 天，范围内最长连续 {longest} 天。\n另有 {duration_text(unknown)}未确定发生日期，不计入日历或趋势，不从安排日期推算。')


def delete_manual_event(store, entry_id, token):
    with store.write(check_budget=False) as c:
        if fingerprint(c)!=token:raise ValueError('学习记录已有变化，请重新打开后核对')
        r=c.execute("SELECT * FROM study_events WHERE id=? AND source='manual'",(entry_id,)).fetchone()
        if not r:raise ValueError('只能撤销仍存在的按日期人工补记，专注记录不可在此修改')
        t=c.execute('SELECT plan_id,manual_seconds FROM tasks WHERE id=?',(r['task_id'],)).fetchone()
        if not t or t[1]<r['seconds']:raise ValueError('累计用时已有变化，请先核对记录')
        c.execute('UPDATE tasks SET manual_seconds=manual_seconds-? WHERE id=?',(r['seconds'],r['task_id']))
        c.execute('DELETE FROM study_events WHERE id=?',(entry_id,));store._touch(c,t[0])


def captures(store):
    return store.read('SELECT c.*,p.title AS plan_title FROM captures c LEFT JOIN plans p ON p.id=c.plan_id ORDER BY c.pinned DESC,c.priority DESC,c.created_at DESC,c.id')


def _insert_task(store, c, payload, due, plan_id, tid, *, priority=0, pinned=False):
    day = date.fromisoformat(due)
    if plan_id:
        p=c.execute('SELECT archived,start_date FROM plans WHERE id=?',(plan_id,)).fetchone()
        if not p or p[0]: raise ValueError('请选择未归档计划，或改为独立任务')
        if due<p[1]: raise ValueError('日期不能早于所属计划开始日')
        s=c.execute('SELECT pause_start,resume_date FROM plan_schedules WHERE plan_id=?',(plan_id,)).fetchone()
        if s and s[0] and s[0]<=due<s[1]: raise ValueError('日期位于计划暂停期间，请换一天或先恢复计划')
    before=store._snapshot_in_transaction(c,plan_id) if plan_id else None
    c.execute('''INSERT INTO tasks (id,plan_id,week,day,payload_json,due_date,manual,ai_locked,user_modified)
        VALUES (?,?,0,NULL,?,?,1,1,1)''',(tid,plan_id,payload.model_dump_json(),day.isoformat()))
    attention_in(c,tid,priority=priority,pinned=pinned)
    if plan_id:
        c.execute('UPDATE plan_schedules SET end_date=MAX(end_date,?) WHERE plan_id=?',(due,plan_id))
        store._touch(c,plan_id)
        store._version(c,plan_id,'从快捷录入安排任务',before)


def save_capture(store, payload, *, plan_id=None, priority=0, pinned=False, due=None):
    payload=DailyTask.model_validate(payload)
    if payload.day!=1: raise ValueError('手动任务不使用学习日编号')
    if type(priority) is not int or priority not in range(4): raise ValueError('优先级无效')
    tid=new_id()
    with store.write(confirm_plan_limits=bool(due)) as c:
        if plan_id and not c.execute('SELECT 1 FROM plans WHERE id=? AND archived=0',(plan_id,)).fetchone():
            raise ValueError('所属计划不可用')
        if due:
            _insert_task(store,c,payload,due,plan_id,tid,priority=priority,pinned=pinned)
        else:
            stamp=datetime.now().astimezone().isoformat()
            c.execute('INSERT INTO captures VALUES (?,?,?,?,?,?,?)',(tid,payload.model_dump_json(),plan_id,priority,int(bool(pinned)),stamp,stamp))
    return tid


def schedule_capture(store, tid, due, *, plan_id=None, token):
    with store.write(confirm_plan_limits=True) as c:
        if fingerprint(c)!=token: raise ValueError('任务或预算已有变化，请刷新收集箱后重新安排')
        row=c.execute('SELECT * FROM captures WHERE id=?',(tid,)).fetchone()
        if not row: raise ValueError('该事项已安排或移除，请刷新')
        payload=DailyTask.model_validate_json(row['payload_json'])
        _insert_task(store,c,payload,due,plan_id,tid,priority=row['priority'],pinned=row['pinned'])
        c.execute('DELETE FROM captures WHERE id=?',(tid,))
    return tid


def delete_capture(store, tid, token):
    with store.write(check_budget=False) as c:
        if fingerprint(c)!=token: raise ValueError('收集箱已有变化，请刷新后重试')
        c.execute('DELETE FROM captures WHERE id=?',(tid,))
