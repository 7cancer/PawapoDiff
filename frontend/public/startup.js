/* Keep this classic script separate from React so failed modules remain visible. */
(function () {
  "use strict";
  var ready = false;
  var failure = null;
  var timer;
  function clean(value) {
    return String(value || "詳細を取得できませんでした")
      .replace(/([#?&]token=)[^\s&#]*/gi, "$1[非表示]")
      .slice(0, 2000);
  }
  function details() {
    return [
      "PawapoDiff 画面起動の診断",
      "コード: " + (failure ? failure.code : "UI_LOADING"),
      "詳細: " + (failure ? failure.message : "画面の読み込み中"),
      "ブラウザ: " + navigator.userAgent,
      "URL: " + location.protocol + "//" + location.host + location.pathname,
      "スライド本文・認証トークンは含みません。",
    ].join("\n");
  }
  function show() {
    var panel = document.getElementById("startup-status");
    if (!panel || !failure) return;
    panel.hidden = false;
    panel.querySelector("h1").textContent = "画面を起動できませんでした";
    document.getElementById("startup-message").textContent =
      "画面ファイルの読み込み、またはJavaScriptの実行に失敗しました。";
    document.getElementById("startup-detail").textContent = details();
    document.getElementById("startup-actions").hidden = false;
    document.getElementById("startup-download").onclick = function () {
      var url = URL.createObjectURL(
        new Blob(["\ufeff" + details()], { type: "text/plain;charset=utf-8" })
      );
      var link = document.createElement("a");
      link.href = url;
      link.download = "PawapoDiff-startup.txt";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      setTimeout(function () {
        URL.revokeObjectURL(url);
      }, 1000);
    };
  }
  function fail(code, message) {
    if (failure) return;
    failure = { code: code, message: clean(message) };
    clearTimeout(timer);
    show();
  }
  window.addEventListener(
    "error",
    function (event) {
      if (ready) return;
      var element = event.target;
      if (element && element !== window && (element.src || element.href)) {
        var resource = document.createElement("a");
        resource.href = element.src || element.href;
        fail("UI_ASSET_LOAD_FAILED", resource.pathname);
      } else {
        fail("UI_SCRIPT_FAILED", event.message);
      }
    },
    true
  );
  window.addEventListener("unhandledrejection", function (event) {
    if (!ready)
      fail(
        "UI_SCRIPT_FAILED",
        (event.reason && event.reason.message) || event.reason
      );
  });
  window.addEventListener("pawapo-startup-error", function (event) {
    fail("UI_RENDER_FAILED", event.detail);
  });
  window.addEventListener("pawapo-ready", function () {
    ready = true;
    failure = null;
    clearTimeout(timer);
    var panel = document.getElementById("startup-status");
    if (panel) panel.hidden = true;
  });
  document.addEventListener("DOMContentLoaded", show);
  timer = setTimeout(function () {
    if (!ready)
      fail(
        "UI_START_TIMEOUT",
        "10秒経っても画面が起動しません。画面ファイルの読み込み、JavaScriptの設定、ブラウザのバージョンを確認してください。"
      );
  }, 10000);
})();
