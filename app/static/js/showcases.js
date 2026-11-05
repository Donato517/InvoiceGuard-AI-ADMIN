/* 供應商範例：新增明細及本機測試預覽；檔案驗證仍由伺服器執行。 */
document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('showcase-find-supplier')?.addEventListener('click', async () => {
    const results = document.getElementById('showcase-suppliers');
    results.replaceChildren();
    results.textContent = '搜尋中…';
    try {
      const query = document.getElementById('showcase-supplier-query').value;
      const response = await fetch(`/showcases/suppliers?q=${encodeURIComponent(query)}`, { headers: { Accept: 'application/json' } });
      if (!response.ok || response.redirected) throw new Error('unavailable');
      const suppliers = await response.json();
      results.replaceChildren();
      if (!suppliers.length) results.textContent = '找不到既有供應商，可於下方填寫新供應商。';
      for (const supplier of suppliers) {
        const button = document.createElement('button');
        button.type = 'button';
        button.textContent = `${supplier.name}（${supplier.tax_id}）`;
        button.addEventListener('click', () => {
          document.querySelector('[name="supplier_name"]').value = supplier.name;
          document.querySelector('[name="tax_id"]').value = supplier.tax_id;
          results.textContent = `已選取 ${supplier.name}，保存時仍會核對供應商關聯。`;
        });
        results.append(button);
      }
    } catch {
      results.textContent = '供應商查詢失敗，請確認登入與權限後再試。';
    }
  });
  const fields = document.getElementById('showcase-fields');
  fields?.addEventListener('input', event => {
    const field = event.target.closest('.showcase-correction-field');
    if (!field) return;
    const input = field.querySelector('[name="truth_text"]');
    if (event.target === input) field.querySelector('[name="truth_state"]').value = 'unconfirmed';
    field.querySelector('.showcase-modified').hidden = input.value.trim() === input.dataset.aiOriginal;
  });
  document.getElementById('showcase-add-item')?.addEventListener('click', () => {
    const paths = Array.from(fields.querySelectorAll('[name="truth_path"]'), input => input.value);
    const rows = paths.map(path => Number(path.split('.')[1])).filter(Number.isFinite);
    const next = Math.max(1, ...rows) + 1;
    if (paths.length + 4 > 100) return;
    for (const [key, label] of [['name', '品名'], ['quantity', '數量'], ['unit_price', '單價'], ['amount', '金額']]) {
      const template = fields.querySelector('.showcase-correction-field').cloneNode(true);
      template.removeAttribute('id');
      template.querySelector('.showcase-correction-label').firstChild.textContent = `第 ${next} 列${label}`;
      template.querySelector('[name="truth_path"]').value = `items.${next}.${key}`;
      template.querySelector('[name="truth_text"]').value = '';
      template.querySelector('[name="truth_text"]').dataset.aiOriginal = '';
      template.querySelector('[name="truth_state"]').value = 'unconfirmed';
      template.querySelector('.showcase-original').textContent = '';
      template.querySelector('.showcase-modified').hidden = true;
      fields.append(template);
    }
  });
  const intakeFile = document.getElementById('showcase-intake-file');
  const intakePreview = document.getElementById('showcase-intake-preview');
  const intakeStatus = document.getElementById('showcase-intake-status');
  const originalSrc = intakePreview?.getAttribute('src');
  const replacementNote = document.getElementById('showcase-replacement-note');
  const saveReview = document.getElementById('showcase-save-review');
  const reviewFields = document.getElementById('showcase-review-fields');
  const imageHint = document.getElementById('showcase-image-hint');
  let intakeUrl;
  intakeFile?.addEventListener('change', () => {
    const selected = intakeFile.files[0];
    const tooLarge = Boolean(selected && selected.size > 4 * 1024 * 1024);
    intakeFile.setCustomValidity(tooLarge ? '請選擇 4 MB 以內的 JPG／PNG 圖片。' : '');
    if (intakeStatus) intakeStatus.textContent = tooLarge
      ? '圖片超過 4 MB，請換一張較小的 JPG／PNG。'
      : selected ? '圖片已選擇。按「上傳並辨識」後，會儲存待核對草稿並帶入辨識欄位。'
        : '尚未選圖。';
    if (replacementNote) replacementNote.hidden = !intakeFile.files.length;
    if (saveReview) saveReview.disabled = Boolean(intakeFile.files.length);
    if (saveReview) reviewFields.hidden = Boolean(intakeFile.files.length);
    if (intakeUrl) URL.revokeObjectURL(intakeUrl);
    if (intakeFile.files[0]) {
      intakeUrl = URL.createObjectURL(intakeFile.files[0]);
      intakePreview.src = intakeUrl;
      intakePreview.hidden = false;
      if (imageHint) imageHint.hidden = true;
    } else if (originalSrc) {
      intakePreview.src = originalSrc;
    } else {
      intakePreview.hidden = true;
      if (imageHint) imageHint.hidden = false;
    }
  });
  intakeFile?.form?.addEventListener('submit', event => {
    if (event.submitter?.name === 'run_ocr' && intakeStatus && intakeFile.checkValidity()) {
      intakeStatus.textContent = '正在上傳與辨識，請稍候；完成後會開啟左圖右欄的核對頁。';
    }
  });
  const file = document.getElementById('showcase-test-file');
  const preview = document.getElementById('showcase-test-preview');
  let url;
  file?.addEventListener('change', () => {
    if (url) URL.revokeObjectURL(url);
    if (!file.files[0]) { preview.hidden = true; return; }
    url = URL.createObjectURL(file.files[0]);
    preview.src = url;
    preview.hidden = false;
  });
});
