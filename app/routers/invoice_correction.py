"""模組名稱：app.routers.invoice_correction
功能說明：採購在 ADMIN 補正被財務退回的資料庫案件，不跳轉前台。
路由：GET /invoices/{id}/correct（補正頁）、POST /invoices/{id}/correct（正式重送）、POST /invoices/{id}/correct/void（終止）
注意事項：
    - 只處理資料庫案件（UUID 且存在於資料庫）；示範發票與草稿一律 404，不會誤用補正 API。
    - 只有原提交者能進入；非本人一律 404（不洩漏案件是否存在）。真正的權限、欄位白名單、輪次、冪等與圖片退件規則
      由 API 判斷並保存（return-items／resubmit／void），這裡不複製。
    - 以登入時換發的使用者自己的 API Session Token 呼叫；沒有 Token 回 401，要求重新登入。
    - 重送與終止逾時、結果不明時回 ambiguous=true，瀏覽器必須用同一把冪等鍵重試，不得換鍵。
    - 終止統一呼叫 API /void，不直接寫資料庫；終止成功不代表已建立新案件，重新上傳需另外完成。
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.core.templating import templates
from app.routers.e_admin import require_e_origin
from app.routers.intake import get_intake_client
from app.routers.invoices import require_invoice_access
from app.schemas.case import CASE_STATUS_LABELS
from app.schemas.intake import IntakeApiError, IntakeError, IntakeTransportError
from app.schemas.invoice_correction import CorrectionSubmit, CorrectionVoid
from app.services import invoice_correction_service as service
from app.services import invoice_review_service
from app.services.invoice_intake_service import ApiIntakeClient

router = APIRouter(
    prefix="/invoices", tags=["invoice-correction"], dependencies=[Depends(require_invoice_access)]
)

_RELOGIN = "請重新登入後再試"


def _own_db_invoice(request: Request, invoice_id: str) -> dict[str, Any]:
    """本人提交、存在於資料庫的案件；其餘（示範、草稿、他人、不存在）一律 404。"""
    try:
        normalized = str(UUID(str(invoice_id)))
    except ValueError:
        raise HTTPException(status_code=404, detail="找不到指定發票") from None
    invoice = invoice_review_service.fetch_invoice(normalized)
    owner = str(getattr(request.state, "invoice_user_id", ""))
    if not invoice or str(invoice.get("uploaded_by", "")) != owner:
        raise HTTPException(status_code=404, detail="找不到指定發票")
    return invoice


def _http_status(error: IntakeApiError) -> int:
    return 502 if error.status >= 500 else error.status


def _json_error(error: IntakeError) -> JSONResponse:
    """瀏覽器用的 JSON 錯誤：訊息已轉繁體中文；ambiguous 表示 API 可能已處理，需用同一把鍵重試。"""
    if isinstance(error, IntakeApiError):
        body = {"error": {"code": error.code, "message": service.error_message(error)}}
        return JSONResponse(body, status_code=_http_status(error))
    ambiguous = isinstance(error, IntakeTransportError) and error.ambiguous
    body = {
        "error": {"code": "UPSTREAM_UNAVAILABLE", "message": error.public_message},
        "ambiguous": ambiguous,
    }
    return JSONResponse(body, status_code=502)


def _json_client(request: Request) -> ApiIntakeClient | JSONResponse:
    try:
        return get_intake_client(request)
    except HTTPException:
        return JSONResponse(
            {"error": {"code": "LOGIN_REQUIRED", "message": _RELOGIN}}, status_code=401
        )


def _render(
    request: Request,
    invoice: dict[str, Any],
    page: service.CorrectionPage | None,
    *,
    error: str = "",
    status_code: int = 200,
) -> HTMLResponse:
    request.state.active_sidebar_route = "/invoices"
    pane_invoice = {
        **invoice,
        "file_bytes": True,
    }  # 原圖窗格只判斷有無檔案；影像由 /invoices/{id}/file 提供
    return templates.TemplateResponse(
        request,
        "pages/invoices/correct.html",
        {
            "invoice": pane_invoice,
            "invoice_id": str(invoice["id"]),
            "page": page,
            "previous": page.info.previous if page else None,
            "error": error,
            "header_fields": service.HEADER_FIELDS,
            "item_fields": service.ITEM_FIELDS,
            "tax_type_options": service.TAX_TYPE_OPTIONS,
            "labels": service.FIELD_LABELS_VIEW,
            "step": 3,
        },
        status_code=status_code,
    )


@router.get("/{invoice_id}/correct", response_class=HTMLResponse)
async def correction_page(request: Request, invoice_id: str) -> Response:
    """補正頁：顯示原圖、上一輪提交值、財務退回原因；只有 API 回傳的 editable_fields 可編輯。"""
    invoice = _own_db_invoice(request, invoice_id)
    client = get_intake_client(request)
    try:
        page = await run_in_threadpool(service.load_page, client, str(invoice["id"]))
    except IntakeApiError as error:
        if error.status == 404:
            raise HTTPException(status_code=404, detail="找不到指定發票") from None
        status = 409 if error.code == "INVOICE_NOT_RETURNED" else _http_status(error)
        return _render(
            request, invoice, None, error=service.error_message(error), status_code=status
        )
    except IntakeError as error:
        return _render(request, invoice, None, error=error.public_message, status_code=502)
    return _render(request, invoice, page)


@router.post("/{invoice_id}/correct", dependencies=[Depends(require_e_origin)], response_model=None)
async def correction_submit(
    request: Request, invoice_id: str, payload: CorrectionSubmit
) -> JSONResponse:
    """正式重送：原樣轉送欄位、編輯事件與逐項回覆給 API；規則與歷程由 API 處理。"""
    invoice = _own_db_invoice(request, invoice_id)
    client = _json_client(request)
    if isinstance(client, JSONResponse):
        return client
    try:
        result = await run_in_threadpool(service.submit, client, str(invoice["id"]), payload)
    except IntakeError as error:
        return _json_error(error)
    return JSONResponse(
        {
            "invoice_id": str(result.invoice_id),
            "status": result.status,
            "status_label": CASE_STATUS_LABELS.get(result.status, result.status),
            "submission_round": result.submission_round,
            "tax_period": result.tax_period,
            "replayed": result.replayed,
            "warnings": [w.message for w in result.warnings],
            "modified_count": len(result.modified_fields),
            "rereview_count": len(result.rereview_fields),
            "redirect": "/invoices",
        }
    )


@router.post(
    "/{invoice_id}/correct/void", dependencies=[Depends(require_e_origin)], response_model=None
)
async def correction_void(
    request: Request, invoice_id: str, payload: CorrectionVoid
) -> JSONResponse:
    """終止被退回的案件（圖片退件的必經步驟）：呼叫 API /void；不代表已建立新案件。"""
    invoice = _own_db_invoice(request, invoice_id)
    client = _json_client(request)
    if isinstance(client, JSONResponse):
        return client
    try:
        result = await run_in_threadpool(service.void, client, str(invoice["id"]), payload)
    except IntakeError as error:
        return _json_error(error)
    return JSONResponse(
        {
            "invoice_id": str(result.invoice_id),
            "status": result.status,
            "status_label": CASE_STATUS_LABELS.get(result.status, result.status),
            "submission_round": result.submission_round,
            "upload_url": "/invoices/new",
        }
    )
