"""測試目標：W1——ADMIN 核對表單 ⇄ 發票 API 契約的純函式映射；合成資料，無 I/O。

不涵蓋：格式／金額／統編檢核本身（由 API 負責，ADMIN 不複製規則）；與真實 API 的相容性見
test_intake_api_contract.py（以 API 的 Pydantic 模型驗證）。
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from starlette.datastructures import FormData

from app.schemas.intake import (
    ApiWarning,
    IntakeError,
    IntakeForm,
    IntakeItemForm,
    MissingFieldView,
    RecognitionView,
)
from app.services import invoice_intake_mapping as mapping

RID = "00000000-0000-4000-8000-0000000000a1"


def _recognition(**invoice: object) -> RecognitionView:
    base: dict[str, object] = {
        "invoice_track": "AB",
        "invoice_number": "12345678",
        "invoice_date": "2026-10-15",
        "seller_tax_id": "04595257",
        "seller_name": "ABC公司",
        "buyer_tax_id": None,
        "buyer_name": "範例科技",
        "tax_type": "taxable",
        "sales_amount": "3000",
        "tax_amount": "150",
        "total_amount": "3150",
        "total_amount_zh": None,
        "items": [
            {
                "line_no": 1,
                "item_name": "辦公文具",
                "quantity": "2",
                "unit_price": "1000",
                "line_amount": "2000",
            },
            {
                "line_no": 2,
                "item_name": "影印紙",
                "quantity": None,
                "unit_price": None,
                "line_amount": "1000",
            },
        ],
    }
    return RecognitionView.model_validate(
        {
            "recognition_id": RID,
            "result_status": "partial",
            "is_triplicate": True,
            "invoice": {**base, **invoice},
            "expires_at": datetime(2026, 11, 2, 12, 0, tzinfo=UTC).isoformat(),
            "future_field_from_api": "ignored",
        }
    )


def test_form_from_recognition_maps_every_field_without_changing_values() -> None:
    form = mapping.form_from_recognition(_recognition())

    assert form.recognition_id == RID
    assert (form.invoice_track, form.invoice_number, form.invoice_date) == (
        "AB",
        "12345678",
        "2026-10-15",
    )
    assert (form.seller_tax_id, form.seller_name, form.buyer_tax_id, form.buyer_name) == (
        "04595257",
        "ABC公司",
        "",
        "範例科技",
    )
    assert (form.tax_type, form.sales_amount, form.tax_amount, form.total_amount) == (
        "taxable",
        "3000",
        "150",
        "3150",
    )
    assert form.items[0] == IntakeItemForm(1, "辦公文具", "2", "1000", "2000")
    assert form.items[1] == IntakeItemForm(2, "影印紙", "", "", "1000")  # null → 空字串


def test_unrecognized_fields_become_blank_not_guessed() -> None:
    form = mapping.form_from_recognition(
        _recognition(invoice_date=None, invoice_track=None, tax_type=None, items=[])
    )

    assert (form.invoice_date, form.invoice_track, form.tax_type, form.items) == ("", "", "", ())


def _post(**scalars: str) -> FormData:
    return FormData(list(scalars.items()))


def test_form_from_post_cleans_codes_and_numbers_but_not_names() -> None:
    data = FormData(
        [
            ("recognition_id", f" {RID} "),
            ("invoice_track", " ａｂ "),  # 全形小寫
            ("invoice_number", "１２３４ ５６７８"),
            ("invoice_date", "２０２６-10-15"),
            ("seller_tax_id", "０４５９５２５７"),
            ("seller_name", "  Ａ公司 "),  # 名稱只去頭尾空白，不做全形轉換
            ("sales_amount", "3,000"),
            ("tax_type", "taxable"),
            ("item_source_line_no", "1"),
            ("item_item_name", " 文具 "),
            ("item_quantity", "１，０００"),
            ("item_unit_price", "2"),
            ("item_line_amount", "2,000"),
        ]
    )

    form = mapping.form_from_post(data)

    assert form.recognition_id == RID
    assert form.invoice_track == "ab"  # 大小寫交給 API 規則（不替使用者改成大寫）
    assert (form.invoice_number, form.invoice_date, form.seller_tax_id) == (
        "12345678",
        "2026-10-15",
        "04595257",
    )
    assert form.seller_name == "Ａ公司" and form.sales_amount == "3000"
    assert form.items == (IntakeItemForm(1, "文具", "1000", "2", "2000"),)


def test_blank_rows_are_dropped_and_new_rows_have_no_source() -> None:
    data = FormData(
        [
            ("item_source_line_no", "1"),
            ("item_item_name", ""),
            ("item_quantity", ""),
            ("item_unit_price", ""),
            ("item_line_amount", ""),  # AI 列被清空＝刪除
            ("item_source_line_no", ""),
            ("item_item_name", "新增"),
            ("item_quantity", ""),
            ("item_unit_price", ""),
            ("item_line_amount", "5"),
            ("item_source_line_no", "abc"),
            ("item_item_name", "竄改來源"),
            ("item_quantity", ""),
            ("item_unit_price", ""),
            ("item_line_amount", "7"),
        ]
    )

    items = mapping.form_from_post(data).items

    assert [(i.source_line_no, i.item_name) for i in items] == [(None, "新增"), (None, "竄改來源")]


def test_too_many_items_rejected() -> None:
    data = FormData([("item_item_name", "x")] * (mapping.MAX_ITEMS + 1))

    with pytest.raises(IntakeError):
        mapping.form_from_post(data)


def test_add_and_remove_item() -> None:
    form = IntakeForm(items=(IntakeItemForm(1, "a"), IntakeItemForm(2, "b")))

    added = mapping.with_blank_item(form)
    removed = mapping.without_item(added, 0)

    assert added.items[-1] == IntakeItemForm() and len(added.items) == 3
    assert [i.item_name for i in removed.items] == ["b", ""]
    with pytest.raises(IntakeError):
        mapping.without_item(form, 5)
    full = IntakeForm(items=tuple(IntakeItemForm() for _ in range(mapping.MAX_ITEMS)))
    with pytest.raises(IntakeError):
        mapping.with_blank_item(full)


def test_submit_body_matches_api_shape() -> None:
    form = mapping.form_from_recognition(_recognition())

    body = mapping.submit_body(form)

    assert body["recognition_id"] == RID and body["changes"] == []
    assert body["buyer_tax_id"] is None  # 空白 → null
    # AI 國字空白且總計 3150 有效 → 辨識當下帶入標準國字（見 test_intake_zh_suggestion.py）
    assert body["total_amount_zh"] == "參仟壹佰伍拾元整"
    assert body["items"][0] == {
        "source_line_no": 1,
        "item_name": "辦公文具",
        "quantity": "2",
        "unit_price": "1000",
        "line_amount": "2000",
    }
    assert body["items"][1]["quantity"] is None
    assert set(body) == {"recognition_id", *mapping.HEADER_FIELDS, "items", "changes"}


def test_check_body_has_no_internal_fields_and_no_identity() -> None:
    body = mapping.check_body(mapping.form_from_recognition(_recognition()))

    assert "recognition_id" not in body and "changes" not in body
    assert not {"user_id", "department_id", "submitted_by"} & set(body)


def test_duplicate_body_only_with_all_three_fields() -> None:
    full = IntakeForm(invoice_track="AB", invoice_number="12345678", seller_tax_id="04595257")

    assert mapping.duplicate_body(full) == {
        "invoice_track": "AB",
        "invoice_number": "12345678",
        "seller_tax_id": "04595257",
    }
    assert mapping.duplicate_body(IntakeForm(invoice_track="AB", invoice_number="1")) is None


def test_error_locations_map_to_screen_keys() -> None:
    errors = mapping.field_errors(
        [
            (("body", "seller_tax_id"), "string_pattern_mismatch"),
            (("body", "items", 1, "line_amount"), "decimal_parsing"),
            (("body", "items", 0), "missing"),
            ((), "x"),
        ]
    )

    assert set(errors) == {"seller_tax_id", "items.1.line_amount", "items"}
    assert all("格式" in message or "內容" in message for message in errors.values())


def test_missing_and_warning_grouping() -> None:
    missing = mapping.missing_by_field(
        [
            MissingFieldView(field="invoice_date", code="required_missing", message="缺日期"),
            MissingFieldView(
                field="item_name", code="required_missing", message="缺品名", line_no=2
            ),
        ]
    )
    warnings = mapping.warnings_by_field(
        [
            ApiWarning(field="seller_tax_id", code="tax_id_checksum", message="檢查碼不符"),
            ApiWarning(field="seller_tax_id", code="x", message="另一則"),
        ]
    )

    assert missing == {"invoice_date": "缺日期", "items.1.item_name": "缺品名"}
    assert warnings == {"seller_tax_id": ["檢查碼不符", "另一則"]}


def test_field_rows_cover_the_14_items_and_only_real_api_fields() -> None:
    assert [row.index for row in mapping.FIELD_ROWS] == list(range(1, 15))
    api_fields = {f for row in mapping.FIELD_ROWS for f in row.fields}
    assert api_fields <= {*mapping.HEADER_FIELDS, *mapping.ITEM_FIELDS}
    unsupported = [row.index for row in mapping.FIELD_ROWS if not row.editable]
    assert unsupported == [1, 13]  # 聯式識別（僅參考）與備註（API 沒有此欄位）
    assert UUID(RID)
