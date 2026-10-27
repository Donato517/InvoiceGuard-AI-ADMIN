# 06_E 開發計畫與 PM 啟動驗收紀錄

更新：2026-10-27。開發基準為使用者確認的 `D:\2026102001` 三站團隊認可版本，以及 PM 核定的 PostgreSQL v1.3 PortableAuth。沿用 ADMIN 的 FastAPI／Jinja2／HTMX、自有 DB 模組與既有分層；E 主責 2-3、2-6、2-7，Auto Login 暫不啟用。

## 本輪先完成的 PM 指示

| 項目 | 結果及證據 |
|---|---|
| SETUP.cmd | 已完成，exit 0；三站既有 venv 依賴版本與匯入檢查通過，健康套件未重裝 |
| 工具 | Python 3.12 通過；cloudflared 2026.10.4、Node 24.22.1、npm 11.19.1、Vercel CLI 65.2.0 通過。工具程序最初使用舊 PATH，改用 Windows 現有 Machine／User PATH 後通過；沒有為此修改專案來源 |
| START.cmd | 已啟動實際三站與手機前台臨時 HTTPS；主 START 與三個平台視窗供使用者操作 |
| ADMIN／API | 直接健康檢查 HTTP 200；ADMIN /dev/accounts、/dev/files 與 API 別名健康檢查 HTTP 200 |
| 前台 | 本機別名回 307 轉到本次 trycloudflare 登入頁，該 HTTPS 頁 HTTP 200；內部 8101 不是瀏覽入口，不以直接瀏覽它的 400 判定故障 |
| 建立本人開發帳號 | 使用者親自在瀏覽器輸入並回覆「已成功新增帳號」；沒有收集／保存 Email、密碼或 UUID。這是使用者回報的新增結果，不是 E 完整登入驗收 |
| 正式寫入界線 | 最新 PM 指示授權的本人開發帳號新增已完成；本輪未做其他正式資料寫入、檔案上傳／刪除或 migration |

HTTP 檢查未輸出頁面或帳號資料。.localhost 在一般 HTTP 工具可能未自動解析，本輪以 curl --resolve 限定 loopback 檢查；未修改 hosts／DNS。瀏覽器帳號頁已要求開啟，使用者成功新增為實際操作結果。

## 每天如何啟動與重啟

1. 平常從 `D:\2026102001\InvoiceGuard-STARTUP\START.cmd` 啟動，保留 START 總控及三站視窗。電腦重開後仍以 START 啟動，SETUP 供首次設定／套件或啟動包更新後使用。
2. ADMIN 入口為 `http://invoiceguard-ai-admin.localhost:8080/`。帳號示範 `/dev/accounts` 與檔案示範 `/dev/files` 只供目前開發用途，正式 E 不建在 /dev，也不經它們呼叫業務功能。
3. 手機使用當次 START 顯示的 trycloudflare HTTPS /login；每次新啟動可能不同，以當次顯示值為準，不寫死在程式或手機書籤。
4. app/ 的 Python 變更由現有 reload 監看。需要單站重啟時，在原平台視窗 Ctrl+C，貼該視窗顯示的本機實際指令；原視窗保有本次注入設定。不要使用 PM 範例中的 C:\Users\jerry 或他的專案路徑。
5. 若更新的是 Vercel 上的設定，單站重啟仍沿用舊程序環境，需正常停止主 START 再重新 START 取得新值。一般程式修正與雲端設定更新分開處理。

## 目前 06_E 還缺什麼

- 正式登入仍查舊 ORM users、int uid Cookie、UserRole.ADMIN；UUID 開發帳號頁已使用 v1.3 user_accounts，但不代表兩者已接通。API auth 仍是 TODO。
- 未找到完整的部門管理、功能目錄、部門 CRUD 操作矩陣、本人頁與共用忘記密碼流程。
- 共用 OperationContext 目前是 int 身分／owner ID；不能將 UUID 強轉數字，也不能只改 E 後讓其他組的共用型別失效。
- v1.3 部門操作 View 能按選取部門查權限，但職務角色、總經理跨部門唯讀、逐筆資料 scope、E 管理稽核及授權集合版本仍待共同接點。
- 本機舊簽署登入目前因設定禁止 SESSION_SECRET_KEY 而不可用，先用既有 pytest 相依替身驗證 E；不以關閉登入保護或開放新 /dev 頁解決。

## 開發順序與每批交付

| 批次 | E 的工作 | 完成條件 |
|---|---|---|
| 0 基準與文件（本輪） | PM 啟動、帳號新增結果、無密鑰測試基準、計畫及驗收文件 | 啟動與 E 功能完成度分開記錄；測試結果有當次證據 |
| 1 身分／授權契約 | UUID、Session、profile、有效部門、選取部門、管理進入資格與操作權限；與認證／帳號／API 組對齊 | 已驗證身分由服務產生，不信任表單宣告的 user_id／角色／grants；未配置正式接點維持拒絕 |
| 2 部門管理（2-6） | 列表／搜尋／新增／修改、核定的停用與關聯檢查；先 schema／service／測試，再接 repository 與畫面 | 空白／重複 code／無效 UUID／越權／主要部門關聯衝突都有明確結果，寫入可 rollback |
| 3 功能與部門矩陣（2-7） | 功能目錄、app_scope、啟用狀態、每部門 view／create／edit／delete 矩陣、撤銷及衝突處理 | 未設定預設拒絕；功能 code 對應真實能力；不可越權授權，撤銷下次請求生效 |
| 4 認證與本人流程（2-3） | 接核定 PortableAuth、UUID／Session／credential_version、部門切換、本人欄位白名單、登出與忘記密碼委派 | 過期／撤銷／停用／離職／改密碼舊 Session 被拒；不另造第二套 reset Token，不假寄信 |
| 5 跨組驗收 | 職務與只讀 scope、其他組 guard 接入、正式 repository 的隔離測試 DB 驗證 | 同部門主管／同仁有核定差異；總經理只讀；直接網址、HTTP 操作及資料查詢皆驗權 |

本輪只建立開發文件及基準，不建立未核定的正式授權規則或變更其他組程式。接下來第一個程式交付單位建議為「UUID 內部契約＋部門新增修改的 schema／service／路由與測試」，可利用測試替身獨立推進。

## 程式落點（提案，尚未建立）

| 功能 | ADMIN 既有目錄 |
|---|---|
| 部門 | app/routers/departments.py、app/services/department_service.py、app/db/repositories/department_repository.py、app/schemas/department.py、app/templates/pages/departments/ |
| 功能目錄 | 對應 routers/features.py、services/feature_service.py、db/repositories/feature_repository.py、schemas/feature.py、templates/pages/features/ |
| 部門矩陣 | 對應 department_permissions router／service／repository／schema 及模板 |
| 本人頁／認證 | profile router／service 與既有 auth／security／deps 的核定接點 |
| 測試／紀錄 | tests/unit/、隔離 DB 就緒後的 tests/integration/、docs/E-ACCEPTANCE.md |

依目前團隊規則，service 控制交易、repository 不 commit；ADMIN 自己經 DB 模組存取資料，不擅自改成 API 代理。API 管理共用 schema／Model／Alembic；E 不在 ADMIN 另建 migration 或直接改表，需同步的共用契約／快照按團隊流程變更。

## DB 與安全驗收重點

1. v1.3 是獨立新建 DDL，不是可在既有 DB 重跑的 migration。使用者新增帳號成功表示該路徑有實際寫入結果，仍不證明全部 v1.3 物件／Policy／角色都已配置或驗收。
2. 部門停用前處理 primary_department_id／有效 membership；v1.3 延遲外鍵可能在 COMMIT 拒絕不完整變更。帳號轉移政策由帳號流程共同核定，不任意替使用者選部門。
3. 按 verified_user_id＋selected_department_id＋function_code 取得部門基礎權限，再依核定的職務優先政策決定操作，並另外驗目標資料 scope。全域 bool_or 不等於職務授權，不可用另一部門的 edit 自動擴權。
4. DB 非 owner runtime 權限與 RLS／Policy 由 DB／API 負責人提供；不能以 owner 測試通過代替權限隔離證據。正式資料不可作一般迴歸測試資料。
5. 密碼雜湊不回傳瀏覽器／日誌；Session／reset 原始 Token 不放 web storage、Git、文件或日誌。模型 Key 不屬於 E 前端設定；Cookie／CSRF 規則與共用認證一起驗收。
6. 職務與部門分開，總經理先跨部門唯讀；不能給 role=admin 全權或加入所有部門就宣稱實作完成。操作核准／匯出另與各組定義。
7. credential_version 是憑證版本，不是部門矩陣更新版本；updated_at 也不自動防止管理者互相覆蓋。E audit／原子版本接點須共同核定。
8. 現有 noindex 設定與實際登入／授權分別驗收；本輪依最新 PM 指示啟用手機通道，不能把臨時網址難猜當成權限保護。

## 交接前需要團隊提供的接點

- 認證／帳號：ADMIN 登入接入的職責、共用 Session／reset 流程、管理站資格、權威職務來源；不要求使用者貼出 Token 或密碼。
- DB／API：實際 schema 版本、非 owner 測試權限、隔離測試 DB、E 管理 audit 及更新衝突策略；如需 migration 由 API 組處理。
- 功能組／整合者：穩定 function_code、操作、資料 scope，以及自己路由／查詢如何使用同一授權接點。
- Git：目前三站在 main；本機 origin/chen_06_E_admin 與 origin/develop 為舊 a474487，E 追蹤版比 ADMIN main 2b69083 落後 6 提交。正式程式開發前依 PM 核定起點準備 E 分支，不直接推 main。本輪不切分支、不 commit／push／merge，也未 fetch。

本輪驗收與未完成項目見 [E-ACCEPTANCE.md](E-ACCEPTANCE.md)；團隊整體狀態見 [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)。
## 開發紀錄與授權確認（2026-10-27）

使用者再次確認：一切開發以目前 D:\2026102001 團隊認可資料為主，除非之後另有明確指示；更換基準或遇不確定／缺資料時先詢問，再進行受影響開發。

每次開發同步 [E-DEVELOPMENT-LOG.md](E-DEVELOPMENT-LOG.md)，記錄目的、基準、決策、檔案與行為、實際檢查結果、限制、問題及下一步；未執行項目不寫通過，紀錄不含機密。

使用者要求確實區隔部門與職務功能。共同原則、現有缺口、待核定矩陣及越權驗收見 [E-AUTHORIZATION-REQUIREMENTS.md](E-AUTHORIZATION-REQUIREMENTS.md)。使用者已確認：所謂個人權限指職務角色帶來的權限，職務權限優先於部門權限；系統管理部門只有明確獲指派管理職務／權限者可管理；功能操作與資料範圍分別授權。本輪不另建個別帳號特例授權模型。具體角色清單／權威介面、矩陣／scope、職務未設定與多職務衝突、可授範圍仍待 PM 核定；不猜測實作，也不宣稱隔離已完成。
## 原始 E01～E07 目標補強（2026-10-27）

重新讀取 2026-10-21 invoicebasic.7z 的 06_E_admin_departments.md；逐項對照見 [E-ORIGINAL-SCOPE-CHECK.md](E-ORIGINAL-SCOPE-CHECK.md)。原單用於任務追溯，技術與實作仍以目前 D:\2026102001 團隊確認版本為準，不將舊 API 清單／表名視為必須照搬。

本次補明：E03 包含忘記密碼及登入後變更密碼 UI；E05 包含功能分類、站內 route allowlist、已知 action 對照及啟用狀態；E06 是供 F／G 接入的共用 require_permission／scope evaluator 及測試／用法；E07 需交付各組核定的初始 registry／seed 清單、管理 guard、稽核接點，未實作能力不得自動啟用。v1.3 沒有的功能欄位可先以核定固定 registry 等效表達，需要共同結構變更時交 API／DB，E 不自行改表。

E／G 交接：E 的部門功能設定畫面保留；原任務 G 維護帳號／membership／grants 及有效權限資料。正式矩陣寫入與計算介面需與 G 對齊，不能 E、G 各做一套授權來源。共用認證維持同一 UUID／安全政策與重設流程，ADMIN 按目前架構保有自己入口與 DB 模組。

更新後的交付順序：共同接點／E 分支基準 → 最小 E registry 與 guard 契約 → 部門 E04 → 目錄 E05／初始功能 E07 → E06 與 G 同源矩陣設定 → E01～E03 正式登入及本人／密碼流程 → E-T01～E-T10 與新增角色／scope 驗收。登入接線可並行；正式管理路由始終需先受保護。

原始驗收編號已加入 E-ACCEPTANCE，全部新功能案例仍待執行。這是計畫補強，不是程式已完成；本輪未實作功能、執行 seed／migration 或 Git 提交。

## 第一批實作後的接續計畫（2026-10-27）

已完成本組可獨立實作的 typed 契約、registry／guard、部門與字典／矩陣畫面服務、SQL repository、認證委派 UI 及 70 個新增離線案例。舊規劃表的個別檔名僅為當時提案，本批實際集中於 e_admin／e_repository／e_ports 等模組，見 [交接文件](E-TEAM-HANDOFF.md)。

下一步依序：

1. B／G／DB 審閱接點並提供真實模組；核定管理站 Session 用途綁定、角色指派、多角色衝突、可授範圍及 journal 儲存。
2. 在 get_e_module 組裝 adapters；沿用 SqlUnitOfWork 直接 ADMIN DB，不改成 /dev 或一律 HTTP 代理。
3. 在隔離 PostgreSQL／非 owner runtime 驗證 Schema、CAS、audit、rollback、延遲外鍵及停用／撤銷競爭；正式 DB 不作一般測試庫。
4. 各組交付財務／採購／帳號／知識庫 code、action、route、scope 後加入 registry，核定初始管理資格／seed；不自動給全權。
5. 接 B 真實登入／寄信／重設完成頁／改密碼撤銷，再與各組移轉既有 route guard，逐項完成 E-T01～T10 整合。

未接線部分保持拒絕。使用者已核定職務未設定即拒絕、本人只改顯示名稱；不再將這兩點列為未知。

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
