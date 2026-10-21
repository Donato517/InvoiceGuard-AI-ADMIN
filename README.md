# InvoiceGuard-AI-ADMIN

InvoiceGuard AI 管理後台：發票審核、使用者管理、排程與稽核紀錄查詢。

| 項目 | 內容 |
|---|---|
| 語言 / 框架 | Python 3.12 + FastAPI + Jinja2 樣板（伺服器端產生 HTML） |
| 互動 | HTMX（以 HTML 屬性做局部更新，幾乎不寫 JavaScript） |
| 資料來源 | **自有 DB 模組**（SQLAlchemy），直接連與 API 相同的 PostgreSQL |
| 登入 | 管理者帳號（`users.role = admin`），已簽署的 httpOnly cookie |
| 品質工具 | Ruff（Python）、djLint（樣板）、pytest |
| 部署 | Vercel Python Runtime（自動偵測 `app/main.py`），不開放搜尋引擎索引 |
| 相關專案 | [InvoiceGuard-AI-API](https://github.com/Donato517/InvoiceGuard-AI-API)（後端 API，資料表結構管理者）、[InvoiceGuard-AI](https://github.com/Donato517/InvoiceGuard-AI)（前台） |

## 快速開始（本機）

```bash
# 1. 建立虛擬環境並安裝套件（Windows 用 .venv\Scripts\activate）
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# 2. 建立環境變數檔，填入 DATABASE_URL、SESSION_SECRET_KEY（Windows 用 copy）
cp .env.example .env

# 3. 啟動（資料表需先由 InvoiceGuard-AI-API 執行 alembic upgrade head 建立）
uvicorn app.main:app --reload --port 8002
```

開啟 <http://localhost:8002>。本機埠號：API `8000`、前台 `8001`、後台 `8002`。

## 常用指令

| 指令 | 說明 |
|---|---|
| `uvicorn app.main:app --reload --port 8002` | 本機啟動 |
| `ruff check .` / `ruff format .` | Python 檢查 / 格式化 |
| `djlint app/templates --lint` | 樣板檢查 |
| `djlint app/templates --reformat` | 樣板格式化 |
| `pytest` | 執行測試 |

## 文件索引

| 文件 | 內容 |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系統全貌、分層架構、目錄說明、檔案放置規則、登入與安全、部署 |
| [docs/CODING_CONVENTIONS.md](docs/CODING_CONVENTIONS.md) | 命名規則、**程式碼與樣板備註格式** |
| [docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md) | 分支、Commit 訊息、PR 規範 |
| [app/db/README.md](app/db/README.md) | **DB Schema 總覽**、與 API 專案的同步流程、存取規則 |
