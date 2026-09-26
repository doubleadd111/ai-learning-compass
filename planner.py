"""调用 DeepSeek Responses API 生成并校验结构化学习计划。"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from curriculum import curriculum_prompt
from models import LearningProfile, StudyPlan, validate_plan_for_profile


DEFAULT_MODEL = "deepseek-flash"
DEFAULT_BASE_URL = "https://api.deepseek.com"


class PlanGenerationError(RuntimeError):
    """可直接展示给用户的计划生成错误。"""


@dataclass(frozen=True)
class GenerationMetrics:
    success: bool
    attempts: int
    latency_ms: int
    input_tokens: int | None
    output_tokens: int | None
    error_category: str | None = None


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
    return f"""你是一位务实、鼓励但不夸大效果的中文 Python 入门学习教练。
根据用户条件生成可执行的学习计划。严格遵守给出的周数、每周学习天数和每天时长；
不要编造课程链接、证书或学习效果保证。每天只安排一个聚焦任务，并清楚写出完成产出。
任务说明保持简洁，每天用一到两句话说明动作与验收方式。
所有字段都必须使用简体中文，输出必须满足指定 JSON Schema。
按当前基础与周期，从下面的 Python 主题中选择合适内容，保持先修顺序；
周期短时优先基础，不要承诺覆盖全部主题：
{curriculum_prompt()}"""


def build_user_input(
    profile: LearningProfile, correction: str | None = None, context: str | None = None
) -> str:
    request = f"""请生成一份学习计划。

这份计划面向 Python 入门；用户的具体目标用于选择练习与小项目主题。

学习目标：{profile.goal}
当前基础：{profile.current_level}
计划周期：{profile.plan_weeks} 周
每周学习天数：{profile.days_per_week} 天
每天最多学习：{profile.minutes_per_day} 分钟
偏好或限制：{profile.preferences}

要求：每周必须恰好安排 {profile.days_per_week} 个学习日；每个任务不得超过
{profile.minutes_per_day} 分钟；每周任务总时长不得超过 {profile.weekly_minutes} 分钟。
week_number 必须从 1 连续到 {profile.plan_weeks}；每周任务的 day
必须从 1 连续到 {profile.days_per_week}，不能跨周累计编号。"""
    if context:
        request += f"\n\n调整背景：{context}"
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


def _request_plan(
    client: Any, profile: LearningProfile, correction: str | None, context: str | None
) -> Any:
    return client.responses.create(
        model=os.getenv("DEEPSEEK_MODEL", DEFAULT_MODEL),
        instructions=build_instructions(),
        input=build_user_input(profile, correction, context),
        text={
            "format": {
                "type": "json_schema",
                "name": "study_plan",
                "strict": True,
                "schema": STUDY_PLAN_SCHEMA,
            }
        },
        temperature=0.3,
        reasoning={"effort": "none"},
        max_output_tokens=min(
            24000, max(4000, profile.plan_weeks * profile.days_per_week * 400)
        ),
        store=False,
    )


def generate_study_plan(
    profile: LearningProfile,
    client: Any | None = None,
    *,
    context: str | None = None,
    on_metrics=None,
) -> StudyPlan:
    """生成计划；格式或时间校验失败时，携带原因自动修正重试一次。"""

    started = perf_counter()
    correction: str | None = None
    attempts = 0
    input_tokens = 0
    output_tokens = 0
    has_usage = False
    success = False
    error_category: str | None = None

    try:
        active_client = client or _get_client()
        for attempt in range(2):
            attempts += 1
            try:
                response = _request_plan(active_client, profile, correction, context)
                usage = getattr(response, "usage", None)
                if usage is not None:
                    has_usage = True
                    input_tokens += getattr(usage, "input_tokens", 0) or 0
                    output_tokens += getattr(usage, "output_tokens", 0) or 0
                if not getattr(response, "output_text", None):
                    raise ValueError("模型没有返回可读取的计划内容")
                plan = StudyPlan.model_validate_json(response.output_text)
                validate_plan_for_profile(plan, profile)
                success = True
                return plan
            except (ValueError, json.JSONDecodeError) as error:
                correction = str(error)
                if attempt == 1:
                    error_category = "invalid_output"
                    raise PlanGenerationError(
                        "生成的计划格式或时长不符合要求，请稍后重新生成。"
                    ) from error
            except RateLimitError as error:
                error_category = "rate_limit"
                raise PlanGenerationError("请求过于频繁或额度暂不可用，请稍后再试。") from error
            except APIStatusError as error:
                if error.status_code in {401, 403}:
                    error_category = "authentication"
                    raise PlanGenerationError(
                        "模型服务拒绝了请求，请检查 API Key 是否有效。"
                    ) from error
                if error.status_code == 402:
                    error_category = "quota"
                    raise PlanGenerationError(
                        "模型服务账户额度不足，请检查服务商账户。"
                    ) from error
                if attempt == 1:
                    error_category = "service"
                    raise PlanGenerationError(
                        "模型服务暂不可用，请稍后重试。"
                    ) from error
            except APIConnectionError as error:
                if attempt == 1:
                    error_category = "connection"
                    raise PlanGenerationError("暂时无法连接模型服务，请检查网络后重试。") from error
        error_category = "unknown"
        raise PlanGenerationError("暂时无法生成计划，请稍后重试。")
    except PlanGenerationError:
        if error_category is None:
            error_category = "configuration"
        raise
    finally:
        if on_metrics is not None:
            on_metrics(
                GenerationMetrics(
                    success=success,
                    attempts=attempts,
                    latency_ms=round((perf_counter() - started) * 1000),
                    input_tokens=input_tokens if has_usage else None,
                    output_tokens=output_tokens if has_usage else None,
                    error_category=error_category,
                )
            )
