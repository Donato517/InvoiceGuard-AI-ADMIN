"""
模組名稱：app.services.company_profile_service
功能說明：提供申報營業人（本公司）的基本資料，供格式 21 申報檔使用。
相依模組：app.db.repositories.company_profile_repository
注意事項：
    - 正式資料來自 public.company_profile（ADMIN「公司基本資料」頁維護），由 DatabaseCompanyProfileProvider 讀取。
    - 尚未設定、資料表尚未建立或讀取失敗時回傳空白資料，F09 會擋下產檔並提示到設定頁設定，
      不以測試統編代替。
    - 沒有資料庫連線（離線測試、未經 START 啟動）時才使用 HardcodedCompanyProfileProvider 的虛構示範值。
"""

from dataclasses import dataclass
from typing import Protocol

from app.db.repositories import company_profile_repository as repository
from app.schemas.e_admin import UnavailableError


@dataclass(frozen=True)
class CompanyProfile:
    """申報營業人的基本資料。

    Attributes:
        tax_reg_no: 稅籍編號 9 碼，不可用統一編號代替。
        ban: 本公司統一編號 8 碼，同時是進項發票的買受人統編與申報檔檔名。
        name: 公司名稱，僅供畫面顯示與核對，不寫入申報檔。
    """

    tax_reg_no: str
    ban: str
    name: str


class CompanyProfileProvider(Protocol):
    """公司基本資料來源的介面，任何來源都要實作 get_profile()。"""

    def get_profile(self) -> CompanyProfile:
        """取得公司基本資料。"""
        ...


class HardcodedCompanyProfileProvider:
    """寫死的示範公司資料，供開發與測試使用。"""

    def get_profile(self) -> CompanyProfile:
        """回傳虛構的公司基本資料。

        Returns:
            示範用公司基本資料；統編為財政資訊中心建議的測試統編。
        """
        return CompanyProfile(
            tax_reg_no="123456789",
            ban="04595252",
            name="範例科技股份有限公司",
        )


# 尚未設定時的空白資料；F09 validate_profile 會回報「尚未設定本公司基本資料」
NOT_CONFIGURED = CompanyProfile(tax_reg_no="", ban="", name="")


class DatabaseCompanyProfileProvider:
    """從 public.company_profile 讀取本公司資料，供 F09 申報檔使用。"""

    def __init__(self, fallback: CompanyProfileProvider | None = None) -> None:
        self._fallback = fallback or HardcodedCompanyProfileProvider()

    def get_profile(self) -> CompanyProfile:
        """讀取公司基本資料。

        Returns:
            公司基本資料；尚未設定或讀取失敗時為 NOT_CONFIGURED（擋下產檔），
            沒有資料庫連線時為示範資料。
        """
        try:
            db = repository.open_session()
        except UnavailableError:
            return self._fallback.get_profile()
        try:
            repository.prepare(db)
            row = repository.get_profile(db)
        except Exception:
            # 資料表尚未建立、RLS 過濾或連線錯誤：視為未設定，不以示範值產生申報檔
            row = None
        finally:
            db.rollback()
            db.close()
        if row is None:
            return NOT_CONFIGURED
        return CompanyProfile(
            tax_reg_no=row["tax_reg_no"], ban=row["ban"], name=row["company_name"]
        )
