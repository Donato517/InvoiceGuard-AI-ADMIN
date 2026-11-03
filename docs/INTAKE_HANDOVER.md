# `/intake` 交接資訊（A 組、F 組）

> 日期：2026-11-02，分支 `feature/admin-real-case-read`。**交接用，不代表正式完成**：ADMIN→API 授權未完成，正式環境一律 503／403。

## 驗證層級

| 項目 | 狀態 |
|---|---|
| Mock E2E（隔離雙服務；Document AI／Groq／Storage 替身；測試專用 Bearer） | 通過：`tests/e2e/test_e2e1_intake_flow.py` 13 案（對 API `origin/main` 複本） |
| 真實瀏覽器（內建瀏覽器＋隔離雙服務） | 通過：PNG 並排、檢核／新增明細後原圖保留、必填與警示出現在接點、API 停機保留輸入 |
| 瀏覽器：JPG／PDF、查重「重複」、窄螢幕 | **未驗證** |
| 真實 Document AI／Storage | **未測** |
| 正式授權（R1） | **未完成** |

## A 組：警示 DOM 接點

- 整頁摘要：`#intake-check-results`，屬性 `data-state`（`unchecked`／`checked`）、`data-required-count`、`data-warning-count`、`data-duplicate`（`yes`／`no`／`unknown`）。子區塊 `#intake-check-required`（blocking）、`#intake-check-warnings`（warning）、`#intake-check-duplicate`，皆有 `data-check-group`、`data-severity`。
- 欄位旁：`.field-notes[data-check-field="<key>"]`，內含 `<p data-check-kind="error|required|warning" data-severity="blocking|warning">`；明細 key 為 `items.N.欄位`。
- 模板：`app/templates/pages/intake/review.html`（`note` macro 與摘要區）；移動位置不需改 router／service。
- API 回應：
  - `check-required`：`{required_complete, missing:[{field, code, message, line_no}]}`
  - `check-warnings`：`{warnings:[{field, code, message}], checked_on}`
  - `check-duplicate`：`{is_duplicate}`（查重需字軌、號碼、賣方統編；缺任一個＝`unknown`，不當成通過）
- 限制：
  - 原圖只在辨識當下的回應內嵌（JPG／PNG 的 data URI），之後靠瀏覽器 sessionStorage；超出容量、無痕或重新開分頁會只剩提示文字。PDF 不內嵌。
  - 沒有伺服器草稿：按「新增明細」或「刪除」後，先前的檢核結果會清空（`data-state="unchecked"`）；完全空白的明細列在送出時略過。
  - 檢核結果是提醒；ADMIN 不改成禁止提交，提交與否由 API 決定。
- 待 A 組調整：警示出現位置與文案（本輪未改）；窄螢幕版面。

## F 組：提交契約與待接功能

- 提交：`POST /intake/review`（`action=submit`）→ `mapping.submit_body(form)` → API `POST /api/v1/invoices`（`InvoiceSubmitRequest`，契約由 `tests/unit/test_intake_api_contract.py` 以 API 真實模型驗證）。
- 提交資料：`recognition_id`、人工修正後欄位、多筆明細（`source_line_no` 對應 AI 原值）。API 端保存 AI 原值與提交值（`invoice_field_reviews`）；ADMIN 不保存檢核結果。
- **實際提交狀態**：API `origin/main` 為 `pending_accounting_review`（`invoice_upload_service.py:103`）；隔離資料庫實測寫入該值。本機 API 檢出若未同步仍為 `pending_department_approval`。ADMIN 顯示使用 `CASE_STATUS_LABELS`，兩者皆可顯示。審核規則本輪未改。
- 原圖關聯：API 於辨識時存入 Storage，路徑在 `ai_recognition_logs.source_storage_path`；提交後案件原圖由 `/cases/{id}/image` 經 `invoice_files` 讀取。
- 授權阻礙：ADMIN Cookie 不能當 API Bearer；`build_intake_client()` 一律 503，直到 ADMIN→API 授權方案（R1，Ed25519 簽章委派）核定與實作（相關研究文件未隨本批交付，見 `docs/ADMIN_INVOICE_INTAKE_PLAN.md`）；`inv.intake` 亦未在正式功能目錄註冊（403）。
- 待接：單位核准／會計覆核／核准／退回、F09、退回補正後重新提交（API 沒有輪次加 1 的端點）。

## 給 F 組的建議（C 組未修改 F 組檔案）

- `app/schemas/finance_review.py:14-19`、`app/schemas/procurement_case.py:15-19` 的註解寫「C 的 API 目前寫入 `pending_department_approval`，日後可能改為 `pending_accounting_review`」。API PR #14 已合併，建議改為「API 已寫入 `pending_accounting_review`；舊值保留相容」。`STATUS_SUBMITTED` 常數是否改值、舊資料是否仍存在，由 F 組決定。
- `app/services/finance_review_service.py:7` 的說明同上。
- `app/routers/invoices.py` 內 `"pending": "pending_department_approval"` 的映射，請 F 組確認是否仍需要。
- 兩個狀態目前都被視為「待財務審核」，功能面不受影響。

## 驗證層級（本批）

- Mock E2E 通過（Document AI／Groq／Storage 替身）≠ 真實 OCR 通過；真實 Document AI 與 Storage 未測。
- JS 原圖還原有 node 假 DOM 測試；真實瀏覽器驗證為前一輪人工操作（PNG），JPG／PDF／窄螢幕未驗證。
- 正式授權未完成：403（`inv.intake` 未註冊）、503（`build_intake_client()`）。
- 隔離資料庫整合測試（`tests/integration/*`）未帶入本批。

