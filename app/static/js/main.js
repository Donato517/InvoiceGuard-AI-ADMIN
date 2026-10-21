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
