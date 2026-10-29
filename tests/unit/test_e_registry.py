"""E07 初始資料與登錄契約測試；不建立功能、角色或 DB seed。"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.schemas.e_admin import UnavailableError
from app.services.e_registry import REGISTRY, initial_manifest, validate_registry


@pytest.mark.parametrize(
    "route",
    [
        "https://external.invalid/admin",
        "//external.invalid",
        "/dev/accounts",
        "/api/dev/accounts",
        "/system/../dev",
        "/system/%2fdev",
        "/system/features?next=bad",
        "/system\\features",
    ],
)
def test_registry_rejects_unsafe_and_demo_routes(route: str) -> None:
    with pytest.raises(UnavailableError):
        validate_registry((replace(REGISTRY[0], route=route),))


def test_registry_rejects_duplicate_codes_and_unknown_actions() -> None:
    with pytest.raises(UnavailableError):
        validate_registry((REGISTRY[0], REGISTRY[0]))
    with pytest.raises(UnavailableError):
        validate_registry((replace(REGISTRY[0], actions=frozenset({"approve"})),))


def test_review_manifest_matches_registry_and_grants_nothing() -> None:
    artifact = Path(__file__).resolve().parents[2] / "docs" / "E-INITIAL-FUNCTIONS.json"
    manifest = initial_manifest()
    assert json.loads(artifact.read_text(encoding="utf-8")) == manifest
    assert manifest["status"] == "review_required"
    assert {row["function_code"] for row in manifest["functions"]} == {
        item.code for item in REGISTRY
    }
    for row in manifest["functions"]:
        assert row["is_active"] is False
        assert row["app_scope"] == "admin"
        assert row["actions"] == sorted(row["actions"])
        assert "grants" not in row and "user_id" not in row
    assert set(manifest["pending_domains"]) == {"finance", "procurement", "accounts", "knowledge"}
