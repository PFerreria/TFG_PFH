const API_BASE = (typeof window !== "undefined")
  ? `${window.location.protocol}//${window.location.host}/api`
  : "http://localhost:8000/api";

let INCS     = [];
let HISTORY  = [];
let HOTSPOTS = [];
let FORECAST = [];
let HOURLY   = [];
let KB       = [];
let KB_ADAPTED = [];
let KB_RAW     = [];
let KB_COVERAGE = {};
let KPI      = {};
let BASES    = [];
let DASHBOARD_META = {};

async function _get(path) {
  const res = await fetch(API_BASE + path);
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}

async function loadAllData() {
  const [incResult, histResult, dashResult, kbResult, kbAdaptedResult, kbRawResult, basesResult, kbCoverageResult] = await Promise.allSettled([
    _get("/incidents/active"),
    _get("/incidents/history"),
    _get("/dashboard"),
    _get("/protocols"),
    _get("/protocols/adapted"),
    _get("/protocols/raw"),
    _get("/bases"),
    _get("/protocols/coverage"),
  ]);

  if (incResult.status === "fulfilled") {
    INCS = (incResult.value.incidents || []).map(_normaliseIncident).filter(_hasRealAddress);
  } else {
    console.error("[data.js] /incidents/active failed:", incResult.reason);
  }

  if (histResult.status === "fulfilled") {
    HISTORY = (histResult.value.incidents || []).map(_normaliseIncident);
  } else {
    console.error("[data.js] /incidents/history failed:", histResult.reason);
  }

  if (dashResult.status === "fulfilled") {
    const dash = dashResult.value;
    HOTSPOTS = (dash.hotspots || []).map(_normaliseHotspot);
    FORECAST = (dash.forecast || []).map(_normaliseForecast);
    HOURLY   = _normaliseHourly(dash.hourly_distribution);
    KPI      = dash.kpis || {};
    DASHBOARD_META = {
      historical_daily_avg: dash.historical_daily_avg || 0,
      generated_at:         dash.generated_at         || null,
      source:               dash.source               || null,
    };
  } else {
    console.error("[data.js] /dashboard failed:", dashResult.reason);
  }

  if (kbResult.status === "fulfilled") {
    KB = (kbResult.value.protocols || []).map(_normaliseProtocol);
  } else {
    console.error("[data.js] /protocols failed:", kbResult.reason);
  }

  if (kbAdaptedResult.status === "fulfilled") {
    KB_ADAPTED = (kbAdaptedResult.value.protocols || []).map(_normaliseProtocol);
    KB = KB_ADAPTED.length ? KB_ADAPTED : KB;
  } else {
    console.error("[data.js] /protocols/adapted failed:", kbAdaptedResult.reason);
  }

  if (kbRawResult.status === "fulfilled") {
    KB_RAW = (kbRawResult.value.documents || []).map(_normaliseRawDocument);
  } else {
    console.error("[data.js] /protocols/raw failed:", kbRawResult.reason);
  }

  if (basesResult.status === "fulfilled") {
    BASES = basesResult.value.bases || [];
  } else {
    console.error("[data.js] /bases failed:", basesResult.reason);
  }

  if (kbCoverageResult.status === "fulfilled") {
    KB_COVERAGE = kbCoverageResult.value || {};
  } else {
    console.error("[data.js] /protocols/coverage failed:", kbCoverageResult.reason);
  }
}

const _INC_TYPE_ES = {
  traffic_accident:      "Accidente de tráfico",
  cardiac_arrest:        "Parada cardiorrespiratoria",
  stroke:                "Ictus",
  drowning:              "Ahogamiento",
  fall_injury:           "Caída / traumatismo",
  overdose:              "Sobredosis",
  mental_health_crisis:  "Crisis de salud mental",
  other_medical:         "Emergencia médica",
  assault:               "Agresión",
  domestic_violence:     "Violencia doméstica",
  robbery:               "Robo",
  missing_person:        "Persona desaparecida",
  other_police:          "Incidente policial",
  fire:                  "Incendio",
  gas_leak:              "Fuga de gas",
  explosion:             "Explosión",
  chemical_spill:        "Vertido químico",
  flooding:              "Inundación",
  infrastructure_collapse: "Derrumbe",
  other:                 "Otro",
};

function _normaliseIncident(raw) {
  if (!raw) {
    return {
      id:       "—",
      type:     "—",
      sev:      "low",
      status:   "resolved",
      addr:     "—",
      lat:      0,
      lon:      0,
      ts:       Date.now(),
      date:     "—",
      units:    0,
      rt:       null,
      conf:     null,
      note:     "",
      decision: "",
      protocol: "",
      victims:  0,
    };
  }

  const location = raw.location || {};
  const dispatch = raw.dispatch || {};
  const procedure = raw.procedure || {};
  const _rawType = raw.incident_type ? String(raw.incident_type) : "";
  const incidentType = _INC_TYPE_ES[_rawType] || (_rawType ? _rawType.replace(/_/g, " ") : "");

  let formattedUnits = raw.units || raw.units_dispatched || 0;
  let unitArray = [];
  if (dispatch.units && Array.isArray(dispatch.units)) unitArray = dispatch.units;
  else if (Array.isArray(raw.units)) unitArray = raw.units;

  const _TYPE_LABEL = {
    ambulance_sva: "SVA",
    ambulance_svb: "SVB",
    police:        "ZETA",
    fire:          "BOM",
    rescue:        "FSV",
  };
  if (unitArray.length > 0) {
    formattedUnits = unitArray.map(u => {
      if (typeof u === "string") return u;
      const label = u.subtype || _TYPE_LABEL[u.type] || (u.type || "UNK").toUpperCase();
      const eta   = u.eta_minutes != null ? `${u.eta_minutes} min` : "";
      return eta ? `${label} (${eta})` : label;
    }).join(" - ");
  }

  let formattedProtocol = "";
  let protocolActions   = [];
  let protocolEscalation = "";
  if (typeof raw.protocol === "string") {
    formattedProtocol = raw.protocol;
  } else if (raw.protocol && typeof raw.protocol === "object") {
    const _rawActions = raw.protocol.key_actions || [];
    protocolActions = _rawActions.map(a =>
      (a && typeof a === "object") ? (a.action || JSON.stringify(a)) : String(a || "")
    ).filter(Boolean);
    formattedProtocol  = raw.protocol.text || protocolActions[0] || "";
    protocolEscalation = raw.protocol.escalation || "";
  }
  if (!formattedProtocol && procedure.protocol_code) {
    formattedProtocol = procedure.protocol_code;
  }

  let formattedRt = raw.rt || raw.response_time_min || dispatch.first_arrival_minutes || null;

  const incLat = raw.lat || raw.latitude  || location.latitude  || null;
  const incLon = raw.lon || raw.longitude || location.longitude || null;
  let mapsUrl = null;
  if (incLat && incLon) {
    mapsUrl = `https://www.google.com/maps/dir/?api=1&destination=${incLat},${incLon}&travelmode=driving`;
  } else {
    const addr = raw.addr || raw.address || location.address || "";
    if (addr && addr !== "—" && addr !== "Dirección no localizada, operador aclare dirección") {
      mapsUrl = `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(addr)}`;
    }
  }

  const _normTs   = raw.ts || (raw.timestamp ? new Date(raw.timestamp).getTime() : Date.now());
  const _normDate = new Date(_normTs).toLocaleString("es-ES", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });

  return {
    id:       raw.id        || raw.incident_id || "—",
    type:     (_INC_TYPE_ES[raw.type] || _INC_TYPE_ES[_rawType]) || raw.type || incidentType || "—",
    sev:      raw.sev       || raw.severity    || "low",
    status:   raw.status                       || "resolved",
    addr:     raw.addr      || raw.address     || location.address || "—",
    lat:      incLat        || 0,
    lon:      incLon        || 0,
    ts:       _normTs,
    date:     _normDate,
    units:    formattedUnits,
    rt:       formattedRt,
    conf:     raw.conf      || raw.confidence_score           || null,
    note:     raw.note      || raw.agent_note                 || "",
    decision: raw.decision  || dispatch.decision         || "",
    protocol:           formattedProtocol,
    protocolActions:    protocolActions,
    protocolEscalation: protocolEscalation,
    victims:  raw.victims                                     || 0,
    mapsUrl:  mapsUrl,
  };
}

function _normaliseHotspot(raw) {
  return {
    area:     raw.area      || raw.area_label                       || "—",
    count:    raw.count     || raw.incident_count                   || 0,
    domCount: raw.domCount  || raw.dominant_count                   || 0,
    risk:     raw.risk      || raw.risk_score                       || 0,
    dominant: raw.dominant  || raw.dominant_type                    || "—",
    lat:      raw.lat       || raw.centre_lat   || raw.lat_center   || 0,
    lon:      raw.lon       || raw.centre_lon   || raw.lon_center   || 0,
  };
}

function _normaliseForecast(raw) {
  const dow = raw.day_of_week ? String(raw.day_of_week).slice(0, 3).toUpperCase() : "";
  const date = raw.date ? String(raw.date).slice(0, 10) : "";
  return {
    d: raw.d || dow || date || "—",
    v: raw.v || raw.predicted_incidents || 0,
  };
}

function _normaliseHourly(raw) {
  if (!raw) return new Array(24).fill(0);
  if (typeof raw[0] === "number") return raw;
  if (typeof raw[0] === "object") {
    const arr = new Array(24).fill(0);
    raw.forEach(item => { arr[item.hour] = item.count; });
    return arr;
  }
  return new Array(24).fill(0);
}

function _normaliseProtocol(raw) {
  const indexedAt = raw.indexed_at ? String(raw.indexed_at).slice(0, 7) : "";
  const steps = raw.steps || [];
  return {
    title:      raw.title                        || "—",
    cat:        raw.cat      || raw.category     || "—",
    code:       raw.code     || raw.protocol_code|| "—",
    updated:    raw.updated  || indexedAt || "—",
    source:     raw.source                       || "—",
    excerpt:    raw.excerpt  || (steps[0]  || ""),
    tags:       raw.tags     || [],
    steps:      raw.steps    || [],
    escalation: raw.escalation                   || "",
    notes:      raw.notes                        || "",
    relatedIds:     raw.relatedIds || raw.related_incident_ids || [],
    incident_type:  raw.incident_type  || "other",
    urgency:        raw.urgency        || "medium",
    retrieval_tier: raw.retrieval_tier || "stub",
    usage_count:    raw.usage_count    || 0,
  };
}

function _normaliseRawDocument(raw) {
  return {
    filename: raw.filename || "—",
    extension: raw.extension || "",
    size_bytes: raw.size_bytes || 0,
    updated_at: raw.updated_at || "",
    download_url: raw.download_url || "",
  };
}

const _ADDR_PLACEHOLDERS = new Set([
  "—", "", "Dirección no localizada, operador aclare dirección",
  "Procesando IA...", "Dirección desconocida",
]);

function _hasRealAddress(norm) {
  if (!norm.addr || _ADDR_PLACEHOLDERS.has(norm.addr.trim())) return false;
  return true;
}

function upsertIncident(report) {
  const norm = _normaliseIncident(report);
  if (norm.status === "resolved" || norm.status === "complete" || norm.status === "resuelto") {
    INCS = INCS.filter(i => i.id !== norm.id);
    if (!HISTORY.some(i => i.id === norm.id)) {
      HISTORY = [norm, ...HISTORY].slice(0, 500);
    } else {
      HISTORY = HISTORY.map(i => i.id === norm.id ? norm : i);
    }
  } else {
    if (!_hasRealAddress(norm)) {
      const existing = INCS.find(i => i.id === norm.id);
      if (existing && _hasRealAddress(existing)) return;
      INCS = INCS.filter(i => i.id !== norm.id);
      return;
    }
    INCS = [norm, ...INCS.filter(i => i.id !== norm.id)].slice(0, 100);
  }
}

async function refreshActive() {
  try {
    const data = await _get("/incidents/active");
    INCS = (data.incidents || []).map(_normaliseIncident).filter(_hasRealAddress);
  } catch (e) {
    console.error("[data.js] refreshActive failed:", e);
  }
}

async function refreshDashboard() {
  try {
    const dash = await _get("/dashboard");
    HOTSPOTS = (dash.hotspots || []).map(_normaliseHotspot);
    FORECAST = (dash.forecast || []).map(_normaliseForecast);
    HOURLY   = _normaliseHourly(dash.hourly_distribution);
    KPI      = dash.kpis || {};
    DASHBOARD_META = {
      historical_daily_avg: dash.historical_daily_avg || 0,
      generated_at:         dash.generated_at         || null,
      source:               dash.source               || null,
    };
  } catch (e) {
    console.error("[data.js] refreshDashboard failed:", e);
  }
}
