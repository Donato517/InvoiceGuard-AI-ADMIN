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

  const api = Object.freeze({ acquire, clearAll, count })
  root.InvoiceGuardBusy = api
  // 其他腳本（例如以 fetch 送出的 invoice-correct.js）：const release = window.invoiceGuardBusy('訊息'); … finally { release() }
  root.invoiceGuardBusy = acquire
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api
  }
})(typeof window !== 'undefined' ? window : globalThis)
