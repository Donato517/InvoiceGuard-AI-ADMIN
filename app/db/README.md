# DB 模組（`app/db`）— 後台

AI 先讀根目錄 [AGENTS.md](../../AGENTS.md)／[CLAUDE.md](../../CLAUDE.md)，
目前實作狀態見 [DEVELOPMENT_STATUS.md](../../docs/DEVELOPMENT_STATUS.md)，
契約／ORM 同步及 Repository 分層檢查見 [PROJECT_CHECKS.md](../../docs/PROJECT_CHECKS.md)。
此文件的部署與 migration 流程只是管理者維運參考，並非當前任務授權。
不下載、輸出或寫入實際連線字串；DB 模組使用程序已取得的 DATABASE_URL（本機由 START 注入，連正式 DB）。

> 後台以本模組**直接存取**與 InvoiceGuard-AI-API **同一個 PostgreSQL 資料庫**。
> **資料表結構（migration）只由 API 專案管理**；本模組的 Model 是對應定義，必須與 API 專案保持一致。
> 每張資料表的完整 Schema 寫在 `models/*.py` 的**檔案開頭備註**，格式與 API 專案相同。

---

## 1. 目錄說明

| 路徑 | 說明 |
|---|---|
| `base.py` | `Base`、約束命名規則、共用欄位 Mixin、`in_values_check()`（與 API 專案相同） |
| `session.py` | Engine、Session、FastAPI 相依注入 `get_db()` |
| `models/` | ORM Model，自 API 專案同步；**檔案開頭必須有 Schema 備註** |
| `models/__init__.py` | 匯入全部 Model |
| `repositories/` | 資料存取層（查詢 / 新增 / 更新）。不含商業邏輯、不自行 `commit` |

本專案**沒有** `migrations/`，也不安裝 Alembic。

第一階段新增 `contracts.py` 定義 Repository 介面，未實作通用 CRUD，也未改動 ORM 模型或 Schema。
`session.py` 驗證 PostgreSQL 連線設定與 TLS 後延遲建立 Engine；本機與雲端均可使用，不再要求 Vercel 執行位置。
transaction pooler 模式停用 psycopg prepared statements；2026-10-25 本機已實測唯讀 SELECT 1。
`repositories/user_account_repository.py` 存取 v1.3 草案的 `invoiceguard_identity.user_accounts`：本站沒有對應 ORM Model（Model 只能自 API 同步），因此以固定 SQL＋綁定參數存取，交易仍由 service 控制。
完整驗證及未完成項目見 [模組契約](../../docs/MODULE_CONTRACT.md)。

---

## 2. 資料表索引

| 資料表 | 說明 | 定義檔 | 後台用途 | 讀寫 |
|---|---|---|---|---|
| `users` | 使用者（前台 / 後台共用，以 `role` 區分） | [models/user.py](models/user.py) | 管理者登入、使用者管理 | 讀 / 寫 |
| `invoices` | 發票主檔（含 AI 風險分析結果） | [models/invoice.py](models/invoice.py) | 發票審核 | 讀 / 寫（審核結果） |
| `invoice_items` | 發票明細 | [models/invoice.py](models/invoice.py) | 審核詳情 | 讀 |
| `audit_logs` | 操作稽核紀錄（append-only） | [models/audit_log.py](models/audit_log.py) | 記錄管理者操作、稽核查詢 | 新增 / 讀 |
| `job_runs` | 排程執行紀錄 | [models/job_run.py](models/job_run.py) | 排程紀錄頁 | 讀（由 API 寫入） |
| `invoiceguard_identity.user_accounts` | 登入帳號（v1.3，UUID 主鍵） | 無 ORM，見 [user_account_repository.py](repositories/user_account_repository.py) | `/dev/accounts`、db-inspection | 讀 / 新增 / 修改 |
| `public.user_profiles` | 帳號顯示資料（v1.3） | 同上 | 新增帳號時建立 | 新增 |
| `public.departments` | 部門（v1.3） | 同上 | 確保預設部門 `UNASSIGNED` | 讀 / 新增 |
| `public.user_departments` | 帳號所屬部門（v1.3） | 同上 | 新增帳號時建立 | 新增 |

上方 ORM 表（`users` 等）是舊草案，與 v1.3 身分表互不引用；管理者登入（`auth_service`）目前查的是 ORM `users`。兩者結構都屬 API 專案管理。

---

## 3. 資料表關聯（ERD）

```
users ──1:N──> invoices ──1:N──> invoice_items
  │
  └───1:N──> audit_logs        job_runs（獨立，無外鍵）
```

---

## 4. 命名規則與 Schema 備註格式

與 InvoiceGuard-AI-API 完全相同，詳見 API 專案 `app/db/README.md`：

- 第 4 節：命名規則（資料表複數、`_at` 時間欄位、`NUMERIC(12,2)` 金額、約束命名…）
- 第 6 節：Schema 備註格式（每張表一個區塊、欄位順序與 ORM 一致、異動紀錄新到舊）

本專案的 Model 檔頭多一行 **「定義來源」**，異動紀錄需註明對應的 **API revision**：

```text
定義來源：InvoiceGuard-AI-API 的同名檔案（資料表結構與 migration 由 API 專案管理，本檔必須與其一致）
...
異動紀錄（新到舊；上線後須附 migration revision）
2026-11-05  JerryChien  同步新增 reviewed_at 欄位（API revision: a1b2c3d4e5f6）
2026-10-21  JerryChien  自 API 專案同步初版草案
```

---

## 5. 同步流程（資料表結構異動時）

1. **API 專案**修改 Model 並建立 migration 檔；執行須另取授權，正式環境依核定流程處理
2. **本專案**同步修改 `models/` 對應檔案：
   - 從 API 專案複製 ORM 定義與 Schema 備註（保留本專案的「定義來源」一行）
   - 異動紀錄加一行，註明 API revision id
   - 若是新資料表，在 `models/__init__.py` 匯入並更新本文件「資料表索引」
3. 相依順序：**migration → API → 後台**；執行 migration 與部署需使用者明確授權

**禁止事項：**

- 在本專案新增 migration 或手動 `ALTER TABLE`
- 只改本專案的 Model 而不改 API 專案（兩邊定義不一致會導致讀寫錯誤）
- 刪除或改名欄位時，API 專案先移除——應先讓本專案停止使用該欄位並部署，再由 API 專案移除

---

## 6. 存取規則

| 項目 | 規則 |
|---|---|
| 連線字串 | `DATABASE_URL`（pooled，與 API 專案相同的資料庫） |
| 連線池 | 應用端使用 `NullPool`，由資料庫端 pooler 管理連線（Vercel Serverless） |
| Session | 每個請求一個（`DbSession` 相依注入），不可放在全域變數共用 |
| 交易 | 由 service 層 `commit` / `rollback`；repository 只做 `add` / `flush` / 查詢 |
| 稽核 | 會變更資料的操作（審核、停用帳號…）必須在**同一個交易**內寫入 `audit_logs` |
| 軟刪除 | 查詢 `users`、`invoices` 必須加 `deleted_at IS NULL` |
| 密碼 | 只用 `app.core.security.verify_password()` 比對，雜湊方式須與 API 專案一致（pwdlib / Argon2） |
| 時區 | 資料庫存 UTC；樣板以 `\|taipei_datetime` 顯示台灣時間 |
