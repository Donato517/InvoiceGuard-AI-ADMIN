"""E2E-1 的外部服務替身（明確標示：Storage／Document AI／Groq 皆為替身，不是真實服務）。

由 api_launcher.py 與 admin_launcher.py 在各自行程內匯入；只用標準函式庫與各專案同版的
`app.integrations.*` 型別。所有狀態放在共用工作目錄（E2E_WORK）：
    storage/<bucket>/<key>   Storage 替身的物件
    flags/<name>             測試用故障／延遲注入（檔案內容為參數）
    calls.log                JSON Lines：每次替身被呼叫的紀錄（測試用來斷言 OCR 只呼叫幾次）
"""

import json
import os
import time
from pathlib import Path
from typing import Any

WORK = Path(os.environ["E2E_WORK"])
STORAGE_ROOT = WORK / "storage"
FLAGS = WORK / "flags"
CALLS = WORK / "calls.log"

# 固定的測試發票內容（模型替身回傳；含刻意缺漏：買受人統編、買受人名稱）
INVOICE_JSON: dict[str, Any] = {
    "is_triplicate": True,
    "invoice_number": "AB12345678",
    "invoice_date": "115年10月15日",
    "tax_period": "115年09-10月",
    "buyer_name": None,
    "buyer_tax_id": None,
    "seller_name": "ABC公司",
    "seller_tax_id": "04595257",
    "items": [
        {"item_name": "辦公文具", "quantity": "2", "unit_price": "1000", "line_amount": "2000"},
        {"item_name": "影印紙", "quantity": "1", "unit_price": "1000", "line_amount": "1000"},
    ],
    "sales_amount": "3000",
    "tax_type": "taxable",
    "tax_amount": "150",
    "total_amount": "3150",
    "total_amount_zh": None,
}


def record(event: str, **info: Any) -> None:
    with CALLS.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps({"event": event, "t": time.time(), **info}, ensure_ascii=False) + "\n"
        )


def flag(name: str) -> str | None:
    path = FLAGS / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else None


class FileStorage:
    """檔案系統版的 S3StorageClient 替身（沒有 SigV4、沒有 Supabase 行為）。"""

    def __init__(self, bucket: str) -> None:
        self.bucket = bucket
        self.root = STORAGE_ROOT / bucket

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("invalid key")
        return path

    def _fail(self) -> None:
        from app.integrations.supabase_storage import StorageError

        code = flag("storage_fail")
        if code:
            raise StorageError(code)  # type: ignore[arg-type]

    def put(self, key: str, data: bytes, *, content_type: str) -> None:
        self._fail()
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.with_name(path.name + ".type").write_text(content_type, encoding="utf-8")
        record("storage.put", bucket=self.bucket, key=key, size=len(data))

    def get(self, key: str, *, timeout_seconds: float = 30) -> tuple[bytes, str]:
        from app.integrations.supabase_storage import StorageError

        self._fail()
        path = self._path(key)
        if not path.is_file():
            raise StorageError("storage_not_found")
        kind = path.with_name(path.name + ".type")
        record("storage.get", bucket=self.bucket, key=key)
        return path.read_bytes(), kind.read_text(
            encoding="utf-8"
        ) if kind.exists() else "application/octet-stream"

    def delete(self, key: str) -> None:
        path = self._path(key)
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".type").unlink(missing_ok=True)
        record("storage.delete", bucket=self.bucket, key=key)

    def list_objects(self, prefix: str) -> list[Any]:
        return []

    def move(self, source_key: str, destination_key: str) -> None:
        data, kind = self.get(source_key)
        self.put(destination_key, data, content_type=kind)
        self.delete(source_key)

    @classmethod
    def from_settings(
        cls, configuration: Any = None, *, bucket: str | None = None
    ) -> "FileStorage":
        return cls(bucket or "default")


class FakeOcr:
    """Document AI 替身：回傳固定文字；可由旗標注入延遲或失敗。"""

    def process(self, content: bytes, mime_type: str, *, timeout_seconds: float = 30) -> Any:
        from app.integrations.document_ai import DocumentAIError, OcrPage, OcrResult

        record("ocr.process", size=len(content), mime=mime_type)
        delay = flag("ocr_delay")
        if delay:
            time.sleep(float(delay))
        code = flag("ocr_fail")
        if code:
            raise DocumentAIError(code)  # type: ignore[arg-type]
        text = "統一發票 AB12345678 115年10月15日 賣方 ABC公司 04595257 辦公文具 2000 影印紙 1000 銷售額 3000 營業稅 150 總計 3150"
        return OcrResult(
            text=text,
            pages=(OcrPage(1, None, None, 0.93, ()),),
            raw_response={"fake": True},
        )

    def close(self) -> None:
        return None

    @classmethod
    def from_settings(cls, *args: Any, **kwargs: Any) -> "FakeOcr":
        return cls()


class FakeGroq:
    """Groq 替身：回傳固定的發票 JSON（含刻意缺漏的買受人欄位）。"""

    def chat(
        self, messages: Any, *, advanced: bool = False, timeout_seconds: float = 30, **options: Any
    ) -> Any:
        from app.integrations.chat_models import ChatReply
        from app.integrations.groq import GroqError

        record("groq.chat", advanced=advanced)
        code = flag("groq_fail")
        if code:
            raise GroqError(code)  # type: ignore[arg-type]
        return ChatReply(
            text=json.dumps(INVOICE_JSON, ensure_ascii=False),
            model="e2e-fake-model",
            finish_reason="stop",
            prompt_tokens=10,
            output_tokens=10,
            total_tokens=20,
            raw_response={"fake": True},
        )

    @classmethod
    def from_settings(cls, *args: Any, **kwargs: Any) -> "FakeGroq":
        return cls()
