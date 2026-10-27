# 開發狀態與工作邊界

更新：2026-10-27。本文是目前狀態摘要，歷史聊天、檔名或 TODO 不代表功能完成；修改能力時必須同步本表、實作與測試。

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

## 06_E 啟動與開發規劃（2026-10-27）

使用者確認 PM 要求以目前 D:\2026102001 的團隊認可三站版本開發，並採已核定 v1.3 PortableAuth。E 負責 2-3、2-6、2-7；Auto Login 暫不啟用。

本輪依最新 PM 指示實際完成 SETUP（exit 0）、START 三站／手機臨時 HTTPS 啟動、ADMIN／API 健康與 /dev/accounts／/dev/files HTTP 200、前台別名 307 及公開登入頁 HTTP 200。使用者親自在瀏覽器新增本人開發帳號並回覆成功；沒有收集帳密或修改其他正式資料。/dev 僅作示範，正式 E 不建於 /dev，也不串接它。

ADMIN 跨站 scripts/check_project.py：PASS、0 issues；既有 pytest：661 passed、9 skipped、1 warning（71.00 秒，exit 0）。測試使用現有無外部服務 fixture，沒有操作正式 DB。warning 為既有 TestClient／httpx 淘汰提示。

以下為本日最初規劃記錄（當時新 E 尚未實作）；最新實作狀態見本文末節。舊 users／int Cookie 與 v1.3 UUID／Session、部門與職務、總經理唯讀、scope／audit／版本接點仍待整合。規劃順序為：身分契約 → 部門新增修改 → 功能目錄／部門 CRUD 矩陣 → PortableAuth／本人頁／忘記密碼 → 跨組驗收。

詳見 [E-DEVELOPMENT-PLAN.md](E-DEVELOPMENT-PLAN.md)、[E-ACCEPTANCE.md](E-ACCEPTANCE.md)。本輪只新增規劃及驗收文件，保留既有三站程式與分支；未執行 migration、commit／push／merge 或部署。START 依使用者最新指示繼續運行，透過原總控視窗可停止本次服務。
### E 開發紀錄與權限要求補充（2026-10-27）

使用者再次要求以目前 D:\2026102001 版本為基準、開發有紀錄，部門與職務需確實隔離。新增 [E-DEVELOPMENT-LOG.md](E-DEVELOPMENT-LOG.md) 與 [E-AUTHORIZATION-REQUIREMENTS.md](E-AUTHORIZATION-REQUIREMENTS.md)，並補計畫／驗收入口。本輪僅文件；使用者已釐清：個人權限即職務角色權限，職務優先於部門；系統管理部門限明確獲指派者可管理；功能與資料範圍分別授權。角色清單／權威介面、具體矩陣／scope、職務未設定與多職務衝突、可授範圍仍待 PM 核定。尚未實作正式授權，不能把現況標示為已完成隔離。
### 06_E 原始任務對照（2026-10-27）

重新讀取 2026-10-21 的 06_E_admin_departments.md，確認目前計畫仍對應 2-3／2-6／2-7，並補明變更密碼 UI、功能分類／route／action、共用 require_permission／scope、初始功能 seed／管理 guard，以及原 E-T01～E-T10。原單 E／G 授權權責與目前 E 設定 UI 需同源接線；不將舊 API／表名覆蓋目前團隊架構。詳見 [E-ORIGINAL-SCOPE-CHECK.md](E-ORIGINAL-SCOPE-CHECK.md)。本輪僅計畫／驗收補強，新功能仍未實作。

## 06_E 第一批程式交付（2026-10-27，最新）

基準 main 2b69083，已建立本機 codex/06e-admin-v13，保留先前文件。程式落在原 ADMIN 分層，未移植舊 Node／admin-e 專案。

| 能力 | 最新狀態 |
|---|---|
| Registry／guard | 已實作固定 E 能力、UUID／Session 綁定、職務明確許可、系統管理資格及獨立 scope；缺接點拒絕 |
| 部門 | 已實作列表／新增／修改 UI、驗證、固定 SQL、影響檢查及交易；正式 journal／DB 整合待接線 |
| 功能及矩陣 | 已實作字典及 G 同源矩陣 UI／委派服務；未知功能不啟用，不直接寫 G 的權限表 |
| 登入／本人 | 已將原 /login、/logout 改接共同 AuthPort；只有顯示名稱可改；忘記／變更密碼委派 B；B 未接線回 503 |
| 其他組路由 | 原 dashboard／invoices／users 等仍為舊 require_admin，尚未移轉到 E；不宣稱全站授權完成 |
| 測試 | ADMIN 全套 731 passed、9 skipped、1 warning（98.00s）；本批新增 70 個離線案例；正式服務未測 |
| 檢查 | 跨站 check_project PASS 0 issues；Ruff lint／format、djLint 23 檔 0 錯、environment 靜態驗證通過 |

正式 B／G／DB journal、真實職務矩陣、各組功能清單與隔離測試 DB 仍待團隊提供。未執行正式 CRUD、seed、migration、部署或 Git 提交／推送。

詳見 [E-TEAM-HANDOFF.md](E-TEAM-HANDOFF.md)、[E-IMPLEMENTATION-RESULTS.md](E-IMPLEMENTATION-RESULTS.md)。前文無功能／舊 Cookie 說明保留作為本次開發前基準；E 最新行為以本節及實際程式為準。
