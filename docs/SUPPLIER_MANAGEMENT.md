# 正式供應商管理

## 入口與使用

正式 ADMIN `/suppliers`，本機網址為 `http://invoiceguard-ai-admin.localhost:8080/suppliers`。
需要有效的既有 ADMIN Session；沒有匿名或本機例外。側欄從既有權限選單取得供應商入口，放在「登出」正上方。
`START_SUPPLIER_DEMO.cmd` 的 8092 仍是獨立 SQLite 模擬站，不是正式入口；原模擬資料不會自動搬入正式 DB。

## 主檔與權限

- 主檔為全公司共用，沒有部門所有權；目前選取部門決定操作許可，不聯集其他部門。
- 固定功能代碼 `suppliers`、平台 `admin`；查看、新增、修改、停用／啟用四項獨立。
- 沿用 `department_permissions.can_delete` 表示停用／啟用；程式沒有 DELETE 供應商路由。
- 新增、修改及狀態操作除了請求驗權，也在同一 DB 交易內以既有鎖與 Session 契約重新驗權。
- 寫入限精確同源表單，拒絕未知／重複欄位；操作者從 Session 取得，表單不能指定。
- 未勾選查看者不能查看主檔列表；若只勾選新增，仍能開新增表單並收到結果，不會因此取得查看權限。

## 資料契約

資料表 `public.suppliers`，由 API migration `5e8b11d0a941` 管理。
識別碼 UUID 自動產生，不使用統編作主鍵。
業務欄位與 `app/schemas/supplier.py::SupplierInput` 一致：名稱、國別、統編、別名、聯絡資料、付款條件／起算基準／天數／方式／幣別、內部徵信等級、交貨條件及備註。
另存 `is_active`、`created_at`、`updated_at`、`created_by`、`updated_by`；操作者參照 `public.user_profiles`，不新增舊 `users` ORM 依賴。
目前以固定參數 SQL repository 存取，沒有另一份 ORM 表定義或 ADMIN migration。

同一國別的非空統編唯一，包含停用資料；國外統編可留空，不能據此認定兩家廠商相同。
台灣統編只檢查八碼格式，未連接稅籍查證。國別／幣別檢查英文字母碼格式，不代表外部名錄驗證。
付款條件是主檔預設值，不計算付款到期日或執行付款。內部等級為人工標籤，沒有外部徵信查核、自動信用限制或評估歷史功能。
修改與狀態採 `updated_at` 原子比對；舊版本會拒絕，避免覆蓋他人修改。
主檔直接存 PostgreSQL，重新啟動保留；本功能未使用 Storage。

## 正式啟用前步驟

1. 資料庫維護者先核對 API migration 與既有 `alembic_version`。parent 為 `7c1d4e9a2b30`；不要為了供應商而擅自套用其他尚未核准的 migration。
2. 本次建立 migration 不等於執行授權。套用前取得使用者確認；需有資料庫備份及可回復的交易，確認 runtime DB role 可存取而匿名角色不可存取。
3. Migration 只新增 `suppliers` 及 `app_functions` 的 `suppliers` 項目，不更新既有部門勾選，不匯入任何虛構供應商。
4. 由已有部門管理權限的人，到正式 ADMIN「部門管理」逐部門勾選供應商操作。未設定時預設拒絕，不自動給全公司所有人。
5. 以核准的虛構測試資料驗收新增、同國別重複統編、修改、舊版本拒絕、停用與啟用；真實資料僅由負責人維護。

資料表啟用 RLS、沒有匿名讀寫政策；沿用既有伺服器受信任 DB 身分。若 runtime role 受 RLS 過濾，程式拒絕全公司查詢，不擅自停用 RLS。由維護者確認角色設定，不能直接新增 PUBLIC／anon／authenticated 許可解決。
回退程式時停用功能目錄並保留主檔。Migration 不提供自動刪表的 downgrade。

## 第一版以外

發票供應商連結、供應商合併、結案候選、完整徵信紀錄及每次變更的完整稽核歷史尚未實作。本版只保留建立者、最後修改者及時間。
