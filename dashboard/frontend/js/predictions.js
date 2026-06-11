/**
 * js/predictions.js
 * -----------------
 * Screen 3 — Predicciones y zonas de riesgo
 *
 * Renders all five analytical blocks:
 *   1. 7-day forecast bar chart  (with historical daily-average baseline)
 *   2. Hourly distribution histogram  (with peak-hour annotation)
 *   3. Hotspot ranking table
 *   4. Incident type breakdown  (computed live from HISTORY + INCS globals)
 *   5. Response time by severity  (read live from KPI.by_severity)
 *
 * Public API:
 *   renderPredictions()   — populate all five blocks (called by app.js)
 *   refreshPredictions()  — re-fetch dashboard data then re-render (refresh btn)
 */

function renderPredictions() {
  _renderPredictMeta();
  _renderForecast();
  _renderHourly();
  _renderHotspotRanking();
  _renderTypeTrend();
  _renderResponseTimes();
}

async function refreshPredictions() {
  const btn = document.getElementById("pred-refresh-btn");
  if (btn) { btn.textContent = "↻ …"; btn.disabled = true; }
  await refreshDashboard();
  renderPredictions();
  if (btn) { btn.textContent = "↻ Actualizar"; btn.disabled = false; }
}

window.refreshPredictions = refreshPredictions;

// -- Meta: last-updated timestamp + data source --------------------------------

function _renderPredictMeta() {
  const el = document.getElementById("pred-updated");
  if (!el) return;

  const ts  = DASHBOARD_META && DASHBOARD_META.generated_at;
  const src = DASHBOARD_META && DASHBOARD_META.source;

  if (!ts) { el.textContent = "Sin datos"; return; }

  const secs = Math.floor((Date.now() - new Date(ts).getTime()) / 1000);
  let age;
  if (secs < 60)        age = "ahora mismo";
  else if (secs < 3600) age = `hace ${Math.floor(secs / 60)} min`;
  else                  age = `hace ${Math.floor(secs / 3600)} h`;

  el.textContent = `Actualizado ${age}`;
}

// -- 1. 7-day forecast ---------------------------------------------------------

function _renderForecast() {
  const fcEl = document.getElementById("fc-bars");
  if (!FORECAST.length) {
    fcEl.innerHTML = `<div style="font-family:var(--mono);font-size:8px;color:var(--t3)">Sin datos de predicción</div>`;
    return;
  }

  const avg    = (DASHBOARD_META && DASHBOARD_META.historical_daily_avg) || 0;
  const maxVal = Math.max(...FORECAST.map(d => d.v), avg, 1);
  const chartH = 80; // matches .forecast-bars { height: 80px } in CSS

  const barsHtml = FORECAST.map(({ d, v }) => {
    const color  = v >= 24 ? "var(--red)" : v >= 20 ? "var(--amber)" : "var(--blue)";
    const height = Math.round((v / maxVal) * chartH);
    return `
      <div class="fc-bar-wrap">
        <div class="fc-bar-val" style="color:${color}">${v}</div>
        <div class="fc-bar" style="height:${height}px;background:${color};opacity:.82"></div>
      </div>`;
  }).join("");

  // Dashed baseline for historical daily average
  let baselineHtml = "";
  if (avg > 0) {
    const bottomPct = Math.round((avg / maxVal) * 100);
    baselineHtml = `
      <div style="position:absolute;bottom:${bottomPct}%;left:0;right:0;pointer-events:none;z-index:1">
        <div style="border-top:1px dashed rgba(176,198,216,.35);width:100%"></div>
        <span style="position:absolute;right:0;top:-10px;
          font-family:var(--mono);font-size:6px;color:var(--t3);
          letter-spacing:.06em;background:var(--bg2);padding:0 3px">Ø ${avg.toFixed(0)}</span>
      </div>`;
  }

  fcEl.style.position = "relative";
  fcEl.innerHTML = barsHtml + baselineHtml;

  document.getElementById("fc-labels").innerHTML = FORECAST.map(({ d }) =>
    `<div class="fc-bar-lbl">${d}</div>`
  ).join("");
}

// -- 2. Hourly distribution ----------------------------------------------------

function _renderHourly() {
  if (!HOURLY.length) return;

  const maxVal   = Math.max(...HOURLY, 1);
  const peakHour = HOURLY.indexOf(maxVal);

  // Peak annotation above the chart
  const metaEl = document.getElementById("hourly-meta");
  if (metaEl) {
    metaEl.textContent = `Pico · ${peakHour}:00 h · ${maxVal} inc/h (promedio)`;
  }

  document.getElementById("hourly-bars").innerHTML = HOURLY.map((v, h) => {
    const color  = v >= 16 ? "var(--red)" : v >= 12 ? "var(--amber)" : "var(--blue)";
    const height = Math.round((v / maxVal) * 56);
    const isPeak = h === peakHour;
    return `<div class="hr-bar" style="
      height:${height}px;
      background:${color};
      opacity:${isPeak ? 1 : .72};
      flex:1;
      ${isPeak ? "box-shadow:0 0 6px rgba(255,255,255,.12);" : ""}
    "></div>`;
  }).join("");

  document.getElementById("hourly-labels").innerHTML = HOURLY.map((_, h) => {
    const isRegular = h % 4 === 0;
    const isPeak    = h === peakHour;
    const label     = (isRegular || isPeak) ? h + "h" : "";
    return `<div class="hr-lbl${isPeak ? " hr-lbl-peak" : ""}">${label}</div>`;
  }).join("");
}

// -- 3. Hotspot ranking --------------------------------------------------------

function _renderHotspotRanking() {
  const el = document.getElementById("hs-ranking");
  if (!HOTSPOTS.length) {
    el.innerHTML = `<div style="font-family:var(--mono);font-size:8px;color:var(--t3);padding:8px 0">Sin zonas identificadas</div>`;
    return;
  }

  el.innerHTML = HOTSPOTS.map((hs, i) => {
    const color     = hs.risk >= 80 ? "var(--red)"  : hs.risk >= 60 ? "var(--amber)" : "var(--blue)";
    const textColor = hs.risk >= 80 ? "#e07060"     : hs.risk >= 60 ? "#d4950a"      : "#5090cc";
    return `
      <div class="hs-table-row">
        <div class="hs-idx" style="color:${textColor}">${String(i + 1).padStart(2, "0")}</div>
        <div>
          <div class="hs-info-name">${_escHtml(hs.area)}</div>
          <div class="hs-info-sub">${hs.domCount || hs.count} INC. DE ${_escHtml(translateIncidentType(hs.dominant).toUpperCase())} · ${hs.count} TOTAL</div>
        </div>
        <div class="hs-bar-col">
          <div style="height:2px;background:var(--ln2);margin-bottom:5px">
            <div style="width:${hs.risk}%;height:100%;background:${color}"></div>
          </div>
        </div>
        <div class="hs-score" style="color:${textColor}">${hs.risk}</div>
      </div>`;
  }).join("");
}

// -- 4. Incident type breakdown — computed live from HISTORY + INCS -----------

function _renderTypeTrend() {
  const counts = {};

  // Count all historical incidents
  HISTORY.forEach(i => {
    const t = i.type || "other";
    counts[t] = (counts[t] || 0) + 1;
  });
  // Include active incidents in the tally
  INCS.forEach(i => {
    const t = i.type || "other";
    counts[t] = (counts[t] || 0) + 1;
  });

  const sorted = Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 6);

  const el = document.getElementById("type-trend");
  if (!sorted.length) {
    el.innerHTML = `<div style="font-family:var(--mono);font-size:8px;color:var(--t3);padding:8px 0">Sin historial disponible</div>`;
    return;
  }

  const maxCount = sorted[0][1];
  const palette  = ["var(--amber)", "var(--blue)", "var(--red)", "var(--teal)", "var(--red)", "var(--amber)"];
  el.innerHTML = sorted
    .map(([label, n], i) => bigBarRow(_escHtml(label), (n / maxCount) * 100, n, palette[i % palette.length]))
    .join("");
}

// -- 5. Response time by severity — read live from KPI.by_severity ------------

function _renderResponseTimes() {
  const bySev = (KPI && KPI.by_severity) ? KPI.by_severity : {};

  // Use the 'mean' field (both real AnalysisAgent output and updated mock schema).
  // Falls back to "—" placeholder if the field is missing rather than showing
  // a hardcoded number that looks like real data.
  const rows = [
    ["Crítico", bySev.critical?.mean ?? null, "var(--red)"  ],
    ["Alto",    bySev.high    ?.mean ?? null, "var(--amber)"],
    ["Medio",   bySev.medium  ?.mean ?? null, "var(--blue)" ],
    ["Bajo",    bySev.low     ?.mean ?? null, "var(--teal)" ],
  ];

  const validVals = rows.map(r => r[1]).filter(v => v !== null);
  const maxRt = validVals.length ? Math.max(...validVals, 1) : 1;
  document.getElementById("rt-chart").innerHTML = rows
    .map(([label, val, color]) => {
      const pct     = val !== null ? (val / maxRt) * 100 : 0;
      const display = val !== null ? val.toFixed(1) + "m" : "—";
      return bigBarRow(label, pct, display, color);
    })
    .join("");
}
