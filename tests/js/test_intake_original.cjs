/** /intake 核對頁原圖對照（app/static/js/intake-original.js）：假 DOM 與假 sessionStorage，不連網路、不用瀏覽器。 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')

const source = fs.readFileSync(path.resolve(__dirname, '../../app/static/js/intake-original.js'), 'utf8')

function run({ recognitionId = 'rid-1', embedded = null, store = {}, storage = 'ok', hasFigure = true }) {
  const attrs = embedded ? { src: embedded } : {}
  const img = {
    hidden: true,
    src: embedded || '',
    getAttribute: name => (name in attrs ? attrs[name] : null),
  }
  const note = { hidden: false }
  const figure = { getAttribute: name => (name === 'data-recognition-id' ? recognitionId : null) }
  const elements = { 'intake-original': hasFigure ? figure : null, 'intake-original-img': img, 'intake-original-note': note }
  const sessionStorage = {
    getItem: key => {
      if (storage === 'throws') throw new Error('blocked')
      return key in store ? store[key] : null
    },
    setItem: (key, value) => {
      if (storage === 'throws' || storage === 'full') throw new Error('quota')
      store[key] = value
    },
  }
  vm.runInNewContext(source, { document: { getElementById: id => elements[id] }, sessionStorage })
  return { img, note, store }
}

test('shows the embedded image and saves it under the recognition id', () => {
  const page = run({ embedded: 'data:image/png;base64,AAA' })
  assert.equal(page.img.hidden, false)
  assert.equal(page.note.hidden, true)
  assert.equal(page.store['intake-original:rid-1'], 'data:image/png;base64,AAA')
})

test('restores the saved image when a re-rendered page has no embedded image', () => {
  const page = run({ store: { 'intake-original:rid-1': 'data:image/png;base64,BBB' } })
  assert.equal(page.img.src, 'data:image/png;base64,BBB')
  assert.equal(page.img.hidden, false)
  assert.equal(page.note.hidden, true)
})

test('never shows an image saved for a different recognition', () => {
  const page = run({ recognitionId: 'rid-2', store: { 'intake-original:rid-1': 'data:image/png;base64,OLD' } })
  assert.equal(page.img.hidden, true)
  assert.equal(page.note.hidden, false)
})

test('keeps the fallback note when nothing was saved', () => {
  const page = run({})
  assert.equal(page.img.hidden, true)
  assert.equal(page.note.hidden, false)
})

test('still shows the embedded image when storage is full or blocked', () => {
  for (const storage of ['full', 'throws']) {
    const page = run({ embedded: 'data:image/png;base64,CCC', storage })
    assert.equal(page.img.hidden, false)
  }
})

test('falls back to the note when storage cannot be read', () => {
  const page = run({ storage: 'throws' })
  assert.equal(page.img.hidden, true)
  assert.equal(page.note.hidden, false)
})

test('does nothing on pages without the original-image section', () => {
  assert.doesNotThrow(() => run({ hasFigure: false }))
})
