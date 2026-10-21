/**
 * @file v-permission 權限指令
 * @description 依目前管理者的權限決定元素是否顯示；沒有權限時直接移除 DOM。
 *   注意：這只是介面控制，真正的權限檢查必須在後端 API 進行。
 * @example
 * <button v-permission="'invoice:review'">核准</button>
 */
import { useAuthStore } from '@/stores/auth'

export const permission = {
  /**
   * @param {HTMLElement} el - 綁定的元素
   * @param {import('vue').DirectiveBinding<string>} binding - 權限代碼，例：'invoice:review'
   */
  mounted(el, binding) {
    const authStore = useAuthStore()
    if (!authStore.hasPermission(binding.value)) {
      el.remove()
    }
  },
}
