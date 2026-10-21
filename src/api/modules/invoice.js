/**
 * @file 發票審核 API
 * @description 後台發票 API：全部發票列表、詳情、人工複核。對應後端：/api/v1/admin/invoices
 */
import http from '@/api/http'

/**
 * 取得發票列表（全部使用者）
 * @param {Object} [params] - 查詢條件
 * @param {number} [params.page=1] - 頁碼
 * @param {number} [params.page_size=20] - 每頁筆數（上限 100）
 * @param {string} [params.status] - 依狀態篩選，值見 constants/invoice.js
 * @returns {Promise<{ items: Object[], total: number, page: number, page_size: number }>}
 */
export function fetchInvoices(params) {
  return http.get('/admin/invoices', { params })
}

/**
 * 取得發票詳情
 * @param {number|string} id - 發票 ID
 * @returns {Promise<Object>}
 */
export function fetchInvoice(id) {
  return http.get(`/admin/invoices/${id}`)
}

/**
 * 人工複核發票
 * @param {number|string} id - 發票 ID
 * @param {{ decision: 'passed'|'rejected', comment?: string }} payload - 複核結果與說明
 * @returns {Promise<Object>} 更新後的發票資料
 */
export function reviewInvoice(id, payload) {
  return http.post(`/admin/invoices/${id}/review`, payload)
}
