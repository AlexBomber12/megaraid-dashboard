// Page-level auto-refresh. Every 30 s re-fetches the current URL and swaps
// each element marked [data-live-region][id] with its fresh counterpart.
// Pauses while the user is typing, a dialog is open, a wizard stage is open
// ([data-live-pause] not hidden) or the tab is hidden.
(function () {
  const INTERVAL_MS = 30000;
  let timer = null;
  let inFlight = false;

  function statusEl() {
    return document.querySelector("[data-live-status]");
  }

  function fmtTime(date) {
    return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(date);
  }

  function setStatus(state, text) {
    const el = statusEl();
    if (!el) return;
    el.dataset.state = state;
    const label = el.querySelector("[data-live-status-text]");
    if (label) label.textContent = text;
  }

  function regions() {
    return Array.prototype.slice.call(document.querySelectorAll("[data-live-region][id]"));
  }

  function pauseReason() {
    if (document.hidden) return "Paused";
    if (document.querySelector("dialog[open]")) return "Paused";
    if (document.querySelector("[data-live-pause]:not([hidden])")) return "Paused during operation";
    const active = document.activeElement;
    if (active && active.matches && active.matches("input, textarea, select") && active.type !== "checkbox") {
      return "Paused while editing";
    }
    return null;
  }

  async function refresh(force) {
    const live = regions();
    if (!live.length || inFlight) return;
    const reason = force ? null : pauseReason();
    if (reason) {
      setStatus("paused", reason);
      return;
    }
    inFlight = true;
    setStatus("loading", "Updating…");
    try {
      const response = await fetch(window.location.href, {
        credentials: "same-origin",
        cache: "no-store",
        headers: { Accept: "text/html" },
      });
      if (response.redirected && new URL(response.url).pathname !== window.location.pathname) {
        setStatus("stale", "Session expired — reload");
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      live.forEach(function (oldNode) {
        const fresh = doc.getElementById(oldNode.id);
        if (!fresh) return;
        const openDetails = Array.prototype.map.call(
          oldNode.querySelectorAll("details[open][id]"),
          function (d) { return d.id; }
        );
        const node = document.importNode(fresh, true);
        openDetails.forEach(function (id) {
          const d = node.querySelector("#" + CSS.escape(id));
          if (d) d.open = true;
        });
        oldNode.replaceWith(node);
        if (window.htmx) window.htmx.process(node);
        node.dispatchEvent(new CustomEvent("live:updated", { bubbles: true }));
      });
      setStatus("live", "Live · " + fmtTime(new Date()));
    } catch (_error) {
      setStatus("stale", "Update failed · retrying");
    } finally {
      inFlight = false;
    }
  }

  function start() {
    const el = statusEl();
    if (!regions().length) {
      setStatus("static", "Loaded " + fmtTime(new Date()));
      if (el) {
        const button = el.querySelector("[data-live-refresh]");
        if (button) button.addEventListener("click", function () { window.location.reload(); });
      }
      return;
    }
    setStatus("live", "Live · " + fmtTime(new Date()));
    if (el) {
      const button = el.querySelector("[data-live-refresh]");
      if (button) button.addEventListener("click", function () { refresh(true); });
    }
    timer = window.setInterval(refresh, INTERVAL_MS);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) refresh();
    });
  }

  window.liveRefresh = { refresh: refresh };
  document.addEventListener("DOMContentLoaded", start);
})();
