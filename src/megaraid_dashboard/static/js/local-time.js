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
    return { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit" };
  }

  function localizeAll(root) {
    (root || document).querySelectorAll("time[data-local-time]").forEach(function (el) {
      const iso = el.getAttribute("datetime");
      if (!iso) return;
      const date = new Date(iso);
      if (Number.isNaN(date.getTime())) return;
      el.textContent = fmt(date, formatOptions(el));
      el.title = iso;
      el.hidden = false;
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    localizeAll(document);
    document.body.addEventListener("htmx:afterSwap", function (evt) {
      localizeAll(evt.target || document);
    });
    document.addEventListener("live:updated", function (evt) {
      localizeAll(evt.target || document);
    });
  });

  function chartDate(value) {
    if (typeof value !== "string" || !value.trim()) return null;
    // Drive range partials use UTC bucket labels without an offset. Never parse them as local.
    const legacyUtc = /^\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?$/.test(value);
    const iso = legacyUtc
      ? value.replace(" ", "T") + (value.length === 10 ? "T00:00:00Z" : ":00Z")
      : value;
    const date = new Date(iso);
    return Number.isNaN(date.getTime()) ? null : date;
  }

  function chartAxis(labels, rangeHours) {
    const first = chartDate(labels[0]);
    const last = chartDate(labels[labels.length - 1]);
    const spanHours = first && last ? (last - first) / 3600000 : 0;
    // A sparse month/year view still needs times when only a few hours are available.
    if (rangeHours > 1 && spanHours > 0) rangeHours = Math.min(rangeHours, Math.max(2, spanHours));
    const time = new Intl.DateTimeFormat(undefined, {
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    });
    const day = new Intl.DateTimeFormat(undefined, { day: "2-digit", month: "short" });
    const year = new Intl.DateTimeFormat(undefined, { year: "numeric" });
    return {
      title: {
        display: true,
        text: "Local time · " + Intl.DateTimeFormat().resolvedOptions().timeZone,
        color: "#9aa5b1",
      },
      ticks: {
        color: "#9aa5b1",
        maxRotation: 0,
        minRotation: 0,
        autoSkip: true,
        autoSkipPadding: 20,
        maxTicksLimit: 8,
        callback(value) {
          const date = chartDate(labels[Number(value)]);
          if (!date) return "Unknown time";
          if (rangeHours <= 1) return time.format(date);
          if (rangeHours <= 48) return [day.format(date), time.format(date)];
          if (rangeHours <= 31 * 24) return day.format(date);
          return [day.format(date), year.format(date)];
        },
      },
    };
  }

  function chartTooltip(labels) {
    const full = new Intl.DateTimeFormat(undefined, {
      year: "numeric", month: "short", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
      hourCycle: "h23", timeZoneName: "short",
    });
    return {
      mode: "index",
      intersect: false,
      callbacks: {
        title(items) {
          const date = items.length ? chartDate(labels[items[0].dataIndex]) : null;
          return date ? full.format(date) : "Unknown time";
        },
      },
    };
  }

  window.localTime = { localizeAll, chartAxis, chartTooltip };
})();
