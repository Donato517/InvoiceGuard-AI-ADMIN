# 供應商管理隔離試作

本版仍是獨立的本機模擬模式；納入 main 僅共享試作程式，不代表正式供應商資料表或授權已完成，也不掛載正式 `app.main`。

2026-11-04 共享範圍：只新增獨立試作程式、模板、樣式、離線測試與操作文件，正式 ADMIN 選單、登入及權限不變。
本機 SQLite、備份、截圖與環境設定不上傳；其他成員初次啟動會建立自己的虛構資料，不會取得本機現有14筆資料。
推送 main 可能觸發既有 Vercel 建置；試作入口本身仍拒絕雲端啟動，正式網站不提供供應商試作路由。

## 開啟方式

工作區：`C:\InvoiceGuard_project\prototypes\InvoiceGuard-AI-ADMIN-supplier-demo`。
分支：`codex/supplier-demo`，基於本機 ADMIN 的 `3de8f5a`。

雙擊 `START_SUPPLIER_DEMO.cmd`。沿用原 ADMIN `.venv` 的 Python，不安裝套件、不使用 STARTUP、不取得雲端憑證。
開啟 `http://127.0.0.1:8092/suppliers`，控制視窗保持開啟；Ctrl+C 停止。
埠被占用時先確認既有試作是否已啟動，不要停止其他服務。

手動啟動：在本工作區執行 `C:\InvoiceGuard_project\InvoiceGuard-AI-ADMIN\.venv\Scripts\python.exe -B scripts\run_supplier_demo.py`。
換電腦時可自行準備既有專案規定的 Python 環境，再用該 Python 執行同一腳本。

## 資料保存

本機 SQLite：工作區的 `artifacts/supplier-demo/suppliers.sqlite3`，已忽略，不上 Git。
初次啟動產生兩筆明確標示的虛構資料，統編只是八碼格式範例，非真實交易識別。
新增、修改、啟停均持久保存，重啟不重設。無刪除或一鍵清空功能。
要備份時先停止試作，再複製這個檔案。遺失工作區或資料檔，資料就不再保留。

## 操作

### 供應商詳情功能列表（2026-11-04 修正）

在供應商列表點名稱或「檢視」，基本資料下方即為「供應商功能列表」。
六列為基本資料、聯絡資訊、付款條件、交貨條件、徵信等級與徵信／評估紀錄。
前五列依適用操作顯示查看／新增／修改／停用；最後一列尚未開發，全部停用勾選。
支援整列全選、全部勾選及全部取消；四項獨立，按「儲存模擬設定」後才保存。
每筆供應商各自保存於本機 `supplier_demo_function_settings`，重啟保留，舊畫面覆蓋會拒絕。
初次畫面沿用原預覽的預設勾選，尚未保存前會明確標示。這些是模擬配置，不攔截實際操作，也不變更供應商主檔或啟用狀態。
真正停用仍使用詳情下方具確認勾選的停用表單。

先前誤加的部門功能設定入口已移除，舊 GET 返回列表、POST 回410，不再保存部門配置；本機舊表保留不刪除。

列表支援名稱、統編及別名包含搜尋，含停用資料。新增一律啟用，詳情可修改或啟停。
停用前須勾選確認，啟用及停用以明確目標狀態送出，不使用反向切換。
代碼由 UUID 自動產生；建立與修改時間使用台灣時區。別名一行一個，最多十個。
台灣統編必填八碼，本版不驗證官方檢查碼；國外可空白。非空編號按國別唯一，包含停用資料。
付款天數為 0～3650 整數。現付須為 0；預付或分期須選依合約並填備註，不計算到期日。
同名供應商暫不阻擋；正式整合時需確認重名提示與人工查核規則。

## 隔離與限制

獨立入口 `scripts.supplier_demo_app:app` 不載入 `app.main`、core.config、登入、SQLAlchemy engine 或雲端整合模組。
本機免登入只存在於試作入口，不代表正式授權；loopback peer、Host、同源與表單 token 會檢查。
僅綁定 127.0.0.1；雲端標記存在即拒絕。請勿自行放上公開 Tunnel 或部署。
本版可讀寫的只有試作 SQLite，與現有 PostgreSQL／Storage 無關。表單不可輸入真實資料。
UI 沿用既有 base.html、CSS 及元件；模擬導覽只有供應商，不冒用真實登入身分。

## 離線驗證

使用既有 ADMIN Python，在本工作區執行：

```
New-Item -ItemType Directory -Force artifacts/supplier-demo
python -B -m pytest --noconftest tests/supplier_demo -q -o cache_dir=artifacts/supplier-demo/pytest-cache --basetemp=artifacts/supplier-demo/pytest-tmp
```

`--noconftest` 避免載入正式測試配置。測試使用獨立暫存 SQLite，封鎖 socket 連線。
測試結果見 `docs/SUPPLIER_DEMO_TEST_REPORT.md`，未執行項目不得視為通過。

## 正式整合前確認

1. 與資料庫負責人確定供應商、別名及聯絡資料結構、唯一限制與異動紀錄；本機表不可當作 migration。
2. 替換 Repository，接回既有交易與授權；資料範圍、四操作權限及管理入口另經確認。
3. 統編特殊情境、國別／幣別字典、重名提示、付款條件及核准規則需團隊定案。
4. 發票關聯、供應商合併、評估紀錄及結案候選均未實作；歷史票面資料需保留。
5. 主檔異動不可回寫舊交易的付款條件；銀行帳戶與實際付款不屬本版。
6. 接回正式 app.main 前撤除模擬入口及種子資料，完成功能註冊、權限、正式 DB 契約和整合驗收。

## 完整檔案清單

2026-11-03 更新：增加 `credit_rating` 供應商徵信等級與 `delivery_terms` 交貨條件。
等級為未評估、A（模擬低風險）、B（模擬一般風險）、C（模擬需加強查核），純人工標籤，不代表正式徵信結論或自動核准。
交貨條件為最多 300 字的文字，與付款條件分開；本次未實作評估紀錄、證據查核或 Incoterms 規則引擎。
本機原有表於啟動時補上兩個欄位，舊資料預設未評估及空白交貨條件；不變更共用 PostgreSQL。
既有本機資料已在 `artifacts/supplier-demo/suppliers-before-rating-*.sqlite3` 備份，原識別碼、名稱、編號、狀態及建立時間已核對保留。

| 檔案 | 用途 |
|---|---|
| `.gitignore` | 忽略試作資料及輸出 |
| `START_SUPPLIER_DEMO.cmd` | 本機雙擊啟動 |
| `scripts/run_supplier_demo.py` | 清除雲端設定繼承、固定 loopback 啟動 |
| `scripts/supplier_demo_app.py` | 獨立 ASGI 組裝、模擬樣板及本機限制 |
| `app/routers/suppliers.py` | 列表、詳情、表單及啟停入口 |
| `app/schemas/supplier.py` | 欄位與付款條件驗證 |
| `app/services/supplier_service.py` | 業務規則及交易邊界 |
| `app/db/repositories/supplier_demo_repository.py` | 僅限本機 SQLite 的固定參數查詢 |
| `app/templates/layouts/supplier_demo.html` | 模擬導覽及提示 |
| `app/templates/pages/suppliers/list.html` | 搜尋與列表 |
| `app/templates/pages/suppliers/detail.html` | 詳情及停用確認 |
| `app/templates/pages/suppliers/form.html` | 新增／修改表單 |
| `app/templates/pages/suppliers/_function_list.html` | 每筆供應商詳情的功能勾選列表 |
| `app/static/js/supplier-demo-permissions.js` | 全選、取消、部分勾選與未儲存提示 |
| `app/static/css/pages/suppliers.css` | 試作桌面與手機布局 |
| `tests/supplier_demo/test_suppliers.py` | 離線功能與隔離驗收 |
| `scripts/verify_supplier_demo.mjs` | 本機專用瀏覽器驗收；使用目前電腦的 Codex Playwright 與 Chrome 路徑 |
| `docs/SUPPLIER_DEMO.md` | 操作、保存及未來整合說明 |
| `docs/SUPPLIER_DEMO_TEST_REPORT.md` | 實際測試結果 |
| `docs/REVIEW_FIX_LOG.md` | 在既有紀錄末尾新增本次試作修正紀錄 |

啟動入口依既有靜態分層規範放在 `scripts/`，未新增 `app` 根目錄檔案。
其他原有檔案（包括 `app/main.py`、共用樣板、登入、權限及套件需求）未修改。
