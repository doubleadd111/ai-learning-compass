"""Allow-listed connection diagnostics: never serialize requests or exception text."""
from __future__ import annotations

import socket
import ssl
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field
from desktop.output_validation import OutputIssue


class ConnectionDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: str
    stage: str
    elapsed_ms: int
    http_status: int | None = None
    category: str
    exception_types: list[str]
    network_mode: Literal["system", "direct"] = "system"
    api_type: Literal["responses", "chat"] | None = None
    output_format: Literal["json_schema", "json_object"] | None = None
    service_code: Literal["invalid_json_schema", "invalid_request_error", "unsupported_parameter", "unsupported_value", "model_not_found", "invalid_model", "not_found"] | None = None
    error_field: Literal["json_schema", "output_format", "model", "output_tokens", "reasoning", "unknown"] | None = None
    schema_feature: Literal["union_type"] | None = None
    validation_issues: list[OutputIssue] = Field(default_factory=list, max_length=3)

    def summary(self):
        labels = {"models": "获取模型列表", "connection_test": "JSON连接测试",
                  "study_plan": "生成计划", "planning_clarification": "需求与概略", "planning_revision": "修改预览"}
        return (f"操作：{labels.get(self.operation, self.operation)}\n阶段：{self.stage}\n"
                f"耗时：{self.elapsed_ms} ms\nHTTP：{self.http_status if self.http_status is not None else '未收到'}\n"
                f"类别：{self.category}\n路由：{'直连' if self.network_mode == 'direct' else '默认代理'}\n"
                f"接口：{self.api_type or '未记录'} / {self.output_format or '未记录'}\n"
                f"服务错误码：{self.service_code or '未记录'}\n参数类别：{self.error_field or '未记录'}\n"
                f"结构类别：{'联合类型（anyOf）' if self.schema_feature == 'union_type' else '未记录'}\n"
                f"异常类型：{' → '.join(self.exception_types) or '无'}" +
                ("\n本地校验：\n" + "\n".join(issue.summary() for issue in self.validation_issues) if self.validation_issues else ""))


def api_error_details(error):
    """Map structured SDK error metadata to fixed values, never copy raw text."""
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return {}
    body = body.get("error", body)
    if not isinstance(body, dict):
        return {}
    code = body.get("code")
    known = {"invalid_json_schema", "invalid_request_error", "unsupported_parameter", "unsupported_value", "model_not_found", "invalid_model", "not_found"}
    code = code if isinstance(code, str) and code in known else None
    fields = {"text.format.schema": "json_schema", "response_format.json_schema.schema": "json_schema",
              "json_schema": "json_schema", "response_format": "output_format", "text.format": "output_format",
              "model": "model", "max_tokens": "output_tokens", "max_output_tokens": "output_tokens",
              "max_completion_tokens": "output_tokens", "reasoning": "reasoning", "thinking": "reasoning"}
    param = body.get("param")
    field = fields.get(param, "unknown") if isinstance(param, str) else "unknown"
    if code == "invalid_json_schema":
        field = "json_schema"
    details = dict(service_code=code, error_field=field)
    message = body.get("message")
    if isinstance(message, str):
        # Interpret in memory; expose only a fixed classification, never excerpts.
        message = message.casefold()
        if "schema" in message and "anyof" in message and any(word in message for word in ("invalid", "unsupported", "not supported", "not allowed")):
            details.update(error_field="json_schema", schema_feature="union_type")
    return details


def classify(error):
    chain, seen = [], set()
    while error is not None and id(error) not in seen and len(chain) < 8:
        seen.add(id(error))
        chain.append(error)
        error = error.__cause__ or error.__context__
    if any(isinstance(e, (ImportError, ModuleNotFoundError)) for e in chain):
        category = "runtime_dependency"
    elif any(isinstance(e, (ssl.SSLError, ssl.CertificateError)) for e in chain):
        category = "tls"
    elif any(isinstance(e, socket.gaierror) for e in chain):
        category = "dns"
    elif any(isinstance(e, httpx.ProxyError) for e in chain):
        category = "proxy"
    elif any(isinstance(e, (httpx.TimeoutException, TimeoutError)) or type(e).__name__ == "APITimeoutError" for e in chain):
        category = "timeout"
    else:
        status = next((getattr(e, "status_code", None) for e in chain if getattr(e, "status_code", None)), None)
        category = ({401: "authentication", 403: "authentication", 402: "quota", 429: "rate_limit"}.get(status)
                    or ("unsupported" if status in (400, 404, 422) else "service" if status else "connection"))
    # Only names of exception classes, not attributes, messages, URLs or bodies.
    return category, [type(e).__name__ for e in chain]


MESSAGES = {
    "runtime_dependency": "程序缺少网络运行依赖，请保留诊断摘要并更新本机测试程序。",
    "tls": "TLS证书校验失败。请检查系统时间、代理证书或安全软件；不要关闭证书校验。",
    "dns": "无法解析服务域名，请检查网络、DNS和服务地址。",
    "proxy": "无法连接代理服务，请检查代理是否运行，或在连接高级设置明确选择直连。",
    "timeout": "模型服务响应超时，请检查网络或稍后重试。",
    "connection": "无法连接模型服务，请检查网络、服务地址和所选代理路由。",
}
