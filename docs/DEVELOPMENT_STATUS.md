# 開發狀態與工作邊界

## 2026-10-27 部門代碼修改增補

依使用者明確要求，部門頁新增代碼修改（POST `/department/{department_id}/code`）；檢查唯一性、保留 UNASSIGNED、拒絕有代碼外鍵的變更。
真實 DB 已將 Finance Department 改為 finance_d，確認 UUID 與名稱不變；33 項部門測試及架構、Ruff、模板檢查通過。

## 2026-10-27 帳號多部門設定增補

正式帳號新增／修改內容頁新增所屬部門複選及主要部門設定。以既有 user_departments.unassigned_at 停用／恢復關聯，is_current 由 DB 自動計算，與主要部門同交易儲存，保留關聯資料列。
真實 DB 已確認主要部門三欄外鍵，未新增 Schema 或修改 department_permissions；詳細驗證及限制見 [帳號管理](ACCOUNTS.md)。

## 2026-10-27 正式帳號管理增補

新增 `/accounts` 列表頁及共用新增／修改內容頁（`/accounts/new`、`/accounts/{user_id}/edit`），沿用既有 v1.3 帳號 Repository、Session 與新增帳號流程，不在 `/dev` 也不依賴其模組。
依使用者最新授權，管理者直接修改 Email／重設密碼，刪除採 disabled 停用並保留資料；先核對 DB 停用狀態契約。
本機匿名操作採與此次部門功能相同邊界；雲端在正式授權與稽核接線前保持拒絕。原 `/users` 與 `/dev/accounts` 邏輯不變。
全套離線測試 721 passed、9 skipped；本機平台回傳 502，真實 DB 帳號操作未驗收。完整檔案及限制見 [帳號管理](ACCOUNTS.md)。

## 2026-10-27 部門管理增補

正式功能 `/department` 已實作列表、代碼／名稱搜尋、新增、名稱修改、啟用／停用與關聯保護，採既有 SQLAlchemy Session 與固定參數 SQL，未放在 `/dev` 或依賴其模組。
依本次使用者授權，僅此功能允許本機匿名測試；雲端完整關閉，登入與權限原程式保留。正式授權及 v1.3 稽核仍待整合。
舊文「唯一匿名例外為 /dev」對本功能由本次明確授權取代，其他功能維持原限制。
詳細範圍、外鍵保護及驗證限制見 [部門管理](DEPARTMENTS.md)。

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
