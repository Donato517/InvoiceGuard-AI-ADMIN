"""模組名稱：tests.unit.test_remote_admin_client；功能說明：以替身驗證本機呼叫雲端 ADMIN 的共用 HTTP 傳輸（GET／POST／PATCH），不連網路。"""

import io
import json
import urllib.error
from email.message import Message
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.integrations import remote_admin_client as client

REMOTE = "https://admin.example.com"
SECRET = "synthetic-secret-never-returned"


class FakeResponse:
    """opener.open 回應替身。"""

    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: Any) -> None:
        return None

    def read(self, _: int) -> bytes:
        return self.body


def http_error(status: int, body: bytes) -> urllib.error.HTTPError:
    """帶回應內容的合成 HTTPError（3xx 未跟隨時也以此回報）。"""
    return urllib.error.HTTPError(REMOTE, status, "x", Message(), io.BytesIO(body))


def opener(monkeypatch: pytest.MonkeyPatch, outcome: Any) -> MagicMock:
    """以替身取代模組的 opener。"""
    fake = MagicMock()
    if isinstance(outcome, FakeResponse):
        fake.open.return_value = outcome
    else:
        fake.open.side_effect = outcome
    monkeypatch.setattr(client, "_OPENER", fake)
    return fake


def test_get_encodes_query_and_sends_only_accept_header(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = opener(monkeypatch, FakeResponse(200, b'{"items":[]}'))
    assert client.get_json(REMOTE + "/", "/api/x", query={"q": "a@b.c"}) == (200, {"items": []})
    request = fake.open.call_args.args[0]
    assert request.full_url == REMOTE + "/api/x?q=a%40b.c"
    assert request.get_method() == "GET"
    assert request.data is None
    assert {name.lower() for name in request.headers} == {"accept"}
    assert fake.open.call_args.kwargs["timeout"] == client.TIMEOUT_SECONDS


@pytest.mark.parametrize("method", ["POST", "PATCH"])
def test_write_methods_send_json_without_credentials(
    monkeypatch: pytest.MonkeyPatch, method: client.Method
) -> None:
    fake = opener(monkeypatch, FakeResponse(201, b'{"account":{}}'))
    payload: dict[str, object] = {"email": "a@b.c", "password": SECRET}
    assert client.request_json(method, REMOTE, "/api/x", payload=payload) == (
        201,
        {"account": {}},
    )
    request = fake.open.call_args.args[0]
    assert request.get_method() == method
    assert json.loads(request.data) == payload
    assert request.get_header("Content-type") == "application/json"
    assert request.get_header("Cookie") is None


def test_error_status_with_json_body_is_returned_to_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    opener(monkeypatch, http_error(409, b'{"error":{"code":"email_taken"}}'))
    assert client.request_json("POST", REMOTE, "/api/x", payload={}) == (
        409,
        {"error": {"code": "email_taken"}},
    )


@pytest.mark.parametrize(
    "outcome,code",
    [
        (http_error(303, b""), "remote_unauthorized"),
        (http_error(401, b'{"detail":"x"}'), "remote_unauthorized"),
        (http_error(403, b"<html>forbidden</html>"), "remote_forbidden"),
        (http_error(404, b"<html>not found</html>"), "remote_endpoint_missing"),
        (http_error(500, b"<html>" + SECRET.encode() + b"</html>"), "remote_server_error"),
        (urllib.error.URLError(OSError("refused")), "remote_unreachable"),
        (urllib.error.URLError(TimeoutError()), "remote_timeout"),
        (TimeoutError(), "remote_timeout"),
        (FakeResponse(200, b"<html>login</html>"), "remote_invalid_response"),
        (FakeResponse(200, b"[1, 2]"), "remote_invalid_response"),
    ],
)
def test_failures_become_fixed_codes_without_echo(
    monkeypatch: pytest.MonkeyPatch, outcome: Any, code: str
) -> None:
    opener(monkeypatch, outcome)
    with pytest.raises(client.RemoteAdminError) as caught:
        client.request_json("GET", REMOTE, "/api/x")
    assert caught.value.code == code
    assert SECRET not in str(caught.value)


def test_redirects_are_not_followed() -> None:
    handler = client._NoRedirect()
    assert handler.redirect_request(None, None, 303, "See Other", {}, REMOTE + "/login") is None


def test_missing_base_url_never_opens_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = opener(monkeypatch, FakeResponse(200, b"{}"))
    with pytest.raises(client.RemoteAdminError) as caught:
        client.get_json("", "/api/x")
    assert caught.value.code == "remote_not_configured"
    fake.open.assert_not_called()


@pytest.mark.parametrize("path", ["api", "/x?sql=1", "/x#y", "/../admin", "//evil.example"])
def test_only_fixed_site_paths_are_accepted(monkeypatch: pytest.MonkeyPatch, path: str) -> None:
    fake = opener(monkeypatch, FakeResponse(200, b"{}"))
    with pytest.raises(ValueError):
        client.get_json(REMOTE, path)
    fake.open.assert_not_called()
