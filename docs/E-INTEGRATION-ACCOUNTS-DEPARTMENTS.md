# 06_E 整合最新 main

日期：2026-10-27。工作目錄 D:\2026102001\InvoiceGuard-AI-ADMIN，保留 codex/06e-admin-v13；沒有切換到 main 覆蓋 E。

## 原因與合併來源

使用者先要求整合 feature/admin-accounts-departments，隨後指定改以最新 main 整合。fetch 後 main 為 cee8dd8（Merge pull request #6），已包含帳號／部門功能的 squash 提交 a48c541。原功能分支 a1c784d 與 main 的檔案樹完全相同（git diff --quiet exit 0），但提交歷史不同，因此最終合併父提交採 main，不另把原功能分支歷史混入。

E 第二批以 b392653 本機 Commit 先保存：本人有效部門／功能／逐操作 scope、工作台資格、目錄反例及初始清單。E 專屬 126 passed；保存前全套 787 passed、9 skipped。遠端 E 尚未更新，本次不 Push。

合併在暫存工作目錄完成，先審閱並封閉來源匿名入口，確認後才更新 D 槽工作目錄。唯一文字衝突為 DEVELOPMENT_STATUS，保留雙方歷史並標示目前規則；未用整份 ours／theirs 覆蓋來源程式。

## 合入什麼、可以怎麼理解

| main 內容 | 與 E 的關係／目前狀態 |
|---|---|
| 帳號列表、新增、修改與停用 | 保留來源服務、repository、模板及測試；管理者重設別人密碼不屬於 E 本人顯示名稱更新 |
| 多部門與主要部門設定 | 提供 UUID／membership 資料存取程式，可供共同 adapter 審閱；不等於已具備角色或有效 scope |
| /department 部門管理 | 保留來源碼；與 E /system/departments 是兩個入口，尚未合併為單一服務，不能同時各自宣布正式授權完成 |
| main 掛載及原選單 | 保留來源整合；新入口在本分支受待接線閘門封閉 |
| E 現有 registry／guard／本人頁／工作台 | 完整保留，B／G／journal 未接線仍拒絕，不接回舊 int Cookie |

合併 Git 分支只是取得程式與歷史，不會執行 SQL、修改 DB table、匯入實際資料或配置登入。沒有抓取其他人的帳密，也沒有建立正式帳號。

## 已核定的安全調整

來源 /accounts、/department 原先在核定 loopback／Host 允許匿名讀寫正式 DB。使用者明確核定：保留程式，但在 E 身分／職務／scope 接妥前先封閉。

兩個 Router 均新增 Depends(require_pending_admin_integration)。該依賴固定拋出 E UnavailableError，沒有環境開關、假角色、允許清單或測試模式。來源本機／Host／同源限制亦保留。

- 核定本機的全部帳號及 /department 讀寫路由：503、no-store，不呼叫業務服務／DB，不發 Cookie。
- 雲端或不可信來源：沿用原先 404；不因 E 登入而自動開放。
- E /system/departments、/system/features 等正式路由：維持原有身分、功能及 scope 檢查。
- /login 的正式 B／G 接點仍缺，這次合併不代表登入 503 已排除。

新增 12 個不覆寫閘門的路由反例，核對包括新增帳號、修改／停用／指派部門、部門名稱／代碼／狀態等操作皆無業務呼叫。來源的三組業務測試 fixture 只在測試內明確覆寫待接線閘門，繼續驗模板／交易／同源；此類成功案例不能當成正式授權驗收，也沒有修改共用測試封網規則。

## 實際驗證

暫存合併的 E＋來源帳號／部門測試：228 passed、1 warning，12.43s。完整離線回歸：888 passed、10 skipped、1 warning，135.37s。暫存目錄不具三站兄弟目錄，正式 D 槽另核對跨站契約及驗證；所有測試仍封鎖外部連線。

暫存版本 check_project PASS、0 issues；Ruff lint 通過、113 個 Python 檔格式通過；djLint 28 樣板、0 errors；environment configuration_valid=true、connectivity_checked=false。沒有新增相依套件或呼叫正式服務。

D 槽實際合併後回歸：889 passed、9 skipped、1 warning，124.72s，exit 0；跨三站 checker PASS（0 issues），Ruff lint／113 個檔案格式、djLint 28 樣板及環境靜態檢查通過。所有測試使用既有封網 fixture；未執行正式 DB 操作。 不能把來源作者的正式 DB 歷史實測當成此次重新驗收。

## 後續只做 E 的整合步驟

1. B／G 審閱 E-ACCESS-CONTRACT.md：沿用 UUID 與多部門模型，提供 ADMIN Session、有效職務／逐操作 scope 與完整功能快照；不能用帳號 active 或部門成員資格代替職務許可。
2. PM／整合者決定部門正式入口與單一服務；核對保留 UNASSIGNED、代碼長度（來源 200／E 100）、停用外鍵／歷史 grants 規則、版本及 audit。這些差異沒有在本次猜測合併。
3. 帳號負責組提供帳號功能的 code／action／scope 與管理重設／停用的安全契約。E 不自行將帳號管理列為所有管理者可用，不把 membership 修改等同職務指派。
4. DB／API 提供隔離庫、非 owner runtime 與 journal；正式角色／scope／CAS／撤銷驗收完成後，才以真實 guard 取代固定拒絕閘門。不直接刪除閘門作為修復 503。
5. 驗證 E-T01～E-T10 及各組路由／資料範圍一致性，再由使用者決定 Push／PR。

本次沒有部署、Push、migration、seed 或正式資料操作。Git 合併完成與功能可正式使用，是兩個需分別記錄的結果。
