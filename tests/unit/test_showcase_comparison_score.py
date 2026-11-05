"""模型比較離線工具的圖片識別、缺漏與失敗分母回歸。"""

import runpy
from copy import deepcopy
from pathlib import Path

import pytest

SCORE = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "tools/score_showcase_comparison.py")
)["score_document"]


def case() -> dict:
    """建立只有合成字串的已核對測試輸入。"""
    run = {"status": "completed", "source_sha256": "a" * 64, "values": {"total_amount": "0"}}
    return {
        "case_id": "synthetic",
        "image_sha256": "a" * 64,
        "truths": {
            "total_amount": {"state": "readable", "text": "0"},
            "unit_price": {"state": "blank", "text": ""},
            "notes": {"state": "unreadable", "text": ""},
        },
        "baseline": deepcopy(run),
        "enhanced": deepcopy(run),
    }


def test_zero_blank_and_unreadable_have_separate_denominators() -> None:
    value = case()
    value["enhanced"]["values"]["notes"] = "猜測"
    score = SCORE({"cases": [value]})
    assert score["summary"]["baseline"]["accuracy"] == 1
    row = score["cases"][0]["enhanced"]
    assert row["blank_correct"] == 1 and row["excluded_with_candidates"] == 1
    assert row["readable"]["fields"] == 1


@pytest.mark.parametrize("failed", [False, True])
def test_failure_and_missing_are_not_excluded(failed: bool) -> None:
    value = case()
    value["baseline"]["values"] = {}
    if failed:
        value["baseline"]["status"] = "failed"
        value["baseline"]["values"]["total_amount"] = "0"
    score = SCORE({"cases": [value]})
    assert score["summary"]["baseline"]["readable_fields"] == 1
    assert score["summary"]["baseline"]["accuracy"] == 0
    assert score["summary"]["baseline"]["failed_cases"] == int(failed)
    if failed:
        assert score["cases"][0]["baseline"]["blank_correct"] == 0


def test_mismatched_image_is_rejected() -> None:
    value = case()
    value["baseline"]["source_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="圖片識別"):
        SCORE({"cases": [value]})


def test_duplicate_image_does_not_inflate_sample_size() -> None:
    first = case()
    second = deepcopy(first)
    second["case_id"] = "second"
    with pytest.raises(ValueError, match="同一圖片"):
        SCORE({"cases": [first, second]})


def test_unconfirmed_truth_does_not_produce_accuracy() -> None:
    value = case()
    value["truths"] = {"total_amount": {"state": "unconfirmed", "text": ""}}
    score = SCORE({"cases": [value]})
    assert score["summary"]["baseline"]["accuracy"] is None
