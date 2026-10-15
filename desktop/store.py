"""桌面版事务存储。安装目录中不保存用户数据或密钥。"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from models import DailyTask, StudyPlan, WeeklyPlan, validate_plan_for_profile
from progress_store import StudyRepository, TaskProgress
from desktop.models import DesktopProfile, ModelConnection, new_id, schedule_dates

APP_ID = 0x4C434D50
SCHEMA_VERSION = 1


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
        self._initialize()

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self):
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
            c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        c.close()

    def read(self, sql, params=()):
        c = self.connect()
        try:
            return [dict(row) for row in c.execute(sql, params).fetchall()]
        finally:
            c.close()

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
    def write(self):
        self._daily_backup()
        c = self.connect()
        try:
            with c:
                yield c
        finally:
            c.close()

    def _touch(self, c, plan_id):
        c.execute("UPDATE plans SET revision=revision+1, updated_at=? WHERE id=?", (now(), plan_id))

    def setting(self, key, default=None):
        rows = self.read("SELECT value_json FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value_json"]) if rows else default

    def set_setting(self, key, value):
        with self.write() as c:
            c.execute("INSERT INTO settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
                      (key, json.dumps(value, ensure_ascii=False)))

    def connections(self) -> list[ModelConnection]:
        return [ModelConnection.model_validate_json(r["data_json"]) for r in self.read("SELECT * FROM connections ORDER BY rowid")]

    def save_connection(self, connection: ModelConnection):
        with self.write() as c:
            c.execute("INSERT INTO connections VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data_json=excluded.data_json",
                      (connection.id, connection.model_dump_json()))

    def delete_connection(self, connection_id):
        with self.write() as c:
            c.execute("DELETE FROM connections WHERE id=?", (connection_id,))

    def save_plan(self, profile: DesktopProfile, plan: StudyPlan, start: date, weekdays: list[int], *, provenance=None):
        validate_plan_for_profile(plan, profile)
        if len(weekdays) != profile.days_per_week:
            raise ValueError("学习日数量与计划不一致")
        dates = schedule_dates(start, weekdays, profile.plan_weeks)
        plan_id = new_id()
        with self.write() as c:
            c.execute("INSERT INTO plans VALUES (?,?,?,?,?,?,?,0,0,?,?)", (
                plan_id, profile.model_dump_json(), plan.title, plan.overview,
                json.dumps(plan.learning_tips, ensure_ascii=False), start.isoformat(), json.dumps(weekdays), now(), now()))
            for week in plan.weekly_plans:
                c.execute("INSERT INTO weeks VALUES (?,?,?,?)", (plan_id, week.week_number, week.theme, week.milestone))
                for task in week.tasks:
                    c.execute("INSERT INTO tasks (id,plan_id,week,day,payload_json,due_date,remind_at) VALUES (?,?,?,?,?,?,?)",
                              (new_id(), plan_id, week.week_number, task.day, task.model_dump_json(), dates[(week.week_number, task.day)], "20:00"))
            if provenance:
                self._event(c, plan_id, "生成", provenance)
        return plan_id

    def list_plans(self, search="", archived=False):
        return self.read("""SELECT p.*, (SELECT count(*) FROM tasks t WHERE t.plan_id=p.id) AS total,
            (SELECT count(*) FROM tasks t WHERE t.plan_id=p.id AND t.status='已完成') AS completed
            FROM plans p WHERE archived=? AND (title LIKE ? OR profile_json LIKE ?) ORDER BY updated_at DESC""",
                         (int(archived), f"%{search}%", f"%{search}%"))

    def tasks(self, plan_id=None, *, due=None):
        sql = "SELECT t.*,p.title AS plan_title,p.archived FROM tasks t JOIN plans p ON p.id=t.plan_id WHERE 1=1"
        params = []
        if plan_id:
            sql += " AND t.plan_id=?"
            params.append(plan_id)
        if due:
            sql += " AND t.due_date<=? AND p.archived=0 AND t.status!='已完成'"
            params.append(due)
        rows = self.read(sql + " ORDER BY t.due_date,t.week,t.day", params)
        for row in rows:
            row["task"] = DailyTask.model_validate_json(row["payload_json"])
        return rows

    def get_plan(self, plan_id):
        rows = self.read("SELECT * FROM plans WHERE id=?", (plan_id,))
        if not rows:
            raise ValueError("找不到这份计划")
        row = rows[0]
        row["profile"] = DesktopProfile.model_validate_json(row["profile_json"])
        row["tasks"] = self.tasks(plan_id)
        weeks = [WeeklyPlan(week_number=w["number"], theme=w["theme"], milestone=w["milestone"],
                    tasks=[t["task"] for t in sorted(row["tasks"], key=lambda t: t["day"]) if t["week"] == w["number"]])
                 for w in self.read("SELECT * FROM weeks WHERE plan_id=? ORDER BY number", (plan_id,))]
        row["plan"] = StudyPlan(title=row["title"], overview=row["overview"], weekly_plans=weeks, learning_tips=json.loads(row["tips_json"]))
        return row

    def rename_plan(self, plan_id, title):
        if not 2 <= len(title.strip()) <= 100:
            raise ValueError("计划名称需要 2–100 个字")
        with self.write() as c:
            c.execute("UPDATE plans SET title=? WHERE id=?", (title.strip(), plan_id))
            self._touch(c, plan_id)

    def archive(self, plan_id, archived=True):
        with self.write() as c:
            c.execute("UPDATE plans SET archived=? WHERE id=?", (int(archived), plan_id))
            self._touch(c, plan_id)

    def delete_plan(self, plan_id):
        with self.write() as c:
            c.execute("DELETE FROM focus_sessions WHERE task_id IN (SELECT id FROM tasks WHERE plan_id=?)", (plan_id,))
            c.execute("DELETE FROM generation_events WHERE plan_id=?", (plan_id,))
            c.execute("DELETE FROM plans WHERE id=?", (plan_id,))

    def update_task(self, task_id, payload: DailyTask, due_date: str, remind_at: str | None,
                    status: str, manual_minutes: int, difficulty, note: str):
        task = next(iter(self.read("SELECT * FROM tasks WHERE id=?", (task_id,))), None)
        if not task:
            raise ValueError("找不到这项任务")
        date.fromisoformat(due_date)
        if remind_at is not None:
            datetime.strptime(remind_at, "%H:%M")
        TaskProgress(status=status, actual_minutes=manual_minutes, difficulty=difficulty, note=note)
        saved = self.get_plan(task["plan_id"])
        if payload.day != task["day"] or payload.duration_minutes > saved["profile"].minutes_per_day:
            raise ValueError("任务时长超过该计划的单日上限")
        old = DailyTask.model_validate_json(task["payload_json"])
        protected = (old != payload or task["due_date"] != due_date or task["remind_at"] != remind_at)
        if self.read("SELECT 1 FROM reviews WHERE plan_id=? AND week=?", (task["plan_id"], task["week"])):
            if old != payload or task["status"] != status:
                raise ValueError("已复盘周次不能修改任务或完成状态；可以补记笔记和实际用时")
        others = [t for t in saved["tasks"] if t["id"] != task_id and t["due_date"] == due_date]
        if sum(t["task"].duration_minutes for t in others) + payload.duration_minutes > saved["profile"].minutes_per_day:
            raise ValueError("该日期已超过这份计划的单日时间上限，请换一天或缩短任务")
        target_week = date.fromisoformat(due_date).isocalendar()[:2]
        actual_week_minutes = sum(t["task"].duration_minutes for t in saved["tasks"]
                                  if t["id"] != task_id and date.fromisoformat(t["due_date"]).isocalendar()[:2] == target_week)
        if actual_week_minutes + payload.duration_minutes > saved["profile"].weekly_minutes:
            raise ValueError("改期后超过这份计划的每周时间上限，请换一周安排")
        with self.write() as c:
            c.execute("""UPDATE tasks SET payload_json=?,due_date=?,remind_at=?,status=?,manual_seconds=?,
                difficulty=?,note=?,user_modified=MAX(user_modified,?) WHERE id=?""",
                      (payload.model_dump_json(), due_date, remind_at, status, manual_minutes * 60,
                       difficulty, note, int(protected), task_id))
            self._touch(c, task["plan_id"])

    def progress(self, plan_id):
        return {(t["week"], t["day"]): TaskProgress(status=t["status"],
                actual_minutes=min(1440, (t["manual_seconds"] + t["focus_seconds"]) // 60),
                difficulty=t["difficulty"], note=t["note"]) for t in self.tasks(plan_id)}

    def reviews(self, plan_id):
        return {r["week"]: r["summary"] for r in self.read("SELECT * FROM reviews WHERE plan_id=?", (plan_id,))}

    def _snapshot(self, plan_id):
        result = {"format": "learning-compass-plan", "version": 2}
        for table in ("plans", "weeks", "tasks", "reviews", "generation_events", "focus_sessions", "notifications"):
            if table == "plans":
                rows = self.read("SELECT * FROM plans WHERE id=?", (plan_id,))
            elif table in {"focus_sessions", "notifications"}:
                rows = self.read(f"SELECT * FROM {table} WHERE task_id IN (SELECT id FROM tasks WHERE plan_id=?)", (plan_id,))
            else:
                rows = self.read(f"SELECT * FROM {table} WHERE plan_id=?", (plan_id,))
            result[table] = rows
        return result

    def export_plan(self, plan_id) -> bytes:
        return json.dumps(self._snapshot(plan_id), ensure_ascii=False, indent=2).encode("utf-8")

    @staticmethod
    def _insert_snapshot(c, snapshot, *, new_ids=False):
        snapshot = json.loads(json.dumps(snapshot))
        if new_ids:
            plan_id = new_id()
            task_ids = {r["id"]: new_id() for r in snapshot["tasks"]}
            snapshot["plans"][0]["id"] = plan_id
            for table in ("weeks", "tasks", "reviews", "generation_events"):
                for row in snapshot[table]:
                    row["plan_id"] = plan_id
                    if table == "tasks":
                        row["id"] = task_ids[row["id"]]
                    elif table == "generation_events":
                        row["id"] = new_id()
            for table in ("focus_sessions", "notifications"):
                for row in snapshot[table]:
                    row["task_id"] = task_ids[row["task_id"]]
                    if table == "focus_sessions":
                        row["id"] = new_id()
        for table in ("plans", "weeks", "tasks", "reviews", "generation_events", "focus_sessions", "notifications"):
            for row in snapshot[table]:
                columns = list(row)
                # 列名来自本机 schema 的白名单，不能执行导入文件提供的 SQL 名称。
                allowed = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
                if set(columns) != allowed:
                    raise ValueError("备份字段不完整或版本不匹配")
                c.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", list(row.values()))
        return snapshot["plans"][0]["id"]

    def import_plan(self, content: bytes, start: date, weekdays: list[int] | None = None):
        if len(content) > 20_000_000:
            raise ValueError("计划文件过大")
        try:
            data = json.loads(content.decode("utf-8-sig"))
            if data.get("version") == 1:
                return self._import_legacy_json(data, start, weekdays)
            if data.get("format") != "learning-compass-plan" or data.get("version") != 2 or len(data["plans"]) != 1:
                raise ValueError("不是有效的学习罗盘计划文件")
            # 同一事务中验证领域约束；任何失败都会回滚整份导入。
            with self.write() as c:
                plan_id = self._insert_snapshot(c, data, new_ids=True)
                saved = self.get_plan_in_transaction(c, plan_id)
                validate_plan_for_profile(saved["plan"], saved["profile"])
                metadata = c.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
                weekdays = json.loads(metadata["weekdays_json"])
                schedule_dates(date.fromisoformat(metadata["start_date"]), weekdays, saved["profile"].plan_weeks)
                if len(weekdays) != saved["profile"].days_per_week:
                    raise ValueError("每周学习日数量不一致")
                imported_tasks = c.execute("SELECT * FROM tasks WHERE plan_id=?", (plan_id,)).fetchall()
                if len(imported_tasks) != saved["profile"].plan_weeks * saved["profile"].days_per_week:
                    raise ValueError("任务数量不一致")
                for task in imported_tasks:
                    date.fromisoformat(task["due_date"])
                    if task["remind_at"] is not None:
                        datetime.strptime(task["remind_at"], "%H:%M")
                    if task["week"] not in range(1, saved["profile"].plan_weeks + 1) or DailyTask.model_validate_json(task["payload_json"]).day != task["day"]:
                        raise ValueError("任务编号不一致")
                    TaskProgress(status=task["status"], actual_minutes=min(1440, task["manual_seconds"] // 60), difficulty=task["difficulty"], note=task["note"])
                    if task["manual_seconds"] < 0 or task["focus_seconds"] < 0:
                        raise ValueError("用时不能为负数")
            return plan_id
        except (ValueError, TypeError, KeyError, IndexError, sqlite3.Error) as error:
            raise ValueError("无法导入，请选择由学习罗盘导出的完整计划 JSON") from error

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
            return self._insert_snapshot(c, snapshot, new_ids=True)

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
            return [self._insert_snapshot(c, snapshot, new_ids=True) for snapshot in snapshots]

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
                minutes = candidate[(row["week"], row["day"])].duration_minutes
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
            c.execute("BEGIN IMMEDIATE")
            if c.execute("SELECT revision FROM plans WHERE id=?", (plan_id,)).fetchone()[0] != expected_revision:
                raise ValueError("计划已有变化，请重新预览")
            if adjusted:
                for future in adjusted.weekly_plans[week:]:
                    c.execute("UPDATE weeks SET theme=?,milestone=? WHERE plan_id=? AND number=?", (future.theme, future.milestone, plan_id, future.week_number))
                    for task in future.tasks:
                        old = next(t for t in saved["tasks"] if (t["week"], t["day"]) == (future.week_number, task.day))
                        if old["user_modified"] or old["status"] != "未开始" or old["focus_seconds"] or old["manual_seconds"] or old["note"] or old["difficulty"] is not None:
                            if task != old["task"]:
                                raise ValueError("调整不能覆盖已开始或手动修改的任务")
                        else:
                            c.execute("DELETE FROM tasks WHERE id=?", (old["id"],))
                            c.execute("INSERT INTO tasks (id,plan_id,week,day,payload_json,due_date,remind_at) VALUES (?,?,?,?,?,?,?)",
                                      (new_id(), plan_id, future.week_number, task.day, task.model_dump_json(), old["due_date"], old["remind_at"]))
            c.execute("INSERT INTO reviews VALUES (?,?,?)", (plan_id, week, summary))
            self._touch(c, plan_id)
            c.execute("INSERT OR REPLACE INTO undo VALUES (?,?,?)", (plan_id, json.dumps(snapshot, ensure_ascii=False), expected_revision + 1))
            if provenance:
                self._event(c, plan_id, "复盘调整", provenance)

    def undo_review(self, plan_id):
        saved = self.get_plan(plan_id)
        rows = self.read("SELECT * FROM undo WHERE plan_id=?", (plan_id,))
        if not rows or rows[0]["expected_revision"] != saved["revision"]:
            raise ValueError("没有可撤销的调整，或调整后已有新记录；为保留新记录，不能直接撤销")
        with self.write() as c:
            c.execute("DELETE FROM focus_sessions WHERE task_id IN (SELECT id FROM tasks WHERE plan_id=?)", (plan_id,))
            c.execute("DELETE FROM generation_events WHERE plan_id=?", (plan_id,))
            c.execute("DELETE FROM plans WHERE id=?", (plan_id,))
            self._insert_snapshot(c, json.loads(rows[0]["snapshot_json"]))

    def record_focus(self, session_id, task_id, seconds, started_at):
        seconds = max(0, int(seconds))
        with self.write() as c:
            row = c.execute("SELECT * FROM focus_sessions WHERE id=?", (session_id,)).fetchone()
            if row and row["task_id"] != task_id:
                raise ValueError("专注记录所属任务不一致")
            previous = row["seconds"] if row else 0
            if seconds < previous:
                raise ValueError("累计专注时间不能倒退")
            c.execute("INSERT INTO focus_sessions VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET seconds=excluded.seconds,updated_at=excluded.updated_at",
                      (session_id, task_id, seconds, started_at, now()))
            if task_id:
                task = c.execute("SELECT plan_id FROM tasks WHERE id=?", (task_id,)).fetchone()
                if not task:
                    raise ValueError("关联任务已删除")
                c.execute("UPDATE tasks SET focus_seconds=focus_seconds+? WHERE id=?", (seconds - previous, task_id))
                if seconds != previous:
                    self._touch(c, task["plan_id"])

    def due_reminders(self, moment: datetime):
        due = []
        for task in self.tasks(due=moment.date().isoformat()):
            if not task["remind_at"]:
                continue
            scheduled = datetime.fromisoformat(f"{task['due_date']}T{task['remind_at']}")
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
            if c.execute("PRAGMA application_id").fetchone()[0] != APP_ID or c.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                raise ValueError("不是当前版本的学习罗盘备份")
            if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or c.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("备份不完整或已损坏")
            for row in c.execute("SELECT id FROM plans"):
                saved = Store.get_plan_in_transaction(c, row["id"])
                validate_plan_for_profile(saved["plan"], saved["profile"])
                tasks = c.execute("SELECT * FROM tasks WHERE plan_id=?", (row["id"],)).fetchall()
                if len(tasks) != saved["profile"].plan_weeks * saved["profile"].days_per_week:
                    raise ValueError("备份任务数量不一致")
                for task in tasks:
                    date.fromisoformat(task["due_date"])
                    if task["remind_at"]:
                        datetime.strptime(task["remind_at"], "%H:%M")
                    TaskProgress(status=task["status"], actual_minutes=0, difficulty=task["difficulty"], note=task["note"])
                    if task["manual_seconds"] < 0 or task["focus_seconds"] < 0:
                        raise ValueError("备份时间记录不正确")
            for row in c.execute("SELECT data_json FROM connections"):
                ModelConnection.model_validate_json(row["data_json"])
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
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
        self._backed_up_date = ""
        return summary
