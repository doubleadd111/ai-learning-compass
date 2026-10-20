"""Derived overdue state; never a persisted completion status."""
from datetime import date


def overdue_days(task, today=None):
    if task.get("archived") or task.get("paused") or task["status"] == "已完成":
        return 0
    return max(0, ((today or date.today()) - date.fromisoformat(task["due_date"])).days)


def display_status(task, today=None):
    if task.get("paused"):
        return f"已暂停 · {task['status']}"
    return f"逾期 · {task['status']}" if overdue_days(task, today) else task["status"]


def matches_status(task, status, today=None):
    if status == "逾期":
        return overdue_days(task, today) > 0
    return status == "全部状态" or task["status"] == status
