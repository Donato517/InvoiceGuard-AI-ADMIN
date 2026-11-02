"""模組名稱：app.services.invoice_review_service
功能說明：發票人工複核與資料庫存取服務（讀取與儲存 public.invoices）。
相依模組：app.db.repositories.invoice_repository
注意事項：
    - 提供給 Router 層讀取與儲存發票之統一介面，維持分層架構（Router 不直接存取 DB）。
"""

from typing import Any

from app.db.repositories import invoice_repository as repository


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


def save_invoice(invoice: dict[str, Any], invoice_id: Any) -> None:
    """將發票儲存至資料庫。"""
    session = repository.try_get_db_session()
    if session is None:
        return
    try:
        repository.save_invoice(session, invoice, invoice_id)
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()
