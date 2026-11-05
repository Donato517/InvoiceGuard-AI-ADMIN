/** ADMIN 統編檢查碼（app/static/js/tax-id.js）：與 API／前台共用向量，只在本機離線執行。 */
const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')

const { validTaxId } = require('../../app/static/js/tax-id.js')
const vectors = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, '../fixtures/tax_id_vectors.json'), 'utf8'),
).vectors

test('共用向量：與 API valid_tax_id 結果一致', () => {
  assert.ok(vectors.length >= 18)
  for (const v of vectors) assert.equal(validTaxId(v.tax_id), v.valid, JSON.stringify(v))
})

test('人工驗收向量：檢查碼不符的統編不通過，合法統編通過', () => {
  for (const ok of ['04595257', '27601907', '22644358', '88412766']) assert.equal(validTaxId(ok), true, ok)
  for (const bad of ['12345678', '12345679', '27601908']) assert.equal(validTaxId(bad), false, bad)
})

test('非字串、非 8 碼、全形與阿拉伯-印度數字一律不通過', () => {
  for (const v of [null, undefined, 4595257, '0459525', '045952577', '０４５９５２５７', '٠٤٥٩٥٢٥٧', '04595257\n', ' 04595257'])
    assert.equal(validTaxId(v), false, String(v))
})
