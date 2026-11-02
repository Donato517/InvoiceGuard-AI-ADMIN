"""測試目標：W3——ADMIN 呼叫既有發票 API 的用戶端；以 httpx.MockTransport 假傳輸，不連網路。

這是 Mock 測試：只證明請求形狀、錯誤處理與解析符合 API 契約（契約本身見 test_intake_api_contract.py），
不代表已與真實 API 串接。
"""

import hashlib
import json
from typing import Any

import httpx
import pytest

from app.integrations.api_intake_client import (
    MAX_RESPONSE_BYTES,
    ApiIntakeClient,
    UnconfiguredCredentials,
)
from app.schemas.intake import (
    IntakeApiError,
    IntakeNotConfiguredError,
    IntakeTransportError,
    OutboundRequest,
)

RID = "00000000-0000-4000-8000-0000000000a1"
INV = "00000000-0000-4000-8000-0000000000b2"
RECOGNITION = {
    "recognition_id": RID,
    "result_status": "success",
    "is_triplicate": True,
    "invoice": {"invoice_track": "AB", "invoice_number": "12345678", "items": []},
    "printed": {},
    "warnings": [],
    "recognition": {},
    "expires_at": "2026-11-02T12:00:00Z",
}


class Recorder:
    """記錄請求並依序回應；同時當作 CredentialProvider。"""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.credential_requests: list[OutboundRequest] = []

    def headers(self, request: OutboundRequest) -> dict[str, str]:
        self.credential_requests.append(request)
        return {"Authorization": "Bearer synthetic-test-credential"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        outcome = self.responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def client(self) -> ApiIntakeClient:
        return ApiIntakeClient(
            "https://api.example.test", self, transport=httpx.MockTransport(self.handler)
        )


def _json(status: int, body: Any) -> httpx.Response:
    return httpx.Response(status, json=body)


def _error(status: int, code: str, message: str, details: Any = None) -> httpx.Response:
    return _json(status, {"error": {"code": code, "message": message, "details": details}})


def test_every_call_goes_to_the_documented_endpoint_with_credentials() -> None:
    rec = Recorder(
        _json(200, RECOGNITION),
        _json(200, {"required_complete": True, "missing": []}),
        _json(200, {"warnings": [], "checked_on": "2026-11-02"}),
        _json(200, {"is_duplicate": False}),
        _json(
            201,
            {
                "invoice_id": INV,
                "status": "pending_accounting_review",
                "tax_period": "202610",
                "submitted_at": "2026-11-02T01:00:00Z",
                "warnings": [],
                "modified_fields": [],
            },
        ),
    )
    client = rec.client()

    client.recognize(b"\xff\xd8", "a.jpg", "image/jpeg")
    client.check_required({"a": 1})
    client.check_warnings({"a": 1})
    client.check_duplicate({"a": 1})
    client.submit({"a": 1})

    assert [(r.method, r.url.path) for r in rec.requests] == [
        ("POST", "/api/v1/invoices/recognize"),
        ("POST", "/api/v1/invoices/check-required"),
        ("POST", "/api/v1/invoices/check-warnings"),
        ("POST", "/api/v1/invoices/check-duplicate"),
        ("POST", "/api/v1/invoices"),
    ]
    assert all(
        r.headers["authorization"] == "Bearer synthetic-test-credential" for r in rec.requests
    )
    assert all(r.headers["accept"] == "application/json" for r in rec.requests)
    assert [c.operation for c in rec.credential_requests] == [
        "recognize",
        "check",
        "check",
        "check",
        "submit",
    ]


def test_credential_request_binds_method_path_and_body_hash() -> None:
    rec = Recorder(_json(200, {"is_duplicate": True}))
    body = {"invoice_track": "AB", "invoice_number": "12345678", "seller_tax_id": "04595257"}

    rec.client().check_duplicate(body)

    sent = rec.requests[0].content
    assert json.loads(sent) == body
    bound = rec.credential_requests[0]
    assert (bound.method, bound.path) == ("POST", "/api/v1/invoices/check-duplicate")
    assert bound.body_sha256 == hashlib.sha256(sent).hexdigest()


def test_recognize_binds_file_hash_and_sends_multipart() -> None:
    rec = Recorder(_json(200, RECOGNITION))
    content = b"\xff\xd8\xff synthetic image"

    result = rec.client().recognize(content, "a.jpg", "image/jpeg")

    assert str(result.recognition_id) == RID
    assert rec.credential_requests[0].body_sha256 == hashlib.sha256(content).hexdigest()
    assert rec.requests[0].headers["content-type"].startswith("multipart/form-data")
    assert content in rec.requests[0].content


def test_unconfigured_credentials_refuse_before_any_network_call() -> None:
    called: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(request)
        return _json(200, {})

    client = ApiIntakeClient("https://api.example.test", transport=httpx.MockTransport(handler))

    with pytest.raises(IntakeNotConfiguredError):
        client.check_duplicate({})
    assert called == []
    with pytest.raises(IntakeNotConfiguredError):
        UnconfiguredCredentials().headers(OutboundRequest("check", "POST", "/x", ""))


@pytest.mark.parametrize(
    "url",
    ["http://api.example.test", "ftp://x", "https://u:p@x.test", "https://x.test?a=1", "https://"],
)
def test_base_url_must_be_https_or_loopback_http(url: str) -> None:
    with pytest.raises(ValueError):
        ApiIntakeClient(url)
    ApiIntakeClient("http://127.0.0.1:8000")  # 本機開發允許


def test_422_details_are_parsed_without_input_echo() -> None:
    rec = Recorder(
        _error(
            422,
            "VALIDATION_ERROR",
            "請求參數格式錯誤",
            [
                {
                    "loc": ["body", "items", 0, "line_amount"],
                    "type": "decimal_parsing",
                    "msg": "欄位格式錯誤",
                }
            ],
        )
    )

    with pytest.raises(IntakeApiError) as caught:
        rec.client().submit({})

    error = caught.value
    assert (error.status, error.code) == (422, "VALIDATION_ERROR")
    assert error.details == ((("body", "items", 0, "line_amount"), "decimal_parsing"),)


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "UNAUTHORIZED"),
        (403, "FORBIDDEN"),
        (409, "INVOICE_DUPLICATED"),
        (503, "SERVICE_UNAVAILABLE"),
    ],
)
def test_business_errors_keep_status_and_code(status: int, code: str) -> None:
    rec = Recorder(_error(status, code, "訊息"))

    with pytest.raises(IntakeApiError) as caught:
        rec.client().submit({})

    assert (caught.value.status, caught.value.code) == (status, code)


def test_non_standard_error_body_gets_a_fixed_code() -> None:
    rec = Recorder(httpx.Response(502, text="<html>gateway</html>"))

    with pytest.raises(IntakeApiError) as caught:
        rec.client().check_required({})

    assert caught.value.code == "UPSTREAM_ERROR" and "html" not in caught.value.public_message


def test_long_error_message_is_capped() -> None:
    rec = Recorder(_error(400, "X", "字" * 1000))

    with pytest.raises(IntakeApiError) as caught:
        rec.client().submit({})

    assert len(caught.value.public_message) == 200


@pytest.mark.parametrize("operation", ["submit", "recognize"])
def test_timeout_after_send_is_ambiguous_for_side_effect_calls(operation: str) -> None:
    rec = Recorder(httpx.ReadTimeout("slow"))
    client = rec.client()

    with pytest.raises(IntakeTransportError) as caught:
        if operation == "submit":
            client.submit({})
        else:
            client.recognize(b"x", "a.jpg", "image/jpeg")

    assert caught.value.ambiguous is True


def test_connect_failure_is_not_ambiguous_and_checks_never_are() -> None:
    with pytest.raises(IntakeTransportError) as connect:
        Recorder(httpx.ConnectError("down")).client().submit({})
    with pytest.raises(IntakeTransportError) as check:
        Recorder(httpx.ReadTimeout("slow")).client().check_required({})

    assert connect.value.ambiguous is False
    assert check.value.ambiguous is False  # 檢核沒有副作用


@pytest.mark.parametrize("body", [{"unexpected": 1}, [], "text"])
def test_success_status_with_wrong_shape_is_not_treated_as_success(body: Any) -> None:
    rec = Recorder(_json(201, body))

    with pytest.raises(IntakeTransportError) as caught:
        rec.client().submit({})

    assert caught.value.ambiguous is True  # API 可能已處理


def test_wrong_success_status_is_an_error() -> None:
    rec = Recorder(_json(200, {"invoice_id": INV}))  # submit 預期 201

    with pytest.raises(IntakeApiError):
        rec.client().submit({})


def test_oversized_response_rejected() -> None:
    rec = Recorder(httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)))

    with pytest.raises(IntakeTransportError):
        rec.client().check_duplicate({})


def test_redirects_are_not_followed() -> None:
    rec = Recorder(httpx.Response(302, headers={"location": "https://evil.example/"}))

    with pytest.raises(IntakeApiError):
        rec.client().check_duplicate({})
    assert len(rec.requests) == 1


def test_credentials_never_appear_in_error_text() -> None:
    rec = Recorder(_error(401, "UNAUTHORIZED", "未授權"))

    with pytest.raises(IntakeApiError) as caught:
        rec.client().submit({"secret_field": "value"})

    assert "synthetic-test-credential" not in str(caught.value) + repr(caught.value.__dict__)
