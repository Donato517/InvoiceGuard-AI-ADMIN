# 06_E 階段交付：功能管理、部門權限頁與登入閘門

日期：2026-10-27。此說明隨目前 E 階段程式一併提交至 codex/06e-admin-v13，供 PM／組員審查；實際 Commit 與遠端上傳狀態以 Git／GitHub 分支紀錄為準。

## 原因與結論

整合 main 後，E 累積完成了功能列表／內容頁、單一部門權限設定內容頁、桌機／手機登入畫面，以及取消四個配置入口匿名例外的驗權閘門。此次以一份可審查的階段快照交付程式、測試與 docs，避免把畫面完成誤解為正式認證與跨組接線全部完成。

基準：本機 merge 0ddca22 已包含 main 的 cee8dd8。這次更新 E 分支，不修改 GitHub main，不執行 migration 或正式資料寫入。既有文件中的「未 Commit／Push」是開發驗證當時的歷史紀錄；本文件用於本次交付。

## 已實作

- 功能管理：列表、搜尋／平台及狀態篩選、每頁 20 筆、新增／編輯內容頁、代碼固定、停用／恢復、版本衝突及返回條件。
- 單一部門權限：逐操作勾選、不可新增原因、搜尋分頁、未儲存提醒、變更預覽、返回保留當頁草稿及確認儲存；僅更新提交列。
- 登入：依附件外觀的桌機／手機響應式卡片，Email／密碼白名單、同源、固定錯誤、安全回填與受控返回位置；不移植示範帳密或 web storage 身分。
- 入口保護：/accounts、/department、/functions、/department-functions 及子路由取消本機免登入，先驗 UUID／ADMIN Session、有效管理功能及對應操作；缺少正式資料與交易接線仍拒絕。
- 授權契約：目前選取部門的基本功能，主管管理另限負責／明確委派與可授上限，操作和資料範圍分開；沒有聯集全部所屬部門或依職稱自動授予全權。

## 仍待 B／G／DB 整合

正式 B 的 AuthPort、G／B 的 AccessPort、ADMIN Session 用途綁定與完整有效權限仍未交付；G 可授上限、帳號管理正式能力、資料範圍過濾及同交易重驗／journal 仍未完成。登入提交目前仍為 503；四配置入口無 Cookie 會導向登入，接點不齊亦不能操作資料。

不得單純刪除 pending 閘門以便操作。需完成上述接線、隔離測試庫與跨組驗收後再開放。這不是正式上線版本，也不是全站授權隔離完成的宣告。

## 驗證及操作文件

本次程式最近一次完整離線回歸為 1065 passed、9 skipped、1 個既有 TestClient／httpx warning；Node 7 passed。Ruff、格式、djLint、三站 checker 及 environment 靜態檢查通過。桌機 1440×900、手機 390×844／320×568 與手機錯誤卡片完成離線 headless 檢查。未操作正式 DB／Storage／寄信或正式認證服務。

- [登入與入口驗權／跨組待辦](E-LOGIN-ACCESS.md)
- [單一部門權限內容頁](E-DEPARTMENT-PERMISSIONS.md)
- [功能列表與內容頁](E-FUNCTION-LIST-DETAIL.md)
- [逐次開發紀錄](E-DEVELOPMENT-LOG.md)
- [測試報告](TEST_REPORT.md)
