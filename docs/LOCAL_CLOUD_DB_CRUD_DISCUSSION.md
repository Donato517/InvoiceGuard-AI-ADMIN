# 歷史討論：Local 網站操作雲端 DB（新增／修改／刪除）

日期：2026-10-24　撰寫：Claude（供與 ChatGPT 討論）

> 2026-10-25 更新：使用者已決定改由 START.cmd 以 Vercel CLI（`vercel env run`）取得 ADMIN 的 DATABASE_URL，只注入本機 ADMIN 程序，`/dev/accounts` 直接使用既有 DB 模組。本文的「通用 SQL 執行器」方案未採用，保留作為紀錄；現況見 [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)。

> 給 ChatGPT：請以本文件為背景，針對「7. 待討論問題」逐題提出建議與理由；如果認為「5. 建議方案」方向有更好的做法，也請直接指出。

## 1. 目標

Local 網站（開發者電腦上 START.cmd 啟動的 localhost ADMIN，之後複製到 API／前台）能直接對**雲端 PostgreSQL** 執行查詢、新增、修改、刪除。

基礎建設的定位：先把能力開放出來，業務規則由開發者自行建置；防呆與限制之後再補，不讓「防範」阻擋功能。

## 2. 已確定的前提

- DB 密碼（DATABASE_URL）、Storage Key、模型 Token 只留在雲端（Vercel 執行環境），不下載、不注入本機 venv，也不提供回傳這些值的 API。
- 7 位開發者不註冊 Vercel，不建立本機 PostgreSQL 或本機 Bucket。
- 雲端 DB 只用一個固定的系統帳號；開發者身分用 InvoiceGuard 自己的 Account（`invoiceguard_identity.user_accounts`）識別。
- 架構沿用：Python 3.12、FastAPI、Jinja2／HTMX；分層 `routers → services → db/repositories`，service 控制交易。

## 3. 現況（已對照原始碼查證）

### 3.1 三個 Repository 與啟動方式

- `InvoiceGuard-AI`（前台）、`InvoiceGuard-AI-ADMIN`（後台）、`InvoiceGuard-AI-API`（API）。
- 開發者只執行 `InvoiceGuard-STARTUP\START.cmd`，在本機啟動三站。
- START 給子程序的環境只保留 OS 白名單變數，並固定 `APP_ENV=local`、`MODULE_BACKEND=remote`；任何 DATABASE_URL、Key、VERCEL 變數都不會傳入（`launcher/mobile_launcher.py` 的 `child_environment()`）。
- 本機要呼叫的雲端 ADMIN 網址來自 `InvoiceGuard-STARTUP\dev-urls.local.json`（只允許 3 個公開 URL 欄位），目前為 `https://invoice-guard-ai-admin.vercel.app`；未設定時 ADMIN 使用程式內同一個預設網址。
- 專案內沒有任何透過 Vercel CLI／管理 API 取得環境變數的機制。

### 3.2 ADMIN 的兩種模式

| 模式 | 執行位置 | DB |
|---|---|---|
| `MODULE_BACKEND=cloud` | Vercel（`VERCEL=1` 時自動選擇） | 用雲端環境變數的 DATABASE_URL 直接連線（SQLAlchemy、NullPool、TLS、transaction pooler） |
| `MODULE_BACKEND=remote` | 開發者電腦 | 沒有 DATABASE_URL；需要資料時經 HTTPS 呼叫雲端 ADMIN |

本機呼叫雲端的共用傳輸已存在：`app/integrations/remote_admin_client.py` 的 `request_json(method, base_url, path, query, payload)`，支援 GET／POST／PATCH JSON、不跟隨轉址、15 秒逾時、回應上限 64 KB、錯誤轉固定代碼。

### 3.3 已驗證過的路徑：SELECT 1

```
localhost ADMIN ──HTTPS GET /health/db──▶ Vercel ADMIN（Production 10719df）──▶ PostgreSQL：SELECT 1 ──▶ DB_OK
```

SQL 是在**雲端**執行的：那段程式碼已部署在 Production，用雲端自己的 DATABASE_URL。本機只發 HTTP 請求。

### 3.4 為什麼帳號的 SELECT／INSERT／UPDATE 還不能用

本機已寫好開發者帳號頁 `/dev/accounts`（查詢、新增、修改），流程是：

```
本機 /dev/accounts → dev_account_service → remote_admin_client
    ──HTTPS──▶ 雲端 /api/dev/accounts → user_service → user_account_repository → DB
```

雲端回 404（畫面顯示「雲端 ADMIN 尚未部署帳號 API」），原因：

- 雲端那一半的程式碼（`routers/dev_accounts.py`、`services/user_service.py` 的帳號函式、`db/repositories/user_account_repository.py`、`schemas/account.py`）只存在本機工作目錄，未 commit、未部署。所有本機與 origin 分支都沒有這些檔案。
- Production 目前是 10719df：第一階段設定模組 + 暫時公開的 `/health/db`。

**一般規則**：在 remote 模式下，本機能做的資料操作 = 雲端已部署程式碼能做的事。現在的寫法是「每一種操作一支雲端端點」，所以每新增一種 SQL 都要再部署一次雲端。這不是 START 有沒有 DATABASE_URL 的問題。

## 4. 核心觀念：兩種能力不同

| | 遠端 HTTP 連線能力 | 環境變數取得能力 |
|---|---|---|
| 意思 | 本機用 HTTPS 呼叫雲端已部署的端點，雲端用自己的 DATABASE_URL 執行 | 把 DATABASE_URL 的值放進本機程序，由本機直接連 DB |
| 現況 | 已有（`remote_admin_client`、`check-db`） | 沒有，也不做 |
| 新增一種 SQL 時 | 看雲端端點的設計：「專用端點」要部署；「通用執行器」不用 | 不用部署 |

要在**不下放密碼**的前提下達到「新增 SQL 不用每次部署」，答案是：雲端只部署一次「通用 SQL 執行器」，SQL 由本機帶上去執行。

## 5. 建議方案：雲端通用 SQL 執行器（一次部署）

### 5.1 架構

```
本機 repository（開發者自己寫的 SQL）
    │  executor.execute(sql, params)
    ▼
RemoteExecutor ──HTTPS POST /api/db/execute──▶ 雲端 ADMIN：DirectExecutor
                                                  │ 既有 get_engine()／Session（雲端 DATABASE_URL）
                                                  ▼
                                               PostgreSQL（同一個系統帳號）
```

- **雲端**（只部署一次）：`POST /api/db/execute`，收 SQL 與綁定參數，在一個交易內執行，全部成功才 commit，任何一句失敗就 rollback，回傳每一句的結果。
- **本機**：`RemoteExecutor` 把同樣的 SQL 與參數送上去。
- **同一份 repository 程式碼兩邊都能用**：repository 只依賴 `SqlExecutor` 介面；雲端模式注入 `DirectExecutor`（直接用 Session），本機 remote 模式注入 `RemoteExecutor`。開發者在本機寫、在本機對真雲端 DB 測，部署後同一份程式在雲端直接連 DB 執行。
- 現有 `user_account_repository` 已經是 `text()` + 綁定參數的寫法，可以直接改走 executor，`/dev/accounts` 就不再需要專用雲端端點。

### 5.2 端點契約（草案）

請求：

```json
POST /api/db/execute
{
  "statements": [
    {"sql": "INSERT INTO invoiceguard_identity.user_accounts (email, password_hash, account_status, primary_department_id) VALUES (:email, :password_hash, 'active', :department_id) RETURNING user_id", "params": {"email": "a@example.com", "password_hash": "...", "department_id": 1}},
    {"sql": "INSERT INTO public.user_profiles (user_id, display_name) VALUES (:user_id, :display_name)", "params": {"user_id": "…", "display_name": "a"}}
  ]
}
```

成功回應：

```json
{
  "results": [
    {"columns": ["user_id"], "rows": [["3f2c…"]], "rowcount": 1},
    {"columns": [], "rows": [], "rowcount": 1}
  ]
}
```

失敗回應（整批 rollback）：

```json
{"error": {"code": "sql_error", "statement_index": 0, "sqlstate": "23505", "constraint": "user_accounts_email_key", "message": "…"}}
```

本機的 `RemoteExecutor` 把 `sqlstate` 轉回與 SQLAlchemy 相同的例外類別（例如 23505 → `IntegrityError`），讓 repository／service 的錯誤處理在兩種模式下一樣。

### 5.3 第一版範圍

- 開放 SELECT／INSERT／UPDATE／DELETE，可以存取任何資料表，SQL 由開發者自訂。
- 一次請求 = 一個交易（可放多句）。
- 部署在既有的雲端 ADMIN；確認可用後依原步驟複製到 API。
- 這次部署從乾淨分支進行，順便移除 Production 上暫時公開的 `/health/db`。

### 5.4 可直接沿用的既有程式

| 位置 | 用途 |
|---|---|
| ADMIN `app/db/session.py` | 雲端 Engine／Session（NullPool、TLS、pooler 設定），DirectExecutor 直接使用 |
| ADMIN `app/integrations/remote_admin_client.py` | RemoteExecutor 的 HTTPS 傳輸（回應上限 64 KB 需調大） |
| ADMIN `app/core/config.py` | `module_backend`、`remote_admin_base_url`，決定注入哪個 executor |
| ADMIN `app/core/deps.py` | `require_cloud_db`：執行器端點只在雲端模式提供 |
| ADMIN `app/db/repositories/user_account_repository.py` | 第一個改用 executor 的範例 |
| ADMIN `app/core/security.py` | Argon2 密碼雜湊／驗證、itsdangerous 簽章（之後做身分 token 時使用） |
| STARTUP `START.cmd`、`dev-urls.local.json` | 不需修改 |

### 5.5 哪些在 localhost 驗證、哪些要動雲端

- **只動本機**：`RemoteExecutor`、`SqlExecutor` 介面、repository 改寫、頁面。
- **localhost 可做的離線測試**：用 FastAPI TestClient 在程序內跑雲端端點，DB 用替身，驗證「本機 executor → 端點 → 回應 → 例外對應」的整條契約。
- **需要部署雲端一次**：`/api/db/execute` 與 `DirectExecutor`。部署後，真實 SQL 都從本機直接驗證，不需要再部署。

## 6. 其他做法比較

| 做法 | 新增 SQL 要部署嗎 | 說明 |
|---|---|---|
| A. 通用 SQL 執行器（建議） | 不用 | 一次部署；開發者自訂 SQL；repository 兩種模式共用 |
| B. 通用表格 CRUD 端點（`/api/db/tables/{table}`，結構化 insert／update／delete） | 不用 | 不傳 SQL 文字，但 JOIN、彙總等複雜查詢做不到；可以之後疊在 A 上面當便利層 |
| C. 每種操作一支專用端點（現行 `/api/dev/accounts`） | 要 | 每加一種操作都要部署雲端，不符合基礎建設目標 |
| D. 下放 DATABASE_URL 到本機 | 不用 | 違反前提，不採用 |

## 7. 待討論問題

1. **交易模型**：Vercel 是無狀態的，每次 HTTP 請求可能落在不同執行個體，無法跨請求保持一個 DB 交易。「一次請求 = 一個交易（多句批次）」夠不夠用？需要「先查、再依結果寫入」的流程，要用 CTE／`RETURNING` 寫成一句，還是在批次裡支援引用前一句的結果（例如 `:$0.user_id`）？
2. **本機程式介面**：要做一個模仿 SQLAlchemy `Session.execute()` 的 RemoteSession，讓現有 repository 幾乎不改？還是定義較小的 `SqlExecutor` 介面（`execute`／`batch`），讓 repository 改呼叫它？前者的 `commit()` 時機和 `RETURNING` 取值要怎麼處理？
3. **型別傳遞**：JSON 沒有 UUID、Decimal、datetime、date、bytes、JSONB、陣列等型別。參數與結果要怎麼無損往返：附型別標記、回傳欄位型別清單，還是約定在 SQL 裡用 `CAST(:x AS uuid)`？
4. **錯誤回傳**：回傳 `sqlstate`、`constraint`、原始 message 是否足夠開發除錯？本機是否應轉成 SQLAlchemy 的 `IntegrityError`／`ProgrammingError`，讓兩種模式的 except 寫法一致？
5. **DDL 範圍**：第一版只開放 DML（SELECT／INSERT／UPDATE／DELETE），Schema 仍由 API 的 Alembic 管理；還是也開放 CREATE／ALTER？
6. **第一版的存取門檻**：端點在公開網址上，直接操作共用 DB。第一版要完全開放、之後再補；還是第一版就直接沿用 InvoiceGuard Account（登入換 token，請求帶 `Authorization`）？後者同時解決第 7 題。
7. **操作者識別**：DB 層只有一個系統帳號，應用層要怎麼記錄「誰執行了哪句 SQL」：只寫 Vercel 日誌？寫入 `audit_logs`？還是透過 `SET LOCAL` 把 actor UUID 放進交易，讓 DB trigger 寫 `updated_by`？
8. **Vercel 限制**：函式最長執行時間、請求／回應大小上限、冷啟動延遲，以及 NullPool 每次請求新建連線對 Supabase pooler 的影響。另外，`remote_admin_client` 目前的 64 KB 回應上限要調到多少？是否需要分頁或串流大結果？（實際數值依目前 Vercel 方案確認。）
9. **部署目標**：執行器部署到 Production 還是 Preview？Preview 若開啟 Vercel Deployment Protection，沒有 Vercel 帳號的開發者就連不到；兩者是否連同一個 DB？
10. **既有程式的處置**：
    - `/api/dev/accounts` 專用端點，要改成 executor 寫法，還是保留作為範例？
    - ADMIN `config.py` 與 AGENTS.md 中「本機可使用 DATABASE_URL」的設定，與第 2 節前提相反，是否一併還原？

## 8. 參考檔案

- STARTUP：`launcher/mobile_launcher.py`（`child_environment`）、`launcher/unified_launcher.py`（REMOTE_ADMIN_BASE_URL、`check-db`）、`launcher/admin_readonly_probe.py`、`config/README.md`
- ADMIN：`app/core/config.py`、`app/core/deps.py`、`app/core/environment.py`、`app/db/session.py`、`app/integrations/remote_admin_client.py`、`app/routers/dev_accounts.py`、`app/services/dev_account_service.py`、`app/services/user_service.py`、`app/db/repositories/user_account_repository.py`、`app/services/data_service.py`、`docs/DEVELOPMENT_STATUS.md`、`docs/REVIEW_FIX_LOG.md`
