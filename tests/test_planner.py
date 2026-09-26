import json
from types import SimpleNamespace

import pytest
import httpx
from openai import APIConnectionError, APIStatusError, RateLimitError

from models import LearningProfile
from planner import (
    PlanGenerationError,
    STUDY_PLAN_SCHEMA,
    build_user_input,
    generate_study_plan,
)


def profile() -> LearningProfile:
    return LearningProfile(
        goal="从零学习 Python 基础语法",
        current_level="零基础",
        plan_weeks=1,
        days_per_week=2,
        minutes_per_day=45,
        preferences="无",
    )


def response_payload() -> str:
    return json.dumps(
        {
            "title": "Python 起步计划",
            "overview": "通过短小练习认识 Python 的核心语法。",
            "weekly_plans": [{
                "week_number": 1,
                "theme": "认识 Python",
                "milestone": "能写出并运行一个简单脚本。",
                "tasks": [
                    {"day": 1, "title": "安装与打印", "description": "安装环境并运行第一段代码。", "duration_minutes": 30, "deliverable": "hello.py"},
                    {"day": 2, "title": "变量练习", "description": "用变量记录并输出三条信息。", "duration_minutes": 40, "deliverable": "变量练习脚本"},
                ],
            }],
            "learning_tips": ["每天结束时写一句复盘。", "遇到报错先读最后一行。"],
        },
        ensure_ascii=False,
    )


class FakeResponses:
    def __init__(self, outputs: list[str]):
        self.outputs = iter(outputs)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=next(self.outputs))


class FakeClient:
    def __init__(self, outputs: list[str]):
        self.responses = FakeResponses(outputs)


def test_prompt_contains_user_constraints() -> None:
    prompt = build_user_input(profile())
    assert "从零学习 Python 基础语法" in prompt
    assert "每周学习天数：2 天" in prompt
    assert "每天最多学习：45 分钟" in prompt


def test_python_curriculum_is_in_instructions() -> None:
    from planner import build_instructions

    instructions = build_instructions()
    assert "变量、基本类型与输入" in instructions
    assert instructions.index("条件判断") < instructions.index("循环")


def test_request_schema_keeps_structure_but_not_local_range_keywords() -> None:
    encoded_schema = json.dumps(STUDY_PLAN_SCHEMA, ensure_ascii=False)
    assert '"additionalProperties": false' in encoded_schema
    assert "minLength" not in encoded_schema
    assert "maximum" not in encoded_schema


def test_valid_json_plan_is_parsed() -> None:
    client = FakeClient([response_payload()])
    plan = generate_study_plan(profile(), client=client)
    assert plan.weekly_plans[0].tasks[0].title == "安装与打印"
    assert client.responses.calls[0]["store"] is False
    assert client.responses.calls[0]["reasoning"] == {"effort": "none"}


def test_invalid_json_retries_once_with_correction() -> None:
    client = FakeClient(["not json", response_payload()])
    plan = generate_study_plan(profile(), client=client)
    assert plan.title == "Python 起步计划"
    assert len(client.responses.calls) == 2
    assert "未通过校验" in client.responses.calls[1]["input"]


def test_generation_metrics_report_retry_without_raw_input() -> None:
    collected = []
    client = FakeClient(["not json", response_payload()])
    generate_study_plan(profile(), client=client, on_metrics=collected.append)
    assert collected[0].success is True
    assert collected[0].attempts == 2
    assert collected[0].latency_ms >= 0
    assert collected[0].input_tokens is None


def test_generation_metrics_report_final_failure() -> None:
    collected = []
    client = FakeClient(["not json", "still not json"])
    with pytest.raises(PlanGenerationError):
        generate_study_plan(profile(), client=client, on_metrics=collected.append)
    assert collected[0].success is False
    assert collected[0].error_category == "invalid_output"


def test_generation_metrics_count_provider_tokens() -> None:
    collected = []
    client = FakeClient([response_payload()])
    original_create = client.responses.create

    def create_with_usage(**kwargs):
        response = original_create(**kwargs)
        response.usage = SimpleNamespace(input_tokens=123, output_tokens=456)
        return response

    client.responses.create = create_with_usage
    generate_study_plan(profile(), client=client, on_metrics=collected.append)
    assert collected[0].input_tokens == 123
    assert collected[0].output_tokens == 456


def test_invalid_output_after_retry_is_user_friendly() -> None:
    client = FakeClient(["not json", "still not json"])
    with pytest.raises(PlanGenerationError, match="格式或时长"):
        generate_study_plan(profile(), client=client)


def test_network_failure_retries_once_then_shows_safe_message() -> None:
    client = FakeClient([])
    calls = []

    def fail(**kwargs):
        calls.append(kwargs)
        raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))

    client.responses.create = fail
    with pytest.raises(PlanGenerationError, match="检查网络") as caught:
        generate_study_plan(profile(), client=client)
    assert len(calls) == 2
    assert "example.invalid" not in str(caught.value)


def test_rate_limit_does_not_expose_provider_error() -> None:
    client = FakeClient([])
    collected = []

    def fail(**kwargs):
        response = httpx.Response(
            429, request=httpx.Request("POST", "https://example.invalid")
        )
        raise RateLimitError("secret-provider-detail", response=response, body=None)

    client.responses.create = fail
    with pytest.raises(PlanGenerationError, match="请求过于频繁") as caught:
        generate_study_plan(profile(), client=client, on_metrics=collected.append)
    assert "secret-provider-detail" not in str(caught.value)
    assert collected[0].error_category == "rate_limit"


def test_invalid_api_key_has_actionable_message() -> None:
    client = FakeClient([])

    def fail(**kwargs):
        response = httpx.Response(
            401, request=httpx.Request("POST", "https://example.invalid")
        )
        raise APIStatusError("provider-private-detail", response=response, body=None)

    client.responses.create = fail
    with pytest.raises(PlanGenerationError, match="API Key 是否有效") as caught:
        generate_study_plan(profile(), client=client)
    assert "provider-private-detail" not in str(caught.value)
