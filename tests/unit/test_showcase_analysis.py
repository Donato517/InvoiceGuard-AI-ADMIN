"""供應商範例集的日期、選樣、候選來源與效果計算離線回歸。"""

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.schemas.showcase import SampleInput, validate_truths
from app.services import showcase_analysis_service as analysis
from app.services.showcase_service import revise, transition


def sample(**changes: object) -> SampleInput:
    """建立純合成的完整範例契約。"""
    return SampleInput.model_validate(
        {
            "title": "離線範例",
            "supplier_name": "合成公司",
            "tax_id": "12345678",
            "layout": "手寫",
            "reason": "測試",
            "key_fields": ["total_amount"],
            "truths": [{"path": "total_amount", "state": "readable", "text": "105"}],
            **changes,
        }
    )


def test_applicability_is_serializable_and_legacy_activation_works() -> None:
    """日期以 ISO 字串保存；舊草稿缺少新增欄位仍可確認。"""
    actor = uuid4()
    payload = revise(None, sample(valid_from="2026-01-01"), {"key": "synthetic"}, actor)
    assert payload["revisions"][0]["valid_from"] == "2026-01-01"
    for key in ("copy_type", "valid_from", "valid_to", "key_fields"):
        payload["revisions"][0].pop(key)
    assert transition(payload, "activate", actor, True)["active_revision"]


@pytest.mark.parametrize(
    "changes",
    [
        {"valid_from": "2026-02-01", "valid_to": "2026-01-01"},
        {"key_fields": ["unknown"]},
        {"key_fields": ["total_amount", "total_amount"]},
        {"truths": [{"path": "total_amount", "state": "unreadable"}]},
    ],
)
def test_bad_applicability_or_unconfirmed_keys_cannot_activate(changes: dict[str, object]) -> None:
    """拒絕倒置期間、無效路徑及未確認的關鍵答案。"""
    with pytest.raises(ValueError):
        validate_truths(sample(**changes), activation=True)


def test_selection_filters_dates_identity_copy_and_duplicate_sources() -> None:
    """不取錯公司、過期版、草稿及重複影像，最多三張。"""
    actor = uuid4()
    rows = []
    for index in range(8):
        payload = transition(
            revise(
                None,
                sample(copy_type="receipt", valid_to="2026-12-31"),
                {"sha256": str(index)},
                actor,
            ),
            "activate",
            actor,
            True,
        )
        rows.append({"sample_id": uuid4(), "revision": payload["revisions"][0]})
    rows[0]["revision"]["tax_id"] = "87654321"
    rows[1]["revision"]["valid_to"] = "2025-12-31"
    rows[2]["revision"]["copy_type"] = "deduction"
    rows[3]["revision"]["status"] = "draft"
    rows[5]["revision"]["source"] = rows[4]["revision"]["source"]
    chosen = analysis.select_examples(rows, "12345678", date(2026, 11, 2), "手寫", "receipt")
    assert chosen == [rows[4], rows[6], rows[7]]


def test_returning_a_revision_preserves_previous_reference_until_deactivated() -> None:
    """退回新版仍可選舊啟用版，停用後兩版皆不提供。"""
    actor, sample_id = uuid4(), uuid4()
    item = sample(copy_type="receipt")
    active = transition(revise(None, item, {"sha256": "a"}, actor), "activate", actor, True)
    updated = revise(active, item, {"sha256": "b"}, actor)
    returned = transition(updated, "return_for_correction", actor, False)
    assert returned["active_revision"] == active["active_revision"]
    assert returned["revisions"][-1]["status"] == "returned"
    assert updated["revisions"][-1]["status"] == "draft"

    def eligible(payload):
        rows = [{"sample_id": sample_id, "revision": revision} for revision in payload["revisions"]]
        return analysis.select_examples(rows, "12345678", date(2026, 11, 2), "手寫", "receipt")

    assert [row["revision"]["id"] for row in eligible(returned)] == [active["active_revision"]]
    inactive = transition(returned, "deactivate", actor, False)
    assert inactive["active_revision"] is None and not eligible(inactive)
    assert [r["source"]["sha256"] for r in inactive["revisions"]] == ["a", "b"]


def test_rules_and_metrics_do_not_claim_unperformed_checks() -> None:
    """Decimal 算術、未檢核與人工答案不足必須明確區分。"""
    values = {"sales_amount": "100", "tax_amount": "5", "total_amount": "105"}
    rules = {r["rule"]: r["status"] for r in analysis.rules(values, True)}
    assert rules["amount_sum"] == "pass"
    assert rules["duplicate_image"] == "review"
    assert rules["track_period"] == rules["duplicate_invoice"] == "not_checked"
    assert analysis.metrics(values, {})["status"] == "not_evaluated"
    metrics = analysis.metrics(
        values, {"sales_amount": "100", "tax_amount": "6", "invoice_number": "AB12345678"}
    )
    assert metrics["correct"] == 1 and metrics["missing"] == 1
    assert metrics["fields_needing_correction"] == 2 and metrics["actual_manual_edits"] is None
    assert {r["rule"]: r["status"] for r in analysis.rules({**values, "tax_amount": "NaN"}, False)}[
        "amount_sum"
    ] == "review"


def test_collection_query_keeps_department_boundary() -> None:
    """供應商總數查詢使用部門參數，不將使用者輸入拼成 SQL。"""
    from app.db.repositories import showcase_repository as repository

    calls = []
    db = SimpleNamespace(
        execute=lambda sql, params: (
            calls.append((str(sql), params)) or SimpleNamespace(mappings=lambda: [])
        )
    )
    department = uuid4()
    assert repository.collections(db, department, "' OR true --", 2) == []
    assert calls[0][1] == {"dept": department, "q": "' OR true --", "offset": 20}
    assert "p.department_id=:dept" in calls[0][0]


def test_ocr_pipeline_uses_real_fewshot_contract_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """以合成影像及模型替身驗證實際證據契約，範例不是本次測試答案。"""
    import io
    import json

    from PIL import Image

    from app.integrations.document_ai_fewshot import CallableBackend, FewShotClient, ModelReply

    def image(size: int) -> bytes:
        """生成不同尺寸影像以避免同圖資料洩漏。"""
        output = io.BytesIO()
        Image.new("RGB", (size, size), "white").save(output, format="PNG")
        return output.getvalue()

    actor = uuid4()
    revision = transition(
        revise(None, sample(), {"key": "synthetic"}, actor), "activate", actor, True
    )["revisions"][0]
    revision["v2_example_version"] = 2
    example = analysis.verified_example(str(uuid4()), revision, image(30))
    assert example.example_version == "2" and example.template_key == revision["layout"]
    calls = []

    def reply(request: object, **kwargs: object) -> ModelReply:
        calls.append(request)
        return ModelReply(
            json.dumps(
                {
                    "fields": [
                        {
                            "field": k,
                            "status": "unreadable",
                            "value": None,
                            "evidence_ids": [],
                            "note": "合成替身",
                        }
                        for k in analysis.FIELDS
                    ]
                }
            )
        )

    monkeypatch.setattr(
        analysis.intake,
        "extract_candidates",
        lambda *args: {
            "values": {"total_amount": "100"},
            "provider": "gemini",
            "model": "offline-baseline",
        },
    )
    monkeypatch.setattr(
        analysis.FewShotClient,
        "from_settings",
        lambda: FewShotClient(CallableBackend(reply), model="gemini-test"),
    )
    result = analysis.recognize(
        image(20), "image/png", str(uuid4()), [example], "12345678", template_key=revision["layout"]
    )
    assert result["baseline"] == {"total_amount": "100"}
    assert result["enhanced"]["status"] == "completed"
    assert result["enhanced"]["used_examples"][0]["version"] == str(revision["v2_example_version"])
    assert result["candidate_values"] == {} and len(calls) == 1
