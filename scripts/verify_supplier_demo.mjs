// 本機試作網頁驗收：專用無頭瀏覽器，不讀取使用者瀏覽器或登入資料。
import { chromium } from 'file:///C:/Users/ynxen/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright/index.mjs';
import fs from 'node:fs/promises';

const directory = new URL('../artifacts/supplier-demo/browser/', import.meta.url);
await fs.mkdir(directory, { recursive: true });
const browser = await chromium.launch({ executablePath: 'C:/Program Files/Google/Chrome/Application/chrome.exe', headless: true });
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', route => route.request().url().startsWith('http://127.0.0.1:8092/') ? route.continue() : route.abort());
  await page.goto('http://127.0.0.1:8092/suppliers');
  await page.getByRole('heading', {name:'供應商管理', exact:true}).waitFor();
  await page.screenshot({path: new URL('list.png', directory).pathname.replace(/^\/(C:)/, '$1'), fullPage:true});
  await page.getByRole('link', {name:'新增供應商', exact:true}).click();
  await page.getByLabel('供應商名稱').fill('模擬網頁驗收供應商');
  await page.getByLabel('統編／稅籍編號').fill(String(Date.now() % 100000).padStart(8, '0'));
  await page.getByLabel('別名').fill('網頁驗收別名');
  await page.getByLabel('聯絡人').fill('虛構測試窗口');
  await page.getByLabel('付款天數').fill('45');
  await page.getByLabel('供應商徵信等級', {exact:true}).selectOption('B');
  await page.getByLabel('交貨條件', {exact:true}).fill('送達模擬指定地點，含安裝驗收');
  await page.getByLabel('備註').fill('僅供網頁驗收的虛構資料，可在試作中修改或停用。');
  await page.getByRole('button', {name:'儲存模擬資料'}).click();
  await page.getByText('已新增模擬供應商', {exact:true}).waitFor();
  await page.getByText('B 級（模擬一般風險）', {exact:true}).waitFor();
  await page.getByText('送達模擬指定地點，含安裝驗收', {exact:true}).waitFor();
  await page.getByRole('checkbox', {name:'我確認停用此模擬供應商'}).check();
  await page.getByRole('button', {name:'確認停用', exact:true}).click();
  await page.getByText('已停用模擬供應商，資料仍保留', {exact:true}).waitFor();
  await page.getByRole('button', {name:'重新啟用', exact:true}).click();
  await page.getByText('已啟用模擬供應商', {exact:true}).waitFor();
  await page.getByRole('link', {name:'修改資料', exact:true}).click();
  await page.getByLabel('供應商名稱').fill('模擬網頁驗收供應商已修改');
  await page.getByRole('button', {name:'儲存模擬資料'}).click();
  await page.getByText('已儲存模擬資料', {exact:true}).waitFor();
  await page.screenshot({path: new URL('detail.png', directory).pathname.replace(/^\/(C:)/, '$1'), fullPage:true});
  await page.getByRole('link', {name:'返回列表', exact:true}).click();
  await page.getByLabel('統編、名稱或別名').fill('網頁驗收別名');
  await page.getByRole('button', {name:'搜尋', exact:true}).click();
  await page.getByRole('link', {name:'模擬網頁驗收供應商已修改', exact:true}).first().waitFor();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path: new URL('mobile-list.png', directory).pathname.replace(/^\/(C:)/, '$1'), fullPage:true});
  await page.getByRole('link', {name:'新增供應商', exact:true}).click();
  await page.screenshot({path: new URL('mobile-form.png', directory).pathname.replace(/^\/(C:)/, '$1'), fullPage:true});
  if (errors.length) throw new Error(errors.join('\n'));
  console.log('PASS: browser create/edit/search/disable/enable; desktop/mobile screenshots; no page errors');
} finally { await browser.close(); }
