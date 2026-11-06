"""模組名稱：app.services.showcase_service；功能說明：供應商範例版本、私有原件與操作稽核。

所有操作均驗當前部門勾選；上傳先留 reservation，原圖與事件全部保留。
"""

import io
import warnings
from contextlib import suppress
from copy import deepcopy
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.db.repositories import admin_login_repository as auth_repository
from app.db.repositories import showcase_repository as repository
from app.db.repositories import showcase_v2_reader as v2
from app.db.repositories import suppliers_repository as master_repository
from app.db.repositories.e_repository import open_session
from app.integrations.document_ai import DocumentAIError
from app.integrations.document_ai_evidence import sha256
from app.integrations.document_ai_fewshot import FewShotError
from app.integrations.gemini import GeminiError
from app.integrations.groq import GroqError
from app.integrations.supabase_storage import S3StorageClient, StorageError
from app.schemas.e_admin import (
    Action,
    ConflictError,
    DeniedError,
    Identity,
    MissingError,
    UnavailableError,
)
from app.schemas.showcase import (
    ACTIVATE_CODE,
    CODE,
    MAX_BYTES,
    MAX_PIXELS,
    SampleInput,
    SearchInput,
    validate_truths,
)
from app.schemas.showcase_v2 import columns as example_columns
from app.services import showcase_analysis_service as analysis
from app.services import showcase_intake_service as intake
from app.services.admin_login_service import DepartmentLoginAdapter
from app.services.e_admin_service import get_e_module


def image_info(content: bytes) -> tuple[str, str]:
    """完整解碼驗證 JPG／PNG，限制像素、大小及動畫，不信任副檔名。"""
    if not content or len(content) > MAX_BYTES:
        raise ValueError("請上傳 4 MB 以內的 JPG／PNG")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(content)) as img:
                fmt = img.format
                if (
                    fmt not in {"JPEG", "PNG"}
                    or img.width * img.height > MAX_PIXELS
                    or getattr(img, "n_frames", 1) != 1
                ):
                    raise ValueError("僅支援二千萬像素以內的單張 JPG／PNG")
                img.verify()
            with Image.open(io.BytesIO(content)) as img:
                img.load()
    except (
        UnidentifiedImageError,
        OSError,
        SyntaxError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ValueError("圖片損毀或格式不支援") from None
    return ("image/jpeg", "jpg") if fmt == "JPEG" else ("image/png", "png")


def authorize(
    db: Any,
    identity: Identity,
    action: Action,
    *,
    activation: bool = False,
    lock: bool = False,
    supplier_master: bool = False,
) -> None:
    """交易內驗有效帳號、所選部門與最新功能勾選；不以部門名稱授權。"""
    adapter = get_e_module().access
    if not isinstance(adapter, DepartmentLoginAdapter):
        raise UnavailableError
    auth_repository.prepare(db, lock=lock)
    current = adapter.current(db, identity)
    decision = adapter.decision(db, current, ACTIVATE_CODE if activation else CODE, action)
    if decision.department_effect != "allow":
        raise DeniedError
    if (
        supplier_master
        and adapter.decision(db, current, "suppliers", Action.VIEW).department_effect != "allow"
    ):
        raise DeniedError


def _read_master(db: Any, supplier_id: UUID) -> dict[str, Any]:
    master_repository.prepare(db)
    master = master_repository.get_supplier(db, supplier_id)
    if master is None:
        raise MissingError
    return master


def master_supplier(
    identity: Identity, supplier_id: UUID, action: Action = Action.VIEW
) -> dict[str, Any]:
    """依主檔 UUID 讀取供應商，保留兩套部門權限。"""
    try:
        with open_session() as db:
            authorize(db, identity, action, supplier_master=True)
            return _read_master(db, supplier_id)
    except repository.StoreError:
        raise UnavailableError from None


def query(
    identity: Identity,
    term: str = "",
    page: int = 1,
    filters: SearchInput | None = None,
    *,
    shared_master: bool = False,
) -> list[dict[str, Any]]:
    """搜尋本部門範例或指定供應商的共用範例。"""
    try:
        with open_session() as db:
            authorize(db, identity, Action.VIEW, supplier_master=shared_master)
            selected = filters or SearchInput(q=term, page=page)
            return repository.search(
                db,
                identity.selected_department_id,
                selected.q,
                selected.page,
                selected,
                shared_master=shared_master,
            )
    except repository.StoreError:
        raise UnavailableError from None


def master_examples(
    identity: Identity, supplier: UUID, page: int
) -> tuple[list[dict[str, Any]], bool]:
    """正式主檔範例清單以 v2.0 為準，另回報歷程能否保存。"""
    try:
        with open_session() as db:
            authorize(db, identity, Action.VIEW, supplier_master=True)
            examples = v2.supplier_examples(db, supplier, page)
            has_archive = repository.archive_available(db)
            if has_archive:
                for example in examples:
                    current = example["payload"]["revisions"][-1]
                    try:
                        archived = repository.read(
                            db, identity.selected_department_id, example["id"], shared_master=True
                        )["payload"]["revisions"][-1]
                    except MissingError:
                        continue
                    if not _same_current_revision(archived, current):
                        continue
                    known = {field["path"] for field in current["truths"]}
                    current["truths"].extend(
                        field
                        for field in archived["truths"]
                        if field["path"] in {"invoice_date", "invoice_number"}
                        and field["path"] not in known
                        and field["text"].strip()
                        and field["state"] == "readable"
                    )
            return examples, has_archive and repository.archive_writable(db)
    except repository.StoreError:
        raise UnavailableError from None


def suppliers(identity: Identity, term: str) -> list[dict[str, Any]]:
    """供應商選取也驗目前部門查看權，不提供全公司資料。"""
    try:
        with open_session() as db:
            authorize(db, identity, Action.VIEW)
            return repository.suppliers(db, identity.selected_department_id, term[:120])
    except repository.StoreError:
        raise UnavailableError from None


def collections(identity: Identity, term: str, page: int) -> list[dict[str, Any]]:
    """查閱本部門的供應商範例集與完整計數。"""
    filters = SearchInput(q=term, page=page)
    try:
        with open_session() as db:
            authorize(db, identity, Action.VIEW)
            return repository.collections(
                db, identity.selected_department_id, filters.q, filters.page
            )
    except repository.StoreError:
        raise UnavailableError from None


def collection(identity: Identity, supplier_id: UUID) -> dict[str, Any]:
    """取固定供應商名稱供帶入新增表單，不信任網址名稱。"""
    try:
        with open_session() as db:
            authorize(db, identity, Action.VIEW)
            return repository.collection(db, identity.selected_department_id, supplier_id)
    except repository.StoreError:
        raise UnavailableError from None


def reference_access(db: Any, identity: Identity) -> None:
    """任何有效登入者可取用啟用範例；不授予維護或查看草稿權。"""
    adapter = get_e_module().access
    if not isinstance(adapter, DepartmentLoginAdapter):
        raise UnavailableError
    auth_repository.prepare(db)
    adapter.current(db, identity)


def references(identity: Identity, tax_id: str, layout: str = "") -> list[dict[str, Any]]:
    """對應供應商的跨部門唯讀候選，去除內部物件鍵、紀錄與確認者資料。"""
    if len(tax_id) != 8 or not tax_id.isascii() or not tax_id.isdigit():
        raise ValueError("請輸入八碼供應商統編")
    try:
        with open_session() as db:
            reference_access(db, identity)
            candidates = repository.reference_candidates(db, tax_id, layout[:120])
        return [
            {
                "sample_id": row["sample_id"],
                "revision_id": row["revision"]["id"],
                **{
                    key: row["revision"].get(key, "")
                    for key in (
                        "title",
                        "supplier_name",
                        "tax_id",
                        "layout",
                        "writing",
                        "copy_type",
                        "valid_from",
                        "valid_to",
                        "key_fields",
                    )
                },
                "truths": [
                    truth
                    for truth in row["revision"].get("truths", [])
                    if truth["state"] == "readable"
                    and truth["path"] in row["revision"].get("key_fields", [])
                ],
            }
            for row in candidates
        ]
    except repository.StoreError:
        raise UnavailableError from None


def reference_file(identity: Identity, sample: UUID, revision: UUID) -> tuple[bytes, str]:
    """跨部門只提供當前啟用版原圖，不能藉此讀管理歷史或測試原件。"""
    try:
        with open_session() as db:
            reference_access(db, identity)
            ref = repository.reference_revision(db, sample, revision)["source"]
        content, _ = S3StorageClient.from_settings(bucket=ref["bucket"]).get(ref["key"])
        if sha256(content) != ref["sha256"]:
            raise UnavailableError
        return content, ref["mime"]
    except (repository.StoreError, StorageError):
        raise UnavailableError from None


def _managed_row(
    db: Any, identity: Identity, sample: UUID, action: Action, *, lock: bool = False
) -> dict[str, Any]:
    """共用範例另驗供應商主檔查看；跨部門修訂需審查權。"""
    authorize(db, identity, action, lock=lock)
    department = identity.selected_department_id
    row = repository.read(db, department, sample, lock=lock, shared_master=True)
    if row["payload"].get("master_supplier_id"):
        authorize(db, identity, action, lock=lock, supplier_master=True)
    if action == Action.EDIT and row["department_id"] != department:
        authorize(db, identity, Action.EDIT, activation=True, lock=lock)
    return row


def detail(identity: Identity, sample: UUID) -> dict[str, Any]:
    """查看共用範例與歷程，測試紀錄仍按執行部門隔離。"""
    try:
        with open_session() as db:
            row = _managed_row(db, identity, sample, Action.VIEW)
            department = identity.selected_department_id
            row["history"] = repository.history(db, row["department_id"], sample, department)
            row["tests"] = repository.tests(db, department, sample)
            try:
                authorize(db, identity, Action.EDIT, activation=True)
            except DeniedError:
                row["can_change_status"] = False
            else:
                row["can_change_status"] = True
            return row
    except repository.StoreError:
        raise UnavailableError from None


def _same_current_revision(archive: dict[str, Any], current: dict[str, Any]) -> bool:
    """核對版號、供應商、狀態與原件，避免混入另一個正式版本。"""
    return (
        archive.get("master_supplier_id") == current.get("master_supplier_id")
        and archive.get("v2_example_version") == current.get("v2_example_version")
        and archive.get("status") == current.get("status")
        and all(
            archive.get("source", {}).get(key) == current.get("source", {}).get(key)
            for key in ("bucket", "key", "sha256")
        )
    )


def _require_current_revision(db: Any, sample: UUID, row: dict[str, Any]) -> None:
    """交易內鎖定正式範例，拒絕過期歷程與唯讀來源的寫入。"""
    latest = row["payload"]["revisions"][-1]
    if not latest.get("v2_example_version"):
        return
    current = v2.get_example(db, sample, lock=True)
    if (
        current is None
        or current.get("linked_source")
        or not _same_current_revision(latest, current)
    ):
        raise ConflictError


def current_detail(identity: Identity, sample: UUID) -> dict[str, Any]:
    """目前版讀 v2.0；可讀的歷程用於補充原文與稽核。"""
    try:
        with open_session() as db:
            authorize(db, identity, Action.VIEW)
            current = v2.get_example(db, sample)
            if current:
                authorize(db, identity, Action.VIEW, supplier_master=True)
            has_archive = repository.archive_available(db)
            can_write = has_archive and repository.archive_writable(db)
    except repository.StoreError:
        raise UnavailableError from None
    if current is None:
        if has_archive:
            return detail(identity, sample)
        raise MissingError

    archive = None
    if has_archive:
        with suppress(MissingError, UnavailableError):
            archive = detail(identity, sample)
    revisions = archive["payload"]["revisions"] if archive else []
    latest = revisions[-1] if revisions else {}
    matched = _same_current_revision(latest, current)
    if matched:
        fields = {
            truth["path"]: truth for truth in latest["truths"] if truth["state"] != "readable"
        }
        fields.update({truth["path"]: truth for truth in current["truths"]})
        current = (
            latest
            | current
            | {
                "id": latest["id"],
                "v2_id": current["id"],
                "truths": list(fields.values()),
                "copy_type": latest.get("copy_type", "unknown"),
                "created_at": latest["created_at"],
                "key_fields": current["key_fields"]
                if current["status"] != "draft"
                else latest["key_fields"],
            }
        )
        revisions = [*revisions[:-1], current]
    else:
        revisions = [*revisions, current]
    can_edit = matched and can_write and not current.get("linked_source")
    return {
        **(archive or {}),
        "read_only": not can_edit,
        "can_change_status": can_edit and archive["can_change_status"],
        "version": archive["version"] if matched else current["v2_example_version"],
        "payload": {
            "revisions": revisions,
            "master_supplier_id": current["master_supplier_id"],
            "active_revision": current["id"] if current["usable"] else None,
        },
    }


def upload(
    identity: Identity, supplier: UUID, content: bytes, action: Action, sample: UUID
) -> dict[str, str]:
    """先保存來源登錄，再寫私人儲存；每次不同物件鍵，失敗可追查且不刪檔。"""
    mime, ext = image_info(content)
    key = f"showcase/{identity.selected_department_id}/{supplier}/{sample}/{uuid4()}.{ext}"
    # 與最新 main 的原圖證據模組使用相同雜湊契約，不改寫原圖或套 EXIF 後再雜湊。
    digest = sha256(content)
    try:
        with open_session() as db:
            authorize(db, identity, action, lock=True)
            repository.reserve(
                db, identity.selected_department_id, identity.user_id, key, mime, digest
            )
            db.commit()
        S3StorageClient.from_settings().put(key, content, content_type=mime)
        with open_session() as db:
            repository.upload_state(db, key, "uploaded")
            db.commit()
    except (repository.StoreError, StorageError):
        raise UnavailableError from None
    return {
        "key": key,
        "mime": mime,
        "sha256": digest,
        "bucket": settings.SUPABASE_STORAGE_BUCKET,
        "size": str(len(content)),
        "filename": f"{sample}.{ext}",
    }


def revise(
    payload: dict[str, Any] | None, data: SampleInput, source: dict[str, str] | None, actor: UUID
) -> dict[str, Any]:
    """新增不可變修訂，原可用版本不覆蓋；確認資格不沿用。"""
    validate_truths(data)
    result = deepcopy(payload) if payload else {"revisions": [], "active_revision": None}
    if source is None:
        if not result["revisions"]:
            raise ValueError("請上傳範例圖片")
        source = result["revisions"][-1]["source"]
        recognition = result["revisions"][-1].get("recognition")
    else:
        recognition = None
    revision = {
        **data.model_dump(mode="json"),
        "id": str(uuid4()),
        "source": source,
        "created_by": str(actor),
        "created_at": datetime.now(UTC).isoformat(),
        "status": "draft",
    }
    if recognition:
        revision["recognition"] = recognition
    result["revisions"].append(revision)
    result.update(
        title=data.title, supplier_name=data.supplier_name, tax_id=data.tax_id, layout=data.layout
    )
    return result


def _recognition_failure(
    error: DocumentAIError | GroqError | GeminiError | ValueError,
) -> dict[str, Any]:
    """只保存固定階段與安全錯誤代碼，不保存上游例外或票面內容。"""
    if isinstance(error, DocumentAIError):
        stage, code = "ocr", error.code
    elif isinstance(error, (GroqError, GeminiError)):
        stage, code = "fields", error.code
    else:
        stage, code = "fields", "invalid_result"
    return {"status": "failed", "failure_stage": stage, "failure_code": code, "values": {}}


def save(
    identity: Identity,
    data: SampleInput,
    content: bytes | None,
    sample: UUID,
    expected: int,
    *,
    run_ocr: bool = False,
    filename: str = "",
) -> None:
    """新增／替換／修改答案，版本快照與異動事件同交易保存。"""
    if expected == 0 and data.master_supplier_id is None:
        raise ValueError("請先從供應商管理選取正式供應商，再新增範例")
    validate_truths(data)
    action = Action.CREATE if expected == 0 else Action.EDIT
    master_id = data.master_supplier_id
    supplier = master_id
    # 在上傳前檢查修改目標及版本，避免明顯過期請求產生孤兒來源。
    if expected:
        before = detail(identity, sample)
        bound = before["payload"].get("master_supplier_id")
        if before["version"] != expected or bound != (str(master_id) if master_id else None):
            raise ConflictError
        if master_id is None:
            supplier = UUID(before["payload"]["supplier_id"])
            if (data.tax_id, data.supplier_name) != (
                before["payload"]["tax_id"],
                before["payload"]["supplier_name"],
            ):
                raise ValueError("歷史範例的供應商身分不可變更，請從正式供應商新增範例")
        try:
            with open_session() as db:
                _managed_row(db, identity, sample, action)
        except repository.StoreError:
            raise UnavailableError from None
    if master_id:
        master_supplier(identity, master_id, action)
        try:
            with open_session() as db:
                if not repository.archive_writable(db):
                    raise UnavailableError
        except repository.StoreError:
            raise UnavailableError from None
    recognition = None
    reuse_original = run_ocr and expected > 0 and content is None
    if run_ocr:
        if reuse_original:
            content, _ = source(identity, sample, UUID(before["payload"]["revisions"][-1]["id"]))
        elif content is None:
            raise ValueError("請先選擇要辨識的圖片")
        mime, _ = image_info(content)
        try:
            with open_session() as db:
                authorize(db, identity, action)
        except repository.StoreError:
            raise UnavailableError from None
        try:
            recognition = intake.extract_candidates(content, mime)
        except (DocumentAIError, GroqError, GeminiError, ValueError) as error:
            recognition = _recognition_failure(error)
        recognition["source_sha256"] = sha256(content)
        data = data.model_copy(update={"truths": [], "key_fields": []})
    uploaded_source = (
        upload(identity, supplier, content, action, sample)
        if content is not None and not reuse_original
        else None
    )
    if uploaded_source and filename:
        basename = filename.replace("\\", "/").rsplit("/", 1)[-1].replace("\x00", "").strip()
        uploaded_source["filename"] = basename[:255] or uploaded_source["filename"]
    try:
        with open_session() as db:
            row = None
            if expected:
                row = _managed_row(db, identity, sample, action, lock=True)
            else:
                authorize(db, identity, action, lock=True, supplier_master=bool(master_id))
            if row and row["version"] != expected:
                raise ConflictError
            if row:
                _require_current_revision(db, sample, row)
            if master_id:
                master = _read_master(db, master_id)
                data = data.model_copy(
                    update={
                        "supplier_name": master["supplier_name"],
                        "tax_id": master["tax_id"] if master["country_code"] == "TW" else "",
                    }
                )
            payload = revise(
                row["payload"] if row else None, data, uploaded_source, identity.user_id
            )
            if recognition:
                payload["revisions"][-1]["recognition"] = recognition
            payload["supplier_id"] = str(supplier)
            if master_id:
                payload["master_supplier_id"] = str(master_id)
                if data.tax_id:
                    revision = payload["revisions"][-1]
                    original = revision["source"]
                    if not original.get("size"):
                        raise ValueError("舊範例缺少原檔大小，請重新上傳原圖並核對")
                    header, items, _ = example_columns(revision)
                    revision["v2_example_version"] = repository.save_example(
                        db,
                        header
                        | {
                            "example_id": str(sample),
                            "supplier_id": str(master_id),
                            "seller_tax_id": data.tax_id,
                            "example_name": data.title,
                            "template_key": data.layout,
                            "bucket_name": original["bucket"],
                            "storage_path": original["key"],
                            "original_filename": original["filename"],
                            "mime_type": original["mime"],
                            "size_bytes": original["size"],
                            "content_sha256": original["sha256"],
                            "status": "candidate",
                            "verified_field_keys": [],
                            "verification_note": "",
                            "created_by": str(identity.user_id),
                            "updated_by": str(identity.user_id),
                        },
                        items,
                        row["payload"]["revisions"][-1].get("v2_example_version") if row else None,
                    )
                    for item in payload["revisions"][:-1]:
                        if item["status"] == "active":
                            item["status"] = "superseded"
                    payload["active_revision"] = None
            department = row["department_id"] if row else identity.selected_department_id
            repository.save(db, department, sample, payload, expected)
            repository.event(
                db,
                department,
                sample,
                identity.user_id,
                "create" if expected == 0 else "revise",
                data.reason,
                expected + 1,
                {
                    "actor_department_id": str(identity.selected_department_id),
                    "before_revision": row["payload"]["revisions"][-1]["id"] if row else None,
                    "after_revision": payload["revisions"][-1]["id"],
                    "recognition": recognition.get("status", "completed") if recognition else None,
                    "changed_fields": [
                        key
                        for key, value in data.model_dump(mode="json").items()
                        if row is None or row["payload"]["revisions"][-1].get(key) != value
                    ],
                },
            )
            if uploaded_source:
                repository.upload_state(db, uploaded_source["key"], "linked")
            db.commit()
    except repository.StoreError:
        raise UnavailableError from None


def transition(
    payload: dict[str, Any], command: str, actor: UUID, confirmed: bool
) -> dict[str, Any]:
    """啟用或退回最新修訂，也可停用整張範例；保留所有版本。"""
    result = deepcopy(payload)
    revision = result["revisions"][-1]
    if command == "activate":
        if not confirmed:
            raise ValueError("請明確確認原圖、供應商與逐欄答案")
        for item in revision["truths"]:
            if item["state"] == "unconfirmed" and item["text"].strip():
                item["state"] = "readable"
        if (
            (revision.get("master_supplier_id") or revision.get("recognition"))
            and revision["tax_id"]
            and not analysis.confirmed_seller(revision, revision["tax_id"])
        ):
            raise ValueError("請確認原圖賣方統編與所選供應商一致")
        validate_truths(
            SampleInput.model_validate(
                {k: revision[k] for k in SampleInput.model_fields if k in revision}
            ),
            activation=True,
        )
        if (
            revision.get("master_supplier_id")
            and not revision.get("v2_example_version")
            and not revision.get("key_fields")
        ):
            revision["key_fields"] = [
                item["path"]
                for item in revision["truths"]
                if item["state"] == "readable" and item["path"] in analysis.FIELDS
            ]
        for item in result["revisions"]:
            if item["status"] == "active":
                item["status"] = "superseded"
        revision.update(
            status="active", confirmed_by=str(actor), confirmed_at=datetime.now(UTC).isoformat()
        )
        result["active_revision"] = revision["id"]
    elif command == "return_for_correction":
        revision["status"] = "returned"
        if result["active_revision"] == revision["id"]:
            result["active_revision"] = None
    elif command == "deactivate":
        for item in result["revisions"]:
            if item["status"] == "active":
                item["status"] = "inactive"
        revision["status"] = "inactive"
        result["active_revision"] = None
    else:
        raise ValueError("未知操作")
    return result


def change_status(
    identity: Identity, sample: UUID, expected: int, command: str, reason: str, confirmed: bool
) -> UUID | None:
    """狀態與操作者事件同交易，使用獨立啟用管理功能授權。"""
    try:
        with open_session() as db:
            authorize(db, identity, Action.EDIT, activation=True, lock=True)
            row = _managed_row(db, identity, sample, Action.VIEW, lock=True)
            if row["version"] != expected:
                raise ConflictError
            _require_current_revision(db, sample, row)
            reason = reason.strip() or {"activate": "確認啟用範例", "deactivate": "停用範例"}.get(
                command, ""
            )
            if not reason or len(reason) > 500:
                raise ValueError("請填寫 1～500 字操作原因")
            payload = transition(row["payload"], command, identity.user_id, confirmed)
            revision = payload["revisions"][-1]
            v2_version = revision.get("v2_example_version")
            if (
                command == "activate"
                and revision.get("master_supplier_id")
                and revision["tax_id"]
                and not v2_version
            ):
                raise ValueError("請重新上傳舊範例的原圖並核對，以建立 v2.0 版本")
            if v2_version:
                keys: list[str] = []
                if command == "activate":
                    master = _read_master(db, UUID(revision["master_supplier_id"]))
                    if not master["is_active"] or master["tax_id"] != revision["tax_id"]:
                        raise ValueError("供應商已停用或統編不符，請先核對供應商主檔")
                    header, _, mapped = example_columns(revision)
                    repository.sync_example_invoice_identity(db, sample, v2_version, header)
                    selected = revision["key_fields"] or list(mapped)
                    if any(key not in mapped for key in selected):
                        raise ValueError("選定的關鍵欄位格式不符，請修正後重新驗證")
                    selected = list(dict.fromkeys([*selected, "seller_tax_id"]))
                    revision["key_fields"] = selected
                    keys = [column for path in selected for column in mapped[path]]
                    revision["v2_verified_field_keys"] = keys
                repository.set_example_status(
                    db,
                    sample,
                    v2_version,
                    {
                        "activate": "verified",
                        "return_for_correction": "revoked",
                        "deactivate": "retired",
                    }[command],
                    identity.user_id,
                    reason,
                    keys,
                )
            department = row["department_id"]
            repository.save(db, department, sample, payload, expected)
            repository.event(
                db,
                department,
                sample,
                identity.user_id,
                command,
                reason,
                expected + 1,
                {
                    "actor_department_id": str(identity.selected_department_id),
                    "revision": payload["revisions"][-1]["id"],
                    "before_status": row["payload"]["revisions"][-1]["status"],
                    "after_status": payload["revisions"][-1]["status"],
                    "before": row["payload"]["active_revision"],
                    "after": payload["active_revision"],
                },
            )
            db.commit()
            master_supplier_id = row["payload"].get("master_supplier_id")
            return UUID(master_supplier_id) if master_supplier_id else None
    except repository.StoreError:
        raise UnavailableError from None


def source(identity: Identity, sample: UUID, revision: UUID) -> tuple[bytes, str]:
    """僅按已驗權的歷史版本取得原件，不接受任意物件鍵或 URL。"""
    row = current_detail(identity, sample)
    item = next(
        (r for r in row["payload"]["revisions"] if str(revision) in (r["id"], r.get("v2_id"))), None
    )
    if item is None:
        raise MissingError
    try:
        data, _ = S3StorageClient.from_settings(bucket=item["source"]["bucket"]).get(
            item["source"]["key"]
        )
        if sha256(data) != item["source"]["sha256"]:
            raise UnavailableError
        return data, item["source"]["mime"]
    except StorageError:
        raise UnavailableError from None


def test_file(identity: Identity, sample: UUID, test: UUID) -> tuple[bytes, str]:
    """重新驗權後讀測試原件，保留固定參考版本。"""
    try:
        with open_session() as db:
            authorize(db, identity, Action.VIEW)
            ref = repository.test_source(db, identity.selected_department_id, sample, test)
        content, _ = S3StorageClient.from_settings(bucket=ref["bucket"]).get(ref["key"])
        if sha256(content) != ref["sha256"]:
            raise UnavailableError
        return content, ref["mime"]
    except (repository.StoreError, StorageError):
        raise UnavailableError from None


def test_review(identity: Identity, sample: UUID, expected: int, content: bytes, note: str) -> UUID:
    """只使用啟用版本保存測試意見與來源，重新驗證範例資格。"""
    if len(note) > 2000:
        raise ValueError("核對意見最多 2000 字")
    row = detail(identity, sample)
    if row["version"] != expected or not row["payload"]["active_revision"]:
        raise ConflictError
    source_ref = upload(
        identity, UUID(row["payload"]["supplier_id"]), content, Action.CREATE, sample
    )
    try:
        with open_session() as db:
            current = _managed_row(db, identity, sample, Action.CREATE, lock=True)
            revision = current["payload"]["active_revision"]
            if current["version"] != expected or not revision:
                raise ConflictError
            test_id = repository.save_test(
                db,
                identity.selected_department_id,
                identity.user_id,
                sample,
                UUID(revision),
                source_ref,
                note,
            )
            repository.upload_state(db, source_ref["key"], "linked")
            repository.event(
                db,
                identity.selected_department_id,
                sample,
                identity.user_id,
                "test",
                "測試發票核對",
                expected,
                {"test_id": str(test_id), "revision": revision, "note": note},
            )
            db.commit()
            return test_id
    except repository.StoreError:
        raise UnavailableError from None


def analyze_test(
    identity: Identity,
    sample: UUID,
    test: UUID,
    content: bytes,
    invoice_date: date,
    copy_type: str,
    gold: dict[str, str],
) -> dict[str, Any]:
    """已保存的測試才可辨識；選樣前及保存報告前重驗權與啟用版本。"""
    row = detail(identity, sample)
    active = row["payload"]["active_revision"]
    if not active:
        raise ConflictError
    revision = next(r for r in row["payload"]["revisions"] if r["id"] == active)
    if not revision["tax_id"]:
        unavailable = {"status": "not_evaluated", "reason": "此供應商未使用台灣統編比對"}
        return {
            "status": "skipped",
            "reason": unavailable["reason"],
            "rules": [],
            "baseline_metrics": unavailable,
            "enhanced_metrics": unavailable,
        }
    try:
        with open_session() as db:
            _managed_row(db, identity, sample, Action.CREATE)
            source_ref = repository.test_source(db, identity.selected_department_id, sample, test)
            if source_ref["sha256"] != sha256(content):
                raise ConflictError
            candidates = []
            for candidate in repository.reference_candidates(
                db,
                revision["tax_id"],
                master_supplier_id=row["payload"].get("master_supplier_id", ""),
            ):
                current = candidate["revision"]
                if current["source"]["sha256"] == source_ref["sha256"] or current["source"][
                    "mime"
                ] not in {"image/jpeg", "image/png"}:
                    continue
                if (
                    current.get("source_file_id") or current.get("source_recognition_id")
                ) and not repository.linked_source_matches(
                    db, candidate["sample_id"], current["v2_example_version"]
                ):
                    continue
                try:
                    snapshot = repository.read(
                        db,
                        identity.selected_department_id,
                        candidate["sample_id"],
                        shared_master=True,
                    )["payload"]["revisions"][-1]
                except MissingError:
                    pass
                else:
                    if (
                        snapshot.get("v2_example_version") == current["v2_example_version"]
                        and snapshot["source"]["sha256"] == current["source"]["sha256"]
                    ):
                        current.update(
                            {
                                key: snapshot.get(key)
                                for key in ("copy_type", "valid_from", "valid_to")
                            }
                        )
                candidates.append(candidate)
            selected = analysis.select_examples(
                candidates,
                revision["tax_id"],
                invoice_date,
                revision["layout"],
                copy_type,
            )
            duplicate = repository.duplicate_test_image(
                db, identity.selected_department_id, test, sha256(content)
            )
        examples = []
        for item in selected:
            image, _ = reference_file(identity, item["sample_id"], UUID(item["revision"]["id"]))
            examples.append(
                analysis.verified_example(str(item["sample_id"]), item["revision"], image)
            )
        report: dict[str, Any] = {
            "status": "skipped",
            "reason": "沒有符合聯別、期間且已確認關鍵欄位的啟用範例",
        }
        if examples:
            try:
                report = analysis.recognize(
                    content,
                    source_ref["mime"],
                    str(test),
                    examples,
                    revision["tax_id"],
                    template_key=revision["layout"],
                )
                report["status"] = report["enhanced"]["status"]
            except (DocumentAIError, FewShotError, GeminiError) as exc:
                report = {"status": "failed", "reason": exc.code}
            except ValueError:
                report = {"status": "failed", "reason": "invalid_result"}
        values = report.get("candidate_values", {})
        report.update(
            test_id=str(test),
            target_sha256=sha256(content),
            invoice_date=invoice_date.isoformat(),
            copy_type=copy_type,
            selected_examples=[
                {
                    "sample_id": str(r["sample_id"]),
                    "revision_id": r["revision"]["id"],
                    "example_version": r["revision"].get("v2_example_version"),
                    "sha256": r["revision"]["source"]["sha256"],
                }
                for r in selected
            ],
            rules=analysis.rules(values, duplicate),
            gold=gold,
            baseline_metrics=analysis.metrics(report.get("baseline", {}), gold)
            if report.get("baseline_available")
            else {"status": "not_evaluated", "reason": "處理器沒有可比較的結構化基線欄位"},
            enhanced_metrics=analysis.metrics(values, gold)
            if report.get("status") == "completed"
            else {"status": "not_evaluated", "reason": "範例辨識尚未完成"},
        )
        with open_session() as db:
            current = _managed_row(db, identity, sample, Action.CREATE, lock=True)
            if current["version"] != row["version"]:
                raise ConflictError
            for item in selected:
                repository.reference_revision(db, item["sample_id"], UUID(item["revision"]["id"]))
            repository.event(
                db,
                identity.selected_department_id,
                sample,
                identity.user_id,
                "ocr_test",
                "測試 OCR 與範例效果核對",
                row["version"],
                report,
            )
            db.commit()
        return report
    except repository.StoreError:
        raise UnavailableError from None
