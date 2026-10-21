<!--
  @component NotFoundView
  @description 404 頁面：所有未定義的路徑都會導到這裡。
  @route /:pathMatch(.*)* （name: not-found，layout: blank，不需登入）
-->
<template>
  <section class="not-found-view">
    <h1>找不到頁面</h1>
    <RouterLink :to="{ name: 'dashboard' }">回儀表板</RouterLink>
  </section>
</template>

<style scoped>
.not-found-view {
  display: grid;
  gap: var(--space-4);
  text-align: center;
}
</style>
