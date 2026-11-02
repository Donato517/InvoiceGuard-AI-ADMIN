"""測試目標：M1——API 真實 submit() 寫入的發票，在 ADMIN（A 組 main）以相同 invoice_id 讀取的整合驗證。

驗證層級（請勿混淆）：
  * 隔離 PostgreSQL：Schema v1.8（去 pgvector）；發票由「API 的真實 submit() 服務」寫入。
  * Repository／Service 層：直接呼叫 A 組的 app.db.repositories.invoice_repository 與 invoice_review_service。
  * HTTP（TestClient）：只有財務工作區（測試替身登入，非正式授權）；以 monkeypatch 讓 ADMIN 使用隔離庫連線。
  * 不涵蓋：正式 DB／Storage、正式 E 組授權、真實瀏覽器。
標記 xfail(strict=True) 的測試＝尚未修復、另案處理的已知缺陷（期望行為目前會失敗）；修復後必須移除標記。
已於 M1 安全修補中修復的缺陷（狀態映射、範例值填補、UPSERT 覆寫）已改為正常斷言。
執行：ISOLATED_CASE_DB_URL=postgresql+psycopg://…@127.0.0.1:55432/ig_test ISOLATED_CASE_DB_MARKER=<受控標記> pytest tests/integration/test_m1_real_read_isolated_db.py
"""

import hashlib
import json
import subprocess
from collections.abc import Iterator
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import m1_isolation
import pytest
from fastapi import Request
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from app.db.repositories import invoice_repository as repo
from app.routers import invoices as inv_router
from app.schemas.e_admin import DeniedError
from app.services import invoice_review_service
from app.services.format21_service import check_snapshot

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT.parent / "InvoiceGuard-AI-API"
SEED = Path(__file__).with_name("m1_seed_via_api.py")
NEW, LEGACY = "pending_accounting_review", "pending_department_approval"


@pytest.fixture(autouse=True)
def seed_test_invoices_fixture() -> None:
    """覆寫根 conftest 的示範發票注入：本檔只看資料庫內的真實資料，不混入記憶體示範資料。"""
    inv_router._INVOICE_STORE.clear()


@pytest.fixture(scope="module")
def m1(
    isolated_engine: Engine, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[dict[str, Any]]:
    if not (API_ROOT / ".venv/bin/python").exists():
        pytest.skip("找不到同層 API 專案的 .venv（需要它來執行真實 submit()）")
    tag = uuid4().hex[:8]
    out = tmp_path_factory.mktemp("m1_seed") / f"{tag}.json"
    done = subprocess.run(  # noqa: S603
        [str(API_ROOT / ".venv/bin/python"), "-B", str(SEED), tag, str(out)],
        cwd=API_ROOT,
        env=m1_isolation.seed_environment(API_ROOT, isolated_engine.url),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr[-1500:]
    data = json.loads(out.read_text(encoding="utf-8"))
    yield data
    ids = [data[k]["invoice_id"] for k in ("c1_full", "c2_other", "c3_sparse")]
    with isolated_engine.begin() as conn:
        for table in ("invoice_field_reviews", "invoice_changes"):
            conn.execute(
                text(f"DELETE FROM public.{table} WHERE invoice_id = ANY(CAST(:i AS uuid[]))"),
                {"i": ids},
            )
        conn.execute(
            text(
                "DELETE FROM public.ai_recognition_logs WHERE requested_by = ANY(CAST(:u AS uuid[]))"
            ),
            {"u": list(data["users"].values())},
        )
        conn.execute(
            text("DELETE FROM public.invoices WHERE invoice_id = ANY(CAST(:i AS uuid[]))"),
            {"i": ids},
        )
        conn.execute(
            text(
                "DELETE FROM invoiceguard_identity.user_accounts WHERE user_id = ANY(CAST(:u AS uuid[]))"
            ),
            {"u": list(data["users"].values())},
        )
        conn.execute(
            text("DELETE FROM public.departments WHERE department_id = ANY(CAST(:d AS uuid[]))"),
            {"d": list(data["depts"].values())},
        )


@pytest.fixture
def db(isolated_engine: Engine) -> Iterator[Session]:
    with Session(isolated_engine) as session:
        yield session


def fingerprint(engine: Engine, ids: list[str]) -> str:
    digest = hashlib.sha256()
    with engine.connect() as conn:
        for table, key in (
            ("invoices", "invoice_id"),
            ("invoice_items", "invoice_id"),
            ("invoice_files", "invoice_id"),
            ("invoice_status_history", "invoice_id"),
            ("invoice_field_reviews", "invoice_id"),
            ("invoice_changes", "invoice_id"),
        ):
            rows = conn.execute(
                text(
                    f"SELECT * FROM public.{table} WHERE {key} = ANY(CAST(:i AS uuid[])) ORDER BY 1"
                ),
                {"i": ids},
            ).mappings()
            for row in rows:
                digest.update(json.dumps(dict(row), default=str, sort_keys=True).encode())
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# 一、真實讀取：相同 invoice_id、欄位與狀態一致（Repository 層，隔離 PostgreSQL）
# ---------------------------------------------------------------------------


def test_admin_repository_reads_the_same_invoice_id_written_by_the_api(
    m1: dict[str, Any], db: Session
) -> None:
    c1 = m1["c1_full"]

    detail = repo.get_invoice_by_id(db, c1["invoice_id"])

    assert detail is not None
    assert detail["id"] == c1["invoice_id"]  # 真實 UUID，不是整數或 uuid5 轉換
    UUID(detail["id"])
    assert detail["invoice_number"] == c1["number"]
    assert (detail["seller_tax_id"], detail["buyer_tax_id"]) == ("04595257", "27601907")
    assert (detail["seller_name"], detail["buyer_name"]) == ("ABC股份有限公司", "買方測試公司")
    assert detail["uploaded_by"] == c1["user"]
    assert detail["file_name"] == "inv.jpg"
    assert c1["status"] == NEW  # 同層 API（W1）寫入的首次提交狀態


def test_the_api_status_is_kept_not_rewritten_by_the_reader(
    m1: dict[str, Any], db: Session
) -> None:
    raw = db.execute(
        text(
            "SELECT status, submission_round FROM public.invoices WHERE invoice_id = CAST(:i AS uuid)"
        ),
        {"i": m1["c1_full"]["invoice_id"]},
    ).one()
    detail = repo.get_invoice_by_id(db, m1["c1_full"]["invoice_id"])

    assert tuple(raw) == (NEW, 1)
    assert detail is not None
    # 原始狀態碼沒有出現在 dict（只剩推導出的 review_status／status）——見 test_raw_status_is_not_exposed
    assert detail["review_status"] == "pending"


def test_raw_status_and_department_are_exposed(m1: dict[str, Any], db: Session) -> None:
    detail = repo.get_invoice_by_id(db, m1["c1_full"]["invoice_id"])

    assert detail is not None
    assert detail["db_status"] == NEW  # 資料庫原始狀態碼，新舊待審狀態可區分
    assert detail["department_id"] == m1["c1_full"]["dept"]
    assert detail["department_name"] == "採購部M1"


def test_list_and_detail_agree_for_every_shared_field(m1: dict[str, Any], db: Session) -> None:
    listing = {i["id"]: i for i in repo.list_invoices(db, is_finance=True)}
    for key in ("c1_full", "c2_other", "c3_sparse"):
        invoice_id = m1[key]["invoice_id"]
        detail = repo.get_invoice_by_id(db, invoice_id)
        assert invoice_id in listing and detail is not None
        row = listing[invoice_id]
        for field in (
            "invoice_number",
            "seller_tax_id",
            "buyer_tax_id",
            "total_amount",
            "net_amount",
            "tax_amount",
            "uploaded_by",
            "review_status",
            "stage",
            "file_name",
        ):
            assert row[field] == detail[field], (key, field)


def test_items_are_read_from_the_database(m1: dict[str, Any], db: Session) -> None:
    detail = repo.get_invoice_by_id(db, m1["c1_full"]["invoice_id"])

    assert detail is not None
    assert "辦公文具" in detail["item_description"] and "影印紙" in detail["item_description"]


def test_non_finance_user_lists_only_their_own_invoices(m1: dict[str, Any], db: Session) -> None:
    mine = {
        i["id"] for i in repo.list_invoices(db, user_id=m1["users"]["purchaser"], is_finance=False)
    }
    theirs = {
        i["id"] for i in repo.list_invoices(db, user_id=m1["users"]["sales"], is_finance=False)
    }
    everyone = {i["id"] for i in repo.list_invoices(db, user_id="ignored", is_finance=True)}

    assert mine == {m1["c1_full"]["invoice_id"], m1["c3_sparse"]["invoice_id"]}
    assert theirs == {m1["c2_other"]["invoice_id"]}
    assert {m1[k]["invoice_id"] for k in ("c1_full", "c2_other", "c3_sparse")} <= everyone


def test_detail_access_decision_is_by_owner_or_finance_workspace(
    m1: dict[str, Any], db: Session
) -> None:
    other = repo.get_invoice_by_id(db, m1["c2_other"]["invoice_id"])
    assert other is not None  # Repository 本身不做擁有者過濾（讀得到任何 id）

    def request(user: str, finance: bool) -> Any:
        return SimpleNamespace(
            state=SimpleNamespace(invoice_user_id=user, is_finance_workspace=finance)
        )

    with pytest.raises(DeniedError):
        inv_router._check_invoice_access(request(m1["users"]["purchaser"], False), other)
    inv_router._check_invoice_access(request(m1["users"]["sales"], False), other)  # 本人
    inv_router._check_invoice_access(request("someone-else", True), other)  # 財務工作區


def test_unknown_and_malformed_ids(m1: dict[str, Any], db: Session) -> None:
    assert repo.get_invoice_by_id(db, str(uuid4())) is None
    assert invoice_review_service.fetch_invoice  # 公開介面存在
    with pytest.raises(Exception) as bad:  # 非 UUID 非數字：路由層會拒絕
        inv_router._find_invoice("not-a-uuid")
    assert getattr(bad.value, "status_code", None) == 400


def test_reading_changes_nothing_in_the_database(
    m1: dict[str, Any], db: Session, isolated_engine: Engine
) -> None:
    ids = [m1[k]["invoice_id"] for k in ("c1_full", "c2_other", "c3_sparse")]
    before = fingerprint(isolated_engine, ids)

    repo.list_invoices(db, is_finance=True)
    repo.list_invoices(db, user_id=m1["users"]["purchaser"], is_finance=False, status=NEW, q="AB")
    for invoice_id in ids:
        repo.get_invoice_by_id(db, invoice_id)
    db.rollback()

    assert fingerprint(isolated_engine, ids) == before


# ---------------------------------------------------------------------------
# 二、HTTP（TestClient，財務工作區測試替身登入）：列表與詳情
# ---------------------------------------------------------------------------


@pytest.fixture
def http(admin_client: Any, isolated_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(repo, "try_get_db_session", lambda: Session(isolated_engine))
    return admin_client


def test_http_list_and_detail_show_the_real_uuid(
    m1: dict[str, Any], http: Any, isolated_engine: Engine
) -> None:
    ids = [m1[k]["invoice_id"] for k in ("c1_full", "c2_other", "c3_sparse")]
    before = fingerprint(isolated_engine, ids)

    listing = http.get("/invoices")
    detail = http.get(f"/invoices/{m1['c1_full']['invoice_id']}")

    assert listing.status_code == 200 and detail.status_code == 200
    assert m1["c1_full"]["number"] in listing.text and m1["c1_full"]["number"] in detail.text
    assert fingerprint(isolated_engine, ids) == before  # HTTP 讀取沒有寫入


def test_http_unknown_uuid_is_404_and_malformed_id_is_400(http: Any) -> None:
    assert http.get(f"/invoices/{uuid4()}").status_code == 404
    assert http.get("/invoices/not-a-uuid").status_code == 400


def test_database_unavailable_silently_looks_like_an_empty_list(
    http: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """特徵測試：連不上資料庫時，列表顯示為「沒有資料」，不是錯誤（見報告 P1）。"""
    monkeypatch.setattr(repo, "try_get_db_session", lambda: None)

    assert invoice_review_service.fetch_invoices() is None
    assert inv_router._get_active_invoices() == []  # 回退到空的 _INVOICE_STORE
    response = http.get("/invoices")
    assert response.status_code == 200  # 沒有 5xx、沒有「資料庫無法連線」提示


# ---------------------------------------------------------------------------
# 三、風險 A：待審核狀態不得被當成已核准（純函式＋F09，Mock 層）
# ---------------------------------------------------------------------------


def _row(status: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "invoice_id": str(uuid4()),
        "status": status,
        "submission_round": 1,
        "invoice_track": "AB",
        "invoice_number": "12345678",
        "invoice_date": __import__("datetime").date(2024, 8, 15),
        "seller_tax_id": "27601907",
        "seller_name": "賣方",
        "buyer_tax_id": "04595252",  # 本公司統編（公司主檔）
        "buyer_name": "買方",
        "tax_type": "taxable",
        "sales_amount": Decimal("6000"),
        "tax_amount": Decimal("300"),
        "total_amount": Decimal("6300"),
        "total_amount_zh": "",
        "submitted_by": str(uuid4()),
        "original_filename": "a.jpg",
        "mime_type": "image/jpeg",
    }
    return {**base, **over}


def _exportable_issues(status: str) -> list[str]:
    inv = repo._row_to_invoice_dict(_row(status))
    _, issues = check_snapshot(
        inv_router._to_filing_snapshot(inv), inv_router._FILING_PROFILE.get_profile(), "11308"
    )
    return [i.code for i in issues]


def _issues_for(inv: dict[str, Any]) -> list[str]:
    _, issues = check_snapshot(
        inv_router._to_filing_snapshot(inv), inv_router._FILING_PROFILE.get_profile(), "11308"
    )
    return [i.code for i in issues]


def test_control_a_genuinely_approved_invoice_still_goes_through_the_f09_rules() -> None:
    """approved 仍按 F09 原有規則檢核：扣抵代號資料契約未核定前，真實案件缺扣抵代號會被如實擋下。"""
    inv = repo._row_to_invoice_dict(_row("approved"))

    assert "NOT_APPROVED" not in _issues_for(inv)  # 已核准，沒有被當成未核准
    assert _issues_for(inv) == ["BAD_DEDUCTION_CODE"]  # 缺扣抵代號：不再被假預設值掩蓋
    inv["deduction_code"] = "1 - 進貨及費用"  # 模擬 F 組資料契約核定後由資料庫提供
    assert _issues_for(inv) == []


@pytest.mark.parametrize(
    "status",
    [
        NEW,
        LEGACY,
        "pending_accounting_approval",
        "returned_to_applicant",
        "returned_to_accounting",
        "rejected",
        "voided",
        "draft",
    ],
)
def test_only_approved_invoices_can_pass_the_filing_approval_check(status: str) -> None:
    assert "NOT_APPROVED" in _exportable_issues(status), f"{status} 被視為已核准"


@pytest.mark.parametrize("status", [NEW, LEGACY, "pending_accounting_approval"])
def test_pending_statuses_stay_pending_and_keep_the_raw_status(status: str) -> None:
    inv = repo._row_to_invoice_dict(_row(status))

    assert (inv["review_status"], inv["status"], inv["db_status"]) == ("pending", "pending", status)
    assert inv["is_completed"] is False
    assert inv_router._to_filing_snapshot(inv)["status"] == status  # 不再被改寫成 approved


def test_a_display_status_of_passed_alone_never_makes_an_invoice_approved() -> None:
    """即使畫面狀態是 passed，只要資料庫原始狀態不是 approved，就不是已核准。"""
    inv = repo._row_to_invoice_dict(_row(NEW))
    inv["status"] = "passed"
    inv["review_status"] = "approved"  # 故意竄改畫面欄位

    assert inv_router._to_filing_snapshot(inv)["status"] != "approved"


def test_demo_invoices_without_a_db_status_keep_their_original_rule() -> None:
    """離線示範資料（沒有 db_status）仍依 review_status 判斷，不受影響。"""
    approved = {"id": 1, "review_status": "approved", "status": "passed"}
    pending = {"id": 2, "review_status": "pending", "status": "passed"}  # 舊規則會誤判

    assert inv_router._to_filing_snapshot(approved)["status"] == "approved"
    assert inv_router._to_filing_snapshot(pending)["status"] != "approved"


@pytest.mark.parametrize("status", ["returned_to_applicant", "returned_to_accounting"])
def test_returned_is_flagged_not_approved(status: str) -> None:
    inv = repo._row_to_invoice_dict(_row(status))

    assert (inv["review_status"], inv["status"], inv["db_status"]) == (
        "returned",
        "flagged",
        status,
    )


def test_voided_and_rejected_keep_distinct_raw_statuses() -> None:
    """畫面狀態仍同為 rejected（另案處理），但原始狀態碼已可區分申請人作廢與主管駁回。"""
    voided = repo._row_to_invoice_dict(_row("voided"))
    rejected = repo._row_to_invoice_dict(_row("rejected"))

    assert (voided["db_status"], rejected["db_status"]) == ("voided", "rejected")
    assert inv_router._to_filing_snapshot(voided)["status"] != "approved"


# ---------------------------------------------------------------------------
# 四、風險 B：真實缺值不得被範例值取代
# ---------------------------------------------------------------------------

SAMPLE_VALUES = {
    "範例科技股份有限公司",
    "範例銷貨人",
    "24681357",
    "12345678",
    "辦公用品",
    "公務使用",
    "5601 辦公用品費",
    "1 - 進貨及費用",
}


def test_the_sparse_real_invoice_shows_blanks_not_sample_values(
    m1: dict[str, Any], db: Session
) -> None:
    detail = repo.get_invoice_by_id(db, m1["c3_sparse"]["invoice_id"])
    raw = db.execute(
        text(
            "SELECT buyer_tax_id, buyer_name, seller_name FROM public.invoices"
            " WHERE invoice_id = CAST(:i AS uuid)"
        ),
        {"i": m1["c3_sparse"]["invoice_id"]},
    ).one()

    assert tuple(raw) == (None, None, None)  # 資料庫裡真的是空值
    assert detail is not None
    assert (detail["buyer_tax_id"], detail["buyer_name"], detail["seller_name"]) == ("", "", "")
    shown = {str(v) for v in detail.values() if isinstance(v, str)}
    assert not shown & SAMPLE_VALUES, shown & SAMPLE_VALUES


def test_accounting_fields_are_not_invented(m1: dict[str, Any], db: Session) -> None:
    detail = repo.get_invoice_by_id(db, m1["c3_sparse"]["invoice_id"])

    assert detail is not None
    assert detail["account_code"] == "" and detail["deduction_code"] == ""


def test_missing_quantity_and_unit_price_are_not_filled(m1: dict[str, Any], db: Session) -> None:
    detail = repo.get_invoice_by_id(db, m1["c3_sparse"]["invoice_id"])

    assert detail is not None
    assert detail["quantity"] == 0 and detail["unit_price"] == 0  # 不再被填成 1 與銷售額


def test_department_is_the_real_department(m1: dict[str, Any], db: Session) -> None:
    detail = repo.get_invoice_by_id(db, m1["c2_other"]["invoice_id"])

    assert detail is not None
    assert detail["department_id"] == m1["c2_other"]["dept"]
    assert detail["department_name"] == "業務部M1"


def test_null_tax_type_is_not_assumed_to_be_taxable() -> None:
    inv = repo._row_to_invoice_dict(_row("draft", tax_type=None))

    assert (inv["tax_category"], inv["tax_type"]) == ("", "")


def test_a_missing_owner_is_not_replaced_by_user_1() -> None:
    assert repo._row_to_invoice_dict(_row("draft", submitted_by=None))["uploaded_by"] == ""


def test_a_missing_tax_type_is_reported_by_f09_instead_of_assumed() -> None:
    inv = repo._row_to_invoice_dict(_row("approved", tax_type=None))
    _, issues = check_snapshot(
        inv_router._to_filing_snapshot(inv), inv_router._FILING_PROFILE.get_profile(), "11308"
    )

    assert issues  # 課稅別缺漏不再被推測為應稅而放行


def _approved_with(**amounts: Any) -> dict[str, Any]:
    inv = repo._row_to_invoice_dict(_row("approved", **amounts))
    inv["deduction_code"] = "1 - 進貨及費用"  # 模擬 F 組資料契約核定後由資料庫提供
    return inv


@pytest.mark.parametrize(
    ("label", "amounts", "expected"),
    [
        (
            "銷售額與稅額皆 NULL",
            {"sales_amount": None, "tax_amount": None},
            {"BAD_AMOUNT", "TAX_MISMATCH"},
        ),
        ("單獨缺銷售額", {"sales_amount": None}, {"BAD_AMOUNT", "TAX_MISMATCH"}),
        ("單獨缺稅額", {"tax_amount": None}, {"TAX_MISMATCH"}),
    ],
)
def test_null_amounts_are_rejected_by_f09_not_treated_as_zero(
    label: str, amounts: dict[str, Any], expected: set[str]
) -> None:
    assert expected <= set(_issues_for(_approved_with(**amounts))), label


def test_the_raw_filing_amounts_keep_none_and_zero_distinct() -> None:
    missing = repo._row_to_invoice_dict(_row("approved", sales_amount=None, tax_amount=None))
    zero = repo._row_to_invoice_dict(
        _row("approved", sales_amount=Decimal("0"), tax_amount=Decimal("0"))
    )

    assert (missing["sales_amount_db"], missing["tax_amount_db"]) == (None, None)
    assert (zero["sales_amount_db"], zero["tax_amount_db"]) == (Decimal("0"), Decimal("0"))
    assert zero["sales_amount_db"] is not None  # 真正的 0 不會被轉成 None
    # 畫面用的金額欄位維持原行為（NULL 顯示 0；精度另案處理）
    assert (missing["net_amount"], missing["tax_amount"]) == (0, 0)


def test_valid_non_zero_amounts_still_pass_with_database_scale() -> None:
    inv = _approved_with(
        sales_amount=Decimal("6000.00"),
        tax_amount=Decimal("300.00"),
        total_amount=Decimal("6300.00"),
    )

    assert _issues_for(inv) == []  # numeric(18,2) 的 Decimal 與原有檢核相容


def test_a_real_zero_amount_is_not_confused_with_a_missing_one() -> None:
    """對照：資料庫真的是 0 的金額維持原行為（F09 沒有『金額必須大於 0』規則，本測試固定現況）。"""
    inv = _approved_with(
        sales_amount=Decimal("0"), tax_amount=Decimal("0"), total_amount=Decimal("0")
    )

    assert _issues_for(inv) == []


def test_total_amount_alone_missing_is_not_checked_by_f09_today() -> None:
    """待確認（獨立事項，不擅自增加 F09 規則）：F09 快照沒有 total_amount，總計單獨為 NULL 不會被拒絕。"""
    inv = _approved_with(total_amount=None)

    assert _issues_for(inv) == []


def test_a_null_amount_does_not_unlock_export_of_a_non_approved_invoice() -> None:
    inv = repo._row_to_invoice_dict(_row(NEW, sales_amount=None, tax_amount=None))
    inv["deduction_code"] = "1 - 進貨及費用"

    assert "NOT_APPROVED" in _issues_for(inv)


def test_demo_invoices_without_db_amounts_keep_using_the_display_amounts() -> None:
    demo = {
        "id": 9,
        "review_status": "approved",
        "net_amount": 6000,
        "tax_amount": 300,
        "invoice_number": "AB12345678",
        "invoice_date": "113/08/15",
    }
    snap = inv_router._to_filing_snapshot(demo)

    assert (snap["sales_amount"], snap["tax_amount"]) == (6000, 300)


@pytest.mark.xfail(
    strict=True,
    reason="未修（另案）：金額與數量被 int() 截斷（3000.50→3000、2.5→2），且只取第一筆明細",
)
def test_amounts_and_quantities_keep_their_precision(m1: dict[str, Any], db: Session) -> None:
    detail = repo.get_invoice_by_id(db, m1["c1_full"]["invoice_id"])

    assert detail is not None
    assert Decimal(str(detail["net_amount"])) == Decimal("3000.50")
    assert Decimal(str(detail["total_amount"])) == Decimal("3150.50")
    assert Decimal(str(detail["quantity"])) == Decimal("2.5")


# ---------------------------------------------------------------------------
# 五、意外寫入：已提交案件不可經核對修正路徑被 UPSERT 覆寫（fail-closed）
# ---------------------------------------------------------------------------

PROTECTED_STATUSES = [
    NEW,
    LEGACY,
    "pending_accounting_approval",
    "returned_to_applicant",
    "returned_to_accounting",
    "approved",
    "rejected",
    "voided",
]


def _set_status(engine: Engine, invoice_id: str, status: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE public.invoices SET status = :s WHERE invoice_id = CAST(:i AS uuid)"),
            {"s": status, "i": invoice_id},
        )


def _protected_columns(engine: Engine, invoice_id: str) -> tuple[Any, ...]:
    with engine.connect() as conn:
        return tuple(
            conn.execute(
                text(
                    "SELECT status, submission_round, tax_period, validation_results, submitted_by,"
                    " department_id, submitted_at, updated_at, seller_name, buyer_tax_id,"
                    " total_amount FROM public.invoices WHERE invoice_id = CAST(:i AS uuid)"
                ),
                {"i": invoice_id},
            ).one()
        )


CORRECT_FORM = {"invoice_date": "113/08/15", "net_amount": 1, "seller_name": "被竄改的賣方"}


@pytest.mark.parametrize("status", PROTECTED_STATUSES)
def test_repository_upsert_refuses_to_touch_a_submitted_case(
    m1: dict[str, Any], isolated_engine: Engine, status: str
) -> None:
    invoice_id = m1["c3_sparse"]["invoice_id"]
    _set_status(isolated_engine, invoice_id, status)
    before = fingerprint(isolated_engine, [invoice_id])

    with Session(isolated_engine) as session:
        with pytest.raises(repo.ProtectedInvoiceError):
            repo.save_invoice(
                session,
                {"invoice_number": m1["c3_sparse"]["number"], "net_amount": 1, "uploaded_by": "x"},
                invoice_id,
            )
        session.rollback()

    assert fingerprint(isolated_engine, [invoice_id]) == before


@pytest.mark.parametrize("status", PROTECTED_STATUSES)
def test_correct_route_rejects_a_submitted_case_and_changes_nothing(
    m1: dict[str, Any], http: Any, isolated_engine: Engine, status: str
) -> None:
    invoice_id = m1["c3_sparse"]["invoice_id"]
    _set_status(isolated_engine, invoice_id, status)
    ids = [invoice_id]
    before, columns = (
        fingerprint(isolated_engine, ids),
        _protected_columns(isolated_engine, invoice_id),
    )

    response = http.post(
        f"/invoices/{invoice_id}/flow/correct",
        data={"invoice_number": m1["c3_sparse"]["number"], **CORRECT_FORM},
    )

    assert response.status_code in (400, 409)  # stage>=5 先擋（400）；退回補正中由保護擋（409）
    assert fingerprint(isolated_engine, ids) == before
    assert _protected_columns(isolated_engine, invoice_id) == columns


@pytest.mark.parametrize("path", ["flow/finish-first-four"])
def test_other_save_paths_reject_a_returned_case_and_change_nothing(
    m1: dict[str, Any], http: Any, isolated_engine: Engine, path: str
) -> None:
    invoice_id = m1["c3_sparse"]["invoice_id"]
    _set_status(isolated_engine, invoice_id, "returned_to_applicant")
    before = fingerprint(isolated_engine, [invoice_id])

    response = http.post(f"/invoices/{invoice_id}/{path}")

    assert response.status_code == 409
    assert fingerprint(isolated_engine, [invoice_id]) == before


def test_advance_from_step_3_rejects_a_returned_case_and_changes_nothing(
    m1: dict[str, Any], http: Any, isolated_engine: Engine
) -> None:
    invoice_id = m1["c3_sparse"]["invoice_id"]
    _set_status(isolated_engine, invoice_id, "returned_to_applicant")
    before = fingerprint(isolated_engine, [invoice_id])

    response = http.post(f"/invoices/{invoice_id}/flow/advance", data={"from_step": 3})

    assert response.status_code == 409
    assert fingerprint(isolated_engine, [invoice_id]) == before


def test_the_legitimate_draft_flow_still_saves_and_updates(
    http: Any, isolated_engine: Engine
) -> None:
    """未提交草稿（status=draft、輪次 0）仍可經核對修正路徑儲存，功能不退化。"""
    user, dept, draft = uuid4(), uuid4(), uuid4()
    with isolated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO public.departments(department_id, department_code, department_name)"
                " VALUES (:d, :c, 'M1草稿部')"
            ),
            {"d": dept, "c": f"m1d_{uuid4().hex[:8]}"},
        )
        conn.execute(
            text(
                "INSERT INTO invoiceguard_identity.user_accounts(user_id, email, password_hash,"
                " account_status, primary_department_id) VALUES (:u, :e, :h, 'active', :d)"
            ),
            {
                "u": user,
                "e": f"m1d_{uuid4().hex[:8]}@example.test",
                "h": "$argon2id$v=19$m=65536,t=3,p=4$" + "A" * 22 + "$" + "B" * 22,
                "d": dept,
            },
        )
        conn.execute(
            text("INSERT INTO public.user_profiles(user_id, display_name) VALUES (:u, 'd')"),
            {"u": user},
        )
        conn.execute(
            text("INSERT INTO public.user_departments(user_id, department_id) VALUES (:u, :d)"),
            {"u": user, "d": dept},
        )
        conn.execute(
            text(
                "INSERT INTO public.invoices(invoice_id, invoice_track, invoice_number, status,"
                " submission_round, submitted_by, department_id) VALUES"
                " (:i, 'AB', '00000001', 'draft', 0, :u, :d)"
            ),
            {"i": draft, "u": user, "d": dept},
        )
    try:
        response = http.post(
            f"/invoices/{draft}/flow/correct",
            data={
                "invoice_number": "AB00000099",
                "invoice_date": "113/08/15",
                "net_amount": 100,
                "seller_name": "草稿賣方",
                "seller_tax_id": "04595257",
            },
        )
        with isolated_engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT status, submission_round, invoice_number, seller_name, sales_amount"
                    " FROM public.invoices WHERE invoice_id = :i"
                ),
                {"i": draft},
            ).one()
        assert response.status_code == 303
        assert tuple(row) == ("draft", 0, "00000099", "草稿賣方", Decimal("100.00"))
    finally:
        with isolated_engine.begin() as conn:
            conn.execute(text("DELETE FROM public.invoices WHERE invoice_id = :i"), {"i": draft})
            conn.execute(
                text("DELETE FROM invoiceguard_identity.user_accounts WHERE user_id = :u"),
                {"u": user},
            )
            conn.execute(
                text("DELETE FROM public.departments WHERE department_id = :d"), {"d": dept}
            )


def test_correcting_a_pending_invoice_is_blocked_and_writes_nothing(
    m1: dict[str, Any], http: Any, isolated_engine: Engine
) -> None:
    invoice_id = m1["c1_full"]["invoice_id"]
    before = fingerprint(isolated_engine, [invoice_id])

    response = http.post(
        f"/invoices/{invoice_id}/flow/correct",
        data={"invoice_number": "AB11111111", "invoice_date": "113/08/15", "net_amount": 100},
    )

    assert response.status_code == 400  # stage>=5 擋下
    assert fingerprint(isolated_engine, [invoice_id]) == before


def test_real_invoice_image_is_not_served_by_the_file_route_today(
    m1: dict[str, Any], http: Any
) -> None:
    """特徵測試（另案）：檔案路由只提供當次暫存的 file_bytes；資料庫讀回的發票只有檔名與類型。"""
    response = http.get(f"/invoices/{m1['c1_full']['invoice_id']}/file")

    assert response.status_code == 404


def test_http_non_finance_user_cannot_read_someone_elses_invoice(
    m1: dict[str, Any], http: Any, isolated_engine: Engine
) -> None:
    """HTTP（TestClient）：非財務工作區的測試替身身分——只讀得到自己提交的發票。

    注意：身分與『是否財務工作區』由測試以相依覆寫提供（正式判斷依據是 A 組的部門名稱比對，E 組授權尚未整合）。
    """
    from app.main import app

    def as_sales_user(request: Request) -> None:
        request.state.invoice_user_id = m1["users"]["sales"]
        request.state.is_finance_workspace = False
        request.state.invoice_max_step = 4

    app.dependency_overrides[inv_router.require_invoice_access] = as_sales_user
    ids = [m1[k]["invoice_id"] for k in ("c1_full", "c2_other", "c3_sparse")]
    before = fingerprint(isolated_engine, ids)
    try:
        own = http.get(f"/invoices/{m1['c2_other']['invoice_id']}")
        others = http.get(f"/invoices/{m1['c1_full']['invoice_id']}")
        listing = http.get("/invoices")
    finally:
        app.dependency_overrides.pop(inv_router.require_invoice_access, None)

    assert own.status_code == 200
    assert others.status_code == 403  # 他人的案件
    assert m1["c2_other"]["number"] in listing.text
    assert (
        m1["c1_full"]["number"] not in listing.text
        and m1["c3_sparse"]["number"] not in listing.text
    )
    assert fingerprint(isolated_engine, ids) == before


def test_service_rolls_back_and_reraises_the_protection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Service 層（不需資料庫）：保護錯誤不被吞掉，且一定 rollback、不 commit、關閉連線。"""
    calls: list[str] = []

    class SpySession:
        def commit(self) -> None:
            calls.append("commit")

        def rollback(self) -> None:
            calls.append("rollback")

        def close(self) -> None:
            calls.append("close")

    def refuse(session: Any, invoice: dict[str, Any], invoice_id: Any) -> None:
        raise repo.ProtectedInvoiceError(str(invoice_id))

    monkeypatch.setattr(repo, "try_get_db_session", lambda: SpySession())
    monkeypatch.setattr(repo, "save_invoice", refuse)

    with pytest.raises(repo.ProtectedInvoiceError):
        invoice_review_service.save_invoice({}, str(uuid4()))

    assert calls == ["rollback", "close"]


def test_service_still_swallows_unrelated_errors_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    """既有行為不變（另案）：非保護類的資料庫錯誤仍 rollback 後吞掉；這裡只固定它，不擴大修改。"""
    calls: list[str] = []

    class SpySession:
        def commit(self) -> None:
            calls.append("commit")

        def rollback(self) -> None:
            calls.append("rollback")

        def close(self) -> None:
            calls.append("close")

    def boom(session: Any, invoice: dict[str, Any], invoice_id: Any) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(repo, "try_get_db_session", lambda: SpySession())
    monkeypatch.setattr(repo, "save_invoice", boom)

    invoice_review_service.save_invoice({}, str(uuid4()))  # 不丟例外

    assert calls == ["rollback", "close"]
