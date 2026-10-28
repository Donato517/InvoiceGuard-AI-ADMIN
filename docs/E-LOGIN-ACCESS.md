# ADMIN 登入畫面與配置入口驗權

> 2026-10-27 階段交付：此份文件與目前 E 成果一併提交至 codex/06e-admin-v13，見 [交付摘要](E-DELIVERY-20261027.md)。下文未 Commit／Push 的敘述屬開發當時紀錄；實際提交與上傳狀態以 Git／GitHub 為準。正式 B／G 與資料交易接線仍待完成。

更新：2026-10-27。開發基準為 D:\2026102001\InvoiceGuard-AI-ADMIN 的現有 E 工作目錄，保留已合併 main 及未提交成果。本批未 Commit／Push、部署、執行 migration 或操作正式 DB。

## 本次核定與結果

使用者確認：ADMIN 同一登入頁支援桌機與手機；B／G 新版模組尚未提供，先完成 E 的畫面與接點測試；取消 /accounts、/department、/functions、/department-functions 的本機免登入例外。

已完成附件外觀的響應式登入卡片、登入失敗回填安全欄位、四個配置入口及子路由的登入／操作驗證前置檢查。正式登入與配置 CRUD 尚未開放；沒有以示範帳號、舊 BIGINT users、記憶體認證或瀏覽器儲存解決缺接線問題。

## 設計依據與操作

- 桌機參考 invoiceguard_final_formatted.html 的藍色漸層背景、白色圓角卡片、品牌文字與登入按鈕。附件的 JavaScript 示範帳密、工程師全權及 sessionStorage 自動恢復身分未採用。
- InvoiceGuard_v4_8.html 沒有登入表單；依使用者確認，採桌機卡片的手機響應式版本，參考手機原型的字體、圓角與按鈕。只改 ADMIN，不修改其他組負責的 FRONTEND／API。
- 登入入口：http://invoiceguard-ai-admin.localhost:8080/login 。表單維持 Email、密碼、忘記密碼；不新增 username 資料欄位、角色選單、示範帳密或「記住我」。
- 正常顯示表單不需要 B／G 接線。提交時目前仍回 503，錯誤留在同一登入卡片內，保留 Email、清空密碼；錯誤密碼／不存在帳號使用相同提示。
- 已存在開發帳號，不代表 B 的 Session 與 G 的權限接點已完成；不需要藉由重建帳號嘗試解決 503。

## 四入口目前的行為

| 情況 | 結果 |
|---|---|
| 非核定本機環境／Host／peer | 維持 404，配置資料服務不被呼叫 |
| POST 缺可信同源證據或跨來源 | 403，先於表單及認證處理拒絕 |
| 無 ADMIN Cookie | 303 導向 /login，不能匿名讀寫 |
| 有 Cookie，但 B／G 未組裝 | 503，不信任 Cookie 本身或角色 Header |
| Session 無效或過期 | 導向登入並清 Cookie |
| 前台用途、無管理站資格、功能／操作或資料範圍不符 | 403 |
| 合成測試接點確認有登入與頁面操作許可 | 仍為 503，正式資料／交易授權未完成，不自動開放 |

### 為何頁面驗權通過後仍保持關閉

這四個配置服務先前在明確的本機匿名例外下開發，尚未具備正式的 SQL 資料範圍過濾、G 可授上限、同交易重驗／稽核與最後管理資格保護。只驗證登入後呼叫原本的完整列表或直接寫入，無法保證資料及權限隔離。因此 require_configuration_access 先驗證身分、有效工作台及明確操作，再保留 pending 整合拒絕。

這不是已完成正式 CRUD 或跨組授權的宣告。待收到 B／G 後，需完成資料與交易接線及驗收，再移除 pending；不可單獨刪除拒絕、改成 return 或啟用本機繞過。

## 權限與資料接點

- 身分只從 ADMIN 的 opaque Cookie 交給 AccessPort.authenticate 驗證。current_identity 每次請求重讀完整有效權限，不接受 URL、X-Role、X-User-ID 自稱身分。
- 功能目錄使用 sys.features；部門使用 sys.departments；部門矩陣使用 sys.department-permissions。VIEW、CREATE、EDIT 依固定路由決定；狀態切換也是 EDIT。配置頁會呈現原資料，因此建立／修改預覽也要求 VIEW，不能把 CREATE／EDIT 自動當作 VIEW。
- 部門／矩陣目標由 UUID 路徑或選定部門參數取得，仍交給 scope 驗證；無特定目標的完整列表與功能字典按全域資源處理。這不取代後續 SQL scope 過濾。
- 帳號管理的正式功能代碼及資料管理範圍尚未由帳號／G 組核定，不擅自造 sys.accounts，也不借用部門／功能目錄許可。無可信系統管理資格時拒絕；有資格仍等待正式接線。
- 既有部門基本功能、目前選取部門、主管負責／明確委派功能與獨立資料範圍規則不變。本機例外取消不會把部門成員自動升級成主管。
- /dev 示範檔及其他組的舊路由不屬於這次四入口遷移。不能將本批描述成整個專案的登入、Session 或驗權遷移全部完成。

## 登入及安全契約

- LoginInput 只收 email、password、next；拒絕額外角色／UUID、重複欄位、格式與長度錯誤。Email 上限 320 字、密碼上限 1024 字；密碼不 trim、不回填、不記錄。密碼政策、雜湊、鎖定與限流仍由 B 負責。
- AuthPort.login 使用同一核定認證核心；E 驗證 UUID、ADMIN 用途、有效部門及完整有效管理功能。缺功能或資格時撤銷剛發出的 Session，不送 Cookie。沒有新的 E 專屬認證資料表或 API 代理。
- 成功 Cookie 為 HttpOnly、SameSite=Lax、Path=/、無 Domain、無 Max-Age／Expires；正式環境 Secure，Session TTL 與撤銷由 B 強制。未新增任何 web storage 自動登入。
- next 僅允許固定站內入口與核定 registry 路徑；其他值回 /workspace，不接受外站、/dev 或任意 Query String。詳細頁未列入返回白名單時亦回工作台。
- 回應 no-store／noindex，HTML 自動跳脫。失敗只回固定訊息；不輸出密碼、Token、SQL 或其他人的資料。新 CSS 僅登入頁載入，不更動全站版型或外部 CDN。
- 忘記／變更密碼與登出仍沿用 B 的原有契約，沒有新增第二種 Token 或假寄信；這些正式接點仍待交付。

## 實作與測試位置

| 檔案 | 用途 |
|---|---|
| app/routers/auth.py、app/schemas/e_login.py | 登入白名單、同源、錯誤卡片與 Cookie 流程 |
| app/templates/layouts/e_login.html、app/templates/pages/auth/login.html | 共用桌機／手機登入卡片 |
| app/static/css/pages/e-login.css | 僅登入頁使用的響應式設計變數與樣式 |
| app/routers/e_admin.py | require_configuration_access，預檢後仍保持 pending |
| app/routers/accounts.py、departments.py、e_function_settings.py | 四入口及所有子路由掛上同一閘門 |
| tests/unit/test_e_login_access.py | 真實閘門、合成 B／G；無配置閘門 override，資料服務呼叫即失敗 |

五份既有配置 UI 測試在各自 fixture 內明確隔離配置閘門，以保存畫面與交易服務回歸；不代表匿名放行。原有 Host／同源檢查仍實際執行，共用封網 fixture 沒有放寬。新閘門測試另外覆蓋各入口及子路由，在拒絕前不觸及配置資料服務。

## 本批驗證

最終完整回歸 1065 passed、9 skipped、1 個既有 TestClient／httpx warning（104.78s，exit 0）；新增 56 個登入／閘門案例。初輪相關 378 passed，補測登入與一般頁面 71 passed（3.53s）。Node 未儲存提醒 7 passed；Ruff lint／121 檔格式、djLint 35 樣板（0 errors）、environment 靜態檢查均通過（connectivity_checked=false）。離線 headless 瀏覽器驗證 1440×900、390×844、320×568 及手機 503 畫面，無橫向溢出、按鈕高度至少 44px、密碼空白，已檢視桌機與手機截圖；不是正式站台端到端驗收。

## 交付 B／G／整合者的待辦

1. 提供 AuthPort、AccessPort 對應的正式核定模組及版本；ADMIN 使用共同認證核心，不跨目錄臨時匯入其他站或串接 /dev。
2. 確認 ADMIN Session 用途綁定、UUID、credential_version、有效／目前部門、撤銷與 TTL；既有前台 Session 不能直接改標籤成 ADMIN。
3. 提供完整有效管理功能決策、帳號管理正式能力、主管委派與 G 可授上限及逐操作 scope。
4. 將這四個配置資料服務接上範圍過濾、同交易重驗／journal、稽核與保護規則；在獨立核定測試庫完成跨部門與併發撤權驗收後再開放。
5. 正式登入、真實 Cookie／寄信／DB／RLS／撤銷端到端尚未驗收；本批僅完成 E UI、守衛前置契約與離線證據。成果仍未 Commit／Push。
