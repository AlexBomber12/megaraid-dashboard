(function () {
  function fmt(seconds) {
    if (seconds <= 0) return "expired";

    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = seconds % 60;

    if (h > 0) return h + "h " + m + "m left";
    if (m > 0) return m + "m " + s + "s left";
    return s + "s left";
  }

  function tickAll() {
    document.querySelectorAll("[data-maintenance-countdown]").forEach(function (el) {
      const expiresAt = new Date(el.getAttribute("data-expires"));
      const remaining = Math.floor((expiresAt - new Date()) / 1000);
      el.textContent = fmt(remaining);
    });
  }

  function defineJsonEncodingExtension() {
    if (!window.htmx) return;

    window.htmx.defineExtension("json-enc", {
      onEvent: function (name, event) {
        if (name === "htmx:configRequest") {
          event.detail.headers["Content-Type"] = "application/json";
        }
      },
      encodeParameters: function (xhr, parameters) {
        return JSON.stringify(Object.fromEntries(parameters.entries()));
      },
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    defineJsonEncodingExtension();
    tickAll();
    setInterval(tickAll, 1000);
  });
})();
