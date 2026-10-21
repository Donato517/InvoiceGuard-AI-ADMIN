/**
 * @file 使用者管理 API
 * @description 後台使用者 API：列表、啟用 / 停用。對應後端：/api/v1/admin/users
 */
import http from '@/api/http'

/**
 * 取得使用者列表
 * @param {Object} [params] - 查詢條件
 * @param {number} [params.page=1] - 頁碼
 * @param {string} [params.keyword] - 搜尋 email 或名稱
 * @returns {Promise<{ items: Object[], total: number, page: number, page_size: number }>}
 */
export function fetchUsers(params) {
  return http.get('/admin/users', { params })
}

/**
 * 啟用 / 停用使用者帳號
 * @param {number|string} id - 使用者 ID
 * @param {boolean} isActive - true 啟用、false 停用
 * @returns {Promise<Object>} 更新後的使用者資料
 */
export function updateUserStatus(id, isActive) {
  return http.patch(`/admin/users/${id}/status`, { is_active: isActive })
}
