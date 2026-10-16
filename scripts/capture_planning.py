"""Render the planning patch with synthetic data; never call a model or real Key."""
import os
from pathlib import Path
import sys
import tempfile
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import QApplication
from desktop.credentials import CredentialStore
from desktop.models import DesktopProfile
from desktop.planning import PlanningDraft, PlanningTurn, PlanOutline, OutlinePoint
from desktop.planning_ui import PlanningWindow
from desktop.planning_widgets import OutlineCard
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.window import MainWindow


def main():
    app = QApplication([])
    if sys.platform == 'win32':
        for name in ('msyh.ttc', 'msyhbd.ttc', 'consola.ttf'):
            QFontDatabase.addApplicationFont(str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name))
    output = ROOT / '.learning_compass/qa/planning-patch-b'
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        owner = MainWindow(Store(Path(directory)), credentials=CredentialStore(object()), enable_tray=False)
        profile = DesktopProfile(subject='项目开发', goal='完善学习罗盘，完成手机与电脑同步的最小可用版本',
                                 current_level='已有基础', plan_weeks=8, days_per_week=5, minutes_per_day=45,
                                 preferences='界面简洁，优先稳定性')
        draft = PlanningDraft(profile=profile, start_date=date(2026, 10, 19), weekdays=list(range(5)))
        outline = PlanOutline(points=[OutlinePoint(id=f'point-{i}', summary=text,
            objective='完成一个可独立验证的阶段目标',
            content='先用已有样例检查当前行为，再实现一个小改动。\n完成后验证正常情况与失败情况，并记录结果。',
            deliverable='可运行的功能、对应测试与一段使用说明', rationale='先稳定核心流程，再扩展跨端功能，避免同时引入过多新内容。')
            for i, text in enumerate((
                '先补齐周视图测试与轻量重构，让核心功能可以稳定演示。',
                '用 FastAPI 暴露接口，选择同步方案实现手机可用的极简前端。',
                '完善文档、许可证与部署，发布公开仓库并提供在线演示。'))],
            assumptions=['保留 PySide6 桌面功能，新增接口层而不重写桌面。',
                         '八周内只实现一种同步方案，暂不做离线冲突合并。',
                         '密钥由用户自备，不随计划导出。'])
        draft.receive_turn(PlanningTurn(decision='outline', questions=[], outline=outline))
        flow = PlanningWindow(owner, draft)
        for theme in ('light', 'dark'):
            apply_theme(app, theme)
            flow.resize(860, 820)
            flow.render()
            flow.show()
            app.processEvents()
            flow.grab().save(str(output / f'{theme}-outline.png'))
            flow.findChildren(OutlineCard)[-3].toggle.setChecked(True)
            app.processEvents()
            flow.grab().save(str(output / f'{theme}-expanded.png'))
            flow.resize(700, 540)
            app.processEvents()
            flow.grab().save(str(output / f'{theme}-compact.png'))
        flow.close()
        owner.exiting = True
        owner.close()
    print(str(output))


if __name__ == '__main__':
    main()
