"""Transient drag feedback, using the existing theme; no scheduling writes."""
from PySide6.QtCore import Qt,QRectF,QPoint,QVariantAnimation,QEasingCurve
from PySide6.QtGui import QColor,QPainter,QPen,QPixmap
from PySide6.QtWidgets import QApplication,QGraphicsOpacityEffect
from desktop.controls import dropdown_colors


def motion_enabled():
    return QApplication.isEffectEnabled(Qt.UIEffect.UI_General)


def drag_preview(source):
    """A logical-pixel card; preserve sharp text at Windows high DPI."""
    ratio=source.devicePixelRatioF();width,height=244,78
    pixmap=QPixmap(round(width*ratio),round(height*ratio));pixmap.setDevicePixelRatio(ratio);pixmap.fill(Qt.GlobalColor.transparent)
    colors=dropdown_colors();p=QPainter(pixmap);p.setRenderHint(QPainter.RenderHint.Antialiasing)
    shadow=QColor(colors['ink']);shadow.setAlpha(25);p.setPen(Qt.PenStyle.NoPen);p.setBrush(shadow)
    p.drawRoundedRect(QRectF(4,6,width-8,height-8),11,11)
    p.setBrush(QColor(colors['card']));p.setPen(QPen(QColor(colors['accent']),1.5));p.drawRoundedRect(QRectF(2,2,width-8,height-10),10,10)
    font=source.font();font.setBold(True);p.setFont(font);p.setPen(QColor(colors['ink']))
    p.drawText(QRectF(14,12,width-32,22),Qt.AlignmentFlag.AlignVCenter,p.fontMetrics().elidedText(source.task['task'].title,Qt.TextElideMode.ElideRight,width-32))
    font.setBold(False);font.setPixelSize(12);p.setFont(font);p.setPen(QColor(colors['muted']))
    p.drawText(QRectF(14,39,width-32,20),Qt.AlignmentFlag.AlignVCenter,'松手查看改期预览')
    p.end();return pixmap,QPoint(22,18)


class DragOpacity:
    def __init__(self,source):
        self.source=source;self.effect=QGraphicsOpacityEffect(source);self.effect.setOpacity(1.0);source.setGraphicsEffect(self.effect)
        self.animation=QVariantAnimation(source);self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(lambda value:self.effect.setOpacity(float(value)))

    def set_dragging(self,active):
        self.animation.stop();target=.45 if active else 1.0
        if not motion_enabled():self.effect.setOpacity(target);return
        self.animation.setDuration(110 if active else 150);self.animation.setStartValue(self.effect.opacity());self.animation.setEndValue(target);self.animation.start()

    def reset(self):
        self.animation.stop();self.effect.setOpacity(1.0)
