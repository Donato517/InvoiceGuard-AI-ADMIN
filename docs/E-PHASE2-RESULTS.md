# 06_E 第二批：可獨立開發的四項補強

日期：2026-10-27。以 D:\2026102001\InvoiceGuard-AI-ADMIN、codex/06e-admin-v13、dcad25e 為開始基準；未移植舊專案。此批僅修改 E 範圍與已有 E 登入入口，不實作 B 認證核心、G 職務計算或其他組業務。

## 原因與結論

前一批本人頁只有 UUID／Email／名稱，工作台只查 E 三項，且允許零功能進入；不足以呈現完整有效部門、逐操作 scope，也會妨礙財務等其他組接入。此批建立完整有效權限投影契約，先驗證來源完整性，再決定進入資格；本人資訊仍僅可改顯示名稱。

| 原工作包 | 本批實作與離線證據 | 正式整合狀態 |
|---|---|---|
| E02 本人資訊 | 有效部門、目前部門、已核定功能／逐操作 scope；UUID 核對、名稱跳脫、顯示名稱更新白名單 | 待 B／G 真實投影及本人持久化 |
| E01 登入／工作台 | 零有效管理操作回 403、不送 Cookie 並撤銷新 Session；財務專用使用者可經完整 registry 進入，仍不能使用 E 管理功能 | 待完整跨組 registry 與正式 Session／effective_access |
| E04／E05 目錄 | 補修改重複部門代碼、重複功能、查無資料、無效 UUID／輸入、停用與舊版本等測試；SQL 失敗 rollback／去敏 | PostgreSQL 約束競爭、journal CAS／稽核及停用競爭仍待隔離庫 |
| E07 初始清單 | 三項 E 功能的 JSON 審查資料與程式一致性測試，含分類／route／action／停用初始值；修正部門授權頁路由 | 其他組定義及正式 seed／啟用尚待核定，無授權自動指派 |

這四項的 E 端實作與合成資料驗證可交付；不能據此宣稱正式登入、資料庫、全部 E-T01～T10 或整體專案已驗收。

## 驗證

第二輪 E 專屬測試：126 passed、1 warning、3.56s。前一批為 70 個 E 案例，本批增加 56 個。第一輪找到部門管理／部門授權共用導覽路徑的問題，已改成實際授權頁並補獨立權限測試後通過。

全專案回歸：787 passed、9 skipped、1 warning，99.75s，exit 0。跨三站 check_project PASS（0 issues）；Ruff lint 通過、102 個 Python 檔案格式通過；djLint 24 個樣板、0 errors；environment 檢查 configuration_valid=true、connectivity_checked=false。

測試沿用禁止外部連線及真 DB 的既有 fixture，只使用合成 UUID、Session、Email（.invalid）與替身。SQL adapter 使用 mock Session 驗 rollback／close 與安全錯誤映射，並非 PostgreSQL 整合測試。既有 Starlette TestClient／httpx warning 保留，沒有升級依賴。

案例涵蓋：各 action 分別保留 scope、財務唯讀不取得修改或系統管理權、只有新增不自動取得查看、撤權下一次請求拒絕、錯人／錯 Session／錯部門／過期／缺項／版本不一致／未解析決策拒絕、只改顯示名稱、輸入驗證、修改成重複代碼、資源不存在、舊版本、停用紀錄、唯一約束失敗回滾及 G 部分更新保留其他組資料。

## 503 與後續接線

`get_e_module()` 仍不注入正式 B／G／journal；提交表單的登入仍可能回 503。此批沒有把本機帳號、記憶體替身或 /dev/accounts 接成正式登入。新增 effective_access 是待團隊審閱的必要接點，缺少時也回 503。

下一步交付給 PM／B／G：[E-ACCESS-CONTRACT.md](E-ACCESS-CONTRACT.md)、[E-INITIAL-FUNCTIONS.json](E-INITIAL-FUNCTIONS.json)、[E-TEAM-HANDOFF.md](E-TEAM-HANDOFF.md)。先確認模組入口、完整 registry、職務政策、管理用途 Session 及 journal，再在隔離 PostgreSQL 進行 E-T01～E-T10；取得這些接點前不猜造角色規則或改 DB table。

沒有讀取／記錄機密、操作正式資料、執行 migration／seed 或部署。本批原先未提交；隨後使用者授權先以本機 Commit 保存 E，再整合 feature/admin-accounts-departments 並封閉其匿名入口。沒有 Push；遠端 E 分支尚不含本批及後續整合。

## 本機保存所用提交說明

```text
feat(e): 補齊本人有效權限與工作台資格並整理初始功能清單

原因：原本人頁缺少有效部門及逐操作資料範圍，工作台只認 E 三項功能且未拒絕零功能身分，不利跨組整合與權限驗收。
修改：新增完整 AccessSnapshot 契約及驗證、本人資訊與工作台呈現；登入先確認有效管理操作；維持顯示名稱更新白名單，補目錄衝突／停用／版本測試與 E07 審查清單。
結論：E 端可用合成資料獨立驗收，財務專用使用者無須取得 E 管理權即可透過已核定 registry 進入；缺正式 B／G／journal 接點仍拒絕，未修改共用 DB schema 或實作其他組功能。
驗證：E 專屬 126 passed；全套 787 passed、9 skipped、1 warning；跨三站 checker、Ruff、djLint 及環境靜態檢查通過。正式 PostgreSQL、跨站認證及寄信尚未驗收。
```
