"""供應商範例：圖片完整性、不可變版本、人工確認及授權故障回歸。"""

import io
from uuid import uuid4

import pytest
from PIL import Image

from app.schemas.showcase import SampleInput, validate_truths
from app.services.showcase_service import image_info, revise, transition


def data(**changes: object) -> SampleInput:
    """產生合成範例，非正式供應商資料。"""
    return SampleInput.model_validate(
        {
            "title": "測試範例",
            "supplier_name": "合成測試公司",
            "tax_id": "12345678",
            "layout": "手寫A",
            "reason": "測試建立",
            "truths": [{"path": "total_amount", "state": "readable", "text": "0"}],
            **changes,
        }
    )


def png() -> bytes:
    """小型合成 PNG，測試不得取用公司發票。"""
    out = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(out, format="PNG")
    return out.getvalue()


def test_new_sample_requires_master_before_any_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """直接提交舊表單也不能建立第二份供應商或呼叫模型。"""
    from app.services import showcase_service as service

    monkeypatch.setattr(service, "open_session", lambda: pytest.fail("不應開啟交易"))
    monkeypatch.setattr(service, "upload", lambda *args: pytest.fail("不應上傳"))
    monkeypatch.setattr(service.intake, "extract_candidates", lambda *args: pytest.fail("不應辨識"))
    with pytest.raises(ValueError, match="正式供應商"):
        service.save(object(), data(), png(), uuid4(), 0, run_ocr=True)


def test_legacy_revision_preserves_supplier_without_upsert(monkeypatch: pytest.MonkeyPatch) -> None:
    """舊範例可繼續修訂，同時保留原供應商 UUID 與歷史。"""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.services import showcase_service as service

    actor = SimpleNamespace(user_id=uuid4(), selected_department_id=uuid4())
    supplier, sample = uuid4(), uuid4()
    payload = revise(None, data(), {"key": "synthetic"}, actor.user_id)
    payload["supplier_id"] = str(supplier)
    row = {"version": 1, "payload": payload, "department_id": actor.selected_department_id}
    saved = []
    monkeypatch.setattr(service, "detail", lambda *args: row)
    monkeypatch.setattr(service, "_managed_row", lambda *args, **kwargs: row)
    monkeypatch.setattr(
        service, "open_session", lambda: nullcontext(SimpleNamespace(commit=lambda: None))
    )
    monkeypatch.setattr(service.repository, "supplier", lambda *args: pytest.fail("不可另建舊主檔"))
    monkeypatch.setattr(
        service.repository, "save", lambda db, dept, key, value, expected: saved.append(value)
    )
    monkeypatch.setattr(service.repository, "event", lambda *args: None)
    service.save(actor, data(title="修訂"), None, sample, 1)
    assert saved[0]["supplier_id"] == str(supplier)
    assert len(saved[0]["revisions"]) == 2 and len(payload["revisions"]) == 1
    with pytest.raises(ValueError, match="供應商身分"):
        service.save(actor, data(supplier_name="改掛另一家"), png(), sample, 1)


def test_shared_status_changes_use_sample_owner_and_require_review_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """跨部門可讀主檔範例，只有審查者可改狀態，事件歸範例且保留操作者。"""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.schemas.e_admin import Action, ConflictError, DeniedError
    from app.services import showcase_service as service

    finance, it, sample_id, master = uuid4(), uuid4(), uuid4(), uuid4()
    actor = SimpleNamespace(selected_department_id=it, user_id=uuid4())
    payload = revise(None, data(master_supplier_id=master), {"key": "synthetic"}, actor.user_id)
    payload["master_supplier_id"] = str(master)
    payload["revisions"][-1]["v2_example_version"] = 1
    row = {"department_id": finance, "payload": payload, "version": 1}
    allowed = set()
    events = []
    statuses = []

    def authorize(db, identity, action, *, lock=False, supplier_master=False, activation=False):
        if (action, activation, supplier_master) not in allowed:
            raise DeniedError

    def read(db, department, sample, *, lock, shared_master):
        assert (department, sample, shared_master) == (it, sample_id, True)
        return row.copy()

    def save(db, department, sample, saved, version):
        assert (department, sample, version) == (finance, sample_id, row["version"])
        row.update(payload=saved, version=version + 1)

    monkeypatch.setattr(
        service, "open_session", lambda: nullcontext(SimpleNamespace(commit=lambda: None))
    )
    monkeypatch.setattr(service, "authorize", authorize)
    monkeypatch.setattr(service.repository, "read", read)
    monkeypatch.setattr(service.repository, "save", save)
    monkeypatch.setattr(service.repository, "event", lambda *args: events.append(args[1:]))
    monkeypatch.setattr(
        service.repository, "set_example_status", lambda *args: statuses.append(args[1:])
    )
    monkeypatch.setattr(
        service, "_read_master", lambda *args: {"tax_id": "12345678", "is_active": True}
    )
    monkeypatch.setattr(service.repository, "history", lambda *args: [args[1:]])
    monkeypatch.setattr(service.repository, "tests", lambda *args: [args[1:]])
    allowed.update({(Action.VIEW, False, False), (Action.VIEW, False, True)})
    detail = service.detail(actor, sample_id)
    assert detail["history"] == [(finance, sample_id, it)]
    assert detail["tests"] == [(it, sample_id)] and not detail["can_change_status"]
    with pytest.raises(DeniedError):
        service.change_status(actor, sample_id, 1, "activate", "核對完成", True)
    monkeypatch.setattr(
        service.v2, "get_example", lambda *args, **kwargs: row["payload"]["revisions"][-1]
    )
    allowed.add((Action.EDIT, True, False))
    assert service.detail(actor, sample_id)["can_change_status"]
    with pytest.raises(ValueError, match="賣方統編"):
        service.change_status(actor, sample_id, 1, "activate", "核對完成", True)
    row["payload"]["revisions"][-1]["truths"].append(
        {"path": "seller_tax_id", "state": "readable", "text": "12345678"}
    )
    service.change_status(actor, sample_id, 1, "activate", "核對完成", True)
    assert row["payload"]["revisions"][-1]["key_fields"] == ["total_amount", "seller_tax_id"]
    assert statuses[-1] == (
        sample_id,
        1,
        "verified",
        actor.user_id,
        "核對完成",
        ["total_amount", "seller_tax_id"],
    )
    assert events[-1][:6] == (finance, sample_id, actor.user_id, "activate", "核對完成", 2)
    assert events[-1][6]["actor_department_id"] == str(it)
    with pytest.raises(ConflictError):
        service.change_status(actor, sample_id, 1, "deactivate", "過期操作", False)
    service.change_status(actor, sample_id, 2, "return_for_correction", "金額待核對", False)
    assert row["payload"]["active_revision"] is None
    assert row["payload"]["revisions"][-1]["status"] == "returned"
    assert statuses[-1][2] == "revoked"


def test_v2_columns_keep_exact_amounts_and_sparse_lines() -> None:
    """映射 v2.0 固定小數、字軌、期別及原始明細列號。"""
    from app.schemas.showcase_v2 import columns

    revision = {
        "tax_id": "12345678",
        "truths": [
            {"path": path, "state": "readable", "text": value}
            for path, value in (
                ("invoice_number", "PV-63031801"),
                ("invoice_date", "民國115年3月18日"),
                ("total_amount", "630,000.00"),
                ("tax_type", "應稅"),
                ("items.2.name", "伺服器"),
                ("items.2.quantity", "1.0001"),
                ("items.2.amount", "12.345"),
            )
        ],
    }
    header, items, keys = columns(revision)
    assert (header["invoice_track"], header["invoice_number"]) == ("PV", "63031801")
    assert (header["invoice_date"], header["tax_period"]) == ("2026-03-18", "202604")
    assert (header["total_amount"], header["tax_type"]) == ("630000", "taxable")
    assert items == [{"line_no": 2, "item_name": "伺服器", "quantity": "1.0001"}]
    assert keys["items.2.name"] == ["items[1].item_name"] and "items.2.amount" not in keys


def test_v2_reader_keeps_current_detail_without_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    """純 v2.0 資料可讀；參考輸出只包含 SQL 的驗證欄位。"""
    from contextlib import nullcontext
    from datetime import UTC, datetime

    from app.db.repositories import showcase_v2_reader as reader
    from app.services import showcase_service as service

    sample_id, supplier_id, actor = uuid4(), uuid4(), uuid4()
    now = datetime.now(UTC)
    db_row = dict.fromkeys(reader.HEADER_FIELDS) | {
        "example_id": sample_id,
        "example_version": 2,
        "supplier_id": supplier_id,
        "example_name": "合成 v2",
        "supplier_name": "測試公司",
        "template_key": "版型A",
        "seller_tax_id": "12345678",
        "status": "verified",
        "created_at": now,
        "created_by": actor,
        "verified_by": actor,
        "verified_at": now,
        "verification_note": "完成",
        "invoice_track": "PV",
        "invoice_number": "63031801",
        "total_amount": "100.01",
        "verified_field_keys": ["invoice_track", "invoice_number", "items[2].item_name"],
        "bucket_name": "private",
        "storage_path": "demo/key",
        "mime_type": "image/png",
        "content_sha256": "a" * 64,
        "size_bytes": 200,
        "original_filename": "sample.png",
        "source_invoice_id": supplier_id,
        "source_file_id": sample_id,
        "source_file_version": 4,
        "source_recognition_id": actor,
        "expires_at": now,
        "items": [
            {
                "line_no": 3,
                "item_name": "伺服器",
                "quantity": "2.0000",
                "unit_price": None,
                "line_amount": None,
            }
        ],
    }
    reference = reader.revision(db_row, verified_only=True)
    assert reference["linked_source"] and reference["document_group_id"] == str(supplier_id)
    assert reference["source_file_version"] == 4 and reference["expires_at"] == now.isoformat()
    assert {item["path"]: item["text"] for item in reference["truths"]} == {
        "invoice_number": "PV63031801",
        "items.3.name": "伺服器",
    }
    current = reader.revision(db_row)
    monkeypatch.setattr(service, "open_session", lambda: nullcontext(object()))
    monkeypatch.setattr(service, "authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.v2, "get_example", lambda *args: current)
    monkeypatch.setattr(service.repository, "archive_available", lambda db: False)
    row = service.current_detail(actor, sample_id)
    assert row["read_only"] and row["version"] == 2
    assert row["payload"]["revisions"] == [current]
    assert "total_amount" in {item["path"] for item in current["truths"]}
    assert current["id"] != reader.revision(db_row | {"example_version": 3})["id"]


def test_image_decode_and_corruption() -> None:
    """合法圖可讀，截斷圖／偽副檔名被拒。"""
    assert image_info(png()) == ("image/png", "png")
    for content in (b"not an image", png()[:30], b"%PDF-1.7", b"", b"x" * (4 * 1024 * 1024 + 1)):
        with pytest.raises(ValueError):
            image_info(content)


def test_master_sample_uses_canonical_binding_and_cannot_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非台灣供應商可保存，主檔值以伺服器為準，已保存範例不可改掛。"""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.schemas.e_admin import ConflictError
    from app.services import showcase_service as service

    master_id, sample = uuid4(), uuid4()
    identity = SimpleNamespace(selected_department_id=uuid4(), user_id=uuid4())
    records = []
    monkeypatch.setattr(
        service, "open_session", lambda: nullcontext(SimpleNamespace(commit=lambda: None))
    )
    monkeypatch.setattr(service, "authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.master_repository, "prepare", lambda db: None)
    monkeypatch.setattr(service.repository, "archive_writable", lambda db: True)
    monkeypatch.setattr(
        service.master_repository,
        "get_supplier",
        lambda db, key: (
            {"supplier_name": "日本合成主檔", "tax_id": "DEMO-JP-002", "country_code": "JP"}
            if key == master_id
            else None
        ),
    )
    monkeypatch.setattr(service, "upload", lambda *args: {"key": "synthetic"})
    monkeypatch.setattr(
        service.repository, "supplier", lambda *args: pytest.fail("主檔範例不能另建舊統編關聯")
    )
    monkeypatch.setattr(
        service.repository, "save", lambda db, dept, key, payload, version: records.append(payload)
    )
    monkeypatch.setattr(service.repository, "event", lambda *args: None)
    monkeypatch.setattr(service.repository, "upload_state", lambda *args: None)
    service.save(identity, data(master_supplier_id=master_id, tax_id=""), png(), sample, 0)
    payload = records[0]
    assert payload["supplier_id"] == payload["master_supplier_id"] == str(master_id)
    assert payload["supplier_name"] == "日本合成主檔" and payload["tax_id"] == ""
    assert transition(payload, "activate", identity.user_id, True)["active_revision"]
    monkeypatch.setattr(service, "detail", lambda *args: {"version": 1, "payload": payload})
    with pytest.raises(ConflictError):
        service.save(identity, data(master_supplier_id=uuid4()), png(), sample, 1)


def test_replacement_preserves_history_and_confirmation() -> None:
    """替換不覆蓋原圖、不繼承人工確認，舊 active 可被明確停用。"""
    actor = uuid4()
    old = revise(None, data(), {"key": "original"}, actor)
    active = transition(old, "activate", actor, True)
    new = revise(active, data(reason="新版替換"), {"key": "replacement"}, actor)
    assert len(active["revisions"]) == 1
    assert new["revisions"][0]["source"]["key"] == "original"
    assert new["revisions"][-1]["source"]["key"] == "replacement"
    assert new["revisions"][-1]["status"] == "draft"
    assert "confirmed_by" not in new["revisions"][-1]
    assert new["active_revision"] == active["active_revision"]
    switched = transition(new, "activate", actor, True)
    assert switched["revisions"][0]["status"] == "superseded"
    assert switched["active_revision"] != active["active_revision"]
    assert transition(switched, "deactivate", actor, False)["active_revision"] is None


def test_activation_requires_manual_confirmation_and_truth() -> None:
    """不以有圖或全部未知當作核定成功。"""
    draft = revise(None, data(), {"key": "original"}, uuid4())
    with pytest.raises(ValueError):
        transition(draft, "activate", uuid4(), False)
    unknown = revise(
        None, data(truths=[{"path": "total_amount", "state": "unreadable"}]), {"key": "x"}, uuid4()
    )
    with pytest.raises(ValueError):
        transition(unknown, "activate", uuid4(), True)


def test_intake_keeps_ai_original_separate_from_confirmed_values() -> None:
    """整張啟用確認有值欄位，保留 AI 原值，錯供應商仍拒絕。"""
    actor = uuid4()
    payload = revise(None, data(), {"key": "original"}, actor)
    original = {"values": {"items.1.unit_price": "600000"}, "source_sha256": "original"}
    payload["revisions"][-1]["recognition"] = original
    seller = {"path": "seller_tax_id", "state": "readable", "text": "12345678"}
    unit = {"path": "items.1.unit_price", "state": "unconfirmed", "text": "600000"}
    pending = revise(payload, data(truths=[seller, unit]), None, actor)
    pending_active = transition(pending, "activate", actor, True)
    assert pending_active["revisions"][-1]["truths"][1]["state"] == "readable"
    assert pending_active["revisions"][-1]["recognition"] == original
    cleared = {"path": "items.1.unit_price", "state": "blank"}
    wrong = revise(payload, data(truths=[seller | {"text": "11111111"}, cleared]), None, actor)
    with pytest.raises(ValueError, match="供應商一致"):
        transition(wrong, "activate", actor, True)
    reviewed = revise(payload, data(truths=[seller, cleared]), None, actor)
    active = transition(reviewed, "activate", actor, True)
    latest = active["revisions"][-1]
    assert latest["recognition"] == original
    assert latest["truths"][1]["state"] == "blank" and latest["truths"][1]["text"] == ""
    assert latest["confirmed_by"] == str(actor) and latest["confirmed_at"]
    replaced = revise(active, data(), {"key": "replacement"}, actor)
    assert "recognition" not in replaced["revisions"][-1]
    assert replaced["active_revision"] == latest["id"]


def test_intake_does_not_fill_blank_unit_price(monkeypatch: pytest.MonkeyPatch) -> None:
    """整理 OCR 票面時保留明細金額，空白單價及模型額外欄位不帶入候選。"""
    from types import SimpleNamespace

    from app.services import showcase_intake_service as intake

    reply = SimpleNamespace(
        text='{"items":[{"item_name":"伺服器","unit_price":null,"line_amount":"600,000"}],"confirmed_by":"模型"}',
        model="offline-model",
        finish_reason="stop",
    )
    monkeypatch.setattr(
        intake.GeminiClient,
        "from_settings",
        lambda: SimpleNamespace(extract_invoice=lambda *args, **kwargs: reply, close=lambda: None),
    )
    snapshot = intake.extract_candidates(png(), "image/png")
    assert snapshot["values"] == {"items.1.name": "伺服器", "items.1.amount": "600,000"}


def test_intake_rejects_empty_candidate_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """模型回傳全部空值時不能向使用者宣稱辨識完成。"""
    from types import SimpleNamespace

    from app.services import showcase_intake_service as intake

    reply = SimpleNamespace(
        text='{"invoice_number":null,"items":[]}', model="offline", finish_reason="stop"
    )
    monkeypatch.setattr(
        intake.GeminiClient,
        "from_settings",
        lambda: SimpleNamespace(extract_invoice=lambda *args, **kwargs: reply, close=lambda: None),
    )
    with pytest.raises(ValueError, match="沒有可用的欄位候選"):
        intake.extract_candidates(png(), "image/png")


def test_intake_uses_document_ai_groq_when_gemini_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gemini 未設定時採既有 OCR 備援，仍須回傳待核對欄位。"""
    from contextlib import nullcontext
    from types import SimpleNamespace

    from app.integrations.gemini import GeminiError
    from app.services import showcase_intake_service as intake

    def missing_gemini() -> None:
        raise GeminiError("gemini_not_configured")

    monkeypatch.setattr(intake.GeminiClient, "from_settings", missing_gemini)
    monkeypatch.setattr(
        intake.DocumentAIClient,
        "from_settings",
        lambda: nullcontext(
            SimpleNamespace(process=lambda *args, **kwargs: SimpleNamespace(text="AB12345678"))
        ),
    )
    reply = SimpleNamespace(
        text='{"invoice_number":"AB12345678","items":[]}',
        model="offline-groq",
        finish_reason="stop",
    )
    monkeypatch.setattr(
        intake.GroqClient,
        "from_settings",
        lambda: SimpleNamespace(chat=lambda *args, **kwargs: reply),
    )
    result = intake.extract_candidates(png(), "image/png")
    assert result["provider"] == "document_ai+groq"
    assert result["values"] == {"invoice_number": "AB12345678"}


def test_intake_does_not_fallback_for_gemini_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gemini 真正服務錯誤須保留階段與代碼，不能改判成未設定。"""
    from app.integrations.gemini import GeminiError
    from app.services import showcase_intake_service as intake

    def unavailable_gemini() -> None:
        raise GeminiError("gemini_denied")

    monkeypatch.setattr(intake.GeminiClient, "from_settings", unavailable_gemini)
    monkeypatch.setattr(
        intake.DocumentAIClient,
        "from_settings",
        lambda: pytest.fail("不應改用 OCR 備援"),
    )
    with pytest.raises(GeminiError) as error:
        intake.extract_candidates(png(), "image/png")
    assert error.value.code == "gemini_denied"


def test_ocr_failure_metadata_does_not_store_sensitive_error_text() -> None:
    """失敗草稿只留安全階段與代碼，避免把上游訊息寫進歷程。"""
    from app.integrations.document_ai import DocumentAIError
    from app.integrations.groq import GroqError
    from app.services.showcase_service import _recognition_failure

    assert _recognition_failure(DocumentAIError("document_ai_timeout")) == {
        "status": "failed",
        "failure_stage": "ocr",
        "failure_code": "document_ai_timeout",
        "values": {},
    }
    assert _recognition_failure(GroqError("groq_timeout"))["failure_stage"] == "fields"
    assert _recognition_failure(ValueError("token=synthetic-secret")) == {
        "status": "failed",
        "failure_stage": "fields",
        "failure_code": "invalid_result",
        "values": {},
    }


@pytest.mark.parametrize(
    "truths",
    [
        [{"path": "total_amount", "state": "readable", "text": ""}],
        [{"path": "total_amount", "state": "blank", "text": "0"}],
        [{"path": "total_amount", "state": "unreadable", "text": "猜的答案"}],
        [{"path": "total_amount", "state": "blank"}] * 2,
    ],
)
def test_invalid_truths(truths: list[dict[str, str]]) -> None:
    """重複欄位與空值語意不符必須拒絕。"""
    with pytest.raises(ValueError):
        validate_truths(data(truths=truths))


def test_no_source_no_sample() -> None:
    """沒有原件不能建立範例。"""
    with pytest.raises(ValueError):
        revise(None, data(), None, uuid4())


def test_search_input_rejects_unknown_filters() -> None:
    """非法狀態、越界頁碼及供應商 ID 不可進入查詢。"""
    from pydantic import ValidationError

    from app.schemas.showcase import SearchInput

    for value in ({"status": "draft_sql"}, {"page": 0}, {"supplier_id": "not-uuid"}):
        with pytest.raises(ValidationError):
            SearchInput.model_validate(value)


def test_query_binds_filters_and_supplier_scope() -> None:
    """含特殊字元的搜尋保持參數化，不能改變部門邊界。"""
    from types import SimpleNamespace

    from app.db.repositories import showcase_repository as repository
    from app.schemas.showcase import SearchInput

    calls = []
    db = SimpleNamespace(
        execute=lambda sql, params: (
            calls.append((str(sql), params)) or SimpleNamespace(mappings=lambda: [])
        )
    )
    department = uuid4()
    filters = SearchInput(
        q="' OR 1=1 --", layout="新版", writing="甲", status="active", supplier_id=uuid4(), page=2
    )
    assert repository.search(db, department, filters.q, 2, filters) == []
    sql, params = calls[0]
    assert filters.q not in sql
    assert params["dept"] == department and params["offset"] == 20
    assert params["status"] == "active" and params["supplier"] == str(filters.supplier_id)
    assert params["shared"] is False
    assert repository.search(db, department, filters.q, 2, filters, shared_master=True) == []
    sql, params = calls[-1]
    assert params["shared"] is True and "payload->>'master_supplier_id'=:supplier" in sql


def test_shared_reference_excludes_internal_data(monkeypatch: pytest.MonkeyPatch) -> None:
    """跨部門參考白名單不暴露草稿、來源 key、操作者及管理紀錄。"""
    from types import SimpleNamespace

    from app.services import showcase_service as service

    class Session:
        def __enter__(self) -> object:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr(service, "open_session", Session)
    monkeypatch.setattr(service, "reference_access", lambda *args: None)
    revision = {
        "id": "active",
        "title": "啟用版",
        "tax_id": "12345678",
        "source": {"key": "secret"},
        "confirmed_by": "private",
        "truths": [],
    }
    monkeypatch.setattr(
        service.repository,
        "reference_candidates",
        lambda *args: [{"sample_id": uuid4(), "revision": revision}],
    )
    result = service.references(SimpleNamespace(), "12345678")
    assert result[0]["title"] == "啟用版"
    assert "secret" not in str(result) and "private" not in str(result)


def test_main_evidence_compatibility_without_ai(monkeypatch: pytest.MonkeyPatch) -> None:
    """原圖 digest 相容 main 證據契約；替換圖不得通過舊來源完整性檢查。"""
    from types import SimpleNamespace

    from app.integrations.document_ai_evidence import SourceRef, sha256
    from app.schemas.e_admin import UnavailableError
    from app.services import showcase_service as service

    content = png()
    ref = SourceRef.from_bytes(
        file_id="offline-source", version="1", recognition_id="offline-run", content=content
    )
    assert service.sha256(content) == ref.sha256 == sha256(content)
    sample, revision = uuid4(), uuid4()
    row = {
        "payload": {
            "revisions": [
                {
                    "id": str(revision),
                    "source": {
                        "key": "synthetic-original",
                        "bucket": "synthetic-private",
                        "sha256": ref.sha256,
                        "mime": "image/png",
                    },
                }
            ]
        }
    }
    monkeypatch.setattr(service, "detail", lambda *args: row)
    monkeypatch.setattr(service, "current_detail", lambda *args: row)
    calls = []

    def storage(**kwargs: object) -> object:
        calls.append(kwargs)
        return SimpleNamespace(get=lambda key: (content, "image/png"))

    monkeypatch.setattr(service.S3StorageClient, "from_settings", storage)
    assert service.source(object(), sample, revision) == (content, "image/png")
    assert calls == [{"bucket": "synthetic-private"}]
    monkeypatch.setattr(
        service.S3StorageClient,
        "from_settings",
        lambda **kwargs: SimpleNamespace(get=lambda key: (b"replaced-image", "image/png")),
    )
    with pytest.raises(UnavailableError):
        service.source(object(), sample, revision)


def test_department_matrix_can_grant_only_implemented_actions() -> None:
    """部門矩陣可配置新增／修改及獨立啟用，沒有實體刪除。"""
    from app.schemas.e_admin import Action
    from app.services.e_registry import supported_actions

    assert supported_actions("show_case") == {Action.VIEW, Action.CREATE, Action.EDIT}
    assert not supported_actions("snow_case") and not supported_actions("supplier.showcase")
    assert supported_actions("supplier.showcase-activation") == {Action.VIEW, Action.EDIT}


def test_authorization_rechecks_current_and_independent_activation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """核定必須使用獨立代碼，拒絕過期或撤權結果。"""
    from types import SimpleNamespace

    from app.schemas.e_admin import Action, DeniedError
    from app.services import showcase_service as service
    from app.services.admin_login_service import DepartmentLoginAdapter

    calls = []
    adapter = DepartmentLoginAdapter(())
    monkeypatch.setattr(service, "get_e_module", lambda: SimpleNamespace(access=adapter))
    monkeypatch.setattr(
        service.auth_repository, "prepare", lambda db, **kw: calls.append("prepare")
    )
    monkeypatch.setattr(adapter, "current", lambda db, actor: calls.append("current") or actor)
    monkeypatch.setattr(
        adapter,
        "decision",
        lambda db, actor, code, action: (
            calls.append(code) or SimpleNamespace(department_effect="deny")
        ),
    )
    with pytest.raises(DeniedError):
        service.authorize(object(), object(), Action.EDIT, activation=True)
    assert calls == ["prepare", "current", "supplier.showcase-activation"]


def test_routes_and_escaped_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """離線渲染真實頁面，檢查身分與 HTML escaping；測試不接 DB。"""
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from fastapi import Request
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers.e_admin import current_identity
    from app.schemas.e_admin import Identity
    from app.services import showcase_service as service
    from app.services.e_admin_service import get_e_module

    actor = Identity(
        uuid4(), uuid4(), uuid4(), 1, datetime.now(UTC) + timedelta(hours=1), True, "admin"
    )

    def current(request: Request) -> Identity:
        request.state.e_access = SimpleNamespace(
            departments=(),
            functions=(
                SimpleNamespace(
                    code="supplier.showcase",
                    grants=(SimpleNamespace(action="create"), SimpleNamespace(action="edit")),
                ),
            ),
        )
        request.state.header_departments = ()
        return actor

    saved = dict(app.dependency_overrides)
    app.dependency_overrides[current_identity] = current
    app.dependency_overrides[get_e_module] = lambda: SimpleNamespace(
        sidebar_menu=lambda identity: ()
    )
    monkeypatch.setattr(
        service,
        "query",
        lambda *args: [
            {
                "id": uuid4(),
                "payload": {
                    "title": "<script>alert(1)</script>",
                    "supplier_name": "測試",
                    "tax_id": "12345678",
                    "active_revision": None,
                },
            }
        ],
    )
    try:
        with TestClient(app) as client:
            page = client.get("/showcases")
            assert page.status_code == 200
            assert "&lt;script&gt;" in page.text
            assert "<script>alert(1)</script>" not in page.text
            new_page = client.get("/showcases/new", follow_redirects=False)
            assert new_page.status_code == 303 and new_page.headers["location"] == "/suppliers"
            assert client.post(f"/showcases/{uuid4()}/status").status_code == 403
            # 本站共用 RequestValidationError handler 以 400 呈現格式錯誤。
            assert client.get("/showcases/not-a-uuid").status_code == 400
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)


def test_detail_template_and_upload_actor(monkeypatch: pytest.MonkeyPatch) -> None:
    """詳情實際渲染、測試並排與上傳使用登入者，忽略表單冒用身分。"""
    from datetime import UTC, datetime, timedelta
    from types import SimpleNamespace

    from fastapi import Request
    from fastapi.testclient import TestClient

    from app.main import app
    from app.routers.e_admin import current_identity
    from app.schemas.e_admin import Identity
    from app.services import showcase_service as service
    from app.services.e_admin_service import get_e_module

    actor = Identity(
        uuid4(), uuid4(), uuid4(), 1, datetime.now(UTC) + timedelta(hours=1), True, "admin"
    )
    sample = uuid4()
    payload = transition(
        revise(None, data(), {"key": "source"}, actor.user_id), "activate", actor.user_id, True
    )
    row = {
        "version": 1,
        "payload": payload,
        "can_change_status": True,
        "history": [],
        "tests": [
            {
                "id": uuid4(),
                "revision_id": payload["active_revision"],
                "note": "測試",
                "created_at": "2026-10-30",
            }
        ],
    }

    granted_actions = ["create", "edit"]

    def current(request: Request) -> Identity:
        request.state.e_access = SimpleNamespace(
            departments=(),
            functions=(
                SimpleNamespace(
                    code="supplier.showcase",
                    grants=tuple(SimpleNamespace(action=action) for action in granted_actions),
                ),
            ),
        )
        return actor

    saved = dict(app.dependency_overrides)
    app.dependency_overrides[current_identity] = current
    app.dependency_overrides[get_e_module] = lambda: SimpleNamespace(
        sidebar_menu=lambda identity: ()
    )
    monkeypatch.setattr(service, "detail", lambda *args: row)
    monkeypatch.setattr(service, "current_detail", lambda *args: row)
    calls = []
    monkeypatch.setattr(service, "save", lambda *args, **kwargs: calls.append((args, kwargs)))

    def status(identity, key, version, command, reason, confirmed):
        assert (identity, key, version, reason) == (actor, sample, 1, "請重核金額")
        row["payload"] = transition(payload, command, identity.user_id, confirmed)

    monkeypatch.setattr(service, "change_status", status)
    try:
        with TestClient(app) as client:
            page = client.get(f"/showcases/{sample}")
            assert page.status_code == 200
            assert "/tests/" in page.text
            assert 'name="command" value="deactivate"' in page.text
            assert all(label in page.text for label in ("退回補正", "停用"))
            for command, expected in (("activate", 303), ("return_for_correction", 303)):
                response = client.post(
                    f"/showcases/{sample}/status",
                    data={"version": 1, "command": command, "reason": "請重核金額"},
                    headers={"Origin": "http://testserver"},
                    follow_redirects=False,
                )
                assert response.status_code == expected
                assert response.headers["location"].endswith("#showcase-status")
            returned_page = client.get(f"/showcases/{sample}")
            assert "範例狀態：退回補正" in returned_page.text
            assert 'name="command" value="activate"' in returned_page.text
            row["payload"] = payload
            row["can_change_status"] = False
            assert 'name="command" value="activate"' not in client.get(f"/showcases/{sample}").text
            supplier_id = uuid4()
            monkeypatch.setattr(
                service,
                "collection",
                lambda *args: {
                    "id": supplier_id,
                    "name": "<script>合成公司</script>",
                    "tax_id": "12345678",
                    "sample_count": 25,
                    "active_count": 10,
                },
            )
            monkeypatch.setattr(
                service, "query", lambda *args, **kwargs: [{"id": sample, "payload": payload}]
            )
            collection_page = client.get(f"/showcases/collections/{supplier_id}")
            assert collection_page.status_code == 200
            assert "共 25 張" in collection_page.text and "已啟用 10 張" in collection_page.text
            assert "showcase-thumbnail" in collection_page.text
            assert "&lt;script&gt;合成公司&lt;/script&gt;" in collection_page.text
            assert "從正式供應商新增範例" in collection_page.text
            assert f"/showcases/new?supplier_id={supplier_id}" not in collection_page.text
            old_new = client.get(
                f"/showcases/new?supplier_id={supplier_id}", follow_redirects=False
            )
            assert old_new.status_code == 303 and old_new.headers["location"] == "/suppliers"
            monkeypatch.setattr(
                service,
                "master_supplier",
                lambda *args: {
                    "supplier_name": "合成公司",
                    "tax_id": "12345678",
                    "country_code": "TW",
                },
            )
            new_page = client.get(f"/showcases/new?master_supplier_id={supplier_id}")
            assert new_page.status_code == 200 and 'value="12345678"' in new_page.text
            assert 'id="showcase-fields"' in new_page.text
            assert 'class="showcase-correction-field"' in new_page.text
            assert "手動建檔欄位" not in new_page.text
            fields = {
                "title": "範例",
                "supplier_name": "合成公司",
                "tax_id": "12345678",
                "layout": "A",
                "reason": "上傳",
                "version": "0",
                "actor_id": str(uuid4()),
            }
            response = client.post(
                f"/showcases/{sample}/save",
                data=fields,
                files={"image": ("image.png", png(), "image/png")},
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )
            assert response.status_code == 303
            assert calls[0][0][0].user_id == actor.user_id
            response = client.post(
                f"/showcases/{sample}/save",
                data=fields | {"run_ocr": "yes", "truth_path": "unsubmitted-draft"},
                files={"image": ("image.png", png(), "image/png")},
                headers={"Origin": "http://testserver"},
                follow_redirects=False,
            )
            assert response.status_code == 303 and response.headers["location"].endswith(
                "#showcase-review-fields"
            )
            assert calls[1][1] == {"run_ocr": True} and calls[1][0][1].truths == []
            payload["revisions"][-1]["recognition"] = {"values": {"items.1.unit_price": "600000"}}
            page = client.get(f"/showcases/{sample}")
            assert page.status_code == 200 and "AI 原值：600000" in page.text
            assert "辨識已完成，讀取 1 個欄位" in page.text
            assert 'class="showcase-review"' in page.text
            assert page.text.index('id="showcase-review-image"') < page.text.index(
                'id="showcase-review-fields"'
            )
            payload["revisions"][-1]["recognition"] = {
                "status": "failed",
                "failure_stage": "ocr",
                "values": {},
            }
            failed_page = client.get(f"/showcases/{sample}")
            assert failed_page.status_code == 200
            assert "失敗階段：影像文字讀取" in failed_page.text
            assert "辨識已完成" not in failed_page.text
            granted_actions.clear()
            monkeypatch.setattr(
                service,
                "master_examples",
                lambda *args: ([{"id": sample, "payload": payload}], True),
            )
            view_only_collection = client.get(f"/showcases/by-supplier/{supplier_id}")
            assert view_only_collection.status_code == 200
            assert "目前部門可查看範例" in view_only_collection.text
            assert "＋ 加入範例發票" not in view_only_collection.text
            view_only_detail = client.get(f"/showcases/{sample}")
            assert view_only_detail.status_code == 200
            assert "檢視／替換此範例" not in view_only_detail.text
            assert "保存核對草稿" not in view_only_detail.text
            assert 'action="/showcases/' + str(sample) + '/test"' not in view_only_detail.text
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved)
