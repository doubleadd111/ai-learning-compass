from evaluation.run import inspect_plan
from evaluation.scenarios import build_scenarios
from tests.test_progress_store import sample_plan


def test_thirty_scenarios_cover_time_and_goal_variation() -> None:
    scenarios = build_scenarios()
    assert len(scenarios) == 30
    assert len({scenario.id for scenario in scenarios}) == 30
    assert {scenario.profile.minutes_per_day for scenario in scenarios} >= {15, 20, 45, 90, 240}
    assert {scenario.profile.plan_weeks for scenario in scenarios} >= {1, 2, 4, 6, 8}


def test_quality_inspection_flags_duplicate_titles() -> None:
    quality = inspect_plan(sample_plan())
    assert quality["task_count"] == 4
    assert quality["duplicate_title_count"] == 2
