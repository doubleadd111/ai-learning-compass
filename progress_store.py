"""本机学习记录：使用 SQLite 保存计划、每日进度和每周复盘。"""

from __future__ import annotations

import sqlite3
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from models import LearningProfile, StudyPlan, WeeklyPlan, validate_plan_for_profile


DEFAULT_DB_PATH = Path(__file__).parent / ".learning_compass" / "plans.sqlite3"
TaskStatus = Literal["未开始", "进行中", "已完成", "延期"]


class TaskProgress(BaseModel):
    status: TaskStatus = "未开始"
    actual_minutes: int = Field(default=0, ge=0, le=1440)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    note: str = Field(default="", max_length=500)


@dataclass(frozen=True)
class SavedPlan:
    id: str
    profile: LearningProfile
    plan: StudyPlan
    created_at: str
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StudyRepository:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or os.getenv("STUDY_DB_PATH", DEFAULT_DB_PATH))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS plans (
                    id TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS task_progress (
                    plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
                    week_number INTEGER NOT NULL,
                    day INTEGER NOT NULL,
                    progress_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (plan_id, week_number, day)
                );
                CREATE TABLE IF NOT EXISTS weekly_reviews (
                    plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
                    week_number INTEGER NOT NULL,
                    summary TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (plan_id, week_number)
                );
                CREATE TABLE IF NOT EXISTS generation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    plan_id TEXT REFERENCES plans(id) ON DELETE SET NULL,
                    kind TEXT NOT NULL,
                    success INTEGER NOT NULL,
                    latency_ms INTEGER NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    error_category TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

    def save_plan(self, profile: LearningProfile, plan: StudyPlan) -> str:
        validate_plan_for_profile(plan, profile)
        plan_id = uuid4().hex
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO plans VALUES (?, ?, ?, ?, ?)",
                (plan_id, profile.model_dump_json(), plan.model_dump_json(), now, now),
            )
        return plan_id

    def load_plan(self, plan_id: str) -> SavedPlan | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM plans WHERE id = ?", (plan_id,)
            ).fetchone()
        if row is None:
            return None
        return SavedPlan(
            id=row["id"],
            profile=LearningProfile.model_validate_json(row["profile_json"]),
            plan=StudyPlan.model_validate_json(row["plan_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_plans(self, limit: int = 20) -> list[SavedPlan]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM plans ORDER BY updated_at DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            SavedPlan(
                id=row["id"],
                profile=LearningProfile.model_validate_json(row["profile_json"]),
                plan=StudyPlan.model_validate_json(row["plan_json"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            for row in rows
        ]

    def save_progress(
        self, plan_id: str, week_number: int, day: int, progress: TaskProgress
    ) -> None:
        saved = self.load_plan(plan_id)
        if saved is None:
            raise ValueError("找不到这份计划")
        if not any(
            week.week_number == week_number and any(task.day == day for task in week.tasks)
            for week in saved.plan.weekly_plans
        ):
            raise ValueError("计划中没有这个学习日")
        if week_number in self.get_reviews(plan_id):
            raise ValueError("本周已经复盘，不能再修改记录")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO task_progress VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(plan_id, week_number, day) DO UPDATE SET
                progress_json = excluded.progress_json, updated_at = excluded.updated_at""",
                (plan_id, week_number, day, progress.model_dump_json(), now),
            )
            connection.execute(
                "UPDATE plans SET updated_at = ? WHERE id = ?", (now, plan_id)
            )

    def get_progress(self, plan_id: str) -> dict[tuple[int, int], TaskProgress]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT week_number, day, progress_json FROM task_progress WHERE plan_id = ?",
                (plan_id,),
            ).fetchall()
        return {
            (row["week_number"], row["day"]): TaskProgress.model_validate_json(
                row["progress_json"]
            )
            for row in rows
        }

    def save_review(self, plan_id: str, week_number: int, summary: str) -> None:
        if self.load_plan(plan_id) is None:
            raise ValueError("找不到这份计划")
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO weekly_reviews VALUES (?, ?, ?, ?)
                ON CONFLICT(plan_id, week_number) DO UPDATE SET
                summary = excluded.summary, updated_at = excluded.updated_at""",
                (plan_id, week_number, summary, now),
            )

    def get_reviews(self, plan_id: str) -> dict[int, str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT week_number, summary FROM weekly_reviews WHERE plan_id = ?",
                (plan_id,),
            ).fetchall()
        return {row["week_number"]: row["summary"] for row in rows}

    def replace_future_weeks(
        self, plan_id: str, after_week: int, future: list[WeeklyPlan]
    ) -> None:
        saved = self.load_plan(plan_id)
        if saved is None:
            raise ValueError("找不到这份计划")
        current = saved.plan.weekly_plans
        if not 1 <= after_week < len(current):
            raise ValueError("没有可调整的后续周次")
        expected = list(range(after_week + 1, len(current) + 1))
        if [week.week_number for week in future] != expected:
            raise ValueError("调整后的周次不连续")
        updated = saved.plan.model_copy(
            update={"weekly_plans": current[:after_week] + future}
        )
        validate_plan_for_profile(updated, saved.profile)
        now = _now()
        with self._connect() as connection:
            connection.execute(
                "UPDATE plans SET plan_json = ?, updated_at = ? WHERE id = ?",
                (updated.model_dump_json(), now, plan_id),
            )

    def apply_weekly_review(
        self,
        plan_id: str,
        week_number: int,
        summary: str,
        adjusted: StudyPlan | None,
    ) -> None:
        """一次提交复盘和未来计划，避免只写入其中一半。"""

        saved = self.load_plan(plan_id)
        if saved is None:
            raise ValueError("找不到这份计划")
        if not 1 <= week_number <= saved.profile.plan_weeks:
            raise ValueError("周次无效")
        reviews = self.get_reviews(plan_id)
        if week_number in reviews:
            raise ValueError("本周已经复盘")
        if week_number > 1 and week_number - 1 not in reviews:
            raise ValueError("请先复盘上一周")
        progress = self.get_progress(plan_id)
        if any(number > week_number for number, _ in progress):
            raise ValueError("后续周次已有学习记录，不能覆盖其任务")
        if adjusted is None:
            if week_number != saved.profile.plan_weeks:
                raise ValueError("还有后续周次，必须提供调整计划")
        else:
            if week_number == saved.profile.plan_weeks:
                raise ValueError("最后一周无需调整计划")
            validate_plan_for_profile(adjusted, saved.profile)
            if adjusted.weekly_plans[:week_number] != saved.plan.weekly_plans[:week_number]:
                raise ValueError("已学习的周次不能修改")

        now = _now()
        with self._connect() as connection:
            if adjusted is not None:
                connection.execute(
                    "UPDATE plans SET plan_json = ?, updated_at = ? WHERE id = ?",
                    (adjusted.model_dump_json(), now, plan_id),
                )
            connection.execute(
                "INSERT INTO weekly_reviews VALUES (?, ?, ?, ?)",
                (plan_id, week_number, summary, now),
            )

    def record_generation(
        self,
        *,
        plan_id: str | None,
        kind: str,
        success: bool,
        latency_ms: int,
        input_tokens: int | None,
        output_tokens: int | None,
        error_category: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO generation_events
                (plan_id, kind, success, latency_ms, input_tokens, output_tokens,
                 error_category, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan_id,
                    kind,
                    int(success),
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    error_category,
                    _now(),
                ),
            )
