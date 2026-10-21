"""
模組名稱：tests.unit.test_format21_service
功能說明：格式 21 申報檔的檢核規則、轉檔與 401 彙總單元測試。
相依模組：app.services.format21_service、app.services.company_profile_service
注意事項：
    - 不連線資料庫；資料取自 fixtures/sample_approved_snapshots.json（虛構資料）。
"""

import json
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.services.company_profile_service import CompanyProfile, HardcodedCompanyProfileProvider
from app.services.format21_service import (
    Format21Record,
    build_format21_file,
    format_record_line,
    is_valid_ban,
    summarize_401,
    to_roc_year_month,
    validate_record,
)

SNAPSHOTS = json.loads(
    (Path(__file__).parent / "fixtures" / "sample_approved_snapshots.json").read_text("utf-8")
)

VALID_RECORD = Format21Record(
    tax_reg_no="123456789",
    buyer_ban="04595252",
    report_period="11510",
    issue_year_month="11509",
    track="EC",
    number="08904069",
    seller_ban="27601907",
    sales_amount=Decimal("6000"),
    tax_type="1",
    tax_amount=Decimal("300"),
    deduction_code="1",
)


class InvalidProfileProvider:
    """模擬寫死的公司資料打錯。"""

    def get_profile(self) -> CompanyProfile:
        """回傳錯誤的公司資料。"""
        return CompanyProfile(tax_reg_no="12345", ban="12345678", name="錯誤公司")


def _issue_codes(record: Format21Record) -> set[str]:
    return {issue.code for issue in validate_record(record)}


# ---- 檢核規則 ----


def test_validate_record_valid_returns_no_issue():
    assert _issue_codes(VALID_RECORD) == set()


def test_is_valid_ban_uses_divisible_by_five_rule():
    assert is_valid_ban("27601907")
    assert is_valid_ban("04595252")
    assert not is_valid_ban("12345678")


def test_validate_record_rejects_wrong_tax():
    record = replace(VALID_RECORD, tax_amount=Decimal("400"))

    codes = _issue_codes(record)

    assert "TAX_MISMATCH" in codes


def test_validate_record_rounds_tax_half_up():
    record = replace(VALID_RECORD, sales_amount=Decimal("10"), tax_amount=Decimal("1"))

    codes = _issue_codes(record)

    assert "TAX_MISMATCH" not in codes


def test_validate_record_rejects_tax_on_zero_rated():
    assert "TAX_SHOULD_BE_ZERO" in _issue_codes(replace(VALID_RECORD, tax_type="2"))


def test_validate_record_rejects_issue_after_report_period():
    assert "AFTER_REPORT_PERIOD" in _issue_codes(replace(VALID_RECORD, issue_year_month="11511"))


def test_validate_record_accepts_next_period_track():
    assert "TRACK_PERIOD_MISMATCH" not in _issue_codes(replace(VALID_RECORD, track="GE"))


def test_validate_record_rejects_wrong_period_track():
    assert "TRACK_PERIOD_MISMATCH" in _issue_codes(replace(VALID_RECORD, track="VT"))


def test_validate_record_rejects_unknown_deduction_code():
    assert "BAD_DEDUCTION_CODE" in _issue_codes(replace(VALID_RECORD, deduction_code="5"))


# ---- 轉檔與彙總 ----


def test_format_record_line_returns_81_chars():
    line = format_record_line(VALID_RECORD, serial=1)

    assert len(line) == 81
    assert line.startswith("21123456789000000111510")


def test_summarize_401_splits_by_deduction_code():
    fixed_asset = replace(
        VALID_RECORD,
        number="08904070",
        sales_amount=Decimal("20000"),
        tax_amount=Decimal("1000"),
        deduction_code="2",
    )

    totals = summarize_401([VALID_RECORD, fixed_asset])

    assert totals["28"] == 6000
    assert totals["29"] == 300
    assert totals["30"] == 20000
    assert totals["31"] == 1000


# ---- 整批產檔 ----


def test_to_roc_year_month_converts_date():
    assert to_roc_year_month(date(2026, 9, 15)) == "11509"


def test_build_format21_file_valid_snapshots_returns_txt():
    result = build_format21_file(SNAPSHOTS, HardcodedCompanyProfileProvider(), "11510")

    lines = result.content.split("\r\n")[:-1]

    assert result.issues == {}
    assert result.filename == "04595252.TXT"
    assert len(lines) == 2
    assert all(len(line) == 81 for line in lines)
    assert lines[1][11:18] == "0000002"


def test_build_format21_file_invalid_profile_blocks_export():
    result = build_format21_file(SNAPSHOTS, InvalidProfileProvider(), "11510")

    assert result.content is None
    assert {i.code for i in result.issues["company"]} == {"BAD_TAX_REG_NO", "BAD_BAN"}


def test_build_format21_file_one_bad_record_blocks_batch():
    bad_snapshot = dict(SNAPSHOTS[1], tax_amount="999")

    result = build_format21_file(
        [SNAPSHOTS[0], bad_snapshot], HardcodedCompanyProfileProvider(), "11510"
    )

    assert result.content is None
    assert "case-0002" in result.issues


def test_build_format21_file_duplicate_in_batch_rejected():
    duplicate = dict(SNAPSHOTS[0], case_id="case-0003")

    result = build_format21_file(
        [SNAPSHOTS[0], duplicate], HardcodedCompanyProfileProvider(), "11510"
    )

    assert result.issues["case-0003"][0].code == "DUPLICATE_IN_BATCH"
