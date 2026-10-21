/**
 * @file 發票相關常數
 * @description 發票狀態列舉與顯示文字。
 *   值必須與 API 專案 app/db/models/invoice.py 的 InvoiceStatus 一致，修改時兩邊同步。
 */

/** 發票狀態 */
export const INVOICE_STATUS = Object.freeze({
  PENDING: 'pending',
  ANALYZING: 'analyzing',
  PASSED: 'passed',
  FLAGGED: 'flagged',
  REJECTED: 'rejected',
})

/** 發票狀態顯示文字 */
export const INVOICE_STATUS_LABEL = Object.freeze({
  [INVOICE_STATUS.PENDING]: '待分析',
  [INVOICE_STATUS.ANALYZING]: '分析中',
  [INVOICE_STATUS.PASSED]: '通過',
  [INVOICE_STATUS.FLAGGED]: '有風險',
  [INVOICE_STATUS.REJECTED]: '異常',
})
