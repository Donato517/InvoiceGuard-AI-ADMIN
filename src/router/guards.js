/**
 * @file 路由守衛
 * @description 後台頁面預設需要登入：未登入導向登入頁並記錄原本要去的頁面；
 *   切換頁面後更新瀏覽器分頁標題。
 */
import { useAuthStore } from '@/stores/auth'

const SITE_NAME = 'InvoiceGuard 後台'

/**
 * 註冊路由守衛
 * @param {import('vue-router').Router} router - Router 實例
 */
export function setupRouterGuards(router) {
  router.beforeEach((to) => {
    if (to.meta.public) return true

    const authStore = useAuthStore()
    if (!authStore.isLoggedIn) {
      return { name: 'login', query: { redirect: to.fullPath } }
    }

    // TODO(JerryChien): 載入 profile 後檢查是否為 admin 角色，否則導向 forbidden
    return true
  })

  router.afterEach((to) => {
    document.title = to.meta.title ? `${to.meta.title} | ${SITE_NAME}` : SITE_NAME
  })
}
