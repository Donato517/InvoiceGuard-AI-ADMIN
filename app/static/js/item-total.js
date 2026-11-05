/**
 * 檔案名稱：app/static/js/item-total.js
 * 功能說明：核對表單的「明細總金額」——輸入或刪除明細金額時，即時加總各筆 item_line_amount（只顯示，不送出、不改任何欄位）。
 * 注意事項：忽略千分位逗號與無法解析的值（視為 0）；與伺服器端首次渲染的加總一致。
 */
(function () {
  'use strict';
  var form = document.getElementById('flow-review-form') || document.querySelector('form.form');
  var out = document.querySelector('[data-items-total]');
  if (!form || !out) return;

  function format(total) {
    var whole = Math.abs(total - Math.round(total)) < 0.005;
    var text = whole ? String(Math.round(total)) : total.toFixed(2);
    return text.replace(/\B(?=(\d{3})+(?!\d))/g, ',');
  }

  function update() {
    var sum = 0;
    form.querySelectorAll('input[name="item_line_amount"]').forEach(function (input) {
      var n = parseFloat(String(input.value).replace(/,/g, ''));
      if (isFinite(n)) sum += n;
    });
    out.textContent = format(sum);
  }

  form.addEventListener('input', function (event) {
    if (event.target && event.target.name === 'item_line_amount') update();
  });
})();
