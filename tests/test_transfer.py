from pathlib import Path

import pytest

from demo_sample import sample_learning_plan
from plan_transfer import export_bundle, import_bundle
from progress_store import StudyRepository, TaskProgress
from session_store import SessionRepository
from tests.test_progress_store import sample_plan, sample_profile
from weekly_review import build_demo_adjustment, decide_week


def test_export_import_roundtrip(tmp_path: Path) -> None:
    source = StudyRepository(tmp_path / "source.sqlite3")
    plan_id = source.save_plan(sample_profile(), sample_plan())
    source.save_progress(
        plan_id, 1, 1, TaskProgress(status="已完成", actual_minutes=32, note="学会运行脚本")
    )
    content = export_bundle(source.load_plan(plan_id), source.get_progress(plan_id), {})
    assert b"DEEPSEEK_API_KEY" not in content

    target = StudyRepository(tmp_path / "target.sqlite3")
    imported_id = import_bundle(target, content)
    assert target.load_plan(imported_id).plan.title == "Python 学习计划"
    assert target.get_progress(imported_id)[(1, 1)].actual_minutes == 32


def test_import_rejects_bad_file_before_writing(tmp_path: Path) -> None:
    target = StudyRepository(tmp_path / "target.sqlite3")
    with pytest.raises(ValueError, match="无法读取"):
        import_bundle(target, b"not-json")
    assert target.list_plans() == []


def test_public_demo_recovers_review_and_progress_in_new_session() -> None:
    profile, plan = sample_learning_plan()
    first_session = SessionRepository({})
    original_id = first_session.save_plan(profile, plan)
    first_session.save_progress(
        original_id, 1, 1,
        TaskProgress(status="已完成", actual_minutes=32, difficulty=2, note="运行了 hello.py"),
    )
    first_session.save_progress(original_id, 1, 2, TaskProgress(status="延期", note="时间不够"))
    first_session.save_progress(
        original_id, 1, 3, TaskProgress(status="已完成", actual_minutes=39, difficulty=3)
    )
    decision = decide_week(profile, plan.weekly_plans[0], first_session.get_progress(original_id))
    adjusted = build_demo_adjustment(plan, profile, 1, decision)
    first_session.apply_weekly_review(original_id, 1, decision.summary, adjusted)

    saved = first_session.load_plan(original_id)
    assert saved is not None
    content = export_bundle(
        saved,
        first_session.get_progress(original_id),
        first_session.get_reviews(original_id),
    )
    second_session = SessionRepository({})
    assert second_session.list_plans() == []
    restored_id = import_bundle(second_session, content)

    restored = second_session.load_plan(restored_id)
    records = second_session.get_progress(restored_id)
    assert restored is not None
    assert restored_id != original_id
    assert sum(record.status == "已完成" for record in records.values()) == 2
    assert records[(1, 2)].status == "延期"
    assert records[(1, 1)].note == "运行了 hello.py"
    assert second_session.get_reviews(restored_id)[1] == decision.summary
    assert restored.plan.weekly_plans[1].tasks[0].title == "使用变量与输入"
