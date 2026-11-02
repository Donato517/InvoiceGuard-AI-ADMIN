"""由 tests/unit/test_intake_api_contract.py 以 API 專案的 Python 執行（cwd＝API 根目錄）。

用 API 真實的 Pydantic 模型與純函式處理 ADMIN 產生的請求內容，並輸出 API 模型序列化後的回應 JSON；
不連 DB、Storage 或網路，也不載入任何設定。stdin：{"submit": {...}, "check": {...}}；stdout：JSON。
"""

import json
import sys
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError

from app.schemas.invoice_upload import (
    DuplicateCheckRead,
    DuplicateCheckRequest,
    FieldWarning,
    InvoiceRecognitionRead,
    InvoiceSubmitRead,
    InvoiceSubmitRequest,
    PrintedText,
    RecognitionInfo,
    RecognizedInvoice,
    RecognizedItem,
)
from app.services import invoice_required_service, invoice_warning_service

payload = json.load(sys.stdin)

try:
    InvoiceSubmitRequest.model_validate(payload["submit"])
    submit_errors: list[list[object]] = []
except ValidationError as error:
    submit_errors = [[list(item["loc"]), item["type"]] for item in error.errors()]

try:
    DuplicateCheckRequest.model_validate(payload["duplicate"])
    duplicate_ok = True
except ValidationError:
    duplicate_ok = False

recognition = InvoiceRecognitionRead(
    recognition_id=uuid4(),
    result_status="partial",
    is_triplicate=True,
    invoice=RecognizedInvoice(
        invoice_track="AB",
        invoice_number="12345678",
        invoice_date=None,
        tax_period=None,
        seller_tax_id="04595257",
        seller_name="ABC公司",
        buyer_tax_id=None,
        buyer_name=None,
        tax_type="taxable",
        sales_amount="3000",
        tax_amount="150",
        total_amount="3150",
        total_amount_zh=None,
        items=(
            RecognizedItem(
                line_no=1, item_name="文具", quantity="2", unit_price="1000", line_amount="2000"
            ),
        ),
    ),
    printed=PrintedText(invoice_date=None, tax_period=None),
    warnings=(
        FieldWarning(
            field="invoice_date", code="invoice_date_invalid", message="無法辨識發票日期，請輸入"
        ),
    ),
    recognition=RecognitionInfo(
        ocr_provider="google_document_ai",
        ocr_confidence=0.9,
        ai_provider="groq",
        model="m",
        fallback_used=False,
    ),
    expires_at=datetime(2026, 11, 2, 12, 0, tzinfo=UTC),
)
submitted = InvoiceSubmitRead(
    invoice_id=uuid4(),
    status="pending_accounting_review",
    tax_period="202610",
    submitted_at=datetime(2026, 11, 2, 1, 0, tzinfo=UTC),
    warnings=(FieldWarning(field="total_amount", code="total_amount_mismatch", message="不符"),),
    modified_fields=(),
)

print(
    json.dumps(
        {
            "submit_errors": submit_errors,
            "duplicate_ok": duplicate_ok,
            "required": invoice_required_service.check_required(payload["check"]).model_dump(
                mode="json"
            ),
            "warnings": invoice_warning_service.preview_warnings(payload["check"]).model_dump(
                mode="json"
            ),
            "recognition": json.loads(recognition.model_dump_json()),
            "submitted": json.loads(submitted.model_dump_json()),
            "duplicate": json.loads(DuplicateCheckRead(is_duplicate=True).model_dump_json()),
        }
    )
)
