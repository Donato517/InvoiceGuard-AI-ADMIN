"""測試目標：app.utils.format"""

import pytest

from app.utils.format import format_currency, format_date, format_datetime


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1234.5", "NT$ 1,234.5"),
        ("1000", "NT$ 1,000"),
        ("0", "NT$ 0"),
        (None, "-"),
        ("abc", "-"),
    ],
)
def test_format_currency(value: str | None, expected: str) -> None:
    assert format_currency(value) == expected


def test_utc_is_converted_to_taipei_time() -> None:
    assert format_date("2026-10-20T16:30:00Z") == "2026/10/21"
    assert format_datetime("2026-10-20T16:30:00Z") == "2026/10/21 00:30"


def test_empty_or_naive_datetime_shows_dash() -> None:
    assert format_date(None) == "-"
    assert format_datetime("2026-10-21T08:00:00") == "-"
