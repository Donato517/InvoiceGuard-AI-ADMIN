/**
 * @file 登入 API
 * @description 登入、登出、取得目前使用者。對應後端：/api/v1/auth
 */
import http from '@/api/http'

/**
 * 帳號密碼登入
 * @param {{ email: string, password: string }} payload - 登入資料
 * @returns {Promise<{ access_token: string, token_type: string, expires_in: number }>}
 */
export function login(payload) {
  return http.post('/auth/login', payload)
}

/**
 * 登出
 * @returns {Promise<void>}
 */
export function logout() {
  return http.post('/auth/logout')
}

/**
 * 取得目前登入的使用者資料
 * @returns {Promise<Object>}
 */
export function fetchMe() {
  return http.get('/auth/me')
}
