/**
 * @file 部門授權整列／全部勾選。僅改表單，不讀取或寫入遠端資料。
 * 歡迎頁、disabled 欄位、隱藏 ID 與版本皆不受影響；仍需使用者儲存。
 */
(() => {
  document.querySelectorAll('[data-permission-bulk]').forEach((form) => {
    const rows = Array.from(form.querySelectorAll('[data-grant-row]'))
    const controls = form.querySelector('[data-bulk-controls]')
    const buttons = Array.from(form.querySelectorAll('[data-bulk-value]'))
    const status = form.querySelector('[data-bulk-status]')
    const locked = form.dataset.bulkLocked === 'true'
    const editable = (row) => Array.from(row.querySelectorAll('input[type="checkbox"][name="grant"]')).filter((box) => !box.disabled)

    const syncRow = (row) => {
      const toggle = row.querySelector('[data-row-toggle]')
      if (!toggle) return
      const boxes = editable(row)
      const checked = boxes.filter((box) => box.checked).length
      toggle.checked = boxes.length > 0 && checked === boxes.length
      toggle.indeterminate = checked > 0 && checked < boxes.length
      toggle.disabled = locked || boxes.length === 0
    }
    const refresh = () => {
      rows.forEach(syncRow)
      const empty = rows.every((row) => editable(row).length === 0)
      buttons.forEach((button) => { button.disabled = locked || empty })
    }
    const apply = (row, checked) => {
      editable(row).forEach((box) => {
        if (box.checked !== checked) {
          box.checked = checked
          box.dispatchEvent(new Event('change', { bubbles: true }))
        }
      })
      syncRow(row)
    }

    rows.forEach((row) => {
      const toggle = row.querySelector('[data-row-toggle]')
      if (!toggle) return
      row.querySelector('[data-row-controls]').hidden = false
      toggle.addEventListener('change', () => {
        if (locked || toggle.disabled) return
        apply(row, toggle.checked)
        if (status) status.textContent = '已調整本列權限，請按「儲存」套用。'
      })
      row.querySelectorAll('input[type="checkbox"][name="grant"]').forEach((box) => {
        box.addEventListener('change', () => syncRow(row))
      })
    })
    buttons.forEach((button) => {
      button.addEventListener('click', () => {
        if (locked || button.disabled) return
        rows.forEach((row) => apply(row, button.dataset.bulkValue === 'true'))
        if (status) status.textContent = '已調整全部可編輯權限，請按「儲存」套用。'
      })
    })
    if (controls) controls.hidden = rows.length === 0
    form.addEventListener('reset', () => queueMicrotask(refresh))
    window.addEventListener('pageshow', refresh)
    refresh()
  })
})()
