"""Contract-level SDK tests; no service credentials or external requests."""
import json

import httpx
from openai import OpenAI
import pytest

from desktop.gateway import GatewayError, ModelGateway
from desktop.diagnostics import api_error_details
from desktop.models import ModelConnection
from desktop.planning import PlanningTurn, RevisionProposal
from planner import UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS
from tests.test_desktop_planning import draft, ready
from tests.test_progress_store import sample_plan


def forbidden(node):
    if isinstance(node, dict):
        return (set(node) & UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS) or any(forbidden(v) for v in node.values())
    if isinstance(node, list):
        return any(forbidden(v) for v in node)
    return False


def sdk(config, outputs, calls):
    results = iter(outputs)
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        fmt = body.get("text", {}).get("format", body.get("response_format", {}))
        schema = fmt.get("schema", fmt.get("json_schema", {}).get("schema"))
        if schema and forbidden(schema):
            return httpx.Response(400, json={"error": {"code": "invalid_json_schema", "param": "text.format.schema", "message": "Unsupported maxItems"}})
        text = next(results)
        if config.api_type == "responses":
            value = dict(id="resp_local", object="response", created_at=1, model="mock", status="completed",
                output=[dict(type="message", id="msg_local", role="assistant", status="completed",
                             content=[dict(type="output_text", text=text, annotations=[])])])
        else:
            value = dict(id="chat_local", object="chat.completion", created=1, model="mock",
                         choices=[dict(index=0, finish_reason="stop", message=dict(role="assistant", content=text))])
        return httpx.Response(200, json=value)
    return OpenAI(api_key="placeholder-not-a-real-key", base_url=config.base_url, max_retries=0,
                  http_client=httpx.Client(transport=httpx.MockTransport(handle)))


@pytest.mark.parametrize("protocol", ["responses", "chat"])
@pytest.mark.parametrize("stage", ["clarify", "revision"])
def test_guided_stages_use_compatible_schema(protocol, stage):
    config = ModelConnection(name="模拟严格服务", provider="DeepSeek", base_url="https://example.invalid", model="mock",
                             api_type=protocol, output_format="json_schema")
    calls = []
    if stage == "clarify":
        result_type, output = PlanningTurn, ready().model_dump_json()
    else:
        result_type = RevisionProposal
        output = json.dumps(dict(changes=[dict(task_id="stable-task", task=sample_plan().weekly_plans[0].tasks[0].model_dump(),
                                              due_date="2026-10-15", reason="细化具体学习步骤")], summary="只细化一项任务"))
    with sdk(config, [output], calls) as client:
        gateway = ModelGateway(config, "placeholder", client=client)
        result, _event = gateway.structured(result_type, "只返回JSON", "请按计划安排", "planning_" + stage)
    assert isinstance(result, result_type) and len(calls) == 1


@pytest.mark.parametrize("protocol", ["responses", "chat"])
def test_json_mode_includes_the_shape_in_prompt(protocol):
    config = ModelConnection(name="模拟JSON模式", base_url="https://example.invalid", model="mock", api_type=protocol, output_format="json_object")
    calls = []
    with sdk(config, [ready().model_dump_json()], calls) as client:
        ModelGateway(config, "placeholder", client=client).clarify(draft())
    prompt = calls[0].get("input") or calls[0]["messages"][-1]["content"]
    assert '"decision"' in prompt and '"outline"' in prompt and '"properties"' in prompt


def test_local_constraints_remain_after_wire_schema_is_relaxed():
    config = ModelConnection(name="模拟服务", base_url="https://example.invalid", model="mock", output_format="json_schema")
    calls = []
    invalid = ready().model_dump()
    invalid["outline"]["points"] = invalid["outline"]["points"][:1]
    with sdk(config, [json.dumps(invalid), ready().model_dump_json()], calls) as client:
        result, event = ModelGateway(config, "placeholder", client=client).clarify(draft())
    assert result.outline and event["metrics"]["attempts"] == 2
    assert len(calls) == 2


@pytest.mark.parametrize("field", ["Bearer private-key-must-not-leak", {"private": "secret"}])
def test_service_error_details_are_allowlisted_not_raw(field):
    from openai import APIStatusError
    from desktop.gateway import friendly_error
    error = APIStatusError("private body", response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")),
                           body={"error": {"code": "invalid_json_schema", "param": field, "message": "private-key"}})
    details = api_error_details(error)
    assert details == {"service_code": "invalid_json_schema", "error_field": "json_schema"}
    assert "private" not in json.dumps(details) + friendly_error(error)[0]


@pytest.mark.parametrize("body", [None, "private-response", {"error": "private-response"}])
def test_unstructured_parameter_error_keeps_safe_friendly_message(body):
    from openai import APIStatusError
    from desktop.gateway import friendly_error
    error = APIStatusError("private exception", response=httpx.Response(400, request=httpx.Request("POST", "https://example.invalid")), body=body)
    message, category = friendly_error(error)
    assert category == "unsupported" and "HTTP 400" in message
    assert "private" not in message
