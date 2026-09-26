"""公开样例的每浏览器会话存储；不会把用户计划写到服务器磁盘。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from models import LearningProfile, StudyPlan, validate_plan_for_profile
from progress_store import SavedPlan, TaskProgress


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionRepository:
    def __init__(self, state: dict):
        self.state = state
        self.state.setdefault("plans", {})
        self.state.setdefault("progress", {})
        self.state.setdefault("reviews", {})
        self.state.setdefault("events", [])

    def save_plan(self, profile: LearningProfile, plan: StudyPlan) -> str:
        validate_plan_for_profile(plan, profile)
        plan_id = uuid4().hex
        now = _now()
        self.state["plans"][plan_id] = SavedPlan(
            plan_id, profile.model_copy(deep=True), plan.model_copy(deep=True), now, now
        )
        return plan_id

    def load_plan(self, plan_id: str) -> SavedPlan | None:
        return self.state["plans"].get(plan_id)

    def list_plans(self, limit: int = 20) -> list[SavedPlan]:
        return sorted(
            self.state["plans"].values(),
            key=lambda saved: (saved.updated_at, saved.id),
            reverse=True,
        )[:limit]

    def save_progress(
        self, plan_id: str, week_number: int, day: int, progress: TaskProgress
    ) -> None:
        saved = self.load_plan(plan_id)
        if saved is None:
            raise ValueError("找不到这份计划")
        if week_number in self.get_reviews(plan_id):
            raise ValueError("本周已经复盘，不能再修改记录")
        if not any(
            week.week_number == week_number and any(task.day == day for task in week.tasks)
            for week in saved.plan.weekly_plans
        ):
            raise ValueError("计划中没有这个学习日")
        self.state["progress"][(plan_id, week_number, day)] = progress.model_copy(deep=True)

    def get_progress(self, plan_id: str) -> dict[tuple[int, int], TaskProgress]:
        return {
            (week, day): progress.model_copy(deep=True)
            for (current_id, week, day), progress in self.state["progress"].items()
            if current_id == plan_id
        }

    def get_reviews(self, plan_id: str) -> dict[int, str]:
        return {
            week: summary
            for (current_id, week), summary in self.state["reviews"].items()
            if current_id == plan_id
        }

    def save_review(self, plan_id: str, week_number: int, summary: str) -> None:
        if self.load_plan(plan_id) is None:
            raise ValueError("找不到这份计划")
        self.state["reviews"][(plan_id, week_number)] = summary

    def apply_weekly_review(
        self, plan_id: str, week_number: int, summary: str, adjusted: StudyPlan | None
    ) -> None:
        saved = self.load_plan(plan_id)
        if saved is None:
            raise ValueError("找不到这份计划")
        reviews = self.get_reviews(plan_id)
        if week_number in reviews:
            raise ValueError("本周已经复盘")
        if week_number > 1 and week_number - 1 not in reviews:
            raise ValueError("请先复盘上一周")
        if any(number > week_number for number, _ in self.get_progress(plan_id)):
            raise ValueError("后续周次已有学习记录，不能覆盖其任务")
        if adjusted is None and week_number != saved.profile.plan_weeks:
            raise ValueError("还有后续周次，必须提供调整计划")
        if adjusted is not None:
            validate_plan_for_profile(adjusted, saved.profile)
            if adjusted.weekly_plans[:week_number] != saved.plan.weekly_plans[:week_number]:
                raise ValueError("已学习的周次不能修改")
            self.state["plans"][plan_id] = SavedPlan(
                saved.id, saved.profile, adjusted.model_copy(deep=True), saved.created_at, _now()
            )
        self.save_review(plan_id, week_number, summary)

    def record_generation(self, **event) -> None:
        self.state["events"].append({**event, "created_at": _now()})
