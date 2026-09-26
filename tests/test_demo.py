from demo_sample import sample_learning_plan
from progress_store import TaskProgress
from session_store import SessionRepository
from weekly_review import build_demo_adjustment, decide_week


def test_sample_runs_full_review_without_api() -> None:
    profile, plan = sample_learning_plan()
    repository = SessionRepository({})
    plan_id = repository.save_plan(profile, plan)
    repository.save_progress(plan_id, 1, 1, TaskProgress(status="延期", difficulty=5))
    repository.save_progress(plan_id, 1, 2, TaskProgress(status="已完成", difficulty=4))
    repository.save_progress(plan_id, 1, 3, TaskProgress(status="已完成", difficulty=4))
    decision = decide_week(profile, plan.weekly_plans[0], repository.get_progress(plan_id))
    adjusted = build_demo_adjustment(plan, profile, 1, decision)
    repository.apply_weekly_review(plan_id, 1, decision.summary, adjusted)

    assert repository.load_plan(plan_id).plan.weekly_plans[1].tasks[0].title == "运行第一段代码"
    assert repository.load_plan(plan_id).plan.weekly_plans[1].tasks[0].duration_minutes <= 36
    assert repository.get_reviews(plan_id)[1] == decision.summary


def test_demo_sessions_do_not_share_plans() -> None:
    first = SessionRepository({})
    second = SessionRepository({})
    profile, plan = sample_learning_plan()
    first.save_plan(profile, plan)
    assert len(first.list_plans()) == 1
    assert second.list_plans() == []
