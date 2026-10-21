# 開發規範與程式碼備註格式 — Python 網站（FastAPI + Jinja2）

> InvoiceGuard-AI（前台）與 InvoiceGuard-AI-ADMIN（後台）共用此規範。
> Python 的部分與 InvoiceGuard-AI-API 相同，三個專案寫法一致。

## 1. 基本原則

1. Python 以 **Ruff** 檢查與格式化（`ruff check .`、`ruff format .`）；樣板以 **djLint** 檢查與格式化（`djlint app/templates --lint`、`--reformat`）。
2. 所有 Python 函式參數與回傳值都要加型別註記。
3. 識別字（變數、函式、類別、CSS class）用**英文**；備註、畫面文字、錯誤訊息用**繁體中文**。
4. 商業邏輯寫在 Python，**樣板只負責呈現**。
5. 密碼、金鑰、連線字串禁止寫在程式碼，一律從 `app.core.config.settings` 讀取。
6. 資料存取只走固定通道：前台透過 `app/clients/` 呼叫 API；後台透過 `app/db/repositories/` 存取資料庫。

---

## 2. 命名規則

| 對象 | 規則 | 範例 |
|---|---|---|
| Python 模組 / 資料夾 | snake_case | `invoice_review_service.py`、`routers/` |
| 類別 | PascalCase | `ApiError`、`InvoiceStatus` |
| 函式 / 變數 | snake_case，函式以動詞開頭 | `fetch_invoices()`、`total_amount` |
| 常數 | UPPER_SNAKE_CASE | `SESSION_COOKIE_NAME` |
| 布林 | `is_` / `has_` / `can_` 開頭 | `is_active` |
| 路由函式 | `<名詞>_<頁面類型>` | `invoice_list()`、`invoice_detail()` |
| 網址路徑 | 小寫 kebab-case、資源用複數 | `/invoices`、`/audit-logs` |
| 樣板資料夾 | 與 routers 檔名一致 | `routers/invoices.py` → `templates/pages/invoices/` |
| 樣板檔 | 小寫 snake_case，表達頁面類型 | `list.html`、`detail.html`、`job_runs.html` |
| CSS 檔 | 小寫 kebab-case | `components.css` |
| CSS class | kebab-case，BEM 結構 | `.app-header__logo`、`.badge--flagged` |
| CSS 變數 | `--<類別>-<名稱>` | `--color-primary`、`--space-4` |
| 圖片檔 | 小寫 kebab-case，`<用途>-<描述>` | `banner-home.webp`、`icon-upload.svg` |
| 測試檔 / 函式 | `test_<模組>.py`、`test_<行為>_<預期>` | `test_protected_page_redirects_to_login` |

---

## 3. 備註格式

### 3.1 Python 檔案開頭（Module Docstring）— 每個 `.py` 檔必填

```python
"""
模組名稱：app.routers.invoices
功能說明：我的發票——列表、上傳、詳情（皆需登入）。
路由：GET /invoices、GET /invoices/upload、GET /invoices/{invoice_id}
注意事項：
    - 列表頁為「router → clients → 樣板」的完整參考寫法。
"""
```

| 欄位 | 必填 | 說明 |
|---|---|---|
| 模組名稱 | ✓ | import 路徑 |
| 功能說明 | ✓ | 一句話說清楚這個檔案負責什麼 |
| 路由 | routers 必填 | 此檔處理的網址 |
| 相依模組 / 相依設定 | | 主要相依的內部模組或環境變數 |
| 注意事項 | | 使用限制、副作用、安全考量 |

`__init__.py` 與簡單檔案可用一行 docstring。**作者與異動歷史以 git 為準**，檔頭不寫異動紀錄（後台 DB Model 檔例外，見 `app/db/README.md`）。

### 3.2 Python 函式 Docstring（Google Style）

公開函式必填；簡單到看名稱就懂的私有函式可省略。

```python
async def fetch_invoices(token: str, *, page: int = 1, status: str | None = None) -> dict[str, Any]:
    """取得我的發票列表（GET /invoices）。

    Args:
        token: 使用者的 access token。
        page: 頁碼，從 1 開始。
        status: 依狀態篩選；None 表示全部。

    Returns:
        {"items": [...], "total": int, "page": int, "page_size": int}

    Raises:
        ApiError: API 回傳錯誤或連線失敗。
    """
```

區塊：第一行摘要（必填）→ 詳細說明 → `Args:` → `Returns:` / `Yields:` → `Raises:` → `Example:`。

### 3.3 Jinja2 樣板開頭 — 每個 `.html` 樣板必填

使用 Jinja 備註 `{# #}`（不會輸出到瀏覽器）：

```jinja
{#
  @template pages/invoices/list.html
  @description 我的發票列表：依狀態篩選（HTMX 只替換表格區塊）、分頁。
  @route GET /invoices（需登入）
  @context result: dict       API 分頁結果 {items, total, page, page_size}
  @context status: str | None 目前篩選的狀態；None 為全部
#}
```

| 標籤 | 必填 | 說明 |
|---|---|---|
| `@template` | ✓ | 相對於 `app/templates/` 的路徑 |
| `@description` | ✓ | 頁面 / 片段用途 |
| `@route` | 頁面樣板必填 | 對應網址、是否需登入 |
| `@context` | 有傳入變數時 | `名稱: 型別  說明`，每個變數一行 |
| `@block` | 版型 / base 必填 | 子樣板可覆寫的 block 與用途 |

### 3.4 樣板內備註

```jinja
{# ✓ 內部說明一律用 Jinja 備註：不會出現在瀏覽器原始碼 #}
{# 選擇狀態後由 HTMX 取回整頁，只替換 #invoice-table #}

<!-- ✗ 不要用 HTML 備註寫內部說明：任何人按「檢視原始碼」都看得到 -->
```

### 3.5 CSS 備註

```css
/* ==========================================================================
   檔案 / 大區塊標題（說明本檔用途）
   ========================================================================== */

/* ---- 小區塊：對應的樣板或元件 ---- */
.app-header__nav {
  height: 64px; /* 與後台側欄 logo 區高度一致 */
}
```

### 3.6 JavaScript 備註（JSDoc）

```js
/**
 * @file 全站共用 JavaScript
 * @description 只放少量通用互動；頁面互動優先使用 HTMX 屬性。
 */

/**
 * 送出前確認：表單加上 data-confirm 屬性即會先跳出確認視窗。
 * @example
 * <form method="post" action="/logout" data-confirm="確定要登出嗎？">
 */
```

### 3.7 行內備註 — 說明「為什麼」，不是「做什麼」

```python
# ✗ 不好：重複程式碼本身
page = page + 1  # 頁碼加 1

# ✓ 好：說明原因
# 不認得的狀態值視為「全部」，避免把任意字串傳給 API
status = status if status in INVOICE_STATUS_LABEL else None
```

### 3.8 標記備註

| 語言 | 格式 |
|---|---|
| Python | `# <標記>(<負責人>): <說明> [#<issue>]` |
| Jinja2 樣板 | `{# <標記>(<負責人>): <說明> [#<issue>] #}` |
| CSS / JS | `/* <標記>(<負責人>): <說明> */`、`// <標記>(<負責人>): <說明>` |

| 標記 | 用途 | 範例 |
|---|---|---|
| `TODO` | 待實作 | `# TODO(JerryChien): 加入上傳進度條 [#18]` |
| `FIXME` | 已知問題待修正 | `# FIXME(JerryChien): 跨月發票期別計算錯誤 [#31]` |
| `NOTE` | 重要提醒，修改前必讀 | `{# NOTE: result 是 dict，需用 result["items"] #}` |
| `HACK` | 暫時性繞道解法，需說明何時移除 | `# HACK: 套件 bug，升級 2.x 後移除` |
| `SECURITY` | 安全相關，修改需 review | `# SECURITY: next 參數必須經過 safe_next_path()` |

### 3.9 不要寫的備註

- 被註解掉的舊程式碼 → 直接刪除，需要時從 git 找回
- 與程式碼重複、沒有新資訊的備註
- 作者、日期、修改歷史 → 交給 git
- 任何密碼、金鑰、個資
- 寫在 HTML 備註 `<!-- -->` 裡的內部說明

---

## 4. 樣板撰寫規則

1. 頁面一律 `{% extends "layouts/<版型>.html" %}`，只覆寫 `title`、`content` 等 block；不要直接繼承 `base.html`
2. 多頁共用的區塊抽成 `partials/`，用 `{% include %}` 引入
3. **樣板不寫商業邏輯**：只做顯示用的判斷與迴圈，計算放在 Python
4. **禁止**對使用者或 API / 資料庫資料使用 `|safe`（會造成 XSS）
5. dict 的 `items` 鍵要寫 `result["items"]`；`result.items` 會取到 dict 的方法
6. 靜態檔網址一律用 `{{ static_url('css/main.css') }}`（自動附版本參數）
7. 站內連結直接寫路徑（`href="/invoices"`）。不使用 `url_for()`：它會產生含 `http://` 的完整網址，在 Vercel 代理後方可能變成不安全的混合內容（djLint J018 因此關閉）
8. 會變更資料的操作一律用 `<form method="post">`，路由加上 `Depends(verify_same_origin)`；不要用 GET 連結做刪除 / 登出
9. 表單欄位都要有 `<label for>`；圖片都要有 `alt`

---

## 5. HTMX 使用規則

1. 先確保**不靠 JavaScript 也能用**（一般連結 / 表單），再用 `hx-*` 屬性加強體驗
2. 優先用 `hx-select` 從整頁回應中取出要替換的區塊，路由不必另外寫「片段版」
3. 需要專用片段時，片段樣板放在 `templates/pages/<功能>/_<名稱>.html`（底線開頭）
4. 不要在 `main.js` 用 `fetch` 呼叫 API；資料一律由伺服器端處理

---

## 6. CSS 規則

1. 顏色、字級、間距、圓角使用 `variables.css` 的 CSS 變數，禁止寫死
2. class 採 BEM：`區塊__元素--狀態`，例：`.invoice-card__title--muted`
3. 共用元件（按鈕、表單、表格…）放 `components.css`；版型與頁面樣式放 `main.css` 對應區塊
4. 單一頁面樣式變多時，拆成 `css/pages/<頁面>.css`，在該頁樣板的 `{% block head %}` 載入
5. 避免 `!important` 與 ID 選擇器；不寫 inline style

---

## 7. 圖片與靜態資源

| 類型 | 建議格式 | 位置 |
|---|---|---|
| 照片、橫幅 | WebP（必要時 JPG） | `app/static/img/` |
| 圖示、Logo、favicon | SVG | `app/static/icons/` |
| 字型 | WOFF2 | `app/static/fonts/` |
| 第三方函式庫 | 原檔 | `app/static/js/vendor/`（記錄版本與授權） |

- 單張圖片建議 **200 KB 以下**，上傳前先壓縮
- 檔名小寫 kebab-case，不使用中文與空白
- 不從外部 CDN 載入 JS / CSS（避免依賴第三方服務）

---

## 8. Python 其他規則

- **Import 順序**（Ruff 自動排序）：標準函式庫 → 第三方套件 → `app.` 開頭的專案模組
- **例外處理**：可預期錯誤拋出 `app.core.exceptions` 或 `ApiError`，由全域處理器顯示錯誤頁；不要 `except Exception: pass`
- **日誌**：`logger = logging.getLogger(__name__)`，使用 `%s` 參數而不是 f-string；禁止記錄密碼、token、cookie
- **時間**：一律 timezone-aware，顯示時用 `|taipei_date`、`|taipei_datetime` filter
- **金額**：一律 `Decimal` 或字串，顯示時用 `|currency` filter，禁止 `float`
- **測試**：單元測試不呼叫真實 API / 資料庫，以 monkeypatch 替換

---

## 9. 提交前檢查清單

- [ ] `ruff check .` 無錯誤、`ruff format .` 已執行
- [ ] `djlint app/templates --lint` 無錯誤、`djlint app/templates --reformat` 已執行
- [ ] `pytest` 全部通過
- [ ] 新的 `.py` 檔與樣板都有檔頭備註，公開函式都有 docstring
- [ ] 新增的 `TODO` 都有負責人
- [ ] 沒有寫死的色碼、網址、金鑰
