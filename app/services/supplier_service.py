"""模組名稱：app.services.supplier_service；功能說明：本機供應商試作服務與交易。"""

import json
from contextlib import closing
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.db.repositories.supplier_demo_repository import (
    SupplierDataConflict,
    SupplierDemoRepository,
)
from app.db.repositories.supplier_demo_repository import (
    SupplierStorageError as SupplierStorageError,
)
from app.schemas.supplier import SupplierInput

DEMO_ACTIONS = {"view": "查看", "create": "新增", "edit": "修改", "disable": "停用"}
SUPPLIER_FUNCTIONS = (
    ("basic", "供應商基本資料", "試作功能", frozenset(DEMO_ACTIONS)),
    ("contact", "聯絡資訊", "隨供應商資料維護", frozenset({"view", "edit"})),
    ("payment", "付款條件", "隨供應商資料維護", frozenset({"view", "edit"})),
    ("delivery", "交貨條件", "隨供應商資料維護", frozenset({"view", "edit"})),
    ("credit", "徵信等級", "模擬手動設定", frozenset({"view", "edit"})),
    ("assessment", "徵信／評估紀錄", "後續規劃 · 尚未開發", frozenset()),
)
FUNCTION_SELECTIONS = frozenset(
    f"{code}.{action}" for code, _, _, actions in SUPPLIER_FUNCTIONS for action in actions
)


class SupplierError(ValueError):
    """可顯示的操作錯誤。"""


def timestamp() -> str:
    """取得含時區的台灣時間。"""
    return datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="microseconds")


class SupplierService:
    """使用本機 Repository；不存在對外連線的工廠。"""

    def __init__(self, repository: SupplierDemoRepository) -> None:
        self.repository = repository

    def initialize(self) -> None:
        """建立本機檔案；只有新檔案加入兩筆虛構範例。"""
        fresh = not self.repository.path.exists()
        self.repository.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.repository.connect()) as db, db:
            self.repository.initialize(db)
            if fresh:
                for name, tax, alias in [
                    ("模擬星禾設備供應商", "00000001", "模擬星禾\n測試設備商"),
                    ("模擬晴川服務供應商", "00000002", "模擬晴川"),
                ]:
                    data = SupplierInput(
                        supplier_name=name,
                        country_code="TW",
                        tax_id=tax,
                        aliases=alias,
                        payment_terms="net",
                        payment_basis="acceptance",
                        payment_days=30,
                        payment_method="transfer",
                        currency="TWD",
                        notes="虛構測試資料，統編非真實交易識別；不得用於付款。",
                    )
                    now = timestamp()
                    self.repository.insert(
                        db,
                        {
                            **data.model_dump(),
                            "supplier_id": str(uuid4()),
                            "is_active": 1,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )

    def list(self, query: str = "") -> list[dict]:
        """依代碼、名稱或別名搜尋。"""
        with closing(self.repository.connect()) as db, db:
            return self.repository.search(db, query.strip())

    def get(self, supplier_id: str) -> dict:
        """讀取詳情；不存在時明確拒絕。"""
        with closing(self.repository.connect()) as db, db:
            result = self.repository.fetch(db, supplier_id)
        if result is None:
            raise SupplierError("找不到這筆供應商資料")
        return result

    def get_function_settings(self, supplier_id: str) -> dict:
        """取得供應商自己的功能列表；初始勾選沿用先前畫面預覽。"""
        self.get(supplier_id)
        with closing(self.repository.connect()) as db, db:
            row = self.repository.fetch_function_settings(db, supplier_id)
        return {
            "selections": frozenset(json.loads(row["selections"])) if row else FUNCTION_SELECTIONS,
            "updated_at": row["updated_at"] if row else "",
        }

    def save_function_settings(
        self, supplier_id: str, selections: frozenset[str], expected_updated_at: str
    ) -> None:
        """保存單筆供應商的模擬功能勾選，不冒充實際權限或啟停。"""
        if not selections <= FUNCTION_SELECTIONS:
            raise SupplierError("功能設定包含未開發或不支援的操作")
        with closing(self.repository.connect()) as db, db:
            self.repository.lock_for_write(db)
            if not self.repository.fetch(db, supplier_id):
                raise SupplierError("找不到這筆供應商資料")
            row = self.repository.fetch_function_settings(db, supplier_id)
            if (row["updated_at"] if row else "") != expected_updated_at:
                raise SupplierError("功能設定已在其他畫面更新，請重新載入後再儲存")
            self.repository.save_function_settings(db, supplier_id, selections, timestamp())

    def save(
        self, data: SupplierInput, supplier_id: str | None = None, expected_updated_at: str = ""
    ) -> str:
        """新增或更新；交易內防止重複統編及舊畫面覆蓋。"""
        try:
            with closing(self.repository.connect()) as db, db:
                self.repository.lock_for_write(db)
                if supplier_id:
                    old = self.repository.fetch(db, supplier_id)
                    if not old:
                        raise SupplierError("找不到這筆供應商資料")
                    if old["updated_at"] != expected_updated_at:
                        raise SupplierError("資料已在其他畫面更新，請重新開啟修改頁後再儲存")
                    self.repository.update(
                        db, supplier_id, {**data.model_dump(), "updated_at": timestamp()}
                    )
                else:
                    supplier_id = str(uuid4())
                    now = timestamp()
                    self.repository.insert(
                        db,
                        {
                            **data.model_dump(),
                            "supplier_id": supplier_id,
                            "is_active": 1,
                            "created_at": now,
                            "updated_at": now,
                        },
                    )
            return supplier_id
        except SupplierDataConflict as error:
            raise SupplierError("相同國家／地區已有此統編或稅籍編號（包含停用資料）") from error

    def set_status(self, supplier_id: str, active: bool, expected_updated_at: str) -> None:
        """核對版本後更新狀態；不刪除資料。"""
        with closing(self.repository.connect()) as db, db:
            self.repository.lock_for_write(db)
            old = self.repository.fetch(db, supplier_id)
            if old is None:
                raise SupplierError("找不到這筆供應商資料")
            if old["updated_at"] != expected_updated_at:
                raise SupplierError("資料已更新，請重新開啟詳情後再操作")
            self.repository.set_status(db, supplier_id, active, timestamp())
