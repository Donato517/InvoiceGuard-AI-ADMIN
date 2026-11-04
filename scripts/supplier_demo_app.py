"""模組名稱：scripts.supplier_demo_app。
功能說明：隔離的本機供應商原型；只掛載供應商及靜態樣式。
注意事項：不匯入 app.main／core.config／登入或外部服務。只允許 loopback，雲端拒絕啟動。
"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.db.repositories.supplier_demo_repository import SupplierDemoRepository
from app.routers.suppliers import router
from app.services.supplier_service import SupplierService

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "artifacts" / "supplier-demo" / "suppliers.sqlite3"


def create_app(data_path: Path = DATA_PATH) -> FastAPI:
    """建立完全離線的原型；測試可傳入暫存資料位置。"""
    if any(
        os.environ.get(key)
        for key in ("VERCEL", "VERCEL_ENV", "AWS_LAMBDA_FUNCTION_NAME", "FUNCTIONS_WORKER_RUNTIME")
    ):
        raise RuntimeError("供應商模擬入口只供本機使用，不可部署")
    service = SupplierService(SupplierDemoRepository(data_path))

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        service.initialize()
        yield

    application = FastAPI(
        title="供應商管理 模擬模式",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.suppliers = service
    application.state.csrf = token_urlsafe(32)
    templates = Jinja2Templates(directory=ROOT / "app" / "templates")
    templates.env.globals.update(
        SITE_NAME="InvoiceGuard 後台 模擬模式",
        SITE_ICON="icons/favicon.svg",
        ROBOTS_POLICY="noindex, nofollow",
        static_url=lambda path: "/static/" + path,
    )
    application.state.templates = templates
    application.add_middleware(
        TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"]
    )

    @application.middleware("http")
    async def local_only(request: Request, call_next):
        peer = request.client.host if request.client else ""
        if peer not in {"127.0.0.1", "::1"}:
            return HTMLResponse("模擬模式僅允許本機連線", status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and (
                urlsplit(origin).netloc != request.url.netloc
                or urlsplit(origin).scheme != request.url.scheme
            ):
                return HTMLResponse("不接受其他網站送出的模擬操作", status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; font-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
        )
        return response

    @application.get("/", include_in_schema=False)
    def home() -> RedirectResponse:
        return RedirectResponse("/suppliers", status_code=307)

    application.mount("/static", StaticFiles(directory=ROOT / "app" / "static"), name="static")
    application.include_router(router)
    return application


app = create_app()
