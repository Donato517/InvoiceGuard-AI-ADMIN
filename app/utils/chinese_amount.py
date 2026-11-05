"""
模組名稱：app.utils.chinese_amount
功能說明：整數金額轉國字大寫（3463 → 參仟肆佰陸拾參元整），供 ADMIN 發票核對在辨識當下帶入建議國字。
注意事項：
    - 輸出必須與手機前台 invoice-field-rules.js 的 amountToChinese 逐項相同；兩站共用向量
      tests/fixtures/amount_to_chinese_vectors.json（前台為來源，兩邊檔案須逐位元組相同）。
    - 只轉換整數（12 位數以內）；空白、負數、小數、非數字一律回 None，不猜測。
    - 只做轉換，不判斷國字原值是否正確；對錯由 API 的 total_amount_zh_* 警示負責。
    - 舊示範流程 routers/invoices.py 的 _invoice_chinese_amount（含角分、兆）屬別人的檔案且放在 router 層，
      service 不應反向相依，所以這裡獨立提供純函式；兩者對整數的輸出相同，日後可由該檔改為引用本模組。
"""

import re
import unicodedata

_DIGITS = "零壹貳參肆伍陸柒捌玖"
_SMALL_UNITS = ("仟", "佰", "拾", "")
_INTEGER = re.compile(r"[0-9]{1,12}")
_NOISE = re.compile(r"[\s,，]")
_TRAILING_ZERO_FRACTION = re.compile(r"\.0+$")


def _section(number: int) -> str:
    """0～9999 的一段（不含段尾的萬、億單位）。"""
    out = ""
    pending_zero = False
    for index, base in enumerate((1000, 100, 10, 1)):
        digit = (number // base) % 10
        if digit:
            if pending_zero:
                out += _DIGITS[0]
            out += _DIGITS[digit] + _SMALL_UNITS[index]
            pending_zero = False
        elif out:
            pending_zero = True
    return out


def amount_to_chinese(value: str | int | None) -> str | None:
    """整數金額 → 國字大寫；不是 0～999,999,999,999 的整數回傳 None。

    Args:
        value: 金額字串（可含千分位逗號、空白、全形數字、「.0」尾巴）或整數。

    Returns:
        例如 "14858" → "壹萬肆仟捌佰伍拾捌元整"；0 → "零元整"。
    """
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    text = _TRAILING_ZERO_FRACTION.sub(
        "", _NOISE.sub("", unicodedata.normalize("NFKC", str(value)))
    )
    if not _INTEGER.fullmatch(text):
        return None
    number = int(text)
    if number == 0:
        return _DIGITS[0] + "元整"
    yi, wan, ge = number // 10**8, (number // 10**4) % 10**4, number % 10**4
    out = ""
    if yi:
        out += _section(yi) + "億"
    if wan:
        out += (_DIGITS[0] if yi and wan < 1000 else "") + _section(wan) + "萬"
    if ge:
        out += (_DIGITS[0] if (yi or wan) and ge < 1000 else "") + _section(ge)
    return out + "元整"
