/** ADMIN 補正頁（app/static/js/invoice-correct.js）：冪等鍵、唯讀欄位不產生事件、金額保持字串。離線執行，不需要瀏覽器。 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const SOURCE = fs.readFileSync(path.resolve(__dirname, '../../app/static/js/invoice-correct.js'), 'utf8')
const HEADER = ['invoice_track', 'invoice_number', 'invoice_date', 'seller_tax_id', 'seller_name', 'buyer_tax_id',
  'buyer_name', 'tax_type', 'sales_amount', 'tax_amount', 'total_amount', 'total_amount_zh']
const VALUES = { invoice_track: 'RT', invoice_number: '39560800', invoice_date: '2026-10-20', seller_tax_id: '43149447',
  tax_type: 'taxable', sales_amount: '1000', tax_amount: '50', total_amount: '1050' }
const ITEM_ID = '0a0a0a0a-1111-4222-8333-444455556666'
const RETURN_ID = '9c9c9c9c-1111-4222-8333-444455556666'

function setup(fetchImpl) {
  const handlers = {}
  const input = (scope, field, value, readonly) => ({
    dataset: { scope, field, lineNo: scope === 'item' ? '1' : undefined, itemId: scope === 'item' ? ITEM_ID : undefined },
    value, readOnly: readonly, disabled: false,
  })
  const headerInputs = HEADER.map((name) => input('header', name, VALUES[name] ?? '', name !== 'total_amount'))
  const itemInputs = ['item_name', 'quantity', 'unit_price', 'line_amount']
    .map((name) => input('item', name, { item_name: '文具', quantity: '2', unit_price: '500', line_amount: '1000' }[name], name !== 'line_amount'))
  const all = [...headerInputs, ...itemInputs]
  const row = { dataset: { itemId: ITEM_ID }, querySelector: (sel) => itemInputs.find((i) => sel.includes(`"${i.dataset.field}"`)) }
  const radio = { value: 'corrected' }
  const fieldset = { dataset: { returnItemId: RETURN_ID }, querySelector: (sel) => (sel.includes('radio') ? radio : { value: '' }) }
  const elements = {
    'correct-page': { dataset: { invoiceId: 'inv-1', expectedRound: '2' } },
    'correct-form': {
      querySelectorAll: (sel) => (sel === '[data-field]' ? all : sel.startsWith('tr') ? [row] : [fieldset]),
      querySelector: (sel) => headerInputs.find((i) => sel.includes(`data-field="${i.dataset.field}"`)),
      addEventListener: (name, fn) => { handlers[name] = fn },
      dataset: {},
    },
    'correct-message': { dataset: {} },
    'correct-retry': { hidden: true, addEventListener: (name, fn) => { handlers.retry = fn } },
    'correct-submit': { disabled: false },
    'correct-done': { hidden: true },
  }
  let counter = 0
  const calls = []
  const sandbox = {
    document: { getElementById: (id) => elements[id] ?? null },
    window: { confirm: () => true },
    crypto: { randomUUID: () => `key-${++counter}` },
    fetch: async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return fetchImpl(calls.length) },
    console,
  }
  vm.runInNewContext(SOURCE, sandbox)
  const tick = () => new Promise((resolve) => setImmediate(resolve))
  return { handlers, headerInputs, itemInputs, elements, calls, tick }
}

const ok = () => ({ ok: true, status: 200, json: async () => ({ submission_round: 3, status: 'x', status_label: '待會計覆核' }) })

test('只讀欄位的 change 不產生事件；金額送出時保持字串', async () => {
  const page = setup(() => ok())
  const readonlyField = page.headerInputs.find((i) => i.dataset.field === 'seller_tax_id')
  readonlyField.value = '99999999'
  page.handlers.change({ target: readonlyField })
  const total = page.headerInputs.find((i) => i.dataset.field === 'total_amount')
  total.value = '1,050.50'
  page.handlers.change({ target: total })
  page.handlers.submit({ preventDefault() {} })
  await page.tick()

  const body = page.calls[0].body
  assert.equal(page.calls[0].url, '/invoices/inv-1/correct')
  assert.equal(body.changes.length, 1)
  assert.deepEqual(body.changes[0], { field_scope: 'header', line_no: null, field_name: 'total_amount', old_value: '1050', new_value: '1050.5' })
  assert.equal(typeof body.total_amount, 'string')
  assert.equal(body.total_amount, '1050.5')
  assert.equal(body.expected_round, 2)
  assert.equal(body.items[0].item_id, ITEM_ID)
  assert.equal(body.responses[0].return_item_id, RETURN_ID)
})

test('逾時重試使用同一把冪等鍵；內容改變才換新的', async () => {
  const page = setup((n) => { if (n === 1) throw new Error('timeout'); return ok() })
  page.handlers.submit({ preventDefault() {} })
  await page.tick()
  assert.equal(page.elements['correct-retry'].hidden, false) // 結果不明：提供以相同識別碼重試

  page.handlers.retry()
  await page.tick()
  assert.equal(page.calls.length, 2)
  assert.equal(page.calls[1].body.idempotency_key, page.calls[0].body.idempotency_key)
  assert.equal(page.calls[1].body.idempotency_key, 'key-1')

  const total = page.headerInputs.find((i) => i.dataset.field === 'total_amount')
  total.value = '1060'
  page.handlers.change({ target: total })
  page.elements['correct-submit'].disabled = false
  page.handlers.submit({ preventDefault() {} })
  await page.tick()
  assert.notEqual(page.calls[2].body.idempotency_key, 'key-1')
})

test('5xx 或網路錯誤不會自動換鍵重送，只顯示重試按鈕', async () => {
  const page = setup(() => ({ ok: false, status: 502, json: async () => ({ error: { code: 'UPSTREAM_UNAVAILABLE', message: 'x' }, ambiguous: true }) }))
  page.handlers.submit({ preventDefault() {} })
  await page.tick()
  assert.equal(page.calls.length, 1)
  assert.equal(page.elements['correct-retry'].hidden, false)
})
