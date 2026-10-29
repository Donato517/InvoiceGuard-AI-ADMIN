# 06_E 本批實作與驗證結果

日期：2026-10-27。版本：本機 `codex/06e-admin-v13`，起點 `2b69083`。未提交／推送；不是已部署版本。

## 本批交付

- E 的 UUID／Session／有效授權／scope／版本契約，以及 B／G／DB 待接線介面。
- 固定 E registry、伺服器職務明確許可、管理資格、資料範圍檢查及權限選單。
- 部門列表、新增修改、停用影響檢查、參數化 SQL repository 及服務交易。
- 功能目錄與 G 同源部門功能矩陣的畫面及服務。
- 原 `/login`／`/logout` 委派共同認證；本人顯示名稱白名單、忘記／變更密碼 UI。
- 測試專用替身、交易失敗／版本競爭／越權／HTTP／資料不外洩案例。

正式 adapters 尚未提供，產品程式不啟用替身、不造假成功；缺接線時拒絕。實作對應、接點語意與各組責任見 [E-TEAM-HANDOFF.md](E-TEAM-HANDOFF.md)。

## 實際執行

以下均從 `D:\2026102001\InvoiceGuard-AI-ADMIN` 使用既有 `.venv` 執行，設定 `PYTHONDONTWRITEBYTECODE=1`。pytest 保留既有網路／Engine／psycopg 禁止連線 fixture，只使用明確合成資料。

| 檢查 | 結果 |
|---|---|
| `python -B scripts/check_project.py --project-root D:\2026102001` | PASS，0 issues；無 app import／DB 連線 |
| `python -B -m ruff check app tests scripts` | All checks passed |
| `python -B -m ruff format --check app tests scripts` | 99 files already formatted |
| `python -B -m djlint app/templates --lint` | 23 files，0 errors |
| `python -B -m app.core.environment --check` | configuration_valid=true；connectivity_checked=false |
| 第一批 E 測試 | 47 passed，1 warning，2.70s |
| 擴充 E／SQL adapter／既有 test_pages | 82 passed，1 warning，4.44s |
| 首次全套 `python -B -m pytest -q -p no:cacheprovider --basetemp tmp/pytest-06e-regression` | 728 passed，9 skipped，1 warning，96.99s，exit 0 |
| 最終全套 `python -B -m pytest -q -p no:cacheprovider --basetemp tmp/pytest-06e-final` | **731 passed，9 skipped，1 warning，98.00s，exit 0** |
| 本機實際 HTTP（未登入 GET，沒有 DB 寫入） | /health 200、/login 200、/auth/password/forgot 200、/system/departments 303 |

相較開發前基準 661 passed，本批新增 **70 個離線測試案例**。warning 為既有 Starlette TestClient／httpx 淘汰提示，未調整相依版本；9 skipped 保留原框架條件。測試不代表正式 PostgreSQL、寄信或跨站認證已通過。

## 原 E-T01～T10 的證據界線

| 編號 | 本批離線證據 | 正式整合仍待完成 |
|---|---|---|
| E-T01 | 共用 AuthPort 發行 UUID 與登入後 Identity 一致；拒絕舊 int Cookie | B 前台／管理站同一真實帳號 UUID |
| E-T02 | frontend audience 被拒，不發管理 Cookie；偽造 Header 無效 | B 提供真實管理用途綁定；v1.3 無 audience 欄位 |
| E-T03 | 職務缺項／非明確系統管理者及直接 HTTP 新增被拒 | 真實職務與有效成員資料 |
| E-T04 | 重複部門、舊 revision 被拒，無部分異動；SQL IntegrityError 轉固定 409 並 rollback | PostgreSQL 唯一約束競爭與真實 journal CAS |
| E-T05 | 停用決策、未知功能與未知／未實作操作被拒 | DB 功能變更後 G 的下一次決策／失效機制 |
| E-T06 | 本人表單夾帶 role／user_id／department_id 被拒，adapter 未被呼叫 | B 本人持久化、audit 與不合法欄位測試 |
| E-T07 | 權威決策改變後，下一次選單及直接路由一致拒絕；矩陣只委派 G | G 真實寫入、集合版本與跨站撤銷 |
| E-T08 | 事件含 actor、目標、before／after；audit／commit 失敗全部 rollback | journal 持久化伺服器時間及版本／最後管理者保護 |
| E-T09 | 操作、功能、Session 與 scope 綁定；跨公司唯讀不隱含修改或管理資格 | 帳號／發票組的實際 code 與核准操作接入 |
| E-T10 | 忘記／變更密碼只呼叫共同 AuthPort；B 未接線回 503，不假寄信 | 寄信、重設完成頁、效期／單次消耗、憑證版本及真實撤銷 |

最後補測：格式錯誤 Origin 在登入／部門 POST 均回 403；公司級功能目錄選單與實際 scope 一致。這三個案例加入後重跑全套通過。

額外覆蓋：職務未明確允許拒絕、公司唯讀與修改分開、資料 scope 限制、系統管理資格、交易內再驗權、超額授權／超額撤銷、精確同源 POST、XSS 跳脫、不持久 Cookie、錯誤訊息與日誌去敏。

## 限制及下一批

1. 目前 API 認證檔仍是 TODO 骨架，未找到 G 的正式有效職務／矩陣介面；不擅自補寫其他組功能。
2. v1.3 未提供完整職務、管理 audit、資源／集合版本及管理站 Session 用途綁定。E 的 Protocol 是待共同核定的接點，不能當成既有 DB 表。
3. 取得 B／G／journal adapters 後，在 get_e_module 組裝並用隔離 PostgreSQL、非 owner runtime 跑整合測試；未接線前維持拒絕。
4. 各組提供財務／採購／帳號／知識庫的真實 code、固定 route、action、scope，再加入 registry 與核定初始清單。未知字典列不自動啟用。
5. 舊 dashboard／invoices／users 等其他組路由仍使用原 require_admin；需各組遷移到共同 guard，不能宣稱全站已完成 v1.3 遷移。

未操作正式資料、未執行 migration／seed、未部署、未 commit／push／merge；沒有存取或保存真實帳密、Session、API key 或 DB URL。

## 2026-10-27｜06_E 第二批最新更新

依使用者指定四項優先順序完成 E02 有效部門／功能／逐操作 scope、E01 零功能拒絕與完整 registry 接點、E04／E05 失敗情境補驗及 E07 初始審查清單。細節與限制見 [E-PHASE2-RESULTS.md](E-PHASE2-RESULTS.md)；新增的 AccessPort.effective_access 必要方法及接入規範見 [E-ACCESS-CONTRACT.md](E-ACCESS-CONTRACT.md)；清單見 [E-INITIAL-FUNCTIONS.json](E-INITIAL-FUNCTIONS.json)。

E 專屬 126 passed（新增 56）；完整回歸 787 passed、9 skipped、1 warning，99.75s。跨三站 checker PASS、0 issues；Ruff lint／102 檔格式、djLint 24 樣板及 environment 靜態檢查通過。只用合成資料與封鎖外部服務 fixture，未驗正式 PostgreSQL／寄信／B／G；get_e_module 未接線仍拒絕，登入 503 並未因本批補強而解除。

本人仍只改顯示名稱；職務必須明確允許、系統管理資格與 scope 分開驗。財務專用 Capability 只在測試提供，未新增正式財務權限。目錄負例涵蓋修改重複代碼、查無資料、輸入、停用、版本、回滾與去敏；完整 E-T01～T10 正式整合及其他組初始功能仍待共同接點，未將離線通過改列正式驗收。

本批以目前 D:\2026102001 的 codex/06e-admin-v13／dcad25e 為起點。使用者隨後授權本機 Commit 保存成果並整合 feature/admin-accounts-departments，且先封閉該分支的本機匿名入口；不 Push、不操作正式資料。後續整合狀態另記，不覆蓋前述歷史證據。
