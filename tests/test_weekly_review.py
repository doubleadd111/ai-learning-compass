from pathlib import Path
from types import SimpleNamespace

import pytest

from progress_store import StudyRepository, TaskProgress
from tests.test_progress_store import sample_plan, sample_profile
from weekly_review import build_adjusted_plan, decide_week


class FakeClient:
    def __init__(self, output: str):
        self.output = output
        self.calls = []
        self.responses = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=self.output)


def test_delayed_task_is_carried_over_with_lower_time_limit() -> None:
    profile = sample_profile()
    original = sample_plan()
    progress = {
        (1, 1): TaskProgress(status="延期", actual_minutes=10, difficulty=5),
        (1, 2): TaskProgress(status="已完成", actual_minutes=30, difficulty=4),
    }
    decision = decide_week(profile, original.weekly_plans[0], progress)
    assert decision.daily_limit == 36
    assert len(decision.carryover) == 1

    generated = sample_plan().model_copy(
        update={"weekly_plans": [sample_plan().weekly_plans[0]]}
    )
    client = FakeClient(generated.model_dump_json())
    adjusted = build_adjusted_plan(original, profile, 1, decision, client=client)

    assert adjusted.weekly_plans[0] == original.weekly_plans[0]
    assert adjusted.weekly_plans[1].week_number == 2
    assert adjusted.weekly_plans[1].tasks[0].title == "练习1"
    assert adjusted.weekly_plans[1].tasks[0].duration_minutes <= 36
    assert "延期任务" in client.calls[0]["input"]


def test_week_review_requires_all_days_finished() -> None:
    with pytest.raises(ValueError, match="请先"):
        decide_week(sample_profile(), sample_plan().weekly_plans[0], {})


def test_review_is_saved_atomically_and_locks_history(tmp_path: Path) -> None:
    repository = StudyRepository(tmp_path / "study.sqlite3")
    profile = sample_profile()
    original = sample_plan()
    plan_id = repository.save_plan(profile, original)
    repository.save_progress(plan_id, 1, 1, TaskProgress(status="已完成"))
    repository.save_progress(plan_id, 1, 2, TaskProgress(status="延期"))
    decision = decide_week(profile, original.weekly_plans[0], repository.get_progress(plan_id))
    generated = original.model_copy(update={"weekly_plans": [original.weekly_plans[0]]})
    adjusted = build_adjusted_plan(
        original, profile, 1, decision, client=FakeClient(generated.model_dump_json())
    )
    repository.apply_weekly_review(plan_id, 1, decision.summary, adjusted)

    assert repository.get_reviews(plan_id)[1] == decision.summary
    assert repository.load_plan(plan_id).plan.weekly_plans[1].tasks[1].title == "练习2"
    with pytest.raises(ValueError, match="已经复盘"):
        repository.save_progress(plan_id, 1, 1, TaskProgress(status="延期"))
