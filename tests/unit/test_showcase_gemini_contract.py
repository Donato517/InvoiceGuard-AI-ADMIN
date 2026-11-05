"""正式 Gemini 擷取契約、資源釋放與待核對候選回歸。"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.integrations.gemini import GeminiError
from app.integrations.invoice_recognition_contract import SYSTEM_PROMPT, USER_PROMPT
from app.services import showcase_intake_service as intake
from app.services.showcase_service import _recognition_failure


def test_same_prompt_and_unconfirmed_numeric_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    """使用正式提示詞，保留數值精度與零值，不接收模型授權資訊。"""
    client = MagicMock()
    client.extract_invoice.return_value = SimpleNamespace(
        text='{"total_amount":0,"sales_amount":1.2300,"tax_amount":true,"confirmed_by":"model","items":[{"item_name":"品項","line_amount":10,"unit_price":null}]}',
        finish_reason="STOP",
        model="synthetic-model",
    )
    monkeypatch.setattr(intake.GeminiClient, "from_settings", lambda: client)
    result = intake.extract_candidates(b"synthetic", "image/png")
    assert result["provider"] == "gemini"
    assert result["values"] == {
        "total_amount": "0",
        "sales_amount": "1.2300",
        "items.1.name": "品項",
        "items.1.amount": "10",
    }
    assert "confirmed_by" not in result
    client.extract_invoice.assert_called_once_with(
        b"synthetic",
        "image/png",
        system_instruction=SYSTEM_PROMPT,
        prompt=USER_PROMPT,
        timeout_seconds=25,
    )
    client.close.assert_called_once()


@pytest.mark.parametrize(
    "text,finish",
    [
        ("{}", "STOP"),
        ('{"error":"no_text"}', "STOP"),
        ('{"total_amount":"10"}', "MAX_TOKENS"),
        ("not-json", "STOP"),
    ],
)
def test_invalid_results_are_not_candidates(
    monkeypatch: pytest.MonkeyPatch, text: str, finish: str
) -> None:
    """空白、錯誤與截斷回覆皆拒絕，且關閉模型連線。"""
    client = MagicMock()
    client.extract_invoice.return_value = SimpleNamespace(
        text=text, finish_reason=finish, model="synthetic"
    )
    monkeypatch.setattr(intake.GeminiClient, "from_settings", lambda: client)
    with pytest.raises(ValueError):
        intake.extract_candidates(b"synthetic", "image/png")
    client.close.assert_called_once()


def test_gemini_failure_closes_client_and_keeps_safe_code(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型故障只存固定代碼，原始例外文字不進入草稿。"""
    client = MagicMock()
    client.extract_invoice.side_effect = GeminiError("gemini_timeout", "synthetic-secret")
    monkeypatch.setattr(intake.GeminiClient, "from_settings", lambda: client)
    with pytest.raises(GeminiError) as caught:
        intake.extract_candidates(b"synthetic", "image/png")
    client.close.assert_called_once()
    assert _recognition_failure(caught.value)["failure_code"] == "gemini_timeout"
    assert "synthetic-secret" not in str(_recognition_failure(caught.value))


def test_prompt_contract_matches_api() -> None:
    """兩站提示詞由共同檔案維護，避免正式辨識與測試基線分岔。"""
    root = Path(__file__).resolve().parents[2]
    sibling = root.parent / "InvoiceGuard-AI-API"
    if not (sibling / "app").is_dir():
        pytest.skip("單站 checkout 沒有 API")
    relative = Path("app/integrations/invoice_recognition_contract.py")
    assert (root / relative).read_bytes() == (sibling / relative).read_bytes()
