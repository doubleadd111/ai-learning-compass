"""把学习反馈转成受约束的下周计划。规则负责边界，模型负责具体任务。"""

from __future__ import annotations

from dataclasses import dataclass

from models import DailyTask, LearningProfile, StudyPlan, WeeklyPlan, validate_plan_for_profile
from planner import generate_study_plan
from progress_store import TaskProgress


@dataclass(frozen=True)
class ReviewDecision:
    summary: str
    daily_limit: int
    carryover: tuple[DailyTask, ...]


def decide_week(
    profile: LearningProfile,
    week: WeeklyPlan,
    progress: dict[tuple[int, int], TaskProgress],
) -> ReviewDecision:
    records = [progress.get((week.week_number, task.day)) for task in week.tasks]
    if any(item is None or item.status not in {"已完成", "延期"} for item in records):
        raise ValueError("请先将本周每一天标为“已完成”或“延期”")

    completed = sum(item.status == "已完成" for item in records if item is not None)
    minutes = sum(item.actual_minutes for item in records if item is not None)
    difficulty = [
        item.difficulty
        for item in records
        if item is not None and item.difficulty is not None
    ]
    average_difficulty = sum(difficulty) / len(difficulty) if difficulty else None
    carryover = tuple(
        task
        for task, record in zip(week.tasks, records)
        if record is not None and record.status == "延期"
    )
    slow_down = completed / len(week.tasks) < 0.6 or (
        average_difficulty is not None and average_difficulty >= 4
    )
    daily_limit = (
        max(15, int(profile.minutes_per_day * 0.8))
        if slow_down
        else profile.minutes_per_day
    )

    summary = (
        f"第 {week.week_number} 周完成 {completed}/{len(week.tasks)} 天，"
        f"实际学习 {minutes} 分钟。"
    )
    if average_difficulty is not None:
        summary += f"平均难度 {average_difficulty:.1f}/5。"
    if carryover:
        summary += f"有 {len(carryover)} 项延期，将优先安排到下一周。"
    if slow_down:
        summary += f"后续周次单日任务上限调整为 {daily_limit} 分钟。"
    else:
        summary += "后续周次维持当前时间上限。"
    return ReviewDecision(summary, daily_limit, carryover)


def build_adjusted_plan(
    original: StudyPlan,
    profile: LearningProfile,
    reviewed_week: int,
    decision: ReviewDecision,
    *,
    client=None,
    on_metrics=None,
) -> StudyPlan:
    """只生成尚未开始的周次，并把延期任务确定性地排在最前。"""

    remaining = profile.plan_weeks - reviewed_week
    if remaining <= 0:
        raise ValueError("这是最后一周，没有后续计划可调整")

    reduced_profile = profile.model_copy(
        update={"plan_weeks": remaining, "minutes_per_day": decision.daily_limit}
    )
    postponed = "、".join(task.title for task in decision.carryover) or "无"
    context = (
        f"这是完成第 {reviewed_week} 周后的调整。{decision.summary}\n"
        f"延期任务：{postponed}。这些任务将由程序确定性地放在新计划首周前"
        f" {len(decision.carryover)} 天，请从此后的学习日开始安排新任务。"
        "要适合零基础 Python 学习者，任务要有可以检查的代码或文字产出。"
    )
    generated = generate_study_plan(
        reduced_profile, client=client, context=context, on_metrics=on_metrics
    )

    shifted_weeks = []
    for index, week in enumerate(generated.weekly_plans):
        tasks = list(week.tasks)
        if index == 0:
            for day_index, task in enumerate(decision.carryover):
                tasks[day_index] = task.model_copy(
                    update={
                        "day": day_index + 1,
                        "duration_minutes": min(task.duration_minutes, decision.daily_limit),
                    }
                )
        shifted_weeks.append(
            week.model_copy(update={"week_number": week.week_number + reviewed_week, "tasks": tasks})
        )

    adjusted = original.model_copy(
        update={"weekly_plans": original.weekly_plans[:reviewed_week] + shifted_weeks}
    )
    validate_plan_for_profile(adjusted, profile)
    return adjusted


def build_demo_adjustment(
    original: StudyPlan,
    profile: LearningProfile,
    reviewed_week: int,
    decision: ReviewDecision,
) -> StudyPlan:
    """公开样例的规则演示，不调用大模型。"""

    if reviewed_week >= profile.plan_weeks:
        raise ValueError("这是最后一周，没有后续计划可调整")
    future = []
    for week in original.weekly_plans[reviewed_week:]:
        tasks = [
            task.model_copy(
                update={"duration_minutes": min(task.duration_minutes, decision.daily_limit)}
            )
            for task in week.tasks
        ]
        if week.week_number == reviewed_week + 1:
            for index, task in enumerate(decision.carryover):
                tasks[index] = task.model_copy(
                    update={
                        "day": index + 1,
                        "duration_minutes": min(task.duration_minutes, decision.daily_limit),
                    }
                )
        future.append(week.model_copy(update={"tasks": tasks}))
    adjusted = original.model_copy(
        update={"weekly_plans": original.weekly_plans[:reviewed_week] + future}
    )
    validate_plan_for_profile(adjusted, profile)
    return adjusted
