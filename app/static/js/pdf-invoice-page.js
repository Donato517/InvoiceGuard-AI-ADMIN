/**
 * @file 多頁 PDF 找發票頁（電腦上傳頁）
 * @description 選了 PDF 後在瀏覽器完成：前置過濾 → 每頁轉縮圖 → 分批請 API 判斷是不是三聯式發票與聯別 → 挑出那一頁
 *   轉成 2000px JPEG，交回上傳頁走和手機拍照相同的辨識流程。
 *   - 整份 PDF 不上傳；只送縮圖（每批最多 9 張）與最後挑出的那一頁。規格比照 RAG 文件：4 MB、45 頁以內。
 *   - 第一聯（存根聯）不能報帳：只找到第一聯時擋下；聯別看不清楚時不擋，交給使用者確認。
 *   - 有多頁可用（例如第二、三聯都在）時跳出選擇視窗，預設選第二聯（扣抵聯）。
 *   - 挑出的頁面會裁掉四周空白（例如 A4 掃描只有上半部是發票），核對畫面與辨識都只看發票本身。
 *   - pdf.js（vendor/pdfjs，Apache-2.0）在第一次選 PDF 時才載入；網址由 script 標籤的 data-* 屬性提供。
 * @exports window.InvoicePdfPage.find(file, { onProgress, isCurrent }) → Promise<{ blob, dataUrl, name, page, pageCount, copy, message } | null>
 *   前台與 ADMIN 使用同一份檔案（內容須相同）；本站的轉送路由都是 POST /invoices/pdf-pages/classify。
 *   找不到或被擋下時丟出 Error（message 可直接顯示）；使用者在選擇視窗按取消時回傳 null。
 */
(function () {
  'use strict';

  const MAX_PDF_BYTES = 4 * 1024 * 1024; // 與 RAG 文件上傳相同
  const MAX_PAGES = 45; // 與 RAG 文件相同
  const BATCH_SIZE = 9; // 與 API 每次上限相同（3×3 總覽圖）
  const THUMB_SIDE = 800;
  const THUMB_QUALITY = 0.75;
  const FULL_SIDE = 2000; // 與拍照上傳縮圖的最長邊相同
  const RENDER_SIDE = 3000; // 原解析度先畫大一點，裁掉空白後再縮到 FULL_SIDE，發票本身的解析度較高
  const INK_LEVEL = 200; // 亮度低於此值視為有內容（掃描的灰底雜點通常更亮）
  const TRIM_MARGIN = 0.02; // 裁切後保留的邊距（佔裁切後寬高的比例）
  const MAIN_BLOCK_SHARE = 0.6; // 內容最多的一段至少要佔全部內容的比例，才只保留這一段
  const MAX_IMAGE_BYTES = 1400 * 1024; // API 單張上限 1.5 MB，保留餘裕
  const MAX_CONFIRM = 4; // 縮圖看不清聯別時，最多再用原解析度確認幾頁
  const COPY_LABELS = { 1: '第一聯（存根聯）', 2: '第二聯（扣抵聯）', 3: '第三聯（收執聯）' };

  const script = document.currentScript;
  const config = {
    lib: script && script.dataset.pdfjs,
    worker: script && script.dataset.pdfjsWorker,
    cmaps: script && script.dataset.pdfjsCmaps,
  };
  let pdfjsPromise = null;

  function loadPdfjs() {
    if (!pdfjsPromise) {
      pdfjsPromise = import(config.lib).then(function (lib) {
        lib.GlobalWorkerOptions.workerSrc = config.worker;
        return lib;
      });
      pdfjsPromise.catch(function () {
        pdfjsPromise = null; // 載入失敗時下次重試
      });
    }
    return pdfjsPromise;
  }

  function fail(message) {
    return new Error(message);
  }

  function copyText(copy) {
    return COPY_LABELS[copy] || '聯別無法辨識';
  }

  async function readHeader(file) {
    const head = new Uint8Array(await file.slice(0, 5).arrayBuffer());
    return String.fromCharCode.apply(null, head);
  }

  // 有內容的列（或行）依空白間隔分段，回傳深色像素最多的一段 [起, 迄]；沒有內容回傳 null。
  // 最多的一段不到全部內容的 MAIN_BLOCK_SHARE（例如發票旁邊還有大段文字）時，不冒險只留一段，改回傳全部內容的範圍
  function largestRun(counts, minInk, minGap) {
    let best = null;
    let current = null;
    let gap = 0;
    let total = 0;
    let first = -1;
    let last = -1;
    for (let i = 0; i < counts.length; i += 1) {
      if (counts[i] >= minInk) {
        if (current && gap < minGap) {
          current.end = i;
        } else {
          current = { start: i, end: i, ink: 0 };
        }
        current.ink += counts[i];
        total += counts[i];
        if (first < 0) first = i;
        last = i;
        gap = 0;
        if (!best || current.ink > best.ink) best = current;
      } else {
        gap += 1;
      }
    }
    if (!best) return null;
    return best.ink >= total * MAIN_BLOCK_SHARE ? [best.start, best.end] : [first, last];
  }

  /**
   * 找出影像中發票所在的範圍（裁掉四周空白）。深色像素過少的列／行視為空白（忽略零星雜點）；
   * 內容依大片空白分段後只保留內容最多的一段，例如 A4 上方的頁首文字、下方的頁碼不會被留下。
   * @param {Uint8ClampedArray} data RGBA 像素
   * @returns {{x: number, y: number, w: number, h: number} | null} 不需要裁切或整頁空白時回傳 null
   */
  function trimBounds(data, width, height) {
    const isInk = function (i) {
      return data[i] * 0.299 + data[i + 1] * 0.587 + data[i + 2] * 0.114 < INK_LEVEL;
    };
    const rows = new Uint32Array(height);
    for (let y = 0; y < height; y += 1) {
      for (let x = 0; x < width; x += 1) {
        if (isInk((y * width + x) * 4)) rows[y] += 1;
      }
    }
    const rowRun = largestRun(rows, Math.max(2, Math.floor(width * 0.004)), Math.ceil(height * 0.04));
    if (!rowRun) return null; // 整頁空白
    const cols = new Uint32Array(width);
    for (let y = rowRun[0]; y <= rowRun[1]; y += 1) {
      for (let x = 0; x < width; x += 1) {
        if (isInk((y * width + x) * 4)) cols[x] += 1;
      }
    }
    const span = rowRun[1] - rowRun[0] + 1;
    const colRun = largestRun(cols, Math.max(2, Math.floor(span * 0.004)), Math.ceil(width * 0.04));
    if (!colRun) return null;
    const [top, bottom] = rowRun;
    const [left, right] = colRun;
    const mx = Math.round((right - left + 1) * TRIM_MARGIN);
    const my = Math.round((bottom - top + 1) * TRIM_MARGIN);
    const x = Math.max(0, left - mx);
    const y = Math.max(0, top - my);
    const box = {
      x: x,
      y: y,
      w: Math.min(width, right + mx + 1) - x,
      h: Math.min(height, bottom + my + 1) - y,
    };
    if (box.w * box.h > width * height * 0.9) return null; // 幾乎沒有空白，不裁
    return box;
  }

  // 把一頁畫成 JPEG；最長邊 side，白底（PDF 透明背景不變黑）。trim 時先畫大、裁掉四周空白，再縮到 side 以內
  async function renderPage(doc, number, side, quality, trim) {
    const page = await doc.getPage(number);
    try {
      const base = page.getViewport({ scale: 1 });
      const drawSide = trim ? RENDER_SIDE : side;
      const viewport = page.getViewport({ scale: drawSide / Math.max(base.width, base.height) });
      let canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.floor(viewport.width));
      canvas.height = Math.max(1, Math.floor(viewport.height));
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      await page.render({ canvasContext: ctx, viewport: viewport }).promise;
      if (trim) canvas = cropAndFit(canvas, ctx, side);
      let q = quality;
      let blob = await toBlob(canvas, q);
      while (blob.size > MAX_IMAGE_BYTES && q > 0.45) {
        q -= 0.15;
        blob = await toBlob(canvas, q);
      }
      return blob;
    } finally {
      page.cleanup();
    }
  }

  // 裁掉四周空白並縮到最長邊 side 以內；瀏覽器不支援讀取像素時只縮放
  function cropAndFit(canvas, ctx, side) {
    let box = null;
    if (typeof ctx.getImageData === 'function') {
      box = trimBounds(ctx.getImageData(0, 0, canvas.width, canvas.height).data, canvas.width, canvas.height);
    }
    const src = box || { x: 0, y: 0, w: canvas.width, h: canvas.height };
    const scale = Math.min(1, side / Math.max(src.w, src.h));
    const out = document.createElement('canvas');
    out.width = Math.max(1, Math.round(src.w * scale));
    out.height = Math.max(1, Math.round(src.h * scale));
    const octx = out.getContext('2d');
    octx.fillStyle = '#fff';
    octx.fillRect(0, 0, out.width, out.height);
    if (typeof octx.drawImage === 'function') {
      octx.drawImage(canvas, src.x, src.y, src.w, src.h, 0, 0, out.width, out.height);
    }
    return out;
  }

  function toBlob(canvas, quality) {
    return new Promise(function (resolve, reject) {
      canvas.toBlob(function (blob) {
        if (blob) resolve(blob);
        else reject(fail('無法轉換 PDF 頁面，請改用其他檔案'));
      }, 'image/jpeg', quality);
    });
  }

  function blobToDataUrl(blob) {
    return new Promise(function (resolve, reject) {
      const reader = new FileReader();
      reader.onload = function () {
        resolve(reader.result);
      };
      reader.onerror = function () {
        reject(fail('無法讀取 PDF 頁面，請改用其他檔案'));
      };
      reader.readAsDataURL(blob);
    });
  }

  // 送一批頁面影像給本站轉 API；回傳與輸入同順序的判斷結果
  async function classify(blobs, pageNumbers) {
    const form = new FormData();
    blobs.forEach(function (blob, i) {
      form.append('files', blob, 'page_' + pageNumbers[i] + '.jpg');
    });
    let res;
    try {
      res = await fetch('/invoices/pdf-pages/classify', {
        method: 'POST',
        body: form,
        credentials: 'same-origin',
      });
    } catch (e) {
      throw fail('網路連線失敗，請稍後再試');
    }
    if (res.redirected && /\/login/.test(res.url)) throw fail('登入已逾時，請重新登入');
    let data = null;
    try {
      data = await res.json();
    } catch (e) {
      data = null;
    }
    if (!res.ok || !data || !Array.isArray(data.pages)) {
      const message = data && data.error && data.error.message;
      throw fail(message || 'AI 無法判斷 PDF 的頁面，請稍後再試');
    }
    return pageNumbers.map(function (page, i) {
      const item = data.pages[i] || {};
      return { page: page, isInvoice: item.is_triplicate_invoice === true, copy: item.copy_no || null };
    });
  }

  // 多頁可用時讓使用者挑；回傳選中的候選或 null（取消）
  function choose(candidates, pageCount) {
    return new Promise(function (resolve) {
      const dialog = document.createElement('dialog');
      dialog.className = 'pdf-pick-dialog';
      dialog.setAttribute('aria-labelledby', 'pdf-pick-title');
      const title = document.createElement('h2');
      title.id = 'pdf-pick-title';
      title.textContent = '找到 ' + candidates.length + ' 頁三聯式發票，請選擇要上傳的那一頁';
      const desc = document.createElement('p');
      desc.className = 'muted';
      desc.textContent = '共 ' + pageCount + ' 頁；同一張發票的第二聯與第三聯擇一上傳即可，建議選第二聯（扣抵聯）。';
      const grid = document.createElement('div');
      grid.className = 'pdf-pick-dialog__grid';
      const urls = [];
      let preferred = null;
      candidates.forEach(function (c) {
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'pdf-pick-dialog__option';
        const img = document.createElement('img');
        const url = URL.createObjectURL(c.thumb);
        urls.push(url);
        img.src = url;
        img.alt = '第 ' + c.page + ' 頁縮圖';
        const caption = document.createElement('span');
        caption.textContent = '第 ' + c.page + ' 頁・' + copyText(c.copy);
        btn.append(img, caption);
        btn.onclick = function () {
          close(c);
        };
        grid.appendChild(btn);
        if (!preferred || (c.copy === 2 && preferred.copy !== 2)) preferred = { c: c, btn: btn };
      });
      const actions = document.createElement('div');
      actions.className = 'pdf-pick-dialog__actions';
      const cancel = document.createElement('button');
      cancel.type = 'button';
      cancel.className = 'btn btn--secondary';
      cancel.textContent = '取消';
      cancel.onclick = function () {
        close(null);
      };
      actions.appendChild(cancel);
      dialog.append(title, desc, grid, actions);
      dialog.addEventListener('cancel', function (e) {
        e.preventDefault();
        close(null);
      });
      let done = false;
      function close(value) {
        if (done) return;
        done = true;
        if (dialog.open) dialog.close();
        dialog.remove();
        urls.forEach(function (u) {
          URL.revokeObjectURL(u);
        });
        resolve(value);
      }
      document.body.appendChild(dialog);
      dialog.showModal();
      if (preferred) preferred.btn.focus();
    });
  }

  /**
   * 從 PDF 找出三聯式發票頁並轉成上傳用的 JPEG。
   * @param {File} file 使用者選的 PDF
   * @param {{onProgress?: function(string): void, isCurrent?: function(): boolean, pdfjs?: object}} [options]
   *   pdfjs 只供測試注入替身；正式頁面不傳，第一次使用時才載入 vendor/pdfjs。
   */
  async function find(file, options) {
    const opts = options || {};
    const progress = opts.onProgress || function () {};
    const isCurrent = opts.isCurrent || function () {
      return true;
    };

    // 1. 前置過濾：大小、檔頭、密碼、頁數
    if (!file || file.size === 0) throw fail('檔案是空的，請重新選擇');
    if (file.size > MAX_PDF_BYTES) throw fail('PDF 需在 4 MB 以內');
    if ((await readHeader(file)) !== '%PDF-') throw fail('檔案內容不是有效的 PDF，請重新選擇');
    progress('正在讀取 PDF…');
    let lib;
    try {
      lib = opts.pdfjs || (await loadPdfjs());
    } catch (e) {
      throw fail('PDF 處理元件載入失敗，請重新整理頁面');
    }
    let doc;
    try {
      doc = await lib.getDocument({
        data: new Uint8Array(await file.arrayBuffer()),
        cMapUrl: config.cmaps,
        cMapPacked: true,
        isEvalSupported: false,
      }).promise;
    } catch (e) {
      if (e && e.name === 'PasswordException') throw fail('PDF 有密碼保護，請移除密碼後再上傳');
      throw fail('無法開啟這個 PDF，請確認檔案沒有損壞');
    }
    try {
      const pageCount = doc.numPages;
      if (pageCount > MAX_PAGES) throw fail('PDF 超過 ' + MAX_PAGES + ' 頁（共 ' + pageCount + ' 頁），請拆分後再上傳');

      // 2. 每頁轉縮圖，分批判斷（只有 1 頁時直接用原解析度判斷，同時取得上傳用影像）
      const full = {}; // 頁碼 → 原解析度 JPEG
      let results = [];
      if (pageCount === 1) {
        progress('正在辨識頁面與聯別…');
        full[1] = await renderPage(doc, 1, FULL_SIDE, 0.9, true);
        results = (await classify([full[1]], [1])).map(function (r) {
          return Object.assign(r, { thumb: full[1], confirmed: true });
        });
      } else {
        for (let start = 1; start <= pageCount; start += BATCH_SIZE) {
          const end = Math.min(pageCount, start + BATCH_SIZE - 1);
          progress('正在分析第 ' + start + '–' + end + ' 頁（共 ' + pageCount + ' 頁）…');
          const numbers = [];
          const thumbs = [];
          for (let n = start; n <= end; n += 1) {
            if (!isCurrent()) return null;
            numbers.push(n);
            thumbs.push(await renderPage(doc, n, THUMB_SIDE, THUMB_QUALITY));
          }
          const batch = await classify(thumbs, numbers);
          batch.forEach(function (r, i) {
            r.thumb = thumbs[i];
          });
          results = results.concat(batch);
        }
      }
      if (!isCurrent()) return null;

      let candidates = results.filter(function (r) {
        return r.isInvoice;
      });
      if (!candidates.length) throw fail('PDF 中找不到三聯式統一發票的頁面，請確認上傳的檔案');

      // 3. 縮圖看不清聯別或判成第一聯時，用原解析度再確認一次（避免誤擋）
      const unsure = candidates.filter(function (c) {
        return !c.confirmed && c.copy !== 2 && c.copy !== 3;
      });
      for (const c of unsure.slice(0, MAX_CONFIRM)) {
        if (!isCurrent()) return null;
        progress('正在確認第 ' + c.page + ' 頁的聯別…');
        full[c.page] = full[c.page] || (await renderPage(doc, c.page, FULL_SIDE, 0.9, true));
        const [again] = await classify([full[c.page]], [c.page]);
        c.confirmed = true;
        if (again.isInvoice) c.copy = again.copy;
      }

      // 4. 擋下第一聯；剩下的若有多頁讓使用者選
      const usable = candidates.filter(function (c) {
        return c.copy !== 1;
      });
      if (!usable.length) {
        throw fail('找到的是第一聯（存根聯），不能報帳；請上傳第二聯（扣抵聯）或第三聯（收執聯）');
      }
      let chosen = usable[0];
      if (usable.length > 1) {
        progress('');
        chosen = await choose(usable, pageCount);
        if (!chosen || !isCurrent()) return null;
      }

      // 5. 挑出的那一頁轉成上傳用 JPEG
      progress('正在準備第 ' + chosen.page + ' 頁…');
      const blob = full[chosen.page] || (await renderPage(doc, chosen.page, FULL_SIDE, 0.9, true));
      const dataUrl = await blobToDataUrl(blob);
      const base = (file.name || 'invoice.pdf').replace(/\.pdf$/i, '');
      const where = pageCount > 1 ? '已找到第 ' + chosen.page + ' 頁（共 ' + pageCount + ' 頁）的三聯式發票' : '已找到 PDF 的三聯式發票';
      const message = chosen.copy
        ? where + '：' + copyText(chosen.copy)
        : where + '；聯別無法辨識，請確認不是第一聯（存根聯）';
      return {
        blob: blob,
        dataUrl: dataUrl,
        name: base + '_p' + chosen.page + '.jpg',
        page: chosen.page,
        pageCount: pageCount,
        copy: chosen.copy,
        message: message,
      };
    } finally {
      doc.destroy();
    }
  }

  window.InvoicePdfPage = { find: find, trimBounds: trimBounds, MAX_PAGES: MAX_PAGES, MAX_PDF_BYTES: MAX_PDF_BYTES };
})();
