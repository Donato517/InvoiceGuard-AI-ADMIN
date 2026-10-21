/**
 * @file 介面狀態 store
 * @description 後台版面相關的全域狀態（側欄收合）。
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAppStore = defineStore('app', () => {
  /** 側欄是否收合 */
  const sidebarCollapsed = ref(false)

  /** 切換側欄收合狀態 */
  function toggleSidebar() {
    sidebarCollapsed.value = !sidebarCollapsed.value
  }

  return { sidebarCollapsed, toggleSidebar }
})
