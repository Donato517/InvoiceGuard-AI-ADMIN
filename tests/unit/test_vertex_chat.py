"""
模組名稱：tests.unit.test_vertex_chat
功能說明：Agent Platform 對話模組的離線測試；以 httpx.MockTransport 模擬 Google，憑證為合成值。
"""

import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import Settings
from app.integrations.chat_models import ChatMessage
from app.integrations.gemini import GeminiError
from app.integrations.vertex_chat import VertexChatClient
from app.integrations.vertex_embedding import EmbeddingError, VertexEmbeddingClient

SERVICE_ACCOUNT = json.dumps(
    {
        "type": "service_account",
        "client_email": "synthetic@example.invalid",
        "private_key": "synthetic",
        "token_uri": "https://oauth2.googleapis.com/token",
    }
)
REPLY = {
    "modelVersion": "synthetic-flash",
    "candidates": [
        {
            "finishReason": "STOP",
            "content": {"parts": [{"text": "思考", "thought": True}, {"text": "回答 [1]"}]},
        }
    ],
    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2, "totalTokenCount": 3},
}


@pytest.fixture(autouse=True)
def synthetic_token(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if "real_token_flow" not in request.node.name:
        monkeypatch.setattr(VertexEmbeddingClient, "_access_token", lambda self, deadline: "token")


def test_real_token_flow_reaches_generate_content() -> None:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    info = {**json.loads(SERVICE_ACCOUNT), "private_key": pem}
    seen: list[httpx.Request] = []

    def google(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "issued", "expires_in": 3600})
        return httpx.Response(200, json=REPLY)

    client = VertexChatClient(
        project_id="synthetic-project",
        model="synthetic-flash",
        service_account_json=json.dumps(info),
        http=httpx.Client(transport=httpx.MockTransport(google)),
    )

    assert client.chat([ChatMessage("user", "問題")], timeout_seconds=6).text == "回答 [1]"
    assert [request.url.host for request in seen] == [
        "oauth2.googleapis.com",
        "aiplatform.googleapis.com",
    ]
    assert seen[1].headers["authorization"] == "Bearer issued"


@pytest.mark.parametrize("reason", ["SAFETY", "RECITATION", "PROHIBITED_CONTENT"])
def test_blocked_reply_with_text_is_rejected(reason: str) -> None:
    data = {"candidates": [{"finishReason": reason, "content": {"parts": [{"text": "回答 [1]"}]}}]}
    client = make_client(lambda request: httpx.Response(200, json=data))

    with pytest.raises(GeminiError) as error:
        client.chat([ChatMessage("user", "問題")], timeout_seconds=6)

    assert error.value.code == "gemini_blocked"


def make_client(google: Any) -> VertexChatClient:
    return VertexChatClient(
        project_id="synthetic-project",
        model="synthetic-flash",
        service_account_json=SERVICE_ACCOUNT,
        http=httpx.Client(transport=httpx.MockTransport(google)),
    )


def test_chat_uses_service_account_and_agent_platform() -> None:
    seen: list[httpx.Request] = []

    def google(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=REPLY)

    reply = make_client(google).chat(
        [ChatMessage("user", "問題")], system_instruction="規則", max_tokens=4000, timeout_seconds=6
    )

    assert (reply.text, reply.finish_reason, reply.total_tokens) == ("回答 [1]", "STOP", 3)
    request = seen[0]
    assert str(request.url) == (
        "https://aiplatform.googleapis.com/v1/projects/synthetic-project/locations/global"
        "/publishers/google/models/synthetic-flash:generateContent"
    )
    assert request.headers["authorization"] == "Bearer token"
    assert "x-goog-api-key" not in request.headers
    body = json.loads(request.content)
    assert body["contents"] == [{"role": "user", "parts": [{"text": "問題"}]}]
    assert body["systemInstruction"] == {"parts": [{"text": "規則"}]}
    assert body["generationConfig"] == {"maxOutputTokens": 4000}


@pytest.mark.parametrize(
    "status,payload,code",
    [
        (429, {"error": {"status": "RESOURCE_EXHAUSTED"}}, "gemini_quota_exceeded"),
        (403, {"error": {"status": "PERMISSION_DENIED"}}, "gemini_denied"),
        (404, {"error": {"status": "NOT_FOUND"}}, "gemini_not_found"),
        (500, {"error": {"status": "INTERNAL"}}, "gemini_failed"),
    ],
)
def test_google_errors_use_fixed_codes(status: int, payload: dict[str, Any], code: str) -> None:
    client = make_client(lambda request: httpx.Response(status, json=payload))

    with pytest.raises(GeminiError) as error:
        client.chat([ChatMessage("user", "問題")], timeout_seconds=6)

    assert error.value.code == code


@pytest.mark.parametrize(
    "failure,code",
    [
        (httpx.ReadTimeout("synthetic"), "gemini_timeout"),
        (httpx.ConnectError("synthetic"), "gemini_unreachable"),
    ],
)
def test_transport_failures_use_fixed_codes(failure: Exception, code: str) -> None:
    def google(request: httpx.Request) -> httpx.Response:
        raise failure

    with pytest.raises(GeminiError) as error:
        make_client(google).chat([ChatMessage("user", "問題")], timeout_seconds=6)

    assert error.value.code == code


@pytest.mark.parametrize(
    "token_error,code",
    [
        (EmbeddingError("embedding_auth_failed", "HTTP401"), "gemini_auth_failed"),
        (EmbeddingError("embedding_timeout"), "gemini_timeout"),
        (EmbeddingError("embedding_failed"), "gemini_failed"),
    ],
)
def test_token_failures_become_chat_errors(
    monkeypatch: pytest.MonkeyPatch, token_error: EmbeddingError, code: str
) -> None:
    def fail(self: VertexEmbeddingClient, deadline: float) -> str:
        raise token_error

    monkeypatch.setattr(VertexEmbeddingClient, "_access_token", fail)
    client = make_client(lambda request: pytest.fail("沒有權杖不可呼叫模型"))

    with pytest.raises(GeminiError) as error:
        client.chat([ChatMessage("user", "問題")], timeout_seconds=6)

    assert error.value.code == code


def test_reply_after_the_budget_is_not_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr("app.integrations.vertex_chat.time.monotonic", lambda: clock[0])

    def slow(request: httpx.Request) -> httpx.Response:
        clock[0] += 2
        return httpx.Response(200, json=REPLY)

    with pytest.raises(GeminiError) as error:
        make_client(slow).chat([ChatMessage("user", "問題")], timeout_seconds=1)

    assert error.value.code == "gemini_timeout"


@pytest.mark.parametrize("seconds", [0, -1])
def test_no_time_left_does_not_call_google(seconds: float) -> None:
    client = make_client(lambda request: pytest.fail("沒有時間不可呼叫模型"))

    with pytest.raises(GeminiError) as error:
        client.chat([ChatMessage("user", "問題")], timeout_seconds=seconds)

    assert error.value.code == "gemini_timeout"


@pytest.mark.parametrize("messages", [[], [ChatMessage("user", " ")], [ChatMessage("model", "答")]])
def test_invalid_messages_are_rejected(messages: list[ChatMessage]) -> None:
    client = make_client(lambda request: pytest.fail("輸入無效不可呼叫模型"))

    with pytest.raises(GeminiError) as error:
        client.chat(messages)

    assert error.value.code == "gemini_invalid_input"


def test_missing_configuration_is_reported_without_network() -> None:
    with pytest.raises(GeminiError) as error:
        VertexChatClient.from_settings(Settings(GOOGLE_CLOUD_PROJECT_ID=""))

    assert error.value.code == "gemini_not_configured"
