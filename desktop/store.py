"""桌面版事务存储。安装目录中不保存用户数据或密钥。"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime, timedelta
import threading
from pathlib import Path
from uuid import uuid4

from models import DailyTask, StudyPlan, WeeklyPlan, validate_plan_for_profile
from progress_store import StudyRepository, TaskProgress
from desktop.models import DesktopProfile, ModelConnection, new_id, schedule_dates

APP_ID = 0x4C434D50
SCHEMA_VERSION = 8


def now() -> str:
    return datetime.now().astimezone().isoformat()


def data_directory() -> Path:
    if os.getenv("LEARNING_COMPASS_DATA_DIR"):
        return Path(os.environ["LEARNING_COMPASS_DATA_DIR"]).resolve()
    from platformdirs import user_data_dir
    return Path(user_data_dir("LearningCompass", appauthor=False))


class Store:
    def __init__(self, directory: Path | None = None):
        self.directory = Path(directory or data_directory())
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "learning-compass.sqlite3"
        self.backups = self.directory / "backups"
        self.backups.mkdir(exist_ok=True)
        self._backed_up_date = ""
        self._budget_local = threading.local()
        self._read_local = threading.local()
        self._initialize()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self):
        with closing(self.connect()) as probe:
            old_version = probe.execute("PRAGMA user_version").fetchone()[0]
            if old_version in range(1, SCHEMA_VERSION + 1) and probe.execute("PRAGMA application_id").fetchone()[0] != APP_ID:
                raise ValueError("不是学习罗盘桌面数据库")
        if old_version in range(1, SCHEMA_VERSION):
            self.backup_to(self.backups / f"before-schema-{SCHEMA_VERSION}-{new_id()}.sqlite3")
            staged_path = self.directory / f"upgrade-{new_id()}.sqlite3"
            try:
                self.backup_to(staged_path)
                with closing(sqlite3.connect(staged_path)) as staged, staged:
                    if old_version == 1:
                        self._upgrade_v2(staged)
                    if old_version <= 2:
                        self._upgrade_v3(staged)
                    if old_version <= 3:
                        self._upgrade_v4(staged)
                    staged.commit()
                    if old_version <= 4:
                        self._upgrade_v5(staged)
                    if old_version <= 5:
                        self._upgrade_v6(staged)
                    if old_version <= 6:
                        self._upgrade_v7(staged)
                    self._upgrade_v8(staged)
                self.inspect_backup(staged_path)
                os.replace(staged_path, self.path)
            finally:
                staged_path.unlink(missing_ok=True)
            return
        with closing(self.connect()) as c, c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise ValueError("数据由更新版本创建，请使用新版学习罗盘打开")
            if version == SCHEMA_VERSION:
                return
            if c.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]:
                raise ValueError("这不是桌面版数据库，请使用旧版导入功能")
            c.executescript("""
                CREATE TABLE plans (
                    id TEXT PRIMARY KEY, profile_json TEXT NOT NULL, title TEXT NOT NULL,
                    overview TEXT NOT NULL, tips_json TEXT NOT NULL, start_date TEXT NOT NULL,
                    weekdays_json TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
                    revision INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE weeks (
                    plan_id TEXT REFERENCES plans(id) ON DELETE CASCADE, number INTEGER,
                    theme TEXT NOT NULL, milestone TEXT NOT NULL, PRIMARY KEY(plan_id,number)
                );
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
                    week INTEGER NOT NULL, day INTEGER NOT NULL, payload_json TEXT NOT NULL,
                    due_date TEXT NOT NULL, remind_at TEXT, status TEXT NOT NULL DEFAULT '未开始',
                    manual_seconds INTEGER NOT NULL DEFAULT 0, focus_seconds INTEGER NOT NULL DEFAULT 0,
                    difficulty INTEGER, note TEXT NOT NULL DEFAULT '', user_modified INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(plan_id,week,day)
                );
                CREATE TABLE reviews (plan_id TEXT REFERENCES plans(id) ON DELETE CASCADE,
                    week INTEGER, summary TEXT NOT NULL, PRIMARY KEY(plan_id,week));
                CREATE TABLE connections (id TEXT PRIMARY KEY, data_json TEXT NOT NULL);
                CREATE TABLE settings (key TEXT PRIMARY KEY, value_json TEXT NOT NULL);
                CREATE TABLE generation_events (id TEXT PRIMARY KEY,
                    plan_id TEXT REFERENCES plans(id) ON DELETE SET NULL, connection_name TEXT,
                    model TEXT, kind TEXT, metrics_json TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE notifications (task_id TEXT REFERENCES tasks(id) ON DELETE CASCADE,
                    fingerprint TEXT, delivered_at TEXT, PRIMARY KEY(task_id,fingerprint));
                CREATE TABLE focus_sessions (id TEXT PRIMARY KEY,
                    task_id TEXT REFERENCES tasks(id) ON DELETE SET NULL, seconds INTEGER NOT NULL,
                    started_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE undo (plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
                    snapshot_json TEXT NOT NULL, expected_revision INTEGER NOT NULL);
                CREATE INDEX tasks_due ON tasks(due_date, status);
            """)
            c.execute(f"PRAGMA application_id={APP_ID}")
            c.execute("PRAGMA user_version=1")
            self._upgrade_v2(c)
            self._upgrade_v3(c)
            self._upgrade_v4(c)
            c.commit()
            self._upgrade_v5(c)
            self._upgrade_v6(c)
            self._upgrade_v7(c)
            self._upgrade_v8(c)
        c.close()

    @staticmethod
    def _upgrade_v2(c):
        if not c.in_transaction:
            c.execute("BEGIN IMMEDIATE")
        c.execute("ALTER TABLE tasks ADD COLUMN ai_locked INTEGER NOT NULL DEFAULT 0")
        c.execute("UPDATE tasks SET ai_locked=user_modified")
        c.execute("CREATE TABLE planning_drafts (id TEXT PRIMARY KEY, data_json TEXT NOT NULL, updated_at TEXT NOT NULL)")
        c.execute("""CREATE TABLE plan_briefs (plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
                  data_json TEXT NOT NULL, parent_plan_id TEXT)""")
        c.execute("""CREATE TABLE plan_versions (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
                  kind TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
                  expected_revision INTEGER NOT NULL, created_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE planning_events (id TEXT PRIMARY KEY, draft_id TEXT,
                  data_json TEXT NOT NULL, created_at TEXT NOT NULL)""")
        # Old undo snapshots did not contain task locks. Preserve them by upgrading
        # their in-memory representation, never rewriting the old backup file.
        for row in c.execute("SELECT plan_id,snapshot_json FROM undo").fetchall():
            data = json.loads(row[1])
            Store._normalize_snapshot(data)
            c.execute("UPDATE undo SET snapshot_json=? WHERE plan_id=?", (json.dumps(data, ensure_ascii=False), row[0]))
        c.execute("PRAGMA user_version=2")

    @staticmethod
    def _upgrade_v3(c):
        if not c.in_transaction:
            c.execute("BEGIN IMMEDIATE")
        # Unknown creation times of existing drafts must not be fabricated.
        c.execute("ALTER TABLE planning_drafts ADD COLUMN created_at TEXT")
        c.execute("PRAGMA user_version=3")

    @staticmethod
    def _upgrade_v4(c):
        if not c.in_transaction:
            c.execute("BEGIN IMMEDIATE")
        c.execute("""CREATE TABLE plan_schedules (plan_id TEXT PRIMARY KEY REFERENCES plans(id) ON DELETE CASCADE,
                     end_date TEXT NOT NULL, pause_start TEXT, resume_date TEXT)""")
        c.execute("""CREATE TABLE pause_history (id TEXT PRIMARY KEY, plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
                     start_date TEXT NOT NULL, resume_date TEXT NOT NULL, ended_date TEXT)""")
        for p in c.execute("SELECT id,start_date,profile_json FROM plans").fetchall():
            end = (date.fromisoformat(p[1]) + timedelta(days=json.loads(p[2])["plan_weeks"] * 7 - 1)).isoformat()
            last = c.execute("SELECT MAX(due_date) FROM tasks WHERE plan_id=?", (p[0],)).fetchone()[0]
            c.execute("INSERT INTO plan_schedules VALUES (?,?,NULL,NULL)", (p[0], max(end, last or end)))
        c.execute("PRAGMA user_version=4")

    def read(self, sql, params=()):
        scope = getattr(self._read_local, 'scope', None)
        if scope is not None:
            connection, cache = scope
            key = (sql, tuple(params))
            if key not in cache:
                cache[key] = [dict(row) for row in connection.execute(sql, params).fetchall()]
            return [dict(row) for row in cache[key]]
        c = self.connect()
        try:
            return [dict(row) for row in c.execute(sql, params).fetchall()]
        finally:
            c.close()

    @contextmanager
    def read_scope(self):
        """Share only a synchronous UI read snapshot, never across a write/event loop."""
        if getattr(self._read_local, 'scope', None) is not None:
            yield
            return
        c = self.connect()
        try:
            c.execute('BEGIN')
            self._read_local.scope = (c, {})
            yield
        finally:
            self._read_local.scope = None
            c.close()

    @staticmethod
    def _upgrade_v5(c):
        # Rebuild only the canonical task table. Child FK names remain `tasks`;
        # foreign keys are disabled outside the transaction on the staging copy.
        c.commit()
        c.execute("PRAGMA foreign_keys=OFF")
        try:
            with c:
                c.execute("BEGIN IMMEDIATE")
                c.execute("""CREATE TABLE tasks_new (
                    id TEXT PRIMARY KEY, plan_id TEXT REFERENCES plans(id) ON DELETE CASCADE,
                    week INTEGER NOT NULL, day INTEGER, payload_json TEXT NOT NULL,
                    due_date TEXT NOT NULL, remind_at TEXT, status TEXT NOT NULL DEFAULT '未开始',
                    manual_seconds INTEGER NOT NULL DEFAULT 0, focus_seconds INTEGER NOT NULL DEFAULT 0,
                    difficulty INTEGER, note TEXT NOT NULL DEFAULT '', user_modified INTEGER NOT NULL DEFAULT 0,
                    ai_locked INTEGER NOT NULL DEFAULT 0, manual INTEGER NOT NULL DEFAULT 0,
                    CHECK ((manual=0 AND plan_id IS NOT NULL AND week BETWEEN 1 AND 8 AND day IS NOT NULL AND day BETWEEN 1 AND 7)
                        OR (manual=1 AND week=0 AND day IS NULL)), UNIQUE(plan_id,week,day))""")
                columns = [r[1] for r in c.execute("PRAGMA table_info(tasks)")]
                names = ','.join(columns)
                c.execute(f"INSERT INTO tasks_new ({names}) SELECT {names} FROM tasks")
                c.execute("DROP TABLE tasks")
                c.execute("ALTER TABLE tasks_new RENAME TO tasks")
                c.execute("CREATE INDEX tasks_due ON tasks(due_date,status)")
                affected = c.execute("SELECT DISTINCT plan_id FROM tasks WHERE status='未开始' AND focus_seconds>0").fetchall()
                c.execute("UPDATE tasks SET status='进行中' WHERE status='未开始' AND focus_seconds>0")
                for row in affected:
                    c.execute("UPDATE plans SET revision=revision+1,updated_at=? WHERE id=?", (now(), row[0]))
                if c.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("任务迁移关联校验失败，原数据库未改动")
                c.execute("PRAGMA user_version=5")
        finally:
            c.execute("PRAGMA foreign_keys=ON")

    @staticmethod
    def _upgrade_v6(c):
        if not c.in_transaction:
            c.execute("BEGIN IMMEDIATE")
        c.execute("""CREATE TABLE IF NOT EXISTS task_details (task_id TEXT PRIMARY KEY REFERENCES tasks(id) ON DELETE CASCADE,
                     stage TEXT NOT NULL, steps_json TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS task_series (id TEXT PRIMARY KEY,
                     plan_id TEXT REFERENCES plans(id) ON DELETE CASCADE, payload_json TEXT NOT NULL,
                     rule_json TEXT NOT NULL, remind_at TEXT, stopped INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL)""")
        c.execute("""CREATE TABLE IF NOT EXISTS series_tasks (series_id TEXT REFERENCES task_series(id) ON DELETE CASCADE,
                     scheduled_date TEXT NOT NULL, task_id TEXT UNIQUE REFERENCES tasks(id) ON DELETE SET NULL,
                     PRIMARY KEY(series_id,scheduled_date))""")
        c.execute("PRAGMA user_version=6")

    @staticmethod
    def _upgrade_v7(c):
        if not c.in_transaction:
            c.execute("BEGIN IMMEDIATE")
        c.execute("""CREATE TABLE IF NOT EXISTS trash (id TEXT PRIMARY KEY, kind TEXT NOT NULL,
                  entity_id TEXT NOT NULL, title TEXT NOT NULL, deleted_at TEXT NOT NULL,
                  expires_at TEXT NOT NULL, snapshot_json TEXT NOT NULL)""")
        c.execute("PRAGMA user_version=7")

    @staticmethod
    def _upgrade_v8(c):
        if not c.in_transaction:
            c.execute('BEGIN IMMEDIATE')
        from desktop.organizer import upgrade
        upgrade(c)

    def _daily_backup(self):
        today = date.today().isoformat()
        if today == self._backed_up_date:
            return
        target = self.backups / f"daily-{today}.sqlite3"
        if not target.exists():
            self.backup_to(target)
        self._backed_up_date = today
        for old in sorted(self.backups.glob("daily-*.sqlite3"), reverse=True)[30:]:
            old.unlink()

    @contextmanager
    def write(self, *, check_budget=True, confirm_plan_limits=False):
        if getattr(self._read_local, 'scope', None) is not None:
            raise ValueError('只读刷新尚未结束，请稍后再保存')
        self._daily_backup()
        c = self.connect()
        try:
            with c:
                from desktop.scheduling import capacity, fingerprint, BudgetExceeded, plan_loads, PlanCapacityExceeded
                c.execute("BEGIN IMMEDIATE")
                policy = c.execute("SELECT value_json FROM settings WHERE key='daily_budget'").fetchone()
                check_budget = check_budget and bool(policy and any(x is not None for x in json.loads(policy[0])))
                before = capacity(c) if check_budget else {}
                token = fingerprint(c) if check_budget or confirm_plan_limits else None
                original_plans = plan_loads(c) if confirm_plan_limits else {}
                original_ids = {r[0] for r in c.execute("SELECT id FROM plans")} if confirm_plan_limits else set()
                yield c
                worse = []
                if check_budget:
                    after = capacity(c)
                    worse = [r for k, r in sorted(after.items()) if r["over"] > before.get(k, {}).get("over", 0)]
                if confirm_plan_limits:
                    updated_plans = plan_loads(c)
                    increased = [r for key, r in sorted(updated_plans.items()) if r["over"] > original_plans.get(key, {}).get("over", 0)]
                    # An imported copy receives fresh UUIDs on each rolled-back
                    # attempt. Bind its approval to stable metadata, not that UUID.
                    def identity(pid):
                        if pid in original_ids:
                            return pid
                        return tuple(c.execute("SELECT title,profile_json,start_date,weekdays_json FROM plans WHERE id=?", (pid,)).fetchone())
                    signature = (token, tuple(sorted(((identity(r['plan_id']), r['kind'], r['period'], r['total'], r['limit']) for r in increased), key=repr)),
                                 tuple((r['date'], r['total'], r['budget']) for r in worse))
                    if increased:
                        if getattr(self._budget_local, "plan_approval", None) != signature:
                            raise PlanCapacityExceeded(signature, increased, worse)
                        worse = []  # The same confirmation covered both sets of limits.
                if check_budget:
                    signature = (token, [(r["date"], r["total"], r["budget"]) for r in worse])
                    if worse and getattr(self._budget_local, "approval", None) != signature:
                        raise BudgetExceeded(signature, worse)
        finally:
            c.close()

    def _touch(self, c, plan_id):
        c.execute("UPDATE plans SET revision=revision+1, updated_at=? WHERE id=?", (now(), plan_id))

    def setting(self, key, default=None):
        rows = self.read("SELECT value_json FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value_json"]) if rows else default

    def set_setting(self, key, value):
        if key == "daily_budget":
            from desktop.scheduling import budget_values
            value = budget_values(value)
        with self.write(check_budget=key != "daily_budget") as c:
            c.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                      (key, json.dumps(value, ensure_ascii=False)))

    @contextmanager
    def accept_budget(self, token):
        self._budget_local.approval = token
        try:
            yield
        finally:
            self._budget_local.approval = None

    @contextmanager
    def accept_plan_capacity(self, token):
        self._budget_local.plan_approval = token
        try:
            yield
        finally:
            self._budget_local.plan_approval = None

    def capacity(self):
        from desktop.scheduling import capacity
        with closing(self.connect()) as c:
            return capacity(c)

    def schedule(self, plan_id):
        return self.read("SELECT * FROM plan_schedules WHERE plan_id=?", (plan_id,))[0]

    def expire_pauses(self, today=None):
        """No backdated reminder delivery; expiry is persisted once, even after downtime."""
        today = today or date.today()
        rows = self.read("SELECT * FROM plan_schedules WHERE resume_date<=?", (today.isoformat(),))
        if not rows:
            return []
        with self.write(check_budget=False) as c:
            for row in rows:
                before = self._snapshot_in_transaction(c, row["plan_id"])
                self._end_pause(c, row["plan_id"], row["resume_date"])
                self._touch(c, row["plan_id"])
                self._version(c, row["plan_id"], "自动恢复计划", before)
        return [r["plan_id"] for r in rows]

    @staticmethod
    def _end_pause(c, plan_id, ended):
        c.execute("UPDATE pause_history SET ended_date=? WHERE plan_id=? AND ended_date IS NULL", (ended, plan_id))
        c.execute("UPDATE plan_schedules SET pause_start=NULL,resume_date=NULL WHERE plan_id=?", (plan_id,))

    def apply_schedule(self, proposal):
        from desktop.scheduling import fingerprint, preview
        manual_override = bool(proposal.request.get("manual_override") and proposal.request.get("exact_date") and proposal.kind == "补排")
        with self.write(confirm_plan_limits=manual_override) as c:
            if fingerprint(c) != proposal.token:
                raise ValueError("计划、记录或预算已有变化，请重新预览")
            fresh = preview(self, **proposal.request)
            if fresh != proposal or fresh.conflicts:
                raise ValueError("排期预览已变化或仍有冲突，请重新预览")
            ids = set(proposal.schedules)
            if not ids and not proposal.changes:
                raise ValueError("没有需要应用的日期变化")
            snapshots = {p: self._snapshot_in_transaction(c, p) for p in ids}
            for change in proposal.changes:
                c.execute("UPDATE tasks SET due_date=? WHERE id=? AND plan_id IS ?", (change["after"], change["id"], change["plan_id"]))
            for pid, schedule in proposal.schedules.items():
                if proposal.kind == "暂停":
                    if self.schedule(pid).get("pause_start"):
                        raise ValueError("该计划已暂停，请先恢复")
                    c.execute("INSERT INTO pause_history VALUES (?,?,?,?,NULL)",
                              (new_id(), pid, schedule["pause_start"], schedule["resume_date"]))
                elif proposal.kind == "恢复":
                    self._end_pause(c, pid, date.today().isoformat())
                c.execute("UPDATE plan_schedules SET end_date=?,pause_start=?,resume_date=? WHERE plan_id=?",
                          (schedule["end_date"], schedule["pause_start"], schedule["resume_date"], pid))
                if not manual_override and proposal.changes:
                    self._check_plan_capacity(c, pid)
                self._touch(c, pid)
                self._version(c, pid, proposal.kind, snapshots[pid])

    @staticmethod
    def _check_plan_capacity(c, plan_id):
        p = c.execute("SELECT profile_json FROM plans WHERE id=?", (plan_id,)).fetchone()
        profile = DesktopProfile.model_validate_json(p[0])
        daily, weekly = {}, {}
        for row in c.execute("SELECT due_date,payload_json FROM tasks WHERE plan_id=?", (plan_id,)):
            day = date.fromisoformat(row[0])
            minutes = DailyTask.model_validate_json(row[1]).duration_minutes
            daily[day] = daily.get(day, 0) + minutes
            week = day.isocalendar()[:2]
            weekly[week] = weekly.get(week, 0) + minutes
        if any(m > profile.minutes_per_day for m in daily.values()) or any(m > profile.weekly_minutes for m in weekly.values()):
            raise ValueError("与保留任务合并后超过这份计划的每日或每周上限，请重新安排日期")

    def connections(self) -> list[ModelConnection]:
        return [ModelConnection.model_validate_json(r["data_json"]) for r in self.read("SELECT * FROM connections ORDER BY rowid")]

    def save_connection(self, connection: ModelConnection):
        with self.write() as c:
            c.execute("INSERT INTO connections VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json",
                      (connection.id, connection.model_dump_json()))

    def delete_connection(self, connection_id):
        with self.write() as c:
            c.execute("DELETE FROM connections WHERE id=?", (connection_id,))

    def save_plan(self, profile: DesktopProfile, plan: StudyPlan, start: date, weekdays: list[int], *, provenance=None,
                  brief=None, parent_plan_id=None, parent_revision=None, archive_parent=False, draft_id=None):
        validate_plan_for_profile(plan, profile)
        if brief is not None:
            from desktop.planning import validate_brief
            validate_brief(brief)
        if len(weekdays) != profile.days_per_week:
            raise ValueError("学习日数量与计划不一致")
        dates = schedule_dates(start, weekdays, profile.plan_weeks)
        plan_id = new_id()
        with self.write() as c:
            if parent_plan_id:
                parent = c.execute("SELECT revision FROM plans WHERE id=?", (parent_plan_id,)).fetchone()
                if not parent or parent[0] != parent_revision:
                    raise ValueError("原计划已有变化，请重新确认副本背景")
            c.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,?,0,0,?,?)", (
                plan_id, profile.model_dump_json(), plan.title, plan.overview,
                json.dumps(plan.learning_tips, ensure_ascii=False), start.isoformat(), json.dumps(weekdays), now(), now()))
            end = (start + timedelta(days=profile.plan_weeks * 7 - 1)).isoformat()
            c.execute("INSERT INTO plan_schedules VALUES (?,?,NULL,NULL)", (plan_id, end))
            for week in plan.weekly_plans:
                c.execute("INSERT INTO weeks VALUES (?,?,?,?)", (plan_id, week.week_number, week.theme, week.milestone))
                for task in week.tasks:
                    c.execute("INSERT INTO tasks (id,plan_id,week,day,payload_json,due_date,remind_at) VALUES (?,?,?,?,?,?,?)",
                              (new_id(), plan_id, week.week_number, task.day, task.model_dump_json(), dates[(week.week_number, task.day)], "20:00"))
            if provenance:
                self._event(c, plan_id, "生成", provenance)
            if brief:
                c.execute("INSERT INTO plan_briefs VALUES (?,?,?)", (plan_id, json.dumps(brief, ensure_ascii=False), parent_plan_id))
            if parent_plan_id and archive_parent:
                parent_before = self._snapshot_in_transaction(c, parent_plan_id)
                c.execute("UPDATE plans SET archived=1 WHERE id=?", (parent_plan_id,))
                self._touch(c, parent_plan_id)
                self._version(c, parent_plan_id, "创建副本并归档原计划", parent_before)
            if draft_id:
                c.execute("DELETE FROM planning_drafts WHERE id=?", (draft_id,))
            self._version(c, plan_id, "创建计划", {})
        return plan_id

    def list_plans(self, search="", archived=False):
        return self.read("""SELECT p.*, (SELECT count(*) FROM tasks t WHERE t.plan_id=p.id) AS total,
            (SELECT count(*) FROM tasks t WHERE t.plan_id=p.id AND t.status='已完成') AS completed
            FROM plans p WHERE archived=? AND (title LIKE ? OR profile_json LIKE ?) ORDER BY updated_at DESC""",
                         (int(archived), f"%{search}%", f"%{search}%"))

    def tasks(self, plan_id=None, *, due=None):
        sql = "SELECT t.*,COALESCE(p.title,'独立任务') AS plan_title,COALESCE(p.archived,0) AS archived,COALESCE(a.priority,0) AS priority,COALESCE(a.pinned,0) AS pinned,a.focus_date FROM tasks t LEFT JOIN plans p ON p.id=t.plan_id LEFT JOIN task_attention a ON a.task_id=t.id WHERE 1=1"
        params = []
        if plan_id == "__standalone__":
            sql += " AND t.plan_id IS NULL"
        elif plan_id:
            sql += " AND t.plan_id=?"
            params.append(plan_id)
        if due:
            sql += " AND t.due_date<=? AND COALESCE(p.archived,0)=0 AND t.status!='已完成'"
            params.append(due)
        rows = self.read(sql + " ORDER BY t.due_date,t.week,t.day", params)
        schedules = {s["plan_id"]: s for s in self.read("SELECT * FROM plan_schedules")}
        focus = self.setting("focus_state", {})
        for row in rows:
            row["task"] = DailyTask.model_validate_json(row["payload_json"])
            from desktop.scheduling import paused
            row["paused"] = paused(schedules.get(row["plan_id"]))
            row["focus_active"] = row["id"] == focus.get("task_id") and bool(focus.get("session_id") or focus.get("phase") == "break")
        if due:
            rows = [r for r in rows if not r["paused"]]
        return rows

    def get_plan(self, plan_id):
        rows = self.read("SELECT * FROM plans WHERE id=?", (plan_id,))
        if not rows:
            raise ValueError("找不到这份计划")
        row = rows[0]
        row["profile"] = DesktopProfile.model_validate_json(row["profile_json"])
        row["tasks"] = self.tasks(plan_id)
        row["schedule"] = self.schedule(plan_id)
        weeks = [WeeklyPlan(week_number=w["number"], theme=w["theme"], milestone=w["milestone"],
                    tasks=[t["task"] for t in sorted(row["tasks"], key=lambda t: t["day"] or 0) if t["week"] == w["number"]])
                 for w in self.read("SELECT * FROM weeks WHERE plan_id=? ORDER BY number", (plan_id,))]
        row["plan"] = StudyPlan(title=row["title"], overview=row["overview"], weekly_plans=weeks, learning_tips=json.loads(row["tips_json"]))
        return row

    def rename_plan(self, plan_id, title):
        if not 2 <= len(title.strip()) <= 100:
            raise ValueError("计划名称需要 2–100 个字")
        with self.write() as c:
            before = self._snapshot_in_transaction(c, plan_id)
            if not before["plans"] or before["plans"][0]["title"] == title.strip():
                return
            c.execute("UPDATE plans SET title=? WHERE id=?", (title.strip(), plan_id))
            self._touch(c, plan_id)
            self._version(c, plan_id, "重命名计划", before)

    def archive(self, plan_id, archived=True):
        with self.write() as c:
            before = self._snapshot_in_transaction(c, plan_id)
            if not before["plans"] or before["plans"][0]["archived"] == int(archived):
                return
            c.execute("UPDATE plans SET archived=? WHERE id=?", (int(archived), plan_id))
            self._touch(c, plan_id)
            self._version(c, plan_id, "归档计划" if archived else "恢复归档计划", before)

    def delete_plan(self, plan_id, *, expected_revision=None):
        with self.write() as c:
            row = c.execute("SELECT revision FROM plans WHERE id=?", (plan_id,)).fetchone()
            if not row:
                raise ValueError("找不到这份计划，未执行删除")
            if expected_revision is not None and row[0] != expected_revision:
                raise ValueError("计划或学习记录已有变化，请重新查看删除确认")
            state = c.execute("SELECT value_json FROM settings WHERE key='focus_state'").fetchone()
            focus = json.loads(state[0]) if state else {}
            if (focus.get("session_id") or focus.get("phase") == "break") and c.execute(
                    "SELECT 1 FROM tasks WHERE id=? AND plan_id=?", (focus.get("task_id"), plan_id)).fetchone():
                raise ValueError("请先结束这份计划关联的专注或休息，再删除计划")
            # BEGIN IMMEDIATE blocks competing writes. The backup reads the
            # committed database through a separate connection before deletion.
            backup = self.manual_backup("before-delete-plan")
            from desktop.trash import capture_plan
            capture_plan(c, plan_id)
            c.execute("DELETE FROM focus_sessions WHERE task_id IN (SELECT id FROM tasks WHERE plan_id=?)", (plan_id,))
            c.execute("DELETE FROM generation_events WHERE plan_id=?", (plan_id,))
            c.execute("DELETE FROM plans WHERE id=?", (plan_id,))
        return backup

    def add_manual_task(self, payload: DailyTask, due_date: str, *, plan_id=None, remind_at=None, confirm_limits=False):
        payload = DailyTask.model_validate(payload)
        if payload.day != 1:
            raise ValueError("手动任务不使用学习日编号")
        date.fromisoformat(due_date)
        if remind_at is not None:
            datetime.strptime(remind_at, "%H:%M")
        task_id = new_id()
        with self.write(confirm_plan_limits=confirm_limits) as c:
            before = None
            if plan_id:
                p = c.execute("SELECT archived,start_date FROM plans WHERE id=?", (plan_id,)).fetchone()
                if not p or p[0]:
                    raise ValueError("请选择未归档的计划，或选择独立任务")
                if due_date < p[1]:
                    raise ValueError("任务日期不能早于这份计划的开始日")
                before = self._snapshot_in_transaction(c, plan_id)
            c.execute("""INSERT INTO tasks (id,plan_id,week,day,payload_json,due_date,remind_at,manual,ai_locked,user_modified)
                VALUES (?,?,0,NULL,?,?,?,1,1,1)""", (task_id, plan_id, payload.model_dump_json(), due_date, remind_at))
            if plan_id:
                if not confirm_limits:
                    self._check_plan_capacity(c, plan_id)
                c.execute("UPDATE plan_schedules SET end_date=MAX(end_date,?) WHERE plan_id=?", (due_date, plan_id))
                self._touch(c, plan_id)
                self._version(c, plan_id, "手动添加任务", before)
        return task_id

    def delete_manual_task(self, task_id):
        with self.write() as c:
            task = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if not task or not task["manual"]:
                raise ValueError("只能在此删除手动添加的任务")
            state = c.execute("SELECT value_json FROM settings WHERE key='focus_state'").fetchone()
            if state and json.loads(state[0]).get("task_id") == task_id:
                raise ValueError("请先结束关联的专注，再删除任务")
            before = self._snapshot_in_transaction(c, task["plan_id"]) if task["plan_id"] else None
            from desktop.trash import capture_task
            capture_task(c, task_id)
            c.execute("DELETE FROM focus_sessions WHERE task_id=?", (task_id,))
            c.execute("DELETE FROM tasks WHERE id=?", (task_id,))
            if task["plan_id"]:
                self._touch(c, task["plan_id"])
                self._version(c, task["plan_id"], "删除手动任务", before)

    def update_task(self, task_id, payload: DailyTask, due_date: str, remind_at: str | None,
                    status: str, manual_minutes: int, difficulty, note: str, ai_locked=None, confirm_limits=False, attention=None):
        task = next(iter(self.read("SELECT * FROM tasks WHERE id=?", (task_id,))), None)
        if not task:
            raise ValueError("找不到这项任务")
        date.fromisoformat(due_date)
        if remind_at is not None:
            datetime.strptime(remind_at, "%H:%M")
        TaskProgress(status=status, actual_minutes=manual_minutes, difficulty=difficulty, note=note)
        if status == "未开始":
            from desktop.learning_tools import task_details
            details, _ = task_details(self, task_id)
            if task["focus_seconds"] > 0 or any(s['done'] for s in details['steps']):
                status = "进行中"
        if task["manual"]:
            if payload.day != 1:
                raise ValueError("手动任务不使用学习日编号")
            with self.write(confirm_plan_limits=confirm_limits) as c:
                before = self._snapshot_in_transaction(c, task["plan_id"]) if task["plan_id"] else None
                from desktop.organizer import _check_manual_balance, attention_in
                _check_manual_balance(c, task_id, manual_minutes * 60)
                if attention is not None:
                    attention_in(c, task_id, **attention)
                c.execute("""UPDATE tasks SET payload_json=?,due_date=?,remind_at=?,status=?,manual_seconds=?,
                    difficulty=?,note=? WHERE id=?""", (payload.model_dump_json(), due_date, remind_at, status,
                    manual_minutes * 60, difficulty, note, task_id))
                if task["plan_id"]:
                    start = c.execute("SELECT start_date FROM plans WHERE id=?", (task["plan_id"],)).fetchone()[0]
                    if due_date < start:
                        raise ValueError("任务日期不能早于计划开始日")
                    if not confirm_limits and (due_date != task["due_date"] or payload.duration_minutes != DailyTask.model_validate_json(task["payload_json"]).duration_minutes):
                        self._check_plan_capacity(c, task["plan_id"])
                    c.execute("UPDATE plan_schedules SET end_date=MAX(end_date,?) WHERE plan_id=?", (due_date, task["plan_id"]))
                    self._touch(c, task["plan_id"])
                    if any(task[k] != value for k, value in (("payload_json", payload.model_dump_json()), ("due_date", due_date), ("remind_at", remind_at))):
                        self._version(c, task["plan_id"], "编辑手动任务", before)
            return
        saved = self.get_plan(task["plan_id"])
        if payload.day != task["day"] or payload.duration_minutes > saved["profile"].minutes_per_day:
            raise ValueError("任务时长超过该计划的单日上限")
        old = DailyTask.model_validate_json(task["payload_json"])
        protected = (old != payload or task["due_date"] != due_date or task["remind_at"] != remind_at)
        if self.read("SELECT 1 FROM reviews WHERE plan_id=? AND week=?", (task["plan_id"], task["week"])):
            if old != payload or task["status"] != status:
                raise ValueError("已复盘周次不能修改任务或完成状态；可以补记笔记和实际用时")
        others = [t for t in saved["tasks"] if t["id"] != task_id and t["due_date"] == due_date]
        if not confirm_limits and sum(t["task"].duration_minutes for t in others) + payload.duration_minutes > saved["profile"].minutes_per_day:
            raise ValueError("该日期已超过这份计划的单日时间上限，请换一天或缩短任务")
        target_week = date.fromisoformat(due_date).isocalendar()[:2]
        actual_week_minutes = sum(t["task"].duration_minutes for t in saved["tasks"]
                                  if t["id"] != task_id and date.fromisoformat(t["due_date"]).isocalendar()[:2] == target_week)
        if not confirm_limits and actual_week_minutes + payload.duration_minutes > saved["profile"].weekly_minutes:
            raise ValueError("改期后超过这份计划的每周时间上限，请换一周安排")
        with self.write(confirm_plan_limits=confirm_limits) as c:
            before = self._snapshot_in_transaction(c, task["plan_id"])
            from desktop.organizer import _check_manual_balance, attention_in
            _check_manual_balance(c, task_id, manual_minutes * 60)
            if attention is not None:
                attention_in(c, task_id, **attention)
            c.execute("""UPDATE tasks SET payload_json=?,due_date=?,remind_at=?,status=?,manual_seconds=?,
                difficulty=?,note=?,user_modified=MAX(user_modified,?),ai_locked=MAX(ai_locked,?) WHERE id=?""",
                      (payload.model_dump_json(), due_date, remind_at, status, manual_minutes * 60,
                       difficulty, note, int(protected), int(protected), task_id))
            self._touch(c, task["plan_id"])
            c.execute("UPDATE plan_schedules SET end_date=MAX(end_date,?) WHERE plan_id=?", (due_date, task["plan_id"]))
            if ai_locked is not None and not protected:
                # Explicit unlocking never removes progress-based protection.
                from desktop.planning import protection_reason
                current = {**task, "status": status, "manual_seconds": manual_minutes * 60,
                           "difficulty": difficulty, "note": note, "ai_locked": 0}
                if not ai_locked and task["ai_locked"] and protection_reason(current, self.reviews(task["plan_id"])):
                    raise ValueError("已有学习记录或已复盘的任务不能解除保护")
                c.execute("UPDATE tasks SET ai_locked=? WHERE id=?", (int(ai_locked), task_id))
            from desktop.plan_history import arrangement
            if arrangement(before) != arrangement(self._snapshot_in_transaction(c, task["plan_id"])):
                self._version(c, task["plan_id"], "编辑任务安排", before)

    def progress(self, plan_id):
        return {(t["week"], t["day"]): TaskProgress(status=t["status"],
                actual_minutes=min(1440, (t["manual_seconds"] + t["focus_seconds"]) // 60),
                difficulty=t["difficulty"], note=t["note"]) for t in self.tasks(plan_id) if not t["manual"]}

    def reviews(self, plan_id):
        return {r["week"]: r["summary"] for r in self.read("SELECT * FROM reviews WHERE plan_id=?", (plan_id,))}

    def _snapshot(self, plan_id):
        with closing(self.connect()) as c:
            return self._snapshot_in_transaction(c, plan_id)

    @staticmethod
    def _snapshot_in_transaction(c, plan_id):
        result = {"format": "learning-compass-plan", "version": 7}
        for table in ("plans", "weeks", "tasks", "reviews", "generation_events", "focus_sessions", "notifications", "plan_briefs", "plan_schedules", "pause_history", "task_details", "task_series", "series_tasks", "task_attention", "study_events"):
            if table == "plans":
                rows = c.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchall()
            elif table in {"focus_sessions", "notifications", "task_details", "task_attention", "study_events"}:
                rows = c.execute(f"SELECT * FROM {table} WHERE task_id IN (SELECT id FROM tasks WHERE plan_id=?)", (plan_id,)).fetchall()
            elif table == "series_tasks":
                rows = c.execute("SELECT * FROM series_tasks WHERE series_id IN (SELECT id FROM task_series WHERE plan_id=?)", (plan_id,)).fetchall()
            else:
                rows = c.execute(f"SELECT * FROM {table} WHERE plan_id=?", (plan_id,)).fetchall()
            result[table] = [dict(row) for row in rows]
        return result

    @staticmethod
    def _version(c, plan_id, kind, before):
        after = Store._snapshot_in_transaction(c, plan_id)
        revision = c.execute("SELECT revision FROM plans WHERE id=?", (plan_id,)).fetchone()[0]
        c.execute("INSERT INTO plan_versions VALUES (?,?,?,?,?,?,?)", (new_id(), plan_id, kind,
                  json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), revision, now()))

    def save_draft(self, draft):
        from desktop.planning import PlanningDraft
        draft = PlanningDraft.model_validate(draft.model_dump())
        with self.write() as c:
            previous = c.execute("SELECT data_json FROM planning_drafts WHERE id=?", (draft.id,)).fetchone()
            body = draft.model_dump(mode="json", exclude={"revision"})
            if previous:
                old_body = PlanningDraft.model_validate_json(previous[0]).model_dump(mode="json", exclude={"revision"})
                if old_body == body:
                    return
            stamp = now()
            c.execute("INSERT INTO planning_drafts (id,data_json,updated_at,created_at) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json,updated_at=excluded.updated_at",
                      (draft.id, draft.model_dump_json(), stamp, stamp))

    def draft_entries(self):
        from desktop.planning import PlanningDraft
        return [dict(draft=PlanningDraft.model_validate_json(row["data_json"]), created_at=row["created_at"], updated_at=row["updated_at"])
                for row in self.read("SELECT * FROM planning_drafts ORDER BY updated_at DESC")]

    def drafts(self):
        from desktop.planning import PlanningDraft
        return [PlanningDraft.model_validate_json(row["data_json"]) for row in self.read("SELECT * FROM planning_drafts ORDER BY updated_at DESC")]

    def delete_draft(self, draft_id):
        with self.write() as c:
            c.execute("DELETE FROM planning_drafts WHERE id=?", (draft_id,))
            c.execute("DELETE FROM planning_events WHERE draft_id=?", (draft_id,))

    def planning_event(self, draft_id, stage, provenance):
        metrics = provenance.get("metrics", {})
        safe = dict(stage=stage, connection_name=provenance.get("connection_name", ""), model=provenance.get("model", ""),
                    metrics={key: metrics[key] for key in ("attempts", "input_tokens", "output_tokens", "latency_ms", "success", "error_category", "summary_excerpted_count") if key in metrics})
        with self.write() as c:
            c.execute("INSERT INTO planning_events VALUES (?,?,?,?)", (new_id(), draft_id, json.dumps(safe, ensure_ascii=False), now()))
        return safe

    def brief(self, plan_id):
        rows = self.read("SELECT data_json FROM plan_briefs WHERE plan_id=?", (plan_id,))
        return json.loads(rows[0]["data_json"]) if rows else None

    def versions(self, plan_id):
        return self.read("SELECT * FROM plan_versions WHERE plan_id=? ORDER BY rowid DESC", (plan_id,))

    def set_task_lock(self, task_id, locked):
        from desktop.planning import protection_reason
        rows = [row for row in self.tasks() if row["id"] == task_id]
        if not rows:
            raise ValueError("找不到任务")
        row = rows[0]
        if not locked and protection_reason({**row, "ai_locked": 0}, self.reviews(row["plan_id"])):
            raise ValueError("已有学习记录或已复盘的任务不能解除保护")
        with self.write() as c:
            before = self._snapshot_in_transaction(c, row["plan_id"])
            c.execute("UPDATE tasks SET ai_locked=? WHERE id=?", (int(locked), task_id))
            self._touch(c, row["plan_id"])
            if row["plan_id"] and row["ai_locked"] != int(locked):
                self._version(c, row["plan_id"], "修改AI保护锁", before)

    def apply_proposal(self, plan_id, proposal, target_ids, expected_revision, *, provenance=None, draft_id=None, brief_update=None):
        from desktop.planning import validate_brief, validate_proposal
        if brief_update is not None:
            validate_brief({"adjustments": [brief_update]})
        saved = self.get_plan(plan_id)
        if saved["revision"] != expected_revision:
            raise ValueError("计划已有变化，请重新生成修改预览")
        validate_proposal(saved, proposal, target_ids, self.reviews(plan_id))
        before = self._snapshot(plan_id)
        with self.write() as c:
            if c.execute("SELECT revision FROM plans WHERE id=?", (plan_id,)).fetchone()[0] != expected_revision:
                raise ValueError("计划已有变化，请重新预览")
            for change in proposal.changes:
                c.execute("UPDATE tasks SET payload_json=?,due_date=? WHERE id=? AND plan_id=?",
                          (change.task.model_dump_json(), change.due_date.isoformat(), change.task_id, plan_id))
            self._touch(c, plan_id)
            if provenance:
                self._event(c, plan_id, "AI局部调整", provenance)
            if brief_update:
                meta = self.brief(plan_id) or {"requirements": {"profile": saved["profile"].model_dump()}}
                meta.setdefault("adjustments", []).append(brief_update)
                c.execute("INSERT INTO plan_briefs VALUES (?,?,NULL) ON CONFLICT(plan_id) DO UPDATE SET data_json=excluded.data_json",
                          (plan_id, json.dumps(meta, ensure_ascii=False)))
            self._version(c, plan_id, "AI局部调整", before)
            if draft_id:
                c.execute("DELETE FROM planning_drafts WHERE id=?", (draft_id,))

    def undo_latest(self, plan_id, *, confirm_limits=False):
        saved = self.get_plan(plan_id)
        versions = self.versions(plan_id)
        if not versions or versions[0]["kind"] not in {"AI局部调整", "周复盘调整", "补排", "暂停", "恢复"} or versions[0]["expected_revision"] != saved["revision"]:
            raise ValueError("没有可撤销的调整，或之后已有新记录；不能覆盖当前进度")
        previous = json.loads(versions[0]["before_json"])
        self._normalize_snapshot(previous)
        before = self._snapshot(plan_id)
        with self.write(confirm_plan_limits=confirm_limits) as c:
            if c.execute("SELECT revision FROM plans WHERE id=?", (plan_id,)).fetchone()[0] != saved["revision"]:
                raise ValueError("计划已有变化，请重新检查")
            # Never delete/reinsert the plan: stable IDs and all unrelated history
            # survive undo. Revision guard ensures no newer progress is erased.
            for row in previous["tasks"]:
                c.execute("UPDATE tasks SET payload_json=?,due_date=?,remind_at=?,ai_locked=? WHERE id=? AND plan_id=?",
                          (row["payload_json"], row["due_date"], row["remind_at"], row["ai_locked"], row["id"], plan_id))
            for row in previous["weeks"]:
                c.execute("UPDATE weeks SET theme=?,milestone=? WHERE plan_id=? AND number=?", (row["theme"], row["milestone"], plan_id, row["number"]))
            c.execute("DELETE FROM plan_briefs WHERE plan_id=?", (plan_id,))
            for row in previous["plan_briefs"]:
                c.execute("INSERT INTO plan_briefs VALUES (?,?,?)", (plan_id, row["data_json"], row["parent_plan_id"]))
            c.execute("DELETE FROM reviews WHERE plan_id=?", (plan_id,))
            for row in previous["reviews"]:
                c.execute("INSERT INTO reviews VALUES (?,?,?)", (plan_id, row["week"], row["summary"]))
            c.execute("DELETE FROM generation_events WHERE plan_id=?", (plan_id,))
            for row in previous["generation_events"]:
                c.execute("INSERT INTO generation_events VALUES (?,?,?,?,?,?,?)", tuple(row[key] for key in ("id", "plan_id", "connection_name", "model", "kind", "metrics_json", "created_at")))
            c.execute("DELETE FROM undo WHERE plan_id=?", (plan_id,))
            for table in ("plan_schedules", "pause_history"):
                c.execute(f"DELETE FROM {table} WHERE plan_id=?", (plan_id,))
                for row in previous[table]:
                    columns = ("plan_id", "end_date", "pause_start", "resume_date") if table == "plan_schedules" else ("id", "plan_id", "start_date", "resume_date", "ended_date")
                    c.execute(f"INSERT INTO {table} VALUES ({','.join('?' for _ in columns)})", tuple(row[k] for k in columns))
            if not confirm_limits:
                self._check_plan_capacity(c, plan_id)
            self._touch(c, plan_id)
            self._version(c, plan_id, "撤销调整", before)

    def export_plan(self, plan_id) -> bytes:
        return json.dumps(self._snapshot(plan_id), ensure_ascii=False, indent=2).encode("utf-8")

    @staticmethod
    def _normalize_snapshot(snapshot):
        if not isinstance(snapshot, dict):
            raise ValueError("计划快照必须是对象")
        if snapshot.get("version") == 2:
            snapshot["version"] = 3
            snapshot["plan_briefs"] = []
            for task in snapshot["tasks"]:
                task["ai_locked"] = int(bool(task.get("user_modified", 0)))
        if snapshot.get("version") == 3:
            p = snapshot["plans"][0]
            end = (date.fromisoformat(p["start_date"]) + timedelta(days=json.loads(p["profile_json"])["plan_weeks"] * 7 - 1)).isoformat()
            end = max([end, *[t["due_date"] for t in snapshot["tasks"]]])
            snapshot["plan_schedules"] = [dict(plan_id=p["id"], end_date=end, pause_start=None, resume_date=None)]
            snapshot["pause_history"] = []
            snapshot["version"] = 4
        if snapshot.get("version") == 4:
            for task in snapshot["tasks"]:
                task["manual"] = 0
            snapshot["version"] = 5
        if snapshot.get("version") == 5:
            snapshot.update(version=6, task_details=[], task_series=[], series_tasks=[])
        if snapshot.get("version") == 6:
            snapshot.update(version=7, task_attention=[], study_events=[])
        if snapshot.get("version") != 7:
            raise ValueError("计划文件版本不受支持")
        return snapshot

    @staticmethod
    def _insert_snapshot(c, snapshot, *, new_ids=False):
        snapshot = json.loads(json.dumps(snapshot))
        Store._normalize_snapshot(snapshot)
        from desktop.learning_tools import validate_snapshot_extras
        validate_snapshot_extras(snapshot)
        if new_ids:
            plan_id = new_id()
            task_ids = {r["id"]: new_id() for r in snapshot["tasks"]}
            series_ids = {r["id"]: new_id() for r in snapshot["task_series"]}
            snapshot["plans"][0]["id"] = plan_id
            for table in ("weeks", "tasks", "reviews", "generation_events", "plan_briefs", "plan_schedules", "pause_history", "task_series"):
                for row in snapshot[table]:
                    row["plan_id"] = plan_id
                    if table == "tasks":
                        row["id"] = task_ids[row["id"]]
                    elif table == "task_series":
                        row["id"] = series_ids[row["id"]]
                    elif table in {"generation_events", "pause_history"}:
                        row["id"] = new_id()
            for table in ("focus_sessions", "notifications", "task_details", "task_attention", "study_events"):
                for row in snapshot[table]:
                    row["task_id"] = task_ids[row["task_id"]]
                    if table in {"focus_sessions", "study_events"}:
                        row["id"] = new_id()
            for row in snapshot["series_tasks"]:
                row["series_id"] = series_ids[row["series_id"]]
                if row["task_id"] is not None:
                    row["task_id"] = task_ids[row["task_id"]]
        for table in ("plans", "weeks", "tasks", "reviews", "generation_events", "focus_sessions", "notifications", "plan_briefs", "plan_schedules", "pause_history", "task_details", "task_series", "series_tasks", "task_attention", "study_events"):
            for row in snapshot[table]:
                if table == "plan_briefs":
                    from desktop.planning import validate_brief
                    validate_brief(json.loads(row["data_json"]))
                columns = list(row)
                # 列名来自本机 schema 的白名单，不能执行导入文件提供的 SQL 名称。
                allowed = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                if set(columns) != allowed:
                    raise ValueError("备份字段不完整或版本不匹配")
                c.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(row.values()))
        return snapshot["plans"][0]["id"]

    def import_plan(self, content: bytes, start: date, weekdays: list[int] | None = None, *, confirm_limits=False):
        if len(content) > 20_000_000:
            raise ValueError("计划文件过大")
        try:
            data = json.loads(content.decode("utf-8-sig"))
            if data.get("version") == 1:
                return self._import_legacy_json(data, start, weekdays)
            if data.get("format") != "learning-compass-plan" or data.get("version") not in {2, 3, 4, 5, 6, 7} or len(data["plans"]) != 1:
                raise ValueError("不是有效的学习罗盘计划文件")
            # 同一事务中验证领域约束；任何失败都会回滚整份导入。
            with self.write(confirm_plan_limits=confirm_limits) as c:
                plan_id = self._insert_snapshot(c, data, new_ids=True)
                saved = self.get_plan_in_transaction(c, plan_id)
                validate_plan_for_profile(saved["plan"], saved["profile"])
                metadata = c.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
                weekdays = json.loads(metadata["weekdays_json"])
                schedule_dates(date.fromisoformat(metadata["start_date"]), weekdays, saved["profile"].plan_weeks)
                if len(weekdays) != saved["profile"].days_per_week:
                    raise ValueError("每周学习日数量不一致")
                imported_tasks = c.execute("SELECT * FROM tasks WHERE plan_id=?", (plan_id,)).fetchall()
                self._validate_schedule(c, plan_id)
                if not confirm_limits:
                    self._check_plan_capacity(c, plan_id)
                if sum(not t["manual"] for t in imported_tasks) != saved["profile"].plan_weeks * saved["profile"].days_per_week:
                    raise ValueError("任务数量不一致")
                for task in imported_tasks:
                    if task["ai_locked"] not in (0, 1):
                        raise ValueError("任务锁定状态无效")
                    date.fromisoformat(task["due_date"])
                    if task["remind_at"] is not None:
                        datetime.strptime(task["remind_at"], "%H:%M")
                    payload = DailyTask.model_validate_json(task["payload_json"])
                    if task["manual"] not in (0, 1) or (task["manual"] and (task["week"] != 0 or task["day"] is not None or payload.day != 1)):
                        raise ValueError("手动任务编号不一致")
                    if not task["manual"] and (task["week"] not in range(1, saved["profile"].plan_weeks + 1) or payload.day != task["day"]):
                        raise ValueError("任务编号不一致")
                    TaskProgress(status=task["status"], actual_minutes=min(1440, task["manual_seconds"] // 60), difficulty=task["difficulty"], note=task["note"])
                    if task["manual_seconds"] < 0 or task["focus_seconds"] < 0:
                        raise ValueError("用时不能为负数")
                from desktop.organizer import validate_database
                validate_database(c)
                self._version(c, plan_id, "导入计划", {})
            return plan_id
        except (ValueError, TypeError, KeyError, IndexError, sqlite3.Error) as error:
            from desktop.scheduling import BudgetExceeded, PlanCapacityExceeded
            if isinstance(error, (BudgetExceeded, PlanCapacityExceeded)):
                raise
            raise ValueError("无法导入，请选择由学习罗盘导出的完整计划 JSON") from error

    @staticmethod
    def _validate_schedule(c, plan_id):
        rows = c.execute("SELECT * FROM plan_schedules WHERE plan_id=?", (plan_id,)).fetchall()
        if len(rows) != 1:
            raise ValueError("缺少完整排期元数据")
        s = rows[0]
        end = date.fromisoformat(s["end_date"])
        dates = c.execute("SELECT MIN(due_date),MAX(due_date) FROM tasks WHERE plan_id=?", (plan_id,)).fetchone()
        if dates[1] and end < date.fromisoformat(dates[1]):
            raise ValueError("排期结束日早于已有任务")
        if bool(s["pause_start"]) != bool(s["resume_date"]):
            raise ValueError("暂停日期不完整")
        if s["pause_start"] and date.fromisoformat(s["pause_start"]) >= date.fromisoformat(s["resume_date"]):
            raise ValueError("暂停恢复日期无效")
        for h in c.execute("SELECT * FROM pause_history WHERE plan_id=?", (plan_id,)):
            if date.fromisoformat(h["start_date"]) >= date.fromisoformat(h["resume_date"]):
                raise ValueError("暂停历史无效")
            if h["ended_date"] and date.fromisoformat(h["ended_date"]) < date.fromisoformat(h["start_date"]):
                raise ValueError("恢复历史无效")

    @staticmethod
    def get_plan_in_transaction(c, plan_id):
        row = c.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        profile = DesktopProfile.model_validate_json(row["profile_json"])
        weeks = []
        for w in c.execute("SELECT * FROM weeks WHERE plan_id=? ORDER BY number", (plan_id,)):
            tasks = [DailyTask.model_validate_json(t[0]) for t in c.execute("SELECT payload_json FROM tasks WHERE plan_id=? AND week=? ORDER BY day", (plan_id, w["number"]))]
            weeks.append(WeeklyPlan(week_number=w["number"], theme=w["theme"], milestone=w["milestone"], tasks=tasks))
        return {"profile": profile, "plan": StudyPlan(title=row["title"], overview=row["overview"], weekly_plans=weeks, learning_tips=json.loads(row["tips_json"]))}

    def _import_legacy_json(self, data, start, weekdays=None):
        profile = DesktopProfile.model_validate(data["profile"])
        plan = StudyPlan.model_validate(data["plan"])
        validate_plan_for_profile(plan, profile)
        # 在临时数据库完成所有校验，旧版错误文件不会留下半份计划。
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            temporary = Store(Path(directory))
            plan_id = temporary.save_plan(profile, plan, start, weekdays or list(range(profile.days_per_week)))
            seen = set()
            for item in data["progress"]:
                key = (int(item["week"]), int(item["day"]))
                if key in seen:
                    raise ValueError("记录重复")
                seen.add(key)
                record = TaskProgress.model_validate(item["record"])
                rows = temporary.read("SELECT id FROM tasks WHERE plan_id=? AND week=? AND day=?", (plan_id, *key))
                if not rows:
                    raise ValueError("学习日不存在")
                with temporary.write() as c:
                    c.execute("UPDATE tasks SET status=?,manual_seconds=?,difficulty=?,note=? WHERE id=?",
                              (record.status, record.actual_minutes * 60, record.difficulty, record.note, rows[0]["id"]))
            for review in data["reviews"]:
                week = int(review["week"])
                records = [t for t in temporary.tasks(plan_id) if t["week"] == week]
                if not records or any(t["status"] not in {"已完成", "延期"} for t in records):
                    raise ValueError("复盘缺少最终记录")
                with temporary.write() as c:
                    c.execute("INSERT INTO reviews VALUES (?,?,?)", (plan_id, week, str(review["summary"])[:1000]))
            snapshot = temporary._snapshot(plan_id)
        with self.write() as c:
            plan_id = self._insert_snapshot(c, snapshot, new_ids=True)
            self._version(c, plan_id, "导入旧版计划", {})
            return plan_id

    def import_legacy_database(self, path: Path, start: date):
        # 只读连接，绝不在源库上初始化 schema 或写入。
        from plan_transfer import export_bundle
        from progress_store import SavedPlan
        source = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        source.row_factory = sqlite3.Row
        bundles = []
        try:
            for row in source.execute("SELECT * FROM plans"):
                profile = DesktopProfile.model_validate_json(row["profile_json"])
                plan = StudyPlan.model_validate_json(row["plan_json"])
                saved = SavedPlan(row["id"], profile, plan, row["created_at"], row["updated_at"])
                progress = {(r["week_number"], r["day"]): TaskProgress.model_validate_json(r["progress_json"])
                            for r in source.execute("SELECT * FROM task_progress WHERE plan_id=?", (row["id"],))}
                reviews = {r["week_number"]: r["summary"] for r in source.execute("SELECT * FROM weekly_reviews WHERE plan_id=?", (row["id"],))}
                bundles.append(export_bundle(saved, progress, reviews))
        except (sqlite3.Error, ValueError) as error:
            raise ValueError("请选择旧版 plans.sqlite3 数据库") from error
        finally:
            source.close()
        # 先在隔离库验证整批数据，再一次事务插入，避免部分导入。
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            temporary = Store(Path(directory))
            ids = [temporary.import_plan(bundle, start) for bundle in bundles]
            snapshots = [temporary._snapshot(pid) for pid in ids]
        with self.write() as c:
            ids = [self._insert_snapshot(c, snapshot, new_ids=True) for snapshot in snapshots]
            for plan_id in ids:
                self._version(c, plan_id, "导入旧版计划", {})
            return ids

    def _event(self, c, plan_id, kind, provenance):
        c.execute("INSERT INTO generation_events VALUES (?,?,?,?,?,?,?)", (new_id(), plan_id,
                  provenance.get("connection_name", ""), provenance.get("model", ""), kind,
                  json.dumps(provenance.get("metrics", {})), now()))

    def apply_review(self, plan_id, week, summary, adjusted: StudyPlan | None, expected_revision: int, provenance=None):
        saved = self.get_plan(plan_id)
        if saved["revision"] != expected_revision:
            raise ValueError("计划已有变化，请重新生成调整预览")
        if week in self.reviews(plan_id) or any(w not in self.reviews(plan_id) for w in range(1, week)):
            raise ValueError("请按顺序复盘，每周只能应用一次")
        if any(t["status"] not in {"已完成", "延期"} for t in saved["tasks"] if t["week"] == week):
            raise ValueError("请先将本周任务标为已完成或延期")
        if adjusted:
            validate_plan_for_profile(adjusted, saved["profile"])
            if adjusted.weekly_plans[:week] != saved["plan"].weekly_plans[:week]:
                raise ValueError("调整不能改写已学习的周次")
            candidate = {(w.week_number, t.day): t for w in adjusted.weekly_plans for t in w.tasks}
            daily, weekly = {}, {}
            for row in saved["tasks"]:
                minutes = row["task"].duration_minutes if row["manual"] else candidate[(row["week"], row["day"])].duration_minutes
                daily[row["due_date"]] = daily.get(row["due_date"], 0) + minutes
                key = date.fromisoformat(row["due_date"]).isocalendar()[:2]
                weekly[key] = weekly.get(key, 0) + minutes
            if any(value > saved["profile"].minutes_per_day for value in daily.values()) or any(value > saved["profile"].weekly_minutes for value in weekly.values()):
                raise ValueError("调整与已改期的任务合并后超过时间上限，请先重新安排日期")
        elif week != saved["profile"].plan_weeks:
            raise ValueError("还有后续周次，必须提供调整方案")
        snapshot = self._snapshot(plan_id)
        with self.write() as c:
            # BEGIN IMMEDIATE 后再比较，避免预览之后被计时或编辑覆盖。
            if c.execute("SELECT revision FROM plans WHERE id=?", (plan_id,)).fetchone()[0] != expected_revision:
                raise ValueError("计划已有变化，请重新预览")
            if adjusted:
                for future in adjusted.weekly_plans[week:]:
                    c.execute("UPDATE weeks SET theme=?,milestone=? WHERE plan_id=? AND number=?", (future.theme, future.milestone, plan_id, future.week_number))
                    for task in future.tasks:
                        old = next(t for t in saved["tasks"] if (t["week"], t["day"]) == (future.week_number, task.day))
                        from desktop.planning import protection_reason
                        if protection_reason(old, self.reviews(plan_id)):
                            if task != old["task"]:
                                raise ValueError("调整不能覆盖已开始或手动修改的任务")
                        else:
                            c.execute("UPDATE tasks SET payload_json=? WHERE id=?", (task.model_dump_json(), old["id"]))
            c.execute("INSERT INTO reviews VALUES (?,?,?)", (plan_id, week, summary))
            self._touch(c, plan_id)
            c.execute("INSERT OR REPLACE INTO undo VALUES (?,?,?)", (plan_id, json.dumps(snapshot, ensure_ascii=False), expected_revision + 1))
            if provenance:
                self._event(c, plan_id, "复盘调整", provenance)
            self._version(c, plan_id, "周复盘调整", snapshot)

    def undo_review(self, plan_id, *, confirm_limits=False):
        if self.versions(plan_id):
            return self.undo_latest(plan_id, confirm_limits=confirm_limits)
        saved = self.get_plan(plan_id)
        rows = self.read("SELECT * FROM undo WHERE plan_id=?", (plan_id,))
        if not rows or rows[0]["expected_revision"] != saved["revision"]:
            raise ValueError("没有可撤销的调整，或调整后已有新记录；为保留新记录，不能直接撤销")
        with self.write() as c:
            c.execute("DELETE FROM focus_sessions WHERE task_id IN (SELECT id FROM tasks WHERE plan_id=?)", (plan_id,))
            c.execute("DELETE FROM generation_events WHERE plan_id=?", (plan_id,))
            c.execute("DELETE FROM plans WHERE id=?", (plan_id,))
            self._insert_snapshot(c, json.loads(rows[0]["snapshot_json"]))

    def record_focus(self, session_id, task_id, seconds, started_at, *, day_seconds=None):
        seconds = max(0, int(seconds))
        with self.write() as c:
            row = c.execute("SELECT * FROM focus_sessions WHERE id=?", (session_id,)).fetchone()
            if row and row["task_id"] != task_id:
                raise ValueError("专注记录所属任务不一致")
            previous = row["seconds"] if row else 0
            if seconds < previous:
                raise ValueError("累计专注时间不能倒退")
            if day_seconds is not None:
                if seconds == previous and row:
                    prefix=f'focus/{session_id}/{seconds}/'
                    saved = {r['occurred_on']: r['seconds'] for r in c.execute(
                        'SELECT occurred_on,seconds FROM study_events WHERE substr(id,1,?)=?', (len(prefix),prefix))}
                    if saved == {k:v for k,v in day_seconds.items() if v}:
                        return  # Retrying the same checkpoint must not duplicate dated evidence.
                if sum(day_seconds.values()) != seconds - previous or any(type(v) is not int or v < 0 for v in day_seconds.values()):
                    raise ValueError('专注日期分段与累计用时不一致')
                for day, amount in day_seconds.items():
                    date.fromisoformat(day)
                    if amount:
                        c.execute('INSERT INTO study_events VALUES (?,?,?,?,?,?)',
                            (f'focus/{session_id}/{seconds}/{day}', task_id, day, amount, 'focus', now()))
            c.execute("INSERT INTO focus_sessions VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET seconds=excluded.seconds,updated_at=excluded.updated_at",
                      (session_id, task_id, seconds, started_at, now()))
            if task_id:
                task = c.execute("SELECT plan_id FROM tasks WHERE id=?", (task_id,)).fetchone()
                if not task:
                    raise ValueError("关联任务已删除")
                c.execute("""UPDATE tasks SET focus_seconds=focus_seconds+?,
                    status=CASE WHEN status='未开始' AND focus_seconds+?>0 THEN '进行中' ELSE status END WHERE id=?""",
                    (seconds - previous, seconds - previous, task_id))
                if seconds != previous:
                    self._touch(c, task["plan_id"])

    def due_reminders(self, moment: datetime):
        due = []
        for task in self.tasks(due=moment.date().isoformat()):
            if not task["remind_at"]:
                continue
            scheduled = datetime.fromisoformat(f"{task['due_date']}T{task['remind_at']}")
            if self.read("SELECT 1 FROM pause_history WHERE plan_id=? AND start_date<=? AND ?<COALESCE(ended_date,resume_date)",
                         (task["plan_id"], task["due_date"], task["due_date"])):
                continue
            fingerprint = scheduled.isoformat()
            if scheduled <= moment.replace(tzinfo=None) and not self.read("SELECT 1 FROM notifications WHERE task_id=? AND fingerprint=?", (task["id"], fingerprint)):
                due.append(task)
        if due:
            with self.write() as c:
                for task in due:
                    fingerprint = datetime.fromisoformat(f"{task['due_date']}T{task['remind_at']}").isoformat()
                    c.execute("INSERT OR IGNORE INTO notifications VALUES (?,?,?)", (task["id"], fingerprint, now()))
        return due

    def backup_to(self, target: Path):
        target = Path(target)
        if target.resolve() == self.path.resolve():
            raise ValueError("备份文件不能覆盖正在使用的数据文件")
        target.parent.mkdir(parents=True, exist_ok=True)
        source = self.connect()
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            source.close()
            destination.close()
        return target

    def manual_backup(self, prefix="manual"):
        return self.backup_to(self.backups / f"{prefix}-{datetime.now():%Y%m%d-%H%M%S}-{uuid4().hex[:6]}.sqlite3")

    @staticmethod
    def inspect_backup(path: Path):
        c = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        try:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            if c.execute("PRAGMA application_id").fetchone()[0] != APP_ID or version not in range(1, SCHEMA_VERSION + 1):
                raise ValueError("不是当前版本的学习罗盘备份")
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or c.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("备份不完整或已损坏")
            for row in c.execute("SELECT id FROM plans"):
                saved = Store.get_plan_in_transaction(c, row["id"])
                validate_plan_for_profile(saved["plan"], saved["profile"])
                tasks = c.execute("SELECT * FROM tasks WHERE plan_id=?", (row["id"],)).fetchall()
                if sum(not (t["manual"] if version >= 5 else 0) for t in tasks) != saved["profile"].plan_weeks * saved["profile"].days_per_week:
                    raise ValueError("备份任务数量不一致")
                for task in tasks:
                    date.fromisoformat(task["due_date"])
                    if task["remind_at"]:
                        datetime.strptime(task["remind_at"], "%H:%M")
                    TaskProgress(status=task["status"], actual_minutes=0, difficulty=task["difficulty"], note=task["note"])
                    if task["manual_seconds"] < 0 or task["focus_seconds"] < 0:
                        raise ValueError("备份时间记录不正确")
            if version >= 5:
                for task in c.execute("SELECT * FROM tasks WHERE manual=1"):
                    payload = DailyTask.model_validate_json(task["payload_json"])
                    if task["week"] != 0 or task["day"] is not None or payload.day != 1:
                        raise ValueError("手动任务编号不正确")
                    date.fromisoformat(task["due_date"])
                    if task["remind_at"]:
                        datetime.strptime(task["remind_at"], "%H:%M")
                    TaskProgress(status=task["status"], actual_minutes=0, difficulty=task["difficulty"], note=task["note"])
                    if task["manual_seconds"] < 0 or task["focus_seconds"] < 0:
                        raise ValueError("手动任务用时不能为负数")
            if version >= 6:
                from desktop.learning_tools import validate_database_extras
                validate_database_extras(c)
            if version >= 7:
                from desktop.trash import validate_entry
                for entry in c.execute("SELECT * FROM trash"):
                    validate_entry(c, dict(entry))
            if version >= 8:
                from desktop.organizer import validate_database
                validate_database(c)
            for row in c.execute("SELECT data_json FROM connections"):
                ModelConnection.model_validate_json(row["data_json"])
            if version >= 4:
                from desktop.scheduling import budget_values
                for row in c.execute("SELECT id FROM plans"):
                    Store._validate_schedule(c, row[0])
                budget = c.execute("SELECT value_json FROM settings WHERE key='daily_budget'").fetchone()
                budget_values(json.loads(budget[0]) if budget else None)
            if version >= 2:
                from desktop.planning import PlanningDraft, validate_brief
                for row in c.execute("SELECT data_json FROM planning_drafts"):
                    PlanningDraft.model_validate_json(row["data_json"])
                if version >= 3:
                    for row in c.execute("SELECT created_at,updated_at FROM planning_drafts"):
                        for stamp in row:
                            if stamp:
                                datetime.fromisoformat(stamp)
                for row in c.execute("SELECT ai_locked FROM tasks"):
                    if row[0] not in (0, 1):
                        raise ValueError("无效的任务锁定状态")
                for row in c.execute("SELECT data_json FROM plan_briefs"):
                    validate_brief(json.loads(row[0]))
                for row in c.execute("SELECT plan_id,before_json,after_json FROM plan_versions"):
                    for encoded in (row["before_json"], row["after_json"]):
                        snapshot = json.loads(encoded)
                        if snapshot == {}:
                            continue  # Creation has no previous version.
                        Store._normalize_snapshot(snapshot)
                        if snapshot.get("format") != "learning-compass-plan" or len(snapshot["plans"]) != 1 or snapshot["plans"][0]["id"] != row["plan_id"]:
                            raise ValueError("历史所属计划不一致")
                        from desktop.learning_tools import validate_snapshot_extras
                        validate_snapshot_extras(snapshot)
                        for task in snapshot["tasks"]:
                            DailyTask.model_validate_json(task["payload_json"])
                            date.fromisoformat(task["due_date"])
                            if task["plan_id"] != row["plan_id"] or task["ai_locked"] not in (0, 1):
                                raise ValueError("历史任务不完整")
                        for brief in snapshot["plan_briefs"]:
                            validate_brief(json.loads(brief["data_json"]))
            return {table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("plans", "tasks", "connections")}
        except (sqlite3.Error, ValueError, TypeError, KeyError) as error:
            raise ValueError("无法读取备份文件，可能已损坏或不属于当前桌面版本") from error
        finally:
            c.close()

    def restore(self, path: Path):
        summary = self.inspect_backup(path)
        if Path(path).resolve() == self.path.resolve():
            raise ValueError("不能从当前正在使用的数据文件恢复")
        self.manual_backup("before-restore")
        temporary = self.directory / f"restore-{new_id()}.sqlite3"
        try:
            source = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
            target = sqlite3.connect(temporary)
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
            self.inspect_backup(temporary)
            # Migrate only the staging copy; the selected source is never edited.
            with closing(sqlite3.connect(temporary)) as staged, staged:
                if staged.execute("PRAGMA user_version").fetchone()[0] == 1:
                    self._upgrade_v2(staged)
                if staged.execute("PRAGMA user_version").fetchone()[0] == 2:
                    self._upgrade_v3(staged)
                if staged.execute("PRAGMA user_version").fetchone()[0] == 3:
                    self._upgrade_v4(staged)
                if staged.execute("PRAGMA user_version").fetchone()[0] == 4:
                    staged.commit()
                    self._upgrade_v5(staged)
                if staged.execute("PRAGMA user_version").fetchone()[0] == 5:
                    self._upgrade_v6(staged)
                if staged.execute("PRAGMA user_version").fetchone()[0] == 6:
                    self._upgrade_v7(staged)
                if staged.execute("PRAGMA user_version").fetchone()[0] == 7:
                    self._upgrade_v8(staged)
            self.inspect_backup(temporary)
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self._backed_up_date = ""
        return summary
