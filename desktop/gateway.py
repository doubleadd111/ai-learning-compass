"""可配置的模型网关；不从环境变量偷偷切换用户所选的服务。"""
from __future__ import annotations

import json
import threading
from time import perf_counter

import httpx
from openai import APIConnectionError, APIStatusError, OpenAI, RateLimitError

from curriculum import curriculum_prompt
from models import StudyPlan, validate_plan_for_profile
from planner import STUDY_PLAN_SCHEMA
from desktop.models import DesktopProfile, ModelConnection


class Cancelled(Exception):
    pass


class GatewayError(ValueError):
    def __init__(self, message, provenance=None):
        super().__init__(message)
        self.provenance = provenance or {}


def instructions(profile: DesktopProfile):
    text = f"""你是严谨、务实的中文学习教练。生成一份适合用户基础的{profile.subject}学习计划。
所有自然语言字段用简体中文，代码标识符可使用原文。每天安排一个聚焦任务，说明动作、预计时间和可检查的产出。
严格遵守周数、每周天数和每日时间上限。每周的 day 必须从1重新编号，week_number从1连续。
时间很短时，不要把环境安装与学习新概念都塞进一次任务；提供已经可用环境中的最小练习。
时间很长时，在同一任务中写明学习、练习、验证和休息的安排，不用一个几分钟的小练习充满整个时段。
保留合理休息和复习，按先修关系递进。不要编造链接、课程或保证效果。
只输出符合所给结构的JSON，不输出Markdown或额外解释。"""
    if profile.subject.strip().lower() in {"python", "python编程", "python 编程"}:
        text += "\nPython主题顺序：\n" + curriculum_prompt()
    return text


def user_prompt(profile, context="", correction=""):
    result = f"""请生成学习计划。资料：
{profile.model_dump_json()}
每周恰好{profile.days_per_week}天，每天最多{profile.minutes_per_day}分钟，每周总时长不超过{profile.weekly_minutes}分钟。
每个任务包含day、title、description、duration_minutes、deliverable；每周包含week_number、theme、milestone、tasks；
整份计划包含title、overview、weekly_plans、learning_tips（2至5条）。描述保持简洁。"""
    if context:
        result += "\n调整背景：" + context
    if correction:
        result += "\n上一版未通过本地校验，请重新完整输出并修正：" + correction[:3000]
    return result


def friendly_error(error):
    if isinstance(error, RateLimitError):
        return "请求过于频繁或临时额度不足，请稍后再试。", "rate_limit"
    if isinstance(error, APIConnectionError):
        return "无法连接模型服务，请检查网络和服务地址。", "connection"
    if isinstance(error, APIStatusError):
        if error.status_code in {401, 403}:
            return "模型服务拒绝访问，请检查这套配置的 API Key 和权限。", "authentication"
        if error.status_code == 402:
            return "模型服务余额不足，请到服务商账户检查额度。", "quota"
        if error.status_code in {400, 404, 422}:
            return "服务地址、模型名或接口参数不受支持。请核对接口类型、JSON格式和高级参数。", "unsupported"
        return "模型服务暂时不可用，请稍后再试。", "service"
    return "模型调用失败，请检查配置后重试。", "unknown"


class ModelGateway:
    def __init__(self, connection: ModelConnection, api_key: str, *, client=None):
        self.connection = connection.model_copy(deep=True)
        if not api_key.strip():
            raise GatewayError("请先在模型设置中填写这套配置的 API Key。")
        self.client = client or OpenAI(api_key=api_key, base_url=connection.base_url,
                                     max_retries=0, timeout=httpx.Timeout(120, connect=10))

    def request(self, system, prompt, schema, name, max_tokens):
        config = self.connection
        if config.api_type == "responses":
            fmt = {"type": config.output_format}
            if config.output_format == "json_schema":
                fmt.update(name=name, schema=schema, strict=True)
            kwargs = dict(model=config.model, instructions=system, input=prompt,
                          text={"format": fmt}, max_output_tokens=max_tokens, store=False)
            if config.provider == "DeepSeek":
                kwargs["reasoning"] = {"effort": "none"}
            response = self.client.responses.create(**kwargs)
            text = getattr(response, "output_text", None) if getattr(response, "status", "completed") != "incomplete" else ""
            usage = getattr(response, "usage", None)
            counts = (getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
        else:
            fmt = {"type": config.output_format}
            if config.output_format == "json_schema":
                fmt["json_schema"] = {"name": name, "schema": schema, "strict": True}
            kwargs = dict(model=config.model, messages=[{"role": "system", "content": system},
                          {"role": "user", "content": prompt}], response_format=fmt)
            kwargs[config.max_tokens_field] = max_tokens
            if config.provider == "DeepSeek":
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
            response = self.client.chat.completions.create(**kwargs)
            choices = getattr(response, "choices", []) or []
            choice = choices[0] if choices else None
            text = choice.message.content if choice and getattr(choice, "finish_reason", "stop") != "length" else None
            usage = getattr(response, "usage", None)
            counts = (getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None))
        return text or "", counts

    def generate(self, profile: DesktopProfile, *, context="", cancelled: threading.Event | None = None):
        started = perf_counter()
        metrics = {"attempts": 0, "input_tokens": None, "output_tokens": None, "success": False}
        provenance = {"connection_name": self.connection.name, "model": self.connection.model, "metrics": metrics}
        correction = ""
        try:
            for attempt in range(2):
                if cancelled and cancelled.is_set():
                    raise Cancelled()
                metrics["attempts"] += 1
                try:
                    text, usage = self.request(instructions(profile), user_prompt(profile, context, correction),
                                               STUDY_PLAN_SCHEMA, "study_plan", min(24000, max(4000, profile.plan_weeks * profile.days_per_week * 400)))
                except (APIStatusError, APIConnectionError) as error:
                    message, category = friendly_error(error)
                    metrics["error_category"] = category
                    raise GatewayError(message, provenance) from None
                for key, value in zip(("input_tokens", "output_tokens"), usage):
                    if value is not None:
                        metrics[key] = (metrics[key] or 0) + value
                if cancelled and cancelled.is_set():
                    raise Cancelled()
                try:
                    plan = StudyPlan.model_validate_json(text)
                    validate_plan_for_profile(plan, profile)
                except ValueError as error:
                    correction = str(error)
                    if attempt == 1:
                        metrics["error_category"] = "invalid_output"
                        raise GatewayError("生成结果连续两次未通过格式或时间校验，请缩短周期或更换模型。", provenance) from None
                else:
                    metrics["success"] = True
                    return plan, provenance
            raise GatewayError("未能生成有效计划。", provenance)
        finally:
            metrics["latency_ms"] = round((perf_counter() - started) * 1000)

    def list_models(self):
        try:
            return sorted({item.id for item in self.client.with_options(timeout=15).models.list().data})
        except Exception as error:
            message, _ = friendly_error(error)
            raise GatewayError(message + " 也可以手动填写模型 ID。") from None

    def test(self):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
        try:
            text, usage = self.request("你正在进行JSON连接测试。只输出JSON对象。", '返回 {"ok":true}', schema, "connection_test", 256)
            if json.loads(text) != {"ok": True}:
                raise ValueError()
            return usage
        except (APIStatusError, APIConnectionError) as error:
            raise GatewayError(friendly_error(error)[0]) from None
        except ValueError:
            raise GatewayError("服务已响应，但未返回可用 JSON。请更换输出格式或模型。") from None
