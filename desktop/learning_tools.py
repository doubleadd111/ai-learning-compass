"""Offline statistics, task checklists and bounded recurring-task proposals."""
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import json
import sqlite3

from models import DailyTask
from desktop.models import new_id
from desktop.scheduling import capacity, fingerprint, plan_loads


def details_in(c, task_id):
    row = c.execute('SELECT stage,steps_json FROM task_details WHERE task_id=?', (task_id,)).fetchone()
    return dict(stage=row[0], steps=json.loads(row[1])) if row else dict(stage='', steps=[])


def validate_details(data):
    if not isinstance(data, dict) or set(data) != {'stage', 'steps'} or not isinstance(data['stage'], str) or len(data['stage']) > 40:
        raise ValueError('分组名称最多40字')
    steps = data['steps']
    if not isinstance(steps, list) or len(steps) > 40:
        raise ValueError('每个任务最多40个执行步骤')
    ids = set()
    for row in steps:
        if (not isinstance(row, dict) or set(row) != {'id', 'title', 'done'} or not isinstance(row['id'], str)
            or not 1 <= len(row['id']) <= 80 or row['id'] in ids or type(row['done']) is not bool
            or not isinstance(row['title'], str) or not 1 <= len(row['title'].strip()) <= 120):
            raise ValueError('执行步骤需要唯一标识和1–120字说明')
        ids.add(row['id'])
    return data


def details_signature(data):
    return data['stage'], [(s['id'], s['title']) for s in data['steps']]


def save_details(store, task_id, data, token):
    data = validate_details(data)
    with store.write() as c:
        if fingerprint(c) != token:
            raise ValueError('任务或学习记录已有变化，请关闭并重新打开分组和步骤')
        task = c.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
        if not task:
            raise ValueError('任务已不存在')
        old = details_in(c, task_id)
        if old == data:
            return
        content_changed = details_signature(old) != details_signature(data)
        if content_changed and not task['manual'] and c.execute('SELECT 1 FROM reviews WHERE plan_id=? AND week=?', (task['plan_id'], task['week'])).fetchone():
            raise ValueError('该学习周已复盘，只能打卡现有子任务，不能修改步骤或阶段')
        before = store._snapshot_in_transaction(c, task['plan_id']) if task['plan_id'] else None
        c.execute('INSERT INTO task_details VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET stage=excluded.stage,steps_json=excluded.steps_json',
                  (task_id, data['stage'].strip(), json.dumps(data['steps'], ensure_ascii=False)))
        if [(s['id'], s['title']) for s in old['steps']] != [(s['id'], s['title']) for s in data['steps']]:
            c.execute('UPDATE tasks SET ai_locked=1,user_modified=1 WHERE id=?', (task_id,))
        if any(s['done'] for s in data['steps']):
            c.execute("UPDATE tasks SET status='进行中' WHERE id=? AND status='未开始'", (task_id,))
        if task['plan_id']:
            store._touch(c, task['plan_id'])
            if content_changed:
                store._version(c, task['plan_id'], '编辑阶段与子任务', before)


def task_details(store, task_id):
    with closing(store.connect()) as c:
        c.execute('BEGIN')
        if not c.execute('SELECT 1 FROM tasks WHERE id=?', (task_id,)).fetchone():
            raise ValueError('任务已不存在')
        return details_in(c, task_id), fingerprint(c)


def assign_stage(store, task_ids, stage, token):
    if not task_ids or len(set(task_ids)) != len(task_ids) or not isinstance(stage, str) or len(stage) > 40:
        raise ValueError('请选任务并填写40字以内的分组名称；留空表示未分组')
    with store.write() as c:
        if fingerprint(c) != token:
            raise ValueError('任务已有变化，请刷新后重新选择')
        plans, originals = set(), {}
        for tid in task_ids:
            row = c.execute('SELECT plan_id,week,manual FROM tasks WHERE id=?', (tid,)).fetchone()
            if not row:
                raise ValueError('任务已不存在')
            if not row['manual'] and c.execute('SELECT 1 FROM reviews WHERE plan_id=? AND week=?', (row['plan_id'], row['week'])).fetchone():
                raise ValueError('已复盘任务不能修改阶段，请取消勾选这些任务')
            if row['plan_id'] and row['plan_id'] not in originals:
                originals[row['plan_id']] = store._snapshot_in_transaction(c, row['plan_id'])
            data = details_in(c, tid)
            if data['stage'] == stage.strip():
                continue
            c.execute('INSERT INTO task_details VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET stage=excluded.stage',
                      (tid, stage.strip(), json.dumps(data['steps'], ensure_ascii=False)))
            if row['plan_id']:
                plans.add(row['plan_id'])
        for pid in plans:
            store._touch(c, pid)
            store._version(c, pid, '批量分配任务阶段', originals[pid])


def statistics(store, start, end, plan_id=None, include_archived=False):
    if start > end or (end - start).days > 366:
        raise ValueError('统计范围请选择一年以内，结束日期不能早于开始日期')
    rows = [r for r in store.tasks(plan_id) if start.isoformat() <= r['due_date'] <= end.isoformat()
            and (include_archived or not r['archived'])]
    days = []
    for n in range((end - start).days + 1):
        day = (start + timedelta(days=n)).isoformat()
        group = [r for r in rows if r['due_date'] == day]
        days.append(dict(date=day, total=len(group), done=sum(r['status'] == '已完成' for r in group),
            expected=sum(r['task'].duration_minutes for r in group), actual_seconds=sum(r['manual_seconds'] + r['focus_seconds'] for r in group)))
    total, done = len(rows), sum(r['status'] == '已完成' for r in rows)
    expected = sum(d['expected'] for d in days)
    seconds = sum(d['actual_seconds'] for d in days)
    overdue = sum(r['status'] != '已完成' and r['due_date'] < date.today().isoformat() for r in rows)
    compare = [r for r in rows if r['status'] == '已完成']
    finished_expected = sum(r['task'].duration_minutes for r in compare)
    finished_seconds = sum(r['manual_seconds'] + r['focus_seconds'] for r in compare)
    summary = (f'{start:%Y-%m-%d} 至 {end:%Y-%m-%d}排期的任务：完成{done}/{total}项，当前完成率{round(done * 100 / total) if total else 0}%；'
               f'未完成且已逾期{overdue}项。累计已记录{seconds // 60}分钟（按任务归属，不代表这段时间实际发生的学习量）。\n'
               f'已完成任务预计{finished_expected}分钟，实际{finished_seconds // 60}分钟；未记录用时的完成任务不能据此断定耗时为零。\n'
               + ('建议先检查逾期任务，按可用时间补排；不要只为了完成率提高每日上限。' if overdue else '可检查任务产出，再决定下一周的安排；无需为了填满时间增加任务。'))
    return dict(rows=rows, days=days, total=total, done=done, expected=expected, actual_seconds=seconds,
                overdue=overdue, finished_expected=finished_expected, finished_seconds=finished_seconds, summary=summary)


def recurrence_dates(rule):
    if not isinstance(rule, dict) or set(rule) != {'kind', 'start', 'end', 'weekdays', 'intervals'}:
        raise ValueError('周期规则不完整')
    start, end = date.fromisoformat(rule['start']), date.fromisoformat(rule['end'])
    if start > end or (end - start).days > 365:
        raise ValueError('结束日期需在开始日期之后一年以内')
    days, offsets = rule['weekdays'], rule['intervals']
    if not isinstance(days, list) or any(type(x) is not int or x not in range(7) for x in days) or len(set(days)) != len(days):
        raise ValueError('请选择有效星期')
    if not isinstance(offsets, list) or len(offsets) > 40 or any(type(x) is not int or not 0 <= x <= 365 for x in offsets) or len(set(offsets)) != len(offsets):
        raise ValueError('复习间隔需为0–365的不重复天数，最多40次')
    if rule['kind'] == 'daily':
        result = [start + timedelta(days=n) for n in range((end - start).days + 1)]
    elif rule['kind'] == 'weekly':
        result = [start + timedelta(days=n) for n in range((end - start).days + 1) if (start + timedelta(days=n)).weekday() in days]
    elif rule['kind'] == 'review':
        result = [start + timedelta(days=n) for n in sorted(offsets) if n <= (end-start).days]
    else:
        raise ValueError('请选择每日、每周或间隔复习')
    if not result:
        raise ValueError('该范围没有发生日期，请检查星期或复习间隔')
    return [d.isoformat() for d in result]


def validate_snapshot_extras(snapshot):
    tids = {r['id'] for r in snapshot['tasks']}
    from desktop.organizer import validate_rows
    validate_rows(snapshot, tids)
    tasks = {r['id']: r for r in snapshot['tasks']}
    pid = snapshot['plans'][0]['id']
    seen = set()
    for r in snapshot['task_details']:
        if r['task_id'] not in tids or r['task_id'] in seen:
            raise ValueError('子任务所属任务不一致')
        seen.add(r['task_id'])
        validate_details(dict(stage=r['stage'], steps=json.loads(r['steps_json'])))
    sids = set()
    for row in snapshot['task_series']:
        if row['id'] in sids or row['plan_id'] != pid or row['stopped'] not in (0, 1):
            raise ValueError('周期任务所属计划不一致')
        sids.add(row['id'])
        validate_series_row(row)
    for row in snapshot['series_tasks']:
        if row['series_id'] not in sids or (row['task_id'] is not None and row['task_id'] not in tids):
            raise ValueError('周期实例关联不完整')
        if row['task_id'] is not None and not tasks[row['task_id']]['manual']:
            raise ValueError('周期实例只能关联手动任务')
        date.fromisoformat(row['scheduled_date'])


def validate_series_row(row):
    payload = DailyTask.model_validate_json(row['payload_json'])
    if payload.day != 1:
        raise ValueError('周期任务不使用学习日编号')
    recurrence_dates(json.loads(row['rule_json']))
    datetime.fromisoformat(row['created_at'])
    if row['remind_at'] is not None:
        datetime.strptime(row['remind_at'], '%H:%M')


def validate_database_extras(c):
    for row in c.execute('SELECT * FROM task_details'):
        validate_details(dict(stage=row['stage'], steps=json.loads(row['steps_json'])))
    for row in c.execute('SELECT * FROM task_series'):
        validate_series_row(row)
        if row['stopped'] not in (0, 1):
            raise ValueError('周期任务状态不正确')
    for row in c.execute('SELECT * FROM series_tasks'):
        date.fromisoformat(row['scheduled_date'])
        if row['task_id'] is not None:
            task = c.execute('SELECT manual,plan_id FROM tasks WHERE id=?', (row['task_id'],)).fetchone()
            series = c.execute('SELECT plan_id FROM task_series WHERE id=?', (row['series_id'],)).fetchone()
            if not task['manual'] or task['plan_id'] != series[0]:
                raise ValueError('周期实例所属任务不一致')


@dataclass(frozen=True)
class SeriesPreview:
    token: str
    series_id: str
    plan_id: str | None
    payload_json: str
    rule_json: str
    remind_at: str | None
    dates: tuple
    task_ids: tuple
    loads: tuple


def _insert_series(c, p):
    from desktop.store import now
    if p.plan_id:
        plan = c.execute('SELECT archived,start_date FROM plans WHERE id=?', (p.plan_id,)).fetchone()
        if not plan or plan['archived'] or min(p.dates) < plan['start_date']:
            raise ValueError('请选择未归档计划，日期不能早于计划开始日')
    c.execute('INSERT INTO task_series VALUES (?,?,?,?,?,0,?)', (p.series_id, p.plan_id, p.payload_json, p.rule_json, p.remind_at, now()))
    for tid, due in zip(p.task_ids, p.dates):
        c.execute('''INSERT INTO tasks (id,plan_id,week,day,manual,payload_json,due_date,remind_at,ai_locked,user_modified)
                     VALUES (?,?,0,NULL,1,?,?,?,1,1)''', (tid, p.plan_id, p.payload_json, due, p.remind_at))
        c.execute('INSERT INTO series_tasks VALUES (?,?,?)', (p.series_id, due, tid))
    if p.plan_id:
        c.execute('UPDATE plan_schedules SET end_date=MAX(end_date,?) WHERE plan_id=?', (max(p.dates), p.plan_id))


def preview_series(store, payload, rule, *, plan_id=None, remind_at=None):
    payload = DailyTask.model_validate(payload)
    if payload.day != 1:
        raise ValueError('周期任务不使用学习日编号')
    if remind_at is not None:
        datetime.strptime(remind_at, '%H:%M')
    dates = tuple(recurrence_dates(rule))
    sid, ids = new_id(), tuple(new_id() for _ in dates)
    with closing(store.connect()) as c, closing(sqlite3.connect(':memory:')) as scratch:
        c.execute('BEGIN')
        token = fingerprint(c)
        scratch.row_factory = sqlite3.Row
        scratch.deserialize(c.serialize())
        scratch.execute('PRAGMA foreign_keys=ON')
        p = SeriesPreview(token, sid, plan_id, payload.model_dump_json(), json.dumps(rule, sort_keys=True), remind_at, dates, ids, ())
        _insert_series(scratch, p)
        loads = [r for d, r in capacity(scratch).items() if d in dates]
        return SeriesPreview(token, sid, plan_id, p.payload_json, p.rule_json, remind_at, dates, ids, tuple(loads))


def apply_series(store, p):
    # Revalidate even a caller-constructed proposal. Never trust UI-only checks.
    payload = DailyTask.model_validate_json(p.payload_json)
    if payload.day != 1 or tuple(recurrence_dates(json.loads(p.rule_json))) != p.dates or len(p.task_ids) != len(p.dates) or len(set(p.task_ids)) != len(p.task_ids):
        raise ValueError('周期预览不完整，请重新预览')
    if p.remind_at is not None:
        datetime.strptime(p.remind_at, '%H:%M')
    with store.write(confirm_plan_limits=True) as c:
        if fingerprint(c) != p.token:
            raise ValueError('任务、记录或预算已有变化，请重新预览')
        before = store._snapshot_in_transaction(c, p.plan_id) if p.plan_id else None
        _insert_series(c, p)
        if p.plan_id:
            store._touch(c, p.plan_id)
            store._version(c, p.plan_id, '创建周期任务或复习', before)
    return p.series_id


def series_entries(store):
    return store.read("""SELECT s.*,COALESCE(p.title,'独立任务') AS plan_title FROM task_series s
                         LEFT JOIN plans p ON s.plan_id=p.id ORDER BY s.created_at DESC""")


def stop_candidates(c, series_id, day):
    from desktop.plan_history import has_records
    state = c.execute("SELECT value_json FROM settings WHERE key='focus_state'").fetchone()
    focus = json.loads(state[0]) if state else {}
    rows = c.execute('SELECT t.* FROM tasks t JOIN series_tasks s ON s.task_id=t.id WHERE s.series_id=? AND t.due_date>=? ORDER BY t.due_date,t.id', (series_id, day)).fetchall()
    removable, kept = [], []
    for row in rows:
        active = row['id'] == focus.get('task_id') and bool(focus.get('session_id') or focus.get('phase') == 'break')
        (kept if has_records(row) or active else removable).append(dict(row))
    return removable, kept


def stop_series(store, series_id, token, day=None):
    day = day or date.today().isoformat()
    date.fromisoformat(day)
    if day != date.today().isoformat():
        raise ValueError('日期已变化，请重新查看停止预览')
    with store.write() as c:
        if fingerprint(c) != token:
            raise ValueError('任务或记录已有变化，请重新查看停止预览')
        s = c.execute('SELECT * FROM task_series WHERE id=?', (series_id,)).fetchone()
        if not s or s['stopped']:
            raise ValueError('这个周期已停止或不存在')
        remove, kept = stop_candidates(c, series_id, day)
        before = store._snapshot_in_transaction(c, s['plan_id']) if s['plan_id'] else None
        for row in remove:
            c.execute('DELETE FROM tasks WHERE id=?', (row['id'],))
        c.execute('UPDATE task_series SET stopped=1 WHERE id=?', (series_id,))
        if s['plan_id']:
            store._touch(c, s['plan_id'])
            store._version(c, s['plan_id'], '停止周期任务后续安排', before)
        return len(remove), len(kept)
