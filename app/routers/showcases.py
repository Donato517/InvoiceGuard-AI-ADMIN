"""模組名稱：app.routers.showcases；功能說明：已登入的供應商範例管理與測試核對入口。"""

import re
from datetime import date
from urllib.parse import urlencode
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from app.core.templating import templates
from app.routers.e_admin import CurrentIdentity, Module, require_e_origin
from app.schemas.e_admin import DeniedError, EError, Identity, UnavailableError
from app.schemas.showcase import MAX_BYTES, MAX_TRUTH_FIELDS, SampleInput, SearchInput
from app.schemas.showcase_v2 import invoice_date as parse_invoice_date
from app.services import showcase_service as service

router = APIRouter(prefix="/showcases", tags=["供應商 Show Case"])
# 本輪僅支援台灣三聯式統一發票；其他國別仍可進入頁面，但顯示尚未支援。
SHOWCASE_SUPPORTED_COUNTRIES = frozenset({"TW"})
FIELD_LABELS = {
    "invoice_number": "發票字軌號碼",
    "invoice_date": "日期",
    "buyer_name": "買受人",
    "buyer_tax_id": "買方統編",
    "sales_amount": "銷售額",
    "tax_type": "課稅別",
    "tax_amount": "稅額",
    "total_amount": "總計",
    "total_amount_zh": "中文大寫金額",
    "seller_name": "銷貨人",
    "seller_tax_id": "賣方統編",
    "notes": "備註",
    "items.1.name": "第 1 列品名",
    "items.1.quantity": "第 1 列數量",
    "items.1.unit_price": "第 1 列單價",
    "items.1.amount": "第 1 列金額",
}

ITEM_LABELS = {"name": "品名", "quantity": "數量", "unit_price": "單價", "amount": "金額"}


def field_label(path: str) -> str:
    """將明細路徑轉為可辨識的列號與欄名。"""
    match = re.fullmatch(r"items\.([1-9][0-9]*)\.(name|quantity|unit_price|amount)", path)
    if match:
        return f"第 {match.group(1)} 列{ITEM_LABELS[match.group(2)]}"
    return FIELD_LABELS.get(path, "其他明細欄位")


def format_invoice_date(value: str) -> str:
    """僅對可解析日期顯示統一的民國年月日，無法解析時保留原文供核對。"""
    day = parse_invoice_date(value)
    if day is None or day.year < 1912:
        return value
    return f"{day.year - 1911}年{day.month}月{day.day}日"


def render(
    request: Request,
    module: Module,
    identity: Identity,
    values: dict[str, object],
    *,
    status_code: int = 200,
) -> HTMLResponse:
    """共用已登入後台版面，不建立匿名例外。"""
    data = values.get("data", {})
    truths = {item["path"]: item for item in data.get("truths", [])}
    original = data.get("recognition", {}).get("values", {})
    paths = list(dict.fromkeys([*FIELD_LABELS, *original, *truths]))
    item_order = {"name": 0, "quantity": 1, "unit_price": 2, "amount": 3}

    def item_sort_key(path: str) -> tuple[int, int, str]:
        match = re.fullmatch(r"items\.([1-9][0-9]*)\.([a-z_]+)", path)
        if match:
            return int(match.group(1)), item_order.get(match.group(2), 4), path
        return 10**9, 4, path

    item_paths = sorted((path for path in paths if path.startswith("items.")), key=item_sort_key)
    paths = [path for path in paths if path not in item_paths]
    paths[4:4] = item_paths
    fields = [
        {
            "path": path,
            "label": field_label(path),
            "state": truths.get(path, {}).get("state", "unconfirmed"),
            "text": format_invoice_date(truths.get(path, {}).get("text", original.get(path, "")))
            if path == "invoice_date"
            else truths.get(path, {}).get("text", original.get(path, "")),
            "original": original.get(path, ""),
            "modified": path in truths and truths[path]["text"] != original.get(path, ""),
        }
        for path in paths
    ]
    return templates.TemplateResponse(
        request,
        "pages/showcases/manage.html",
        {
            "identity": identity,
            "e_menu": module.sidebar_menu(identity),
            "effective_access": request.state.e_access,
            "fields": fields,
            "format_invoice_date": format_invoice_date,
            **values,
        },
        status_code=status_code,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("", response_class=HTMLResponse)
def showcase_list(
    request: Request,
    module: Module,
    identity: CurrentIdentity,
    q: str = "",
    page: int = 1,
    layout: str = "",
    writing: str = "",
    status: str = "all",
    kind: str = "all",
    supplier_id: str = "",
) -> HTMLResponse:
    """查詢本部門供應商範例，來源缺表明確回 503。"""
    try:
        filters = SearchInput(
            q=q,
            page=page,
            layout=layout,
            writing=writing,
            status=status,
            kind=kind,
            supplier_id=supplier_id or None,
        )
    except ValidationError:
        raise EError from None
    params = filters.model_dump(mode="json", exclude_none=True)
    return render(
        request,
        module,
        identity,
        {
            "items": service.query(identity, q, page, filters),
            "q": q,
            "page": page,
            "filters": filters,
            "previous_url": "/showcases?" + urlencode(params | {"page": max(1, page - 1)}),
            "next_url": "/showcases?" + urlencode(params | {"page": page + 1}),
        },
    )


@router.get("/suppliers")
def showcase_suppliers(identity: CurrentIdentity, q: str = "") -> list[dict[str, object]]:
    """查詢既有供應商供選取；結果由服務端限制資料範圍。"""
    return service.suppliers(identity, q)


@router.get("/collections", response_class=HTMLResponse)
def showcase_collections(
    request: Request, module: Module, identity: CurrentIdentity, q: str = "", page: int = 1
) -> HTMLResponse:
    """本部門供應商範例集入口。"""
    try:
        filters = SearchInput(q=q, page=page)
    except ValidationError:
        raise EError from None
    return render(
        request,
        module,
        identity,
        {
            "collections": service.collections(identity, filters.q, filters.page),
            "q": filters.q,
            "page": filters.page,
            "previous_url": "/showcases/collections?"
            + urlencode({"q": q, "page": max(1, page - 1)}),
            "next_url": "/showcases/collections?" + urlencode({"q": q, "page": page + 1}),
        },
    )


@router.get("/collections/{supplier_id}", response_class=HTMLResponse)
def showcase_collection(
    request: Request, module: Module, identity: CurrentIdentity, supplier_id: UUID, page: int = 1
) -> HTMLResponse:
    """集中顯示獨立範例與縮圖，分頁不影響啟用總數。"""
    try:
        filters = SearchInput(supplier_id=supplier_id, page=page)
    except ValidationError:
        raise EError from None
    return render(
        request,
        module,
        identity,
        {
            "collection": service.collection(identity, supplier_id),
            "items": service.query(identity, filters=filters),
            "filters": filters,
            "page": page,
            "previous_url": f"/showcases/collections/{supplier_id}?page={max(1, page - 1)}",
            "next_url": f"/showcases/collections/{supplier_id}?page={page + 1}",
        },
    )


@router.get("/reference", response_class=HTMLResponse)
def showcase_reference(
    request: Request, module: Module, identity: CurrentIdentity, tax_id: str = "", layout: str = ""
) -> HTMLResponse:
    """一般登入者依供應商統編查看啟用範例，不提供維護動作。"""
    try:
        items = service.references(identity, tax_id, layout) if tax_id else []
    except ValueError:
        raise EError from None
    return render(
        request, module, identity, {"references": items, "tax_id": tax_id, "layout": layout}
    )


@router.get("/by-supplier/{supplier_id}", response_class=HTMLResponse)
def showcase_by_supplier(
    request: Request, module: Module, identity: CurrentIdentity, supplier_id: UUID, page: int = 1
) -> HTMLResponse:
    """同一供應商主檔的範例由有查看權的部門共用。"""
    try:
        filters = SearchInput(supplier_id=supplier_id, page=page)
    except ValidationError:
        raise EError from None
    try:
        master = service.master_supplier(identity, supplier_id)
        can_store_history = True
        unsupported_country = master["country_code"] not in SHOWCASE_SUPPORTED_COUNTRIES
        if unsupported_country:
            # 尚未提供該國別範例：不讀歷程表，也不把缺表當作可用資料庫錯誤。
            items, can_store_history = [], False
        else:
            items, can_store_history = service.master_examples(identity, supplier_id, filters.page)
    except DeniedError:
        return render(request, module, identity, {"access_problem": "permission"}, status_code=403)
    except UnavailableError:
        return render(request, module, identity, {"access_problem": "unavailable"}, status_code=503)
    path = f"/showcases/by-supplier/{supplier_id}"
    return render(
        request,
        module,
        identity,
        {
            "collection": {
                "id": supplier_id,
                "name": master["supplier_name"],
                "tax_id": master["tax_id"],
            },
            "master_collection": True,
            "unsupported_country": unsupported_country,
            "can_store_history": can_store_history,
            "items": items,
            "filters": filters,
            "page": page,
            "previous_url": f"{path}?page={max(1, page - 1)}",
            "next_url": f"{path}?page={page + 1}",
        },
    )


@router.get("/reference/{sample}/{revision}/file")
def showcase_reference_file(identity: CurrentIdentity, sample: UUID, revision: UUID) -> Response:
    """啟用範例專用預覽，不沿用可讀歷史的維護入口。"""
    content, mime = service.reference_file(identity, sample, revision)
    return Response(
        content,
        media_type=mime,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/new", response_class=HTMLResponse)
def showcase_new(
    request: Request,
    module: Module,
    identity: CurrentIdentity,
    supplier_id: UUID | None = None,
    master_supplier_id: UUID | None = None,
) -> Response:
    """由正式供應商進入新增表單；舊入口導向供應商管理。"""
    if master_supplier_id is None:
        service.query(identity)
        return RedirectResponse("/suppliers", status_code=303)
    data = {"title": "新增範例發票", "layout": "三聯式", "reason": "加入範例發票"}
    if master_supplier_id:
        master = service.master_supplier(identity, master_supplier_id)
        data.update(
            master_supplier_id=master_supplier_id,
            supplier_name=master["supplier_name"],
            tax_id=master["tax_id"] if master["country_code"] == "TW" else "",
            layout="三聯式" if master["country_code"] == "TW" else "一般發票",
        )
    return render(
        request,
        module,
        identity,
        {"editing": True, "sample_id": uuid4(), "version": 0, "data": data},
    )


@router.get("/{sample}", response_class=HTMLResponse)
def showcase_detail(
    request: Request, module: Module, identity: CurrentIdentity, sample: UUID
) -> HTMLResponse:
    """原圖、逐欄答案、修訂、啟用與操作紀錄。"""
    row = service.current_detail(identity, sample)
    return render(
        request,
        module,
        identity,
        {
            "sample_id": sample,
            "row": row,
            "version": row["version"],
            "data": row["payload"]["revisions"][-1],
            "editing": not row.get("read_only", False),
        },
    )


@router.get("/{sample}/files/{revision}")
def showcase_file(identity: CurrentIdentity, sample: UUID, revision: UUID) -> Response:
    """檔案預覽同樣驗權，網址不包含物件鍵。"""
    content, mime = service.source(identity, sample, revision)
    return Response(
        content,
        media_type=mime,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{sample}/tests/{test}/file")
def showcase_test_file(identity: CurrentIdentity, sample: UUID, test: UUID) -> Response:
    """測試原件以固定資源關聯驗權。"""
    content, mime = service.test_file(identity, sample, test)
    return Response(
        content,
        media_type=mime,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{sample}/save", dependencies=[Depends(require_e_origin)])
async def showcase_save(
    request: Request, identity: CurrentIdentity, sample: UUID
) -> RedirectResponse:
    """上傳／更動均取登入身分，不能從表單冒用上傳者。"""
    form = await request.form(
        max_files=1, max_fields=4 * MAX_TRUTH_FIELDS + 30, max_part_size=MAX_BYTES
    )
    run_ocr = form.get("run_ocr") == "yes"
    try:
        data = SampleInput.model_validate(
            {
                name: form.get(name, "")
                for name in ("title", "supplier_name", "tax_id", "layout", "writing", "reason")
            }
            | {
                "master_supplier_id": form.get("master_supplier_id") or None,
                "copy_type": str(form.get("copy_type", "unknown")),
                "valid_from": form.get("valid_from") or None,
                "valid_to": form.get("valid_to") or None,
                "key_fields": [] if run_ocr else form.getlist("key_fields"),
                "truths": []
                if run_ocr
                else [
                    {"path": str(path), "state": str(state), "text": str(value)}
                    for path, state, value in zip(
                        form.getlist("truth_path"),
                        form.getlist("truth_state"),
                        form.getlist("truth_text"),
                        strict=True,
                    )
                ],
            }
        )
        expected = int(str(form.get("version", "0")))
        if expected < 0:
            raise ValueError
        file = form.get("image")
        content = (
            await file.read(MAX_BYTES + 1)
            if hasattr(file, "read") and getattr(file, "filename", "")
            else None
        )
        if expected and content is not None and not run_ocr:
            raise ValueError("替換圖片請使用替換並辨識")
        upload_options = (
            {"filename": file.filename} if content is not None and data.master_supplier_id else {}
        )
        if run_ocr:
            await run_in_threadpool(
                service.save,
                identity,
                data,
                content,
                sample,
                expected,
                run_ocr=True,
                **upload_options,
            )
        else:
            await run_in_threadpool(
                service.save, identity, data, content, sample, expected, **upload_options
            )
    except (ValidationError, ValueError, TypeError):
        raise EError from None
    return RedirectResponse(f"/showcases/{sample}#showcase-review-fields", status_code=303)


@router.post("/{sample}/status", dependencies=[Depends(require_e_origin)])
async def showcase_status(
    request: Request, module: Module, identity: CurrentIdentity, sample: UUID
) -> Response:
    """依啟用管理權調整狀態並保留原因。"""
    form = await request.form()
    try:
        version = int(str(form.get("version")))
    except (ValueError, TypeError):
        raise EError from None
    command = str(form.get("command", ""))
    reason = str(form.get("reason", ""))
    try:
        master_supplier_id = await run_in_threadpool(
            service.change_status,
            identity,
            sample,
            version,
            command,
            reason,
            form.get("confirmed") == "yes" or command == "activate",
        )
    except ValueError as exc:
        row = await run_in_threadpool(service.current_detail, identity, sample)
        return render(
            request,
            module,
            identity,
            {
                "sample_id": sample,
                "row": row,
                "version": row["version"],
                "data": row["payload"]["revisions"][-1],
                "editing": True,
                "status_error": str(exc),
                "status_command": command,
                "status_reason": reason,
            },
            status_code=422,
        )
    if command in {"activate", "deactivate"} and master_supplier_id:
        return RedirectResponse(f"/showcases/by-supplier/{master_supplier_id}", status_code=303)
    return RedirectResponse(f"/showcases/{sample}#showcase-status", status_code=303)


@router.post("/{sample}/test", dependencies=[Depends(require_e_origin)])
async def showcase_test(
    request: Request, module: Module, identity: CurrentIdentity, sample: UUID
) -> HTMLResponse:
    """測試原件與參考並排，保存意見，不建立正式案件。"""
    form = await request.form(max_files=1, max_part_size=MAX_BYTES)
    file = form.get("image")
    run_ocr = form.get("run_ocr") == "yes"
    gold: dict[str, str] = {}
    try:
        if run_ocr:
            if form.get("supplier_confirmed") != "yes":
                raise ValueError("請先確認本次測試票的賣方與版型和此範例一致")
            invoice_date = date.fromisoformat(str(form.get("test_date", "")))
            copy_type = str(form.get("test_copy_type", "unknown"))
            if copy_type not in {"unknown", "deduction", "receipt", "stub"}:
                raise ValueError
            for key in service.analysis.FIELDS:
                value = str(form.get("gold_" + key, "")).strip()
                if len(value) > 2000:
                    raise ValueError
                if value:
                    gold[key] = value
        if not hasattr(file, "read"):
            raise ValueError
        content = await file.read(MAX_BYTES + 1)
        test_id = service.test_review(
            identity, sample, int(str(form.get("version"))), content, str(form.get("note", ""))
        )
    except (ValueError, TypeError):
        raise EError from None
    report = (
        service.analyze_test(identity, sample, test_id, content, invoice_date, copy_type, gold)
        if run_ocr
        else None
    )
    row = service.detail(identity, sample)
    return render(
        request,
        module,
        identity,
        {"sample_id": sample, "row": row, "test_id": test_id, "analysis_report": report},
    )
