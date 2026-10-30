"""
模組名稱：app.services.company_profile_service
功能說明：提供申報營業人（本公司）的基本資料，供格式 21 申報檔使用。
相依模組：無
注意事項：
    - 目前以 HardcodedCompanyProfileProvider 寫死示範資料，全部為虛構測試值。
    - 改以資料庫為準時，新增實作 get_profile() 的類別並替換 provider 即可，
      format21_service 不需修改。
"""

from dataclasses import dataclass
from typing import Protocol


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

    # TODO(StacieChen): 確認公司主檔是否以資料庫為準，確認後改為讀取資料庫的 provider

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
