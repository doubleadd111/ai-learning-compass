"""Append-only arrangement history. Learning evidence is never replayed."""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, date
import json
import sqlite3

from models import DailyTask, validate_plan_for_profile
from desktop.scheduling import fingerprint, capacity, plan_loads

PLAN_FIELDS = ("title", "overview", "profile_json", "tips_json", "start_date", "weekdays_json", "archived")
TASK_FIELDS = ("payload_json", "due_date", "remind_at", "user_modified", "ai_locked")


def utc_now():
    return datetime.now(timezone.utc)


def stamp(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def recent_versions(store, plan_id):
    cutoff, end = utc_now() - timedelta(days=7), utc_now()
    return [r for r in store.versions(plan_id) if cutoff <= stamp(r["created_at"]) <= end]


def has_records(row):
    return row["status"] != "未开始" or row["manual_seconds"] or row["focus_seconds"] or row["note"] or row["difficulty"] is not None


def arrangement(snapshot):
    return dict(plan={k: snapshot["plans"][0][k] for k in PLAN_FIELDS},
        tasks=sorted([{k: row[k] for k in ("id", "week", "day", "manual", *TASK_FIELDS)} for row in snapshot["tasks"]], key=lambda r: r["id"]),
        weeks=sorted(snapshot["weeks"], key=lambda r: r["number"]),
        schedule=snapshot["plan_schedules"], briefs=snapshot["plan_briefs"],
        attention=sorted(snapshot.get('task_attention',[]),key=lambda r:r['task_id']),
        dependencies=sorted(snapshot.get('task_dependencies',[]),key=lambda r:(r['task_id'],r['prerequisite_id'])),
        resources=sorted(snapshot.get('task_resources',[]),key=lambda r:r['task_id']),
        details=sorted([(r['task_id'], r['stage'], [(s['id'], s['title']) for s in json.loads(r['steps_json'])])
                        for r in snapshot.get('task_details', []) if r['stage'] or json.loads(r['steps_json'])]))


def _replay(store, c, plan_id, target):
    """Whitelist-only writes; do not delete/reinsert plans or existing records."""
    from desktop.models import new_id
    current = store._snapshot_in_transaction(c, plan_id)
    current_tasks = {r["id"]: r for r in current["tasks"]}
    target_tasks = {r["id"]: r for r in target["tasks"]}
    if {i for i, r in current_tasks.items() if not r["manual"]} != {i for i, r in target_tasks.items() if not r["manual"]}:
        raise ValueError("这份历史的学习任务结构与当前计划不同，不能原地回溯；请使用副本保留当前记录")
    if len(target_tasks) != len(target["tasks"]) or target["plans"][0]["id"] != plan_id:
        raise ValueError("历史任务或所属计划不完整，不能回溯")
    focus_row = c.execute("SELECT value_json FROM settings WHERE key='focus_state'").fetchone()
    focus = json.loads(focus_row[0]) if focus_row else {}
    if focus.get("task_id") in current_tasks and (focus.get("session_id") or focus.get("phase") == "break"):
        raise ValueError("请先到专注页结束这份计划关联的专注或休息，再重新预览回溯")
    notices = []
    reviewed = {r["week"] for r in current["reviews"]}
    for tid, row in current_tasks.items():
        if tid not in target_tasks:
            if has_records(row):
                notices.append(f"保留任务“{json.loads(row['payload_json'])['title']}”：已有学习记录，不随回溯删除")
            else:
                c.execute("DELETE FROM tasks WHERE id=? AND plan_id=?", (tid, plan_id))
    for tid, row in target_tasks.items():
        payload = DailyTask.model_validate_json(row["payload_json"])
        date.fromisoformat(row["due_date"])
        if row["remind_at"]:
            datetime.strptime(row["remind_at"], "%H:%M")
        old = current_tasks.get(tid)
        if old:
            if (row["week"], row["day"], row["manual"]) != (old["week"], old["day"], old["manual"]):
                raise ValueError("历史中的任务归属已变化，不能原地回溯")
            if old["week"] in reviewed and not old["manual"]:
                if any(old[k] != row[k] for k in TASK_FIELDS):
                    notices.append(f"保留任务“{payload.title}”：该学习周已复盘，保留当前安排")
                continue
            values = {k: row[k] for k in TASK_FIELDS}
            if has_records(old):
                values["ai_locked"] = max(old["ai_locked"], values["ai_locked"])
                if values["ai_locked"] != row["ai_locked"]:
                    notices.append(f"保留任务“{payload.title}”的保护锁：已有学习记录，不因回溯解除保护")
            c.execute("UPDATE tasks SET " + ",".join(f"{k}=?" for k in TASK_FIELDS) + " WHERE id=? AND plan_id=?",
                      (*[values[k] for k in TASK_FIELDS], tid, plan_id))
        else:
            if not row["manual"] or c.execute("SELECT 1 FROM tasks WHERE id=?", (tid,)).fetchone():
                raise ValueError("历史任务ID已被占用，不能覆盖其他任务")
            c.execute("""INSERT INTO tasks (id,plan_id,week,day,manual,payload_json,due_date,remind_at,user_modified,ai_locked)
                         VALUES (?,?,0,NULL,1,?,?,?,?,?)""",
                      (tid, plan_id, row["payload_json"], row["due_date"], row["remind_at"], row["user_modified"], row["ai_locked"]))
            notices.append(f"恢复手动任务“{payload.title}”的内容和日期；不从历史填回已删除的学习记录")
    from desktop.learning_tools import details_in, validate_details
    target_details = {r['task_id']: r for r in target.get('task_details', [])}
    for tid in target_tasks:
        if not c.execute('SELECT 1 FROM tasks WHERE id=?', (tid,)).fetchone():
            continue
        old = details_in(c, tid)
        row = target_details.get(tid)
        desired = dict(stage=row['stage'], steps=json.loads(row['steps_json'])) if row else dict(stage='', steps=[])
        validate_details(desired)
        if target_tasks[tid]['week'] in reviewed and not target_tasks[tid]['manual']:
            continue
        current_steps = {s['id']: s for s in old['steps']}
        steps = [dict(s, done=current_steps.get(s['id'], {}).get('done', False)) for s in desired['steps']]
        target_ids = {s['id'] for s in steps}
        for step in old['steps']:
            if step['done'] and step['id'] not in target_ids:
                steps.append(step)
                notices.append('保留已打卡的子任务：' + step['title'])
        validate_details(dict(stage=desired['stage'], steps=steps))
        if desired['stage'] or steps:
            c.execute('INSERT INTO task_details VALUES (?,?,?) ON CONFLICT(task_id) DO UPDATE SET stage=excluded.stage,steps_json=excluded.steps_json',
                      (tid, desired['stage'], json.dumps(steps, ensure_ascii=False)))
        else:
            c.execute('DELETE FROM task_details WHERE task_id=?', (tid,))
    if target.get('task_series') != current.get('task_series'):
        notices.append('周期规则保持当前状态；本次仅回溯已生成任务的安排，不重新启动或扩增周期')
    from desktop.organizer import attention_in
    target_attention={r['task_id']:r for r in target.get('task_attention',[])}
    for tid in target_tasks:
        if c.execute('SELECT 1 FROM tasks WHERE id=?',(tid,)).fetchone():
            r=target_attention.get(tid)
            if r is None:
                c.execute('DELETE FROM task_attention WHERE task_id=?',(tid,))
            else:
                attention_in(c,tid,priority=r['priority'],pinned=r['pinned'],focus_date=r['focus_date'])
    for tid in target_tasks:
        row=c.execute('SELECT week,manual FROM tasks WHERE id=?',(tid,)).fetchone()
        if not row or (not row['manual'] and row['week'] in reviewed):continue
        c.execute('DELETE FROM task_dependencies WHERE task_id=?',(tid,))
        for edge in target.get('task_dependencies',[]):
            if edge['task_id']==tid:
                if not c.execute('SELECT 1 FROM tasks WHERE id=?',(edge['prerequisite_id'],)).fetchone():
                    raise ValueError('历史中的前置任务不存在，不能静默移除依赖')
                c.execute('INSERT INTO task_dependencies VALUES (?,?)',(tid,edge['prerequisite_id']))
    from desktop.workflow import validate
    validate(c)
    # Associated files are never modified; replay only their saved references.
    desired_resources={r['task_id']:r for r in target.get('task_resources',[])}
    for tid in target_tasks:
        if not c.execute('SELECT 1 FROM tasks WHERE id=?',(tid,)).fetchone():continue
        c.execute('DELETE FROM task_resources WHERE task_id=?',(tid,))
        if tid in desired_resources:
            c.execute('INSERT INTO task_resources VALUES (?,?)',(tid,desired_resources[tid]['items_json']))
    plan = target["plans"][0]
    c.execute("UPDATE plans SET " + ",".join(f"{k}=?" for k in PLAN_FIELDS) + " WHERE id=?",
              (*[plan[k] for k in PLAN_FIELDS], plan_id))
    for row in target["weeks"]:
        c.execute("UPDATE weeks SET theme=?,milestone=? WHERE plan_id=? AND number=?",
                  (row["theme"], row["milestone"], plan_id, row["number"]))
    c.execute("DELETE FROM plan_briefs WHERE plan_id=?", (plan_id,))
    for row in target["plan_briefs"]:
        c.execute("INSERT INTO plan_briefs VALUES (?,?,?)", (plan_id, row["data_json"], row["parent_plan_id"]))
    schedule = target["plan_schedules"][0]
    today = date.today().isoformat()
    start, resume = schedule["pause_start"], schedule["resume_date"]
    if start and resume <= today:
        start, resume = None, None
        notices.append("历史暂停已到恢复日期，保持可学习状态，不重新暂停或补发旧提醒")
    elif start:
        start = today
    old_schedule = current["plan_schedules"][0]
    if (old_schedule["pause_start"], old_schedule["resume_date"]) != (start, resume):
        store._end_pause(c, plan_id, today)
        if start:
            c.execute("INSERT INTO pause_history VALUES (?,?,?,?,NULL)", (new_id(), plan_id, start, resume))
    end = max(schedule["end_date"], c.execute("SELECT MAX(due_date) FROM tasks WHERE plan_id=?", (plan_id,)).fetchone()[0] or schedule["end_date"])
    c.execute("UPDATE plan_schedules SET end_date=?,pause_start=?,resume_date=? WHERE plan_id=?", (end, start, resume, plan_id))
    saved = store.get_plan_in_transaction(c, plan_id)
    validate_plan_for_profile(saved["plan"], saved["profile"])
    store._validate_schedule(c, plan_id)
    if c.execute("SELECT 1 FROM tasks WHERE plan_id=? AND due_date<?", (plan_id, plan["start_date"])).fetchone():
        raise ValueError("保留任务的日期早于历史开始日，不能回溯；请先调整日期或选择其他历史")
    return notices


def differences(before, after):
    result = []
    if before.get('task_attention',[]) != after.get('task_attention',[]):
        titles={r['id']:json.loads(r['payload_json'])['title'] for r in after['tasks']+before['tasks']}
        def describe_attention(snapshot):
            from desktop.organizer import PRIORITIES
            return '\n'.join(f"{titles.get(r['task_id'],'任务')}：{PRIORITIES[r['priority']]}优先级，{'置顶' if r['pinned'] else '不置顶'}，重点日期{r['focus_date'] or '无'}" for r in snapshot.get('task_attention',[])) or '默认展示顺序'
        result.append(dict(name='优先级、置顶与重点',before=describe_attention(before),after=describe_attention(after)))
    labels = dict(title="计划名称", overview="计划概述", profile_json="目标与时间设置", tips_json="学习建议",
                  start_date="开始日期", weekdays_json="每周学习日", archived="归档状态")
    for field in PLAN_FIELDS:
        a, b = before["plans"][0][field], after["plans"][0][field]
        if a != b:
            def value(text):
                if field == "archived":
                    return "已归档" if text else "未归档"
                if field == "weekdays_json":
                    return "、".join("周" + "一二三四五六日"[d] for d in json.loads(text))
                if field == "tips_json":
                    return "\n".join(json.loads(text))
                if field == "profile_json":
                    p = json.loads(text)
                    return "\n".join(f"{name}：{p.get(key, '未填写')}" for key, name in (
                        ("subject", "科目"), ("goal", "目标"), ("current_level", "基础"), ("plan_weeks", "学习周数"),
                        ("days_per_week", "每周学习天数"), ("minutes_per_day", "每日分钟上限"), ("preferences", "偏好与限制")))
                return str(text)
            result.append(dict(name=labels[field], before=value(a), after=value(b)))
    old, new = ({r["id"]: r for r in s["tasks"]} for s in (before, after))
    for tid in sorted(old.keys() | new.keys()):
        a, b = old.get(tid), new.get(tid)
        def text(row, other):
            if not row:
                return "不在这份计划中"
            p = json.loads(row["payload_json"])
            q = json.loads(other["payload_json"]) if other else {}
            lines = [f"{name}：{p[key]}" for key, name in (("title", "名称"), ("duration_minutes", "预计分钟"),
                      ("description", "具体说明"), ("deliverable", "完成标准")) if p[key] != q.get(key)]
            for key, name in (("due_date", "日期"), ("remind_at", "提醒"), ("ai_locked", "AI保护锁"), ("user_modified", "手动修改标记")):
                if other is None or row[key] != other[key]:
                    value = ("是" if row[key] else "否") if key in {"ai_locked", "user_modified"} else row[key] or "关闭"
                    lines.append(f"{name}：{value}")
            return "\n".join(lines)
        if a is None or b is None or any(a[k] != b[k] for k in TASK_FIELDS):
            result.append(dict(name=json.loads((b or a)["payload_json"])["title"], before=text(a, b), after=text(b, a)))
    for table, label in (("weeks", "阶段目标"), ("plan_schedules", "实际排期与暂停"), ("plan_briefs", "需求与概略")):
        if before[table] != after[table]:
            def describe(rows):
                if table == "plan_schedules":
                    r = rows[0]
                    return f"结束：{r['end_date']}\n" + (f"暂停至：{r['resume_date']}（当天恢复）" if r["pause_start"] else "正常学习")
                if table == "weeks":
                    return "\n".join(f"第{r['number']}周：{r['theme']} · {r['milestone']}" for r in rows)
                if not rows:
                    return "未记录"
                data = json.loads(rows[0]["data_json"])
                requirements = data.get("requirements", {})
                lines = ["目标：" + str(requirements.get("profile", {}).get("goal", "未记录"))]
                lines += [str(p.get("summary", "")) + "\n" + str(p.get("content", "")) for p in data.get("outline", {}).get("points", [])]
                lines += [str(a.get("question", "")) + "：" + str(a.get("answer", "")) for a in requirements.get("answers", [])]
                lines += ["修改意见：" + str(a.get("request", "")) for a in data.get("adjustments", [])]
                return "\n".join(lines)
            result.append(dict(name=label, before=describe(before[table]), after=describe(after[table])))
    old_details, new_details = ({r['task_id']: r for r in s.get('task_details', [])} for s in (before, after))
    for tid in sorted(old_details.keys() | new_details.keys()):
        def describe_detail(row):
            if not row:
                return '未分组，没有执行步骤'
            return '分组：' + (row['stage'] or '未分组') + '\n' + '\n'.join(s['title'] for s in json.loads(row['steps_json']))
        a, b = describe_detail(old_details.get(tid)), describe_detail(new_details.get(tid))
        if a != b:
            task = new.get(tid) or old.get(tid)
            result.append(dict(name=(json.loads(task['payload_json'])['title'] if task else '任务') + ' · 阶段与子任务', before=a, after=b))
    if before.get('task_resources',[]) != after.get('task_resources',[]):
        def resources_text(snapshot):
            return '\n'.join(f"{x['title']}：{x['target']}" for r in snapshot.get('task_resources',[]) for x in json.loads(r['items_json'])) or '没有资料或成果关联'
        result.append(dict(name='资料与成果关联（不修改原文件）',before=resources_text(before),after=resources_text(after)))
    return result


@dataclass(frozen=True)
class HistoryPreview:
    plan_id: str
    entry_id: str
    side: str
    token: str
    changes: list
    notices: list
    overloads: list


def preview_in_transaction(store, c, plan_id, entry_id, side):
    if side not in {"before", "after"}:
        raise ValueError("请选择调整前或调整后状态")
    row = c.execute("SELECT * FROM plan_versions WHERE id=? AND plan_id=?", (entry_id, plan_id)).fetchone()
    if not row or not utc_now() - timedelta(days=7) <= stamp(row["created_at"]) <= utc_now():
        raise ValueError("这条历史不在最近7天内，请重新打开历史列表")
    target = json.loads(row[f"{side}_json"])
    if not target:
        raise ValueError("创建计划之前没有可回溯的状态，请选择创建后")
    store._normalize_snapshot(target)
    before = store._snapshot_in_transaction(c, plan_id)
    if not before["plans"]:
        raise ValueError("这份计划已不存在")
    token = fingerprint(c)
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.row_factory = sqlite3.Row
        # serialize also works while the applying connection owns BEGIN
        # IMMEDIATE; backing up that same write connection would wait forever.
        scratch.deserialize(c.serialize())
        scratch.execute("PRAGMA foreign_keys=ON")
        notices = _replay(store, scratch, plan_id, target)
        after = store._snapshot_in_transaction(scratch, plan_id)
        changes = differences(before, after)
        loads = [dict(r, source="计划上限") for r in plan_loads(scratch).values() if r["plan_id"] == plan_id and r["over"]]
        loads += [dict(r, source="每日总预算") for r in capacity(scratch).values() if r["over"]]
    return HistoryPreview(plan_id, entry_id, side, token, changes, notices, loads)


def preview(store, plan_id, entry_id, side="after"):
    with closing(store.connect()) as c:
        c.execute("BEGIN")
        return preview_in_transaction(store, c, plan_id, entry_id, side)


def apply(store, proposal):
    with store.write(confirm_plan_limits=True) as c:
        fresh = preview_in_transaction(store, c, proposal.plan_id, proposal.entry_id, proposal.side)
        if fresh != proposal:
            raise ValueError("计划、记录或预算已有变化，请重新预览回溯")
        if not fresh.changes:
            raise ValueError("当前安排与所选状态相同，无需回溯")
        row = c.execute("SELECT * FROM plan_versions WHERE id=?", (proposal.entry_id,)).fetchone()
        target = store._normalize_snapshot(json.loads(row[f"{proposal.side}_json"]))
        before = store._snapshot_in_transaction(c, proposal.plan_id)
        store.manual_backup("before-history-replay")
        _replay(store, c, proposal.plan_id, target)
        store._touch(c, proposal.plan_id)
        local_time = stamp(row["created_at"]).astimezone().strftime("%m-%d %H:%M:%S")
        store._version(c, proposal.plan_id, f"回溯历史 {local_time} · {'调整前' if proposal.side == 'before' else '调整后'}", before)
    return True
