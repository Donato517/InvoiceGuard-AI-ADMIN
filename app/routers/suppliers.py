"""模組名稱：app.routers.suppliers。
功能說明：獨立模擬供應商畫面；不掛載到正式 app.main。
路由：GET/POST /suppliers，GET /suppliers/new、/{id}、/{id}/edit，POST /{id}、/{id}/status。
"""

from secrets import compare_digest

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.schemas.supplier import (
    CREDIT_RATINGS,
    PAYMENT_BASES,
    PAYMENT_METHODS,
    PAYMENT_TERMS,
    SupplierInput,
)
from app.services.supplier_service import (
    DEMO_ACTIONS,
    FUNCTION_SELECTIONS,
    SUPPLIER_FUNCTIONS,
    SupplierError,
    SupplierStorageError,
)

router = APIRouter(prefix="/suppliers", tags=["supplier-demo"])
DEFAULTS = {
    "country_code": "TW",
    "currency": "TWD",
    "payment_terms": "net",
    "payment_basis": "invoice",
    "payment_days": "30",
    "payment_method": "transfer",
    "credit_rating": "unassessed",
}
MESSAGES = {
    "functions-saved": "已儲存此供應商的模擬功能設定；尚未套用實際操作權限",
    "created": "已新增模擬供應商",
    "saved": "已儲存模擬資料",
    "enabled": "已啟用模擬供應商",
    "disabled": "已停用模擬供應商，資料仍保留",
}
FIELD_LABELS = {
    "supplier_name": "供應商名稱",
    "country_code": "國家／地區代碼",
    "tax_id": "統編",
    "aliases": "別名",
    "contact_name": "聯絡人",
    "phone": "電話",
    "email": "Email",
    "address": "地址",
    "payment_terms": "付款條件",
    "payment_basis": "起算基準",
    "payment_days": "付款天數",
    "payment_method": "付款方式",
    "currency": "幣別",
    "notes": "備註",
    "credit_rating": "供應商徵信等級",
    "delivery_terms": "交貨條件",
}


def render(request: Request, page: str, status: int = 200, **context: object) -> HTMLResponse:
    """沿用樣板環境，提供有限的顯示選項。"""
    if page == "detail":
        try:
            context["function_settings"] = request.app.state.suppliers.get_function_settings(
                context["supplier"]["supplier_id"]
            )
        except SupplierStorageError as error:
            raise HTTPException(503, "本機功能設定暫時無法讀取") from error
        context["supplier_functions"] = SUPPLIER_FUNCTIONS
        context["function_actions"] = DEMO_ACTIONS
    return request.app.state.templates.TemplateResponse(
        request,
        "pages/suppliers/" + page + ".html",
        {
            "csrf": request.app.state.csrf,
            "ratings": CREDIT_RATINGS,
            "terms": PAYMENT_TERMS,
            "bases": PAYMENT_BASES,
            "methods": PAYMENT_METHODS,
            **context,
        },
        status_code=status,
    )


def get_supplier(request: Request, supplier_id: str) -> dict:
    """把查無資料轉成 404。"""
    try:
        return request.app.state.suppliers.get(supplier_id)
    except SupplierError as error:
        raise HTTPException(404, str(error)) from error


async def checked_form(request: Request) -> dict:
    """驗證試作表單 token；拒絕跨站提交。"""
    form = dict(await request.form())
    token = form.get("csrf", "")
    if not isinstance(token, str) or not compare_digest(token, request.app.state.csrf):
        raise HTTPException(403, "表單已過期，請重新整理畫面後再操作")
    return form


@router.get("", response_class=HTMLResponse)
def supplier_list(request: Request, q: str = "") -> HTMLResponse:
    """顯示模擬列表及字面搜尋。"""
    q = q[:200]
    return render(request, "list", suppliers=request.app.state.suppliers.list(q), q=q)


@router.get("/new", response_class=HTMLResponse)
def supplier_new(request: Request) -> HTMLResponse:
    """新增表單。"""
    return render(request, "form", values=DEFAULTS, supplier=None, errors=[])


@router.get("/department-functions")
def supplier_department_functions() -> RedirectResponse:
    """移除誤加的部門頁，舊書籤返回供應商列表。"""
    return RedirectResponse("/suppliers", status_code=303)


@router.post("/department-functions", response_model=None)
async def supplier_department_functions_save(request: Request) -> HTMLResponse | RedirectResponse:
    """舊表單不再接受寫入。"""
    raise HTTPException(410, "部門設定頁已移除，請從供應商詳情開啟功能列表")


@router.post("/{supplier_id}/function-settings", response_model=None)
async def supplier_function_settings_save(
    request: Request, supplier_id: str
) -> HTMLResponse | RedirectResponse:
    """保存此供應商的功能列表，拒絕未知操作、重複欄位及舊版本。"""
    supplier = get_supplier(request, supplier_id)
    form = await checked_form(request)
    original = await request.form()
    if set(form) - {"csrf", "functions_updated_at", *FUNCTION_SELECTIONS} or any(
        len(original.getlist(key)) != 1 for key in original
    ):
        raise HTTPException(400, "功能設定包含未知或重複欄位")
    try:
        selections = frozenset(key for key in FUNCTION_SELECTIONS if key in form)
        if any(form[key] != "1" for key in selections):
            raise SupplierError("無效的功能勾選值")
        request.app.state.suppliers.save_function_settings(
            supplier_id, selections, str(form.get("functions_updated_at", ""))
        )
    except SupplierError as error:
        return render(request, "detail", 400, supplier=supplier, message="", error=str(error))
    except SupplierStorageError:
        raise HTTPException(503, "本機模擬設定暫時無法儲存，請稍後再試") from None
    return RedirectResponse(
        f"/suppliers/{supplier_id}?result=functions-saved#supplier-functions", 303
    )


@router.get("/{supplier_id}", response_class=HTMLResponse)
def supplier_detail(request: Request, supplier_id: str, result: str = "") -> HTMLResponse:
    """查看完整資料及啟停確認表單。"""
    return render(
        request,
        "detail",
        supplier=get_supplier(request, supplier_id),
        message=MESSAGES.get(result, ""),
        error="",
    )


@router.get("/{supplier_id}/edit", response_class=HTMLResponse)
def supplier_edit(request: Request, supplier_id: str) -> HTMLResponse:
    """修改畫面保留原資料版本。"""
    supplier = get_supplier(request, supplier_id)
    return render(request, "form", supplier=supplier, values=supplier, errors=[])


async def save_form(
    request: Request, supplier_id: str | None = None
) -> HTMLResponse | RedirectResponse:
    """處理新增及修改；驗證失敗保留已輸入欄位。"""
    supplier = get_supplier(request, supplier_id) if supplier_id else None
    form = await checked_form(request)
    values = {
        field: form.get(field, "unassessed" if field == "credit_rating" else "")
        for field in SupplierInput.model_fields
    }
    values["updated_at"] = form.get("updated_at", "")
    try:
        data = SupplierInput.model_validate({k: values[k] for k in SupplierInput.model_fields})
        result_id = request.app.state.suppliers.save(data, supplier_id, str(values["updated_at"]))
    except ValidationError as error:
        errors = []
        for issue in error.errors(include_input=False, include_url=False):
            if issue["type"] == "value_error":
                errors.append(str(issue["ctx"]["error"]))
            else:
                label = FIELD_LABELS.get(str(issue["loc"][0]), "欄位")
                errors.append(label + "：請填寫有效值，並符合欄位長度或數字範圍")
        return render(request, "form", 400, values=values, supplier=supplier, errors=errors)
    except SupplierError as error:
        return render(request, "form", 400, values=values, supplier=supplier, errors=[str(error)])
    except SupplierStorageError:
        return render(
            request,
            "form",
            503,
            values=values,
            supplier=supplier,
            errors=["本機模擬資料暫時無法儲存，請稍後重試"],
        )
    result = "saved" if supplier_id else "created"
    return RedirectResponse(f"/suppliers/{result_id}?result={result}", status_code=303)


@router.post("", response_model=None)
async def supplier_create(request: Request) -> HTMLResponse | RedirectResponse:
    """新增。"""
    return await save_form(request)


@router.post("/{supplier_id}", response_model=None)
async def supplier_update(request: Request, supplier_id: str) -> HTMLResponse | RedirectResponse:
    """儲存修改。"""
    return await save_form(request, supplier_id)


@router.post("/{supplier_id}/status", response_model=None)
async def supplier_status(request: Request, supplier_id: str) -> HTMLResponse | RedirectResponse:
    """停用須明確勾選確認；啟用／停用皆檢查版本。"""
    supplier = get_supplier(request, supplier_id)
    form = await checked_form(request)
    try:
        if form.get("active") not in {"0", "1"}:
            raise SupplierError("無效的狀態操作")
        active = form["active"] == "1"
        if not active and form.get("confirm") != "yes":
            raise SupplierError("請先勾選確認停用，再送出")
        request.app.state.suppliers.set_status(supplier_id, active, str(form.get("updated_at", "")))
    except SupplierError as error:
        return render(
            request,
            "detail",
            400,
            supplier=get_supplier(request, supplier_id),
            message="",
            error=str(error),
        )
    except SupplierStorageError:
        return render(
            request,
            "detail",
            503,
            supplier=supplier,
            message="",
            error="本機模擬資料暫時無法儲存，請稍後重試",
        )
    return RedirectResponse(
        f"/suppliers/{supplier_id}?result={'enabled' if active else 'disabled'}", status_code=303
    )
