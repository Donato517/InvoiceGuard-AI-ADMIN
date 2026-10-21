## 變更說明

<!-- 這個 PR 做了什麼、為什麼要做 -->

## 變更類型

- [ ] feat：新功能
- [ ] fix：修正錯誤
- [ ] refactor：重構（不影響功能）
- [ ] style：樣式調整
- [ ] docs：文件
- [ ] chore：設定 / 套件 / 建置

## 關聯 Issue

<!-- 例：Closes #12 -->

## 測試方式

<!-- 如何驗證：操作了哪些頁面與情境、Vercel Preview 網址 -->

## 畫面截圖

<!-- 畫面變更時附上修改前 / 後截圖 -->

## 檢查清單

- [ ] 程式碼與樣板備註符合 `docs/CODING_CONVENTIONS.md`
- [ ] `ruff check .`、`djlint app/templates --lint`、`pytest` 通過
- [ ] 同步 Model 時：已對照 InvoiceGuard-AI-API 的定義，並在異動紀錄註明 API revision
- [ ] 沒有在本專案新增 migration 或手動變更資料表結構
- [ ] 會變更資料的操作已寫入 `audit_logs`
- [ ] 新增環境變數時：已更新 `.env.example`、`app/core/config.py`、`docs/ARCHITECTURE.md`
- [ ] 沒有對資料庫資料使用 `|safe`
- [ ] 沒有提交任何金鑰、`.env` 檔
