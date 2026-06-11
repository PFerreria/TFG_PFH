/**
 * js/app.js
 * ---------
 * App orchestrator for dashboard startup, nav, realtime sync and KPI/ticker UI.
 */

let currentView = "map";
let ws = null;
let wsReconnectTimer = null;
let activePollTimer = null;
let dashboardPollTimer = null;
let callPanel = null;
let callQueuePanel = null;

/**
 * Fill all dispatch-modal form fields from a normalised incident object.
 * Centralises Fix 5 (Google Maps link) and Fix 6 (protocol text) so every
 * code path that receives a report (onPreliminaryReport, onFinalReport, WS
 * broadcast, openDispatchModal re-seed) uses the same logic.
 *
 * @param {object} norm  Result of _normaliseIncident(rawReport)
 */
// Placeholder strings that indicate the transcript has no real content yet.
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

/**
 * @param {object} norm         Normalised incident from _normaliseIncident()
 * @param {boolean} [force]     Skip the transcript-presence guard (used for final reports)
 * @param {string}  [reportType] "preliminary" | "final" — drives the badge
 */
function _fillDispatchForm(norm, force = false, reportType = null) {
  // Do not populate any field if no real transcription has arrived yet,
  // unless this is a final report (which always supersedes).
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

// Dispatch-countdown state
let _dispatchCountdownTimer = null;
let _dispatchEndTime        = null;   // null = stopwatch mode (counting up)

// Active call session ID — set when a call starts recording, cleared when the
// dispatch modal is closed.  Used to match pipeline-report broadcasts to the
// current operator session even after the audio WebSocket has been closed.
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
      // Don't overwrite a final report that already arrived (race condition guard).
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
      // A queued recording is being processed — open dispatch modal without starting the mic.
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
      // Capture session ID so we can match pipeline broadcasts after the audio
      // WS closes (the backend always broadcasts the final report via /ws/incidents).
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

      // Pipeline report broadcast — fill the dispatch modal.
      // This is the fallback path for when the audio WebSocket was already
      // closed (AudioCapture._cleanup() closes it immediately after "hangup").
      // The backend now always broadcasts the report via /ws/incidents even
      // when the direct audio-WS send fails, so we catch it here.
      if (
        msg.type === "incident_report" &&
        (msg.report_type === "preliminary" || msg.report_type === "final") &&
        msg.session_id && msg.session_id === _activeCallSessionId &&
        msg.data
      ) {
        const isFinal = msg.report_type === "final";

        // Race-condition guard: once a final report has been received, ignore
        // any late-arriving preliminary report (slow LLM race).
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
        // If the ending session is a recording the operator hasn't viewed yet,
        // stop the continuous pulse and do a brief flash to draw attention.
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
      // Ignore malformed messages to keep dashboard alive.
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

  // Initialise call queue panel
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
// Tracks whether the current dispatch session has already received a final
// pipeline report.  Prevents a slow preliminary run from overwriting better
// final data when both pipelines race concurrently.
let _dispatchSessionFinalReceived = false;
// Session ID of a queued recording currently being processed.
// Set on "recording_started", cleared when the operator opens the modal or closes it.
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

  // Re-geocode when the operator edits the address: Enter key or leaving the
  // field (change). Replaces the old ↻ refresh button.
  const dmAddrInput = document.getElementById("dm-addr");
  if (dmAddrInput && !dmAddrInput.dataset.listenerAttached) {
    dmAddrInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); refreshGeocode(); }
    });
    dmAddrInput.addEventListener("change", () => refreshGeocode());
    dmAddrInput.dataset.listenerAttached = "true";
  }

  // Initialise mini map inside the dispatch modal (map must exist before
  // updateDispatchMap is called later, but route-drawing must happen AFTER
  // the form fields — especially dm-units — have been populated).
  if (typeof window.initDispatchMap === 'function') {
    window.initDispatchMap();
  }

  // Clear form fields; if no AI report has arrived yet, show a brief
  // "processing" placeholder so the operator knows the pipeline is running.
  const _aiReady = !!pendingDispatchReport;
  const _placeholder = _aiReady ? "" : "Procesando IA...";
  if (document.getElementById("dm-type"))     document.getElementById("dm-type").value     = _aiReady ? "" : _placeholder;
  if (document.getElementById("dm-sev"))      document.getElementById("dm-sev").value      = "medium";
  if (document.getElementById("dm-addr"))     document.getElementById("dm-addr").value     = _aiReady ? "" : _placeholder;
  if (document.getElementById("dm-units"))    document.getElementById("dm-units").value    = "";
  if (document.getElementById("dm-protocol")) document.getElementById("dm-protocol").value = _aiReady ? "" : _placeholder;
  if (document.getElementById("dm-rt"))       document.getElementById("dm-rt").value       = "";
  // Hide Maps link when clearing (shown again once a report arrives)
  const _mapsLink = document.getElementById("dm-maps-link");
  if (_mapsLink) _mapsLink.style.display = "none";

  // Seed modal transcript from the sidebar transcript so both stay in sync
  const sidebarTranscript = document.getElementById("call-transcript");
  const dmTranscript = document.getElementById("dm-transcript");
  if (dmTranscript) {
    const liveText = sidebarTranscript ? sidebarTranscript.textContent : "";
    const isEmpty = !liveText || liveText === "Transcripcion en vivo...";
    dmTranscript.textContent = isEmpty ? "Escuchando..." : liveText;
  }

  // If a report is already available, pre-fill the form immediately.
  // This handles the case where the modal is re-opened after being closed while
  // the AI was still processing and a report already arrived.
  // IMPORTANT: updateDispatchMap must be called AFTER _fillDispatchForm so that
  // dm-units is already populated when routes are fetched.
  if (pendingDispatchReport) {
    const _preType = _dispatchSessionFinalReceived ? "final" : "preliminary";
    _fillDispatchForm(_normaliseIncident(pendingDispatchReport), _dispatchSessionFinalReceived, _preType);
    if (typeof window.updateDispatchMap === 'function') {
      window.updateDispatchMap(pendingDispatchReport);
    }
  }

  // Do not clear pendingDispatchReport here; it will be used when sending
}

function closeDispatchModal() {
  const modal = document.getElementById("dispatch-modal");
  if (modal) modal.style.display = "none";
  // Clear mini map when modal is closed
  if (typeof window.clearDispatchMap === 'function') {
    window.clearDispatchMap();
  }
  // Also hang up the active call so the operator doesn't accidentally leave
  // the microphone open after dismissing the modal.
  if (callPanel && (callPanel.state === "recording" || callPanel.state === "connecting" || callPanel.state === "stopping")) {
    callPanel.hangUp();
  }
  // Reset hangup button to original state
  const hangupBtn = document.getElementById("dm-hangup-btn");
  if (hangupBtn) {
    hangupBtn.textContent = "Colgar Llamada";
    hangupBtn.style.background = "";
    hangupBtn.disabled = false;
  }
  // Reset CallPanel state to idle
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

/**
 * Discard the current pending case — removes it from the DB and all client
 * INCS lists. Used for fake calls, test calls, or pipeline errors.
 * Unlike "Cerrar caso", this permanently deletes the record.
 */
async function discardCase() {
  const incidentId = pendingDispatchReport
    ? (pendingDispatchReport.incident_id || pendingDispatchReport.id)
    : null;

  // Remove from local state immediately so sidebar updates at once
  if (incidentId) {
    INCS = INCS.filter(i => i.id !== incidentId);
    _renderAll();
  }

  closeDispatchModal();

  // Persist deletion to backend (fire-and-forget; UI already updated)
  if (incidentId) {
    fetch(`/api/incidents/${encodeURIComponent(incidentId)}`, { method: "DELETE" })
      .catch(e => console.error("[discardCase] Error:", e));
  }
}

/**
 * Re-geocode the address typed by the operator and refresh the mini-map,
 * Google Maps link, and unit-route ETAs.  Triggered by Enter / leaving the
 * address field (the old ↻ button was removed).
 *
 * This is the recovery path for calls where the pipeline could not resolve
 * the location ("Dirección no localizada..."): the operator types the address
 * heard on the call and the map, routes and response time fill in.
 */
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
  addrField.style.opacity = "0.6";   // visual feedback while waiting for Nominatim

  try {
    const res  = await fetch(`/api/geocode?address=${encodeURIComponent(address)}`);
    const data = await res.json();

    if (data.found) {
      // Update in-memory report so ETA/dispatch use the corrected coords
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

      // Refresh map marker and route lines.  Pass the full report so the
      // structured dispatch.units (with types) drive the route drawing.
      if (typeof window.updateDispatchMap === "function") {
        window.updateDispatchMap(
          pendingDispatchReport || { lat: data.lat, lon: data.lon,
            units: (document.getElementById("dm-units") || {}).value || "" }
        );
      }

      // Update Google Maps deep-link
      if (mapsLink) {
        mapsLink.href          = data.map_url;
        mapsLink.style.display = "inline-block";
      }

      // Update address field with the canonical resolved address
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


// ── Dispatch countdown helpers ────────────────────────────────────────────────

/**
 * Transition the modal from "Enviar Despacho" state to countdown + arrival state.
 * @param {number|null} rtMinutes  Estimated response time in minutes (null = stopwatch)
 */
function _startDispatchCountdown(rtMinutes) {
  const dispBtn     = document.getElementById("dm-dispatch-btn");
  const arrivalZone = document.getElementById("dm-arrival-zone");
  const countdownEl = document.getElementById("dm-countdown");
  const arriveBtn   = document.getElementById("dm-arrive-btn");

  // Swap button states
  if (dispBtn)     { dispBtn.style.display = "none"; }
  if (arrivalZone) { arrivalZone.style.display = "flex"; }

  const hasEstimate = rtMinutes != null && rtMinutes > 0;
  _dispatchEndTime = hasEstimate ? Date.now() + rtMinutes * 60 * 1000 : null;

  let elapsedSec = 0;   // used in stopwatch mode

  function _tick() {
    if (!countdownEl) return;

    if (hasEstimate) {
      // ── Countdown mode ──────────────────────────────────────────────────────
      const remaining = _dispatchEndTime - Date.now();

      if (remaining <= 0) {
        // Timer expired — units should have arrived
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

      // Colour transitions: green → amber (≤ 40 %) → red (≤ 15 %)
      const pct = remaining / (rtMinutes * 60 * 1000);
      countdownEl.className =
        pct > 0.40 ? "dm-countdown" :
        pct > 0.15 ? "dm-countdown dm-warn" :
                     "dm-countdown dm-urgent";
    } else {
      // ── Stopwatch mode (no ETA) ─────────────────────────────────────────────
      elapsedSec++;
      const m = Math.floor(elapsedSec / 60);
      const s = elapsedSec % 60;
      countdownEl.textContent = `⏱ +${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
      countdownEl.className   = "dm-countdown";
    }
  }

  _tick();   // immediate first render
  _dispatchCountdownTimer = setInterval(_tick, 1000);
}

/** Stop the timer and reset the modal back to the pre-dispatch button state. */
function _clearDispatchCountdown() {
  if (_dispatchCountdownTimer) {
    clearInterval(_dispatchCountdownTimer);
    _dispatchCountdownTimer = null;
  }
  _dispatchEndTime = null;

  // Reset DOM back to initial state for next use
  const dispBtn     = document.getElementById("dm-dispatch-btn");
  const arrivalZone = document.getElementById("dm-arrival-zone");
  const countdownEl = document.getElementById("dm-countdown");
  const arriveBtn   = document.getElementById("dm-arrive-btn");

  if (dispBtn)     { dispBtn.style.display = ""; dispBtn.disabled = false; dispBtn.textContent = "Enviar Despacho"; dispBtn.style.background = ""; }
  if (arrivalZone) { arrivalZone.style.display = "none"; }
  if (countdownEl) { countdownEl.textContent = "⏱ --:--"; countdownEl.className = "dm-countdown"; }
  if (arriveBtn)   { arriveBtn.classList.remove("dm-arrive-pulse"); arriveBtn.disabled = false; arriveBtn.textContent = "✓ Llegada confirmada"; }
}

/**
 * Operator confirms the units have arrived on scene from the DISPATCH MODAL.
 * Marks the incident as resolved, updates local state immediately, closes the
 * modal, then persists to the backend in the background.
 *
 * NOTE: This function is intentionally named confirmDispatchArrival (not
 * confirmArrival) so it does not collide with the identically-named function
 * in map.js which handles the same action from the detail panel.
 */
async function confirmDispatchArrival() {
  const arriveBtn = document.getElementById("dm-arrive-btn");

  // Disable immediately to prevent double-clicks
  if (arriveBtn) { arriveBtn.disabled = true; arriveBtn.textContent = "Confirmando..."; }

  // Capture what we need before closeDispatchModal() clears pendingDispatchReport
  const incidentId = pendingDispatchReport
    ? (pendingDispatchReport.incident_id || pendingDispatchReport.id)
    : null;

  // Update local state to resolved right away so the sidebar reflects the change
  if (pendingDispatchReport) {
    pendingDispatchReport.status = "resolved";
    upsertIncident(pendingDispatchReport);
    _renderAll();
  }

  // Close the modal immediately — no need to wait for the network
  closeDispatchModal();

  // Persist resolution to backend (fire-and-forget; UI already updated)
  if (incidentId) {
    fetch(`/api/incidents/${encodeURIComponent(incidentId)}/resolve`, {
      method: "POST",
    }).catch(e => console.error("[confirmDispatchArrival] resolve error:", e));
  }
}

async function sendDispatchModal() {
  if (!pendingDispatchReport) {
    // No AI report arrived yet — warn the operator instead of silently failing
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

  // ── 1. Read operator edits from the form ──────────────────────────────────
  const typeVal     = (document.getElementById("dm-type")     || {}).value || "";
  const sevVal      = (document.getElementById("dm-sev")      || {}).value || "";
  const addrVal     = (document.getElementById("dm-addr")     || {}).value || "";
  const unitsVal    = (document.getElementById("dm-units")    || {}).value || "";
  const rtRaw       = (document.getElementById("dm-rt")       || {}).value;
  const protocolVal = (document.getElementById("dm-protocol") || {}).value || "";
  const rtVal       = parseFloat(rtRaw);

  // ── Address validation ────────────────────────────────────────────────────
  // Block dispatch when the address is blank, a placeholder, or the pipeline's
  // "unresolved" message — the operator must type the location first.
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

  // Patch local report object (keep both snake_case and short aliases in sync)
  report.type           = typeVal;
  report.incident_type  = typeVal;
  report.sev            = sevVal;
  report.severity       = sevVal;
  report.addr           = addrVal;
  report.address        = addrVal;
  report.units          = unitsVal;
  // units_dispatched stays as a human string; the backend patch endpoint accepts this
  report.units_dispatched = unitsVal;
  if (!report.dispatch) report.dispatch = {};
  // Count non-empty lines to get the numeric unit count
  report.dispatch.total_units = unitsVal
    ? unitsVal.split("\n").filter(l => l.trim()).length
    : 0;
  report.rt                = isNaN(rtVal) ? null : rtVal;
  report.response_time_min = isNaN(rtVal) ? null : rtVal;
  if (!report.procedure) report.procedure = {};
  report.procedure.protocol_code = protocolVal;
  report.protocol = protocolVal;

  // ── 2. Update local in-memory state so the map/list updates immediately ───
  upsertIncident(report);
  _renderAll();

  // ── 3. Persist to the backend via PATCH ───────────────────────────────────
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
        // Update local state to en_route immediately
        report.status = "en_route";
        upsertIncident(report);
        _renderAll();

        // Brief acknowledgement on the button, then switch to countdown
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
    // No incident_id (shouldn't happen, but still start the countdown locally)
    console.warn("[sendDispatch] No incident_id — starting countdown without backend persist");
    if (sendBtn) {
      sendBtn.textContent = "✓ En camino";
      sendBtn.style.background = "var(--amber)";
      sendBtn.disabled = true;
    }
    setTimeout(() => _startDispatchCountdown(isNaN(rtVal) ? null : rtVal), 900);
  }
}

// -- THEME TOGGLE -------------------------------------------------
function _syncThemeToggle() {
  const isLight = document.documentElement.getAttribute("data-theme") === "light";
  const d = document.getElementById("theme-opt-dark");
  const l = document.getElementById("theme-opt-light");
  if (!d || !l) return;

  if (isLight) {
    // Light mode: apply inline styles — CSS var() chain doesn't re-evaluate
    // after data-theme changes in this browser; inline styles always win.
    d.style.color      = "#263544";              // light --t2
    d.style.background = "transparent";
    l.style.color      = "#b8700a";              // light --amber
    l.style.background = "rgba(184,112,10,0.10)"; // light --amber-d
    d.classList.remove("is-active");
    l.classList.add("is-active");
  } else {
    // Dark mode: remove inline overrides and let CSS classes drive it
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
  try { localStorage.setItem("imers-theme", isLight ? "dark" : "light"); } catch (_) {}
}

// Sync toggle UI on load (the data-theme attr may already be set by the inline
// <head> script before app.js ran)
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
// NOTE: window.confirmArrival is intentionally NOT set here.
// map.js exports its own confirmArrival(id) which handles the detail-panel
// "Confirmar Llegada" button and calls the /resolve endpoint correctly.

// Expose callPanel so inline onclick handlers in index.html can access it
// (let variables are not window properties, so explicit export is required
// once the modal hangup button calls callPanel.hangUp() from an attribute).
Object.defineProperty(window, "callPanel", {
  get: () => callPanel,
  configurable: true,
});