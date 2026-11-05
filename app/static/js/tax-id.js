/*
 * 檔案：tax-id.js
 * 用途：統一編號檢查碼（與 API invoice_format.valid_tax_id、前台 invoice-field-rules.js 同一演算法；
 *       共用測試向量 tests/fixtures/tax_id_vectors.json）。
 * 規則：只接受 8 碼半形數字（0-9）；權數 1,2,1,2,1,2,4,1，各乘積的十位與個位相加，總和可被 5 整除；
 *       第 7 碼為 7 時另可 (總和+1) 被 5 整除。
 * 注意：通過只代表算式正確，不代表公司真實存在；只用來提醒，不阻擋送出，最終判斷仍由 API。
 */
(function (root) {
  "use strict";
  var WEIGHTS = [1, 2, 1, 2, 1, 2, 4, 1];
  var RE = /^[0-9]{8}$/;

  /** 統編檢查碼；非字串或不是 8 碼半形數字一律回 false（格式問題由別的提示處理）。 */
  function validTaxId(value) {
    if (typeof value !== "string" || !RE.test(value)) return false;
    var sum = 0;
    for (var i = 0; i < 8; i += 1) {
      var product = Number(value.charAt(i)) * WEIGHTS[i];
      sum += Math.floor(product / 10) + (product % 10);
    }
    return sum % 5 === 0 || (value.charAt(6) === "7" && (sum + 1) % 5 === 0);
  }

  var api = { validTaxId: validTaxId };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.TaxId = api;
})(typeof window !== "undefined" ? window : globalThis);
