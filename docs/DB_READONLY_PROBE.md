# ADMIN 最小唯讀 DB 盤點

> 現況（2026-10-26）：此入口需要管理者登入；本機無法登入（`SESSION_SECRET_KEY` 屬本機禁用機密），所以只能在雲端使用，目前尚未部署。本機查看或修改 DB 改用 START 注入的 DATABASE_URL 與 `/dev` 示範檔案（見 AGENTS.md「ADMIN 現況」）。下文「本輪」「本次」指 2026-10-24 實作當時的範圍；與現況衝突時以現況為準。

本輪只新增 `GET /system/db-inspection`：開發者以自己的既有網站 Session 呼叫 ADMIN，由 ADMIN 使用原有 Vercel／DB 設定查詢。一般開發者不需要 Supabase SQL Editor 或 Vercel 管理員權限，也不取得公司 DB 密碼。此入口沿用網站管理者角色；沒有網站權限就回原有的登入轉址或 403，不提供測試繞過。

程式已在本機實作；本輪沒有部署或連線公司 DB。雲端舊部署不會因本機新增檔案就自動具有此入口；在核定的部署流程完成之前，遠端可能是 404，不能寫成測試通過。

## HTTP／HTTPS 與 DB 是獨立問題

HTTP／HTTPS 決定瀏覽器或 Python 如何連 ADMIN；PostgreSQL 連線另用其協定與 DB TLS 設定；venv 只隔離 Python 依賴。本機 ADMIN 使用 HTTP，技術上完全可以查遠端 DB，不需要先有 HTTPS。前端的 cloudflared 方法也能提供 ADMIN HTTPS，但不是查 DB 的必要條件。

2026-10-24 依使用者指示，ADMIN 已移除 local 直連的執行位置限制；使用程序已取得的合法 DATABASE_URL。此處 check-db 選用雲端 ADMIN 作為「密鑰留雲端」的一種測試路徑，因此才驗證該遠端 HTTPS URL；不得推論成所有 ADMIN／DB 操作都必須 HTTPS，或把 URL 格式錯誤當成 DB 連線失敗。

## 入口及範圍

```text
https://<核定的-ADMIN-origin>/system/db-inspection?table=public.user_profiles
```

固定允許 `public.user_profiles`、`public.departments`、`public.user_departments`、舊模型的 `public.users`、v1.3 草案的 `invoiceguard_identity.user_accounts`。這是候選結構的盤點清單，不表示五張表都已存在；不查詢 `auth.users`，不接受任意 SQL 或其他表名。新增核定目標須同步 repository、Python 工具、測試及本文，不允許從任意 JSON 擴充。

回傳內容只有服務識別、連線結果、表是否存在、欄位名稱／型別／可空值及計數狀態。`password_hash` 等欄位名稱可以出現在結構中，任何實際 Email、雜湊、個資資料列、欄位預設值／CHECK 表達式、DB role、連線字串或雲端環境變數都不回傳。

| count_status | row_count | 意義 |
|---|---|---|
| exact | 非負整數 | 一般表的精確 COUNT；0 才代表確認為零筆 |
| missing | null | 連線成功，但指定表不存在；不是 DB 斷線 |
| rls_active | null | 目前 DB 身分受 RLS 限制，不以可見子集冒充整表筆數 |
| permission_denied | null | 缺 schema USAGE 或整表 SELECT；仍可回傳可取得的結構 |
| unsupported_relation | null | View、外部表、分割表或有繼承子表的表不執行 COUNT |

失敗使用固定代碼：`target_not_allowed`（HTTP 400）、`configuration_unavailable`、`db_unavailable`、`query_timeout`、`query_failed`（後四者 HTTP 503）。成功盤點或計數受限都是 HTTP 200，必須再看 `exists`／`count_status`，不能只看 HTTP。

## 唯讀與既有授權

沿用 `require_admin` 驗證簽署 Cookie、有效使用者及 `role=admin`。網站管理者不是 Supabase／Vercel 管理員。既有授權仍以舊 `public.users` ORM 與整數 UID 查驗；若雲端已改成 v1.3 自管帳號，需另行處理網站登入對接，本輪不假造相容性、不改 Schema 或登入流程。

盤點使用與授權 Session 分開的連線。Service 控制交易，第一條 SQL 是 `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`；交易內固定 statement timeout 4 秒、lock timeout 1 秒。`SET LOCAL row_security = off` 的用途是讓受 RLS 篩選的查詢直接失敗，**不是繞過 RLS**；另先檢查 `row_security_active()`，RLS 生效就不計數。COUNT 使用核定常數與 `FROM ONLY`，不掃描繼承子表。成功、失敗均 rollback 並關閉連線；不修改永久設定、不 commit。回應 `Cache-Control: private, no-store`。

這些是程式與替身測試驗證的防護，真實 PostgreSQL／Supabase 權限和連線仍待核定環境實測。DB 模組已允許本機使用合法 DATABASE_URL，不需偽造 VERCEL=1，也不因 ADMIN 網頁使用 HTTP 而拒絕。

## 本輪 DB 限制移除

已執行 START、ADMIN 已啟動且 venv 已取得連線設定是本輪前提。查詢可直接由該 ADMIN 的既有 HTTP 入口 `/system/db-inspection` 進入本站 DB 模組，不要求再改 START、提供遠端 ADMIN URL 或建立 Tunnel；網站授權仍照舊。下方 check-db 是先前另做的遠端工具，不是本機 DB 連線的必要步驟。本輪未實際連 DB，離線測試不能當成真實連線通過。

## 開發者操作：只用 START.cmd

服務啟動使用既有入口：

```powershell
& "C:\InvoiceGuard_project\InvoiceGuard-STARTUP\START.cmd"
```

START 開啟三站及手機前端 HTTPS，另列「ADMIN 雲端 DB 查詢 HTTPS」或缺少公開設定的具體原因。本機 ADMIN HTTP 別名是本機服務入口，其既有 DB 模組可使用 START 注入的設定直連雲端 DB；此處 check-db 另行呼叫雲端 ADMIN。前端 Quick Tunnel 是手機網站入口，不能當作雲端 ADMIN。不要把 http 改成 https 假裝已有 TLS／雲端 DB 設定。

需要盤點時，在另一個 PowerShell 執行同一個入口：

```powershell
& "C:\InvoiceGuard_project\InvoiceGuard-STARTUP\START.cmd" check-db
# 選用其他核定表；不用改 URL 或再啟動服務。
& "C:\InvoiceGuard_project\InvoiceGuard-STARTUP\START.cmd" check-db --table public.departments
$LASTEXITCODE
```

check-db 沿用既有 venv，不啟動／接管／停止本機服務或 Tunnel，不要求手動 Activate、Python／Uvicorn 或 pytest。它從 ADMIN 既有設定載入核定公開 `REMOTE_ADMIN_BASE_URL`，先檢查設定，再隱藏輸入本人網站 Session，最後只發一個 GET。不自動發送公司 DB 查詢；一般 START 只顯示網址及狀態。

## 一次性公開設定與雲端前提

使用既有 `InvoiceGuard-STARTUP/dev-urls.local.json` 的 `admin.REMOTE_ADMIN_BASE_URL`。欄位只放經核定的 ADMIN 公開 HTTPS origin，不放 /login、/system/db-inspection、query 或任何帳密。若檔案已有其他設定，保留原內容，只填核定欄位；範例在 config/dev-urls.example.json。公司密鑰仍在雲端，沒有新增本機秘密設定。

缺設定時 START check-db 直接指出欄位與範例位置，在要求 Cookie／發 HTTP 前停止；不讓使用者每次手填 URL 或複製教學用假網址。錯設定、前端 Tunnel、本機 HTTP 別名及帶路徑的值都明確拒絕。無效值不原樣回顯，避免把誤貼秘密帶進終端。

核定 ADMIN 雲端版本必須已提供此入口、原網站授權可用，且操作者已具有本人有效 ADMIN 網站 Session。本輪沒有部署；雲端舊版可能回 404。網址格式正確不代表新版本已部署，也不代表 DB 已連通。網站授權仍使用舊 users／整數 UID；與 v1.3 草案不相容時需另行處理，不能用探測入口繞過。

## 原網站 Session 與結果

工具不自動 POST /login：現有成功登入會更新 last_login_at，不符合本輪不寫正式資料的要求。沒有既有有效 Session 時，先記錄「授權前提未滿足」，不要為了本輪測試另行登入正式站。

在本人已登入的核定 ADMIN 頁面，開瀏覽器開發者工具 → Application／Storage → Cookies，取得該 origin 的 ig_admin_session 值。這是個人的網站憑證，不是 SESSION_SECRET_KEY；不要貼給 AI、放截圖、命令列、環境變數或檔案。START check-db 執行後才在隱藏提示貼上，僅存本次記憶體，不產生憑證檔。沒有 Supabase／Vercel 管理員權限不影響此設計，但原網站管理者授權仍須通過。

只輸出白名單 metadata；不用 Web UI 查表、不建立 JSON 結果檔、不追隨轉址、不使用環境 Proxy、不停用 TLS。`--table` 可指定前述五表，未指定使用 public.user_profiles。

| 退出碼 | 意義 |
|---|---|
| 0 | 表存在且取得精確筆數 |
| 3 | 表不存在或計數受限，不算完整通過 |
| 2 | 公開 URL 未設定／無效、參數或隱藏輸入無效 |
| 4／5 | 未登入／無權限，保留原網站授權 |
| 6 | 入口未提供或部署版本不同 |
| 7 | 網路、DB、設定或回應契約失敗 |
| 130 | 使用者取消 |

pytest 與手動 Python 相容工具僅供維護／Claude 驗證，不是日常服務啟動步驟。歷史檢驗指令見 [CLAUDE_DB_READONLY_REVIEW.md](CLAUDE_DB_READONLY_REVIEW.md)。

## 檔案生命週期與下一步

`STARTUP/launcher/admin_readonly_probe.py` 是 START 使用且隨分享套件封裝的受管理執行模組；`tests/tools/admin_readonly_probe.py` 是相容薄入口；`tests/test_admin_readonly_probe.py` 是離線回歸測試。依使用者指定，`tests/_manual_tmp/admin_readonly_probe/` 只放可刪的薄入口，不放 Cookie、秘密或結果。測完可以刪除該暫存子目錄；日常改用 START.cmd check-db，無須依賴暫存目錄。一般 START 不自動查詢 DB；分享套件已同步納入執行模組，沒有公司秘密。

本次只有連線、存在性、欄位、筆數；未加入約束盤點、CRUD、Storage、登入 API 或資料交接。下一階段須先核對真實結構，再決定 CREATE／ALTER，不能以草案或離線 PASS 代替雲端結果。

依據：[PostgreSQL 唯讀交易](https://www.postgresql.org/docs/current/sql-set-transaction.html)、[RLS 的 row_security 保護](https://www.postgresql.org/docs/current/ddl-rowsecurity.html)、[繼承與 ONLY](https://www.postgresql.org/docs/current/ddl-inherit.html)。
