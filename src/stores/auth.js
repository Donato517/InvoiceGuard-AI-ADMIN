/**
 * @file 登入狀態 store
 * @description 保存管理者 token、個人資料與權限；token 同步寫入 localStorage，重新整理後仍維持登入。
 */
import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { STORAGE_KEYS } from '@/constants/storage'

export const useAuthStore = defineStore('auth', () => {
  /** 存取權杖（access token） */
  const token = ref(localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN) ?? '')
  /** 目前登入的管理者資料（GET /auth/me 的回應），未載入時為 null */
  const profile = ref(null)

  /** 是否已登入 */
  const isLoggedIn = computed(() => Boolean(token.value))

  /**
   * 檢查是否擁有指定權限
   * @param {string} code - 權限代碼，格式「模組:動作」，例：invoice:review
   * @returns {boolean}
   */
  function hasPermission(code) {
    return profile.value?.permissions?.includes(code) ?? false
  }

  /**
   * 設定 token（登入成功後呼叫）
   * @param {string} value - 後端回傳的 access token
   */
  function setToken(value) {
    token.value = value
    localStorage.setItem(STORAGE_KEYS.ACCESS_TOKEN, value)
  }

  /** 登出：清除 token 與個人資料 */
  function logout() {
    token.value = ''
    profile.value = null
    localStorage.removeItem(STORAGE_KEYS.ACCESS_TOKEN)
  }

  // TODO(JerryChien): fetchProfile() 呼叫 api/modules/auth.js 的 fetchMe() 載入個人資料與權限

  return { token, profile, isLoggedIn, hasPermission, setToken, logout }
})
