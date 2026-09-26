from pathlib import Path
from types import SimpleNamespace

from streamlit.testing.v1 import AppTest

from progress_store import StudyRepository, TaskProgress
from tests.test_progress_store import sample_plan, sample_profile


def test_saved_plan_can_record_a_day(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_MODE", "local")
    database_path = tmp_path / "study.sqlite3"
    monkeypatch.setenv("STUDY_DB_PATH", str(database_path))
    repository = StudyRepository(database_path)
    plan_id = repository.save_plan(sample_profile(), sample_plan())

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=10).run()
    assert not app.exception
    assert repository.load_plan(plan_id) is not None

    form_key = f"progress-{plan_id}-1-1"
    app.selectbox(key=f"{form_key}-status").select("已完成")
    app.number_input(key=f"{form_key}-minutes").set_value(35)
    app.selectbox(key=f"{form_key}-difficulty").select(3)
    app.text_area(key=f"{form_key}-note").set_value("完成了练习")
    save_button = next(
        button for button in app.button
        if button.label == "保存当天记录" and form_key in button.key
    )
    save_button.click().run()

    assert not app.exception
    assert repository.get_progress(plan_id)[(1, 1)].status == "已完成"
    assert repository.get_progress(plan_id)[(1, 1)].actual_minutes == 35


def test_final_week_review_needs_no_api(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_MODE", "local")
    database_path = tmp_path / "study.sqlite3"
    monkeypatch.setenv("STUDY_DB_PATH", str(database_path))
    repository = StudyRepository(database_path)
    profile = sample_profile().model_copy(update={"plan_weeks": 1})
    plan = sample_plan().model_copy(
        update={"weekly_plans": [sample_plan().weekly_plans[0]]}
    )
    plan_id = repository.save_plan(profile, plan)
    repository.save_progress(plan_id, 1, 1, TaskProgress(status="已完成", actual_minutes=30))
    repository.save_progress(plan_id, 1, 2, TaskProgress(status="延期"))

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=10).run()
    assert not app.exception
    review_button = next(button for button in app.button if button.label == "完成最终复盘")
    review_button.click().run()

    assert not app.exception
    assert "完成 1/2 天" in repository.get_reviews(plan_id)[1]


def test_public_sample_reviews_without_key_or_database(monkeypatch) -> None:
    monkeypatch.setenv("APP_MODE", "demo")
    for name in (
        "DEMO_INVITE_CODE", "DEMO_DATABASE_URL", "DEMO_DAILY_LIMIT", "DEEPSEEK_API_KEY"
    ):
        monkeypatch.delenv(name, raising=False)
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=10).run()
    assert not app.exception
    plan_id = app.session_state["sample_plan_id"]

    for day in (1, 2, 3):
        form_key = f"progress-{plan_id}-1-{day}"
        app.selectbox(key=f"{form_key}-status").select("已完成")
        save_button = next(
            button for button in app.button
            if button.label == "保存当天记录" and form_key in button.key
        )
        save_button.click().run()
        assert not app.exception

    review_button = next(
        button for button in app.button
        if button.label == "生成本周复盘并调整后续计划"
    )
    review_button.click().run()
    assert not app.exception
    assert "完成 3/3 天" in app.session_state["demo_store"]["reviews"][(plan_id, 1)]


def test_missing_mode_defaults_to_session_only_sample(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "should-not-exist.sqlite3"
    monkeypatch.delenv("APP_MODE", raising=False)
    monkeypatch.setenv("STUDY_DB_PATH", str(database_path))
    monkeypatch.setattr("dotenv.load_dotenv", lambda: None)

    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=10).run()

    assert not app.exception
    assert "sample_plan_id" in app.session_state
    assert not database_path.exists()


def test_form_generation_saves_new_plan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("APP_MODE", "local")
    database_path = tmp_path / "study.sqlite3"
    monkeypatch.setenv("STUDY_DB_PATH", str(database_path))

    class FakeClient:
        responses = None

        def __init__(self):
            self.responses = self

        def create(self, **_kwargs):
            return SimpleNamespace(output_text=sample_plan().model_dump_json())

    monkeypatch.setattr("planner._get_client", FakeClient)
    app = AppTest.from_file(Path(__file__).parents[1] / "app.py", default_timeout=10).run()
    app.text_input[0].set_value("从零学习 Python 并写出小工具")
    app.number_input[0].set_value(2)
    app.number_input[1].set_value(2)
    app.number_input[2].set_value(45)
    generate_button = next(
        button for button in app.button if button.label == "生成我的学习计划"
    )
    generate_button.click().run()

    assert not app.exception
    assert len(StudyRepository(database_path).list_plans()) == 1
