"""模組名稱：app.services.showcase_analysis_service；功能說明：測試用選樣、OCR 候選及獨立驗收計算。"""

import json
import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.integrations.document_ai_evidence import (
    SourceRef,
    full_page_evidence,
    prepare_original_image,
)
from app.integrations.document_ai_fewshot import FewShotClient, FieldRequest, VerifiedExample
from app.services import showcase_intake_service as intake

FIELDS = {
    "invoice_number": "發票字軌號碼",
    "invoice_date": "發票日期",
    "buyer_tax_id": "買方統編",
    "seller_tax_id": "賣方統編",
    "sales_amount": "銷售額",
    "tax_amount": "稅額",
    "total_amount": "總計",
}


def confirmed_seller(revision: dict[str, Any], tax_id: str) -> bool:
    """確認原圖賣方統編已由人工核對，且與供應商主檔相符。"""
    return any(
        item["path"] == "seller_tax_id" and item["state"] == "readable" and item["text"] == tax_id
        for item in revision.get("truths", [])
    )


def select_examples(
    rows: list[dict[str, Any]], tax_id: str, invoice_date: date, layout: str, copy_type: str
) -> list[dict[str, Any]]:
    """只選已確認、同統編且適用的版本，最多三張；不猜聯別或適用期間。"""
    selected = []
    seen = set()
    for row in rows:
        revision = row["revision"]
        if (
            revision.get("status") != "active"
            or revision.get("tax_id") != tax_id
            or (revision.get("master_supplier_id") and not confirmed_seller(revision, tax_id))
            or not revision.get("confirmed_by")
            or not revision.get("confirmed_at")
            or not any(
                item["state"] == "readable"
                and item["path"] in FIELDS
                and item["path"] in revision.get("key_fields", [])
                for item in revision.get("truths", [])
            )
            or (layout and revision.get("layout") != layout)
            or (copy_type != "unknown" and revision.get("copy_type") != copy_type)
        ):
            continue
        start, end = revision.get("valid_from"), revision.get("valid_to")
        if (start and invoice_date < date.fromisoformat(start)) or (
            end and invoice_date > date.fromisoformat(end)
        ):
            continue
        # 未設定日期可供測試參考，但不宣稱版型期間已確認。
        digest = revision["source"]["sha256"]
        if digest in seen:
            continue
        seen.add(digest)
        selected.append(row)
        if len(selected) == 3:
            break
    return selected


def rules(values: dict[str, str], duplicate_image: bool) -> list[dict[str, str]]:
    """各項結果分開呈現；格式與算術不代表合法性或真偽。"""
    result = [
        {
            "rule": "required_fields",
            "status": "pass" if all(values.get(k) for k in FIELDS) else "review",
            "message": "本輪關鍵欄位完整"
            if all(values.get(k) for k in FIELDS)
            else "關鍵欄位缺失或不可讀；其他法定欄位仍需人工核對",
        }
    ]
    try:
        amounts = [
            Decimal(values[k].replace(",", ""))
            for k in ("sales_amount", "tax_amount", "total_amount")
        ]
        valid = all(v.is_finite() and v >= 0 for v in amounts)
        status = "pass" if valid and amounts[0] + amounts[1] == amounts[2] else "review"
    except (KeyError, InvalidOperation):
        status = "not_checked"
    result.append(
        {
            "rule": "amount_sum",
            "status": status,
            "message": "銷售額＋稅額＝總計；不推算或改寫票面金額",
        }
    )
    result.extend(
        [
            {
                "rule": "track_format",
                "status": "pass"
                if re.fullmatch(r"[A-Z]{2}[ -]?[0-9]{8}", values.get("invoice_number", ""))
                else "review",
                "message": "僅檢查兩碼大寫英文字母與八碼數字，不代表字軌年期有效",
            },
            {"rule": "tax_rate", "status": "not_checked", "message": "待確認課稅別與核定計算規則"},
            {
                "rule": "track_period",
                "status": "not_checked",
                "message": "沿用 main 暫停字軌年期對照檢核；需人工確認",
            },
            {
                "rule": "duplicate_image",
                "status": "review" if duplicate_image else "pass",
                "message": "僅檢查本部門既有測試的相同影像雜湊",
            },
            {
                "rule": "duplicate_invoice",
                "status": "not_checked",
                "message": "尚未接正式發票唯一性查核；不同照片仍可能是同一張發票",
            },
        ]
    )
    return result


def metrics(values: dict[str, str], gold: dict[str, str]) -> dict[str, Any]:
    """以同一測試票的人工答案計算逐字正確率，不拿範例答案當標準。"""
    if not gold:
        return {"status": "not_evaluated", "reason": "尚無本次測試票的人工答案"}
    total = len(gold)
    correct = sum(values.get(k) == v for k, v in gold.items())
    missing = sum(not values.get(k) for k in gold)
    return {
        "status": "evaluated",
        "fields": total,
        "correct": correct,
        "missing": missing,
        "accuracy": correct / total,
        "missing_rate": missing / total,
        "fields_needing_correction": total - correct,
        "actual_manual_edits": None,
    }


def recognize(
    content: bytes,
    mime: str,
    run_id: str,
    examples: list[VerifiedExample],
    tax_id: str,
    *,
    template_key: str | None = None,
) -> dict[str, Any]:
    """只回傳測試結果與來源；不寫入或核准正式發票。"""
    source = SourceRef.from_bytes(
        file_id=run_id, version="1", recognition_id=run_id, content=content
    )
    target = full_page_evidence(prepare_original_image(content, source=source), evidence_id=run_id)
    baseline_result = intake.extract_candidates(content, mime)
    baseline = baseline_result["values"]
    with FewShotClient.from_settings() as fewshot:
        enhanced = fewshot.review(
            targets=[target],
            fields=[
                FieldRequest(k, f"只讀取本次測試票的{label}，不可複製範例值")
                for k, label in FIELDS.items()
            ],
            examples=examples,
            scope_key="showcase-test",
            seller_tax_id=tax_id,
            template_key=template_key,
            supplier_verified=True,
            authorization_check=lambda example: example in examples,
            ocr_context=json.dumps(baseline, ensure_ascii=False)[:24000],
            timeout_seconds=15,
        )
    return {
        "baseline": baseline,
        "baseline_provider": baseline_result["provider"],
        "baseline_model": baseline_result["model"],
        "baseline_available": bool(baseline),
        "enhanced": enhanced.to_dict(),
        "candidate_values": enhanced.candidate_values(),
        "created_at": datetime.now(UTC).isoformat(),
    }


def verified_example(sample: str, revision: dict[str, Any], content: bytes) -> VerifiedExample:
    """將人工核對的關鍵欄位綁定固定原圖版本，整頁影像作為證據。"""
    source = SourceRef.from_bytes(
        file_id=revision.get("source_file_id") or sample,
        version=str(revision["source_file_version"])
        if revision.get("source_file_id")
        else str(revision.get("v2_example_version", revision["id"])),
        recognition_id=revision.get("source_recognition_id")
        or revision.get("source_file_id")
        or sample,
        document_group_id=revision.get("document_group_id"),
        content=content,
    )
    image = full_page_evidence(
        prepare_original_image(content, source=source), evidence_id=revision["id"]
    )
    answers = {
        t["path"]: t["text"]
        for t in revision["truths"]
        if t["path"] in FIELDS and t["path"] in revision["key_fields"] and t["state"] == "readable"
    }
    return VerifiedExample(
        example_id=sample,
        example_version=str(revision.get("v2_example_version", revision["id"])),
        scope_key="showcase-test",
        seller_tax_id=revision["tax_id"],
        images=(image,),
        answers=answers,
        answer_evidence={key: (image.artifact_id,) for key in answers},
        verified_by=revision["confirmed_by"],
        verified_at=datetime.fromisoformat(revision["confirmed_at"]),
        template_key=revision["layout"] if revision.get("v2_example_version") else None,
        expires_at=datetime.fromisoformat(revision["expires_at"])
        if revision.get("expires_at")
        else None,
    )
