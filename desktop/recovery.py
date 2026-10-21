"""Insert a missing plan from a same-schema backup without replacing live data.

The caller owns the transaction, pre-operation backup and app-exit checks.
Nothing here restores settings, model connections or unrelated plans.
"""
TABLES = ("plans", "weeks", "tasks", "reviews", "generation_events", "focus_sessions",
          "notifications", "undo", "plan_briefs", "plan_versions", "plan_schedules", "pause_history", "task_details", "task_series", "series_tasks", "task_attention", "study_events")


def missing_plan_rows(source, plan_id):
    result = {}
    for table in TABLES:
        if table == "plans":
            query = "SELECT * FROM plans WHERE id=?"
        elif table in {"focus_sessions", "notifications", "task_details", "task_attention", "study_events"}:
            query = f"SELECT * FROM {table} WHERE task_id IN (SELECT id FROM tasks WHERE plan_id=?)"
        elif table == "series_tasks":
            query = "SELECT * FROM series_tasks WHERE series_id IN (SELECT id FROM task_series WHERE plan_id=?)"
        else:
            query = f"SELECT * FROM {table} WHERE plan_id=?"
        result[table] = [dict(row) for row in source.execute(query, (plan_id,))]
    if len(result["plans"]) != 1:
        raise ValueError("备份中未找到指定计划")
    return result


def restore_missing_plan(target, source, plan_id):
    """No replace/upsert: conflicting IDs cause the caller's transaction to fail."""
    from desktop.store import APP_ID
    if any(c.execute("PRAGMA application_id").fetchone()[0] != APP_ID for c in (target, source)):
        raise ValueError("只能恢复学习罗盘桌面数据库中的计划")
    if target.execute("PRAGMA user_version").fetchone()[0] != source.execute("PRAGMA user_version").fetchone()[0]:
        raise ValueError("恢复前需先在副本中迁移备份，不可直接跨版本复制")
    if not target.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise ValueError("恢复时必须启用外键校验")
    if target.execute("SELECT 1 FROM plans WHERE id=?", (plan_id,)).fetchone():
        raise ValueError("当前库已存在这份计划，不能覆盖恢复")
    rows = missing_plan_rows(source, plan_id)
    for table in TABLES:
        columns = [r[1] for r in target.execute(f"PRAGMA table_info({table})")]
        if columns != [r[1] for r in source.execute(f"PRAGMA table_info({table})")]:
            raise ValueError("备份字段与当前数据库不一致")
        for row in rows[table]:
            target.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                           [row[name] for name in columns])
    if target.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("恢复后的关联记录不完整，已取消")
    return {table: len(records) for table, records in rows.items()}
