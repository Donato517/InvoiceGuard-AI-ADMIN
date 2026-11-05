/*
 * 檔案：intake-amounts.js
 * 用途：ADMIN 發票核對表單（/intake 核對頁與發票作業流程步驟 3 共用 _review_form.html）的金額輔助——
 *       (1) 修改「總計」後，國字大寫自動更新（規則同手機前台 invoice-field-rules.js 的 amountToChinese）；
 *       (2) 銷售額＋稅額與總計不一致時，在總計欄位旁即時提醒；
 *       (3) 伺服器端在辨識當下帶入的國字建議值，使用者改過國字後隱藏其核對提醒。
 * 原則：
 *   - 只提醒、不阻擋提交；不改稅額、不改總計，也不呼叫 API。國字是否可解析、是否等於總計，完全由 API
 *     check-warnings 的警示負責（按「檢核」時顯示）；本檔不做任何國字格式預檢，避免和 API 的容錯解析互相矛盾
 *     （例如預印單位殘留但可解析且與總計一致的國字，API 視為正常）。
 *   - 頁面載入時不覆寫 AI 辨識值；只有使用者實際修改「總計」才會更新國字。
 *     使用者自己改過國字（非空白）後不再自動更新；清空後恢復自動。
 *   - 金額一律以整數「分」（BigInt）運算，不使用浮點數。
 *   - 程式內不使用 id 以外的全域狀態；修改後送出的資料仍是一般表單欄位，AI 原值與修改紀錄由 API 依 recognition_id 比對。
 */
(function (root) {
  'use strict';

  // ---- 國字大寫轉換（與手機前台 amountToChinese 相同的輸出；共用向量 tests/fixtures/amount_to_chinese_vectors.json）----
  var ZH_DIGITS = ['零', '壹', '貳', '參', '肆', '伍', '陸', '柒', '捌', '玖'];
  var ZH_UNITS = ['仟', '佰', '拾', ''];

  function cleanNumber(value) {
    return String(value === null || value === undefined ? '' : value)
      .normalize('NFKC')
      .replace(/[\s,，]/g, '');
  }

  function zhSection(n) {
    var out = '';
    var pendingZero = false;
    [1000, 100, 10, 1].forEach(function (base, i) {
      var digit = Math.floor(n / base) % 10;
      if (digit) {
        if (pendingZero) out += ZH_DIGITS[0];
        out += ZH_DIGITS[digit] + ZH_UNITS[i];
        pendingZero = false;
      } else if (out) {
        pendingZero = true;
      }
    });
    return out;
  }

  /** 整數金額字串 → 國字大寫（3463 → 參仟肆佰陸拾參元整）；空白、非整數、負數、超過 12 位回傳 null。 */
  function amountToChinese(value) {
    var text = cleanNumber(value).replace(/\.0+$/, '');
    if (!/^[0-9]{1,12}$/.test(text)) return null;
    var n = Number(text);
    if (n === 0) return ZH_DIGITS[0] + '元整';
    var yi = Math.floor(n / 1e8);
    var wan = Math.floor(n / 1e4) % 1e4;
    var ge = n % 1e4;
    var out = '';
    if (yi) out += zhSection(yi) + '億';
    if (wan) out += (yi && wan < 1000 ? ZH_DIGITS[0] : '') + zhSection(wan) + '萬';
    if (ge) out += ((yi || wan) && ge < 1000 ? ZH_DIGITS[0] : '') + zhSection(ge);
    return out + '元整';
  }

  // ---- 金額運算（整數分，不用浮點數）----
  var AMOUNT_RE = /^[0-9]{1,16}(\.[0-9]{1,2})?$/;

  /** 金額字串 → 以「分」計的 BigInt；空白或格式不符（負數、字母、超過 2 位小數）回傳 null。 */
  function toCents(value) {
    var text = cleanNumber(value);
    if (!AMOUNT_RE.test(text)) return null;
    var parts = text.split('.');
    return BigInt(parts[0]) * 100n + BigInt(((parts[1] || '') + '00').slice(0, 2));
  }

  function formatCents(cents) {
    var whole = (cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    var fraction = (cents % 100n).toString().padStart(2, '0').replace(/0+$/, '');
    return fraction ? whole + '.' + fraction : whole;
  }

  /**
   * 銷售額＋稅額與總計的核對。三個都是合法金額才比較；任何一個空白或格式不合，回傳 null（不下結論）。
   * @returns {{ok: boolean, expected: string, message: string}|null}
   */
  function checkTotal(sales, tax, total) {
    var s = toCents(sales);
    var t = toCents(tax);
    var g = toCents(total);
    if (s === null || t === null || g === null) return null;
    var expected = s + t;
    if (expected === g) return { ok: true, expected: formatCents(expected), message: '' };
    var diff = g > expected ? g - expected : expected - g;
    return {
      ok: false,
      expected: formatCents(expected),
      message:
        '總計與銷售額及營業稅額不一致，請核對。銷售額 ' + formatCents(s) + ' ＋ 營業稅額 ' + formatCents(t) +
        ' ＝ ' + formatCents(expected) + '；輸入總計 ' + formatCents(g) + '，差額 ' + formatCents(diff) + ' 元。',
    };
  }

  /**
   * 欄位連動控制器；與 DOM 無關，讀寫與提醒都由呼叫端注入（方便離線測試）。
   * @param {{read: function(string): string, write: function(string, string): void, note: function(string, string|null): void}} io
   *   read/write 的欄位名：sales_amount、tax_amount、total_amount、total_amount_zh；
   *   note(欄位名, 文字或 null) 顯示或清除該欄位旁的即時提醒。
   */
  function createController(io) {
    var zhManual = false; // 使用者自己填過國字：之後不自動覆寫

    function refreshTotalNote() {
      var result = checkTotal(io.read('sales_amount'), io.read('tax_amount'), io.read('total_amount'));
      io.note('total_amount', result && !result.ok ? result.message : null);
    }

    return {
      /** 使用者在欄位輸入／修改時呼叫（程式自己寫入欄位不會經過這裡，所以不會形成事件循環）。 */
      onEdit: function (field) {
        if (field === 'total_amount_zh') {
          zhManual = cleanNumber(io.read('total_amount_zh')) !== '';
          return;
        }
        if (field === 'total_amount' && !zhManual) {
          var next = amountToChinese(io.read('total_amount'));
          // 總計還不是合法整數（輸入到一半、空白、有小數）時不動國字，避免洗掉使用者的值
          if (next !== null && next !== io.read('total_amount_zh')) io.write('total_amount_zh', next);
        }
        if (field === 'sales_amount' || field === 'tax_amount' || field === 'total_amount') refreshTotalNote();
      },
      /**
       * 載入時只顯示提醒，不改任何欄位值。
       * 表單每次重新顯示（檢核、新增／刪除明細）時腳本會重新載入，所以要從目前的值還原「國字是人工填的」：
       * 國字非空、且不是總計換算的結果（含總計空白或不合法時無從換算），就視為使用者自己填的或需保留的原值，
       * 之後改總計不覆寫——包含 OCR 的容錯國字（例如預印單位殘留但可解析），寧可不自動更新，也不洗掉既有內容。
       * 伺服器在辨識當下帶入的建議值等於總計換算結果，所以不算人工值，總計再改時仍會跟著更新。
       * 限制：只看欄位內容，無法分辨「OCR 剛填的」與「使用者填的」；是否與總計一致由 API 警示提醒。
       */
      init: function () {
        var zh = io.read('total_amount_zh');
        var computed = amountToChinese(io.read('total_amount')); // 總計不合法時為 null
        zhManual = cleanNumber(zh) !== '' && zh !== computed;
        refreshTotalNote();
      },
    };
  }

  var api = {
    amountToChinese: amountToChinese,
    toCents: toCents,
    formatCents: formatCents,
    checkTotal: checkTotal,
    createController: createController,
  };

  /**
   * 綁定到核對表單；沒有金額欄位的頁面直接略過。
   * @param {Document} doc 文件物件（測試可傳入假的）。
   * @param {Window} [win] 視窗物件，用來取得 MutationObserver（沒有時略過「伺服器已有同類警示就隱藏本機提醒」的去重）。
   * @returns {object|null} 控制器；沒有綁定時為 null。
   */
  function bind(doc, win) {
    if (!doc || typeof doc.getElementById !== 'function') return null;
    var FIELDS = ['sales_amount', 'tax_amount', 'total_amount', 'total_amount_zh'];
    var inputs = {};
    FIELDS.forEach(function (name) {
      inputs[name] = doc.getElementById('f-' + name);
    });
    if (!inputs.total_amount || !inputs.total_amount_zh) return null;

    function container(name) {
      return doc.querySelector('[data-check-field="' + name + '"]');
    }

    // 伺服器上一次檢核（API）的警示已過期：只隱藏，等使用者再按「檢核」取得最新結果。
    // 任何金額欄位被改，總計與國字旁的舊警示都可能失效（總計＝銷售額＋稅額、國字＝總計），所以一起隱藏。
    function hideStaleServerWarnings(name) {
      ['total_amount', 'total_amount_zh', name].forEach(function (key) {
        var box = container(key);
        if (box) {
          box.querySelectorAll('.field-warning:not([data-live]):not([data-amounts-note]):not(.field-suggestion)').forEach(function (n) {
            n.hidden = true;
          });
        }
      });
    }

    var controller = createController({
      read: function (name) {
        return inputs[name] ? inputs[name].value : '';
      },
      write: function (name, value) {
        inputs[name].value = value;
      },
      note: function (name, text) {
        var box = container(name);
        if (!box) return;
        var old = box.querySelector('[data-amounts-note]');
        if (old) old.remove();
        if (!text) return;
        var p = doc.createElement('p');
        p.className = 'field-warning';
        p.setAttribute('data-check-kind', 'local');
        p.setAttribute('data-severity', 'warning');
        // 注意：不可用 data-live。新版 invoice-flow-review.js 的 clearLive() 會移除表單內所有 [data-live]，
        // 本機提醒必須用自己的標記，才不會被伺服器即時檢核的每一輪渲染清掉。
        p.setAttribute('data-amounts-note', '');
        p.textContent = text;
        p.hidden = serverHasTotalWarning(box);
        box.appendChild(p);
      },
    });

    // 伺服器即時檢核（API）已在同一欄位回報警示時，隱藏本機提醒，避免同一件事出現兩則；
    // 伺服器警示被清掉（下一輪檢核開始）或失敗時，本機提醒補位。只比對 .field-warning，必填缺漏與錯誤不算。
    function serverHasTotalWarning(box) {
      return Boolean(box.querySelector('.field-warning[data-live]'));
    }

    function syncLocalNote() {
      var box = container('total_amount');
      var local = box && box.querySelector('[data-amounts-note]');
      if (local) local.hidden = serverHasTotalWarning(box);
    }

    // 伺服器帶入的國字建議提醒：國字仍是當初的建議值才顯示（使用者改過，或總計帶動重算後即隱藏）
    function refreshSuggestion() {
      var box = container('total_amount_zh');
      if (!box) return;
      var current = cleanNumber(inputs.total_amount_zh.value);
      box.querySelectorAll('.field-suggestion').forEach(function (node) {
        node.hidden = cleanNumber(node.getAttribute('data-suggested-value')) !== current;
      });
    }

    function handle(name) {
      hideStaleServerWarnings(name);
      controller.onEdit(name);
      refreshSuggestion();
    }

    FIELDS.forEach(function (name) {
      var el = inputs[name];
      if (!el) return;
      el.addEventListener('input', function (event) {
        if (event && event.isComposing) return; // 輸入法組字中不處理，等 compositionend
        handle(name);
      });
      // Chrome 的最後一個 input 事件仍帶 isComposing，之後只有 compositionend；沒有它最後的輸入會被漏掉
      el.addEventListener('compositionend', function () {
        handle(name);
      });
    });
    var Observer = win && win.MutationObserver;
    var totalBox = container('total_amount');
    if (Observer && totalBox) new Observer(syncLocalNote).observe(totalBox, { childList: true });
    controller.init();
    refreshSuggestion();
    return controller;
  }

  api.bind = bind;
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.IntakeAmounts = api;
  bind(root.document, root);
})(typeof window !== 'undefined' ? window : globalThis);
