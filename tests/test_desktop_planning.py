import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import date

import pytest

from desktop.gateway import GatewayError, ModelGateway
from desktop.models import DesktopProfile, ModelConnection
from desktop.planning import (ClarificationQuestion, OutlinePoint, PlanOutline, PlanningDraft,
                              PlanningTurn, RevisionProposal, TaskChange, modification_context,
                              protection_reason, validate_proposal)
from desktop.store import Store
from tests.test_desktop_gateway import Client
from tests.test_desktop_store import create, edit
from tests.test_progress_store import sample_plan, sample_profile


def outline():
    return PlanOutline(points=[OutlinePoint(id=f"point-{i}", summary="从基础概念开始，通过短练习巩固关键步骤，并留下可以检查的学习产出。",
                      objective="建立可执行的基础", content="讲解后完成短练习", deliverable="能独立展示的练习", rationale="根据当前基础逐步推进")
                      for i in range(3)], assumptions=["使用已有电脑完成练习"])


def draft(subject="Python", **kwargs):
    return PlanningDraft(profile=DesktopProfile(**sample_profile().model_dump(), subject=subject),
                         start_date=date(2026, 10, 12), weekdays=[0, 2], **kwargs)


def ask(qid="goal", text="你希望最终做出什么具体成果？"):
    return PlanningTurn(decision="ask", questions=[ClarificationQuestion(id=qid, question=text,
                        purpose="确定练习与验收方式", options=["做一个小作品", "完成考试复习"])], outline=None)


def ready():
    return PlanningTurn(decision="outline", questions=[], outline=outline())


def proposal_for(row, **kwargs):
    values = dict(task_id=row["id"], task=row["task"].model_copy(update={"title": "更聚焦的任务"}),
                  due_date=date.fromisoformat(row["due_date"]), reason="保留时间，减少无关内容")
    values.update(kwargs)
    return RevisionProposal(changes=[TaskChange(**values)], summary="只修改选定任务，其余保持不变")


@pytest.mark.parametrize("subject", ["Python", "英语", "考试复习"])
def test_bounded_questions_outline_preview_and_restart(tmp_path, subject):
    store = Store(tmp_path)
    d = draft(subject)
    d.receive_turn(ask())
    d.pending_answers = {"goal": "自己的想法，不用参考答案"}
    store.save_draft(d)
    d = Store(tmp_path).drafts()[0]
    assert d.pending_answers["goal"].startswith("自己的")
    d.answer_questions(d.pending_answers)
    d.receive_turn(ask("device", "你可以使用哪些学习设备？"))
    d.answer_questions({})
    assert "跳过" in d.answers[-1].answer
    with pytest.raises(ValueError, match="上限"):
        d.receive_turn(ask("third"))
    d.receive_turn(ready())
    assert d.state == "outline" and d.candidate is None
    d.candidate = sample_plan()
    d.state = "preview"
    store.save_draft(d)
    assert Store(tmp_path).drafts()[0].candidate == sample_plan()
    assert store.list_plans() == []


def test_quick_mode_repeated_questions_and_outline_feedback():
    with pytest.raises(ValueError, match="上限"):
        draft(quick=True).receive_turn(ask())
    d = draft()
    d.receive_turn(ask())
    d.answer_questions({"goal": "小作品"})
    with pytest.raises(ValueError, match="重复"):
        d.receive_turn(ask())
    d.receive_turn(ready())
    d.revise_outline("point-1", "增加实际练习，其他点保留")
    assert d.state == "base" and d.candidate is None
    assert "其他点保留" in d.feedback[-1]


@pytest.mark.parametrize("invalid", ["short", "duplicate", "ask_empty", "wrong_state"])
def test_planning_types_reject_invalid_shapes(invalid):
    data = ready().model_dump()
    if invalid == "short":
        data["outline"]["points"][0]["summary"] = "太短"
        data["outline"]["points"][1]["summary"] = "太短"
    elif invalid == "duplicate":
        data["outline"]["points"][1]["id"] = "point-0"
    elif invalid == "ask_empty":
        data = dict(decision="ask", questions=[], outline=None)
    else:
        data["questions"] = ask().model_dump()["questions"]
    with pytest.raises(ValueError):
        PlanningTurn.model_validate(data)


@pytest.mark.parametrize("protocol", ["responses", "chat"])
def test_gateway_clarification_retry_usage_and_same_model(protocol):
    config = ModelConnection(name="测试模型", base_url="https://example.invalid/v1", model="selected", api_type=protocol)
    d = draft(connection=config, quick=True)
    client = Client([ask().model_dump_json(), ready().model_dump_json()])
    gateway = ModelGateway(config, "placeholder-not-a-real-key", client=client)
    result, event = gateway.clarify(d)
    assert result.decision == "outline" and len(client.calls) == 2
    assert event["metrics"]["input_tokens"] == 20
    assert event["metrics"]["success"]
    assert all(c["model"] == "selected" for c in client.calls)
    assert "placeholder" not in json.dumps(event)


def test_invalid_structured_response_stops_after_one_repair():
    config = ModelConnection(name="mock", base_url="https://example.invalid", model="mock")
    client = Client(["not json", "still invalid"])
    with pytest.raises(GatewayError, match="连续两次"):
        ModelGateway(config, "placeholder", client=client).clarify(draft())
    assert len(client.calls) == 2


def test_conflicts_are_explained_after_bounded_repair(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    saved = store.get_plan(pid)
    first, second = saved["tasks"][:2]
    proposal = proposal_for(first, due_date=date.fromisoformat(second["due_date"]))
    config = ModelConnection(name="mock", base_url="https://example.invalid", model="mock")
    client = Client([proposal.model_dump_json(), proposal.model_dump_json()])
    d = draft(connection=config, kind="modify", target_ids=[first["id"]])
    d.receive_turn(ready())
    with pytest.raises(GatewayError, match="与保留任务合并后超过"):
        ModelGateway(config, "placeholder", client=client).propose_revision(d, saved, {})
    assert len(client.calls) == 2 and store.get_plan(pid)["revision"] == saved["revision"]


@pytest.mark.parametrize("change", [dict(status="已完成"), dict(status="进行中"), dict(note="学习记录"),
                                   dict(manual_seconds=60), dict(focus_seconds=30), dict(difficulty=2), dict(ai_locked=1)])
def test_protection_is_shared_by_all_ai_actions(tmp_path, change):
    store = Store(tmp_path)
    pid = create(store)
    saved = store.get_plan(pid)
    saved["tasks"][0].update(change)
    assert protection_reason(saved["tasks"][0])
    with pytest.raises(ValueError, match="没有可修改"):
        modification_context(saved, [saved["tasks"][0]["id"]], {})
    with pytest.raises(ValueError, match="保护"):
        validate_proposal(saved, proposal_for(saved["tasks"][0]), [saved["tasks"][0]["id"]], {})


def test_partial_scope_excludes_locks_and_does_not_send_other_plan(tmp_path):
    store = Store(tmp_path)
    pid, other = create(store), create(store)
    rows = store.tasks(pid)
    store.set_task_lock(rows[0]["id"], True)
    saved = store.get_plan(pid)
    context = modification_context(saved, [row["id"] for row in rows], {})
    assert context["protected_count"] == 1
    assert rows[0]["id"] not in context["target_ids"]
    assert not any(row["id"] in json.dumps(context) for row in store.tasks(other))


def test_proposal_apply_keeps_ids_history_and_safe_undo(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    saved = store.get_plan(pid)
    row = saved["tasks"][0]
    proposal = proposal_for(row)
    ids = [t["id"] for t in saved["tasks"]]
    store.apply_proposal(pid, proposal, [row["id"]], saved["revision"])
    assert [t["id"] for t in store.tasks(pid)] == ids
    assert store.tasks(pid)[0]["task"].title == "更聚焦的任务"
    assert len(store.versions(pid)) == 2
    store.undo_latest(pid)
    assert store.tasks(pid)[0]["task"] == row["task"]
    assert len(store.versions(pid)) == 3
    with pytest.raises(ValueError, match="不能覆盖"):
        store.undo_latest(pid)


def test_preview_and_undo_reject_new_records(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    saved = store.get_plan(pid)
    row = saved["tasks"][0]
    edit(store, row, note="新记录")
    with pytest.raises(ValueError, match="已有变化"):
        store.apply_proposal(pid, proposal_for(row), [row["id"]], saved["revision"])
    row2 = store.tasks(pid)[1]
    current = store.get_plan(pid)
    store.apply_proposal(pid, proposal_for(row2), [row2["id"]], current["revision"])
    store.record_focus("new-focus", row["id"], 10, "2026-10-12T10:00:00")
    with pytest.raises(ValueError, match="新记录"):
        store.undo_latest(pid)
    assert store.tasks(pid)[0]["note"] == "新记录"


@pytest.mark.parametrize("invalid", ["scope", "duplicate", "duration", "date", "day", "overlap"])
def test_invalid_proposals_never_save(tmp_path, invalid):
    store = Store(tmp_path)
    pid = create(store)
    saved = store.get_plan(pid)
    row, second = saved["tasks"][:2]
    proposal = proposal_for(row)
    change = proposal.changes[0]
    if invalid == "scope":
        change.task_id = second["id"]
    elif invalid == "duplicate":
        proposal.changes.append(change.model_copy(deep=True))
    elif invalid == "duration":
        change.task.duration_minutes = saved["profile"].minutes_per_day + 1
    elif invalid == "date":
        change.due_date = date(2027, 1, 1)
    elif invalid == "day":
        change.task.day = 7
    elif invalid == "overlap":
        change.due_date = date.fromisoformat(second["due_date"])
    with pytest.raises(ValueError):
        store.apply_proposal(pid, proposal, [row["id"]], saved["revision"])
    assert store.get_plan(pid)["revision"] == saved["revision"]
    assert len(store.versions(pid)) == 1


def test_manual_edits_lock_and_explicit_unlock_is_safe(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    row = store.tasks(pid)[0]
    edit(store, row, payload=row["task"].model_copy(update={"title": "手动编辑的任务"}))
    assert store.tasks(pid)[0]["ai_locked"] == 1
    store.set_task_lock(row["id"], False)
    assert not protection_reason(store.tasks(pid)[0])
    edit(store, store.tasks(pid)[0], note="记录后不可解锁")
    with pytest.raises(ValueError):
        store.set_task_lock(row["id"], False)


def test_fork_is_atomic_and_does_not_clone_progress(tmp_path):
    store = Store(tmp_path)
    pid = create(store)
    row = store.tasks(pid)[0]
    edit(store, row, status="已完成", manual_minutes=10)
    old = store.get_plan(pid)
    fork = store.save_plan(old["profile"], old["plan"], date(2026, 10, 19), [0, 2],
                           parent_plan_id=pid, parent_revision=old["revision"], archive_parent=True,
                           brief={"outline": outline().model_dump()})
    assert store.get_plan(pid)["archived"]
    assert all(t["status"] == "未开始" and not t["manual_seconds"] for t in store.tasks(fork))
    assert not {t["id"] for t in store.tasks(pid)} & {t["id"] for t in store.tasks(fork)}
    count = len(store.read("SELECT * FROM plans"))
    with pytest.raises(ValueError, match="已有变化"):
        store.save_plan(old["profile"], old["plan"], date.today(), [0, 2], parent_plan_id=pid, parent_revision=old["revision"])
    assert len(store.read("SELECT * FROM plans")) == count


def make_v1(store):
    # A representative previous schema generated from an isolated test fixture.
    with closing(sqlite3.connect(store.path)) as c, c:
        for table in ("planning_drafts", "planning_events", "plan_versions", "plan_briefs", "plan_schedules", "pause_history"):
            c.execute(f"DROP TABLE {table}")
        c.execute("ALTER TABLE tasks DROP COLUMN ai_locked")
        c.execute("PRAGMA user_version=1")


def test_migration_old_backup_and_v2_json_source_are_preserved(tmp_path):
    source = Store(tmp_path / "old")
    pid = create(source)
    row = source.tasks(pid)[0]
    edit(source, row, payload=row["task"].model_copy(update={"title": "旧手动任务"}))
    payload = json.loads(source.export_plan(pid))
    payload["version"] = 2
    payload.pop("plan_briefs")
    for task in payload["tasks"]:
        task.pop("ai_locked")
    make_v1(source)
    old_backup = tmp_path / "schema-one.sqlite3"
    old_backup.write_bytes(source.path.read_bytes())
    before = hashlib.sha256(old_backup.read_bytes()).digest()
    migrated = Store(source.directory)
    assert migrated.tasks(pid)[0]["ai_locked"]
    assert list(migrated.backups.glob("before-schema-8-*.sqlite3"))
    target = Store(tmp_path / "restored")
    target.restore(old_backup)
    assert target.tasks(pid)[0]["task"].title == "旧手动任务"
    assert hashlib.sha256(old_backup.read_bytes()).digest() == before
    imported = target.import_plan(json.dumps(payload).encode(), date.today())
    assert target.tasks(imported)[0]["ai_locked"]
    assert imported != pid


def test_failed_migration_never_changes_original_database(tmp_path, monkeypatch):
    source = Store(tmp_path / "data")
    create(source)
    make_v1(source)
    before = source.path.read_bytes()
    original = Store._upgrade_v2
    def fail_after_ddl(c):
        original(c)
        raise ValueError("模拟升级失败")
    monkeypatch.setattr(Store, "_upgrade_v2", staticmethod(fail_after_ddl))
    with pytest.raises(ValueError, match="模拟升级失败"):
        Store(source.directory)
    assert source.path.read_bytes() == before
    assert not list(source.directory.glob("upgrade-*.sqlite3"))
    assert list(source.backups.glob("before-schema-8-*.sqlite3"))


@pytest.mark.parametrize("area", ["brief", "history"])
def test_corrupt_backup_metadata_is_rejected_before_restore(tmp_path, area):
    store = Store(tmp_path / "data")
    pid = create(store)
    backup = store.manual_backup()
    with sqlite3.connect(backup) as c:
        if area == "brief":
            c.execute("INSERT INTO plan_briefs VALUES (?, ?, NULL)", (pid, '{"requirements": []}'))
        else:
            c.execute("UPDATE plan_versions SET after_json='[]'")
    before = store.path.read_bytes()
    with pytest.raises(ValueError, match="无法读取备份"):
        store.restore(backup)
    assert store.path.read_bytes() == before


def test_backup_restores_drafts_versions_and_new_export_brief(tmp_path):
    store = Store(tmp_path / "data")
    pid = create(store)
    d = draft()
    d.receive_turn(ready())
    store.save_draft(d)
    saved = store.get_plan(pid)
    store.apply_proposal(pid, proposal_for(saved["tasks"][0]), [saved["tasks"][0]["id"]], saved["revision"])
    backup = store.manual_backup()
    store.delete_draft(d.id)
    store.restore(backup)
    assert store.drafts()[0].outline == outline()
    assert len(store.versions(pid)) == 2
    exported = store.export_plan(pid)
    assert json.loads(exported)["version"] == 7
    assert b"api_key" not in exported
    event = store.planning_event(d.id, "mock", {"api_key": "MUST-NOT-SAVE", "model": "mock", "metrics": {"input_tokens": 3, "secret": "MUST-NOT-SAVE"}})
    assert "MUST-NOT-SAVE" not in json.dumps(event)
