/**
 * @file Pinia 實例
 * @description 建立全域 Pinia 實例，於 main.js 掛載。各 store 依功能拆成同資料夾的獨立檔案。
 */
import { createPinia } from 'pinia'

const pinia = createPinia()

export default pinia
