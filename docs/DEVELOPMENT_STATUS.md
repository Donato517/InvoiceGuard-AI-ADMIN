# 開發狀態與工作邊界

更新：2026-10-26。本文是目前狀態摘要，歷史聊天、檔名或 TODO 不代表功能完成；修改能力時必須同步本表、實作與測試。

## 本站角色

ADMIN 維持自己的 Vercel／DB 模組、管理者 cookie 登入程式及使用者查詢，不改為只依賴 API。頁面完成度：/system/job-runs 已讀取資料；儀表板、發票審核、使用者管理、稽核紀錄的資料呼叫仍為 TODO。本機無法登入（SESSION_SECRET_KEY 不在本機），需要登入的頁面只能在雲端使用，或以測試替身驗證。第一階段契約 DataService／FileService／get_storage_client() 尚未接線（fail closed）。

## 共同狀態（三站相同）

| 能力 | 狀態與證據 |
|---|---|
| AI 規範入口 | AGENTS.md（三站架構、共同規則、本站現況）＋ CLAUDE.md 匯入；2026-10-26 已提交並 Push 至 feature/python-web |
| 契約／分層檢查 | scripts/check_project.py + contracts/project-contract.json；只做無副作用靜態分析 |
| 本機三站／手機 HTTPS | InvoiceGuard-STARTUP 的 SETUP.cmd／START.cmd；三站各在自己的 CMD 視窗執行並自動重載本站 app/ 的 Python |
| 本機雲端設定 | START 以 Vercel CLI 讀取 ADMIN／API 各自專案的 Production 設定，只把 DATABASE_URL、S3_*、SUPABASE_STORAGE_BUCKET 注入本機該站程序（2026-10-25 核定）；前台不取得。本機 ADMIN／API 的資料就是正式資料 |
| 共用常數 | app/core/constants.py（2026-10-26，三站相同）：正式／本機網址、網站名稱、圖示、搜尋引擎設定 |
| 搜尋引擎 | 三站 noindex：網頁 meta 與 vercel.json 的 X-Robots-Tag（後者部署後生效）；robots.txt 不封鎖抓取 |
| 模組 | ADMIN／API 的 DB 模組（app/db）與 Vercel 模組（檔案存取，app/integrations/supabase_storage.py）可直接操作正式 DB／Storage；ADMIN 的 /dev 示範檔案使用的就是這兩個模組。第一階段契約 DataService／FileService／get_storage_client() 尚未接線，呼叫時拋出 ModuleNotReadyError |
| Schema／部門 | 既有 ORM 為草案；StoredFile、version 與部門模型尚待設計，未與雲端結構核對 |
| 授權 | APP_ENV=local 不跳過登入（唯一例外：ADMIN 本機 /dev 示範檔案）；OperationContext 不可直接信任請求宣告的身分／權限 |
| 模型整合 | 尚未串接；舊 Blob 保留名稱不代表遷移 |
| CI | 已有單站無公司密鑰工作流程（.github/workflows/project-checks.yml）；2026-10-26 已 Push 至 feature/python-web，遠端執行結果須到 GitHub Actions 確認 |

## 本站狀態

| 能力 | 狀態與證據 |
|---|---|
| DB 模組 | 程序有合法 DATABASE_URL 即可使用（本機與雲端），TLS／NullPool／設定去敏保留；2026-10-25 起本機以 START 注入值連線，實測 SELECT 1 = 1 |
| Vercel 模組（檔案存取） | app/integrations/supabase_storage.py（與 API 位元組相同）：上傳、下載、列表、移動、刪除；/dev/files 為示範 |
| ADMIN 唯讀盤點 | GET /system/db-inspection（受網站登入保護）；固定表的存在性、欄位與筆數，無密鑰測試；本機無法登入，只能在雲端使用（尚未部署），見 [DB_READONLY_PROBE.md](DB_READONLY_PROBE.md) |
| 開發者帳號（示範 DB 模組） | 本機 /dev/accounts（示範檔案，不需登入，只在本機開放）：查詢（UUID／Email）、新增（Email＋初始密碼，建立即為 active，同交易寫 user_profiles 與預設部門 UNASSIGNED 的 user_departments）、修改（需目前密碼）；`dev_account_service` → `user_service` → `user_account_repository` 直接讀寫正式 DB，service 控制 commit／rollback。本機讀取已於 2026-10-25 START 驗收實測；表單以 HTMX 送出，同頁更新結果，不換頁、不經網址參數 |
| 檔案（示範 Vercel 模組） | 本機 /dev/files（示範檔案，不需登入，只在本機開放）：圖片（dev/images/，.jpg/.jpeg/.png）與 RAG 文字檔（dev/rag/，.txt/.md），含列表、下載、移動、刪除；`dev_file_service` → `integrations/supabase_storage`。真 START 實測圖片全流程通過；Bucket 只允許 image/jpeg、image/png、application/pdf，RAG 文字檔目前被拒（415），需在 Supabase 加入 text/plain、text/markdown。不寫 DB、不接發票流程；表單以 HTMX 送出，同頁更新結果，不換頁、不經網址參數 |
| 遠端 ADMIN 用戶端 | `remote_admin_client` 保留但沒有呼叫端；本機改為直接使用 DB／Storage |

開發者不取得 Vercel／Supabase 管理員權限；雲端變數更新後，雲端需重新部署、本機需重新 START 才會套用。部署、執行 migration、修改正式資料、Commit／Merge／Push 需使用者明確授權。

## 文件權責

AGENTS.md 說明三站架構、閱讀順序與共同約定；README 說明使用方式；ARCHITECTURE／CODING_CONVENTIONS 管目錄與格式；ADMIN/API MODULE_CONTRACT 管型別與未實作邊界；API 的 app/db/README.md 管 Schema 權威。若文件與實際程式衝突，先指出並對齊，不能擅自繞過保護。單站 clone 無兄弟專案時仍可執行本站檢查；不得為讀取兄弟站而索取管理員 Token。

精確檢查與限制見 [PROJECT_CHECKS.md](PROJECT_CHECKS.md)，實測結果見 [TEST_REPORT.md](TEST_REPORT.md)，修正紀錄見 [REVIEW_FIX_LOG.md](REVIEW_FIX_LOG.md)。
