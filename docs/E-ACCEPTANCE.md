# 06_E 驗收紀錄與開發完成條件

> 2026-10-27 第三批最新規則：使用者改採「部門基本功能＋主管授權管理」，主管可管理本部門及外部部門，但僅限負責／明確受委派功能；一般功能不要求額外職務許可，權限以目前選取的有效部門為準。指定本機配置頁暫時免登入、由使用者操作共用正式 DB，代理只做離線測試；這取代本文前期的一般功能角色優先及新入口全部封閉條款。正式 B／G／journal 與主管身分尚待接線，雲端仍關閉。操作、版本及限制見 [E-FUNCTION-PAGES.md](E-FUNCTION-PAGES.md)。

更新：2026-10-27。計畫見 [E-DEVELOPMENT-PLAN.md](E-DEVELOPMENT-PLAN.md)。本文件區分啟動環境、既有程式基準及新 E 功能；不以啟動成功宣稱 06_E 完成。

## 本輪結果

| 項目 | 狀態 |
|---|---|
| SETUP | 已執行，exit 0；三站既有 venv 依賴檢查通過 |
| START | 已啟動並保留供使用者操作 |
| ADMIN／API 健康檢查 | HTTP 200 |
| /dev/accounts、/dev/files | HTTP 200，未輸出頁面或他人資料；沒有在 /dev 建立正式 E 功能 |
| 前台別名／手機 HTTPS | 別名 307 到當次 trycloudflare /login；HTTPS HTTP 200 |
| 本人開發帳號新增 | 使用者回覆「已成功新增帳號」；本人密碼直接輸入瀏覽器，沒有收集帳密 |
| ADMIN 跨站靜態契約檢查 | scripts/check_project.py --project-root D:\2026102001：PASS，0 issues |
| ADMIN 既有 pytest 基準 | 661 passed、9 skipped、1 warning，71.00 秒，exit 0；warning 為既有 Starlette TestClient／httpx 淘汰提示，未更動鎖定依賴 |
| 06_E 正式功能／DB 整合 | 尚未實作及驗收；舊 users／int Cookie 與 v1.3 UUID 接線待做 |

本輪未測正式帳號登入、重設寄信、部門 CRUD／矩陣、完整權限／RLS、Storage 上傳或其他組業務流程。頁面 HTTP 200 與使用者新增帳號回報各有不同證據範圍。

## 開發批次必須涵蓋的案例

- [ ] UUID 與輸入驗證：空白、重複 code、無效 UUID、資源不存在及資料格式錯誤。
- [ ] 部門新增修改：合法交易成功；驗證／權限／外鍵／衝突失敗時不留下部分異動。
- [ ] 部門停用：仍有主部門指向時明確拒絕或走核定轉移流程，不任意改歸屬。
- [ ] 功能目錄：code 對應真實實作；停用或未知功能不能因新增一列資料取得能力。
- [ ] 部門矩陣：view／create／edit／delete 分開驗證，未設定拒絕；撤銷下次請求生效。
- [ ] 權限邊界：不能藉修改 user_id、部門 ID、角色或提交額外 grants 提權；直接 HTTP 與畫面一致。
- [ ] 多部門：一般切換驗本人有效部門；核定跨部門角色另驗其 scope。其他部門的 edit 不自動成為職務授權，最終依職務優先政策及目標資料範圍判斷。
- [ ] 職務／scope：同部門主管／同仁差異、總經理跨部門唯讀、資料列及敏感欄位限制。
- [ ] Session：登入、到期、登出撤銷、停用／離職、改密碼後舊憑證拒絕；Auto Login 不啟用。
- [ ] 本人頁：僅核定欄位可改，不自改部門／職務／啟用狀態；資料不過度揭露。
- [ ] 忘記密碼：共用服務、一般化回應、效期、單次消耗及競爭使用；未配置服務明示不可用。
- [ ] 網頁安全：CSRF、輸出跳脫／XSS、導向限制、憑證不出現在 URL／web storage／日誌。
- [ ] DB 整合：隔離測試庫、非 owner runtime、最小 GRANT／Policy、rollback、管理 audit 及版本衝突。
- [ ] 跨組接入：各組保護自己的路由／查詢；E 文件含範例與失敗回應，不接手他組功能。

## 本輪測試方式

使用現有 ADMIN .venv、PYTHONDONTWRITEBYTECODE=1、python -B 執行既有檢查；pytest -q -p no:cacheprovider，唯一 basetemp 放專案受忽略 tmp 下。測試沿用現有禁止外部網路／真 DB 的 fixture；沒有從 START 服務程序複製 DB／Storage 設定給測試。測試結果只代表目前基準，未執行的新 E 案例保持未勾選。
## 部門與職務隔離補充（2026-10-27）

完整要求及待核定表見 [E-AUTHORIZATION-REQUIREMENTS.md](E-AUTHORIZATION-REQUIREMENTS.md)，逐次紀錄見 [E-DEVELOPMENT-LOG.md](E-DEVELOPMENT-LOG.md)。本輪只補文件，不重跑未變更的應用測試。

- [ ] 同部門不同職務的結果符合已核定矩陣，並測試直接 HTTP 操作。
- [ ] 職務權限優先於部門；職務未設定、明確拒絕與多職務衝突依核定細則，不猜測或默認全權。
- [ ] 所需身分／角色／有效部門／scope 不明或接點未配置時拒絕。
- [ ] 權限管理者的可管理對象與可授出權限有伺服器檢查，不能自我提權。
- [ ] 業務／財務越權、總經理只讀、撤銷即時性及資料列／欄位限制都有反例測試。


使用者已釐清個人權限等於職務角色權限，不另建個別帳號特例模型；系統管理部門限明確獲指派者可管理；功能與資料範圍分別授權。新增必要案例（尚未執行）：

- [ ] 部門未授予、職務明確授予該操作，且資料 scope 允許時，依核定職務優先政策允許。
- [ ] 職務允許操作但目標資料不在 scope 時拒絕。
- [ ] 系統管理部門一般成員未獲管理職務／權限時，仍不得操作授權管理。
## 原任務 E-T01～E-T10 對照（2026-10-21 來源，本輪補列）

本表保留原驗收目的，路徑與接口按目前 SSR／service 契約對齊，不強制新增舊 JSON API。原單與目前計畫的差異見 [E-ORIGINAL-SCOPE-CHECK.md](E-ORIGINAL-SCOPE-CHECK.md)。以下皆為尚未執行的新 E 整合案例。

| 編號 | 必須驗證的結果 | 狀態 |
|---|---|---|
| E-T01 | 同一使用者前後台 UUID 一致 | 待實作／測試 |
| E-T02 | 前台 Session 不可直接用於管理端 me／部門或新架構等效受保護操作；管理用途／進入資格須共同認證契約支持 | 待接點／測試 |
| E-T03 | 無對應系統管理權限者不能查看或修改部門，含直接 HTTP 請求 | 待實作／測試 |
| E-T04 | 部門代碼重複被拒，且不留下部分異動 | 待實作／測試 |
| E-T05 | 功能停用後直接呼叫仍被拒，不只從選單消失 | 待實作／測試 |
| E-T06 | 本人資料修改不能自改部門、角色或 grants | 待實作／測試 |
| E-T07 | G 修改 grants 後，E 的有效權限、伺服器檢查與選單一致 | 待跨組／測試 |
| E-T08 | 權限變更、部門停用留下核定的操作者與時間／稽核證據 | 待 audit 接點／測試 |
| E-T09 | 帳號管理權不等於發票核准權；原 sys.accounts 僅作概念範例，實際 code 依最新字典 | 待跨組／測試 |
| E-T10 | 忘記密碼沿用同一 reset Token policy，不建立第二套；另驗登入後變更密碼 UI | 待認證接點／測試 |

功能目錄另驗：分類／route／action 的 registry 對照、外部／可執行 route 被拒、未知 action 不擴權，以及初始功能清單與實作能力一致。E06 需有 F／G 接入範例、scope 正反例與未配置拒絕行為。正式 seed、DB 寫入及跨組測試皆按核定環境及授權流程，不拿 production 作回歸測試。

## 第一批程式驗證更新（2026-10-27）

前文未勾選表保留完整正式整合目標，不能直接用離線測試勾為跨站通過。最新實際結果為全套 731 passed、9 skipped、1 warning，新增 E 案例 70 個；Ruff、djLint、環境及跨站靜態檢查通過。

部門 HTTP／交易、職務未設定拒絕、系統管理資格、scope、本人額外欄位、G 委派與版本衝突、登入 Cookie、共用密碼 UI、去敏等已用替身驗證。SQL adapter 的 commit／rollback／close、查詢參數與 scope 有離線測試；尚未在 PostgreSQL 驗真實 Schema、RLS、journal 或 B／G。

E-T01～E-T10 各項離線證據及正式待辦見 [E-IMPLEMENTATION-RESULTS.md](E-IMPLEMENTATION-RESULTS.md)，團隊接線見 [E-TEAM-HANDOFF.md](E-TEAM-HANDOFF.md)。

## 2026-10-27｜06_E 第二批最新更新

依使用者指定四項優先順序完成 E02 有效部門／功能／逐操作 scope、E01 零功能拒絕與完整 registry 接點、E04／E05 失敗情境補驗及 E07 初始審查清單。細節與限制見 [E-PHASE2-RESULTS.md](E-PHASE2-RESULTS.md)；新增的 AccessPort.effective_access 必要方法及接入規範見 [E-ACCESS-CONTRACT.md](E-ACCESS-CONTRACT.md)；清單見 [E-INITIAL-FUNCTIONS.json](E-INITIAL-FUNCTIONS.json)。

E 專屬 126 passed（新增 56）；完整回歸 787 passed、9 skipped、1 warning，99.75s。跨三站 checker PASS、0 issues；Ruff lint／102 檔格式、djLint 24 樣板及 environment 靜態檢查通過。只用合成資料與封鎖外部服務 fixture，未驗正式 PostgreSQL／寄信／B／G；get_e_module 未接線仍拒絕，登入 503 並未因本批補強而解除。

本人仍只改顯示名稱；職務必須明確允許、系統管理資格與 scope 分開驗。財務專用 Capability 只在測試提供，未新增正式財務權限。目錄負例涵蓋修改重複代碼、查無資料、輸入、停用、版本、回滾與去敏；完整 E-T01～T10 正式整合及其他組初始功能仍待共同接點，未將離線通過改列正式驗收。

本批以目前 D:\2026102001 的 codex/06e-admin-v13／dcad25e 為起點。使用者隨後授權本機 Commit 保存成果並整合 feature/admin-accounts-departments，且先封閉該分支的本機匿名入口；不 Push、不操作正式資料。後續整合狀態另記，不覆蓋前述歷史證據。

## 2026-10-27｜E 本機功能頁與部門模式（第三批）

- 使用基準：目前 D:\2026102001 的 codex/06e-admin-v13、0ddca22（已含 main）。保留目前分支；本批不 Commit／Push／Merge，不操作正式資料。
- 使用者新核定：目前指定頁暫免登入；限本機 loopback／Host／模式，雲端 404。使用正式 DB，由使用者點擊操作；代理只用封網替身。有效部門成員繼承所選部門基本功能，不聯集多部門。主管可管理本部門與外部部門，但限負責或明確委派功能，操作及資料 scope 分開。
- 新增 /functions 與 /department-functions 的 schema、固定 SQL repository、交易服務、SSR／表單；重開 main 的 /accounts、/department 本機入口，保留原 Host／peer／同源限制；部門列表加入功能設定連結。不是 /dev 串接，沒有另建會員或角色核心。
- 字典新增不授權；未知能力只存停用草稿。矩陣只更新提交列、保留其他列；撤銷保存 false 不硬刪除。短交易檢內容指紋、完整可見性與鎖定；任何失敗 rollback，機密例外固定化。指紋不冒充正式 audit／journal CAS。
- Decision 增 department_effect，預設 unresolved 拒絕。一般功能依所選部門，管理能力另驗明確 role_effect／scope；系統部門／字典維護仍須 system_management。部門功能授權的固定 Capability.system_only=False，讓受委派主管無須系統管理資格，但仍受 G delegable 上限及目標限制。
- 實測：相關 249 passed、1 warning，6.84s；主管邊界補測 E／新頁／registry 159 passed、1 warning，9.81s。最終完整回歸：936 passed、9 skipped、1 warning，126.10s，exit 0。跨三站 checker PASS（0 issues）、Ruff lint／118 檔格式、djLint 30 樣板（0 errors）、environment configuration_valid=true、connectivity_checked=false。
- 本批新增離線案例 47 個；既有來源測試移除臨時閘門 override，真正測本機免登入；原 12 個本機封閉測試改驗雲端仍封閉，依據為使用者明確變更，不放寬共用封網 fixture、checker 或快照。
- 仍未驗：正式 DB 欄位／RLS／鎖競爭、真實瀏覽器操作、B 登入與切換部門、G 主管 owner／委派／資料範圍及正式 audit。未造角色表、owner 欄位、Session、測試模式或操作者 UUID；登入仍可能 503。使用方式及跨組待辦見 E-FUNCTION-PAGES.md。
