"""離線比較已保存的辨識結果與人工答案，產生純統計報告。

執行方式為 python -B tests/tools/score_showcase_comparison.py INPUT.json OUTPUT.json。
輸入與輸出皆由操作者指定，僅讀取既有結果，不呼叫模型。
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.services.showcase_analysis_service import metrics  # noqa: E402


def score_run(run: dict[str, Any], truths: dict[str, Any], digest: str) -> dict[str, Any]:
    """檢查圖片識別與執行狀態，逐字比較人工已核對的欄位。"""
    if run.get("source_sha256") != digest:
        raise ValueError("模型結果與測試圖片識別不一致")
    status = run.get("status")
    if status not in {"completed", "failed"}:
        raise ValueError("模型結果需明確標示 completed 或 failed")
    values = run.get("values", {})
    if not isinstance(values, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in values.items()
    ):
        raise ValueError("模型候選需為欄位與字串的對照")
    # 失敗回覆的殘留欄位不作為成功辨識，也不能把失敗算成正確辨識空白。
    if status == "failed":
        values = {}
    readable = {}
    blank = []
    excluded = []
    for field, truth in truths.items():
        if not isinstance(truth, dict):
            raise ValueError("人工答案格式不符")
        state, text = truth.get("state"), truth.get("text", "")
        if not isinstance(field, str) or not isinstance(text, str):
            raise ValueError("人工答案需使用欄位名稱與原文字串")
        if state == "readable" and text.strip():
            readable[field] = text
        elif state == "blank" and not text:
            blank.append(field)
        elif state in {"unreadable", "not_applicable", "unconfirmed"} and not text:
            excluded.append(field)
        else:
            raise ValueError("人工答案的狀態與原文不一致")
    return {
        "run_status": status,
        "readable": metrics(values, readable),
        "blank_fields": len(blank),
        "blank_correct": sum(not values.get(field) for field in blank)
        if status == "completed"
        else 0,
        "excluded_fields": len(excluded),
        "excluded_with_candidates": sum(bool(values.get(field)) for field in excluded),
    }


def score_document(document: dict[str, Any]) -> dict[str, Any]:
    """以同圖的基線與參考結果產生報告，避免重複圖片灌入樣本數。"""
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("至少需要一筆測試案例")
    seen_ids: set[str] = set()
    seen_images: set[str] = set()
    results = []
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("測試案例格式不符")
        identifier, digest = case.get("case_id"), case.get("image_sha256")
        if not isinstance(identifier, str) or not identifier.strip() or identifier in seen_ids:
            raise ValueError("案例識別需非空且不重複")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("圖片識別需為小寫 SHA-256")
        if digest in seen_images:
            raise ValueError("同一圖片不可重複計入測試集合")
        truths = case.get("truths")
        if not isinstance(truths, dict) or not truths:
            raise ValueError("每筆案例需提供人工核對狀態")
        if not all(isinstance(case.get(name), dict) for name in ("baseline", "enhanced")):
            raise ValueError("每筆案例需同時提供基線與參考結果")
        seen_ids.add(identifier)
        seen_images.add(digest)
        results.append(
            {
                "case_id": identifier,
                "baseline": score_run(case["baseline"], truths, digest),
                "enhanced": score_run(case["enhanced"], truths, digest),
            }
        )
    summary = {}
    for name in ("baseline", "enhanced"):
        scores = [row[name] for row in results]
        fields = sum(score["readable"].get("fields", 0) for score in scores)
        correct = sum(score["readable"].get("correct", 0) for score in scores)
        summary[name] = {
            "failed_cases": sum(score["run_status"] == "failed" for score in scores),
            "readable_fields": fields,
            "correct": correct,
            "accuracy": correct / fields if fields else None,
        }
    return {
        "case_count": len(results),
        "comparison": "exact_text",
        "summary": summary,
        "cases": results,
    }


def main() -> None:
    """從本機 JSON 讀取已保存結果，檢查通過後才寫出統計。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("輸入與輸出必須分開")
    document = json.loads(args.input.read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict):
        parser.error("輸入需為 JSON 物件")
    result = score_document(document)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"已完成 {result['case_count']} 筆案例的離線評分")


if __name__ == "__main__":
    main()
