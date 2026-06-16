let leafMap   = null;
let tileLayer = null;
let markers   = [];
let circles   = [];
let baseMarkers = [];
let incFilter = "all";
let selId     = null;

const SEVILLA_LAT_MIN =  37.25;
const SEVILLA_LAT_MAX =  37.52;
const SEVILLA_LON_MIN = -6.12;
const SEVILLA_LON_MAX = -5.82;

function inSevillaArea(lat, lon) {
  return lat >= SEVILLA_LAT_MIN && lat <= SEVILLA_LAT_MAX
      && lon >= SEVILLA_LON_MIN && lon <= SEVILLA_LON_MAX;
}

function initMap() {
  if (leafMap) return;

  const sevillaBounds = L.latLngBounds(
    [SEVILLA_LAT_MIN, SEVILLA_LON_MIN],
    [SEVILLA_LAT_MAX, SEVILLA_LON_MAX]
  );

  leafMap = L.map("map-el", {
    zoomControl: true,
    attributionControl: true,
    maxBounds: sevillaBounds,
    maxBoundsViscosity: 0.85,
    minZoom: 11,
  }).setView([37.3886, -5.9823], 13);

  tileLayer = L.tileLayer(_mapTileUrl(), {
    attribution: '&copy; <a href="https://carto.com">CartoDB</a>',
    maxZoom: 19,
    subdomains: "abcd",
  }).addTo(leafMap);

  leafMap.on("move", () => {
    const c = leafMap.getCenter();
    const el = document.getElementById("map-coords");
    if (el) el.textContent = `${c.lat.toFixed(4)}°N · ${Math.abs(c.lng.toFixed(4))}°W · SEVILLA`;
  });

  renderMapOverlays();
}

function renderMapOverlays() {
  markers.forEach(m => m.remove()); markers = [];
  circles.forEach(c => c.remove()); circles = [];
  baseMarkers.forEach(b => b.remove()); baseMarkers = [];

  BASES.forEach(base => {
    if (!base.lat || !base.lon) return;

    let color = "#888";
    let letter = "B";
    const types = Array.isArray(base.types) ? base.types : [];
    if (types.includes("ambulance_sva") || types.includes("ambulance_svb")) {
      color = "#3aaa72";
      letter = "H";
    } else if (types.includes("police")) {
      color = "#5090cc";
      letter = "P";
    } else if (types.includes("fire") || types.includes("rescue")) {
      color = "#e07060";
      letter = "F";
    }

    const size = 18;
    const html = `
      <div style="width:${size}px;height:${size}px;border-radius:3px;
        background:${color};border:1px solid rgba(255,255,255,0.3);
        display:flex;align-items:center;justify-content:center;
        font-family:var(--mono);font-size:8px;color:#fff;font-weight:bold;
        box-shadow: 0 0 8px rgba(0,0,0,0.5);">
        ${letter}
      </div>`;

    const icon = L.divIcon({ html, className: "", iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
    const popup = `
      <div style="font-family:var(--mono);font-size:8px;color:#fff;font-weight:bold;margin-bottom:4px">${base.name}</div>
      <div style="font-family:var(--mono);font-size:9px;color:var(--t2)">Tipos: ${types.join(", ")}</div>`;

    const marker = L.marker([base.lat, base.lon], { icon })
      .addTo(leafMap)
      .bindPopup(popup, { maxWidth: 220 });
    baseMarkers.push(marker);
  });

  HOTSPOTS.forEach(hs => {
    if (!hs.lat || !hs.lon) return;
    const color  = hs.risk >= 80 ? "#b83030" : hs.risk >= 60 ? "#c97f0a" : "#1a62a0";
    const radius = 300 + (hs.risk / 100) * 300;
    const circle = L.circle([hs.lat, hs.lon], {
      radius, color, fillColor: color,
      fillOpacity: 0.07, weight: 1.2, opacity: 0.3, dashArray: "5 5",
    })
      .addTo(leafMap)
      .bindTooltip(
        `<div style="font-family:var(--mono);font-size:9px;background:#0c0f13;` +
        `border:1px solid rgba(255,255,255,.12);padding:6px 10px;color:#cdd4dc">` +
        `<b>${hs.area}</b><br>Riesgo ${hs.risk} · ${hs.count} incidentes</div>`,
        { sticky: true, opacity: 1, className: "" }
      );
    circles.push(circle);
  });

  const toShow = (incFilter === "all" ? INCS : INCS.filter(i => i.sev === incFilter))
    .filter(i => i.lat && i.lon && inSevillaArea(i.lat, i.lon));
  toShow.forEach(inc => {
    if (!inc.lat || !inc.lon) return;
    const hexColor = { critical:"#b83030", high:"#c97f0a", medium:"#1a62a0", low:"#0e7a5e" }[inc.sev] || "#1a62a0";
    const isActive = inc.status === "active";
    const size     = isActive ? 14 : 11;
    const html     = `
      <div style="width:${size}px;height:${size}px;border-radius:50%;
        background:${hexColor};border:2px solid #07090b;position:relative;
        ${isActive ? "animation:mapPulse 1.8s ease-out infinite;" : ""}">
        <div style="position:absolute;inset:3px;border-radius:50%;background:rgba(255,255,255,.55)"></div>
      </div>`;
    const icon   = L.divIcon({ html, className: "", iconSize: [size, size], iconAnchor: [size / 2, size / 2] });
    const popup  = `
      <div class="lp-id">${inc.id}</div>
      <div class="lp-type">${inc.type}</div>
      <div class="lp-addr">${inc.addr}</div>
      ${sevTag(inc.sev)}`;
    const marker = L.marker([inc.lat, inc.lon], { icon })
      .addTo(leafMap)
      .bindPopup(popup, { maxWidth: 220 })
      .on("click", () => openDetail(inc.id));
    markers.push(marker);
  });
}

function renderIncList() {
  const toShow = (incFilter === "all" ? INCS : INCS.filter(i => i.sev === incFilter))
    .filter(i => !i.lat || !i.lon || inSevillaArea(i.lat, i.lon));
  const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };
  const sorted  = [...toShow].sort((a, b) => SEV_ORDER[a.sev] - SEV_ORDER[b.sev]);

  document.getElementById("inc-count").textContent = sorted.length;

  document.getElementById("inc-list").innerHTML = sorted.length
    ? sorted.map(inc => `
        <div class="inc-row${selId === inc.id ? " sel" : ""}" data-id="${_escHtml(inc.id)}" onclick="openDetail(this.dataset.id)">
          <div class="inc-stripe" style="background:${SEV_C[inc.sev]}"></div>
          <div>
            <div class="inc-type">${_escHtml(inc.type)}</div>
            <div class="inc-addr">${_escHtml(inc.addr)}</div>
            <div class="inc-meta">
              ${sevTag(inc.sev)}
              <div class="status-dot-row">
                <div class="sdot" style="background:${statusColor(inc.status)}"></div>
                ${statusLabel(inc.status)}
              </div>
            </div>
          </div>
          <div>
            <div class="inc-time">${ago(inc.ts)}</div>
            ${inc.rt ? `<div class="inc-units">${inc.rt} min</div>` : ""}
          </div>
        </div>`).join("")
    : `<div style="padding:24px;text-align:center;font-family:var(--mono);font-size:9px;
         color:var(--t3);letter-spacing:.1em">SIN INCIDENTES ACTIVOS</div>`;
}

function filterInc(sev, btn) {
  incFilter = sev;
  document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  renderIncList();
  renderMapOverlays();
}

function openDetail(id) {
  selId = id;
  const inc = INCS.find(i => i.id === id);
  if (!inc) return;

  renderIncList();

  document.getElementById("det-id").textContent = inc.id;
  document.getElementById("det-body").innerHTML = `
    <div class="det-sev-bar" style="background:${SEV_C[inc.sev]}"></div>
    <div class="det-title">${_escHtml(inc.type)}</div>
    <div class="det-id">${_escHtml(inc.id)} · ${new Date(inc.ts).toLocaleString("es-ES")}</div>
    <div class="det-grid">
      <div class="det-cell"><div class="det-cell-lbl">Severidad</div>    <div class="det-cell-val">${SEV_L[inc.sev]}</div></div>
      <div class="det-cell"><div class="det-cell-lbl">Estado</div>       <div class="det-cell-val" style="color:${statusColor(inc.status)}">${statusLabel(inc.status)}</div></div>
      <div class="det-cell" style="grid-column: 1 / -1"><div class="det-cell-lbl">Ubicación</div>    <div class="det-cell-val">${_escHtml(inc.addr)}</div></div>
      <div class="det-cell"><div class="det-cell-lbl">T. activo</div>    <div class="det-cell-val">${ago(inc.ts)}</div></div>
      <div class="det-cell"><div class="det-cell-lbl">T. respuesta</div> <div class="det-cell-val">${inc.rt ? inc.rt + " min" : "—"}</div></div>
      <div class="det-cell"><div class="det-cell-lbl">Víctimas</div>     <div class="det-cell-val">${inc.victims !== undefined ? inc.victims : "—"}</div></div>
      <div class="det-cell"><div class="det-cell-lbl">Confianza</div>    <div class="det-cell-val">${inc.conf ? inc.conf + "%" : "—"}</div></div>
      <div class="det-cell" style="grid-column: 1 / -1"><div class="det-cell-lbl">Unidades</div>     <div class="det-cell-val">${inc.units || "—"}</div></div>
      <div class="det-cell" style="grid-column: 1 / -1"><div class="det-cell-lbl">Protocolo</div>    <div class="det-cell-val">${_escHtml(inc.protocol || "—")}</div></div>
    </div>
    ${inc.decision ? `
      <div class="det-sec">Decisión del sistema</div>
      <div style="font-family:var(--mono);font-size:9px;color:var(--amber);letter-spacing:.06em;margin-bottom:10px">${_escHtml(inc.decision)}</div>` : ""}
    ${inc.note ? `
      <div class="det-sec">Nota del agente</div>
      <div style="font-family:var(--mono);font-size:8px;color:var(--t2);line-height:1.6;letter-spacing:.03em">${_escHtml(inc.note)}</div>` : ""}
    ${(inc.status !== "resolved") ? `
      <div style="margin-top:15px">
        <button class="call-btn call-btn-primary det-confirm-btn" data-id="${_escHtml(inc.id)}" onclick="confirmArrival(this.dataset.id)">
          Confirmar Llegada
        </button>
      </div>` : ""}
  `;

  document.getElementById("detail-panel").classList.add("open");
  if (leafMap && inc.lat && inc.lon) {
    leafMap.flyTo([inc.lat, inc.lon], 15, { duration: 0.6 });
  }
}

function closeDetail() {
  document.getElementById("detail-panel").classList.remove("open");
  selId = null;
  const detBody = document.getElementById("det-body");
  if (detBody) detBody.innerHTML = "";
  renderIncList();
}

function renderSeverityBars() {
  const counts = { critical: 0, high: 0, medium: 0, low: 0 };
  INCS.forEach(i => { if (counts[i.sev] !== undefined) counts[i.sev]++; });
  const max = Math.max(...Object.values(counts), 1);

  const rows = [
    ["CRÍTICO", "critical", "#e07060", "var(--red)"],
    ["ALTO",    "high",     "#d4950a", "var(--amber)"],
    ["MEDIO",   "medium",   "#5090cc", "var(--blue)"],
    ["BAJO",    "low",      "#3aaa72", "var(--teal)"],
  ];

  document.getElementById("sev-bars").innerHTML = rows.map(([label, key, textColor, barColor]) => `
    <div class="sev-bar-row">
      <div class="sev-bar-lbl" style="color:${textColor}">${label}</div>
      <div class="sev-bar-track">
        <div class="sev-bar-fill" style="width:${(counts[key] / max) * 100}%;background:${barColor}"></div>
      </div>
      <div class="sev-bar-n">${counts[key]}</div>
    </div>`).join("");
}

function renderTypeBars() {
  const counts = {};
  INCS.forEach(i => { counts[i.type] = (counts[i.type] || 0) + 1; });

  const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5);
  const max    = sorted.length ? sorted[0][1] : 1;

  const palette = ["var(--amber)", "var(--blue)", "var(--red)", "var(--teal)", "var(--red)"];

  document.getElementById("type-bars").innerHTML = sorted.length
    ? sorted.map(([type, n], idx) => `
        <div style="display:flex;align-items:center;gap:7px;margin-bottom:6px">
          <span style="font-family:var(--cond);font-size:11px;font-weight:500;color:var(--t2);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_escHtml(type)}</span>
          <div style="width:34px;height:2px;background:var(--ln2)">
            <div style="width:${(n / max) * 100}%;height:100%;background:${palette[idx % palette.length]}"></div>
          </div>
          <span style="font-family:var(--mono);font-size:11px;color:var(--t3);width:14px;text-align:right">${n}</span>
        </div>`).join("")
    : `<div style="font-family:var(--mono);font-size:11px;color:var(--t3)">Sin datos</div>`;
}

function renderHsMini() {
  document.getElementById("hs-list-mini").innerHTML = HOTSPOTS.slice(0, 4).map(hs => {
    const color = hs.risk >= 80 ? "var(--red)" : hs.risk >= 60 ? "var(--amber)" : "var(--blue)";
    return `
      <div class="hs-row">
        <div class="hs-name">${_escHtml(hs.area)}
          <span class="hs-risk" style="color:${color}">${hs.risk}</span>
        </div>
        <div class="hs-track"><div class="hs-fill" style="width:${hs.risk}%;background:${color}"></div></div>
        <div class="hs-meta">${hs.count} INC · ${_escHtml(translateIncidentType(hs.dominant).toUpperCase())}</div>
      </div>`;
  }).join("") || `<div style="font-family:var(--mono);font-size:8px;color:var(--t3);padding:8px 0">Sin datos</div>`;
}

async function confirmArrival(id) {
  closeDetail();

  try {
    const res = await fetch(`${API_BASE}/incidents/${id}/resolve`, {
      method: "POST"
    });
    if (!res.ok) throw new Error("Server error: " + res.status);
    const data = await res.json();
    console.log("[confirmArrival] Resolved incident:", data);

    const inc = INCS.find(i => i.id === id);
    if (inc) {
      inc.status = "resolved";
      upsertIncident(inc);
    }
    _renderAll();
  } catch (err) {
    console.error("[confirmArrival] Error:", err);
    const isNetworkErr = err instanceof TypeError && (
      err.message.includes("NetworkError") || err.message.includes("Failed to fetch")
    );
    alert(isNetworkErr
      ? "No se pudo conectar con el servidor. Verifica que el backend esté en ejecución e inténtalo de nuevo."
      : "No se pudo confirmar la llegada del incidente: " + err.message
    );
  }
}

window.confirmArrival = confirmArrival;

function _mapTileUrl() {
  return document.documentElement.getAttribute("data-theme") === "light"
    ? "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png"
    : "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png";
}

function updateMapTileTheme() {
  if (tileLayer) tileLayer.setUrl(_mapTileUrl());
}
window.updateMapTileTheme = updateMapTileTheme;
