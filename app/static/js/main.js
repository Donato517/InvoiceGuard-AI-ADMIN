/**
 * @file 全站共用 JavaScript
 * @description 只放少量通用互動。頁面互動優先使用 HTMX 屬性（hx-get、hx-post…），
 *   不要自行寫 fetch 呼叫；資料一律由伺服器端（Python）處理後產生 HTML。
 */

let currentInvoicePreviewUrl = null

/**
 * 顯示所選或拖曳的發票檔案名稱與即時預覽（支援 JPG、PNG、PDF）。
 * @param {File | null} file 使用者選取或拖曳的檔案
 */
function renderInvoiceFilePreview(file) {
  const nameLabel = document.getElementById('invoice-file-name')
  const dropIcon = document.getElementById('invoice-drop-icon')
  const previewWrap = document.getElementById('invoice-file-preview')
  const previewImg = document.getElementById('invoice-preview-image')
  const previewFrame = document.getElementById('invoice-preview-frame')

  if (!file) {
    return
  }

  if (!/\.(jpe?g|png|pdf)$/i.test(file.name)) {
    window.alert('請選擇 JPG、PNG 或 PDF 檔案。')
    return
  }

  if (nameLabel) {
    nameLabel.textContent = `已選擇：${file.name}`
  }

  if (!previewWrap) {
    return
  }

  if (currentInvoicePreviewUrl) {
    URL.revokeObjectURL(currentInvoicePreviewUrl)
    currentInvoicePreviewUrl = null
  }

  currentInvoicePreviewUrl = URL.createObjectURL(file)
  const isPdf =
    (file.type && file.type.includes('application/pdf')) || /\.pdf$/i.test(file.name)

  if (dropIcon) {
    dropIcon.hidden = true
  }
  previewWrap.hidden = false

  if (isPdf) {
    if (previewImg) {
      previewImg.hidden = true
      previewImg.removeAttribute('src')
    }
    if (previewFrame) {
      previewFrame.src = currentInvoicePreviewUrl
      previewFrame.hidden = false
    }
  } else {
    if (previewFrame) {
      previewFrame.hidden = true
      previewFrame.removeAttribute('src')
    }
    if (previewImg) {
      previewImg.src = currentInvoicePreviewUrl
      previewImg.alt = `發票預覽：${file.name}`
      previewImg.hidden = false
    }
  }
}

/**
 * 送出前確認：表單加上 data-confirm 屬性即會先跳出確認視窗，按取消則不送出。
 * @example
 * <form method="post" action="/logout" data-confirm="確定要登出嗎？">
 */
document.addEventListener('submit', (event) => {
  const message = event.target.dataset.confirm
  if (message && !window.confirm(message)) {
    event.preventDefault()
  }
})

/**
 * 處理中遮罩：表單加上 data-busy="訊息"，送出後整個畫面反灰並顯示處理中，避免重複送出。
 * 遮罩由 busy.js 以「擁有者 Token」管理（每個請求只釋放自己的 Token，全部釋放才移除）；
 * 一般表單送出後等待換頁；HTMX 請求在請求結束時只釋放自己的 Token。
 * @example
 * <form method="post" action="/knowledge/.../index" data-busy="正在建立索引…">
 */
// busy.js 沒載入時（版面漏掉 script）只停用遮罩，不影響本檔其他功能；tests/unit/test_busy_overlay.py 會檢查 base.html 有載入
const Busy = window.InvoiceGuardBusy || {
  acquire: () => () => {},
  clearAll: () => {},
}

// 換頁型（一般表單、帶 data-busy 的連結）：瀏覽器換頁或從快取還原（pageshow）時清除，不使用固定逾時
let releaseNavigationBusy = () => {}
function showNavigationBusy(message) {
  releaseNavigationBusy()
  releaseNavigationBusy = Busy.acquire(message)
}

// 一般表單（非 HTMX，送出後換頁）：POST 預設顯示「處理中，請稍候…」遮罩，避免使用者以為卡住而重複送出；
// GET（查詢、篩選）預設不顯示（改用局部或輕量回饋），需要時以非空 data-busy 指定訊息。
// 有 data-busy 一律用指定訊息，data-busy="" 代表不顯示；POST 開新分頁（target）不顯示。HTMX 表單沿用下方 htmx 事件。
const DEFAULT_BUSY_MESSAGE = '處理中，請稍候…'
document.addEventListener('submit', (event) => {
  const form = event.target
  if (event.defaultPrevented || !form?.dataset) {
    return
  }
  const attribute = (name) => (typeof form.getAttribute === 'function' ? form.getAttribute(name) : null)
  const isHtmx = ['hx-post', 'hx-get', 'hx-put', 'hx-patch', 'hx-delete'].some((name) => attribute(name) !== null)
  const isPost = (attribute('method') || '').toLowerCase() === 'post'
  const opensElsewhere = Boolean(attribute('target')) && attribute('target') !== '_self'
  let message = ''
  if (form.dataset.busy !== undefined) {
    message = form.dataset.busy
  } else if (isPost && !isHtmx && !opensElsewhere) {
    message = DEFAULT_BUSY_MESSAGE
  }
  if (message) {
    showNavigationBusy(message)
  }
})

// 按「上一頁」從瀏覽器快取還原頁面時，送出前留下的遮罩一併清掉（舊的 release 之後被呼叫也不會影響新請求）
window.addEventListener('pageshow', (event) => {
  if (event.persisted) {
    Busy.clearAll()
  }
})

// 會換頁的連結（例如上傳完成後重新整理清單）加上 data-busy 也顯示遮罩；新分頁或修飾鍵開啟時不顯示
document.addEventListener('click', (event) => {
  const link = event.target.closest('a[data-busy]')
  if (!link || !link.dataset.busy || event.ctrlKey || event.metaKey || event.shiftKey) {
    return
  }
  showNavigationBusy(link.dataset.busy)
})
// HTMX：有 data-busy 用指定訊息（data-busy="" 為不顯示）；沒有時，會寫入資料的請求（POST／PUT／PATCH／DELETE）
// 預設顯示「處理中」，查詢類的 GET（即時搜尋、篩選）不顯示，避免打字時一直閃遮罩。
// 在 htmx:beforeSend（請求確定要送出才觸發，beforeRequest 被其他監聽器取消時不會到這裡）取得 Token，
// 以 detail.xhr（HTMX 2.0.11 每個請求唯一，beforeSend 與 afterRequest／各種失敗事件相同）當識別；
// 結束時只釋放自己的 Token，沒有顯示遮罩的請求結束不會影響別人的遮罩。
const htmxBusyReleases = new WeakMap()
document.addEventListener('htmx:beforeSend', (event) => {
  const { xhr, elt, requestConfig } = event.detail || {}
  if (!xhr) {
    return
  }
  const marked = elt?.closest?.('[data-busy]')
  const verb = String(requestConfig?.verb || 'get').toLowerCase()
  const message = marked ? marked.dataset.busy : verb !== 'get' ? DEFAULT_BUSY_MESSAGE : ''
  if (message) {
    htmxBusyReleases.set(xhr, Busy.acquire(message))
  }
})
function releaseHtmxBusy(event) {
  const xhr = event.detail?.xhr
  const release = xhr && htmxBusyReleases.get(xhr)
  if (release) {
    htmxBusyReleases.delete(xhr)
    release()
  }
}
// afterRequest 在成功、HTTP 錯誤、網路失敗、逾時、中止時都會觸發；其餘事件是保險，重複釋放無效
for (const name of ['afterRequest', 'sendError', 'sendAbort', 'timeout', 'responseError']) {
  document.addEventListener(`htmx:${name}`, releaseHtmxBusy)
}
window.addEventListener('pageshow', () => {
  Busy.clearAll()
  const uploadBtn =
    document.getElementById('btn-flow-upload') ||
    document.querySelector('button[form="flow-upload-form"]')
  if (uploadBtn && uploadBtn.classList.contains('btn--loading')) {
    uploadBtn.classList.remove('btn--loading')
    uploadBtn.removeAttribute('aria-busy')
    uploadBtn.disabled = false
    uploadBtn.textContent = '上傳並開始辨識 →'
  }
  const loginBtn = document.querySelector('.e-login__submit')
  if (loginBtn && loginBtn.classList.contains('btn--loading')) {
    loginBtn.classList.remove('btn--loading')
    loginBtn.removeAttribute('aria-busy')
    loginBtn.disabled = false
    loginBtn.textContent = '登入系統'
  }
})

/**
 * 登入表單：送出時將登入按鈕轉為載入旋轉圈狀態。
 */
document.addEventListener('submit', (event) => {
  const form = event.target
  if (
    form &&
    (form.classList.contains('e-login__form') || form.getAttribute('action') === '/login') &&
    !event.defaultPrevented
  ) {
    const loginBtn =
      form.querySelector('.e-login__submit') ||
      form.querySelector('button[type="submit"]')
    if (loginBtn) {
      loginBtn.classList.add('btn--loading')
      loginBtn.setAttribute('aria-busy', 'true')
      loginBtn.innerHTML = '<span class="btn__spinner" aria-hidden="true"></span>登入中…'
      setTimeout(() => {
        loginBtn.disabled = true
      }, 0)
    }
  }
})

/**
 * 發票流程步驟 1：送出辨識時，將「上傳並開始辨識 →」按鈕轉為載入旋轉圈狀態。
 */
document.addEventListener('submit', (event) => {
  const form = event.target
  if (form && form.id === 'flow-upload-form' && !event.defaultPrevented) {
    const fileInput = document.getElementById('invoice-file')
    if (fileInput && fileInput.files && fileInput.files.length > 0) {
      const uploadBtn =
        document.getElementById('btn-flow-upload') ||
        document.querySelector('button[form="flow-upload-form"]')
      if (uploadBtn) {
        uploadBtn.classList.add('btn--loading')
        uploadBtn.setAttribute('aria-busy', 'true')
        uploadBtn.innerHTML = '<span class="btn__spinner" aria-hidden="true"></span>辨識中…'
        setTimeout(() => {
          uploadBtn.disabled = true
        }, 0)
      }
    }
  }
})

/**
 * 對話框開關與拖曳上傳點擊觸發。
 */
document.addEventListener('click', (event) => {
  const btnReOcr = event.target.closest('#btn-re-ocr')
  if (btnReOcr) {
    const buyerInput = document.getElementById('buyer_tax_id')
    if (!buyerInput) {
      return
    }
    const val = buyerInput.value.trim()
    const existingErr = document.getElementById('buyer_tax_id_error')
    if (!val) {
      buyerInput.classList.add('is-invalid')
      buyerInput.setAttribute('aria-invalid', 'true')
      if (!existingErr) {
        const err = document.createElement('small')
        err.id = 'buyer_tax_id_error'
        err.className = 'field-error-msg'
        err.textContent = '請填寫買受人統一編號'
        buyerInput.after(err)
      } else {
        existingErr.textContent = '請填寫買受人統一編號'
        existingErr.hidden = false
      }
      window.alert('請先填寫買受人統一編號！')
      buyerInput.scrollIntoView({ block: 'center', behavior: 'smooth' })
      buyerInput.focus()
      return
    }
    buyerInput.classList.remove('is-invalid')
    buyerInput.removeAttribute('aria-invalid')
    if (existingErr) {
      existingErr.remove()
    }
    const confirmed = window.confirm(`請確認買受人統一編號：${val} 是否正確？`)
    if (confirmed) {
      // 後續辨識邏輯預留此處寫入
    }
    return
  }

  const openTrigger = event.target.closest('[data-open-dialog]')
  if (openTrigger) {
    const dialogId = openTrigger.dataset.openDialog
    const dialog = document.getElementById(dialogId)
    if (dialog && typeof dialog.showModal === 'function') {
      dialog.showModal()
    }
    return
  }

  const closeTrigger = event.target.closest('[data-close-dialog]')
  if (closeTrigger) {
    const dialog = closeTrigger.closest('dialog')
    if (dialog && typeof dialog.close === 'function') {
      // 取消後不留下輸入中的內容，下次開啟回到原值
      dialog.querySelector('form')?.reset()
      dialog.close()
    }
    return
  }

  const dropzone = event.target.closest('[data-dropzone]')
  if (dropzone && event.target.tagName !== 'INPUT') {
    const input = dropzone.querySelector('input[type="file"]')
    if (input) {
      input.click()
    }
    return
  }

  // 點擊對話框背景時關閉
  if (event.target.tagName === 'DIALOG' && event.target.open) {
    const rect = event.target.getBoundingClientRect()
    const clickedInDialog =
      rect.top <= event.clientY &&
      event.clientY <= rect.top + rect.height &&
      rect.left <= event.clientX &&
      event.clientX <= rect.left + rect.width
    if (!clickedInDialog) {
      event.target.close()
    }
  }
})

/**
 * 檔案選取變更（知識庫多檔上傳與發票單檔上傳預覽）及自動送出選單
 */
document.addEventListener('change', (event) => {
  if (event.target && event.target.hasAttribute('data-auto-submit')) {
    const form = event.target.closest('form')
    if (form) {
      form.submit()
    }
    return
  }

  if (event.target && event.target.id === 'document-files') {
    const summary = document.getElementById('fileSummary')
    if (summary) {
      const count = event.target.files ? event.target.files.length : 0
      summary.textContent = count > 0 ? `已選擇 ${count} 個檔案` : '尚未選擇檔案'
    }
    return
  }

  if (event.target && event.target.id === 'invoice-file') {
    const file = event.target.files && event.target.files[0] ? event.target.files[0] : null
    renderInvoiceFilePreview(file)
    return
  }

  if (event.target && event.target.dataset && event.target.dataset.invoice14) {
    recalculateInvoice14Fields(event.target.dataset.invoice14)
  }
})

document.addEventListener('input', (event) => {
  if (event.target && event.target.id === 'buyer_tax_id') {
    if (event.target.value.trim()) {
      event.target.classList.remove('is-invalid')
      event.target.removeAttribute('aria-invalid')
      const existingErr = document.getElementById('buyer_tax_id_error')
      if (existingErr) {
        existingErr.remove()
      }
    }
  }
})

/**
 * 將數值金額轉為繁體中文國字大寫金額（例如 6300 -> 陸仟參佰元整）。
 * @param {number} amount 數值金額
 * @returns {string}
 */
function invoiceChineseAmount(amount) {
  const cents = Math.round((Number(amount) || 0) * 100)
  if (!Number.isSafeInteger(cents) || cents < 0 || cents > 99999999999999) {
    return ''
  }
  const digits = '零壹貳參肆伍陸柒捌玖'
  const small = ['', '拾', '佰', '仟']
  const large = ['', '萬', '億', '兆']
  let integer = Math.floor(cents / 100)
  const groups = []
  let result = ''
  let between = false
  while (integer) {
    groups.push(integer % 10000)
    integer = Math.floor(integer / 10000)
  }
  for (let index = groups.length - 1; index >= 0; index -= 1) {
    const group = groups[index]
    if (!group) {
      if (result) {
        between = true
      }
      continue
    }
    if (result && (between || group < 1000)) {
      result += '零'
    }
    between = false
    let part = ''
    let zero = false
    for (let p = 3; p >= 0; p -= 1) {
      const digit = Math.floor(group / 10 ** p) % 10
      if (!digit) {
        if (part) {
          zero = true
        }
        continue
      }
      if (zero) {
        part += '零'
      }
      zero = false
      part += digits[digit] + small[p]
    }
    result += part + large[index]
  }
  result = (result || '零') + '元'
  const jiao = Math.floor((cents % 100) / 10)
  const fen = cents % 10
  if (!jiao && !fen) {
    return `${result}整`
  }
  if (jiao) {
    result += `${digits[jiao]}角`
  }
  if (fen) {
    result += `${!jiao ? '零' : ''}${digits[fen]}分`
  }
  return result
}

/**
 * 步驟 3（採購核對修正）14 項欄位連動重算。
 * @param {string} changedField 變更欄位識別名稱
 */
function recalculateInvoice14Fields(changedField) {
  const qtyEl = document.getElementById('quantity')
  const unitEl = document.getElementById('unit_price')
  const amountEl = document.getElementById('amount')
  const netEl = document.getElementById('net_amount')
  const taxCatEl = document.getElementById('tax_category')
  const taxAmtEl = document.getElementById('tax_amount')
  const totalAmtEl = document.getElementById('total_amount')
  const totalCnEl = document.getElementById('total_chinese')
  if (!netEl || !taxAmtEl || !totalAmtEl || !totalCnEl) {
    return
  }

  const qty = Math.max(1, Math.round(Number(qtyEl ? qtyEl.value : 1) || 1))
  const unit = Math.max(0, Math.round(Number(unitEl ? unitEl.value : 0) || 0))
  if (changedField === 'quantity' || changedField === 'unit_price') {
    const computedAmount = qty * unit
    if (amountEl) {
      amountEl.value = String(computedAmount)
    }
    netEl.value = String(computedAmount)
  } else if (changedField === 'amount' && amountEl) {
    const amt = Math.max(0, Math.round(Number(amountEl.value) || 0))
    netEl.value = String(amt)
  }

  const net = Math.max(0, Math.round(Number(netEl.value) || 0))
  const taxCat = taxCatEl ? taxCatEl.value : '應稅'
  if (
    [
      'quantity',
      'unit_price',
      'amount',
      'net_amount',
      'tax_category',
    ].includes(changedField)
  ) {
    const computedTax = taxCat === '應稅' ? Math.round(net * 0.05) : 0
    taxAmtEl.value = String(computedTax)
  }

  const tax = Math.max(0, Math.round(Number(taxAmtEl.value) || 0))
  if (
    [
      'quantity',
      'unit_price',
      'amount',
      'net_amount',
      'tax_category',
      'tax_amount',
    ].includes(changedField)
  ) {
    const computedTotal = net + tax
    totalAmtEl.value = String(computedTotal)
    totalCnEl.value = invoiceChineseAmount(computedTotal)
  } else if (changedField === 'total_amount') {
    const total = Math.max(0, Math.round(Number(totalAmtEl.value) || 0))
    totalCnEl.value = invoiceChineseAmount(total)
  }
}

/**
 * 發票檔案拖曳上傳（dragover / dragleave / drop）
 */
document.addEventListener('dragover', (event) => {
  const dropzone = event.target.closest('[data-dropzone]')
  if (!dropzone) {
    return
  }
  event.preventDefault()
  dropzone.classList.add('dropzone--drag')
})

document.addEventListener('dragleave', (event) => {
  const dropzone = event.target.closest('[data-dropzone]')
  if (!dropzone) {
    return
  }
  if (!dropzone.contains(event.relatedTarget)) {
    dropzone.classList.remove('dropzone--drag')
  }
})

document.addEventListener('drop', (event) => {
  const dropzone = event.target.closest('[data-dropzone]')
  if (!dropzone) {
    return
  }
  event.preventDefault()
  dropzone.classList.remove('dropzone--drag')

  const file =
    event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0]
      ? event.dataTransfer.files[0]
      : null
  if (!file) {
    return
  }

  if (!/\.(jpe?g|png|pdf)$/i.test(file.name)) {
    window.alert('請選擇 JPG、PNG 或 PDF 檔案。')
    return
  }

  const input = dropzone.querySelector('input[type="file"]')
  if (input && typeof DataTransfer !== 'undefined') {
    const transfer = new DataTransfer()
    transfer.items.add(file)
    input.files = transfer.files
  }

  renderInvoiceFilePreview(file)
})

/**
 * 批次產生申報檔：動態計算勾選發票筆數、未稅合計、稅額合計與含稅總額。
 */
function updateFilingSummary() {
  const allCheckbox = document.querySelector('input[data-filing="all"]')
  const rowCheckboxes = Array.from(
    document.querySelectorAll('input[data-filing="row"]:not(:disabled)')
  )
  if (!rowCheckboxes.length && !allCheckbox) {
    return
  }

  let count = 0
  let salesTotal = 0
  let taxTotal = 0

  rowCheckboxes.forEach((cb) => {
    if (cb.checked) {
      count += 1
      salesTotal += Number(cb.dataset.sales) || 0
      taxTotal += Number(cb.dataset.tax) || 0
    }
  })

  if (allCheckbox) {
    allCheckbox.checked =
      rowCheckboxes.length > 0 && rowCheckboxes.every((cb) => cb.checked)
    allCheckbox.indeterminate = count > 0 && count < rowCheckboxes.length
  }

  const countEl = document.getElementById('filing-checked-count')
  if (countEl) {
    countEl.textContent = `${count} 筆`
  }
  const salesEl = document.getElementById('filing-sales-total')
  if (salesEl) {
    salesEl.textContent = `$${salesTotal.toLocaleString('en-US')}`
  }
  const taxEl = document.getElementById('filing-tax-total')
  if (taxEl) {
    taxEl.textContent = `$${taxTotal.toLocaleString('en-US')}`
  }
  const grandEl = document.getElementById('filing-grand-total')
  if (grandEl) {
    grandEl.textContent = `$${(salesTotal + taxTotal).toLocaleString('en-US')}`
  }
  const exportBtn = document.getElementById('btn-filing-export')
  if (exportBtn) {
    exportBtn.disabled = count === 0
  }
}

document.addEventListener('change', (event) => {
  if (event.target.matches('input[data-filing="all"]')) {
    const isChecked = event.target.checked
    const rowCheckboxes = document.querySelectorAll(
      'input[data-filing="row"]:not(:disabled)'
    )
    rowCheckboxes.forEach((cb) => {
      cb.checked = isChecked
    })
    updateFilingSummary()
  } else if (event.target.matches('input[data-filing="row"]')) {
    updateFilingSummary()
  }
})

document.addEventListener('click', (event) => {
  if (event.target.closest('#btn-filing-query')) {
    const periodEl = document.getElementById('filing-period')
    const fromEl = document.getElementById('filing-from-date')
    const toEl = document.getElementById('filing-to-date')
    const params = new URLSearchParams()
    if (periodEl && periodEl.value) {
      params.set('period', periodEl.value)
    }
    if (fromEl && fromEl.value) {
      params.set('from_date', fromEl.value)
    }
    if (toEl && toEl.value) {
      params.set('to_date', toEl.value)
    }
    window.location.href = `/invoices/filing?${params.toString()}`
  }
})

// 財務審核（2-4）：先選費用大項，會計科目只列出該大項的科目；換大項時不屬於新大項的已選科目會清空
// 扣抵代號為獨立選項，不與會計科目連動
function filterAccountsByCategory(categorySelect) {
  const accountSelect = document.querySelector(categorySelect.dataset.accountFilter)
  if (!accountSelect) {
    return
  }
  const category = categorySelect.value
  accountSelect.disabled = !category
  for (const option of accountSelect.options) {
    if (!option.value) {
      continue
    }
    const mismatch = option.dataset.category !== category
    option.hidden = mismatch
    option.disabled = mismatch
    if (mismatch && option.selected) {
      accountSelect.value = ''
    }
  }
}

document.addEventListener('change', (event) => {
  const categorySelect = event.target.closest('select[data-account-filter]')
  if (categorySelect) {
    filterAccountsByCategory(categorySelect)
  }
})

// 公司基本資料：選縣市後重建鄉鎮市區選項，選鄉鎮市區後帶出 3 碼郵遞區號
// 清單來自頁面內 #tw-postal-codes（中華郵政一覽表）；郵遞區號只供顯示，伺服器會依縣市與區重新帶出
document.addEventListener('change', (event) => {
  const citySelect = event.target.closest('select[data-postal-city]')
  if (citySelect) {
    const districtSelect = document.querySelector(citySelect.dataset.postalCity)
    const zipInput = document.querySelector(citySelect.dataset.postalZip)
    const source = document.getElementById('tw-postal-codes')
    if (!districtSelect || !source) {
      return
    }
    // 資料為 [[縣市, [[鄉鎮市區, 郵遞區號], …]], …]，保留中華郵政原檔順序
    const entry = JSON.parse(source.textContent).find(([city]) => city === citySelect.value)
    districtSelect.replaceChildren(new Option('請選擇', ''))
    for (const [name, code] of entry ? entry[1] : []) {
      const option = new Option(name, name)
      option.dataset.zip = code
      districtSelect.append(option)
    }
    if (zipInput) {
      zipInput.value = ''
    }
    return
  }
  const districtSelect = event.target.closest('select[data-postal-district]')
  if (districtSelect) {
    const zipInput = document.querySelector(districtSelect.dataset.postalDistrict)
    const option = districtSelect.options[districtSelect.selectedIndex]
    if (zipInput) {
      zipInput.value = option ? option.dataset.zip || '' : ''
    }
  }
})

// 知識庫題庫測試：「全部執行」依序送出每一題（一次一題，避開 60 秒限制），等上一題完成才送下一題
document.addEventListener('click', async (event) => {
  const runAll = event.target.closest('[data-eval-run-all]')
  if (!runAll || typeof htmx === 'undefined') {
    return
  }
  runAll.disabled = true
  try {
    for (const button of document.querySelectorAll('[data-eval-run]')) {
      await htmx.ajax('POST', '/knowledge/eval/run', {
        source: button,
        target: button.dataset.evalTarget,
        swap: 'innerHTML',
        values: { case_id: button.dataset.evalRun },
      })
    }
  } finally {
    runAll.disabled = false
  }
})

/**
 * 側欄手風琴與圖示列（partials/sidebar_groups.html、sidebar_head.html，2026-11-05）
 * - 展開時：點群組標題展開／收合；目前頁面所在群組由伺服器預設展開，其他群組的開合記在這台瀏覽器。
 * - 收合成圖示列（按 Logo 旁的按鈕；手機寬度固定是圖示列）：點群組圖示在右側跳出功能清單，點其他地方或 Esc 關閉。
 * - 瀏覽器儲存只是個人偏好；讀寫失敗（無痕、封鎖）時照預設顯示，不影響功能。
 */
const SIDEBAR_COLLAPSED_KEY = 'ig-admin-sidebar-collapsed'
const SIDEBAR_OPEN_KEY = 'ig-admin-sidebar-open-groups'

function readSidebarPreference(key, fallback) {
  try {
    const value = window.localStorage.getItem(key)
    return value === null ? fallback : JSON.parse(value)
  } catch {
    return fallback
  }
}

function writeSidebarPreference(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // 無法儲存時只是不記住偏好
  }
}

function isSidebarRail() {
  const narrow = typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 800px)').matches
  return narrow || document.documentElement.classList.contains('sidebar-collapsed')
}

function closeSidebarFlyouts(except) {
  document.querySelectorAll('.app-sidebar__group-items[data-flyout]').forEach((items) => {
    if (items === except) {
      return
    }
    items.removeAttribute('data-flyout')
    const group = items.closest('[data-sidebar-group]')
    const expanded = group?.querySelector('.app-sidebar__group-toggle')?.getAttribute('aria-expanded') === 'true'
    items.hidden = !expanded
  })
}

function setSidebarGroupOpen(group, open) {
  const toggle = group.querySelector('.app-sidebar__group-toggle')
  const items = group.querySelector('.app-sidebar__group-items')
  if (!toggle || !items) {
    return
  }
  toggle.setAttribute('aria-expanded', String(open))
  items.hidden = !open
}

function setSidebarCollapsed(collapsed) {
  document.documentElement.classList.toggle('sidebar-collapsed', collapsed)
  document.querySelectorAll('[data-sidebar-collapse]').forEach((button) => {
    const label = collapsed ? '展開側欄' : '收合側欄'
    button.setAttribute('aria-label', label)
    button.setAttribute('title', label)
  })
}

function initSidebar() {
  if (!document.documentElement?.classList || typeof document.querySelectorAll !== 'function') {
    return
  }
  setSidebarCollapsed(readSidebarPreference(SIDEBAR_COLLAPSED_KEY, false) === true)
  const saved = readSidebarPreference(SIDEBAR_OPEN_KEY, [])
  const openGroups = new Set(Array.isArray(saved) ? saved : [])
  document.querySelectorAll('[data-sidebar-group]').forEach((group) => {
    if (openGroups.has(group.dataset.sidebarGroup)) {
      setSidebarGroupOpen(group, true)
    }
  })
}

document.addEventListener('click', (event) => {
  const target = event.target
  if (typeof target?.closest !== 'function') {
    return
  }
  const collapse = target.closest('[data-sidebar-collapse]')
  if (collapse) {
    const collapsed = !document.documentElement.classList.contains('sidebar-collapsed')
    closeSidebarFlyouts()
    setSidebarCollapsed(collapsed)
    writeSidebarPreference(SIDEBAR_COLLAPSED_KEY, collapsed)
    return
  }
  const toggle = target.closest('.app-sidebar__group-toggle')
  const group = toggle?.closest('[data-sidebar-group]')
  if (!group) {
    if (!target.closest('.app-sidebar__group-items[data-flyout]')) {
      closeSidebarFlyouts()
    }
    return
  }
  const items = group.querySelector('.app-sidebar__group-items')
  if (isSidebarRail()) {
    const opening = !items.hasAttribute('data-flyout')
    closeSidebarFlyouts()
    if (opening) {
      items.setAttribute('data-flyout', '')
      items.hidden = false
    }
    return
  }
  const open = toggle.getAttribute('aria-expanded') !== 'true'
  setSidebarGroupOpen(group, open)
  const stored = readSidebarPreference(SIDEBAR_OPEN_KEY, [])
  const openGroups = new Set(Array.isArray(stored) ? stored : [])
  if (open) {
    openGroups.add(group.dataset.sidebarGroup)
  } else {
    openGroups.delete(group.dataset.sidebarGroup)
  }
  writeSidebarPreference(SIDEBAR_OPEN_KEY, [...openGroups])
})

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    closeSidebarFlyouts()
  }
})

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initSidebar)
} else {
  initSidebar()
}
