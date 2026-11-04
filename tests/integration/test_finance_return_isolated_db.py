"""測試目標：財務退回在「真實 PostgreSQL」上的交易一致性與錯誤分類（隔離庫，不連任何正式服務）。

涵蓋：正常退回（狀態＋歷程＋結構化退件項目同交易，之後 load() 讀得到）；Migration 未套用（缺表）；
權限不足；RLS 拒絕；CHECK 約束拒絕——後三者都必須整筆回滾（案件維持原狀、沒有歷程、沒有半套退件項目），
並回報正確的失敗分類與可對照的日誌（只含階段、SQLSTATE、結構名稱，不含發票內容）。

需要兩個「僅本機回送位址」的一次性資料庫（由人工或 API 的 tests/integration/db_lifecycle.py 建立）：
    ISOLATED_FINANCE_MIGRATED_URL  已載入基線並套用重送 Migration 7c1d4e9a2b30
    ISOLATED_FINANCE_BASELINE_URL  只載入基線（模擬 Migration 尚未套用的環境）
兩者都需要可建立角色的帳號（測試會建立並移除自己的臨時角色）。未設定則略過。
"""

import json
import logging
import os
import secrets
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.schemas.finance_review import ReturnItem
from app.services import finance_review_db_service as svc
from app.services.finance_review_service import return_to_applicant

LOCAL = {"127.0.0.1", "::1", "localhost"}


@pytest.fixture(autouse=True)
def block_external_services() -> None:
    """覆寫 tests/conftest.py 的同名保護：本模組只會連到環境變數指定的「本機回送位址」一次性資料庫
    （_engine 會拒絕其他主機；未設定環境變數時在連線前就略過），因此不套用全域的連線封鎖。"""


def _engine(name: str) -> Engine:
    raw = os.environ.get(name, "")
    if not raw:
        pytest.skip(f"未設定 {name}；略過隔離資料庫整合測試")
    url = make_url(raw)
    assert url.host in LOCAL and not url.query.get("host"), "只允許本機回送位址的一次性資料庫"
    return create_engine(url)


@pytest.fixture
def migrated() -> Iterator[Engine]:
    engine = _engine("ISOLATED_FINANCE_MIGRATED_URL")
    yield engine
    engine.dispose()


@pytest.fixture
def baseline() -> Iterator[Engine]:
    engine = _engine("ISOLATED_FINANCE_BASELINE_URL")
    yield engine
    engine.dispose()


def _seed(engine: Engine) -> tuple[str, str]:
    """建立一張待財務覆核的案件（第 1 輪、1 筆明細、1 筆欄位確認列）；回傳 (invoice_id, finance_user_id)。"""
    tag = uuid4().hex[:8]
    with engine.begin() as conn:
        dep = conn.execute(
            text(
                "INSERT INTO public.departments(department_code, department_name)"
                " VALUES (:c, '測試部') RETURNING department_id"
            ),
            {"c": f"f_{tag}"},
        ).scalar_one()
        users = []
        for role in ("sub", "fin"):
            uid = conn.execute(
                text(
                    "INSERT INTO invoiceguard_identity.user_accounts(email, password_hash, account_status,"
                    " primary_department_id) VALUES (:e, :h, 'active', :d) RETURNING user_id"
                ),
                {
                    "e": f"{role}_{tag}@example.test",
                    "h": "$argon2id$v=19$m=65536,t=3,p=4$" + "A" * 22 + "$" + "B" * 22,
                    "d": dep,
                },
            ).scalar_one()
            conn.execute(
                text("INSERT INTO public.user_profiles(user_id, display_name) VALUES (:u, :n)"),
                {"u": uid, "n": role},
            )
            conn.execute(
                text("INSERT INTO public.user_departments(user_id, department_id) VALUES (:u, :d)"),
                {"u": uid, "d": dep},
            )
            users.append(uid)
        number = f"{secrets.randbelow(10**8):08d}"
        invoice_id = conn.execute(
            text(
                "INSERT INTO public.invoices(invoice_track, invoice_number, invoice_date, tax_period,"
                " seller_tax_id, seller_name, buyer_tax_id, buyer_name, sales_amount, tax_amount, total_amount,"
                " tax_type, status, submission_round, validation_results, submitted_by, department_id,"
                " submitted_at) VALUES ('AB', :n, '2026-10-15', '202610', '12345675', '甲', '27601907', '乙',"
                " 1000, 50, 1050, 'taxable', 'pending_accounting_review', 1, CAST('{}' AS jsonb), :u, :d,"
                " now()) RETURNING invoice_id"
            ),
            {"n": number, "u": users[0], "d": dep},
        ).scalar_one()
        conn.execute(
            text(
                "INSERT INTO public.invoice_status_history(invoice_id, old_status, new_status, changed_by)"
                " VALUES (:i, NULL, 'pending_accounting_review', :u)"
            ),
            {"i": invoice_id, "u": users[0]},
        )
        conn.execute(
            text(
                "INSERT INTO public.invoice_field_reviews(invoice_id, submission_round, field_scope,"
                " field_name, ai_original_value, submitted_value) VALUES (:i, 1, 'header', 'tax_amount',"
                " CAST('\"50\"' AS jsonb), CAST('\"50\"' AS jsonb))"
            ),
            {"i": invoice_id},
        )
    return str(invoice_id), str(users[1])


def _return_change(engine: Engine, invoice_id: str, finance: str, *, field: str = "tax_amount"):
    loaded = svc.load(invoice_id, session_factory=lambda: Session(engine))
    assert loaded is not None
    change = return_to_applicant(
        loaded.case,
        finance,
        [ReturnItem(field, "測試退回原因", None, ("total_amount",))],
        datetime.now(UTC),
    )
    return loaded.case, change


def _state(engine: Engine, invoice_id: str) -> tuple[str, int, int | None]:
    with engine.connect() as conn:
        status = conn.execute(
            text("SELECT status FROM public.invoices WHERE invoice_id = :i"), {"i": invoice_id}
        ).scalar_one()
        history = conn.execute(
            text("SELECT count(*) FROM public.invoice_status_history WHERE invoice_id = :i"),
            {"i": invoice_id},
        ).scalar_one()
        has_table = conn.execute(
            text("SELECT to_regclass('public.invoice_return_items') IS NOT NULL")
        ).scalar_one()
        items = (
            conn.execute(
                text("SELECT count(*) FROM public.invoice_return_items WHERE invoice_id = :i"),
                {"i": invoice_id},
            ).scalar_one()
            if has_table
            else None
        )
    return status, history, items


def _commit(engine: Engine, case, change) -> None:  # noqa: ANN001
    svc.commit_change(
        change, case=case, tax_type=case.tax_type, session_factory=lambda: Session(engine)
    )


def _assert_untouched(engine: Engine, invoice_id: str, items: int | None) -> None:
    assert _state(engine, invoice_id) == ("pending_accounting_review", 1, items)


def test_return_is_saved_in_one_transaction_and_is_readable(migrated: Engine) -> None:
    invoice_id, finance = _seed(migrated)
    case, change = _return_change(migrated, invoice_id, finance)

    _commit(migrated, case, change)

    assert _state(migrated, invoice_id) == ("returned_to_applicant", 2, 1)
    with migrated.connect() as conn:
        row = conn.execute(
            text(
                "SELECT item_kind, field_name, allowed_fields, status FROM public.invoice_return_items"
                " WHERE invoice_id = :i"
            ),
            {"i": invoice_id},
        ).one()
        old_status = conn.execute(
            text(
                "SELECT old_status FROM public.invoice_status_history WHERE invoice_id = :i"
                " AND new_status = 'returned_to_applicant'"
            ),
            {"i": invoice_id},
        ).scalar_one()
    assert row[0] == "field" and row[1] == "tax_amount" and row[3] == "open"
    assert row[2] == [{"field_scope": "header", "field_name": "total_amount"}]
    assert old_status == "pending_accounting_review"  # 重送要靠它回到來源節點
    loaded = svc.load(invoice_id, session_factory=lambda: Session(migrated))
    assert loaded is not None and [i["field_name"] for i in loaded.return_items] == ["tax_amount"]


def test_missing_migration_refuses_the_return_cleanly_and_classifies_it(
    baseline: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    invoice_id, finance = _seed(baseline)
    case, change = _return_change(baseline, invoice_id, finance)  # 讀取在缺表時仍可運作

    with caplog.at_level(logging.ERROR), pytest.raises(svc.ReviewUnavailableError) as raised:
        _commit(baseline, case, change)

    assert raised.value.reason == "schema_missing" and len(raised.value.trace_id) == 8
    _assert_untouched(baseline, invoice_id, None)  # 狀態沒變、沒有歷程、也沒有半套資料
    log = next(
        r.getMessage() for r in caplog.records if "finance_review_db_failure" in r.getMessage()
    )
    assert "stage=insert_return_items" in log and "sqlstate=42P01" in log
    assert raised.value.trace_id in log and invoice_id in log
    assert "測試退回原因" not in log and "12345675" not in log  # 日誌不含原因或發票內容


def test_approve_still_works_when_migration_is_missing(baseline: Engine) -> None:
    """核准不寫退件項目，Migration 未套用時不受影響（只有「退回」需要新資料表）。"""
    from app.services.finance_review_service import approve

    invoice_id, finance = _seed(baseline)
    loaded = svc.load(invoice_id, session_factory=lambda: Session(baseline))
    assert loaded is not None
    case = loaded.case.__class__(**{**loaded.case.__dict__, "deduction_code": "1"})
    change = approve(case, finance, datetime.now(UTC))
    _commit(baseline, case, change)
    assert _state(baseline, invoice_id)[0] == "approved"


@pytest.fixture
def restricted_role(migrated: Engine) -> Iterator[str]:
    """臨時登入角色：對 public 表有 SELECT／INSERT／UPDATE；測試依情境再收回權限或加上 RLS。"""
    role = f"ig_fin_{secrets.token_hex(4)}"
    with migrated.begin() as conn:
        conn.exec_driver_sql(f"CREATE ROLE {role} LOGIN PASSWORD 'pw_{role}'")
        conn.exec_driver_sql(f"GRANT USAGE ON SCHEMA public, invoiceguard_internal TO {role}")
        conn.exec_driver_sql(
            f"GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public TO {role}"
        )
        conn.exec_driver_sql(
            f"GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA invoiceguard_internal TO {role}"
        )
    yield role
    with migrated.begin() as conn:
        conn.exec_driver_sql(f"DROP OWNED BY {role}")
        conn.exec_driver_sql(f"DROP ROLE {role}")


def _as_role(migrated: Engine, role: str) -> Engine:
    url = migrated.url.set(username=role, password=f"pw_{role}")
    return create_engine(url)


def test_missing_insert_privilege_is_classified_and_rolled_back(
    migrated: Engine, restricted_role: str, caplog: pytest.LogCaptureFixture
) -> None:
    with migrated.begin() as conn:
        conn.exec_driver_sql(f"ALTER ROLE {restricted_role} BYPASSRLS")  # 只測「缺 INSERT 權限」
        conn.exec_driver_sql(f"REVOKE INSERT ON public.invoice_return_items FROM {restricted_role}")
        conn.exec_driver_sql(f"GRANT SELECT ON public.invoice_return_items TO {restricted_role}")
    invoice_id, finance = _seed(migrated)
    runtime = _as_role(migrated, restricted_role)
    try:
        case, change = _return_change(runtime, invoice_id, finance)
        with caplog.at_level(logging.ERROR), pytest.raises(svc.ReviewUnavailableError) as raised:
            _commit(runtime, case, change)
    finally:
        runtime.dispose()
    assert raised.value.reason == "permission_denied"
    _assert_untouched(migrated, invoice_id, 0)
    log = " ".join(r.getMessage() for r in caplog.records)
    assert "stage=insert_return_items" in log and "sqlstate=42501" in log


def test_row_level_security_denial_is_classified_and_rolled_back(
    migrated: Engine, restricted_role: str, caplog: pytest.LogCaptureFixture
) -> None:
    with migrated.begin() as conn:
        for table in (
            "invoices",
            "invoice_status_history",
            "invoice_field_reviews",
            "invoice_items",
        ):
            conn.exec_driver_sql(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
            conn.exec_driver_sql(
                f"CREATE POLICY p_{restricted_role} ON public.{table} FOR ALL TO {restricted_role}"
                " USING (true) WITH CHECK (true)"
            )
        # invoice_return_items 維持「預設拒絕」（RLS 已啟用、沒有給此角色的 policy）
    invoice_id, finance = _seed(migrated)
    runtime = _as_role(migrated, restricted_role)
    try:
        case, change = _return_change(runtime, invoice_id, finance)
        with caplog.at_level(logging.ERROR), pytest.raises(svc.ReviewUnavailableError) as raised:
            _commit(runtime, case, change)
    finally:
        runtime.dispose()
    assert raised.value.reason == "permission_denied"
    _assert_untouched(migrated, invoice_id, 0)
    assert "sqlstate=42501" in " ".join(r.getMessage() for r in caplog.records)


def test_check_constraint_failure_rolls_back_status_and_history(
    migrated: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    invoice_id, finance = _seed(migrated)
    case, change = _return_change(migrated, invoice_id, finance)
    bad = change.__class__(
        **{
            **change.__dict__,
            "return_items": (
                ReturnItem("tax_amount", "   ", None, ()),
            ),  # 空白原因：資料庫 CHECK 拒絕
        }
    )
    with caplog.at_level(logging.ERROR), pytest.raises(svc.ReviewUnavailableError) as raised:
        _commit(migrated, case, bad)
    assert raised.value.reason == "unavailable"
    _assert_untouched(migrated, invoice_id, 0)
    log = " ".join(r.getMessage() for r in caplog.records)
    assert "stage=insert_return_items" in log and "sqlstate=23" in log
    assert json.dumps("   ") not in log
