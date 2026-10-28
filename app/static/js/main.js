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
 * 檔案選取變更（知識庫多檔上傳與發票單檔上傳預覽）
 */
document.addEventListener('change', (event) => {
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
  }
})

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
