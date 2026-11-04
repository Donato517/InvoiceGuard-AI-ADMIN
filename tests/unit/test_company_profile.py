# ruff: noqa: F811
"""公司基本資料頁驗權、交易、表單驗證與 F09 讀取；合成 Session／DB，不呼叫正式服務。"""

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from test_department_login import (  # noqa: F401
    BASE,
    ORIGIN,
    USER,
    Database,
    SidebarNavigation,
    browser,
    encoded_password,
    login,
    module,
    state,
)

from app.db.repositories import company_profile_repository as repository
from app.schemas.e_admin import Action, UnavailableError
from app.services import company_profile_management_service as service
from app.services import company_profile_service as profiles
from app.services.e_configuration_security import configuration_active
from app.services.e_registry import supported_actions
from app.services.format21_service import validate_profile

STAMP = datetime(2026, 11, 4, tzinfo=UTC)
# 04595252 為財政資訊中心建議的測試統編（通過檢查碼）
DATA = {
    "company_name": "合成測試股份有限公司",
    "ban": "04595252",
    "tax_reg_no": "123456789",
    "tax_reg_no_confirm": "123456789",
}
STORED = {
    "company_name": "合成測試股份有限公司",
    "ban": "04595252",
    "tax_reg_no": "123456789",
    "representative_name": "",
    "business_zip_code": "",
    "business_city": "",
    "business_district": "",
    "business_street": "",
    "tax_office": "",
    "contact_name": "",
    "contact_phone": "",
    "contact_email": "",
    "created_at": STAMP,
    "updated_at": STAMP,
}


@pytest.fixture
def company(state: dict, monkeypatch: pytest.MonkeyPatch) -> dict:
    """財務部已勾選公司基本資料的查看與修改；DB 讀寫為替身，記錄寫入內容。"""
    row = dict(
        state["functions"][0],
        function_code="company_profile",
        function_name="公司基本資料",
        app_scope="admin",
    )
    state["functions"].append(row)
    state["company_profile"] = None
    state["company_writes"] = []
    monkeypatch.setattr(repository, "open_session", lambda: Database(state))
    monkeypatch.setattr(repository, "prepare", lambda db: None)
    monkeypatch.setattr(
        repository,
        "get_profile",
        lambda db: dict(state["company_profile"]) if state["company_profile"] else None,
    )

    def create(db, values, actor):
        state["company_writes"].append(("create", values, actor))
        return True

    def update(db, values, revision, actor):
        state["company_writes"].append(("update", values, actor, revision))
        return True

    monkeypatch.setattr(repository, "create_profile", create)
    monkeypatch.setattr(repository, "update_profile", update)
    return row


def test_registered_with_view_and_edit_only() -> None:
    assert supported_actions("company.profile") == frozenset({Action.VIEW, Action.EDIT})


def test_requires_session(browser: TestClient, company: dict) -> None:
    response = browser.get("/company-profile")
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_sidebar_and_empty_state(browser: TestClient, company: dict) -> None:
    login(browser)
    response = browser.get("/company-profile")
    assert response.status_code == 200
    assert "/company-profile" in SidebarNavigation(response.text).entries
    assert "尚未設定公司基本資料" in response.text and "設定公司資料" in response.text


def test_first_setup_records_session_user(browser: TestClient, company: dict, state: dict) -> None:
    login(browser)
    assert browser.get("/company-profile/edit").status_code == 200
    response = browser.post("/company-profile", headers=ORIGIN, data=DATA)
    assert response.status_code == 303
    assert response.headers["location"] == "/company-profile?result=saved"
    kind, values, actor = state["company_writes"][0]
    assert kind == "create" and actor == USER
    assert (values.company_name, values.ban, values.tax_reg_no) == (
        "合成測試股份有限公司",
        "04595252",
        "123456789",
    )
    assert any(sql.startswith("LOCK TABLE") for sql, _ in state["queries"])
    assert not configuration_active()


def test_existing_profile_shows_values_and_updates_with_revision(
    browser: TestClient, company: dict, state: dict
) -> None:
    state["company_profile"] = STORED
    login(browser)
    page = browser.get("/company-profile")
    assert "04595252" in page.text and "123456789" in page.text and "修改資料" in page.text
    form = browser.get("/company-profile/edit").text
    # 確認欄不帶入原值，要重新輸入
    assert 'name="tax_reg_no_confirm"' in form and 'value="123456789"' in form
    assert form.count('value="123456789"') == 1
    revision = "2026-11-04T08:00:00+08:00"
    response = browser.post(
        "/company-profile", headers=ORIGIN, data=DATA | {"updated_at": revision}
    )
    assert response.status_code == 303
    assert state["company_writes"][0][0] == "update"


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"tax_reg_no_confirm": "123456780"}, "兩次輸入的稅籍編號不一致"),
        ({"ban": "12345678"}, "統一編號未通過檢查碼驗算"),
        ({"ban": "1234567"}, "統一編號為 8 碼數字"),
        ({"tax_reg_no": "12345678", "tax_reg_no_confirm": "12345678"}, "稅籍編號為 9 碼數字"),
        ({"company_name": "  "}, "公司名稱已填寫"),
        ({"contact_email": "not-an-email"}, "Email 格式須正確"),
        ({"business_street": "地" * 201}, "選填欄位不可超過長度"),
        ({"business_city": "臺北市", "business_district": "竹北"}, "須從清單選擇"),
        ({"business_city": "臺北市"}, "須從清單選擇"),
        ({"business_district": "中正區"}, "須從清單選擇"),
    ],
)
def test_validation_keeps_input_and_writes_nothing(
    browser: TestClient, company: dict, state: dict, changes: dict, message: str
) -> None:
    login(browser)
    response = browser.post("/company-profile", headers=ORIGIN, data=DATA | changes)
    assert response.status_code == 400 and message in response.text
    assert state["company_writes"] == []


def test_conflict_and_unknown_fields(
    browser: TestClient, company: dict, state: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(browser)
    monkeypatch.setattr(repository, "create_profile", lambda *args: False)
    response = browser.post("/company-profile", headers=ORIGIN, data=DATA)
    assert response.status_code == 400 and "已由其他人設定" in response.text
    monkeypatch.setattr(repository, "update_profile", lambda *args: False)
    response = browser.post(
        "/company-profile", headers=ORIGIN, data=DATA | {"updated_at": STAMP.isoformat()}
    )
    assert response.status_code == 400 and "其他人修改" in response.text
    assert (
        browser.post("/company-profile", headers=ORIGIN, data=DATA | {"user": "x"}).status_code
        == 400
    )


@pytest.mark.parametrize(
    "flag,path,method",
    [
        ("can_view", "/company-profile", "get"),
        ("can_edit", "/company-profile/edit", "get"),
        ("can_edit", "/company-profile", "post"),
    ],
)
def test_each_permission_is_enforced(
    browser: TestClient, company: dict, state: dict, flag: str, path: str, method: str
) -> None:
    company[flag] = False
    login(browser)
    kwargs = {"headers": ORIGIN, "data": DATA} if method == "post" else {}
    assert getattr(browser, method)(path, **kwargs).status_code == 403
    assert state["company_writes"] == []


def test_edit_only_grant_does_not_show_data(
    browser: TestClient, company: dict, state: dict
) -> None:
    company["can_view"] = False
    login(browser)
    response = browser.post("/company-profile", headers=ORIGIN, data=DATA)
    assert response.status_code == 200 and "已儲存公司基本資料" in response.text
    assert "04595252" not in response.text


def test_cross_origin_rejected(browser: TestClient, company: dict, state: dict) -> None:
    login(browser)
    assert (
        browser.post(
            "/company-profile", headers={"Origin": "https://external.invalid"}, data=DATA
        ).status_code
        == 403
    )
    assert state["company_writes"] == []


def test_service_without_verified_context_fails_closed() -> None:
    with pytest.raises(UnavailableError):
        service.get_profile()


def test_database_failure_is_sanitized(
    browser: TestClient, company: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    login(browser)

    def failure(db):
        raise RuntimeError("private-connection-secret")

    monkeypatch.setattr(repository, "get_profile", failure)
    response = browser.get("/company-profile")
    assert response.status_code == 503 and "private-connection-secret" not in response.text


# ---- F09 讀取 ----


class _Session:
    def rollback(self) -> None:
        return None

    def close(self) -> None:
        return None


def test_provider_reads_saved_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(repository, "open_session", _Session)
    monkeypatch.setattr(repository, "prepare", lambda db: None)
    monkeypatch.setattr(repository, "get_profile", lambda db: dict(STORED))
    profile = profiles.DatabaseCompanyProfileProvider().get_profile()
    assert (profile.ban, profile.tax_reg_no, profile.name) == (
        "04595252",
        "123456789",
        "合成測試股份有限公司",
    )
    assert validate_profile(profile) == []


@pytest.mark.parametrize("broken", [False, True])
def test_provider_not_configured_blocks_filing(
    monkeypatch: pytest.MonkeyPatch, broken: bool
) -> None:
    """尚未設定、資料表尚未建立或讀取失敗時都不以示範統編產檔。"""

    def read(db):
        if broken:
            raise RuntimeError("relation does not exist")
        return None

    monkeypatch.setattr(repository, "open_session", _Session)
    monkeypatch.setattr(repository, "prepare", lambda db: None)
    monkeypatch.setattr(repository, "get_profile", read)
    profile = profiles.DatabaseCompanyProfileProvider().get_profile()
    assert profile == profiles.NOT_CONFIGURED
    assert [issue.code for issue in validate_profile(profile)] == ["COMPANY_NOT_CONFIGURED"]


def test_provider_without_database_uses_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable():
        raise UnavailableError

    monkeypatch.setattr(repository, "open_session", unavailable)
    profile = profiles.DatabaseCompanyProfileProvider().get_profile()
    assert profile == profiles.HardcodedCompanyProfileProvider().get_profile()


def test_optional_fields_are_saved_and_shown(
    browser: TestClient, company: dict, state: dict
) -> None:
    """負責人、地址、稽徵機關與聯絡窗口選填：空白可存，有填則存入並顯示。"""
    optional = {
        "representative_name": "王小明",
        "business_city": "臺北市",
        "business_district": "中正區",
        "business_street": "合成路 1 號",
        "tax_office": "財政部臺北國稅局中正分局",
        "contact_name": "李會計",
        "contact_phone": "02-1234-5678",
        "contact_email": "tax@example.com",
    }
    login(browser)
    assert browser.post("/company-profile", headers=ORIGIN, data=DATA | optional).status_code == 303
    values = state["company_writes"][0][1]
    assert {name: getattr(values, name) for name in optional} == optional
    assert values.business_zip_code == "100"
    state["company_profile"] = STORED | optional | {"business_zip_code": "100"}
    page = browser.get("/company-profile").text
    assert "王小明" in page and "tax@example.com" in page and "未提供" not in page
    assert "100 臺北市中正區合成路 1 號" in page
    state["company_profile"] = STORED
    assert "未提供" in browser.get("/company-profile").text


def test_zip_code_comes_from_official_list_not_the_form(
    browser: TestClient, company: dict, state: dict
) -> None:
    """郵遞區號依縣市與鄉鎮市區帶出；表單自填的郵遞區號一律不採用。"""
    login(browser)
    address = {"business_city": "連江縣", "business_district": "東引", "business_zip_code": "999"}
    assert browser.post("/company-profile", headers=ORIGIN, data=DATA | address).status_code == 303
    assert state["company_writes"][0][1].business_zip_code == "212"


def test_form_lists_districts_of_selected_city(
    browser: TestClient, company: dict, state: dict
) -> None:
    state["company_profile"] = STORED | {
        "business_zip_code": "302",
        "business_city": "新竹縣",
        "business_district": "竹北",
    }
    login(browser)
    form = browser.get("/company-profile/edit").text
    assert '<option value="新竹縣" selected>' in form
    assert 'data-zip="302"' in form and 'data-zip="100"' not in form
    assert 'id="tw-postal-codes"' in form and "臺北市" in form
    # 郵遞區號欄只顯示，不送出
    assert 'id="business_zip_code"' in form and 'name="business_zip_code"' not in form
    # 連動資料保留中華郵政原檔順序（新竹縣第一個是竹北）
    raw = form.split('id="tw-postal-codes">', 1)[1].split("</script>", 1)[0]
    options = dict((city, districts) for city, districts in json.loads(raw))
    assert list(options)[:3] == ["臺北市", "基隆市", "新北市"]
    assert options["新竹縣"][0] == ["竹北", "302"]


def test_postal_list_matches_official_file_counts() -> None:
    """中華郵政 103.12.25 一覽表：23 個縣市（含南海諸島）、368 個鄉鎮市區，郵遞區號不重複。"""
    from app.schemas.tw_postal_codes import POSTAL_CODES

    codes = [code for districts in POSTAL_CODES.values() for code in districts.values()]
    assert len(POSTAL_CODES) == 23 and len(codes) == 368 and len(set(codes)) == 368
    assert all(len(code) == 3 and code.isdigit() for code in codes)
    assert (
        POSTAL_CODES["臺北市"]["中正區"] == "100" and POSTAL_CODES["新竹市"]["新竹市全區"] == "300"
    )
