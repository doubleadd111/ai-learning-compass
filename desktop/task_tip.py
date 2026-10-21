"""Bounded task previews using the existing focus-tip surface, never native HTML."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout
from desktop.controls import dropdown_colors
from desktop.focus_tip import RoundedTip

TASK_TIP_ROLE = Qt.ItemDataRole.UserRole + 34


def set_task_tip(item, row, query='', scope='title', *, hint='双击任务查看完整内容'):
    from desktop.task_search import match_details
    from desktop.task_status import display_status
    from datetime import date
    task = row['task']
    item.setData(0, TASK_TIP_ROLE, dict(title=task.title, description=task.description,
        deliverable=task.deliverable, plan=row.get('plan_title') or '独立任务',
        meta=f"{row['due_date']} · {task.duration_minutes} 分钟 · {display_status(row, date.today())}",
        matches=match_details(row, query, scope) if scope=='all' else [], hint=hint))
    # Accessibility fallback remains concise and plain; the delegate handles visual help.
    item.setToolTip(0, task.title)


def preview_text(value, font, width, lines):
    """Wrap arbitrary plain text with an explicit line budget, including long tokens."""
    value = ' '.join(str(value).split())
    layout = QTextLayout(value, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    end = 0
    for index in range(lines):
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(max(1, width - (font.pixelSize() if index==lines-1 else 0)))
        end = line.textStart() + line.textLength()
    layout.endLayout()
    return value[:end].rstrip() + ('…' if end < len(value) else '')


class TaskTip(RoundedTip):
    def __init__(self):
        super().__init__()
        self.setObjectName('taskPreviewPopover')
        box = QVBoxLayout(self)
        box.setContentsMargins(16, 12, 16, 12)
        box.setSpacing(6)
        for name in ('title', 'meta', 'plan', 'matches', 'description_caption', 'description',
                     'deliverable_caption', 'deliverable', 'hint'):
            label = QLabel()
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True)
            setattr(self, name, label)
            box.addWidget(label)

    def present(self, payload, position, anchor):
        from desktop.task_search import match_excerpt
        bounds = (QApplication.screenAt(position) or QApplication.primaryScreen()).availableGeometry()
        width = min(380, bounds.width()-16)
        self.setFixedWidth(width)
        colors = dropdown_colors()
        compact = bounds.height() < 420
        values = dict(payload, description_caption='任务说明', deliverable_caption='完成标准')
        details = payload.get('matches') or []
        values['matches'] = '匹配位置：' + '、'.join(label for label, _, _ in details) if details else ''
        # Show the matching region in its own section, never duplicate the description.
        for field, caption in (('description', '说明'), ('deliverable', '完成标准'), ('plan', '所属计划')):
            match = next(((value, terms) for label, value, terms in details if label==caption), None)
            if match:
                values[field] = match_excerpt(*match, limit=130)
        values['plan'] = '所属：' + values['plan']
        for name in ('title', 'meta', 'plan', 'matches', 'description_caption', 'description',
                     'deliverable_caption', 'deliverable', 'hint'):
            label = getattr(self, name)
            color = colors['accent'] if name=='matches' else colors['ink'] if name in ('title','description','deliverable') else colors['muted']
            size = 14 if name=='title' else 11 if name in ('meta','plan','matches','hint') else 12
            weight = 600 if name=='title' else 400
            label.setStyleSheet(f'background:transparent;border:0;padding:0;color:{color};font-size:{size}px;font-weight:{weight};')
            label.ensurePolished()
            lines = 1 if compact or name not in ('title','description','deliverable') else 3 if name=='description' else 2
            label.setText(preview_text(values.get(name, ''), label.font(), width-32, lines))
            label.setVisible(bool(values.get(name)))
        self.description_caption.setVisible(bool(payload.get('description')))
        self.deliverable_caption.setVisible(bool(payload.get('deliverable')))
        self.adjustSize()
        self.place(position, anchor, timeout=10000)
