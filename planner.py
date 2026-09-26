"""调用 DeepSeek Responses API 生成并校验结构化学习计划。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from models import LearningProfile, StudyPlan, validate_plan_for_profile


DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


class PlanGenerationError(RuntimeError):
    """可直接展示给用户的计划生成错误。"""


UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS = {
    "default",
    "format",
    "maxItems",
    "maxLength",
    "maximum",
    "minItems",
    "minLength",
    "minimum",
    "multipleOf",
    "pattern",
    "uniqueItems",
}


def _make_strict_schema(schema: Any) -> Any:
    """将 Pydantic schema 转成适用于 Structured Outputs 的严格对象 schema。

    Pydantic 仍会在本地执行长度、范围和数量校验；请求 schema 仅保留
    Structured Outputs 所需且兼容的结构信息。
    """

    if isinstance(schema, list):
        return [_make_strict_schema(item) for item in schema]
    if not isinstance(schema, Mapping):
        return schema

    strict_schema = {
        key: _make_strict_schema(value)
        for key, value in schema.items()
        if key not in UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS
    }
    if strict_schema.get("type") == "object":
        strict_schema["additionalProperties"] = False
        if "properties" in strict_schema:
            strict_schema["required"] = list(strict_schema["properties"].keys())
    return strict_schema


STUDY_PLAN_SCHEMA = _make_strict_schema(StudyPlan.model_json_schema())


def build_instructions() -> str:
    return """你是一位务实、鼓励但不夸大效果的中文学习教练。
根据用户条件生成可执行的学习计划。严格遵守给出的周数、每周学习天数和每天时长；
不要编造课程链接、证书或学习效果保证。每天只安排一个聚焦任务，并清楚写出完成产出。
所有字段都必须使用简体中文，输出必须满足指定 JSON Schema。"""


def build_user_input(profile: LearningProfile, correction: str | None = None) -> str:
    request = f"""请生成一份学习计划。

学习目标：{profile.goal}
当前基础：{profile.current_level}
计划周期：{profile.plan_weeks} 周
每周学习天数：{profile.days_per_week} 天
每天最多学习：{profile.minutes_per_day} 分钟
偏好或限制：{profile.preferences}

要求：每周必须恰好安排 {profile.days_per_week} 个学习日；每个任务不得超过
{profile.minutes_per_day} 分钟；每周任务总时长不得超过 {profile.weekly_minutes} 分钟。
week_number 必须从 1 连续到 {profile.plan_weeks}，每周任务中的 day 必须不重复。"""
    if correction:
        request += f"\n\n上一版计划未通过校验：{correction}\n请完整重新生成并修正此问题。"
    return request


def _get_client() -> OpenAI:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise PlanGenerationError(
            "未找到 DeepSeek API Key。请在 .env 文件中配置 DEEPSEEK_API_KEY 后再生成。"
        )
    return OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", DEFAULT_BASE_URL),
    )


def _request_plan(client: Any, profile: LearningProfile, correction: str | None) -> StudyPlan:
    response = client.responses.create(
        model=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
        instructions=build_instructions(),
        input=build_user_input(profile, correction),
        text={
            "format": {
                "type": "json_schema",
                "name": "study_plan",
                "strict": True,
                "schema": STUDY_PLAN_SCHEMA,
            }
        },
        temperature=0.3,
        max_output_tokens=4000,
        store=False,
    )
    if not getattr(response, "output_text", None):
        raise ValueError("模型没有返回可读取的计划内容")
    return StudyPlan.model_validate_json(response.output_text)


def generate_study_plan(profile: LearningProfile, client: Any | None = None) -> StudyPlan:
    """生成计划；格式或时间校验失败时，携带原因自动修正重试一次。"""

    active_client = client or _get_client()
    correction: str | None = None

    for attempt in range(2):
        try:
            plan = _request_plan(active_client, profile, correction)
            validate_plan_for_profile(plan, profile)
            return plan
        except (ValueError, json.JSONDecodeError) as error:
            correction = str(error)
            if attempt == 1:
                raise PlanGenerationError("生成的计划格式或时长不符合要求，请稍后重新生成。") from error
        except RateLimitError as error:
            raise PlanGenerationError("请求过于频繁或额度暂不可用，请稍后再试。") from error
        except (APIConnectionError, APIStatusError) as error:
            if attempt == 1:
                raise PlanGenerationError("暂时无法连接模型服务，请检查网络后重试。") from error

    raise PlanGenerationError("暂时无法生成计划，请稍后重试。")
