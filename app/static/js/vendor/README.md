# 第三方前端函式庫（vendor）

此資料夾放**原樣引入、不修改**的第三方檔案。不從外部 CDN 載入，避免網站依賴第三方服務的可用性與安全性。

| 檔案 | 套件 | 版本 | 授權 | 來源 |
|---|---|---|---|---|
| `htmx.min.js` | htmx | 2.0.11 | 0BSD | npm 套件 `htmx.org@2.0.11` 的 `dist/htmx.min.js` |
| `pdfjs/pdf.min.mjs`、`pdfjs/pdf.worker.min.mjs`、`pdfjs/cmaps/`、`pdfjs/LICENSE` | pdf.js | 4.10.38 | Apache-2.0 | npm 套件 `pdfjs-dist@4.10.38` 的 `legacy/build/`（相容較舊瀏覽器）、`cmaps/`（中文字型對照）與 `LICENSE`；與前台相同。發票流程步驟 1 新上傳選 PDF 時才載入（`js/pdf-invoice-page.js`、`js/flow-upload-pdf.js`） |

## 更新方式

> pdf.js：`npm pack pdfjs-dist@<版本>`，取出 `package/legacy/build/pdf.min.mjs`、`pdf.worker.min.mjs`、`package/cmaps/` 與 `package/LICENSE`；前台與 ADMIN 同步更新，再到兩站上傳頁選多頁 PDF 確認能找出發票頁。

1. 下載新版：`npm pack htmx.org@<版本>`，解壓後取出 `package/dist/htmx.min.js`
2. 覆蓋本資料夾的檔案，更新上表版本
3. 在本機操作有用到 `hx-*` 屬性的頁面確認正常

> 禁止直接修改 vendor 檔案；需要客製化請寫在 `static/js/main.js`。
