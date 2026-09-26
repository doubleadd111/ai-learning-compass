import csv
import json

import pytest

import evaluation.run as evaluation_run
from evaluation.run import PILOT_SCENARIO_IDS, inspect_plan, select_scenarios
from evaluation.scenarios import build_scenarios
from planner import GenerationMetrics
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


def test_pilot_covers_distinct_schedules_and_constraints() -> None:
    selected = select_scenarios(1, pilot=True)
    assert tuple(scenario.id for scenario in selected) == PILOT_SCENARIO_IDS
    assert len({scenario.profile.plan_weeks for scenario in selected}) >= 3
    assert len({scenario.profile.days_per_week for scenario in selected}) == 4
    assert len({scenario.profile.minutes_per_day for scenario in selected}) == 4


def test_invalid_limit_is_rejected_before_any_live_request() -> None:
    with pytest.raises(ValueError, match="--limit"):
        select_scenarios(0)
    with pytest.raises(ValueError, match="--limit"):
        select_scenarios(31)


def test_pilot_preview_does_not_call_model(monkeypatch, capsys) -> None:
    def unexpected_request(*_args, **_kwargs):
        raise AssertionError("预览模式不应调用模型")

    monkeypatch.setattr(evaluation_run, "generate_study_plan", unexpected_request)
    monkeypatch.setattr("sys.argv", ["evaluation.run", "--pilot"])
    evaluation_run.main()

    output = capsys.readouterr().out
    assert all(scenario_id in output for scenario_id in PILOT_SCENARIO_IDS)


def test_live_pilot_disables_sdk_retries_and_uses_four_scenarios(tmp_path, monkeypatch) -> None:
    class FakeClient:
        def with_options(self, *, max_retries):
            assert max_retries == 0
            return self

    client = FakeClient()
    calls = []

    def fake_generate(profile, *, client, on_metrics):
        assert client is expected_client
        calls.append(profile)
        on_metrics(GenerationMetrics(True, 1, 12, 10, 20))
        return sample_plan()

    expected_client = client
    monkeypatch.setattr(evaluation_run, "_get_client", lambda: client)
    monkeypatch.setattr(evaluation_run, "generate_study_plan", fake_generate)

    report_path = evaluation_run.run_live(1, tmp_path, pilot=True)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert len(calls) == 4
    assert report["sdk_max_retries"] == 0
    assert [result["scenario"] for result in report["results"]] == list(PILOT_SCENARIO_IDS)
    assert report["results"][0]["profile"]["days_per_week"] == 7
    assert report["results"][0]["plan"]["weekly_plans"][0]["tasks"][0]["duration_minutes"] == 30
    review_path = next(tmp_path.glob("human-review-*.csv"))
    with review_path.open(encoding="utf-8-sig", newline="") as file:
        review_rows = list(csv.DictReader(file))
    assert review_rows[0]["duration_minutes"] == "30"
