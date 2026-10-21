# 第三方前端函式庫（vendor）

此資料夾放**原樣引入、不修改**的第三方檔案。不從外部 CDN 載入，避免網站依賴第三方服務的可用性與安全性。

| 檔案 | 套件 | 版本 | 授權 | 來源 |
|---|---|---|---|---|
| `htmx.min.js` | htmx | 2.0.11 | 0BSD | npm 套件 `htmx.org@2.0.11` 的 `dist/htmx.min.js` |

## 更新方式

1. 下載新版：`npm pack htmx.org@<版本>`，解壓後取出 `package/dist/htmx.min.js`
2. 覆蓋本資料夾的檔案，更新上表版本
3. 在本機操作有用到 `hx-*` 屬性的頁面確認正常

> 禁止直接修改 vendor 檔案；需要客製化請寫在 `static/js/main.js`。
