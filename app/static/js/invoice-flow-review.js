/*
 * 檔案：invoice-flow-review.js
 * 用途：發票作業流程步驟 3（核對修正）的即時檢核，不需按「檢核」按鈕：
 *   1. 輸入時立即顯示格式提示與框線（格式正確／格式相容／格式錯誤），規則與 API InvoiceSubmitRequest 相同，
 *      對齊前台 invoice-field-rules.js 的欄位規則；
 *   2. 停止輸入約 0.8 秒後自動呼叫伺服器檢核（API 的必填、業務警示、查重），結果顯示在欄位旁與上方摘要。
 * 注意：
 *   - 格式提示只代表「輸入符合欄位格式」，不代表資料正確或整張發票已通過；真正的必填、警示與查重一律來自 API。
 *   - 檢核失敗時只顯示「目前無法完成檢核，資料尚未送出」，不當成通過；送出（確認提交）仍由 API 最終判斷。
 *   - 只讀取頁面上的表單，不保存任何資料到瀏覽器儲存空間。
 */
(function () {
  "use strict";
  var form = document.getElementById("flow-review-form");
  if (!form) return;

  var TRACK_RE = /^[A-Z]{2}$/;
  var EIGHT_DIGITS_RE = /^[0-9]{8}$/;
  var AMOUNT_RE = /^\d{1,16}(\.\d{1,2})?$/;
  var QUANTITY_RE = /^\d{1,14}(\.\d{1,4})?$/;
  var DEBOUNCE_MS = 800;
  var HINT_ICONS = { ok: "✓", compatible: "≈", error: "✗" };

  function halfWidth(value) {
    return String(value || "").replace(/[０-９．／－，]/g, function (c) {
      return { "．": ".", "／": "/", "－": "-", "，": "," }[c] || String.fromCharCode(c.charCodeAt(0) - 0xfee0);
    });
  }
  function compact(value) {
    return halfWidth(value).replace(/[\s-]/g, "");
  }
  function money(value) {
    return halfWidth(value).replace(/[\s,]/g, "");
  }
  function validIsoDate(text) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
    if (!m) return false;
    var d = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
    return d.getUTCFullYear() === +m[1] && d.getUTCMonth() === +m[2] - 1 && d.getUTCDate() === +m[3];
  }

  // 欄位名稱（含明細欄位）→ 規則；沒有明確格式規則的欄位（名稱、課稅別、國字大寫、品名）不顯示綠勾
  var RULES = {
    invoice_track: function (raw) {
      var v = raw.toUpperCase().replace(/[\s-]/g, "");
      if (TRACK_RE.test(raw)) return ["ok", "格式正確"];
      return TRACK_RE.test(v) ? ["compatible", "格式相容，可送出"] : ["error", "字軌需為 2 碼英文字母"];
    },
    invoice_number: function (raw) {
      var v = compact(raw);
      if (EIGHT_DIGITS_RE.test(raw)) return ["ok", "格式正確"];
      return EIGHT_DIGITS_RE.test(v) ? ["compatible", "格式相容，可送出"] : ["error", "發票號碼需為 8 碼數字"];
    },
    invoice_date: function (raw) {
      var v = compact(raw).replace(/\//g, "-");
      if (validIsoDate(raw)) return ["ok", "格式正確"];
      return validIsoDate(v) ? ["compatible", "格式相容，可送出"] : ["error", "日期格式為 YYYY-MM-DD（西元）"];
    },
    seller_tax_id: taxId,
    buyer_tax_id: taxId,
    sales_amount: amount,
    tax_amount: amount,
    total_amount: amount,
    item_line_amount: amount,
    item_quantity: quantity,
    item_unit_price: quantity
  };
  function taxId(raw) {
    var v = compact(raw);
    if (EIGHT_DIGITS_RE.test(raw)) return ["ok", "格式正確"];
    return EIGHT_DIGITS_RE.test(v) ? ["compatible", "格式相容，可送出"] : ["error", "統一編號需為 8 碼數字"];
  }
  function amount(raw) {
    var v = money(raw);
    if (AMOUNT_RE.test(raw)) return ["ok", "格式正確"];
    return AMOUNT_RE.test(v) ? ["compatible", "格式相容，可送出"] : ["error", "金額需為數字，最多 2 位小數"];
  }
  function quantity(raw) {
    var v = money(raw);
    if (QUANTITY_RE.test(raw)) return ["ok", "格式正確"];
    return QUANTITY_RE.test(v) ? ["compatible", "格式相容，可送出"] : ["error", "需為數字，最多 4 位小數"];
  }

  function hintFor(input) {
    var next = input.parentNode.querySelector(":scope > .field-hint");
    if (next) return next;
    var hint = document.createElement("small");
    hint.className = "field-hint";
    hint.setAttribute("aria-live", "polite");
    hint.hidden = true;
    var notes = input.parentNode.querySelector(":scope > .field-notes");
    input.parentNode.insertBefore(hint, notes || input.nextSibling);
    return hint;
  }

  function showFormat(input) {
    var rule = RULES[input.name];
    if (!rule || input.tagName === "SELECT") return;
    var hint = hintFor(input);
    var raw = input.value.trim();
    input.classList.remove("is-valid", "is-invalid");
    hint.className = "field-hint";
    if (raw === "") {
      hint.hidden = true;
      hint.textContent = "";
      input.removeAttribute("aria-invalid");
      return;
    }
    var result = rule(raw);
    hint.hidden = false;
    hint.className = "field-hint field-hint--" + result[0];
    hint.textContent = HINT_ICONS[result[0]] + " " + result[1];
    if (result[0] === "error") {
      input.classList.add("is-invalid");
      input.setAttribute("aria-invalid", "true");
    } else {
      input.classList.add("is-valid");
      input.removeAttribute("aria-invalid");
    }
  }

  // ---- 伺服器檢核（必填、業務警示、查重）----
  var summary = document.getElementById("live-check-summary");
  var dirty = {}; // 使用者編輯過的欄位：未編輯的缺漏用淺色提示，編輯後才顯示紅字
  var timer = null;
  var seq = 0;

  function setSummary(state, text) {
    if (!summary) return;
    summary.setAttribute("data-state", state);
    summary.textContent = text;
  }

  function containers() {
    return form.querySelectorAll("[data-check-field]");
  }
  function clearLive() {
    form.querySelectorAll("[data-live]").forEach(function (node) {
      node.remove();
    });
  }
  function add(container, className, text, kind) {
    var p = document.createElement("p");
    p.className = className;
    p.setAttribute("data-live", "");
    p.setAttribute("data-check-kind", kind);
    if (className === "field-error") p.setAttribute("role", "alert");
    p.textContent = text;
    container.appendChild(p);
  }

  function render(result) {
    clearLive();
    var missing = result.missing || {};
    var warnings = result.warnings || {};
    var requiredCount = Object.keys(missing).length;
    var warningCount = Object.keys(warnings).length;
    containers().forEach(function (box) {
      var key = box.getAttribute("data-check-field");
      if (missing[key]) add(box, dirty[key] ? "field-error" : "field-missing", dirty[key] ? missing[key] : "AI 沒有辨識到，請對照左側影像補填：" + missing[key], "required");
      (warnings[key] || []).forEach(function (message) {
        add(box, "field-warning", message, "warning");
      });
    });
    var parts = [];
    parts.push(requiredCount ? "尚缺 " + requiredCount + " 個必填欄位（提交會被拒絕）" : "必填欄位已齊全");
    parts.push(warningCount ? "業務提醒 " + warningCount + " 項（不阻擋提交）" : "沒有業務提醒");
    if (result.duplicate === true) parts.push("系統內已有相同發票，送出時會被拒絕");
    else if (result.duplicate === false) parts.push("沒有發現重複發票");
    else parts.push("資訊不足，尚未查重（需要字軌、號碼、日期與賣方統編）");
    var clean = !requiredCount && !warningCount && result.duplicate === false;
    setSummary(clean ? "clean" : "issues", parts.join("；") + "。檢核通過只代表程式沒發現問題，仍需人工確認。");
  }

  function runCheck() {
    var mine = ++seq;
    setSummary("checking", "檢核中…");
    var body = new URLSearchParams();
    new FormData(form).forEach(function (value, name) {
      if (typeof value === "string") body.append(name, value);
    });
    body.set("action", "check");
    fetch(form.getAttribute("data-live-check-url"), {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body: body
    })
      .then(function (response) {
        return response.json().then(function (data) {
          return { ok: response.ok, status: response.status, data: data };
        });
      })
      .then(function (res) {
        if (mine !== seq) return; // 已有更新的輸入，丟棄過期結果
        if (res.ok && res.data && !res.data.error) return render(res.data);
        clearLive();
        var message = res.data && res.data.error ? res.data.error : "目前無法完成檢核";
        setSummary("error", message + "。資料尚未送出，檢核結果不可視為通過。");
      })
      .catch(function () {
        if (mine !== seq) return;
        clearLive();
        setSummary("error", "目前無法完成檢核，資料尚未送出，檢核結果不可視為通過。");
      });
  }

  function schedule() {
    window.clearTimeout(timer);
    timer = window.setTimeout(runCheck, DEBOUNCE_MS);
  }

  function noteKey(input) {
    var box = input.closest(".invoice14-field, td");
    var note = box && box.querySelector("[data-check-field]");
    return note ? note.getAttribute("data-check-field") : null;
  }

  form.addEventListener("input", function (event) {
    var input = event.target;
    if (!(input instanceof HTMLElement) || !/^(INPUT|SELECT)$/.test(input.tagName)) return;
    var key = noteKey(input);
    if (key) dirty[key] = true;
    showFormat(input);
    schedule();
  });
  form.addEventListener("change", function (event) {
    var input = event.target;
    if (input instanceof HTMLElement && input.tagName === "SELECT") {
      var key = noteKey(input);
      if (key) dirty[key] = true;
      schedule();
    }
  });

  form.querySelectorAll("input").forEach(function (input) {
    if (input.type !== "hidden") showFormat(input);
  });
  runCheck(); // 載入時先檢核一次，顯示 AI 沒辨識到的必填欄位
})();
