/* Audit Evidence Compiler — WebSocket-driven dashboard */
(function () {
  "use strict";

  var controlSelect = document.getElementById("control");
  var runBtn = document.getElementById("run-btn");
  var runForm = document.getElementById("run-form");
  var statusBar = document.getElementById("status-bar");
  var panelEl = document.getElementById("panel");
  var consensusEl = document.getElementById("consensus");
  var downloadsEl = document.getElementById("downloads");
  var errorBar = document.getElementById("error-bar");

  var ws = null;
  var running = false;

  // Load available controls from the API
  function loadControls() {
    fetch("/api/controls")
      .then(function (r) { return r.json(); })
      .then(function (controls) {
        controlSelect.innerHTML = "";
        controls.forEach(function (c) {
          var opt = document.createElement("option");
          opt.value = c.sample;
          opt.textContent = c.framework + " " + c.label;
          controlSelect.appendChild(opt);
        });
      })
      .catch(function () {
        var opt = document.createElement("option");
        opt.value = "soc2-cc61";
        opt.textContent = "SOC 2 CC6.1 — MFA enforcement";
        controlSelect.appendChild(opt);
      });
  }

  function show(el) { el.classList.remove("hidden"); }
  function hide(el) { el.classList.add("hidden"); }

  function resetUI() {
    hide(errorBar);
    hide(consensusEl);
    hide(downloadsEl);
    consensusEl.className = "consensus hidden";
    consensusEl.textContent = "";
    downloadsEl.innerHTML = "";

    ["auditor", "engineer", "adversary"].forEach(function (name) {
      var card = panelEl.querySelector('[data-name="' + name + '"]');
      card.querySelector(".status").textContent = "idle";
      card.querySelector(".status").classList.remove("thinking");
      card.querySelector(".reasoning").textContent = "";
      card.querySelector(".verdict-pill").textContent = "";
      card.querySelector(".verdict-pill").className = "verdict-pill";
      card.querySelector(".concerns").textContent = "";
    });
  }

  function setStatus(msg) {
    statusBar.textContent = msg;
    show(statusBar);
  }

  function showError(msg) {
    errorBar.textContent = msg;
    show(errorBar);
  }

  function updatePersona(persona, data) {
    var card = panelEl.querySelector('[data-name="' + persona + '"]');
    if (!card) return;

    var statusEl = card.querySelector(".status");
    var reasoningEl = card.querySelector(".reasoning");
    var verdictEl = card.querySelector(".verdict-pill");
    var concernsEl = card.querySelector(".concerns");

    if (data.status === "thinking") {
      statusEl.textContent = "analyzing...";
      statusEl.classList.add("thinking");
      if (data.rationale) {
        reasoningEl.textContent = data.rationale;
      }
    }

    if (data.status === "complete") {
      statusEl.textContent = data.model
        ? "done (" + data.model + ", " + data.latency_ms + "ms)"
        : "done";
      statusEl.classList.remove("thinking");
      reasoningEl.textContent = data.rationale || "";
      if (data.verdict) {
        verdictEl.textContent = data.verdict;
        verdictEl.className = "verdict-pill " + data.verdict;
      }
      if (data.confidence !== undefined) {
        verdictEl.textContent += " (" + Math.round(data.confidence * 100) + "%)";
      }
      if (data.concerns && data.concerns.length) {
        concernsEl.textContent = "Concerns: " + data.concerns.join("; ");
      }
    }
  }

  function handleMessage(event) {
    var msg;
    try { msg = JSON.parse(event.data); } catch (e) { return; }

    switch (msg.type) {
      case "run_start":
        setStatus("Run " + msg.run_id.slice(0, 8) + " started — sample: " + msg.sample);
        show(panelEl);
        break;

      case "phase":
        if (msg.name === "snapshot_fetch" && msg.status === "done") {
          setStatus(
            "Loaded " + msg.control_id + " (" + msg.framework + ") — " +
            msg.event_count + " events"
          );
        } else if (msg.name === "panel_debate" && msg.status === "start") {
          setStatus("Panel debate running — 4 personas analyzing evidence...");
        } else if (msg.name === "artifacts" && msg.status === "start") {
          setStatus("Writing artifacts...");
        }
        break;

      case "panel":
        updatePersona(msg.persona, msg);
        break;

      case "consensus":
        consensusEl.textContent = "CONSENSUS: " + msg.verdict + " (" + (msg.method || "lowest_of_three") + ")";
        consensusEl.className = "consensus " + msg.verdict;
        show(consensusEl);
        if (msg.degraded) {
          consensusEl.textContent += " [degraded]";
        }
        break;

      case "done":
        setStatus("Done — run " + msg.run_id.slice(0, 8));
        running = false;
        runBtn.disabled = false;
        runBtn.textContent = "Run debate";

        if (msg.artifacts) {
          downloadsEl.innerHTML = "";
          if (msg.artifacts.transcript) {
            var a = document.createElement("a");
            a.href = "/api/artifact/" + encodeURIComponent(msg.artifacts.transcript);
            a.textContent = "Download transcript";
            a.download = msg.artifacts.transcript;
            downloadsEl.appendChild(a);
          }
          if (msg.artifacts.memo) {
            var b = document.createElement("a");
            b.href = "/api/artifact/" + encodeURIComponent(msg.artifacts.memo);
            b.textContent = "Download audit memo";
            b.download = msg.artifacts.memo;
            downloadsEl.appendChild(b);
          }
          show(downloadsEl);
        }
        break;

      case "error":
        showError(msg.message);
        running = false;
        runBtn.disabled = false;
        runBtn.textContent = "Run debate";
        break;
    }
  }

  function startDebate() {
    if (running) return;
    running = true;
    runBtn.disabled = true;
    runBtn.textContent = "Running...";

    resetUI();
    show(panelEl);

    var sample = controlSelect.value;
    var protocol = location.protocol === "https:" ? "wss:" : "ws:";
    var wsUrl = protocol + "//" + location.host + "/ws/run";

    if (ws) {
      try { ws.close(); } catch (e) { /* ignore */ }
    }

    ws = new WebSocket(wsUrl);

    ws.onopen = function () {
      ws.send(JSON.stringify({ sample: sample }));
    };

    ws.onmessage = handleMessage;

    ws.onerror = function () {
      showError("WebSocket connection error");
      running = false;
      runBtn.disabled = false;
      runBtn.textContent = "Run debate";
    };

    ws.onclose = function () {
      if (running) {
        running = false;
        runBtn.disabled = false;
        runBtn.textContent = "Run debate";
      }
    };
  }

  runForm.addEventListener("submit", function (e) {
    e.preventDefault();
    startDebate();
  });

  loadControls();
})();
