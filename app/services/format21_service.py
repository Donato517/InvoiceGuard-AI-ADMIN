"""
模組名稱：app.services.format21_service
功能說明：格式代號 21（進項三聯式、電子計算機統一發票）申報資料檢核、
          轉為 81 字元固定長度文字，並彙總 401 申報書進項欄位。
相依模組：app.services.company_profile_service
注意事項：
    - 本模組不存取資料庫；資料表欄位對應集中在 build_record_from_snapshot()，
      資料表結構變更時只需修改該函式。
    - 任一筆檢核失敗即整批不產生申報檔，避免匯入金額與 401 對不上。
    - 欄位位置依「營業稅電子資料申報繳稅作業要點」第 20 點整理，正式使用前
      須與附件五逐欄核對，並以財政部申報軟體實測匯入。
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.services.company_profile_service import CompanyProfile, CompanyProfileProvider

# ---- 規則設定 ----

TAX_RATE = Decimal("0.05")
ROC_YEAR_OFFSET = 1911
RECORD_LENGTH = 81
# NOTE: 換行符號待附件五與申報軟體實測確認
LINE_ENDING = "\r\n"

# 系統內部課稅別 → 官方代碼；已是官方代碼的值也接受
TAX_TYPE_CODES = {
    "taxable": "1",
    "zero_rated": "2",
    "exempt": "3",
    "1": "1",
    "2": "2",
    "3": "3",
}

# 甲種（三聯式）字軌：(民國年, 期別起始月) → 字軌
# TODO(StacieChen): 確認字軌組 1／2 各對應哪一種三聯式發票，並改為由資料庫或設定檔匯入
TRACKS: dict[tuple[int, int], set[str]] = {
    (115, 1): {"VT", "VU"},
    (115, 3): {"XV", "XW"},
    (115, 5): {"ZX", "ZY"},
    (115, 7): {"CA", "CB"},
    (115, 9): {"EC", "ED"},
    (115, 11): {"GE", "GF"},
}

# 81 字元欄位配置：(欄位名稱, 長度)，依序串接
LAYOUT = [
    ("format_code", 2),
    ("tax_reg_no", 9),
    ("serial_no", 7),
    ("period", 5),
    ("buyer_ban", 8),
    ("seller_ban", 8),
    ("invoice_no", 10),
    ("sales_amount", 12),
    ("tax_type", 1),
    ("tax_amount", 10),
    ("deduction_code", 1),
    ("blank", 5),
    ("special_tax_rate", 1),
    ("summary_flag", 1),
    ("customs_flag", 1),
]


# ---- 資料結構 ----


@dataclass
class Format21Record:
    """一筆格式 21 申報資料。金額一律為 Decimal。"""

    tax_reg_no: str
    buyer_ban: str
    report_period: str
    issue_year_month: str
    track: str
    number: str
    seller_ban: str
    sales_amount: Decimal
    tax_type: str
    tax_amount: Decimal
    deduction_code: str


@dataclass
class Format21Issue:
    """一項檢核問題；code 給程式判斷，message 給使用者閱讀。"""

    field: str
    code: str
    message: str


@dataclass
class Format21BuildResult:
    """整批產檔結果；有任何問題時 filename 與 content 皆為 None。"""

    filename: str | None
    content: str | None
    issues: dict[str, list[Format21Issue]]
    totals_401: dict[str, Decimal] | None
    record_count: int


# ---- 檢核規則 ----


def is_valid_ban(ban: str) -> bool:
    """檢查統一編號是否通過財政部檢查碼邏輯。

    權數為 1,2,1,2,1,2,4,1，各乘積的位數相加後總和可被 5 整除即有效；
    第 7 位為 7 時，該位可算 1 或 0，任一可被 5 整除即有效。

    Args:
        ban: 統一編號字串。

    Returns:
        是否有效。
    """
    if not re.fullmatch(r"\d{8}", ban):
        return False
    weights = [1, 2, 1, 2, 1, 2, 4, 1]
    total = 0
    for digit, weight in zip(ban, weights, strict=True):
        product = int(digit) * weight
        total += product // 10 + product % 10
    if total % 5 == 0:
        return True
    return ban[6] == "7" and (total + 1) % 5 == 0


def _get_period_start_month(month: int) -> int:
    return month if month % 2 == 1 else month - 1  # 統一發票兩個月一期


def _get_next_period(year: int, start_month: int) -> tuple[int, int]:
    return (year + 1, 1) if start_month == 11 else (year, start_month + 2)


def calculate_tax(sales_amount: Decimal) -> Decimal:
    """計算應稅營業稅額，四捨五入至整數。

    不可使用內建 round()，它採銀行家捨入，0.5 會變成 0。

    Args:
        sales_amount: 未稅銷售額。

    Returns:
        營業稅額。
    """
    return (sales_amount * TAX_RATE).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _check_issue_year_month(record: Format21Record) -> list[Format21Issue]:
    match = re.fullmatch(r"(\d{3})(\d{2})", record.issue_year_month)
    if not match or not 1 <= int(match.group(2)) <= 12:
        return [
            Format21Issue(
                "issue_year_month", "BAD_YEAR_MONTH", "開立年月須為民國三位數年加 01~12 月"
            )
        ]
    if record.issue_year_month > record.report_period:
        return [
            Format21Issue(
                "issue_year_month", "AFTER_REPORT_PERIOD", "發票開立年月不可大於申報所屬期"
            )
        ]
    return []


def _check_track(record: Format21Record) -> list[Format21Issue]:
    if not re.fullmatch(r"[A-Z]{2}", record.track):
        return [Format21Issue("track", "BAD_TRACK", "字軌須為 2 碼英文字母")]
    match = re.fullmatch(r"(\d{3})(\d{2})", record.issue_year_month)
    if not match:
        return []  # 開立年月格式錯誤已由其他規則回報
    year, start = int(match.group(1)), _get_period_start_month(int(match.group(2)))
    # 字軌可為開立年月所屬期或次期
    known_periods = [p for p in [(year, start), _get_next_period(year, start)] if p in TRACKS]
    if not known_periods:
        return [Format21Issue("track", "TRACK_TABLE_MISSING", "字軌對照表缺少該期資料，請人工確認")]
    if not any(record.track in TRACKS[p] for p in known_periods):
        return [Format21Issue("track", "TRACK_PERIOD_MISMATCH", "字軌不屬於開立年月所屬期或次期")]
    return []


def _check_amounts(record: Format21Record) -> list[Format21Issue]:
    issues = []
    sales = record.sales_amount
    if sales < 0 or sales != sales.to_integral_value():
        issues.append(Format21Issue("sales_amount", "BAD_AMOUNT", "銷售額須為非負整數"))
    if record.tax_type not in {"1", "2", "3"}:
        issues.append(Format21Issue("tax_type", "BAD_TAX_TYPE", "課稅別只允許 1、2、3"))
    elif record.tax_type == "1" and record.tax_amount != calculate_tax(sales):
        issues.append(
            Format21Issue("tax_amount", "TAX_MISMATCH", f"應稅稅額應為 {calculate_tax(sales)}")
        )
    elif record.tax_type in {"2", "3"} and record.tax_amount != 0:
        issues.append(Format21Issue("tax_amount", "TAX_SHOULD_BE_ZERO", "零稅率或免稅的稅額應為 0"))
    return issues


def validate_record(record: Format21Record) -> list[Format21Issue]:
    """依財政部格式 21 規則檢核一筆申報資料。

    Args:
        record: 申報資料。

    Returns:
        問題清單；空清單代表通過。
    """
    issues = _check_issue_year_month(record) + _check_track(record)
    if not re.fullmatch(r"\d{8}", record.number):
        issues.append(Format21Issue("number", "BAD_NUMBER", "發票號碼須為 8 碼數字"))
    if not is_valid_ban(record.seller_ban):
        issues.append(Format21Issue("seller_ban", "BAD_BAN", "銷售人統編未通過統一編號邏輯檢查"))
    issues += _check_amounts(record)
    if record.deduction_code not in {"1", "2", "3", "4"}:
        issues.append(Format21Issue("deduction_code", "BAD_DEDUCTION_CODE", "扣抵代號只允許 1~4"))
    return issues


def validate_profile(profile: CompanyProfile) -> list[Format21Issue]:
    """檢核公司基本資料，避免寫死或設定錯誤的值進入申報檔。

    Args:
        profile: 公司基本資料。

    Returns:
        問題清單；空清單代表通過。
    """
    issues = []
    if not re.fullmatch(r"\d{9}", profile.tax_reg_no):
        issues.append(
            Format21Issue("company.tax_reg_no", "BAD_TAX_REG_NO", "稅籍編號須為 9 碼數字")
        )
    if not is_valid_ban(profile.ban):
        issues.append(Format21Issue("company.ban", "BAD_BAN", "本公司統編未通過統一編號邏輯檢查"))
    return issues


# ---- 轉檔與彙總 ----


def format_record_line(record: Format21Record, serial: int) -> str:
    """將已通過檢核的資料轉為 81 字元的一行。

    數字欄左補零，其餘欄依長度右補空白。

    Args:
        record: 已通過檢核的申報資料。
        serial: 流水號，從 1 開始。

    Returns:
        81 字元的申報記錄。

    Raises:
        ValueError: 欄位內容超過規定長度。
    """
    values = {
        "format_code": "21",
        "tax_reg_no": record.tax_reg_no,
        "serial_no": f"{serial:07d}",
        "period": record.report_period,
        "buyer_ban": record.buyer_ban,
        "seller_ban": record.seller_ban,
        "invoice_no": record.track + record.number,
        "sales_amount": f"{int(record.sales_amount):012d}",
        "tax_type": record.tax_type,
        "tax_amount": f"{int(record.tax_amount):010d}",
        "deduction_code": record.deduction_code,
    }
    parts = []
    for name, length in LAYOUT:
        value = values.get(name, "")
        if len(value) > length:
            raise ValueError(f"{name} 超過長度 {length}：{value}")
        parts.append(value.ljust(length))
    return "".join(parts)


def summarize_401(records: list[Format21Record]) -> dict[str, Decimal]:
    """依課稅別與扣抵代號彙總 401 申報書進項欄位，供與申報軟體匯入結果對帳。

    Args:
        records: 申報資料清單。

    Returns:
        欄位編號（28、29、30、31、48、49）對應的金額。
    """
    totals = {field: Decimal(0) for field in ["28", "29", "30", "31", "48", "49"]}
    for r in records:
        if r.tax_type == "1" and r.deduction_code == "1":
            totals["28"] += r.sales_amount
            totals["29"] += r.tax_amount
        elif r.tax_type == "1" and r.deduction_code == "2":
            totals["30"] += r.sales_amount
            totals["31"] += r.tax_amount
        elif r.deduction_code == "3":
            totals["48"] += r.sales_amount + r.tax_amount
        elif r.deduction_code == "4":
            totals["49"] += r.sales_amount + r.tax_amount
    return totals


# ---- 從核准資料組出申報資料 ----


def to_roc_year_month(value: date) -> str:
    """將日期轉為民國年月，例如 2026-09-15 → 11509。

    Args:
        value: 日期。

    Returns:
        5 碼民國年月。
    """
    return f"{value.year - ROC_YEAR_OFFSET:03d}{value.month:02d}"


def _to_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal(-1)  # 交由檢核規則回報金額錯誤


def build_record_from_snapshot(
    snapshot: dict, profile: CompanyProfile, report_period: str
) -> Format21Record:
    """將一筆核准快照與公司資料組成申報資料。

    資料表欄位名稱只在此函式出現，資料表結構變更時只需修改這裡。

    Args:
        snapshot: 核准快照；invoice_number 為字軌加號碼共 10 碼，
            deduction_code 來自財務審核。
        profile: 公司基本資料。
        report_period: 申報所屬年月（民國 5 碼）。

    Returns:
        申報資料。
    """
    # TODO(StacieChen): 資料表結構確定後，改為接收 ORM Model 或 schema，並同步調整欄位對應
    invoice_number = str(snapshot.get("invoice_number") or "").replace(" ", "").replace("-", "")
    tax_type = str(snapshot.get("tax_type") or "")
    return Format21Record(
        tax_reg_no=profile.tax_reg_no,
        buyer_ban=profile.ban,
        report_period=report_period,
        issue_year_month=to_roc_year_month(date.fromisoformat(snapshot["invoice_date"])),
        track=invoice_number[:2],
        number=invoice_number[2:],
        seller_ban=str(snapshot.get("seller", {}).get("tax_id") or ""),
        sales_amount=_to_decimal(snapshot.get("sales_amount")),
        tax_type=TAX_TYPE_CODES.get(tax_type, tax_type),
        tax_amount=_to_decimal(snapshot.get("tax_amount")),
        deduction_code=str(snapshot.get("deduction_code") or ""),
    )


def build_format21_file(
    snapshots: list[dict], provider: CompanyProfileProvider, report_period: str
) -> Format21BuildResult:
    """整批檢核核准快照並產生格式 21 申報檔內容。

    任一筆有問題即整批不產檔；同批內以「銷售人統編＋字軌號碼」去重。

    Args:
        snapshots: 核准快照清單。
        provider: 公司基本資料來源。
        report_period: 申報所屬年月（民國 5 碼）。

    Returns:
        產檔結果；檔名為「本公司統編.TXT」。

    Example:
        result = build_format21_file(snapshots, HardcodedCompanyProfileProvider(), "11510")
    """
    profile = provider.get_profile()
    issues: dict[str, list[Format21Issue]] = {}
    if profile_issues := validate_profile(profile):
        issues["company"] = profile_issues

    records: list[Format21Record] = []
    seen: dict[tuple[str, str], str] = {}
    for snapshot in snapshots:
        case_id = str(snapshot.get("case_id", "unknown"))
        record = build_record_from_snapshot(snapshot, profile, report_period)
        found = validate_record(record)
        key = (record.seller_ban, record.track + record.number)
        if key in seen:
            found.append(
                Format21Issue(
                    "invoice_number", "DUPLICATE_IN_BATCH", f"與案件 {seen[key]} 的發票重複"
                )
            )
        else:
            seen[key] = case_id
        if found:
            issues[case_id] = found
        records.append(record)

    if issues:
        return Format21BuildResult(None, None, issues, None, len(records))

    lines = [format_record_line(r, serial=i) for i, r in enumerate(records, start=1)]
    content = LINE_ENDING.join(lines) + LINE_ENDING
    return Format21BuildResult(
        f"{profile.ban}.TXT", content, {}, summarize_401(records), len(records)
    )
