# 06_E 階段交付：部門授權登入與選單（2026-10-28）

## 原因與結論

原配置需要額外管理職務、獨立部門功能頁與固定功能選單，與目前核定的「帳號所屬目前部門勾選即授權」不同。本批沿用 v1.3 帳號／員工／部門／功能及四操作資料，完成網站登入 Session、部門內容與授權區整合、內建唯讀歡迎頁、原生登出及部門功能側欄。

## 已完成

- 登入只驗帳密、讀設定組合網站 Session，唯一登入寫入為既有 last_login_at；沒有初始管理員或額外職務資格。
- /department/{UUID}/edit 同頁儲存部門資料與查看／新增／修改／刪除配置，提供全區與逐列全選；歡迎頁固定可查看、不能修改。
- /accounts、/department、/functions 逐操作驗目前部門勾選，配置寫入前交易內重驗；未授權不能以直接輸入 URL 繞過。
- /welcome 為有效員工登入後首頁；側欄依目前部門啟用且適用 ADMIN 的授權目錄顯示。已接線且獲查看許可才產生固定連結，其他項目標示未授權查看或尚未接線。
- 功能目錄新增／修改／啟用、搜尋／篩選／分頁；停用保留資料及授權關聯。舊 /department-functions GET 轉址、POST 410。

## 限制與接續

- 任意新增功能資料不等於新增可執行業務程式；三聯式發票等其他組功能仍需接入實際頁面與對應操作驗權。本批不製作其他組的業務功能。
- 其他業務頁仍可能沿用舊 require_admin；不能把畫面存在當成已完成部門驗權。忘記密碼／本人修改與舊 /system/* journal 不是本批已接妥項目。
- 本機簽章金鑰只存在程序記憶體，重載後需重新登入。登出清 Cookie，不宣稱全站 DB Session 撤銷。
- 本機配置入口沿用既有部署邊界；本批不新增 migration、不修改正式 DB 或雲端環境。

## 驗證

- 提交前本機完整離線回歸：1179 passed、9 skipped、1 個既有 Starlette／httpx warning（124.82s）。
- 本批側欄桌機／手機實際瀏覽器離線 DOM：8 passed。Ruff、djLint、離線專案檢查及 diff --check 通過。
- 待提交的 tracked／untracked 檔案經本機規則檢查；未發現疑似供應商 Token、含密碼連線字串、私鑰或個人 Email，且未納入 .env、.venv、.vercel、tmp。

## Git 交付

先保存本批成果到獨立 E 分支，再整合最新 origin/main 並重驗。上傳分支後以 PR 提交 main，不直接 Push main；是否已合併應以 GitHub PR 狀態為準。依團隊規範，合併前須有組員 review 與 Preview 操作驗收，不把建立 PR 當成合併完成。

交接入口：[登入說明](E-LOGIN-ACCESS.md)、[開發紀錄](E-DEVELOPMENT-LOG.md)、[測試報告](TEST_REPORT.md)。
