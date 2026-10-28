/**
 * @file 單一部門權限頁的未儲存提醒。
 * @description 只比較勾選與已儲存值；預覽／儲存仍走同源表單。
 * 不使用 fetch、localStorage、sessionStorage、Cookie 或任何持久草稿。
 */
(() => {
  const page = document.querySelector('[data-permission-page]')
  if (!page) return
  const editor = page.querySelector('[data-permission-editor]')
  const controls = editor ? Array.from(editor.querySelectorAll('input[type="checkbox"][name="grant"]')) : []
  const message = page.querySelector('[data-unsaved-message]')
  let leaving = false
  const isDirty = () => editor
    ? controls.some(input => input.checked !== (input.dataset.saved === 'true'))
    : page.dataset.unsaved === 'true'
  const refresh = () => {
    if (message) message.textContent = isDirty() ? '有尚未儲存的變更。' : '尚未修改設定。'
  }
  if (editor) editor.addEventListener('change', refresh)
  document.addEventListener('submit', event => {
    if (event.target.matches('form[data-permission-transition]') && !event.defaultPrevented) {
      leaving = true
    }
  })
  window.addEventListener('beforeunload', event => {
    if (!leaving && isDirty()) {
      event.preventDefault()
      event.returnValue = ''
    }
  })
  window.addEventListener('pageshow', () => {
    leaving = false
    refresh()
  })
  refresh()
})()
