# 部門管理（2-6 系統部）

> 本分支整合更新（2026-10-27）：使用者已核定暫時封閉本功能。核定本機入口亦回 503，待 E 身分／職務／scope／稽核接妥後再開放。下文匿名放行與 DB 實測均為來源分支歷史，不適用於目前 E 整合分支；此次沒有重跑正式資料操作。見 [整合報告](E-INTEGRATION-ACCOUNTS-DEPARTMENTS.md)。


正式功能入口：`http://invoiceguard-ai-admin.localhost:8080/department`。
路由依使用者更正採用 `/department`；未新增 `/dev` 路由、不匯入任何 dev 模組。

## 範圍與架構

- `app/routers/departments.py`：頁面與 POST 表單、此功能專用的本機存取與同源檢查。
- `app/services/department_service.py`：輸入驗證、交易、錯誤去敏、停用業務限制。
- `app/db/repositories/department_repository.py`：固定 SQL＋綁定參數，沿用既有 SessionLocal/get_engine；不 commit、不變更 Schema。
- `app/schemas/department.py`：部門輸出契約，不增加 ORM。
- `app/templates/pages/departments/list.html`：既有 ADMIN 版型、繁中表格與表單、HTMX、既有 data-confirm。
- `app/main.py`、`app/constants.py`：路由掛載及導覽選單。
- `tests/unit/test_departments.py`：不連線的合成資料回歸測試。

依代碼或名稱做不分大小寫的文字搜尋，`%`、`_` 不視為萬用字元；顯示全部搜尋結果。
新增代碼與名稱須為去除頭尾空白後 1–200 字。可分別修改代碼與名稱，代碼仍須唯一。
依 2026-10-27 使用者明確要求新增代碼修改；UNASSIGNED 為保留代碼，禁止更名或改用該代碼。
修改代碼前鎖定部門並檢查是否存在以代碼為目標的外鍵；有此類外鍵則拒絕，避免連動其他資料。UUID、名稱、狀態及帳號關聯不變。
顯示建立／更新時間（台北時區）；狀態操作不執行 DELETE。
正式畫面沒有測試資料。POST 成功或失敗均回傳明確訊息，失敗不顯示假成功。

## 權限邊界

這是正式業務功能，本階段依使用者要求允許本機匿名測試：同時要求 `APP_ENV=local`、非 Vercel、解析後 `module_backend=remote`、loopback 連線及核定本機 Host。
這裡 remote 是既有設定對「本機」的命名，不代表呼叫遠端 ADMIN，也不依賴 `/dev`。
POST 另外要求完整同源 Origin，缺漏或跨站直接拒絕。
雲端整個路由回傳 404，即使已登入亦暫不開放；正式授權與部門權限接妥後才能調整此功能的保護。
沒有變更共用登入、會員、Session 或權限程式。未部署、未提交或推送。

## 停用限制

拒絕停用 `UNASSIGNED`，保護現有帳號建立流程。
檢查 `invoiceguard_identity.user_accounts.primary_department_id` 與 `public.user_departments.department_id`，任何帳號／成員關聯均拒絕，不受帳號狀態影響。
讀取 pg_catalog 實際外鍵：已核對的外鍵只有上述兩組單欄關聯；其他外鍵一律拒絕停用，需 DB 維護者核對。
資料表缺漏、權限不足或 RLS 可能隱藏關聯均失敗回滾，不能視為無關聯。
狀態操作鎖定目標部門；停用時短暫 SHARE 鎖住上述兩張關聯表，避免檢查期間有新的關聯寫入；取鎖逾時兩秒則回滾。
不自動修改主要部門、部門成員或權限。鎖會短暫阻擋帳號及成員寫入；這是停用交易的暫時影響。
其他程式若未檢查 is_active，仍可能在停用提交後加入關聯；須由權限／帳號功能日後整合，本次未修改其程式。

## 驗證與限制

離線 pytest 使用合成資料與 Session 替身，不執行正式 DB 寫入。
指定 SQL 草案未在目前專案樹找到；依需求提供的部門欄位及現有 v1.3 帳號 SQL 實作，未建立或更動任何表。
真實資料庫欄位、外鍵與鎖權限仍須由啟動中的本站驗收，不能將單元測試視為真實 DB CRUD 通過。
既有 audit_logs ORM 與登入仍屬舊 users 草案，本功能未接入該舊表；正式開放前須確認可用的 v1.3 操作者與稽核契約並接線。

## 本次驗證結果（2026-10-27）

- 部門代碼修改增補：33 項部門測試通過，架構、Ruff、djLint 檢查通過。
- 依使用者指定，真實 DB 的 `Finance Department` 已改為 `finance_d`；重新讀取確認部門 UUID 與名稱不變。僅更新代碼與 updated_at。

- 全套離線測試：688 passed、9 skipped；僅既有 TestClient/httpx 棄用警告。
- `check_project.py`、Ruff check、Ruff format --check、djLint 全部通過；environment --check 通過（不代表連線檢查）。
- 本機網址 HTTP 200；真實部門列表、新增、搜尋、重複代碼拒絕、名稱修改及啟用成功；`/dev/accounts` 真實列表讀取正常。
- 真實停用未通過：資料庫另有 `public.department_permissions(department_id)` 外鍵，超出需求列出及既有帳號模組已核對的關聯；保護邏輯拒絕停用，沒有繞過或更動部門權限。
- 虛構測試資料代碼：`IGTEST_81d56327f9e647be`，名稱「虛構驗收部門已改名」，因上述限制仍為啟用。未 DELETE；未修改真實使用者或其部門。
- 建議由資料庫／部門權限負責者提供 department_permissions 契約，確認該關聯的停用規則，再擴充唯讀關聯檢查；保持不修改成員、主要部門或權限資料。
- 沒有實際瀏覽器目視或確認框點擊驗收；目前以模板檢查、TestClient 與本機 HTTP 驗證。
