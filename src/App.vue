<!--
  @component App
  @description 根元件：依路由 meta.layout 套用對應版型，頁面內容由 RouterView 呈現。
-->
<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import AdminLayout from '@/layouts/AdminLayout.vue'
import BlankLayout from '@/layouts/BlankLayout.vue'

const LAYOUTS = { admin: AdminLayout, blank: BlankLayout }

const route = useRoute()

// 路由未指定 layout 時使用後台版型（側欄 + 頁首）
const currentLayout = computed(() => LAYOUTS[route.meta.layout] ?? AdminLayout)
</script>

<template>
  <component :is="currentLayout">
    <RouterView />
  </component>
</template>
