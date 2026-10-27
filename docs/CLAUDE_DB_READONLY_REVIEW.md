# 歷史檢驗指令：ADMIN 最小唯讀入口獨立檢驗

> 這是過去一次檢驗任務的指令，保留作紀錄，不是現行規則；其中「本輪」的範圍限制只適用當時。現行規則以 AGENTS.md 為準。

請直接在目前工作區獨立驗收以下實作，發現本範圍問題請直接修正並補上有意義的無密鑰回歸測試。先讀 ADMIN／STARTUP 的 AGENTS.md、分層規範與 DB_READONLY_PROBE.md，不將先前報告當成本輪通過證據。

專案根目錄：`C:\InvoiceGuard_project`。本輪允許範圍：ADMIN 最小唯讀盤點入口、STARTUP 的手動探測工具／薄入口、離線測試與相關文件。公司密鑰留雲端；不讀實際 .env、不 env pull、不索取 DB 密碼／SESSION_SECRET_KEY，不偽造 VERCEL／授權。**不部署、不執行 SQL 或 migration、不連真實 DB、不修改正式資料、不實作 CRUD／Storage／新登入架構、不 Commit／Stage／Merge／Push／Reset。** 不放寬治理規則、不更新核定快照來掩蓋失敗，不覆蓋其他未提交修改。

1. 記錄三站 Git branch／HEAD／index／完整 status 與本輪檔案 SHA256；保留既有修改。只列設定名稱，不讀取秘密。確認 AI／API、Schema／Models／契約快照及 SETUP／START／封裝產物僅有本輪 check-db 所需核定變更，其他既有功能與安全規則保持。
2. 逐檔檢查 ADMIN 的 app/routers/db_inspection.py、services/db_inspection_service.py、db/repositories/db_inspection_repository.py、schemas/db_inspection.py、main.py 及 tests/unit/test_db_inspection.py。路由 GET /system/db-inspection，沿用原網站 require_admin；未登入 303、非管理者 403，拒絕時不能建立盤點連線，沒有 Basic popup、測試金鑰或 APP_ENV=local bypass。
3. 確認五個完整表名允許清單一致，不接受 auth.users、任意 SQL、識別字拼接或設定透傳。Catalog 參數化，COUNT 完整常數加 FROM ONLY；只計無子表的一般表。不得回傳資料列、Email／password_hash 的值、defaults／CHECK、DB role／DSN／環境變數。欄位名稱與非敏感型別可以回傳。
4. Service 控制獨立交易，不能借用授權查詢已開始的 Session。第一 SQL 是 REPEATABLE READ, READ ONLY，固定交易內逾時；row_security=off 是「若會受 RLS 篩選就報錯」，不得當成繞過。RLS 生效、權限不足、表不存在、unsupported relation、timeout 分開處理，row_count 除 exact 外必須 null。普通空表才 exact=0。成功／失敗／cleanup 出錯都不 commit，釋放連線，錯誤與日誌不含原始例外或秘密。
5. 檢查 STARTUP/launcher/admin_readonly_probe.py、tests/tools/admin_readonly_probe.py 相容薄入口、tests/_manual_tmp/admin_readonly_probe/run_admin_readonly_probe.py、tests/test_admin_readonly_probe.py。薄入口可刪，回歸測試不依賴暫存檔存在才能執行。工具只發一個 GET；Cookie 是本人既有 ig_admin_session，僅隱藏輸入、記憶體保存，不能放 CLI／env／檔案。禁止自動 POST /login，因原登入會寫 last_login_at。無有效 Session 應報授權前提未滿足，不得繞過。
6. 用 MockTransport 與替身驗證 TLS、origin／loopback HTTP 限制、禁止轉址與環境 Proxy、大小上限、嚴格 JSON、未知欄位／HTML 登入頁／500／秘密原文去敏、退出碼。所有正式測試禁止網路／DB；不要用真 Cookie 或公司密鑰做離線測試。測試宣告與程式行為不一致時先查原因，不跳過失敗。
7. 實際重跑 ADMIN 完整 pytest、check_project（單站及 --project-root ..）、Ruff check／format；STARTUP 完整離線 pytest、Ruff。TestClient 在限制沙盒無法建立本機 socketpair 時，以受允許的離線執行方式處理；不能因此關閉既有網路防護或改產品授權。記錄實際 passed／skipped／warnings，未執行不能算通過。
8. 驗證文件說明現有 users／整數 UID 授權與 v1.3 草案不保證相容；本機仍不能直連公司 DB；雲端版本未部署時入口未就緒。不把 metadata 成功當作登入、CRUD、權限隔離或 Schema migration 已完成。
9. 更新 ADMIN 及 STARTUP 的 docs/TEST_REPORT.md、docs/REVIEW_FIX_LOG.md，追加問題、原因、修改檔案、實測結果、未驗證項目。STARTUP/SHA256SUMS.txt 依既有 CRLF、無 BOM、雙空格、UTF-8 位元組排序規則重建並獨立核對；排除 tests/_manual_tmp、artifacts／快取／私人設定，受管理工具與測試必須列入。本輪整合 START check-db，根入口與 dist 必須依新版 builder 重建且逐位元組一致，不手改產物。日常 START 不發 DB 請求，check-db 在 port／Tunnel 預檢前分流且不影響已啟服務；核對 README 的單一入口操作，不把 pytest 當成服務啟動。
10. 最後用逐檔雜湊及 Git 狀態核對範圍；git diff --check 之外另檢查本輪未追蹤文字檔。回報發現及直接修正紀錄、測試指令與數字，標示未部署／未連真 DB；提供處理紀錄路徑。不要宣稱本輪完成真實雲端串接。

真實 ADMIN URL／DB 驗收不在本指令範圍，須日後在核定可用部署及已有合法 Session 的前提下明確安排；本輪不為驗收而登入正式網站。


本輪 START 整合另請使用 STARTUP/docs/CLAUDE_ADMIN_DB_WORKFLOW_REVIEW.md 驗證參數、七份 payload、公開設定缺少的明確提示及根入口／分享包一致性。不得為消除 invalid_url 而接受前端 Tunnel 作 ADMIN、放寬公司 DB 本機防護或憑空填入網域。
