/**
 * @file 測試目標：src/utils/format.js
 */
import { describe, expect, it } from 'vitest'

import { formatCurrency, formatDate, formatDateTime } from '@/utils/format'

describe('formatCurrency', () => {
  it('加上千分位與幣別', () => {
    expect(formatCurrency('1234.5')).toBe('NT$ 1,234.5')
  })

  it('空值顯示 -', () => {
    expect(formatCurrency(null)).toBe('-')
  })
})

describe('formatDate / formatDateTime', () => {
  it('UTC 轉為台灣時間（跨日）', () => {
    expect(formatDate('2026-10-20T16:30:00Z')).toBe('2026/10/21')
    expect(formatDateTime('2026-10-20T16:30:00Z')).toBe('2026/10/21 00:30')
  })

  it('空值顯示 -', () => {
    expect(formatDate('')).toBe('-')
  })
})
