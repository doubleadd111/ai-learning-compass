"""Plain text form fields with real reading space, using the existing theme."""
from math import ceil
from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QTextDocument, QTextOption
from PySide6.QtWidgets import QPlainTextEdit, QSizePolicy, QScrollArea, QFrame, QWidget, QFormLayout


class ReadableTextEdit(QPlainTextEdit):
    def __init__(self, value='', parent=None, *, min_lines=4, max_lines=8):
        super().__init__(parent)
        self.min_lines, self.max_lines = min_lines, max_lines
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTabChangesFocus(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._measure=QTimer(self);self._measure.setSingleShot(True)
        self._measure.timeout.connect(self.fit_reading_height)
        self.textChanged.connect(self.queue_measure)
        self.setPlainText(value)
        self.fit_reading_height()

    def text(self):
        return self.toPlainText()

    def setText(self, value):
        self.setPlainText(value)

    def queue_measure(self):
        self._measure.start(0)

    def fit_reading_height(self):
        line=max(self.fontMetrics().lineSpacing(),self.fontMetrics().height())
        document=QTextDocument();document.setDefaultFont(self.font())
        document.setDocumentMargin(0);document.setDefaultTextOption(self.document().defaultTextOption())
        document.setPlainText(self.toPlainText());document.setTextWidth(max(40,self.viewport().width()))
        padding=max(32,self.height()-self.viewport().height())
        height=ceil(max(self.min_lines*line,min(self.max_lines*line,document.size().height()))+padding)
        if self.minimumHeight()!=height or self.maximumHeight()!=height:
            self.setFixedHeight(height)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'_measure'):self.queue_measure()

    def changeEvent(self,event):
        super().changeEvent(event)
        if hasattr(self,'_measure') and event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):self.queue_measure()

    def sizeHint(self):
        return QSize(super().sizeHint().width(),self.minimumHeight())


def form_scroll(form):
    """Scroll long forms without shrinking multiline fields or footer actions."""
    form.setVerticalSpacing(12);form.setHorizontalSpacing(18)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setContentsMargins(0,0,12,0)
    content=QWidget();content.setLayout(form)
    scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setWidget(content)
    return scroll
