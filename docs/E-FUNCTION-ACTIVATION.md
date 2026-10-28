> 2026-10-28 最新版本：登入已改為「驗帳密 → 讀目前部門功能／操作 → 網站 Session → 歡迎頁」。配置入口已恢復登入檢查（LOCAL_SETUP_ENABLED=False）；沒有初始管理員、個別管理職務、交接或新增 DB 表。登入唯一寫入為既有 last_login_at。以 [登入說明](E-LOGIN-ACCESS.md) 為準，以下較早政策及測試為歷史記錄。

# 功能目錄啟用失敗修正


日期：2026-10-28。

## 原因與結論

使用者建立 function_code=accounts、app_scope=both 並嘗試啟用時，舊 change_function_status 會要求 supported_actions(code) 非空。registry 只有三項 E 系統能力，因此 accounts 被 EError 擋成「輸入資料不正確」，在執行 UPDATE 前即失敗；不是已確認的 DB 故障、重複代碼或版本衝突。save_function 另有同一舊限制。

目前使用者已改為自行建立功能目錄、在部門內容頁先配置待接線功能，故本機目錄維護不再以 registry／admin 平台限制 is_active。新增仍預設停用，但可選擇啟用；admin/frontend/both 均能維護目錄狀態。固定欄位驗證、不可變代碼、版本指紋／鎖、參數化 SQL、交易及同源與本機邊界不變。沒有自動建立路由、部門 grant 或帳號權限。

列表另列「目錄狀態」「程式對應」；內容頁說明待接線與啟用不同。registry、正式 EModule／require_permission 與工作台驗證完全不改，accounts 不會被自動映射成帳號管理的正式權限代碼，未知能力仍拒絕。已有 registry 也不代表 B／G 正式接線已完成。

啟用是既有 DB is_active 值，其他直接使用 DB View 的組員程式也可能看到此旗標；UI 的待接線標記不是新隔離欄位，跨組仍須以一致正式 guard 驗權。未接線的功能目前無法單憑啟用就產生業務作用。

## 使用者重試

返回 /functions，重新開啟「帳號管理」內容頁，使用最新指紋後按「重新啟用功能」。不要在瀏覽器重新提交舊 POST，避免拿舊指紋覆蓋。這次不更改任何既有正式資料，由使用者操作頁面確認。

## 驗證

既有三項舊限制案例依最新目錄語意更新，另加 accounts 在三種平台啟用／停用及正式 registry 仍拒絕案例。測試只有合成 Session／repository 替身，不連正式 DB；實際結果記錄於開發紀錄。未 Commit／Push。
驗證完成（2026-10-28）：相關回歸 263 passed；完整離線回歸 1116 passed、9 skipped、1 個既有 TestClient/httpx warning（110.54s，exit 0）。新增 accounts 三平台啟用／停用及正式 registry 仍拒絕案例；保留版本、同源、雲端關閉與不自動授權測試。三站 checker PASS（0 issues）、Ruff lint 及格式檢查通過、34 個樣板 lint 0 errors。未對正式 DB 重現或自動啟用任何資料，由使用者重新開啟內容頁後操作；未 Commit／Push。
