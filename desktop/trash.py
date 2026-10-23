"""Transactional, bounded recovery of explicitly deleted plans and manual tasks.

Only the fixed entity tables are captured; never settings or credentials. Expiry
disables restoration, but physical removal is an explicit user action.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import sqlite3

from desktop.recovery import TABLES, missing_plan_rows
from desktop.models import new_id
from desktop.scheduling import fingerprint, capacity, plan_loads
from models import DailyTask, validate_plan_for_profile
from progress_store import TaskProgress

RETENTION_DAYS = 30
TASK_TABLES = ('tasks', 'focus_sessions', 'notifications', 'task_details', 'series_tasks', 'task_attention', 'study_events', 'task_dependencies', 'reminder_controls', 'task_resources')


def clock():
    return datetime.now().astimezone()


def _save(c, kind, entity_id, title, rows):
    stamp = clock()
    entry_id = new_id()
    c.execute('INSERT INTO trash VALUES (?,?,?,?,?,?,?)', (entry_id, kind, entity_id, title,
        stamp.isoformat(), (stamp + timedelta(days=RETENTION_DAYS)).isoformat(),
        json.dumps(rows, ensure_ascii=False)))
    validate_entry(c, dict(c.execute('SELECT * FROM trash WHERE id=?', (entry_id,)).fetchone()))
    return entry_id


def capture_plan(c, plan_id):
    rows = missing_plan_rows(c, plan_id)
    return _save(c, 'plan', plan_id, rows['plans'][0]['title'], rows)


def capture_task(c, task_id):
    rows = {}
    for table in TASK_TABLES:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            continue
        rows[table] = [dict(r) for r in c.execute(
            f'SELECT * FROM {table} WHERE {"id" if table == "tasks" else "task_id"}=?', (task_id,))]
    return _save(c, 'task', task_id, json.loads(rows['tasks'][0]['payload_json'])['title'], rows)


def validate_entry(c, entry):
    """Reject unrecognized tables/columns and cross-entity rows before any SQL."""
    try:
        start = datetime.fromisoformat(entry['deleted_at'])
        end = datetime.fromisoformat(entry['expires_at'])
        if not start.tzinfo or not end.tzinfo or end-start != timedelta(days=RETENTION_DAYS):
            raise ValueError('回收站保留时间无效')
        rows = json.loads(entry['snapshot_json'])
        tables = TABLES if entry['kind'] == 'plan' else TASK_TABLES if entry['kind'] == 'task' else ()
        tables = tuple(t for t in tables if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone())
        from desktop.organizer import TASK_TABLES as ORGANIZER_TABLES
        for table in (*ORGANIZER_TABLES, 'task_dependencies','reminder_controls','task_resources'):
            if table in tables:
                rows.setdefault(table, [])  # Old schema7 snapshots stay unchanged on disk.
        if not tables or set(rows) != set(tables):
            raise ValueError('回收站资料格式不正确')
        for table in tables:
            columns = {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
            if not isinstance(rows[table], list) or any(not isinstance(r, dict) or set(r) != columns for r in rows[table]):
                raise ValueError('回收站资料字段不完整')
        tasks = rows['tasks']
        ids = {r['id'] for r in tasks}
        if len(ids) != len(tasks):
            raise ValueError('回收站任务标识重复')
        pid = entry['entity_id'] if entry['kind'] == 'plan' else None
        if pid:
            if len(rows['plans']) != 1 or rows['plans'][0]['id'] != pid:
                raise ValueError('回收站计划不一致')
            series = {r['id'] for r in rows['task_series']}
            for table in tables:
                for r in rows[table]:
                    if table in ('plans', 'focus_sessions', 'notifications', 'task_details', 'task_attention', 'study_events', 'task_dependencies', 'reminder_controls', 'task_resources'):
                        continue
                    if table == 'series_tasks':
                        if r['series_id'] not in series or (r['task_id'] is not None and r['task_id'] not in ids):
                            raise ValueError('回收站周期关联不一致')
                    elif r['plan_id'] != pid:
                        raise ValueError('回收站包含其他计划资料')
        elif len(tasks) != 1 or tasks[0]['id'] != entry['entity_id'] or tasks[0]['manual'] != 1:
            raise ValueError('回收站只支持单个手动任务')
        for table in ('focus_sessions', 'notifications', 'task_details', 'task_attention', 'study_events', 'task_dependencies', 'reminder_controls', 'task_resources'):
            if any(r['task_id'] not in ids for r in rows.get(table, [])):
                raise ValueError('回收站包含其他任务记录')
        if not pid and any(r['task_id'] not in ids for r in rows['series_tasks']):
            raise ValueError('回收站周期任务不一致')
        for r in tasks:
            task = DailyTask.model_validate_json(r['payload_json'])
            from datetime import date
            date.fromisoformat(r['due_date'])
            if r['remind_at']:
                datetime.strptime(r['remind_at'], '%H:%M')
            TaskProgress(status=r['status'], actual_minutes=0, difficulty=r['difficulty'], note=r['note'])
            if r['focus_seconds'] < 0 or r['manual_seconds'] < 0 or r['ai_locked'] not in (0, 1):
                raise ValueError('回收站学习记录无效')
            if r['manual'] and (r['week'] != 0 or r['day'] is not None or task.day != 1):
                raise ValueError('回收站手动任务编号无效')
        from desktop.learning_tools import validate_details
        from desktop.task_resources import validate_rows as validate_resources
        validate_resources(rows.get('task_resources',[]),ids)
        for r in rows['task_details']:
            validate_details(dict(stage=r['stage'], steps=json.loads(r['steps_json'])))
        for r in rows['focus_sessions']:
            if r['seconds'] < 0:
                raise ValueError('回收站专注记录无效')
        from desktop.organizer import validate_rows
        validate_rows(rows, ids)
        # Validate complete plan aggregates in an empty in-memory database. Never
        # execute SQL supplied by the snapshot or copy unrelated live rows.
        if pid:
            with closing(sqlite3.connect(':memory:')) as scratch:
                scratch.row_factory = sqlite3.Row
                scratch.execute('PRAGMA foreign_keys=ON')
                for table in tables:
                    scratch.execute(c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0])
                _insert(scratch, rows)
                from desktop.store import Store
                saved = Store.get_plan_in_transaction(scratch, pid)
                validate_plan_for_profile(saved['plan'], saved['profile'])
                Store._validate_schedule(scratch, pid)
                from desktop.learning_tools import validate_database_extras
                validate_database_extras(scratch)
                if 'task_dependencies' in tables:
                    from desktop.workflow import validate
                    validate(scratch)
                from desktop.planning import validate_brief
                for r in rows['plan_briefs']:
                    validate_brief(json.loads(r['data_json']))
                from desktop.learning_tools import validate_snapshot_extras
                for r in rows['plan_versions']:
                    for encoded in (r['before_json'], r['after_json']):
                        snapshot = json.loads(encoded)
                        if not snapshot:
                            continue
                        Store._normalize_snapshot(snapshot)
                        if snapshot.get('format') != 'learning-compass-plan' or len(snapshot['plans']) != 1 or snapshot['plans'][0]['id'] != pid:
                            raise ValueError('回收站历史所属计划不一致')
                        validate_snapshot_extras(snapshot)
                if scratch.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('回收站关联记录不完整')
        return rows
    except (KeyError, TypeError, sqlite3.Error) as e:
        raise ValueError('回收站资料不完整，不能恢复') from e


def _insert(c, rows):
    # JSON dictionary order is not a dependency-order guarantee.
    for table in TABLES:
        for r in rows.get(table, []):
            names = list(r)
            c.execute(f'INSERT INTO {table} ({",".join(names)}) VALUES ({",".join("?" for _ in names)})', list(r.values()))


def entries(store):
    return store.read('SELECT id,kind,entity_id,title,deleted_at,expires_at FROM trash ORDER BY deleted_at DESC,id')


def detail(store, entry_id):
    """Read the deleted snapshot, never the live task sharing its old ID.

    Expired entries remain readable; viewing cannot revive them or extend expiry.
    """
    with closing(store.connect()) as c:
        c.execute('BEGIN')
        entry = c.execute('SELECT * FROM trash WHERE id=?', (entry_id,)).fetchone()
        if not entry:
            raise ValueError('此项目已恢复或已彻底删除，请刷新回收站')
        entry = dict(entry)
        rows = validate_entry(c, entry)
        pid = entry['entity_id'] if entry['kind']=='plan' else rows['tasks'][0]['plan_id']
        parent = c.execute('SELECT title FROM plans WHERE id=?', (pid,)).fetchone() if pid else None
        owner = rows['plans'][0]['title'] if entry['kind']=='plan' else parent[0] if parent else '原所属计划（当前不在计划库）' if pid else '独立任务'
        return dict(entry=entry, rows=rows, owner=owner)


def _token(c):
    # Focus checkpoints, reminder history and history writes also invalidate a
    # preview, even if no plan revision changes. No connection or Key is read.
    state = [fingerprint(c)]
    for table in ('focus_sessions', 'notifications', 'reviews', 'undo', 'plan_briefs', 'plan_versions', 'trash'):
        state.append([tuple(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY 1,2')])
    return hashlib.sha256(json.dumps(state, ensure_ascii=False).encode()).hexdigest()


def _restore(c, entry, target_date=None):
    if clock() >= datetime.fromisoformat(entry['expires_at']):
        raise ValueError('已超过30天可恢复期限；请使用删除前的整库备份，不能在回收站恢复')
    rows = validate_entry(c, entry)
    if target_date is not None:
        if entry['kind'] != 'task':
            raise ValueError('指定恢复日期仅适用于单项任务；整份计划请按原排期恢复，再使用调整排期')
        if not isinstance(target_date, str) or date.fromisoformat(target_date).isoformat() != target_date:
            raise ValueError('请选择有效的恢复安排日期')
        rows['tasks'][0]['due_date'] = target_date
    if entry['kind'] == 'plan' and c.execute('SELECT 1 FROM plans WHERE id=?', (entry['entity_id'],)).fetchone():
        raise ValueError('这份计划已经存在，不能覆盖恢复')
    for t in rows['tasks']:
        if c.execute('SELECT 1 FROM tasks WHERE id=?', (t['id'],)).fetchone():
            raise ValueError('同一任务已存在，可能已从历史恢复；不能覆盖其新记录')
    before = None
    pid = rows['tasks'][0]['plan_id'] if entry['kind'] == 'task' else entry['entity_id']
    from desktop.store import Store, now
    if entry['kind'] == 'task' and pid:
        parent = c.execute('SELECT start_date FROM plans WHERE id=?', (pid,)).fetchone()
        if not parent:
            raise ValueError('所属计划已删除，请先从回收站恢复该计划，再恢复此任务')
        if rows['tasks'][0]['due_date'] < parent[0]:
            raise ValueError('该任务原日期早于所属计划现在的开始日，请先核对计划设置；没有自动改期')
        before = Store._snapshot_in_transaction(c, pid)
        if target_date is not None:
            schedule = c.execute('SELECT pause_start,resume_date FROM plan_schedules WHERE plan_id=?', (pid,)).fetchone()
            if schedule and schedule['pause_start'] and schedule['pause_start'] <= target_date < schedule['resume_date']:
                raise ValueError(f"所选日期在计划暂停期间，请选择 {schedule['resume_date']} 当天或之后，或按原日期恢复")
    links = rows['series_tasks'] if entry['kind'] == 'task' else []
    insert_rows = {k: v for k, v in rows.items() if k != 'series_tasks' or entry['kind'] == 'plan'}
    try:
        _insert(c, insert_rows)
        for link in links:
            existing = c.execute('SELECT task_id FROM series_tasks WHERE series_id=? AND scheduled_date=?',
                                 (link['series_id'], link['scheduled_date'])).fetchone()
            if existing:
                if existing[0] is not None:
                    raise ValueError('原周期位置已有新任务，不能覆盖；请先核对周期任务')
                c.execute('UPDATE series_tasks SET task_id=? WHERE series_id=? AND scheduled_date=?',
                          (link['task_id'], link['series_id'], link['scheduled_date']))
            # A removed rule is never resurrected by restoring one occurrence.
        from desktop.workflow import validate
        validate(c)
        if pid:
            if entry['kind'] == 'task':
                c.execute('UPDATE plan_schedules SET end_date=MAX(end_date,?) WHERE plan_id=?', (rows['tasks'][0]['due_date'], pid))
                c.execute('UPDATE plans SET revision=revision+1,updated_at=? WHERE id=?', (now(), pid))
                Store._version(c, pid, '从回收站恢复任务', before)
            else:
                restored = Store._snapshot_in_transaction(c, pid)
                c.execute('UPDATE plans SET revision=revision+1,updated_at=? WHERE id=?', (now(), pid))
                Store._version(c, pid, '从回收站恢复计划', restored)
            Store._validate_schedule(c, pid)
        if c.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('关联记录不完整，已取消恢复')
    except sqlite3.IntegrityError as e:
        raise ValueError('原标识或关联位置已被占用，未覆盖任何当前记录') from e
    c.execute('DELETE FROM trash WHERE id=?', (entry['id'],))
    return rows


@dataclass(frozen=True)
class RestorePreview:
    entry_id: str
    token: str
    title: str
    summary: str
    conflicts: tuple = ()
    budget_rows: tuple = ()
    plan_rows: tuple = ()
    target_date: str | None = None


def preview(store, entry_id, *, target_date=None):
    with closing(store.connect()) as c:
        c.execute('BEGIN')
        entry = c.execute('SELECT * FROM trash WHERE id=?', (entry_id,)).fetchone()
        if not entry:
            raise ValueError('此项目已恢复或已彻底删除，请刷新回收站')
        entry = dict(entry)
        token = _token(c)
        before_budget, before_plans = capacity(c), plan_loads(c)
        c.execute('SAVEPOINT restore_preview')
        try:
            rows = _restore(c, entry, target_date)
            original_dates = sorted(t['due_date'] for t in json.loads(entry['snapshot_json'])['tasks'])
            tasks = rows['tasks']
            dates = sorted(t['due_date'] for t in tasks)
            summary = (f"{'计划' if entry['kind']=='plan' else '手动任务'}：{entry['title']}\n"
                f"{len(tasks)} 项任务 · 原日期 {original_dates[0] if original_dates else '无'} — {original_dates[-1] if original_dates else '无'}")
            if target_date is not None:
                summary += f'\n原日期 → 新日期：{original_dates[0]} → {dates[0]}'
            else:
                summary += '\n按原日期恢复，不自动改期。'
            summary += (f"\n已完成 {sum(t['status']=='已完成' for t in tasks)} 项 · 专注 {sum(t['focus_seconds'] for t in tasks)//60} 分钟\n"
                '原ID、状态、笔记、用时、步骤和提醒发送记录一并恢复；不恢复模型配置。')
            if target_date is not None:
                summary += '\n只更改安排日期，已有学习记录保留；按安排日期汇总的统计会归入新日期。'
            if entry['kind'] == 'task' and rows['series_tasks']:
                summary += '\n只恢复这一次任务；已停止或移除的周期规则不会重新开启。'
            if entry['kind'] == 'plan':
                title = entry['title']
                if c.execute('SELECT COUNT(*) FROM plans WHERE title=?', (title,)).fetchone()[0] > 1:
                    summary += '\n已有同名但不同ID的计划；恢复后两份均保留，可另行重命名。'
            budgets = tuple(r for key, r in capacity(c).items() if r['over'] > before_budget.get(key, {}).get('over', 0))
            plans = tuple(r for key, r in plan_loads(c).items() if r['over'] > before_plans.get(key, {}).get('over', 0))
            return RestorePreview(entry_id, token, entry['title'], summary, budget_rows=budgets, plan_rows=plans, target_date=target_date)
        except ValueError as e:
            return RestorePreview(entry_id, token, entry['title'], '', conflicts=(str(e),))
        finally:
            c.execute('ROLLBACK TO restore_preview')
            c.rollback()


def apply(store, proposal):
    if proposal.conflicts:
        raise ValueError('此预览不能恢复：' + '；'.join(proposal.conflicts))
    with store.write(confirm_plan_limits=True) as c:
        if _token(c) != proposal.token:
            raise ValueError('数据或预算已有变化，请重新查看恢复预览；没有覆盖新变化')
        entry = c.execute('SELECT * FROM trash WHERE id=?', (proposal.entry_id,)).fetchone()
        if not entry:
            raise ValueError('此项目已不在回收站，请刷新')
        _restore(c, dict(entry), proposal.target_date)
    return True


def purge(store, entry_id, token, phrase):
    if phrase != '彻底删除':
        raise ValueError('请输入“彻底删除”四个字')
    with store.write(check_budget=False) as c:
        if _token(c) != token:
            raise ValueError('数据已有变化，请重新查看后再清除')
        if c.execute('DELETE FROM trash WHERE id=?', (entry_id,)).rowcount != 1:
            raise ValueError('此项目已不在回收站，请刷新')
