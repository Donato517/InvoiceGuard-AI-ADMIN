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
    this.innerHTML = ''
    this.disabled = false
    const classes = new Set()
    this.classes = classes
    this.classList = {
      add: (c) => classes.add(c),
      remove: (c) => classes.delete(c),
      contains: (c) => classes.has(c),
      toggle() {},
    }
  }
  get tagName() {
    return this.tag.toUpperCase()
  }
  get parentElement() {
    return this.parentNode
  }
  get href() {
    return this.attrs.href
  }
  setAttribute(k, v) {
    this.attrs[k] = String(v)
  }
  getAttribute(k) {
    return k in this.attrs ? this.attrs[k] : null
  }
  hasAttribute(k) {
    return k in this.attrs
  }
  removeAttribute(k) {
    delete this.attrs[k]
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
    if (sel === '.busy-overlay__text') return this.textEl
    if (this.submitButton && /button/.test(sel)) return this.submitButton
    return null
  }
  matches(sel) {
    if (sel === 'a[href]') return this.tag === 'a' && 'href' in this.attrs
    if (sel === 'form') return this.tag === 'form'
    if (sel === '[data-busy]') return this.dataset.busy !== undefined
    return false
  }
  closest(sel) {
    for (let el = this; el; el = el.parentNode || el.owner) if (el.matches(sel)) return el
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

// 假的 Navigation API：測試用 fire('navigate', event) 模擬瀏覽器的導覽事件
function fakeNavigation() {
  const handlers = {}
  return {
    addEventListener: (type, fn) => {
      ;(handlers[type] = handlers[type] || []).push(fn)
    },
    fire: (type, event = {}) => {
      for (const fn of handlers[type] || []) fn(event)
    },
  }
}

// 建一個載入 busy.js 與 main.js 的環境；回傳可派送事件的輔助函式。options.navigation：是否提供 Navigation API
function load({ navigation = false } = {}) {
  const body = new El('body')
  const listeners = { document: {}, window: {} }
  const record = (store) => (type, fn, capture) => {
    ;(store[type] = store[type] || []).push({ fn, capture: capture === true })
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
  const nav = navigation ? fakeNavigation() : undefined
  const window = {
    addEventListener: record(listeners.window),
    localStorage: { getItem: () => null, setItem() {} },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    confirm: () => true,
    location: { href: 'https://app.test/invoices', origin: 'https://app.test', pathname: '/invoices', search: '' },
    navigation: nav,
  }
  window.window = window
  const sandbox = vm.createContext({
    window,
    document,
    URL,
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
  // 依註冊順序派送；capture 監聽器先於一般監聽器（只模擬到本測試需要的程度）；stopImmediatePropagation 會中止後續
  const dispatch = (store, type, event) => {
    const list = listeners[store][type] || []
    let stopped = false
    event.stopImmediatePropagation = () => {
      stopped = true
    }
    event.preventDefault = event.preventDefault || (() => {
      event.defaultPrevented = true
    })
    for (const { fn } of list.filter((l) => l.capture)) if (!stopped) fn(event)
    for (const { fn } of list.filter((l) => !l.capture)) if (!stopped) fn(event)
    return event
  }
  // 假表單：attrs 是屬性（method、action、target、hx-post…），busy 是 data-busy（undefined＝沒有設定）
  const form = (attrs = {}, busy, extra = {}) => {
    const f = new El('form')
    f.attrs = { ...attrs }
    if (busy !== undefined) f.dataset.busy = busy
    if (extra.loadingMode) f.dataset.loadingMode = extra.loadingMode
    if (extra.loadingText) f.dataset.loadingText = extra.loadingText
    return f
  }
  const button = (name, html = '送出') => {
    const b = new El('button')
    b.attrs.name = name
    b.innerHTML = html
    return b
  }
  const submit = (f, defaultPrevented = false, submitter) =>
    dispatch('document', 'submit', { target: f, defaultPrevented, submitter })
  const link = (href, { sidebar = true, busy, attrs = {} } = {}) => {
    const a = new El('a')
    a.attrs = { href, ...attrs }
    if (sidebar) a.classList.add('app-sidebar__link')
    if (busy !== undefined) a.dataset.busy = busy
    return a
  }
  const click = (a, extra = {}) =>
    dispatch('document', 'click', { target: a, button: 0, defaultPrevented: false, ...extra })
  // 假的 HTMX 事件：detail.elt 以 closest 回傳帶 data-busy 的祖先
  const htmxEvent = (verb, { busy, xhr = {}, elt, submitter } = {}) => {
    const marked = busy === undefined ? null : { dataset: { busy } }
    const target = elt || { closest: (sel) => (sel === '[data-busy]' ? marked : null), dataset: {} }
    return {
      detail: { xhr, elt: target, requestConfig: { verb, triggeringEvent: submitter ? { submitter } : undefined } },
    }
  }
  const busy = sandbox.window.InvoiceGuardBusy
  return { busy, overlayText, dispatch, form, button, submit, link, click, htmxEvent, window, nav }
}

const tick = (ms = 5) => new Promise((resolve) => setTimeout(resolve, ms))

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

// ---------- 模式 B：側邊欄導覽（有 Navigation API：等 navigate 事件確認導覽真的開始才顯示） ----------
test('側邊欄左鍵點擊：navigate 事件確認導覽開始後才顯示「載入中，請稍候…」', async () => {
  const t = load({ navigation: true })
  t.click(t.link('/invoices/upload'))
  assert.equal(t.overlayText(), null, '點擊當下還不知道導覽會不會開始，不可先顯示')
  t.nav.fire('navigate', { defaultPrevented: false, hashChange: false, downloadRequest: null })
  assert.equal(t.overlayText(), '載入中，請稍候…')
  assert.equal(t.busy.count(), 1)
})

test('點擊後沒有等到 navigate（被取消、新分頁等）：之後別的導覽事件不會顯示遮罩', async () => {
  const t = load({ navigation: true })
  t.click(t.link('/invoices'))
  await tick() // 工作回合結束，navigate 沒來
  t.nav.fire('navigate', { defaultPrevented: false, hashChange: false, downloadRequest: null })
  assert.equal(t.overlayText(), null)
})

test('新分頁與非同頁導覽的點擊都不要求遮罩：Ctrl／Cmd／Shift／Alt、中鍵、target、download、外部網址、同頁錨點', async () => {
  const cases = [
    ['Ctrl', t0 => t0.click(t0.link('/a'), { ctrlKey: true })],
    ['Cmd', t0 => t0.click(t0.link('/a'), { metaKey: true })],
    ['Shift', t0 => t0.click(t0.link('/a'), { shiftKey: true })],
    ['Alt', t0 => t0.click(t0.link('/a'), { altKey: true })],
    ['中鍵', t0 => t0.click(t0.link('/a'), { button: 1 })],
    ['右鍵', t0 => t0.click(t0.link('/a'), { button: 2 })],
    ['target=_blank', t0 => t0.click(t0.link('/a', { attrs: { target: '_blank' } }))],
    ['download', t0 => t0.click(t0.link('/a', { attrs: { download: '' } }))],
    ['aria-disabled', t0 => t0.click(t0.link('/a', { attrs: { 'aria-disabled': 'true' } }))],
    ['外部網址', t0 => t0.click(t0.link('https://other.test/x'))],
    ['同頁錨點', t0 => t0.click(t0.link('/invoices#top'))],
    ['已被取消的點擊', t0 => t0.click(t0.link('/a'), { defaultPrevented: true })],
  ]
  for (const [name, act] of cases) {
    const t = load({ navigation: true })
    act(t)
    // 就算之後真的來了 navigate 事件（例如別的程式導覽），也不能因為這些點擊顯示遮罩
    t.nav.fire('navigate', { defaultPrevented: false, hashChange: false, downloadRequest: null })
    assert.equal(t.overlayText(), null, `${name} 不應顯示遮罩`)
  }
})

test('navigate 事件是錨點切換、下載或已被取消：不顯示遮罩', async () => {
  for (const event of [{ hashChange: true }, { downloadRequest: 'file.csv' }, { defaultPrevented: true }]) {
    const t = load({ navigation: true })
    t.click(t.link('/invoices'))
    t.nav.fire('navigate', { defaultPrevented: false, hashChange: false, downloadRequest: null, ...event })
    assert.equal(t.overlayText(), null)
  }
})

test('導覽失敗或被使用者中止（navigateerror）：遮罩釋放，之後可再次導覽', async () => {
  const t = load({ navigation: true })
  t.click(t.link('/invoices'))
  t.nav.fire('navigate', {})
  assert.equal(t.overlayText(), '載入中，請稍候…')
  t.nav.fire('navigateerror', {})
  assert.equal(t.overlayText(), null)
  assert.equal(t.busy.count(), 0)
  t.click(t.link('/knowledge'))
  t.nav.fire('navigate', {})
  assert.equal(t.overlayText(), '載入中，請稍候…')
})

test('非側邊欄連結：沒有 data-busy 不顯示；非空 data-busy 顯示；data-busy="" 不顯示', async () => {
  const t = load({ navigation: true })
  t.click(t.link('/x', { sidebar: false }))
  t.nav.fire('navigate', {})
  assert.equal(t.overlayText(), null)
  t.click(t.link('/x', { sidebar: false, busy: '正在更新清單…' }))
  t.nav.fire('navigate', {})
  assert.equal(t.overlayText(), '正在更新清單…')
  const t2 = load({ navigation: true })
  t2.click(t2.link('/x', { sidebar: false, busy: '' }))
  t2.nav.fire('navigate', {})
  assert.equal(t2.overlayText(), null)
})

test('沒有 Navigation API 的瀏覽器：側邊欄點擊在事件處理完且未被取消才顯示；Escape 釋放', async () => {
  const t = load({ navigation: false })
  t.click(t.link('/invoices'))
  await tick()
  assert.equal(t.overlayText(), '載入中，請稍候…')
  t.dispatch('document', 'keydown', { key: 'Escape' })
  assert.equal(t.overlayText(), null, 'Escape（停止載入）要能釋放')
  // 被其他處理函式取消的點擊（在 defer 之前就 preventDefault）不顯示
  const a = t.link('/invoices')
  const event = t.click(a)
  event.defaultPrevented = true
  await tick()
  assert.equal(t.overlayText(), null)
})

// ---------- 換頁表單：Navigation API 閘門與同一動作防重 ----------
test('POST 表單（有 Navigation API）：事件處理完沒被取消就顯示（導覽排在之後的工作回合）；導覽開始後 navigateerror 釋放並可再送出', async () => {
  const t = load({ navigation: true })
  const f = t.form({ method: 'post', action: '/x' })
  const ev = t.submit(f)
  assert.equal(t.overlayText(), null, '送出事件還在處理時不先顯示')
  await tick()
  assert.equal(t.overlayText(), '處理中，請稍候…', '瀏覽器要在之後的工作回合才導覽，所以不能等同一回合的 navigate')
  t.nav.fire('navigate', {}) // 這次導覽開始
  t.nav.fire('navigateerror', {}) // 使用者按停止
  assert.equal(t.overlayText(), null)
  assert.notEqual(t.submit(f).defaultPrevented, true, '導覽被取消後同一表單可以重新送出')
  assert.equal(ev.defaultPrevented, false)
})

test('上一次導覽補發的 navigateerror 不會誤關這次的表單遮罩（要等到這次的 navigate 事件之後才算數）', async () => {
  const t = load({ navigation: true })
  t.submit(t.form({ method: 'post', action: '/x' }))
  await tick()
  t.nav.fire('navigateerror', {}) // 屬於前一次導覽（例如下載）的錯誤，在新導覽開始時才補發
  assert.equal(t.overlayText(), '處理中，請稍候…', '還沒看到這次的 navigate 事件，不可釋放')
  t.nav.fire('navigate', {})
  t.nav.fire('navigateerror', {})
  assert.equal(t.overlayText(), null)
})

test('送出事件之後才被其他處理函式取消（preventDefault）：不顯示遮罩', async () => {
  const t = load({ navigation: true })
  const ev = t.submit(t.form({ method: 'post', action: '/x' }))
  ev.defaultPrevented = true // 之後註冊的處理函式取消了這次送出
  await tick()
  assert.equal(t.overlayText(), null)
  assert.equal(t.busy.count(), 0)
})

test('同一個表單進行中不再送第二次（鍵盤 Enter、requestSubmit）；pageshow 還原後可再送', async () => {
  const t = load({ navigation: true })
  const f = t.form({ method: 'post', action: '/x' })
  t.submit(f)
  await tick()
  t.nav.fire('navigate', {})
  const dup = t.submit(f)
  assert.equal(dup.defaultPrevented, true, '重複送出必須被擋')
  assert.equal(t.busy.count(), 1, '重複送出不可多取得擁有者')
  t.dispatch('window', 'pageshow', { persisted: true })
  const after = t.submit(f)
  assert.notEqual(after.defaultPrevented, true)
})

test('不同表單互不影響；沒有顯示遮罩的表單（data-busy=""、GET）不會被當成進行中', async () => {
  const t = load({ navigation: true })
  const a = t.form({ method: 'post', action: '/a' })
  const b = t.form({ method: 'post', action: '/b' })
  t.submit(a)
  await tick()
  t.nav.fire('navigate', {})
  assert.notEqual(t.submit(b).defaultPrevented, true)
  const dl = t.form({ method: 'post', action: '/download' }, '')
  t.submit(dl)
  await tick()
  assert.notEqual(t.submit(dl).defaultPrevented, true, '下載表單不留旗標，可重複使用')
})

// ---------- 模式 A：按鈕載入 ----------
test('表單 data-loading-mode="button"：送出鈕轉圈＋訊息、之後才停用（保留 name/value）、不顯示全頁遮罩', async () => {
  const t = load({ navigation: false })
  const f = t.form({ method: 'post', action: '/x' }, undefined, { loadingMode: 'button', loadingText: '辨識中…' })
  const b = t.button('intent', '上傳並開始辨識 →')
  t.submit(f, false, b)
  assert.equal(t.overlayText(), null, '模式 A 不顯示全頁遮罩')
  assert.ok(b.classList.contains('btn--loading'))
  assert.equal(b.getAttribute('aria-busy'), 'true')
  assert.match(b.innerHTML, /btn__spinner/)
  assert.match(b.innerHTML, /辨識中…/)
  assert.equal(b.disabled, false, '送出事件處理完之前不可停用，否則按鈕的 name/value 會遺失')
  await tick()
  assert.equal(b.disabled, true)
  t.dispatch('window', 'pageshow', { persisted: true })
  assert.equal(b.innerHTML, '上傳並開始辨識 →')
  assert.equal(b.disabled, false)
  assert.equal(b.classList.contains('btn--loading'), false)
  assert.equal(b.getAttribute('aria-busy'), null)
})

test('data-loading-mode="disable"：只停用並標示 aria-busy，不改按鈕內容；訊息有跳脫', async () => {
  const t = load({ navigation: false })
  const f = t.form({ method: 'post', action: '/x' }, undefined, { loadingMode: 'disable' })
  const b = t.button('active', '<span>啟用</span>')
  t.submit(f, false, b)
  assert.equal(b.innerHTML, '<span>啟用</span>')
  assert.equal(b.getAttribute('aria-busy'), 'true')
  await tick()
  assert.equal(b.disabled, true)
  const t2 = load({ navigation: false })
  const f2 = t2.form({ method: 'post' }, undefined, { loadingMode: 'button', loadingText: '<b>x</b>' })
  const b2 = t2.button('a')
  t2.submit(f2, false, b2)
  assert.ok(!b2.innerHTML.includes('<b>'), '訊息要當純文字，不可注入標籤')
})

test('按鈕載入中的表單不可重複送出；導覽被取消後恢復', async () => {
  const t = load({ navigation: true })
  const f = t.form({ method: 'post' }, undefined, { loadingMode: 'button' })
  const b = t.button('go')
  t.submit(f, false, b)
  await tick()
  t.nav.fire('navigate', {})
  assert.equal(t.submit(f, false, b).defaultPrevented, true)
  t.nav.fire('navigateerror', {})
  assert.equal(b.innerHTML, '送出')
  assert.notEqual(t.submit(f, false, b).defaultPrevented, true)
})

test('HTMX 寫入 data-loading-mode：按鈕載入、沒有全頁遮罩，結束後還原', async () => {
  const t = load({ navigation: false })
  const b = t.button('toggle', '啟用')
  const f = t.form({ 'hx-post': '/department/1/status' }, undefined, { loadingMode: 'disable' })
  f.children.push(b)
  const ev = t.htmxEvent('post', { elt: f, submitter: b })
  t.dispatch('document', 'htmx:beforeSend', ev)
  assert.equal(t.overlayText(), null)
  assert.equal(b.getAttribute('aria-busy'), 'true')
  await tick()
  assert.equal(b.disabled, true)
  t.dispatch('document', 'htmx:afterRequest', ev)
  assert.equal(b.getAttribute('aria-busy'), null)
  assert.equal(b.disabled, false)
  t.dispatch('document', 'htmx:afterRequest', ev) // 重複結束事件
  assert.equal(b.disabled, false)
})

test('HTMX 失敗、網路錯誤、逾時、中止：按鈕模式都會還原', async () => {
  for (const name of ['responseError', 'sendError', 'timeout', 'sendAbort']) {
    const t = load({ navigation: false })
    const b = t.button('x', '送出')
    const f = t.form({ 'hx-post': '/x' }, undefined, { loadingMode: 'button' })
    const ev = t.htmxEvent('post', { elt: f, submitter: b })
    t.dispatch('document', 'htmx:beforeSend', ev)
    t.dispatch('document', `htmx:${name}`, ev)
    assert.equal(b.innerHTML, '送出', `${name} 後要還原內容`)
    assert.equal(b.getAttribute('aria-busy'), null)
  }
})

test('hx-disabled-elt 先停用的按鈕：結束後不可被還原成停用（部門啟停開關）；原本就停用的按鈕維持停用', async () => {
  const t = load({ navigation: false })
  const b = t.button('active', '<span>啟用</span>')
  const f = t.form({ 'hx-post': '/department/1/status' }, undefined, { loadingMode: 'disable' })
  b.disabled = true // HTMX 在 beforeSend 之前就先停用了
  b.attrs['data-disabled-by-htmx'] = ''
  const ev = t.htmxEvent('post', { elt: f, submitter: b })
  t.dispatch('document', 'htmx:beforeSend', ev)
  await tick()
  t.dispatch('document', 'htmx:afterRequest', ev)
  assert.equal(b.disabled, false, '結束後按鈕要能再按')
  const t2 = load({ navigation: false })
  const locked = t2.button('x', '徽章')
  locked.disabled = true // 本來就唯讀（沒有 data-disabled-by-htmx）
  const f2 = t2.form({ 'hx-post': '/x' }, undefined, { loadingMode: 'disable' })
  const ev2 = t2.htmxEvent('post', { elt: f2, submitter: locked })
  t2.dispatch('document', 'htmx:beforeSend', ev2)
  t2.dispatch('document', 'htmx:afterRequest', ev2)
  assert.equal(locked.disabled, true, '原本就停用的按鈕不可被還原成啟用')
})

// ---------- HTMX 同一元素寫入防重 ----------
test('同一個 HTMX 元素的寫入進行中，第二個寫入請求在 beforeRequest 被取消；結束後可再送', async () => {
  const t = load({ navigation: false })
  const elt = { closest: () => null, dataset: {} }
  const first = t.htmxEvent('post', { elt, xhr: {} })
  const second = t.htmxEvent('post', { elt, xhr: {} })
  t.dispatch('document', 'htmx:beforeRequest', t.htmxEvent('post', { elt }))
  t.dispatch('document', 'htmx:beforeSend', first)
  const dup = t.dispatch('document', 'htmx:beforeRequest', second)
  assert.equal(dup.defaultPrevented, true, '進行中的寫入不可再送')
  assert.equal(t.busy.count(), 1, '被取消的重複請求不可取得 Token')
  t.dispatch('document', 'htmx:afterRequest', first)
  const next = t.dispatch('document', 'htmx:beforeRequest', t.htmxEvent('post', { elt }))
  assert.notEqual(next.defaultPrevented, true)
})

test('HTMX 排隊的重複觸發：寫入進行中，同一個元素（或其子元素）的 click／submit 在 capture 階段被取消', async () => {
  const t = load({ navigation: false })
  const trigger = t.button('go', '送出')
  const first = t.htmxEvent('post', { elt: trigger, xhr: {} })
  t.dispatch('document', 'htmx:beforeSend', first)
  const child = new El('span')
  trigger.append(child)
  const clickOnChild = t.dispatch('document', 'click', { target: child, button: 0 })
  assert.equal(clickOnChild.defaultPrevented, true, '鍵盤 Enter／Space 或連點產生的 click 要被擋掉')
  const submitDup = t.submit(trigger)
  assert.equal(submitDup.defaultPrevented, true)
  t.dispatch('document', 'htmx:afterRequest', first)
  const clickAfter = t.dispatch('document', 'click', { target: trigger, button: 0 })
  assert.notEqual(clickAfter.defaultPrevented, true, '結束後可以再按')
})

test('HTMX GET（即時搜尋）進行中的點擊不會被擋', async () => {
  const t = load({ navigation: false })
  const input = t.button('q')
  t.dispatch('document', 'htmx:beforeSend', t.htmxEvent('get', { elt: input, xhr: {} }))
  const click = t.dispatch('document', 'click', { target: input, button: 0 })
  assert.notEqual(click.defaultPrevented, true)
})

test('HTMX GET（即時搜尋）同一元素連續請求不被擋；不同元素的寫入互不影響', async () => {
  const t = load({ navigation: false })
  const elt = { closest: () => null, dataset: {} }
  t.dispatch('document', 'htmx:beforeSend', t.htmxEvent('get', { elt }))
  const again = t.dispatch('document', 'htmx:beforeRequest', t.htmxEvent('get', { elt }))
  assert.notEqual(again.defaultPrevented, true)
  const a = { closest: () => null, dataset: {} }
  const b = { closest: () => null, dataset: {} }
  t.dispatch('document', 'htmx:beforeSend', t.htmxEvent('post', { elt: a }))
  const other = t.dispatch('document', 'htmx:beforeRequest', t.htmxEvent('post', { elt: b }))
  assert.notEqual(other.defaultPrevented, true)
})

let failed = 0
;(async () => {
  for (const [name, fn] of tests) {
    try {
      await fn()
      console.log(`ok - ${name}`)
    } catch (error) {
      failed++
      console.log(`not ok - ${name}\n${error.stack}`)
    }
  }
  console.log(`${tests.length - failed}/${tests.length} passed`)
  process.exit(failed ? 1 : 0)
})()
