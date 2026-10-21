/**
 * @file 應用程式進入點
 * @description 建立 Vue app，依序掛載 Pinia、Router、全域指令與全域樣式。
 *   Pinia 必須先於 Router 掛載，路由守衛才能使用 store。
 */
import { createApp } from 'vue'

import App from '@/App.vue'
import { permission } from '@/directives/permission'
import router from '@/router'
import pinia from '@/stores'
import '@/assets/css/main.css'

createApp(App).use(pinia).use(router).directive('permission', permission).mount('#app')
