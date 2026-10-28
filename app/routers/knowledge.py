"""
模組名稱：app.routers.knowledge
功能說明：知識庫與文件管理——列表、文件清單、上傳對話框、部門選用切換、編輯與下載。
路由：/knowledge
注意事項：
    - 本路由需管理者權限（require_admin）。
    - 支援 HTML 原型所規劃之知識庫與文件管理功能，包含 HTMX 部門標籤切換與多檔批次上傳。
"""

from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.deps import require_admin
from app.core.templating import templates

router = APIRouter(prefix="/knowledge", tags=["knowledge"], dependencies=[Depends(require_admin)])

DEPARTMENTS: list[dict[str, str]] = [
    {"id": "finance", "name": "財務部"},
    {"id": "procurement", "name": "採購部"},
    {"id": "administration", "name": "行政部"},
    {"id": "it", "name": "資訊部"},
]

# 記憶體種子資料（對應原型 sampleDatabase）
_KNOWLEDGE_STORE: list[dict[str, Any]] = [
    {
        "id": "shared-tax",
        "title": "營業稅相關規定",
        "assigned_departments": ["finance", "procurement", "administration", "it"],
        "usage": {"finance": True, "procurement": True, "administration": True, "it": True},
        "documents": [
            {
                "id": "sample-tax",
                "title": "營業稅相關規定（範例文件）",
                "file_name": "tax-guide.pdf",
                "status": "active",
                "processing_status": "ready",
                "created_at": datetime(2026, 8, 1, 10, 0, tzinfo=UTC),
                "is_sample": True,
                "usage": {"finance": True, "procurement": True, "administration": True, "it": True},
                "text": "加值型及非加值型營業稅法重點摘錄：\n1. 統一發票應依規定開立與申報。\n2. 進項憑證扣抵條件：載有營業稅額，為營業所需，非屬不得扣抵貨物或勞務。\n3. 三聯式發票應確認買受人統編、日期、金額與稅額計算無誤。",
                "revision": 1,
            }
        ],
    },
    {
        "id": "shared-format",
        "title": "進項申報檔格式說明",
        "assigned_departments": ["finance", "procurement", "administration", "it"],
        "usage": {"finance": True, "procurement": True, "administration": True, "it": True},
        "documents": [
            {
                "id": "sample-format",
                "title": "進項申報檔格式說明（範例文件）",
                "file_name": "format-guide.pdf",
                "status": "active",
                "processing_status": "ready",
                "created_at": datetime(2026, 8, 5, 14, 30, tzinfo=UTC),
                "is_sample": True,
                "usage": {"finance": True, "procurement": True, "administration": True, "it": True},
                "text": "國稅局媒體申報檔「格式 21」規格說明：\n1. 固定長度文字檔，包含營業人統編、發票字軌號碼、進貨金額與稅額。\n2. 匯入前應先驗算借貸平衡及統編邏輯乘積和。",
                "revision": 1,
            }
        ],
    },
    {
        "id": "shared-deduction",
        "title": "進項扣抵判斷指引",
        "assigned_departments": ["finance", "procurement", "administration", "it"],
        "usage": {"finance": True, "procurement": True, "administration": False, "it": True},
        "documents": [
            {
                "id": "sample-deduction",
                "title": "進項扣抵判斷指引（範例文件）",
                "file_name": "deduction-guide.pdf",
                "status": "active",
                "processing_status": "ready",
                "created_at": datetime(2026, 8, 10, 9, 15, tzinfo=UTC),
                "is_sample": True,
                "usage": {
                    "finance": True,
                    "procurement": True,
                    "administration": False,
                    "it": True,
                },
                "text": "進項不可扣抵項目審核標準：\n1. 交際應酬用之貨物或勞務。\n2. 酬勞員工個人之貨物或勞務。\n3. 自用乘人小汽車。\n4. 未依規定載明扣抵代號之憑證。",
                "revision": 1,
            }
        ],
    },
    {
        "id": "shared-voucher",
        "title": "會計傳票作業手冊",
        "assigned_departments": ["finance", "procurement", "administration", "it"],
        "usage": {"finance": True, "procurement": True, "administration": True, "it": True},
        "documents": [
            {
                "id": "sample-voucher",
                "title": "會計傳票作業手冊（範例文件）",
                "file_name": "voucher-guide.pdf",
                "status": "active",
                "processing_status": "ready",
                "created_at": datetime(2026, 8, 12, 16, 0, tzinfo=UTC),
                "is_sample": True,
                "usage": {"finance": True, "procurement": True, "administration": True, "it": True},
                "text": "自動傳票切立準則：\n借：費用/資產科目（未稅金額）\n借：1415 進項稅額（稅額）\n貸：2202 應付帳款（含稅總額）\n確認借貸合計相等方可送出儲存。",
                "revision": 1,
            }
        ],
    },
    {
        "id": "shared-workflow",
        "title": "InvoiceGuard 操作說明",
        "assigned_departments": ["finance", "procurement", "administration", "it"],
        "usage": {"finance": True, "procurement": True, "administration": True, "it": True},
        "documents": [
            {
                "id": "sample-workflow",
                "title": "InvoiceGuard 操作說明（範例文件）",
                "file_name": "workflow-guide.pdf",
                "status": "active",
                "processing_status": "ready",
                "created_at": datetime(2026, 8, 15, 11, 20, tzinfo=UTC),
                "is_sample": True,
                "usage": {"finance": True, "procurement": True, "administration": True, "it": True},
                "text": "InvoiceGuard 作業九步驟說明：\n步驟 1～3 由採購人員上傳發票與核對 OCR 結果；\n步驟 4～9 由財務與管理端執行系統自動檢核、人工複核、申報檔產出、傳票平衡與匯出檔案。",
                "revision": 1,
            }
        ],
    },
]


def _find_kb(kb_id: str) -> dict[str, Any] | None:
    return next((k for k in _KNOWLEDGE_STORE if k["id"] == kb_id), None)


def _find_doc(kb: dict[str, Any], doc_id: str) -> dict[str, Any] | None:
    return next((d for d in kb.get("documents", []) if d["id"] == doc_id), None)


def _build_kb_badges(kb: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": d["id"],
            "name": d["name"],
            "state": "on" if kb.get("usage", {}).get(d["id"]) else "off",
            "can_toggle": True,
        }
        for d in DEPARTMENTS
    ]


def _build_doc_badges(kb: dict[str, Any], doc: dict[str, Any]) -> list[dict[str, Any]]:
    # 只有該知識庫已選用的部門才會列在文件的使用部門中
    kb_usage = kb.get("usage", {})
    return [
        {
            "id": d["id"],
            "name": d["name"],
            "state": "on" if doc.get("usage", {}).get(d["id"]) else "off",
            "can_toggle": True,
        }
        for d in DEPARTMENTS
        if kb_usage.get(d["id"])
    ]


@router.get("", response_class=HTMLResponse)
def knowledge_list(
    request: Request,
    q: Annotated[str, Query()] = "",
    department_id: Annotated[str, Query()] = "all",
) -> HTMLResponse:
    """全部門知識庫管理：統計、搜尋、部門篩選、部門選用切換。"""
    filtered_kbs = _KNOWLEDGE_STORE

    if q.strip():
        search_term = q.strip().lower()
        filtered_kbs = [k for k in filtered_kbs if search_term in k["title"].lower()]

    if department_id != "all":
        filtered_kbs = [k for k in filtered_kbs if k.get("usage", {}).get(department_id)]

    rows: list[dict[str, Any]] = []
    for kb in filtered_kbs:
        rows.append(
            {
                "id": kb["id"],
                "title": kb["title"],
                "badges": _build_kb_badges(kb),
                "toggle_url": f"/knowledge/{kb['id']}/toggle-dept",
            }
        )

    # 至少一個部門選用即計為在使用中；所有部門未選用計為未使用
    active_count = sum(1 for k in _KNOWLEDGE_STORE if any(k.get("usage", {}).values()))
    inactive_count = len(_KNOWLEDGE_STORE) - active_count
    counts = {
        "total": len(_KNOWLEDGE_STORE),
        "active": active_count,
        "inactive": inactive_count,
        "unknown": 0,
    }

    return templates.TemplateResponse(
        request,
        "pages/knowledge/list.html",
        {
            "counts": counts,
            "rows": rows,
            "departments": DEPARTMENTS,
            "q": q,
            "department_id": department_id,
        },
    )


@router.post("/{kb_id}/toggle-dept", response_class=HTMLResponse)
def toggle_kb_department(
    request: Request,
    kb_id: str,
    department_id: Annotated[str, Form()],
    enable: Annotated[str, Form()],
) -> HTMLResponse:
    """切換知識庫部門選用狀態（HTMX 片段替換）。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    is_enabled = enable.lower() == "true"
    kb.setdefault("usage", {})[department_id] = is_enabled

    badges = _build_kb_badges(kb)
    return templates.TemplateResponse(
        request,
        "pages/knowledge/_dept_badges.html",
        {
            "badges": badges,
            "toggle_url": f"/knowledge/{kb['id']}/toggle-dept",
            "target": f"dept-badges-{kb['id']}",
        },
    )


@router.get("/{kb_id}/documents", response_class=HTMLResponse)
def document_list(
    request: Request,
    kb_id: str,
    department_id: Annotated[str, Query()] = "finance",
    q: Annotated[str, Query()] = "",
    approval: Annotated[str, Query()] = "all",
) -> HTMLResponse:
    """知識庫文件清單：統計、搜尋、核准篩選、文件部門選用、核准／暫停。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    all_docs = kb.get("documents", [])
    filtered_docs = all_docs

    if q.strip():
        search_term = q.strip().lower()
        filtered_docs = [
            d
            for d in filtered_docs
            if search_term in d["title"].lower() or search_term in d.get("file_name", "").lower()
        ]

    if approval == "approved":
        filtered_docs = [d for d in filtered_docs if d.get("status") == "active"]
    elif approval == "unapproved":
        filtered_docs = [d for d in filtered_docs if d.get("status") != "active"]

    rows: list[dict[str, Any]] = []
    for doc in filtered_docs:
        rows.append(
            {
                "id": doc["id"],
                "title": doc["title"],
                "file_name": doc.get("file_name", ""),
                "status": doc.get("status", "active"),
                "processing_status": doc.get("processing_status", "ready"),
                "created_at": doc.get("created_at"),
                "is_sample": doc.get("is_sample", False),
                "badges": _build_doc_badges(kb, doc),
                "toggle_url": f"/knowledge/{kb['id']}/documents/{doc['id']}/toggle-dept",
            }
        )

    approved_count = sum(1 for d in all_docs if d.get("status") == "active")
    counts = {
        "total": len(all_docs),
        "approved": approved_count,
        "unapproved": len(all_docs) - approved_count,
    }

    kb_view = {
        "id": kb["id"],
        "title": kb["title"],
        "badges": _build_kb_badges(kb),
        "toggle_url": f"/knowledge/{kb['id']}/toggle-dept",
    }

    return templates.TemplateResponse(
        request,
        "pages/knowledge/documents.html",
        {
            "kb": kb_view,
            "department_id": department_id,
            "assigned_departments": DEPARTMENTS,
            "departments": DEPARTMENTS,
            "counts": counts,
            "rows": rows,
            "q": q,
            "approval": approval,
        },
    )


@router.post("/{kb_id}/documents/{doc_id}/toggle-dept", response_class=HTMLResponse)
def toggle_document_department(
    request: Request,
    kb_id: str,
    doc_id: str,
    department_id: Annotated[str, Form()],
    enable: Annotated[str, Form()],
) -> HTMLResponse:
    """切換文件部門選用狀態（HTMX 片段替換）。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    doc = _find_doc(kb, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="找不到指定文件")

    is_enabled = enable.lower() == "true"
    doc.setdefault("usage", {})[department_id] = is_enabled

    badges = _build_doc_badges(kb, doc)
    return templates.TemplateResponse(
        request,
        "pages/knowledge/_dept_badges.html",
        {
            "badges": badges,
            "toggle_url": f"/knowledge/{kb['id']}/documents/{doc['id']}/toggle-dept",
            "target": f"dept-badges-doc-{doc['id']}",
        },
    )


@router.post("/{kb_id}/documents/{doc_id}/status")
def update_document_status(
    kb_id: str,
    doc_id: str,
    status: Annotated[str, Form()],
) -> RedirectResponse:
    """切換文件審核狀態（active 核准 / hold 暫停）。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    doc = _find_doc(kb, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="找不到指定文件")

    doc["status"] = "active" if status == "active" else "hold"
    return RedirectResponse(f"/knowledge/{kb_id}/documents", status_code=303)


@router.get("/{kb_id}/edit", response_class=HTMLResponse)
def edit_knowledge_base_page(request: Request, kb_id: str) -> HTMLResponse:
    """編輯知識庫名稱頁面。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    return templates.TemplateResponse(
        request,
        "pages/knowledge/edit.html",
        {
            "mode": "kb_name",
            "kb": kb,
            "doc": None,
            "can_edit": True,
            "content": None,
            "error": None,
        },
    )


@router.post("/{kb_id}/rename")
def rename_knowledge_base(
    kb_id: str,
    title: Annotated[str, Form()],
) -> RedirectResponse:
    """重新命名知識庫。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    title_clean = title.strip()
    if title_clean:
        kb["title"] = title_clean
    return RedirectResponse("/knowledge", status_code=303)


@router.post("/{kb_id}/delete")
def delete_knowledge_base(kb_id: str) -> RedirectResponse:
    """刪除知識庫及其所有文件。"""
    global _KNOWLEDGE_STORE
    _KNOWLEDGE_STORE = [k for k in _KNOWLEDGE_STORE if k["id"] != kb_id]
    return RedirectResponse("/knowledge", status_code=303)


@router.get("/{kb_id}/documents/{doc_id}/edit", response_class=HTMLResponse)
def edit_document_name_page(request: Request, kb_id: str, doc_id: str) -> HTMLResponse:
    """編輯文件名稱頁面。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    doc = _find_doc(kb, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="找不到指定文件")

    return templates.TemplateResponse(
        request,
        "pages/knowledge/edit.html",
        {
            "mode": "doc_name",
            "kb": kb,
            "doc": doc,
            "can_edit": True,
            "content": None,
            "error": None,
        },
    )


@router.post("/{kb_id}/documents/{doc_id}/rename")
def rename_document(
    kb_id: str,
    doc_id: str,
    title: Annotated[str, Form()],
) -> RedirectResponse:
    """重新命名文件。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    doc = _find_doc(kb, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="找不到指定文件")

    title_clean = title.strip()
    if title_clean:
        doc["title"] = title_clean
    return RedirectResponse(f"/knowledge/{kb_id}/documents", status_code=303)


@router.post("/{kb_id}/documents/{doc_id}/delete")
def delete_document(kb_id: str, doc_id: str) -> RedirectResponse:
    """刪除文件。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    kb["documents"] = [d for d in kb.get("documents", []) if d["id"] != doc_id]
    return RedirectResponse(f"/knowledge/{kb_id}/documents", status_code=303)


@router.get("/{kb_id}/documents/{doc_id}/content", response_class=HTMLResponse)
def document_content_page(request: Request, kb_id: str, doc_id: str) -> HTMLResponse:
    """檢視與編輯文件內容。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    doc = _find_doc(kb, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="找不到指定文件")

    content_data = {
        "text": doc.get("text", "（此文件無文字內容）"),
        "text_editable": True,
        "preview_url": None,
    }

    return templates.TemplateResponse(
        request,
        "pages/knowledge/edit.html",
        {
            "mode": "doc_content",
            "kb": kb,
            "doc": doc,
            "can_edit": True,
            "content": content_data,
            "error": None,
        },
    )


@router.post("/{kb_id}/documents/{doc_id}/content")
async def update_document_content(
    kb_id: str,
    doc_id: str,
    text: Annotated[str | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> RedirectResponse:
    """更新文件文字內容或上傳替換檔案。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    doc = _find_doc(kb, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="找不到指定文件")

    if file and file.filename:
        content_bytes = await file.read()
        doc["file_name"] = file.filename
        try:
            doc["text"] = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            doc["text"] = (
                f"已替換為二進位檔案 {file.filename}（大小：{len(content_bytes)} 位元組）。"
            )
    elif text is not None:
        doc["text"] = text

    # 修改內容後重設為未核准並增加修訂版本
    doc["status"] = "hold"
    doc["revision"] = doc.get("revision", 1) + 1

    return RedirectResponse(f"/knowledge/{kb_id}/documents/{doc_id}/content", status_code=303)


@router.get("/{kb_id}/documents/{doc_id}/download")
def download_document(kb_id: str, doc_id: str) -> Response:
    """下載原始文件。"""
    kb = _find_kb(kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="找不到指定知識庫")

    doc = _find_doc(kb, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="找不到指定文件")

    text_content = doc.get("text", "")
    filename = doc.get("file_name", "document.txt")
    encoded_fn = quote(filename)

    return Response(
        content=text_content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_fn}"},
    )


@router.post("/documents", response_class=HTMLResponse)
async def upload_documents(
    request: Request,
    department_id: Annotated[str, Form()],
    files: Annotated[list[UploadFile], File()],
    knowledge_base_id: Annotated[str | None, Form()] = None,
    knowledge_base_title: Annotated[str | None, Form()] = None,
    title: Annotated[str | None, Form()] = None,
    status: Annotated[str, Form()] = "hold",
) -> HTMLResponse:
    """批次上傳多個文件到既有或新建立的知識庫（HTMX 回傳 batch-results 片段）。"""
    kb: dict[str, Any] | None = None

    if knowledge_base_id:
        kb = _find_kb(knowledge_base_id)
    elif knowledge_base_title and knowledge_base_title.strip():
        new_id = f"kb-{int(datetime.now().timestamp())}"
        kb = {
            "id": new_id,
            "title": knowledge_base_title.strip(),
            "assigned_departments": [department_id],
            "usage": {d["id"]: (d["id"] == department_id) for d in DEPARTMENTS},
            "documents": [],
        }
        _KNOWLEDGE_STORE.append(kb)

    if not kb:
        return HTMLResponse(
            '<div class="alert alert--danger">建立或尋找知識庫失敗，請確認已輸入知識庫名稱。</div>'
        )

    results: list[str] = []
    for idx, uploaded_file in enumerate(files):
        if not uploaded_file.filename:
            continue

        doc_title = title.strip() if (title and len(files) == 1) else uploaded_file.filename
        content_bytes = await uploaded_file.read()

        try:
            doc_text = content_bytes.decode("utf-8")
        except UnicodeDecodeError:
            doc_text = (
                f"檔案 {uploaded_file.filename} 已儲存（大小：{len(content_bytes)} 位元組）。"
            )

        new_doc_id = f"doc-{int(datetime.now().timestamp())}-{idx}"
        new_doc = {
            "id": new_doc_id,
            "title": doc_title,
            "file_name": uploaded_file.filename,
            "status": "active" if status == "active" else "hold",
            "processing_status": "ready",
            "created_at": datetime.now(UTC),
            "is_sample": False,
            "usage": {d["id"]: (d["id"] == department_id) for d in DEPARTMENTS},
            "text": doc_text,
            "revision": 1,
        }
        kb.setdefault("documents", []).append(new_doc)
        status_label = "已核准" if new_doc["status"] == "active" else "未核准（待審核）"
        results.append(f"✓ 成功上傳 {uploaded_file.filename}（{status_label}）")

    result_html = (
        '<div class="alert alert--success" style="margin-top:12px">'
        + "<br>".join(results)
        + '</div><p class="muted small-text">上傳完成，請重整頁面檢視最新文件列表。</p>'
    )
    return HTMLResponse(result_html)
