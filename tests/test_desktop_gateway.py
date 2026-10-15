import threading
from types import SimpleNamespace

import pytest

from desktop.credentials import CredentialStore
from desktop.gateway import Cancelled, GatewayError, ModelGateway, instructions
from desktop.models import DesktopProfile, ModelConnection
from tests.test_progress_store import sample_plan, sample_profile


class Client:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = []
        self.responses = self
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        text = next(self.outputs)
        return SimpleNamespace(output_text=text, usage=SimpleNamespace(input_tokens=10, output_tokens=20, prompt_tokens=10, completion_tokens=20),
                               choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")])


@pytest.mark.parametrize("protocol", ["responses", "chat"])
@pytest.mark.parametrize("subject", ["Python", "英语", "考试复习"])
def test_both_protocols_validate_general_subjects(protocol, subject):
    profile = DesktopProfile(**sample_profile().model_dump(), subject=subject)
    config = ModelConnection(name="测试", base_url="https://example.invalid/v1", model="custom-model", api_type=protocol)
    client = Client([sample_plan().model_dump_json()])
    gateway = ModelGateway(config, "not-real-key", client=client)
    plan, provenance = gateway.generate(profile)
    assert plan == sample_plan()
    assert client.calls[0]["model"] == "custom-model"
    assert provenance["metrics"]["input_tokens"] == 10
    if subject != "Python":
        assert "Python主题顺序" not in instructions(profile)


def test_switching_connections_uses_selected_url_key_and_model(monkeypatch):
    configured = []
    def make_client(**kwargs):
        configured.append(kwargs)
        return Client([sample_plan().model_dump_json()])
    monkeypatch.setattr("desktop.gateway.OpenAI", make_client)
    for index in (1, 2):
        config = ModelConnection(name=f"连接{index}", base_url=f"https://provider{index}.invalid/v1", model=f"model{index}")
        gateway = ModelGateway(config, f"test-key-{index}")
        gateway.generate(DesktopProfile.model_validate(sample_profile().model_dump()))
        assert gateway.client.calls[0]["model"] == f"model{index}"
    assert configured[0]["base_url"] != configured[1]["base_url"]
    assert configured[0]["api_key"] == "test-key-1"
    assert configured[1]["api_key"] == "test-key-2"
    assert all(c["max_retries"] == 0 for c in configured)


def test_invalid_output_retries_once_and_cancel_discards_response():
    config = ModelConnection(name="测试", base_url="https://example.invalid", model="model")
    client = Client(["not json", "still not json"])
    gateway = ModelGateway(config, "placeholder", client=client)
    profile = DesktopProfile.model_validate(sample_profile().model_dump())
    with pytest.raises(GatewayError, match="连续两次"):
        gateway.generate(profile)
    assert len(client.calls) == 2
    cancel = threading.Event()
    class CancellingClient(Client):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            cancel.set()
            return response
    gateway = ModelGateway(config, "placeholder", client=CancellingClient([sample_plan().model_dump_json()]))
    with pytest.raises(Cancelled):
        gateway.generate(profile, cancelled=cancel)


def test_credential_fallback_stays_in_memory_and_config_has_no_key():
    class Unavailable:
        def set_password(self, *_args):
            raise RuntimeError("unavailable")
        def get_password(self, *_args):
            raise RuntimeError("unavailable")
    credentials = CredentialStore(Unavailable())
    assert not credentials.set("config", "private-placeholder", True)
    assert credentials.get("config") == "private-placeholder"
    assert CredentialStore(Unavailable()).get("config") == ""
    config = ModelConnection(name="测试", base_url="https://example.invalid", model="model")
    assert "api_key" not in config.model_dump()


def test_custom_address_cannot_embed_credentials():
    with pytest.raises(ValueError):
        ModelConnection(name="坏地址", base_url="https://example.invalid/v1?api_key=private", model="test")


@pytest.mark.parametrize("protocol", ["responses", "chat"])
def test_real_sdk_serialization_with_mock_http_transport(protocol):
    import httpx
    from openai import OpenAI
    captured = []
    def handler(request):
        captured.append(request)
        if protocol == "responses":
            payload = dict(id="resp_test", object="response", created_at=1, status="completed", model="selected-model",
                           output=[dict(type="message", id="msg_test", role="assistant", status="completed",
                                        content=[dict(type="output_text", text=sample_plan().model_dump_json(), annotations=[])])],
                           usage=dict(input_tokens=7, output_tokens=11, total_tokens=18))
        else:
            payload = dict(id="chat_test", object="chat.completion", created=1, model="selected-model",
                           choices=[dict(index=0, finish_reason="stop", message=dict(role="assistant", content=sample_plan().model_dump_json()))],
                           usage=dict(prompt_tokens=7, completion_tokens=11, total_tokens=18))
        return httpx.Response(200, json=payload)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with OpenAI(api_key="placeholder-credential", base_url="https://provider.invalid/v1", http_client=http, max_retries=0) as client:
            config = ModelConnection(name="自选连接", base_url="https://provider.invalid/v1", model="selected-model", api_type=protocol)
            _, provenance = ModelGateway(config, "placeholder-credential", client=client).generate(DesktopProfile.model_validate(sample_profile().model_dump()))
    assert captured[0].headers["authorization"] == "Bearer placeholder-credential"
    assert captured[0].url.path == ("/v1/responses" if protocol == "responses" else "/v1/chat/completions")
    assert provenance["metrics"]["input_tokens"] == 7


def test_auth_error_is_understandable_and_does_not_echo_provider_secret():
    import httpx
    from openai import OpenAI
    transport = httpx.MockTransport(lambda request: httpx.Response(401, json={"error": {"message": "private-value-must-not-leak", "type": "authentication_error"}}))
    with httpx.Client(transport=transport) as http:
        with OpenAI(api_key="placeholder", base_url="https://provider.invalid", http_client=http, max_retries=0) as client:
            config = ModelConnection(name="测试", base_url="https://provider.invalid", model="test")
            with pytest.raises(GatewayError, match="API Key") as error:
                ModelGateway(config, "placeholder", client=client).generate(DesktopProfile.model_validate(sample_profile().model_dump()))
            assert "private-value" not in str(error.value)
