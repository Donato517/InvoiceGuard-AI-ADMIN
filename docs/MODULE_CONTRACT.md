# ADMIN Vercel / DB 模組與契約

> 2026-10-30 後續核定：三模組已依要求同步至 API，兩站程式維持相同內容；下方本日早期分版敘述不再適用。來源綁定、欄位圖片、授權與逾時契約仍保留，沒有新增業務接線。

> 2026-10-30 Document AI 三模組僅整合 ADMIN，與 API 分別版本化。新增 Pillow，OCR 與 Few-Shot 沿用既有 Google／Gemini 設定；設定工廠不讀 dotenv，不新增 DB 或 Storage 寫入。辨識結果進入裁圖前由受信任 service 呼叫 `bind_recognition()`；多張 TARGET 圖片的欄位必須宣告 `target_artifact_ids`。使用、限制與離線證據見 [檢驗報告](DOCUMENT_AI_REVIEW_20261030.md)。

AI 入口見 [AGENTS.md](../AGENTS.md)／[CLAUDE.md](../CLAUDE.md)；
本文件保留第一階段的實作範圍與歷史說明，目前能力以 [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md) 為準。
新增或同步契約後執行 [無密鑰契約／分層檢查](PROJECT_CHECKS.md)。

第一階段完成設定驗證、Python 型別介面、輸入／輸出驗證及離線單元測試；其後加入本機也可使用的 DB 模組、Vercel 模組（檔案存取，`integrations/supabase_storage.py`）與 `/dev` 示範檔案（見 DEVELOPMENT_STATUS）。
部署、migration 與正式資料寫入需使用者明確授權。

## 可用與未完成的能力

| 能力 | 狀態 |
|---|---|
| `get_platform()` 安全執行描述 | 已實作，不含密鑰 |
| `check_configuration()` 與 CLI | 已實作；只做靜態設定檢查 |
| DB Engine 設定與 Session 關閉 | 已實作保護與參數；2026-10-25 本機實測 SELECT 1（見 DEVELOPMENT_STATUS） |
| 檔案 / CRUD 輸入輸出與 Protocol | 已實作契約，尚無實際業務操作 |
| 第一階段契約：Storage / FileService / DataService 工廠 | 尚未接線，明確拋出 `ModuleNotReadyError`，不假裝成功 |
| Vercel 模組（檔案存取） | `integrations/supabase_storage.py`：上傳、下載、列表、移動、刪除；`/dev/files` 為示範 |
| AI 模組 | `integrations/document_ai.py`：`DocumentAIClient.from_settings().process(content, mime_type, timeout_seconds=None)`；`integrations/gemini.py`：`GeminiClient.from_settings().chat(...)`；`integrations/groq.py`：`GroqClient.from_settings().chat(messages, purpose=..., advanced=..., json_output=..., timeout_seconds=...)`；與 API 相同檔案（2026-10-29），只負責呼叫與解析、不寫 DB，錯誤為固定代碼。ADMIN 尚未接線任何功能 |
| Agent Platform 向量模組 | `integrations/vertex_embedding.py`：`VertexEmbeddingClient.from_settings().embed_texts(texts, task="document" 或 "query", title=None, timeout_seconds=None)` 回傳 `EmbeddingBatch`（vectors、model、dimensions），順序與輸入相同；以服務帳號呼叫 `embedContent`（location 固定 global），多筆併發上限 8，autoTruncate 固定 false，一筆失敗即整批失敗、不自動重試；與 API 相同檔案（2026-10-30），只負責呼叫與解析、不寫 DB，錯誤為 `embedding_*` 固定代碼。尚未接線任何功能；用法見 [RAG 開發說明](RAG_DEVELOPMENT.md) |
| 遠端 ADMIN 用戶端 | `remote_admin_client.py` 保留，目前沒有呼叫端 |
| ADMIN 唯讀盤點 JSON 路由 | GET /system/db-inspection，沿用既有登入；本機無法登入，只能在雲端使用（尚未部署），見 [DB_READONLY_PROBE.md](DB_READONLY_PROBE.md) |
| 業務 JSON 路由、身分接續、部門授權 | 尚未實作；既有登入保護保留 |
| StoredFile 表、發票 version 欄位 | 尚未建立；由 API 統一規劃 schema / migration |

## 匯入方式

Vercel 模組（檔案存取；示範與正式功能都使用）：

```python
from app.integrations.supabase_storage import S3StorageClient

storage = S3StorageClient.from_settings()
```

設定檢查與第一階段契約（`get_data_service()`／`get_file_service()` 尚未接線）：

```python
from app.integrations.vercel import check_configuration, get_platform
from app.schemas.data import InvoiceCreate, InvoicePatch, InvoiceUpdate
from app.schemas.files import UploadFileRequest, MoveFileRequest, DeleteFileRequest
from app.services.data_service import DataService, get_data_service
from app.services.file_service import FileService, get_file_service
```

這些匯入不會連線。現在呼叫 `get_data_service()` 或 `get_file_service()` 會明確失敗，
不能將契約存在視為已能新增、刪除、修改公司資料。

## 設定邊界

所有執行期設定只從 `app.core.config.settings` 取得。預設不讀 `.env` 或 secrets 目錄。
`.env.example` 僅供管理者識別設定名稱，機密欄位保持空白，不把公司密鑰寫入本機檔案；本機只由 START 把核定的 DATABASE_URL／S3_* 注入程序記憶體。
Pydantic 的 SecretStr 遮罩 repr / JSON，不等於加密儲存；勿記錄 `get_secret_value()`。

| 名稱 | 用途與預設 |
|---|---|
| APP_ENV | local / preview / production；預設 local。Vercel 不會自動設定，必須明確設為 preview / production；與 VERCEL_ENV 不一致（含未設定）時本站拒絕啟動，所有頁面都無法使用 |
| DEBUG | 預設 false，雲端驗證要求 false |
| MODULE_BACKEND | auto / remote / cloud；auto 在 Vercel 選 cloud，其餘選 remote |
| REMOTE_ADMIN_BASE_URL | 保留的本機遠端模式欄位；`remote_admin_client.request_json()` 目前沒有呼叫端。空白時 `remote_admin_base_url` 回退到正式 ADMIN 網址（`app/core/constants.py`）；START 啟動時為空值 |
| DATABASE_URL | DB 連線必填機密，本機／雲端一致（本機由 START 注入 Production 值）；不從 POSTGRES_URL 猜測或自動複製。未設定回報 `required`，格式不符 `invalid_postgres_url`，TLS 降級 `unsafe_ssl_mode`，含驅動不支援的參數（例如 supa、pgbouncer）`unsupported_url_option` |
| DB_POOL_MODE | transaction / session / direct；預設 transaction，管理者需核對實際連線模式 |
| DB_SSL_MODE | require / verify-full；預設 require；verify-full 需管理者另確認 CA 設定 |
| DB_CONNECT_TIMEOUT_SECONDS | 1–30 秒；預設 5 秒 |
| SUPABASE_URL | 第一階段 `check_configuration("storage")` 使用的公開專案網址；S3StorageClient 不讀此欄位 |
| SUPABASE_STORAGE_BUCKET | S3StorageClient 使用的 Bucket 名稱，亦由第一階段靜態檢查驗證；真正存在與私有政策須另驗證 |
| SUPABASE_SECRET_KEY | 第一階段 storage 設定檢查要求的機密；S3StorageClient 不讀此欄位，使用 S3_ACCESS_KEY_ID／S3_SECRET_ACCESS_KEY |
| S3_ENDPOINT_URL / S3_REGION | Supabase S3 相容端點與區域；`integrations/supabase_storage.py` 使用。本機由 START 注入（2026-10-25 核定例外） |
| S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY | Supabase S3 金鑰；本機由 START 注入，不在本機機密拒絕清單。`check_configuration("storage")` 仍驗證 SUPABASE_URL／SUPABASE_STORAGE_BUCKET／SUPABASE_SECRET_KEY，不驗證 S3_*；這是第一階段靜態檢查，不是另一個 Storage 模組 |
| SESSION_SECRET_KEY | ADMIN 雲端 Session 必填機密；至少 32 字元。本機屬禁用機密，因此本機無法登入 |
| SESSION_MAX_AGE_SECONDS | 1–86400 秒；預設 28800 秒 |
| GEMINI_API_KEY / GEMINI_MODEL | AI 模組 `integrations/gemini.py` 使用；Key 本機由 START 注入（2026-10-29 核定，LOCAL_INJECTED_FIELDS），模型預設 gemini-3.8-flash |
| GROQ_API_KEY / GROQ_DEFAULT_MODEL / GROQ_ADVANCED_MODEL | AI 模組 `integrations/groq.py` 使用；Key 本機由 START 注入，模型預設 openai/gpt-oss-20b／openai/gpt-oss-120b |
| GOOGLE_CLOUD_PROJECT_ID / GOOGLE_CLOUD_LOCATION / DOCUMENT_AI_PROCESSOR_ID / GOOGLE_SERVICE_ACCOUNT_JSON | AI 模組 `integrations/document_ai.py` 使用；服務帳號 JSON 是機密，本機由 START 注入（多行壓成單行） |
| VERTEX_EMBEDDING_MODEL / VERTEX_EMBEDDING_DIMENSIONS | AI 模組 `integrations/vertex_embedding.py` 使用，不是機密；預設 gemini-embedding-2／1536（維度 128～2000）。沿用 GOOGLE_CLOUD_PROJECT_ID 與 GOOGLE_SERVICE_ACCOUNT_JSON，服務帳號需有 Agent Platform User；換模型或維度須全部重算向量 |
| OPENAI_API_KEY | 雲端選填；未使用，本機仍屬禁用機密 |
| VERCEL / VERCEL_GIT_COMMIT_SHA | 平台自動提供，不是使用者授權憑證 |

`VERCEL=1` 是部署標記，並非安全認證；不能靠此旗標保護公開端點。
表單或路徑參數格式錯誤時顯示 400 錯誤頁，不回顯輸入（不使用 FastAPI 預設 JSON 422）。
本機沒有 SESSION_SECRET_KEY 時，殘留的登入 cookie 視為未登入並導向登入頁；雲端設定不合格仍拒絕。
依 2026-10-24 核定，ADMIN 程序已取得合法 DATABASE_URL 時可在本機建立 Engine，不要求 Vercel／雲端標記；格式、TLS、逾時與去敏檢查保留。其他機密欄位的本機限制與網站授權不變。
雲端仍須保留真正的登入、角色、資料範圍與每次操作授權。
目前沒有任何回傳環境變數的 HTTP 端點。

密鑰輪替由各站雲端設定及新部署套用；本機需重新 START 才會取得新值。
新增公開設定仍須經核定，不能任意透過 JSON 欄位加入密鑰。
歷史第一階段未調整啟動器 PUBLIC_ENV；目前來源與允許欄位以 [環境隔離](ENVIRONMENT_ISOLATION.md) 及 `app/core/environment.py` 為準，不據歷史描述推定任意 JSON 欄位可用。

## 契約

資料使用具名發票資源，禁止任意 SQL、資料表及欄位名稱。
`InvoiceService` 提供 create / get / list / update / delete；`DataService.invoices` 取得該服務。
新增不接受 user_id、status、risk_score、ai_result；擁有者由未來授權服務決定。
更新採 `InvoiceUpdate(patch=..., expected_version=...)`，刪除也需要版本。
金額使用 Decimal / JSON 字串，不接受 float；資料查詢分頁每頁最多 100 筆。
ID 與版本上限為 PostgreSQL BIGINT，頁碼 1–10,000；HTTP query / path 可接受數字字串，但拒絕布林與浮點數。
稽核僅新增／查詢、排程紀錄由 API 寫入，不提供一般 CRUD。

`FileService` 提供 upload_file / get_file / list_files / create_download / move_file / delete_file。
上傳宣告最多 4 MiB，型別為 JPEG、PNG 或 PDF；未來實作必須比對串流大小與實際內容，
不能僅相信 metadata。移動只能指定邏輯資料夾；檔名不能夾帶路徑、控制或格式字元
（含雙向覆寫、零寬字元）、Windows 保留名與結尾空白 / 句點，UTF-8 不超過 255 bytes。
移動、刪除須攜帶 expected_version 與 UUID idempotency_key。
公開 FileInfo 不含 Bucket、object_key、密鑰或永久下載網址。
DownloadGrant 是短效使用者能力，URL 不得寫入日誌；有效期間由未來 service 強制限制。

OperationContext 是內部授權上下文契約，不是驗證器，也不能從 HTTP JSON 建構後直接信任。
部門模型及真實權限服務尚未完成，不得以本地環境、測試 fixture 或角色字串取代授權。
StorageClient 為內部 Protocol，必須由 service 完成權限與路徑產生後使用。
Repository 不 commit；Service 未來負責交易、稽核、補償及冪等性。


### 未來上傳傳輸的限制（尚未實作）

DTO 的檔案大小上限為 4 MiB（4,194,304 bytes），不是整個 HTTP request 的預算。
Vercel Function 請求／回應上限為 4.5 MB，multipart 邊界、欄位與 metadata 都必須計入；
未來 adapter／端點需驗證實際檔案與完整 body，不可以 Base64 編碼後仍假設 4 MiB 可以通過。
需要調整傳輸方式時另提設計。
參考 [Vercel Function 限制](https://vercel.com/docs/functions/limitations)。

## Windows 驗證（不需公司密鑰）

初次設定與日常啟動使用同層 `InvoiceGuard-STARTUP` 的 `SETUP.cmd`／`START.cmd`（手機 HTTPS 已包含在日常啟動），詳見 [README](../README.md)。
以下在 ADMIN 根目錄使用已建立的 Python 3.12 `.venv`，不必手動 activate 或每日重建。
不要執行 vercel env pull，也不要建立含公司機密的 .env。

```powershell
# 預設不讀 .env；此檢查不連線，也不回傳任何設定值。
.\.venv\Scripts\python.exe -m app.integrations.vercel --check local
$LASTEXITCODE

# 測試程序會清除已知設定的繼承值（S3_* 目前不在清除清單），使用假值，封鎖網路與真實 DB 連線。
.\.venv\Scripts\python.exe -B scripts/check_project.py
.\.venv\Scripts\python.exe -m pytest -q --basetemp=tmp/pytest
.\.venv\Scripts\python.exe -m ruff check app tests scripts
.\.venv\Scripts\python.exe -m ruff format --check app tests scripts
```

local 檢查有效時回傳 0，設定錯誤回傳 2。未設定遠端網址的警告屬預期結果。
輸出 connectivity_checked=false、operations_implemented=false，不能解讀為雲端測試通過。
若有 cloud_secret_in_local，僅會列出欄位名稱；請管理者處理本機繼承設定，不要貼出值。
`tmp/pytest` 是 pytest 專用暫存目錄，每次測試可能清理該目錄，勿放其他檔案。

Windows asyncio 需要程序內 socketpair；測試只放行 socketpair 自己的 loopback 通道，
仍阻擋一般 loopback 連線、外部連線以及 SQLAlchemy / psycopg 連線。
Windows AppContainer 沙箱可能阻擋 socketpair；此時請在一般 PowerShell 執行，勿放寬測試的外部連線限制。

日常頁面入口為 <http://invoiceguard-ai-admin.localhost:8080/login> 與 `/health`；以下只供單站診斷，先關閉統一啟動器以免埠衝突：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

可檢查 http://127.0.0.1:8002/health（status=ok）、/login 與 /static/css/main.css。
未登入開 /dashboard 應轉往 /login。這些 URL 只驗證既有頁面與程序，不能證明 DB / Storage 可用。
本機無法登入（SESSION_SECRET_KEY 不在本機）；不要以公司帳密嘗試本機登入：本機連正式 DB，登入流程會先寫入 last_login_at 再失敗。

## 待完成

依序處理真實登入與部門授權、API schema、Repository / service、第一階段契約（DataService／FileService）接線與受控 ADMIN JSON 端點。
需測版本衝突、跨使用者拒絕、檔案內容、冪等性、失敗補償及稽核，不能以 Protocol 或單元測試代替。
本機連正式 DB／Storage，寫入測試需使用者明確授權。

## 技術依據

- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [SQLAlchemy Engine](https://docs.sqlalchemy.org/en/21/core/engines.html)
- [Supabase 連線模式](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [Supabase transaction pooler 與 prepared statements](https://supabase.com/kb/troubleshooting/disabling-prepared-statements-qL8lEL)
- [Vercel 環境變數](https://vercel.com/docs/environment-variables)
