# 系統架構說明 — InvoiceGuard-AI-ADMIN（後台網站）

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
│ PostgreSQL (Neon)    │◀─────────────────────┘
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
│   │   ├── config.py             #   環境變數設定（唯一讀取環境變數的地方）
│   │   ├── templating.py         #   Jinja2 樣板環境、filter、static_url()
│   │   ├── security.py           #   登入 cookie 簽署、密碼雜湊、防 open redirect、同源檢查（防 CSRF）
│   │   ├── deps.py               #   相依注入：DbSession、require_admin、CurrentAdmin
│   │   ├── exceptions.py         #   未登入導向、403 / 404 / 錯誤頁
│   │   └── logging_config.py     #   日誌格式
│   ├── routers/                  # 頁面路由，一個功能一個檔案
│   │   ├── health.py             #   GET /health
│   │   ├── robots.py             #   GET /robots.txt（Disallow: /）
│   │   ├── auth.py               #   GET/POST /login、POST /logout
│   │   ├── dashboard.py          #   GET /dashboard
│   │   ├── invoices.py           #   /invoices（發票審核）
│   │   ├── users.py              #   /users（使用者管理）
│   │   └── system.py             #   /system/job-runs、/system/audit-logs
│   ├── services/                 # 商業邏輯層（auth / invoice_review / user / system）
│   ├── db/                       # ★ 資料庫模組（說明見 app/db/README.md）
│   │   ├── README.md             #   Schema 總覽、與 API 的同步流程、存取規則
│   │   ├── base.py               #   Base、約束命名規則、共用欄位 Mixin
│   │   ├── session.py            #   Engine / Session / get_db
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
| 搜尋引擎 | `/robots.txt`（Disallow）、`<meta name="robots">`、`X-Robots-Tag` 標頭三重 noindex | `routers/robots.py`、`base.html`、`vercel.json` |
| 存取限制（建議） | 正式環境可另開 Vercel Deployment Protection 或限制 IP | Vercel 設定 |

---

## 6. 環境變數

完整範本見 [`.env.example`](../.env.example)，讀取統一透過 `app/core/config.py`。

| 變數 | 說明 | 必填 |
|---|---|---|
| `APP_ENV` | `local` / `preview` / `production`；非 local 時 cookie 只走 HTTPS | ✓ |
| `DEBUG` | 日誌輸出 DEBUG 等級 | |
| `DATABASE_URL` | PostgreSQL pooled 連線字串（與 API 相同資料庫） | ✓ |
| `SESSION_SECRET_KEY` | 簽署登入 cookie 的金鑰，至少 32 字元；變更後所有人需重新登入 | ✓ |
| `SESSION_MAX_AGE_SECONDS` | 登入有效秒數（預設 8 小時） | |

新增變數時：`.env.example`、`app/core/config.py`、本表、Vercel Project Settings 四處同步。

---

## 7. 部署（Vercel）

| 項目 | 設定 |
|---|---|
| Framework Preset | FastAPI（由 `requirements.txt` 與 `app/main.py` 自動偵測） |
| Python 版本 | `.python-version`（3.12） |
| Function 設定 | `vercel.json` → `functions["app/main.py"]`：`maxDuration`、`excludeFiles` |
| 靜態檔 | `app.mount("/static", ...)` 會在建置時提升到 CDN；**不要加全域 middleware**，否則靜態檔改由 Function 回應 |
| Production | `main` 分支 |
| Preview | 其他分支 / PR；`DATABASE_URL` 指向測試資料庫（或 Neon branch），**不可指向正式資料庫** |
| 部署順序 | 資料表有異動時：API migration → API → 後台 |

---

## 8. 新增頁面檢查清單（以「使用者管理」為例）

1. `app/db/repositories/user_repository.py` — 新增查詢函式
2. `app/services/user_service.py` — 商業邏輯；會變更資料的操作寫入 `audit_logs`
3. `app/routers/users.py` — 路由；router 已有 `require_admin`，POST 再加 `Depends(verify_same_origin)`
4. `app/templates/pages/users/list.html` — 繼承 `layouts/admin.html`，檔頭寫 `@template` 備註；寫法參考 `pages/system/job_runs.html`
5. 需出現在側欄時，更新 `app/constants.py` 的 `MENU_ITEMS`
6. `tests/unit/` 新增測試（`admin_client` fixture + monkeypatch service）
7. `ruff check .`、`djlint app/templates --lint`、`pytest` 通過
