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

  async function getJson(url) {
    const response = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
    const text = await response.text();
    let parsed = null;
    try {
      parsed = JSON.parse(text);
    } catch (_error) {
      parsed = null;
    }
    return { ok: response.ok, status: response.status, body: text, json: parsed };
  }

  function actionTarget(target) {
    return target && target.closest ? target.closest("[data-replace-action]") : null;
  }

  function init(root) {
    const expectedSerial = root.dataset.serial || "";
    const offlineUrl = root.dataset.replaceOfflineUrl;
    const missingUrl = root.dataset.replaceMissingUrl;
    const topologyUrl = root.dataset.replaceTopologyUrl;
    const insertUrl = root.dataset.replaceInsertUrl;
    const rebuildStatusUrl = root.dataset.replaceRebuildStatusUrl;
    const stages = {
      confirm: root.querySelector('[data-stage="confirm"]'),
      serial: root.querySelector('[data-stage="serial"]'),
      "physical-swap": root.querySelector('[data-stage="physical-swap"]'),
      insert: root.querySelector('[data-stage="insert"]'),
      result: root.querySelector('[data-stage="result"]'),
      rebuild: root.querySelector('[data-stage="rebuild"]'),
    };
    const serialInput = root.querySelector('[data-replace-input="serial"]');
    const dryRunInput = root.querySelector('[data-replace-input="dry-run"]');
    const newSerialInput = root.querySelector('[data-replace-input="new-serial"]');
    const dryRunStep3Input = root.querySelector('[data-replace-input="dry-run-step3"]');
    const runButton = root.querySelector('[data-replace-action="run-step1"]');
    const runStep3Button = root.querySelector('[data-replace-action="run-step3"]');
    const dgCell = root.querySelector('[data-replace-meta="dg"]');
    const arrayCell = root.querySelector('[data-replace-meta="array"]');
    const rowCell = root.querySelector('[data-replace-meta="row"]');
    const output = root.querySelector("[data-replace-output]");
    const rebuildProgress = root.querySelector("[data-rebuild-progress]");
    const openButton = root.querySelector('[data-replace-action="open"]');
    let inFlight = false;
    let topology = null;

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
      runButton.disabled = inFlight || serialInput.value.trim() !== expectedSerial.trim();
    }

    function updateRunStep3Button() {
      if (!runStep3Button) return;
      const serialOk =
        newSerialInput &&
        newSerialInput.value.trim() !== "" &&
        newSerialInput.value.trim() !== expectedSerial.trim();
      runStep3Button.disabled = inFlight || topology === null || !serialOk;
    }

    function updateCloseControls() {
      const closeControls = root.querySelectorAll(
        '[data-replace-action="cancel"], [data-replace-action="close"]'
      );
      closeControls.forEach(function (button) {
        button.disabled = inFlight;
      });
    }

    function setInFlight(value) {
      inFlight = value;
      root.setAttribute("aria-busy", value ? "true" : "false");
      updateRunButton();
      updateRunStep3Button();
      updateCloseControls();
    }

    function close() {
      show(null);
      openButton.hidden = false;
      output.textContent = "";
      serialInput.value = "";
      dryRunInput.checked = true;
      if (newSerialInput) newSerialInput.value = "";
      if (dryRunStep3Input) dryRunStep3Input.checked = true;
      if (dgCell) dgCell.textContent = "...";
      if (arrayCell) arrayCell.textContent = "...";
      if (rowCell) rowCell.textContent = "...";
      if (rebuildProgress) {
        rebuildProgress.removeAttribute("hx-get");
        rebuildProgress.removeAttribute("hx-trigger");
        rebuildProgress.removeAttribute("hx-target");
        rebuildProgress.removeAttribute("hx-swap");
        rebuildProgress.textContent = "Loading rebuild status...";
      }
      topology = null;
      updateRunButton();
      updateRunStep3Button();
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
    if (newSerialInput) newSerialInput.addEventListener("input", updateRunStep3Button);

    async function loadTopology() {
      if (!topologyUrl || !dgCell || !arrayCell || !rowCell) return;
      topology = null;
      dgCell.textContent = "loading...";
      arrayCell.textContent = "loading...";
      rowCell.textContent = "loading...";
      updateRunStep3Button();
      try {
        const response = await getJson(topologyUrl);
        if (!response.ok || response.json === null) {
          dgCell.textContent = "error";
          arrayCell.textContent = "error";
          rowCell.textContent = "error";
          return;
        }
        topology = {
          dg: response.json.dg,
          array: response.json.array,
          row: response.json.row,
        };
        dgCell.textContent = String(topology.dg);
        arrayCell.textContent = String(topology.array);
        rowCell.textContent = String(topology.row);
      } catch (_error) {
        dgCell.textContent = "error";
        arrayCell.textContent = "error";
        rowCell.textContent = "error";
      } finally {
        updateRunStep3Button();
      }
    }

    function startRebuildPolling() {
      if (!rebuildProgress || !rebuildStatusUrl || !window.htmx) return;
      rebuildProgress.setAttribute("hx-get", rebuildStatusUrl);
      rebuildProgress.setAttribute("hx-trigger", "load, every 30s");
      rebuildProgress.setAttribute("hx-target", "this");
      rebuildProgress.setAttribute("hx-swap", "innerHTML");
      window.htmx.process(rebuildProgress);
    }

    root.addEventListener("click", async function (evt) {
      const button = actionTarget(evt.target);
      if (!button) return;

      const action = button.dataset.replaceAction;
      if (inFlight) return;

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
        setInFlight(true);
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
          if (!missing.ok) return;

          show("physical-swap");
        } catch (error) {
          appendRequestError("replace request failed", error);
        } finally {
          setInFlight(false);
        }
      }
      if (action === "continue-to-insert") {
        show("insert");
        await loadTopology();
      }
      if (action === "run-step3") {
        if (topology === null) return;
        setInFlight(true);
        show("result");

        const body = {
          serial_number: newSerialInput.value.trim(),
          dry_run: dryRunStep3Input.checked,
        };
        try {
          output.textContent = "Running step 3 insert...\n";
          const insertResponse = await postJson(insertUrl, body);
          appendResult("insert response", insertResponse);
          if (insertResponse.ok && !body.dry_run) {
            show("rebuild");
            startRebuildPolling();
          }
        } catch (error) {
          appendRequestError("replace request failed", error);
        } finally {
          setInFlight(false);
        }
      }
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("[data-replace-wizard]").forEach(init);
  });
})();
