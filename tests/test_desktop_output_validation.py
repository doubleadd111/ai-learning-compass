import json

import pytest
from pydantic import ValidationError

from desktop.gateway import GatewayError, ModelGateway
from desktop.models import ModelConnection
from desktop.output_validation import constraint_hint, validation_issues
from desktop.planning import PlanningTurn
from tests.test_desktop_planning import ask, draft, ready
from tests.test_desktop_schema import sdk


def explain(value):
    text = value if isinstance(value, str) else json.dumps(value)
    try:
        PlanningTurn.model_validate_json(text)
    except ValidationError as error:
        return validation_issues(error, text)
    raise AssertionError("expected invalid output")


@pytest.mark.parametrize("kind", ["missing", "extra", "type", "json", "empty", "summary", "options", "decision"])
def test_safe_failures_have_specific_rules_and_do_not_echo_output(kind):
    value = ready().model_dump()
    if kind == "missing":
        del value["outline"]["points"][0]["rationale"]
        expected = "missing_field"
    elif kind == "extra":
        value["private-model-value"] = "secret-never-log"
        expected = "extra_field"
    elif kind == "type":
        value["outline"]["points"][0]["summary"] = {"private": "secret-never-log"}
        expected = "wrong_type"
    elif kind == "json":
        value, expected = "not-json-secret-never-log", "invalid_json"
    elif kind == "empty":
        value, expected = "  ", "empty_output"
    elif kind == "summary":
        for point in value["outline"]["points"]:
            point["summary"] = "简短内容"
        expected = "summary_length"
    elif kind == "options":
        value = ask().model_dump()
        value["questions"][0]["options"] = ["重复", "重复"]
        expected = "question_options"
    else:
        value["decision"] = "ask"
        expected = "decision_ask"
    issues = explain(value)
    assert issues[0].rule == expected
    serialized = json.dumps([i.model_dump() for i in issues]) + "".join(i.summary() for i in issues)
    assert "private" not in serialized and "secret" not in serialized
    if kind == "summary":
        assert (issues[0].actual, issues[0].minimum, issues[0].maximum) == (12, 80, 140)


def test_field_length_count_is_exposed_but_value_is_not():
    value = ready().model_dump()
    value["outline"]["points"][0]["summary"] = "secret-never-log" * 10
    issues = explain(value)
    assert issues[0].rule == "length" and issues[0].maximum == 40
    assert issues[0].actual == len("secret-never-log" * 10)
    assert "secret" not in issues[0].summary()


def test_hint_keeps_local_limits_visible_to_model_without_changing_schema():
    raw = PlanningTurn.model_json_schema()
    before = json.dumps(raw)
    hint = constraint_hint(raw)
    assert '"maxLength":40' in hint and '"minItems":3' in hint and "OutlinePoint.summary" in hint
    assert json.dumps(raw) == before


def test_repair_reports_fields_clears_old_diagnostic_and_never_echoes_bad_output():
    config = ModelConnection(name="mock", base_url="https://example.invalid", model="mock", api_type="responses", output_format="json_schema")
    broken = ready().model_dump()
    broken["outline"]["points"][0]["rationale"] = ""
    broken["outline"]["points"][1]["rationale"] = "secret-never-echo" * 30
    calls = []
    with sdk(config, [json.dumps(broken), ready().model_dump_json()], calls) as client:
        gateway = ModelGateway(config, "placeholder", client=client)
        result, event = gateway.clarify(draft())
    assert result == ready() and event["metrics"]["attempts"] == 2
    assert "rationale" in calls[1]["input"] and "校验" in calls[1]["input"]
    assert "secret-never-echo" not in calls[1]["input"]
    assert gateway.last_diagnostic.validation_issues == []


def test_final_error_and_copy_diagnostic_show_safe_summary_not_raw_body():
    config = ModelConnection(name="mock", base_url="https://example.invalid", model="mock", api_type="responses")
    broken = ready().model_dump()
    for point in broken["outline"]["points"]:
        point["summary"] = "简短内容"
        point["rationale"] = "secret-model-output"
    calls = []
    with sdk(config, [json.dumps(broken)] * 2, calls) as client:
        gateway = ModelGateway(config, "placeholder", client=client)
        with pytest.raises(GatewayError, match="校验原因") as raised:
            gateway.clarify(draft())
    text = str(raised.value) + gateway.last_diagnostic.summary()
    assert "80" in text and "140" in text and "当前12" in text
    assert "secret-model-output" not in text and len(calls) == 2


def test_many_missing_fields_are_bounded_to_three_safe_issues():
    issues = explain({'private-extra-field': 'secret'})
    assert len(issues) == 3
    assert any(i.rule == 'missing_field' for i in issues)
    assert all(i.rule in {'missing_field', 'extra_field'} for i in issues)
    assert 'private' not in json.dumps([i.model_dump() for i in issues])


def test_unknown_validator_message_is_not_exposed():
    from pydantic import BaseModel, model_validator
    class PrivateModel(BaseModel):
        @model_validator(mode='after')
        def fail(self):
            raise ValueError('private-server-output')
    try:
        PrivateModel.model_validate_json('{}')
    except ValidationError as error:
        issues = validation_issues(error, '{}')
    assert issues[0].rule == 'invalid_value'
    assert 'private' not in issues[0].summary()
