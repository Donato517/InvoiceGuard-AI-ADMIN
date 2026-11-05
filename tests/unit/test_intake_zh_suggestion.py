"""測試目標：辨識完成時，國字大寫無法判讀或空白、且數字總計有效 → 伺服器端帶入標準國字並附核對提醒。

規則（業務決定 2026-11-05）：
    - 判斷只依 API 的警示（total_amount_zh_unreadable）或 AI 國字空白；ADMIN 不自行解析國字。
    - 可解析但與總計不符（mismatch）不自動覆寫；容錯解析一致但寫法不標準時帶入標準國字並保留原字串於提醒。
    - 總計空白、非法、非整數時不產生建議。
    - 只在辨識當下填一次；之後重新渲染不再填值，人工修改不被覆寫。
替身測試：不連 DB、Storage、AI 或真實 API。
"""

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from starlette.datastructures import FormData

from app.schemas.intake import RecognitionView
from app.services import invoice_intake_mapping as mapping
from app.utils.chinese_amount import amount_to_chinese

RID = "00000000-0000-4000-8000-0000000000a1"
STANDARD = "壹萬肆仟捌佰伍拾捌元整"
TOLERANT = "億仟佰拾壹萬肆仟捌佰伍拾捌元"
UNREADABLE = {
    "field": "total_amount_zh",
    "code": "total_amount_zh_unreadable",
    "message": "無法判讀國字大寫金額，請對照票面確認",
}
MISMATCH = {
    "field": "total_amount_zh",
    "code": "total_amount_zh_mismatch",
    "message": "國字大寫金額（14,859）與總計不符，請確認",
}


def recognition(
    zh: str | None, total: str | None = "14858", warnings: tuple[dict, ...] = ()
) -> RecognitionView:
    return RecognitionView.model_validate(
        {
            "recognition_id": RID,
            "result_status": "partial",
            "is_triplicate": True,
            "invoice": {
                "invoice_track": "AB",
                "invoice_number": "12345678",
                "sales_amount": "14150",
                "tax_amount": "708",
                "total_amount": total,
                "total_amount_zh": zh,
                "items": [],
            },
            "warnings": list(warnings),
            "expires_at": datetime(2026, 11, 5, 12, 0, tzinfo=UTC).isoformat(),
        }
    )


# ---- 共用向量：與手機前台 amountToChinese 逐項相同 ----


def test_converter_matches_the_shared_vectors_with_the_mobile_frontend() -> None:
    path = Path(__file__).resolve().parents[1] / "fixtures" / "amount_to_chinese_vectors.json"
    vectors = json.loads(path.read_text(encoding="utf-8"))["vectors"]
    assert len(vectors) >= 40
    for entry in vectors:
        assert amount_to_chinese(entry["input"]) == entry["output"], entry


@pytest.mark.parametrize("value", [None, "", "abc", "-5", "14858.5", "1000000000000", True, 1.5])
def test_converter_rejects_non_integers(value: object) -> None:
    assert amount_to_chinese(value) is None  # type: ignore[arg-type]


# ---- 觸發條件 ----


def test_unreadable_zh_with_valid_total_is_filled_and_keeps_original_for_the_note() -> None:
    rec = recognition("約壹萬四", warnings=(UNREADABLE,))
    form = mapping.form_from_recognition(rec)
    assert form.total_amount_zh == STANDARD
    suggestion = mapping.zh_suggestion(rec)
    assert (
        suggestion is not None
        and suggestion.value == STANDARD
        and suggestion.original == "約壹萬四"
    )


@pytest.mark.parametrize("blank", [None, "", "   "])
def test_blank_zh_with_valid_total_is_filled(blank: str | None) -> None:
    rec = recognition(blank)
    assert mapping.form_from_recognition(rec).total_amount_zh == STANDARD
    suggestion = mapping.zh_suggestion(rec)
    assert suggestion is not None and suggestion.original == ""


def test_tolerant_nonstandard_string_is_standardized_and_original_kept_in_the_note() -> None:
    """人工驗收案例：API 容錯解析為 14858（無警示），但畫面仍留著預印單位殘留的 OCR 字串。"""
    rec = recognition(TOLERANT)
    form = mapping.form_from_recognition(rec)
    assert form.total_amount_zh == "壹萬肆仟捌佰伍拾捌元整"
    suggestion = mapping.zh_suggestion(rec)
    assert suggestion is not None and suggestion.kind == "nonstandard"
    assert suggestion.original == TOLERANT
    note = mapping.zh_suggestion_note(rec, form)
    assert TOLERANT in note and "壹萬肆仟捌佰伍拾捌元整" in note and "寫法不標準" in note
    # AI 原值保留在辨識結果，不被改寫
    assert rec.invoice.total_amount_zh == TOLERANT


@pytest.mark.parametrize(
    "same", ["壹萬肆仟捌佰伍拾捌元整", "壹萬肆仟捌佰伍拾捌元", " 壹萬肆仟 捌佰伍拾捌元整 "]
)
def test_standard_spelling_up_to_the_final_zheng_or_spaces_is_not_touched(same: str) -> None:
    rec = recognition(same)
    assert mapping.zh_suggestion(rec) is None
    assert mapping.form_from_recognition(rec).total_amount_zh == same
    assert mapping.zh_suggestion_note(rec, mapping.form_from_recognition(rec)) == ""


def test_nonstandard_suggestion_never_overrides_a_manual_edit_on_rerender() -> None:
    rec = recognition(TOLERANT)
    form = replace(mapping.form_from_recognition(rec), total_amount_zh="我自己改的")
    assert mapping.zh_suggestion_note(rec, form) == ""


def test_mismatch_is_only_a_warning_never_overwritten() -> None:
    rec = recognition("壹萬肆仟捌佰伍拾玖元整", warnings=(MISMATCH,))
    assert mapping.form_from_recognition(rec).total_amount_zh == "壹萬肆仟捌佰伍拾玖元整"
    assert mapping.zh_suggestion(rec) is None


@pytest.mark.parametrize("total", [None, "", "abc", "14858.5", "-5"])
def test_no_suggestion_when_total_is_blank_invalid_or_not_an_integer(total: str | None) -> None:
    for zh, warnings in ((None, ()), ("約壹萬", (UNREADABLE,))):
        rec = recognition(zh, total, warnings)
        assert mapping.zh_suggestion(rec) is None, (zh, total)
        assert mapping.form_from_recognition(rec).total_amount_zh == (zh or "")


def test_other_warnings_do_not_trigger_a_suggestion() -> None:
    other = {"field": "total_amount", "code": "total_amount_mismatch", "message": "x"}
    assert mapping.zh_suggestion(recognition("壹萬肆仟捌佰伍拾捌元整", warnings=(other,))) is None


# ---- 核對提醒文字與保護 ----


def test_note_shows_original_and_suggestion_and_asks_to_check() -> None:
    rec = recognition("約壹萬四", warnings=(UNREADABLE,))
    note = mapping.zh_suggestion_note(rec, mapping.form_from_recognition(rec))
    assert "「約壹萬四」" in note and f"「{STANDARD}」" in note and "核對" in note
    blank = recognition("")
    assert "沒有辨識到" in mapping.zh_suggestion_note(blank, mapping.form_from_recognition(blank))


def test_note_disappears_once_the_user_changes_the_zh_field() -> None:
    rec = recognition("約壹萬四", warnings=(UNREADABLE,))
    edited = mapping.form_from_post(
        FormData(
            [
                ("recognition_id", RID),
                ("total_amount", "14858"),
                ("total_amount_zh", "壹萬肆仟捌佰伍拾捌圓整"),
            ]
        )
    )
    assert mapping.zh_suggestion_note(rec, edited) == ""


def test_long_original_is_truncated_in_the_note() -> None:
    rec = recognition("壹" * 200, warnings=(UNREADABLE,))
    note = mapping.zh_suggestion_note(rec, mapping.form_from_recognition(rec))
    assert "壹" * 60 + "…" in note and "壹" * 61 not in note


def test_rerendering_the_form_never_refills_a_manual_value() -> None:
    # 填值只發生在 form_from_recognition（辨識當下）；之後的表單來自使用者送出的內容，不會再經過建議邏輯
    manual = mapping.form_from_post(
        FormData([("recognition_id", RID), ("total_amount", "14858"), ("total_amount_zh", "")])
    )
    assert manual.total_amount_zh == ""
    again = mapping.form_from_post(
        FormData([("recognition_id", RID), ("total_amount", "14858"), ("total_amount_zh", "自填")])
    )
    assert again.total_amount_zh == "自填"


def test_ai_original_object_is_not_modified_by_the_suggestion() -> None:
    rec = recognition("約壹萬四", warnings=(UNREADABLE,))
    mapping.form_from_recognition(rec)
    assert rec.invoice.total_amount_zh == "約壹萬四"  # API 回應物件（AI 原值）維持不變


def test_submit_body_carries_the_suggested_value_and_leaves_changes_to_the_api() -> None:
    rec = recognition("約壹萬四", warnings=(UNREADABLE,))
    body = mapping.submit_body(mapping.form_from_recognition(rec))
    assert body["total_amount_zh"] == STANDARD
    assert body["changes"] == []  # AI 原值與送出值的差異由 API 依 recognition_id 比對並寫入欄位複核


def test_new_converter_agrees_with_the_legacy_router_converter_on_every_shared_integer_vector() -> (
    None
):
    # 技術債防線：routers/invoices.py 的 _invoice_chinese_amount（舊示範流程／財務儲存路徑使用，含角分與兆）
    # 與 app/utils/chinese_amount.py 並存；整數輸出必須相同，日後任何一邊改規則都會在這裡先失敗。
    from app.routers.invoices import _invoice_chinese_amount

    path = Path(__file__).resolve().parents[1] / "fixtures" / "amount_to_chinese_vectors.json"
    compared = 0
    for entry in json.loads(path.read_text(encoding="utf-8"))["vectors"]:
        if entry["output"] is None:
            continue
        number = int(str(entry["input"]).replace(",", "").replace(" ", "").split(".")[0])
        assert _invoice_chinese_amount(number) == entry["output"], entry
        compared += 1
    assert compared >= 30
