/**
 * @file 發票流程步驟 1：多頁 PDF 先挑出三聯式發票頁再送出辨識
 * @description 只在新上傳（表單 data-pdf-pick="true"）時作用；既有案件補附件仍上傳原檔。
 *   - 按「上傳並開始辨識」且選的是 PDF：攔下送出 → 按鈕改「辨識中…」並停用、蓋上全頁遮罩（busy.js，文字隨進度更新）
 *     → pdf-invoice-page.js 找出發票頁（第一聯擋下、多頁可用時跳選擇視窗；視窗出現前先拿掉遮罩，避免蓋住選項）
 *     → 遮罩顯示「已找到第 X 頁…正在上傳辨識」→ 把檔案換成該頁 JPEG 後送出，遮罩維持到進入步驟 3。
 *   - 失敗或取消時拿掉遮罩，在上傳框下方顯示原因並恢復按鈕，不跳警示。
 *   - 選的是影像，或挑頁模組沒有載入、瀏覽器不支援 DataTransfer 時不攔截，照原本送出原檔（API 只接受單頁 PDF），只蓋全頁遮罩。
 *   - 以捕獲階段監聽 submit，先於 main.js 的按鈕載入處理。
 */
;(function () {
  'use strict'

  const FOUND_DELAY_MS = 900
  const IDLE_TEXT = '上傳並開始辨識 →'

  function isPdf(file) {
    return file.type === 'application/pdf' || /\.pdf$/i.test(file.name || '')
  }

  function uploadButton() {
    return (
      document.getElementById('btn-flow-upload') ||
      document.querySelector('button[form="flow-upload-form"]')
    )
  }

  function setBusy(busy) {
    const button = uploadButton()
    if (!button) return
    if (busy) {
      button.classList.add('btn--loading')
      button.setAttribute('aria-busy', 'true')
      button.innerHTML = '<span class="btn__spinner" aria-hidden="true"></span>辨識中…'
      button.disabled = true
    } else {
      button.classList.remove('btn--loading')
      button.removeAttribute('aria-busy')
      button.textContent = IDLE_TEXT
      button.disabled = false
    }
  }

  // 全頁遮罩：每次換訊息就換一個擁有者（busy.js 一次只顯示最後的訊息）；空訊息代表先拿掉遮罩
  let releaseOverlay = function () {}
  function overlay(message) {
    releaseOverlay()
    releaseOverlay =
      message && typeof window.invoiceGuardBusy === 'function'
        ? window.invoiceGuardBusy(message)
        : function () {}
  }

  function showStatus(message, tone) {
    const status = document.getElementById('pdf-pick-status')
    if (!status) return
    status.hidden = !message
    status.textContent = message || ''
    status.classList.toggle('pdf-pick-status--error', tone === 'error')
    status.classList.toggle('pdf-pick-status--found', tone === 'found')
  }

  document.addEventListener(
    'submit',
    async function (event) {
      const form = event.target
      if (!form || form.id !== 'flow-upload-form' || form.dataset.pdfPick !== 'true') return
      const input = document.getElementById('invoice-file')
      const file = input && input.files && input.files[0]
      if (!file) return
      if (!isPdf(file) || !window.InvoicePdfPage || typeof DataTransfer !== 'function') {
        // 影像（或無法挑頁時的原檔）照原本送出，同樣蓋上全頁遮罩直到進入步驟 3
        overlay('正在上傳並辨識發票…')
        return
      }
      event.preventDefault()
      event.stopImmediatePropagation()
      if (form.dataset.pdfBusy === 'true') return
      form.dataset.pdfBusy = 'true'
      setBusy(true)
      showStatus('')
      overlay('正在讀取 PDF…')
      try {
        const found = await window.InvoicePdfPage.find(file, {
          onProgress: overlay // 進度文字顯示在遮罩上；選擇視窗出現前會收到空訊息而拿掉遮罩
        })
        if (!found) {
          overlay('')
          setBusy(false)
          return
        }
        const transfer = new DataTransfer()
        transfer.items.add(new File([found.blob], found.name, { type: 'image/jpeg' }))
        input.files = transfer.files
        overlay(found.message + '，正在上傳辨識…')
        showStatus(found.message, 'found')
        await new Promise(function (resolve) {
          setTimeout(resolve, FOUND_DELAY_MS)
        })
        form.submit() // 不再觸發 submit 事件；按鈕維持「辨識中…」直到換頁
      } catch (error) {
        overlay('')
        showStatus((error && error.message) || 'PDF 處理失敗，請改用其他檔案', 'error')
        setBusy(false)
      } finally {
        delete form.dataset.pdfBusy
      }
    },
    true
  )

  // 從下一頁按上一頁回來時，清掉上一次的狀態文字
  window.addEventListener('pageshow', function () {
    showStatus('')
  })
})()
