> 2026-10-28 最新版本：登入已改為「驗帳密 → 讀目前部門功能／操作 → 網站 Session → 歡迎頁」。配置入口已恢復登入檢查（LOCAL_SETUP_ENABLED=False）；沒有初始管理員、個別管理職務、交接或新增 DB 表。登入唯一寫入為既有 last_login_at。以 [登入說明](E-LOGIN-ACCESS.md) 為準，以下較早政策及測試為歷史記錄。

# E01／E02 本人有效權限契約：部門模式（第三批）

> 2026-10-27 最新核定：四個配置入口的本機匿名例外已取消，必須登入及驗操作權限；正式資料／交易授權未完成時仍拒絕。本文先前匿名開放及操作紀錄屬歷史，最新入口狀態、驗證與交接待辦以 [E-LOGIN-ACCESS.md](E-LOGIN-ACCESS.md) 為準。部門基本功能／主管委派／scope 規則不變。

> 2026-10-27 第三批最新規則：使用者改採「部門基本功能＋主管授權管理」，主管可管理本部門及外部部門，但僅限負責／明確受委派功能；一般功能不要求額外職務許可，權限以目前選取的有效部門為準。指定本機配置頁暫時免登入、由使用者操作共用正式 DB，代理只做離線測試；這取代本文前期的一般功能角色優先及新入口全部封閉條款。正式 B／G／journal 與主管身分尚待接線，雲端仍關閉。操作、版本及限制見 [E-FUNCTION-PAGES.md](E-FUNCTION-PAGES.md)。

日期：2026-10-27。實作基準為目前 D:\2026102001\InvoiceGuard-AI-ADMIN，分支 codex/06e-admin-v13，開始點 dcad25e。此文件是提供 B／G／整合者審查的 E 端契約擴充，並非宣稱其他組已實作；未更動共用 schema／快照／migration。

## 接點與責任

`app/schemas/e_ports.py` 的 `AccessPort` 新增必要方法：

```python
def effective_access(self, identity: Identity) -> AccessSnapshot:
    ...
```

`AccessSnapshot`、`EffectiveDepartment` 定義於 `app/schemas/e_access.py`。既有 authenticate、decide、AuthPort 及 GrantPort 的職責不變。B／G 可由伺服器端整合 adapter 組成這個接點；E 不推測角色資料表、不另寫授權計算核心、不從 /dev 取資料。

| 欄位 | 來源必須保證的意義 |
|---|---|
| user_id／session_id／selected_department_id | 綁定同一個當次已驗證的 ADMIN 身分；必須與每筆 Decision 一致 |
| departments | 本人現行有效成員關係所對應的部門投影：UUID、code、name、is_active；不含同事資料 |
| policy_version | 非空、代表同一份一致政策快照；所有 Decision 版本相同 |
| valid_until | 有時區且尚未到期；E 對外有效期限取 Session、快照及逐筆決策期限的最早值 |
| decisions | 目前伺服器登錄的完整管理功能，每個 code/action 恰好一筆，包含 allow／deny／unset；不能只傳允許清單 |
| complete | 明確為 True 才表示全部已登錄功能均已解析；缺來源或尚未解析不可宣稱完整 |

E 會移除非現行有效部門，不把跨部門 scope 當成成員關係；選取部門不在有效部門內即拒絕。跨部門唯讀角色可以有 company scope，無須假造加入全部部門。

完整覆蓋集合取自 `EModule.registry`。整合者須讓 G 的快照與該伺服器審查後的 registry 使用相同功能／操作集合。缺項、額外未知項、重複、過期、版本不一致或 unresolved 回 503，不能藉略過無權項目把不完整結果當成成功。不要將不同政策版本逐筆拼成一份快照。

## 有效操作與工作台資格

G 依目前選取的有效部門提供 Decision.department_effect，必須明確 allow；一般功能不要求另外有職務許可，也不聯集其他部門。管理功能另要求 role_effect=allow 與目標 scope；主管的負責／委派來源由 G／帳號組提供，E 不自建角色引擎。department_effect 預設 unresolved，不允許舊 adapter 只回角色結果就相容。

有效項目需同時符合：明確 department_effect=allow、功能啟用、適用管理站、有效身分／部門、可表達且非空的資料 scope。`Capability.management=True` 的能力另驗明確 role_effect。部門／字典維護的 system_only=True 再要求可信 system_management 資格；部門功能授權為 system_only=False，可提供受委派主管，但仍受 G 的可授功能／操作上限、目標部門與獨立資料 scope 限制。功能目錄是公司級資源，還需 company scope。

每個 action 各保留自己的 scope，不把「全公司查看」合併成「全公司修改」。`SelfAccess.functions[].grants[]` 包含 action、顯示名稱、scope 及 scope_label。可用功能投影不取代目標資源驗權：各路由仍需驗 action／Target、列表 SQL scope，寫入仍在同交易重驗權限、版本與稽核。

登入在 B 發行 Session、驗證 ADMIN 用途與 UUID 之後，先取得這份完整投影。沒有任何有效管理操作回 403，委派撤銷剛發行的 Session，且不送 Cookie。已登入但撤銷全部權限，下一次 E 請求亦拒絕，不顯示空工作台假成功。只有 create／edit 許可不隱含 view；仍可顯示獲授的操作，但不因此提供查看連結。

這裡的「管理功能」涵蓋整個 ADMIN 已整合的功能，包含財務／採購等業務功能，並非只指 E 的系統設定三項。整合者可透過 `EModule(registry=...)` 注入各組已實作、已核定的靜態定義；名稱／route／action 不能從 HTTP、字典表單或 grant 提供。其他組若尚未登錄不能聲稱該組可正式登入；也不能只靠 E 三項推定財務人員沒有管理站功能。

財務專用測試僅在測試內加入合成 Capability，驗證無 E 管理資格但有財務查看權的人可登入、查看工作台／本人頁，並仍被 E 部門管理拒絕；不新增正式財務路由或授權。

## 顯示與更新

`EModule.profile` 回傳 `SelfProfile(profile, access)`；基本資料 UUID 再與操作者核對。`/account` 顯示 UUID、Email、顯示名稱、有效部門／目前部門、可用功能及逐操作 scope。唯一可更新的個人欄位仍為 display_name。角色、scope、部門、Email、admin_access、UUID 不能經該表單修改。

`/workspace` 顯示相同功能／操作／範圍投影；有 view 才顯示功能連結。部門授權設定改指向實際 `/system/departments/{department_id}/permissions`，不再借用部門列表權限。固定模板參數只由已驗證的選取部門 UUID 填入；目標仍由該路由再次驗 scope。

HTML 自動跳脫部門／功能名稱，回應 no-store，沒有 Token、角色帳冊或其他帳號資料。請求之間不保留有效權限快取；來源必須反映目前撤權／停用情況，不能永久重用舊決策。系統對併發撤權的完整保證仍依 G／journal 同交易契約與隔離庫驗收。

## 回應與相容性

| 情境 | E 行為 |
|---|---|
| 缺少／無效 Cookie | 既有登入導向；不接受 UUID／角色 Header 代替 |
| 無 ADMIN 用途、識別不符、選取部門無效、完整快照但零有效操作 | 403，不回本人資訊／功能選單 |
| 尚未組裝 adapter、缺少 effective_access 方法、資料不完整／來源故障 | 503，固定訊息、不洩漏內部例外 |
| 完整且有有效操作 | 提供本人投影／工作台；實際業務操作仍各自驗權 |

這是 AccessPort 的必要契約擴充；只有舊 authenticate／decide 的 adapter 不能自動視為已相容。整合時先讓 B／G 審閱，再實作真實 effective_access 並注入 get_e_module。沒有預設 allow、正式測試替身或環境變數放行模式。

## E07 初始清單

[E-INITIAL-FUNCTIONS.json](E-INITIAL-FUNCTIONS.json) 由 `initial_manifest()` 產生，測試會核對它與 registry 一致。只有已知 E 三項定義；全部 is_active=false，沒有任何人員／角色／grant 指派，屬待審查資料，不是執行 seed 的指令。

分類、route、action 留在靜態 registry／manifest；v1.3 app_functions 未有的欄位不自行建表。其他組依其實作提供穩定 code、名稱、分類、路由、action、管理站適用性及 scope；核定後登錄、重產清單並驗收，由負責組處理正式 seed／啟用。不能用 E 的小清單宣稱全專案 E07 seed 已完成。

整體交接仍見 [E-TEAM-HANDOFF.md](E-TEAM-HANDOFF.md)；本批測試及限制見 [E-PHASE2-RESULTS.md](E-PHASE2-RESULTS.md)。


## 2026-10-27｜E 本機功能頁與部門模式（第三批）

- 使用基準：目前 D:\2026102001 的 codex/06e-admin-v13、0ddca22（已含 main）。保留目前分支；本批不 Commit／Push／Merge，不操作正式資料。
- 使用者新核定：目前指定頁暫免登入；限本機 loopback／Host／模式，雲端 404。使用正式 DB，由使用者點擊操作；代理只用封網替身。有效部門成員繼承所選部門基本功能，不聯集多部門。主管可管理本部門與外部部門，但限負責或明確委派功能，操作及資料 scope 分開。
- 新增 /functions 與 /department-functions 的 schema、固定 SQL repository、交易服務、SSR／表單；重開 main 的 /accounts、/department 本機入口，保留原 Host／peer／同源限制；部門列表加入功能設定連結。不是 /dev 串接，沒有另建會員或角色核心。
- 字典新增不授權；未知能力只存停用草稿。矩陣只更新提交列、保留其他列；撤銷保存 false 不硬刪除。短交易檢內容指紋、完整可見性與鎖定；任何失敗 rollback，機密例外固定化。指紋不冒充正式 audit／journal CAS。
- Decision 增 department_effect，預設 unresolved 拒絕。一般功能依所選部門，管理能力另驗明確 role_effect／scope；系統部門／字典維護仍須 system_management。部門功能授權的固定 Capability.system_only=False，讓受委派主管無須系統管理資格，但仍受 G delegable 上限及目標限制。
- 實測：相關 249 passed、1 warning，6.84s；主管邊界補測 E／新頁／registry 159 passed、1 warning，9.81s。最終完整回歸：936 passed、9 skipped、1 warning，126.10s，exit 0。跨三站 checker PASS（0 issues）、Ruff lint／118 檔格式、djLint 30 樣板（0 errors）、environment configuration_valid=true、connectivity_checked=false。
- 本批新增離線案例 47 個；既有來源測試移除臨時閘門 override，真正測本機免登入；原 12 個本機封閉測試改驗雲端仍封閉，依據為使用者明確變更，不放寬共用封網 fixture、checker 或快照。
- 仍未驗：正式 DB 欄位／RLS／鎖競爭、真實瀏覽器操作、B 登入與切換部門、G 主管 owner／委派／資料範圍及正式 audit。未造角色表、owner 欄位、Session、測試模式或操作者 UUID；登入仍可能 503。使用方式及跨組待辦見 E-FUNCTION-PAGES.md。
