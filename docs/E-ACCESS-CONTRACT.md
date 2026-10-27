# E01／E02 本人有效權限契約：第二批

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

E 不合併職務與部門規則；G 提供已依使用者核定政策解析的 Decision：職務必須明確 allow，未設定拒絕，部門許可不能補足 unset。多職務衝突由 G／PM 核定，來源未解析完成回 unavailable。

有效項目需同時符合：明確 allow、功能啟用、適用管理站、有效身分／部門、可表達且非空的資料 scope。固定 `Capability.management=True` 的 E 管理功能另需可信來源驗證的 system_management 資格；該資格不是請求欄位，也不是僅加入系統管理部門就取得。功能目錄是公司級資源，還需 company scope。

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
