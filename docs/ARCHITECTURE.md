# 系統架構說明 — InvoiceGuard-AI-ADMIN（後台網站）

AI 先讀根目錄 [AGENTS.md](../AGENTS.md)（Claude 見 [CLAUDE.md](../CLAUDE.md)），
再依 [開發狀態](DEVELOPMENT_STATUS.md) 區分已完成能力與目標設計。
本文件描述分工，不代表外部服務或每個流程均已接線。日常三站啟動與全名別名見 [README](../README.md)，
無密鑰分層與契約檢查見 [PROJECT_CHECKS.md](PROJECT_CHECKS.md)。

## 1. 系統全貌

InvoiceGuard AI 由三個獨立的 Git repository 組成，**全部以 Python 撰寫**，各自部署為一個 Vercel Python Function：

```
      User                              Admin
       |                                  |
       v                                  v
┌──────────────────────┐        ┌──────────────────────────┐
│ InvoiceGuard-AI      │        │ InvoiceGuard-AI-ADMIN    │
│ FastAPI + Jinja2     │        │ FastAPI + Jinja2         │
│ Vercel Function (Py) │        │ Vercel Function (Py)     │
└──────────┬───────────┘        └─────────────┬────────────┘
           │ HTTPS + JSON (server side)       │
           ▼                                  │
┌──────────────────────┐                      │ SQL
│ InvoiceGuard-AI-API  │◀── Vercel Cron       │ (own DB module)
│ FastAPI              │                      │
│ Vercel Function (Py) │──▶ AI Model          │
└──────────┬───────────┘──▶ MOF e-Invoice API │
           │ SQL                              │
           ▼                                  │
┌──────────────────────┐                      │
│ PostgreSQL           │◀─────────────────────┘
└──────────────────────┘
```

| Repository | 角色 | 技術 | 資料存取 |
|---|---|---|---|
| InvoiceGuard-AI | 前台網站：使用者上傳發票、查看 AI 分析結果 | FastAPI + Jinja2 | 由伺服器端呼叫 API |
| **InvoiceGuard-AI-ADMIN**（本專案） | 後台網站：發票審核、使用者管理、排程與稽核紀錄 | FastAPI + Jinja2 | **自有 DB 模組，直接連資料庫** |
| InvoiceGuard-AI-API | 後端 API：AI 分析、第三方串接、排程；資料表結構管理者 | FastAPI | 自有 DB 模組 + Alembic |

**資料庫共用規則**：本專案可讀寫資料，但**不能變更資料表結構**；結構由 API 專案的 Alembic migration 管理，
本專案的 Model 需同步更新，流程見 [`app/db/README.md`](../app/db/README.md)。
排程（Vercel Cron）在 API 專案執行，本專案只讀取 `job_runs` 顯示執行紀錄。

---

## 2. 分層架構

```
routers ──▶ services ──▶ db/repositories ──▶ db/models
   │
   └──▶ templates（Jinja2）──▶ static（css / js / img）

core、utils：任何層都可使用
```

| 層 | 職責 | 不可以做的事 |
|---|---|---|
| `routers` | 接收請求、權限檢查（相依注入）、呼叫 service、選擇樣板 | 寫商業邏輯、直接查資料庫 |
| `services` | 商業邏輯、**控制交易**、寫入稽核紀錄 | 處理 HTTP 細節 |
| `db/repositories` | ORM 查詢與寫入 | 商業判斷、`commit` |
| `db/models` | 資料表定義與 **Schema 備註**（自 API 同步） | 自行變更結構 |
| `templates` | 呈現資料 | 商業邏輯、查資料庫 |
| `core` | 設定、樣板環境、安全、例外、相依注入 | 相依 services / routers |
| `utils` | 無狀態工具函式 | 相依 db / services |

**相依方向只能由上往下**，禁止反向 import。

---

## 3. 目錄結構

```
InvoiceGuard-AI-ADMIN/
├── app/
│   ├── main.py                   # ★ 進入點：建立 app、掛載 /static、註冊例外處理與頁面路由（Vercel 自動偵測）
│   ├── constants.py              # 全站常數：網站名稱、cookie 名稱、側欄選單、狀態顯示文字
│   ├── core/                     # 全域核心
│   │   ├── config.py             #   設定公開入口（環境隔離由 core/environment.py 處理）
│   │   ├── constants.py          #   三站共用：網址（Vercel 用正式、本機用開發）、網站名稱、圖示、搜尋引擎 noindex
│   │   ├── environment.py        #   三站相同：環境隔離、本機機密拒絕（check_project 快照比對）
│   │   ├── module_errors.py      #   模組未就緒／設定錯誤例外（ModuleNotReadyError 等）
│   │   ├── templating.py         #   Jinja2 樣板環境、filter、static_url()
│   │   ├── security.py           #   登入 cookie 簽署、密碼雜湊、防 open redirect、同源檢查（防 CSRF）
│   │   ├── deps.py               #   相依注入：DbSession、require_admin、CurrentAdmin
│   │   ├── exceptions.py         #   未登入導向、403 / 404 / 錯誤頁
│   │   └── logging_config.py     #   日誌格式
│   ├── routers/                  # 頁面路由，一個功能一個檔案
│   │   ├── health.py             #   GET /health
│   │   ├── robots.py             #   GET /robots.txt（內容取自 core/constants.py；不收錄靠 noindex）
│   │   ├── auth.py               #   GET/POST /login、POST /logout
│   │   ├── dashboard.py          #   GET /dashboard
│   │   ├── invoices.py           #   /invoices（發票審核）
│   │   ├── users.py              #   /users（使用者管理）
│   │   ├── system.py             #   /system/job-runs、/system/audit-logs
│   │   ├── db_inspection.py      #   GET /system/db-inspection（唯讀 DB 盤點，需登入）
│   │   ├── dev_accounts.py       #   /dev/accounts 示範 DB 模組（帳號查詢／新增／修改；僅本機）
│   │   └── dev_files.py          #   /dev/files 示範 Vercel 模組（圖片／RAG 檔案；僅本機）
│   ├── services/                 # 商業邏輯層（auth / invoice_review / user / system / db_inspection；dev_account / dev_file 供 /dev 示範；data / file 為第一階段契約，未接線）
│   ├── schemas/                  # Pydantic 資料契約（account / data / files / db_inspection / common）
│   ├── integrations/             # supabase_storage（Vercel 模組：檔案存取，與 API 相同）、storage_client（第一階段契約，未接線）、remote_admin_client（無呼叫端）、vercel（執行環境資訊與設定檢查）
│   ├── db/                       # ★ 資料庫模組（說明見 app/db/README.md）
│   │   ├── README.md             #   Schema 總覽、與 API 的同步流程、存取規則
│   │   ├── base.py               #   Base、約束命名規則、共用欄位 Mixin
│   │   ├── session.py            #   Engine / Session / get_db
│   │   ├── contracts.py          #   Repository 介面契約（Protocol）
│   │   ├── models/               #   ORM Model（自 API 同步），檔案開頭備註完整 Schema
│   │   └── repositories/         #   資料存取層
│   ├── templates/                # ★ Jinja2 樣板（HTML）
│   │   ├── base.html             #   HTML 骨架（<head>、全站 css / js、noindex）
│   │   ├── layouts/              #   版型：admin（側欄 + 頁首）、blank（登入 / 錯誤頁）
│   │   ├── partials/             #   共用片段：sidebar
│   │   └── pages/                #   頁面樣板，依功能分資料夾（與 routers/ 對應）
│   │       ├── auth/
│   │       ├── dashboard/
│   │       ├── invoices/
│   │       ├── users/
│   │       ├── system/
│   │       ├── dev_accounts/
│   │       ├── dev_files/
│   │       └── errors/
│   ├── static/                   # ★ 靜態檔（網址 /static/...，部署後由 Vercel CDN 提供）
│   │   ├── css/                  #   variables.css（含後台版面變數）、reset.css、components.css、main.css
│   │   ├── js/                   #   main.js（全站共用）
│   │   │   └── vendor/           #   第三方函式庫（htmx），原樣引入不修改
│   │   ├── img/
│   │   ├── icons/
│   │   └── fonts/
│   └── utils/                    # 無狀態工具：format.py（金額 / 台灣時間）、file_utils.py（暫存檔）
├── tests/
│   ├── conftest.py               # client / admin_client fixture（以 dependency_overrides 略過登入與資料庫）
│   ├── unit/                     # 單元測試（不連資料庫）
│   └── integration/              # 整合測試（需測試資料庫）
├── docs/
├── scripts/check_project.py      # 無密鑰架構／契約檢查（三站相同）
├── contracts/project-contract.json # 跨站非機密契約快照（check_project 使用）
├── tmp/                          # 本機暫存（不進版控；Vercel 上改用 /tmp）
├── .github/pull_request_template.md
├── .vscode/extensions.json
├── vercel.json                   # Function 設定、安全標頭、noindex、靜態檔快取
├── requirements.txt / requirements-dev.txt
├── ruff.toml / .djlintrc / pytest.ini
├── .python-version / .env.example
├── .editorconfig / .gitattributes / .gitignore
└── README.md
```

---

## 4. 檔案放置規則（程式碼 / js / css / 圖片 / 暫存檔該放哪？）

| 要放的東西 | 位置 | 說明 |
|---|---|---|
| 頁面路由（Python） | `app/routers/<功能>.py` | 只做：權限 → 呼叫 service → 回傳樣板 |
| 商業邏輯 | `app/services/<功能>_service.py` | 控制交易、寫入稽核紀錄 |
| 資料庫查詢 | `app/db/repositories/<資料表>_repository.py` | routers / services 不直接寫 SQL |
| 資料表定義 | `app/db/models/` | 只能從 API 專案同步 |
| 資料契約（Pydantic） | `app/schemas/<主題>.py` | routers 與 services 之間的輸入／輸出 |
| 外部服務（Storage 等） | `app/integrations/` | routers 不直接呼叫 |
| 頁面 HTML | `app/templates/pages/<功能>/<頁面>.html` | 資料夾名稱與 routers 檔名一致 |
| 共用 HTML 片段 / 版型 | `app/templates/partials/`、`layouts/` | |
| 常數、選單、狀態文字 | `app/constants.py` | 新增頁面時同步更新 `MENU_ITEMS` |
| CSS | `app/static/css/` | 設計變數在 `variables.css`，共用元件在 `components.css` |
| JavaScript | `app/static/js/main.js` | 只放全站共用的小型互動 |
| 第三方 JS / CSS | `app/static/js/vendor/` | 原樣引入，版本記錄於該資料夾 README |
| 圖片 / 圖示 / 字型 | `app/static/img/`、`icons/`、`fonts/` | |
| 處理中的暫存檔（例如匯出報表） | 本機 `tmp/`、Vercel `/tmp/invoiceguard` | 一律用 `app.utils.file_utils.temp_file_path()`，用完自動刪除 |

---

## 5. 登入與安全

2026-10-27：06_E 的正式 `/login`／`/logout` 已改委派共用認證，新增部門／功能／本人頁使用 UUID、有效職務及 scope guard。正式 adapters 尚未提供時拒絕操作。詳見 [E 交接](E-TEAM-HANDOFF.md)。**以下表格描述保留給其他組待移轉的舊登入守衛及共通防護；其中 users／已簽署 int Cookie 並非新 E 的登入路徑。**

| 項目 | 做法 | 位置 |
|---|---|---|
| 管理者帳號 | `users` 表中 `role = admin` 且 `is_active = true` | `services/auth_service.py` |
| 密碼比對 | pwdlib（Argon2），與 API 專案相同方式 | `core/security.py` |
| 登入狀態 | cookie 只存**已簽署的管理者 ID**（itsdangerous + `SESSION_SECRET_KEY`），遭竄改或過期即失效 | `core/security.py` |
| 權限檢查 | router 加 `dependencies=[Depends(require_admin)]`；每次請求重新確認帳號仍啟用、仍是管理者 | `core/deps.py` |
| 未登入 / 非管理者 | 導向 `/login?next=原頁面` / 顯示 403 | `core/exceptions.py` |
| CSRF | cookie 設 `SameSite=Lax`；POST 路由加上 `Depends(verify_same_origin)` | `core/security.py` |
| Open redirect | 登入後導向的 `next` 只允許站內路徑 | `core/security.py` |
| XSS | Jinja2 自動跳脫；**禁止**對資料庫資料使用 `\|safe` | `core/templating.py` |
| 稽核 | 審核發票、停用帳號等操作寫入 `audit_logs` | `services/` |
| 搜尋引擎 | `<meta name="robots">` 與 `X-Robots-Tag` 標頭 noindex；`/robots.txt` 不封鎖抓取（否則讀不到 noindex），值集中在 `core/constants.py` | `core/constants.py`、`routers/robots.py`、`base.html`、`vercel.json` |
| 本機與示範檔案 | 本機無法登入（`SESSION_SECRET_KEY` 屬本機禁用機密）；`/dev` 示範檔案只在本機開放、不需登入，直接讀寫正式 DB／Storage | `core/deps.py`、`routers/dev_*.py` |
| 存取限制（建議） | 正式環境可另開 Vercel Deployment Protection 或限制 IP | Vercel 設定 |

---

## 6. 環境變數

完整範本見 [`.env.example`](../.env.example)，讀取統一透過 `app/core/config.py`。

本機 JSON、Vercel Preview／Production 的實際來源與啟動驗證以 [環境與 URL 隔離](ENVIRONMENT_ISOLATION.md) 為準；dotenv 與 secrets 目錄永遠不載入。

預設不讀取 `.env`。本機只由 START 注入核定的 `DATABASE_URL`、`S3_*`、`SUPABASE_STORAGE_BUCKET`（Production 值，連正式 DB／Storage）；其他機密（例如 `SESSION_SECRET_KEY`）出現在本機即拒絕啟動，所以本機無法登入。
完整新增設定、檢查能力及未完成功能見 [MODULE_CONTRACT.md](MODULE_CONTRACT.md)。

| 變數 | 說明 | 必填 |
|---|---|---|
| `APP_ENV` | `local` / `preview` / `production`；須與 `VERCEL_ENV` 一致，否則拒絕啟動。Vercel 上或非 local 時 cookie 只走 HTTPS。Vercel 不會自動設定，必須明確設為 preview / production | ✓ |
| `DEBUG` | 日誌輸出 DEBUG 等級 | |
| `DATABASE_URL` | PostgreSQL pooled 連線字串（與 API 相同資料庫）；本機由 START 注入 | ✓ |
| `SESSION_SECRET_KEY` | 簽署登入 cookie 的金鑰，至少 32 字元；變更後所有人需重新登入；本機屬禁用機密 | 雲端 ✓ |
| `SESSION_MAX_AGE_SECONDS` | 登入有效秒數（預設 8 小時） | |
| `MODULE_BACKEND` | auto：Vercel 使用 cloud，其餘 remote；本機 remote 會開放 `/dev` 示範檔案；不取代授權 | |
| `REMOTE_ADMIN_BASE_URL` | 保留欄位；`remote_admin_client` 沒有呼叫端，空白時回退到正式 ADMIN 網址 | |
| `DB_POOL_MODE` | transaction / session / direct；預設 transaction | |
| `DB_SSL_MODE` / `DB_CONNECT_TIMEOUT_SECONDS` | TLS 模式與連線逾時；預設 require / 5 秒 | |
| `SUPABASE_URL` | 第一階段 storage 設定檢查使用；S3StorageClient 不讀此欄位 | 第一階段靜態檢查要求 |
| `SUPABASE_STORAGE_BUCKET` | S3StorageClient 使用的 Bucket，亦由第一階段靜態檢查驗證 | Storage 用戶端必填 |
| `SUPABASE_SECRET_KEY` | 第一階段 storage 設定檢查使用；S3StorageClient 改讀 S3 金鑰，不讀此欄位 | 第一階段靜態檢查要求 |
| `S3_ENDPOINT_URL` / `S3_REGION` / `S3_ACCESS_KEY_ID` / `S3_SECRET_ACCESS_KEY` | Supabase S3 相容端點與金鑰（`integrations/supabase_storage.py`）；本機由 START 注入 | Storage 用戶端必填 |
| `GEMINI_API_KEY` / `OPENAI_API_KEY` | 雲端選填；模型服務尚未串接 | |

新增設定需先分類並核定；公開設定同步 `app/core/config.py`、`app/core/environment.py` 的允許集合／驗證、空值範本及文件，不能任意新增 JSON 欄位。公司機密只由管理者存於雲端；只新增或輪替雲端機密不改本機啟動器。

---

## 7. 部署（Vercel）

| 項目 | 設定 |
|---|---|
| Framework Preset | FastAPI（由 `requirements.txt` 與 `app/main.py` 自動偵測） |
| Python 版本 | `.python-version`（3.12） |
| Function 設定 | `vercel.json` → `functions["app/main.py"]`：`maxDuration`、`excludeFiles` |
| 靜態檔 | `app.mount("/static", ...)` 會在建置時提升到 CDN；**不要加全域 middleware**，否則靜態檔改由 Function 回應 |
| Production | `main` 分支 |
| Preview | 其他分支 / PR；`DATABASE_URL` 指向測試資料庫（或核定的隔離分支），**不可指向正式資料庫** |
| 部署順序 | 資料表有異動時：API migration → API → 後台 |

---

## 8. 新增頁面檢查清單（以「使用者管理」為例）

1. `app/db/repositories/user_repository.py` — 新增查詢函式
2. `app/services/user_service.py` — 商業邏輯；會變更資料的操作寫入 `audit_logs`
3. `app/routers/users.py` — 路由；router 已有 `require_admin`，POST 再加 `Depends(verify_same_origin)`
4. `app/templates/pages/users/list.html` — 繼承 `layouts/admin.html`，檔頭寫 `@template` 備註；寫法參考 `pages/system/job_runs.html`
5. 需出現在側欄時，更新 `app/constants.py` 的 `MENU_ITEMS`
6. `tests/unit/` 新增測試（`admin_client` fixture + monkeypatch service）
7. 依 AGENTS.md「無公司密鑰驗證」執行檢查，另跑 `djlint app/templates --lint`
