"""Typed, bounded planning workflow. No GUI, credentials or database writes."""
from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from desktop.models import DesktopProfile, ModelConnection, new_id
from models import DailyTask, StudyPlan


def prepare_outline_excerpt(text: str) -> tuple[str, int]:
    """Excerpt presentation titles only; preserve full source in existing details.

    This is not a semantic repair. Fail atomically if any source text cannot fit
    without loss. The caller must still validate the complete result as usual.
    """
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text, 0
    if not isinstance(data, dict) or data.get("decision") != "outline":
        return text, 0
    if "outlines" in data and "outline" not in data:
        outlines = data["outlines"]
        outline = outlines[0] if isinstance(outlines, list) and len(outlines) == 1 else None
    elif "outline" in data and "outlines" not in data:
        outline = data["outline"]
    else:
        return text, 0
    points = outline.get("points") if isinstance(outline, dict) else None
    if not isinstance(points, list) or not 3 <= len(points) <= 5:
        return text, 0
    if any(not isinstance(p, dict) or not isinstance(p.get("summary"), str)
           or not isinstance(p.get("content"), str) or not 2 <= len(p["content"]) <= 500
           or len(p["summary"]) < 4 for p in points):
        return text, 0
    if all(len(p["summary"]) <= 40 for p in points) and 80 <= sum(len(p["summary"]) for p in points) <= 140:
        return text, 0
    summaries = [" ".join(p["summary"].split()) for p in points]
    capacities = [min(len(summary), 40) for summary in summaries]
    if min(capacities) < 4 or sum(capacities) < 80:
        return text, 0
    budgets = [4] * len(points)
    target = min(100, sum(capacities))
    while sum(budgets) < target:
        for i, capacity in enumerate(capacities):
            if budgets[i] < capacity and sum(budgets) < target:
                budgets[i] += 1
    count = 0
    for point, summary, budget in zip(points, summaries, budgets):
        if len(point["summary"]) <= budget:
            continue
        content = f"完整概略（标题为原文摘录）：{point['summary']}\n\n原主要内容：{point['content']}"
        if len(content) > 500:
            return text, 0
        point["summary"] = summary[:budget - 1] + "…" if len(summary) > budget else summary
        point["content"] = content
        count += 1
    return (json.dumps(data, ensure_ascii=False), count) if count else (text, 0)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ClarificationQuestion(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    question: str = Field(min_length=4, max_length=200)
    purpose: str = Field(min_length=2, max_length=200)
    options: list[str] = Field(min_length=2, max_length=4)

    @model_validator(mode="after")
    def check_options(self):
        if any(not option.strip() or len(option) > 200 for option in self.options) or len(set(self.options)) != len(self.options):
            raise ValueError("参考选项需要简短、非空且不重复")
        return self


class OutlinePoint(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=4, max_length=40)
    objective: str = Field(min_length=2, max_length=300)
    content: str = Field(min_length=2, max_length=500)
    deliverable: str = Field(min_length=2, max_length=300)
    rationale: str = Field(min_length=2, max_length=300)


class PlanOutline(StrictModel):
    points: list[OutlinePoint] = Field(min_length=3, max_length=5)
    assumptions: list[str] = Field(max_length=8)

    @model_validator(mode="after")
    def check_points(self):
        if len({p.id for p in self.points}) != len(self.points):
            raise ValueError("概略各点ID必须唯一")
        if not 80 <= sum(len(p.summary) for p in self.points) <= 140:
            raise ValueError("概略各点摘要合计需要80–140字，详情不计入")
        return self


class PlanningTurn(StrictModel):
    decision: Literal["ask", "outline"]
    questions: list[ClarificationQuestion] = Field(max_length=3)
    outline: PlanOutline | None

    @model_validator(mode="after")
    def check_decision(self):
        if self.decision == "ask" and (not self.questions or self.outline is not None):
            raise ValueError("追问阶段只返回1–3道问题")
        if self.decision == "outline" and (self.questions or self.outline is None):
            raise ValueError("信息充分时只返回概略")
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError("问题ID不能重复")
        return self


class ClarificationPayload(StrictModel):
    """DeepSeek strict wire shape, without a nullable-object anyOf branch.

    Keep the domain model and saved drafts unchanged; validate both layers.
    """
    decision: Literal["ask", "outline"]
    questions: list[ClarificationQuestion] = Field(max_length=3)
    outlines: list[PlanOutline] = Field(max_length=1)

    def to_turn(self):
        return PlanningTurn(decision=self.decision, questions=self.questions,
                            outline=self.outlines[0] if self.outlines else None)

    @model_validator(mode="after")
    def validate_turn(self):
        self.to_turn()
        return self


class QuestionAnswer(StrictModel):
    id: str
    question: str
    answer: str = Field(max_length=1500)


class TaskChange(StrictModel):
    task_id: str
    task: DailyTask
    due_date: date
    reason: str = Field(min_length=2, max_length=300)


class RevisionProposal(StrictModel):
    changes: list[TaskChange] = Field(min_length=1, max_length=56)
    summary: str = Field(min_length=4, max_length=500)


def validate_brief(value):
    """Validate the shapes consumed by the offline history reader on import/restore."""
    if not isinstance(value, dict):
        raise ValueError("需求记录必须是对象")
    requirements = value.get("requirements", {})
    if not isinstance(requirements, dict):
        raise ValueError("需求资料不完整")
    if "profile" in requirements:
        DesktopProfile.model_validate(requirements["profile"])
    sections = [requirements, *value.get("adjustments", [])]
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("修改记录不完整")
        if "request" in section and not isinstance(section["request"], str):
            raise ValueError("修改意见必须是文字")
        for answer in section.get("answers", []):
            QuestionAnswer.model_validate(answer)
    for section in [value, *value.get("adjustments", [])]:
        if "outline" in section:
            PlanOutline.model_validate(section["outline"])
        for event in section.get("events", []):
            if not isinstance(event, dict) or not isinstance(event.get("metrics", {}), dict):
                raise ValueError("模型用量记录不完整")
    return value


class PlanningDraft(StrictModel):
    id: str = Field(default_factory=new_id)
    kind: Literal["create", "modify"] = "create"
    state: Literal["base", "questions", "outline", "preview", "saved"] = "base"
    revision: int = 0
    profile: DesktopProfile
    start_date: date
    weekdays: list[int]
    outcome: str = Field(default="", max_length=1000)
    mastered: str = Field(default="", max_length=1500)
    connection: ModelConnection | None = None
    quick: bool = False
    rounds: int = Field(default=0, ge=0, le=2)
    questions: list[ClarificationQuestion] = Field(default_factory=list, max_length=3)
    answers: list[QuestionAnswer] = Field(default_factory=list)
    feedback: list[str] = Field(default_factory=list)
    outline: PlanOutline | None = None
    candidate: StudyPlan | None = None
    proposal: RevisionProposal | None = None
    source_plan_id: str | None = None
    source_revision: int | None = None
    target_ids: list[str] = Field(default_factory=list)
    request: str = Field(default="", max_length=2000)
    source_context: dict = Field(default_factory=dict)
    parent_plan_id: str | None = None
    parent_revision: int | None = None
    archive_parent: bool = True
    events: list[dict] = Field(default_factory=list)
    base_inputs: dict = Field(default_factory=dict)
    pending_answers: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_schedule(self):
        if len(self.weekdays) != self.profile.days_per_week or len(set(self.weekdays)) != len(self.weekdays) or any(d not in range(7) for d in self.weekdays):
            raise ValueError("学习日数量与资料不一致")
        if self.state in {"outline", "preview"} and self.outline is None:
            raise ValueError("需要先确认概略")
        if self.state == "preview" and ((self.kind == "create" and self.candidate is None) or (self.kind == "modify" and self.proposal is None)):
            raise ValueError("详细预览不完整")
        return self

    def context(self):
        return dict(profile=self.profile.model_dump(), start_date=self.start_date.isoformat(), weekdays=self.weekdays,
                    desired_outcome=self.outcome, already_mastered=self.mastered,
                    answers=[a.model_dump() for a in self.answers], feedback=self.feedback,
                    request=self.request, modification=self.source_context if self.kind == "modify" else None)

    def receive_turn(self, turn: PlanningTurn):
        if turn.decision == "ask":
            if self.quick or self.rounds >= 2:
                raise ValueError("已达到追问上限，请直接给出概略及必要假设")
            old_ids = {a.id for a in self.answers}
            old_text = {"".join(a.question.split()).casefold() for a in self.answers}
            if any(q.id in old_ids or "".join(q.question.split()).casefold() in old_text for q in turn.questions):
                raise ValueError("不能重复询问已回答的问题")
            self.rounds += 1
            self.questions = turn.questions
            self.state = "questions"
        else:
            self.outline = turn.outline
            self.questions = []
            self.candidate = None
            self.proposal = None
            self.state = "outline"
        self.revision += 1

    def answer_questions(self, answers: dict[str, str]):
        if self.state != "questions":
            raise ValueError("当前不在问答阶段")
        self.answers.extend(QuestionAnswer(id=q.id, question=q.question,
                                          answer=answers.get(q.id, "").strip() or "跳过；请列明合理假设")
                            for q in self.questions)
        self.questions = []
        self.state = "base"
        self.revision += 1

    def revise_outline(self, point_id, text):
        if not self.outline or point_id not in {p.id for p in self.outline.points} or not text.strip():
            raise ValueError("请选择概略中的一点并填写修改意见")
        point = next(p for p in self.outline.points if p.id == point_id)
        self.feedback.append(f"修改概略【{point.summary}】：{text.strip()[:1500]}。其余方向保持，除非因此产生约束冲突。")
        self.state = "base"
        self.candidate = None
        self.proposal = None
        self.revision += 1


def protection_reason(task, reviewed_weeks=()):
    if task["week"] in reviewed_weeks:
        return "该周已复盘"
    if task["status"] not in {"未开始", "延期"}:
        return "任务已开始或完成"
    if task["manual_seconds"] or task["focus_seconds"] or task["note"] or task["difficulty"] is not None:
        return "任务已有学习记录"
    if task.get("ai_locked", task.get("user_modified", False)):
        return "用户已锁定"
    return ""


def modification_context(saved, target_ids, reviewed_weeks):
    targets = [row for row in saved["tasks"] if row["id"] in target_ids]
    if not targets or len(targets) != len(set(target_ids)):
        raise ValueError("请选择当前计划内的任务")
    blocked = [row for row in targets if protection_reason(row, reviewed_weeks)]
    editable = [row for row in targets if row not in blocked]
    if not editable:
        raise ValueError("范围内没有可修改任务；已有记录不能覆盖，未开始的锁定任务可先明确解锁")
    return dict(target_ids=[t["id"] for t in editable], protected_count=len(blocked),
                tasks=[dict(task_id=t["id"], task=t["task"].model_dump(), due_date=t["due_date"]) for t in editable],
                reserved=[dict(due_date=t["due_date"], duration_minutes=t["task"].duration_minutes)
                          for t in saved["tasks"] if t not in editable])


def validate_proposal(saved, proposal, target_ids, reviewed_weeks):
    ids = [change.task_id for change in proposal.changes]
    if len(ids) != len(set(ids)) or not set(ids) <= set(target_ids):
        raise ValueError("修改越出所选范围或重复修改同一任务")
    rows = {t["id"]: t for t in saved["tasks"]}
    start = date.fromisoformat(saved["start_date"])
    end = start + timedelta(days=saved["profile"].plan_weeks * 7)
    weekdays = json.loads(saved["weekdays_json"])
    candidate = {t["id"]: (t["task"], t["due_date"]) for t in saved["tasks"]}
    for change in proposal.changes:
        row = rows.get(change.task_id)
        if not row or protection_reason(row, reviewed_weeks):
            raise ValueError("修改不能覆盖受保护任务")
        if change.task.day != row["day"]:
            raise ValueError("不能改变任务学习日编号")
        if change.task.duration_minutes > saved["profile"].minutes_per_day:
            raise ValueError("任务超过单日上限")
        if change.due_date.isoformat() != row["due_date"] and not (start <= change.due_date < end and change.due_date.weekday() in weekdays):
            raise ValueError("改期必须在原周期的可用学习日内；请减内容或创建延长周期副本")
        candidate[change.task_id] = (change.task, change.due_date.isoformat())
    daily, weekly = {}, {}
    for task, due in candidate.values():
        daily[due] = daily.get(due, 0) + task.duration_minutes
        key = date.fromisoformat(due).isocalendar()[:2]
        weekly[key] = weekly.get(key, 0) + task.duration_minutes
    if any(v > saved["profile"].minutes_per_day for v in daily.values()) or any(v > saved["profile"].weekly_minutes for v in weekly.values()):
        raise ValueError("与保留任务合并后超过单日或每周时间上限；请缩减任务或创建延长周期副本")
