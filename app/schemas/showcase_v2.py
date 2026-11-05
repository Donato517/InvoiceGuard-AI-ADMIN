"""模組名稱：app.schemas.showcase_v2；功能說明：將人工票面答案映射為 v2.0 欄位。"""

import re
import unicodedata
from datetime import date
from typing import Any

from app.schemas.showcase import MAX_ITEM_LINES

TEXT_FIELDS = {"buyer_name", "seller_name", "notes", "total_amount_zh", "seller_tax_id"}
AMOUNT_FIELDS = {"sales_amount", "tax_amount", "total_amount"}
ITEM_FIELDS = {
    "name": "item_name",
    "quantity": "quantity",
    "unit_price": "unit_price",
    "amount": "line_amount",
}
TAX_TYPES = {"應稅": "taxable", "零稅率": "zero_rated", "免稅": "tax_exempt"}


def amount(value: str, scale: int) -> str | None:
    """只接受可精確存進 numeric(18, scale) 的票面數值，不四捨五入。"""
    match = re.fullmatch(r"[+-]?([0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.([0-9]+))?", value)
    if not match:
        return None
    whole, fraction = match.groups()
    whole = whole.replace(",", "").lstrip("0") or "0"
    fraction = (fraction or "").rstrip("0")
    if len(whole) > 18 - scale or len(fraction) > scale:
        return None
    return ("-" if value.startswith("-") else "") + whole + (f".{fraction}" if fraction else "")


def invoice_date(value: str) -> date | None:
    """接受四位西元年及明確的民國年日期。"""
    match = re.fullmatch(
        r"(中華民國|民國)?([0-9]{1,4})[-/年]([0-9]{1,2})[-/月]([0-9]{1,2})日?",
        re.sub(r"\s+", "", value),
    )
    if not match:
        return None
    era, year, month, day = match.groups()
    try:
        return date(
            int(year) + (1911 if len(year) < 4 or era else 0),
            int(month),
            int(day),
        )
    except ValueError:
        return None


def columns(
    revision: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[str]]]:
    """分拆表頭和明細，並回傳可用真值與 SQL 驗證鍵的對照。"""
    header: dict[str, Any] = {}
    items: dict[int, dict[str, Any]] = {}
    keys: dict[str, list[str]] = {}
    for truth in revision["truths"]:
        if truth["state"] != "readable":
            continue
        path, raw = truth["path"], truth["text"]
        value = unicodedata.normalize("NFKC", raw).strip()
        item = re.fullmatch(r"items\.([1-9][0-9]?)\.(name|quantity|unit_price|amount)", path)
        if item and (line := int(item[1])) <= MAX_ITEM_LINES:
            field = ITEM_FIELDS[item[2]]
            normalized = (
                raw if field == "item_name" else amount(value, 2 if field == "line_amount" else 4)
            )
            if normalized is not None:
                items.setdefault(line, {"line_no": line})[field] = normalized
                keys[path] = [f"items[{line - 1}].{field}"]
        elif path in TEXT_FIELDS and (path != "seller_tax_id" or raw == revision["tax_id"]):
            header[path] = raw
            keys[path] = [path]
        elif path in AMOUNT_FIELDS and (number := amount(value, 2)) is not None:
            header[path] = number
            keys[path] = [path]
        elif path == "buyer_tax_id" and re.fullmatch(r"[0-9]{8}", value):
            header[path] = value
            keys[path] = [path]
        elif path == "invoice_number" and (
            number := re.fullmatch(r"([A-Za-z]{2})[ -]?([0-9]{8})", value)
        ):
            header.update(invoice_track=number[1].upper(), invoice_number=number[2])
            keys[path] = ["invoice_track", "invoice_number"]
        elif path == "invoice_date" and (day := invoice_date(value)) is not None:
            end = ((day.month + 1) // 2) * 2
            header.update(invoice_date=day.isoformat(), tax_period=f"{day.year}{end:02d}")
            keys[path] = [path]
        elif path == "tax_type" and (value in TAX_TYPES or value in TAX_TYPES.values()):
            header[path] = TAX_TYPES.get(value, value)
            keys[path] = [path]
    return header, list(items.values()), keys
