/**
 * @file 全站共用 JavaScript
 * @description 只放少量通用互動。頁面互動優先使用 HTMX 屬性（hx-get、hx-post…），
 *   不要自行寫 fetch 呼叫；資料一律由伺服器端（Python）處理後產生 HTML。
 */

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
 * 對話框開關：加上 data-open-dialog="dialog-id" 開啟，加上 data-close-dialog 關閉。
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
 * 檔案選取數量提示（例如知識庫多檔上傳）
 */
document.addEventListener('change', (event) => {
  if (event.target && event.target.id === 'document-files') {
    const summary = document.getElementById('fileSummary')
    if (summary) {
      const count = event.target.files ? event.target.files.length : 0
      summary.textContent = count > 0 ? `已選擇 ${count} 個檔案` : '尚未選擇檔案'
    }
  }
})
