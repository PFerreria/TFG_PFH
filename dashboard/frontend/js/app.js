let currentView = "map";
let ws = null;
let wsReconnectTimer = null;
let activePollTimer = null;
let dashboardPollTimer = null;
let callPanel = null;
let callQueuePanel = null;

const _TRANSCRIPT_PLACEHOLDERS = new Set([
  "Transcripcion en vivo...",
  "Transcripción en vivo...",
  "Escuchando...",
  "Conectando simulación...",
  "Procesando grabación...",
  "",
]);

function _hasRealTranscript() {
  const el = document.getElementById("dm-transcript");
  if (!el) return false;
  const text = (el.textContent || "").trim();
  return text.length > 0 && !_TRANSCRIPT_PLACEHOLDERS.has(text);
}

function _fillDispatchForm(norm, force = false, reportType = null) {
  if (!force && !_hasRealTranscript()) return;

  const _set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = val != null ? val : "";
  };

  _set("dm-type",  norm.type  || "");
  _set("dm-addr",  norm.addr  || "");
  _set("dm-units", norm.units || "");
  _set("dm-rt",    norm.rt    || "");

  const dmSev = document.getElementById("dm-sev");
  if (dmSev) dmSev.value = norm.sev || "medium";

  let protocolText = norm.protocol || "";
  if (norm.protocolActions && norm.protocolActions.length > 0) {
    const steps = norm.protocolActions.map((a, i) => `${i + 1}. ${a}`).join("\n");
    protocolText = (protocolText ? protocolText + "\n\n" : "") + steps;
  }
  if (norm.protocolEscalation) {
    protocolText += (protocolText ? "\n\n" : "") + "⚠ ESCALADO: " + norm.protocolEscalation;
  }
  _set("dm-protocol", protocolText);

  const mapsLink = document.getElementById("dm-maps-link");
  if (mapsLink) {
    if (norm.mapsUrl) {
      mapsLink.href        = norm.mapsUrl;
      mapsLink.style.display = "inline-block";
    } else {
      mapsLink.style.display = "none";
    }
  }
}

let _dispatchCountdownTimer = null;
let _dispatchEndTime        = null;

let _activeCallSessionId = null;

function setView(view) {
  currentView = view;
  document.querySelectorAll(".screen").forEach((s) => s.classList.remove("active"));
  document.querySelectorAll(".nav-tab").forEach((t) => t.classList.remove("active"));

  const screen = document.getElementById(`screen-${view}`);
  if (screen) screen.classList.add("active");

  const tabIdx = { map: 0, history: 1, predict: 2, knowledge: 3 }[view];
  const tabs = document.querySelectorAll(".nav-tab");
  if (tabs[tabIdx]) tabs[tabIdx].classList.add("active");

  if (view === "map" && typeof leafMap !== "undefined" && leafMap) {
    setTimeout(() => leafMap.invalidateSize(), 50);
  }
  if (view === "predict") {
    refreshDashboard().then(_renderAll);
  }
}

function _setSystemStatus(label, ok) {
  const dot = document.getElementById("sys-dot");
  const text = document.getElementById("sys-label");
  if (text) text.textContent = label;
  if (dot) dot.style.background = ok ? "var(--teal)" : "var(--red)";
}

function _renderClock() {
  const el = document.getElementById("clock");
  if (el) {
    el.textContent = new Date().toLocaleTimeString("es-ES", { hour12: false });
  }
}

function _renderKpis() {
  const active = INCS.filter((i) => i.status === "active" || i.status === "en_route").length;
  const today = HISTORY.filter((i) => Date.now() - i.ts < 24 * 3600 * 1000).length + active;
  const overall = KPI && KPI.overall ? KPI.overall : {};
  const meanRt = overall.mean || _avgRt(INCS) || _avgRt(HISTORY) || 0;
  const unitsAvail = KPI ? KPI.available_units : null;
  const unitsTotal = KPI ? KPI.total_units : null;

  document.getElementById("kv-active").textContent = active || "0";
  document.getElementById("kv-today").textContent = today || "0";
  document.getElementById("kv-rt").innerHTML = `${meanRt ? meanRt.toFixed(1) : "—"}<span> min</span>`;
  document.getElementById("kv-units").innerHTML = (unitsAvail != null && unitsTotal != null)
    ? `${unitsAvail}<span>/${unitsTotal}</span>`
    : "—";

  const critical = INCS.filter((i) => i.sev === "critical").length;
  const badge = document.getElementById("alert-badge");
  const critTxt = document.getElementById("crit-count");
  if (badge && critTxt) {
    if (critical > 0) {
      badge.style.display = "flex";
      critTxt.textContent = `${critical} CRITICOS`;
    } else {
      badge.style.display = "none";
    }
  }
}

function _avgRt(list) {
  const vals = list.map((i) => Number(i.rt)).filter((n) => Number.isFinite(n) && n > 0);
  if (!vals.length) return 0;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

function _renderTicker() {
  const ticker = document.getElementById("ticker-txt");
  if (!ticker) return;

  const critical = INCS
    .filter((i) => i.sev === "critical")
    .slice(0, 3)
    .map((i) => `<span class="t-alert">ALERTA:  </span> ${_escHtml(i.id)} · ${_escHtml(i.type)} · ${_escHtml(i.addr)}`);

  const topHotspots = HOTSPOTS
    .slice(0, 2)
    .map((h) => `HOTSPOT ${_escHtml(h.area)} · riesgo ${h.risk} · ${h.count} incidentes`);

  const source = [
    ...critical,
    ...topHotspots,
    `SISTEMA IMERS · ${INCS.length} incidentes activos`,
    `CONOCIMIENTO · ${KB.length} protocolos indexados`,
  ].filter(Boolean);

  const sep = `<span class="t-sep">///</span>`;
  const line = source.join(` ${sep} `);
  ticker.innerHTML = `${line} ${sep} ${line}`;
}

function _renderAll() {
  renderIncList();
  renderMapOverlays();
  renderSeverityBars();
  renderTypeBars();
  renderHsMini();
  renderHistory();
  renderPredictions();
  renderKB();
  _renderKpis();
  _renderTicker();
}

function _initCallLogic() {
  const answerBtn = document.getElementById("call-answer-btn");
  const simulateBtn = document.getElementById("call-simulate-btn");
  const stateEl = document.getElementById("call-state");
  const sessionEl = document.getElementById("call-session");
  const transcriptEl = document.getElementById("call-transcript");
  if (!answerBtn || !stateEl || !sessionEl || !transcriptEl) return;

  if (typeof window.CallPanel !== "function") {
    stateEl.textContent = "Estado: no disponible";
    answerBtn.disabled = true;
    if (simulateBtn) simulateBtn.disabled = true;
    return;
  }

  callPanel = new window.CallPanel({
    onPreliminaryReport: (report) => {
      if (_dispatchSessionFinalReceived) return;
      if (report) {
        pendingDispatchReport = report;
        _fillDispatchForm(_normaliseIncident(report), false, "preliminary");
      }
      if (typeof window.updateDispatchMap === 'function') {
        window.updateDispatchMap(report);
      }
    },
    onFinalReport: (report) => {
      if (report) {
        _dispatchSessionFinalReceived = true;
        pendingDispatchReport = report;
        _fillDispatchForm(_normaliseIncident(report), true, "final");
        if (typeof window.updateDispatchMap === 'function') {
          window.updateDispatchMap(report);
        }
      }
    },
    onTranscriptUpdate: (text) => {
      transcriptEl.textContent = text || "Transcripcion en vivo...";
      const dmTranscript = document.getElementById("dm-transcript");
      if (dmTranscript) dmTranscript.textContent = text || "Transcripción en vivo...";
    },
  });

  answerBtn.addEventListener("click", async () => {
    if (_pendingRecordingSession) {
      answerBtn.classList.remove("call-btn-recording");
      _pendingRecordingSession = null;
      const preview = pendingDispatchReport && pendingDispatchReport.transcript_preview;
      const transcriptText = preview || "Procesando grabación...";
      transcriptEl.textContent = transcriptText;
      const dmTranscript = document.getElementById("dm-transcript");
      if (dmTranscript) dmTranscript.textContent = transcriptText;
      openDispatchModal();
      return;
    }
    transcriptEl.textContent = "Escuchando...";
    try {
      await callPanel.answerCall();
    } catch (_err) {
      stateEl.textContent = "Estado: error de microfono/ws";
    }
  });

  if (simulateBtn) {
    simulateBtn.addEventListener("click", async () => {
      transcriptEl.textContent = "Conectando simulación...";
      try {
        await callPanel.simulateCall();
      } catch (_err) {
        stateEl.textContent = "Estado: error de simulación/ws";
      }
    });
  }

  document.addEventListener("callStateChange", (event) => {
    const detail = event && event.detail ? event.detail : {};
    const state = detail.state || "idle";
    const sessionId = detail.sessionId || "—";
    stateEl.textContent = `Estado: ${state}`;
    sessionEl.textContent = `Sesion: ${sessionId}`;
    answerBtn.disabled = state === "recording" || state === "connecting";
    if (simulateBtn) simulateBtn.disabled = state === "recording" || state === "connecting";
    if (state === "idle") {
      transcriptEl.textContent = "Transcripcion en vivo...";
    } else if (state === "recording") {
      if (detail.sessionId && detail.sessionId !== "—") {
        _activeCallSessionId = detail.sessionId;
      }
      openDispatchModal();
    }
  });
}


function _connectWs() {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${window.location.host}/ws/incidents`);

  ws.onopen = () => {
    _setSystemStatus("EN VIVO", true);
  };

  ws.onmessage = (event) => {
    try {
      const msg = JSON.parse(event.data);

      if (msg.type === "new_incident" || msg.type === "incident_report") {
        if (msg.data) {
          upsertIncident(msg.data);
          _renderAll();
        }
      }

      if (msg.type === "incident_discarded" && msg.incident_id) {
        INCS = INCS.filter(i => i.id !== msg.incident_id);
        _renderAll();
      }

      if (
        msg.type === "incident_report" &&
        (msg.report_type === "preliminary" || msg.report_type === "final") &&
        msg.session_id && msg.session_id === _activeCallSessionId &&
        msg.data
      ) {
        const isFinal = msg.report_type === "final";

        if (!isFinal && _dispatchSessionFinalReceived) {
          console.log(`[WS] Ignoring late preliminary broadcast — final already received (session ${msg.session_id})`);
        } else {
          if (isFinal) _dispatchSessionFinalReceived = true;
          pendingDispatchReport = msg.data;
          _fillDispatchForm(_normaliseIncident(msg.data), isFinal, msg.report_type);

          const _preview = msg.data.transcript_preview;
          if (_preview && _preview.trim()) {
            const _callTr = document.getElementById("call-transcript");
            if (_callTr && _TRANSCRIPT_PLACEHOLDERS.has((_callTr.textContent || "").trim())) {
              _callTr.textContent = _preview;
            }
            const _dmTr = document.getElementById("dm-transcript");
            if (_dmTr && _TRANSCRIPT_PLACEHOLDERS.has((_dmTr.textContent || "").trim())) {
              _dmTr.textContent = _preview;
            }
          }

          if (typeof window.updateDispatchMap === "function") {
            window.updateDispatchMap(msg.data);
          }
          console.log(`[WS] Dispatch modal filled from ${msg.report_type} broadcast (session ${msg.session_id})`);
        }
      }

      if (msg.type === "queue_update" && callQueuePanel) {
        callQueuePanel.handleQueueUpdate(msg);
      }
      if (msg.type === "mic_ready" && callQueuePanel) {
        callQueuePanel.handleMicReady(msg);
      }
      if (msg.type === "session_ended") {
        if (callQueuePanel) callQueuePanel.handleSessionEnded(msg);
        if (_pendingRecordingSession && msg.session_id === _pendingRecordingSession) {
          const ab = document.getElementById("call-answer-btn");
          if (ab) {
            ab.classList.remove("call-btn-recording");
            ab.classList.add("call-btn-flash");
            setTimeout(() => ab.classList.remove("call-btn-flash"), 2000);
          }
          const st = document.getElementById("call-state");
          if (st) st.textContent = "Estado: informe listo — pulse Responder";
        }
      }
      if (msg.type === "recording_started") {
        _activeCallSessionId         = msg.session_id;
        _pendingRecordingSession      = msg.session_id;
        _dispatchSessionFinalReceived = false;
        pendingDispatchReport         = null;
        const ab = document.getElementById("call-answer-btn");
        if (ab) ab.classList.add("call-btn-recording");
        const st = document.getElementById("call-state");
        if (st) st.textContent = "Estado: grabación en proceso";
        if (callQueuePanel) callQueuePanel.handleRecordingStarted(msg);
      }

    } catch (_err) {
    }
  };

  ws.onerror = () => {
    _setSystemStatus("DEGRADADO", false);
  };

  ws.onclose = () => {
    _setSystemStatus("RECONECTANDO", false);
    if (wsReconnectTimer) clearTimeout(wsReconnectTimer);
    wsReconnectTimer = setTimeout(_connectWs, 2000);
  };
}

async function _bootstrap() {
  _setSystemStatus("CARGANDO", false);
  _renderClock();
  setInterval(_renderClock, 1000);

  await loadAllData();
  try { initMap(); } catch (mapErr) { console.error("[bootstrap] initMap failed — Leaflet may be unavailable:", mapErr); }
  _initCallLogic();

  if (typeof window.CallQueuePanel === "function") {
    callQueuePanel = new window.CallQueuePanel({
      onMicReady: (item) => {
        const answerBtn = document.getElementById("call-answer-btn");
        if (answerBtn && !answerBtn.disabled) {
          answerBtn.classList.add("call-btn-flash");
          setTimeout(() => answerBtn.classList.remove("call-btn-flash"), 2000);
        }
      },
    });
  }

  _renderAll();
  _connectWs();

  if (activePollTimer) clearInterval(activePollTimer);
  activePollTimer = setInterval(async () => {
    await refreshActive();
    _renderAll();
  }, 60000);

  if (dashboardPollTimer) clearInterval(dashboardPollTimer);
  dashboardPollTimer = setInterval(async () => {
    await refreshDashboard();
    if (currentView === "predict" || currentView === "map") {
      _renderAll();
    }
  }, 300000);
}

document.addEventListener("DOMContentLoaded", () => {
  _bootstrap().catch((err) => {
    console.error("[app.js] bootstrap failed:", err);
    _setSystemStatus("ERROR", false);
  });
});

let pendingDispatchReport = null;
let _dispatchSessionFinalReceived = false;
let _pendingRecordingSession = null;

function hangUpCall() {
  if (callPanel) callPanel.hangUp();
  const btn = document.getElementById("dm-hangup-btn");
  if (btn) { btn.textContent = "Llamada colgada"; btn.disabled = true; }
}

function openDispatchModal() {
  const modal = document.getElementById("dispatch-modal");
  if (modal) modal.style.display = "flex";

  const hangupBtn = document.getElementById("dm-hangup-btn");
  if (hangupBtn) { hangupBtn.textContent = "Colgar Llamada"; hangupBtn.disabled = false; }

  const dmUnitsInput = document.getElementById("dm-units");
  if (dmUnitsInput && !dmUnitsInput.dataset.listenerAttached) {
    dmUnitsInput.addEventListener("input", () => {
      if (typeof window.updateDispatchMapFromInput === 'function') {
        window.updateDispatchMapFromInput();
      }
    });
    dmUnitsInput.dataset.listenerAttached = "true";
  }

  const dmAddrInput = document.getElementById("dm-addr");
  if (dmAddrInput && !dmAddrInput.dataset.listenerAttached) {
    dmAddrInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); refreshGeocode(); }
    });
    dmAddrInput.addEventListener("change", () => refreshGeocode());
    dmAddrInput.dataset.listenerAttached = "true";
  }

  if (typeof window.initDispatchMap === 'function') {
    window.initDispatchMap();
  }

  const _aiReady = !!pendingDispatchReport;
  const _placeholder = _aiReady ? "" : "Procesando IA...";
  if (document.getElementById("dm-type"))     document.getElementById("dm-type").value     = _aiReady ? "" : _placeholder;
  if (document.getElementById("dm-sev"))      document.getElementById("dm-sev").value      = "medium";
  if (document.getElementById("dm-addr"))     document.getElementById("dm-addr").value     = _aiReady ? "" : _placeholder;
  if (document.getElementById("dm-units"))    document.getElementById("dm-units").value    = "";
  if (document.getElementById("dm-protocol")) document.getElementById("dm-protocol").value = _aiReady ? "" : _placeholder;
  if (document.getElementById("dm-rt"))       document.getElementById("dm-rt").value       = "";
  const _mapsLink = document.getElementById("dm-maps-link");
  if (_mapsLink) _mapsLink.style.display = "none";

  const sidebarTranscript = document.getElementById("call-transcript");
  const dmTranscript = document.getElementById("dm-transcript");
  if (dmTranscript) {
    const liveText = sidebarTranscript ? sidebarTranscript.textContent : "";
    const isEmpty = !liveText || liveText === "Transcripcion en vivo...";
    dmTranscript.textContent = isEmpty ? "Escuchando..." : liveText;
  }

  if (pendingDispatchReport) {
    const _preType = _dispatchSessionFinalReceived ? "final" : "preliminary";
    _fillDispatchForm(_normaliseIncident(pendingDispatchReport), _dispatchSessionFinalReceived, _preType);
    if (typeof window.updateDispatchMap === 'function') {
      window.updateDispatchMap(pendingDispatchReport);
    }
  }
}

function closeDispatchModal() {
  const modal = document.getElementById("dispatch-modal");
  if (modal) modal.style.display = "none";
  if (typeof window.clearDispatchMap === 'function') {
    window.clearDispatchMap();
  }
  if (callPanel && (callPanel.state === "recording" || callPanel.state === "connecting" || callPanel.state === "stopping")) {
    callPanel.hangUp();
  }
  const hangupBtn = document.getElementById("dm-hangup-btn");
  if (hangupBtn) {
    hangupBtn.textContent = "Colgar Llamada";
    hangupBtn.style.background = "";
    hangupBtn.disabled = false;
  }
  if (callPanel && callPanel.state === "hung_up") {
    callPanel._state = "idle";
    callPanel._notifyUI();
  }
  _clearDispatchCountdown();
  pendingDispatchReport             = null;
  _activeCallSessionId              = null;
  _dispatchSessionFinalReceived     = false;
  _pendingRecordingSession          = null;
  const stateEl = document.getElementById("call-state");
  if (stateEl && stateEl.textContent.startsWith("Estado: informe")) {
    stateEl.textContent = "Estado: inactivo";
  }
}

async function discardCase() {
  const incidentId = pendingDispatchReport
    ? (pendingDispatchReport.incident_id || pendingDispatchReport.id)
    : null;

  if (incidentId) {
    INCS = INCS.filter(i => i.id !== incidentId);
    _renderAll();
  }

  closeDispatchModal();

  if (incidentId) {
    fetch(`/api/incidents/${encodeURIComponent(incidentId)}`, { method: "DELETE" })
      .catch(e => console.error("[discardCase] Error:", e));
  }
}

let _geocodeInFlight = false;
async function refreshGeocode() {
  const addrField = document.getElementById("dm-addr");
  const mapsLink  = document.getElementById("dm-maps-link");
  if (!addrField || _geocodeInFlight) return;

  const address = addrField.value.trim();
  if (!address
      || address === "—"
      || address.toLowerCase().includes("procesando")
      || address.toLowerCase().startsWith("dirección no localizada")) return;

  _geocodeInFlight = true;
  addrField.style.opacity = "0.6";

  try {
    const res  = await fetch(`/api/geocode?address=${encodeURIComponent(address)}`);
    const data = await res.json();

    if (data.found) {
      if (pendingDispatchReport) {
        pendingDispatchReport.lat       = data.lat;
        pendingDispatchReport.lon       = data.lon;
        pendingDispatchReport.latitude  = data.lat;
        pendingDispatchReport.longitude = data.lon;
        pendingDispatchReport.addr      = data.address;
        pendingDispatchReport.address   = data.address;
        if (pendingDispatchReport.location) {
          pendingDispatchReport.location.address   = data.address;
          pendingDispatchReport.location.latitude  = data.lat;
          pendingDispatchReport.location.longitude = data.lon;
        }
        pendingDispatchReport.mapsUrl   = data.map_url;
      }

      if (typeof window.updateDispatchMap === "function") {
        window.updateDispatchMap(
          pendingDispatchReport || { lat: data.lat, lon: data.lon,
            units: (document.getElementById("dm-units") || {}).value || "" }
        );
      }

      if (mapsLink) {
        mapsLink.href          = data.map_url;
        mapsLink.style.display = "inline-block";
      }

      addrField.value             = data.address;
      addrField.style.borderColor = "var(--teal)";
      setTimeout(() => { addrField.style.borderColor = ""; }, 1500);

    } else {
      addrField.style.borderColor = "var(--red)";
      setTimeout(() => { addrField.style.borderColor = ""; }, 1500);
    }
  } catch (e) {
    console.error("[refreshGeocode]", e);
    addrField.style.borderColor = "var(--red)";
    setTimeout(() => { addrField.style.borderColor = ""; }, 1500);
  } finally {
    addrField.style.opacity = "";
    _geocodeInFlight = false;
  }
}

function _startDispatchCountdown(rtMinutes) {
  const dispBtn     = document.getElementById("dm-dispatch-btn");
  const arrivalZone = document.getElementById("dm-arrival-zone");
  const countdownEl = document.getElementById("dm-countdown");
  const arriveBtn   = document.getElementById("dm-arrive-btn");

  if (dispBtn)     { dispBtn.style.display = "none"; }
  if (arrivalZone) { arrivalZone.style.display = "flex"; }

  const hasEstimate = rtMinutes != null && rtMinutes > 0;
  _dispatchEndTime = hasEstimate ? Date.now() + rtMinutes * 60 * 1000 : null;

  let elapsedSec = 0;

  function _tick() {
    if (!countdownEl) return;

    if (hasEstimate) {
      const remaining = _dispatchEndTime - Date.now();

      if (remaining <= 0) {
        countdownEl.textContent = "⏱ 00:00";
        countdownEl.className   = "dm-countdown dm-overdue";
        if (arriveBtn) arriveBtn.classList.add("dm-arrive-pulse");
        clearInterval(_dispatchCountdownTimer);
        _dispatchCountdownTimer = null;
        return;
      }

      const totalSec = Math.ceil(remaining / 1000);
      const m        = Math.floor(totalSec / 60);
      const s        = totalSec % 60;
      countdownEl.textContent = `⏱ ${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;

      const pct = remaining / (rtMinutes * 60 * 1000);
      countdownEl.className =
        pct > 0.40 ? "dm-countdown" :
        pct > 0.15 ? "dm-countdown dm-warn" :
                     "dm-countdown dm-urgent";
    } else {
      elapsedSec++;
      const m = Math.floor(elapsedSec / 60);
      const s = elapsedSec % 60;
      countdownEl.textContent = `⏱ +${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
      countdownEl.className   = "dm-countdown";
    }
  }

  _tick();
  _dispatchCountdownTimer = setInterval(_tick, 1000);
}

function _clearDispatchCountdown() {
  if (_dispatchCountdownTimer) {
    clearInterval(_dispatchCountdownTimer);
    _dispatchCountdownTimer = null;
  }
  _dispatchEndTime = null;

  const dispBtn     = document.getElementById("dm-dispatch-btn");
  const arrivalZone = document.getElementById("dm-arrival-zone");
  const countdownEl = document.getElementById("dm-countdown");
  const arriveBtn   = document.getElementById("dm-arrive-btn");

  if (dispBtn)     { dispBtn.style.display = ""; dispBtn.disabled = false; dispBtn.textContent = "Enviar Despacho"; dispBtn.style.background = ""; }
  if (arrivalZone) { arrivalZone.style.display = "none"; }
  if (countdownEl) { countdownEl.textContent = "⏱ --:--"; countdownEl.className = "dm-countdown"; }
  if (arriveBtn)   { arriveBtn.classList.remove("dm-arrive-pulse"); arriveBtn.disabled = false; arriveBtn.textContent = "✓ Llegada confirmada"; }
}

async function confirmDispatchArrival() {
  const arriveBtn = document.getElementById("dm-arrive-btn");

  if (arriveBtn) { arriveBtn.disabled = true; arriveBtn.textContent = "Confirmando..."; }

  const incidentId = pendingDispatchReport
    ? (pendingDispatchReport.incident_id || pendingDispatchReport.id)
    : null;

  if (pendingDispatchReport) {
    pendingDispatchReport.status = "resolved";
    upsertIncident(pendingDispatchReport);
    _renderAll();
  }

  closeDispatchModal();

  if (incidentId) {
    fetch(`/api/incidents/${encodeURIComponent(incidentId)}/resolve`, {
      method: "POST",
    }).catch(e => console.error("[confirmDispatchArrival] resolve error:", e));
  }
}

async function sendDispatchModal() {
  if (!pendingDispatchReport) {
    const sendBtn = document.querySelector("#dispatch-modal .call-btn-primary");
    if (sendBtn) {
      const origText = sendBtn.textContent;
      sendBtn.textContent = "⚠ Sin datos de IA";
      sendBtn.style.background = "var(--amber)";
      setTimeout(() => { sendBtn.textContent = origText; sendBtn.style.background = ""; }, 2500);
    }
    return;
  }

  const report = pendingDispatchReport;

  const typeVal     = (document.getElementById("dm-type")     || {}).value || "";
  const sevVal      = (document.getElementById("dm-sev")      || {}).value || "";
  const addrVal     = (document.getElementById("dm-addr")     || {}).value || "";
  const unitsVal    = (document.getElementById("dm-units")    || {}).value || "";
  const rtRaw       = (document.getElementById("dm-rt")       || {}).value;
  const protocolVal = (document.getElementById("dm-protocol") || {}).value || "";
  const rtVal       = parseFloat(rtRaw);

  const _addrBlank = !addrVal.trim()
    || addrVal.trim() === "—"
    || addrVal.toLowerCase().includes("procesando")
    || addrVal.toLowerCase().startsWith("dirección no localizada");
  if (_addrBlank) {
    const addrField = document.getElementById("dm-addr");
    if (addrField) {
      addrField.style.borderColor = "var(--red)";
      addrField.focus();
      addrField.select();
      addrField.addEventListener("input", () => { addrField.style.borderColor = ""; }, { once: true });
    }
    return;
  }

  report.type           = typeVal;
  report.incident_type  = typeVal;
  report.sev            = sevVal;
  report.severity       = sevVal;
  report.addr           = addrVal;
  report.address        = addrVal;
  report.units          = unitsVal;
  report.units_dispatched = unitsVal;
  if (!report.dispatch) report.dispatch = {};
  report.dispatch.total_units = unitsVal
    ? unitsVal.split("\n").filter(l => l.trim()).length
    : 0;
  report.rt                = isNaN(rtVal) ? null : rtVal;
  report.response_time_min = isNaN(rtVal) ? null : rtVal;
  if (!report.procedure) report.procedure = {};
  report.procedure.protocol_code = protocolVal;
  report.protocol = protocolVal;

  upsertIncident(report);
  _renderAll();

  const incidentId = report.incident_id || report.id;
  const sendBtn = document.querySelector("#dispatch-modal .call-btn-primary");

  if (incidentId) {
    try {
      const res = await fetch(`/api/incidents/${encodeURIComponent(incidentId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          incident_type:     typeVal     || null,
          severity:          sevVal      || null,
          address:           addrVal     || null,
          units_dispatched:  unitsVal    || null,
          response_time_min: isNaN(rtVal) ? null : rtVal,
          protocol:          protocolVal || null,
          status:            "en_route",
        }),
      });

      if (res.ok) {
        report.status = "en_route";
        upsertIncident(report);
        _renderAll();

        if (sendBtn) {
          sendBtn.textContent = "✓ En camino";
          sendBtn.style.background = "var(--teal)";
          sendBtn.disabled = true;
        }
        setTimeout(() => _startDispatchCountdown(isNaN(rtVal) ? null : rtVal), 900);
      } else {
        const err = await res.json().catch(() => ({}));
        console.error("[sendDispatch] PATCH failed:", res.status, err);
        if (sendBtn) {
          const origText = sendBtn.textContent;
          sendBtn.textContent = `✗ Error ${res.status}`;
          sendBtn.style.background = "var(--red)";
          setTimeout(() => { sendBtn.textContent = origText; sendBtn.style.background = ""; }, 3000);
        }
      }
    } catch (err) {
      console.error("[sendDispatch] Network error:", err);
      if (sendBtn) {
        const origText = sendBtn.textContent;
        sendBtn.textContent = "✗ Sin conexión";
        sendBtn.style.background = "var(--red)";
        setTimeout(() => { sendBtn.textContent = origText; sendBtn.style.background = ""; }, 3000);
      }
    }
  } else {
    console.warn("[sendDispatch] No incident_id — starting countdown without backend persist");
    if (sendBtn) {
      sendBtn.textContent = "✓ En camino";
      sendBtn.style.background = "var(--amber)";
      sendBtn.disabled = true;
    }
    setTimeout(() => _startDispatchCountdown(isNaN(rtVal) ? null : rtVal), 900);
  }
}

function _syncThemeToggle() {
  const isLight = document.documentElement.getAttribute("data-theme") === "light";
  const d = document.getElementById("theme-opt-dark");
  const l = document.getElementById("theme-opt-light");
  if (!d || !l) return;

  if (isLight) {
    d.style.color      = "#263544";
    d.style.background = "transparent";
    l.style.color      = "#b8700a";
    l.style.background = "rgba(184,112,10,0.10)";
    d.classList.remove("is-active");
    l.classList.add("is-active");
  } else {
    d.style.color = ""; d.style.background = "";
    l.style.color = ""; l.style.background = "";
    d.classList.add("is-active");
    l.classList.remove("is-active");
  }
}

function toggleTheme() {
  const html = document.documentElement;
  const isLight = html.getAttribute("data-theme") === "light";
  if (isLight) {
    html.removeAttribute("data-theme");
  } else {
    html.setAttribute("data-theme", "light");
  }
  _syncThemeToggle();
  if (typeof updateMapTileTheme === "function") updateMapTileTheme();
  if (typeof updateDispatchMapTileTheme === "function") updateDispatchMapTileTheme();
  try { localStorage.setItem("imers-theme", isLight ? "dark" : "light"); } catch (_) {}
}

document.addEventListener("DOMContentLoaded", _syncThemeToggle);

window.setView = setView;
window.toggleTheme = toggleTheme;
window.hangUpCall = hangUpCall;
window.openDispatchModal = openDispatchModal;
window.closeDispatchModal = closeDispatchModal;
window.sendDispatchModal = sendDispatchModal;
window.confirmDispatchArrival = confirmDispatchArrival;
window.discardCase = discardCase;
window.refreshGeocode = refreshGeocode;

Object.defineProperty(window, "callPanel", {
  get: () => callPanel,
  configurable: true,
});
