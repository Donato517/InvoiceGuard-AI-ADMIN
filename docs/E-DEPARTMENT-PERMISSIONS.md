# 單一部門的權限設定內容頁

> 2026-10-28 最新整合：部門內容與四項授權功能改為同頁儲存；獨立部門功能頁移除，舊 GET 僅轉址、舊 POST 停用。未接線功能可保存配置。詳見 [E-DEPARTMENT-DETAIL.md](E-DEPARTMENT-DETAIL.md)。下文舊矩陣 UI 流程僅供歷史追溯。


> 2026-10-28 最新核定：本機初始化配置暫免登入，新增 /welcome；雲端維持關閉、正式 B／G 尚未完成。操作及恢復方式見 [E-LOCAL-SETUP.md](E-LOCAL-SETUP.md)。下文先前全面關閉規則為歷史狀態。


> 2026-10-27 最新核定：四個配置入口的本機匿名例外已取消，必須登入及驗操作權限；正式資料／交易授權未完成時仍拒絕。本文先前匿名開放及操作紀錄屬歷史，最新入口狀態、驗證與交接待辦以 [E-LOGIN-ACCESS.md](E-LOGIN-ACCESS.md) 為準。部門基本功能／主管委派／scope 規則不變。

更新：2026-10-27。基準為 D:\2026102001\InvoiceGuard-AI-ADMIN。本批保留現有 E 分支、main 整合與未提交成果，尚未 Commit／Push／部署。

## 原因與本批結論

原部門功能頁已有多功能勾選與部分列儲存，但缺少搜尋分頁、不可勾選原因、未儲存提醒及儲存前摘要。現在沿用 /department-functions，完成「編輯 → 預覽變更 → 確認儲存」，並在最後寫入時重新核對原內容指紋。

本批只完善 E 的單一部門配置頁；沒有新增角色管理核心、主管委派來源、資料範圍欄位或共用 Schema／migration，也沒有變更正式 /login 或 /system/* 的驗權規則。

## 使用順序

1. 使用原 START 啟動 ADMIN，開啟 http://invoiceguard-ai-admin.localhost:8080/department-functions 。也可由部門列表的「設定功能」進入。
2. 選設定對象部門，按「讀取設定」。此選擇不會更換任何人的登入部門。
3. 依功能代碼／名稱搜尋，並以平台、功能啟用狀態篩選。每頁 20 筆，顯示總數與上一頁／下一頁；搜尋會回第一頁，超出末頁時校正。
4. 勾選查看／新增／修改／刪除。四種操作獨立，修改不會自動授予查看。取消全部表示撤銷該功能的全部操作，不刪資料。
5. 按「預覽變更」，逐功能查看原設定、變更後、新增與撤銷的操作。預覽不會寫入 DB；沒有實際差異時不提供確認儲存。
6. 按「返回修改（保留勾選）」可回當頁修改，保留原內容指紋及搜尋條件；按「確認儲存」才執行短交易寫入並返回原部門、搜尋、篩選與頁碼。
7. 要放棄時按取消／重新載入。已勾選變更或停留在未儲存摘要時，啟用 JavaScript 的瀏覽器會在離開、搜尋、換頁或切換部門時提示；確認離開即放棄未儲存內容。勾選還原成已儲存值後不再提示。

草稿不跨頁累積，不放 Cookie、Session、localStorage 或 sessionStorage。搜尋／換頁前先完成當頁儲存；只更新提交的功能，其他頁、被篩掉的功能及其他部門不被清除。停用部門僅供查看。

## 畫面如何解讀

| 欄位 | 意義 |
|---|---|
| 功能名稱／代碼 | 來自既有 app_functions，在此頁不能修改字典 |
| 平台／狀態 | 區分管理站、前台、兩者與功能啟用狀態 |
| 四項操作勾選 | 本次編輯的部門基本操作設定 |
| 已儲存設定 | DB 目前保留的操作，不把未儲存草稿當成既成事實 |
| 啟用及程式支援條件下的操作 | 僅反映部門／功能啟用、平台及 registry 支援條件，不是完整有效權限 |
| 不可新增原因 | 部門停用、功能停用、不適用管理站、能力未登錄或功能未提供該操作 |

對停用／未登錄等功能，原有許可仍顯示，且有效部門可撤銷；不能新增超出目前程式支援的許可。停用不等於清除授權，重新啟用可能恢復原設定的作用。E 未提供實體刪除，不因 permission 表具有 can_delete 欄位而自動開放此操作。

資料範圍、主管 owner／明確委派、可授上限與操作者身分仍待正式 B／G／API 接點。一般功能使用與授權管理資格分開；本機免登入頁無法宣稱已驗證誰是主管，表格的基本操作概況也不能當成全公司資料存取許可。

## 路由及提交契約

| 方法／路徑 | 行為 |
|---|---|
| GET /department-functions | 以 department_id 選單一部門；q／platform／status／page 控制顯示 |
| POST /department-functions/{department_id}/preview | 驗證當頁提交，產生差異摘要，不寫入 |
| POST /department-functions/{department_id}/draft | 由摘要返回編輯，保留合法勾選，不寫入 |
| POST /department-functions/{department_id} | 明確確認後，鎖定並重新驗證版本，更新提交列 |

- POST 只接受 expected_revision、function_id 多值、grant 多值（UUID:action）及 return_q／return_platform／return_status／return_page。最後儲存另外要求 confirmed=true；舊程式若直接提交儲存需配合此契約。confirmed 只是確認流程欄位，不是登入或授權憑證。
- 一次提交 1～20 個不同 function UUID；不接受未知操作、grant 不在提交目標、重複值、額外身分／角色欄位或任意返回網址。設定對象只取自 UUID 路徑。
- matrix_changes 是預覽與寫入共用的變更驗證；最後儲存不信任預覽結果或隱藏欄位，會重新讀取並驗證所有提交操作。內容指紋涵蓋原完整部門矩陣，不只篩選後的畫面列。
- 搜尋及分頁在既有矩陣投影上處理，DB 仍沿用原本查詢整個選定部門的方式；不宣稱本批已做 DB 分頁最佳化。平台採精確分類，both 不會混入 admin 篩選；文字搜尋不把 %／_ 當萬用字元。
- 輸入或未知目標操作回 400，查無部門回 404，原內容變更或部門停用時回 409，服務／DB／鎖／RLS 問題回 503；成功為 303 返回列表條件。
- 409 或可呈現的服務錯誤會保留原表單指紋與勾選、阻擋再送出，提供重新載入。解析錯誤只回固定錯誤，不回顯夾帶欄位。若 DB 無法重讀，顯示錯誤及重載入口，不假裝是空清單。
- 提交或 commit 出錯不能推定 DB 絕對沒有提交；必須重讀核對。重載可能捨棄草稿，不默默把舊稿套到新版本。內容指紋仍不是持久稽核／journal，也不能防止所有 ABA 情況。

## 安全與驗證邊界

沿用核定的暫時本機免登入：local、非 Vercel、remote、loopback peer、固定 Host 同時成立；雲端關閉。每一個 POST 都驗同源；HTML 自動跳脫、回應 no-store／noindex，不新增外部請求或任何機密設定。

JavaScript 只做未儲存提醒，不 fetch、不存草稿。未啟用 JavaScript 時仍可透過伺服器表單完成預覽、返回與確認，但沒有離開提醒；頁面已有提示。前端確認與 disabled 不能代替後端驗證。

## 本批離線驗證

相關 Python 測試 114 passed、1 個既有 warning（10.27s）；完整回歸 1009 passed、9 skipped、1 個既有 TestClient／httpx warning（104.11s，exit 0）。新增 32 個 Python 案例；Node 未儲存提醒 7 passed、0 failed（DOM 替身）。跨三站 check_project PASS（0 issues）、Ruff lint／119 檔格式、djLint 34 樣板（0 errors）、environment 靜態檢查通過（connectivity_checked=false）。不連真實 DB 或網路。

重現本批相關測試（從 ADMIN 專案根目錄執行）：

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests/unit/test_e_function_settings.py tests/unit/test_e_matrix_pages.py -q -p no:cacheprovider
node --test tests/js/test_e_department_permissions.cjs
```

其他完整檢查沿用 AGENTS.md「無公司密鑰驗證」及 djLint。未升級依賴，未放寬封網 fixture、檢查器或跨站快照。

## 人工待驗與交接

本批代理沒有啟動或瀏覽連接正式資料庫的頁面，沒有正式資料讀寫驗收，也沒有執行 migration。START 使用的是共用正式 DB，以下寫入驗收由使用者執行：

1. 確認目前 ADMIN 載入本次版本，選部門、搜尋、篩選、切頁，核對唯讀與不可勾選原因。
2. 使用核准測試對象修改一項，確認預覽不改資料、返回保留勾選；確認儲存後再重讀結果。
3. 驗證撤銷只影響提交列，其他頁與其他部門保留；以兩個視窗確認衝突不覆寫。
4. 實際瀏覽器驗證離開提醒、取消確認與返回後提醒恢復；Node DOM 替身測試不代表瀏覽器視覺驗收。
5. 正式上線前仍需 B／G 的主管身分、委派與資料範圍、持久稽核及正式 DB／RLS／並行交易整合驗收。
