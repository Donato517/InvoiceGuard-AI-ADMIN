"""
模組名稱：app.routers.dev_files
功能說明：/dev 示範檔案——示範 Vercel 模組（app.integrations.supabase_storage）：圖片與 RAG 文字檔兩種上傳
    （不同 Storage 路徑），以及列表、下載、移動、刪除。
路由（settings.dev_pages_enabled：APP_ENV=local、VERCEL 不為 1、解析後 module_backend=remote；含本機 auto）：
    GET /dev/files、POST /dev/files/{category}/upload、GET /dev/files/{category}/download?name=…、
    POST /dev/files/{category}/move、POST /dev/files/{category}/delete
注意事項：
    - 示範檔案，不是串聯路徑；經 dev_file_service 呼叫 Vercel 模組，不寫 DB、不接發票流程。
    - category 只接受 images、rag；物件路徑由 service 驗證與產生。
    - 表單由 HTMX 送出：POST 直接回傳更新後的頁面（不轉址），結果訊息不經網址參數；
      頁面只替換 #dev-files 區塊，不換頁。
"""

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response

from app.core.deps import require_dev_pages
from app.core.security import verify_same_origin
from app.core.templating import templates
from app.services import dev_file_service

PAGE_TEMPLATE = "pages/dev_files/index.html"
SAVED_MESSAGES = {"uploaded": "已上傳", "moved": "已移動", "deleted": "已刪除"}

page = APIRouter(prefix="/dev/files", tags=["dev"], dependencies=[Depends(require_dev_pages)])


@page.get("", response_class=HTMLResponse)
def dev_file_page(request: Request) -> HTMLResponse:
    """兩種檔案的上傳表單與列表；每次都重新向 Storage 讀取。"""
    return _render(request, notice=(None, None))


@page.post(
    "/{category}/upload", dependencies=[Depends(verify_same_origin)], response_class=HTMLResponse
)
def dev_file_upload(
    request: Request, category: str, file: Annotated[UploadFile, File()]
) -> HTMLResponse:
    """上傳一個檔案；成功時回傳重新讀取的列表與訊息。"""
    data = file.file.read(dev_file_service.MAX_BYTES + 1)
    try:
        name = dev_file_service.upload(category, file.filename or "", data)
    except dev_file_service.DevFileError as error:
        return _render(request, notice=(category, str(error)), failed=True)
    return _saved(request, category, "uploaded", name)


@page.get("/{category}/download", response_model=None)
def dev_file_download(request: Request, category: str, name: str) -> Response:
    """以附件下載檔案內容。"""
    try:
        data, content_type, filename = dev_file_service.download(category, name)
    except dev_file_service.DevFileError as error:
        return _render(request, notice=(category, str(error)), failed=True)
    disposition = f"attachment; filename*=UTF-8''{quote(filename)}"
    return Response(data, media_type=content_type, headers={"Content-Disposition": disposition})


@page.post(
    "/{category}/move", dependencies=[Depends(verify_same_origin)], response_class=HTMLResponse
)
def dev_file_move(
    request: Request,
    category: str,
    name: Annotated[str, Form()],
    folder: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """移到同類別底下的資料夾；資料夾留空表示移回根目錄。成功時回傳重新讀取的列表與訊息。"""
    try:
        moved = dev_file_service.move(category, name, folder)
    except dev_file_service.DevFileError as error:
        return _render(request, notice=(category, str(error)), failed=True)
    return _saved(request, category, "moved", moved)


@page.post(
    "/{category}/delete", dependencies=[Depends(verify_same_origin)], response_class=HTMLResponse
)
def dev_file_delete(request: Request, category: str, name: Annotated[str, Form()]) -> HTMLResponse:
    """刪除一個檔案；成功時回傳重新讀取的列表與訊息。"""
    try:
        dev_file_service.delete(category, name)
    except dev_file_service.DevFileError as error:
        return _render(request, notice=(category, str(error)), failed=True)
    return _saved(request, category, "deleted", name)


def _saved(request: Request, category: str, saved: str, name: str) -> HTMLResponse:
    return _render(request, notice=(category, f"{SAVED_MESSAGES[saved]}：{name}"))


def _render(
    request: Request, *, notice: tuple[str | None, str | None], failed: bool = False
) -> HTMLResponse:
    sections = []
    for key, category in dev_file_service.CATEGORIES.items():
        files: list[dev_file_service.FileEntry] = []
        error = None
        try:
            files = dev_file_service.list_files(key)
        except dev_file_service.DevFileError as caught:
            error = str(caught)
        target, text = notice
        sections.append(
            {
                "key": key,
                "label": category.label,
                "prefix": category.prefix,
                "accept": ",".join(category.types),
                "files": files,
                "error": error,
                "message": text if target == key and not failed else None,
                "action_error": text if target == key and failed else None,
            }
        )
    return templates.TemplateResponse(
        request,
        PAGE_TEMPLATE,
        {"sections": sections, "max_mb": dev_file_service.MAX_BYTES // (1024 * 1024)},
        status_code=400 if failed else 200,
    )
