"""Plan menu using the same rounded surface as the app's dropdowns."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QFrame, QVBoxLayout, QPushButton, QLabel

from desktop.controls import PopupSurface


class PlanActionsPopup(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(230)
        self._surface = PopupSurface(self)
        self.installEventFilter(self._surface)
        box = QVBoxLayout(self)
        box.setContentsMargins(8, 10, 8, 8)
        box.setSpacing(6)
        caption = QLabel("危险操作")
        caption.setObjectName("eyebrow")
        caption.setContentsMargins(10, 0, 10, 0)
        box.addWidget(caption)
        self.delete_action = QAction("删除计划…", self)
        self.delete_button = QPushButton("删除计划…")
        self.delete_button.setObjectName("planDangerAction")
        self.delete_button.setAccessibleDescription("需要输入确认删除四个字，不会直接删除")
        self.delete_button.clicked.connect(self._activate)
        self.delete_action.changed.connect(lambda: self.delete_button.setEnabled(self.delete_action.isEnabled()))
        box.addWidget(self.delete_button)

    def _activate(self):
        self.hide()
        self.delete_action.trigger()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def popup(self, position):
        self.adjustSize()
        area = self.screen().availableGeometry()
        x = min(max(area.left() + 8, position.x()), area.right() - self.width() - 8)
        y = min(max(area.top() + 8, position.y() + 6), area.bottom() - self.height() - 8)
        self.move(x, y)
        self.show()
        self.delete_button.setFocus()
