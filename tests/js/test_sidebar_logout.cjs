/** Offline real-browser sidebar geometry and stale-style-cache regression. */
const { test, before, after } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const { chromium } = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright')
const root = path.resolve(__dirname, '../..')
const output = path.join(root, 'tmp/sidebar-logout')
let browser

before(async () => {
  const executablePath = [chromium.executablePath(), 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', 'C:/Program Files/Microsoft/Edge/Application/msedge.exe'].find(fs.existsSync)
  assert.ok(executablePath, 'Use an already installed browser; do not download')
  browser = await chromium.launch({ headless: true, executablePath, args: ['--disable-background-networking'] })
})
after(async () => { if (browser) await browser.close() })

async function open(t, { width = 1440, fixture = 'welcome', legacy = false } = {}) {
  const context = await browser.newContext({ offline: true, serviceWorkers: 'block', viewport: { width, height: 1000 } })
  t.after(() => context.close())
  const writes = []
  await context.route('**/*', async route => {
    const request = route.request()
    const url = new URL(request.url())
    if (request.method() !== 'GET') {
      writes.push({ method: request.method(), path: url.pathname })
      if (url.pathname === '/logout') return route.fulfill({ status: 303, headers: { location: '/login' }, body: '' })
      return route.abort()
    }
    if (url.pathname === '/login') return route.fulfill({ contentType: 'text/html', body: '<main>登入頁</main>' })
    if (url.pathname === '/welcome' || url.pathname === '/workspace') {
      let html = fs.readFileSync(path.join(output, `${fixture}.html`), 'utf8')
      if (legacy) html = html.replace(/(css\/main\.css)\?v=[^"]+/, '$1?v=dev')
      return route.fulfill({ contentType: 'text/html; charset=utf-8', body: html })
    }
    if (url.pathname.startsWith('/static/')) {
      const file = path.resolve(root, 'app', url.pathname.slice(1))
      if (!file.startsWith(path.resolve(root, 'app/static') + path.sep) || !fs.existsSync(file)) return route.abort()
      let body = fs.readFileSync(file)
      // A browser that has the old ?v=dev root stylesheet sees the native button.
      if (url.pathname === '/static/css/main.css' && url.searchParams.get('v') === 'dev') {
        body = Buffer.from(body.toString().replace(/\.app-sidebar__logout(?:-button)?(?::focus-visible)?\s*\{[^}]*\}/g, ''))
      }
      return route.fulfill({ contentType: file.endsWith('.css') ? 'text/css' : file.endsWith('.svg') ? 'image/svg+xml' : 'application/javascript', body })
    }
    return route.abort()
  })
  const page = await context.newPage()
  await page.goto(`http://invoiceguard-ai-admin.localhost:8080/${fixture}`)
  return { page, writes }
}

async function geometry(locator) {
  return locator.evaluate(el => {
    const r = el.getBoundingClientRect()
    const s = getComputedStyle(el)
    return { left: r.left, right: r.right, width: r.width, height: r.height, top: r.top, bottom: r.bottom,
      font: s.fontFamily, size: s.fontSize, weight: s.fontWeight, line: s.lineHeight,
      padding: [s.paddingTop, s.paddingRight, s.paddingBottom, s.paddingLeft],
      color: s.color, background: s.backgroundColor, border: s.borderTopWidth, align: s.textAlign }
  })
}

test('舊快取可重現原生登出按鈕的寬度不一致', async t => {
  const { page } = await open(t, { legacy: true })
  const account = await geometry(page.locator('nav a[href="/accounts"]'))
  const logout = await geometry(page.locator('nav form[action="/logout"] button'))
  assert.ok(Math.abs(account.width - logout.width) > 5)
  await page.screenshot({ path: path.join(output, 'stale-cache.png') })
})

for (const fixture of ['welcome', 'workspace']) for (const width of [1440, 390]) {
  test(`${fixture} ${width}px 登出尺寸與文字一致`, async t => {
    const { page, writes } = await open(t, { fixture, width })
    const account = await geometry(page.locator('nav a[href="/accounts"]'))
    const logout = await geometry(page.locator('nav form[action="/logout"] button'))
    for (const key of (width < 700 && fixture === 'welcome' ? ['height'] : ['height', 'width', 'left', 'right'])) assert.ok(Math.abs(account[key] - logout[key]) <= 1, `${key}: ${JSON.stringify({ account, logout })}`)
    for (const key of ['font', 'size', 'weight', 'line', 'padding', 'color', 'background']) assert.deepEqual(logout[key], account[key], key)
    assert.equal(logout.border, '0px')
    assert.equal(logout.align, 'left')
    assert.equal(await page.locator('nav a[href="/accounts"]').evaluate(el => el.nextElementSibling.matches('form[action="/logout"]')), true)
    if (width >= 700 || fixture !== 'welcome') assert.ok(Math.abs(account.bottom - logout.top) <= 1, 'logout is immediately after accounts')
    assert.equal(await page.locator('nav form[action="/logout"]').count(), 1)
    assert.deepEqual(writes, [])
    if (fixture === 'welcome') {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), true)
      await page.screenshot({ path: path.join(output, `fixed-${width}.png`), fullPage: true })
    }
  })
}

test('滑鼠及鍵盤效果沿用側欄且原生表單正常登出', async t => {
  const { page, writes } = await open(t)
  const account = page.locator('nav a[href="/accounts"]')
  const logout = page.locator('nav form[action="/logout"] button')
  await account.hover()
  const hovered = await geometry(account)
  await logout.hover()
  const actual = await geometry(logout)
  assert.equal(actual.background, hovered.background)
  assert.equal(actual.color, hovered.color)
  await logout.focus()
  await page.keyboard.press("Tab")
  await page.keyboard.press("Shift+Tab")
  assert.equal(await logout.evaluate(el => getComputedStyle(el).outlineStyle), 'solid')
  await logout.click()
  await page.waitForURL('**/login')
  assert.deepEqual(writes, [{ method: 'POST', path: '/logout' }])
})


for (const width of [1440, 390]) {
  test(`部門授權目錄 ${width}px 顯示待接線功能且不產生錯誤連結`, async t => {
    const { page, writes } = await open(t, { width })
    const pending = page.locator('nav .app-sidebar__link--pending')
    assert.equal(await pending.count(), 1)
    assert.ok((await pending.textContent()).includes('(21)三聯式統一發票'))
    assert.ok((await pending.textContent()).includes('尚未接線'))
    assert.equal(await pending.getAttribute('aria-disabled'), 'true')
    assert.equal(await pending.locator('a, button').count(), 0)
    assert.equal(await page.locator('nav a[href="/invoices"]').count(), 0)
    const box = await pending.boundingBox()
    assert.ok(box.x >= 0 && box.x + box.width <= width + 1)
    await pending.click()
    assert.ok(page.url().endsWith('/welcome'))
    assert.deepEqual(writes, [])
    await page.screenshot({ path: path.join(output, `catalog-${width}.png`), fullPage: true })
  })
}
