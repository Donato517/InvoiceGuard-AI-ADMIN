"""模組名稱：app.services.e_adapter；功能說明：共同 adapter 的錯誤去敏邊界。"""

from collections.abc import Callable

from app.schemas.e_admin import EError, UnavailableError


def call_adapter[**Params, Result](
    operation: Callable[Params, Result],
    *args: Params.args,
    **kwargs: Params.kwargs,
) -> Result:
    """保留固定業務錯誤；未知錯誤不帶 cause／參數進入 HTTP 或例外日誌。

    Args:
        operation: 僅由伺服器組裝的 adapter 方法。
        args: 不會寫入日誌的內部參數。
        kwargs: 不會寫入日誌的內部參數。

    Returns:
        adapter 原始安全結果。
    """
    try:
        return operation(*args, **kwargs)
    except EError:
        raise
    except Exception:
        raise UnavailableError from None
