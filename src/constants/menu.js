/**
 * @file 側欄選單
 * @description 後台側欄的選單項目；routeName 對應 src/router/index.js 的路由 name。
 *   新增頁面時同步更新此處。
 */

/**
 * @typedef {Object} MenuItem
 * @property {string} title - 顯示文字
 * @property {string} routeName - 對應的路由 name
 */

/** @type {ReadonlyArray<MenuItem>} */
export const MENU_ITEMS = Object.freeze([
  { title: '儀表板', routeName: 'dashboard' },
  { title: '發票審核', routeName: 'invoice-review-list' },
  { title: '使用者管理', routeName: 'user-list' },
  { title: '排程紀錄', routeName: 'job-run-list' },
  { title: '稽核紀錄', routeName: 'audit-log-list' },
])
