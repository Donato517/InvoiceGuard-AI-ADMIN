# ADMIN 發票上傳流程整合計畫（審查版第三版，供 API、B／E、F 討論；認證尚未核定）

> 日期：2026-11-02（第三版：新增「沿用 API 既有 Bearer Session」方案 B 的評估、R 的重放與稽核檢查、W1～W3 實作狀態；前版有幾處更正，見第 12.1 節）。
> 分支：ADMIN `feature/admin-real-case-read`、API `feature/tax-id-checksum-tests`（僅測試）。
> 跨組審查摘要（`R1_REVIEW_SUMMARY.md`）、方案 R 設計、跨站 E2E 計畫、正式庫唯讀查核提案：**未隨本批交付**（保留在本機備份分支 `backup/c-intake-worktree-20261103` 的歷史中，待 B／E／API 擁有者審查後另案提出）；以下對這些文件的提及僅為名稱，沒有連結。
> 本批只交付 `/intake`、`/cases` 的程式與測試；認證方案（R1）尚未核定、尚未實作。
> 本文是分析與提案，**不是已核定的設計**。認證、權限、狀態與資料表變更都需要 API 擁有者與 B／E／F 組確認後才能實作。
> 目標不是新增一套認證系統，而是用最小、可驗收的方式讓 ADMIN 完成現有 API 的辨識、檢核與提交。
> 本輪未連正式 DB／Storage、未執行 migration、未 Commit。

## 0. 與第一版的差異

1. 重新審視方案：**不再把方案 P（API 發行 Token、需新資料表）當作預設答案**。核心安全價值來自「API 自己重新查權限」與「憑證綁定單一請求」，這兩項在不需要 migration 的方案 R 也能達成；P 是在 R 之上追加「單次使用、可撤銷、可稽核」的升級選項。
2. 新增 ADMIN 登入狀態的事實：**ADMIN 的 Session 是無狀態簽章 Cookie，登出只清 Cookie，沒有服務端撤銷**（第 4.1 節）。
3. 新增統編 ASCII 收緊的影響分析與 F09 輸入來源盤點（第 9 節）。
4. 新增可獨立驗收的工作包與依賴（第 10 節）。

## 1. 先看結論

**可以立即實作（不依賴未決規則）**
- 表單 ⇄ `InvoiceSubmitRequest` 轉換層、多筆明細的核對表單（伺服器端渲染）。
- ADMIN 端 API 用戶端（假傳輸測試）與錯誤對應。
- 功能預設不註冊 → 無人可進入（fail closed）。
- 已完成：統編檢查碼測試（API、ADMIN）與 `/cases` 唯讀檢視。

**必須先跨組協調**
- ADMIN → API 的認證方式與金鑰責任（第 4 節）。
- 「新增案件」的功能代碼與可用部門（E，第 5 節）。
- 單位核准、會計覆核、核准、退回的狀態轉換與操作者（F，第 6 節）。
- `/cases` 案件範圍（E／F，第 7 節）。

## 2. 現有 ADMIN 上傳流程可沿用的部分

| 檔案 | 現況 | 整合時 |
|---|---|---|
| `routers/invoices.py` 的 `/new`、`/upload`、`/{id}/flow/upload`、`/{id}/file`、`/{id}/flow/correct` | 全是記憶體展示：整數 ID、`_DRAFT_INVOICE_STORE`、`file_bytes` 在行程記憶體；**沒有呼叫任何 AI**，「OCR」只是顯示暫存資料 | 不修改（F 組）。新流程放在獨立路由，不與展示資料混用 |
| `_flow_step_upload.html`（49 行） | 拖曳／選檔的上傳步驟 | 可沿用外觀，動作改指向新路由 |
| `_flow_step_ocr.html`（24 行） | 影像與辨識結果並排預覽 | 可沿用；內容改為 API `InvoiceRecognitionRead` |
| `_flow_step_correct.html`（225 行） | 14 欄位**單筆**核對，數字為整數 | **需重做**：API 是多筆明細、字軌與號碼分開、金額為字串小數、有 `source_line_no` |
| `_flow_step_check.html`（26 行） | 顯示系統檢核結果 | 可沿用，資料改為 API 警示 |
| `_flow_step_review.html` 以後 | 財務審核（F 組，展示資料） | 不動 |
| 前台 `mobile-camera.js`（約 1700 行） | 已有完整核對頁與修改紀錄追蹤 | ADMIN 不引用也不複製；`changes` 可留空，API 會補「有差異但沒紀錄」的欄位（失去中間修改歷程） |

## 3. ADMIN ⇄ API 的 Request／Response 資料流

瀏覽器只和 ADMIN 通訊；ADMIN **伺服器端**呼叫 API。

| 步驟 | ADMIN 動作 | API 呼叫 | 重點 |
|---|---|---|---|
| 1 上傳 | 驗登入、驗「新增」權限；讀檔（JPG/PNG/單頁 PDF，4 MB） | `POST /api/v1/invoices/recognize`（multipart `file`） | API 存影像到私有 `invoices` bucket、OCR、模型整理、寫 `ai_recognition_logs`；回 `recognition_id`、`invoice`、`printed`、`warnings`、`recognition`、`expires_at`（1 小時） |
| 2 核對 | 以辨識結果預填表單 | `POST /check-required`、`POST /check-warnings` | 純函式、不寫資料；警示含 `tax_id_checksum`、`tax_amount_mismatch`、`total_amount_mismatch`、`items_total_mismatch`、`invoice_date_future` |
| 3 查重 | 送出前 | `POST /check-duplicate` | 只回 `is_duplicate` |
| 4 提交 | 組出 `InvoiceSubmitRequest`（`recognition_id`、表頭、`items[]` 含 `source_line_no`、`changes[]` 可留空） | `POST /api/v1/invoices` | 201：`invoice_id`、`status`（API 目前寫入 `pending_accounting_review`，見第 6 節）、`warnings`、`modified_fields`；409 重複／已送出／逾時；422 格式；API 重新計算警示，不信任前端 |
| 5 查看 | 導向 `/cases/{invoice_id}` | （ADMIN 直讀 DB） | 讀同一筆資料與原圖 |

錯誤對應：401 → 重新簽發後最多重試一次；403 → 無權限；409 → 顯示對應訊息且不重送；422 → 逐欄顯示；5xx／逾時 → 明確顯示「尚未確認是否送出」，**不當成成功**（提交的重試方式見 4.3 的「冪等」）。

## 4. 認證整合（審查版）

### 4.1 事實：現有機制為何不能直接用，以及 ADMIN 登入狀態的真實性質

- API 只接受 `Authorization: Bearer <不透明 Token>`，以 SHA-256 摘要查 `user_sessions`；**不檢查 `app_scope`、`department_permissions` 或 Token 用途**，有效 Session 即可呼叫全部發票端點。
- ADMIN Session 是 itsdangerous 簽章 Cookie（內含 `uid`、`sid`、`department`、`cv`、`expires`、`functions`），**不在資料庫**。
- ADMIN 每次請求都會向 DB 重新驗證：帳號狀態、`credential_version`、離職、部門成員關係（`DepartmentLoginAdapter.current`）。
- **ADMIN 登出沒有服務端撤銷**：`DepartmentLoginAdapter.logout` 是空實作（註解「由路由清 Cookie 結束」），已簽發的 Cookie 在到期前（`SESSION_MAX_AGE_SECONDS` 預設 8 小時）仍有效；切換部門會簽發新 Cookie，舊 Cookie 只要該部門仍是成員也仍可用。E 模組的註解寫「先由 B 撤銷服務端 Session」，與實作不一致，建議 B／E 確認。
- 因此「ADMIN 使用者目前有登入」只能以 **簽章有效＋未過期＋DB 重新驗證帳號與部門** 為準，無法像 `user_sessions` 那樣立即撤銷單一 Session。

### 4.2 信任模型（必讀，影響所有方案的評估）

ADMIN 伺服器**本來就握有 DB 連線憑證**（與 API 是同一個 PostgreSQL；DB 角色的逐表權限未逐項驗證，但 ADMIN 已有帳號、部門、知識庫等多處寫入功能，應假設其角色權限很大）。所以：
- 任何方案都**無法防禦 ADMIN 伺服器本身被入侵**：攻擊者很可能已能直接讀寫資料表，包含 `user_sessions` 或 `invoices`（權限範圍需 DB 管理者確認）。
- 方案的實際價值是：(a) 讓 **API 端**對每次操作自行強制身分、部門、權限與範圍，不靠 ADMIN 的宣告；(b) 防止 ADMIN 程式 bug 或誤用（例如帶錯使用者）造成越權；(c) 縮小憑證洩漏（記錄、傳輸、備份）時的影響；(d) 提供 API 端稽核。
- 不應向決策者宣稱它能抵抗 ADMIN 執行環境被攻破。

### 4.3 修訂後的最小設計

**A1　使用者身分的可信來源**
- ADMIN 簽發任何委派請求前，必須先通過現有 `module.identity(session_evidence(request))`（Cookie 簽章、有效期、DB 重新驗證帳號與部門）與 `module.check(…, Action.CREATE, Target(部門))`。
- 委派內容中的 `sub`（使用者）與 `dept`（部門）**只取自這個已驗證的 `Identity`**，路由、表單、查詢參數、標頭中的 `user_id`／`department_id` 一律忽略，並有測試證明。
- API **不信任**這些宣告：收到後自行查 DB 驗證帳號啟用、未鎖定、profile 啟用且未離職、`credential_version` 相符、使用者為該部門的現行成員、部門啟用、該部門對指定功能擁有對應操作權限。API 沒有辦法確認「ADMIN 使用者此刻是否真的還登入著」（ADMIN Session 不在 DB），這是上面 4.1 的限制。

**私鑰外洩時攻擊者能做的事**
- 能：為任何**確實擁有該權限**的使用者，在其現行部門內，發出受限操作（辨識、檢核、提交）；也就是以他們的名義上傳假發票、消耗 OCR／Groq 額度與 Storage、造出 `pending_department_approval` 案件。
- 能：用錯誤差異探測哪些使用者／部門存在或有權限（需以一致的錯誤回應緩解）。
- 不能：讀取任何案件、列表、詳情、影像；使用沒有權限的帳號；呼叫其他 API 端點；長期持有（簽章只有 60 秒，但攻擊者可持續自行簽發，直到公鑰被撤下）。
- **重新查詢權限消除不了**：以有權限者身分的「合法外觀」操作（內容是否真實無從判斷）、速率濫用、以及在簽發窗口內的行為。緩解：API 端速率限制與告警、每次委派呼叫記錄（`jti` 或請求雜湊、使用者、部門、操作、`kid`，不記錄內容）、發現異常時撤下該 `kid` 的公鑰並輪替。

**A2　憑證設計與限制（以方案 R 為基準）**
- 格式：JWS 緊湊序列，**只允許 EdDSA（Ed25519）**；拒絕 `none`、HS*、RS* 與未知 `alg`／`typ`。`cryptography` 已隨 `google-auth` 安裝於兩站。
- 標頭：`alg=EdDSA`、`typ=ig-delegation+jwt`、`kid`。
- 宣告（皆必填，缺一即拒絕）：`iss`（固定 `invoiceguard-admin`）、`aud`（固定 `invoiceguard-api`）、`sub`（使用者 UUID）、`dept`（部門 UUID）、`cv`（`credential_version`）、`op`（`recognize`／`check`／`submit`）、`htm`（HTTP 方法）、`htu`（路徑）、`bind`（請求綁定：JSON 為原始 body 的 SHA-256；`recognize` 為檔案內容的 SHA-256）、`iat`、`exp`（`exp - iat ≤ 60 秒`，允許 ±30 秒時差）、`jti`（隨機，**只供稽核；R 沒有儲存 `jti`，所以它不能防止重放**，見第 12.2 節）、`ads`（ADMIN `sid` 的雜湊，僅供稽核）。
- **操作範圍／對象**：`op`＋`htm`＋`htu`＋`bind` 同時符合才通過，所以同一份憑證不能改用在別的端點、別的方法或不同內容。只對五個端點生效（`recognize`、`check-required`、`check-warnings`、`check-duplicate`、`POST /invoices`）；`/auth/*`、列表、詳情、登出一律不接受。
- **有效期限**：60 秒。每次呼叫由 ADMIN 重新簽發，不需要保存任何憑證，也不寫入 Cookie、HTML、日誌。
- **重放**：憑證綁定請求內容＋60 秒＋既有冪等（見下）。完整的單次使用需要儲存 `jti`（資料表＝方案 P）；R 不做，接受「60 秒內重放同一個完全相同的請求」的殘餘風險：`recognize` 會重複 OCR 並多一筆辨識紀錄（成本與額度），`check` 無副作用，`submit` 會被 `RECOGNITION_ALREADY_SUBMITTED`／重複發票檢查擋下。
- **金鑰輪替**：API 設定 `kid → 公鑰` 的對照（可同時有兩把）。輪替＝API 新增新公鑰並部署 → ADMIN 改用新私鑰並部署 → API 移除舊公鑰。緊急撤銷＝API 移除該 `kid` 並部署（需要有 Vercel 設定權限的人；開發者沒有）。
- **停權／撤銷／權限變更**：因為每次呼叫 API 都重新查 DB，帳號停用、密碼變更（`credential_version`）、離開部門、部門停用、功能權限收回，在**下一次呼叫**立即生效；已簽發但未使用的憑證最多再有 60 秒。ADMIN 端同樣每個請求重驗。登出無法撤銷 ADMIN Cookie（4.1），所以不宣稱「登出即撤銷」。
- **瀏覽器不暴露伺服器端憑證**：私鑰只在 ADMIN 伺服器環境變數；簽章與呼叫在伺服器端完成；回應給瀏覽器的只有業務結果。
- **連續流程**：辨識 → 修正 → 檢核 → 提交 → 其間可相隔數分鐘，因為每個請求獨立簽發，沒有「憑證先過期」的問題。限制只來自 API 既有規則：辨識結果 1 小時內未送出即失效（`expires_at`）；ADMIN 登入過期（預設 8 小時）時，需要重新登入並**重新上傳**（API 沒有「重新取得既有辨識結果」的端點）。
- **冪等與重複提交**：API 的 `submit()` 已經天然冪等——`recognition_id` 只能送出一次（第二次 409 `RECOGNITION_ALREADY_SUBMITTED`）、字軌＋號碼＋賣方統編在 advisory lock 下查重（409 `INVOICE_DUPLICATED`），交易全有全無，所以失敗重試不會產生兩筆。ADMIN 在逾時／網路錯誤時：不顯示成功；先以 `ai_recognition_logs.invoice_id`（提交成功後由 API 回填）確認；已有則導向該案件；沒有則允許使用者重新送出（新憑證）。不需要額外的 idempotency key。
- **一致的錯誤**：憑證無效、過期、簽章錯誤、使用者不存在、無權限，對外統一回 401／403 的固定訊息，不洩漏原因；細節只寫 API 內部記錄（不含憑證與請求內容）。

**A3　API 端自行驗證（具體做法）**
1. 新依賴 `resolve_actor(op)`：若請求帶前台 Session Token，走既有 `auth_service.resolve_session`（行為不變）；若帶委派憑證，依 A2 驗證，**之後**查 DB：
   - 帳號／profile／`credential_version`／部門成員（沿用 `user_repository` 既有查詢）；
   - 功能權限：建議使用 v1.8 草案已定義的 **`v_user_department_permissions`**（以 `verified_user_id＋selected_department_id＋function_code` 查詢，註解寫明這是部門模式的權威來源），條件 `app_scope IN ('admin','both')`、`is_active`，並要求 `can_create`（辨識／提交；檢核類也要求 `can_create`，因為屬於新增流程的一部分）。**須先確認正式庫有這個 View**；ADMIN 目前用自己的 SQL（`admin_login_repository.functions`），兩邊必須使用同一來源與同一過濾條件，避免結論不一致。
2. 產生與現有 `SessionRead` 相同形狀的 actor（`user_id`、`department`＝已驗證的 `dept`、`departments`），讓 `invoice_upload_service` 的所有既有授權不變：辨識紀錄只能由 `requested_by` 本人送出（`lock_for_submit`）、`department_id` 取自該 actor、影像路徑以 `{user_id}/…` 命名。
3. 案件存取範圍：這五個操作只涉及本人建立的辨識紀錄與新增案件，不讀取他人案件；`check-duplicate` 只回布林值。
4. 憑證限制：`op/htm/htu/bind` 比對放在服務層（`recognize` 需先讀完檔案才能算雜湊），失敗一律拒絕。
5. 前台 Session 路徑完全不變；API 其他端點不接受委派憑證（以測試固定）。

### 4.4 方案比較（含沿用現有認證架構的替代方案）

| 方案 | 說明 | 實作／維護成本 | Migration | 新增金鑰責任 | 影響前台 | 專題時程內可驗收 | 可用隔離環境完整測試 |
|---|---|---|---|---|---|---|---|
| **R　單請求簽章宣告（建議的最小基準）** | 如 A2；每次呼叫簽發，API 驗證並重查 DB | 低：API 一個依賴＋一個公鑰設定；ADMIN 一個簽章函式 | **否** | 是（一對金鑰＋輪替） | 否 | 高 | 是（測試用臨時金鑰對） |
| **P　委派憑證換發（原提案）** | R 之上再多一步：API 驗證後發行單次／限次的不透明 Token | 中高：多一個端點、一張表、清理排程、兩次往返 | **是**（新表；v1.8 草案與 `app/db/README.md` 同步更新） | 是 | 否 | 中 | 是 |
| **Z　重新登入換前台 Session** | ADMIN 請使用者再輸入密碼，伺服器端轉呼叫既有 `POST /auth/login`，取得一般前台 Session | 低 | 否 | 否 | 否 | 高 | 是 |
| Q　擴充 `user_sessions`（種類、範圍） | ADMIN 為使用者建立受限 Session | 高 | 是（改既有表） | 否 | **是**（`resolve_session` 語意改變） | 低 | 是 |
| 靜態服務 Token＋body 帶 `user_id` | 最簡單的服務間呼叫 | 最低 | 否 | 是 | 否 | 高 | 是 |

評估：
- **Z 的問題**：取得的是**不受限的前台 Session**（可呼叫全部前台端點，違反「Token 不能做所有發票操作」）；ADMIN 伺服器需轉手使用者密碼；Token 必須暫存（無狀態部署下只能放進 Cookie＝讓瀏覽器持有 Bearer）；登入選的是主要部門而非目前部門。它唯一的優點是完全沿用現有架構，因此列為「若時程不允許任何 API 修改的最後備案」，需 B／E 明確接受上述限制。
- **靜態服務 Token＋body 帶身分**：API 只能相信 ADMIN 的宣告，無法自行驗證；不採用。
- **Q**：影響前台所有端點的認證路徑，風險與回歸範圍最大；不採用。
- **R vs P**：兩者的信任根相同（ADMIN 私鑰）；P 多出的只有「單次使用、明確撤銷、發行稽核」，代價是 migration、清理與兩次往返。對本專案的威脅模型（4.2）而言，這些額外價值有限；建議**先做 R**，若 API／B／E 認為需要單次使用與撤銷，再把 P 作為在 R 之上的追加（ADMIN 的簽章與用戶端幾乎不用改）。
- **R 的已知缺口**（要讓審查者明確接受）：沒有 per-credential 撤銷（只有 60 秒＋逐次重查）；沒有嚴格單次使用（殘餘風險如上）；金鑰撤銷需要部署。

### 4.5 需要 API 與 B／E 組回答的具體問題

**API 擁有者（JerryChien／Rayne Li）**
1. 可接受新增 `resolve_actor` 依賴與公鑰設定（R）嗎？還是要求 P（含新表）？
2. 委派呼叫要新增的稽核記錄放哪裡（現有日誌？`audit_logs`？）？
3. 功能權限檢查要使用 `v_user_department_permissions` 嗎？正式庫是否已有此 View？
4. `RECOGNITION_ALREADY_SUBMITTED` 的 409 能否在回應中帶回 `invoice_id`（減少 ADMIN 查 DB 的需要，屬契約新增欄位）？
5. 速率限制要放在 API 還是平台層？

**B／E 組**
1. ADMIN 登出沒有服務端撤銷、切換部門不會讓舊 Cookie 失效——是否為設計決定？對委派呼叫的影響已評估為可接受嗎？
2. 「新增案件」是否使用獨立功能代碼（`app_scope='admin'`）？目前 `Triplicate Uniform Invoice` 同時是前台功能，給 ADMIN 部門 `can_create` 可能同時開前台。
3. 可新增的部門／角色；財務部、資訊部是否預設都沒有。
4. ADMIN 與 API 兩邊的權限判斷是否同源（建議共用 `v_user_department_permissions`）。

## 5. 把「ADMIN 能執行新增流程」和「誰能新增」分開

- **能力（程式）**：新路由與 Service 完成後，預設**不註冊功能代碼**，等於沒有任何人能進入；測試以隔離身分在測試註冊表覆寫。
- **誰能新增（授權）**：只由 `department_permissions.can_create` 決定，預設沒有任何部門。財務部、資訊部**不因為是 ADMIN 使用者而自動取得**。
- 正式庫 `app_functions` 的實際設定**無法確認**，需要經批准的唯讀查詢。

## 6. 案件狀態：既有流程、實作現況與缺口

（**更正 2026-11-03**：API PR #14 已合併，首次提交狀態為 `pending_accounting_review`（`invoice_upload_service.py` 的 `SUBMITTED_STATUS`），團隊 2026-10-30 決定只留財務一層審核。下表中 `pending_department_approval` 為**舊值**，保留是因為資料庫狀態約束仍允許；ADMIN 不改狀態規則。）

| 狀態（`invoices_status_chk`） | 目前誰會寫入 | 缺口 |
|---|---|---|
| `draft` | 無 | 暫不處理 |
| `pending_department_approval`（舊值） | **無**（PR #14 之後 API 不再寫入） | 資料庫約束仍允許；既有資料可能存在 |
| `pending_accounting_review` | **API `submit()`**（同交易寫 `invoice_status_history`） | 已實作；未在正式環境驗證；後續覆核、核准、退回由 F 組實作 |
| → `pending_accounting_approval` | **無** | 是否啟用「會計核准」層由團隊決定 |
| → `approved` | **無** | 缺核准操作 |
| `returned_to_applicant`／`returned_to_accounting` | **無** | schema 有 `invoice_field_reviews.is_checked` 與退回語意；ADMIN 的退回補正（F）只在記憶體 |
| `rejected`／`voided` | **無** | 未定義操作者與條件 |

F 組現有財務審核使用**另一套 5 種展示狀態**且全在記憶體，與 API 9 種簽核狀態沒有對照，**不能硬套**；需要 F 提供正式對照與每個轉換的操作者。新增案件會停在 `pending_accounting_review`，ADMIN 與 API 目前都沒有程式能讓它前進（財務覆核屬 F 組）。

## 7. 提交後財務部如何看到案件

現況：不會。案件 `department_id` 是提交者部門；`/cases` 預設拒絕；E 對一般功能只給「目前選取部門」範圍（`company` 只有 `sys.*`）；API 提交後的 `pending_accounting_review` 正是財務覆核的起點，但財務看見案件的範圍尚未核定。
可行順序：E／F 核定財務的案件範圍 → `/cases` 改用 `Decision.scope` 與 `Target(案件部門, 提交者)` → 財務可唯讀看到「待會計覆核」案件。

## 8. 測試狀態

**已通過（不連正式服務；最新數字見第 12.6 節）**
- 統編檢查碼、`/cases`、W1～W3 的測試與隔離 PostgreSQL 整合測試，詳見第 12.6 節。

**尚未執行**：認證方案相關的任何測試（尚未實作）；ADMIN 與**真實** API 的上傳／提交整合測試（需要認證核定；目前只有假傳輸與 API 模型契約測試）。
**尚待驗證**：正式 DB／Storage、真實 Document AI／Groq、部署、真實瀏覽器、`v_user_department_permissions` 在正式庫是否存在、Supabase pooler 下的唯讀交易。

**認證實作後的測試分層（草案）**
1. API 單元：簽章錯誤、`alg=none`／HS／RS、未知 `kid`、`aud`／`iss` 錯誤、`exp` 過期或超過 60 秒、`op/htm/htu/bind` 任一不符、缺宣告；帳號停用、`credential_version` 不符、非部門成員、部門停用、缺權限；憑證打到 `/auth/me`、列表、詳情必須拒絕；前台 Session 對五個端點仍可用（迴歸）；錯誤訊息一致且不含憑證。
2. ADMIN 單元：`sub/dept` 只取自已驗證 Identity（偽造表單／查詢參數的 `user_id`、`department_id` 無效）；假傳輸測試用戶端（逾時、401 重試一次、409、422、5xx 不當成功）；憑證不出現在 HTML／日誌／Cookie；功能未註冊 → 403。
3. 契約：ADMIN 的請求樣本交 API 的 Pydantic 模型驗證。
4. 隔離整合：隔離 PostgreSQL（需要 TLS 自簽憑證）＋兩個本機行程＋Document AI、Groq、Storage 替身＋測試金鑰對：授權帳號完成上傳 → 辨識 → 檢核 → 提交 → `/cases` 讀到同一筆；未授權、跨部門、重複提交、辨識逾時、Storage 失敗各一條負面案例。

## 9. 統編檢查碼（ASCII 收緊與 F09 相容性）

**維持**：2023 年新制 `S % 5 == 0`；第 7 碼為 7 時另可 `(S + 1) % 5 == 0`；檢查碼失敗只產生警示，不阻擋提交。

**F09 統編輸入來源（已逐一查證）**
| 呼叫點 | 輸入來源 | 能否出現非 ASCII 數字 |
|---|---|---|
| `validate_record` → `is_valid_ban(record.seller_ban)` | `check_snapshot` 以 `str(snapshot.get("seller_tax_id") or "")` 取值，**不做正規化**；快照由 `routers/invoices.py::_to_filing_snapshot` 自記憶體展示發票轉換；該欄位來自 `/flow/correct` 表單的 `seller_tax_id.strip()` | **可能**：使用者在 ADMIN 展示流程輸入全形數字，目前會通過 `is_valid_ban`；之後寫入 81 字元申報行時寬度與位元組都會錯 |
| `validate_profile` → `is_valid_ban(profile.ban)` | 寫死的公司資料 `HardcodedCompanyProfileProvider`（`04595252`） | 否 |
| 未來真實案件來源 | API `submit()` 寫入的 `invoices.seller_tax_id` | 否：提交 schema 以 `[0-9]{8}` 先擋下 |
| 同檔其他 `\d` | `re.fullmatch(r"\d{9}", tax_reg_no)`、`\d{8}`（發票號碼） | 同樣會接受 Unicode 數字（**同類問題，不在本次範圍**） |

另外：`04595252` 是公司自己的統編，它**只在新制通過**（舊制 `% 10` 不通過），所以這次收緊絕不能動到 `% 5`。

**最小修改方案（尚未實作）**
- API：`valid_tax_id` 的 `re.fullmatch(r"\d{8}", …)` 改成 `r"[0-9]{8}"`（一行）——**已在 API 分支 `feature/tax-id-checksum-tests` 完成（未 Commit）**，原 `xfail(strict)` 已移除並改為 7 項一般測試。
- F09（F 組決定或授權）：`is_valid_ban` 同一行改動。影響：全形統編由「通過檢查碼」變成 `BAD_BAN`，反而阻止壞資料進入申報檔；不影響 ASCII 輸入；`test_format21_service.py` 現有 3 項斷言不受影響。
- 測試：兩邊 `VECTORS` 加入 `０４５９５２５７`、`٠٤٥٩٥٢٥٧`、`٠٠٠٠٠٠٠٠` 預期 False；跨站比對測試在兩邊都完成後才含這些輸入。
- 順序：API 與 F09 的改動**不必同一天**——ADMIN 的比對測試目前只含兩邊都為 False 的非 ASCII 向量（`１２３４５６７８`），只改一邊時仍通過；加入「數學上通過的全形統編」向量時需兩邊都已完成。
- `xfail(strict)` 的處置：API 端**已移除**（修正已完成）。ADMIN 的 `test_tax_id_parity.py` 仍保留一個 `xfail(strict)`（`test_f09_rejects_non_ascii_digit_ids`），因為 F09 尚未修改；移除條件＝F 組把 `format21_service.py` 的 `\d{8}` 改成 `[0-9]{8}`，並把 `NON_ASCII_DIGIT_IDS` 加入逐筆比對取樣。單邊修正期間，其餘比對測試（含 API 拒絕全部非 ASCII 向量）照常通過。
- 影響評估：提交路徑不受影響（輸入已被 `[0-9]{8}` schema 擋下）；**辨識路徑有一個小行為變化**：`normalize_tax_id` 只會把全形轉成 ASCII，**不會**轉阿拉伯-印度數字（第二版的敘述有誤，已更正）。AI 若辨識出阿拉伯-印度數字的統編，以前會因 `\d` 誤判通過、不出現警示，現在會出現「檢查碼不符」警示——結果更正確，但訊息文字不夠精準（實際是字元不是 ASCII）。

## 10. ADMIN 完整流程：可獨立驗收的工作包

| # | 工作包 | 驗收方式 | 現在可獨立實作 | 需跨組協調 | 需先完成認證才能整合測試 | 業務規則未核定 |
|---|---|---|---|---|---|---|
| W1 | ADMIN 欄位映射（表單 ⇄ API，含 `source_line_no`、金額字串） | 純函式單元測試＋API 模型驗證樣本 | ✅ | | | |
| W2 | 多筆明細與人工修正的核對表單（伺服器端渲染；`changes` 可留空） | 渲染測試、表單轉換測試 | ✅ | | | 國字大寫格式（財會確認） |
| W3 | 呼叫現有辨識／檢核 API 的用戶端（假傳輸） | 逾時、重試、錯誤對應單元測試 | ✅（用戶端與錯誤處理） | | 真實呼叫需認證 | |
| W4 | 安全認證與受限操作（R，或 P） | 4.5 節與第 8 節測試 | 文件與測試設計 ✅ | ✅（API 擁有者、B／E） | | 新增案件功能代碼與部門 |
| W5 | 真實提交與狀態保存（ADMIN 端流程、冪等處理） | 隔離整合：提交後以 `/cases` 讀回 | 協調層與假 API 測試 ✅ | | ✅（真實 API 呼叫） | |
| W6 | ADMIN 真實案件讀取 | **已完成**（`/cases`，隔離 DB 26 項通過） | ✅ | 案件範圍（E／F） | | 案件範圍 |
| W7 | 單位核准 | 狀態轉換＋歷程同交易測試 | | ✅ | | ✅ 誰核准、如何對應狀態 |
| W8 | 會計覆核、核准、退回 | 同上，並與 F 組現有審核畫面整合 | | ✅（F 組） | | ✅ 5 種展示狀態 ⇄ 9 種簽核狀態、操作者、退回條件 |

**建議順序**
1. 本輪：W1、W2、W3 的設計與測試（不碰認證與狀態）。
2. API／B／E 回答 4.5 節問題後：W4（R）→ W5（隔離整合）。
3. E／F 核定案件範圍與狀態對照後：W6 改用 E 機制 → W7 → W8。
4. 每個工作包完成都保持「功能未註冊／預設拒絕」，直到權限核定才由 E 開放。

## 11. 其他待決定事項

- 正式庫唯讀結構查詢（`app_functions`、`department_permissions`、`v_user_department_permissions` 是否存在及內容）是否批准。
- 統編檢查碼失敗是否維持警示（目前維持）。
- ADMIN 新增案件與讀取案件的稽核要求。
- 提交後直接進「待會計覆核」（PR #14 已決定）；財務看見案件的範圍與覆核操作仍待 E／F 核定。

## 12. 第三版補充

### 12.1 對前兩版的更正與補充

1. **`jti` 在方案 R 不能防止重放**：R 不儲存 `jti`，它只是稽核識別碼。要真的防重放需要共享儲存（資料表＝P 的一部分，或單機記憶體——serverless 不可靠）。前版寫「選用的重放偵測」容易誤導，已更正。
2. **`normalize_tax_id` 只把全形轉成 ASCII，不轉阿拉伯-印度數字**（前版說兩者都轉，有誤）。
3. **API 目前對前台 Session 沒有任何功能權限檢查**（只看 Session）。所以 R／P 在 API 端加上的 `department_permissions` 檢查只會約束「委派呼叫」；同一位使用者仍可用前台 Session 直接提交，不經過該檢查。這代表：若不同時要求 API 對所有發票提交都檢查功能權限（業務政策，可能影響前台既有使用者，須 B／E／API 決定），API 端的授權在實際效果上只是**縮小 ADMIN 發起的呼叫**，不是整體的授權邊界。

### 12.2 對方案 R 的重點檢查

| 問題 | 結論 |
|---|---|
| 60 秒內重放 | 可以。攻擊者需先取得完整請求（TLS 之內、伺服器對伺服器，不經瀏覽器）。重放同一份請求的效果：`check-*` 無副作用；`submit` 會被 `RECOGNITION_ALREADY_SUBMITTED`／重複發票擋下；`recognize` 會**再跑一次 OCR 與模型並多一筆辨識紀錄與影像**（成本與額度），因為 `bind` 只綁檔案內容，不防止同一檔案被重複辨識 |
| `jti` 能防重複請求嗎 | **不能**（見 12.1）。若要防止，需要共享的已用 `jti` 儲存；選項：新資料表（migration）、或在 `ai_recognition_logs.raw_response`（JSONB，不需 migration）記錄來源檔案雜湊，讓 API 對同一使用者短時間內相同檔案重用既有辨識（同時也解決使用者連點兩次造成的重複 OCR） |
| API 能區分「使用者授權」與「ADMIN 伺服器身分」嗎 | 能區分兩層：`iss`＋簽章＝哪個伺服器在呼叫；`sub`＋DB 查詢＝這位使用者此刻是否有權限。**但「使用者此刻正在 ADMIN 登入」只有 ADMIN 能證明**，API 無法獨立驗證（ADMIN Session 不在 DB） |
| ADMIN 登出後舊 Cookie 仍有效 | 對 R 是直接影響：只要 Cookie 簽章有效且未過期，ADMIN 就會替它簽發宣告。選項：(a) 接受（Cookie 外洩才有風險，且仍受 DB 重驗帳號／部門／權限約束）；(b) 新增流程要求 Cookie 的簽發時間在 N 分鐘內（簽發時間可由 `expires − SESSION_MAX_AGE_SECONDS` 推得），逾時需重新登入；(c) 在 B／E 的登入服務加入服務端撤銷（需要狀態儲存，屬登入契約變更） |
| 私鑰外洩 | 見 4.3 的 A1：可為任何「確實有權限」的使用者發起受限操作，不能讀案件、不能冒用無權限者。補充：ADMIN 執行環境若被入侵，攻擊者同時握有 DB 憑證，R 無法再提供保護（4.2） |
| OCR 重複請求 | 見上，需要 API 端以檔案雜湊去重或速率限制；ADMIN 端另外在按鈕上防連點（只是體驗，不是防線） |
| 提交重試 | 由 `recognition_id` 單次提交＋advisory lock 查重保證只會建立一筆；ADMIN 逾時時先以 `ai_recognition_logs.invoice_id` 查證（W3 已實作 `confirm_submitted`，並用隔離 DB 與 API `submit()` 寫入的案件驗證） |
| 稽核 | 目前 `invoice_status_history.changed_by` 是使用者、`reason` 是 API 固定文字，**看不出是 ADMIN 發起還是前台發起**。建議（不需 migration）在辨識紀錄的 `raw_response` JSON 記錄 `origin: "admin"` 與 `kid`；API 端另寫不含內容的委派呼叫日誌 |

### 12.3 方案 B：ADMIN 後端沿用 API 既有 Bearer Token／Session

**做法**：ADMIN 在使用者登入的當下（ADMIN 本來就收到並驗證密碼），同一份密碼轉呼叫 API 的 `POST /api/v1/auth/login`，取得 API 自己發行的不透明 Token；之後 ADMIN 後端以 `Authorization: Bearer <該 Token>` 呼叫五個發票端點。Token 由 API 合法簽發，ADMIN 沒有偽造任何東西。

依實際程式查證：

| 問題 | 結論（依實際 Repository） |
|---|---|
| 能否沿用 API 登入與 Session 服務 | 能。`auth_service.login`（Argon2id 驗證、建立 `user_sessions`、預設主要部門、`SESSION_EXPIRE_MINUTES` 預設 60 分鐘、上限 1440）；`resolve_session` 每次重驗摘要、到期、撤銷、`credential_version`、帳號／profile、選取部門；`logout` 撤銷摘要 |
| ADMIN 如何合法取得 Token | 只有兩條路：(1) 用使用者的密碼呼叫 API 登入（登入時轉送，或之後再要求輸入一次密碼）；(2) 自己用 DB 模組寫入 `user_sessions`（自行簽發＝偽造，不可接受）。因此 B 必然讓 ADMIN 在登入時把密碼再轉給 API |
| 是否需要修改 `user_sessions` | **不需要**，也不需要 migration |
| 能否限制 Token 只能執行授權操作 | **不能**。`user_sessions` 沒有用途／範圍欄位，`resolve_session` 對所有前台端點一視同仁。持有該 Token 就等於該使用者的前台 Session（只能存取自己的資料）。要限制就需要 Q（改 Session 表與全部端點）或改走 R／P |
| ADMIN 登出 | 目前 ADMIN `logout` 是空實作；B 需改為呼叫 API `POST /auth/logout`（撤銷 Token）。這屬於 B／E 的登入服務 |
| 停權 | 立即生效：API 每次請求重驗帳號、`credential_version`、部門 |
| **切換部門** | **有缺口**：API 沒有切換部門的端點；Session 的 `selected_department_id` 在登入時固定為主要部門。ADMIN 的切換只更新帳號的主要部門與 ADMIN Cookie，API Token 仍是舊部門，提交會用**舊部門**。需新增 API 端點（例如選取部門，驗證成員關係後更新該 Session），或強迫重新登入（ADMIN 此時沒有密碼） |
| **有效期限不一致** | API 預設 60 分鐘、ADMIN Cookie 8 小時。需把 API 設為相同期限（設定值，須 API 擁有者）或加入續期端點；否則使用者過一小時就無法上傳 |
| Token 放哪裡 | ADMIN 是無狀態部署，沒有伺服器端儲存；只能放在瀏覽器 Cookie（另一個 httpOnly Cookie；不能塞進現有 Session Cookie，因為 `session_data` 會嚴格檢查鍵集合）。這是使用者自己的 Session Token，不是伺服器對伺服器憑證，但它會暴露在使用者的瀏覽器 |
| 密碼處理 | ADMIN 登入服務要把明文密碼轉給 API（兩者都在同一團隊的 Vercel 專案，但密碼多經過一個伺服器邊界） |
| 對共用認證的影響 | 修改 B／E 擁有的 `DepartmentLoginAdapter.login/logout/switch_department` 與 Cookie 結構 |

### 12.4 三個候選方案的比較（需修改的檔案與負擔）

| | **R　單請求簽章宣告** | **P　換發單次憑證** | **B　沿用 API Bearer Session** |
|---|---|---|---|
| 需要修改（API） | `core/config.py`（公鑰設定）、新 `services/delegation_service.py`（驗章＋DB 重查）、`core/deps.py`（`resolve_actor`）、`routers/v1/invoices.py`（五個端點換依賴）、權限查詢 repository、測試 | R 全部＋新端點、新 repository、新表與 Alembic migration、v1.8 草案 SQL 與 `app/db/README.md`、清理排程 | 新端點（選取部門）＋測試；設定 `SESSION_EXPIRE_MINUTES`；**無 migration** |
| 需要修改（ADMIN） | 新 `integrations/api_delegation_signer.py`（實作 `CredentialProvider`）、`core/config.py`（私鑰、API 位址）、`build_intake_client`、START 環境白名單、測試 | 同 R，用戶端多一次換發 | **`services/admin_login_service.py`（登入、登出、切換部門，E 擁有）**、Cookie 結構、`CredentialProvider` 實作（讀 Token Cookie）、測試 |
| 新增概念／責任 | 一對金鑰與輪替 | 金鑰、Token 資料表、清理 | 無新金鑰、無新表；但 ADMIN 與 API 的 Session 生命週期要互相對齊 |
| 是否需要 migration | 否 | **是** | 否 |
| 影響前台 | 否 | 否 | 否（新端點只增不改） |
| 影響共用認證（B／E） | 否（ADMIN 登入不變） | 否 | **是** |
| API 能限制 ADMIN 發起的呼叫 | 能（單一操作、請求綁定、60 秒） | 能（再加單次使用） | **不能**（不受限的使用者 Session） |
| API 端強制「ADMIN 新增」權限 | 能（僅約束委派路徑，見 12.1） | 能（同左） | 不能；只能靠 ADMIN 後端 `module.check` |
| 撤銷 | 60 秒＋逐次重查 | 有明確撤銷與單次使用 | 原生撤銷（登出、停權、改密碼） |
| 稽核 | 需新增 | 發行紀錄天然可稽核 | 與前台無法區分 |
| 專題時程內可驗收 | 高 | 中 | 中（卡在切換部門與期限對齊） |
| 隔離環境可完整測試 | 是 | 是 | 是 |

**判斷原則（供決策，不預設答案）**
- 若團隊接受「ADMIN 發起的呼叫＝該使用者的一般前台 Session」，且能接受 ADMIN 登入服務的修改與切換部門的新端點：**B 新增的概念最少**（沒有金鑰、沒有新表）。
- 若要求 API 必須把 ADMIN 發起的呼叫限制得比一般前台 Session 更窄（單一操作、可稽核、不能呼叫其他端點）：**R 是最小的做法**；只有在明確需要單次使用與撤銷時才升級為 P。
- 無論選哪個，12.1 第 3 點的問題（API 要不要對所有提交檢查功能權限）要先回答，否則 API 端的授權沒有整體意義。

### 12.5 W1～W3 實作狀態（本輪）

狀態：**測試通過、待整合**（假傳輸與 API 模型契約；**尚未與真實 API 串接**）。

| 工作包 | 檔案 | 說明 |
|---|---|---|
| W1 欄位映射 | `app/schemas/intake.py`、`app/services/invoice_intake_mapping.py` | 辨識結果 → 表單；瀏覽器表單 → 表單；表單 → check／duplicate／submit 請求內容；422／缺漏／警示 → 畫面欄位位置；14 項欄位與 API 欄位對照（備註 API 沒有、聯式識別只供參考） |
| W2 核對表單 | `app/routers/intake.py`、`app/templates/pages/intake/{upload,review,done}.html` | 14 項核對欄位＋多筆明細（新增／刪除列，無 JavaScript）；顯示 API 的辨識提醒、必填缺漏、業務警示、查重；不複製檢核規則 |
| W3 API 用戶端 | `app/integrations/api_intake_client.py`、`app/services/invoice_intake_service.py` | 五個端點；憑證提供者介面（預設拒絕）；逾時／連線／回應格式／大小／轉址處理；提交結果不明時以 `ai_recognition_logs.invoice_id` 唯讀查證（`case_repository.find_invoice_id_for_recognition`） |
| 註冊 | `app/main.py` | 加入 `/intake` 路由 |

**雙重關閉**：(1) 功能代碼 `inv.intake` 不在正式功能目錄，`module.check` 一律 403（以真實 `DepartmentLoginAdapter` 測試：即使部門被授予所有功能的全部操作也拒絕）；(2) `build_intake_client` 在認證與 API 位址核定前一律拒絕（503）。
**未做／刻意不做**：原圖並排預覽（需要新的 Storage 讀取授權，列為後續）、`API_BASE_URL` 與私鑰等設定欄位（隨認證方案核定後加入）、單位核准以後的狀態轉換、修改 `/invoices`。
**既有展示頁的說明文字**（「不會將發票圖檔存入資料庫」）與真實流程矛盾（API 會把影像保存在私有 Storage）；新頁面沒有沿用，F 組的展示頁需另行處理。

### 12.6 測試狀態（本輪）

| 範圍 | 指令／環境 | 結果 |
|---|---|---|
| API 統編測試 | `pytest tests/unit/test_tax_id_checksum.py` | 34 passed |
| API 全套 | `pytest` | 1188 passed、14 skipped |
| ADMIN W1 映射 | `tests/unit/test_invoice_intake_mapping.py` | 12 passed |
| ADMIN W3 用戶端（假傳輸） | `tests/unit/test_api_intake_client.py` | 26 passed |
| ADMIN 路由（假 EModule＋假 API） | `tests/unit/test_intake_routes.py` | 27 passed |
| ADMIN 契約（以 API 的真實 Pydantic 模型與純函式） | `tests/unit/test_intake_api_contract.py`（子行程使用 API 的 `.venv`） | 7 passed |
| ADMIN 真實權限鏈（`inv.intake` 未註冊） | `tests/unit/test_intake_permission_chain.py` | 2 passed |
| ADMIN 統編一致性 | `tests/unit/test_tax_id_parity.py` | 20 passed、1 xfailed（F09 尚未修正） |
| ADMIN 隔離 PostgreSQL 整合 | `ISOLATED_CASE_DB_URL=… pytest tests/integration` | 29 passed |
| ADMIN 全套（含隔離 DB） | 同上，排除缺 Pillow 的檔案 | 2538 passed、9 skipped、1 xfailed、1 failed（Pillow 環境問題） |
| ADMIN 全套（無 DB 環境變數） | | 2510 passed、37 skipped、1 xfailed、1 failed（同上） |
| 靜態檢查 | ruff、format、djlint、`check_project.py`、環境檢查 | 全部通過 |

**未執行／無法驗證**：與真實 API 的 HTTP 串接與認證、真實 Document AI／Groq／Storage、正式 DB（已準備唯讀查核提案，見 [PROD_READONLY_QUERY_PROPOSAL.md](PROD_READONLY_QUERY_PROPOSAL.md)，尚未執行）、部署、真實瀏覽器。

### 12.7 給 F 組的問題（統編 ASCII）

1. 同意把 `format21_service.py` 的 `is_valid_ban` 由 `re.fullmatch(r"\d{8}", ban)` 改成 `r"[0-9]{8}"` 嗎？影響：全形或阿拉伯-印度數字的銷貨人統編會被標示 `BAD_BAN`（以前會通過，並讓 81 字元申報行寬度錯誤）；ASCII 輸入與 `% 5` 新制算法不變；現有 3 項測試不受影響。
2. 展示流程的統編來源（`/flow/correct` 的 `seller_tax_id`）是否要同時在輸入階段正規化（NFKC）？
3. 同一檔案的 `re.fullmatch(r"\d{9}", tax_reg_no)`（稅籍編號）與 `\d{8}`（發票號碼）有同樣的 Unicode 數字問題，是否一併處理？
4. 請注意公司統編 `04595252` **只在新制 `% 5` 通過**（舊制 `% 10` 不通過），任何調整都不能改回 `% 10`。

**單邊修正期間 API 與 ADMIN 的一致性測試**：`test_tax_id_parity.py` 已拆開——API 拒絕全部非 ASCII 向量（立即通過）；F09 拒絕同一批向量（`xfail(strict)`，F09 修正後會 XPASS 失敗，提醒移除標記）；逐筆比對的取樣暫不含「數學上通過的非 ASCII 向量」，F09 修正後把 `NON_ASCII_DIGIT_IDS` 加入取樣。

## 13. 到財務部全流程：跨組阻塞與缺少的功能

> 目前**只完成**「ADMIN 授權使用者上傳 → API 辨識／檢核 → 提交 → 案件停在 `pending_accounting_review` → `/cases` 唯讀讀回」的程式與假傳輸／隔離 DB 測試，且全部預設關閉。**不得宣稱已完成送財務部的全流程。**

### 13.1 明確的跨組阻塞

| # | 阻塞項目 | 負責 | 目前證據 | 解除前不可做的事 |
|---|---|---|---|---|
| 1 | 新增發票的 ADMIN 功能權限 | E（B） | `inv.intake` 不在功能目錄；`department_permissions` 無對應列；`Triplicate Uniform Invoice` 同時是前台功能，`app_scope` 待查 | 註冊功能代碼、對任何部門開放 `/intake` |
| 2 | ADMIN → API 認證方案 | API 擁有者、B／E | R／P／B 皆未核定（見 R 提案） | 實作簽章器或換發、修改登入服務、對真實 API 發出請求 |
| 3 | 財務部可讀取案件的範圍 | E、F | `/cases` 預設拒絕；E 對一般功能只給「目前選取部門」，`company` 範圍只有 `sys.*` | 放寬 `/cases`、讓財務看到其他部門案件 |
| 4 | 財務覆核、核准、退回（`pending_accounting_review` 之後的轉換） | F（E） | API 與 ADMIN 都沒有任何程式會改變 `pending_accounting_review`；操作者、端點、權限、歷程寫入規則皆未定義 | 寫入任何狀態轉換 |
| 6 | 財會覆核、核准與退回 | F | F 的 `finance_review_service` 使用 5 種展示狀態、全在記憶體；API 有 9 種簽核狀態與 `invoice_field_reviews.is_checked`；兩者沒有對照 | 把展示狀態寫進正式 DB、自行對照狀態 |
| 7 | 退回補正後的重新提交 | F、API | API `submit()` 固定建立 `submission_round = 1` 的新案件，**沒有「同一案件重新提交（輪次 + 1）」的端點**；`returned_to_applicant` 之後如何回到前台修正沒有實作 | 宣稱退回補正流程可運作 |
| 8 | F 組展示頁與真實 UUID 案件整合 | F（C 提供讀取層） | `/invoices` 使用整數 ID 與記憶體資料；`_to_filing_snapshot` 有 TODO「接上資料庫後改由 repository 讀 invoices」 | 修改 `/invoices` |

### 13.2 完成「ADMIN 到財務部」仍缺少的功能（依相依順序）

| 順序 | 功能 | 狀態 | 備註 |
|---|---|---|---|
| 1 | 認證方案核定與實作 | 未實作 | 阻塞項 2 |
| 2 | `inv.intake` 功能代碼與部門授權 | 未實作 | 阻塞項 1 |
| 3 | ADMIN 與真實 API 的整合測試（E2E 計畫 L1，之後 L2／L3） | 計畫完成、未建置 | 見 E2E 計畫 |
| 4 | `/cases` 改用 E 組授權機制（`Decision.scope`＋`Target`） | 方案已提出、未實作 | 阻塞項 3；需保留預設拒絕外層閘門 |
| 5 | 單位核准（端點、權限、狀態轉換、`invoice_status_history` 同交易） | 未實作 | 阻塞項 4 |
| 6 | `pending_accounting_review` 之後的覆核、核准、退回 | 未實作 | 阻塞項 5、6 |
| 7 | 退回補正與重新提交（輪次 +1、欄位覆核勾選的延續規則） | 未實作 | 阻塞項 7；API 需新端點 |
| 8 | 展示狀態 ⇄ 簽核狀態正式對照；`/invoices` 讀真實案件 | 未實作 | 阻塞項 6、8 |
| 9 | 財會欄位（科目、扣抵代號）的資料表欄位 | 未實作 | F09 內有 TODO「v1.4 尚無 deduction_code 欄位」；Schema 由 API 管理 |
| 10 | 稽核：誰看過／處理過哪個案件 | 未實作 | `audit_logs` 無事件串接；需 F 決定要求 |
| 11 | F09 申報檔、傳票等下游 | 既有展示功能 | 依賴 8、9 |
| 12 | 正式庫唯讀查核、正式環境驗證、部署 | 未批准 | 見唯讀查核清單 |
