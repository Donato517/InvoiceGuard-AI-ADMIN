> 2026-10-28 最新版本：登入已改為「驗帳密 → 讀目前部門功能／操作 → 網站 Session → 歡迎頁」。配置入口已恢復登入檢查（LOCAL_SETUP_ENABLED=False）；沒有初始管理員、個別管理職務、交接或新增 DB 表。登入唯一寫入為既有 last_login_at。以 [登入說明](E-LOGIN-ACCESS.md) 為準，以下較早政策及測試為歷史記錄。

# 部門內容頁與授權功能整合


> 2026-10-28 啟用修正：目錄啟用與 registry／正式驗權分開；自行建立的代碼可啟用目錄，待接線另行標示，不自動授予部門權限。見 [E-FUNCTION-ACTIVATION.md](E-FUNCTION-ACTIVATION.md)。下文未登錄只能停用的規則是歷史狀態。


日期：2026-10-28。使用者要求將部門功能併入原部門的內容頁，四項操作獨立勾選，未接線功能允許先設定但需明確標示待接線。

## 操作

1. 開啟 http://invoiceguard-ai-admin.localhost:8080/department 。
2. 保留列表的新增部門及搜尋；每筆用「編輯／授權功能」進入 /department/{UUID}/edit。
3. 同一頁修改部門代碼、名稱及啟用狀態，在「授權功能區」勾選查看、新增、修改、刪除。
4. 按儲存並確認，基本資料及功能配置同一交易提交。取消返回列表，不寫入。

此頁一次載入完整功能目錄，不分頁、不隱藏未提交列。沒有功能時仍可修改部門，並提供新增功能入口。新部門先於列表建立，再開內容頁設定。資料仍只存 v1.3 原 departments 與 department_permissions，沒有新 DB／schema／migration。

## 配置語意

- 四項操作獨立，不因查看自動授予寫入，不做實體刪除。
- 未登錄程式、未支援的操作標示待接線，可依最新使用者規則先保存配置；停用的功能／部門亦保留配置。
- 這些配置不是正式主管授權核心、可授上限、scope 或 actor audit。正式認證與工作台仍未接 B／G；目前只是核定本機初始化例外。
- 其他功能組須接入相同 registry、Session、作用中狀態與權限 guard，不能單以資料庫布林值當作完整有效權限。若其他服務直接使用既有 DB view，它仍可能看到已儲存旗標；「待接線」是 E 端狀態描述，不是新的 DB 隔離欄位或全站保證。

## 一致性與安全

內容頁以 repeatable-read、read-only 交易取得同一快照。儲存採原有有限等待鎖、RLS 可見性檢查及部門列鎖；功能目錄與權限表鎖保護尚不存在的關聯列。部門欄位、功能清單及原四項設定共同形成內容指紋，任何他人變更或功能目錄增刪都需重新載入，不自動覆寫。

POST 只收固定欄位；完整功能 ID 清單與目前目錄必須一致，拒絕未知／漏列／重複 ID、未知／重複操作及額外身分欄位。全部取消寫回 false，保留對照記錄。任何部門或權限寫入／commit 失敗整筆 rollback。保留 UNASSIGNED、代碼引用、停用關聯檢查與重複代碼限制。

本機模式、loopback、固定 Host 與嚴格同源限制仍保留；雲端 404。關閉 LOCAL_SETUP_ENABLED 後恢復身分與 pending 閘門；內容查看另需 grants view，儲存另需 grants edit，正式資料／交易驗權未完成前不會放行。START 連的是正式 DB；代理只離線驗證，使用者儲存會改正式資料。

## 舊入口

側欄及歡迎頁不再有獨立「部門功能」入口。GET /department-functions 僅 303 到 /department；帶 department_id 時到相應部門內容頁。舊矩陣 POST（含 preview／draft）回 410，不讀 DB、不轉送寫入。舊獨立頁樣板刪除。原矩陣 service 的既有測試保留為歷史契約，正式本機寫入改走 department_detail_service。

## 驗證狀態

test_e_matrix_pages 已由舊獨立頁流程遷移為部門整頁表單、交易 rollback、CAS、未知功能配置、空目錄、四項獨立及全部撤銷、來源與關閉模式、舊入口退場等案例。僅移除已被新流程取代的舊 HTTP 測試，原 catalog／repository／service 測試保留。實際結果記錄於開發紀錄。未讀寫正式 DB、未 Commit／Push／部署。
驗證完成（2026-10-28）：相關回歸 247 passed；最終完整離線回歸 1109 passed、9 skipped、1 個既有 TestClient/httpx warning（116.61s，exit 0）。跨三站 checker PASS（0 issues）、Ruff lint 通過、124 檔格式通過、34 個樣板 lint 0 errors。以合成資料及封鎖外部服務的 fixture 產生內容頁，再用全請求攔截／offline headless 驗證 1440、900、390 寬度：整頁無橫向溢出、8 個獨立操作控制項、無舊入口、儲存按鈕至少44px；已檢視 panel/mobile 截圖。僅為離線視覺與替身交易驗證，未操作正式 DB／帳號或儲存；未 Commit／Push。

2026-10-28 追加：授權功能區提供整頁及整列全選／取消，歡迎頁與停用欄位不變，仍需按儲存。見 E-DEPARTMENT-BULK.md。
