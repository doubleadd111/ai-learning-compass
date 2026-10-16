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
from tests.test_desktop_planning import ask, draft, ready
from tests.test_progress_store import sample_plan


def forbidden(node):
    if isinstance(node, dict):
        return (set(node) & UNSUPPORTED_STRUCTURED_OUTPUT_KEYWORDS) or any(forbidden(v) for v in node.values())
    if isinstance(node, list):
        return any(forbidden(v) for v in node)
    return False


def sdk(config, outputs, calls, *, reject_unions=False):
    results = iter(outputs)
    def handle(request):
        body = json.loads(request.content)
        calls.append(body)
        fmt = body.get("text", {}).get("format", body.get("response_format", {}))
        schema = fmt.get("schema", fmt.get("json_schema", {}).get("schema"))
        if schema and forbidden(schema):
            return httpx.Response(400, json={"error": {"code": "invalid_json_schema", "param": "text.format.schema", "message": "Unsupported maxItems"}})
        if schema and reject_unions and '"anyOf"' in json.dumps(schema):
            return httpx.Response(400, json={"error": {"code": "invalid_request_error", "message": "Invalid schema anyOf type"}})
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


def wire_turn(turn):
    data = turn.model_dump()
    data['outlines'] = [data.pop('outline')] if turn.outline is not None else []
    data.pop('outline', None)
    return json.dumps(data, ensure_ascii=False)


@pytest.mark.parametrize('protocol', ['responses', 'chat'])
@pytest.mark.parametrize('decision', ['ask', 'outline'])
def test_deepseek_clarification_avoids_union_schema_and_returns_domain_turn(protocol, decision):
    config = ModelConnection(name='严格联合类型服务', provider='DeepSeek', base_url='https://example.invalid', model='mock',
                             api_type=protocol, output_format='json_schema')
    expected = ask() if decision == 'ask' else ready()
    calls = []
    with sdk(config, [wire_turn(expected)], calls, reject_unions=True) as client:
        result, event = ModelGateway(config, 'placeholder', client=client).clarify(draft(connection=config))
    assert result == expected and len(calls) == 1 and event['metrics']['success']
    assert 'outlines' not in result.model_dump()
    fmt = calls[0].get('text', {}).get('format', calls[0].get('response_format', {}))
    schema = fmt.get('schema', fmt.get('json_schema', {}).get('schema'))
    assert '"anyOf"' not in json.dumps(schema) and schema['properties']['outlines']['type'] == 'array'


@pytest.mark.parametrize('invalid', ['extra_outline', 'wrong_decision', 'short_summary', 'wrong_shape', 'quick_question'])
def test_deepseek_wire_preserves_local_constraints_and_bounded_repair(invalid):
    config = ModelConnection(name='严格服务', provider='DeepSeek', base_url='https://example.invalid', model='mock', output_format='json_schema')
    value = json.loads(wire_turn(ready()))
    if invalid == 'extra_outline':
        value['outlines'] *= 2
    elif invalid == 'wrong_decision':
        value['decision'] = 'ask'
    elif invalid == 'short_summary':
        value['outlines'][0]['points'][0]['summary'] = '短'
    elif invalid == 'wrong_shape':
        value['outlines'] = value['outlines'][0]
    else:
        value = json.loads(wire_turn(ask()))
    calls = []
    with sdk(config, [json.dumps(value), wire_turn(ready())], calls, reject_unions=True) as client:
        result, event = ModelGateway(config, 'placeholder', client=client).clarify(draft(connection=config, quick=True))
    assert result == ready() and event['metrics']['attempts'] == 2 and len(calls) == 2


def test_union_rejection_is_classified_without_copying_raw_message():
    from openai import APIStatusError
    from desktop.gateway import friendly_error
    error = APIStatusError('private', response=httpx.Response(400, request=httpx.Request('POST', 'https://example.invalid')),
                           body={'error': {'code': 'invalid_request_error', 'message': 'Invalid schema anyOf type private-key'}})
    details = api_error_details(error)
    assert details['error_field'] == 'json_schema' and details['schema_feature'] == 'union_type'
    assert 'private' not in json.dumps(details) + friendly_error(error)[0]


@pytest.mark.parametrize('provider,output_format', [('DeepSeek', 'json_object'), ('OpenAI', 'json_schema')])
def test_other_wire_contracts_are_unchanged(provider, output_format):
    config = ModelConnection(name='原格式', provider=provider, base_url='https://example.invalid', model='mock',
                             api_type='responses', output_format=output_format)
    calls = []
    with sdk(config, [ready().model_dump_json()], calls) as client:
        result, _ = ModelGateway(config, 'placeholder', client=client).clarify(draft(connection=config))
    assert result == ready() and len(calls) == 1
    fmt = calls[0]['text']['format']
    if output_format == 'json_schema':
        assert 'outline' in fmt['schema']['properties'] and 'outlines' not in fmt['schema']['properties']
    else:
        assert '"outline"' in calls[0]['input'] and 'outlines数组' not in calls[0]['instructions']


def test_union_wire_can_resume_questions_from_old_draft(tmp_path):
    from desktop.store import Store
    config = ModelConnection(name='DeepSeek', provider='DeepSeek', base_url='https://example.invalid', model='mock', output_format='json_schema')
    saved = draft(connection=config)
    store = Store(tmp_path)
    store.save_draft(saved)
    calls = []
    outputs = [wire_turn(ask()), wire_turn(ask('device', '你现在可以使用什么学习设备？')), wire_turn(ready())]
    with sdk(config, outputs, calls, reject_unions=True) as client:
        gateway = ModelGateway(config, 'placeholder', client=client)
        for index in range(3):
            saved = store.drafts()[0]
            turn, _ = gateway.clarify(saved)
            saved.receive_turn(turn)
            if saved.state == 'questions':
                saved.answer_questions({saved.questions[0].id: '我自己的回答'})
            store.save_draft(saved)
    assert saved.state == 'outline' and saved.rounds == 2 and len(saved.answers) == 2
    assert len(calls) == 3 and saved.outline == ready().outline and store.list_plans() == []
    assert 'outlines' not in saved.model_dump()


def test_bad_wire_still_stops_after_one_repair():
    config = ModelConnection(name='严格服务', provider='DeepSeek', base_url='https://example.invalid', model='mock', output_format='json_schema')
    calls = []
    with sdk(config, ['{"decision":"outline","questions":[],"outlines":[]}'] * 2, calls, reject_unions=True) as client:
        with pytest.raises(GatewayError, match='连续两次'):
            ModelGateway(config, 'placeholder', client=client).clarify(draft(connection=config))
    assert len(calls) == 2
