<!--
  @component BlankLayout
  @description 空白版型：無頁首頁尾，用於登入頁、錯誤頁（meta.layout = 'blank'）。
  @slot default - 頁面內容
-->
<template>
  <main class="blank-layout">
    <slot />
  </main>
</template>

<style scoped>
.blank-layout {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: var(--space-4);
}
</style>
