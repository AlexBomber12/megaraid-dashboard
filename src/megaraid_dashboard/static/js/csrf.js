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

// HTMX deliberately does not swap error responses. Always explain a refusal as
// plain text, including Secure-cookie/auth failures and foreign-config actions.
document.addEventListener("htmx:responseError", function (event) {
  const detail = event.detail;
  const element = detail.elt;
  const form = element.closest("form");
  const selector = element.getAttribute("hx-target") || (form && form.getAttribute("hx-target"));
  const target = selector && document.querySelector(selector);
  const feedback = target || document.querySelector("[data-request-error]");
  if (feedback) {
    feedback.textContent = detail.xhr.responseText || "Request failed. Refresh and try again.";
    feedback.hidden = false;
  }
});
