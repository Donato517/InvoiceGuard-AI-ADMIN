/*
 * ADMIN 退回補正頁：收集逐次修改事件（editLog），正式重送時一次經 ADMIN 伺服器送到 API。
 * 欄位白名單、輪次、冪等與歷程全由 API 判斷；這裡只收集事件、保存冪等鍵並顯示結果。
 * 規則全部由 API 決定（授權欄位、事件鏈、冪等、查重）；本檔只負責收集、送出與顯示結果。
 * canonicalValue 與 mobile-camera.js、API invoice_upload_service._canonical_value 相同。
 */
(function () {
  'use strict';
  const NUMBER_FIELDS = new Set(['sales_amount', 'tax_amount', 'total_amount', 'quantity', 'unit_price', 'line_amount']);
  const CODE_FIELDS = new Set(['invoice_track', 'invoice_number', 'seller_tax_id', 'buyer_tax_id']);
  const SPACE_CHARS = '\\t\\n\\v\\f\\r \\u00a0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff';
  const EDGE_SPACES = new RegExp('^[' + SPACE_CHARS + ']+|[' + SPACE_CHARS + ']+$', 'g');
  const NUMBER_NOISE = new RegExp('[' + SPACE_CHARS + ',，]', 'g');
  const CODE_NOISE = new RegExp('[' + SPACE_CHARS + '-]', 'g');
  const MESSAGES = {
    RESUBMIT_ROUND_STALE: '案件已有較新的輪次，請重新整理頁面後再補正。',
    RESUBMIT_IMAGE_REQUIRES_VOID: '這張發票有圖片問題，請作廢後重新上傳。',
    RESUBMIT_FIELD_NOT_ALLOWED: '修改了財務沒有授權的欄位，請還原後再送出。',
    RESUBMIT_RESPONSES_INCOMPLETE: '請逐項回覆；申覆需填寫說明，已補正的項目必須實際修改。',
    RESUBMIT_EVENTS_INVALID: '修改紀錄與資料不一致，請重新整理頁面後再補正。',
    INVOICE_DUPLICATED: '此發票已存在於系統中，不能重複送出。',
    INVOICE_NOT_RETURNED: '這張發票已不是退回狀態（可能已重新送出）。',
    IDEMPOTENCY_KEY_REUSED: '內容已變更，請重新按送出。',
    RESUBMIT_ITEMS_STRUCTURE_CHANGED: '補正時不能新增、刪除或重排明細。',
    LOGIN_REQUIRED: '登入已過期，請重新登入後再補正。',
  };

  function canonicalValue(field, value) {
    if (value === null || value === undefined) return null;
    const text = String(value).replace(EDGE_SPACES, '');
    if (!text) return null;
    if (NUMBER_FIELDS.has(field)) {
      const cleaned = text.replace(NUMBER_NOISE, '');
      if (!/^[0-9]+(\.[0-9]+)?$/.test(cleaned)) return cleaned;
      const [intPart, fracPart = ''] = cleaned.split('.');
      const integer = intPart.replace(/^0+(?=[0-9])/, '');
      const fraction = fracPart.replace(/0+$/, '');
      return fraction ? integer + '.' + fraction : integer;
    }
    if (CODE_FIELDS.has(field)) return text.replace(CODE_NOISE, '').toUpperCase();
    return text;
  }

  const page = document.getElementById('correct-page');
  if (!page) return;
  // 以 fetch 送出、不換頁：向 busy.js 取得自己的遮罩 Token，完成時只釋放自己的（busy.js 沒載入時不做事）
  const startBusy = (text) =>
    typeof window.invoiceGuardBusy === 'function' ? window.invoiceGuardBusy(text) : () => {};

  // 圖片問題：只能由原提交者作廢後重新上傳（新案件走既有的 OCR 辨識與首次提交流程，舊案件與影像保留不變）
  const voidButton = document.getElementById('void-button');
  if (voidButton) {
    let voiding = false; // 終止進行中不再送第二次（前端防重，不取代後端判斷）
    voidButton.addEventListener('click', async () => {
      if (voiding) return;
      if (!window.confirm('終止後這張案件不能再補正，需要重新上傳影像建立新案件。確定要終止嗎？')) return;
      const note = document.getElementById('void-message');
      voiding = true;
      voidButton.disabled = true;
      note.textContent = '終止中…';
      const releaseBusy = startBusy('正在終止案件，請稍候…');
      try {
        const response = await fetch('/invoices/' + page.dataset.invoiceId + '/correct/void', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
          body: JSON.stringify({
            expected_round: Number(page.dataset.expectedRound),
            reason: (document.getElementById('void-reason').value || '').trim() || null,
          }),
        });
        const data = await response.json().catch(() => null);
        if (response.ok && data && data.status === 'voided') {
          note.textContent = '已終止，原案件與影像已保留；新的發票尚未建立，請重新上傳。';
          document.getElementById('void-next').hidden = false;
          voidButton.hidden = true;
          return;
        }
        const code = data && data.error ? data.error.code : '';
        note.textContent = code === 'INVOICE_NOT_RETURNED' ? '這張發票已不是退回狀態（可能已作廢或已重新送出）。'
          : code === 'RESUBMIT_ROUND_STALE' ? '案件已有較新的輪次，請重新整理頁面。'
          : (data && data.error && data.error.message) || '終止失敗，請稍後再試。';
      } catch {
        note.textContent = '網路逾時，不確定是否已終止。請重新整理頁面確認案件狀態後再操作。';
      } finally {
        releaseBusy();
        voiding = false;
      }
      voidButton.disabled = false;
    });
  }

  const form = document.getElementById('correct-form');
  if (!form) return;

  const inputs = Array.from(form.querySelectorAll('[data-field]'));
  const message = document.getElementById('correct-message');
  const retryButton = document.getElementById('correct-retry');
  const submitButton = document.getElementById('correct-submit');
  const editLog = []; // 修改事件（依時間順序）：{ field_scope, line_no, field_name, old_value, new_value }
  const lastValue = new Map(inputs.map((el) => [el, canonicalValue(el.dataset.field, el.value)]));
  let attempt = { key: null, body: null };

  function say(text, isError) {
    message.textContent = text;
    message.dataset.state = isError ? 'error' : 'ok';
  }

  // 每次離開欄位且值有變才記一筆（改了又改回也保留事件）；起點是上一輪正式提交值，不是 AI 原值
  form.addEventListener('change', (event) => {
    const el = event.target;
    if (!lastValue.has(el) || el.readOnly || el.disabled) return;
    const next = canonicalValue(el.dataset.field, el.value);
    const prev = lastValue.get(el);
    if (next === prev) return;
    editLog.push({
      field_scope: el.dataset.scope,
      line_no: el.dataset.scope === 'item' ? Number(el.dataset.lineNo) : null,
      field_name: el.dataset.field,
      old_value: prev,
      new_value: next,
    });
    lastValue.set(el, next);
  });

  function headerValue(name) {
    const el = form.querySelector('[data-scope="header"][data-field="' + name + '"]');
    return canonicalValue(name, el ? el.value : null);
  }

  function buildBody() {
    const body = {
      expected_round: Number(page.dataset.expectedRound),
      items: [],
      changes: editLog.slice(),
      responses: [],
    };
    ['invoice_track', 'invoice_number', 'seller_tax_id', 'seller_name', 'buyer_tax_id', 'buyer_name',
      'tax_type', 'sales_amount', 'tax_amount', 'total_amount', 'total_amount_zh'].forEach((name) => {
      body[name] = headerValue(name);
    });
    body.invoice_date = headerValue('invoice_date');
    form.querySelectorAll('tr[data-item-id]').forEach((row) => {
      const item = { item_id: row.dataset.itemId };
      ['item_name', 'quantity', 'unit_price', 'line_amount'].forEach((name) => {
        item[name] = canonicalValue(name, row.querySelector('[data-field="' + name + '"]').value);
      });
      body.items.push(item);
    });
    form.querySelectorAll('fieldset.response').forEach((box) => {
      const id = box.dataset.returnItemId;
      const action = box.querySelector('input[type="radio"]:checked').value;
      const note = box.querySelector('textarea').value.trim();
      body.responses.push({ return_item_id: id, action: action, note: note || null });
    });
    return body;
  }

  // 相同內容的重試使用同一個冪等識別碼（第一次若已成功，API 回原結果，不會多一輪）；內容不同才換新的
  function withKey(body) {
    const signature = JSON.stringify(body);
    if (attempt.body !== signature) attempt = { key: crypto.randomUUID(), body: signature };
    return Object.assign({ idempotency_key: attempt.key }, body);
  }

  let inFlight = false; // 補正提交進行中不再送第二次（前端防重；重送的正確性仍靠 idempotency_key 與後端）
  let requestStarted = false; // 這次提交是否已把請求送出去（決定例外後怎麼告訴使用者）

  async function send() {
    if (inFlight || form.dataset.done === 'true') return;
    inFlight = true;
    requestStarted = false;
    const releaseBusy = startBusy('正在送出補正，請稍候…');
    try {
      await sendRequest();
    } catch (error) {
      // 非預期的 JavaScript 例外：依是否已送出告訴使用者，並恢復按鈕；已成功的提交不重新開放
      if (form.dataset.done !== 'true') {
        if (requestStarted) {
          say('發生未預期的錯誤，不確定是否已送出。請按「重試」（使用相同識別碼，不會重複送出）；若仍失敗請重新整理頁面確認案件狀態。', true);
          retryButton.hidden = false;
        } else {
          say('送出前發生未預期的錯誤，尚未送出。請重新整理頁面後再試。', true);
        }
        submitButton.disabled = false;
      }
      console.error(error);
    } finally {
      inFlight = false;
      releaseBusy();
    }
  }

  async function sendRequest() {
    submitButton.disabled = true;
    retryButton.hidden = true;
    say('送出中…', false);
    const payload = JSON.stringify(withKey(buildBody())); // 組資料出錯是程式問題，不當成網路錯誤
    let response;
    try {
      requestStarted = true;
      response = await fetch('/invoices/' + page.dataset.invoiceId + '/correct', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' }, // Accept 讓錯誤回 JSON 而不是錯誤頁
        body: payload,
      });
    } catch {
      say('網路逾時，不確定是否已送出。請按「重試」（使用相同識別碼，不會重複送出）。', true);
      retryButton.hidden = false;
      submitButton.disabled = false;
      return;
    }
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (response.ok && data && data.submission_round) {
      say('已重新送出（第 ' + data.submission_round + ' 輪），案件狀態：' + (data.status_label || data.status) + '。' + (data.replayed ? '（這是先前已成功送出的結果）' : ''), false);
      form.dataset.done = 'true';
      submitButton.disabled = true;
      const back = document.getElementById('correct-done');
      if (back) back.hidden = false;
      return;
    }
    const code = data && data.error ? data.error.code : '';
    if (response.status >= 500 || response.status === 0) {
      say('服務暫時異常，不確定是否已送出。請按「重試」（使用相同識別碼，不會重複送出）。', true);
      retryButton.hidden = false;
    } else {
      say(MESSAGES[code] || (data && data.error && data.error.message) || '送出失敗，請檢查欄位後再試。', true);
    }
    submitButton.disabled = false;
  }

  form.addEventListener('submit', (event) => {
    event.preventDefault();
    send();
  });
  retryButton.addEventListener('click', send);
})();
