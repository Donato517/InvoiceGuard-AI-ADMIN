/**
 * @file 顯示格式化工具
 * @description 金額、日期等畫面顯示用的純函式（不得有副作用、不得呼叫 API）。
 *   API 回傳的時間為 UTC，一律在此轉為台灣時間顯示。
 */

const amountFormatter = new Intl.NumberFormat('zh-TW', { maximumFractionDigits: 2 })

const taipeiFormatter = new Intl.DateTimeFormat('zh-TW', {
  timeZone: 'Asia/Taipei',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

/**
 * 將時間拆成台灣時區的年月日時分
 * @param {string|Date} value - ISO 8601 字串或 Date
 * @returns {Record<string, string>} 例：{ year: '2026', month: '10', day: '21', hour: '08', minute: '30' }
 */
function toTaipeiParts(value) {
  const parts = taipeiFormatter.formatToParts(new Date(value))
  return Object.fromEntries(parts.map((part) => [part.type, part.value]))
}

/**
 * 格式化金額（API 金額為字串，避免浮點誤差）
 * @param {string|number|null} value - 金額
 * @returns {string} 例：'1234.5' → 'NT$ 1,234.5'；空值回傳 '-'
 */
export function formatCurrency(value) {
  if (value === null || value === undefined || value === '') return '-'
  return `NT$ ${amountFormatter.format(Number(value))}`
}

/**
 * 格式化日期（台灣時間）
 * @param {string|Date|null} value - ISO 8601 字串或 Date
 * @returns {string} 例：'2026-10-20T16:30:00Z' → '2026/10/21'；空值回傳 '-'
 */
export function formatDate(value) {
  if (!value) return '-'
  const { year, month, day } = toTaipeiParts(value)
  return `${year}/${month}/${day}`
}

/**
 * 格式化日期時間（台灣時間，24 小時制）
 * @param {string|Date|null} value - ISO 8601 字串或 Date
 * @returns {string} 例：'2026-10-20T16:30:00Z' → '2026/10/21 00:30'；空值回傳 '-'
 */
export function formatDateTime(value) {
  if (!value) return '-'
  const { year, month, day, hour, minute } = toTaipeiParts(value)
  return `${year}/${month}/${day} ${hour}:${minute}`
}
