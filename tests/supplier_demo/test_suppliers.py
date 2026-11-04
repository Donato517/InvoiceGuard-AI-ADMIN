"""供應商試作離線驗收；每個案例使用獨立暫存檔，不載入原測試 conftest。"""

import re
import socket
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.schemas.supplier import SupplierInput
from app.services.supplier_service import SupplierError
from scripts.supplier_demo_app import create_app


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """封鎖對外連線，以真實本機 SQLite 檔驗證。"""

    original_connect = socket.socket.connect

    def forbidden(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("模擬功能不應對外連線")
        return original_connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    with TestClient(
        create_app(tmp_path / "suppliers.sqlite3"),
        base_url="http://127.0.0.1",
        client=("127.0.0.1", 50000),
    ) as result:
        yield result


def payload(client: TestClient, **changes: str) -> dict:
    """取得表單 token 並建立可用測試資料。"""
    token = re.search(r'name="csrf"\s+value="([^"]+)"', client.get("/suppliers/new").text).group(1)
    return {
        "csrf": token,
        "supplier_name": "模擬新供應商",
        "country_code": "TW",
        "tax_id": "00000003",
        "aliases": "測試別名\n第二別名",
        "payment_terms": "net",
        "payment_basis": "invoice",
        "payment_days": "30",
        "payment_method": "transfer",
        "currency": "TWD",
        **changes,
    }


def create(client: TestClient, **changes: str) -> dict:
    """新增後讀回服務資料。"""
    response = client.post("/suppliers", data=payload(client, **changes))
    assert response.status_code == 200
    assert "已新增模擬供應商" in response.text
    return client.app.state.suppliers.list("00000003")[0]


def test_create_search_duplicate_and_edit(client: TestClient) -> None:
    """新增、三種搜尋、重複拒絕及改名保留欄位。"""
    row = create(client)
    for query in ["00000003", "模擬新供應商", "第二別名"]:
        assert "模擬新供應商" in client.get("/suppliers", params={"q": query}).text
    assert "沒有符合條件" in client.get("/suppliers", params={"q": "%_"}).text
    response = client.post("/suppliers", data=payload(client))
    assert response.status_code == 400
    assert "已有此統編" in response.text
    changed = payload(
        client, supplier_name="模擬改名", payment_days="60", updated_at=row["updated_at"]
    )
    assert client.post("/suppliers/" + row["supplier_id"], data=changed).status_code == 200
    current = client.app.state.suppliers.get(row["supplier_id"])
    assert current["supplier_name"] == "模擬改名"
    assert current["payment_days"] == 60
    assert current["supplier_id"] == row["supplier_id"]
    assert current["created_at"] == row["created_at"]


def test_disable_requires_confirmation_and_reenable(client: TestClient) -> None:
    """拒絕未確認停用，停用後資料與唯一限制仍存在。"""
    row = create(client)
    url = "/suppliers/" + row["supplier_id"] + "/status"
    form = {"csrf": payload(client)["csrf"], "active": "0", "updated_at": row["updated_at"]}
    assert client.post(url, data=form).status_code == 400
    assert client.post(url, data={**form, "confirm": "yes"}).status_code == 200
    disabled = client.app.state.suppliers.get(row["supplier_id"])
    assert disabled["is_active"] == 0
    assert client.post("/suppliers", data=payload(client)).status_code == 400
    assert (
        client.post(
            url, data={**form, "active": "1", "updated_at": disabled["updated_at"]}
        ).status_code
        == 200
    )
    assert client.app.state.suppliers.get(row["supplier_id"])["is_active"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"supplier_name": " "},
        {"tax_id": "abc"},
        {"email": "bad"},
        {"country_code": "123"},
        {"payment_days": "-1"},
        {"payment_days": "1.5"},
        {"currency": "12$"},
        {"payment_terms": "unknown"},
        {"payment_terms": "immediate", "payment_days": "30"},
        {"payment_terms": "prepaid", "payment_basis": "contract", "notes": ""},
    ],
)
def test_invalid_form_preserves_input(client: TestClient, changes: dict) -> None:
    """必填、格式及付款規則都在服務端驗證，失敗不寫入。"""
    before = len(client.app.state.suppliers.list())
    response = client.post("/suppliers", data=payload(client, **changes))
    assert response.status_code == 400
    assert "尚未儲存" in response.text
    assert len(client.app.state.suppliers.list()) == before


def test_foreign_no_tax_and_xss_escape(client: TestClient) -> None:
    """國外無統編允許；輸入文字在列表、表單及詳情均自動跳脫。"""
    text = '<script>alert("test")</script>'
    response = client.post(
        "/suppliers", data=payload(client, country_code="US", tax_id="", supplier_name=text)
    )
    assert response.status_code == 200
    assert "<script>alert" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "&lt;script&gt;" in client.get("/suppliers").text


def test_token_origin_host_and_peer_guards(client: TestClient, tmp_path: Path) -> None:
    """無 token、外站來源、外部 Host 及非本機 peer 不可寫入或讀取。"""
    assert client.post("/suppliers", data={}).status_code == 403
    assert (
        client.post(
            "/suppliers", data=payload(client), headers={"Origin": "https://other.example"}
        ).status_code
        == 403
    )
    assert client.get("/suppliers", headers={"Host": "other.example"}).status_code == 400
    with TestClient(
        create_app(tmp_path / "remote.sqlite3"),
        base_url="http://127.0.0.1",
        client=("192.0.2.1", 50000),
    ) as remote:
        assert remote.get("/suppliers").status_code == 403


def test_restart_preserves_and_missing_record(client: TestClient, tmp_path: Path) -> None:
    """同一資料檔重啟保留新增、修改與停用，不重新灌入範例。"""
    row = create(client)
    assert client.get("/suppliers/not-found").status_code == 404
    path = client.app.state.suppliers.repository.path
    with TestClient(
        create_app(path), base_url="http://127.0.0.1", client=("127.0.0.1", 50001)
    ) as restarted:
        assert restarted.app.state.suppliers.get(row["supplier_id"])["tax_id"] == "00000003"
        assert len(restarted.app.state.suppliers.list()) == 3


def test_stale_edit_and_duplicate_on_update(client: TestClient) -> None:
    """舊畫面不可覆蓋；更新也受資料庫唯一限制。"""
    row = create(client)
    service = client.app.state.suppliers
    data = SupplierInput.model_validate({k: row[k] for k in SupplierInput.model_fields})
    service.save(data, row["supplier_id"], row["updated_at"])
    with pytest.raises(SupplierError, match="其他畫面"):
        service.save(data, row["supplier_id"], row["updated_at"])
    latest = service.get(row["supplier_id"])
    data.tax_id = "00000001"
    with pytest.raises(SupplierError, match="已有此統編"):
        service.save(data, row["supplier_id"], latest["updated_at"])
    assert service.get(row["supplier_id"])["tax_id"] == "00000003"


def test_cloud_entry_refused(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """雲端環境即使直接載入試作入口也拒絕建立。"""
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(RuntimeError, match="不可部署"):
        create_app(tmp_path / "blocked.sqlite3")
    assert not (tmp_path / "blocked.sqlite3").exists()


def test_rating_and_delivery_roundtrip(client: TestClient) -> None:
    """新增兩欄能保存、顯示、修改，停用不變動欄位。"""
    row = create(client, credit_rating="A", delivery_terms="指定地點交付，含安裝驗收")
    assert row["credit_rating"] == "A"
    assert row["delivery_terms"] == "指定地點交付，含安裝驗收"
    url = "/suppliers/" + row["supplier_id"]
    assert "A 級（模擬低風險）" in client.get(url).text
    form = payload(
        client, credit_rating="C", delivery_terms="依合約分批交付", updated_at=row["updated_at"]
    )
    assert client.post(url, data=form).status_code == 200
    current = client.app.state.suppliers.get(row["supplier_id"])
    assert current["credit_rating"] == "C"
    assert current["delivery_terms"] == "依合約分批交付"
    assert current["created_at"] == row["created_at"]


@pytest.mark.parametrize("changes", [{"credit_rating": "INVALID"}, {"delivery_terms": "字" * 301}])
def test_rating_delivery_validation(client: TestClient, changes: dict) -> None:
    """無效等級及過長交貨條件不可寫入。"""
    assert client.post("/suppliers", data=payload(client, **changes)).status_code == 400
    assert not client.app.state.suppliers.list("00000003")


def test_old_database_upgrades_without_loss(tmp_path: Path) -> None:
    """舊版本機表增加欄位，既有 UUID、狀態與內容不變。"""
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE suppliers (supplier_id TEXT PRIMARY KEY, supplier_name TEXT, country_code TEXT, tax_id TEXT, aliases TEXT, contact_name TEXT, phone TEXT, email TEXT, address TEXT, payment_terms TEXT, payment_basis TEXT, payment_days INTEGER, payment_method TEXT, currency TEXT, notes TEXT, is_active INTEGER, created_at TEXT, updated_at TEXT)"
        )
        db.execute(
            "INSERT INTO suppliers VALUES ('old-id','模擬舊資料','TW','00000999','','','','','','net','invoice',30,'transfer','TWD','保留原註記',0,'old-created','old-updated')"
        )
    application = create_app(path)
    service = application.state.suppliers
    service.initialize()
    service.initialize()
    row = service.get("old-id")
    assert row["credit_rating"] == "unassessed"
    assert row["delivery_terms"] == ""
    assert row["notes"] == "保留原註記"
    assert row["is_active"] == 0
    assert row["created_at"] == "old-created"
    assert len(service.list()) == 1


def function_form(client: TestClient, supplier_id: str) -> dict:
    """讀取供應商詳情的功能表單 token 與獨立版本。"""
    html = client.get("/suppliers/" + supplier_id).text
    return {
        "csrf": re.search(r'name="csrf"\s+value="([^"]+)"', html).group(1),
        "functions_updated_at": re.search(
            r'name="functions_updated_at"\s+value="([^"]*)"', html
        ).group(1),
    }


def test_supplier_function_list_is_on_detail_and_isolated(client: TestClient) -> None:
    """功能列表在每筆詳情；儲存只影響該供應商的模擬勾選，不改主檔。"""
    service = client.app.state.suppliers
    before = service.list()
    first, second = before
    sid = first["supplier_id"]
    html = client.get("/suppliers/" + sid).text
    assert "供應商功能列表" in html
    assert "徵信／評估紀錄" in html
    assert "部門功能設定" not in html
    url = f"/suppliers/{sid}/function-settings"
    response = client.post(url, data={**function_form(client, sid), "basic.create": "1"})
    assert response.status_code == 200
    assert "已儲存此供應商的模擬功能設定" in response.text
    assert service.get_function_settings(sid)["selections"] == {"basic.create"}
    assert "basic.view" in service.get_function_settings(second["supplier_id"])["selections"]
    assert service.list() == before
    assert client.get("/suppliers/" + sid + "/edit").status_code == 200


def test_supplier_functions_revoke_stale_guard_and_restart(client: TestClient) -> None:
    """空選項可保存；舊畫面不能覆蓋；重建 Service 不重設。"""
    from app.db.repositories.supplier_demo_repository import SupplierDemoRepository
    from app.services.supplier_service import SupplierService

    service = client.app.state.suppliers
    sid = service.list()[0]["supplier_id"]
    url = f"/suppliers/{sid}/function-settings"
    stale = function_form(client, sid)
    assert client.post(url, data={**stale, "basic.disable": "1"}).status_code == 200
    assert client.post(url, data=stale).status_code == 400
    assert service.get_function_settings(sid)["selections"] == {"basic.disable"}
    assert client.post(url, data=function_form(client, sid)).status_code == 200
    restarted = SupplierService(SupplierDemoRepository(service.repository.path))
    restarted.initialize()
    assert not restarted.get_function_settings(sid)["selections"]


@pytest.mark.parametrize("invalid", ["assessment.view", "contact.create", "unknown.edit"])
def test_supplier_functions_reject_unavailable_operations(client: TestClient, invalid: str) -> None:
    """未開發及不適用操作不能經偽造表單保存。"""
    service = client.app.state.suppliers
    sid = service.list()[0]["supplier_id"]
    before = service.get_function_settings(sid)
    response = client.post(
        f"/suppliers/{sid}/function-settings", data={**function_form(client, sid), invalid: "1"}
    )
    assert response.status_code == 400
    assert service.get_function_settings(sid) == before


def test_supplier_functions_security_and_removed_department_page(client: TestClient) -> None:
    """沿用 token／同源；誤加部門頁不再呈現或接受寫入。"""
    sid = client.app.state.suppliers.list()[0]["supplier_id"]
    url = f"/suppliers/{sid}/function-settings"
    form = function_form(client, sid)
    assert client.post(url, data={**form, "csrf": "bad"}).status_code == 403
    assert (
        client.post(url, data=form, headers={"Origin": "https://other.example"}).status_code == 403
    )
    assert client.get("/suppliers/department-functions", follow_redirects=False).status_code == 303
    assert client.post("/suppliers/department-functions", data=form).status_code == 410
