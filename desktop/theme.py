from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPalette, QPixmap, QPolygonF


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
    bg, card, ink, muted, line, sidebar = ("#151F2E", "#1E2C3F", "#E9EFF8", "#A6B7CD", "#32435B", "#192639") if dark else ("#F5F7FB", "#FFFFFF", "#162B45", "#62748D", "#DDE4EF", "#EDF2F9")
    pal = QPalette()
    for role, color in ((QPalette.ColorRole.Window, bg), (QPalette.ColorRole.Base, card),
                        (QPalette.ColorRole.AlternateBase, bg), (QPalette.ColorRole.Text, ink),
                        (QPalette.ColorRole.WindowText, ink), (QPalette.ColorRole.Button, card),
                        (QPalette.ColorRole.ButtonText, ink), (QPalette.ColorRole.Highlight, "#2364D8"),
                        (QPalette.ColorRole.HighlightedText, "#FFFFFF"), (QPalette.ColorRole.ToolTipBase, card),
                        (QPalette.ColorRole.ToolTipText, ink), (QPalette.ColorRole.PlaceholderText, muted)):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    family = "Microsoft YaHei UI" if "Microsoft YaHei UI" in QFontDatabase.families() else "Microsoft YaHei"
    app.setFont(QFont(family, 10))
    app.setStyleSheet(f"""
        QWidget {{ color:{ink}; }} QMainWindow, QDialog {{ background:{bg}; }}
        QLabel#muted {{ color:{muted}; }} QLabel#heading {{ font-size:26px; font-weight:700; }}
        QLabel#brand {{ font-size:21px; font-weight:700; }} QLabel#timer {{ font:64px 'Consolas'; color:#3E80F2; }}
        QFrame#sidebar {{ background:{sidebar}; border-right:1px solid {line}; }}
        QFrame#card {{ background:{card}; border:1px solid {line}; border-radius:10px; }}
        QPushButton {{ background:{card}; border:1px solid {line}; padding:9px 16px; border-radius:7px; }}
        QPushButton:hover {{ border-color:#4282E7; }} QPushButton:focus {{ border:2px solid #4282E7; }}
        QPushButton:disabled {{ color:{muted}; background:{bg}; }}
        QPushButton#primary {{ background:#2364D8; color:white; border:1px solid #2364D8; font-weight:600; }}
        QPushButton#primary:hover {{ background:#1B52BA; }} QPushButton#danger {{ color:#D05A5A; }}
        QPushButton#nav {{ background:transparent; border:0; text-align:left; padding:13px 16px; }}
        QPushButton#nav:checked {{ background:{card}; color:#3E80F2; font-weight:700; }}
        QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDateEdit, QTimeEdit {{
            background:{card}; border:1px solid {line}; border-radius:6px; padding:7px; selection-background-color:#2364D8;
        }}
        QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border:1px solid #4282E7; }}
        QListWidget, QTreeWidget, QTableWidget {{ background:{card}; border:1px solid {line}; border-radius:8px; outline:0; }}
        QListWidget::item {{ padding:12px; border-bottom:1px solid {line}; }}
        QListWidget::item:selected, QTreeWidget::item:selected, QTableWidget::item:selected {{ background:#2364D8; color:white; }}
        QTreeWidget::item {{ padding:7px 4px; }} QHeaderView::section {{ background:{sidebar}; padding:9px; border:0; border-bottom:1px solid {line}; }}
        QScrollArea {{ border:0; background:transparent; }} QProgressBar {{ border:0; background:{line}; height:7px; border-radius:3px; }}
        QProgressBar::chunk {{ background:#168879; border-radius:3px; }} QGroupBox {{ border:1px solid {line}; border-radius:8px; margin-top:16px; padding-top:18px; }}
        QGroupBox::title {{ subcontrol-origin:margin; padding:0 8px; }}
    """)
