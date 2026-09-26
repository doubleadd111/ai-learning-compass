"""覆盖学习周期、时间预算和目标类型的 30 组输入。"""

from __future__ import annotations

from dataclasses import dataclass

from models import LearningProfile


@dataclass(frozen=True)
class Scenario:
    id: str
    profile: LearningProfile


GOALS = (
    "从零学习 Python 语法并能解释简单脚本",
    "从零学习 Python，最终写一个命令行待办工具",
    "从零学习 Python，能读取文本并做简单统计",
)


def build_scenarios() -> list[Scenario]:
    scenarios: list[Scenario] = []
    for goal_index, goal in enumerate(GOALS, 1):
        for days in (2, 3, 5):
            for minutes in (20, 45, 90):
                scenarios.append(
                    Scenario(
                        id=f"goal{goal_index}-days{days}-minutes{minutes}",
                        profile=LearningProfile(
                            goal=goal,
                            current_level="零基础",
                            plan_weeks=(2, 4, 6)[goal_index - 1],
                            days_per_week=days,
                            minutes_per_day=minutes,
                            preferences="先动手练习，每天有可检查的代码产出",
                        ),
                    )
                )
    scenarios.extend(
        [
            Scenario(
                "short-daily",
                LearningProfile(
                    goal="从零学习 Python 并运行第一个脚本",
                    current_level="零基础",
                    plan_weeks=1,
                    days_per_week=7,
                    minutes_per_day=15,
                    preferences="只能在手机旁看说明，电脑练习时间有限",
                ),
            ),
            Scenario(
                "long-weekly",
                LearningProfile(
                    goal="从零学习 Python 并做命令行记账工具",
                    current_level="零基础",
                    plan_weeks=8,
                    days_per_week=1,
                    minutes_per_day=240,
                    preferences="每周只能集中学习一天",
                ),
            ),
            Scenario(
                "limited-english",
                LearningProfile(
                    goal="从零学习 Python 并用脚本整理文件名",
                    current_level="零基础",
                    plan_weeks=4,
                    days_per_week=4,
                    minutes_per_day=60,
                    preferences="优先中文资料，避免假设我懂英文术语",
                ),
            ),
        ]
    )
    return scenarios
