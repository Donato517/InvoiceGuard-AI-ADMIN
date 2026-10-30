# ADMIN 三模組環境變數必要性查核

> 發佈註記（2026-10-30）：本報告保留當時的環境契約查核與未驗證事項。本機 reviews/ 的腳本與輸出未納入版本控制；本文標示的本機證據路徑只供原工作目錄查核。

日期：2026-10-30（Asia/Taipei）。本輪僅確認必要設定，不新增變數、不修改模組、Settings、START、套件或雲端設定。

## 直接結論

**現有設定契約已足以建立三模組的用戶端，不需要增加環境變數名稱。** 本輪合成設定檢驗 exit 0：既有五個名稱加模型預設值即可建立 OCR 與 Few-Shot client，裁圖模組沒有環境需求。此結論與「雲端是否確實填好有效值」不同；後者本輪未能驗證。

Vercel CLI 沒有既有登入憑證，瀏覽器的 Vercel Dashboard 也顯示登入頁；本站 `.vercel/project.json` 不存在。沒有另行登入、讀取 START 的 Token 檔、下載秘密或重新 link，因此不能宣稱任何雲端 scope 已填齊，也不能因無權列示就判定雲端缺少。

## 必要設定矩陣

| 模組／能力 | 設定名稱 | ADMIN 讀取位置 | 必要性 | START 白名單 | 本輪判定 |
|---|---|---|---|---|---|
| Document AI OCR | GOOGLE_CLOUD_PROJECT_ID | config.py:106；document_ai.py:480 | 使用 OCR 時必須有有效值 | 已有 | 不新增名稱 |
| Document AI OCR | GOOGLE_CLOUD_LOCATION | config.py:107；document_ai.py:481 | 同上 | 已有 | 不新增名稱 |
| Document AI OCR | DOCUMENT_AI_PROCESSOR_ID | config.py:108；document_ai.py:482 | 同上 | 已有 | 不新增名稱 |
| Document AI OCR | GOOGLE_SERVICE_ACCOUNT_JSON | config.py:109；document_ai.py:478 | 同上，服務帳號 JSON | 已有，JSON 會壓成單行 | 不新增名稱 |
| evidence 裁圖 | 無 | document_ai_evidence.py | bytes／source／座標為函式輸入 | 不需要 | 不新增環境變數 |
| Few-Shot Gemini backend | GEMINI_API_KEY | config.py:98；document_ai_fewshot.py:788 | 使用目前 Gemini factory 時必須有有效值 | 已有 | 沿用既有 Key |
| Few-Shot 模型 | GEMINI_MODEL | config.py:100；document_ai_fewshot.py:789 | 已有預設 gemini-3.8-flash，不必另設 | 未透傳 | 使用預設不構成必要缺口 |

這五項不是三模組匯入或 ADMIN 啟動的必要條件，而是呼叫對應外部服務時的設定。OCR 不需要 Gemini Key；Few-Shot 的 Gemini REST backend 不需要第二組服務帳號 JSON。現有 START 將五項一起顯示為 Google AI 狀態，單看該組提示不能判定 OCR 四項全部缺少。

`DOCUMENT_AI_PROCESSOR_VERSION`、`DOCUMENT_AI_PROCESSOR_TYPE` 未宣告為 ADMIN Settings 欄位。雖然 client 工廠保留 getattr 的擴充入口，新增同名 Vercel 變數不會自動變成 Settings 支援的欄位。指定版本／類型可在 `from_settings(..., processor_version=..., processor_type=...)` 提供；目前沒有必須固定版本／類型的要求，因此不新增這兩個變數。

`include_page_images`、profile、頁碼、timeout、授權回呼、scope、來源版本及 verified 範例都是呼叫參數／service 資料，並非必須新增的環境變數。Pillow 已在 requirements；pypdfium2 是選用依賴，兩者均不是環境變數。

## Vercel 實際 metadata 查核狀態

目標名稱由 START 原始碼指定為 `invoice-guard-ai-admin`，不是從 API 的設定推論。因沒有登入，尚未核實實際 team、project ID 或名稱在該 team 的對應。

| 名稱 | Production | Preview | 分支覆寫／team shared | Development | 現有 Config／Secret 類型 |
|---|---|---|---|---|---|
| GOOGLE_CLOUD_PROJECT_ID | 未驗證 | 未驗證 | 未驗證 | 未驗證 | 未驗證 |
| GOOGLE_CLOUD_LOCATION | 未驗證 | 未驗證 | 未驗證 | 未驗證 | 未驗證 |
| DOCUMENT_AI_PROCESSOR_ID | 未驗證 | 未驗證 | 未驗證 | 未驗證 | 未驗證 |
| GOOGLE_SERVICE_ACCOUNT_JSON | 未驗證 | 未驗證 | 未驗證 | 未驗證 | 未驗證 |
| GEMINI_API_KEY | 未驗證 | 未驗證 | 未驗證 | 未驗證 | 未驗證 |
| GEMINI_MODEL | 未驗證 | 未驗證 | 未驗證 | 未驗證 | 未驗證 |

六項的真實值是否非空／格式有效、正在執行的 ADMIN 是否取得設定，以及 Google 服務是否可用，均未驗證。`.env.example` 的空白不是雲端缺值證據，Settings 有欄位也不是已部署填值的證據。

## 實際設定鏈與條件限制

1. `InvoiceGuard-STARTUP/launcher/unified_launcher.py:76,94,389` 指定 ADMIN 自己的 Vercel 專案及 **Production**。本機 START 並非預設讀 Development，也未指定 Preview 分支。
2. START 的既有流程以 `vercel env run` 將白名單值讀到記憶體；本輪只讀原始碼，**沒有執行這個流程**。`JSON_VARIABLES` 壓縮服務帳號 JSON，非法 JSON 會被視為未取得。
3. `mobile_launcher.py:313,341,350` 白名單保留四項 OCR 與 GEMINI_API_KEY；建立 ADMIN 子程序時才合併入該站環境。子程序載入 config Settings 後，由 client factory 讀取。
4. **GEMINI_MODEL：**未在 START 取得／注入名單中，合成檢驗也證明它被過濾。本機使用程式預設；雲端 Settings 可讀該站部署的 GEMINI_MODEL。只有未來確定需要本機與雲端同一個自訂模型時，才有理由調整 START 透傳；這是既有名稱的注入接線，不是新增名稱。
5. **Production Secret：**官方文件說明 Secret 仍可供部署使用，但儲存後不能從管理介面讀回。本機 CLI 65.2.0 原始碼也明示 Production Secret 不能 pull，並在 env run 過濾無法取得的 Secret。若 ADMIN 兩項憑證採此類型，START 從 Production 讀回它們的方式就存在條件限制；這不代表 Vercel 雲端執行程序也取不到 Secret。因無 metadata，本輪不能宣稱此限制已發生於實際帳號。
6. Secret 不可讀回時，不應改名複製或改成可讀 Config 來滿足 START。可評估使用既有名稱的獨立開發憑證／受控 secret manager 注入；需先核定實際需求與來源，本輪不建立。

第 4 項是已由原始碼及合成資料確認的行為；第 5 項是官方／CLI 行為所導出的條件限制，尚非實際專案的已重現故障。都不構成現在要新增變數名稱的理由。[Vercel Secret 文件](https://vercel.com/docs/environment-variables/sensitive-environment-variables)、[環境變數文件](https://vercel.com/docs/environment-variables)

## 與環境名稱無關的事項

- 本地 `vercel.json:5` 為 `app/main.py` 設 maxDuration=30，兩個 client 預設合作式預算各 25 秒；目前沒有將三模組接入業務 service，因此尚無可驗收的共享 deadline pipeline。新增 timeout 環境變數不能替代這項接線。
- 本輪沒有核實雲端實際部署版本、runtime 或 override。環境變數變更只對新部署生效；不能把本地檔案或未部署程式當成線上現況。[官方說明](https://vercel.com/docs/environment-variables)
- DB、Storage、登入、RAG 不是這三個底層模組的新增環境依賴，本輪不擴充相關服務。

## 本輪證據與最小處理

- Vercel CLI 65.2.0／Node.js 24.20.0。`vercel.cmd project inspect invoice-guard-ai-admin --no-color --non-interactive`：exit 1，顯示沒有既有憑證並 fetch failed。沒有取得 metadata；未反覆對三個 scope 執行相同失敗查詢。
- CLI help 顯示 env list 支援 `--project`、`--environment`。也顯示 `--json` 會包含可讀取的環境值，因此本輪沒有使用 env list --json。未使用 env pull、vercel pull、env run 或 debug。
- 瀏覽器唯讀前往 Vercel Dashboard，頁面顯示「Log in to Vercel」；未操作登入／權限／設定。
- verify_contract.py（本機檢驗資料：reviews/admin-env-20261030/verify_contract.py）：禁網、清除繼承設定後，僅用合成資料驗證 factory 與 AST 擷取的 START 白名單；exit 0。檢驗結果（本機檢驗資料：reviews/admin-env-20261030/contract-result.json）。此結果不是服務帳號格式、簽章或真實連線驗證。
- runtime 檔案雜湊保存於 contract-result.json；本輪結束核對沒有更動。保留上一輪尚未提交的整合變更，只新增查核資料與 Claude 指令。

現在最小處理：**不新增、不改名任何環境變數。** 後續有登入的維護者只需確認 ADMIN 正確 project/scope 中四項 OCR 與 GEMINI_API_KEY 的 metadata；若名稱已存在就不重複新增。模型維持預設時不用補 GEMINI_MODEL。值有效性與外部服務另做有界限的授權驗證。
