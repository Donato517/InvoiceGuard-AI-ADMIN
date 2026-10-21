/**
 * @file 瀏覽器儲存鍵名
 * @description 集中管理 localStorage / sessionStorage 的鍵名，避免字串散落各處。
 *   一律加上 ig_admin_ 前綴，與前台區隔。
 */
export const STORAGE_KEYS = Object.freeze({
  ACCESS_TOKEN: 'ig_admin_access_token',
})
