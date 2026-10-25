// Page-level auto-refresh. Every 30 s re-fetches the current URL and swaps
// each element marked [data-live-region][id] with its fresh counterpart.
// Pauses while the user is typing, a dialog is open, a wizard stage is open
// ([data-live-pause] not hidden) or the tab is hidden. Pause conditions are
// checked again right before the swap, because the fetch is asynchronous.
// Pages opt out with <body data-live-refresh="off">.
//
// Pages that describe one physical drive mark it with [data-live-identity].
// When the fresh page reports a different identity (the disk was swapped), the
// page reloads so no outdated serial stays on screen. While a workflow step is
// open the page is not reloaded; instead a "live:identity-changed" event is
// dispatched and <body data-live-stale="identity"> blocks stale submits.
(function () {
  const INTERVAL_MS = 30000;
  const EDITABLE = "input, textarea, select, [contenteditable]";
  const INTERACTIVE =
    "a[href], button, input, select, textarea, summary, [tabindex], [contenteditable]";
  const WORKFLOW_PAUSE = "Paused during operation";
  let timer = null;
  let inFlight = false;
  let pageIdentity = null;
  let announcedIdentity;

  function statusEl() {
    return document.querySelector("[data-live-status]");
  }

  function fmtTime(date) {
    return new Intl.DateTimeFormat(undefined, {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hourCycle: "h23",
    }).format(date);
  }

  function setStatus(state, text) {
    const el = statusEl();
    if (!el) return;
    el.dataset.state = state;
    const label = el.querySelector("[data-live-status-text]");
    if (label) label.textContent = text;
  }

  function regions() {
    if (document.body.dataset.liveRefresh === "off") return [];
    return Array.prototype.slice.call(document.querySelectorAll("[data-live-region][id]"));
  }

  function identityOf(root) {
    const el = root.querySelector("[data-live-identity]");
    return el ? el.getAttribute("data-live-identity") : null;
  }

  function workflowOpen() {
    return Boolean(document.querySelector("[data-live-pause]:not([hidden])"));
  }

  function pauseReason() {
    if (document.hidden) return "Paused";
    if (document.querySelector("dialog[open]")) return "Paused";
    if (workflowOpen()) return WORKFLOW_PAUSE;
    const active = document.activeElement;
    if (active && active !== document.body && active.matches && active.matches(EDITABLE)) {
      return "Paused while editing";
    }
    if (active && active.matches && active.matches(INTERACTIVE) &&
        !active.matches("[data-live-refresh]")) {
      return "Paused while focused";
    }
    return null;
  }

  function swapRegion(oldNode, fresh) {
    const node = document.importNode(fresh, true);
    oldNode.querySelectorAll("details[open][id]").forEach(function (d) {
      const twin = node.querySelector("#" + CSS.escape(d.id));
      if (twin) twin.open = true;
    });
    oldNode.replaceWith(node);
    if (window.htmx) window.htmx.process(node);
    node.dispatchEvent(new CustomEvent("live:updated", { bubbles: true }));
  }

  function handleIdentityChange(freshIdentity) {
    document.body.dataset.liveStale = "identity";
    if (announcedIdentity !== freshIdentity) {
      announcedIdentity = freshIdentity;
      document.dispatchEvent(
        new CustomEvent("live:identity-changed", {
          detail: { previous: pageIdentity, current: freshIdentity },
        })
      );
    }
    if (workflowOpen()) {
      setStatus("stale", "Drive changed · reload after the wizard");
      return;
    }
    const reason = pauseReason();
    if (reason) {
      // Reload on the next idle cycle; stale drive actions are blocked meanwhile.
      setStatus("stale", "Drive changed · " + reason.toLowerCase());
      return;
    }
    setStatus("loading", "Drive changed · reloading");
    window.location.reload();
  }

  async function refresh() {
    const live = regions();
    if (!live.length || inFlight) return;
    const startReason = pauseReason();
    // Detect disk changes even while a workflow, confirmation or focused control
    // pauses DOM updates. Never reload or swap until those pauses have ended.
    const probeOnly = Boolean(startReason && pageIdentity !== null && !document.hidden);
    if (startReason && !probeOnly) {
      setStatus("paused", startReason);
      return;
    }
    inFlight = true;
    if (!probeOnly) setStatus("loading", "Updating…");
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
      if (response.status === 404 && pageIdentity !== null) {
        handleIdentityChange(null);
        return;
      }
      if (!response.ok) throw new Error(String(response.status));
      const doc = new DOMParser().parseFromString(await response.text(), "text/html");
      const freshIdentity = identityOf(doc);
      if (pageIdentity !== null &&
          (freshIdentity !== pageIdentity || document.body.dataset.liveStale)) {
        handleIdentityChange(freshIdentity);
        return;
      }
      // The user may have opened a dialog, a wizard step or an input while we waited.
      const lateReason = pauseReason();
      if (lateReason) {
        setStatus("paused", lateReason);
        return;
      }
      for (const oldNode of live) {
        // Processing a region can synchronously open a dialog or focus a control.
        const reason = pauseReason();
        if (reason) {
          setStatus("paused", reason);
          return;
        }
        if (!oldNode.isConnected) continue;
        const fresh = doc.getElementById(oldNode.id);
        if (fresh) swapRegion(oldNode, fresh);
      }
      setStatus("live", "Live · " + fmtTime(new Date()));
    } catch (_error) {
      setStatus("stale", "Update failed · retrying");
    } finally {
      inFlight = false;
    }
  }

  function start() {
    const el = statusEl();
    pageIdentity = identityOf(document);
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
      if (button) button.addEventListener("click", function () { refresh(); });
    }
    timer = window.setInterval(refresh, INTERVAL_MS);
    document.addEventListener("visibilitychange", function () {
      if (!document.hidden) refresh();
    });
  }

  window.liveRefresh = { refresh: refresh };
  // Capture before confirm-dialog's document handler, including its confirmed replay.
  window.addEventListener("submit", function (event) {
    if (document.body.dataset.liveStale !== "identity") return;
    if (!event.target.closest("[data-live-identity]")) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    setStatus("stale", "Drive changed · reload before acting on this drive");
  }, true);
  document.addEventListener("htmx:beforeRequest", function (event) {
    if (document.body.dataset.liveStale === "identity" &&
        event.detail.requestConfig.verb !== "get" &&
        event.detail.elt.closest("[data-live-identity]")) {
      event.preventDefault();
    }
  });
  document.addEventListener("DOMContentLoaded", start);
})();
