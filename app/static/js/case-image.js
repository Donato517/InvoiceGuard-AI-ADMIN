/*
 * 檔名：case-image.js
 * 功能：/cases 詳情頁的原圖載入失敗提示。
 * 行為：<img data-case-image> 載入失敗時，以 Accept: application/json 再問同一個 /image 網址，
 *       顯示伺服器回傳的固定中文訊息（沒有可顯示的原圖／原圖無法讀取／暫時性錯誤）；
 *       連不上伺服器則顯示暫時性錯誤。只用 textContent，不顯示任何內部資訊。
 */
(function () {
  "use strict";

  var TEMPORARY = "暫時無法讀取原圖，請稍後重試";

  function show(image, message) {
    var box = image.parentNode.querySelector("[data-case-image-error]");
    image.hidden = true;
    if (box) {
      box.textContent = message;
      box.hidden = false;
    }
  }

  function explain(image) {
    var request;
    try {
      request = fetch(image.getAttribute("src"), {
        headers: { Accept: "application/json" },
        credentials: "same-origin",
        cache: "no-store",
      });
    } catch (error) {
      show(image, TEMPORARY);
      return;
    }
    request
      .then(function (response) {
        return response.json().then(function (body) {
          return { ok: response.ok, body: body };
        });
      })
      .then(function (result) {
        var message = result.body && result.body.message;
        show(image, !result.ok && typeof message === "string" ? message : TEMPORARY);
      })
      .catch(function () {
        show(image, TEMPORARY);
      });
  }

  function init() {
    var images = document.querySelectorAll("img[data-case-image]");
    Array.prototype.forEach.call(images, function (image) {
      image.addEventListener("error", function () {
        explain(image);
      });
      // 腳本延後執行，圖片可能已在腳本掛上之前就載入失敗
      if (image.complete && image.naturalWidth === 0) {
        explain(image);
      }
    });
  }

  if (typeof module !== "undefined" && module.exports) {
    module.exports = { explain: explain, init: init };
  } else {
    init();
  }
})();
