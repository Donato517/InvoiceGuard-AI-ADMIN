<!--
  @component ForbiddenView
  @description 403 頁面：已登入但沒有該頁面的存取權限。
  @route /403 （name: forbidden，layout: blank，不需登入）
-->
<template>
  <section class="forbidden-view">
    <h1>沒有權限</h1>
    <p>您的帳號沒有此頁面的存取權限，請聯絡系統管理員。</p>
    <RouterLink :to="{ name: 'dashboard' }">回儀表板</RouterLink>
  </section>
</template>

<style scoped>
.forbidden-view {
  display: grid;
  gap: var(--space-4);
  text-align: center;
}
</style>
