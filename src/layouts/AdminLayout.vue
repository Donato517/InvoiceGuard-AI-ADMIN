<!--
  @component AdminLayout
  @description 後台預設版型：左側選單 + 頁首 + 內容。路由未指定 meta.layout 時使用。
  @slot default - 頁面內容
-->
<script setup>
import AppHeader from '@/components/layout/AppHeader.vue'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import { useAppStore } from '@/stores/app'

const appStore = useAppStore()
</script>

<template>
  <div class="admin-layout" :class="{ 'admin-layout--collapsed': appStore.sidebarCollapsed }">
    <AppSidebar class="admin-layout__sidebar" :collapsed="appStore.sidebarCollapsed" />
    <div class="admin-layout__main">
      <AppHeader />
      <main class="admin-layout__content">
        <slot />
      </main>
    </div>
  </div>
</template>

<style scoped>
.admin-layout {
  display: grid;
  grid-template-columns: var(--sidebar-width) 1fr;
  min-height: 100vh;
}

.admin-layout--collapsed {
  grid-template-columns: var(--sidebar-width-collapsed) 1fr;
}

.admin-layout__main {
  display: flex;
  flex-direction: column;
  min-width: 0; /* 避免表格等寬內容撐破 grid 欄寬 */
}

.admin-layout__content {
  flex: 1;
  padding: var(--space-6);
}
</style>
