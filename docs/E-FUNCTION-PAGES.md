> 2026-10-28 最新版本：登入已改為「驗帳密 → 讀目前部門功能／操作 → 網站 Session → 歡迎頁」。配置入口已恢復登入檢查（LOCAL_SETUP_ENABLED=False）；沒有初始管理員、個別管理職務、交接或新增 DB 表。登入唯一寫入為既有 last_login_at。以 [登入說明](E-LOGIN-ACCESS.md) 為準，以下較早政策及測試為歷史記錄。

# E 功能頁：本機操作與新版部門規則

> 2026-10-28 啟用修正：目錄啟用與 registry／正式驗權分開；自行建立的代碼可啟用目錄，待接線另行標示，不自動授予部門權限。見 [E-FUNCTION-ACTIVATION.md](E-FUNCTION-ACTIVATION.md)。下文未登錄只能停用的規則是歷史狀態。


> 2026-10-28 最新整合：部門內容與四項授權功能改為同頁儲存；獨立部門功能頁移除，舊 GET 僅轉址、舊 POST 停用。未接線功能可保存配置。詳見 [E-DEPARTMENT-DETAIL.md](E-DEPARTMENT-DETAIL.md)。下文舊矩陣 UI 流程僅供歷史追溯。


> 2026-10-28 最新核定：本機初始化配置暫免登入，新增 /welcome；雲端維持關閉、正式 B／G 尚未完成。操作及恢復方式見 [E-LOCAL-SETUP.md](E-LOCAL-SETUP.md)。下文先前全面關閉規則為歷史狀態。


> 2026-10-27 最新核定：四個配置入口的本機匿名例外已取消，必須登入及驗操作權限；正式資料／交易授權未完成時仍拒絕。本文先前匿名開放及操作紀錄屬歷史，最新入口狀態、驗證與交接待辦以 [E-LOGIN-ACCESS.md](E-LOGIN-ACCESS.md) 為準。部門基本功能／主管委派／scope 規則不變。

> 本批完成單一部門權限內容頁：搜尋／篩選／分頁、不可新增原因、未儲存提醒與預覽／確認儲存。最新操作及提交契約見 [E-DEPARTMENT-PERMISSIONS.md](E-DEPARTMENT-PERMISSIONS.md)；下列歷史驗證不取代本批紀錄。

> 本批新增列表／內容頁、搜尋、篩選、分頁及獨立狀態操作。最新使用與驗證見 [E-FUNCTION-LIST-DETAIL.md](E-FUNCTION-LIST-DETAIL.md)；下列保留第三批配置規則及歷史驗證。

更新：2026-10-27。基準為 D:\2026102001\InvoiceGuard-AI-ADMIN、codex/06e-admin-v13，已合併 main 的 0ddca22。這批程式尚未 Commit／Push；不是已部署版本。

## 使用者核定的本批規則

1. 指定設定頁暫時免登入，僅本機使用；雲端維持 404。使用 START 現有共用正式 DB，由使用者在頁面操作；代理只做合成資料／替身測試，不執行正式讀寫驗收。
2. 有效部門成員直接取得目前選取部門的基本功能；不再要求一般功能一定有職務角色許可。切換部門才切換該部門許可，不合併其他部門權限。
3. 管理職務與一般操作分開。主管可管理本部門及外部部門的設定，但只限負責或系統管理員明確委派的功能；不能因此管理所有系統功能。這取代早前「主管不能調整本部門基本權限」的提案。
4. 功能操作與資料範圍仍分開；部門 can_view=true 不等於可讀全公司財務資料。系統管理資格與可授上限不由一般部門許可取代。

免登入頁無法辨識操作者身分，因此本批是使用者核定的本機配置工具，不宣稱已驗證誰是主管。正式 owner／委派／scope 仍須 B／G／API 資料接點；E 沒有猜造角色表、owner 欄位或假操作者 UUID。

## 操作入口及順序

| 用途 | 本機 URL | 可操作內容 |
|---|---|---|
| 功能列表與內容頁 | http://invoiceguard-ai-admin.localhost:8080/functions | 搜尋／平台與狀態篩選／分頁；獨立新增、修改、停用／恢復；已建立 code 固定 |
| 部門功能 | http://invoiceguard-ai-admin.localhost:8080/department-functions | 選部門，一次勾選多功能的獨立查看／新增／修改／刪除旗標 |
| 部門 | http://invoiceguard-ai-admin.localhost:8080/department | 沿用 main 新增／修改；每列增加「設定功能」連結 |
| 帳號 | http://invoiceguard-ai-admin.localhost:8080/accounts/new | 沿用 main：新增帳號、勾多個部門並指定其中一個主要部門 |

1. 先在部門管理確認設定對象存在且啟用；需要時由使用者新增部門。
2. 到功能列表按「新增功能」進入內容頁；輸入框提供目前已登錄的代碼候選。分類／固定 route／action 由程式 registry 審閱維護，不在一般列表提供可編輯欄位。已知 E 功能為 sys.departments、sys.features、sys.department-permissions；不會自動建立或啟用任何資料列。
3. 新功能預設停用。未登錄的其他組 code 可先保存停用草稿；等對方提供並審閱真實 route／action 後再加入 registry。只有字典列不能產生可執行路由。已登錄且適用管理站的能力，使用者才可勾選啟用。
4. 到部門功能頁選定一個部門，搜尋／篩選當頁功能並勾選各操作，先預覽變更摘要，再確認儲存；返回修改可保留當頁勾選。不存在／未實作／停用的操作不可新增授權；E 未實作硬刪除，所以對應刪除欄不可新增勾選。原有許可可撤銷，不默默刪掉其他組未提交的列。
5. 到新增帳號頁勾選對應部門並指定主要部門。這只是帳號成員設定，不是主管指派；也不自動發行登入 Session。登入後的目前部門由 B 的有效 Session 管理，不能拿本頁選擇的「設定對象」冒充。

本批沒有改套件、不需要重新 SETUP。START 依原機制重載 app 程式；若仍看到舊版，由使用者依 ADMIN 視窗原啟動指令重啟本站。沒有以真實瀏覽器／正式 DB 驗上述入口，離線 TestClient 的 200 不等於已通過正式 CRUD。

## 資料與失敗行為

- 功能只寫 public.app_functions 的既有欄位；部門功能只寫 public.department_permissions，四種操作各自獨立。帳號／多部門沿用 main 的原交易，不另建會員系統。
- 矩陣保存採「只更新提交列」；全部取消保存 false，不刪字典、部門或其他功能列。功能停用保留 grants，由有效權限來源的 is_active 條件排除。
- 本機服務直接寫共用矩陣是本批使用者核定的例外，並非正式 GrantPort／G 引擎已接線。G 後續沿用同一張表，沒有第二份本機權限資料來源。
- 字典 code 修改被拒。空白／控制字元／超長／額外 role、route 等欄位回 400；資料不存在 404；重複代碼／舊表單內容衝突回 409；資料庫、RLS 可見性／鎖定或未設定連線回 503，不假成功。
- 內容指紋可偵測表單讀取後的資料變化。它不是持久管理稽核、不可回退歷史版本或正式 journal CAS，不能拿來宣稱 E-T08 已正式驗收。
- 矩陣交易有兩秒取鎖／五秒 statement 上限，短暫鎖字典與權限表，避免同時更新或新增 grant 列互相覆蓋。大量流量下需正式 G／DB 共同策略，不以此暫行全表鎖取代正式設計。
- 保守檢查三表的 row_security_active；RLS 可能隱藏資料時拒絕本機寫入，不改 Policy、不切 owner、不提升 DB 權限。實際 runtime 權限由 DB 負責人核對。

## 安全邊界與正式接線

本機例外同時檢 APP_ENV=local、非 Vercel、module_backend=remote、loopback peer 與固定 Host；不信任轉送來源標頭。POST 使用 E 嚴格同源檢查，沒有 Origin／可信 Referer 或跨來源即拒絕。HTML 自動跳脫，設定回應 no-store，沒有 Session 發行、web storage、機密輸出或模型 Key。

本批 Decision 新增 department_effect，預設 unresolved；部門功能授權 Capability.system_only=False，不要求主管具備系統管理部門資格，但仍需明確管理職務與 G 的可授範圍。一般能力必須由 G 回傳「本次已驗證 user／Session／selected_department 的部門基本許可」；跨部門 bool_or 不能當該決策。管理能力再要求 role_effect=allow 及目標 scope；system_only=True 的目錄維護再驗可信 system_management 資格；原 GrantPort 的 delegable 上限同時限制授予與撤銷。

只有舊 role_effect 的 adapter 不會自動相容；未提供 department_effect 仍拒絕。正式 B／G／journal 沒有注入，/login、/workspace、/account、/system/* 的正式 E 路由不因本機例外自動放行。

下一步須取得：主管負責功能／明確委派來源、管理目標部門範圍、逐操作資料 scope、正式 journal／操作者稽核及最後管理者保護。角色管理頁由對應負責組提供並接 E／G 契約；E 不另做一套角色核心。本批不宣稱跨組完整授權或登入已完成。

## 驗證紀錄

相關離線案例：249 passed、1 warning，6.84s；包含 main 帳號／多部門、部門、新功能頁及 E guard。正式資料操作、實際 DB schema／RLS、瀏覽器點擊、Session 撤銷與主管權限端到端仍未驗收。

最終完整回歸：936 passed、9 skipped、1 warning，126.10s，exit 0；新增 47 個案例。主管界線補測 159 passed、1 warning，9.81s。跨三站 checker PASS（0 issues）、Ruff lint／118 檔格式、djLint 30 樣板（0 errors）、environment 靜態檢查通過。保留既有 TestClient／httpx warning，未升級套件。
