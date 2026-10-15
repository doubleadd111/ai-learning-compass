from pathlib import Path

from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap, QPolygonF

from desktop.ui import ButtonFeedback, button_icon
from desktop.controls import DropdownStyle


LIGHT = dict(bg="#F6F7F9", card="#FFFFFF", ink="#202938", muted="#68758A",
             line="#E5E9F0", accent="#356CF6", sidebar="#F0F3F8", hover="#EAF0FF",
             selected="#E5EDFF", pressed="#D4E1FF", primary_hover="#255AE0",
             primary_pressed="#1B48BD", disabled="#EFF1F5", danger="#B73E4C",
             danger_hover="#FFF0F1", track="#E9EDF3", good="#238371")
DARK = dict(bg="#15191F", card="#1D232C", ink="#EBEFF6", muted="#A2AFC1",
            line="#343E4D", accent="#83A9FF", sidebar="#191F28", hover="#293750",
            selected="#283B5C", pressed="#364C71", primary_hover="#2E60DC",
            primary_pressed="#2859CD", disabled="#242B35", danger="#FFA1AA",
            danger_hover="#3B2832", track="#303A49", good="#68C9B1")


def compass_icon(size=128):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2364D8"))
    p.drawRoundedRect(0, 0, size, size, size * .23, size * .23)
    p.setBrush(QColor("#D8E8FF"))
    p.drawEllipse(QPointF(size * .5, size * .5), size * .32, size * .32)
    p.setBrush(QColor("#16365F"))
    p.drawPolygon(QPolygonF([QPointF(size * .69, size * .26), QPointF(size * .59, size * .61), QPointF(size * .31, size * .74), QPointF(size * .41, size * .39)]))
    p.setBrush(QColor("#FFFFFF"))
    p.drawPolygon(QPolygonF([QPointF(size * .69, size * .26), QPointF(size * .5, size * .5), QPointF(size * .41, size * .39)]))
    p.end()
    return QIcon(pixmap)


def apply_theme(app, choice="system"):
    dark = choice == "dark" or (choice == "system" and app.styleHints().colorScheme() == Qt.ColorScheme.Dark)
    colors = DARK if dark else LIGHT
    if not hasattr(app, "_dropdown_style"):
        app._dropdown_style = DropdownStyle("Fusion")
        app.setStyle(app._dropdown_style)
    app.setProperty("dropdownColors", colors)
    bg, card, ink, muted, line = (colors[key] for key in ("bg", "card", "ink", "muted", "line"))
    sidebar, accent, hover, selected, pressed = (colors[key] for key in ("sidebar", "accent", "hover", "selected", "pressed"))
    assets = Path(__file__).with_name("assets").as_posix()
    variant = "dark" if dark else "light"
    if not hasattr(app, "_button_feedback"):
        app._button_feedback = ButtonFeedback(app)
        app.installEventFilter(app._button_feedback)
    app.setProperty("iconColor", muted)
    app.setProperty("accentColor", accent)
    app.setProperty("statusColors", {"未开始": muted, "进行中": accent,
                                     "已完成": colors["good"], "延期": colors["danger"]})
    pal = QPalette()
    for role, color in ((QPalette.ColorRole.Window, bg), (QPalette.ColorRole.Base, card),
                        (QPalette.ColorRole.AlternateBase, bg), (QPalette.ColorRole.Text, ink),
                        (QPalette.ColorRole.WindowText, ink), (QPalette.ColorRole.Button, card),
                        (QPalette.ColorRole.ButtonText, ink), (QPalette.ColorRole.Highlight, selected),
                        (QPalette.ColorRole.HighlightedText, ink), (QPalette.ColorRole.ToolTipBase, card),
                        (QPalette.ColorRole.ToolTipText, ink), (QPalette.ColorRole.PlaceholderText, muted)):
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(muted))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(muted))
    app.setPalette(pal)
    family = "Microsoft YaHei UI" if "Microsoft YaHei UI" in QFontDatabase.families() else "Microsoft YaHei"
    font = QFont(family)
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(f"""
        QWidget {{ color:{ink}; font-family:'{family}'; font-size:13px; }}
        QMainWindow, QDialog {{ background:{bg}; }} QLabel {{ background:transparent; }}
        QLabel#muted, QLabel#eyebrow {{ color:{muted}; }}
        QLabel#eyebrow {{ font-size:11px; font-weight:600; letter-spacing:1px; }}
        QLabel#heading {{ font-size:28px; font-weight:700; }}
        QLabel#brand {{ font-size:18px; font-weight:600; }}
        QLabel#dayNumber {{ font-size:19px; font-weight:600; }}
        QLabel#timer {{ font:80px 'Consolas'; color:{ink}; }}
        QLabel#notice {{ background:{hover}; color:{ink}; padding:12px 16px; border-radius:10px; }}
        QLabel#requestCountdown {{ font:600 14px 'Consolas'; color:{accent}; background:{selected}; padding:8px 12px; border:0; border-radius:8px; }}
        QFrame#sidebar {{ background:{sidebar}; border-right:1px solid {line}; }}
        QFrame#card, QFrame#focusCard {{ background:{card}; border:1px solid {line}; border-radius:14px; }}
        QFrame#card[currentDay="true"] {{ border:1px solid {accent}; background:{hover}; }}
        QPushButton {{ background:{card}; border:1px solid {line}; padding:9px 16px; border-radius:9px; min-height:20px; }}
        QPushButton#taskFocus {{ padding:5px 10px; min-height:20px; border-radius:7px; }}
        QPushButton:hover, QPushButton[hovered="true"] {{ background:{hover}; border-color:{accent}; color:{accent}; }}
        QPushButton:pressed {{ background:{pressed}; border-color:{accent}; color:{accent}; }}
        QPushButton:focus {{ border:1px solid {accent}; }}
        QPushButton:disabled {{ color:{muted}; background:{colors['disabled']}; border-color:{line}; }}
        QPushButton#primary {{ background:#356CF6; color:#FFFFFF; border-color:#356CF6; font-weight:600; }}
        QPushButton#primary:hover, QPushButton#primary[hovered="true"] {{ background:{colors['primary_hover']}; border-color:{colors['primary_hover']}; color:#FFFFFF; }}
        QPushButton#primary:pressed {{ background:{colors['primary_pressed']}; border-color:{colors['primary_pressed']}; }}
        QPushButton#primary:focus {{ border:1px solid {ink}; }}
        QPushButton#primary:disabled {{ background:{colors['disabled']}; border-color:{line}; color:{muted}; }}
        QPushButton#danger {{ color:{colors['danger']}; }}
        QPushButton#danger:hover, QPushButton#danger[hovered="true"] {{ background:{colors['danger_hover']}; border-color:{colors['danger']}; }}
        QPushButton#danger:pressed {{ background:{pressed}; }}
        QPushButton#nav {{ background:transparent; border:1px solid transparent; text-align:left; padding:12px 16px; color:{muted}; }}
        QPushButton#nav:hover, QPushButton#nav[hovered="true"] {{ background:{hover}; color:{ink}; border-color:transparent; }}
        QPushButton#nav:checked {{ background:{card}; color:{accent}; border-color:{line}; font-weight:600; }}
        QPushButton#nav:checked:hover, QPushButton#nav:checked[hovered="true"] {{ background:{selected}; border-color:{accent}; }}
        QPushButton#nav:pressed {{ background:{pressed}; border-color:{accent}; }}
        QPushButton#nav:focus {{ border-color:{accent}; }}
        QPushButton#nav:disabled, QPushButton#danger:disabled {{ background:{colors['disabled']}; color:{muted}; border-color:{line}; }}
        QToolButton {{ background:{card}; color:{ink}; border:1px solid {line}; border-radius:10px; padding:14px 16px; text-align:left; }}
        QToolButton:hover, QToolButton[hovered="true"], QToolButton:checked {{ background:{hover}; border-color:{accent}; color:{accent}; }}
        QToolButton:pressed {{ background:{pressed}; }} QToolButton:focus {{ border-color:{accent}; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDateEdit, QTimeEdit {{
            background:{card}; border:1px solid {line}; border-radius:8px; padding:9px 10px; min-height:20px;
            selection-background-color:{selected}; selection-color:{ink};
        }}
        QComboBox:hover {{ border-color:{accent}; background:{hover}; }}
        QComboBox:on {{ border-color:{accent}; background:{selected}; }}
        QComboBox:disabled {{ background:{colors['disabled']}; color:{muted}; border-color:{line}; }}
        QSpinBox:hover, QDateEdit:hover, QTimeEdit:hover, QLineEdit:hover {{ border-color:{muted}; }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDateEdit:focus, QTimeEdit:focus {{ border-color:{accent}; }}
        QTextBrowser#reading {{ background:{card}; border:1px solid {line}; border-radius:12px; padding:16px; font-size:14px; }}
        QFrame#weekDay {{ background:{card}; border:1px solid {line}; border-radius:10px; }}
        QFrame#weekDay[currentDay="true"] {{ border-color:{accent}; }}
        QFrame#weekTask {{ background:{bg}; border:1px solid {line}; border-radius:8px; }}
        QFrame#weekTask:hover, QFrame#weekTask[selected="true"], QFrame#weekTask:focus {{ border-color:{accent}; }}
        QLabel#taskStatus {{ color:{muted}; }}
        QLabel#taskStatus[taskState="进行中"] {{ color:{accent}; }}
        QLabel#taskStatus[taskState="已完成"] {{ color:{colors['good']}; }}
        QLabel#taskStatus[taskState="延期"] {{ color:{colors['danger']}; }}
        QComboBox {{ padding-right:28px; }} QComboBox::drop-down {{ border:0; width:28px; }}
        QComboBox::down-arrow, QDateEdit::down-arrow {{ image:url("{assets}/chevron-{variant}.svg"); width:14px; height:14px; }}
        QSpinBox, QTimeEdit {{ padding-right:28px; }}
        QSpinBox::up-button, QTimeEdit::up-button {{ subcontrol-origin:border; subcontrol-position:top right; width:25px; border:0; background:transparent; }}
        QSpinBox::down-button, QTimeEdit::down-button {{ subcontrol-origin:border; subcontrol-position:bottom right; width:25px; border:0; background:transparent; }}
        QDateEdit::drop-down {{ border:0; width:28px; }}
        QSpinBox::up-arrow, QTimeEdit::up-arrow {{ image:url("{assets}/up-{variant}.svg"); width:10px; height:7px; }}
        QSpinBox::down-arrow, QTimeEdit::down-arrow {{ image:url("{assets}/chevron-{variant}.svg"); width:10px; height:9px; }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover, QTimeEdit::up-button:hover, QTimeEdit::down-button:hover {{ background:{hover}; border-radius:5px; }}
        QComboBox QAbstractItemView {{ background:{card}; color:{ink}; padding:6px; border:1px solid {line}; selection-background-color:{selected}; selection-color:{ink}; outline:0; }}
        QFrame#softComboPopup {{ background:{card}; border:1px solid {line}; border-radius:12px; }}
        QListView#softComboList {{ background:transparent; border:0; padding:0; outline:0; }}
        QCheckBox {{ spacing:9px; padding:5px 0; }} QCheckBox:hover {{ color:{accent}; }}
        QCheckBox::indicator {{ width:17px; height:17px; background:{card}; border:1px solid {muted}; border-radius:5px; }}
        QCheckBox::indicator:hover {{ border-color:{accent}; background:{hover}; }}
        QCheckBox::indicator:checked {{ background:#356CF6; border-color:#356CF6; image:url("{assets}/check.svg"); }}
        QCheckBox::indicator:disabled {{ background:{colors['disabled']}; border-color:{line}; }}
        QListWidget, QTreeWidget, QTableWidget {{ background:{card}; alternate-background-color:{card}; border:1px solid {line}; border-radius:12px; padding:5px; outline:0; }}
        QListWidget::item {{ padding:15px 14px; margin:3px; border:1px solid transparent; border-radius:9px; }}
        QListWidget::item:hover, QTreeWidget::item:hover {{ background:{hover}; }}
        QListWidget::item:selected {{ background:{selected}; border-color:{accent}; color:{ink}; }}
        QTreeWidget::item:selected, QTableWidget::item:selected {{ background:{selected}; color:{ink}; }}
        QTreeWidget::item {{ padding:11px 8px; border:0; }}
        QHeaderView::section {{ background:{card}; color:{muted}; font-weight:600; padding:12px 8px; border:0; border-bottom:1px solid {line}; }}
        QTabWidget::pane {{ border:0; background:transparent; }}
        QTabBar {{ background:{sidebar}; border-radius:10px; }}
        QTabBar::tab {{ background:transparent; color:{muted}; padding:10px 22px; border:1px solid transparent; border-radius:8px; margin:3px; }}
        QTabBar::tab:hover {{ background:{hover}; color:{accent}; }}
        QTabBar::tab:selected {{ background:{card}; color:{accent}; border-color:{line}; font-weight:600; }}
        QTabBar::tab:focus {{ border-color:{accent}; }}
        QScrollArea {{ border:0; background:transparent; }}
        QScrollBar:vertical {{ background:transparent; width:9px; margin:2px; }}
        QScrollBar:horizontal {{ background:transparent; height:9px; margin:2px; }}
        QScrollBar::handle {{ background:{line}; border-radius:3px; min-height:28px; min-width:28px; }}
        QScrollBar::handle:hover {{ background:{muted}; }}
        QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
        QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}
        QProgressBar {{ border:0; background:{colors['track']}; height:6px; max-height:6px; border-radius:3px; }}
        QProgressBar::chunk {{ background:{colors['good']}; border-radius:3px; }}
        QGroupBox {{ background:{card}; border:1px solid {line}; border-radius:12px; margin-top:16px; padding:18px; }}
        QGroupBox::title {{ subcontrol-origin:margin; padding:0 8px; }}
        QMenu {{ background:{card}; border:1px solid {line}; border-radius:8px; padding:6px; }}
        QMenu::item {{ padding:8px 24px; border-radius:5px; }} QMenu::item:selected {{ background:{selected}; }}
        QToolTip {{ background:{card}; color:{ink}; border:1px solid {line}; padding:6px; }}
    """)
    for widget in app.allWidgets():
        if widget.property("lineIcon"):
            button_icon(widget, widget.property("lineIcon"))
