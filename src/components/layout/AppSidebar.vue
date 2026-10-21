<!--
  @component AppSidebar
  @description 後台左側選單，項目來自 constants/menu.js。
-->
<script setup>
import { MENU_ITEMS } from '@/constants/menu'

defineProps({
  /** 是否收合（收合時只顯示 Logo 縮寫） */
  collapsed: { type: Boolean, default: false },
})
</script>

<template>
  <aside class="app-sidebar">
    <div class="app-sidebar__logo">{{ collapsed ? 'IG' : 'InvoiceGuard' }}</div>
    <nav v-show="!collapsed">
      <RouterLink
        v-for="item in MENU_ITEMS"
        :key="item.routeName"
        :to="{ name: item.routeName }"
        class="app-sidebar__link"
        active-class="app-sidebar__link--active"
      >
        {{ item.title }}
      </RouterLink>
    </nav>
  </aside>
</template>

<style scoped>
.app-sidebar {
  color: var(--color-sidebar-text);
  background-color: var(--color-sidebar-bg);
}

.app-sidebar__logo {
  display: flex;
  align-items: center;
  height: var(--header-height);
  padding-inline: var(--space-4);
  font-weight: 700;
  color: var(--color-bg);
}

.app-sidebar__link {
  display: block;
  padding: var(--space-3) var(--space-4);
  color: inherit;
}

.app-sidebar__link:hover,
.app-sidebar__link--active {
  color: var(--color-bg);
  background-color: var(--color-primary);
}
</style>
