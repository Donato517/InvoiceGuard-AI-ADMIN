/** /cases 原圖失敗提示（app/static/js/case-image.js）：假 DOM 與假 fetch，不連網路、不用瀏覽器。 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const path = require('node:path')

const script = path.resolve(__dirname, '../../app/static/js/case-image.js')

function load({ fetchImpl, complete = false, naturalWidth = 0 }) {
  const box = { textContent: '', hidden: true }
  const listeners = {}
  const image = {
    hidden: false,
    complete,
    naturalWidth,
    getAttribute: name => (name === 'src' ? '/cases/abc/image' : null),
    addEventListener: (type, fn) => { listeners[type] = fn },
    parentNode: { querySelector: selector => (selector === '[data-case-image-error]' ? box : null) },
  }
  const calls = []
  global.document = { querySelectorAll: () => [image] }
  global.fetch = (...args) => { calls.push(args); return fetchImpl(...args) }
  delete require.cache[script]
  require(script).init()
  return { image, box, listeners, calls }
}

const reply = (status, body) => () => Promise.resolve({ ok: status < 400, status, json: () => Promise.resolve(body) })
const settle = () => new Promise(resolve => setTimeout(resolve, 5))

for (const [code, status, message] of [
  ['no_image', 404, '此案件沒有可顯示的原圖'],
  ['image_unreadable', 404, '原圖無法讀取，檔案可能已被移除，請聯絡系統管理員'],
  ['temporary', 503, '暫時無法讀取原圖，請稍後重試'],
]) {
  test(`shows the server message for ${code}`, async () => {
    const page = load({ fetchImpl: reply(status, { code, message }) })
    page.listeners.error()
    await settle()
    assert.equal(page.box.textContent, message)
    assert.equal(page.box.hidden, false)
    assert.equal(page.image.hidden, true)
    const [url, options] = page.calls[0]
    assert.equal(url, '/cases/abc/image')
    assert.equal(options.headers.Accept, 'application/json')
    assert.equal(options.credentials, 'same-origin')
  })
}

test('network failure falls back to the temporary message', async () => {
  const page = load({ fetchImpl: () => Promise.reject(new Error('offline')) })
  page.listeners.error()
  await settle()
  assert.equal(page.box.textContent, '暫時無法讀取原圖，請稍後重試')
  assert.equal(page.box.hidden, false)
})

test('unexpected body never leaks: non-string message falls back', async () => {
  const page = load({ fetchImpl: reply(500, { message: { sql: 'SELECT …' } }) })
  page.listeners.error()
  await settle()
  assert.equal(page.box.textContent, '暫時無法讀取原圖，請稍後重試')
})

test('image that already failed before the script ran is explained', async () => {
  const page = load({ fetchImpl: reply(404, { code: 'no_image', message: '此案件沒有可顯示的原圖' }), complete: true, naturalWidth: 0 })
  await settle()
  assert.equal(page.box.textContent, '此案件沒有可顯示的原圖')
})

test('a healthy image is left alone', async () => {
  const page = load({ fetchImpl: reply(200, {}), complete: true, naturalWidth: 640 })
  await settle()
  assert.equal(page.calls.length, 0)
  assert.equal(page.box.hidden, true)
  assert.equal(page.image.hidden, false)
})
