(function () {
  function actionButton(target) {
    return target && target.closest ? target.closest("[data-locate-action]") : null;
  }

  function confirmIfNeeded(evt) {
    const el = actionButton(evt.target);
    if (!el || !el.hasAttribute) return;

    const message = el.getAttribute("data-confirm");
    if (message && !window.confirm(message)) {
      evt.preventDefault();
    }
  }

  function begin(el) {
    const section = el.closest(".drive-actions");
    section.querySelectorAll("[data-locate-action]").forEach(function (button) {
      button.disabled = true;
    });
    el.setAttribute("aria-busy", "true");
  }

  function flash(el, detail) {
    const section = el.closest(".drive-actions");
    const fb = section.querySelector("[data-action-feedback]");
    const start = section.querySelector('[data-locate-action="start"]');
    const stop = section.querySelector('[data-locate-action="stop"]');
    const label = section.querySelector("[data-locate-label]");
    const status = section.querySelector("[data-locate-status]");
    let response = {};
    try {
      const parsed = JSON.parse(detail.xhr.responseText);
      response = parsed && typeof parsed === "object" ? parsed : {};
    } catch (_) {
      // Network errors and proxy responses may have no JSON body.
    }
    el.removeAttribute("aria-busy");
    const accepted = detail.successful && response.command_accepted === true;
    const action = el.getAttribute("data-locate-action");
    const started = accepted && action === "start";
    section.setAttribute("data-locate-state", accepted ? action : "unknown");
    start.disabled = started || section.getAttribute("data-locate-start-disabled") === "true";
    // An accepted command does not verify the physical LED, including after stop.
    stop.disabled = false;
    label.textContent = started ? "Blink command accepted" : "Blink Locate LED";
    status.textContent = "LED state unverified." +
      (accepted ? " Last accepted command: " + action + "." : "");

    if (accepted) {
      fb.textContent = "Controller accepted " + action + ". Check the physical LED.";
      fb.className = "drive-actions__feedback drive-actions__feedback--ok";
    } else {
      const csrfRefused = detail.xhr && detail.xhr.status === 403 &&
        /\bcsrf\b/i.test(detail.xhr.responseText || "");
      if (csrfRefused) {
        fb.textContent = "CSRF token missing or mismatched. Reload the HTTPS page and retry.";
      } else {
        fb.textContent = typeof response.message === "string" ? response.message :
          "Command not confirmed. LED state is unverified. " +
          "Inspect the physical LED and audit log before another Start. " +
          "Do not repeat Start blindly. " +
          "Stop Locate can request LED shutdown without maintenance.";
      }
      fb.className = "drive-actions__feedback drive-actions__feedback--error";
    }
  }

  document.body && document.body.addEventListener("htmx:confirm", confirmIfNeeded);

  window.driveActions = { begin: begin, flash: flash };
})();
