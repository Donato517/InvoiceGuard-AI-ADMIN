// main.js 的「送出請求後的遮罩」事件處理單元測試（Node 內建 assert，不需任何套件）。
// 在 vm 裡載入真正的 busy.js 與 main.js，用假 DOM 記錄事件監聽器，再手動派送 submit／htmx 事件。
// 這是假 DOM 的邏輯測試，不是瀏覽器行為驗證（瀏覽器行為另用本機測試頁驗證）。
// 執行：node tests/unit/js/main_busy.test.js（由 tests/unit/test_busy_manager_node.py 呼叫）
'use strict'
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

// BUSY_STATIC_DIR 只給「改壞程式後測試應該失敗」的檢查用；平常使用專案的 app/static/js
const STATIC = process.env.BUSY_STATIC_DIR || path.join(__dirname, '../../../app/static/js')

class El {
  constructor(tag) {
    this.tag = tag
    this.id = ''
    this.className = ''
    this.children = []
    this.parentNode = null
    this.attrs = {}
    this.dataset = {}
    this.textEl = { textContent: '' }
    this.classList = { add() {}, remove() {}, contains: () => false, toggle() {} }
  }
  get tagName() {
    return this.tag.toUpperCase()
  }
  setAttribute(k, v) {
    this.attrs[k] = v
  }
  getAttribute(k) {
    return k in this.attrs ? this.attrs[k] : null
  }
  append(child) {
    child.remove()
    child.parentNode = this
    this.children.push(child)
  }
  remove() {
    if (this.parentNode) {
      this.parentNode.children = this.parentNode.children.filter((c) => c !== this)
      this.parentNode = null
    }
  }
  querySelector(sel) {
    return sel === '.busy-overlay__text' ? this.textEl : null
  }
  closest() {
    return null
  }
  find(id) {
    if (this.id === id) return this
    for (const child of this.children) {
      const found = child.find(id)
      if (found) return found
    }
    return null
  }
}

// 建一個載入 busy.js 與 main.js 的環境；回傳可派送事件的輔助函式
function load() {
  const body = new El('body')
  const listeners = { document: {}, window: {} }
  const record = (store) => (type, fn) => {
    ;(store[type] = store[type] || []).push(fn)
  }
  const document = {
    body,
    documentElement: new El('html'),
    readyState: 'complete',
    createElement: (tag) => new El(tag),
    getElementById: (id) => body.find(id),
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: record(listeners.document),
  }
  const window = {
    addEventListener: record(listeners.window),
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    confirm: () => true,
  }
  window.window = window
  const sandbox = vm.createContext({
    window,
    document,
    htmx: undefined,
    console,
    setTimeout,
    clearTimeout,
    globalThis: undefined,
    MutationObserver: class {
      observe() {}
    },
  })
  sandbox.globalThis = sandbox
  for (const file of ['busy.js', 'main.js']) {
    vm.runInContext(fs.readFileSync(path.join(STATIC, file), 'utf8'), sandbox, { filename: file })
  }
  const overlayText = () => {
    const overlay = document.getElementById('busy-overlay')
    return overlay ? overlay.textEl.textContent : null
  }
  const dispatch = (store, type, event) => {
    for (const fn of listeners[store][type] || []) fn(event)
  }
  // 假表單：attrs 是屬性（method、action、target、hx-post…），busy 是 data-busy（undefined＝沒有設定）
  const form = (attrs = {}, busy) => {
    const f = new El('form')
    f.attrs = { ...attrs }
    if (busy !== undefined) f.dataset.busy = busy
    return f
  }
  const submit = (f, defaultPrevented = false) =>
    dispatch('document', 'submit', { target: f, defaultPrevented })
  // 假的 HTMX 事件：detail.elt 以 closest 回傳帶 data-busy 的祖先
  const htmxEvent = (verb, { busy, xhr = {} } = {}) => {
    const marked = busy === undefined ? null : { dataset: { busy } }
    return { detail: { xhr, elt: { closest: () => marked }, requestConfig: { verb } } }
  }
  const busy = sandbox.window.InvoiceGuardBusy
  return { busy, overlayText, dispatch, form, submit, htmxEvent, window }
}

const tests = []
const test = (name, fn) => tests.push([name, fn])

test('GET 表單沒有 data-busy：預設不顯示遮罩', () => {
  const t = load()
  t.submit(t.form({ method: 'get', action: '/accounts' }))
  assert.equal(t.overlayText(), null)
  assert.equal(t.busy.count(), 0)
})

test('沒有 method 屬性（預設就是 GET）的表單也不顯示遮罩', () => {
  const t = load()
  t.submit(t.form({ action: '/suppliers' }))
  assert.equal(t.overlayText(), null)
})

test('GET 表單設定非空 data-busy：顯示指定訊息', () => {
  const t = load()
  t.submit(t.form({ method: 'get', action: '/accounts' }, '正在查詢…'))
  assert.equal(t.overlayText(), '正在查詢…')
  assert.equal(t.busy.count(), 1)
})

test('GET 表單設定 data-busy=""：不顯示遮罩', () => {
  const t = load()
  t.submit(t.form({ method: 'get', action: '/accounts' }, ''))
  assert.equal(t.overlayText(), null)
  assert.equal(t.busy.count(), 0)
})

test('POST 表單沒有 data-busy：預設顯示「處理中，請稍候…」', () => {
  const t = load()
  t.submit(t.form({ method: 'post', action: '/logout' }))
  assert.equal(t.overlayText(), '處理中，請稍候…')
})

test('POST 表單自訂訊息與 data-busy=""', () => {
  const t = load()
  t.submit(t.form({ method: 'post', action: '/knowledge/x/index' }, '正在建立索引…'))
  assert.equal(t.overlayText(), '正在建立索引…')
  const t2 = load()
  t2.submit(t2.form({ method: 'post', action: '/invoices/filing/export' }, ''))
  assert.equal(t2.overlayText(), null, '下載表單 data-busy="" 不可留下遮罩')
})

test('POST 開新分頁（target）不顯示；大小寫不同的 POST 仍視為 POST', () => {
  const t = load()
  t.submit(t.form({ method: 'post', target: '_blank' }))
  assert.equal(t.overlayText(), null)
  t.submit(t.form({ method: 'POST', action: '/x' }))
  assert.equal(t.overlayText(), '處理中，請稍候…')
})

test('已被取消的送出（確認視窗按取消、其他腳本自行處理）不顯示遮罩', () => {
  const t = load()
  t.submit(t.form({ method: 'post' }), true)
  t.submit(t.form({ method: 'get' }, '正在查詢…'), true)
  assert.equal(t.overlayText(), null)
})

test('HTMX 表單自己處理：主處理函式不為 hx-post／hx-get 表單額外取得換頁 Token', () => {
  const t = load()
  t.submit(t.form({ 'hx-post': '/department', method: 'post' }))
  t.submit(t.form({ 'hx-get': '/invoices', method: 'get' }))
  assert.equal(t.overlayText(), null)
})

test('HTMX GET（即時搜尋）不取得遮罩，也不影響別人的 Token', () => {
  const t = load()
  const post = t.htmxEvent('post')
  t.dispatch('document', 'htmx:beforeSend', post)
  assert.equal(t.overlayText(), '處理中，請稍候…')
  const get = t.htmxEvent('get')
  t.dispatch('document', 'htmx:beforeSend', get)
  t.dispatch('document', 'htmx:afterRequest', get) // GET 先結束
  assert.equal(t.overlayText(), '處理中，請稍候…', 'GET 結束不可關掉 POST 的遮罩')
  t.dispatch('document', 'htmx:afterRequest', post)
  assert.equal(t.overlayText(), null)
})

test('HTMX POST 與 fetch（invoiceGuardBusy）並行：先結束的不會提早關閉遮罩', () => {
  const t = load()
  const post = t.htmxEvent('post')
  t.dispatch('document', 'htmx:beforeSend', post)
  const release = t.window.invoiceGuardBusy('正在送出補正，請稍候…')
  t.dispatch('document', 'htmx:afterRequest', post) // HTMX 先結束
  assert.equal(t.overlayText(), '正在送出補正，請稍候…', 'fetch 仍在進行，遮罩要維持')
  release()
  assert.equal(t.overlayText(), null)
  // 反過來：fetch 先結束
  const post2 = t.htmxEvent('post')
  t.dispatch('document', 'htmx:beforeSend', post2)
  const release2 = t.window.invoiceGuardBusy('fetch')
  release2()
  assert.equal(t.overlayText(), '處理中，請稍候…', 'HTMX 仍在進行，遮罩要維持')
  t.dispatch('document', 'htmx:afterRequest', post2)
  assert.equal(t.overlayText(), null)
})

test('HTMX data-busy="" 與自訂訊息；重複結束事件不影響其他請求', () => {
  const t = load()
  const optOut = t.htmxEvent('post', { busy: '' })
  t.dispatch('document', 'htmx:beforeSend', optOut)
  assert.equal(t.overlayText(), null)
  const custom = t.htmxEvent('post', { busy: '正在上傳文件，請勿關閉視窗…' })
  t.dispatch('document', 'htmx:beforeSend', custom)
  assert.equal(t.overlayText(), '正在上傳文件，請勿關閉視窗…')
  const other = t.htmxEvent('post')
  t.dispatch('document', 'htmx:beforeSend', other)
  for (const name of ['afterRequest', 'sendError', 'timeout', 'afterRequest']) t.dispatch('document', `htmx:${name}`, custom)
  assert.equal(t.overlayText(), '處理中，請稍候…', '同一請求的重複結束事件只釋放自己')
  t.dispatch('document', 'htmx:afterRequest', other)
  assert.equal(t.overlayText(), null)
})

test('pageshow（從快取還原）清空遮罩；之後舊的結束事件不影響新請求', () => {
  const t = load()
  const old = t.htmxEvent('post')
  t.dispatch('document', 'htmx:beforeSend', old)
  t.dispatch('window', 'pageshow', { persisted: true })
  assert.equal(t.overlayText(), null)
  const fresh = t.htmxEvent('post')
  t.dispatch('document', 'htmx:beforeSend', fresh)
  t.dispatch('document', 'htmx:afterRequest', old)
  assert.equal(t.overlayText(), '處理中，請稍候…')
  t.dispatch('document', 'htmx:afterRequest', fresh)
  assert.equal(t.overlayText(), null)
})

let failed = 0
for (const [name, fn] of tests) {
  try {
    fn()
    console.log(`ok - ${name}`)
  } catch (error) {
    failed++
    console.log(`not ok - ${name}\n${error.stack}`)
  }
}
console.log(`${tests.length - failed}/${tests.length} passed`)
process.exit(failed ? 1 : 0)
