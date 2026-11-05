"""正式範例與歷史快照一致性、寫入衝突及鎖定契約。"""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.schemas.e_admin import ConflictError
from app.services import showcase_service as service


def revision() -> dict:
    """產生同版號的合成目前版本。"""
    return {
        "id": str(uuid4()),
        "master_supplier_id": str(uuid4()),
        "v2_example_version": 2,
        "status": "draft",
        "source": {"bucket": "private", "key": "synthetic", "sha256": "a" * 64},
        "truths": [],
        "key_fields": [],
        "usable": False,
        "created_at": "2026-11-05",
    }


def test_current_fields_do_not_resurrect_archived_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """正式欄位已清空時，舊歷程的可讀金額不能成為目前答案。"""
    current = revision()
    old = deepcopy(current)
    old["truths"] = [{"path": "total_amount", "state": "readable", "text": "999"}]
    archive = {"payload": {"revisions": [old]}, "version": 3, "can_change_status": True}
    monkeypatch.setattr(service, "open_session", lambda: nullcontext(object()))
    monkeypatch.setattr(service, "authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(service.v2, "get_example", lambda *args: current)
    monkeypatch.setattr(service.repository, "archive_available", lambda db: True)
    monkeypatch.setattr(service.repository, "archive_writable", lambda db: True)
    monkeypatch.setattr(service, "detail", lambda *args: archive)
    result = service.current_detail(object(), uuid4())
    assert result["payload"]["revisions"][-1]["truths"] == []
    assert old["truths"][0]["text"] == "999"


@pytest.mark.parametrize("changed", ["version", "source", "status", "linked", "missing"])
def test_status_conflict_writes_nothing(monkeypatch: pytest.MonkeyPatch, changed: str) -> None:
    """歷程與正式資料不符時，在更新及提交前拒絕狀態操作。"""
    old = revision()
    current = deepcopy(old)
    if changed == "version":
        current["v2_example_version"] += 1
    elif changed == "source":
        current["source"]["key"] = "new-source"
    elif changed == "status":
        current["status"] = "inactive"
    elif changed == "linked":
        current["linked_source"] = True
    else:
        current = None
    db = MagicMock()
    reader = MagicMock(return_value=current)
    sample = uuid4()
    monkeypatch.setattr(service, "open_session", lambda: nullcontext(db))
    monkeypatch.setattr(service, "authorize", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        service,
        "_managed_row",
        lambda *args, **kwargs: {"version": 3, "payload": {"revisions": [old]}},
    )
    monkeypatch.setattr(service.v2, "get_example", reader)
    save = MagicMock()
    monkeypatch.setattr(service.repository, "save", save)
    with pytest.raises(ConflictError):
        service.change_status(
            SimpleNamespace(user_id=uuid4()), sample, 3, "deactivate", "停用", False
        )
    reader.assert_called_once_with(db, sample, lock=True)
    save.assert_not_called()
    db.execute.assert_not_called()
    db.commit.assert_not_called()


def test_current_reader_locks_only_when_requested() -> None:
    """查閱保持唯讀，狀態交易才要求正式範例資料列鎖。"""
    db = MagicMock()
    db.execute.return_value.mappings.return_value.one_or_none.return_value = None
    service.v2.get_example(db, uuid4())
    assert "FOR UPDATE" not in str(db.execute.call_args.args[0])
    service.v2.get_example(db, uuid4(), lock=True)
    assert str(db.execute.call_args.args[0]).endswith("FOR UPDATE OF e")
