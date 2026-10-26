"""
模組名稱：app.services.dev_file_service
功能說明：/dev/files 示範的服務層——以 Vercel 模組（app.integrations.supabase_storage）上傳、列表、下載、移動、刪除檔案。
相依模組：app.integrations.supabase_storage
注意事項：
    - 示範檔案：不寫 DB 中繼資料、不接發票流程；正式功能也使用同一個 Vercel 模組，沒有另一套。
    - 兩種檔案各有固定路徑前綴（dev/images/、dev/rag/），物件鍵一律由本模組產生，不接受任意路徑。
    - 本模組不讀環境變數；S3 設定由 Vercel 模組從 app.core.config.settings 取得
      （本機由 START.cmd 經 Vercel CLI 注入 ADMIN 程序，雲端為 Vercel 環境變數）。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from uuid import uuid4

from app.integrations import supabase_storage
from app.schemas.files import MAX_UPLOAD_BYTES


@dataclass(frozen=True)
class Category:
    """一種檔案的顯示名稱、Storage 路徑前綴與允許的副檔名（→ Content-Type）。"""

    label: str
    prefix: str
    types: dict[str, str]


# 圖片使用 image/jpeg、image/png；RAG 文字使用 text/plain、text/markdown，Bucket 是否接受由服務端設定決定。
CATEGORIES = {
    "images": Category(
        "圖片",
        "dev/images/",
        {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"},
    ),
    "rag": Category(
        "RAG 文字檔",
        "dev/rag/",
        {".txt": "text/plain", ".md": "text/markdown"},
    ),
}
MAX_BYTES = MAX_UPLOAD_BYTES
# 類別內的相對路徑：資料夾只允許英數字、底線、連字號；檔名另可有句點
NAME = re.compile(r"(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9][A-Za-z0-9._-]*")
FOLDER = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")
MESSAGES = {
    "unknown_category": "不支援的檔案類別",
    "file_required": "請選擇檔案",
    "file_too_large": f"檔案超過 {MAX_BYTES // (1024 * 1024)} MB",
    "unsupported_type": "副檔名不在此類別的允許清單",
    "content_mismatch": "檔案內容與圖片格式不符",
    "text_not_utf8": "RAG 文字檔需為 UTF-8 文字",
    "invalid_name": "檔案路徑不正確",
    "invalid_folder": "資料夾只能使用英數字、底線、連字號，以 / 分隔",
    "same_location": "目的位置與目前位置相同",
    "storage_not_configured": "本機 ADMIN 沒有 S3 設定；請以 START.cmd 啟動（Vercel CLI 取得 S3_* 與 Bucket）",
    "storage_unreachable": "連不到 Supabase Storage",
    "storage_denied": "Supabase Storage 拒絕此操作（金鑰或權限）",
    "storage_not_found": "Storage 找不到此 Bucket 或檔案",
    "storage_type_rejected": "Bucket 不接受此檔案類型；請在 Supabase Bucket 的 Allowed MIME types 加入",
    "storage_failed": "Supabase Storage 操作失敗",
}
_SIGNATURES = {
    ".jpg": b"\xff\xd8\xff",
    ".jpeg": b"\xff\xd8\xff",
    ".png": b"\x89PNG\r\n\x1a\n",
}


class DevFileError(RuntimeError):
    """操作失敗；message 為固定畫面文字，detail 只有 S3 錯誤代碼。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code if code in MESSAGES else "storage_failed"
        self.detail = detail
        super().__init__(MESSAGES[self.code] + (f"（{detail}）" if detail else ""))


@dataclass(frozen=True)
class FileEntry:
    """畫面列出的一筆檔案；name 為類別前綴底下的相對路徑。"""

    name: str
    size_bytes: int
    last_modified: datetime


def list_files(category: str) -> list[FileEntry]:
    """列出類別前綴底下的所有檔案（含子資料夾）。"""
    prefix = _category(category).prefix
    objects = _call(lambda client: client.list_objects(prefix))
    return [
        FileEntry(stored.key.removeprefix(prefix), stored.size_bytes, stored.last_modified)
        for stored in objects
        if stored.key.startswith(prefix) and stored.key != prefix
    ]


def upload(category: str, filename: str, data: bytes) -> str:
    """驗證副檔名、大小與內容後上傳，回傳新檔的相對路徑（時間戳記避免覆蓋同名檔）。"""
    selected = _category(category)
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if not filename or not data:
        raise DevFileError("file_required")
    if len(data) > MAX_BYTES:
        raise DevFileError("file_too_large")
    if suffix not in selected.types:
        raise DevFileError("unsupported_type")
    _check_content(category, suffix, data)
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", PurePosixPath(filename.replace("\\", "/")).stem)
    stem = stem.strip("_-")[:80] or "file"
    name = f"{datetime.now():%Y%m%d-%H%M%S}_{uuid4().hex[:6]}_{stem}{suffix}"
    _call(
        lambda client: client.put(selected.prefix + name, data, content_type=selected.types[suffix])
    )
    return name


def download(category: str, name: str) -> tuple[bytes, str, str]:
    """回傳（內容, Content-Type, 下載檔名）。"""
    key = _key(category, name)
    data, content_type = _call(lambda client: client.get(key))
    return data, content_type, PurePosixPath(name).name


def move(category: str, name: str, folder: str) -> str:
    """移到同類別底下的資料夾（空白表示類別根目錄），回傳新的相對路徑。"""
    source = _key(category, name)
    folder = folder.strip().strip("/")
    if folder and not FOLDER.fullmatch(folder):
        raise DevFileError("invalid_folder")
    destination_name = (
        f"{folder}/{PurePosixPath(name).name}" if folder else PurePosixPath(name).name
    )
    if destination_name == name:
        raise DevFileError("same_location")
    destination = _key(category, destination_name)
    _call(lambda client: client.move(source, destination))
    return destination_name


def delete(category: str, name: str) -> None:
    """刪除類別底下的一個檔案。"""
    key = _key(category, name)
    _call(lambda client: client.delete(key))


def _category(category: str) -> Category:
    if category not in CATEGORIES:
        raise DevFileError("unknown_category")
    return CATEGORIES[category]


def _key(category: str, name: str) -> str:
    if not NAME.fullmatch(name) or ".." in name:
        raise DevFileError("invalid_name")
    return _category(category).prefix + name


def _check_content(category: str, suffix: str, data: bytes) -> None:
    if category == "images":
        if not data.startswith(_SIGNATURES[suffix]):
            raise DevFileError("content_mismatch")
        return
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise DevFileError("text_not_utf8") from None


def _call[T](operation: Callable[[supabase_storage.S3StorageClient], T]) -> T:
    try:
        return operation(supabase_storage.S3StorageClient.from_settings())
    except supabase_storage.StorageError as error:
        raise DevFileError(error.code, error.detail) from None
