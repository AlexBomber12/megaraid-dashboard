(function () {
  function getCookie(name) {
    const match = document.cookie.match(
      new RegExp("(?:^|; )" + name.replace(/[$()*+./?[\\\]^{|}-]/g, "\\$&") + "=([^;]*)")
    );
    return match ? decodeURIComponent(match[1]) : null;
  }

  document.body && document.body.addEventListener("htmx:configRequest", function (evt) {
    const token = getCookie("__Host-csrf");
    if (token) {
      evt.detail.headers["X-CSRF-Token"] = token;
    }
  });
})();
