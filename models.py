"""学习计划领域的数据模型与校验规则。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


LearningLevel = Literal["零基础", "入门", "已有基础"]


class LearningProfile(BaseModel):
    """用户在生成计划前提供的学习条件。"""

    goal: str = Field(min_length=4, max_length=240, description="具体、可观察的学习目标")
    current_level: LearningLevel
    plan_weeks: int = Field(ge=1, le=8)
    days_per_week: int = Field(ge=1, le=7)
    minutes_per_day: int = Field(ge=15, le=240)
    preferences: str = Field(default="无", max_length=500)

    @property
    def weekly_minutes(self) -> int:
        return self.days_per_week * self.minutes_per_day


class DailyTask(BaseModel):
    """计划中的一个学习日。"""

    day: int = Field(ge=1, le=7)
    title: str = Field(min_length=2, max_length=80)
    description: str = Field(min_length=4, max_length=300)
    duration_minutes: int = Field(ge=5, le=240)
    deliverable: str = Field(min_length=2, max_length=160)


class WeeklyPlan(BaseModel):
    week_number: int = Field(ge=1, le=8)
    theme: str = Field(min_length=2, max_length=80)
    milestone: str = Field(min_length=4, max_length=200)
    tasks: list[DailyTask] = Field(min_length=1, max_length=7)

    @model_validator(mode="after")
    def task_days_must_be_unique(self) -> "WeeklyPlan":
        days = [task.day for task in self.tasks]
        if len(days) != len(set(days)):
            raise ValueError("同一周的学习日不能重复")
        return self


class StudyPlan(BaseModel):
    title: str = Field(min_length=2, max_length=100)
    overview: str = Field(min_length=10, max_length=500)
    weekly_plans: list[WeeklyPlan] = Field(min_length=1, max_length=8)
    learning_tips: list[str] = Field(min_length=2, max_length=5)


def validate_plan_for_profile(plan: StudyPlan, profile: LearningProfile) -> None:
    """验证模型输出是否真正满足用户填写的时间和周期约束。"""

    if len(plan.weekly_plans) != profile.plan_weeks:
        raise ValueError(f"计划应包含 {profile.plan_weeks} 周，而不是 {len(plan.weekly_plans)} 周")

    expected_weeks = list(range(1, profile.plan_weeks + 1))
    actual_weeks = [week.week_number for week in plan.weekly_plans]
    if actual_weeks != expected_weeks:
        raise ValueError("周次必须从第 1 周连续排列")

    for week in plan.weekly_plans:
        if len(week.tasks) != profile.days_per_week:
            raise ValueError(
                f"第 {week.week_number} 周应安排 {profile.days_per_week} 个学习日"
            )

        actual_days = sorted(task.day for task in week.tasks)
        expected_days = list(range(1, profile.days_per_week + 1))
        if actual_days != expected_days:
            raise ValueError(
                f"第 {week.week_number} 周的学习日必须从第 1 天连续到第 {profile.days_per_week} 天"
            )

        total_minutes = sum(task.duration_minutes for task in week.tasks)
        if total_minutes > profile.weekly_minutes:
            raise ValueError(
                f"第 {week.week_number} 周总时长 {total_minutes} 分钟，超过可用时间"
            )

        for task in week.tasks:
            if task.duration_minutes > profile.minutes_per_day:
                raise ValueError(
                    f"第 {week.week_number} 周第 {task.day} 天的任务超过单日可用时间"
                )
