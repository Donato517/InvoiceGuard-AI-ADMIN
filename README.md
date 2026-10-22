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

## 快速開始（本機離線驗證）

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m app.integrations.vercel --check local
.\.venv\Scripts\python.exe -m pytest -q --basetemp=tmp/pytest
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002
```

預設不讀 `.env`，公司 DB、Storage、模型及 Session 密鑰只保留在雲端。
本階段完成模組設定驗證與介面契約，實際檔案／CRUD 服務及 localhost 遠端登入尚未接線。
本機直接建立 DB Engine 會被阻擋；不要將公司密鑰填入本機以繞過限制。
可先開啟 <http://127.0.0.1:8002/health> 與 <http://127.0.0.1:8002/login>。
本機埠號：API `8000`、前台 `8001`、後台 `8002`。
完整狀態、契約與 PowerShell 驗證方式見 [模組契約](docs/MODULE_CONTRACT.md)。

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
| [docs/MODULE_CONTRACT.md](docs/MODULE_CONTRACT.md) | Vercel / DB 模組第一階段、機密邊界、檔案與 CRUD 契約、離線驗證 |
