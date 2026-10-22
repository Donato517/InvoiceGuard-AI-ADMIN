# ADMIN Vercel / DB 模組：第一階段

本階段完成設定驗證、Python 型別介面、輸入／輸出驗證及離線單元測試。
未部署、未執行 migration、未建立新表，也未操作真實資料或檔案。
API、採購前台及啟動器沒有變更。

## 可用與未完成的能力

| 能力 | 狀態 |
|---|---|
| `get_platform()` 安全執行描述 | 已實作，不含密鑰 |
| `check_configuration()` 與 CLI | 已實作；只做靜態設定檢查 |
| DB Engine 設定與 Session 關閉 | 已實作保護與參數，真實連線未驗證 |
| 檔案 / CRUD 輸入輸出與 Protocol | 已實作契約，尚無實際業務操作 |
| Storage / FileService / DataService 工廠 | 明確拋出 `ModuleNotReadyError`，不假裝成功 |
| Supabase Storage、遠端 ADMIN 用戶端 | 尚未實作 |
| 新 JSON 路由、身分接續、部門授權 | 尚未實作；既有登入保護保留 |
| StoredFile 表、發票 version 欄位 | 尚未建立；由 API 統一規劃 schema / migration |

## 匯入方式

```python
from app.integrations.vercel import check_configuration, get_platform
from app.schemas.data import InvoiceCreate, InvoicePatch, InvoiceUpdate
from app.schemas.files import UploadFileRequest, MoveFileRequest, DeleteFileRequest
from app.services.data_service import DataService, get_data_service
from app.services.file_service import FileService, get_file_service
```

這些匯入不會連線。現在呼叫 `get_data_service()` 或 `get_file_service()` 會明確失敗，
不能將本階段完成視為已能新增、刪除、修改公司資料。

## 設定邊界

所有執行期設定只從 `app.core.config.settings` 取得。預設不讀 `.env` 或 secrets 目錄。
`.env.example` 僅供管理者識別設定名稱，機密欄位保持空白，不複製公司密鑰到本機。
Pydantic 的 SecretStr 遮罩 repr / JSON，不等於加密儲存；勿記錄 `get_secret_value()`。

| 名稱 | 用途與預設 |
|---|---|
| APP_ENV | local / preview / production；預設 local。Vercel 不會自動設定，必須明確設為 preview / production，否則登入與受保護頁面一律拒絕（500，日誌記錄欄位與代碼） |
| DEBUG | 預設 false，雲端驗證要求 false |
| MODULE_BACKEND | auto / remote / cloud；auto 在 Vercel 選 cloud，其餘選 remote |
| REMOTE_ADMIN_BASE_URL | 公開 HTTPS 網址；空白時本機可做離線測試，不能遠端操作 |
| DATABASE_URL | 雲端 DB 必填機密；不從 POSTGRES_URL 猜測或自動複製 |
| DB_POOL_MODE | transaction / session / direct；預設 transaction，管理者需核對實際連線模式 |
| DB_SSL_MODE | require / verify-full；預設 require；verify-full 需管理者另確認 CA 設定 |
| DB_CONNECT_TIMEOUT_SECONDS | 1–30 秒；預設 5 秒 |
| SUPABASE_URL | 雲端 Storage 必填公開專案網址 |
| SUPABASE_STORAGE_BUCKET | 雲端 Storage 必填 Bucket 名稱，真正存在與私有設定尚未驗證 |
| SUPABASE_SECRET_KEY | 雲端 Storage 必填機密，是否有效與權限尚未驗證 |
| SESSION_SECRET_KEY | ADMIN 雲端 Session 必填機密；至少 32 字元 |
| SESSION_MAX_AGE_SECONDS | 1–86400 秒；預設 28800 秒 |
| GEMINI_API_KEY / OPENAI_API_KEY | 雲端選填；本階段不使用、不要求取得模型金鑰 |
| VERCEL / VERCEL_GIT_COMMIT_SHA | 平台自動提供，不是使用者授權憑證 |

`VERCEL=1` 是部署標記，並非安全認證；不能靠此旗標保護公開端點。
表單或路徑參數格式錯誤時顯示 400 錯誤頁，不回顯輸入（不使用 FastAPI 預設 JSON 422）。
本機沒有 SESSION_SECRET_KEY 時，殘留的登入 cookie 視為未登入並導向登入頁；雲端設定不合格仍拒絕。
本機檢查會拒絕已知機密欄位；DB 模組在本機模式會阻擋 Engine 建立。
雲端仍須保留真正的登入、角色、資料範圍與每次操作授權。
目前沒有任何回傳環境變數的 HTTP 端點。

未來密鑰輪替由各站雲端設定及新部署套用，不改七位開發者的啟動檔。
新增公開設定仍須經核定，不能任意透過 JSON 欄位加入密鑰。
本階段新增的 REMOTE_ADMIN_BASE_URL 尚未接到啟動器；其現有 PUBLIC_ENV 白名單沒有改動。

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

## Windows 驗證（不需公司密鑰）

在 ADMIN 根目錄使用 Python 3.12。首次準備環境：

```powershell
Set-Location 'C:\InvoiceGuard_project\InvoiceGuard-AI-ADMIN'
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

若電腦沒有註冊 `py -3.12`，以已安裝 Python 3.12 的完整執行檔路徑取代。
不要執行 vercel env pull，也不要下載或建立含公司機密的 .env。

```powershell
# 預設不讀 .env；此檢查不連線，也不回傳任何設定值。
.\.venv\Scripts\python.exe -m app.integrations.vercel --check local
$LASTEXITCODE

# 測試程序會清除已知設定的繼承值，使用假值，封鎖網路與真實 DB 連線。
.\.venv\Scripts\python.exe -m pytest -q --basetemp=tmp/pytest
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m ruff format --check app tests
```

local 檢查有效時回傳 0，設定錯誤回傳 2。未設定遠端網址的警告屬預期結果。
輸出 connectivity_checked=false、operations_implemented=false，不能解讀為雲端測試通過。
若有 cloud_secret_in_local，僅會列出欄位名稱；請管理者處理本機繼承設定，不要貼出值。
`tmp/pytest` 是 pytest 專用暫存目錄，每次測試可能清理該目錄，勿放其他檔案。

Windows asyncio 需要程序內 socketpair；測試只放行 socketpair 自己的 loopback 通道，
仍阻擋一般 loopback 連線、外部連線以及 SQLAlchemy / psycopg 連線。
Windows AppContainer 沙箱可能阻擋 socketpair；此時請在一般 PowerShell 執行，勿放寬測試的外部連線限制。

現有頁面驗證另開 PowerShell：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

可檢查 http://127.0.0.1:8002/health（status=ok）、/login 與 /static/css/main.css。
未登入開 /dashboard 應轉往 /login。這些 URL 只驗證既有頁面與程序，不能證明 DB / Storage 可用。
不要在尚未接線遠端登入時，以雲端公司帳密嘗試本機登入。

## 後續驗收

真實 DB / Storage 測試只能在管理者控制、具有測試環境機密的雲端執行器進行。
依序處理真實登入與部門授權、API schema、Repository / service、Storage adapter、
受控 ADMIN JSON 端點、localhost 遠端傳輸。需測版本衝突、跨使用者拒絕、檔案內容、
冪等性、失敗補償及稽核，不能以本階段 Protocol 或單元測試代替。

## 技術依據

- [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)
- [SQLAlchemy Engine](https://docs.sqlalchemy.org/en/21/core/engines.html)
- [Supabase 連線模式](https://supabase.com/docs/guides/database/connecting-to-postgres)
- [Supabase transaction pooler 與 prepared statements](https://supabase.com/kb/troubleshooting/disabling-prepared-statements-qL8lEL)
- [Vercel 環境變數](https://vercel.com/docs/environment-variables)
