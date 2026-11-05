/** ADMIN 核對表單金額連動（app/static/js/intake-amounts.js）：純函式與控制器，假欄位，不連網路、不用瀏覽器。 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const A = require('../../app/static/js/intake-amounts.js')
const vectors = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../fixtures/amount_to_chinese_vectors.json'), 'utf8'),
).vectors

const ANOMALY_1 = '億仟佰拾壹萬肆仟捌佰伍拾捌元'
const ANOMALY_2 = '億仟佰 拾壹萬捌仟貳佰伍拾零元'

/** 假欄位＋提醒記錄；write 不觸發 onEdit（和瀏覽器一樣，程式寫入不產生 input 事件）。 */
function page(initial = {}) {
  const values = { sales_amount: '', tax_amount: '', total_amount: '', total_amount_zh: '', ...initial }
  const notes = {}
  const writes = []
  const controller = A.createController({
    read: name => values[name],
    write: (name, value) => {
      writes.push([name, value])
      values[name] = value
    },
    note: (name, text) => {
      notes[name] = text
    },
  })
  const edit = (name, value) => {
    values[name] = value
    controller.onEdit(name)
  }
  return { values, notes, writes, controller, edit }
}

test('轉換結果與手機前台 amountToChinese 的共用向量逐項相同', () => {
  assert.ok(vectors.length >= 40)
  for (const { input, output } of vectors) {
    assert.equal(A.amountToChinese(input), output, JSON.stringify(input))
  }
})

test('14858 → 壹萬肆仟捌佰伍拾捌元整；非整數與非法輸入回傳 null', () => {
  assert.equal(A.amountToChinese('14858'), '壹萬肆仟捌佰伍拾捌元整')
  for (const bad of ['', 'abc', '-5', '14858.5', null, undefined]) assert.equal(A.amountToChinese(bad), null)
})

test('金額以整數分運算：10000＋500＝10500，小數與千分位精確，無浮點誤差', () => {
  assert.equal(A.checkTotal('10000', '500', '10500').ok, true)
  assert.equal(A.checkTotal('10000', '500', '10500').expected, '10,500')
  assert.equal(A.checkTotal('0.1', '0.2', '0.3').ok, true) // 浮點數會得到 0.30000000000000004
  assert.equal(A.checkTotal('1,000.50', '50.05', '1050.55').ok, true)
  assert.equal(A.checkTotal('０', '０', '０').ok, true) // 全形數字與 0
  const bad = A.checkTotal('10000', '500', '10000')
  assert.equal(bad.ok, false)
  assert.match(bad.message, /銷售額 10,000 ＋ 營業稅額 500 ＝ 10,500；輸入總計 10,000，差額 500 元。/)
})

test('空值、非法輸入、超過 2 位小數：不下結論（回傳 null），不當成通過也不報錯', () => {
  for (const args of [['', '500', '10500'], ['10000', '', '10500'], ['10000', '500', ''], ['abc', '500', '10500'],
    ['-1', '1', '0'], ['1.234', '0', '1.234'], ['10000', '500', null]]) {
    assert.equal(A.checkTotal(...args), null, JSON.stringify(args))
  }
})

test('不假設稅率：稅額不是 5% 也不會被改寫，只要三者相加一致就沒有提醒', () => {
  const p = page({ sales_amount: '10000', tax_amount: '0', total_amount: '10000' })
  p.controller.init()
  assert.equal(p.notes.total_amount, null)
  p.edit('tax_amount', '123')
  assert.equal(p.values.tax_amount, '123')
  assert.match(p.notes.total_amount, /不一致/)
  assert.deepEqual(p.writes, []) // 沒有寫入任何欄位
})

test('修改銷售額、稅額、總計後提醒隨之更新並可消失', () => {
  const p = page({ sales_amount: '10000', tax_amount: '500', total_amount: '10000' })
  p.controller.init()
  assert.match(p.notes.total_amount, /差額 500 元/)
  p.edit('total_amount', '10500')
  assert.equal(p.notes.total_amount, null)
  p.edit('sales_amount', '9000')
  assert.match(p.notes.total_amount, /銷售額 9,000 ＋ 營業稅額 500 ＝ 9,500；輸入總計 10,500，差額 1,000 元。/)
  p.edit('tax_amount', '1500')
  assert.equal(p.notes.total_amount, null)
  p.edit('tax_amount', '')
  assert.equal(p.notes.total_amount, null) // 稅額空白：無法判斷，不顯示舊提醒
})

test('頁面載入只顯示提醒，不覆寫 OCR 的總計、稅額與國字', () => {
  const p = page({ sales_amount: '10000', tax_amount: '500', total_amount: '9999', total_amount_zh: ANOMALY_1 })
  p.controller.init()
  assert.deepEqual(p.writes, [])
  assert.equal(p.values.total_amount_zh, ANOMALY_1)
  assert.match(p.notes.total_amount, /不一致/)
})

test('修改總計 14858 後國字自動更新；改成其他金額再次更新', () => {
  const p = page({ total_amount: '6300', total_amount_zh: '陸仟參佰元整' })
  p.edit('total_amount', '14858')
  assert.equal(p.values.total_amount_zh, '壹萬肆仟捌佰伍拾捌元整')
  p.edit('total_amount', '10500')
  assert.equal(p.values.total_amount_zh, '壹萬零伍佰元整')
  assert.equal(p.writes.length, 2)
})

test('輸入到一半、空白或有小數的總計不會洗掉國字', () => {
  const p = page({ total_amount: '14858', total_amount_zh: '壹萬肆仟捌佰伍拾捌元整' })
  for (const v of ['', '14858.5', 'abc', '-3']) p.edit('total_amount', v)
  assert.equal(p.values.total_amount_zh, '壹萬肆仟捌佰伍拾捌元整')
  assert.deepEqual(p.writes, [])
})

test('OCR 的容錯國字（預印單位殘留但可解析）不被改總計洗掉；不顯示任何本機國字異常提示', () => {
  const p = page({ total_amount: '14858', total_amount_zh: ANOMALY_1 })
  p.controller.init()
  assert.equal(p.notes.total_amount_zh, undefined) // 本檔不做國字格式預檢，判斷全交給 API
  p.edit('total_amount', '14859')
  assert.equal(p.values.total_amount_zh, ANOMALY_1) // 既有內容保留；是否與總計一致由 API 警示提醒
  assert.deepEqual(p.writes, [])
})

test('使用者手動改過國字後不再自動覆寫；清空後恢復自動', () => {
  const p = page({ total_amount: '14858', total_amount_zh: '壹萬肆仟捌佰伍拾捌元整' })
  p.edit('total_amount_zh', '壹萬肆仟捌佰伍拾捌圓整') // 手動
  p.edit('total_amount', '20000')
  assert.equal(p.values.total_amount_zh, '壹萬肆仟捌佰伍拾捌圓整')
  assert.deepEqual(p.writes, [])
  p.edit('total_amount_zh', '') // 清空
  p.edit('total_amount', '30000')
  assert.equal(p.values.total_amount_zh, '參萬元整')
})

test('程式寫入國字不會再觸發連動（沒有事件循環）：一次修改只寫一次', () => {
  const p = page({ total_amount: '1' })
  p.edit('total_amount', '2')
  assert.equal(p.writes.length, 1)
})

// ---- 重新載入還原「手動國字」（缺陷 3）----
test('重新載入後：人工填的國字（格式正常、與總計換算不同）不被改總計覆寫', () => {
  const p = page({ total_amount: '14858', total_amount_zh: '壹萬肆仟捌佰伍拾捌圓整' })
  p.controller.init()
  p.edit('total_amount', '20000')
  assert.equal(p.values.total_amount_zh, '壹萬肆仟捌佰伍拾捌圓整')
  assert.deepEqual(p.writes, [])
})

test('重新載入後：與總計換算相同（含伺服器帶入的建議值）或空白的國字仍會隨總計更新', () => {
  for (const zh of ['壹萬肆仟捌佰伍拾捌元整', '']) {
    const p = page({ total_amount: '14858', total_amount_zh: zh })
    p.controller.init()
    p.edit('total_amount', '20000')
    assert.equal(p.values.total_amount_zh, '貳萬元整', JSON.stringify(zh))
  }
})

// ---- DOM 綁定（假 document）：舊警示隱藏、輸入法組字結束補跑（缺陷 1、2）----
function el(value = '') {
  const listeners = {}
  return {
    value,
    listeners,
    addEventListener: (type, fn) => (listeners[type] = listeners[type] || []).push(fn),
    fire: (type, event = {}) => (listeners[type] || []).forEach(fn => fn(event)),
  }
}

/** 最小的元素替身：有屬性、class、hidden、remove，足以跑 intake-amounts.js 與 invoice-flow-review.js 用到的選擇器。 */
function node(className = '', attrs = {}) {
  const n = {
    className,
    attrs: { ...attrs },
    hidden: false,
    textContent: '',
    parent: null,
    setAttribute(key, value) {
      n.attrs[key] = String(value)
    },
    getAttribute: key => (key in n.attrs ? n.attrs[key] : null),
    hasAttribute: key => key in n.attrs,
    remove() {
      if (n.parent) n.parent.kids.splice(n.parent.kids.indexOf(n), 1)
      n.parent = null
    },
  }
  return n
}

/** 支援 .class、[attr]、[attr="v"] 與 :not(...) 的簡單選擇器（只服務這個檔案用到的寫法）。 */
function matches(n, selector) {
  const tokens = selector.match(/:not\((?:[^()]|\([^()]*\))*\)|\.[\w-]+|\[[^\]]+\]/g) || []
  return tokens.every(token => {
    if (token.startsWith(':not(')) return !matches(n, token.slice(5, -1))
    if (token[0] === '.') return n.className.split(/\s+/).includes(token.slice(1))
    const m = /^\[([\w-]+)(?:="?([^"\]]*)"?)?\]$/.exec(token)
    return m[2] === undefined ? n.hasAttribute(m[1]) : n.getAttribute(m[1]) === m[2]
  })
}

function container(serverNotes = [], suggestion = null) {
  const box = {
    kids: [],
    querySelector: sel => box.kids.find(k => matches(k, sel)) || null,
    querySelectorAll: sel => box.kids.filter(k => matches(k, sel)),
    appendChild(k) {
      k.parent = box
      box.kids.push(k)
    },
  }
  serverNotes.forEach(text => {
    const n = node('field-warning')
    n.textContent = text
    box.appendChild(n)
  })
  if (suggestion) box.appendChild(suggestion)
  return box
}

/** 模擬新版 invoice-flow-review.js 的伺服器即時檢核：render 加 data-live 警示、clearLive 移除所有 [data-live]。 */
const serverLive = {
  render(box, text) {
    const n = node('field-warning', { 'data-live': '', 'data-check-kind': 'warning' })
    n.textContent = text
    box.appendChild(n)
    return n
  },
  clearLive(boxes) {
    Object.values(boxes).forEach(box => box.kids.filter(k => k.hasAttribute('data-live')).forEach(k => k.remove()))
  },
}

function dom(values, serverNotes = {}, suggested = null) {
  const fields = {
    sales_amount: el(values.sales_amount ?? ''),
    tax_amount: el(values.tax_amount ?? ''),
    total_amount: el(values.total_amount ?? ''),
    total_amount_zh: el(values.total_amount_zh ?? ''),
  }
  const suggestion =
    suggested === null ? null : node('field-warning field-suggestion', { 'data-suggested-value': suggested })
  const boxes = {}
  for (const key of Object.keys(fields)) boxes[key] = container(serverNotes[key] || [], key === 'total_amount_zh' ? suggestion : null)
  const doc = {
    getElementById: id => fields[id.replace(/^f-/, '')] || null,
    querySelector: sel => boxes[/"(.*)"/.exec(sel)[1]] || null,
    createElement: () => node(),
  }
  const observers = []
  const win = {
    MutationObserver: function (callback) {
      this.observe = () => observers.push(callback)
    },
  }
  const controller = A.bind(doc, win)
  const mutated = () => observers.forEach(callback => callback([]))
  const localNote = () => boxes.total_amount.kids.find(k => k.hasAttribute('data-amounts-note')) || null
  return { fields, boxes, controller, note: suggestion, mutated, localNote }
}

test('改銷售額或稅額時，總計與國字旁過期的 API 警示一併隱藏；新的即時提醒不被隱藏', () => {
  const d = dom(
    { sales_amount: '10000', tax_amount: '500', total_amount: '10500' },
    { total_amount: ['總計不符（舊）'], total_amount_zh: ['國字不符（舊）'] },
  )
  d.fields.sales_amount.value = '9000'
  d.fields.sales_amount.fire('input', {})
  assert.equal(d.boxes.total_amount.kids[0].hidden, true)
  assert.equal(d.boxes.total_amount_zh.kids[0].hidden, true)
  const live = d.localNote()
  assert.ok(live && live.hidden !== true)
  assert.match(live.textContent, /不一致/)
})

test('輸入法：組字中不處理，compositionend 補跑一次，最後的輸入不會漏掉', () => {
  const d = dom({ total_amount: '6300', total_amount_zh: '陸仟參佰元整' })
  d.fields.total_amount.value = '14858'
  d.fields.total_amount.fire('input', { isComposing: true })
  assert.equal(d.fields.total_amount_zh.value, '陸仟參佰元整') // 組字中不動
  d.fields.total_amount.fire('compositionend', {})
  assert.equal(d.fields.total_amount_zh.value, '壹萬肆仟捌佰伍拾捌元整')
})

test('Safari 順序（compositionend 後再來一個 input）重複處理也不會多寫或改變結果', () => {
  const d = dom({ total_amount: '6300', total_amount_zh: '陸仟參佰元整' })
  d.fields.total_amount.value = '14858'
  d.fields.total_amount.fire('compositionend', {})
  d.fields.total_amount.fire('input', { isComposing: false })
  assert.equal(d.fields.total_amount_zh.value, '壹萬肆仟捌佰伍拾捌元整')
})

test('載入時 bind 只顯示提醒，不改任何欄位；缺少金額欄位的頁面不綁定', () => {
  const d = dom({ sales_amount: '10000', tax_amount: '500', total_amount: '9999', total_amount_zh: ANOMALY_1 })
  assert.equal(d.fields.total_amount.value, '9999')
  assert.equal(d.fields.total_amount_zh.value, ANOMALY_1)
  assert.equal(A.bind({ getElementById: () => null }), null)
})

// ---- 殘餘缺陷：總計空白／非法時重新載入，格式正常的人工國字不得被之後的合法總計覆寫 ----
test('總計空白、國字已有合法內容：重新載入後補上總計，原國字不被覆寫', () => {
  const p = page({ total_amount: '', total_amount_zh: '壹萬肆仟捌佰伍拾捌元整' })
  p.controller.init()
  assert.deepEqual(p.writes, []) // 載入不改任何欄位
  p.edit('total_amount', '20000')
  assert.equal(p.values.total_amount_zh, '壹萬肆仟捌佰伍拾捌元整')
  assert.deepEqual(p.writes, [])
})

test('總計非法、國字已有合法內容：之後修正總計，原國字不被覆寫', () => {
  for (const bad of ['abc', '14858.5', '-3']) {
    const p = page({ total_amount: bad, total_amount_zh: '陸仟參佰元整' })
    p.controller.init()
    p.edit('total_amount', '9000')
    assert.equal(p.values.total_amount_zh, '陸仟參佰元整', bad)
    assert.deepEqual(p.writes, [], bad)
  }
})

test('國字空白、總計空白：之後輸入合法整數總計仍自動產生國字', () => {
  const p = page({ total_amount: '', total_amount_zh: '' })
  p.controller.init()
  p.edit('total_amount', '14858')
  assert.equal(p.values.total_amount_zh, '壹萬肆仟捌佰伍拾捌元整')
})

test('總計空白、國字是容錯字串：重新載入不改值，補上總計後仍保留', () => {
  const p = page({ total_amount: '', total_amount_zh: ANOMALY_1 })
  p.controller.init()
  assert.deepEqual(p.writes, [])
  p.edit('total_amount', '14858')
  assert.equal(p.values.total_amount_zh, ANOMALY_1)
  assert.deepEqual(p.writes, [])
})

test('總計空白時重新載入後，使用者清空國字即恢復自動產生', () => {
  const p = page({ total_amount: '', total_amount_zh: '陸仟參佰元整' })
  p.controller.init()
  p.edit('total_amount_zh', '') // 清空
  p.edit('total_amount', '30000')
  assert.equal(p.values.total_amount_zh, '參萬元整')
})

test('總計空白時重新載入後，銷售額＋稅額一致性提醒照常運作', () => {
  const p = page({ sales_amount: '10000', tax_amount: '500', total_amount: '', total_amount_zh: '陸仟參佰元整' })
  p.controller.init()
  assert.equal(p.notes.total_amount, null) // 總計空白：無法比較，不報錯
  p.edit('total_amount', '10000')
  assert.match(p.notes.total_amount, /差額 500 元/)
  assert.equal(p.values.total_amount_zh, '陸仟參佰元整') // 國字仍保留；是否一致交給 API 警示
})

// ---- 伺服器帶入的建議提醒：只在國字仍是建議值時顯示 ----
test('建議提醒：載入時顯示；使用者改國字後隱藏；改回建議值再顯示；總計帶動重算後隱藏', () => {
  const values = { total_amount: '14858', total_amount_zh: '壹萬肆仟捌佰伍拾捌元整' }
  const d = dom(values, {}, '壹萬肆仟捌佰伍拾捌元整')
  assert.equal(d.note.hidden, false)
  d.fields.total_amount_zh.value = '自行填寫'
  d.fields.total_amount_zh.fire('input', {})
  assert.equal(d.note.hidden, true)
  d.fields.total_amount_zh.value = '壹萬肆仟捌佰伍拾捌元整'
  d.fields.total_amount_zh.fire('input', {})
  assert.equal(d.note.hidden, false)
  d.fields.total_amount_zh.value = ''
  d.fields.total_amount_zh.fire('input', {}) // 清空 → 恢復自動
  d.fields.total_amount.value = '20000'
  d.fields.total_amount.fire('input', {})
  assert.equal(d.fields.total_amount_zh.value, '貳萬元整')
  assert.equal(d.note.hidden, true) // 已不是當初的建議值
})

test('建議提醒：過期 API 警示隱藏時，建議提醒不被當成 API 警示一起隱藏', () => {
  const d = dom({ total_amount: '14858', total_amount_zh: 'x' }, { total_amount_zh: ['國字不符（舊）'] })
  d.fields.sales_amount.value = '1'
  d.fields.sales_amount.fire('input', {})
  assert.equal(d.boxes.total_amount_zh.kids[0].hidden, true)
})

// ---- 與新版 invoice-flow-review.js 的伺服器即時檢核共存（PR #22）----
const totalMismatchText = '銷售額＋稅額與總計不符，請確認'

test('本機金額提醒使用獨立標記 data-amounts-note，不帶 data-live（新版 clearLive 會移除所有 [data-live]）', () => {
  const d = dom({ sales_amount: '10000', tax_amount: '500', total_amount: '10000' })
  const note = d.localNote()
  assert.ok(note)
  assert.equal(note.hasAttribute('data-live'), false)
  assert.equal(note.getAttribute('data-amounts-note'), '')
  assert.match(note.textContent, /差額 500 元/)
})

test('伺服器 clearLive 後本機提醒仍在；伺服器警示被清掉，兩套互不清除', () => {
  const d = dom({ sales_amount: '10000', tax_amount: '500', total_amount: '10000' })
  serverLive.render(d.boxes.total_amount, totalMismatchText)
  d.mutated()
  serverLive.clearLive(d.boxes)
  assert.ok(d.localNote(), '本機提醒不得被伺服器的 clearLive 移除')
  assert.equal(d.boxes.total_amount.kids.filter(k => k.hasAttribute('data-live')).length, 0)
})

test('伺服器已回報同一類警示時隱藏本機提醒（不重複）；伺服器警示被清掉後本機提醒恢復', () => {
  const d = dom({ sales_amount: '10000', tax_amount: '500', total_amount: '10000' })
  assert.equal(d.localNote().hidden, false) // 伺服器結果還沒回來：先顯示本機提醒
  serverLive.render(d.boxes.total_amount, totalMismatchText)
  d.mutated()
  assert.equal(d.localNote().hidden, true) // 伺服器警示到了：只留一則
  serverLive.clearLive(d.boxes)
  d.mutated()
  assert.equal(d.localNote().hidden, false) // 下一輪檢核開始：本機提醒補位
})

test('伺服器的必填缺漏或錯誤訊息不會讓本機金額提醒消失（只有同欄位的警示才算重複）', () => {
  const d = dom({ sales_amount: '10000', tax_amount: '500', total_amount: '10000' })
  const missing = node('field-missing', { 'data-live': '', 'data-check-kind': 'required' })
  d.boxes.total_amount.appendChild(missing)
  d.mutated()
  assert.equal(d.localNote().hidden, false)
})

test('修改欄位只隱藏過期的非即時警示；伺服器即時警示、本機提醒與建議提醒都不被誤藏', () => {
  const d = dom(
    { sales_amount: '10000', tax_amount: '500', total_amount: '10000', total_amount_zh: 'x' },
    { total_amount: ['整頁檢核留下的舊警示'] },
    'x',
  )
  const stale = d.boxes.total_amount.kids.find(k => k.textContent === '整頁檢核留下的舊警示')
  const live = serverLive.render(d.boxes.total_amount, totalMismatchText)
  d.fields.sales_amount.value = '9000'
  d.fields.sales_amount.fire('input', {})
  assert.equal(stale.hidden, true) // 過期的整頁檢核警示隱藏
  assert.equal(live.hidden, false) // 剛到的伺服器即時警示保留
  assert.equal(d.localNote().hasAttribute('data-amounts-note'), true)
  assert.equal(d.note.hidden, false) // 建議提醒（國字仍是建議值）保留
})

test('連續輸入：本機提醒永遠只有一則，內容跟著最新的值；伺服器清除後不累積', () => {
  const d = dom({ sales_amount: '10000', tax_amount: '500', total_amount: '9000' })
  for (const v of ['9500', '9600', '10000', '10500', '10400']) {
    d.fields.total_amount.value = v
    d.fields.total_amount.fire('input', {})
    serverLive.clearLive(d.boxes)
    d.mutated()
    assert.equal(d.boxes.total_amount.kids.filter(k => k.hasAttribute('data-amounts-note')).length <= 1, true)
  }
  assert.match(d.localNote().textContent, /輸入總計 10,400，差額 100 元/)
  d.fields.total_amount.value = '10500'
  d.fields.total_amount.fire('input', {})
  assert.equal(d.localNote(), null) // 一致後消失
})

test('檢核失敗（只呼叫 clearLive、沒有新結果）：本機提醒與建議提醒狀態不變，不把空白當成通過', () => {
  const d = dom({ sales_amount: '10000', tax_amount: '500', total_amount: '10000', total_amount_zh: '壹萬元整' }, {}, '壹萬元整')
  serverLive.clearLive(d.boxes)
  d.mutated()
  assert.ok(d.localNote() && d.localNote().hidden === false)
  assert.equal(d.note.hidden, false)
})

test('沒有 MutationObserver 的環境也能運作（只是不做去重）', () => {
  const doc = { getElementById: () => null }
  assert.equal(A.bind(doc, {}), null)
})

// ---- 直接執行新版 invoice-flow-review.js 的「實際原始碼」clearLive／add，確認不會移除本機金額提醒 ----
function realFlowReviewHelpers(boxes) {
  const source = fs.readFileSync(path.resolve(__dirname, '../../app/static/js/invoice-flow-review.js'), 'utf8')
  const pick = name => {
    const m = new RegExp(`function ${name}\\([^)]*\\) \\{[\\s\\S]*?\\n  \\}`).exec(source)
    assert.ok(m, `找不到 invoice-flow-review.js 的 ${name}()（檔案結構變動時請同步這個測試）`)
    return m[0]
  }
  const form = {
    querySelectorAll: sel => Object.values(boxes).flatMap(box => box.kids.filter(k => matches(k, sel))),
  }
  const factory = new Function('form', 'document', `${pick('clearLive')}\n${pick('add')}\nreturn { clearLive, add }`)
  return factory(form, { createElement: () => node() })
}

test('新版實際的 clearLive()：移除它自己加的 [data-live] 警示，不動本機金額提醒與建議提醒', () => {
  const d = dom({ sales_amount: '10000', tax_amount: '500', total_amount: '10000', total_amount_zh: 'x' }, {}, 'x')
  const real = realFlowReviewHelpers(d.boxes)
  real.add(d.boxes.total_amount, 'field-warning', totalMismatchText, 'warning') // 新版渲染伺服器警示
  assert.equal(d.boxes.total_amount.kids.filter(k => k.hasAttribute('data-live')).length, 1)
  real.clearLive() // 下一輪即時檢核開始
  assert.equal(d.boxes.total_amount.kids.filter(k => k.hasAttribute('data-live')).length, 0)
  assert.ok(d.localNote(), '本機金額提醒必須留下')
  assert.equal(d.note.hidden, false, '國字建議提醒必須留下')
})

test('新版實際的 add() 產生的警示會被視為「伺服器已回報」，本機提醒隱藏；clearLive 後恢復', () => {
  const d = dom({ sales_amount: '10000', tax_amount: '500', total_amount: '10000' })
  const real = realFlowReviewHelpers(d.boxes)
  real.add(d.boxes.total_amount, 'field-warning', totalMismatchText, 'warning')
  d.mutated()
  assert.equal(d.localNote().hidden, true)
  real.clearLive()
  d.mutated()
  assert.equal(d.localNote().hidden, false)
})
