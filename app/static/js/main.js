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
 * 對話框開關與拖曳上傳點擊觸發。
 */
document.addEventListener('click', (event) => {
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

// 財務審核（2-4）：會計科目與扣抵代號連動
// 選科目 → 帶出該科目的扣抵代號；選扣抵代號 → 科目只列出同一類，不同類的已選科目會清空
function filterAccountsByDeduction(deductionSelect) {
  const accountSelect = document.querySelector(deductionSelect.dataset.accountFilter)
  if (!accountSelect) {
    return
  }
  const code = deductionSelect.value
  for (const option of accountSelect.options) {
    if (!option.value) {
      continue
    }
    const mismatch = Boolean(code) && option.dataset.deductionCode !== code
    option.hidden = mismatch
    option.disabled = mismatch
    if (mismatch && option.selected) {
      accountSelect.value = ''
    }
  }
}

document.addEventListener('change', (event) => {
  const accountSelect = event.target.closest('select[data-account-deduction]')
  if (accountSelect) {
    const option = accountSelect.options[accountSelect.selectedIndex]
    const deduction = option ? option.dataset.deductionCode : ''
    const target = document.querySelector(accountSelect.dataset.accountDeduction)
    if (target && deduction) {
      target.value = deduction
      filterAccountsByDeduction(target)
    }
    return
  }
  const deductionSelect = event.target.closest('select[data-account-filter]')
  if (deductionSelect) {
    filterAccountsByDeduction(deductionSelect)
  }
})
