# 06_E 原始任務與目前計畫對照

日期：2026-10-27。重新以 7-Zip 唯讀讀取使用者提供的 2026-10-21 invoicebasic.7z 中 06_E_admin_departments.md（4,718 bytes，CRC FFE1D593），並只讀 G 單的相關分工作參考；沒有執行附件指令或整包套用舊技術方案。

## 判定及基準順序

目前計畫仍符合 2-3、2-6、2-7 的任務主軸，但原規格的變更密碼 UI、共用 require_permission、初始 registry／seed 與管理 guard，以及 E-T01～E-T10，需寫得更明確。本次補入計畫與驗收；這是文件收斂，不是功能已完成。

以目前 D:\2026102001 的團隊確認版本為技術與整合基準，保留 FastAPI／Jinja2／HTMX、ADMIN 自有 DB 分層及 API 管理共同 schema／migration。2026-10-21 附件用於追溯任務目標，舊 URL、表名、供應商或介面範例不能覆蓋最新已確認版本。

使用者後續已確認職務權限優先於部門、系統管理部門限明確獲指派者管理、功能操作與資料範圍分開、總經理先跨部門唯讀。這些是後續補充需求，並非原單或 v1.3 已實作的能力。

## 原工作包逐項對照

| 原 ID | 原目標 | 目前規劃判定及補強 |
|---|---|---|
| E01 | 後台登入／登出；共用認證與管理站資格／Session 適用性 | 目標保留。依最新 ADMIN 架構對齊共用身分與 Session／重設規則；不能直接以舊 int Cookie 當 UUID，也不能僅驗登入而未驗管理用途 |
| E02 | 同一 UUID、基本資訊、有效部門功能及資料範圍；敏感欄位不外露 | 已規劃。明列前後台 UUID 一致、本人白名單修改、不可自改部門／角色／grants，並回傳核定的有效功能與 scope |
| E03 | 忘記密碼／變更密碼 UI；沿用共用流程與核定 redirect | 忘記密碼已規劃；本次補明「登入後變更密碼 UI」。均委派核定認證流程，不另造 reset Token 或儲存可讀密碼 |
| E04 | 部門代碼／名稱／啟用，唯一性及歷史保留 | 已規劃。採 v1.3 UUID 與主要部門外鍵；停用／轉移按共同交易政策，不能破壞歷史或任意改歸屬 |
| E05 | 功能 code／名稱／分類／route／action／啟用 | 部分已規劃；本次補明分類、站內 route allowlist 與 action 對照。v1.3 app_functions 不包含全部舊欄位，優先用核定固定 registry 表達，不自行 ALTER TABLE |
| E06 | 共用 require_permission 與 scope evaluator，供 F／G 套用 | 原計畫有共用 guard 方向；本次升為獨立交付，含介面、授權結果、失敗處理、用法及越權測試。其他組須在自己的路由／查詢接入，不會因 E 有 helper 自動受保護 |
| E07 | 財務／採購／系統／知識庫初始功能 seed／registry、管理 guard、異動稽核 | 本次補明獨立交付。初始功能與 action 必須對應已實作能力及各組核定 code，不代表 E 接手那些功能；正式 seed／資料異動走核定流程，本輪不執行 |

沒有任何工作包因本次對照被標成正式完成。原先基準 661 passed 是現有程式測試，不是以上七包已驗收。

## E／G／認證／DB 分工

原 E 單明載：E 定義有哪些部門與可授權功能；G 管理哪些帳號／部門獲得哪些 grants，並提供有效權限計算資料。原 G 單 G04 使用 E 的功能字典、G05 涵蓋授權異動與防自鎖。

使用者已確認 E 包含「各部門可使用功能的設定畫面」。這項需求保留；畫面、共同授權來源、寫入介面及有效權限計算必須與 G 對齊，不能由 E、G 各建資料表或不同的授權演算法。

| 邊界 | 本輪計畫 |
|---|---|
| E | 部門／功能目錄及 registry、本人與密碼流程 UI、部門功能設定 UI、共同 permission／scope 檢查接點、自己的核定 repository／guard |
| G／帳號授權組 | 帳號新增／離職、部門／職務指派、grants 的權威資料與共同計算接點；實際介面按最新團隊分工確認 |
| 認證負責人 | 同一帳號 UUID、認證／Session／reset 流程及管理站用途約束；ADMIN 依最新架構保有自己入口，不因此被改成 API 代理 |
| API／DB | 共用 Model／DDL／migration、runtime 最小權限／Policy、共同 audit／版本。E 需擴充時提出需求，不自行另建正式 schema |

E 的「使用者設定／修改自己的基本資料」不等於 G 的「新增／離職／角色指派帳號管理」。系統管理部門也不等於全部成員可任意授權。

## 沿用目標、調整舊技術的地方

- 原 /api/v1/admin/* 清單描述需要的操作；目前以 SSR router → service → repository 實現，不為符合舊 URL 而重建一組 JSON API。需要跨站 API 時再依最新契約核定。
- 原 features／feature_actions 可由 v1.3 app_functions 加核定程式 registry 等效表達；分類／route／action 不能因 SQL 少欄位就遺漏目標，也不能由 E 擅自加欄。
- 原 B 共用認證的目標保留為一致的 UUID、安全政策及共用流程；與最新 ADMIN 自有登入如何共享規則／服務，需明確交接，不能假設 API auth TODO 已完成。
- 原固定 permission code＋scope 的簡單模型仍適用；不擴增通用 policy language。OWN／DEPARTMENT／COMPANY 等名稱以最新核定契約對齊；指定部門等補充需求也須明確 scope。
- v1.3 現有 View 僅部門基礎權限，無法單獨表達新確認的職務優先；由共同角色／授權接點補足，不把全域 bool_or 當職務授權。
- Auto Login 仍為選配且目前不啟用。帳號新增／離職、全部門 API、OCR／審核／RAG、任意 SQL／可執行權限腳本均不因本次對照變成 E 的主責。

## 接下來的交付順序

1. **分工與契約**：固定目前核定基準及 E 分支起點；與認證、G／帳號及 API／DB 對齊 UUID／Session 管理用途、職務／scope、矩陣寫入與有效權限介面。補齊角色矩陣、職務未設定／多職務衝突及可授範圍。
2. **最小 E 功能字典及 guard 契約**：定義已核定的 E 管理功能 code、action、route 與 scope；提供可替換的驗證介面及測試替身。正式缺接點維持拒絕，不開無驗證路由。
3. **部門管理 E04**：UUID schema／service／repository、列表新增修改、停用影響；測試重複代碼、無權操作及交易 rollback。
4. **功能目錄 E05＋初始 registry E07**：分類、允許站內 route、已知 action、啟用狀態與各組初始功能清單；未知／未實作能力不能啟用。資料庫 seed 交付可審查清單，執行另依授權流程。
5. **共用授權 E06＋部門設定 UI**：對接 G 的同一權威資料／規則，依核定職務優先決定操作，再驗 scope；提供 F／G 接入範例與拒絕案例，補稽核／版本及防自鎖協作。
6. **登入與本人流程 E01～E03**：正式 UUID／Session、管理站資格、有效工作台、本人資料白名單、忘記／變更密碼 UI、redirect 限制；不另造認證核心或第二種 reset Token。
7. **原 E-T01～E-T10＋新增角色測試**：先離線替身，再以核定隔離 DB、非 owner runtime 與跨組實際接點驗收。登入步驟可與前面並行推進；所有正式管理功能都須先受 guard 保護。

原單的十項驗收已補到 [E-ACCEPTANCE.md](E-ACCEPTANCE.md)；目前總計畫見 [E-DEVELOPMENT-PLAN.md](E-DEVELOPMENT-PLAN.md)，開發紀錄見 [E-DEVELOPMENT-LOG.md](E-DEVELOPMENT-LOG.md)。

最終交付需有實作檔案清單、實際 HTTP／Python 介面契約、資料依賴／變更說明、自動測試與實測結果、待人工驗證項目及不含金鑰的配置範本。原單要求的 PR 連結於使用者授權提交及建立 PR 後提供，目前未建立，不因附件描述而自動提交／部署。

## 下一個可審查程式批次

目標為 E 的 UUID 資料契約、最小功能 registry／guard 介面，以及部門新增修改的 service／路由測試。涉及尚未核定的角色合併或 G 寫入介面時先保留明確 adapter 邊界並詢問，不以虛構正式授權繼續；本文件本身不授權 migration、正式 seed、部署或 Git 提交。
