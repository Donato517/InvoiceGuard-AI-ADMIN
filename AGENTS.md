> 2026-10-28 最新版本：登入已改為「驗帳密 → 讀目前部門功能／操作 → 網站 Session → 歡迎頁」。配置入口已恢復登入檢查（LOCAL_SETUP_ENABLED=False）；沒有初始管理員、個別管理職務、交接或新增 DB 表。登入唯一寫入為既有 last_login_at。以 [登入說明](docs/E-LOGIN-ACCESS.md) 為準，以下較早政策及測試為歷史記錄。

# InvoiceGuard-AI-ADMIN：AI 開發入口

> 2026-10-28 最新介面：部門內容頁 /department/{UUID}/edit 同頁儲存資料與四操作配置；移除獨立部門功能頁，舊 GET 只轉址、舊 POST 410。未接線功能可先存配置，不宣稱正式生效；免登入仍限本機。見 [E-DEPARTMENT-DETAIL](docs/E-DEPARTMENT-DETAIL.md)。

> 2026-10-28 使用者最新核定：四個本機配置入口暫免登入以補齊設定，新增 /welcome；雲端仍關閉。這取代下文 10-27 的本機關閉規則，詳見 [E-LOCAL-SETUP](docs/E-LOCAL-SETUP.md)。正式 B／G 及主管授權未完成，代理只離線測試，正式 DB 由使用者操作。

本站是部門管理後台，使用 Python 3.12、FastAPI、Jinja2 與 HTMX；ASGI 入口為 `app.main:app`。ADMIN 保有自己的 Vercel／DB 模組與管理者登入；一般資料／檔案服務尚未接線，不能將畫面入口視為 CRUD／Storage 完成。

依序閱讀：`README.md` → `docs/DEVELOPMENT_STATUS.md` → `docs/ARCHITECTURE.md` → `docs/CODING_CONVENTIONS.md` → `docs/MODULE_CONTRACT.md`。修改資料存取前再讀 `app/db/README.md`；修改環境設定前讀 `docs/ENVIRONMENT_ISOLATION.md`。

本站分層為 `routers → services → db/repositories → db/models`；service 控制交易，repository 不 commit。管理者與部門授權不可省略，不得擅自把 ADMIN 改成 API 代理。API 管理共用 DB Schema／Alembic；本站只有同步的 Model，不能產生自己的 migration。UI 額外驗證：`.\.venv\Scripts\python.exe -B -m djlint app/templates --lint`。

## ADMIN 現況（2026-10-26）

- DB：程序有合法 `DATABASE_URL` 即可使用（本機與雲端皆可，仍驗證 PostgreSQL 格式與 TLS）；本機值由 START 注入，連的是正式 DB。
- Vercel 模組（檔案存取）：`app/integrations/supabase_storage.py`，與 API 相同；本機值由 START 注入，連的是正式 Storage。
- AI 模組（2026-10-29 使用者核定）：`app/integrations/document_ai.py`、`gemini.py`、`groq.py`、`chat_models.py` 與 API 逐位元組相同（`tests/unit/test_shared_integrations.py` 比對），Settings 有對應欄位；本機由 START 從 ADMIN 自己的 Vercel 專案注入 Google AI 五項與 GROQ_API_KEY（缺少時只提示），雲端需在 Vercel ADMIN 專案設定。只提供模組，尚未接線任何功能；ADMIN 函式上限 30 秒，接線時要以 `timeout_seconds` 控制時限。
- 示範檔案 `/dev/accounts`（DB 模組：帳號查詢／新增／修改）與 `/dev/files`（Vercel 模組：圖片／RAG 檔案）只在 `dev_pages_enabled` 為真（`APP_ENV=local`、非 Vercel、解析後 `module_backend=remote`，含本機 auto）時開放、不需登入，直接讀寫正式 DB／Storage。
- 舊 `require_admin` 路由仍受本機禁用 `SESSION_SECRET_KEY` 限制。06_E 的 `/login` 已改委派 B 共用 PortableAuth；B／G adapter 尚未接線時回 503，不開本機繞過。E 測試以 `get_e_module` 的 dependency override 提供合成替身，正式程式沒有測試模式開關；見 `docs/E-TEAM-HANDOFF.md`。
- `remote_admin_client` 目前沒有呼叫端；`MODULE_BACKEND=remote` 實際代表「本機開發」並開放 `/dev` 示範檔案。

## 2026-10-27 最新核定：ADMIN 登入與配置入口恢復驗權

- 使用者取消 /accounts、/department、/functions、/department-functions 的本機免登入例外。它們及所有子路由保留本機／Host／peer／同源條件，再經 require_configuration_access 驗身分與明確操作。無 Cookie 導向登入；B／G 或正式資料／交易接線不足時 503，不放行配置服務。
- ADMIN 登入頁依附件外觀共用桌機／手機響應式。正式認證、G 與 journal 尚未交付；只用合成替身測試，不從示範帳密、舊 BIGINT users、web storage 或任意角色 Header 建立正式身分。
- 部門基本功能＋主管負責／明確委派、目前選取部門與獨立 scope 規則不變。既有配置 UI／服務仍保留，待正式資料範圍與交易重驗完成後驗收，不可直接刪除 pending 閘門。詳見 docs/E-LOGIN-ACCESS.md。

## 2026-10-27 第三批歷史規則（匿名例外已取消）

- 指定 /accounts、/department、/functions、/department-functions 暫時本機免登入；必須保留 local／非 Vercel／remote、loopback、固定 Host 與嚴格同源限制，雲端關閉。共用正式 DB 由使用者在頁面操作；代理只做離線測試，不做正式資料讀寫驗收。
- 授權改為「目前選取有效部門的基本功能＋主管授權管理」，不聯集多部門。主管可管理本部門及外部部門，但只限負責／明確委派功能。操作與資料 scope 分開；系統部門／功能字典的 system_management 資格不自動授予主管。
- 此使用者例外優先於下方舊的一般本機不得免登入條款，只限上述配置入口。正式 /login／本人／工作台與 /system/* 的 B／G／journal 仍未組裝，保持缺接線拒絕。見 docs/E-FUNCTION-PAGES.md 及 docs/E-ACCESS-CONTRACT.md。

<!-- shared-rules:start -->
## 三站架構

- 能串聯雲端 DB／Storage 的只有 ADMIN 與 API。
- `InvoiceGuard-AI`（前台）：使用者網站；不連 DB、Storage 或模型服務，伺服器端經 `app/clients/` 以 `API_BASE_URL` 呼叫 API 平台串聯雲端；不持有公司機密。
- `InvoiceGuard-AI-ADMIN`（後台）：管理網站；以自己的 DB 模組與 Vercel 模組直接串聯雲端，不經 API 平台。
- `InvoiceGuard-AI-API`（後端 API）：供前台伺服器呼叫的 REST API、第三方串接與排程；以自己的 DB 模組與 Vercel 模組串聯雲端；共用 DB Schema 與 Alembic 的唯一管理者。
- ADMIN／API 的模組：DB 模組 `app/db`；Vercel 模組（檔案存取）`app/integrations/supabase_storage.py`；AI 模組 `app/integrations/document_ai.py`（Document AI OCR）、`gemini.py`、`groq.py` 與共用的 `chat_models.py`（2026-10-29 起兩站同版；ADMIN 只提供模組，尚未接線功能）。`app/integrations/vercel.py` 只是執行環境資訊與設定檢查。
- `/dev` 是示範檔案，不是串聯路徑；示範的就是上述模組：ADMIN `/dev/accounts` 示範 DB 模組、`/dev/files` 示範 Vercel 模組，只在本機開放。API 也可以有 `/dev` 示範檔案，但本機開發與前台都不串接 API 的 `/dev`。
- 三站各自部署於 Vercel：前台 `https://invoice-guard-ai.vercel.app`、API `https://invoice-guard-ai-api.vercel.app`、ADMIN `https://invoice-guard-ai-admin.vercel.app`。三站共用的公開網址、網站名稱、圖示與搜尋引擎設定集中在 `app/core/constants.py`。
- 三站可互相連結，但一律不被搜尋引擎收錄：網頁 `<meta name="robots">` 與 `vercel.json` 的 `X-Robots-Tag` 為 `noindex, nofollow`；`/robots.txt` 不封鎖抓取，否則搜尋引擎讀不到 noindex。
- 三站內容相同、修改時須同步三站的檔案：`app/core/environment.py`（由 `check_project` 快照比對）、`app/core/constants.py`、`app/routers/robots.py`、`tests/unit/test_constants.py`（後三者手動同步，檢查程式不比對）；ADMIN 與 API 的 `app/integrations/supabase_storage.py` 與四個 AI 模組檔亦相同，由兩站相同的 `tests/unit/test_shared_integrations.py` 在兩站同目錄時逐位元組比對。
- 功能完成度以各站 `docs/DEVELOPMENT_STATUS.md` 與實際程式為準；本機日常啟動見下方「本機入口」。

## 共同規則

- 使用者最新的明確指令優先；先讀此入口，再讀上述文件與目標程式。文件若與程式衝突，指出差異並修正對齊，不能把規劃或範例當成已完成功能。
- 三個 Repository 固定命名為 `InvoiceGuard-AI`、`InvoiceGuard-AI-ADMIN`、`InvoiceGuard-AI-API`。沿用既有 Python 3.12／FastAPI 架構，UI 沿用 Jinja2／HTMX；不得另建框架或假設新的 API 入口。
- ADMIN 與 API 各自保有 Vercel／DB 模組。ADMIN 依既有分層使用自己的 DB，不擅自改為 API 代理；API 是共用 DB Schema 與 Alembic 的唯一管理者。ADMIN Model 必須與 API 一致，不能自行更改表結構。
- ADMIN／API 的第一階段契約 `DataService`、`FileService`、`get_storage_client()` 尚未接線，呼叫時拋出 `ModuleNotReadyError`；不得用假成功代替實作。

## 設定與安全邊界

- 業務程式一律從 `app.core.config.settings` 取得設定，不自行讀取 `.env`、列印環境變數或新增任意設定透傳。公開設定須依核定欄位與驗證規則擴充。
- 公司機密（DB 密碼、S3 Key、模型 Key、Supabase secret key、session／cron secret）由雲端管理，不放 Git、測試報告、日誌、URL 或公開設定端點；測試只用明確的合成假值與替身。
- 本機例外（2026-10-25 使用者核定，2026-10-28、2026-10-29 擴充）：START 以 Vercel CLI 讀取 ADMIN／API 各自專案的 Production 設定，把 `DATABASE_URL`、`S3_*`、`SUPABASE_STORAGE_BUCKET` 注入本機該站程序記憶體；ADMIN 與 API 另各自注入自己專案的 Google Document AI／Gemini 設定（`GOOGLE_CLOUD_PROJECT_ID`、`GOOGLE_CLOUD_LOCATION`、`DOCUMENT_AI_PROCESSOR_ID`、`GOOGLE_SERVICE_ACCOUNT_JSON`、`GEMINI_API_KEY`）與 Groq 金鑰（`GROQ_API_KEY`）（API 2026-10-28、ADMIN 2026-10-29 核定；ADMIN 只有 DB／S3 為啟動必要，AI 設定缺少時 START 只提示）；前台不取得。這些是必要設定，值只存在 START 啟動的該站程序記憶體（不經 START、不在該站 venv 程序內就取不到），不視為本機禁用機密；START 未注入的其他公司機密出現在本機程序環境時，站台仍拒絕啟動。
- 因此本機 ADMIN／API 連到的是正式（Production）DB 與 Storage，本機寫入就是正式資料寫入。`APP_ENV=local` 不能繞過登入、部門權限或正式 API 驗證；唯一例外是 ADMIN 只在本機開放的 `/dev` 示範檔案。
- 未經使用者明確授權，不部署、不執行 migration、不修改正式資料、不 Commit／Merge／Push；保留既有分支、未提交修改及索引，不執行 reset 或覆蓋他人工作。建立 migration 檔屬 API 的一般開發，執行仍需授權。

## 檔案與格式

- 僅在本站核定的既有目錄與分層內擴充：HTTP 入口放 `app/routers/`，無狀態工具放 `app/utils/`；ADMIN／API 的資料契約放 `app/schemas/`、業務放 `app/services/`、外部服務放 `app/integrations/`。前台只使用 core、utils、clients、routers、static、templates，不建立 schemas、DB 或 service 目錄，不因其他站有此目錄就自行新增。
- ADMIN／API 的 DB 查詢放 `app/db/repositories/`、定義放 `app/db/models/`；交易由 service 控制，repository 不 `commit`。Router 不直接操作 DB／Storage／模型；core／utils 原則上不反向依賴 router／service，既有 DI／例外適配等精確例外以 `docs/PROJECT_CHECKS.md` 為準，不得擴大允許範圍。
- 前台與 ADMIN 的 UI 樣板放 `app/templates/`，公開 CSS／JS／圖片才放 `app/static/`；API 只回 JSON，沒有樣板與靜態檔目錄。實際上傳、機密、私人文件及 runtime 檔不得放 static 或版本控制。
- 暫存檔沿用 `app/utils/file_utils.py` 的受控暫存生命週期，不自行拼接使用者路徑；永久檔案的 key／權限／刪除政策須先依契約明確定義，不自行決定。
- Python 模組／函式／變數用英文 snake_case，類別用 PascalCase；中文說明使用繁體中文。Python 檔頭須有用途與模組 docstring，所有函式參數與回傳值加型別；公開函式寫繁中 docstring，格式依 `CODING_CONVENTIONS.md`。樣板使用 Jinja 備註描述 route／context。
- 契約變動須同步 Schema、Protocol、文件與測試；不可直接改快照、fixture 或允許清單來掩蓋失敗。跨站副本先確認來源，再同步及檢查差異。

## 本機入口

啟動器的 `SETUP.cmd` 從三個固定名稱 Repository 的共同上層推導並記錄 `PROJECT_ROOT`，不在程式中寫死個人絕對路徑。從本站 PowerShell 執行：

```powershell
# 初次設定：建立／使用三站各自的 venv 並安裝依賴（相容指令：..\InvoiceGuard-STARTUP\START.cmd setup）
..\InvoiceGuard-STARTUP\SETUP.cmd
# 日常啟動：三站、本機別名及電腦／手機共用的臨時 HTTPS；不需 --mobile，保留視窗直到停止
..\InvoiceGuard-STARTUP\START.cmd
```

START 就緒後自動開啟當次 HTTPS 的 `/login`，電腦與手機都直接看到前台原有的 Email／密碼登入表單；不需要開發存取碼，也不會出現額外的瀏覽器帳密視窗，網站登入、部門權限與 API 驗證照舊。開發電腦另有本機別名 `http://invoiceguard-ai.localhost:8080/login`（307 轉至同一次 HTTPS 的 /login）、`http://invoiceguard-ai-admin.localhost:8080/`、`http://invoiceguard-ai-api.localhost:8080/health`；ADMIN／API 只供開發電腦，手機不能使用電腦的 localhost 或 `.localhost`。

三站各在自己的 CMD 視窗執行，存檔 `app/` 內的 Python 只重載該站。在某站視窗按 Ctrl+C 只停止該站，貼上視窗顯示的啟動指令即可重啟；在 START 總控視窗按 Ctrl+C 才停止全部。啟動器只停止自己啟動的服務，不終止其他 Python 或 cloudflared 程序。

## 無公司密鑰驗證

從本站根目錄依序執行；所有測試不得呼叫正式服務：

```powershell
.\.venv\Scripts\python.exe -B scripts/check_project.py
.\.venv\Scripts\python.exe -B -m pytest -q
.\.venv\Scripts\python.exe -B -m ruff check app tests scripts
.\.venv\Scripts\python.exe -B -m ruff format --check app tests scripts
.\.venv\Scripts\python.exe -B -m app.core.environment --check
```

`check_project.py` 可在只有本站時獨立檢查；若三站都在同一根目錄，可加 `--project-root <root>`，比對跨站非機密契約快照。這是額外一致性檢查，不是下載或掃描公司機密。失敗時先查原因、補有意義的回歸測試，再修正；不能任意放寬規則。

完成後回報目標檔案、行為差異、實際執行的指令與結果、未驗證部分。發現問題並修正時，在既有 `docs/REVIEW_FIX_LOG.md` 記錄問題、原因、處理檔案與驗證結果；沒有跑過的測試不能寫通過。
<!-- shared-rules:end -->
