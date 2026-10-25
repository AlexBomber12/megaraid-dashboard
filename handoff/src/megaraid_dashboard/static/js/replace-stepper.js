// Keeps the replace-wizard stepper in sync with the visible stage.
// Works alongside replace-wizard.js without modifying it.
(function () {
  const STEP_FOR_STAGE = { confirm: 1, serial: 1, "physical-swap": 2, insert: 3, rebuild: 4 };

  function sync(root) {
    const stepper = root.querySelector("[data-replace-stepper]");
    if (!stepper) return;
    const visible = root.querySelector("[data-stage]:not([hidden])");
    const stage = visible ? visible.dataset.stage : null;
    if (stage === "result") return; // keep the current step while showing output
    const current = stage ? STEP_FOR_STAGE[stage] || 0 : 0;
    stepper.hidden = current === 0;
    stepper.querySelectorAll("li").forEach(function (li, index) {
      const step = index + 1;
      if (step === current) li.setAttribute("aria-current", "step");
      else li.removeAttribute("aria-current");
      li.dataset.done = step < current ? "true" : "false";
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-replace-wizard]").forEach(function (root) {
      sync(root);
      new MutationObserver(function () { sync(root); }).observe(root, {
        attributes: true,
        attributeFilter: ["hidden"],
        subtree: true,
      });
    });
  });
})();
