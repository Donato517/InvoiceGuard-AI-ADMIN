/** @file 用實際 DOM 驗證部門批次勾選；所有網頁請求攔截、offline，禁止送出。 */
const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const root = path.resolve(__dirname, '../..')
const fixture = process.env.BULK_PREVIEW_HTML || path.join(root, 'tmp/e-department-bulk/detail.html')
let chromium
try { ({ chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright')) } catch {}

if (!chromium || !fs.existsSync(fixture)) {
  test('部門批次勾選實際 DOM 測試', { skip: '需既有 Playwright 與合成 HTML，見 E-DEPARTMENT-BULK.md' }, () => {})
} else {
  let browser
  before(async () => {
    const executablePath = [chromium.executablePath(), 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe', process.env.LOCALAPPDATA && path.join(process.env.LOCALAPPDATA, 'Google/Chrome/Application/chrome.exe')].filter(Boolean).find(fs.existsSync)
    assert.ok(executablePath, '請使用已安裝瀏覽器，不自動下載')
    browser = await chromium.launch({ headless: true, executablePath, args: ['--disable-background-networking'] })
  })
  after(async () => { if (browser) await browser.close() })

  async function open(t, { js = true, locked = false, width = 1000 } = {}) {
    const context = await browser.newContext({ offline: true, javaScriptEnabled: js, serviceWorkers: 'block', viewport: { width, height: 1000 } })
    t.after(() => context.close())
    let writes = 0
    await context.route('**/*', async route => {
      const request = route.request()
      if (request.method() !== 'GET') { writes++; return route.abort() }
      const url = new URL(request.url())
      if (url.pathname.endsWith('/edit')) {
        let html = fs.readFileSync(fixture, 'utf8')
        if (locked) html = html.replace('data-bulk-locked="false"', 'data-bulk-locked="true"')
        return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html })
      }
      if (url.pathname.startsWith('/static/')) {
        const file = path.resolve(root, 'app', url.pathname.slice(1))
        if (!file.startsWith(path.resolve(root, 'app/static') + path.sep) || !fs.existsSync(file)) return route.abort()
        return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'application/javascript', body: fs.readFileSync(file) })
      }
      return route.abort()
    })
    const page = await context.newPage()
    await page.goto('http://invoiceguard-ai-admin.localhost:8080/department/synthetic/edit')
    return { page, writes: () => writes, rows: page.locator('[data-grant-row]'), all: page.getByRole('button', { name: '全部勾選', exact: true }), none: page.getByRole('button', { name: '全部取消', exact: true }) }
  }
  const actions = row => row.locator('input[type="checkbox"][name="grant"]')
  const selected = row => row.locator('input[type="checkbox"][name="grant"]:checked').count()
  const mixed = row => row.locator('[data-row-toggle]').evaluate(el => el.indeterminate)

  test('初始整列半選正確，不修改原始設定', async t => {
    const { rows, page, writes } = await open(t)
    assert.equal(await selected(rows.nth(0)), 1)
    assert.equal(await mixed(rows.nth(0)), true)
    assert.equal(await mixed(rows.nth(1)), false)
    assert.equal(await page.locator('[data-builtin-welcome] input:checked').count(), 1)
    assert.equal(writes(), 0)
  })

  test('整列全選與取消只影響該列四項操作', async t => {
    const { rows, page, writes } = await open(t)
    const toggle = rows.nth(0).locator('[data-row-toggle]')
    await toggle.check()
    assert.equal(await selected(rows.nth(0)), 4)
    assert.equal(await selected(rows.nth(1)), 0)
    await toggle.uncheck()
    assert.equal(await selected(rows.nth(0)), 0)
    assert.equal(await mixed(rows.nth(0)), false)
    assert.equal(await page.locator('[data-builtin-welcome] input:checked').count(), 1)
    assert.equal(writes(), 0)
  })

  test('逐項變更同步半選與全選狀態', async t => {
    const { rows } = await open(t)
    const row = rows.nth(0)
    await row.locator('[data-row-toggle]').check()
    await actions(row).nth(1).uncheck()
    assert.equal(await row.locator('[data-row-toggle]').isChecked(), false)
    assert.equal(await mixed(row), true)
    await actions(row).nth(1).check()
    assert.equal(await row.locator('[data-row-toggle]').isChecked(), true)
    assert.equal(await mixed(row), false)
  })

  test('整頁全選與取消保留歡迎頁、部門、ID及版本，無自動送出', async t => {
    const { page, rows, all, none, writes } = await open(t)
    const fields = () => page.locator('[data-permission-bulk]').evaluate(form => Array.from(new FormData(form).entries()).filter(([key]) => key !== 'grant'))
    const original = await fields()
    await all.click()
    assert.equal(await selected(rows.nth(0)), 4)
    assert.equal(await selected(rows.nth(1)), 4)
    const names = await page.locator('[data-permission-bulk]').evaluate(form => Array.from(new FormData(form).keys()))
    assert.deepEqual([...new Set(names)].sort(), ['catalog_complete','department_code','department_name','expected_revision','function_id','grant','is_active'].sort())
    await none.click()
    assert.equal(await selected(rows.nth(0)), 0)
    assert.equal(await selected(rows.nth(1)), 0)
    assert.equal(await page.locator('[data-builtin-welcome] input:checked').count(), 1)
    assert.equal(await page.locator('[data-builtin-welcome] input:not(:disabled)').count(), 0)
    assert.deepEqual(await fields(), original)
    assert.equal(writes(), 0)
  })

  test('批次操作略過停用欄位，無可編輯操作時停用控制項', async t => {
    const { page, rows, all, none } = await open(t)
    const fixed = actions(rows.nth(0)).nth(1)
    await fixed.evaluate(el => { el.disabled = true; el.checked = true })
    await all.click()
    await none.click()
    assert.equal(await fixed.isChecked(), true)
    assert.equal(await selected(rows.nth(0)), 1)
    await page.locator('[data-grant-row] input[name="grant"]').evaluateAll(boxes => boxes.forEach(box => { box.disabled = true }))
    await page.evaluate(() => window.dispatchEvent(new Event('pageshow')))
    assert.equal(await all.isDisabled(), true)
    assert.equal(await none.isDisabled(), true)
    assert.equal(await rows.nth(0).locator('[data-row-toggle]').isDisabled(), true)
  })

  test('重設與頁面還原後重新計算整列狀態', async t => {
    const { page, rows, all } = await open(t)
    await all.click()
    await page.locator('[data-permission-bulk]').evaluate(form => form.reset())
    assert.equal(await selected(rows.nth(0)), 1)
    assert.equal(await mixed(rows.nth(0)), true)
    await actions(rows.nth(0)).evaluateAll(boxes => boxes.forEach(box => { box.checked = true }))
    await page.evaluate(() => window.dispatchEvent(new Event('pageshow')))
    assert.equal(await rows.nth(0).locator('[data-row-toggle]').isChecked(), true)
  })

  test('JS 未啟用時隱藏批次控制項，仍可逐項勾選', async t => {
    const { page, rows } = await open(t, { js: false })
    assert.equal(await page.locator('[data-bulk-controls]').isVisible(), false)
    assert.equal(await rows.nth(0).locator('[data-row-controls]').isVisible(), false)
    await actions(rows.nth(0)).nth(1).check()
    assert.equal(await selected(rows.nth(0)), 2)
  })

  test('衝突表單停用批次操作，不能由事件改動權限', async t => {
    const { rows, all } = await open(t, { locked: true })
    assert.equal(await all.isDisabled(), true)
    assert.equal(await rows.nth(0).locator('[data-row-toggle]').isDisabled(), true)
    await all.evaluate(button => button.dispatchEvent(new MouseEvent('click', { bubbles: true })))
    assert.equal(await selected(rows.nth(0)), 1)
    assert.equal(await selected(rows.nth(1)), 0)
  })

  test('桌機與手機六欄對齊，只有表格內橫向捲動', async t => {
    for (const width of [1000, 390]) {
      const { page } = await open(t, { width })
      await page.locator('[data-grant-row] .e-permission-function code').last().evaluate(el => { el.textContent = 'Triplicate Uniform Invoice / A long feature code' })
      assert.equal(await page.locator('thead th').count(), 6)
      const widths = await page.locator('thead th:not(:first-child)').evaluateAll(cells => cells.map(cell => cell.getBoundingClientRect().width))
      assert.ok(Math.max(...widths) - Math.min(...widths) <= 1, 'five control columns must have equal width')
      for (const row of await page.locator('[data-grant-row], [data-builtin-welcome]').all()) {
        const centers = await row.locator('input[type="checkbox"]').evaluateAll(boxes => boxes.map(box => { const r = box.getBoundingClientRect(); return r.y + r.height / 2 }))
        assert.ok(Math.max(...centers) - Math.min(...centers) <= 1, 'checkbox centers must align across the row')
      }
      assert.equal(await page.locator('[data-builtin-welcome] td').count(), 6)
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
      await page.screenshot({ path: path.join(path.dirname(fixture), `bulk-${width}.png`), fullPage: true })
    }
  })
}
