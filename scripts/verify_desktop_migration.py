"""Validate a read-only copy of local learning data; never open the credential vault."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop.store import Store


def fingerprint(connection, columns):
    result = {}
    for table, names in columns.items():
        # Identifiers are read from this database, not composed from user text.
        quoted = lambda name: '"' + name.replace('"', '""') + '"'
        fields = ",".join(map(quoted, names))
        rows = connection.execute(f"SELECT {fields} FROM {quoted(table)} ORDER BY rowid").fetchall()
        encoded = json.dumps([list(row) for row in rows], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result[table] = dict(rows=len(rows), sha256=hashlib.sha256(encoded).hexdigest())
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(os.environ["LOCALAPPDATA"]) / "LearningCompass" / "learning-compass.sqlite3")
    parser.add_argument("--report", type=Path, default=ROOT / ".learning_compass" / "qa" / "migration-2026.10.5.json")
    args = parser.parse_args()
    source = args.source.resolve()
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix="learning-compass-migration-") as directory:
        staged = Path(directory) / "learning-compass.sqlite3"
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
            version = original.execute("PRAGMA user_version").fetchone()[0]
            tables = [row[0] for row in original.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            # Undo's technical snapshot representation intentionally changes v2→v3.
            columns = {table: [row[1] for row in original.execute('PRAGMA table_info("' + table.replace('"', '""') + '")')]
                       for table in tables if table != "undo"}
            states = {r[0]: r[1] for r in original.execute("SELECT id,status FROM tasks")}
            affected = {r[0] for r in original.execute("SELECT id FROM tasks WHERE status='未开始' AND focus_seconds>0")} if version < 5 else set()
            affected_plans = {r[0] for r in original.execute("SELECT DISTINCT plan_id FROM tasks WHERE status='未开始' AND focus_seconds>0")} if affected else set()
            revisions = {r[0]: (r[1], r[2]) for r in original.execute("SELECT id,revision,updated_at FROM plans")}
            # All other original columns must be byte-for-byte equivalent. Only
            # the requested status repair and its owning plan revisions may change.
            columns["tasks"].remove("status")
            columns["plans"].remove("revision")
            columns["plans"].remove("updated_at")
            before = fingerprint(original, columns)
            with closing(sqlite3.connect(staged)) as destination:
                original.backup(destination)
        store = Store(Path(directory))
        Store.inspect_backup(store.path)
        with closing(store.connect()) as migrated:
            after = fingerprint(migrated, columns)
            target_version = migrated.execute("PRAGMA user_version").fetchone()[0]
            for tid, status in migrated.execute("SELECT id,status FROM tasks"):
                assert status == ("进行中" if tid in affected else states[tid])
            for pid, revision, stamp in migrated.execute("SELECT id,revision,updated_at FROM plans"):
                old_revision, old_stamp = revisions[pid]
                assert revision == old_revision + int(pid in affected_plans)
                if pid not in affected_plans:
                    assert stamp == old_stamp
            if version == 1:
                assert not migrated.execute("SELECT 1 FROM tasks WHERE ai_locked != user_modified").fetchone()
        assert before == after, "旧字段或学习记录发生了意外变化"
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash, "源数据库发生变化，请关闭软件后重测"
    report = dict(source_schema=version, target_schema=target_version, source_unchanged=True,
                  learning_data_unchanged_except_requested_status=True, repaired_statuses=len(affected),
                  credentials_not_accessed=True, tables=after)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "tables"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
