# 06_E 逐次開發紀錄

> 2026-10-27 階段交付：此份文件與目前 E 成果一併提交至 codex/06e-admin-v13，見 [交付摘要](E-DELIVERY-20261027.md)。下文未 Commit／Push 的敘述屬開發當時紀錄；實際提交與上傳狀態以 Git／GitHub 為準。正式 B／G 與資料交易接線仍待完成。

## 2026-10-27 ADMIN 登入與取消匿名例外（本批最新）

- 使用者核定 ADMIN 共用桌機／手機登入頁，B／G 尚未提供時先交付 UI 與接點測試；取消 /accounts、/department、/functions、/department-functions 的本機免登入。此決定取代下列早期匿名開放紀錄，部門／主管授權規則不變。
- 已依附件完成藍色漸層白卡片、手機響應式與安全錯誤回填，未採示範帳密、工程師全權或 web storage 自動登入。四入口及子路由先驗身分／操作，資料 scope、G 可授上限與交易內重驗／journal 未完成仍保持 503，不能以單純登入成功開放原完整資料服務。
- 初輪全套 1 failed、1062 passed、9 skipped：舊的無效表單測試未給 Origin，新流程先拒絕來源而回 403。補上可信同源前提以驗 400，並另測缺 Origin 仍 403，未放寬正式檢查。
- 最終完整回歸 1065 passed、9 skipped、1 個既有 TestClient／httpx warning（104.78s，exit 0）；新增 56 個登入／閘門案例。初輪相關 378 passed，補測登入與一般頁面 71 passed（3.53s）。Node 未儲存提醒 7 passed；Ruff lint／121 檔格式、djLint 35 樣板（0 errors）、environment 靜態檢查均通過（connectivity_checked=false）。離線 headless 瀏覽器驗證 1440×900、390×844、320×568 及手機 503 畫面，無橫向溢出、按鈕高度至少 44px、密碼空白，已檢視桌機與手機截圖；不是正式站台端到端驗收。
- 本批未 Commit／Push、部署、migration 或正式資料操作。正式 B／G／寄信／資料隔離端到端仍未完成，見 [E-LOGIN-ACCESS.md](E-LOGIN-ACCESS.md)。較早匿名操作與驗收紀錄保留為歷史。


## 2026-10-27 單一部門權限內容頁（本批最新）

- 原因：原矩陣欠缺搜尋分頁、原因提示、未儲存提醒與儲存前摘要。結論：沿用 /department-functions，完成編輯 → 預覽 → 確認儲存；返回修改保留當頁勾選，提交只更新選定部門的提交列。
- 預覽與寫入共用驗證，確認時重新取鎖及檢原內容指紋；失敗不假成功、不自動更新舊稿版本。停用／未實作操作可顯示原因，原許可保留並可撤銷；部門停用只讀。
- 複查發現原儲存確認放在 button，既有 main.js 讀的是 form.dataset.confirm；新流程改放確認表單並以 7 項 Node DOM 替身案例驗證未儲存及取消確認行為。未修改共用 main.js。
- 相關 Python 測試 114 passed、1 個既有 warning（10.27s）；完整回歸 1009 passed、9 skipped、1 個既有 TestClient／httpx warning（104.11s，exit 0）。新增 32 個 Python 案例；Node 未儲存提醒 7 passed、0 failed（DOM 替身）。跨三站 check_project PASS（0 issues）、Ruff lint／119 檔格式、djLint 34 樣板（0 errors）、environment 靜態檢查通過（connectivity_checked=false）。不連真實 DB 或網路。
- 本批未 Commit／Push、部署、Schema／migration 變更或正式 DB 讀寫；不新增角色／scope 核心。人工瀏覽器與正式 DB 仍待驗。操作、POST 契約與限制見 [E-DEPARTMENT-PERMISSIONS.md](E-DEPARTMENT-PERMISSIONS.md)。


## 2026-10-27 功能管理列表／內容頁（本批最新）

- 原因：舊 /functions 混合新增及逐筆修改，缺少列表操作與內容頁。結論：拆成搜尋／平台與狀態篩選／二十筆分頁列表，以及新增、編輯、獨立停用／恢復的內容頁；儲存與取消保留列表條件。
- 字典代碼固定、衝突拒絕覆寫，停用保留授權。只改功能管理；原有部門、帳號、矩陣與 B／G 規則沒有新增變更。本批未 Commit／Push、部署或操作正式 DB。
- 功能管理 82 passed、1 個既有 warning（3.18s）；完整回歸 977 passed、9 skipped、1 個既有 TestClient／httpx warning（102.01s，exit 0）。跨三站 check_project PASS（0 issues）、Ruff lint／118 檔格式、djLint 32 樣板（0 errors）、environment 靜態檢查均通過；connectivity_checked=false。未連正式 DB／網路、未升級套件；實際瀏覽器及正式資料操作仍待人工驗收。
- 入口、路由、限制及人工待驗見 [E-FUNCTION-LIST-DETAIL.md](E-FUNCTION-LIST-DETAIL.md)。較早紀錄保留為歷史。


本文件為開發過程紀錄；計畫見 [E-DEVELOPMENT-PLAN.md](E-DEVELOPMENT-PLAN.md)，驗收見 [E-ACCEPTANCE.md](E-ACCEPTANCE.md)，授權需求與待確認事項見 [E-AUTHORIZATION-REQUIREMENTS.md](E-AUTHORIZATION-REQUIREMENTS.md)。

## 紀錄約定

- 每次開發結束記錄：日期、目標、使用基準、使用者決策、修改檔案及行為、實際檢查指令／結果、未驗證內容、已知問題及下一步。
- 區分「使用者已確認需求」「團隊待核定提案」「程式已實作」「測試已通過」「尚未驗證」。未執行的測試不寫通過，不以啟動或既有基準測試替代新功能驗收。
- 以 D:\2026102001 現有團隊確認版本及之後使用者明確更新為開發基準；若要更換來源、引入舊程式作基準或改共同架構，先詢問使用者。外部技術文件只能作參考。
- 不記錄密碼、Token、金鑰、連線字串、真實資料列或未去敏的錯誤輸出。引用帳號測試結果時，只記操作結果，不收集本人帳密。
- 本機 Markdown 紀錄與 Git 提交歷史不同；commit／push／merge 需另有使用者授權。應用內管理異動的安全 audit 是另項正式功能，不能由此文件取代。

## 2026-10-27｜團隊基準與 PM 啟動流程

- 基準：D:\2026102001；ADMIN main 2b69083，前台 main 4f31528，API main c42db7b。v1.3 PortableAuth 已由使用者確認 PM 核定。
- 使用者決策：以目前三站認可版本開發；正式 E 沿用 ADMIN FastAPI／Jinja2／HTMX 及自有 DB 分層，其他組功能仍由其他組負責。
- 執行：SETUP.cmd 完成 exit 0；START 啟動三站與本次手機 HTTPS。工具程序最初的舊 PATH 問題，以 Windows 現有 Machine／User PATH 解決，未為此修改程式。
- 驗證：ADMIN／API health、ADMIN /dev/accounts／dev/files HTTP 200；前台別名 307 到當次 HTTPS，該頁 HTTP 200。
- 本人帳號：使用者親自在瀏覽器新增並回覆成功；沒有收集帳密。這不代表正式 E 登入／角色授權驗收完成。
- 基準檢查：ADMIN scripts/check_project.py --project-root D:\2026102001：PASS、0 issues；既有 pytest：661 passed、9 skipped、1 warning，71.00 秒，exit 0。警告為既有 TestClient／httpx 淘汰提示，未變更鎖定依賴。
- 文件異動：新增 E-DEVELOPMENT-PLAN.md、E-ACCEPTANCE.md，在 DEVELOPMENT_STATUS.md 加入 E 啟動及規劃入口；文件連結與 diff 格式檢查通過。
- 未驗證：正式 UUID 登入、本人頁、重設寄信、部門 CRUD、操作矩陣、職務／資料 scope、管理 audit／衝突更新、完整 RLS。
- 範圍：未改業務程式、資料表或 Git 分支；未 migration、commit／push／merge 或部署。本人帳號新增是使用者依最新 PM 指示親自進行的授權操作。

## 2026-10-27｜開發紀錄及部門／職務隔離要求

- 使用者再次確認：所有開發以目前 D:\2026102001 的資料為主，除非之後另有明確指示；不確定或缺資料時先詢問，再進行相關開發。
- 使用者要求：保留開發紀錄，確實區隔不同部門與職務的功能，禁止未獲授權者使用不該使用的功能。
- 查核：目前 require_admin 仍用舊 UserRole.ADMIN；OperationContext 的 actor_id／owner IDs 仍為 int。v1.3 有部門 CRUD 矩陣及部門 View，未定義完整職務角色／指派／合併模型，尚不能宣稱隔離已完成。
- 本輪交付：新增本紀錄與 E-AUTHORIZATION-REQUIREMENTS.md；在計畫、驗收與狀態文件加入紀錄入口、基準約束及授權驗收要求。只做文件，不實作未核定政策。
- 第一輪提問：權限矩陣及角色來源、誰能管理部門／功能／授權與可管理範圍、部門和職務衝突時的合併規則。提問當時未取得答案；其後使用者答覆見下一節，不自行當作完整政策已核定。
- 驗證：本輪僅文件變更，完成後核對連結、編碼、diff 及既有靜態契約檢查；本條不重算前一輪 661 項結果為本輪實測，也不重跑未變更的應用測試。
- 下一步：先取得授權政策及跨組接點，更新確認狀態，再開始受影響的契約／程式。每批分開記錄實作與驗收。

## 2026-10-27｜使用者補充個人權限優先與系統管理部門

- 使用者答覆：目前沒有／不清楚正式權限表或角色介面，先整理待 PM 核定表；會有系統管理部門；個人權限大於部門權限。
- 決策更新：個人優先列為需求方向，原「部門為操作上限再取交集」的提案不採用。系統管理部門存在，不等於已確認全部成員都有管理或授權權限。
- 現況影響：v1.3 僅部門矩陣不足以表達完整個人／職務優先；需要權威授權來源與語意，可能涉及 API／DB 的共同模型或介面，不在 E 自行建立正式表。
- 追加詢問：個人權限指直接指派／職務／兩者；系統管理部門全部成員或僅獲指派者可管理；功能與資料範圍是否分開授權。另將允許／拒絕／未設定及多職務衝突解析列入待 PM 核定。
- 本輪仍僅文件，未實作個人覆寫或授權規則，尚無新授權測試通過結果。

## 2026-10-27｜釐清職務優先、管理資格與資料範圍

- 使用者確認：「個人權限」指職務角色帶來的權限，不是對每位使用者另外設定的例外；因此正式方向為職務優先於部門，不另增個別帳號特例模型。
- 使用者確認：系統管理部門只有明確獲指派管理職務／權限的人可管理，不是全部門成員自動取得。
- 使用者確認：功能操作與資料範圍分別授權，角色允許功能不等於允許全公司資料。
- 文件已依這三項答案收斂；角色清單、權威指派介面、具體矩陣／scope、職務未設定或多職務衝突、可授範圍仍留給 PM／帳號／API 組核定。
- 後續實作不得使用舊的部門上限交集提案，不把 department_permissions View 當完整最終授權，也不從請求自帶角色或系統部名稱推導權限。
- 本輪只確認與記錄，未變更業務程式／資料表或進行新授權驗收。
- 本輪文件驗證：五份文件的相對連結／UTF-8 檢查通過；git diff --check 通過；scripts/check_project.py --project-root D:\2026102001：PASS、0 issues。未重跑應用 pytest，未把先前 661 passed 當成新授權測試。

## 後續每批紀錄格式

日期／目標：

使用基準與使用者決策：

修改檔案與行為：

實際檢查指令及結果：

權限正例／越權反例結果：

未驗證內容、已知問題及待確認事項：

下一步與交接責任：
## 2026-10-27｜重新對照原始 06_E 任務

- 使用者要求以 2026-10-21 invoicebasic.7z 核對原始目標，實作基準仍是目前 D:\2026102001 最新團隊確認版本。
- 唯讀以 7-Zip -so 讀取 06_E_admin_departments.md（4,718 bytes／CRC FFE1D593），只讀 G 單相關分工參考；沒有把附件指令當成本輪執行授權。
- 結論：2-3／2-6／2-7 主軸一致；補明變更密碼 UI、分類／route／action registry、共用 require_permission／scope evaluator、初始 seed／管理 guard 及 E-T01～E-T10。
- 分工缺口：原單 E 管部門與功能字典、G 管 grants／有效權限來源；E 的設定 UI 與 G 共同寫入／計算接點仍需對齊，不各建一套。新確認職務優先與獨立 scope 保留，不因舊附件退回舊政策。
- 文件：新增 E-ORIGINAL-SCOPE-CHECK.md，更新 E-DEVELOPMENT-PLAN.md、E-ACCEPTANCE.md 及 DEVELOPMENT_STATUS.md 的對照入口。
- 本輪僅文件，不改業務程式、不執行 seed／migration、不讀帳密、不提交／部署。未重跑應用測試，不把先前基準當作新案例通過。
- 本輪對照文件檢查：六份文件連結／UTF-8、E-T01～E-T10 完整性及 git diff --check 通過；靜態契約檢查 PASS、0 issues。未執行新 E 功能測試。

## 2026-10-27｜第一批 E 實作與離線回歸

- 使用者要求依六階段正式開始實作；基準仍為 D:\2026102001 團隊版本，ADMIN HEAD 2b69083。新建本機分支 codex/06e-admin-v13，保留舊文件及遠端追蹤分支，未 fetch／commit／push／merge。
- 本輪新核定：職務必須明確允許，未設定拒絕；本人只允許改顯示名稱。部門不能補足未設定的職務；系統管理部門僅明確管理資格者可管理；資料 scope 另驗。
- 新增 e_admin DTO、e_ports、registry、guard、service、SQL repository／UnitOfWork、正式 SSR 及本人／密碼 UI；原 auth 路由改委派 B，B 缺少時回 503，不再先更新舊 users.last_login_at。其他組原 guard 未改。
- E 不直接寫 G 的矩陣，採共同版本與可授上限接點；只更新提交列，保留其他功能。不建立角色／audit／版本資料表，不造第二套 Session／reset Token。
- 停用部門檢查成員、所有主要部門參照及存活 Session；使用中拒絕，沒有自動移轉／刪歷史。固定 SQL 綁定參數、SQL scope 過濾；同交易再驗權、CAS、audit、commit／rollback 需真實 journal。
- 發現並修正：service 不直接匯入 SQL 建構／例外及 session factory；未知 adapter 錯誤去敏；可授上限同時限制新增與撤銷；commit／audit 失敗完整 rollback。未放寬檢查器或快照。
- 實測：第一批 47 passed；擴充 E／SQL／既有頁面 82 passed；全套 728 passed、9 skipped、1 warning，96.99s，exit 0。相較基準新增 67 個離線案例。跨站 checker PASS 0 issues；Ruff lint／format、23 個樣板 lint 0 errors、environment 靜態驗證通過。
- 保留既有 TestClient／httpx 警告，沒有升級套件。未驗正式 PostgreSQL、B／G、寄信、重設完成頁、RLS 或跨組實際資料流，未操作正式資料／migration／seed／部署。
- 交接文件 E-TEAM-HANDOFF.md；完整結果與 E-T01～T10 證據界線 E-IMPLEMENTATION-RESULTS.md。下一批取得共同 adapters 後進行隔離 DB 與跨組整合，缺接線維持拒絕。


### 本批最終補驗

補上 Origin 格式錯誤與公司級目錄選單範圍反例；移除登入路由對舊寬鬆來源檢查的依賴，統一使用 E 嚴格同源驗證。最終全套 731 passed、9 skipped、1 warning，98.00s、exit 0；本批共新增 70 個離線案例。最後跨站靜態檢查 PASS、0 issues，Ruff lint／format 通過。本機未登入 GET：/health、/login、/auth/password/forgot 為 200，/system/departments 為 303；不代表認證或 DB 串接完成。

## 2026-10-27｜06_E 第二批最新更新

依使用者指定四項優先順序完成 E02 有效部門／功能／逐操作 scope、E01 零功能拒絕與完整 registry 接點、E04／E05 失敗情境補驗及 E07 初始審查清單。細節與限制見 [E-PHASE2-RESULTS.md](E-PHASE2-RESULTS.md)；新增的 AccessPort.effective_access 必要方法及接入規範見 [E-ACCESS-CONTRACT.md](E-ACCESS-CONTRACT.md)；清單見 [E-INITIAL-FUNCTIONS.json](E-INITIAL-FUNCTIONS.json)。

E 專屬 126 passed（新增 56）；完整回歸 787 passed、9 skipped、1 warning，99.75s。跨三站 checker PASS、0 issues；Ruff lint／102 檔格式、djLint 24 樣板及 environment 靜態檢查通過。只用合成資料與封鎖外部服務 fixture，未驗正式 PostgreSQL／寄信／B／G；get_e_module 未接線仍拒絕，登入 503 並未因本批補強而解除。

本人仍只改顯示名稱；職務必須明確允許、系統管理資格與 scope 分開驗。財務專用 Capability 只在測試提供，未新增正式財務權限。目錄負例涵蓋修改重複代碼、查無資料、輸入、停用、版本、回滾與去敏；完整 E-T01～T10 正式整合及其他組初始功能仍待共同接點，未將離線通過改列正式驗收。

本批以目前 D:\2026102001 的 codex/06e-admin-v13／dcad25e 為起點。使用者隨後授權本機 Commit 保存成果並整合 feature/admin-accounts-departments，且先封閉該分支的本機匿名入口；不 Push、不操作正式資料。後續整合狀態另記，不覆蓋前述歷史證據。

## 2026-10-27｜整合最新 main，保留 E 與暫停匿名入口

使用者最終指定 main；fetch 後為 cee8dd8，已含帳號／部門與多部門設定。先以 b392653 保存 E 第二批，再在暫存目錄準備 main 合併；唯一狀態文件衝突保留雙方歷史。使用者另明確核定：來源 /accounts 與 /department 匿名入口暫停，核定本機亦回 503，雲端／不可信來源原 404 保留；真實身分／職務／scope／journal 接妥前不開放。

E 與來源程式、模板、repository 及交易測試保留。新增 12 個未覆寫閘門的預設拒絕案例，確認不呼叫業務服務；來源業務測試 fixture 明確且僅於測試覆寫待接線閘門，仍測本機／同源限制，沒有更改共用封網 fixture。

暫存 E＋來源案例 228 passed；完整離線 888 passed、10 skipped、1 warning，135.37s。Ruff 113 檔、djLint 28 樣板、靜態 checker／environment 通過；main 暫存版本與已測試版本的程式／測試／設定逐檔核對相同。D 槽最終結果另更新。

沒有操作正式資料、部署或 Push。合併取得的是程式，不會自動解決登入 503，亦不代表角色來源、DB schema、RLS、正式多部門或 E-T01～T10 已驗收。後續接點及 E／來源部門規則差異見 [E-INTEGRATION-ACCOUNTS-DEPARTMENTS.md](E-INTEGRATION-ACCOUNTS-DEPARTMENTS.md)。

### main 合併最終確認（2026-10-27）

D 槽實際合併後回歸：889 passed、9 skipped、1 warning，124.72s，exit 0；跨三站 checker PASS（0 issues），Ruff lint／113 個檔案格式、djLint 28 樣板及環境靜態檢查通過。所有測試使用既有封網 fixture；未執行正式 DB 操作。

目前工作分支仍是 codex/06e-admin-v13，origin/main（cee8dd8）已包含在本機歷史。E 成果保留，新帳號／部門匿名入口封閉；未 Push。登入 503 仍須正式 B／G／journal 接線，不能以此次 Git 合併宣稱解除。

## 2026-10-27｜E 本機功能頁與部門模式（第三批）

- 使用基準：目前 D:\2026102001 的 codex/06e-admin-v13、0ddca22（已含 main）。保留目前分支；本批不 Commit／Push／Merge，不操作正式資料。
- 使用者新核定：目前指定頁暫免登入；限本機 loopback／Host／模式，雲端 404。使用正式 DB，由使用者點擊操作；代理只用封網替身。有效部門成員繼承所選部門基本功能，不聯集多部門。主管可管理本部門與外部部門，但限負責或明確委派功能，操作及資料 scope 分開。
- 新增 /functions 與 /department-functions 的 schema、固定 SQL repository、交易服務、SSR／表單；重開 main 的 /accounts、/department 本機入口，保留原 Host／peer／同源限制；部門列表加入功能設定連結。不是 /dev 串接，沒有另建會員或角色核心。
- 字典新增不授權；未知能力只存停用草稿。矩陣只更新提交列、保留其他列；撤銷保存 false 不硬刪除。短交易檢內容指紋、完整可見性與鎖定；任何失敗 rollback，機密例外固定化。指紋不冒充正式 audit／journal CAS。
- Decision 增 department_effect，預設 unresolved 拒絕。一般功能依所選部門，管理能力另驗明確 role_effect／scope；系統部門／字典維護仍須 system_management。部門功能授權的固定 Capability.system_only=False，讓受委派主管無須系統管理資格，但仍受 G delegable 上限及目標限制。
- 實測：相關 249 passed、1 warning，6.84s；主管邊界補測 E／新頁／registry 159 passed、1 warning，9.81s。最終完整回歸：936 passed、9 skipped、1 warning，126.10s，exit 0。跨三站 checker PASS（0 issues）、Ruff lint／118 檔格式、djLint 30 樣板（0 errors）、environment configuration_valid=true、connectivity_checked=false。
- 本批新增離線案例 47 個；既有來源測試移除臨時閘門 override，真正測本機免登入；原 12 個本機封閉測試改驗雲端仍封閉，依據為使用者明確變更，不放寬共用封網 fixture、checker 或快照。
- 仍未驗：正式 DB 欄位／RLS／鎖競爭、真實瀏覽器操作、B 登入與切換部門、G 主管 owner／委派／資料範圍及正式 audit。未造角色表、owner 欄位、Session、測試模式或操作者 UUID；登入仍可能 503。使用方式及跨組待辦見 E-FUNCTION-PAGES.md。


## 2026-10-28 恢復本機配置與歡迎入口

原因：使用者需先補齊功能／部門／帳號設定，明確取消本機配置的登入限制。結論：僅四類本機配置及 /welcome 暫免登入，保留 loopback、固定 Host、部署與 POST 同源檢查；正式登入／工作台及雲端不放行。加入初始化提示、登入頁連結與無 DB 的歡迎流程；沿用 department_permissions，不新增表。

實作、風險、關閉方式與測試設計見 E-LOCAL-SETUP.md。驗證結果待本輪實測補記；未連雲端 DB／未修改正式資料，未 Commit／Push。
驗證完成（2026-10-28）：完整離線回歸 1107 passed、9 skipped、1 個既有 TestClient/httpx warning（124.13s）；新增 42 項本機模式案例。三站 checker PASS（0 issues）、Ruff lint 及 122 檔格式檢查通過，36 個樣板 lint 0 errors。本機 /welcome 實測 HTTP 200、設定模式提示及固定連結正常，no-store；此頁不查 DB。正式 DB 的功能列表／儲存留由使用者操作，未由代理讀寫。初輪新增測試誤用布林 VERCEL，已改為設定模型要求的字串；補齊歡迎頁新增功能捷徑後完整回歸通過。未 Commit／Push。


## 2026-10-28 部門內容與授權同頁

原因：使用者要求最簡操作，不再獨立矩陣頁，並核定四項操作與未接線配置。結論：列表開內容頁，基本資料與完整功能配置同一交易儲存；整頁 CAS、來源、保留部門限制及固定錯誤。舊 GET 僅轉址、POST 410，側欄移除入口。詳見 E-DEPARTMENT-DETAIL.md。相關離線測試初輪 247 passed、1 個既有 warning；checker 指出 service 內 SQL 初始化，已移至 repository；不放寬檢查。完整結果待補。未操作正式 DB，未 Commit／Push。
驗證完成（2026-10-28）：相關回歸 247 passed；最終完整離線回歸 1109 passed、9 skipped、1 個既有 TestClient/httpx warning（116.61s，exit 0）。跨三站 checker PASS（0 issues）、Ruff lint 通過、124 檔格式通過、34 個樣板 lint 0 errors。以合成資料及封鎖外部服務的 fixture 產生內容頁，再用全請求攔截／offline headless 驗證 1440、900、390 寬度：整頁無橫向溢出、8 個獨立操作控制項、無舊入口、儲存按鈕至少44px；已檢視 panel/mobile 截圖。僅為離線視覺與替身交易驗證，未操作正式 DB／帳號或儲存；未 Commit／Push。


## 2026-10-28 功能目錄啟用失敗

原因：accounts 不在 E registry，舊本機目錄服務將啟用與正式能力綁死，因而在 DB 更新前回泛用 400。結論：本機目錄新增／狀態維護與 registry 分開，三種平台均可設定啟用狀態；新增程式對應欄，未知代碼明示待接線。正式 registry 與權限 guard 不改，沒有自動授權。詳見 E-FUNCTION-ACTIVATION.md。離線驗證結果待補，未操作正式資料。
驗證完成（2026-10-28）：相關回歸 263 passed；完整離線回歸 1116 passed、9 skipped、1 個既有 TestClient/httpx warning（110.54s，exit 0）。新增 accounts 三平台啟用／停用及正式 registry 仍拒絕案例；保留版本、同源、雲端關閉與不自動授權測試。三站 checker PASS（0 issues）、Ruff lint 及格式檢查通過、34 個樣板 lint 0 errors。未對正式 DB 重現或自動啟用任何資料，由使用者重新開啟內容頁後操作；未 Commit／Push。


## 2026-10-28 歡迎頁簡化

原因：使用者希望歡迎頁溫馨簡單，避免設定流程與技術訊息造成焦慮。結論：改為暖色留白、太陽圖示及兩句問候，移除流程卡片／資料表說明。歡迎頁覆寫 setup_notice 呈現區塊，不顯示紅色初始化提示；實際編輯頁仍保留提示，登入／來源／DB 邏輯未修改。沿用現有測試調整呈現斷言，不新增鏡像測試。驗證結果待補。未 Commit／Push。
驗證完成：相關離線測試 51 passed、1 個既有 TestClient/httpx warning；34 個樣板 lint 0 errors、checker PASS（0 issues）。實際不查 DB 的 /welcome 回 200，已顯示問候且沒有紅色初始化提示。以保存的匿名歡迎頁做全請求攔截離線檢查，1440／900／390 寬度均無整頁橫向溢出；已檢視面板版截圖。沒有讀寫正式 DB，未 Commit／Push。


## 2026-10-28 內建歡迎頁與無功能帳號

原因：使用者要求所有部門及未來新部門固定可查看歡迎頁、不可修改，避免漏設定就擋登入；另確認尚無初始管理員，維持本機模式。結論：內建 VIEW 能力、登入固定導向、完整有效快照無業務功能可僅看歡迎，其他身分／來源／功能檢查不變。部門固定列與目錄伺服器鎖定，無 DB seed／批次回填。相關離線回歸 379 passed；後補拒絕使用 welcome 取得全域資料或自訂寫入能力。完整結果待補。詳見 E-BUILTIN-WELCOME.md。未操作正式 DB、未 Commit／Push。
驗證完成（2026-10-28）：相關離線回歸 379 passed；加入全域資料／自訂寫入能力拒絕案例後，完整回歸 1142 passed、9 skipped、1 個既有 TestClient/httpx warning（115.03s，exit 0）。Ruff lint 通過，三站 checker PASS（0 issues），34 個樣板 lint 0 errors。部門內容頁以合成資料及封鎖外部服務的 fixture 重繪，離線 headless 驗證 1440／900／390 寬度均無整頁橫向溢出，已檢視面板版固定歡迎列。維持 LOCAL_SETUP_ENABLED=True，未指定或自動建立管理員，未驗證正式 B／G、未操作正式 DB，未 Commit／Push。


## 2026-10-28 部門授權全選／取消

原因：功能增多後逐項勾選繁瑣。結論：授權區提供整頁全選／取消，並在名稱與查看之間加入整列核取方塊；半選同步、略過 disabled，歡迎頁固定權限不變。只變更表單，仍需儲存，UUID／版本／後端交易及權限規則不改。相關驗證待補，見 E-DEPARTMENT-BULK.md。未操作正式資料、未 Commit／Push。
驗證完成（2026-10-28）：相關 Python 回歸 122 passed、1 個既有 TestClient/httpx warning；實際 DOM 離線瀏覽器測試 9 passed、0 skipped（19.76s），涵蓋部分勾選同步、整列／整頁、disabled、reset／pageshow、JS 關閉、blocked 表單及無自動送出。1000／390 寬度六欄對齊、整頁無橫向溢出，已檢視合成畫面。三站 checker PASS（0 issues）、Ruff lint／125 檔格式通過、34 樣板 lint 0 errors。未重跑全套業務回歸；本次只改前端互動，原後端授權／交易不變。未讀寫正式 DB、未 Commit／Push。


## 2026-10-28 授權表格對齊

原因：全選只有一行、操作格有兩行說明，垂直置中使 checkbox 錯位；歡迎列缺少一致標籤。結論：固定五個控制欄等寬，每格採相同兩行結構，統一 checkbox 尺寸／文字行高／說明位置；功能資訊分層、表格圓角與留白。歡迎頁四項呈現一致且維持 disabled。只改樣板與頁面 CSS，不改 JavaScript／授權／表單欄位。擴充現有 DOM 測試，在桌機／手機量測欄寬及 checkbox 中心誤差不超過 1px，並測長代碼換行。實際結果待補；不操作正式資料，未 Commit／Push。
驗證完成：最終相關 Python 回歸 47 passed、1 個既有 warning（2.96s）；實際 DOM 瀏覽器測試 9 passed、0 skipped（16.31s）。在 1000／390 寬度實測五控制欄等寬與各列 checkbox 中心對齊誤差不超過 1px，含長代碼換行；整頁無橫向溢出。已檢視桌機／手機合成截圖，歡迎頁與一般功能呈現一致，六欄完整對齊；保留表格內捲動。34 樣板 lint 0 errors。只修改樣板及頁面 CSS，未變更授權／JS 邏輯或讀寫正式 DB，未 Commit／Push。

## 2026-10-28 登入恢復前唯讀核對與管理資格交接待確認

原因：使用者要求先驗證既有帳號，再恢復登入，並指定兩名初始管理者及交接能力。結論：START 正式來源唯讀核對為 7 個有效、1 個停用帳號；兩候選帳號皆有效，但只有第一位是 administration_d 成員。該部門三個管理功能四操作均已勾選，不能代替明確管理員指派與正式驗權。交接核准、獨立復原、第二位部門要求與資格持久化來源仍待確認。

實測只讀交易、必要狀態與配置，未驗密碼／登入，未輸出個人識別資料或密鑰到專案。LOCAL_SETUP_ENABLED=True，沒有寫入正式資料、建立角色表、恢復限制或變更應用程式。本輪僅記錄實際核對結果；未重跑軟體測試、未 Commit／Push。詳見 [核對紀錄](E-LOGIN-READINESS-20261028.md)。


## 2026-10-28 系統管理跨部門明確指派與交接核定

原因：使用者核定兩位初始候選及可交接管理資格，並要求跨部門管理能力獨立於部門矩陣。結論：Decision 增 authorization_basis，預設保留 department；可信來源可提供 system_assignment，只適用管理能力且同時驗 system_management、逐操作 role_effect、啟用狀態及 scope。共用 helper 對齊 guard 與本人／工作台投影，一般業務不能借系統資格放行。正式登入與本機模式未改，不硬編人員或新增角色核心。

交接核定為現任提出、新任接受、另一位現任核准；撤銷角色分離、最後管理員保護及 PM 核准／DB 執行復原，已形成跨組持久化需求。只有兩位而其中一位是撤銷對象時，需走獨立復原，不能自行核准。實作與未完成範圍見 [系統管理契約](E-SYSTEM-MANAGEMENT.md)。相關離線回歸 240 passed、1 個既有 Starlette/httpx warning（6.72s）；Ruff lint、三站結構 checker PASS。完整離線回歸完成：1154 passed、9 skipped、1 個既有 Starlette/httpx warning，118.40s（exit 0）；125 個 Python 檔格式檢查通過、Ruff lint 通過、三站 checker PASS（0 issues）、git diff --check 無問題。未寫正式資料、建立 Session、實測登入、Commit／Push 或部署。

## 2026-10-28 登入至歡迎頁的管理資格生命週期驗證

原因：使用者詢問是否可繼續登入權限開發，需驗證前批獨立管理指派在真實 E 路由是否一致。結論：補測 POST /login 發 Cookie／固定 /welcome、管理選單依獨立指派呈現、同 Cookie 管理資格撤銷後歡迎頁仍可讀但管理入口 403、B 撤銷後重新登入、僅查看不得 POST 修改。既有無功能登入案例改用沒有系統管理資格的合成有效員工。未覆寫配置 pending guard，不把其 503 當作正式功能開通。

相關離線回歸 242 passed、1 個既有 Starlette/httpx warning（6.57s）；測試檔 Ruff lint 通過。本批僅修改 tests/unit/test_e_login_access.py 與文件，應用程式不變；未重跑完整套件（前批完整結果為 1154 passed、9 skipped）。沒有真實登入、正式 DB 操作、Session 建立或資格指派，LOCAL_SETUP_ENABLED=True 不變；未 Commit／Push。正式接線前置仍為 B ADMIN Session 用途與撤銷、G 明確管理指派／逐操作 scope、交易內重驗與稽核，見 E-SYSTEM-MANAGEMENT.md。


## 2026-10-28 依最新要求完成簡單部門登入

原因：使用者否決額外管理員／職務設計，要求登入只驗帳密、讀部門勾選、組網站 Session，最多更新登入紀錄。結論：新增 admin_login_repository／admin_login_service，使用既有欄位及簽章 Cookie；唯一登入 DML 為 last_login_at。移除前批未部署的 PortableAuth／管理員 SQL 草案、獨立指派與額外資格守衛。舊相容 DTO 欄位不參與授權，舊歷史設計已撤回，不是目前開發依據。四操作各自驗證，配置入口同交易重驗，沒有其他新功能或 DB 表。

相關回歸第一輪 26 failed／409 passed，失敗為舊角色規則與未組裝 adapter 的假設；依使用者新規格更新對應案例，保留身分、scope、未勾選及異常來源拒絕覆蓋。第二輪相關 446 passed（19.56s）；獨立操作修正後登入核心 210 passed（8.73s）。Ruff lint 與跨三站 checker PASS。START 正式來源唯讀投影：7 有效員工，6 有配置許可、1 僅歡迎，另 1 停用。未驗正式密碼、未發 Cookie、未更新正式 DB。恢復 LOCAL_SETUP_ENABLED=False；完整離線回歸完成：1170 passed、9 skipped、1 個既有 Starlette/httpx warning，117.69s（exit 0）。Ruff lint、129 個 Python 檔格式、三站 checker PASS（0 issues）、34 個樣板 lint（0 errors）及 git diff --check 通過。本機匿名 HTTP：/login 200 且 no-store；/welcome、/accounts、/department、/functions 均 303 至登入。


## 2026-10-28 側欄登出入口

原因：使用者要求在「帳號管理」下一項提供登出，方便回登入頁。結論：共用側欄及 E 版型在帳號管理項目後加入 POST /logout 表單按鈕，沿用原登入 Cookie 清除與 /login 導向；沒有帳號管理許可的人仍可從側欄底部登出。樣式沿用導覽項目間距、字體及 hover，不新增登出服務或 DB 寫入。補強既有真實路由測試，驗證一般配置頁／歡迎頁的項目順序、POST 及無功能人員亦可登出。驗證完成：登入／側欄／本機模式相關 132 passed、1 個既有 Starlette/httpx warning（8.11s）；35 個樣板 lint 0 errors、測試檔 Ruff lint／格式、三站 checker PASS（0 issues）、git diff --check 通過。本批未重跑完整套件，未操作正式帳號或 DB、未 Commit／Push。


## 2026-10-28 側欄登出外觀修正

原因：原生灰色按鈕與側欄不一致，舊 ?v=dev 樣式快取可重現。結論：本機資源版本隨檔案變動，登出 reset 與側欄一致；保留原 POST /logout。驗證完成：實際離線瀏覽器 6 passed、0 skipped（11.97s），可重現舊快取；歡迎／工作台在 1440 與 390 寬度的字體、padding、色彩、行高相同，桌機控制項寬度／左右對齊／高度誤差不超過 1px，hover／鍵盤焦點及 POST 登出通過。已檢視桌機及手機合成截圖。相關 Python 回歸 132 passed、1 個既有 warning（8.67s）；Ruff lint／格式、三站 checker PASS（0 issues）、git diff --check 通過。未重跑完整套件、未操作正式 Cookie／DB、未 Commit／Push。


## 2026-10-28 取消帳號所屬部門後重新登入的診斷

回報：主要部門已改採購，取消資訊部後回登入，重登只剩歡迎及登出。唯讀核對前後確認：資訊部關聯已由有效改成非目前所屬，主要部門及採購部關聯保留；外鍵沒有阻擋，取消已儲存。原網站 Session 若仍選資訊部，下一次請求因關聯失效要求重登；重登依採購部設定，該部門未勾帳號／部門／功能管理，所以不顯示三配置入口。

採購部另有三聯式統一發票四操作許可；讀取與登入 Session 保留此資料，但該業務目前尚未註冊到後台選單，原 /invoices 也仍是舊登入依賴及 TODO，不把未串接項目假裝成可用入口。本次不新增該業務功能、不自動加權、不加回資訊部、不改登入／配置程式或 DB。

補上精確情境合成回歸：舊 Session 部門被移除、主要部門仍有效、重登後只保留新部門許可、未接線發票設定仍保留，配置入口拒絕。正式來源全程 READ ONLY；紀錄不含個人 Email、UUID、密碼、Cookie 或連線資訊。相關離線回歸 45 passed、1 個既有 Starlette/httpx warning（6.08s）。初輪新增案例 1 failed／44 passed，原因是測試手動 Cookie 缺網域，與 HTTP Cookie 並存而產生 CookieConflict；修正合成 Cookie 網域與瀏覽器一致後通過，未更改應用行為。未重跑完整套件、未修改正式資料。


## 2026-10-28 左側選單改依部門授權目錄顯示

- 原因：網站 Session 已讀取自行新增功能的四項許可，但舊側欄只取固定 registry，造成部門已授權的三聯式發票功能完全不出現。
- 結論：歡迎頁、配置頁與 E 工作台共用側欄投影。每次請求重新驗證目前帳號／所選有效部門，讀取啟用且適用 ADMIN／兩站、至少勾選一個操作的功能，使用資料庫功能名稱呈現；不聯集其他部門。
- 已有固定程式路徑且獲授權查看的功能可點選；已接線但只允許其他操作的項目顯示「未授權查看」。尚未接線的目錄功能顯示名稱及「尚未接線」，不產生可點連結、不從任意功能代碼拼出網址，也不代表發票業務已完成。
- 撤銷四操作、停用功能或移除部門資格後，下次請求即反映；歡迎頁與登出仍保留。帳號管理與登出相鄰。長名稱會換行，手機側欄避免待接線項目的高度拉伸其他按鈕。
- 僅新增側欄讀取及呈現；不修改正式資料、資料表、帳號身分或部門授權，不新增發票 CRUD，不替代各功能既有伺服器操作驗權。


### 本批補充回歸結果

- 額外執行既有本機配置、功能列表／內容、部門矩陣與帳號部門測試：最終 197 passed，1 個既有 warning（6.24s）；連同登入與 E 驗權 219 項，本批共 416 項相關 Python 測試通過，另有 8 項實際瀏覽器測試通過。這是相關測試，並非本批重跑整個專案全部測試。
- 擴充回歸初輪 2 failed、195 passed：兩個歷史測試把「沒有額外 Session／列表沒有修改表單」寫成整頁不得有登出 POST，與先前已核定側欄登出衝突。保留不產生 Cookie 的原檢查，補驗側欄原生 POST 登出，並將禁止修改表單的範圍限定在 main 列表內容；正式權限與寫入 guard 未放寬。
- 最終 Ruff lint／format、djLint、離線專案檢查通過。開發紀錄已寫回專案；未 Commit／Push 或修改正式資料。


## 2026-10-28 整批交付前完整回歸

- 1179 passed、9 skipped、1 個既有 warning（124.82s）；這是整合最新 main 前的 E 工作樹。側欄瀏覽器離線驗證 8 passed。交付說明見 [E-DELIVERY-20261028](E-DELIVERY-20261028.md)，合併後測試另行記錄。


## 2026-10-28 與最新 main 的整合檢視

- E 工作樹已先以獨立分支保存；再合入 origin/main 1105460，保留發票作業步驟、知識庫畫面、儀表板示範資料及其路由／測試。
- 原因：來源 main 在 app/main.py 啟動時寫入固定本機 Session 金鑰，會使舊登入 Cookie 可以預測簽署來源，並改變核定的本機設定邊界。結論：移除此舊預設值及不再使用的匯入；E 登入仍由目前程序隨機金鑰簽章，雲端沿用既有設定，不新增環境變數。
- 主頁仍由 auth 導向 /welcome；固定選單保留知識庫項目，但 E 登入側欄依目前部門授權投影。來源業務頁的舊 require_admin 不因合入而宣稱完成部門權限接線。
- 自動 Git 合併沒有文字衝突；整合後完整離線回歸與格式／契約檢查另記結果。尚未修改遠端 main 或正式 DB。


## 2026-10-28 最新 main 整合後驗證

- 整合後完整離線回歸 1193 passed、9 skipped、1 個既有 Starlette／httpx warning（197.25s）
- 部門全選與側欄桌機／手機實際瀏覽器離線 DOM：17 passed、0 skipped（50.00s）。
- Ruff lint／format、djLint（49 樣板、0 errors）、跨站專案契約檢查通過；Git 合併沒有文字衝突。來源 main 的 JS 尾端多餘空白行已清理，無功能變動。
- 保留來源 main 1105460 的發票流程、知識庫及儀表板；移除舊固定本機 Session 金鑰預設，E 登入維持核定的目前部門授權流程。
- 準備上傳 codex/06e-login-department-20261028，PR 目標 main。上傳／PR／合併實際結果以 GitHub 為準；未修改正式資料或雲端環境設定。
