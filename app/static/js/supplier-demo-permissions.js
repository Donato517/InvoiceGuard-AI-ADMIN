/** @file 單筆供應商功能勾選。只操作畫面，按儲存後才提交本機配置。 */
(() => {
  const form = document.getElementById('supplier-permission-form')
  if (!form) return
  const items = Array.from(form.querySelectorAll('[data-permission-action]:not(:disabled)'))
  const rows = Array.from(form.querySelectorAll('[data-permission-row]'))
  const notice = form.querySelector('#permission-dirty')
  function sync() {
    rows.forEach(row => {
      const controls = Array.from(row.querySelectorAll('[data-permission-action]:not(:disabled)'))
      const all = row.querySelector('[data-permission-row-all]')
      const count = controls.filter(item => item.checked).length
      all.checked = controls.length > 0 && count === controls.length
      all.indeterminate = count > 0 && count < controls.length
    })
  }
  function changed() {
    sync()
    notice.textContent = '有尚未儲存的變更，請按「儲存模擬設定」。'
  }
  items.forEach(item => item.addEventListener('change', changed))
  rows.forEach(row => row.querySelector('[data-permission-row-all]').addEventListener('change', event => {
    row.querySelectorAll('[data-permission-action]:not(:disabled)').forEach(item => { item.checked = event.target.checked })
    changed()
  }))
  form.querySelectorAll('[data-permission-select]').forEach(button => {
    button.addEventListener('click', () => {
      items.forEach(item => { item.checked = button.dataset.permissionSelect === 'all' })
      changed()
    })
  })
  sync()
})()
