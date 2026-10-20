"""全部为本机服务或 HTTP Mock，不使用真实 API Key。"""
import asyncio
import gzip
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from time import monotonic

import httpx
from openai import OpenAI
import pytest

from desktop.gateway import GatewayError, ModelGateway
from desktop.models import ModelConnection
from desktop.network import Cancelled, CancellableTransport, RequestControl
from tests.test_progress_store import sample_plan, sample_profile
from desktop.models import DesktopProfile


def test_tls_uses_system_and_public_roots_without_disabling_verification(monkeypatch):
    from desktop.network import tls_context
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    context = tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert len(context.get_ca_certs()) > 0


def test_tls_explicit_ca_policy_is_not_silently_expanded(monkeypatch):
    from desktop.network import tls_context
    calls = []
    marker = object()
    monkeypatch.setenv("SSL_CERT_FILE", "explicit-private-ca.pem")
    monkeypatch.setenv("SSL_CERT_DIR", "explicit-private-ca-directory")
    monkeypatch.setattr(ssl, "create_default_context", lambda **kwargs: calls.append(kwargs) or marker)
    assert tls_context() is marker
    assert calls == [{"cafile": "explicit-private-ca.pem", "capath": "explicit-private-ca-directory"}]


@pytest.mark.parametrize("gzip_body", [False, True])
def test_transport_keeps_request_and_response_semantics(gzip_body):
    captured = []
    async def handle(request):
        captured.append(request)
        payload = b'{"ok":true}'
        return httpx.Response(200, content=gzip.compress(payload) if gzip_body else payload,
                              headers={"content-encoding": "gzip"} if gzip_body else {})
    transport = CancellableTransport(RequestControl(), async_transport=httpx.MockTransport(handle))
    with httpx.Client(transport=transport, trust_env=False) as client:
        response = client.post("https://example.invalid/responses", headers={"authorization": "Bearer placeholder"}, json={"model": "chosen"})
    assert response.json() == {"ok": True}
    assert captured[0].headers["authorization"] == "Bearer placeholder"
    assert b"chosen" in captured[0].content
    assert transport.control.response_status == 200
    assert transport.control.received_bytes > 0 and transport.control.stage == "校验结果"


def test_total_timeout_cancels_even_when_server_sends_keep_alive():
    closed = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            try:
                for _ in range(80):
                    self.wfile.write(b" "); self.wfile.flush()
                    if closed.wait(.03):
                        break
            except (OSError, ConnectionError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    control = RequestControl()
    control.begin(.3)
    started = monotonic()
    try:
        with httpx.Client(transport=CancellableTransport(control), trust_env=False) as client:
            with pytest.raises(httpx.TimeoutException):
                client.get(f"http://127.0.0.1:{server.server_port}/")
        assert monotonic() - started < 1.5
    finally:
        closed.set()
        server.shutdown()
        server.server_close()


def test_cancellation_interrupts_pending_headers_and_cleans_up():
    entered, cleaned = threading.Event(), threading.Event()
    async def handle(request):
        entered.set()
        try:
            await asyncio.sleep(60)
        finally:
            cleaned.set()
    control = RequestControl()
    timer = threading.Timer(.2, control.cancel)
    timer.start()
    try:
        transport = CancellableTransport(control, async_transport=httpx.MockTransport(handle))
        with httpx.Client(transport=transport, trust_env=False) as client:
            with pytest.raises(Cancelled):
                client.get("https://example.invalid")
        assert entered.is_set() and cleaned.is_set()
    finally:
        timer.cancel()


def test_sdk_timeout_message_and_no_implicit_retry(monkeypatch):
    monkeypatch.setattr("desktop.gateway.TEST_TIMEOUT", 1)
    calls = []
    async def handle(request):
        calls.append(request)
        await asyncio.sleep(60)
    config = ModelConnection(name="测试", model="test", base_url="https://example.invalid")
    control = RequestControl()
    with httpx.Client(transport=CancellableTransport(control, async_transport=httpx.MockTransport(handle)), trust_env=False) as http:
        with OpenAI(api_key="placeholder", base_url=config.base_url, http_client=http, max_retries=0) as client:
            # This test measures one stalled transport request, not the SDK's
            # first lazy resource import (which can consume 1s on loaded Windows).
            _ = client.responses
            gateway = ModelGateway(config, "placeholder", client=client)
            gateway.control = control
            with pytest.raises(GatewayError, match="超时"):
                gateway.test()
    assert len(calls) == 1


@pytest.mark.parametrize("protocol", ["chat", "responses"])
def test_real_sdk_and_new_transport_generate_valid_plan(protocol):
    captured = []
    async def handle(request):
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
    control = RequestControl()
    config = ModelConnection(name="测试", model="selected-model", base_url="https://example.invalid/v1", api_type=protocol)
    transport = CancellableTransport(control, async_transport=httpx.MockTransport(handle))
    with httpx.Client(transport=transport, trust_env=False) as http:
        with OpenAI(api_key="placeholder", base_url=config.base_url, http_client=http, max_retries=0) as client:
            gateway = ModelGateway(config, "placeholder", client=client)
            gateway.control = control
            plan, _ = gateway.generate(DesktopProfile.model_validate(sample_profile().model_dump()))
            assert plan == sample_plan()
    assert len(captured) == 1
    assert captured[0].url.path == ("/v1/responses" if protocol == "responses" else "/v1/chat/completions")


def test_cancelled_before_send_does_not_issue_request():
    calls = []
    control = RequestControl()
    control.cancel()
    with httpx.Client(transport=CancellableTransport(control, async_transport=httpx.MockTransport(lambda r: calls.append(r))), trust_env=False) as http:
        with pytest.raises(Cancelled):
            http.get("https://example.invalid")
    assert not calls
