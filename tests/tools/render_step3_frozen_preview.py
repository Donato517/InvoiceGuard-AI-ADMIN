"""模組名稱：tests.tools.render_step3_frozen_preview；功能說明：離線合成步驟 3 凍結分欄的瀏覽器驗收頁。

用途：以既有 TestClient＋假 API（httpx.MockTransport）渲染步驟 3 的 HTML，輸出到忽略版控的
tmp/frozen-split/，再由本機靜態伺服器（只服務該資料夾，只綁 127.0.0.1）供瀏覽器載入。
安全：沿用 tests/conftest.py 的外部網路／資料庫封鎖；只用程式產生的合成圖片與 PDF，不讀任何實際發票、
Token 或 Session，不連資料庫、Storage、AI 或 API。
執行：python -B tests/tools/render_step3_frozen_preview.py
"""

import copy
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest


def load(name: str, path: Path) -> ModuleType:
    """載入既有測試模組（沿用其合成資料與封鎖外部服務機制）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load offline fixture")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def synthetic_png() -> bytes:
    """產生合成的直式「發票」影像（純色底與線條，沒有任何實際資料）。"""
    from io import BytesIO

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (1000, 1500), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 20, 980, 1480), outline="black", width=6)
    draw.text((60, 60), "SYNTHETIC SAMPLE INVOICE - TOP", fill="black")
    for y in range(160, 1400, 80):
        draw.line((60, y, 940, y), fill="gray", width=2)
    draw.text((60, 1420), "SYNTHETIC SAMPLE INVOICE - BOTTOM", fill="black")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def synthetic_pdf() -> bytes:
    """產生最小的單頁合成 PDF。"""
    content = b"BT /F1 36 Tf 60 760 Td (SYNTHETIC SAMPLE PDF) Tj 0 -60 Td (page text line) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(content), content),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(out)


def main() -> None:
    """輸出多種情境的合成頁面。"""
    import httpx

    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "tests/unit"))
    conftest = load("frozen_conftest", root / "tests/conftest.py")
    routes = load("frozen_intake_routes", root / "tests/unit/test_intake_routes.py")
    from app.routers import invoices as inv

    output = root / "tmp/frozen-split"
    output.mkdir(parents=True, exist_ok=True)
    static_link = output / "static"
    if not static_link.exists():
        static_link.symlink_to(root / "app/static", target_is_directory=True)
    png, pdf = synthetic_png(), synthetic_pdf()
    (output / "sample.png").write_bytes(png)
    (output / "sample.pdf").write_bytes(pdf)

    def rewrite(html: str, media: str) -> str:
        """把原檔網址改成本機合成素材；其餘 /static/ 由靜態伺服器提供。"""
        return re.sub(r"/invoices/[^\"'/]+/file", f"/sample.{media}", html)

    patch = pytest.MonkeyPatch()
    try:
        conftest.block_external_services.__wrapped__(patch)
        conftest.no_api_login.__wrapped__(patch)
        gen = conftest.admin_client.__wrapped__()
        client = next(gen)

        def make_api(items: int = 0, long_names: bool = False, fail_check: bool = False):
            fake = routes.FakeApi()
            payload = copy.deepcopy(routes.RECOGNITION)
            if items:
                base = payload["invoice"]["items"][0]
                payload["invoice"]["items"] = [
                    {
                        **base,
                        "item_name": (
                            f"超長品名測試項目{n}" + "ABCDEFGHIJ" * 8 if long_names else f"品項{n}"
                        ),
                    }
                    for n in range(items)
                ]
            fake.routes = {
                "/api/v1/invoices/recognize": httpx.Response(200, json=payload),
                "/api/v1/invoices/check-required": httpx.Response(
                    500 if fail_check else 200, json={"required_complete": True, "missing": []}
                ),
                "/api/v1/invoices/check-warnings": httpx.Response(
                    200, json={"warnings": [], "checked_on": "2026-11-02"}
                ),
                "/api/v1/invoices/check-duplicate": httpx.Response(
                    200, json={"is_duplicate": False}
                ),
            }
            patch.setattr(inv, "get_intake_client", lambda request, f=fake: f.client())

        def render(name: str, media: str, upload: tuple, check: bool = False, **kw) -> None:
            inv._DRAFT_INVOICE_STORE.clear()
            make_api(**kw)
            draft = client.get("/invoices/new").headers["location"].split("/")[2]
            response = client.post(
                f"/invoices/{draft}/flow/upload",
                headers={"Origin": "http://testserver"},
                files={"file": upload},
            )
            if response.status_code != 303:
                raise RuntimeError(f"{name}: upload failed {response.status_code}")
            if check:
                page = client.post(
                    f"/invoices/{draft}/flow/review",
                    headers={"Origin": "http://testserver"},
                    data=routes.review_data("check", recognition_id=routes.RID),
                )
            else:
                page = client.get(f"/invoices/{draft}/flow?step=3")
            if page.status_code != 200 or "correction-split--frozen" not in page.text:
                raise RuntimeError(f"{name}: unexpected page {page.status_code}")
            (output / f"{name}.html").write_text(rewrite(page.text, media), encoding="utf-8")

        image = ("invoice.png", png, "image/png")
        render("step3-image", "png", image)
        render("step3-many-long", "png", image, items=10, long_names=True)
        render("step3-banner", "png", image, check=True, fail_check=True)
        render("step3-pdf", "pdf", ("invoice.pdf", pdf, "application/pdf"))

        # 步驟 3 舊表單分支（非 API 辨識的示範發票）；補上合成原圖
        inv._INVOICE_STORE.clear()
        inv._INVOICE_STORE.extend(copy.deepcopy(inv.DEMO_TEST_INVOICES))
        for item in inv._INVOICE_STORE:
            if str(item.get("id")) == "3":
                item["file_bytes"], item["file_media_type"] = png, "image/png"
        legacy = client.get("/invoices/3/flow?step=3")
        if legacy.status_code != 200 or "flow-correct-form" not in legacy.text:
            raise RuntimeError("legacy branch not rendered")
        (output / "step3-legacy.html").write_text(rewrite(legacy.text, "png"), encoding="utf-8")
        # 步驟 5 財務審核（用來確認新 CSS 沒有改變 .fr-split 版面）
        for item in list(inv._INVOICE_STORE):
            item["file_bytes"], item["file_media_type"] = png, "image/png"
            page = client.get(f"/invoices/{item['id']}/flow?step=5")
            if page.status_code == 200 and "fr-split" in page.text:
                (output / "step5.html").write_text(rewrite(page.text, "png"), encoding="utf-8")
                break
        # 退回補正：先以財務人員退回示範發票 3，再以申請人身分渲染步驟 3（退回補正區塊在分欄上方）
        from types import SimpleNamespace

        from fastapi import Request

        from app.core.deps import require_admin
        from app.main import app

        def login_as(user_id: str) -> None:
            def override(request: Request) -> SimpleNamespace:
                admin = SimpleNamespace(
                    id=user_id, display_name="測試人員", role="admin", is_active=True
                )
                request.state.admin = admin
                return admin

            app.dependency_overrides[require_admin] = override

        procurement = load("frozen_procurement", root / "tests/unit/test_procurement_pages.py")
        login_as(procurement.FINANCE_USER)
        returned = client.post(
            "/invoices/3/finance-review/return",
            data={
                "return_field": ["total_amount", "attachment"],
                "return_reason": ["請依發票修正總計", "補上採購單"],
            },
        )
        login_as("1")
        page = client.get("/invoices/3/flow?step=3")
        if returned.status_code != 303 or "退回補正" not in page.text:
            raise RuntimeError("returned-case page not rendered")
        (output / "step3-legacy-return.html").write_text(
            rewrite(page.text, "png"), encoding="utf-8"
        )
        # 退回補正頁（/invoices/{id}/correct）：可補正（含多筆退回原因）與不可補正（終止）兩種
        corr = load("frozen_correction", root / "tests/unit/test_invoice_correction.py")
        corr_api = corr.api.__wrapped__()
        corr_env = corr.env.__wrapped__(corr_api, patch)
        corr_env.invoice["file_bytes"], corr_env.invoice["file_media_type"] = png, "image/png"
        browser = corr._as(corr.APPLICANT, client)
        many = [
            {
                "return_item_id": f"0000000{n}-1111-4222-8333-444455556666",
                "item_kind": "field",
                "target": corr._ref("header", "total_amount"),
                "reason": f"退回原因 {n}：總計與明細不符，請重新核對並補正" * 2,
                "allowed_fields": [],
                "status": "open",
            }
            for n in range(1, 6)
        ]
        for name, body in (
            ("correct-editable", corr.return_items_json()),
            ("correct-many-reasons", corr.return_items_json(return_items=many)),
            (
                "correct-void",
                corr.return_items_json(
                    can_resubmit=False,
                    requires_void=True,
                    editable_fields=[],
                    return_items=[
                        {
                            "return_item_id": corr.OTHER_ID,
                            "item_kind": "image",
                            "target": None,
                            "reason": "影像模糊",
                            "allowed_fields": [],
                            "status": "open",
                        }
                    ],
                ),
            ),
        ):
            corr_api.responses["return-items"] = httpx.Response(200, json=body)
            page = browser.get(f"/invoices/{corr.INVOICE_ID}/correct")
            if page.status_code != 200 or "return-reasons" not in page.text:
                raise RuntimeError(f"{name}: unexpected page {page.status_code}")
            (output / f"{name}.html").write_text(rewrite(page.text, "png"), encoding="utf-8")
        gen.close()
    finally:
        patch.undo()
    print(f"Synthetic step 3 pages ready in {output}; production DB and external network blocked")


if __name__ == "__main__":
    main()
