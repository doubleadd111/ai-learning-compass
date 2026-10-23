"""Offline, deterministic scheduling. Proposals never write data or call a model."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from contextlib import closing
from datetime import date, timedelta
import hashlib
import json

from desktop.planning import protection_reason


def paused(schedule, day=None):
    day = (day or date.today()).isoformat()
    return bool(schedule and schedule.get("pause_start") and
                schedule["pause_start"] <= day < schedule["resume_date"])


def budget_values(value):
    if value is None:
        return [None] * 7
    if not isinstance(value, list) or len(value) != 7 or any(
            x is not None and (type(x) is not int or not 0 <= x <= 1440) for x in value):
        raise ValueError("每日预算需为七个0–1440的分钟数，未设置使用空值")
    return value


def fingerprint(c):
    """All plan revisions + policy; no secrets, credentials or model contents."""
    plans = [tuple(r) for r in c.execute("SELECT id,revision,archived FROM plans ORDER BY id")]
    schedules = [tuple(r) for r in c.execute("SELECT * FROM plan_schedules ORDER BY plan_id")]
    budget = c.execute("SELECT value_json FROM settings WHERE key='daily_budget'").fetchone()
    f = c.execute("SELECT value_json FROM settings WHERE key='focus_state'").fetchone()
    focus = json.loads(f[0]) if f else {}
    identity = {k: focus.get(k) for k in ("task_id", "session_id", "phase")}
    tasks = [tuple(r) for r in c.execute("SELECT * FROM tasks ORDER BY id")]
    extras = [[tuple(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY 1,2")] for table in ("task_details", "task_series", "series_tasks", "task_attention", "study_events", "captures")]
    for table in ('task_dependencies','reminder_controls','task_resources'):
        if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
            extras.append([tuple(r) for r in c.execute(f'SELECT * FROM {table} ORDER BY 1,2')])
    return hashlib.sha256(json.dumps([plans, schedules, budget[0] if budget else None, identity, tasks, extras],
                                    ensure_ascii=False).encode()).hexdigest()


def capacity(c, *, include_paused=False):
    raw = c.execute("SELECT value_json FROM settings WHERE key='daily_budget'").fetchone()
    limits = budget_values(json.loads(raw[0]) if raw else None)
    days = {}
    for r in c.execute("""SELECT t.due_date,t.payload_json,COALESCE(p.id,'__standalone__') AS id,
            COALESCE(p.title,'独立任务') AS title,s.pause_start,s.resume_date
            FROM tasks t LEFT JOIN plans p ON p.id=t.plan_id
            LEFT JOIN plan_schedules s ON s.plan_id=p.id WHERE COALESCE(p.archived,0)=0"""):
        if not include_paused and paused(dict(r)):
            continue
        due = r["due_date"]
        row = days.setdefault(due, dict(date=due, total=0, budget=limits[date.fromisoformat(due).weekday()], plans={}))
        minutes = json.loads(r["payload_json"])["duration_minutes"]
        row["total"] += minutes
        item = row["plans"].setdefault(r["id"], dict(title=r["title"], minutes=0))
        item["minutes"] += minutes
    for row in days.values():
        row["over"] = max(0, row["total"] - row["budget"]) if row["budget"] is not None else 0
    return days


def report_text(rows):
    return "\n".join(f"{r['date']}：{r['total']} / {r['budget']} 分钟，超出 {r['over']} 分钟；" +
                     "、".join(f"{v['title']} {v['minutes']}分钟" for v in r["plans"].values()) for r in rows)


class BudgetExceeded(ValueError):
    def __init__(self, token, rows):
        self.token, self.rows = token, rows
        self.details = report_text(rows)
        brief = "\n".join(f"{r['date']}：{r['total']} / {r['budget']} 分钟，超出 {r['over']} 分钟" for r in rows[:3])
        super().__init__(f"新安排在 {len(rows)} 天超过所有活动计划的总预算：\n" + brief +
                         "\n展开详细信息可查看各计划占用。仍要保留这些安排吗？每份计划自己的每日／每周上限不会改变。")


def plan_loads(c):
    """Calendar-day/week commitments, including retained and completed tasks."""
    loads, profiles = {}, {}
    for r in c.execute("""SELECT t.plan_id,t.due_date,t.payload_json,p.title,p.profile_json
            FROM tasks t JOIN plans p ON p.id=t.plan_id"""):
        from desktop.models import DesktopProfile
        if r['plan_id'] not in profiles:
            profiles[r['plan_id']] = DesktopProfile.model_validate_json(r['profile_json'])
        profile = profiles[r['plan_id']]
        day = date.fromisoformat(r["due_date"])
        for kind, period, limit in (("日", day.isoformat(), profile.minutes_per_day),
                ("周", (day - timedelta(days=day.weekday())).isoformat(), profile.weekly_minutes)):
            key = (r["plan_id"], kind, period)
            load = loads.setdefault(key, dict(plan_id=r["plan_id"], title=r["title"], kind=kind, period=period, total=0, limit=limit))
            load["total"] += json.loads(r["payload_json"])["duration_minutes"]
    for load in loads.values():
        load["over"] = max(0, load["total"] - load["limit"])
    return loads


def plan_limit_text(rows):
    return "\n".join(f"{r['title']} · {r['period']}" + (f" 至 {(date.fromisoformat(r['period']) + timedelta(days=6)).isoformat()}" if r['kind'] == "周" else "") +
        f"：本{r['kind']}安排 {r['total']} 分钟，上限 {r['limit']} 分钟，超出 {r['over']} 分钟" for r in rows)


class PlanCapacityExceeded(ValueError):
    def __init__(self, token, rows, global_rows):
        self.token, self.rows = token, rows
        self.global_rows = global_rows
        self.details = plan_limit_text(rows)
        if global_rows:
            self.details += "\n\n全部任务的每日预算：\n" + report_text(global_rows)
        super().__init__("这次手动安排将超过计划时间上限：\n" + plan_limit_text(rows[:3]) +
            "\n\n仍然保留本次安排吗？原来的时间上限不变，AI 不会自动获得超限权限。")


@dataclass
class SchedulePreview:
    token: str
    kind: str
    changes: list = field(default_factory=list)
    protected: list = field(default_factory=list)
    schedules: dict = field(default_factory=dict)
    extensions: dict = field(default_factory=dict)
    conflicts: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    loads: list = field(default_factory=list)
    allow_overload: bool = False
    request: dict = field(default_factory=dict)


def preview(store, ids, start, *, include_future=False, include_locked=False,
            allow_overload=False, kind="补排", plan_id=None, resume_date=None,
            exact_date=False, keep_dates=True, manual_override=False):
    today = date.today()
    if start < today or start > today + timedelta(days=366):
        raise ValueError("起排日请选择今天至一年内的日期")
    saved = {p["id"]: store.get_plan(p["id"]) for p in store.list_plans()}
    if kind in {"暂停", "恢复"} and plan_id not in saved:
        raise ValueError("请选择未归档的计划")
    if kind == "暂停" and paused(saved[plan_id]["schedule"]):
        raise ValueError("该计划已暂停，请先恢复")
    if kind == "恢复" and not paused(saved[plan_id]["schedule"]):
        raise ValueError("该计划没有处于暂停状态，请刷新")
    if kind == "暂停" and (not resume_date or not today < resume_date <= today + timedelta(days=366)):
        raise ValueError("恢复日期请选择明天至一年内")
    rows = store.tasks()
    ids = set(ids)
    if kind in {"暂停", "恢复"} and not keep_dates:
        ids = {r["id"] for r in rows if r["plan_id"] == plan_id and r["status"] != "已完成"}
    selected_plans = {r["plan_id"] for r in rows if r["id"] in ids}
    if include_future:
        thresholds = {p: min(r["due_date"] for r in rows if r["id"] in ids and r["plan_id"] == p) for p in selected_plans}
        ids.update(r["id"] for r in rows if r["plan_id"] in thresholds and r["due_date"] >= thresholds[r["plan_id"]])
    if not ids and kind == "补排":
        raise ValueError("请至少选择一项任务")
    if not ids <= {r["id"] for r in rows}:
        raise ValueError("任务已有变化，请重新选择")
    with closing(store.connect()) as c:
        token = fingerprint(c)
    result = SchedulePreview(token, kind, allow_overload=allow_overload)
    result.request = dict(ids=sorted(ids), start=start, include_future=False, include_locked=include_locked,
                          allow_overload=allow_overload, kind=kind, plan_id=plan_id, resume_date=resume_date,
                          exact_date=exact_date, keep_dates=keep_dates, manual_override=manual_override)
    manual_override = bool(manual_override and exact_date and kind == "补排")
    schedules = {p: dict(s["schedule"]) for p, s in saved.items()}
    if kind in {"暂停", "恢复"}:
        schedule = schedules[plan_id]
        schedule["pause_start"] = today.isoformat() if kind == "暂停" else None
        schedule["resume_date"] = resume_date.isoformat() if kind == "暂停" else None
        result.schedules[plan_id] = schedule
        start = resume_date if kind == "暂停" else start
    targets = []
    reviewed = {pid: store.reviews(pid) for pid in saved}
    for row in rows:
        if row["id"] not in ids:
            continue
        s = saved.get(row["plan_id"])
        reason = "计划已归档" if row["archived"] else protection_reason({**row, "manual": 0}, reviewed.get(row["plan_id"], {}))
        if exact_date and not row["archived"]:
            reason = manual_move_reason(row, reviewed.get(row["plan_id"], {}))
        if reason == "用户已锁定" and include_locked:
            reason = ""
        if s and paused(s["schedule"]) and kind not in {"恢复", "暂停"} and not exact_date:
            reason = "计划已暂停，请先恢复"
        if reason:
            result.protected.append(dict(id=row["id"], title=row["task"].title, reason=reason))
        else:
            targets.append(row)
    target_ids = {r["id"] for r in targets}
    daily, weekly, global_daily = defaultdict(int), defaultdict(int), defaultdict(int)
    def add(row, due):
        minutes = row["task"].duration_minutes
        p = row["plan_id"]
        daily[p, due] += minutes
        weekly[p, date.fromisoformat(due).isocalendar()[:2]] += minutes
        # Count the candidate's load after a scheduled pause has ended as well.
        if not row["archived"] and (p is None or p in saved and (not paused(schedules[p]) or kind == "暂停" and p == plan_id)):
            global_daily[due] += minutes
    for r in rows:
        if r["id"] not in target_ids:
            add(r, r["due_date"])
    limits = budget_values(store.setting("daily_budget"))
    floors = {}
    for row in sorted(targets, key=lambda r: (r["due_date"], r["week"], r["day"] or 0, r["id"])):
        p, task = row["plan_id"], row["task"]
        s = saved.get(p)
        days = json.loads(s["weekdays_json"]) if s and not exact_date else list(range(7))
        first = max(start, date.fromisoformat(s["start_date"]) if s else start, floors.get(p, start),
                    date.fromisoformat(row["due_date"]) if kind != "恢复" and not exact_date else start)
        placed = None
        for offset in range(1 if exact_date else 367):
            candidate = start + timedelta(days=offset)
            if candidate < first or candidate.weekday() not in days:
                continue
            if s and schedules[p].get("pause_start") and schedules[p]["pause_start"] <= candidate.isoformat() < schedules[p]["resume_date"]:
                continue
            due = candidate.isoformat()
            if s and not manual_override and daily[p, due] + task.duration_minutes > s["profile"].minutes_per_day:
                continue
            if s and not manual_override and weekly[p, candidate.isocalendar()[:2]] + task.duration_minutes > s["profile"].weekly_minutes:
                continue
            limit = limits[candidate.weekday()]
            if not allow_overload and limit is not None and global_daily[due] + task.duration_minutes > limit:
                continue
            placed = due
            break
        if placed is None:
            if exact_date:
                reasons = []
                if s and start < date.fromisoformat(s["start_date"]):
                    reasons.append(f"早于计划开始日 {s['start_date']}")
                if s and schedules[p].get("pause_start") and schedules[p]["pause_start"] <= start.isoformat() < schedules[p]["resume_date"]:
                    reasons.append(f"计划暂停中，{schedules[p]['resume_date']} 才恢复")
                if s and daily[p, start.isoformat()] + task.duration_minutes > s["profile"].minutes_per_day:
                    reasons.append(f"该计划当天将安排 {daily[p, start.isoformat()] + task.duration_minutes} 分钟，上限 {s['profile'].minutes_per_day} 分钟")
                if s and weekly[p, start.isocalendar()[:2]] + task.duration_minutes > s["profile"].weekly_minutes:
                    reasons.append(f"该计划本周将超过 {s['profile'].weekly_minutes} 分钟上限")
                limit = limits[start.weekday()]
                if not allow_overload and limit is not None and global_daily[start.isoformat()] + task.duration_minutes > limit:
                    reasons.append(f"当天全部任务将超过总预算 {limit} 分钟")
                result.conflicts.append(f"{row['plan_title']} / {task.title}：" + "；".join(reasons or ["该日期不可用"]))
            else:
                result.conflicts.append(f"{row['plan_title']} / {task.title}：一年内没有满足学习日及时间上限的位置；可减少范围、调整预算或明确允许总预算超载后重试。")
            add(row, row["due_date"])
            continue
        floors[p] = date.fromisoformat(placed)
        add(row, placed)
        if placed != row["due_date"]:
            result.changes.append(dict(id=row["id"], plan_id=p, title=task.title, plan=row["plan_title"],
                                       before=row["due_date"], after=placed, minutes=task.duration_minutes))
    for p in {r["plan_id"] for r in result.changes if r["plan_id"]} | set(result.schedules):
        end = max([saved[p]["schedule"]["end_date"], *[r["after"] for r in result.changes if r["plan_id"] == p]])
        schedules[p]["end_date"] = end
        result.schedules[p] = schedules[p]
        result.extensions[p] = (date.fromisoformat(end) - date.fromisoformat(saved[p]["schedule"]["end_date"])).days
    for due in sorted({r["after"] for r in result.changes}):
        limit = limits[date.fromisoformat(due).weekday()]
        result.loads.append(dict(date=due, total=global_daily[due], budget=limit,
                                 over=max(0, global_daily[due] - limit) if limit is not None else 0))
    if manual_override:
        with closing(store.connect()) as c:
            original = plan_loads(c)
        for p in {r["plan_id"] for r in result.changes if r["plan_id"]}:
            s = saved[p]
            for kind, source, limit in (("日", daily, s["profile"].minutes_per_day), ("周", weekly, s["profile"].weekly_minutes)):
                for (pid, period), total in source.items():
                    if pid != p:
                        continue
                    period_text = date.fromisocalendar(period[0], period[1], 1).isoformat() if kind == "周" else period
                    if max(0, total - limit) > original.get((p, kind, period_text), {}).get("over", 0):
                        result.warnings.append(plan_limit_text([dict(title=s["title"], kind=kind, period=period_text, total=total, limit=limit, over=total-limit)]))
    from desktop.workflow import dependency_warnings
    with closing(store.connect()) as c:
        result.warnings.extend(dependency_warnings(c,dates={r['id']:r['after'] for r in result.changes}))
    return result


def manual_move_reason(row, reviewed=()):
    """Deliberate date-only edits preserve records and the independent AI lock."""
    if row["archived"]:
        return "计划已归档，请先恢复归档"
    if row["status"] == "已完成":
        return "任务已完成，保留原日期"
    if row["week"] in reviewed:
        return "这一周已复盘，保留历史日期"
    if row.get("focus_active"):
        return "请先结束关联的专注或休息"
    return ""
