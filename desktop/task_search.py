"""Presentation-only task search. No saved task or status is changed."""
import re
import unicodedata
from functools import lru_cache
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QLineEdit, QCheckBox, QHBoxLayout, QVBoxLayout, QLabel
from desktop.controls import SoftComboBox
from desktop.dialogs import date_edit
from desktop.task_status import matches_status


@lru_cache(maxsize=4096)
def normalized(text):
    value = unicodedata.normalize('NFKC', text).casefold()
    return re.sub(r'(\d{4})[年/.-](\d{1,2})[月/.-](\d{1,2})日?',
                  lambda m: f'{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}', value)


def search_fields(row, scope='all'):
    task = row['task']
    if scope == 'title':
        return [('任务名称', task.title)]
    if scope != 'all':
        raise ValueError('未知搜索范围')
    return [('任务名称', task.title), ('说明', task.description), ('完成标准', task.deliverable),
            ('安排日期', row['due_date']), ('所属计划', row.get('plan_title', ''))]


def match_details(row, query, scope='all'):
    terms = list(dict.fromkeys(normalized(query).split()))
    fields = search_fields(row, scope)
    if not terms or not all(any(term in normalized(value) for _, value in fields) for term in terms):
        return []
    return [(label, value, [term for term in terms if term in normalized(value)])
            for label, value in fields if any(term in normalized(value) for term in terms)]


def match_excerpt(value, terms, limit=64):
    # Map normalized characters back to the original text; never render user text as HTML.
    folded, positions = '', []
    for i, char in enumerate(value):
        part = unicodedata.normalize('NFKC', char).casefold()
        folded += part
        positions.extend([i] * len(part))
    spans = sorted({(positions[m.start()], positions[m.end()-1]+1)
        for term in terms for m in re.finditer(re.escape(term), folded)})
    start = max(0, spans[0][0]-4) if spans else 0
    end = min(len(value), start+limit)
    result, cursor = '', start
    for left, right in spans:
        if right <= cursor or left >= end:
            continue
        left, right = max(cursor, left), min(end, right)
        result += value[cursor:left] + '【' + value[left:right] + '】'
        cursor = right
    result += value[cursor:end]
    return ('…' if start else '') + ' '.join(result.split()) + ('…' if end<len(value) else '')


def annotate_match(item, row, query, scope='all', *, hint='双击任务查看完整内容'):
    from desktop.task_style import SEARCH_MATCH_ROLE
    from desktop.task_tip import set_task_tip
    details = match_details(row, query, scope) if scope == 'all' else []
    summary = '；'.join(f'{label}命中：{match_excerpt(value, terms)}' for label, value, terms in details)
    item.setData(0, SEARCH_MATCH_ROLE, summary)
    set_task_tip(item, row, query, scope, hint=hint)


def search_tasks(rows, query='', status='全部状态', start=None, end=None, today=None, *, scope='all'):
    terms = normalized(query).split()
    result = []
    for row in rows:
        if status == '未完成':
            if row['status'] == '已完成':
                continue
        elif not matches_status(row, status, today):
            continue
        if start and row['due_date'] < start or end and row['due_date'] > end:
            continue
        fields = [normalized(value) for _, value in search_fields(row, scope)]
        if all(any(term in value for value in fields) for term in terms):
            result.append(row)
    return result


class TaskQuery(QWidget):
    """Same title/all-content scope and query in all task entry points."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        line = QHBoxLayout(self)
        line.setContentsMargins(0, 0, 0, 0)
        self.query = QLineEdit()
        self.query.setAccessibleName('搜索任务')
        self.query.setClearButtonEnabled(True)
        self.query.setMaxLength(240)
        self.scope = SoftComboBox()
        self.scope.addItem('仅任务名称', 'title')
        self.scope.addItem('全部内容', 'all')
        self.scope.setAccessibleName('任务搜索范围')
        self.scope.setToolTip('仅任务名称：只匹配名称；全部内容：名称、说明、完成标准、安排日期和所属计划。')
        self.scope.setFixedWidth(144)
        line.addWidget(self.query, 1)
        line.addWidget(self.scope)
        self.query.textChanged.connect(self.changed)
        self.scope.currentIndexChanged.connect(self.scope_changed)
        self.scope_changed()

    def scope_changed(self, *_):
        self.query.setPlaceholderText('搜索任务名称' if self.scope.currentData()=='title' else '搜索名称、说明、完成标准或日期')
        self.changed.emit()


class TaskSearch(QWidget):
    changed = Signal()

    def __init__(self, *, status=True, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(8)
        query = TaskQuery()
        self.query, self.scope = query.query, query.scope
        box.addWidget(query)
        line = QHBoxLayout()
        if status:
            line.addWidget(QLabel('筛选'))
        self.status = SoftComboBox()
        self.status.addItems(['全部状态', '未完成', '未开始', '进行中', '延期', '逾期', '已完成'])
        self.status.setAccessibleName('任务状态筛选')
        self.status.setMaximumWidth(150)
        self.status.setVisible(status)
        if status:
            line.addWidget(self.status)
        else:
            self.status.setParent(self)
        self.limit_dates = QCheckBox('限定日期')
        if status:
            line.addWidget(self.limit_dates)
            line.addStretch()
            box.addLayout(line)
        else:
            query.layout().addWidget(self.limit_dates)
        self.dates = QWidget()
        dates = QHBoxLayout(self.dates)
        dates.setContentsMargins(0, 0, 0, 0)
        self.start, self.end = date_edit(), date_edit()
        self.start.setAccessibleName('任务安排起始日期')
        self.end.setAccessibleName('任务安排结束日期')
        for item in (QLabel('安排日期从'), self.start, QLabel('至'), self.end):
            dates.addWidget(item)
        dates.addStretch()
        self.dates.hide()
        box.addWidget(self.dates)
        query.changed.connect(self.changed)
        self.status.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self.limit_dates.toggled.connect(self.dates.setVisible)
        self.limit_dates.toggled.connect(self.changed)
        self.start.dateChanged.connect(lambda *_: self.changed.emit())
        self.end.dateChanged.connect(lambda *_: self.changed.emit())

    def apply(self, rows, today=None):
        return search_tasks(rows, self.query.text(), self.status.currentText(),
            self.start.date().toString('yyyy-MM-dd') if self.limit_dates.isChecked() else None,
            self.end.date().toString('yyyy-MM-dd') if self.limit_dates.isChecked() else None, today,
            scope=self.scope.currentData())

    def annotate(self, item, row):
        annotate_match(item, row, self.query.text(), self.scope.currentData())

    def invalid_range(self):
        return self.limit_dates.isChecked() and self.start.date() > self.end.date()
