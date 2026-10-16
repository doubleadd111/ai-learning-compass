"""Long plan names must not expand the library or hide completion counts."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from html import escape
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics
from PySide6.QtWidgets import QApplication, QInputDialog, QListWidgetItem, QStyleOptionViewItem

from desktop.credentials import CredentialStore
from desktop.gateway import instructions, user_prompt
from desktop.models import DesktopProfile, ModelConnection
from desktop.plan_list import PlanListDelegate, TITLE_ROLE, SUBJECT_ROLE, PROGRESS_ROLE, populate_plan_item
from desktop.store import Store
from desktop.theme import apply_theme
from desktop.window import MainWindow
from tests.test_desktop_store import create
from tests.test_progress_store import sample_plan, sample_profile


@pytest.fixture
def ui(qtbot, tmp_path):
    original_font = QFont(QApplication.instance().font())
    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/msyh.ttc"
    font_id = (QFontDatabase.addApplicationFont(str(font))
               if font.exists() and "Microsoft YaHei UI" not in QFontDatabase.families() else -1)
    apply_theme(QApplication.instance(), "light")
    store = Store(tmp_path)
    store.set_setting("reminders_paused", True)
    window = MainWindow(store, credentials=CredentialStore(object()), enable_tray=False)
    qtbot.addWidget(window)
    window.show()
    window.navigate(1)
    yield window
    window.exiting = True
    window.close()
    if font_id >= 0:
        QFontDatabase.removeApplicationFont(font_id)
    QApplication.instance().setFont(original_font)


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("width", [215, 255])
@pytest.mark.parametrize("title", ["软件完善与双端同步学习计划" * 7, "UnbrokenLongProjectName" * 4, "短计划"])
def test_card_elides_each_line_and_retains_progress(ui, tmp_path, qtbot, theme, width, title):
    pid = create(ui.store)
    title = title[:100]
    ui.store.rename_plan(pid, title)
    # Saving a long custom subject remains supported; no real user data involved.
    saved = ui.store.get_plan(pid)
    profile = saved["profile"].model_copy(update={"subject": "以现有软件为基础进一步完善学习与同步功能" * 3})
    other = ui.store.save_plan(profile, sample_plan(), profile_start(), [0, 2])
    ui.store.rename_plan(other, title)
    before = ui.store.export_plan(other)
    ui.selected_plan_id = other
    ui.refresh()
    apply_theme(QApplication.instance(), theme)
    ui.plan_list.setFixedWidth(width)
    qtbot.wait(50)
    item = ui.plan_list.currentItem()
    assert isinstance(ui.plan_list.itemDelegate(), PlanListDelegate)
    index = ui.plan_list.indexFromItem(item)
    rect = ui.plan_list.visualRect(index)
    lines = ui.plan_list.itemDelegate().lines(index, ui.plan_list.font(), rect.width() - 34)
    assert all(QFontMetrics(font).horizontalAdvance(text) <= rect.width() - 34 for text, font in lines)
    if len(title) > 30:
        assert lines[0][0].endswith("\u2026")
    assert lines[1][0].endswith("\u2026")
    assert lines[2][0] == "0/4 项完成"
    assert not ui.plan_list.horizontalScrollBar().isVisible()
    assert rect.width() <= ui.plan_list.viewport().width()
    assert escape(title) in item.toolTip() and escape(profile.subject) in item.toolTip()
    assert item.data(TITLE_ROLE) == ui.plan_title.text() == title
    assert item.data(Qt.ItemDataRole.AccessibleTextRole) == item.text()
    assert before == ui.store.export_plan(other)
    assert ui.plan_list.grab().save(str(tmp_path / f"cards-{theme}-{width}.png"))


def profile_start():
    from datetime import date
    return date(2026, 10, 12)


def test_same_names_selection_search_rename_and_refresh_keep_ids(ui, monkeypatch):
    first, second = create(ui.store), create(ui.store)
    title = "计划名称很长但后缀仍然可以搜索" * 4
    ui.store.rename_plan(first, title + "甲")
    ui.store.rename_plan(second, title + "乙")
    ui.selected_plan_id = second
    ui.refresh()
    assert ui.plan_list.currentItem().data(Qt.ItemDataRole.UserRole) == second
    ui.search.setText("乙")
    assert ui.plan_list.count() == 1
    assert ui.plan_list.currentItem().data(Qt.ItemDataRole.UserRole) == second
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **kw: ("我的简短计划", True))
    ui.rename_selected()
    ui.search.clear()
    ui.refresh()
    item = next(ui.plan_list.item(i) for i in range(ui.plan_list.count())
                if ui.plan_list.item(i).data(Qt.ItemDataRole.UserRole) == second)
    ui.plan_list.setCurrentItem(item)
    assert item.data(TITLE_ROLE) == ui.plan_title.text() == "我的简短计划"
    assert len(ui.store.tasks(second)) == 4


def test_tooltip_escapes_markup_and_size_is_independent_of_text(ui):
    short, long = QListWidgetItem(), QListWidgetItem()
    populate_plan_item(short, dict(id="a", title="短名", completed=0, total=40), "Python")
    populate_plan_item(long, dict(id="b", title="<b>" + "很长的标题" * 15, completed=40, total=40), "<img src=x>")
    for item in (short, long):
        ui.plan_list.addItem(item)
    delegate = ui.plan_list.itemDelegate()
    option = QStyleOptionViewItem()
    option.font = ui.plan_list.font()
    assert delegate.sizeHint(option, ui.plan_list.indexFromItem(short)) == delegate.sizeHint(option, ui.plan_list.indexFromItem(long))
    assert "&lt;b&gt;" in long.toolTip() and "&lt;img src=x&gt;" in long.toolTip()
    assert long.data(PROGRESS_ROLE) == "40/40 项完成"
    assert long.data(SUBJECT_ROLE) == "<img src=x>"


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_small_window_resize_keyboard_and_hover(ui, tmp_path, qtbot, theme):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QColor, QHoverEvent, QMouseEvent
    create(ui.store)
    pid = create(ui.store)
    ui.store.rename_plan(pid, "长标题的软件完善计划" * 8)
    ui.refresh()
    apply_theme(QApplication.instance(), theme)
    ui.resize(980, 680)
    qtbot.wait(60)
    ui.plan_list.setCurrentRow(0)
    QTest.keyClick(ui.plan_list, Qt.Key.Key_Down)
    assert ui.selected_plan_id == ui.plan_list.item(1).data(Qt.ItemDataRole.UserRole)
    ui.plan_list.setCurrentRow(0)
    rect = ui.plan_list.visualItemRect(ui.plan_list.item(1))
    point = rect.center()
    # Compare a blank part of the nonselected card before/after hover.
    point.setY(rect.bottom() - 8)
    def move_to(position):
        viewport = ui.plan_list.viewport()
        # Deliver logical Qt events: native OS mouse simulation is unreliable
        # at Windows 200% scaling. This is a component, not physical-pointer test.
        QApplication.sendEvent(viewport, QMouseEvent(QEvent.Type.MouseMove, QPointF(position),
            QPointF(viewport.mapToGlobal(position)), Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        QApplication.sendEvent(viewport, QHoverEvent(QEvent.Type.HoverMove, QPointF(position),
            QPointF(viewport.mapToGlobal(position)), QPointF(-1, -1)))
        QApplication.processEvents()
    move_to(ui.plan_list.viewport().rect().bottomRight())
    qtbot.wait(30)
    def color_at():
        image = ui.plan_list.viewport().grab().toImage()
        dpr = image.devicePixelRatio()
        return image.pixelColor(round(point.x() * dpr), round(point.y() * dpr))
    before = color_at()
    move_to(point)
    qtbot.wait(60)
    after = color_at()
    assert before != after
    assert after == QColor(QApplication.instance().property("dropdownColors")["hover"])
    assert not ui.plan_list.horizontalScrollBar().isVisible()
    assert ui.grab().save(str(tmp_path / f"library-small-{theme}.png"))
    ui.resize(1400, 900)
    qtbot.wait(40)
    assert not ui.plan_list.horizontalScrollBar().isVisible()


@pytest.mark.parametrize("subject", ["Python", "英语", "考试复习", "以现有学习软件为基础进一步完善"])
def test_short_name_guidance_does_not_break_legacy_or_generate_extra_requests(subject):
    from desktop.gateway import ModelGateway
    from tests.test_desktop_gateway import Client
    profile = DesktopProfile(**sample_profile().model_dump(), subject=subject)
    for prompt in (instructions(profile), user_prompt(profile)):
        assert "8–20字" in prompt and "overview" in prompt
    original = sample_plan().model_copy(update={"title": "旧版完整学习计划" * 12})
    client = Client([original.model_dump_json()])
    gateway = ModelGateway(ModelConnection(name="本地Mock", base_url="https://example.invalid", model="fake"), "not-a-real-key", client=client)
    generated, _ = gateway.generate(profile)
    assert generated.title == original.title
    assert len(client.calls) == 1
