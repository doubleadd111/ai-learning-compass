"""不含密钥的计划与进度导出、导入。"""

from __future__ import annotations

import json
from typing import Protocol

from models import LearningProfile, StudyPlan, validate_plan_for_profile
from progress_store import SavedPlan, TaskProgress


MAX_IMPORT_BYTES = 1_000_000


class PlanRepository(Protocol):
    def save_plan(self, profile: LearningProfile, plan: StudyPlan) -> str: ...
    def save_progress(
        self, plan_id: str, week_number: int, day: int, progress: TaskProgress
    ) -> None: ...
    def save_review(self, plan_id: str, week_number: int, summary: str) -> None: ...


def export_bundle(
    saved: SavedPlan,
    progress: dict[tuple[int, int], TaskProgress],
    reviews: dict[int, str],
) -> bytes:
    payload = {
        "version": 1,
        "profile": saved.profile.model_dump(mode="json"),
        "plan": saved.plan.model_dump(mode="json"),
        "progress": [
            {"week": week, "day": day, "record": record.model_dump(mode="json")}
            for (week, day), record in sorted(progress.items())
        ],
        "reviews": [
            {"week": week, "summary": summary}
            for week, summary in sorted(reviews.items())
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def import_bundle(repository: PlanRepository, content: bytes) -> str:
    if len(content) > MAX_IMPORT_BYTES:
        raise ValueError("文件超过 1 MB，请检查是否选择了正确的计划文件")
    try:
        payload = json.loads(content.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError("不支持的计划文件版本")
        profile = LearningProfile.model_validate(payload["profile"])
        plan = StudyPlan.model_validate(payload["plan"])
        validate_plan_for_profile(plan, profile)
        raw_progress = payload["progress"]
        raw_reviews = payload["reviews"]
        if not isinstance(raw_progress, list) or not isinstance(raw_reviews, list):
            raise ValueError("进度数据格式不正确")
        task_keys = {
            (week.week_number, task.day)
            for week in plan.weekly_plans
            for task in week.tasks
        }
        progress: dict[tuple[int, int], TaskProgress] = {}
        for item in raw_progress:
            key = (int(item["week"]), int(item["day"]))
            if key not in task_keys or key in progress:
                raise ValueError("进度对应的学习日不存在或重复")
            progress[key] = TaskProgress.model_validate(item["record"])
        reviews: dict[int, str] = {}
        for item in raw_reviews:
            week_number = int(item["week"])
            if week_number not in range(1, profile.plan_weeks + 1) or week_number in reviews:
                raise ValueError("复盘周次不存在或重复")
            summary = item["summary"]
            if not isinstance(summary, str) or len(summary) > 1000:
                raise ValueError("复盘内容格式不正确")
            reviews[week_number] = summary
        for week_number in reviews:
            week = plan.weekly_plans[week_number - 1]
            if any(
                progress.get((week_number, task.day), TaskProgress()).status
                not in {"已完成", "延期"}
                for task in week.tasks
            ):
                raise ValueError("已有复盘的周次缺少最终学习记录")
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError("无法读取计划文件，请确认它由本应用导出且内容完整") from error

    plan_id = repository.save_plan(profile, plan)
    for (week, day), record in progress.items():
        repository.save_progress(plan_id, week, day, record)
    for week, summary in reviews.items():
        repository.save_review(plan_id, week, summary)
    return plan_id
