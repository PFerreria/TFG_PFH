const SEV_C  = { critical:"var(--red)",   high:"var(--amber)",   medium:"var(--blue)",  low:"var(--teal)"   };
const SEV_T  = { critical:"#e07060",      high:"#d4950a",        medium:"#5090cc",      low:"#3aaa72"       };
const SEV_BG = { critical:"var(--red-d)", high:"var(--amber-d)", medium:"var(--blue-d)",low:"var(--teal-d)" };
const SEV_L  = { critical:"CRÍTICO",      high:"ALTO",           medium:"MEDIO",        low:"BAJO"          };

function statusColor(s) {
  return (s === "active" || s === "processed") ? "var(--red)" : s === "en_route" ? "var(--amber)" : "var(--teal)";
}

function statusLabel(s) {
  return (s === "active" || s === "processed") ? "ACTIVO" : s === "en_route" ? "EN CAMINO" : "RESUELTO";
}

function ago(ts) {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60)   return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m";
  return Math.floor(s / 3600) + "h";
}

function _escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function sevTag(sev) {
  return `<span class="sev-tag" style="border-color:${SEV_C[sev]};background:${SEV_BG[sev]};color:${SEV_T[sev]}">${SEV_L[sev]}</span>`;
}

function confColor(c) {
  return c >= 90 ? "var(--teal)" : c >= 70 ? "var(--amber)" : "var(--red)";
}

function translateIncidentType(raw) {
  if (!raw) return "—";
  if (_INC_TYPE_ES[raw]) return _INC_TYPE_ES[raw];
  const key = raw.replace(/ /g, "_");
  if (_INC_TYPE_ES[key]) return _INC_TYPE_ES[key];
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

function bigBarRow(label, pct, value, color) {
  return `
    <div class="big-bar-row">
      <div class="big-bar-lbl">${label}</div>
      <div class="big-bar-track"><div class="big-bar-fill" style="width:${pct}%;background:${color}"></div></div>
      <div class="big-bar-val">${value}</div>
    </div>`;
}

function buildTimeline(incident) {
  const tl = [
    { t: "00:00",               approx: false, lbl: "Llamada recibida",                                                                    c: "var(--t3)"   },
    { t: "~18s",                approx: true,  lbl: "Transcripción completada",                                                             c: "var(--blue)" },
    { t: "~24s",                approx: true,  lbl: "Clasificación: " + _escHtml(incident.type),                                           c: "var(--amber)"},
    { t: "~31s",                approx: true,  lbl: "Protocolo " + _escHtml(incident.protocol || "—") + " recuperado",                     c: "var(--amber)"},
    { t: "~38s",                approx: true,  lbl: "Despacho: " + _escHtml(incident.decision || "—"),                                     c: "var(--teal)" },
    { t: incident.rt ? incident.rt + "m" : "—", approx: false, lbl: "Primera unidad llegó",                          c: "var(--teal)" },
    ...(incident.status === "resolved"
      ? [{ t: "—", approx: false, lbl: "Incidente resuelto", c: "var(--t3)" }]
      : []),
  ];

  return `
    <div style="position:relative;padding-left:18px">
      <div style="position:absolute;left:5px;top:6px;bottom:6px;width:1px;background:var(--ln2)"></div>
      ${tl.map(e => `
        <div style="position:relative;margin-bottom:10px">
          <div style="position:absolute;left:-13px;top:3px;width:6px;height:6px;border-radius:50%;background:${e.c};border:1.5px solid var(--bg1)"></div>
          <div style="font-family:var(--mono);font-size:7px;color:var(--t3);margin-bottom:2px">
            +${e.t}${e.approx ? `<span style="opacity:.5;margin-left:3px">(aprox.)</span>` : ""}
          </div>
          <div style="font-family:var(--mono);font-size:8px;color:var(--t2)">${e.lbl}</div>
        </div>`).join("")}
    </div>`;
}
