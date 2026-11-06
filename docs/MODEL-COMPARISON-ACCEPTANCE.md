# Show Case 模型比較驗收

更新日期為 2026-11-05。本文件準備實際模型驗收的輸入、比較方式及完成條件。

## 輸入資料

測試圖片由使用者提供核准使用的本機路徑，人工真值由核對人員獨立填寫。原圖、真值及模型輸出放在版本控制以外的私人目錄。

每張測試票保存以下資訊。

```json
{
  "case_id": "synthetic-example",
  "image_path": "測試圖片的本機路徑",
  "image_sha256": "執行前計算",
  "supplier_id": "正式供應商識別碼",
  "template_key": "版型",
  "truths": {
    "invoice_number": {"state": "readable", "text": "合成原文"},
    "items.1.unit_price": {"state": "blank", "text": ""}
  }
}
```

狀態使用既有 readable、blank、unreadable、not_applicable。無法確認的欄位標記待核對，完成核對後才納入正確率分母。

## 執行與計算

1. 固定測試圖片、供應商、版型與啟用範例版本，記錄來源摘要、模型及執行時間。
2. Gemini 基線與 Few-Shot 比較使用同一張測試圖，排除同圖及同源範例，記錄實際採用的範例。
3. 分別保存原始候選、人工真值與比較結果。人工真值不由模型輸出自動產生。
4. 逐欄計算正確、缺漏與錯誤，未驗項目單獨列出。若對金額、日期或空白作正規化，先寫明規則並保留原文。
5. 整張票與各欄位分別統計，納入模型失敗及無答案案例。另記錄耗時與可取得的用量資訊。
6. 人工實際修改次數由操作者記錄，與待修正欄位數分開報告。

## 完成條件

- 測試集合及人工真值已核對，涵蓋不同版型、筆跡、空白欄位、無法辨讀及模型失敗案例。
- 比較過程可追溯至原圖與範例版本，結果可重算。
- 報告區分本機離線替身測試與實際模型結果，說明樣本數、未評估欄位與失敗案例。
- 對樣本規模和接受門檻由 PM 核定。取得實測資料後再決定是否將 Few-Shot 接入正式辨識。

目前已完成契約及合成候選測試，實際圖片集合、人工真值與模型執行結果仍待準備。

## 離線評分工具（2026-11-06）

白話說明：準備發票圖片，再由人確認發票號碼、日期、金額等正確內容，才能檢查 AI 有沒有讀錯。尚未準備時不需要自行製作技術檔案，可先逐張核對再整理成以下格式。

工具 `tests/tools/score_showcase_comparison.py` 只讀取已保存的 JSON，不呼叫模型或連接 DB。以下是合成格式範例，摘要不是實際圖片的計算結果：

```json
{
  "cases": [{
    "case_id": "synthetic-example",
    "image_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "truths": {
      "total_amount": {"state": "readable", "text": "1050"},
      "items.1.unit_price": {"state": "blank", "text": ""}
    },
    "baseline": {
      "status": "completed",
      "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "values": {"total_amount": "1050"}
    },
    "enhanced": {
      "status": "completed",
      "source_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "values": {"total_amount": "1050"}
    }
  }]
}
```

在 ADMIN 根目錄執行，將 INPUT.json 與 OUTPUT.json 換成版本控制外的私人檔案路徑：

```powershell
.\.venv\Scripts\python.exe -B tests/tools/score_showcase_comparison.py INPUT.json OUTPUT.json
```

- readable 使用原文字串逐字比較；未回覆與失敗案例保留在分母，零值可正常比較。沒有可核對欄位時，正確率為 null。
- blank 另計空白正確數；unreadable、not_applicable、unconfirmed 排除正確率分母。後三種狀態仍列出模型是否提供候選。
- 同一圖片不可重複計入，兩種結果的來源摘要必須一致。工具只核對輸入的摘要欄位；原圖摘要須由執行辨識的流程計算並保存。
- 輸出只含案例識別及統計，不複製欄位原文；輸入仍可能含發票資訊，應放在私人目錄。
- 合成單元測試 6 passed，Ruff 格式與 lint 通過。這不代表真實模型辨識率已完成驗收。
