# ADMIN Document AI 三模組整合檢驗報告

> 發佈註記（2026-10-30）：本報告保留各階段當時的檢驗結果。後續已核定 ADMIN／API 三模組恢復同步；目前狀態以 DEVELOPMENT_STATUS.md 為準。本機 reviews/ 的命令、JUnit、JSON 與副本未納入版本控制；本文標示的本機證據路徑只供原工作目錄查核。

查核日期：2026-10-30（Asia/Taipei）。範圍：`InvoiceGuard-AI-ADMIN`。

## 結論與實際交付

最終完整離線回歸：**1418 passed、10 skipped、0 failed／errors**；原版完整基準 1336 passed、10 skipped。原件與整合版三模組的 6 種匯入順序均通過，未發現正常套件匯入造成的循環或初始化衝突。

已將 Document AI REST、影像證據與供應商 Few-Shot 三模組整合到 ADMIN 的 `app/integrations/`，並修正兩項已重現的來源綁定問題。舊 `DocumentAIClient.from_settings()`、`process()`、`describe_processor()`、錯誤代碼及 `text/pages/confidence/raw_response` 介面保留。完整測試結果與命令見 TEST_RESULTS.md（本機檢驗資料：reviews/document-ai-20261030/TEST_RESULTS.md），雜湊與來源見 manifest.json（本機檢驗資料：reviews/document-ai-20261030/manifest.json）。

本次交付是平台模組整合。尚未將模組接線到發票頁面、正式授權 service、範例管理或審批流程，沒有新增 DB schema、migration、Storage 寫入、訓練或自動核准。API、前台及 STARTUP 程式未修改。沒有部署、Commit 或 Push；使用者本次要求的「提交檢驗報告」以此文件交付。

原始交付需先修正才能採用；修正後的 ADMIN 版可進入有授權 service 的後續整合測試。真實 Google 認證、Processor 權限／版本支援、Gemini 額度、辨識準確率與正式端到端流程未驗證。

## 缺陷與修正

### R1／P1：辨識結果沒有完整綁定檔案版本及辨識輪次（已修正 ADMIN 版）

- 原交付 `document_ai_evidence.py:225` 的 `prepare_document_page()` 僅驗證原件雜湊與 `metadata.source_sha256`，無法確認呼叫端給的 `file_id/version/recognition_id/document_group_id` 屬於該次辨識。
- 觸發：相同原件 bytes，將 SourceRef 改標為另一辨識輪次或檔案版本。預期應拒絕；原件副本實際接受並在 PreparedPage 中保留錯誤標籤，會讓後續裁圖被誤追溯。
- 重現：reproduce_original.py（本機檢驗資料：reviews/document-ai-20261030/reproduce_original.py） 與 原始反例輸出（本機檢驗資料：reviews/document-ai-20261030/original-counterexamples.txt）。這個腳本 exit 0 表示成功重現缺陷，並非原包通過驗收。
- 修正：RecognitionMetadata 增加來源識別；新增 `bind_recognition()`，僅允許受信任 service 初次綁定，已綁定結果不能改標。原生頁圖準備必須核對全部識別；OcrPage／EvidenceRegion 另攜帶 document hash，裁圖拒絕不同辨識文件的區域。provenance 保存 document hash。
- 證據：`tests/unit/test_document_ai_bundle.py` 的 `test_bound_recognition_cannot_be_relabelled` 四類反例、`test_unbound_recognition_and_same_size_wrong_image_are_rejected`、精確像素裁圖及 provenance 斷言。
- 邊界：第一次綁定的識別、資料庫的目前版本與 ACL 仍由可信任 service 提供；雜湊和 frozen dataclass 不是對惡意應用程式呼叫端的數位簽章。

### R2／P1：候選欄位可引用本次其他欄位的圖片（已修正 ADMIN 版）

- 原交付 `document_ai_fewshot.py:480` 的 `validate_candidates()` 僅檢查引用是否存在於 TARGET alias 集合。
- 觸發：金額只出現在 T1，日期在 T2；模型將金額候選引用 T2。預期拒絕跨欄位引用；原版接受。存在本次請求中的圖片 ID 並不能證明此欄位有對應影像來源。
- 重現：同一 原始反例腳本（本機檢驗資料：reviews/document-ai-20261030/reproduce_original.py） 及輸出。
- 修正：FieldRequest 新增 `target_artifact_ids`。多張 TARGET 時每個欄位必須明確指定允許圖片；候選驗證拒絕超出該欄位集合的引用，並將圖片 alias 限制寫入欄位請求。單張 TARGET 保留簡便介面。
- 證據：`test_field_cannot_cite_another_fields_target_image`、`test_request_size_limit_skips_backend_and_multiple_targets_require_field_binding`。
- 邊界：仍只證明引用關係合法，不能證明候選文字在圖片上正確可見；所有候選保留 `requires_human_review=True`。

### R3／P2：平台缺少影像執行依賴（已修正）

- ADMIN 原 `requirements.txt` 沒有 Pillow，`document_ai_evidence` 與 `document_ai_fewshot` 同時匯入前會因缺少 `PIL` 失敗。原 venv 套件盤點確認未安裝。
- 已新增精確版本 `Pillow==13.0.0` 並安裝到 ADMIN venv，實際解碼、EXIF、PNG 與像素斷言通過。保留 `httpx==0.28.1`、`google-auth==2.62.0`。
- 此環境 google-auth 的已安裝 metadata 明確依賴 cryptography，實測為 50.0.2；原生 RSA 簽章與公鑰驗證已實跑，沒有偷偷補裝另一個簽章依賴。
- PDFium 僅用於本機原始 PDF 渲染，不是處理 Google 回傳頁圖的必要依賴。本次未安裝，也未宣稱 PDF renderer 通過。

### R4／P2：舊跨站同步規則與「只整合 ADMIN」衝突（已在 ADMIN 明確版本化）

- 原測試要求 `document_ai.py` 與 API 逐位元組相同；更新 ADMIN 後此條件必然不成立。
- 依本次明確授權，ADMIN 移除 Document AI 的位元組同步要求，改以舊公開契約及三模組身分一致性測試驗證；其餘 Storage、Gemini、Groq、chat_models 的同步測試保留。
- ADMIN 的 AGENTS、README、架構、模組契約及開發狀態已記錄此範圍例外。API 檔案與其舊測試沒有修改；日後若在 API checkout 跑舊跨站 Document AI 同步測試，仍會因這次核定分版而失敗，需在 API 授權範圍另行更新規則。

## 既有 ADMIN 單模組的額外查核

原版完整基準為 1336 passed、10 skipped，但這不代表所有邊界均無缺陷。legacy_probe.py（本機檢驗資料：reviews/document-ai-20261030/legacy_probe.py） 另外用原版副本重現：原 `_number()` 將布林 confidence 轉成 1.0；原預設 OCR read timeout 為 45 秒，超過 ADMIN 的 30 秒函式上限。兩項均為 P2：分別會誤示原生分數，或在慢回應時先被平台中止。整合版以嚴格 `_score()` 及預設 25 秒合作式預算修正，回歸包含非法分數與實際請求 timeout 斷言；仍不把合作式預算宣稱為硬總時限。證據見 legacy-counterexamples.txt（本機檢驗資料：reviews/document-ai-20261030/legacy-counterexamples.txt）。

## 同時 import 與邏輯衝突檢驗

依賴方向為 `document_ai_fewshot → document_ai_evidence → document_ai`，沒有反向依賴或循環。匯入時不建立 HTTP client、不換 token、不讀私人原件、不操作 DB／Storage；Pillow 是唯一新增必要套件。`app/integrations/__init__.py` 保持原樣，避免將選用模組變成整站的 eager import。

原件獨立目錄、整合版獨立目錄、整合版 `app.integrations` 套件都實跑三模組的 6 種匯入順序，逐一確認 `__file__`、OcrResult／TextSegment／CropArtifact 的類別身分及空 token cache。套件模式另同時匯入既有 Gemini 與 Groq。詳見 匯入探測腳本（本機檢驗資料：reviews/document-ai-20261030/import_probe.py） 與各 `import-*.txt`。

ADMIN 統一使用完整套件路徑；不要把 integrations 目錄另加 sys.path，再混用頂層 `import document_ai`。Python 的模組快取以完整名稱識別，同一檔案使用兩個名稱載入會產生兩套類別／token cache。這是錯用匯入方式的邊界，不是正常同時匯入三模組的衝突。[Python 3.12 匯入文件](https://docs.python.org/3.12/reference/import.html)

各模組以具名匯入使用；不要使用 `import *`，以免 PROVIDER 等通用名稱互相遮蔽。OCR 使用服務帳號 OAuth；Few-Shot 使用獨立 GeminiRestBackend 與 API Key，兩者的認證、快取與回應不互相覆寫。已有的 GeminiClient.chat 只處理文字，不直接拿來冒充多模態 CallableBackend。

## 環境參數評估

本次依 ADMIN Settings、`.env.example`、START 白名單及依賴 metadata 核對。未讀取真實 `.env`、服務帳號私鑰或金鑰，未查詢 Vercel 雲端密鑰，亦未讀取正在運行的 ADMIN 程序記憶體。因此以下證明「目前程式提供的設定契約足夠」，不代表雲端已填值或 Google 權限已有效。

| 能力 | 名稱 | 現況與是否需新增 |
|---|---|---|
| OCR | GOOGLE_CLOUD_PROJECT_ID | Settings 與 START Google 白名單已有；使用時必填，無須新增名稱 |
| OCR | GOOGLE_CLOUD_LOCATION | 同上；需與 Processor 的實際 location 一致，不能任意把區域互換 |
| OCR | DOCUMENT_AI_PROCESSOR_ID | 同上；需是 Processor ID，非完整資源路徑 |
| OCR | GOOGLE_SERVICE_ACCOUNT_JSON | SecretStr；START 從 ADMIN 自己的 Vercel 專案注入；無須另一組憑證 |
| Few-Shot | GEMINI_API_KEY | 沿用既有 Key；新增 FewShotClient.from_settings()，不新增環境機密 |
| Few-Shot | GEMINI_MODEL | Settings 已有，預設 gemini-3.8-flash；雲端可設定。本機 START 不透傳此名稱，因此本機維持預設；若需本機自訂，後續另核定 START 白名單變更 |
| OCR 版本／類型 | processor_version、processor_type | 可透過 from_settings 的具名覆寫提供，非必要環境變數；本次不新增 DOCUMENT_AI_PROCESSOR_VERSION／TYPE 到 Settings 或 START |
| 圖片 | Pillow==13.0.0 | 原缺少，已加入 runtime requirements 並安裝 |
| 原始 PDF 本機渲染 | pypdfium2 | 選用，本次未安裝；缺 renderer 會明確報錯，普通圖片不受影響 |

不需要新增 Google Cloud Storage、GOOGLE_APPLICATION_CREDENTIALS、本機憑證檔、第二組 Gemini Key、另一套 DB 或系統環境。這些模組以 bytes 工作，不自行建立儲存資源。

Google/Gemini 設定缺少仍不阻擋 ADMIN 原本其他功能啟動，僅在建立該能力的 client 時拒絕。環境 CLI 的 `configuration_valid=true` 是一般環境靜態檢查，`connectivity_checked=false`，不能當成 AI 設定完整或實際連線成功。

ADMIN Vercel `maxDuration=30`。OCR 與 Few-Shot 預設合作式預算均為 25 秒，保留回應時間；串接兩階段時 service 必須共用同一 deadline 並只傳剩餘秒數，不能各給 25 秒後宣稱整條 pipeline 在 30 秒內。HTTPX 是 connect/read/write/pool 分階段 timeout，不能視為強制終止工作執行緒的總時限。[HTTPX 文件](https://www.python-httpx.org/advanced/timeouts/)

## 官方介面核對（2026-10-30）

| 官方來源 | 本次核對項目與程式對應 |
|---|---|
| [processors.process](https://docs.cloud.google.com/document-ai/docs/reference/rest/v1/projects.locations.processors/process)／[processorVersions.process](https://docs.cloud.google.com/document-ai/docs/reference/rest/v1/projects.locations.processors.processorVersions/process) | DocumentAIClient 的 rawDocument、fieldMask、版本 URL 與 cloud-platform scope 相符；未指定版本使用預設版本，metadata 不猜測實際執行版本。skipHumanReview 已棄用，整合版省略它；這不控制 InvoiceGuard 審批 |
| [ProcessOptions](https://docs.cloud.google.com/document-ai/docs/reference/rest/v1/ProcessOptions) | OCR／Form 的 ocrConfig、Layout 的 layoutConfig、1 起算的個別選頁；輸出 pageNumber 保留原件頁碼 |
| [Document](https://docs.cloud.google.com/document-ai/docs/reference/rest/v1/Document) | textAnchor 的字元索引、pageRef 陣列索引、原生頁圖、transforms 及 confidence 定義。原生頁圖已校正，不以相同尺寸推定原件座標等價 |
| [Gemini generateContent](https://ai.google.dev/api/generate-content)／[圖片輸入](https://ai.google.dev/gemini-api/docs/image-understanding) | GeminiRestBackend 使用 inlineData 實際圖片、systemInstruction、STOP 檢查與 usageMetadata；未提供的用量維持缺值，並非零費用 |
| [Structured outputs](https://ai.google.dev/gemini-api/docs/structured-output) | responseJsonSchema；結構合法後仍需本機驗證欄位、型別與引用，並需人工核對內容 |
| [Google JWT](https://google-auth.readthedocs.io/en/latest/reference/google.auth.jwt.html) | RSASigner、jwt.encode／decode；已用本輪記憶體測試金鑰實際簽章與公鑰驗證 |
| [Pillow 發行資訊](https://pypi.org/project/pillow/13.0.0/) | CPython 3.12 Windows wheel 可用，已實際 import 與解碼 |

官方圖片文件示例使用 gemini-3.8-flash，與本專案既有預設一致；但帳號是否可用、區域、服務版本及額度仍需後續真實驗證。

## 後續接線方式與責任

```python
from app.integrations.document_ai import DocumentAIClient, ProcessOptions
from app.integrations.document_ai_evidence import (
    SourceRef, bind_recognition, prepare_document_page, region_from_line, crop_evidence,
)
from app.integrations.document_ai_fewshot import FewShotClient, FieldRequest

# 以下識別必須來自已驗權 service／目前辨識紀錄。
source = SourceRef.from_bytes(
    file_id=file_id, version=version, recognition_id=recognition_id,
    document_group_id=document_group_id, content=content,
)
with DocumentAIClient.from_settings() as client:
    result = client.process(content, mime_type, timeout_seconds=remaining_seconds,
        options=ProcessOptions(profile="evidence", include_page_images=True))
result = bind_recognition(result, source=source, original_content=content)
page = prepare_document_page(result, source=source, original_content=content, page_number=page_number)
region = region_from_line(result.pages[page.page_index], line_index, source=source)
crop = crop_evidence(page, (region,))
field = FieldRequest("amount", "讀取票面金額", "decimal", target_artifact_ids=(crop.artifact_id,))
# FewShotClient.from_settings() 沿用設定；review() 還需已驗證範例、scope、
# supplier_verified、authorization_check 及更新後的 remaining_seconds。
```

上層仍需提供：目前檔案／辨識版本、部門 scope、可信任供應商確認、verified 欄位標籤、每次呼叫的撤回／授權重驗、同案 document_group_id、整體 deadline，以及寫回前的版本與授權重驗。群組資料缺失時只能排除已知同 file/hash，不能保證排除不同角度重拍。

raw_response 是實際收到的遮罩後 Google 回應，不是完整未過濾文件；Few-Shot 結果不加入其中。存 DB JSON 時使用 raw_response 與明確的安全 provenance；不要將含 bytes 的 evidence dataclass 直接 json.dumps(asdict(...))。不要將原件、模型請求或回應內容寫入一般日誌。

## 未驗證與交付限制

- 下載資料夾實際沒有 ZIP、SHA256SUMS、requirements、tests 或 examples，不能重現原報告所稱 138 項測試；本輪只採實測結果。
- 真實 OAuth、Document AI GET／POST、Gemini API、DB 與 Storage 都未呼叫；不等同雲端或辨識效果驗收。
- 未實跑 PDFium 渲染、所有 Processor 版本組合、極大文件壓力、真實模型提示注入／準確率評估及正式權限端到端測試。
- 圖片最大尺寸／byte guard、請求預算與原始 PDF guard 是本機限制；上層上傳及 Vercel request 上限仍須另外驗證，不能用模組 20 MiB 限制宣稱 HTTP 上傳可接受 20 MiB。
- 原始下載六檔與 API／前台保持未修改；ADMIN 正式來源依本次授權已修改。完整性比對以 manifest 為準。
