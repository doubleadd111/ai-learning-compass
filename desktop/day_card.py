"""Keyboard and mouse accessible date card; selection is presentation-only."""
from PySide6.QtCore import Qt,Signal
from PySide6.QtWidgets import QFrame


class DayCard(QFrame):
    clicked=Signal()

    def __init__(self,parent=None):
        super().__init__(parent);self.setObjectName('card');self.setProperty('dateCard',True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus);self.setCursor(Qt.CursorShape.PointingHandCursor);self._pressed=False;self.day=None

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:self._pressed=True;self.setFocus();event.accept()
        else:super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            pressed=self._pressed;self._pressed=False
            if pressed and self.rect().contains(event.position().toPoint()):self.clicked.emit()
            event.accept()
        else:super().mouseReleaseEvent(event)

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Space):self.clicked.emit();event.accept()
        elif event.key() in (Qt.Key.Key_Left,Qt.Key.Key_Right):self.focusNextPrevChild(event.key()==Qt.Key.Key_Right);event.accept()
        else:super().keyPressEvent(event)
