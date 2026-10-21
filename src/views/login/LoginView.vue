<!--
  @component LoginView
  @description 後台登入頁：管理者帳號密碼登入，成功後導回 query.redirect 指定的頁面（預設儀表板）。
  @route /login （name: login，layout: blank，不需登入）
-->
<script setup>
// TODO(JerryChien): 表單驗證 → login() → authStore.setToken() → fetchProfile() → 導頁
</script>

<template>
  <section class="login-view">
    <h1>InvoiceGuard 後台登入</h1>
  </section>
</template>

<style scoped>
.login-view {
  width: 100%;
  max-width: 400px;
}
</style>
