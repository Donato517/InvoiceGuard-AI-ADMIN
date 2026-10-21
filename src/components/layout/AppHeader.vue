<!--
  @component AppHeader
  @description 後台頁首：側欄收合按鈕、目前頁面標題、登出。
-->
<script setup>
import { useRoute, useRouter } from 'vue-router'

import { useAppStore } from '@/stores/app'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const appStore = useAppStore()
const authStore = useAuthStore()

/** 登出並回到登入頁 */
function handleLogout() {
  authStore.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <header class="app-header">
    <button type="button" class="app-header__toggle" @click="appStore.toggleSidebar">☰</button>
    <h1 class="app-header__title">{{ route.meta.title }}</h1>
    <button type="button" @click="handleLogout">登出</button>
  </header>
</template>

<style scoped>
.app-header {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  height: var(--header-height);
  padding-inline: var(--space-6);
  background-color: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
}

.app-header__toggle {
  border: none;
  background: none;
  font-size: var(--font-size-lg);
}

.app-header__title {
  margin-right: auto;
  font-size: var(--font-size-lg);
}
</style>
