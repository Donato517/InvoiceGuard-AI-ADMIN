"""E2E-1 冒煙：兩個服務都啟動、資料庫有種子資料。"""

import httpx


def test_services_are_up(stack) -> None:  # type: ignore[no-untyped-def]
    assert httpx.get(f"{stack.api_url}/health", timeout=5).status_code == 200
    assert httpx.get(f"{stack.admin_url}/health", timeout=5).status_code == 200
    assert (
        stack.scalar("SELECT count(*) FROM public.departments WHERE department_code LIKE 'e2e_%'")
        == 2
    )
