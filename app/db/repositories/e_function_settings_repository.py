"""E 本機設定固定 SQL；僅存取 v1.3 字典／矩陣，不提交、不修改 Schema。"""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError as SQLAlchemyError
from sqlalchemy.orm import Session

from app.db.repositories.e_repository import open_session as open_session
from app.schemas.e_admin import Action, FeatureInput, MissingError, UnavailableError
from app.schemas.e_function_settings import (
    DepartmentFunctionSettings,
    FunctionListQuery,
    FunctionPermission,
    FunctionSetting,
)

COLUMNS = "function_id, function_code, function_name, app_scope, is_active"
FILTERS = """
    (:q = '' OR strpos(lower(function_code), lower(:q)) > 0
             OR strpos(lower(function_name), lower(:q)) > 0)
    AND (:platform = 'all' OR app_scope = :platform)
    AND (:status = 'all' OR is_active = (:status = 'active'))
"""
MATRIX = text("""
    SELECT d.department_id, d.department_code, d.department_name, d.is_active AS department_active,
           f.function_id, f.function_code, f.function_name, f.app_scope, f.is_active,
           p.can_view, p.can_create, p.can_edit, p.can_delete, p.updated_at
    FROM public.departments d
    LEFT JOIN public.app_functions f ON TRUE
    LEFT JOIN public.department_permissions p
      ON p.department_id = d.department_id AND p.function_id = f.function_id
    WHERE d.department_id = :department_id
    ORDER BY f.function_code, f.function_id
""")
UPSERT = text("""
    INSERT INTO public.department_permissions
      (department_id, function_id, can_view, can_create, can_edit, can_delete)
    VALUES (:department_id, :function_id, :can_view, :can_create, :can_edit, :can_delete)
    ON CONFLICT (department_id, function_id) DO UPDATE SET
      can_view = EXCLUDED.can_view, can_create = EXCLUDED.can_create,
      can_edit = EXCLUDED.can_edit, can_delete = EXCLUDED.can_delete, updated_at = now()
    WHERE (department_permissions.can_view, department_permissions.can_create,
           department_permissions.can_edit, department_permissions.can_delete)
      IS DISTINCT FROM (EXCLUDED.can_view, EXCLUDED.can_create, EXCLUDED.can_edit, EXCLUDED.can_delete)
""")


def prepare_write(db: Session, *, matrix: bool = False) -> None:
    """短交易限定取鎖時間；可見性不足時拒絕，不停用 RLS 或提升 DB 身分。"""
    db.execute(text("SET LOCAL lock_timeout = '2s'"))
    db.execute(text("SET LOCAL statement_timeout = '5s'"))
    restricted = db.execute(
        text("""
        SELECT pg_catalog.row_security_active('public.departments'::regclass)
            OR pg_catalog.row_security_active('public.app_functions'::regclass)
            OR pg_catalog.row_security_active('public.department_permissions'::regclass)
    """)
    ).scalar_one()
    if restricted is not False:
        raise UnavailableError
    if matrix:
        # 含不存在的 grant 列，需短暫序列化矩陣寫入；不靠本機記憶體鎖。
        db.execute(text("LOCK TABLE public.app_functions IN SHARE MODE"))
        db.execute(text("LOCK TABLE public.department_permissions IN SHARE ROW EXCLUSIVE MODE"))


def list_functions(db: Session) -> tuple[FunctionSetting, ...]:
    """列出安全字典欄位，不讀帳號或機密。"""
    rows = db.execute(
        text(f"SELECT {COLUMNS} FROM public.app_functions ORDER BY function_code, function_id")
    ).mappings()
    return tuple(FunctionSetting(**dict(row)) for row in rows)


def count_functions(db: Session, query: FunctionListQuery) -> int:
    """計算固定條件總數；搜尋以字面子字串匹配。"""
    return db.execute(
        text(f"SELECT count(*) FROM public.app_functions WHERE {FILTERS}"),
        query.model_dump(exclude={"page"}),
    ).scalar_one()


def page_functions(db: Session, query: FunctionListQuery) -> tuple[FunctionSetting, ...]:
    """固定排序及每頁二十筆；搜尋與頁碼一律參數化。"""
    rows = db.execute(
        text(f"""
            SELECT {COLUMNS} FROM public.app_functions WHERE {FILTERS}
            ORDER BY function_code, function_id LIMIT :limit OFFSET :offset
        """),
        query.model_dump(exclude={"page"}) | {"limit": 20, "offset": (query.page - 1) * 20},
    ).mappings()
    return tuple(FunctionSetting(**dict(row)) for row in rows)


def get_function(db: Session, function_id: UUID, *, lock: bool = True) -> FunctionSetting:
    """更新前鎖定指定列；內容頁唯讀不鎖列，找不到回 404。"""
    row = (
        db.execute(
            text(
                f"SELECT {COLUMNS} FROM public.app_functions WHERE function_id=:function_id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"function_id": function_id},
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise MissingError
    return FunctionSetting(**dict(row))


def insert_function(db: Session, data: FeatureInput) -> None:
    """新增字典，不自動授權任何部門。"""
    db.execute(
        text("""
        INSERT INTO public.app_functions (function_code, function_name, app_scope, is_active)
        VALUES (:function_code, :function_name, :app_scope, :is_active)
    """),
        data.model_dump(),
    )


def update_function(db: Session, function_id: UUID, data: FeatureInput) -> None:
    """不改 code、不刪 grants；停用由共用權限 View 的啟用條件排除。"""
    db.execute(
        text("""
        UPDATE public.app_functions SET function_name=:function_name,
          app_scope=:app_scope, is_active=:is_active WHERE function_id=:function_id
    """),
        data.model_dump() | {"function_id": function_id},
    )


def read_matrix(
    db: Session, department_id: UUID, *, lock: bool = False
) -> DepartmentFunctionSettings:
    """單一 SELECT 取得字典與矩陣一致投影；未設定旗標視為拒絕。"""
    if lock:
        row = db.execute(
            text(
                "SELECT department_id FROM public.departments WHERE department_id=:department_id FOR UPDATE"
            ),
            {"department_id": department_id},
        ).one_or_none()
        if row is None:
            raise MissingError
    rows = list(db.execute(MATRIX, {"department_id": department_id}).mappings())
    if not rows:
        raise MissingError
    first = rows[0]
    permissions = []
    for row in rows:
        if row["function_id"] is None:
            continue
        function = FunctionSetting(
            **{
                key: row[key]
                for key in [
                    "function_id",
                    "function_code",
                    "function_name",
                    "app_scope",
                    "is_active",
                ]
            }
        )
        actions = frozenset(action for action in Action if row["can_" + action.value] is True)
        permissions.append(FunctionPermission(function, actions, row["updated_at"]))
    return DepartmentFunctionSettings(
        first["department_id"],
        first["department_code"],
        first["department_name"],
        first["department_active"],
        tuple(permissions),
    )


def write_permission(
    db: Session, department_id: UUID, function_id: UUID, actions: frozenset[Action]
) -> None:
    """只更新提交功能；全部取消保存 false，不刪資料或其他功能列。"""
    db.execute(
        UPSERT,
        {
            "department_id": department_id,
            "function_id": function_id,
            **{"can_" + action.value: action in actions for action in Action},
        },
    )
