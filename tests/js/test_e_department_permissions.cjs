/** @file 使用 Node 內建測試工具驗證未儲存提醒；DOM 替身，不啟動站台或網路。 */
const test = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const script = fs.readFileSync(path.resolve(__dirname, '../../app/static/js/e-department-permissions.js'), 'utf8')
const shared = fs.readFileSync(path.resolve(__dirname, '../../app/static/js/main.js'), 'utf8')

function setup({ preview = false, checked = false, saved = false, confirm = true } = {}) {
  const handlers = { document: {}, window: {}, editor: {} }
  const listen = scope => (name, callback) => (handlers[scope][name] ||= []).push(callback)
  const control = { checked, dataset: { saved: String(saved) } }
  const message = { textContent: '' }
  const editor = { querySelectorAll: () => [control], addEventListener: listen('editor') }
  const page = {
    dataset: { unsaved: String(preview) },
    querySelector: selector => selector === '[data-permission-editor]' ? (preview ? null : editor) : message
  }
  const document = { querySelector: () => page, addEventListener: listen('document') }
  const window = { addEventListener: listen('window'), confirm: () => confirm }
  const context = vm.createContext({ document, window })
  vm.runInContext(shared, context)
  vm.runInContext(script, context)
  function fire(scope, name, event = {}) {
    event.preventDefault = () => { event.defaultPrevented = true }
    for (const handler of handlers[scope][name] || []) handler(event)
    return event
  }
  const unload = () => Boolean(fire('window', 'beforeunload').defaultPrevented)
  const submit = (transition = true, needsConfirm = false) => fire('document', 'submit', {
    target: { dataset: { confirm: needsConfirm ? '確認儲存' : '' }, matches: () => transition }
  })
  return { control, message, fire, unload, submit }
}

test('未修改不提示；勾選修改會提示，還原後不再提示', () => {
  const page = setup()
  assert.equal(page.unload(), false)
  page.control.checked = true
  page.fire('editor', 'change')
  assert.equal(page.unload(), true)
  assert.match(page.message.textContent, /尚未儲存/)
  page.control.checked = false
  page.fire('editor', 'change')
  assert.equal(page.unload(), false)
})

test('預覽返回的勾選與已儲存值不同，進頁即提示', () => {
  const page = setup({ checked: true, saved: false })
  assert.equal(page.unload(), true)
  page.control.checked = false
  assert.equal(page.unload(), false)
})

test('預覽尚未寫入，離開會提示；合法送出確認才略過', () => {
  const page = setup({ preview: true })
  assert.equal(page.unload(), true)
  page.submit(true, true)
  assert.equal(page.unload(), false)
})

test('取消確認視窗後仍保留未儲存提醒', () => {
  const page = setup({ preview: true, confirm: false })
  assert.equal(page.submit(true, true).defaultPrevented, true)
  assert.equal(page.unload(), true)
})

test('搜尋或切換部門的 GET 不會清掉提醒', () => {
  const page = setup({ checked: true })
  page.submit(false)
  assert.equal(page.unload(), true)
})

test('預覽返回修改送出不提示；瀏覽器快取還原頁面後重新檢查', () => {
  const page = setup({ preview: true })
  page.submit()
  assert.equal(page.unload(), false)
  page.fire('window', 'pageshow')
  assert.equal(page.unload(), true)
})

test('一般頁面沒有對應元素時不加任何監聽', () => {
  vm.runInNewContext(script, { document: { querySelector: () => null } })
})
