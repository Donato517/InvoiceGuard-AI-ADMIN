"""測試目標：ADMIN 的請求內容／回應解析與 API「真實的 Pydantic 模型與純函式」相容（契約測試）。

作法：在同層的 InvoiceGuard-AI-API 以它自己的 .venv 執行 fixtures/api_contract_probe.py——
      用 API 的 InvoiceSubmitRequest 驗證 ADMIN 產生的提交內容、用 API 的 check_required／preview_warnings
      處理檢核內容，並取回 API 模型序列化的回應 JSON 交給 ADMIN 的解析模型。
範圍：只證明雙方欄位與型別相容；不經過 HTTP、認證、DB 或 AI，**不是與真實 API 的整合測試**。
同層沒有 API 或其 .venv 時略過（單站 clone 仍可執行本站其餘測試）。
"""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from app.schemas.intake import (
    DuplicateView,
    IntakeForm,
    IntakeItemForm,
    RecognitionView,
    RequiredCheckView,
    SubmitView,
    WarningCheckView,
)
from app.services import invoice_intake_mapping as mapping

API_ROOT = Path(__file__).resolve().parents[3] / "InvoiceGuard-AI-API"
API_PYTHON = API_ROOT / ".venv/bin/python"
PROBE = Path(__file__).parent / "fixtures/api_contract_probe.py"

GOOD = IntakeForm(
    recognition_id="00000000-0000-4000-8000-0000000000a1",
    invoice_track="AB",
    invoice_number="12345678",
    invoice_date="2026-10-15",
    seller_tax_id="04595257",
    seller_name="ABC公司",
    buyer_tax_id="",
    buyer_name="範例科技",
    tax_type="taxable",
    sales_amount="3000",
    tax_amount="150",
    total_amount="3150",
    total_amount_zh="",
    items=(
        IntakeItemForm(1, "辦公文具", "2", "1000", "2000"),
        IntakeItemForm(None, "影印紙", "", "", "1000"),
    ),
)


def run_probe(form: IntakeForm) -> dict[str, Any]:
    if not API_PYTHON.exists():
        pytest.skip("找不到同層的 InvoiceGuard-AI-API/.venv；略過契約測試")
    payload = {
        "submit": mapping.submit_body(form),
        "check": mapping.check_body(form),
        "duplicate": mapping.duplicate_body(form) or {},
    }
    done = subprocess.run(  # noqa: S603
        [str(API_PYTHON), "-B", str(PROBE)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=API_ROOT,
        env={"PYTHONPATH": str(API_ROOT), "PATH": "/usr/bin:/bin"},
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    return json.loads(done.stdout)


def test_submit_body_is_accepted_by_the_real_api_model() -> None:
    result = run_probe(GOOD)

    assert result["submit_errors"] == []
    assert result["duplicate_ok"] is True


def test_check_body_is_complete_for_the_real_api_check() -> None:
    result = run_probe(GOOD)

    required = RequiredCheckView.model_validate(result["required"])
    warnings = WarningCheckView.model_validate(result["warnings"])
    assert required.required_complete is True and required.missing == ()
    assert warnings.warnings == ()  # 金額一致、統編檢查碼通過


def test_blank_form_reports_missing_fields_in_the_screen_key_format() -> None:
    blank = IntakeForm(
        recognition_id=GOOD.recognition_id, items=(IntakeItemForm(None, "", "", "", "5"),)
    )
    result = run_probe(blank)

    required = RequiredCheckView.model_validate(result["required"])
    keys = mapping.missing_by_field(required.missing)

    assert required.required_complete is False
    assert {"invoice_track", "invoice_number", "invoice_date", "seller_tax_id", "tax_type"} <= set(
        keys
    )
    assert "items.0.item_name" in keys  # 明細欄位 line_no 從 1 開始，轉成 0 起算的畫面 key


def test_422_locations_from_the_real_model_map_to_screen_keys() -> None:
    bad = GOOD.__class__(
        **{
            **GOOD.__dict__,
            "seller_tax_id": "12",
            "items": (IntakeItemForm(1, "x", "", "", "abc"),),
        }
    )
    result = run_probe(bad)

    details = [(tuple(loc), kind) for loc, kind in result["submit_errors"]]
    keys = mapping.field_errors(details)

    assert "seller_tax_id" in keys and "items.0.line_amount" in keys


def test_wrong_checksum_is_only_a_warning_on_both_sides() -> None:
    wrong = GOOD.__class__(**{**GOOD.__dict__, "seller_tax_id": "12345678"})
    result = run_probe(wrong)

    warnings = WarningCheckView.model_validate(result["warnings"])
    assert result["submit_errors"] == []  # 不阻擋提交
    assert [(w.field, w.code) for w in warnings.warnings] == [("seller_tax_id", "tax_id_checksum")]


def test_non_ascii_tax_id_is_rejected_by_the_real_submit_model() -> None:
    # 映射層（form_from_post）會先把全形轉 ASCII；這裡直接把全形放進表單，確認即使繞過映射，API 也會拒絕
    result = run_probe(GOOD.__class__(**{**GOOD.__dict__, "seller_tax_id": "０４５９５２５７"}))

    assert [["seller_tax_id"], "string_pattern_mismatch"] in result["submit_errors"]


def test_response_samples_from_the_real_api_models_parse_in_admin() -> None:
    result = run_probe(GOOD)

    recognition = RecognitionView.model_validate(result["recognition"])
    submitted = SubmitView.model_validate(result["submitted"])
    duplicate = DuplicateView.model_validate(result["duplicate"])
    form = mapping.form_from_recognition(recognition)

    assert (form.invoice_track, form.seller_tax_id, form.tax_type) == ("AB", "04595257", "taxable")
    assert recognition.warnings[0].code == "invoice_date_invalid" and form.invoice_date == ""
    assert (
        submitted.status == "pending_accounting_review"
        and submitted.warnings[0].code == "total_amount_mismatch"
    )
    assert duplicate.is_duplicate is True
