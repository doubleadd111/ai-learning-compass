import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading

import pytest
pytest.importorskip("PySide6")
pytest.importorskip("pytestqt")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QToolButton

from desktop.credentials import CredentialStore
from desktop.guide import CHAPTERS, GuideWindow, guide_sections
from desktop.models import ModelConnection
from desktop.network import RequestControl
from desktop.planning import modification_context
from desktop.planning_ui import HistoryWindow, ModificationDialog
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.window import MainWindow
from tests.test_desktop_planning import ask, draft, proposal_for, ready
from tests.test_desktop_store import create, edit
from tests.test_progress_store import sample_plan


@pytest.fixture
def ui(qtbot, tmp_path, monkeypatch):
    app = QApplication.instance()
    apply_theme(app, "light")
    store = Store(tmp_path)
    config = ModelConnection(name="无费用测试", base_url="https://example.invalid", model="mock-a")
    store.save_connection(config)
    owner = MainWindow(store, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(owner)
    owner.show()
    calls = []
    turns = [ready()]
    gate = [None]
    errors = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: errors.append(args[-1]))
    class FakeGateway:
        def __init__(self, connection, _key):
            self.connection = connection.model_copy(deep=True)
            self.control = RequestControl()
        def close(self):
            pass
        def cancel(self):
            self.control.cancel()
        def provenance(self):
            return dict(connection_name=self.connection.name, model=self.connection.model,
                        metrics=dict(input_tokens=10, output_tokens=20, latency_ms=2, success=True))
        def clarify(self, d, *, cancelled=None):
            calls.append(("clarify", self.connection.model, d.model_copy(deep=True)))
            if gate[0]:
                gate[0].wait(3)
            result = turns.pop(0)
            if isinstance(result, Exception):
                raise result
            return result, self.provenance()
        def generate(self, profile, **kwargs):
            calls.append(("generate", self.connection.model, kwargs))
            return sample_plan(), self.provenance()
        def propose_revision(self, d, saved, reviewed, **kwargs):
            calls.append(("revise", self.connection.model, d))
            row = next(t for t in saved["tasks"] if t["id"] == d.target_ids[0])
            return proposal_for(row), self.provenance()
    monkeypatch.setattr("desktop.planning_ui.ModelGateway", FakeGateway)
    yield owner, config, calls, turns, gate, errors
    if gate[0]:
        gate[0].set()
    if owner.planning_window and not owner.planning_window.closed:
        owner.planning_window.close()
    for dialog in owner.history_windows:
        dialog.close()
    if getattr(owner, "_guide_window", None):
        owner._guide_window.close()
    owner.exiting = True
    owner.close()


def open_flow(owner, config, **kwargs):
    owner.open_planning(draft(connection=config, **kwargs))
    return owner.planning_window


def wait_stage(qtbot, owner, flow, state):
    qtbot.waitUntil(lambda: owner.worker is None and flow.draft.state == state, timeout=5000)


def test_guide_f1_search_chapter_and_offline_version(ui, qtbot):
    owner, *_ = ui
    assert set(guide_sections()) == set(CHAPTERS)
    owner.activateWindow()
    qtbot.keyClick(owner, Qt.Key.Key_F1)
    qtbot.waitUntil(lambda: getattr(owner, "_guide_window", None) is not None)
    guide = owner._guide_window
    assert guide.isVisible() and not guide.isModal()
    guide.go("modify")
    assert "AI 调整计划" in guide.browser.toPlainText()
    guide.search.setText("不存在的关键词-999")
    assert guide.toc.count() == 0 and "没有找到" in guide.browser.toPlainText()
    guide.search.setText("HTTP200")
    assert guide.toc.count() == 1
    assert "常见问题" == guide.toc.currentItem().text()


def test_questions_custom_answers_outline_expand_and_confirmed_save(ui, qtbot):
    owner, config, calls, turns, *_ = ui
    turns[:] = [ask(), ready()]
    flow = open_flow(owner, config)
    flow.advance()
    wait_stage(qtbot, owner, flow, "questions")
    combo, edit_box = flow.answer_widgets["goal"]
    combo.setCurrentIndex(1)
    edit_box.setPlainText("我想做一个具体的生活小工具")
    flow.advance()
    wait_stage(qtbot, owner, flow, "outline")
    assert calls[1][2].answers[0].answer == "我想做一个具体的生活小工具"
    assert len(calls) == 2 and owner.store.list_plans() == []
    toggles = flow.findChildren(QToolButton)
    for toggle in toggles:
        toggle.setChecked(True)
    assert len(calls) == 2  # Expanding is entirely local.
    flow.advance()
    wait_stage(qtbot, owner, flow, "preview")
    assert len(calls) == 3 and owner.store.list_plans() == []
    flow.close()
    resumed = owner.store.drafts()[0]
    owner.open_planning(resumed)
    flow = owner.planning_window
    assert flow.draft.state == "preview"
    flow.advance()
    assert len(owner.store.list_plans()) == 1 and len(calls) == 3
    assert owner.store.drafts() == []
    brief = owner.store.brief(owner.selected_plan_id)
    assert brief["requirements"]["answers"][0]["answer"].startswith("我想")
    assert len(brief["events"]) == 3


def test_excerpt_notice_is_visible_without_extra_request(ui, qtbot, monkeypatch):
    from desktop.planning_ui import ModelGateway
    owner, config, calls, *_ = ui
    previous = ModelGateway.provenance
    def provenance(gateway):
        result = previous(gateway)
        result['metrics']['summary_excerpted_count'] = 3
        return result
    monkeypatch.setattr(ModelGateway, 'provenance', provenance)
    flow = open_flow(owner, config)
    flow.advance()
    wait_stage(qtbot, owner, flow, 'outline')
    qtbot.wait(350)  # A later busy timer tick must not erase the completion notice.
    assert '原文摘录' in flow.status.text() and '展开内容' in flow.status.text()
    assert len(calls) == 1 and owner.store.list_plans() == []


def test_model_snapshot_only_changes_after_explicit_switch(ui, qtbot):
    owner, config, calls, turns, *_ = ui
    flow = open_flow(owner, config, quick=True)
    other = config.model_copy(update={"id": "other", "model": "mock-b", "name": "另一个模型"})
    owner.store.save_connection(other)
    owner.refresh()
    owner.model_picker.setCurrentIndex(owner.model_picker.findData(other.id))
    flow.advance()
    wait_stage(qtbot, owner, flow, "outline")
    assert calls[0][1] == "mock-a" and calls[0][2].quick
    flow.switch_model()
    flow.advance()
    wait_stage(qtbot, owner, flow, "preview")
    assert calls[-1][1] == "mock-b"


@pytest.mark.parametrize("change", ["delete", "address"])
def test_changed_connection_requires_explicit_rebind_before_reading_key(ui, qtbot, change, monkeypatch):
    owner, config, calls, turns, gate, errors = ui
    flow = open_flow(owner, config)
    if change == "delete":
        owner.store.delete_connection(config.id)
    else:
        owner.store.save_connection(config.model_copy(update={"base_url": "https://new.invalid"}))
    def forbidden_key_read(*_):
        pytest.fail("must not read a credential for a stale destination")
    monkeypatch.setattr(owner.credentials, "get", forbidden_key_read)
    flow.advance()
    assert errors and "连接已删除或设置已变化" in errors[0]
    assert calls == [] and owner.worker is None


def test_cancel_and_closed_window_discard_late_output(ui, qtbot):
    owner, config, calls, turns, gate, errors = ui
    gate[0] = threading.Event()
    flow = open_flow(owner, config)
    flow.advance()
    qtbot.waitUntil(lambda: bool(calls))
    assert owner.worker is not None
    flow.close()
    assert owner.worker is None and flow.closed
    gate[0].set()
    qtbot.wait(150)
    assert owner.store.list_plans() == []
    assert owner.store.drafts()[0].outline is None
    assert not errors


def test_failure_preserves_draft_and_never_saves_a_plan(ui, qtbot):
    owner, config, calls, turns, gate, errors = ui
    turns[:] = [ValueError("无费用模拟失败")]
    flow = open_flow(owner, config)
    flow.advance()
    qtbot.waitUntil(lambda: owner.worker is None and bool(errors))
    assert owner.store.drafts() and owner.store.list_plans() == []
    assert flow.draft.state == "base"


def test_cannot_close_unsaved_draft_when_disk_write_fails(ui, qtbot, monkeypatch):
    owner, config, *_ = ui
    flow = open_flow(owner, config)
    def fail(*_):
        raise OSError("模拟磁盘已满")
    with monkeypatch.context() as patch:
        patch.setattr(owner.store, "save_draft", fail)
        assert not flow.close()
        assert not flow.closed and "草稿保存失败" in flow.status.text()
    assert flow.close()


def test_pending_answers_autosave_and_resume(ui, qtbot):
    owner, config, calls, turns, *_ = ui
    turns[:] = [ask()]
    flow = open_flow(owner, config)
    flow.advance()
    wait_stage(qtbot, owner, flow, "questions")
    combo, edit_box = flow.answer_widgets["goal"]
    combo.setCurrentIndex(2)
    flow.close()
    owner.open_planning(owner.store.drafts()[0])
    resumed = owner.planning_window
    assert resumed.answer_widgets["goal"][1].toPlainText() == "完成考试复习"
    assert len(calls) == 1


def test_modification_preview_keeps_plan_until_apply_and_preserves_context(ui, qtbot):
    owner, config, calls, turns, *_ = ui
    pid = create(owner.store)
    saved = owner.store.get_plan(pid)
    row = saved["tasks"][0]
    context = modification_context(saved, [row["id"]], {})
    owner.open_planning(draft(connection=config, kind="modify", source_plan_id=pid,
        source_revision=saved["revision"], source_context=context, target_ids=context["target_ids"], request="只把这项任务改得更具体"))
    flow = owner.planning_window
    flow.advance()
    wait_stage(qtbot, owner, flow, "outline")
    flow.advance()
    wait_stage(qtbot, owner, flow, "preview")
    assert owner.store.tasks(pid)[0]["task"] == row["task"]
    assert "修改前" in flow.preview.toPlainText() and "修改后" in flow.preview.toPlainText()
    flow.advance()
    assert owner.store.tasks(pid)[0]["id"] == row["id"]
    assert owner.store.tasks(pid)[0]["task"].title == "更聚焦的任务"
    assert owner.store.brief(pid)["adjustments"][0]["request"] == "只把这项任务改得更具体"
    assert not owner.store.drafts()
    assert owner.store.versions(pid)[0]["kind"] == "AI局部调整"


def test_stale_modification_is_rejected_before_paid_stage(ui, qtbot):
    owner, config, calls, turns, gate, errors = ui
    pid = create(owner.store)
    saved = owner.store.get_plan(pid)
    row = saved["tasks"][0]
    context = modification_context(saved, [row["id"]], {})
    owner.open_planning(draft(connection=config, kind="modify", source_plan_id=pid,
        source_revision=saved["revision"], source_context=context, target_ids=context["target_ids"], request="更简单一点"))
    edit(owner.store, row, note="新记录")
    messages = []
    monkey = pytest.MonkeyPatch()
    monkey.setattr(QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    try:
        owner.planning_window.advance()
        assert messages and "已有变化" in messages[0]
        assert calls == [] and owner.worker is None
    finally:
        monkey.undo()


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_guide_and_planning_fit_small_windows_without_clipped_footer(ui, qtbot, theme):
    owner, config, *_ = ui
    apply_theme(QApplication.instance(), theme)
    flow = open_flow(owner, config)
    flow.resize(700, 540)
    QApplication.processEvents()
    for control in (flow.next, flow.close_button, flow.switch):
        point = control.mapTo(flow, control.rect().bottomRight())
        assert point.x() < flow.width() and point.y() < flow.height()
    guide = GuideWindow(flow)
    qtbot.addWidget(guide)
    guide.resize(680, 480)
    guide.go("create")
    assert guide.browser.width() > 300
    guide.close()
