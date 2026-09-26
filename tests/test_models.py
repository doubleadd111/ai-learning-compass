import pytest
from pydantic import ValidationError

from models import DailyTask, LearningProfile, StudyPlan, WeeklyPlan, validate_plan_for_profile


def profile() -> LearningProfile:
    return LearningProfile(
        goal="从零学习 Python 基础语法",
        current_level="零基础",
        plan_weeks=1,
        days_per_week=2,
        minutes_per_day=45,
        preferences="工作日晚上学习",
    )


def valid_plan() -> StudyPlan:
    return StudyPlan(
        title="Python 起步计划",
        overview="通过短小练习认识 Python 的核心语法。",
        weekly_plans=[
            WeeklyPlan(
                week_number=1,
                theme="认识 Python",
                milestone="能写出并运行一个简单脚本。",
                tasks=[
                    DailyTask(day=1, title="安装与打印", description="安装环境并运行第一段代码。", duration_minutes=30, deliverable="hello.py"),
                    DailyTask(day=2, title="变量练习", description="用变量记录并输出三条信息。", duration_minutes=40, deliverable="变量练习脚本"),
                ],
            )
        ],
        learning_tips=["每天结束时写一句复盘。", "遇到报错先读最后一行。"],
    )


def test_profile_rejects_invalid_duration() -> None:
    with pytest.raises(ValidationError):
        LearningProfile(
            goal="学 Python", current_level="零基础", plan_weeks=9,
            days_per_week=2, minutes_per_day=45
        )


def test_plan_matches_profile_constraints() -> None:
    validate_plan_for_profile(valid_plan(), profile())


def test_plan_rejects_weekly_time_overage() -> None:
    plan = valid_plan()
    plan.weekly_plans[0].tasks[0].duration_minutes = 50
    plan.weekly_plans[0].tasks[1].duration_minutes = 45
    with pytest.raises(ValueError, match="超过可用时间"):
        validate_plan_for_profile(plan, profile())


def test_weekly_tasks_require_unique_days() -> None:
    with pytest.raises(ValueError, match="不能重复"):
        WeeklyPlan(
            week_number=1,
            theme="测试",
            milestone="完成测试任务。",
            tasks=[
                DailyTask(day=1, title="任务一", description="第一项练习任务。", duration_minutes=20, deliverable="记录一"),
                DailyTask(day=1, title="任务二", description="第二项练习任务。", duration_minutes=20, deliverable="记录二"),
            ],
        )


def test_each_week_days_restart_at_one() -> None:
    plan = valid_plan()
    plan.weekly_plans[0].tasks[0].day = 3
    plan.weekly_plans[0].tasks[1].day = 4
    with pytest.raises(ValueError, match="从第 1 天连续"):
        validate_plan_for_profile(plan, profile())
