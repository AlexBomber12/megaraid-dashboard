(function () {
  function getCookie(name) {
    const match = document.cookie.match(
      new RegExp("(?:^|; )" + name.replace(/[$()*+./?[\\\]^{|}-]/g, "\\$&") + "=([^;]*)")
    );
    return match ? decodeURIComponent(match[1]) : null;
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRF-Token": getCookie("__Host-csrf") || "",
      },
      body: JSON.stringify(body),
    });
    const text = await response.text();
    return { ok: response.ok, status: response.status, body: text };
  }

  function actionTarget(target) {
    return target && target.closest ? target.closest("[data-replace-action]") : null;
  }

  function init(root) {
    const expectedSerial = root.dataset.serial || "";
    const offlineUrl = root.dataset.replaceOfflineUrl;
    const missingUrl = root.dataset.replaceMissingUrl;
    const stages = {
      confirm: root.querySelector('[data-stage="confirm"]'),
      serial: root.querySelector('[data-stage="serial"]'),
      result: root.querySelector('[data-stage="result"]'),
    };
    const serialInput = root.querySelector('[data-replace-input="serial"]');
    const dryRunInput = root.querySelector('[data-replace-input="dry-run"]');
    const runButton = root.querySelector('[data-replace-action="run-step1"]');
    const output = root.querySelector("[data-replace-output]");
    const openButton = root.querySelector('[data-replace-action="open"]');

    function show(name) {
      Object.keys(stages).forEach(function (key) {
        if (stages[key]) stages[key].hidden = true;
      });
      if (name && stages[name]) {
        stages[name].hidden = false;
        const cancelButton = stages[name].querySelector('[data-replace-action="cancel"]');
        if (cancelButton) cancelButton.focus();
      }
    }

    function updateRunButton() {
      runButton.disabled = serialInput.value.trim() !== expectedSerial.trim();
    }

    function close() {
      show(null);
      openButton.hidden = false;
      output.textContent = "";
      serialInput.value = "";
      dryRunInput.checked = true;
      updateRunButton();
    }

    function appendResult(label, result) {
      output.textContent += label + "\n";
      output.textContent += JSON.stringify({ status: result.status, body: result.body }, null, 2);
      output.textContent += "\n";
    }

    function appendRequestError(label, error) {
      output.textContent += label + "\n";
      output.textContent += JSON.stringify(
        {
          error: error instanceof Error ? error.message : String(error),
        },
        null,
        2
      );
      output.textContent += "\n";
    }

    serialInput.addEventListener("input", updateRunButton);

    root.addEventListener("click", async function (evt) {
      const button = actionTarget(evt.target);
      if (!button) return;

      const action = button.dataset.replaceAction;
      if (action === "open") {
        openButton.hidden = true;
        show("confirm");
      }
      if (action === "cancel" || action === "close") {
        close();
      }
      if (action === "continue-to-serial") {
        show("serial");
      }
      if (action === "run-step1") {
        runButton.disabled = true;
        show("result");

        const body = {
          serial_number: serialInput.value.trim(),
          dry_run: dryRunInput.checked,
        };
        try {
          output.textContent = "Running set offline...\n";
          const offline = await postJson(offlineUrl, body);
          appendResult("set offline response", offline);
          if (!offline.ok) return;

          output.textContent += "Running set missing...\n";
          const missing = await postJson(missingUrl, body);
          appendResult("set missing response", missing);
        } catch (error) {
          appendRequestError("replace request failed", error);
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-replace-wizard]").forEach(init);
  });
})();
