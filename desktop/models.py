from __future__ import annotations

from datetime import date, timedelta
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from models import LearningProfile


def new_id() -> str:
    return uuid4().hex


class DesktopProfile(LearningProfile):
    subject: str = Field(default="Python", min_length=1, max_length=80)


class ModelConnection(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str = Field(min_length=1, max_length=80)
    provider: str = "自定义"
    base_url: str
    model: str = Field(min_length=1, max_length=160)
    api_type: Literal["responses", "chat"] = "chat"
    output_format: Literal["json_schema", "json_object"] = "json_object"
    max_tokens_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        from urllib.parse import urlsplit

        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("请填写完整的服务地址")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("服务地址不能包含密码或查询参数，请在 API Key 栏填写密钥")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("远程模型服务请使用 HTTPS 地址")
        return value.strip().rstrip("/")


PROVIDER_PRESETS = {
    "DeepSeek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "api_type": "responses", "output_format": "json_schema"},
    "OpenAI": {"base_url": "https://api.openai.com/v1", "model": "", "api_type": "responses", "output_format": "json_schema", "max_tokens_field": "max_completion_tokens"},
    "自定义": {"base_url": "https://", "model": "", "api_type": "chat", "output_format": "json_object"},
}


def schedule_dates(start: date, weekdays: list[int], weeks: int) -> dict[tuple[int, int], str]:
    if not weekdays or len(weekdays) != len(set(weekdays)) or any(d not in range(7) for d in weekdays):
        raise ValueError("请选择不重复的每周学习日")
    result = {}
    for week in range(1, weeks + 1):
        dates = [start + timedelta(days=(week - 1) * 7 + offset) for offset in range(7)]
        for day, scheduled in enumerate((d for d in dates if d.weekday() in weekdays), 1):
            result[(week, day)] = scheduled.isoformat()
    return result
