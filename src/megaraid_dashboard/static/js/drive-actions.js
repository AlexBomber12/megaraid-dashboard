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

  function resetFeedback(fb) {
    fb.textContent = "";
    fb.className = "drive-actions__feedback";
  }

  function flash(el, detail) {
    const section = el.closest(".drive-actions");
    const fb = section && section.querySelector("[data-action-feedback]");
    if (!fb) return;

    if (detail.successful) {
      section.setAttribute("data-locate-state", el.getAttribute("data-locate-action") || "unknown");
      fb.textContent = "Sent";
      fb.className = "drive-actions__feedback drive-actions__feedback--ok";
    } else {
      fb.textContent = "Error: " + (detail.xhr && detail.xhr.status);
      fb.className = "drive-actions__feedback drive-actions__feedback--error";
    }

    setTimeout(function () {
      resetFeedback(fb);
    }, 5000);
  }

  document.body && document.body.addEventListener("htmx:confirm", confirmIfNeeded);

  window.driveActions = { flash: flash };
})();
