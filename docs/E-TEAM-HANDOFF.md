# 06_E 開發與共同接點交接

更新：2026-10-27。基準是 `D:\2026102001\InvoiceGuard-AI-ADMIN` 的 `2b69083`，本機分支 `codex/06e-admin-v13`。沿用 FastAPI／Jinja2、ADMIN 自有 DB 模組及已核定 v1.3 PortableAuth，未修改其他兩站程式或共用 Schema。

**這批已交付可離線測試的 E 程式與畫面；正式 B／G／journal adapter 尚未提供，因此不是正式資料串接完成版。** 未登入的 E 頁會轉到登入；帶 Cookie 但共同服務未接線時回 503。登入 POST 也會回 503，不再先對舊 users 表更新 last_login_at。不能以 `/dev/accounts` 新增帳號成功當成正式登入已接線。

## 六階段狀態

| 階段 | 本批實作 | 尚缺驗收 |
|---|---|---|
| 1 接點及分支 | 從核定本機 HEAD 建 E 分支；定義 UUID／Session／Access／Auth／Grant／UnitOfWork 契約 | B／G／DB 確認並實作 adapter；沒有 fetch，不能代表遠端最新狀態 |
| 2 registry／guard | E 三項固定能力、逐次職務明確許可、管理資格、操作及 scope 檢查；缺接點拒絕 | 各功能組加入自己的已實作能力與核定政策 |
| 3 部門 | 驗證、列表／新增／修改、SQL repository、交易、版本及 audit 接點、停用影響檢查 | journal 持久化、PostgreSQL 隔離測試與正式權限角色驗證 |
| 4 功能及部門設定 | 功能字典、固定 route／action／分類、版本保護、G 同源矩陣 UI／委派寫入 | G 權威矩陣／可授範圍／失效機制及各組初始清單 |
| 5 登入及本人 | `/login`、`/logout`、本人、忘記／變更密碼 UI；共用認證委派；Auto Login 關閉 | B 共用 Session／寄信／重設完成頁／限流／密碼政策／撤銷的實際接線 |
| 6 驗收 | 已新增離線 HTTP、scope、交易及 SQL adapter 生命週期測試 | 原 E-T01～T10 的跨站真實整合、非 owner DB、資料欄位範圍與競爭測試 |

## 已確認的授權規範

1. 部門與職務分開。職務必須明確允許本次操作，未設定、明確拒絕、未解析衝突均拒絕；不沿用部門許可補足職務缺項，也不以部門永遠封頂職務。
2. `system_management` 必須由權威 adapter 根據「有效系統管理部門成員＋明確指派的管理職務／權限」計算，不由名稱、`role=admin`、前端 Header 或單純部門歸屬判斷。仍須通過本次功能、操作與資料 scope。
3. 功能操作與資料範圍分別授權。總經理先跨部門唯讀；不從職稱推導修改、核准或授權管理權。
4. 本人頁只可修改顯示名稱，不能修改 Email、電話、部門、職務、grants 或帳號狀態。
5. 多職務衝突由 G 按 PM 核定政策先解析；不能提供已解析的明確 allow 時，E 不允許操作。

## 程式入口

| 檔案 | 給誰使用／用途 |
|---|---|
| [schemas/e_admin.py](../app/schemas/e_admin.py) | UUID／Identity／Decision／DataScope／表單 DTO／固定錯誤 |
| [schemas/e_ports.py](../app/schemas/e_ports.py) | B 的 AuthPort、B／G 的 AccessPort、G 的 GrantPort、交易契約 |
| [services/e_registry.py](../app/services/e_registry.py) | 固定能力與 route／action；不是會執行 SQL 的 seed |
| [services/e_authorization.py](../app/services/e_authorization.py) | 各組共用 verified_identity、require_permission |
| [services/e_admin_service.py](../app/services/e_admin_service.py) | 部門／功能／矩陣／本人；get_e_module 是正式組裝入口 |
| [db/repositories/e_repository.py](../app/db/repositories/e_repository.py) | v1.3 固定 SQL、scope 過濾、影響盤點及 JournalPort |
| [services/e_sql_uow.py](../app/services/e_sql_uow.py) | 同一 Session 的 authorize／資料異動／CAS／audit／commit／rollback |
| [routers/e_admin.py](../app/routers/e_admin.py) | 正式 SSR 表單、嚴格同源檢查、Cookie 證據及安全錯誤頁 |
| [routers/auth.py](../app/routers/auth.py) | 沿用原 `/login`／`/logout` 入口，改委派共用認證，不另開登入系統 |
| [tests/unit/test_e_admin.py](../tests/unit/test_e_admin.py) | 僅測試可用的記憶體替身與 HTTP／權限測試 |
| [tests/unit/test_e_sql_uow.py](../tests/unit/test_e_sql_uow.py) | SQL adapter 的離線交易／參數與資料範圍測試 |

`layouts/e_admin.html` 及 `pages/e_admin/` 沿用原網站 CSS 與 Jinja2。E 選單使用同一有效決策，不使用舊版固定 `MENU_ITEMS`。舊 dashboard／invoices 等其他組路由仍是原版 guard，尚未改接 E；不要將新 Cookie 成功發行等同舊頁面也完成 PortableAuth 遷移。

## B：認證與本人流程

- `AuthPort.login(email, SecretStr)` 使用共用 Argon2id／登入限流／帳號鎖定流程，建立同一 UUID 的 opaque Session。不得建立 E 專屬 users、JWT 或第二份密碼資料。
- `AccessPort.authenticate(token)` 每次檢查帳號 active、profile 有效／未離職、Session 效期與撤銷、credential_version、有效選取部門及 ADMIN 適用性。Token 只從 ADMIN Cookie 取得。
- v1.3 沒有 session audience 欄位。`Identity.session_audience='admin'` 是 adapter 的已驗證結果，不能只是把前台 Session 改標籤；B／整合者須提供可靠的管理站用途綁定方案，若需共用 Schema 變更由 API／DB 管理。
- E 先取得 B 發行憑證，再確認 UUID／ADMIN 適用資格；未通過不送 Cookie，並委派撤銷已發行 Session。
- Cookie 為 HttpOnly、SameSite=Lax、Path=/、無 Domain、無 Max-Age／Expires；非本機依既有設定加 Secure。有效時間仍由 B 的服務端 Session 強制控制。沒有 localStorage、sessionStorage 或持久化自動登入。
- `logout` 必須撤銷服務端 Session，不能只清 Cookie。改密碼由 B 驗舊密碼、套用新密碼政策、遞增 credential_version 並撤銷舊 Session，E 清 Cookie 要求重新登入。
- 忘記密碼僅提出 B 的共用申請。B 負責一般化回應、限流、寄信、單次消耗、效期及固定 ADMIN redirect；E 不假寄信、不發另一種 reset Token。寄送後的重設完成頁仍須與 B 對接。
- 本人查詢不接受外部 user_id；回傳 UUID 必須與驗證身分一致。正式 adapter 只更新顯示名稱，並保留必要 audit；不提供任意 profile 欄位透傳。

## G：有效職務與矩陣寫入

`AccessPort.decide(identity, function_code, action)` 回傳的 Decision 必須綁定同一 user_id／session_id／selected_department_id／code／action，包含明確職務結果、目前功能狀態、獨立 scope、政策版本及有效期限。來源故障拋 UnavailableError，不能以允許或空成功取代。

E 不會把 v1.3 `v_user_effective_permissions` 的跨部門 bool_or 當職務權限，也不單靠 `v_user_department_permissions` 決定最終 allow。G 須提供實際職務來源及解析政策。功能停用、app_scope 不適用、部門停用、離職或 grant 撤銷後，下一次驗證必須拒絕；adapter 不得永久快取舊決策。

`GrantPort.read` 提供部門矩陣、集合 revision 及依操作者計算的可新增／撤銷操作上限。`replace` 的語意是「原子更新提交各列；未提交的其他功能列保持原樣」，不是刪掉整個部門所有 grants。E 會檢查重複列、已知操作、可授上限及舊 revision，但 **G 在實際寫入交易內仍須再次重驗**：

- 操作者身分、有效管理職務、目標部門範圍與功能啟用狀態。
- 每項新增及撤銷是否在管理邊界內；不能藉撤銷或更改本人而提權／鎖死最後管理者。
- 集合 revision 的原子比較更新、同交易 audit、撤銷即時性／必要失效事件。

矩陣只代表部門基礎功能，不額外發行職務許可或公司資料 scope。E 不直接寫 `department_permissions`，避免與 G 形成兩套來源。

## DB：journal 與交易

E repository 僅使用現有 departments、app_functions、user_departments、user_accounts、user_sessions 的 v1.3 欄位。沒有 migration、ALTER TABLE、共用 ORM 改動或正式 seed。

v1.3 沒有可供 E 直接使用的完整管理 audit／資源版本表。`JournalPort` 是待共同確認的接點，不是聲稱已存在的 DB 功能：

- `revision(session, kind, id)` 提供持久且不可重複回退的版本；新資源使用約定初始版本。不能拿 credential_version 或 `updated_at` 冒充矩陣集合版本。
- `authorize(session, identity, decision)` 在寫入的同一交易重驗／鎖定當前憑證、管理資格、功能及政策版本，與 B／G 的撤銷寫入協調，避免驗權後被撤權仍寫入。
- `append(session, event)` 原子檢查 expected_revision、保護最後管理資格、寫 actor／目標／before／after／伺服器時間、新版本及必要失效事件；不得另開交易或 commit。
- E service 在資料異動與 append 都成功後才 commit；query、CAS、audit、延遲外鍵或 commit 失敗均 rollback。repository 自己不 commit。

部門停用採保守模式：目前成員、**所有** primary_department_id 參照（含 disabled 帳號）或有效 Session 任一存在即回 409。先由帳號／G 完成移轉／撤銷，再停用；E 不自動改主要部門或刪歷史。B／G 的新增成員／Session 須與停用鎖定策略協調並在隔離庫測試競爭情境。

正式 SQL adapter 是 `SqlUnitOfWork(journal)`，只有取得上述真實 journal 才能在 get_e_module 的伺服器組裝中注入。不要為了顯示畫面接入記憶體替身、owner 連線、固定 allow 或 APP_ENV=local 放行。

## 其他功能組接 guard

1. 由該請求的共用 adapter 取得已驗證 Identity，不從 JSON 或 Header 建構身分。
2. 用程式內固定 Capability 定義 code／category／站內 route／允許 actions，經整合者審查後加入共同 registry。不能接受使用者自訂 definition。
3. 詳情先按安全 repository 查得資源真正 department_id／owner_id，再交 require_permission 檢查 Target；不要信任表單自稱的擁有者。
4. 列表直接把決策的 DataScope 套進 SQL 條件。寫入在同交易再次驗權，下載／匯出／批次每個目標都需檢查，不能只驗選單或第一筆。
5. E 的 DataScope 目前表達 none／own／departments／company；敏感欄位遮罩、核准／匯出等額外操作仍需各組核定並擴充，不能把 company 當成所有欄位與所有操作。

## 初始功能交接清單

| code／領域 | 本批狀態 | 初始 grant |
|---|---|---|
| sys.departments | E 固定 registry；view/create/edit，無硬刪除 | 不自動給任何人 |
| sys.features | E 固定 registry；view/create/edit | 不自動給任何人 |
| sys.department-permissions | E 設定 UI；view/edit，G 寫入 | 不自動給任何人 |
| 財務、採購、帳號、知識庫等 | 各組提供真實 code／route／action／scope 後登錄；不虛構可執行能力 | 未核定，不啟用 |

分類／route／action 不在 v1.3 app_functions 內，本批由固定程式 registry 表達。未知功能可先新增「停用」字典列，不能因此啟用能力或授出未知 action。正式 seed／最初管理者指派由 PM／G／API 在核定環境共同處理。

## 正式接線檢查表

- [ ] B／G／DB 確認本文件及 e_ports.py；提供可用模組／責任人及版本，無須提供密碼或 Token。
- [ ] 在 get_e_module 組裝真實 AccessPort、AuthPort、GrantPort 與 SqlUnitOfWork factory；沒有測試模式設定開關。
- [ ] 取得隔離 PostgreSQL 測試庫與非 owner runtime；驗證實際 Schema、RLS／GRANT、UUID 欄位及延遲外鍵。
- [ ] 核定職務清單、多職務衝突、管理與可授範圍、各功能 code／scope、最初管理者及歷史資料整併。
- [ ] 完成共用寄信／重設完成頁、密碼政策、Session 管理用途綁定及舊站路由移轉。
- [ ] 逐項完成 [E-ACCEPTANCE.md](E-ACCEPTANCE.md) 原 E-T01～T10 真實整合；離線測試不能取代。

本輪未連正式 DB 測 CRUD、未執行 migration／seed、未部署、未 commit／push／merge。開發紀錄見 [E-DEVELOPMENT-LOG.md](E-DEVELOPMENT-LOG.md)。
