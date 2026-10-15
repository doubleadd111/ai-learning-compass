"""The packaged Markdown is the only source of the offline user guide."""
import re
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QTextBrowser, QVBoxLayout

from desktop import VERSION
from desktop.ui import readable_document, format_reading_blocks

CHAPTERS = {
    "quickstart": "首次体验", "models": "接入自己的模型", "create": "创建学习计划",
    "modify": "修改与保护计划", "records": "记录与每周复盘", "focus": "专注与提醒",
    "backup": "数据与备份", "errors": "常见问题", "build": "从源码运行与构建",
}


def guide_sections():
    source = Path(__file__).resolve().parents[1] / "docs" / "desktop-guide.md"
    markdown = source.read_text(encoding="utf-8")
    if f"`{VERSION}`" not in markdown:
        raise ValueError("使用指南与程序版本不一致")
    parts = re.split(r"^## (.+)$", markdown, flags=re.MULTILINE)
    sections = dict(zip(parts[1::2], parts[2::2]))
    return {key: (title, "## " + title + "\n" + sections[title]) for key, title in CHAPTERS.items()}


class GuideWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setWindowTitle(f"使用指南 · 学习罗盘 {VERSION}")
        self.resize(900, 680)
        self.setMinimumSize(680, 480)
        self.sections = guide_sections()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        heading = QLabel(f"使用指南  /  {VERSION}")
        heading.setObjectName("brand")
        layout.addWidget(heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索操作、功能或错误，例如：修改、API Key、恢复")
        self.search.setAccessibleName("搜索使用指南")
        layout.addWidget(self.search)
        row = QHBoxLayout()
        self.toc = QListWidget()
        self.toc.setMaximumWidth(210)
        self.browser = QTextBrowser()
        readable_document(self.browser)
        self.browser.setOpenExternalLinks(True)
        row.addWidget(self.toc)
        row.addWidget(self.browser, 1)
        layout.addLayout(row, 1)
        self.hint = QLabel("离线说明随软件更新；查看指南不会使用 API 额度。")
        self.hint.setObjectName("muted")
        layout.addWidget(self.hint)
        self.toc.currentItemChanged.connect(self.show_chapter)
        self.search.textChanged.connect(self.filter_chapters)
        self.filter_chapters("")

    def filter_chapters(self, text):
        self.toc.clear()
        query = text.strip().casefold()
        for key, (title, content) in self.sections.items():
            if not query or query in content.casefold():
                item = QListWidgetItem(title)
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.toc.addItem(item)
        if self.toc.count():
            self.toc.setCurrentRow(0)
        else:
            self.browser.setPlainText("没有找到相关说明。试试“创建计划”“模型”或“备份”等关键词。")

    def show_chapter(self, item, *_args):
        if item:
            key = item.data(Qt.ItemDataRole.UserRole)
            self.browser.setMarkdown(self.sections[key][1])
            format_reading_blocks(self.browser)
            if self.search.text().strip():
                self.browser.find(self.search.text().strip())

    def go(self, chapter=None):
        if chapter is not None:
            self.search.clear()
            for row in range(self.toc.count()):
                if self.toc.item(row).data(Qt.ItemDataRole.UserRole) == chapter:
                    self.toc.setCurrentRow(row)
                    break
        if self.isMinimized():
            self.showNormal()
        else:
            self.show()
        self.raise_()
        self.activateWindow()


def open_guide(owner, chapter=None):
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if not getattr(app, "_learning_guide", None):
        app._learning_guide = GuideWindow()
    # Alias retained for callers/tests; lifetime and window ownership are global.
    owner._guide_window = app._learning_guide
    app._learning_guide.go(chapter)
    return app._learning_guide


def close_guide():
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if getattr(app, "_learning_guide", None):
        app._learning_guide.close()
