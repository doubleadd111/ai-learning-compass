"""Safe local validation diagnostics: fixed rules/paths/counts, no model text."""
from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, ConfigDict


RULES = {
    "invalid_json": "返回内容不是有效JSON",
    "empty_output": "没有收到可解析的文本（可能为空或被截断）",
    "missing_field": "缺少必填字段",
    "extra_field": "包含未定义字段",
    "wrong_type": "字段类型不符",
    "invalid_value": "字段值不符合要求",
    "length": "字段长度或数量不符合要求",
    "summary_length": "概略摘要合计字数不符（详情不计）",
    "outline_ids": "概略各点ID必须唯一",
    "question_options": "参考选项必须非空、简短且不重复",
    "question_ids": "问题ID不能重复",
    "decision_ask": "追问时需要1–3个问题且不能同时返回概略",
    "decision_outline": "概略阶段不能包含问题且必须有一份概略",
    "question_limit": "已达到追问上限，必须直接返回概略",
    "question_repeat": "不能重复询问已回答的问题",
}
DOMAIN_RULES = dict(zip((
    "概略各点摘要合计需要80–140字，详情不计入", "概略各点ID必须唯一",
    "参考选项需要简短、非空且不重复", "问题ID不能重复",
    "追问阶段只返回1–3道问题", "信息充分时只返回概略",
    "已达到追问上限，请直接给出概略及必要假设", "不能重复询问已回答的问题",
), ("summary_length", "outline_ids", "question_options", "question_ids", "decision_ask",
    "decision_outline", "question_limit", "question_repeat")))
FIELDS = set("decision questions outline outlines points assumptions id question purpose options summary objective content deliverable rationale changes task_id task due_date reason title overview weekly_plans week_number theme milestone tasks day description duration_minutes learning_tips".split())


class OutputIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str
    rule: Literal["invalid_json", "empty_output", "missing_field", "extra_field", "wrong_type", "invalid_value", "length",
                  "summary_length", "outline_ids", "question_options", "question_ids", "decision_ask", "decision_outline",
                  "question_limit", "question_repeat"]
    actual: int | None = None
    minimum: int | None = None
    maximum: int | None = None

    def summary(self):
        counts = []
        if self.actual is not None:
            counts.append(f"当前{self.actual}")
        if self.minimum is not None:
            counts.append(f"至少{self.minimum}")
        if self.maximum is not None:
            counts.append(f"最多{self.maximum}")
        return f"{self.field}：{RULES[self.rule]}" + ("（" + "，".join(counts) + "）" if counts else "")


def _field_path(loc):
    # Extra-field locations can be arbitrary model output; never copy them.
    parts = []
    for part in loc[:8]:
        if isinstance(part, int) and 0 <= part <= 1000:
            parts.append(f"[{part}]")
        else:
            parts.append(("." if parts else "") + (part if part in FIELDS else "未知字段"))
    return "".join(parts) or "结果"


def _at(value, loc):
    for part in loc:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and isinstance(part, int) and 0 <= part < len(value):
            value = value[part]
        else:
            return None
    return value


def validation_issues(error, text):
    from pydantic import ValidationError
    if not text.strip():
        return [OutputIssue(field="结果", rule="empty_output")]
    try:
        raw = json.loads(text)
    except ValueError:
        return [OutputIssue(field="结果", rule="invalid_json")]
    if not isinstance(error, ValidationError):
        code = DOMAIN_RULES.get(str(error), "invalid_value")
        return [OutputIssue(field="结果", rule=code)]
    issues = []
    for item in error.errors(include_input=False, include_url=False)[:3]:
        kind, loc, ctx = item["type"], item["loc"], item.get("ctx", {})
        code = DOMAIN_RULES.get(str(ctx.get("error", "")))
        if code is None:
            code = ("missing_field" if kind == "missing" else "extra_field" if kind == "extra_forbidden"
                    else "length" if kind in {"too_long", "too_short", "string_too_long", "string_too_short"}
                    else "wrong_type" if kind.endswith(("_type", "_parsing")) else "invalid_value")
        issue = OutputIssue(field=_field_path(loc), rule=code)
        value = _at(raw, loc)
        if code == "summary_length" and isinstance(value, dict):
            points = value.get("points")
            if isinstance(points, list):
                issue.actual = sum(len(p.get("summary", "")) for p in points if isinstance(p, dict) and isinstance(p.get("summary", ""), str))
            issue.minimum, issue.maximum = 80, 140
        elif code == "length":
            issue.actual = len(value) if isinstance(value, (str, list, dict)) else None
            issue.minimum = ctx.get("min_length") if isinstance(ctx.get("min_length"), int) else None
            issue.maximum = ctx.get("max_length") if isinstance(ctx.get("max_length"), int) else None
        issues.append(issue)
    return issues


def constraint_hint(schema):
    """State constraints omitted from the conservative wire schema to the model."""
    limits = {}
    def walk(node, path):
        if not isinstance(node, dict):
            return
        found = {key: node[key] for key in ("minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum") if key in node}
        if found:
            limits[path] = found
        for key, value in node.get("properties", {}).items():
            walk(value, path + "." + key)
        for name, value in node.get("$defs", {}).items():
            walk(value, name)
        if isinstance(node.get("items"), dict):
            walk(node["items"], path + "[]")
    walk(schema, schema.get("title", "result"))
    return "\n本机还会检查以下字段长度/数量/范围（不是要输出的新字段；字符串按字符计）：\n" + json.dumps(limits, ensure_ascii=False, separators=(",", ":")) if limits else ""
