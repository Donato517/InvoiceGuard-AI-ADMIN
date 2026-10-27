"""模組名稱：app.schemas.db_inspection；功能說明：管理者唯讀 DB 盤點回應，不包含資料列或設定值。"""

from typing import Literal

from pydantic import BaseModel, Field

CountStatus = Literal["exact", "missing", "rls_active", "permission_denied", "unsupported_relation"]


class InspectionColumn(BaseModel):
    """僅提供欄位名稱、型別及可空值，不回傳預設值或約束原文。"""

    name: str
    data_type: str
    nullable: bool


class InspectionConnection(BaseModel):
    """表示本次真實查詢結果，不代表登入或業務功能通過。"""

    status: Literal["ok"] = "ok"


class InspectedTable(BaseModel):
    """指定核定資料表的存在、欄位及未受 RLS 過濾的精確筆數。"""

    schema_name: str = Field(serialization_alias="schema")
    name: str
    exists: bool
    columns: list[InspectionColumn] = Field(default_factory=list)
    row_count: int | None = None
    count_status: CountStatus


class DatabaseInspection(BaseModel):
    """盤點結果；只包含核定的非敏感中繼資料與彙總筆數。"""

    service: Literal["InvoiceGuard-AI-ADMIN"] = "InvoiceGuard-AI-ADMIN"
    read_only: Literal[True] = True
    connection: InspectionConnection = Field(default_factory=InspectionConnection)
    table: InspectedTable
