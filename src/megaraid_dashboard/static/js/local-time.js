(function () {
  function fmt(date, opts) {
    return new Intl.DateTimeFormat(undefined, opts).format(date);
  }

  function formatOptions(el) {
    if (el.dataset.localTimeFormat === "date") {
      return { year: "numeric", month: "short", day: "2-digit" };
    }
    if (el.dataset.localTimeFormat === "time") {
      return { hour: "2-digit", minute: "2-digit", second: "2-digit" };
    }
    return {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    };
  }

  function localizeAll(root) {
    const els = (root || document).querySelectorAll("time[data-local-time]");
    els.forEach(function (el) {
      const iso = el.getAttribute("datetime");
      if (!iso) {
        return;
      }
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) {
        return;
      }
      el.textContent = fmt(date, formatOptions(el));
      el.title = iso;
    });
  }

  function startClock() {
    const el = document.querySelector("[data-local-time-clock]");
    if (!el) {
      return;
    }
    function tick() {
      el.textContent = fmt(new Date(), {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      });
    }
    tick();
    setInterval(tick, 1000);
  }

  document.addEventListener("DOMContentLoaded", function () {
    localizeAll(document);
    startClock();
    document.body.addEventListener("htmx:afterSwap", function (evt) {
      localizeAll(evt.target || document);
    });
  });
})();
