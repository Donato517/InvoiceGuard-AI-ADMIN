"""模組名稱：app.schemas.department_detail；功能說明：部門內容頁與四項操作配置契約。"""

from dataclasses import dataclass

from pydantic import Field

from app.schemas.department import Department
from app.schemas.e_admin import DepartmentInput
from app.schemas.e_function_settings import DepartmentFunctionSettings


class DepartmentDetailInput(DepartmentInput):
    """部門資料與整頁授權共同的原內容指紋；不接受角色或 Session。"""

    department_code: str = Field(min_length=1, max_length=200)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class DepartmentDetail:
    """同一快照的部門基本資料與完整功能配置。"""

    department: Department
    matrix: DepartmentFunctionSettings
