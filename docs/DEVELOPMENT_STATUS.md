> 2026-10-28 側欄最新狀態：依目前部門的啟用功能及四操作勾選動態顯示；已接線且可查看才提供連結，其他已授權目錄項目顯示未接線／未授權查看。詳見 [登入說明](E-LOGIN-ACCESS.md)。

> 2026-10-28 最新版本：登入已改為「驗帳密 → 讀目前部門功能／操作 → 網站 Session → 歡迎頁」。配置入口已恢復登入檢查（LOCAL_SETUP_ENABLED=False）；沒有初始管理員、個別管理職務、交接或新增 DB 表。登入唯一寫入為既有 last_login_at。以 [登入說明](E-LOGIN-ACCESS.md) 為準，以下較早政策及測試為歷史記錄。

# 開發狀態與工作邊界

> 2026-10-30 後續更新：使用者要求將三模組同步至 API，兩站三檔恢復逐位元組比對；本日早期僅 ADMIN 分版紀錄被此範圍取代。三模組本身未再修改，evidence／Few-Shot 仍尚未接線業務流程。

> 2026-10-30 最新 Document AI 狀態：僅 ADMIN 完成 `document_ai`、`document_ai_evidence`、`document_ai_fewshot` 模組整合及離線驗證，Document AI 改為分站版本化。保留原 OCR 介面，新增辨識輪次綁定、欄位圖片引用限制與 Few-Shot 設定工廠，依賴增加 Pillow。Google／Gemini 設定沿用既有名稱；未接線發票 UI、範例 CRUD、正式資料寫入、雲端呼叫或審批。下列較早兩站 AI 全部同版敘述對 Document AI 不再適用；詳見 [檢驗報告](DOCUMENT_AI_REVIEW_20261030.md)。


> 2026-10-28 歡迎頁最新調整：僅顯示溫馨問候，移除設定流程及技術提示。功能入口保留在側欄，實際編輯頁仍顯示初始化提示。以下舊歡迎頁導覽描述為歷史紀錄。

> 2026-10-28 啟用修正：目錄啟用與 registry／正式驗權分開；自行建立的代碼可啟用目錄，待接線另行標示，不自動授予部門權限。見 [E-FUNCTION-ACTIVATION.md](E-FUNCTION-ACTIVATION.md)。下文未登錄只能停用的規則是歷史狀態。


> 2026-10-28 最新整合：部門內容與四項授權功能改為同頁儲存；獨立部門功能頁移除，舊 GET 僅轉址、舊 POST 停用。未接線功能可保存配置。詳見 [E-DEPARTMENT-DETAIL.md](E-DEPARTMENT-DETAIL.md)。下文舊矩陣 UI 流程僅供歷史追溯。


> 2026-10-28 最新核定：本機初始化配置暫免登入，新增 /welcome；雲端維持關閉、正式 B／G 尚未完成。操作及恢復方式見 [E-LOCAL-SETUP.md](E-LOCAL-SETUP.md)。下文先前全面關閉規則為歷史狀態。


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


> 2026-10-27 第三批最新規則：使用者改採「部門基本功能＋主管授權管理」，主管可管理本部門及外部部門，但僅限負責／明確受委派功能；一般功能不要求額外職務許可，權限以目前選取的有效部門為準。指定本機配置頁暫時免登入、由使用者操作共用正式 DB，代理只做離線測試；這取代本文前期的一般功能角色優先及新入口全部封閉條款。正式 B／G／journal 與主管身分尚待接線，雲端仍關閉。操作、版本及限制見 [E-FUNCTION-PAGES.md](E-FUNCTION-PAGES.md)。

## 2026-10-27 最新整合狀態

使用者最終指定將最新 main（cee8dd8，已含帳號／部門功能）整合進 E 分支，並暫時封閉其本機匿名入口。帳號／多部門與 /department 原始程式保留，但 /accounts、/department 的全部路由新增待整合相依檢查，核定本機仍回 503、雲端／不可信來源維持 404；沒有環境開關放行。下文來源作者的匿名測試及正式 DB 實測屬歷史證據，不是本分支當前授權。

E 的正式 /system/departments、/system/features、本人／工作台保留；來源 /department 與 E 尚未合併成單一部門服務。來源帳號建立／多部門管理不等於 B 登入／Session 或 G 職務／scope 已接妥。整合結果及待辦見 [E-INTEGRATION-ACCOUNTS-DEPARTMENTS.md](E-INTEGRATION-ACCOUNTS-DEPARTMENTS.md)。未 Push、部署或操作正式 DB。


更新：2026-10-27。本文是目前狀態摘要，歷史聊天、檔名或 TODO 不代表功能完成；修改能力時必須同步本表、實作與測試。

### 來源分支歷史紀錄（下列匿名規則已由本次整合取消）

## 2026-10-27 部門代碼修改增補

依使用者明確要求，部門頁新增代碼修改（POST `/department/{department_id}/code`）；檢查唯一性、保留 UNASSIGNED、拒絕有代碼外鍵的變更。
真實 DB 已將 Finance Department 改為 finance_d，確認 UUID 與名稱不變；33 項部門測試及架構、Ruff、模板檢查通過。

## 2026-10-27 帳號多部門設定增補

正式帳號新增／修改內容頁新增所屬部門複選及主要部門設定。以既有 user_departments.unassigned_at 停用／恢復關聯，is_current 由 DB 自動計算，與主要部門同交易儲存，保留關聯資料列。
真實 DB 已確認主要部門三欄外鍵，未新增 Schema 或修改 department_permissions；詳細驗證及限制見 [帳號管理](ACCOUNTS.md)。

## 2026-10-27 正式帳號管理增補

新增 `/accounts` 列表頁及共用新增／修改內容頁（`/accounts/new`、`/accounts/{user_id}/edit`），沿用既有 v1.3 帳號 Repository、Session 與新增帳號流程，不在 `/dev` 也不依賴其模組。
依使用者最新授權，管理者直接修改 Email／重設密碼，刪除採 disabled 停用並保留資料；先核對 DB 停用狀態契約。
本機匿名操作採與此次部門功能相同邊界；雲端在正式授權與稽核接線前保持拒絕。原 `/users` 與 `/dev/accounts` 邏輯不變。
全套離線測試 721 passed、9 skipped；本機平台回傳 502，真實 DB 帳號操作未驗收。完整檔案及限制見 [帳號管理](ACCOUNTS.md)。

## 2026-10-27 部門管理增補

正式功能 `/department` 已實作列表、代碼／名稱搜尋、新增、名稱修改、啟用／停用與關聯保護，採既有 SQLAlchemy Session 與固定參數 SQL，未放在 `/dev` 或依賴其模組。
依本次使用者授權，僅此功能允許本機匿名測試；雲端完整關閉，登入與權限原程式保留。正式授權及 v1.3 稽核仍待整合。
舊文「唯一匿名例外為 /dev」對本功能由本次明確授權取代，其他功能維持原限制。
詳細範圍、外鍵保護及驗證限制見 [部門管理](DEPARTMENTS.md)。

更新：2026-10-26。本文是目前狀態摘要，歷史聊天、檔名或 TODO 不代表功能完成；修改能力時必須同步本表、實作與測試。

## 本站角色

ADMIN 維持自己的 Vercel／DB 模組、管理者 cookie 登入程式及使用者查詢，不改為只依賴 API。頁面完成度：/system/job-runs 已讀取資料；儀表板、發票審核、使用者管理、稽核紀錄的資料呼叫仍為 TODO。本機無法登入（SESSION_SECRET_KEY 不在本機），需要登入的頁面只能在雲端使用，或以測試替身驗證。第一階段契約 DataService／FileService／get_storage_client() 尚未接線（fail closed）。

## 共同狀態（三站相同）

| 能力 | 狀態與證據 |
|---|---|
| AI 規範入口 | AGENTS.md（三站架構、共同規則、本站現況）＋ CLAUDE.md 匯入；2026-10-26 已提交並 Push 至 feature/python-web |
| 契約／分層檢查 | scripts/check_project.py + contracts/project-contract.json；只做無副作用靜態分析 |
| 本機三站／手機 HTTPS | InvoiceGuard-STARTUP 的 SETUP.cmd／START.cmd；三站各在自己的 CMD 視窗執行並自動重載本站 app/ 的 Python |
| 本機雲端設定 | START 以 Vercel CLI 讀取 ADMIN／API 各自專案的 Production 設定，把 DATABASE_URL、S3_*、SUPABASE_STORAGE_BUCKET 注入本機該站程序（2026-10-25 核定）；ADMIN 與 API 另各自注入自己專案的 GOOGLE_CLOUD_PROJECT_ID、GOOGLE_CLOUD_LOCATION、DOCUMENT_AI_PROCESSOR_ID、GOOGLE_SERVICE_ACCOUNT_JSON、GEMINI_API_KEY、GROQ_API_KEY（API 2026-10-28、ADMIN 2026-10-29 核定，不視為本機禁用機密；ADMIN 缺 AI 設定時 START 只提示、照常啟動）；前台不取得。本機 ADMIN／API 的資料就是正式資料 |
| 共用常數 | app/core/constants.py（2026-10-26，三站相同）：正式／本機網址、網站名稱、圖示、搜尋引擎設定 |
| 搜尋引擎 | 三站 noindex：網頁 meta 與 vercel.json 的 X-Robots-Tag（後者部署後生效）；robots.txt 不封鎖抓取 |
| 模組 | ADMIN／API 的 DB 模組（app/db）與 Vercel 模組（檔案存取，app/integrations/supabase_storage.py）可直接操作正式 DB／Storage；ADMIN 的 /dev 示範檔案使用的就是這兩個模組。AI 模組（integrations 的 document_ai／gemini／groq／chat_models）2026-10-29 起兩站同版，由兩站的 tests/unit/test_shared_integrations.py 比對一致。第一階段契約 DataService／FileService／get_storage_client() 尚未接線，呼叫時拋出 ModuleNotReadyError |
| Schema／部門 | 既有 ORM 為草案；StoredFile、version 與部門模型尚待設計，未與雲端結構核對 |
| 授權 | APP_ENV=local 不跳過登入（唯一例外：ADMIN 本機 /dev 示範檔案）；OperationContext 不可直接信任請求宣告的身分／權限 |
| 模型整合 | API 已串接：Document AI（OCR）與 Groq gpt-oss（20B／120B）用於手機與電腦上傳辨識（2026-10-29）；Gemini 模組已建、對話待儲值後驗證。ADMIN 有同版 AI 模組與設定（2026-10-29），尚未接線任何功能；前台不直接串接模型；舊 Blob 保留名稱不代表遷移 |
| CI | 已有單站無公司密鑰工作流程（.github/workflows/project-checks.yml）；2026-10-26 已 Push 至 feature/python-web，遠端執行結果須到 GitHub Actions 確認 |

## 本站狀態

| 能力 | 狀態與證據 |
|---|---|
| DB 模組 | 程序有合法 DATABASE_URL 即可使用（本機與雲端），TLS／NullPool／設定去敏保留；2026-10-25 起本機以 START 注入值連線，實測 SELECT 1 = 1 |
| Vercel 模組（檔案存取） | app/integrations/supabase_storage.py（與 API 位元組相同）：上傳、下載、列表、移動、刪除；/dev/files 為示範 |
| AI 模組 | 2026-10-29：app/integrations/document_ai.py（Document AI OCR）、gemini.py、groq.py、chat_models.py 自 API 複製、逐位元組相同（tests/unit/test_shared_integrations.py）；Settings 新增 GOOGLE_*、GEMINI_MODEL、GROQ_*，LOCAL_INJECTED_FIELDS 允許 START 注入的 GEMINI_API_KEY、GOOGLE_SERVICE_ACCOUNT_JSON、GROQ_API_KEY（其他機密在本機仍拒絕）；requirements 加 httpx、google-auth（ADMIN venv 實測 cryptography 可載入）。離線測試（自 API 複製，合成值與 MockTransport）通過；尚未接線任何功能、未實際呼叫 Google／Groq。雲端使用前需在 Vercel ADMIN 專案設定六項；ADMIN 函式上限 30 秒 |
| ADMIN 唯讀盤點 | GET /system/db-inspection（受網站登入保護）；固定表的存在性、欄位與筆數，無密鑰測試；本機無法登入，只能在雲端使用（尚未部署），見 [DB_READONLY_PROBE.md](DB_READONLY_PROBE.md) |
| 開發者帳號（示範 DB 模組） | 本機 /dev/accounts（示範檔案，不需登入，只在本機開放）：查詢（UUID／Email）、新增（Email＋初始密碼，建立即為 active，同交易寫 user_profiles 與預設部門 UNASSIGNED 的 user_departments）、修改（需目前密碼）；`dev_account_service` → `user_service` → `user_account_repository` 直接讀寫正式 DB，service 控制 commit／rollback。本機讀取已於 2026-10-25 START 驗收實測；表單以 HTMX 送出，同頁更新結果，不換頁、不經網址參數 |
| 檔案（示範 Vercel 模組） | 本機 /dev/files（示範檔案，不需登入，只在本機開放）：圖片（dev/images/，.jpg/.jpeg/.png）與 RAG 文字檔（dev/rag/，.txt/.md），含列表、下載、移動、刪除；`dev_file_service` → `integrations/supabase_storage`。真 START 實測圖片全流程通過；Bucket 只允許 image/jpeg、image/png、application/pdf，RAG 文字檔目前被拒（415），需在 Supabase 加入 text/plain、text/markdown。不寫 DB、不接發票流程；表單以 HTMX 送出，同頁更新結果，不換頁、不經網址參數 |
| 遠端 ADMIN 用戶端 | `remote_admin_client` 保留但沒有呼叫端；本機改為直接使用 DB／Storage |

開發者不取得 Vercel／Supabase 管理員權限；雲端變數更新後，雲端需重新部署、本機需重新 START 才會套用。部署、執行 migration、修改正式資料、Commit／Merge／Push 需使用者明確授權。

## 文件權責

AGENTS.md 說明三站架構、閱讀順序與共同約定；README 說明使用方式；ARCHITECTURE／CODING_CONVENTIONS 管目錄與格式；ADMIN/API MODULE_CONTRACT 管型別與未實作邊界；API 的 app/db/README.md 管 Schema 權威。若文件與實際程式衝突，先指出並對齊，不能擅自繞過保護。單站 clone 無兄弟專案時仍可執行本站檢查；不得為讀取兄弟站而索取管理員 Token。

精確檢查與限制見 [PROJECT_CHECKS.md](PROJECT_CHECKS.md)，實測結果見 [TEST_REPORT.md](TEST_REPORT.md)，修正紀錄見 [REVIEW_FIX_LOG.md](REVIEW_FIX_LOG.md)。

## 06_E 啟動與開發規劃（2026-10-27）

使用者確認 PM 要求以目前 D:\2026102001 的團隊認可三站版本開發，並採已核定 v1.3 PortableAuth。E 負責 2-3、2-6、2-7；Auto Login 暫不啟用。

本輪依最新 PM 指示實際完成 SETUP（exit 0）、START 三站／手機臨時 HTTPS 啟動、ADMIN／API 健康與 /dev/accounts／/dev/files HTTP 200、前台別名 307 及公開登入頁 HTTP 200。使用者親自在瀏覽器新增本人開發帳號並回覆成功；沒有收集帳密或修改其他正式資料。/dev 僅作示範，正式 E 不建於 /dev，也不串接它。

ADMIN 跨站 scripts/check_project.py：PASS、0 issues；既有 pytest：661 passed、9 skipped、1 warning（71.00 秒，exit 0）。測試使用現有無外部服務 fixture，沒有操作正式 DB。warning 為既有 TestClient／httpx 淘汰提示。

以下為本日最初規劃記錄（當時新 E 尚未實作）；最新實作狀態見本文末節。舊 users／int Cookie 與 v1.3 UUID／Session、部門與職務、總經理唯讀、scope／audit／版本接點仍待整合。規劃順序為：身分契約 → 部門新增修改 → 功能目錄／部門 CRUD 矩陣 → PortableAuth／本人頁／忘記密碼 → 跨組驗收。

詳見 [E-DEVELOPMENT-PLAN.md](E-DEVELOPMENT-PLAN.md)、[E-ACCEPTANCE.md](E-ACCEPTANCE.md)。本輪只新增規劃及驗收文件，保留既有三站程式與分支；未執行 migration、commit／push／merge 或部署。START 依使用者最新指示繼續運行，透過原總控視窗可停止本次服務。
### E 開發紀錄與權限要求補充（2026-10-27）

使用者再次要求以目前 D:\2026102001 版本為基準、開發有紀錄，部門與職務需確實隔離。新增 [E-DEVELOPMENT-LOG.md](E-DEVELOPMENT-LOG.md) 與 [E-AUTHORIZATION-REQUIREMENTS.md](E-AUTHORIZATION-REQUIREMENTS.md)，並補計畫／驗收入口。本輪僅文件；使用者已釐清：個人權限即職務角色權限，職務優先於部門；系統管理部門限明確獲指派者可管理；功能與資料範圍分別授權。角色清單／權威介面、具體矩陣／scope、職務未設定與多職務衝突、可授範圍仍待 PM 核定。尚未實作正式授權，不能把現況標示為已完成隔離。
### 06_E 原始任務對照（2026-10-27）

重新讀取 2026-10-21 的 06_E_admin_departments.md，確認目前計畫仍對應 2-3／2-6／2-7，並補明變更密碼 UI、功能分類／route／action、共用 require_permission／scope、初始功能 seed／管理 guard，以及原 E-T01～E-T10。原單 E／G 授權權責與目前 E 設定 UI 需同源接線；不將舊 API／表名覆蓋目前團隊架構。詳見 [E-ORIGINAL-SCOPE-CHECK.md](E-ORIGINAL-SCOPE-CHECK.md)。本輪僅計畫／驗收補強，新功能仍未實作。

## 06_E 第一批程式交付（2026-10-27，最新）

基準 main 2b69083，已建立本機 codex/06e-admin-v13，保留先前文件。程式落在原 ADMIN 分層，未移植舊 Node／admin-e 專案。

| 能力 | 最新狀態 |
|---|---|
| Registry／guard | 已實作固定 E 能力、UUID／Session 綁定、職務明確許可、系統管理資格及獨立 scope；缺接點拒絕 |
| 部門 | 已實作列表／新增／修改 UI、驗證、固定 SQL、影響檢查及交易；正式 journal／DB 整合待接線 |
| 功能及矩陣 | 已實作字典及 G 同源矩陣 UI／委派服務；未知功能不啟用，不直接寫 G 的權限表 |
| 登入／本人 | 已將原 /login、/logout 改接共同 AuthPort；只有顯示名稱可改；忘記／變更密碼委派 B；B 未接線回 503 |
| 其他組路由 | 原 dashboard／invoices／users 等仍為舊 require_admin，尚未移轉到 E；不宣稱全站授權完成 |
| 測試 | ADMIN 全套 731 passed、9 skipped、1 warning（98.00s）；本批新增 70 個離線案例；正式服務未測 |
| 檢查 | 跨站 check_project PASS 0 issues；Ruff lint／format、djLint 23 檔 0 錯、environment 靜態驗證通過 |

正式 B／G／DB journal、真實職務矩陣、各組功能清單與隔離測試 DB 仍待團隊提供。未執行正式 CRUD、seed、migration、部署或 Git 提交／推送。

詳見 [E-TEAM-HANDOFF.md](E-TEAM-HANDOFF.md)、[E-IMPLEMENTATION-RESULTS.md](E-IMPLEMENTATION-RESULTS.md)。前文無功能／舊 Cookie 說明保留作為本次開發前基準；E 最新行為以本節及實際程式為準。

## 2026-10-27｜06_E 第二批最新更新

依使用者指定四項優先順序完成 E02 有效部門／功能／逐操作 scope、E01 零功能拒絕與完整 registry 接點、E04／E05 失敗情境補驗及 E07 初始審查清單。細節與限制見 [E-PHASE2-RESULTS.md](E-PHASE2-RESULTS.md)；新增的 AccessPort.effective_access 必要方法及接入規範見 [E-ACCESS-CONTRACT.md](E-ACCESS-CONTRACT.md)；清單見 [E-INITIAL-FUNCTIONS.json](E-INITIAL-FUNCTIONS.json)。

E 專屬 126 passed（新增 56）；完整回歸 787 passed、9 skipped、1 warning，99.75s。跨三站 checker PASS、0 issues；Ruff lint／102 檔格式、djLint 24 樣板及 environment 靜態檢查通過。只用合成資料與封鎖外部服務 fixture，未驗正式 PostgreSQL／寄信／B／G；get_e_module 未接線仍拒絕，登入 503 並未因本批補強而解除。

本人仍只改顯示名稱；職務必須明確允許、系統管理資格與 scope 分開驗。財務專用 Capability 只在測試提供，未新增正式財務權限。目錄負例涵蓋修改重複代碼、查無資料、輸入、停用、版本、回滾與去敏；完整 E-T01～T10 正式整合及其他組初始功能仍待共同接點，未將離線通過改列正式驗收。

本批以目前 D:\2026102001 的 codex/06e-admin-v13／dcad25e 為起點。使用者隨後授權本機 Commit 保存成果並整合 feature/admin-accounts-departments，且先封閉該分支的本機匿名入口；不 Push、不操作正式資料。後續整合狀態另記，不覆蓋前述歷史證據。

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
