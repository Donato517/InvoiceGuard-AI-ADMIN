# 開發規範與程式碼備註格式 — 前端（Vue 3）

> InvoiceGuard-AI（前台）與 InvoiceGuard-AI-ADMIN（後台）共用此規範。

## 1. 基本原則

1. 以 **ESLint + Prettier** 自動檢查與格式化（`npm run lint`、`npm run format`），不手動爭論格式。
2. 元件一律使用 **Composition API + `<script setup>`**，不使用 Options API。
3. 一個元件只負責一件事；單檔超過約 300 行就考慮拆分。
4. 識別字用**英文**；備註、畫面文字、錯誤訊息用**繁體中文**。
5. 跨資料夾 import 一律使用 `@/` 別名，不寫 `../../`。
6. 前端不存放任何金鑰；`VITE_` 環境變數會公開。

---

## 2. 命名規則

| 對象 | 規則 | 範例 |
|---|---|---|
| Vue 元件檔 | PascalCase，至少兩個單字 | `InvoiceCard.vue`、`AppHeader.vue` |
| 頁面元件 | PascalCase + `View` 結尾 | `InvoiceListView.vue` |
| 版型元件 | PascalCase + `Layout` 結尾 | `DefaultLayout.vue` |
| 通用元件 | `Base` 開頭 | `BaseButton.vue`、`BaseModal.vue` |
| JS 檔案 | camelCase | `http.js`、`format.js` |
| 資料夾 | 小寫 kebab-case | `views/invoice/`、`components/common/` |
| 組合式函式 | `use` 開頭 | `useLoading.js` → `useLoading()` |
| Pinia store | 檔名為名詞，函式 `use<名詞>Store` | `user.js` → `useUserStore()` |
| 函式 / 變數 | camelCase，函式以動詞開頭 | `fetchInvoices()`、`totalAmount` |
| API 函式 | `fetch`（查詢）/ `create` / `update` / `delete` / 動詞 | `fetchInvoice()`、`uploadInvoice()` |
| 事件處理 | `handle` / `on` 開頭 | `handleSubmit()` |
| 布林 | `is` / `has` / `can` 開頭 | `isLoggedIn`、`hasPermission` |
| 常數 | UPPER_SNAKE_CASE | `INVOICE_STATUS` |
| 路由 name | kebab-case | `invoice-detail` |
| CSS class | kebab-case，BEM 結構 | `.app-header__logo`、`.risk-badge--danger` |
| CSS 變數 | `--<類別>-<名稱>` | `--color-primary`、`--space-4` |
| 圖片檔 | 小寫 kebab-case，`<用途>-<描述>` | `banner-home.webp`、`icon-upload.svg` |
| 測試檔 | 與被測檔同名 + `.test.js` | `format.test.js` |

---

## 3. 備註格式

### 3.1 JS 檔案開頭 — 每個 `.js` 檔必填

```js
/**
 * @file 發票 API
 * @description 前台使用者的發票 API：列表、詳情、上傳、重新分析。對應後端：/api/v1/invoices
 */
```

| 標籤 | 必填 | 說明 |
|---|---|---|
| `@file` | ✓ | 檔案名稱或用途（簡短） |
| `@description` | ✓ | 負責什麼、使用限制、對應的後端路徑 |
| `@example` | | 用法不直覺時（composables 建議必填） |

### 3.2 Vue 元件開頭 — 每個 `.vue` 檔必填

寫在檔案最上方、`<script setup>` 之前：

```vue
<!--
  @component InvoiceDetailView
  @description 發票詳情：發票資料、明細、AI 風險分數與判斷理由。
  @route /invoices/:id （name: invoice-detail，需登入）
-->
```

| 標籤 | 必填 | 說明 |
|---|---|---|
| `@component` | ✓ | 元件名稱（與檔名一致） |
| `@description` | ✓ | 元件用途 |
| `@route` | 頁面元件必填 | 路徑、路由 name、是否需登入 |
| `@slot` | 有 slot 時 | 各 slot 用途 |

> **作者與異動歷史以 git 為準**，檔頭不寫異動紀錄，避免備註與實際內容不同步。

### 3.3 函式（JSDoc）

所有 `export` 的函式必填；內部小函式在用途不明顯時才寫。

```js
/**
 * 取得我的發票列表
 * @param {Object} [params] - 查詢條件
 * @param {number} [params.page=1] - 頁碼
 * @param {string} [params.status] - 依狀態篩選，值見 constants/invoice.js
 * @returns {Promise<{ items: Object[], total: number }>} 分頁結果
 * @throws {ApiError} 呼叫失敗時（見 api/http.js）
 */
export function fetchInvoices(params) {
  return http.get('/invoices', { params })
}
```

| 標籤 | 格式 |
|---|---|
| `@param` | `{型別} 名稱 - 說明`；選填參數用 `[名稱]`，有預設值用 `[名稱=預設]` |
| `@returns` | `{型別} 說明` |
| `@throws` | 會拋出 / reject 的錯誤 |
| `@example` | 用法範例 |
| `@typedef` | 定義共用的物件結構（例：`ApiError`） |

### 3.4 Props / Emits / 狀態

每個 prop、emit、ref 用一行 `/** */` 說明（IDE 滑鼠移上去就看得到）：

```js
const props = defineProps({
  /** 發票 ID（由路由參數 :id 傳入） */
  id: { type: String, required: true },
  /** 是否顯示明細 */
  showItems: { type: Boolean, default: false },
})

const emit = defineEmits({
  /** 使用者確認複核結果，參數為 'passed' | 'rejected' */
  review: (decision) => ['passed', 'rejected'].includes(decision),
})

/** 目前登入的使用者資料，未載入時為 null */
const profile = ref(null)
```

### 3.5 行內備註 — 說明「為什麼」，不是「做什麼」

```js
// ✗ 不好：重複程式碼本身
count.value++ // count 加 1

// ✓ 好：說明原因
timeout: 30000, // AI 分析可能較久，逾時設 30 秒

// ✓ 好：說明業務規則
// Pinia 必須先於 Router 掛載，路由守衛才能使用 store
createApp(App).use(pinia).use(router).mount('#app')
```

Template 內的備註使用 `<!-- -->`，只在區塊結構複雜時使用。

### 3.6 標記備註

格式：`// <標記>(<負責人>): <說明> [#<issue 編號>]`

| 標記 | 用途 | 範例 |
|---|---|---|
| `TODO` | 待實作 | `// TODO(JerryChien): 加入拖曳上傳 [#18]` |
| `FIXME` | 已知問題待修正 | `// FIXME(JerryChien): Safari 日期解析錯誤 [#25]` |
| `NOTE` | 重要提醒，修改前必讀 | `// NOTE: 值須與後端 InvoiceStatus 一致` |
| `HACK` | 暫時性繞道解法，需說明何時移除 | `// HACK: 套件 bug，升級 2.x 後移除` |
| `SECURITY` | 安全相關，修改需 review | `// SECURITY: 不可用 v-html 顯示使用者輸入` |

### 3.7 CSS 備註

```css
/* ==========================================================================
   檔案 / 大區塊標題（說明本檔用途）
   ========================================================================== */

/* ---- 小區塊 ---- */
.app-header {
  height: 64px; /* 與 AppSidebar logo 區高度一致 */
}
```

### 3.8 不要寫的備註

- 被註解掉的舊程式碼 → 直接刪除，需要時從 git 找回
- 與程式碼重複、沒有新資訊的備註
- 作者、日期、修改歷史 → 交給 git
- 任何金鑰、帳號密碼、個資

---

## 4. Vue 元件撰寫規則

1. SFC 區塊順序：**檔頭備註 → `<script setup>` → `<template>` → `<style scoped>`**
2. `<script setup>` 內順序：import → props / emits → store / router → 狀態（ref / reactive）→ computed → 函式 → 生命週期
3. Props 一定要寫型別；需要時加 `required` 或 `default`
4. Template 內不寫複雜運算，抽成 `computed`
5. `v-for` 一定要有唯一的 `:key`（使用資料 id，不用 index）
6. 禁止用 `v-html` 顯示使用者或 API 輸入的內容（XSS 風險）
7. 頁面元件負責取資料，`components/common/` 只透過 props / emits 溝通

---

## 5. API 呼叫規則

1. 一律透過 `src/api/modules/*.js`，元件內禁止直接 `axios` / `fetch`
2. 一個後端資源一個檔案，函式要有 JSDoc 並註明對應的後端路徑
3. 錯誤統一為 `ApiError`，畫面顯示 `error.message`；需要特別處理時判斷 `error.code`
4. API 位址只從 `import.meta.env.VITE_API_BASE_URL` 取得，禁止寫死

---

## 6. 樣式規則

1. 元件樣式一律 `<style scoped>`
2. 顏色、字級、間距、圓角使用 `variables.css` 的 CSS 變數，禁止寫死
3. class 採 BEM：`區塊__元素--狀態`，例：`.invoice-card__title--muted`
4. 避免 `!important` 與 ID 選擇器
5. 全站共用樣式才放 `assets/css/main.css`

---

## 7. 圖片與靜態資源

| 類型 | 建議格式 | 位置 |
|---|---|---|
| 照片、橫幅 | WebP（必要時 JPG） | `src/assets/img/` |
| 圖示、Logo | SVG | `src/assets/icons/` |
| 需固定網址（favicon、OG 圖） | SVG / PNG | `public/` |

- 單張圖片建議 **200 KB 以下**，上傳前先壓縮
- 檔名小寫 kebab-case，不使用中文與空白
- `<img>` 一定要有 `alt`

---

## 8. 提交前檢查清單

- [ ] `npm run lint`、`npm run test`、`npm run build` 通過
- [ ] 新檔案都有檔頭備註，export 的函式都有 JSDoc
- [ ] 新增的 `TODO` 都有負責人
- [ ] 沒有 `console.log` 殘留（除錯用的請刪除）
- [ ] 沒有寫死的色碼、API 網址、金鑰
