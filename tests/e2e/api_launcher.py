"""E2E-1：以 API 專案的 .venv 啟動真實 API 服務（uvicorn），只替換三個外部服務的建構點。

用法（由 conftest.py 啟動）：cd InvoiceGuard-AI-API && PYTHONPATH=. .venv/bin/python <此檔>
安全：DATABASE_URL 的主機必須是回送位址，否則拒絕啟動；不載入任何正式設定。
注意：這是測試專用程式，只能放在 tests/e2e/，不得進入任何專案的 app/。
"""

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))

if urlsplit(
    os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
).hostname not in {
    "127.0.0.1",
    "localhost",
}:
    raise SystemExit("E2E 只允許連本機資料庫")

import fakes  # noqa: E402
import uvicorn  # noqa: E402

from app.integrations.document_ai import DocumentAIClient  # noqa: E402
from app.integrations.groq import GroqClient  # noqa: E402
from app.integrations.supabase_storage import S3StorageClient  # noqa: E402

S3StorageClient.from_settings = fakes.FileStorage.from_settings  # type: ignore[method-assign,assignment]
DocumentAIClient.from_settings = fakes.FakeOcr.from_settings  # type: ignore[method-assign,assignment]
GroqClient.from_settings = fakes.FakeGroq.from_settings  # type: ignore[method-assign,assignment]

from app.main import app  # noqa: E402


@app.middleware("http")
async def slow_submit(request, call_next):  # type: ignore[no-untyped-def]
    """測試用：寫入完成後才延遲回應，用來模擬『已處理但回應遲到』。"""
    response = await call_next(request)
    delay = fakes.flag("slow_submit")
    if delay and request.method == "POST" and request.url.path == "/api/v1/invoices":
        await asyncio.sleep(float(delay))
    return response


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ["E2E_API_PORT"]), log_level="warning")
