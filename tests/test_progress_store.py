from pathlib import Path

import pytest

from models import DailyTask, LearningProfile, StudyPlan, WeeklyPlan
from progress_store import StudyRepository, TaskProgress


def sample_profile() -> LearningProfile:
    return LearningProfile(
        goal="从零学习 Python 并写出小工具",
        current_level="零基础",
        plan_weeks=2,
        days_per_week=2,
        minutes_per_day=45,
    )


def sample_plan() -> StudyPlan:
    weeks = []
    for number in (1, 2):
        weeks.append(
            WeeklyPlan(
                week_number=number,
                theme=f"第{number}周主题",
                milestone="完成两个小练习并能解释代码。",
                tasks=[
                    DailyTask(
                        day=day,
                        title=f"练习{day}",
                        description="编写脚本并运行验证结果。",
                        duration_minutes=30,
                        deliverable="可运行的脚本",
                    )
                    for day in (1, 2)
                ],
            )
        )
    return StudyPlan(
        title="Python 学习计划",
        overview="从最基本的代码运行开始，逐步完成入门练习。",
        weekly_plans=weeks,
        learning_tips=["每天运行代码。", "记录遇到的错误。"],
    )


def test_progress_survives_repository_restart(tmp_path: Path) -> None:
    path = tmp_path / "study.sqlite3"
    repository = StudyRepository(path)
    plan_id = repository.save_plan(sample_profile(), sample_plan())
    repository.save_progress(
        plan_id,
        1,
        1,
        TaskProgress(status="已完成", actual_minutes=35, difficulty=3, note="理解了 print"),
    )

    reopened = StudyRepository(path)
    assert reopened.load_plan(plan_id).plan.title == "Python 学习计划"
    assert reopened.get_progress(plan_id)[(1, 1)].note == "理解了 print"
    assert reopened.list_plans()[0].id == plan_id


def test_progress_rejects_unknown_day(tmp_path: Path) -> None:
    repository = StudyRepository(tmp_path / "study.sqlite3")
    plan_id = repository.save_plan(sample_profile(), sample_plan())
    with pytest.raises(ValueError, match="没有这个学习日"):
        repository.save_progress(plan_id, 1, 7, TaskProgress(status="已完成"))


def test_future_replacement_preserves_completed_week(tmp_path: Path) -> None:
    repository = StudyRepository(tmp_path / "study.sqlite3")
    plan_id = repository.save_plan(sample_profile(), sample_plan())
    repository.save_progress(plan_id, 1, 1, TaskProgress(status="已完成"))
    next_week = sample_plan().weekly_plans[1].model_copy(update={"theme": "加强练习"})
    repository.replace_future_weeks(plan_id, 1, [next_week])

    saved = repository.load_plan(plan_id)
    assert saved.plan.weekly_plans[0].theme == "第1周主题"
    assert saved.plan.weekly_plans[1].theme == "加强练习"
    assert repository.get_progress(plan_id)[(1, 1)].status == "已完成"
