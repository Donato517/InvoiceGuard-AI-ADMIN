/**
 * 全頁「處理中」遮罩管理（main.js、invoice-correct.js 共用）。
 *
 * 遮罩由「擁有者」決定：每個需要遮罩的動作用 acquire(訊息) 取得自己的 Token，完成時呼叫回傳的 release()；
 * 只要還有任何擁有者，遮罩就維持顯示，全部釋放後才移除。每個 release 只釋放自己的 Token，重複呼叫無效，
 * 所以結束事件重複、或別的請求先結束，都不會關掉仍在進行中的請求的遮罩。
 *
 * - acquire('')（空訊息）代表「不需要遮罩」，回傳不做事的 release，不影響其他擁有者。
 * - clearAll() 只用在 pageshow（從上一頁快取還原）：清掉前一個畫面留下的所有擁有者；
 *   舊的 release 之後被呼叫也只是刪除不存在的 Token，不會影響新的請求。
 * - 同時有多個擁有者時，畫面顯示最後取得者的訊息。
 *
 * 對話框：遮罩掛在開啟中的 <dialog>（showModal 的最上層）裡才看得到；對話框被開啟或關閉（Escape、按鈕、
 * JavaScript close()）時，只要還有擁有者就重新安置遮罩——關閉時移回 body 繼續蓋住頁面，之後才開啟的對話框則把遮罩移進去。
 * 不阻止對話框關閉：Escape 的 cancel 事件第二次按下時不可取消（瀏覽器仍會關閉），所以改成讓遮罩跟著保護，而不是擋住關閉。
 *
 * 換頁型遮罩（requestNavigation）：側邊欄連結、一般表單送出會換頁。支援 Navigation API（Chromium）時，
 * 等 `navigate` 事件確認導覽真的開始才顯示（新分頁、被取消的點擊、同頁錨點、下載連結都不會開始導覽），
 * 並在 `navigateerror`（使用者按停止、導覽失敗或被中止）時釋放，不使用固定秒數；沒有 Navigation API 時退回
 * 「確認未被取消就立即顯示」，並讓 Escape（瀏覽器的停止載入鍵）釋放。回傳下載檔案、不換頁的表單不會觸發
 * navigateerror（實測），仍要加 data-busy=""。
 *
 * 按鈕載入（buttonLoading）：模式 A，把送出鈕轉成「轉圈＋訊息」並停用（deferred 停用，保留 name/value），
 * 結束或換頁被取消時還原；keepContent 只停用並標示 aria-busy，不改內容（部門啟停開關、徽章這類小型切換）。
 *
 * 以 <script defer> 載入，需排在 main.js 之前（templates/base.html）；也能被 Node 載入做單元測試。
 */
;(function (root) {
  'use strict'

  const owners = new Map() // Token（每次 acquire 新建，唯一）→ 訊息，依取得順序

  function overlayParent() {
    // 開啟中的 <dialog> 位於最上層，遮罩要放進對話框裡才看得到
    return document.querySelector('dialog[open]') || document.body
  }

  function render() {
    let overlay = document.getElementById('busy-overlay')
    if (owners.size === 0) {
      overlay?.remove()
      return
    }
    if (!overlay) {
      overlay = document.createElement('div')
      overlay.id = 'busy-overlay'
      overlay.className = 'busy-overlay'
      overlay.setAttribute('role', 'status')
      overlay.setAttribute('aria-live', 'assertive')
      overlay.innerHTML =
        '<div class="busy-overlay__box"><span class="busy-overlay__spinner"></span><span class="busy-overlay__text"></span></div>'
    }
    const parent = overlayParent()
    if (overlay.parentNode !== parent) {
      parent.append(overlay)
    }
    const messages = Array.from(owners.values())
    overlay.querySelector('.busy-overlay__text').textContent = messages[messages.length - 1]
  }

  const noop = () => {}

  /**
   * 取得一個遮罩擁有者。
   * @param {string} message 顯示的訊息；空字串代表不需要遮罩。
   * @returns {() => void} release：釋放這個擁有者（只能釋放一次，重複呼叫無效）。
   */
  function acquire(message) {
    if (!message) {
      return noop
    }
    const token = Symbol('busy')
    owners.set(token, String(message))
    render()
    return () => {
      if (owners.delete(token)) {
        render()
      }
    }
  }

  /** 清空所有擁有者（只給 pageshow 使用）。 */
  function clearAll() {
    owners.clear()
    render()
    endCurrentNavigation() // 同時還原換頁型動作附帶的按鈕載入狀態與表單防重旗標
  }

  /** 目前仍有效的擁有者數量（測試用）。 */
  function count() {
    return owners.size
  }

  // <dialog> 開啟／關閉會增減它的 open 屬性（showModal、show、close、Escape 都一樣）；只在仍有擁有者時才重新安置遮罩
  if (typeof MutationObserver === 'function' && typeof document !== 'undefined' && document.documentElement) {
    new MutationObserver((records) => {
      if (owners.size > 0 && records.some((record) => record.target && record.target.tagName === 'DIALOG')) {
        render()
      }
    }).observe(document.documentElement, { attributes: true, attributeFilter: ['open'], subtree: true })
  }

  // ---- 換頁型遮罩與按鈕載入 ----
  const navigation =
    root.navigation && typeof root.navigation.addEventListener === 'function' ? root.navigation : null
  let endCurrentNavigation = noop // 釋放目前換頁型遮罩與其附帶的還原動作
  let pendingNavigation = null // 已提出、等 navigate 事件確認的開始動作（連結）
  let navigationStarted = false // 目前這次換頁是否已看到屬於它的 navigate 事件

  function startNavigation(start, started) {
    endCurrentNavigation()
    navigationStarted = started
    const cleanup = start()
    let ended = false
    endCurrentNavigation = () => {
      if (ended) return
      ended = true
      endCurrentNavigation = noop
      if (typeof cleanup === 'function') cleanup()
    }
  }

  /**
   * 提出一次「可能換頁」的動作。start() 負責顯示遮罩／按鈕載入，回傳 cleanup（導覽被取消或頁面還原時呼叫）。
   * @param {() => (void | (() => void))} start 開始動作；回傳 undefined 代表這次不算導覽（例如點擊已被取消）。
   * @param {{ gate?: 'navigate' | 'task', isCanceled?: () => boolean }} [options]
   *   gate 'navigate'（預設，用於連結）：連結的導覽在點擊的同一個工作回合就會觸發 navigate 事件，等到事件才開始；
   *   gate 'task'（用於表單）：瀏覽器把表單送出排到之後的工作回合才導覽，所以改成等目前事件處理完、確認沒被取消
   *   （isCanceled）就開始，並忽略「屬於上一次導覽」的 navigateerror，直到看到這次的 navigate 事件為止。
   */
  function requestNavigation(start, options = {}) {
    const { gate = 'navigate', isCanceled } = options
    if (!navigation) {
      // 沒有 Navigation API：表單立即開始（維持原本行為）；連結延到事件處理完再開始，start() 自己檢查有沒有被取消
      if (gate === 'navigate') setTimeout(() => startNavigation(start, true), 0)
      else startNavigation(start, true)
      return
    }
    if (gate === 'task') {
      setTimeout(() => {
        if (!(isCanceled && isCanceled())) startNavigation(start, false)
      }, 0)
      return
    }
    const mine = { start }
    pendingNavigation = mine
    // navigate 事件與點擊在同一個工作回合觸發；回合結束還沒等到，就不是一次導覽（新分頁、被取消、同頁錨點）
    setTimeout(() => {
      if (pendingNavigation === mine) pendingNavigation = null
    }, 0)
  }

  if (navigation) {
    navigation.addEventListener('navigate', (event) => {
      const pending = pendingNavigation
      if (pending) {
        pendingNavigation = null
        if (event.defaultPrevented || event.hashChange || event.downloadRequest != null) return
        startNavigation(pending.start, true)
        return
      }
      if (!event.hashChange && event.downloadRequest == null) navigationStarted = true // 表單這次導覽開始了
    })
    // 使用者按停止、導覽失敗或被中止：釋放遮罩與按鈕載入狀態（回傳下載檔案時不會觸發，見檔頭說明）。
    // 上一次導覽的錯誤事件會在新導覽開始時才補發，所以要等到屬於這次的 navigate 事件之後才算數
    navigation.addEventListener('navigateerror', () => {
      if (navigationStarted) endCurrentNavigation()
    })
  } else if (typeof document !== 'undefined' && typeof document.addEventListener === 'function') {
    // 沒有 Navigation API：Escape 是瀏覽器停止載入鍵，盡力釋放換頁型遮罩
    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') endCurrentNavigation()
    })
  }

  const buttonStates = new WeakMap() // 按鈕 → 還原資料
  const escapeHtml = (text) =>
    String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])

  /**
   * 模式 A：把按鈕轉成載入狀態並停用。回傳 restore（只還原一次）。
   * 停用會延到目前事件處理完才做，否則送出鈕的 name／value 與 formaction 會因為已停用而不被送出。
   */
  function buttonLoading(button, options = {}) {
    if (!button || buttonStates.has(button)) return noop
    const { text = '處理中…', keepContent = false } = options
    // hx-disabled-elt 在 beforeSend 之前就停用了按鈕（並加 data-disabled-by-htmx，結束時由 HTMX 自己移除）；
    // 那不是按鈕原本的狀態，不能記起來還原，否則請求結束後按鈕會一直停用
    const disabledByHtmx = button.hasAttribute('data-disabled-by-htmx')
    const state = { html: button.innerHTML, disabled: button.disabled && !disabledByHtmx, active: true }
    buttonStates.set(button, state)
    button.classList.add('btn--loading')
    button.setAttribute('aria-busy', 'true')
    if (!keepContent) {
      button.innerHTML = '<span class="btn__spinner" aria-hidden="true"></span>' + escapeHtml(text)
    }
    setTimeout(() => {
      if (state.active) button.disabled = true
    }, 0)
    return () => {
      if (!state.active) return
      state.active = false
      buttonStates.delete(button)
      if (!keepContent) button.innerHTML = state.html
      button.classList.remove('btn--loading')
      button.removeAttribute('aria-busy')
      button.disabled = state.disabled
    }
  }

  /** 按鈕目前是否在載入狀態（用來擋同一個動作的重複送出）。 */
  function isButtonLoading(button) {
    return Boolean(button) && buttonStates.has(button)
  }

  const api = Object.freeze({ acquire, clearAll, count, requestNavigation, buttonLoading, isButtonLoading })
  root.InvoiceGuardBusy = api
  // 其他腳本（例如以 fetch 送出的 invoice-correct.js）：const release = window.invoiceGuardBusy('訊息'); … finally { release() }
  root.invoiceGuardBusy = acquire
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api
  }
})(typeof window !== 'undefined' ? window : globalThis)
