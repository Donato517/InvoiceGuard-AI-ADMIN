"""模組名稱：app.services.invoice_intake_mapping
功能說明：ADMIN 核對表單 ⇄ 既有發票 API 契約的純函式映射（W1）——辨識結果轉表單、表單轉各端點的請求內容、
          API 錯誤／警示／缺漏轉成畫面欄位位置，以及 14 項核對欄位與 API 欄位的對照表。
相依模組：app.schemas.intake
注意事項：
    - 純函式、無 I/O；不連 DB、Storage 或 API。
    - 不複製檢核規則：格式、金額、統編檢查碼等一律由 API 判斷；這裡只做輸入整理（去空白、全形轉半形的數字與代碼欄位、
      去千分位逗號），整理後的值才送出，AI 原值由 API 以 recognition_id 對照，不在 ADMIN 保存或比較。
    - 空白一律送 null（API 的選填欄位視為未提供，必填欄位由 API 回報缺漏／422）。
    - changes（逐次修改紀錄）留空：API 會把「與 AI 原值不同卻沒有紀錄」的欄位補一筆，代價是沒有中間修改歷程。
    - API 目前沒有「備註」欄位，14 項表格中的備註只顯示、不送出（見 FIELD_ROWS）。
"""

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from app.schemas.intake import (
    ApiWarning,
    IntakeError,
    IntakeForm,
    IntakeItemForm,
    MissingFieldView,
    RecognitionView,
)

MAX_ITEMS = 50
TAX_TYPE_OPTIONS: tuple[tuple[str, str], ...] = (
    ("taxable", "應稅"),
    ("zero_rated", "零稅率"),
    ("tax_exempt", "免稅"),
)
HEADER_FIELDS = (
    "invoice_track",
    "invoice_number",
    "invoice_date",
    "seller_tax_id",
    "seller_name",
    "buyer_tax_id",
    "buyer_name",
    "tax_type",
    "sales_amount",
    "tax_amount",
    "total_amount",
    "total_amount_zh",
)
ITEM_FIELDS = ("item_name", "quantity", "unit_price", "line_amount")
# 這些欄位的輸入會先做 NFKC（全形轉半形）並去除所有空白
_CODE_FIELDS = frozenset(
    {"invoice_track", "invoice_number", "invoice_date", "seller_tax_id", "buyer_tax_id", "tax_type"}
)
# 數字欄位另外去除千分位逗號
_NUMBER_FIELDS = frozenset(
    {"sales_amount", "tax_amount", "total_amount", "quantity", "unit_price", "line_amount"}
)


@dataclass(frozen=True)
class FieldRow:
    """14 項核對欄位中的一列：對應的 API 欄位與是否逐筆明細。"""

    index: int
    label: str
    fields: tuple[str, ...]
    per_item: bool = False
    editable: bool = True
    note: str = ""


# 沿用 ADMIN 既有 14 項核對欄位的編號與名稱（_build_14_fields_rows），對應到 API 欄位
FIELD_ROWS: tuple[FieldRow, ...] = (
    FieldRow(1, "發票聯式／識別", (), editable=False, note="由 AI 辨識判斷，僅供參考，不送出"),
    FieldRow(2, "發票字軌號碼", ("invoice_track", "invoice_number")),
    FieldRow(3, "發票日期", ("invoice_date",)),
    FieldRow(4, "買受人名稱", ("buyer_name",)),
    FieldRow(5, "買受人統編", ("buyer_tax_id",)),
    FieldRow(6, "品名／用途", ("item_name",), per_item=True),
    FieldRow(7, "數量", ("quantity",), per_item=True),
    FieldRow(8, "單價", ("unit_price",), per_item=True),
    FieldRow(9, "金額", ("line_amount",), per_item=True),
    FieldRow(10, "銷售額合計", ("sales_amount",)),
    FieldRow(11, "課稅別與稅額", ("tax_type", "tax_amount")),
    FieldRow(12, "總計／國字大寫", ("total_amount", "total_amount_zh")),
    FieldRow(13, "備註", (), editable=False, note="API 目前沒有備註欄位，此欄不會送出"),
    FieldRow(14, "銷貨人", ("seller_name", "seller_tax_id")),
)


class FormLike(Protocol):
    """Starlette FormData／ImmutableMultiDict 的最小介面。"""

    def get(self, key: str, default: Any = ...) -> Any: ...

    def getlist(self, key: str) -> list[Any]: ...


# ---- 輸入整理 ----


def clean_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def clean_code(value: object) -> str:
    """代碼類欄位（字軌、號碼、日期、統編、課稅別）：NFKC 後去除所有空白。"""
    if not isinstance(value, str):
        return ""
    return "".join(unicodedata.normalize("NFKC", value).split())


def clean_number(value: object) -> str:
    """數字類欄位：NFKC、去除空白與千分位逗號。"""
    return clean_code(value).replace(",", "")


def _clean(name: str, value: object) -> str:
    if name in _NUMBER_FIELDS:
        return clean_number(value)
    if name in _CODE_FIELDS:
        return clean_code(value)
    return clean_text(value)


def _null(value: str) -> str | None:
    return value if value else None


# ---- 辨識結果 → 表單 ----


def _text(value: object) -> str:
    return "" if value is None else str(value)


def form_from_recognition(recognition: RecognitionView) -> IntakeForm:
    """把 API 辨識結果轉成可編輯表單（逐欄直接對應，不改值）。"""
    invoice = recognition.invoice
    return IntakeForm(
        recognition_id=str(recognition.recognition_id),
        invoice_track=_text(invoice.invoice_track),
        invoice_number=_text(invoice.invoice_number),
        invoice_date=invoice.invoice_date.isoformat() if invoice.invoice_date else "",
        seller_tax_id=_text(invoice.seller_tax_id),
        seller_name=_text(invoice.seller_name),
        buyer_tax_id=_text(invoice.buyer_tax_id),
        buyer_name=_text(invoice.buyer_name),
        tax_type=_text(invoice.tax_type),
        sales_amount=_text(invoice.sales_amount),
        tax_amount=_text(invoice.tax_amount),
        total_amount=_text(invoice.total_amount),
        total_amount_zh=_text(invoice.total_amount_zh),
        items=tuple(
            IntakeItemForm(
                source_line_no=item.line_no,
                item_name=_text(item.item_name),
                quantity=_text(item.quantity),
                unit_price=_text(item.unit_price),
                line_amount=_text(item.line_amount),
            )
            for item in invoice.items
        ),
    )


# ---- 瀏覽器表單 → 表單 ----


def _source_line_no(value: object) -> int | None:
    text = clean_code(value)
    return int(text) if text.isascii() and text.isdigit() and int(text) >= 1 else None


def form_from_post(data: FormLike) -> IntakeForm:
    """解析核對頁送出的欄位；明細以平行陣列欄位（item_*）傳入，完全空白的列略過（視為刪除）。

    Raises:
        IntakeError: 明細超過 MAX_ITEMS。
    """
    columns = {name: data.getlist(f"item_{name}") for name in ("source_line_no", *ITEM_FIELDS)}
    count = max((len(values) for values in columns.values()), default=0)
    if count > MAX_ITEMS:
        raise IntakeError(f"品名明細最多 {MAX_ITEMS} 筆")

    def cell(name: str, row: int) -> object:
        values = columns[name]
        return values[row] if row < len(values) else ""

    items: list[IntakeItemForm] = []
    for row in range(count):
        item = IntakeItemForm(
            source_line_no=_source_line_no(cell("source_line_no", row)),
            **{name: _clean(name, cell(name, row)) for name in ITEM_FIELDS},
        )
        if any((item.item_name, item.quantity, item.unit_price, item.line_amount)):
            items.append(item)
    return IntakeForm(
        recognition_id=clean_code(data.get("recognition_id", "")),
        items=tuple(items),
        **{name: _clean(name, data.get(name, "")) for name in HEADER_FIELDS},
    )


def with_blank_item(form: IntakeForm) -> IntakeForm:
    """新增一列空白明細（source_line_no 為 None＝使用者新增，AI 未辨識）。"""
    if len(form.items) >= MAX_ITEMS:
        raise IntakeError(f"品名明細最多 {MAX_ITEMS} 筆")
    return replace(form, items=(*form.items, IntakeItemForm()))


def without_item(form: IntakeForm, index: int) -> IntakeForm:
    if not 0 <= index < len(form.items):
        raise IntakeError("找不到要刪除的明細")
    return replace(form, items=tuple(it for pos, it in enumerate(form.items) if pos != index))


# ---- 表單 → 各端點的請求內容 ----


def _item_body(item: IntakeItemForm) -> dict[str, Any]:
    return {
        "source_line_no": item.source_line_no,
        "item_name": _null(item.item_name),
        "quantity": _null(item.quantity),
        "unit_price": _null(item.unit_price),
        "line_amount": _null(item.line_amount),
    }


def check_body(form: IntakeForm) -> dict[str, Any]:
    """check-required／check-warnings 的請求內容：欄位同提交，可缺漏；不含 recognition_id 與 changes。"""
    body: dict[str, Any] = {name: _null(getattr(form, name)) for name in HEADER_FIELDS}
    body["items"] = [_item_body(item) for item in form.items]
    return body


def submit_body(form: IntakeForm) -> dict[str, Any]:
    """POST /api/v1/invoices 的請求內容。changes 留空，API 會補上與 AI 原值不同的欄位。"""
    return {"recognition_id": _null(form.recognition_id), **check_body(form), "changes": []}


def duplicate_body(form: IntakeForm) -> dict[str, Any] | None:
    """查重條件；任一欄位空白就不查（沒有足夠資訊，格式問題交給必填檢核與提交）。"""
    track, number, seller = form.invoice_track, form.invoice_number, form.seller_tax_id
    if not (track and number and seller):
        return None
    return {"invoice_track": track, "invoice_number": number, "seller_tax_id": seller}


# ---- API 回應 → 畫面欄位位置 ----


def item_key(index: int, field: str) -> str:
    return f"items.{index}.{field}"


def field_errors(details: Sequence[tuple[tuple[str | int, ...], str]]) -> dict[str, str]:
    """422 的欄位位置轉成畫面 key（表頭欄位名，或 items.N.欄位）；訊息固定，不回顯原始輸入。"""
    errors: dict[str, str] = {}
    for loc, _kind in details:
        parts = [part for part in loc if part != "body"]
        if not parts:
            continue
        if parts[0] == "items" and len(parts) >= 3 and isinstance(parts[1], int):
            key = item_key(parts[1], str(parts[2]))
        else:
            key = str(parts[0])
        errors.setdefault(key, "格式或內容不正確，請確認")
    return errors


def missing_by_field(missing: Sequence[MissingFieldView]) -> dict[str, str]:
    """必填缺漏 → 畫面 key；明細欄位的 line_no 是送出內容中的序號（從 1 開始）。"""
    result: dict[str, str] = {}
    for entry in missing:
        if entry.line_no is not None and entry.field in ITEM_FIELDS:
            result.setdefault(item_key(entry.line_no - 1, entry.field), entry.message)
        else:
            result.setdefault(entry.field, entry.message)
    return result


def warnings_by_field(warnings: Sequence[ApiWarning]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for warning in warnings:
        grouped.setdefault(warning.field, []).append(warning.message)
    return grouped
