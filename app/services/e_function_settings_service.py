"""E 本機功能設定交易；使用者核定免登入例外，不冒充 G 的主管授權核心。"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from uuid import UUID

from sqlalchemy.orm import Session

from app.db.repositories import e_function_settings_repository as repository
from app.db.repositories.e_repository import safe_store_error
from app.schemas.e_admin import Action, ConflictError, EError, FeatureInput, UnavailableError
from app.schemas.e_function_settings import (
    DepartmentFunctionSettings,
    FunctionListPage,
    FunctionListQuery,
    FunctionSetting,
    MatrixChange,
)
from app.services.e_adapter import call_adapter
from app.services.e_registry import supported_actions

ACTION_LABELS = {
    Action.VIEW: "查看",
    Action.CREATE: "新增",
    Action.EDIT: "修改",
    Action.DELETE: "刪除",
}
PLATFORM_LABELS = {"admin": "管理站", "frontend": "前台", "both": "前台與管理站"}


def content_revision(value: FunctionSetting | DepartmentFunctionSettings) -> str:
    """內容指紋供本機表單防覆寫，並非正式持久 journal／audit 版本。"""
    raw = json.dumps(asdict(value), default=_json_value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _json_value(value: object) -> object:
    if isinstance(value, frozenset):
        return sorted(value)
    return str(value)


def list_functions() -> tuple[FunctionSetting, ...]:
    """取得可顯示字典，不產生預設資料。"""
    return _run(repository.list_functions)


def search_functions(query: FunctionListQuery) -> FunctionListPage:
    """在 DB 分頁，不將整份字典載入畫面；刪減結果時校正末頁。"""

    def operation(db: Session) -> FunctionListPage:
        total = repository.count_functions(db, query)
        pages = max(1, (total + 19) // 20)
        normalized = query.model_copy(update={"page": min(query.page, pages)})
        return FunctionListPage(normalized, repository.page_functions(db, normalized), total, pages)

    return _run(operation)


def get_function(function_id: UUID) -> FunctionSetting:
    """內容頁只讀取固定欄位，不建立寫入鎖。"""
    return _run(lambda db: repository.get_function(db, function_id, lock=False))


def change_function_status(function_id: UUID, is_active: bool, expected_revision: str) -> None:
    """獨立停用／啟用，保留名稱、平台及授權關聯，過期版本不可覆寫。"""

    def operation(db: Session) -> None:
        repository.prepare_write(db)
        current = repository.get_function(db, function_id)
        if content_revision(current) != expected_revision:
            raise ConflictError
        if is_active and (
            not supported_actions(current.function_code)
            or current.app_scope not in {"admin", "both"}
        ):
            raise EError
        repository.update_function(
            db,
            function_id,
            FeatureInput(
                function_code=current.function_code,
                function_name=current.function_name,
                app_scope=current.app_scope,
                is_active=is_active,
            ),
        )

    _run(operation, write=True)


def save_function(
    data: FeatureInput, function_id: UUID | None = None, expected_revision: str | None = None
) -> None:
    """新增／修改固定欄位；未登錄能力只允許先存停用草稿。"""
    if data.is_active and (
        not supported_actions(data.function_code) or data.app_scope not in {"admin", "both"}
    ):
        raise EError

    def operation(db: Session) -> None:
        repository.prepare_write(db)
        if function_id is None:
            repository.insert_function(db, data)
        else:
            current = repository.get_function(db, function_id)
            if content_revision(current) != expected_revision:
                raise ConflictError
            if current.function_code != data.function_code:
                raise EError
            repository.update_function(db, function_id, data)

    _run(operation, write=True)


def read_matrix(department_id: UUID) -> DepartmentFunctionSettings:
    """取得設定對象，不從此參數認定操作者成員資格。"""
    return _run(lambda db: repository.read_matrix(db, department_id))


def matrix_changes(
    current: DepartmentFunctionSettings,
    expected_revision: str,
    grants: dict[UUID, frozenset[Action]],
) -> tuple[MatrixChange, ...]:
    """預覽與寫入共用驗證，未知目標、過期或新增不支援操作一律拒絕。"""
    if not grants or len(expected_revision) != 64:
        raise EError
    if content_revision(current) != expected_revision or not current.is_active:
        raise ConflictError
    known = {row.function.function_id: row for row in current.rows}
    changes = []
    for function_id, actions in sorted(grants.items(), key=lambda item: str(item[0])):
        if function_id not in known or any(not isinstance(action, Action) for action in actions):
            raise EError
        previous = known[function_id]
        additions = actions - previous.actions
        if additions and (
            not previous.function.is_active
            or previous.function.app_scope not in {"admin", "both"}
            or not additions <= supported_actions(previous.function.function_code)
        ):
            raise EError
        if actions != previous.actions:
            changes.append(MatrixChange(previous.function, previous.actions, actions))
    return tuple(changes)


def review_matrix(
    department_id: UUID, expected_revision: str, grants: dict[UUID, frozenset[Action]]
) -> tuple[DepartmentFunctionSettings, tuple[MatrixChange, ...]]:
    """以目前資料產生變更摘要；不寫入、不取寫入鎖、不更新版本。"""
    current = read_matrix(department_id)
    return current, matrix_changes(current, expected_revision, grants)


def matrix_page(
    matrix: DepartmentFunctionSettings,
    query: FunctionListQuery,
    draft: dict[UUID, frozenset[Action]] | None = None,
) -> dict[str, object]:
    """單一部門的畫面投影；只篩選顯示列，不將其他列帶入儲存。"""
    matches = [
        row
        for row in matrix.rows
        if (
            not query.q
            or query.q.casefold() in row.function.function_code.casefold()
            or query.q.casefold() in row.function.function_name.casefold()
        )
        and (query.platform == "all" or query.platform == row.function.app_scope)
        and (query.status == "all" or row.function.is_active == (query.status == "active"))
    ]
    matches.sort(key=lambda row: (row.function.function_code, str(row.function.function_id)))
    pages = max(1, (len(matches) + 19) // 20)
    query = query.model_copy(update={"page": min(query.page, pages)})
    cells = []
    for row in matches[(query.page - 1) * 20 : query.page * 20]:
        selected = (
            draft.get(row.function.function_id, row.actions) if draft is not None else row.actions
        )
        options = []
        for action in Action:
            reason = _action_reason(matrix, row.function, action)
            previous = action in row.actions
            editable = matrix.is_active and (previous or not reason)
            if reason and editable:
                reason += "；原有設定可撤銷"
            options.append(
                {
                    "action": action,
                    "label": ACTION_LABELS[action],
                    "checked": action in selected,
                    "editable": editable,
                    "reason": reason,
                }
            )
        ready = row.actions & supported_actions(row.function.function_code)
        usable = (
            matrix.is_active
            and row.function.is_active
            and row.function.app_scope in {"admin", "both"}
        )
        cells.append(
            {
                "row": row,
                "actions": options,
                "platform": PLATFORM_LABELS.get(row.function.app_scope, "待確認"),
                "summary": "、".join(ACTION_LABELS[action] for action in Action if action in ready)
                if usable and ready
                else "目前無可用的基本操作",
                "configured": "、".join(
                    ACTION_LABELS[action] for action in Action if action in row.actions
                )
                or "未授予任何操作",
            }
        )
    return {"cells": cells, "query": query, "total": len(matches), "pages": pages}


def _action_reason(
    matrix: DepartmentFunctionSettings, function: FunctionSetting, action: Action
) -> str:
    if not matrix.is_active:
        return "部門已停用，僅供查看"
    if not function.is_active:
        return "功能已停用，不能新增授權"
    if function.app_scope not in {"admin", "both"}:
        return "不適用管理站，不能新增授權"
    if not supported_actions(function.function_code):
        return "程式能力尚未登錄，不能新增授權"
    if action not in supported_actions(function.function_code):
        return "功能未提供此操作"
    return ""


def save_matrix(
    department_id: UUID, expected_revision: str, grants: dict[UUID, frozenset[Action]]
) -> None:
    """同交易鎖定、比較內容並更新提交列；任何失敗全部 rollback。"""
    if not grants or len(expected_revision) != 64:
        raise EError

    def operation(db: Session) -> None:
        repository.prepare_write(db, matrix=True)
        current = repository.read_matrix(db, department_id, lock=True)
        for change in matrix_changes(current, expected_revision, grants):
            repository.write_permission(
                db, department_id, change.function.function_id, change.after
            )

    _run(operation, write=True)


def _run[T](operation: Callable[[Session], T], *, write: bool = False) -> T:
    db = repository.open_session()
    try:
        result = operation(db)
        if write:
            db.commit()
        else:
            call_adapter(db.rollback)
        return result
    except Exception as error:
        call_adapter(db.rollback)
        sanitized = safe_store_error(error)
        if sanitized is not None:
            raise sanitized from None
        if isinstance(error, EError):
            raise
        raise UnavailableError from None
    finally:
        call_adapter(db.close)
