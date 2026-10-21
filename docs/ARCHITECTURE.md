# 系統架構說明 — InvoiceGuard-AI-ADMIN（後台）

## 1. 系統全貌

InvoiceGuard AI 由三個獨立的 Git repository 組成，各自部署為一個 Vercel Project：

```
      User                              Admin
       |                                  |
       v                                  v
┌──────────────────────┐        ┌──────────────────────────┐
│ InvoiceGuard-AI      │        │ InvoiceGuard-AI-ADMIN    │
│ Vue 3 + Vite (SPA)   │        │ Vue 3 + Vite (SPA)       │
│ Vercel Static        │        │ Vercel Static            │
└──────────┬───────────┘        └─────────────┬────────────┘
           │ /api/v1/*                        │ /api/v1/admin/*
           └────────────────┬─────────────────┘
                            ▼  HTTPS + JSON (Bearer token)
             ┌──────────────────────────────┐
             │ InvoiceGuard-AI-API          │◀──── Vercel Cron
             │ Python FastAPI               │      GET /api/cron/*
             │ Vercel Function (Python)     │
             └───┬──────────┬──────────┬────┘
                 │          │          │
                 ▼          ▼          ▼
            PostgreSQL  AI Model   MOF e-Invoice
            (Neon)      Service    API
```

| Repository | 角色 | 技術 | 部署 |
|---|---|---|---|
| InvoiceGuard-AI | 前台：使用者上傳發票、查看 AI 分析結果 | Vue 3 + Vite | Vercel 靜態網站 |
| **InvoiceGuard-AI-ADMIN**（本專案） | 後台：發票審核、使用者管理、排程與稽核紀錄 | Vue 3 + Vite | Vercel 靜態網站 |
| InvoiceGuard-AI-API | 後端：REST API、AI 分析、第三方串接、排程 | Python FastAPI | Vercel Python Function |

後台只呼叫 `/api/v1/auth/*` 與 `/api/v1/admin/*`；**排程本身在 API 專案執行**，後台只負責查看執行紀錄。

---

## 2. 目錄結構

```
InvoiceGuard-AI-ADMIN/
├── public/                       # 不經編譯、原樣複製到網站根目錄的檔案
│   ├── favicon.svg
│   └── robots.txt                #   Disallow: /（後台不開放索引）
├── src/
│   ├── main.js                   # ★ 進入點：掛載 Pinia / Router / v-permission / 全域樣式
│   ├── App.vue                   # 根元件：依路由 meta.layout 切換版型
│   ├── api/                      # 後端 API 呼叫
│   │   ├── http.js               #   axios 共用實例（token、逾時、統一錯誤格式）
│   │   └── modules/              #   auth.js、invoice.js、user.js、system.js
│   ├── assets/                   # 會經過 Vite 編譯的靜態資源
│   │   ├── css/                  #   variables.css（含後台版面變數）、reset.css、main.css
│   │   ├── img/
│   │   ├── icons/
│   │   └── fonts/
│   ├── components/
│   │   ├── common/               #   通用 UI 元件（BaseTable、BasePagination…）
│   │   ├── business/             #   業務元件（RiskScoreTag、InvoicePreview…）
│   │   └── layout/               #   AppSidebar（左側選單）、AppHeader（頁首）
│   ├── composables/              # 可重用的組合式函式（useLoading…）
│   ├── constants/                # 常數：invoice.js（狀態）、menu.js（側欄選單）、storage.js
│   ├── directives/               # 自訂指令：permission.js（v-permission）
│   ├── layouts/                  # AdminLayout（側欄 + 頁首）、BlankLayout（登入 / 錯誤頁）
│   ├── router/                   # index.js（路由表）、guards.js（登入檢查、頁面標題）
│   ├── stores/                   # index.js（實例）、auth.js（登入 / 權限）、app.js（側欄狀態）
│   ├── utils/                    # 純函式工具（format.js）
│   └── views/                    # 頁面元件
│       ├── login/                #   登入
│       ├── dashboard/            #   儀表板
│       ├── invoice/              #   發票審核列表 / 詳情
│       ├── user/                 #   使用者管理
│       ├── system/               #   排程紀錄 / 稽核紀錄
│       └── error/                #   404 / 403
├── tests/
│   └── unit/                     # Vitest 單元測試
├── docs/
├── tmp/                          # 本機暫存（不進版控）
├── .github/pull_request_template.md
├── .vscode/extensions.json
├── index.html                    # HTML 樣板（含 noindex）
├── vite.config.js                # Vite 設定（@ 別名、port 5174）
├── vercel.json                   # SPA rewrite、安全標頭、X-Robots-Tag、快取
├── eslint.config.js / .prettierrc.json / jsconfig.json
├── package.json
├── .env.example
├── .editorconfig / .gitattributes / .gitignore
└── README.md
```

---

## 3. 檔案放置規則（我要放 js / css / 圖片 / 暫存檔，該放哪？）

| 要放的東西 | 位置 | 說明 |
|---|---|---|
| 頁面 | `src/views/<功能>/XxxView.vue` | 一個路由一個檔案 |
| 通用 UI 元件 | `src/components/common/` | 不可呼叫 API、不可依賴 store |
| 業務元件 | `src/components/business/` | 例：風險分數標籤、發票預覽 |
| 呼叫後端 API | `src/api/modules/<資源>.js` | 元件內禁止直接用 axios / fetch |
| 可重用的狀態邏輯 | `src/composables/useXxx.js` | |
| 跨頁共享的狀態 | `src/stores/<名稱>.js` | |
| 自訂指令 | `src/directives/` | 於 `main.js` 全域註冊 |
| 工具函式（JS） | `src/utils/` | 純函式 |
| 常數 / 列舉 / 選單 | `src/constants/` | 側欄選單在 `menu.js` |
| 全域 CSS / 設計變數 | `src/assets/css/` | 元件樣式寫在 `<style scoped>` |
| 圖片 / 圖示 / 字型 | `src/assets/img/`、`icons/`、`fonts/` | |
| 需固定網址的檔案 | `public/` | |
| 暫存檔 | `tmp/` | 已列入 `.gitignore`，只保留 `.gitkeep` |
| 建置輸出 | `dist/` | 自動產生，不進版控 |

---

## 4. 資料流

```
View ──▶ composables ──▶ api/modules/xxx.js ──▶ api/http.js ──▶ InvoiceGuard-AI-API /api/v1/admin/*
  │
  └─ 跨頁共享狀態 ──▶ stores（auth：登入與權限、app：側欄）
```

- `http.js` 成功時直接回傳 `response.data`；失敗時 reject `ApiError`（`{ code, message, details, status }`）
- API 欄位為 snake_case，前端直接沿用
- 金額為字串、時間為 UTC，顯示時一律透過 `utils/format.js`

---

## 5. 路由與權限

### 5.1 路由 meta

| meta 欄位 | 型別 | 說明 |
|---|---|---|
| `title` | string | 頁面標題，同時顯示在頁首 |
| `layout` | string | `admin`（預設）/ `blank` |
| `public` | boolean | 允許未登入存取（只有登入頁、錯誤頁） |

後台**預設所有頁面都需要登入**（白名單制），比前台的 `requiresAuth`（黑名單制）更安全。

### 5.2 權限控制（三層）

| 層級 | 實作 | 說明 |
|---|---|---|
| 頁面 | `router/guards.js` | 未登入 → `/login`；非管理員 → `/403`（待實作） |
| 按鈕 / 區塊 | `v-permission="'invoice:review'"` | 無權限時移除元素 |
| 資料 | **API 端** | 前端控制只是介面，真正的權限檢查一定在後端 |

權限代碼格式：`<模組>:<動作>`，例：`invoice:review`、`user:disable`。

---

## 6. 環境變數

| 變數 | 說明 | 範例 |
|---|---|---|
| `VITE_API_BASE_URL` | 後端 API 位址（含 `/api/v1`） | `http://localhost:8000/api/v1` |

- `VITE_` 變數打包後公開，嚴禁放金鑰
- 本機寫在 `.env.local`；Vercel 分別設定 Production / Preview 的值

---

## 7. 部署（Vercel）

| 項目 | 設定 |
|---|---|
| Framework Preset | Vite（自動偵測） |
| Build Command / Output | `npm run build` / `dist` |
| SPA 路由 | `vercel.json` rewrite 到 `index.html` |
| 搜尋引擎 | `robots.txt`、`<meta name="robots">`、`X-Robots-Tag` 三重 noindex |
| 存取限制（建議） | 正式環境可另開 Vercel 的 Deployment Protection 或限制 IP |
| CORS | 後台正式網域必須加入 API 的 `CORS_ORIGINS` |

---

## 8. 新增頁面檢查清單

1. `src/views/<功能>/XxxView.vue`（含檔頭備註）
2. `src/router/index.js` 新增路由（`name`、`meta.title`）
3. 需要出現在側欄時，更新 `src/constants/menu.js`
4. API 加到 `src/api/modules/<資源>.js`，並確認 API 專案 `/api/v1/admin/*` 有對應端點
5. 需權限控制的按鈕加上 `v-permission`
6. `npm run lint && npm run test && npm run build` 通過
