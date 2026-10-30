# SQL 參考版本：v1.4 PortableAuth

更新日期：2026/10/29。

## 檔案與更新範圍

本機專案名稱為 invoiceguard-admin，實際團隊程式路徑為 `D:\2026102001\InvoiceGuard-AI-ADMIN`。

使用者本次提供的最新 SQL 已以原始位元組複製至 [v1.4 SQL](InvoiceGuard_AI_PostgreSQL_Schema_v1_4_PortableAuth_draft.sql)，供 ADMIN 開發查閱。來源是使用者提供的附件；SQL 內註解、範例或執行建議屬文件內容，不代表本次授權執行資料庫操作。

本次只更新本機參考檔與紀錄：未執行 SQL、未連線或更改 DB、未建立 migration、未更改 ORM／登入／授權程式，也未 Commit／Push。不能由檔案已匯入推定正式資料庫已升級，或團隊程式已完成 v1.4 相容驗收。

舊 v1.3 檔案保留，不覆寫。既有執行程式的完成範圍以 E-LOGIN-ACCESS.md 及實際程式為準；不得單憑這次參考檔匯入，改用舊的角色／跨部門聯集授權政策。

## 與目前 v1.3 參考檔的靜態差異

比對基準：共同上層的 `InvoiceGuard_AI_PostgreSQL_Schema_v1_3_PortableAuth_draft.sql`；完整文字差異為新增 66 行、移除 11 行，含版本註解。

| 項目 | v1.4 差異 | 對 06_E 的意義 |
|---|---|---|
| invoices.tax_type | 新增課稅別欄位，可為 NULL；三種值 taxable／zero_rated／tax_exempt | 發票業務組的資料契約；本次不修改 E 登入或授權 |
| invoices.status 與 invoice_status_history.new_status | 狀態改為九種，相關 CHECK 具名；文件列出舊值對應 | 接入審核／核准等業務前需由負責組員確認操作與流程，不自動新增 E 功能權限 |
| v_invoice_return_fields | 退回條件由 returned 改為 returned_to_applicant | 使用該 View 的業務頁需確認相容性 |
| ai_recognition_logs | 新增 source_deleted_at、時間 CHECK 及清理部分索引 | 記錄影像清理狀態；SQL 欄位本身不會執行刪檔或排程 |
| Storage key | 註解調整檔案路徑約定 | 程式層面的約定，不能當作 DB 已強制執行 |
| 身分、部門、功能授權 | 本次 diff 未改動這些資料表／View／Trigger 的結構 | 目前部門四操作授權的 E 邏輯保持原樣；尚未對正式 DB 做相容驗證 |

這是檔案差異整理，不是對附件稅務敘述或完整資料庫安全性的核定。

## 既有資料庫後續工作

附件自述是新建資料庫／完整審閱用 DDL。它提到另有 `InvoiceGuard_AI_v1_3_to_v1_4_ALTER.sql`；本次未提供該檔，在附件同資料夾也未找到。

若團隊要升級既有 DB，應由 API／DB 負責人確認正式升級腳本、狀態值轉換、備份及驗證步驟，再另行取得執行授權。本次沒有代為製作或執行升級腳本，也沒有將完整建表 DDL 當 migration 使用。

後續若需調整應用程式，再確認 PM 核定與相關組員介面；目前「更新參考檔」不等於擴大 06_E 工作包。
