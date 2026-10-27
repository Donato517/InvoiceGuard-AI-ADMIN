# 正式帳號管理

> 2026-10-27 第三批最新規則：使用者改採「部門基本功能＋主管授權管理」，主管可管理本部門及外部部門，但僅限負責／明確受委派功能；一般功能不要求額外職務許可，權限以目前選取的有效部門為準。指定本機配置頁暫時免登入、由使用者操作共用正式 DB，代理只做離線測試；這取代本文前期的一般功能角色優先及新入口全部封閉條款。正式 B／G／journal 與主管身分尚待接線，雲端仍關閉。操作、版本及限制見 [E-FUNCTION-PAGES.md](E-FUNCTION-PAGES.md)。




## 多部門設定增補（2026-10-27）

新增與修改內容頁均可勾選多個所屬部門，並明確指定一個主要部門。主要部門必須包含在勾選項目中，至少保留一個部門。
只允許新指派啟用部門；既有停用部門可保留，不可成為新的主要部門。
新增帳號時帳號、profile、主要部門與全部成員關聯同交易提交，不再先提交預設部門再分開更新。
修改頁另有「儲存部門設定」按鈕，POST `/accounts/{user_id}/departments`，不順帶變更 Email 或密碼。

實際 DB 外鍵已確認：`user_accounts(user_id, primary_department_id, primary_department_active)` 對應 `user_departments(user_id, department_id, is_current)`。
`is_current` 是依 `unassigned_at IS NULL` 自動計算的欄位，不能直接寫入。
取消勾選設定 `unassigned_at=now()`，保留關聯資料列；再次勾選以同一 `(user_id, department_id)` 清除 `unassigned_at`，恢復目前所屬。保留 assigned_at 與其他欄位。
先新增／恢復關聯，再更新主要部門，最後停用未選關聯；同一交易內完成，任何一步失敗全部回滾。
鎖定帳號及選取部門，鎖等待上限兩秒；RLS 可能隱藏既有關聯時拒絕，不把不可見當成不存在。
仍會檢查指向 user_departments 的額外外鍵：除已核對的主要部門外鍵外，遇到未知外鍵就拒絕移除，避免自動連動其他資料。
不修改 department_permissions、權限內容或其他站登入流程。

本次新增 `app/schemas/account_management.py`、`app/templates/pages/accounts/_departments.html`、`tests/unit/test_account_departments.py`；擴充帳號 Router、Service、Repository、detail 模板與既有帳號測試。

### 多部門真實 DB 驗收

最終離線回歸：745 passed、9 skipped；check_project、Ruff check／format --check、djLint 全部通過。僅既有 TestClient/httpx 棄用警告。

- 新增頁 HTTP 200，部門選項讀取既有 DB。
- 用唯一虛構帳號建立兩筆部門關聯並指定主要部門成功。
- 主要部門切換、取消關聯、重新加入同部門及再次取消均成功；既有未變動關聯保留。
- 未勾選的主要部門被拒絕；重新讀取確認主要部門及關聯未變。
- 測試帳號 `igtest_depts_ea11c1ca9faf@example.invalid`（UUID `cddeac1d-b899-4a37-a05d-245515ba1fa8`）維持 disabled；最後僅保留 UNASSIGNED 為目前所屬及主要部門，虛構部門關聯保留且已 unassigned。
- 只操作本次虛構帳號；未修改既有使用者、部門或 department_permissions。
- 瀏覽器目視、確認框點擊及登入／Session 端到端行為未驗證。

## 頁面與行為

- 列表頁：`http://invoiceguard-ai-admin.localhost:8080/accounts`。
- 新增內容頁：`http://invoiceguard-ai-admin.localhost:8080/accounts/new`。
- 修改內容頁：`/accounts/{user_id}/edit`，與新增共用同一份內容模板。

共兩種頁面：列表與內容。列表提供 Email 部分搜尋、完整 UUID 搜尋及每頁 20 筆分頁，顯示 UUID、Email 與帳號狀態。
內容頁提供新增 Email／初始密碼、修改 Email／重設密碼、停用確認及返回列表。
依 2026-10-27 使用者最新指令，管理者可直接修改，不要求目標帳號原密碼；新密碼留空保留現有密碼。
刪除採停用，設定 `account_status=disabled`，不執行 DELETE、不移除部門成員、主要部門或其他關聯。
停用前唯讀檢查真實 DB CHECK 是否明列 disabled；無法核對、缺表或權限不足皆拒絕操作。
操作使用 POST；成功 303 轉址，避免重新整理重複新增。錯誤回填 Email，但不回填、記錄或回傳密碼。
密碼沿用既有 Argon2 雜湊；Email 正規化為小寫、移除頭尾空白。

## 架構與檔案

新增：

- `app/routers/accounts.py`：正式路由、表單及 HTML 回應，與其他功能分開的本機／同源保護。
- `app/services/account_management_service.py`：列表、新增、管理者直接修改、停用、輸入驗證及交易控制。
- `app/templates/pages/accounts/list.html`：列表頁，沿用 ADMIN 版型與表格、按鈕、分頁元件。
- `app/templates/pages/accounts/detail.html`：新增／修改內容頁與停用確認。
- `tests/unit/test_account_management.py`：合成資料、Session 替身測試，不連正式 DB。
- `docs/ACCOUNTS.md`：本文件。

修改：

- `app/db/repositories/user_account_repository.py`：新增管理列表／總數、狀態契約核對及停用方法；原有方法不變。
- `app/main.py`：掛載正式 `/accounts` Router。
- `app/constants.py`：主導覽的帳號管理指向 `/accounts`。
- `docs/DEVELOPMENT_STATUS.md`、`docs/REVIEW_FIX_LOG.md`：狀態及驗證紀錄。

重用 `AccountSummary`、既有 SessionLocal/get_engine、新增帳號 Service 與 Repository update；不新建 ORM、Schema 或 migration。
沒有匯入或依賴 dev Router／Service；沒有修改 `/users` 預留頁、登入、會員、權限或其他站程式。
`/dev/accounts` 原有「修改需原密碼」契約維持原樣。

## 本機與正式部署

正式功能路由不在 `/dev`。延續本次專案本機測試邊界：要求 APP_ENV=local、非 Vercel、module_backend=remote、核定本機 Host 及 loopback 來源；寫入必須有完整同源 Origin。
雲端整個路由回傳 404，避免匿名管理寫入。正式部署前仍需接回 v1.3 管理者授權、操作者與稽核契約；本次未改造舊 users 登入。
既有 Repository 說明密碼變更由 DB Trigger 撤銷 Session；本次沿用該寫入方式，但沒有實際驗證 Session 撤銷或修改登入程式。
停用後的登入／Session 拒絕須由實際登入系統檢查帳號狀態，本次不宣稱已完成端到端登入驗證。

## 帳號初版驗證（2026-10-27；多部門最新結果見上方）

- 全套離線 pytest：721 passed、9 skipped；僅既有 TestClient/httpx 棄用警告。
- 相關帳號／部門／dev_accounts：110 passed。
- check_project.py、Ruff check、Ruff format --check、djLint 全部通過。
- environment --check 通過；此為設定檢查，不代表連線成功。
- 本機實測嘗試：`/accounts` 回傳 HTTP 502、`Local platform is unavailable.`；新增與修改 DB 未驗收，本次未成功建立虛構帳號。
- 真實 DB 的 disabled 狀態、停用效果、密碼重設後 Session 行為、瀏覽器目視及確認框點擊均未驗證。
- 沒有部署、提交、推送、硬刪除、Schema 修改或真實使用者資料修改。
