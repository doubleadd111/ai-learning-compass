"""Categorized action menus on the same rounded surface as other dropdowns."""
from PySide6.QtCore import Qt, QEvent,QPointF
from PySide6.QtGui import QAction,QPainter,QPen,QColor
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QPushButton, QLabel, QSizePolicy, QScrollArea, QWidget
from desktop.controls import PopupSurface,dropdown_colors


class ActionMenu(QFrame):
    def __init__(self, parent=None, *, scrollable=False):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setMinimumWidth(240)
        self._surface = PopupSurface(self)
        self.installEventFilter(self._surface)
        self.scroll = None
        if scrollable:
            outer = QVBoxLayout(self)
            outer.setContentsMargins(2, 2, 2, 2)
            self.scroll = QScrollArea()
            self.scroll.setFrameShape(QFrame.Shape.NoFrame)
            self.scroll.setWidgetResizable(True)
            self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.scroll.setStyleSheet('QScrollArea, QScrollArea > QWidget > QWidget { background:transparent; border:0; }')
            self.content = QWidget()
            self.scroll.setWidget(self.content)
            outer.addWidget(self.scroll)
            self.box = QVBoxLayout(self.content)
        else:
            self.box = QVBoxLayout(self)
        self.box.setContentsMargins(8, 10, 8, 10)
        self.box.setSpacing(4)
        self.items = []

    def add_action(self, title, callback, *, danger=False, checkable=False):
        action = QAction(title, self)
        action.setCheckable(checkable)
        button = QPushButton(title)
        button.setObjectName('planDangerAction' if danger else 'menuAction')
        button.setProperty('compactMenu',self.scroll is not None)
        button.setCheckable(checkable)
        button.setAutoDefault(False)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.installEventFilter(self)
        def activate():
            self.hide()
            action.trigger()
        button.clicked.connect(activate)
        def sync():
            button.setText(('✓  ' if action.isCheckable() and action.isChecked() else '') + action.text())
            button.setEnabled(action.isEnabled())
            button.setChecked(action.isChecked())
        action.changed.connect(sync)
        action.triggered.connect(callback)
        self.box.addWidget(button)
        self.items.append((action, button))
        return action

    def section(self, title):
        label = QLabel(title)
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setWordWrap(True)
        label.setObjectName('eyebrow')
        label.setContentsMargins(10, 8, 10, 0)
        self.box.addWidget(label)

    def popup(self, position):
        area = (QApplication.screenAt(position) or self.screen()).availableGeometry().adjusted(8,8,-8,-8)
        if self.scroll:
            self.setFixedWidth(min(320,area.width()))
            self.content.setMinimumHeight(self.content.sizeHint().height())
            self.setFixedHeight(min(self.content.minimumHeight()+4,area.height()))
        else:
            self.adjustSize()
        self.move(max(area.left(),min(position.x(),area.right()-self.width()+1)),
                  max(area.top(),min(position.y()+6,area.bottom()-self.height()+1)))
        self.show()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFocus()
        self.active_index = -1
        self.highlight(-1)

    def highlight(self, index):
        self.active_index = index
        for i, (_, button) in enumerate(self.items):
            button.setProperty('menuActive', i == index)
            button.setProperty('hovered', False)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()
        if self.scroll and 0 <= index < len(self.items):
            self.scroll.ensureWidgetVisible(self.items[index][1],0,4)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Enter:
            for i, (action, button) in enumerate(self.items):
                if watched is button:
                    self.highlight(i if action.isEnabled() else -1)
                    break
        elif event.type() == QEvent.Type.Leave:
            if any(watched is button for _, button in self.items):
                self.highlight(-1)
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        if event.key() in (Qt.Key.Key_Down, Qt.Key.Key_Up):
            enabled = [i for i, (action, _) in enumerate(self.items) if action.isEnabled()]
            if enabled:
                pos = enabled.index(self.active_index) if self.active_index in enabled else (-1 if event.key() == Qt.Key.Key_Down else 0)
                self.highlight(enabled[(pos + (1 if event.key() == Qt.Key.Key_Down else -1)) % len(enabled)])
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if 0 <= self.active_index < len(self.items) and self.items[self.active_index][0].isEnabled():
                self.items[self.active_index][1].click()
            return
        super().keyPressEvent(event)


class MenuButton(QPushButton):
    def paintEvent(self,event):
        super().paintEvent(event)
        p=QPainter(self);p.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors=dropdown_colors();p.setPen(QPen(QColor(colors['accent'] if self.underMouse() else colors['muted']),1.5))
        x,y=self.width()-16,self.height()/2
        p.drawLine(QPointF(x-3,y-2),QPointF(x,y+1));p.drawLine(QPointF(x,y+1),QPointF(x+3,y-2));p.end()


def menu_button(title, entries, parent=None):
    button = MenuButton(title + '   ', parent)
    button.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    menu = ActionMenu(button,scrollable=True)
    button.action_menu = menu
    for label, callback in entries:
        menu.add_action(label, lambda _=False, cb=callback: cb())
    button.clicked.connect(lambda: menu.popup(button.mapToGlobal(button.rect().bottomLeft())))
    return button
