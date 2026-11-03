/*
 * 檔案：intake-original.js
 * 用途：發票核對頁（/intake）的原始影像對照。伺服器沒有草稿，影像只在辨識那一次的回應中附上；
 *       此腳本把它存入瀏覽器 sessionStorage（鍵含 recognition_id），之後檢核／新增明細等重新顯示時取回。
 * 注意：只讀寫 sessionStorage；失敗（容量不足、無痕）時退回提示文字，不影響核對與提交。
 */
(function () {
  var figure = document.getElementById("intake-original");
  if (!figure) return;
  var img = document.getElementById("intake-original-img");
  var note = document.getElementById("intake-original-note");
  var key = "intake-original:" + (figure.getAttribute("data-recognition-id") || "");

  function show(src) {
    img.src = src;
    img.hidden = false;
    note.hidden = true;
  }

  var embedded = img.getAttribute("src");
  if (embedded) {
    show(embedded);
    try {
      sessionStorage.setItem(key, embedded);
    } catch (e) {
      /* 容量不足：下次重新顯示時只會看到提示文字 */
    }
    return;
  }
  try {
    var saved = sessionStorage.getItem(key);
    if (saved) show(saved);
  } catch (e) {
    /* 無法讀取：維持提示文字 */
  }
})();
