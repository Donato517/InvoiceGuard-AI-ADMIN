"""模組名稱：app.services.invoice_review_service
功能說明：發票人工複核與資料庫存取服務（讀取與儲存 public.invoices，以及讀取儲存檔案與上傳者資訊）。
相依模組：app.db.repositories.invoice_repository、app.db.repositories.case_repository、app.integrations.supabase_storage
注意事項：
    - 提供給 Router 層讀取與儲存發票之統一介面，維持分層架構（Router 不直接存取 DB 與雲端整合）。
"""

from typing import Any
from uuid import UUID

from app.db.repositories import case_repository
from app.db.repositories import invoice_repository as repository
from app.integrations.supabase_storage import S3StorageClient


def fetch_invoices(
    user_id: str | None = None,
    is_finance: bool = True,
    status: str | None = None,
    q: str = "",
) -> list[dict[str, Any]] | None:
    """自資料庫取得發票清單；若資料庫未連線則回傳 None。"""
    session = repository.try_get_db_session()
    if session is None:
        return None
    try:
        return repository.list_invoices(
            session,
            user_id=user_id,
            is_finance=is_finance,
            status=status,
            q=q,
        )
    finally:
        session.close()


def fetch_invoice(invoice_id: Any) -> dict[str, Any] | None:
    """自資料庫依 ID 取得單筆發票；若資料庫未連線或不存在則回傳 None。"""
    session = repository.try_get_db_session()
    if session is None:
        return None
    try:
        return repository.get_invoice_by_id(session, invoice_id)
    finally:
        session.close()


def fetch_storage_file_bytes(invoice_id: Any) -> tuple[bytes, str] | None:
    """嘗試從 Supabase Storage 與資料庫讀取發票原始檔案內容與 MIME 類型。"""
    try:
        uuid_obj = UUID(str(invoice_id))
    except (ValueError, TypeError, AttributeError):
        return None
    session = None
    try:
        session = repository.try_get_db_session()
        if session is None:
            return None
        file_info = case_repository.get_current_file(session, uuid_obj)
        if file_info is None:
            return None
        _, location = file_info
        client = S3StorageClient.from_settings(bucket=location.bucket_name)
        content, _ = client.get(location.storage_path)
        mime = location.mime_type or "image/jpeg"
        return content, mime
    except Exception:
        return None
    finally:
        if session is not None:
            session.close()


def get_invoice_uploader_info(invoice_id: Any) -> tuple[str, str]:
    """取得發票的 (上傳部門, 上傳者)；失敗或查無資料回傳空字串元組。"""
    try:
        uuid_obj = UUID(str(invoice_id))
    except (ValueError, TypeError, AttributeError):
        return "", ""
    session = repository.try_get_db_session()
    if session is None:
        return "", ""
    try:
        case_header = case_repository.get_case(session, uuid_obj)
        if case_header is None:
            return "", ""
        return case_header.department_name or "", case_header.submitter_name or ""
    except Exception:
        return "", ""
    finally:
        session.close()
