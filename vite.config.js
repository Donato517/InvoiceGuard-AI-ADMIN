/**
 * @file Vite 建置設定
 * @description 設定 Vue 外掛、路徑別名（@ → src）、開發伺服器埠號。
 */
import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // 前台固定 5173、後台固定 5174，需與 API 的 CORS_ORIGINS 一致
    port: 5174,
    strictPort: true,
  },
})
