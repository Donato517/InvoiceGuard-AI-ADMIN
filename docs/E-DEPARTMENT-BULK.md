# 部門授權批次勾選

日期：2026-10-28。

部門內容頁的授權功能區新增「全部勾選」「全部取消」；在功能／平台／狀態與查看之間新增全選欄，每列用與四操作相同的原生核取方塊，勾選／取消該列可編輯的操作。

僅部分操作勾選時，全選框顯示原生半選狀態；逐項變更會同步整列狀態。恢復表單或瀏覽器返回也重新計算。歡迎頁顯示固定欄，不提供整列勾選；其固定查看及禁止寫入不受批次操作影響。disabled 操作一律略過，衝突／無法確認的 blocked 表單停用批次控制項。

這是純前端便利操作：無 fetch、無自動送出、不讀寫 storage／Cookie，不動隱藏 UUID、內容版本、部門基本資料。控制項沒有 name，按鈕 type=button，實際送出仍只有原四操作 grant，仍需按儲存與原確認流程，後端驗證及原子交易不變。

工具控制項預設 hidden，僅腳本初始化後顯示；未啟用 JS 時仍可逐項設定、以原表單儲存。空功能目錄不提供可用批次操作。

新增實際 DOM 瀏覽器驗證（tests/js/test_e_department_bulk.cjs）：使用 tests/tools/render_department_bulk_preview.py 的合成頁面，全部請求攔截並 offline，不連本機或正式服務。需要開發環境有 Playwright，可用 PLAYWRIGHT_MODULE_PATH 指向既有 runtime；沒有 Playwright 或合成 fixture 時會明確 skip。用 node --test tests/js/test_e_department_bulk.cjs 執行，fixture 預設 tmp/e-department-bulk/detail.html，可用 BULK_PREVIEW_HTML 指定。執行前先用本站 venv Python -B tests/tools/render_department_bulk_preview.py 產生合成頁。

測試結果見開發紀錄；未操作正式 DB、未 Commit／Push。
驗證完成（2026-10-28）：相關 Python 回歸 122 passed、1 個既有 TestClient/httpx warning；實際 DOM 離線瀏覽器測試 9 passed、0 skipped（19.76s），涵蓋部分勾選同步、整列／整頁、disabled、reset／pageshow、JS 關閉、blocked 表單及無自動送出。1000／390 寬度六欄對齊、整頁無橫向溢出，已檢視合成畫面。三站 checker PASS（0 issues）、Ruff lint／125 檔格式通過、34 樣板 lint 0 errors。未重跑全套業務回歸；本次只改前端互動，原後端授權／交易不變。未讀寫正式 DB、未 Commit／Push。

2026-10-28 排版修正：五個控制欄等寬置中，統一兩行選項結構、checkbox 尺寸與說明位置；歡迎列亦統一標籤。驗證完成：最終相關 Python 回歸 47 passed、1 個既有 warning（2.96s）；實際 DOM 瀏覽器測試 9 passed、0 skipped（16.31s）。在 1000／390 寬度實測五控制欄等寬與各列 checkbox 中心對齊誤差不超過 1px，含長代碼換行；整頁無橫向溢出。已檢視桌機／手機合成截圖，歡迎頁與一般功能呈現一致，六欄完整對齊；保留表格內捲動。34 樣板 lint 0 errors。只修改樣板及頁面 CSS，未變更授權／JS 邏輯或讀寫正式 DB，未 Commit／Push。
