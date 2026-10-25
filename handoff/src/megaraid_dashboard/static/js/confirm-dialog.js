// Accessible confirmation dialog for forms.
// Put on a <form> (or its submit button):
//   data-confirm-title="Set drive 252:3 offline?"
//   data-confirm-body="The array becomes degraded until rebuild."
//   data-confirm-phrase="252:3"        (optional — user must type it)
//   data-confirm-label="Set Offline"   (confirm button text)
//   data-confirm-tone="danger|warning"
// The dialog intercepts submit in the capture phase, so existing submit
// handlers on the form only run after the user confirms.
(function () {
  let dialog = null;

  function build() {
    dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog";
    dialog.setAttribute("aria-labelledby", "confirm-dialog-title");
    dialog.innerHTML =
      '<form method="dialog" class="confirm-dialog__inner">' +
      '<h2 class="confirm-dialog__title" id="confirm-dialog-title" data-cd-title></h2>' +
      '<p class="confirm-dialog__body" data-cd-body></p>' +
      '<label class="field" data-cd-field>' +
      '<span class="field__label">Type <code data-cd-phrase></code> to confirm</span>' +
      '<input class="input mono" data-cd-input autocomplete="off" spellcheck="false">' +
      "</label>" +
      '<div class="confirm-dialog__actions">' +
      '<button type="submit" value="cancel" class="button" data-cd-cancel>Cancel</button>' +
      '<button type="submit" value="confirm" class="button" data-cd-confirm></button>' +
      "</div></form>";
    document.body.appendChild(dialog);
  }

  function confirmAction(opts) {
    if (!dialog) build();
    const tone = opts.confirmTone || "danger";
    const phrase = opts.confirmPhrase || "";
    const input = dialog.querySelector("[data-cd-input]");
    const confirmButton = dialog.querySelector("[data-cd-confirm]");
    dialog.dataset.tone = tone;
    dialog.querySelector("[data-cd-title]").textContent = opts.confirmTitle || "Are you sure?";
    const body = dialog.querySelector("[data-cd-body]");
    body.textContent = opts.confirmBody || "";
    body.hidden = !opts.confirmBody;
    dialog.querySelector("[data-cd-field]").hidden = !phrase;
    dialog.querySelector("[data-cd-phrase]").textContent = phrase;
    input.value = "";
    confirmButton.textContent = opts.confirmLabel || "Confirm";
    confirmButton.className = "button " + (tone === "danger" ? "button--danger-solid" : "button--warning");
    confirmButton.disabled = Boolean(phrase);
    input.oninput = function () {
      confirmButton.disabled = input.value.trim() !== phrase;
    };
    return new Promise(function (resolve) {
      dialog.onclose = function () {
        resolve(dialog.returnValue === "confirm");
      };
      dialog.returnValue = "";
      dialog.showModal();
      (phrase ? input : dialog.querySelector("[data-cd-cancel]")).focus();
    });
  }

  document.addEventListener(
    "submit",
    function (event) {
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      const submitter = event.submitter || null;
      const source = submitter && submitter.hasAttribute("data-confirm-title")
        ? submitter
        : form.hasAttribute("data-confirm-title") ? form : null;
      if (!source) return;
      if (form.dataset.confirmed === "true") {
        delete form.dataset.confirmed;
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      confirmAction(source.dataset).then(function (ok) {
        if (!ok) return;
        form.dataset.confirmed = "true";
        if (submitter) form.requestSubmit(submitter);
        else form.requestSubmit();
      });
    },
    true
  );

  window.confirmAction = confirmAction;
})();
