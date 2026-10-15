"""生成调整预览；保护已开始或手动编辑的未来任务。"""
from models import validate_plan_for_profile
from weekly_review import decide_week
from desktop.planning import protection_reason


def prepare_review(store, plan_id, gateway, *, cancelled=None):
    saved = store.get_plan(plan_id)
    profile = saved["profile"]
    original = saved["plan"]
    reviews = store.reviews(plan_id)
    week_number = next((w.week_number for w in original.weekly_plans if w.week_number not in reviews), None)
    if week_number is None:
        raise ValueError("这份计划已完成全部复盘")
    decision = decide_week(profile, original.weekly_plans[week_number - 1], store.progress(plan_id))
    if week_number == profile.plan_weeks:
        return dict(week=week_number, summary=decision.summary, adjusted=None, revision=saved["revision"], provenance=None)
    future_records = [t for t in saved["tasks"] if t["week"] > week_number]
    protected = {(t["week"], t["day"]): t for t in future_records if protection_reason(t, reviews)}
    open_slots = [t for t in future_records if t["week"] == week_number + 1 and (t["week"], t["day"]) not in protected]
    if len(open_slots) < len(decision.carryover):
        raise ValueError("下一周的保留任务占满了可用学习日，无法安置延期任务。请先调整未来任务或日期。")
    reduced = profile.model_copy(update={"plan_weeks": profile.plan_weeks - week_number, "minutes_per_day": decision.daily_limit})
    generated, provenance = gateway.generate(reduced, context=f"用户正在复盘第{week_number}周。{decision.summary}。继续推进原目标，已学习周次不需要生成。", cancelled=cancelled)
    shifted = []
    for week in generated.weekly_plans:
        number = week.week_number + week_number
        tasks = [protected[(number, t.day)]["task"] if (number, t.day) in protected else t for t in week.tasks]
        shifted.append(week.model_copy(update={"week_number": number, "tasks": tasks}))
    for task, slot in zip(decision.carryover, open_slots):
        tasks = shifted[0].tasks
        position = next(i for i, t in enumerate(tasks) if t.day == slot["day"])
        tasks[position] = task.model_copy(update={"day": slot["day"], "duration_minutes": min(task.duration_minutes, decision.daily_limit)})
    adjusted = original.model_copy(update={"weekly_plans": original.weekly_plans[:week_number] + shifted})
    validate_plan_for_profile(adjusted, profile)
    summary = decision.summary
    if protected and decision.daily_limit < profile.minutes_per_day:
        summary += "已开始或手动保留的任务维持原安排，降低后的上限用于新生成任务。"
    return dict(week=week_number, summary=summary, adjusted=adjusted, revision=saved["revision"], provenance=provenance)
