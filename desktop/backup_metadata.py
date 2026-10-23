"""Small, allow-listed backup descriptions shared by the app and updater.

The sidecar is informational, not a signature or a replacement for SQLite
validation. Never infer a source application version from a backup filename.
"""
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

KINDS = {
    'manual': ('手动备份', 'manual'),
    'automatic_daily': ('自动备份 · 每日', 'daily'),
    'before_update': ('自动备份 · 软件升级前', 'guard'),
    'before_schema': ('自动备份 · 数据升级前', 'guard'),
    'before_restore': ('自动备份 · 整库恢复前', 'guard'),
    'before_history': ('自动备份 · 历史回溯前', 'guard'),
    'before_delete_plan': ('自动备份 · 删除计划前', 'guard'),
    'other': ('其他备份 · 来源未记录', 'other'),
}


def file_hash(path):
    with Path(path).open('rb') as source:
        h = hashlib.sha256()
        while chunk := source.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def kind_from_name(path):
    """A filename is a type hint only; callers must label this provenance."""
    name = Path(path).name
    if re.fullmatch(r'daily-\d{4}-\d{2}-\d{2}\.sqlite3', name):
        return 'automatic_daily'
    for prefix, kind in (('manual-', 'manual'), ('pre-update-', 'before_update'),
                         ('before-schema-', 'before_schema'), ('before-restore-', 'before_restore'),
                         ('before-history-replay-', 'before_history'), ('before-delete-plan-', 'before_delete_plan')):
        if name.startswith(prefix) and name.endswith('.sqlite3'):
            return kind
    return 'other'


def record(path, kind, *, app_version=None, target_version=None, source_app_version=None):
    from desktop import VERSION, version_parts
    if kind not in KINDS:
        raise ValueError('无法识别备份类型')
    app_version = app_version or VERSION
    for value in (app_version, target_version, source_app_version):
        if value is not None:
            version_parts(value)
    path = Path(path)
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as c:
        schema = c.execute('PRAGMA user_version').fetchone()[0]
    info = dict(format='learning-compass-backup', metadata_version=1, kind=kind,
                app_version=app_version, schema=schema,
                created_at=datetime.now().astimezone().isoformat(), sha256=file_hash(path))
    if target_version is not None:
        info['target_version'] = target_version
    if source_app_version is not None:
        info['source_app_version'] = source_app_version
    sidecar = path.with_suffix('.meta.json')
    temporary = sidecar.with_name(sidecar.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, sidecar)
    finally:
        temporary.unlink(missing_ok=True)
    return info


def description(path, schema):
    """Missing/broken sidecars don't make an otherwise readable DB corrupt."""
    from desktop import version_parts
    path = Path(path)
    kind = kind_from_name(path)
    result = dict(kind=kind, kind_source='filename' if kind != 'other' else 'unknown',
                  version='未记录', created_at=None, source_app_version=None,
                  target_version=None, metadata_warning='')
    sidecar = path.with_suffix('.meta.json')
    if sidecar.exists():
        try:
            if sidecar.stat().st_size > 64 * 1024:
                raise ValueError('说明文件过大')
            info = json.loads(sidecar.read_text(encoding='utf-8'))
            if not isinstance(info, dict) or info.get('sha256') != file_hash(path):
                raise ValueError('说明与备份摘要不匹配')
            if info.get('schema') != schema:
                raise ValueError('说明与数据结构不匹配')
            version_parts(info['app_version'])
            if 'format' in info and (info['format'] != 'learning-compass-backup' or info.get('metadata_version') != 1):
                raise ValueError('说明格式不支持')
            if 'kind' in info:
                if info['kind'] not in KINDS:
                    raise ValueError('说明中的备份类型无效')
            stamp = info.get('created_at')
            if stamp and not datetime.fromisoformat(stamp).tzinfo:
                raise ValueError('备份时间缺少时区')
            for key in ('target_version', 'source_app_version'):
                if info.get(key) is not None:
                    version_parts(info[key])
            result.update(version=info['app_version'], created_at=stamp,
                          target_version=info.get('target_version'), source_app_version=info.get('source_app_version'))
            if 'kind' in info:
                result.update(kind=info['kind'], kind_source='metadata')
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            result['metadata_warning'] = '备份说明缺失有效信息或与文件不匹配；未采用其中版本，恢复时仍须校验数据库。'
    result['kind_label'], result['category'] = KINDS[result['kind']]
    result['kind_note'] = ('已记录备份类型' if result['kind_source'] == 'metadata' else
                           '旧备份：按文件名识别类型，不据此推断版本' if result['kind_source'] == 'filename' else
                           '没有可靠的备份类型记录')
    return result
