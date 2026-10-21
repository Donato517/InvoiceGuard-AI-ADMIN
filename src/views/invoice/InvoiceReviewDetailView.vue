<!--
  @component InvoiceReviewDetailView
  @description 發票審核詳情：原始檔、辨識結果、AI 判斷理由，並提供核准 / 駁回操作。
  @route /invoices/:id （name: invoice-review-detail）
-->
<script setup>
defineProps({
  /** 發票 ID（由路由參數 :id 傳入） */
  id: { type: String, required: true },
})

// TODO(JerryChien): fetchInvoice(id) 載入資料；核准 / 駁回按鈕加 v-permission="'invoice:review'"
</script>

<template>
  <section class="invoice-review-detail-view">
    <p>發票 #{{ id }} 審核詳情建置中</p>
  </section>
</template>
