"""模組名稱：app.routers.intake
功能說明：ADMIN 發票上傳流程（W2／W3）——上傳 → AI 辨識 → 核對（14 項欄位與多筆明細）→ 檢核 → 提交。
路由：/intake
注意事項：
    - 功能代碼 inv.intake 已註冊在 get_e_module() 的功能目錄（VIEW／CREATE）；
      「誰能新增」仍由 E 組在 department_permissions 授予 can_create，這裡不假設任何部門。
      app_functions 需有 function_code = inv.intake 的列，未授權的部門仍回 403。
    - 呼叫 API 使用登入時換發的使用者自己的 Session Token（方案 A）；沒有 Token 時 401，請重新登入。
    - 2026-11-03：GET /intake 改為轉入發票作業流程（/invoices/new）；POST /intake/recognize、/review 暫留，
      待測試遷移後移除。辨識與核對的主要入口是 /invoices/{id}/flow。
    - 辨識、檢核與提交全部呼叫既有 API；本路由不寫入資料庫、不呼叫任何 AI、不複製檢核規則。
    - 操作者一律取自已驗證身分；表單中的使用者或部門欄位不採用。
    - 提交成功後的案件狀態由 API 決定（以 API 的 SUBMITTED_STATUS 為準，ADMIN 不寫死）；後續審核狀態轉換尚未實作。
"""

import base64
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.constants import API_TOKEN_COOKIE_NAME
from app.core.templating import templates
from app.routers.e_admin import require_e_origin, session_evidence
from app.schemas.case import CASE_STATUS_LABELS
from app.schemas.e_admin import Action, Identity, Target
from app.schemas.intake import (
    IntakeApiError,
    IntakeError,
    IntakeFileError,
    IntakeForm,
    IntakeNotConfiguredError,
    IntakeTransportError,
    RecognitionView,
)
from app.services import invoice_intake_mapping as mapping
from app.services import invoice_intake_service as intake_service
from app.services.e_admin_service import EModule, get_e_module
from app.services.invoice_intake_service import ApiIntakeClient

INTAKE = "inv.intake"  # 與 e_admin_service.INTAKE 相同；授權仍依部門的 can_create
Module = Annotated[EModule, Depends(get_e_module)]
NOT_CONFIRMED = "尚未確認是否已送出，請先到案件列表確認；若沒有這筆案件可再按一次「確認提交」，系統會防止重複建立。"


def get_intake_client(request: Request) -> ApiIntakeClient:
    """FastAPI 相依：以登入時向 API 換發的 Session Token 建立用戶端；沒有 Token 時回 401 要求重新登入。

    測試以 dependency_overrides 注入替身。
    """
    try:
        return intake_service.build_intake_client(request.cookies.get(API_TOKEN_COOKIE_NAME))
    except IntakeNotConfiguredError:
        raise HTTPException(status_code=401, detail="請重新登入後再試") from None


Client = Annotated[ApiIntakeClient, Depends(get_intake_client)]


def require_intake_access(request: Request, module: Module) -> None:
    """驗證登入與「新增發票」操作權限（目前部門）；功能未註冊或未授權一律拒絕。"""
    identity = module.identity(session_evidence(request))
    module.check(identity, INTAKE, Action.CREATE, Target(identity.selected_department_id))
    request.state.e_configuration_authenticated = True
    request.state.e_configuration_menu = module.sidebar_menu(identity)
    request.state.header_departments = module.departments(identity)
    request.state.header_selected_department_id = identity.selected_department_id
    request.state.intake_identity = identity


router = APIRouter(prefix="/intake", tags=["intake"], dependencies=[Depends(require_intake_access)])


def _identity(request: Request) -> Identity:
    identity: Identity = request.state.intake_identity
    return identity


def _safe_form(data: object) -> IntakeForm:
    """錯誤頁重新顯示用：表單本身解析失敗（例如明細過多）時退回只保留 recognition_id 的空表單。"""
    try:
        return mapping.form_from_post(data)  # type: ignore[arg-type]
    except IntakeError:
        recognition_id = data.get("recognition_id", "") if hasattr(data, "get") else ""
        return IntakeForm(recognition_id=mapping.clean_code(recognition_id))


def _review(
    request: Request,
    form: IntakeForm,
    *,
    banner: str = "",
    errors: dict[str, str] | None = None,
    warnings: dict[str, list[str]] | None = None,
    missing: dict[str, str] | None = None,
    duplicate: bool | None = None,
    recognition: RecognitionView | None = None,
    original: str | None = None,
    original_note: str = "",
    checked: bool = False,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "pages/intake/review.html",
        {
            "form": form,
            "banner": banner,
            "errors": errors or {},
            "warnings": warnings or {},
            "missing": missing or {},
            "duplicate": duplicate,
            "recognition": recognition,
            "zh_suggestion_note": mapping.zh_suggestion_note(recognition, form),
            "original": original,
            "original_note": original_note,
            "checked": checked,
            "rows": mapping.FIELD_ROWS,
            "tax_type_options": mapping.TAX_TYPE_OPTIONS,
            "max_items": mapping.MAX_ITEMS,
        },
        status_code=status_code,
    )


def _upload_page(request: Request, message: str = "", status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "pages/intake/upload.html", {"message": message}, status_code=status_code
    )


def api_message(error: IntakeApiError) -> str:
    """給使用者的訊息：401／403 與 5xx 用固定文字，其餘沿用 API 的業務訊息（長度已限制）。"""
    if error.status in (401, 403):
        return "授權失敗，請重新登入或聯絡管理員"
    if error.status >= 500:
        return "發票服務暫時發生錯誤，請稍後再試"
    return error.public_message


_INLINE_IMAGE_TYPES = frozenset({"image/jpeg", "image/png"})


def _original_view(content: bytes, content_type: str | None) -> tuple[str | None, str]:
    """辨識完成當下附上原始影像（data URI）；PDF 無法用 img 內嵌，改給說明。影像不寫入伺服器。"""
    if content_type in _INLINE_IMAGE_TYPES:
        return f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}", ""
    return None, "PDF 檔案無法在此頁內嵌顯示，請另開原始檔對照。"


@router.get("", response_class=Response)
def intake_upload_page() -> RedirectResponse:
    """合併入口：上傳與核對統一在發票作業流程（步驟 1～4）進行。"""
    return RedirectResponse("/invoices/new", status_code=303)


@router.post("/recognize", response_class=HTMLResponse, dependencies=[Depends(require_e_origin)])
async def intake_recognize(
    request: Request, client: Client, file: Annotated[UploadFile | None, File()] = None
) -> HTMLResponse:
    """步驟 2：送 API 辨識，成功後顯示預填的核對頁。"""
    if file is None or not file.filename:
        return _upload_page(request, "請先選擇發票檔案", 400)
    content = await file.read(intake_service.MAX_UPLOAD_BYTES + 1)
    try:
        recognition, form = intake_service.recognize(
            client,
            content=content,
            filename=file.filename,
            content_type=file.content_type or "application/octet-stream",
        )
    except IntakeFileError as error:
        return _upload_page(request, error.public_message, 400)
    except IntakeNotConfiguredError:
        raise HTTPException(status_code=503, detail="功能尚未啟用") from None
    except IntakeApiError as error:
        return _upload_page(request, api_message(error), 502 if error.status >= 500 else 400)
    except IntakeTransportError as error:
        return _upload_page(request, error.public_message, 502)
    original, original_note = _original_view(content, file.content_type)
    return _review(
        request, form, recognition=recognition, original=original, original_note=original_note
    )


@router.post("/review", response_class=HTMLResponse, dependencies=[Depends(require_e_origin)])
async def intake_review(request: Request, client: Client) -> Response:
    """步驟 3～5：新增／刪除明細、檢核、提交。表單以 action 區分動作；沒有任何伺服器端草稿。"""
    data = await request.form()
    action = str(data.get("action", ""))
    try:
        form = mapping.form_from_post(data)
        if action == "add_item":
            return _review(request, mapping.with_blank_item(form))
        if action.startswith("remove_item:"):
            index = int(action.split(":", 1)[1]) if action.split(":", 1)[1].isdigit() else -1
            return _review(request, mapping.without_item(form, index))
        if action == "check":
            outcome = intake_service.check(client, form)
            return _review(
                request,
                form,
                warnings=outcome.warnings,
                missing=outcome.missing,
                duplicate=outcome.duplicate,
                checked=True,
            )
        if action == "submit":
            return _submit(request, client, form)
        raise HTTPException(status_code=400, detail="未知的動作")
    except IntakeNotConfiguredError:
        raise HTTPException(status_code=503, detail="功能尚未啟用") from None
    except IntakeApiError as error:
        return _review(
            request,
            _safe_form(data),
            banner=api_message(error),
            errors=mapping.field_errors(error.details),
            status_code=422 if error.status == 422 else 502 if error.status >= 500 else 400,
        )
    except IntakeTransportError as error:
        return _review(request, _safe_form(data), banner=error.public_message, status_code=502)
    except IntakeError as error:
        return _review(request, _safe_form(data), banner=error.public_message, status_code=400)


def _submit(request: Request, client: ApiIntakeClient, form: IntakeForm) -> Response:
    try:
        result = intake_service.submit(client, form)
    except IntakeTransportError as error:
        if not error.ambiguous:
            raise
        existing = intake_service.confirm_submitted(form.recognition_id, _identity(request).user_id)
        if existing is None:
            return _review(request, form, banner=NOT_CONFIRMED, status_code=502)
        return _done(request, existing, None, recovered=True)
    except IntakeApiError as error:
        if error.code == "RECOGNITION_ALREADY_SUBMITTED":
            existing = intake_service.confirm_submitted(
                form.recognition_id, _identity(request).user_id
            )
            if existing is not None:
                return _done(request, existing, None, recovered=True)
        raise
    return _done(request, result.invoice_id, result)


def _done(
    request: Request, invoice_id: UUID, result: object | None, *, recovered: bool = False
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "pages/intake/done.html",
        {
            "invoice_id": invoice_id,
            "result": result,
            "recovered": recovered,
            "status_labels": CASE_STATUS_LABELS,
        },
    )


@router.get("/done", include_in_schema=False)
def intake_done_redirect() -> RedirectResponse:
    return RedirectResponse("/intake", status_code=303)
