let kbCat        = "TODOS";
let kbActive = null;
let kbMode   = "adapted";

let _kbSearchTimer = null;

const _TIER_C = { cache: "#3aaa72", vector: "#d4950a", stub: "#888888" };
const _TIER_L = { cache: "CACHÉ",   vector: "VECTOR",  stub: "STUB"    };

const _URG_C  = { critical: "#e07060", high: "#d4950a", medium: "#5090cc", low: "#3aaa72" };
const _URG_BG = { critical: "rgba(184,48,48,.1)", high: "rgba(201,127,10,.1)", medium: "rgba(80,144,204,.1)", low: "rgba(58,170,114,.1)" };
const _URG_L  = { critical: "CRÍTICO", high: "ALTO", medium: "MEDIO", low: "BAJO" };

function setKBMode(mode, btn) {
  kbMode = mode;
  document.querySelectorAll("#kb-mode-bar .fr-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");

  const articleCol = document.getElementById("kb-article-col");
  const cards      = document.getElementById("kb-cards");
  const rawList    = document.getElementById("kb-raw-list");
  const searchRes  = document.getElementById("kb-search-results");

  const adaptedFilters = document.getElementById("kb-adapted-filters");

  if (mode === "raw") {
    if (articleCol)     articleCol.classList.add("hidden");
    if (cards)          cards.style.display          = "none";
    if (rawList)        rawList.style.display        = "block";
    if (searchRes)      searchRes.style.display      = "none";
    if (adaptedFilters) adaptedFilters.style.display = "none";
  } else {
    if (cards)          cards.style.display          = "block";
    if (rawList)        rawList.style.display        = "none";
    if (adaptedFilters) adaptedFilters.style.display = "";
  }
  renderKB();
}

function setKBCat(cat, btn) {
  kbCat = cat;
  document.querySelectorAll("#screen-knowledge .fr-btn")
    .forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  renderKB();
}

function handleKBSearch() {
  clearTimeout(_kbSearchTimer);
  const q = (document.getElementById("kb-search")?.value || "").trim();

  if (kbMode === "raw") {
    renderKB();
    return;
  }

  if (q.length < 3) {
    _clearSearchResults();
    renderKB();
    return;
  }

  _kbSearchTimer = setTimeout(() => _doAdaptedSearch(q), 300);
}

function _doAdaptedSearch(q) {
  const items  = KB_ADAPTED && KB_ADAPTED.length ? KB_ADAPTED : KB;
  const q_low  = q.toLowerCase();
  const matched = items.filter(k =>
    [k.title, k.excerpt, (k.steps || []).join(" "), (k.tags || []).join(" "),
     k.incident_type, k.escalation || "", k.notes || ""]
      .some(s => s && s.toLowerCase().includes(q_low))
  );
  _renderAdaptedSearchResults(matched, q);
}

function _renderAdaptedSearchResults(items, q) {
  const panel = document.getElementById("kb-search-results");
  const cards = document.getElementById("kb-cards");
  if (!panel) return;

  if (!items || items.length === 0) {
    panel.innerHTML = `<div class="kb-search-empty">SIN RESULTADOS · "${_escHtml(q)}"</div>`;
    panel.style.display = "block";
    if (cards) cards.style.display = "none";
    return;
  }

  if (cards) cards.style.display = "none";
  panel.style.display = "block";

  panel.innerHTML = `
    <div class="kb-search-hdr">
      <span>${items.length} resultado${items.length !== 1 ? "s" : ""} · <em>${_escHtml(q)}</em></span>
    </div>
    ${items.map(k => {
      const stepCount = (k.steps && k.steps.length) || 0;
      const incType   = translateIncidentType(k.incident_type || "other");
      return `
      <div class="kb-card${kbActive === k.code ? " active" : ""}" onclick='openArticle(${JSON.stringify(k.code)})'>
        <div class="kb-card-head">
          <div class="kb-card-title">${_escHtml(k.title)}</div>
          <div class="kb-cat-tag">${_escHtml(k.cat)}</div>
        </div>
        <div class="kb-code">${_escHtml(k.code)}</div>
        <div class="kb-excerpt">${_escHtml(k.excerpt)}</div>
        <div class="kb-meta-row">
          <span class="kb-inc-badge">${_escHtml(incType)}</span>
          ${stepCount > 0 ? `<span class="kb-step-badge">${stepCount} pasos</span>` : ""}
        </div>
        <div class="kb-tags">${k.tags.map(t => `<span class="kb-tag">${_escHtml(t)}</span>`).join("")}</div>
      </div>`;
    }).join("")}
  `;
}

function _clearSearchResults() {
  const panel = document.getElementById("kb-search-results");
  const cards = document.getElementById("kb-cards");
  if (panel) panel.style.display = "none";
  if (cards) cards.style.display = "block";
}

function renderKB() {
  const el1 = document.getElementById("kb-count-adapted");
  const el2 = document.getElementById("kb-count-raw");
  if (el1) el1.textContent = (KB_ADAPTED && KB_ADAPTED.length) ? KB_ADAPTED.length : (KB ? KB.length : 0);
  if (el2) el2.textContent = (KB_RAW || []).length;

  if (kbMode === "raw") {
    renderRawDocs();
    return;
  }

  const q              = (document.getElementById("kb-search")?.value || "").toLowerCase();
  const incidentFilter = document.getElementById("kb-incident-filter")?.value || "all";

  let items = KB_ADAPTED && KB_ADAPTED.length ? KB_ADAPTED : KB;
  if (kbCat !== "TODOS")        items = items.filter(k => k.cat === kbCat);
  if (incidentFilter !== "all") items = items.filter(k => k.incident_type === incidentFilter);
  if (q.length > 0 && q.length < 3) {
    items = items.filter(k =>
      (k.title + k.excerpt + k.tags.join() + (k.incident_type || "")).toLowerCase().includes(q)
    );
  }

  const cardsEl = document.getElementById("kb-cards");
  if (!cardsEl) return;

  if (items.length === 0) {
    cardsEl.innerHTML = `
      <div style="padding:40px;text-align:center;font-family:var(--mono);font-size:9px;color:var(--t3);letter-spacing:.1em">
        SIN RESULTADOS · INTENTE CON TÉRMINOS DIFERENTES
      </div>`;
    return;
  }

  cardsEl.innerHTML = items.map(k => {
    const urgColor  = _URG_C[k.urgency]  || "var(--t3)";
    const urgBg     = _URG_BG[k.urgency] || "transparent";
    const urgLabel  = _URG_L[k.urgency]  || (k.urgency || "MEDIO").toUpperCase();
    const tierColor = _TIER_C[k.retrieval_tier] || "#888";
    const tierLabel = _TIER_L[k.retrieval_tier] || "STUB";
    const stepCount = (k.steps && k.steps.length) || 0;
    const incType   = translateIncidentType(k.incident_type || "other");

    return `
    <div class="kb-card${kbActive === k.code ? " active" : ""}" onclick='openArticle(${JSON.stringify(k.code)})'>
      <div class="kb-card-head">
        <div class="kb-card-title">${_escHtml(k.title)}</div>
        <div class="kb-cat-tag">${_escHtml(k.cat)}</div>
      </div>
      <div class="kb-code">${_escHtml(k.code)}</div>
      <div class="kb-excerpt">${_escHtml(k.excerpt)}</div>
      <div class="kb-meta-row">
        <span class="kb-inc-badge">${_escHtml(incType)}</span>
        ${stepCount > 0 ? `<span class="kb-step-badge">${stepCount} pasos</span>` : ""}
      </div>
<div class="kb-tags">${k.tags.map(t => `<span class="kb-tag">${_escHtml(t)}</span>`).join("")}</div>
    </div>`;
  }).join("");
}

function renderRawDocs() {
  const q    = (document.getElementById("kb-search")?.value || "").toLowerCase();
  const list = document.getElementById("kb-raw-list");
  if (!list) return;

  let docs = KB_RAW || [];
  if (q.length > 1) {
    docs = docs.filter((d) => (d.filename + " " + d.extension).toLowerCase().includes(q));
  }

  if (!docs.length) {
    list.innerHTML = `
      <div style="padding:40px;text-align:center;font-family:var(--mono);font-size:9px;color:var(--t3);letter-spacing:.1em">
        SIN DOCUMENTOS DISPONIBLES
      </div>`;
    return;
  }

  list.innerHTML = docs.map((d) => {
    const sizeKb  = Math.round((d.size_bytes || 0) / 1024);
    const updated = d.updated_at ? new Date(d.updated_at).toLocaleString("es-ES") : "—";
    const href    = d.download_url || "#";
    return `
      <div class="kb-raw-row">
        <div>
          <div class="kb-raw-name">${_escHtml(d.filename)}</div>
          <div class="kb-raw-meta">${d.extension.toUpperCase()} · ${sizeKb} KB · ${updated}</div>
        </div>
        <a class="kb-raw-link" href="${href}" target="_blank" rel="noopener noreferrer">Abrir</a>
      </div>`;
  }).join("");
}

function _renderArticleBody(k) {
  const body = document.getElementById("kb-art-body");
  if (!body) return;

  const steps = k.steps || [];

  body.innerHTML = `
    <div class="art-title">${_escHtml(k.title)}</div>

    <div class="art-sec">Pasos de actuación</div>
    ${steps.map((step, i) => `
      <div class="art-step">
        <div class="art-step-n">${String(i + 1).padStart(2, "0")}</div>
        <div class="art-step-txt">${_escHtml(step)}</div>
      </div>`).join("")}

    ${k.escalation ? `<div class="art-sec">Criterio de escalada</div><div class="art-esc">${_escHtml(k.escalation)}</div>` : ""}

    ${k.notes ? `<div class="art-sec">Notas especiales</div><div class="art-note">${_escHtml(k.notes)}</div>` : ""}

    ${k.relatedIds && k.relatedIds.length ? `
      <div class="art-sec">Incidentes relacionados</div>
      ${k.relatedIds.map(id => `
        <div class="rel-inc" onclick="goToIncident(${JSON.stringify(id)})">
          <div style="width:5px;height:5px;border-radius:50%;background:var(--amber);flex-shrink:0"></div>
          ${_escHtml(id)}
          <svg style="margin-left:auto;flex-shrink:0" width="8" height="8" viewBox="0 0 8 8" fill="none">
            <path d="M2 2h4v4M2 6l4-4" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
          </svg>
        </div>`).join("")}
    ` : ""}
  `;
}

function openArticle(code) {
  const sourceList = KB_ADAPTED && KB_ADAPTED.length ? KB_ADAPTED : KB;
  const k = sourceList.find(x => x.code === code);
  if (!k) return;

  kbActive = code;

  renderKB();

  document.getElementById("kb-art-code").textContent = k.code;
  _renderArticleBody(k);
  document.getElementById("kb-article-col").classList.remove("hidden");
}

function closeArticle() {
  kbActive = null;
  renderKB();
  document.getElementById("kb-article-col").classList.add("hidden");
}

function goToIncident(id) {
  setView("history");
  setTimeout(() => {
    const el = document.getElementById("hist-search");
    if (el) { el.value = id; renderHistory(); }
  }, 50);
}

window.handleKBSearch  = handleKBSearch;
window.setKBMode       = setKBMode;
window.setKBCat        = setKBCat;
window.renderKB        = renderKB;
window.openArticle  = openArticle;
window.closeArticle = closeArticle;
window.goToIncident = goToIncident;
