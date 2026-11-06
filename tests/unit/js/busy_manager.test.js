// busy.js（全頁處理中遮罩的擁有者／Token 管理）的單元測試。
// 以 Node 內建 assert 執行，不需要任何套件；用極簡的假 DOM 取代瀏覽器。
// 執行：node tests/unit/js/busy_manager.test.js（由 tests/unit/test_busy_manager_node.py 呼叫）
'use strict'
const assert = require('node:assert/strict')
const path = require('node:path')

class El {
  constructor(tag) {
    this.tag = tag
    this.id = ''
    this.className = ''
    this.attrs = {}
    this.children = []
    this.parentNode = null
    this.textEl = { textContent: '' }
  }
  get tagName() {
    return this.tag.toUpperCase()
  }
  setAttribute(k, v) {
    this.attrs[k] = v
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
  find(id) {
    if (this.id === id) return this
    for (const child of this.children) {
      const found = child.find(id)
      if (found) return found
    }
    return null
  }
}

function freshBusy() {
  const body = new El('body')
  const state = { dialog: null, observer: null }
  // 極簡 MutationObserver：只記下 busy.js 註冊的回呼與選項，測試用 state.observer.fire(...) 模擬 <dialog> 的 open 屬性變化
  global.MutationObserver = class {
    constructor(callback) {
      this.callback = callback
      state.observer = this
    }
    observe(target, options) {
      this.target = target
      this.options = options
    }
    fire(records) {
      this.callback(records)
    }
  }
  global.document = {
    documentElement: new El('html'),
    body,
    createElement: (tag) => new El(tag),
    getElementById: (id) => body.find(id),
    querySelector: (sel) => (sel === 'dialog[open]' ? state.dialog : null),
  }
  delete require.cache[require.resolve(path.join(__dirname, '../../../app/static/js/busy.js'))]
  const busy = require(path.join(__dirname, '../../../app/static/js/busy.js'))
  const overlay = () => document.getElementById('busy-overlay')
  const text = () => (overlay() ? overlay().textContent ?? overlay().textEl.textContent : null)
  return { busy, overlay, text, state, body }
}

const tests = []
const test = (name, fn) => tests.push([name, fn])

test('兩個擁有者並行：A 先釋放，B 還在時遮罩仍在，B 釋放後才移除', () => {
  const { busy, overlay } = freshBusy()
  const releaseA = busy.acquire('A')
  const releaseB = busy.acquire('B')
  assert.ok(overlay())
  releaseA()
  assert.ok(overlay(), 'A 結束但 B 仍在進行，遮罩不應消失')
  assert.equal(busy.count(), 1)
  releaseB()
  assert.equal(overlay(), null)
  assert.equal(busy.count(), 0)
})

test('兩個擁有者並行：B 先釋放，A 還在時遮罩仍在', () => {
  const { busy, overlay } = freshBusy()
  const releaseA = busy.acquire('A')
  const releaseB = busy.acquire('B')
  releaseB()
  assert.ok(overlay(), 'B 結束但 A 仍在進行，遮罩不應消失')
  releaseA()
  assert.equal(overlay(), null)
})

test('一個成功、一個失敗：不管哪個先結束，都只釋放自己', () => {
  const { busy, overlay } = freshBusy()
  const ok = busy.acquire('成功的請求')
  const failed = busy.acquire('失敗的請求')
  failed() // 失敗路徑（例如 sendError）釋放自己
  assert.ok(overlay())
  ok()
  assert.equal(overlay(), null)
})

test('同一個 Token 重複釋放不影響其他擁有者', () => {
  const { busy, overlay } = freshBusy()
  const releaseA = busy.acquire('A')
  const releaseB = busy.acquire('B')
  releaseA()
  releaseA()
  releaseA()
  assert.ok(overlay(), '重複釋放 A 不可關掉 B 的遮罩')
  assert.equal(busy.count(), 1)
  releaseB()
  assert.equal(overlay(), null)
})

test('空訊息（data-busy="" 或查詢型 GET）不取得擁有者，也不影響別人的遮罩', () => {
  const { busy, overlay } = freshBusy()
  const none = busy.acquire('')
  assert.equal(overlay(), null)
  assert.equal(busy.count(), 0)
  const releaseA = busy.acquire('A')
  none()
  none()
  assert.ok(overlay(), '沒有遮罩的請求結束，不可關掉別人的遮罩')
  releaseA()
  assert.equal(overlay(), null)
})

test('clearAll 之後，舊的 release 不影響新的 Token', () => {
  const { busy, overlay } = freshBusy()
  const stale = busy.acquire('舊頁面的請求')
  busy.clearAll()
  assert.equal(overlay(), null)
  const fresh = busy.acquire('新請求')
  stale() // 舊請求遲到的結束事件
  assert.ok(overlay(), '舊 release 不可關掉新請求的遮罩')
  assert.equal(busy.count(), 1)
  fresh()
  assert.equal(overlay(), null)
})

test('多個擁有者時顯示最新的訊息，最新的釋放後退回前一個訊息', () => {
  const { busy, text } = freshBusy()
  const releaseA = busy.acquire('第一個')
  const releaseB = busy.acquire('第二個')
  assert.equal(text(), '第二個')
  releaseB()
  assert.equal(text(), '第一個')
  releaseA()
})

test('只建立一個遮罩元素，不重複堆疊', () => {
  const { busy, body } = freshBusy()
  const a = busy.acquire('A')
  const b = busy.acquire('B')
  assert.equal(body.children.filter((c) => c.id === 'busy-overlay').length, 1)
  a()
  b()
  assert.equal(body.children.length, 0)
})

test('開啟中的 dialog 會承接遮罩（遮罩要在最上層）', () => {
  const { busy, state, body } = freshBusy()
  const dialog = new El('dialog')
  body.append(dialog)
  state.dialog = dialog
  const release = busy.acquire('上傳中')
  assert.equal(dialog.children.length, 1)
  assert.equal(dialog.children[0].id, 'busy-overlay')
  release()
  assert.equal(dialog.children.length, 0)
})

test('只監看 <dialog> 的 open 屬性（showModal、close、Escape 都會改它）', () => {
  const { state } = freshBusy()
  assert.ok(state.observer, '載入 busy.js 時應註冊 MutationObserver')
  assert.deepEqual(state.observer.options, { attributes: true, attributeFilter: ['open'], subtree: true })
})

test('對話框關閉（Escape、按鈕、close()）時，遮罩移回 body 繼續保護頁面，Token 不變', () => {
  const { busy, state, body, overlay } = freshBusy()
  const dialog = new El('dialog')
  body.append(dialog)
  state.dialog = dialog
  const release = busy.acquire('上傳中')
  assert.equal(overlay().parentNode, dialog)
  state.dialog = null // 對話框被關閉：open 屬性移除
  state.observer.fire([{ target: dialog }])
  assert.ok(overlay(), '請求仍在進行，遮罩不可消失')
  assert.equal(overlay().parentNode, body, '遮罩應移回 body')
  assert.equal(busy.count(), 1)
  release()
  assert.equal(overlay(), null)
})

test('先取得 Token、之後才開啟對話框：遮罩移進對話框', () => {
  const { busy, state, body, overlay } = freshBusy()
  const dialog = new El('dialog')
  body.append(dialog)
  const release = busy.acquire('先有遮罩')
  assert.equal(overlay().parentNode, body)
  state.dialog = dialog // showModal() 之後
  state.observer.fire([{ target: dialog }])
  assert.equal(overlay().parentNode, dialog)
  assert.equal(busy.count(), 1)
  release()
  assert.equal(overlay(), null)
  assert.equal(dialog.children.length, 0)
})

test('沒有擁有者或事件不是來自對話框時，不建立也不搬動遮罩', () => {
  const { busy, state, body, overlay } = freshBusy()
  const dialog = new El('dialog')
  body.append(dialog)
  state.observer.fire([{ target: dialog }])
  assert.equal(overlay(), null, '沒有擁有者時不應建立遮罩')
  const release = busy.acquire('A')
  state.dialog = dialog
  state.observer.fire([{ target: new El('details') }]) // 不是 <dialog>
  assert.equal(overlay().parentNode, body, '非對話框的 open 屬性變化不應搬動遮罩')
  release()
})

test('對話框關閉後釋放最後一個 Token：遮罩確實移除，不留在已關閉的對話框裡', () => {
  const { busy, state, body, overlay } = freshBusy()
  const dialog = new El('dialog')
  body.append(dialog)
  state.dialog = dialog
  const a = busy.acquire('A')
  const b = busy.acquire('B')
  state.dialog = null
  state.observer.fire([{ target: dialog }])
  a()
  assert.ok(overlay(), '還有 B，遮罩不應消失')
  b()
  assert.equal(overlay(), null)
  assert.equal(dialog.children.length, 0)
  assert.equal(body.children.filter((c) => c.id === 'busy-overlay').length, 0)
})

test('window.invoiceGuardBusy 與 InvoiceGuardBusy 指向同一組函式（供其他腳本使用）', () => {
  freshBusy()
  const root = globalThis
  assert.equal(typeof root.invoiceGuardBusy, 'function')
  assert.equal(root.InvoiceGuardBusy.acquire, root.invoiceGuardBusy)
  assert.ok(Object.isFrozen(root.InvoiceGuardBusy))
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
