"""在隔离数据目录内生成桌面界面 QA 图，不调用模型。"""
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget, QToolButton
from PySide6.QtGui import QFontDatabase
from desktop.dialogs import ConnectionDialog, PlanDialog, TaskDialog
from desktop.models import ModelConnection
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.window import MainWindow
from desktop.guide import GuideWindow
from desktop.planning import PlanningDraft, PlanningTurn, PlanOutline, OutlinePoint, ClarificationQuestion, RevisionProposal, TaskChange, modification_context
from desktop.planning_ui import PlanningWindow, HistoryWindow, DraftsDialog
from datetime import date


app = QApplication([])
if sys.platform == "win32":
    for filename in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf", "consola.ttf"):
        QFontDatabase.addApplicationFont(str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename))
app.setStyle("Fusion")
output = ROOT / ".learning_compass" / "qa" / "screenshots"
output.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as directory:
    store = Store(Path(directory))
    config = ModelConnection(name="我的 DeepSeek", provider="DeepSeek", base_url="https://api.deepseek.com", model="deepseek-flash", api_type="responses", output_format="json_schema")
    store.save_connection(config)
    window = MainWindow(store, enable_tray=False)
    window.load_sample()
    for theme in ("light", "dark"):
        apply_theme(app, theme)
        window.notice.clear()
        for page in range(5):
            window.navigate(page)
            window.show()
            app.processEvents()
            if app.focusWidget():
                app.focusWidget().clearFocus()
            window.grab().save(str(output / f"{theme}-{page}.png"))
        window.navigate(2)
        window.week_panel.select_task(store.tasks()[0]["id"])
        window.week_panel.mode.setCurrentIndex(1)
        app.processEvents()
        window.grab().save(str(output / f"{theme}-week-table.png"))
        window.week_panel.mode.setCurrentIndex(0)
        window.resize(980, 680)
        app.processEvents()
        window.grab().save(str(output / f"{theme}-week-compact.png"))
        window.resize(1220, 820)
        window.navigate(4)
        hover_button = next(button for button in window.findChildren(QPushButton) if button.text() == "测试连接（使用额度）")
        QApplication.sendEvent(hover_button, QEvent(QEvent.Type.Enter))
        app.processEvents()
        window.grab().save(str(output / f"{theme}-hover.png"))
        QTest.mousePress(hover_button, Qt.MouseButton.LeftButton)
        app.processEvents()
        window.grab().save(str(output / f"{theme}-pressed.png"))
        # Cancel the synthetic press without emitting clicked or calling the API.
        hover_button.setDown(False)
        QApplication.sendEvent(hover_button, QEvent(QEvent.Type.Leave))
        window.resize(980, 680)
        window.navigate(0)
        app.processEvents()
        window.grab().save(str(output / f"{theme}-compact.png"))
        window.resize(1220, 820)
        window.navigate(4)
        tabs = window.findChild(QTabWidget)
        for index, name in ((1, "appearance"), (2, "data")):
            tabs.setCurrentIndex(index)
            app.processEvents()
            window.grab().save(str(output / f"{theme}-{name}.png"))
        tabs.setCurrentIndex(0)
        for name, dialog in (("connection", ConnectionDialog(config, parent=window)),
                             ("create", PlanDialog([config], parent=window)),
                             ("task", TaskDialog(store.tasks()[0], parent=window))):
            dialog.show()
            app.processEvents()
            dialog.grab().save(str(output / f"{theme}-{name}.png"))
            dialog.close()
        guide = GuideWindow(window)
        guide.go("create")
        app.processEvents()
        guide.grab().save(str(output / f"{theme}-guide.png"))
        guide.close()
        saved = store.get_plan(window.selected_plan_id)
        draft = PlanningDraft(profile=saved["profile"], start_date=date.today(), weekdays=[date.fromisoformat(t["due_date"]).weekday() for t in saved["tasks"][:3]],
                              connection=config, outcome="独立完成一个可运行的小工具")
        flow = PlanningWindow(window, draft)
        flow.show()
        app.processEvents()
        flow.grab().save(str(output / f"{theme}-wizard.png"))
        flow.draft.receive_turn(PlanningTurn(decision="ask", questions=[ClarificationQuestion(id="output", question="你更希望最后做出哪一种成果？", purpose="用来选择练习和验收方式", options=["命令行小工具", "数据处理脚本", "先巩固基础语法"])], outline=None))
        flow.render()
        app.processEvents()
        flow.grab().save(str(output / f"{theme}-questions.png"))
        flow.draft.answer_questions({"output": "命令行小工具"})
        flow.draft.receive_turn(PlanningTurn(decision="outline", questions=[], outline=PlanOutline(points=[OutlinePoint(id=f"point-{i}", summary=text,
            objective="在可用时间内逐步建立基础", content="先运行短示例，再完成独立练习，最后检查输出。", deliverable="可运行的Python练习脚本", rationale="从当前零基础出发，每天聚焦一个小目标。") for i, text in enumerate([
            "先用短练习熟悉代码运行、输入输出与变量，逐步建立自己动手的信心。",
            "再通过判断、循环与函数处理重复步骤，每天留下可以验证的练习产出。",
            "最后把已有练习组合成命令行小工具，并保留复习与修正错误的时间。"
        ])], assumptions=["已具备可用电脑，优先使用中文资料"])))
        flow.render()
        flow.findChildren(QToolButton)[0].setChecked(True)
        app.processEvents()
        flow.grab().save(str(output / f"{theme}-outline.png"))
        flow.resize(700, 540)
        app.processEvents()
        flow.grab().save(str(output / f"{theme}-outline-compact.png"))
        flow.resize(860, 740)
        flow.draft.candidate = saved["plan"]
        flow.draft.state = "preview"
        flow.render()
        app.processEvents()
        flow.grab().save(str(output / f"{theme}-details.png"))
        row = saved["tasks"][0]
        context = modification_context(saved, [row["id"]], {})
        flow.draft.kind = "modify"
        flow.draft.source_plan_id = saved["id"]
        flow.draft.source_revision = saved["revision"]
        flow.draft.target_ids = context["target_ids"]
        flow.draft.source_context = context
        flow.draft.candidate = None
        flow.draft.proposal = RevisionProposal(summary="只细化选中任务的操作步骤，其他任务和时间保持。", changes=[TaskChange(task_id=row["id"], task=row["task"].model_copy(update={"title": "运行并验证第一段代码", "description": "1.新建hello.py；2.输出自己的问候语；3.修改文字并再次运行，检查输出。"}), due_date=date.fromisoformat(row["due_date"]), reason="把原来的宽泛要求拆成三个能验证的小动作。")])
        flow.render()
        app.processEvents()
        flow.grab().save(str(output / f"{theme}-revision.png"))
        flow.close()
        drafts = DraftsDialog(store, window)
        drafts.show()
        app.processEvents()
        drafts.grab().save(str(output / f"{theme}-drafts.png"))
        drafts.close()
        history = HistoryWindow(store, saved["id"], window)
        history.show()
        app.processEvents()
        history.grab().save(str(output / f"{theme}-history.png"))
        history.close()
        gate = threading.Event()
        window.navigate(4)
        window.start_work("正在测试 JSON 连接…", lambda cancel: gate.wait(10), lambda result: None, timeout_seconds=30)
        app.processEvents()
        window.grab().save(str(output / f"{theme}-countdown.png"))
        window.cancel_request()
        gate.set()
    window.exiting = True
    window.close()
print(output)
