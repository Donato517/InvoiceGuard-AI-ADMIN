"""由「API 的真實 submit() 服務」把測試發票寫入隔離資料庫（不連正式環境）。

用法（以 API 專案的 .venv，工作目錄為 API 根目錄）：
    M1_SEED_DB_URL=<db_url> .venv/bin/python -B m1_seed_via_api.py <tag> <out.json>
資料庫 URL 與標記只經環境變數傳入（不放命令列參數），子程序環境是白名單（見 m1_isolation.seed_environment）。
寫入：採購部使用者的完整發票（C1）與缺買受人／賣方名稱的稀疏發票（C3）、業務部使用者的發票（C2）。
"""

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

# 先於任何 API 模組匯入：驗證資料庫標記並安裝 Python 層連線封鎖（只放行這個資料庫身分）。
# 這是 monkeypatch，不是 OS／容器層的網路隔離；它不防原生函式庫或額外啟動的外部程序。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # tests/（network_guard）
import m1_isolation  # noqa: E402

_URL = m1_isolation.load_target(  # 標記不符或 URL 不安全：在任何匯入與寫入之前直接失敗
    {
        m1_isolation.URL_ENV: os.environ[m1_isolation.SEED_URL_ENV],
        m1_isolation.MARKER_ENV: os.environ.get(m1_isolation.MARKER_ENV, ""),
    }
)
assert _URL is not None
m1_isolation.install_process_guard(_URL)

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db.repositories import recognition_repository as rr  # noqa: E402
from app.schemas.auth import DepartmentRead, SessionRead  # noqa: E402
from app.schemas.invoice_upload import InvoiceSubmitRequest  # noqa: E402
from app.services import invoice_upload_service as svc  # noqa: E402

DB_URL = os.environ[m1_isolation.SEED_URL_ENV]
TAG, OUT = sys.argv[1], sys.argv[2]
HASH = "$argon2id$v=19$m=65536,t=3,p=4$" + "A" * 22 + "$" + "B" * 22
engine = create_engine(DB_URL)


def identity(db: Session, code: str, name: str, email: str) -> tuple[UUID, UUID]:
    dept = db.execute(
        text(
            "INSERT INTO public.departments(department_code, department_name) VALUES (:c, :n)"
            " RETURNING department_id"
        ),
        {"c": code, "n": name},
    ).scalar_one()
    user = db.execute(
        text(
            "INSERT INTO invoiceguard_identity.user_accounts(email, password_hash, account_status,"
            " primary_department_id) VALUES (:e, :h, 'active', :d) RETURNING user_id"
        ),
        {"e": email, "h": HASH, "d": dept},
    ).scalar_one()
    db.execute(
        text("INSERT INTO public.user_profiles(user_id, display_name) VALUES (:u, :n)"),
        {"u": user, "n": email.split("@")[0]},
    )
    db.execute(
        text("INSERT INTO public.user_departments(user_id, department_id) VALUES (:u, :d)"),
        {"u": user, "d": dept},
    )
    return user, dept


def submit(
    db: Session, user: UUID, dept: UUID, dept_name: str, number: str, *, sparse: bool
) -> dict:
    rec = uuid4()
    path = f"{user}/INVOICES_20261102100000_{rec}.jpg"
    rr.insert(
        db,
        recognition_id=rec,
        source_storage_path=path,
        ai_provider="test-double",
        requested_by=user,
    )
    rr.complete(
        db,
        rec,
        model_name="test-model",
        is_triplicate=True,
        confidence_score=0.9,
        extracted_fields={"invoice": {}},
        raw_response={"source": {"filename": "inv.jpg", "size_bytes": 12}},
        result_status="success",
        error_message=None,
    )
    db.commit()
    body = {
        "recognition_id": str(rec),
        "invoice_track": "AB",
        "invoice_number": number,
        "invoice_date": "2024-08-15",
        "seller_tax_id": "04595257",
        "tax_type": "taxable",
        "sales_amount": "3000.50" if not sparse else "1000",
        "tax_amount": "150" if not sparse else "50",
        "total_amount": "3150.50" if not sparse else "1050",
        "items": [
            {
                "item_name": "辦公文具",
                "quantity": "2.5",
                "unit_price": "1000",
                "line_amount": "2500",
            },
            {
                "item_name": "影印紙",
                "quantity": "1",
                "unit_price": "500.5",
                "line_amount": "500.50",
            },
        ]
        if not sparse
        else [{"item_name": "運費", "line_amount": "1000"}],  # 缺數量與單價
    }
    if not sparse:
        body.update(
            seller_name="ABC股份有限公司",
            buyer_tax_id="27601907",
            buyer_name="買方測試公司",
            total_amount_zh="參仟壹佰伍拾元整",
        )
    request = InvoiceSubmitRequest.model_validate(body)
    current = SessionRead(
        user_id=user,
        email="x@example.test",
        display_name="x",
        department=DepartmentRead(
            department_id=dept, department_code="c", department_name=dept_name, is_primary=True
        ),
        departments=(),
        expires_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    result = svc.submit(db, current, request)
    return {
        "invoice_id": str(result.invoice_id),
        "status": result.status,
        "user": str(user),
        "dept": str(dept),
        "number": f"AB{number}",
        "recognition_id": str(rec),
    }


with Session(engine) as db:
    p_user, p_dept = identity(db, f"m1p_{TAG}", "採購部M1", f"m1p_{TAG}@example.test")
    s_user, s_dept = identity(db, f"m1s_{TAG}", "業務部M1", f"m1s_{TAG}@example.test")
    db.commit()
    out = {
        "c1_full": submit(
            db, p_user, p_dept, "採購部M1", f"{int(TAG, 16) % 10**8:08d}", sparse=False
        ),
        "c3_sparse": submit(
            db, p_user, p_dept, "採購部M1", f"{(int(TAG, 16) + 1) % 10**8:08d}", sparse=True
        ),
        "c2_other": submit(
            db, s_user, s_dept, "業務部M1", f"{(int(TAG, 16) + 2) % 10**8:08d}", sparse=False
        ),
        "users": {"purchaser": str(p_user), "sales": str(s_user)},
        "depts": {"purchaser": str(p_dept), "sales": str(s_dept)},
    }
with open(OUT, "w", encoding="utf-8") as handle:
    json.dump(out, handle, ensure_ascii=False, indent=2)
