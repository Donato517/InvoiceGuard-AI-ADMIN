# 06_E 逐次開發紀錄

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
