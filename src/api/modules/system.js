/**
 * @file 系統紀錄 API
 * @description 後台系統 API：排程執行紀錄、操作稽核紀錄。
 *   對應後端：/api/v1/admin/job-runs、/api/v1/admin/audit-logs
 */
import http from '@/api/http'

/**
 * 取得排程執行紀錄
 * @param {Object} [params] - 查詢條件
 * @param {string} [params.job_name] - 依工作名稱篩選，例：sync_einvoice
 * @param {number} [params.page=1] - 頁碼
 * @returns {Promise<{ items: Object[], total: number, page: number, page_size: number }>}
 */
export function fetchJobRuns(params) {
  return http.get('/admin/job-runs', { params })
}

/**
 * 取得操作稽核紀錄
 * @param {Object} [params] - 查詢條件
 * @param {string} [params.action] - 依動作代碼篩選，例：invoice.review
 * @param {number} [params.page=1] - 頁碼
 * @returns {Promise<{ items: Object[], total: number, page: number, page_size: number }>}
 */
export function fetchAuditLogs(params) {
  return http.get('/admin/audit-logs', { params })
}
