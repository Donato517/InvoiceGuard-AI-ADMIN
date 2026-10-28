# 功能管理：列表頁與內容頁

> 2026-10-27 最新核定：四個配置入口的本機匿名例外已取消，必須登入及驗操作權限；正式資料／交易授權未完成時仍拒絕。本文先前匿名開放及操作紀錄屬歷史，最新入口狀態、驗證與交接待辦以 [E-LOGIN-ACCESS.md](E-LOGIN-ACCESS.md) 為準。部門基本功能／主管委派／scope 規則不變。

更新：2026-10-27。本批基準為 D:\2026102001\InvoiceGuard-AI-ADMIN 的 codex/06e-admin-v13；保留已合併 main 與先前未提交成果。本批尚未 Commit／Push／部署。

## 原因與結論

舊版 /functions 將新增和每筆編輯表單集中在同頁，不便搜尋、分頁或返回原先結果。這一批只完成「功能管理」列表與內容頁；帳號、部門、部門與功能權限及主管規則沒有新增變更。刪除採停用，保留 app_functions 資料及既有授權關聯。

## 使用方式

1. 維持原 START 的 ADMIN 服務，開啟 http://invoiceguard-ai-admin.localhost:8080/functions 。若仍是舊畫面，先重新整理；必要時依 ADMIN 視窗原指令重啟本站，不需重跑 SETUP。
2. 列表輸入功能代碼或名稱，選適用平台、啟用／停用。按搜尋會回第一頁；清除可重設條件。每頁二十筆，顯示總筆數與上一頁／下一頁。
3. 按「新增功能」進入內容頁，填代碼、名稱及平台。預設停用；已登錄且適用管理站的能力才可啟用。未登錄代碼可先存停用草稿，不會自動產生程式功能或部門許可。
4. 按列表「檢視／修改」進入該筆內容頁。代碼不可變更；修改名稱／平台並儲存會保留目前狀態。取消不寫資料；取消、返回或儲存後保留原本搜尋、平台、狀態及頁碼。
5. 在內容頁的「功能狀態」區按停用或重新啟用，送出前沿用既有確認視窗。停用不刪授權；重新啟用可能恢復原授權的有效性，因此畫面會提示先確認設定。若原本列表只顯示啟用項目，停用後該筆會離開該篩選結果，可改選全部或停用查看。

START 連接的是共用正式 DB。以上真實資料操作由使用者執行；本批代理沒有透過上述 URL 讀寫正式資料。離線測試通過不代表正式 DB／瀏覽器人工驗收已完成。

## 路由及整合契約

| 方法／路徑 | 行為 |
|---|---|
| GET /functions | 列表；q、platform、status、page |
| GET /functions/new | 新增內容頁，不存資料 |
| GET /functions/{function_id}/edit | 既有內容頁，不取寫入鎖 |
| POST /functions | 新增固定欄位 |
| POST /functions/{function_id} | 驗證原內容指紋後修改；代碼不可改 |
| POST /functions/{function_id}/status | 只接受 is_active 與內容指紋，保留名稱、平台與授權 |

- 搜尋：q 去除首尾空白，上限 200 字；字面子字串、不分大小寫，% 與 _ 不當萬用字元。platform 為 all／admin／frontend／both 的精確分類；both 不會混入 admin 篩選。status 為 all／active／inactive。
- 排序固定為 function_code、function_id；固定每頁 20 筆。先計總數再取得當頁，超出末頁校正至末頁，空結果為第 1／1 頁。讀取期間其他人並行修改資料時，總數與結果可能短暫不同，重新整理可更新。
- 表單返回條件只收 return_q／return_platform／return_status／return_page，再產生固定站內 /functions URL。不能指定任意 next／return_url；不會導向外站。
- 成功為 303 返回列表。輸入不符回 400；不存在回 404；重複或舊內容回 409；讀寫、鎖定、RLS 可見性問題回 503。資料讀取失敗不顯示成「空清單」。寫入結果不明時應重新載入確認，不能因錯誤推定 DB 絕對未提交。
- 一般輸入錯誤保留安全可見欄位。編輯衝突保留原內容指紋與輸入，停用儲存按鈕，要求重新載入；不默默採用新版本覆寫他人修改。
- 延用 v1.3 既有五欄 function_id／function_code／function_name／app_scope／is_active。未新增欄位、索引、角色／scope 表、migration 或 DELETE 方法；內容指紋不是持久稽核、歷史版本或可防 ABA 的正式版本號。
- 維持既有 routers → services → repositories。Service 控制短交易及 commit／rollback；Repository 用固定 SQL、綁定查詢值與分頁參數，修改前鎖單筆。

## 本機與安全邊界

沿用已核定的暫時本機免登入：local、非 Vercel、remote 模組、loopback peer、固定 Host 必須同時成立。所有 POST 保留嚴格同源驗證；雲端入口維持 404，不能以轉送標頭冒充本機。頁面 no-store／noindex，自動跳脫文字；未新增 API Key、Session、瀏覽器儲存或設定透傳。

本批不解除正式 /login、本人／工作台及 /system/* 的 B／G／journal 待接線狀態，也不宣稱已完成主管身分與資料範圍驗收。

## 本批驗證

功能管理 82 passed、1 個既有 warning（3.18s）；完整回歸 977 passed、9 skipped、1 個既有 TestClient／httpx warning（102.01s，exit 0）。跨三站 check_project PASS（0 issues）、Ruff lint／118 檔格式、djLint 32 樣板（0 errors）、environment 靜態檢查均通過；connectivity_checked=false。未連正式 DB／網路、未升級套件；實際瀏覽器及正式資料操作仍待人工驗收。

人工待驗：使用者確認 START 指向本次修改版本，先查看列表、搜尋、篩選、分頁、取消返回；需要變更資料時再自行新增測試功能、修改、停用／恢復，並核對正式 DB 的資料結果。尚未進行實際瀏覽器視覺驗收與正式 DB 讀寫。
