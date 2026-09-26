from pathlib import Path

import pytest

from plan_transfer import export_bundle, import_bundle
from progress_store import StudyRepository, TaskProgress
from tests.test_progress_store import sample_plan, sample_profile


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
