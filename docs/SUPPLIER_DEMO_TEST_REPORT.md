# 供應商管理試作驗收

日期：2026-11-03，台灣時間。
工作區：`C:\InvoiceGuard_project\prototypes\InvoiceGuard-AI-ADMIN-supplier-demo`。
分支：`codex/supplier-demo`。未 commit、merge、push 或部署。

## 成功

- 最終 pytest：17 passed，1 個既有 Starlette TestClient 使用 httpx 的棄用提醒。未因此更動團隊套件。
- 測試包含新增、名稱／統編／別名搜尋、字面搜尋、重複拒絕（包括停用資料）、修改與建立時間保留。
- 停用前未勾選確認拒絕；勾選後停用成功；資料保留及重新啟用成功。
- 必填、統編格式、Email、國別、幣別、天數與付款類型驗證失敗時不寫入。
- 外國空白稅籍編號、HTML 跳脫、404、舊表單版本衝突及重啟保存成功。
- 表單 token、外站 Origin、Host、非本機 peer 及雲端入口拒絕成功。
- 測試封鎖非 loopback socket 連線；Windows asyncio 必需的 loopback 通訊允許。
- 真實 Uvicorn 以 127.0.0.1:8092 啟動；原型重啟後，既有模擬資料仍存在。
- 最終無頭 Chrome：完成新增、修改、搜尋、確認停用與啟用；未出現 pageerror。
- 桌面 1440px、手機 390px 截圖已檢查；手機列表可在表格內橫向捲動。
- Ruff check、Ruff format --check：通過；djLint：4 檔 0 錯誤。
- scripts/check_project.py：PASS，0 issues，僅離線靜態檢查。
- 原 ADMIN 仍在 main，原本未追蹤的 tests/unit/test_login_readiness.py 保留。
- app/main.py、登入及權限程式沒有變更；共用 DB／Storage／真實帳號未連線或修改。

## 過程中失敗但已處理

- 初次 pytest 暫存父目錄不存在，已建立試作專用 artifacts 路徑。
- 初次連線封鎖也擋到 Windows asyncio socketpair，已限縮為封鎖非 loopback。
- 受限制執行環境測試停滯，已中止並在正常 Windows 執行離線測試。
- HTML 格式化後 token 屬性換行，測試解析式已支援空白換行。
- 第一次靜態分層檢查 11 項問題；獨立入口移至 scripts，SQL 集中 Repository，路由錯誤介面改由 Service 提供；未修改檢查器。
- 詳情模板空白與共用表單欄位類別已修正，並重新進行瀏覽器驗收。
- 瀏覽器驗收腳本的 Windows 模組路徑及重複名稱定位已修正，最後執行通過。

## 未測試或不屬本次

- 未執行正式登入、原專案完整測試、正式供應商 DB、Storage 或權限端到端測試。
- 未驗證官方統編檢查碼；國別及幣別僅檢查字母長度，未接正式字典。
- 未實作實際付款日計算、發票串接、銀行帳戶、評估、合併及結案候選。
- 多使用者正式稽核歷史未實作；本版只有建立／修改時間及舊表單衝突檢查。
- 預付／分期只有條件與備註，不包含期別明細或付款排程。

## 證據位置

忽略路徑 `artifacts/supplier-demo/browser/`：list.png、detail.png、mobile-list.png、mobile-form.png。
瀏覽器驗收留下數筆明確標示的虛構供應商；與初始兩筆種子資料一起保留，便於試用。

## 2026-11-03 徵信等級與交貨條件更新

新增人工模擬徵信標籤與交貨條件，列表、詳情及編輯表單皆支援。
本機資料先備份再增加欄位，原識別碼、名稱、統編、狀態及建立時間核對保留。
本次最終結果：pytest 21 passed、djLint 3 檔 0 錯誤、Ruff 及 check_project 通過；真實本機瀏覽器儲存及顯示兩欄通過。
徵信僅為模擬人工標籤，未實作正式徵信查核、歷次評估或自動交易限制。未連共用 DB／Storage，未 Push／Merge。

## 2026-11-04 模擬部門功能配置（已由下方修正版取代）

僅在獨立工作區新增 `/suppliers/department-functions`；正式 ADMIN 程式與共用 DB 未改。
四個固定模擬部門可分別保存查看／新增／修改／停用；設定不作為實際驗權。

- pytest：27 passed、1 個既有 Starlette/httpx 棄用警告；全部使用暫存 SQLite，對外連線封鎖。
- 初次受限制環境的測試無輸出停滯，已中止；同一指令在正常 Windows 環境通過。
- Ruff check 通過；兩份修改模板 djLint 0 errors；check_project PASS（0 issues）。
- 真實試作 GET 頁面 HTTP 200；隱藏瀏覽器驗證全選／取消、四項獨立、部分勾選狀態及返回供應商列表通過。
- 390px 手機頁面無整頁橫向溢出；表格在自身容器內滑動。已檢視桌面畫面截圖。
- 瀏覽器未提交設定；儲存、重啟保留、跨部門隔離、全撤銷與過期提交拒絕由離線 HTTP／Service 測試驗證。
- 本機 14 筆供應商新增設定表前後的完整資料雜湊一致，原資料保留；設定表目前 0 筆，沒有代替使用者勾選。
- 已先以 SQLite backup 備份至忽略目錄 `artifacts/supplier-demo/suppliers-before-permissions-*.sqlite3`。

截圖：`artifacts/supplier-demo/browser/department-functions.png`、`department-functions-mobile.png`。
未驗證／未實作：正式帳號登入、正式部門、正式四操作權限，以及依模擬設定攔截供應商操作。

## 2026-11-04 修正為供應商詳情的功能列表

依使用者重新貼上的原預覽，更正為每筆供應商詳情中的六列功能表，移除誤加的部門入口與模板。
新設定以供應商 UUID 個別保存；不轉用先前的部門設定，不刪除本機舊資料。
離線測試27 passed（含原21案、修正版6案），既有Starlette/httpx警告1個；Ruff、3份模板djLint及check_project均通過。
瀏覽器驗證六列、整列全選、全部勾選／取消、未開發操作disabled、390px無整頁橫向溢出及舊選單消失；未提交實際設定。
原14筆供應商完整資料雜湊與修正前一致。備份為 artifacts/supplier-demo/suppliers-before-function-list-*.sqlite3。
截圖：artifacts/supplier-demo/browser/supplier-function-list.png、supplier-function-list-mobile.png。
設定保存、各供應商隔離、全部撤銷、重啟保留、過期提交拒絕與安全檢查均以暫存SQLite離線驗證。
這些勾選不作為真實權限或功能啟停；正式登入、DB、Storage及主專案未改，未Push／Merge。

## 2026-11-04 納入最新 main 前的整合檢查

以遠端 main `5fd72f18d3c21dde0c93329e13bd9b304a48e976` 建立獨立檢查工作區，只附加試作檔案，保留其他成員的更新。
完整離線回歸：2696 passed、40 skipped、1個既有Starlette/httpx棄用警告；供應商27案包含於通過結果。
check_project PASS，Ruff lint通過，214個Python檔案格式通過，76份模板lint零錯誤。
第一次測試因新工作區缺少指定的暫存父目錄而報錯；建立忽略目錄後重新執行上述完整回歸，沒有修改業務程式或放寬測試。
未驗證正式資料庫、Storage、帳號或雲端部署。納入main僅共享隔離試作，正式入口不掛載。
