/**
 * @file HTTP 請求實例
 * @description 建立共用 axios 實例：設定 API 位址與逾時、自動附帶 token、統一錯誤格式。
 *   所有 API 呼叫都必須透過此實例，禁止在元件內直接使用 axios / fetch。
 *   後端錯誤格式：{ error: { code, message, details } }（見 API 專案 docs/API_CONVENTIONS.md）
 */
import axios from 'axios'

import { STORAGE_KEYS } from '@/constants/storage'

/**
 * 統一的 API 錯誤物件（回應攔截器 reject 的內容）
 * @typedef {Object} ApiError
 * @property {string} code - 錯誤代碼，例：NOT_FOUND、VALIDATION_ERROR、NETWORK_ERROR
 * @property {string} message - 可直接顯示給使用者的訊息
 * @property {*} [details] - 額外資訊（例：欄位錯誤清單）
 * @property {number} [status] - HTTP 狀態碼；網路錯誤時為 undefined
 */

const http = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL,
  timeout: 30000, // AI 分析可能較久，逾時設 30 秒
})

// 請求攔截：已登入時自動帶入 token
http.interceptors.request.use((config) => {
  const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN)
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

// 回應攔截：成功直接回傳 data；失敗轉為 ApiError
http.interceptors.response.use(
  (response) => response.data,
  (error) => {
    const body = error.response?.data?.error
    /** @type {ApiError} */
    const apiError = {
      code: body?.code ?? 'NETWORK_ERROR',
      message: body?.message ?? '網路連線異常，請稍後再試',
      details: body?.details,
      status: error.response?.status,
    }
    // TODO(JerryChien): status 401 時清除登入狀態並導向登入頁
    return Promise.reject(apiError)
  },
)

export default http
