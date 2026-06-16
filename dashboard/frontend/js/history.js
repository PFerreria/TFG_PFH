const histFilters  = { sev: "all", status: "all" };
const histSort     = { key: null, dir: null };
const HIST_PAGE_SIZE = 50;
let   histPage     = 1;

function setHistFilter(type, val, btn) {
  histFilters[type] = val;
  btn.parentElement.querySelectorAll(".fr-btn").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  histPage = 1;
  renderHistory();
}

function sortHist(key) {
  if (histSort.key === key) {
    if (histSort.dir === "asc") {
      histSort.dir = "desc";
    } else {
      histSort.key = null;
      histSort.dir = null;
    }
  } else {
    histSort.key = key;
    histSort.dir = "asc";
  }
  renderHistory();
}

function renderHistory() {
  const q = document.getElementById("hist-search").value.toLowerCase();

  const allRows = [
    ...HISTORY,
    ...INCS.map(i => ({
      ...i,
      date: new Date(i.ts).toLocaleString("es-ES", {
        day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
      }),
    })),
  ];

  let rows = allRows;
  if (q) rows = rows.filter(r => Object.values(r).some(v => String(v).toLowerCase().includes(q)));
  if (histFilters.sev    !== "all") rows = rows.filter(r => r.sev    === histFilters.sev);
  if (histFilters.status !== "all") rows = rows.filter(r => r.status === histFilters.status);

  const sortKey = histSort.key || "ts";
  const sortDir = histSort.dir || "desc";
  rows.sort((a, b) => {
    const va = a[sortKey] == null ? 0 : a[sortKey];
    const vb = b[sortKey] == null ? 0 : b[sortKey];
    const cmp = typeof va === "number" ? va - vb : String(va).localeCompare(String(vb));
    return sortDir === "asc" ? cmp : -cmp;
  });

  const total      = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / HIST_PAGE_SIZE));
  if (histPage > totalPages) histPage = totalPages;

  const pageRows = rows.slice((histPage - 1) * HIST_PAGE_SIZE, histPage * HIST_PAGE_SIZE);

  document.getElementById("hist-count-badge").textContent = total + " REGISTROS";

  document.getElementById("hist-tbody").innerHTML = pageRows.map(r => `
    <tr data-id="${_escHtml(r.id)}" onclick="openHistModal(this.dataset.id)">
      <td style="color:var(--amber);letter-spacing:.08em">${_escHtml(r.id)}</td>
      <td style="color:var(--t3)">${_escHtml(r.date || "")}</td>
      <td style="font-family:var(--cond);font-size:11px;font-weight:600;color:var(--t1)">${_escHtml(r.type)}</td>
      <td>${sevTag(r.sev)}</td>
      <td>${_escHtml(r.addr)}</td>
      <td style="text-align:center;color:var(--t3)">${r.units}</td>
      <td style="color:var(--amber)">${r.rt}m</td>
      <td><span style="color:${confColor(r.conf || 0)}">${r.conf || "—"}%</span></td>
      <td>
        <span style="color:${statusColor(r.status)};font-family:var(--mono);font-size:7px;letter-spacing:.08em">
          ${statusLabel(r.status)}
        </span>
      </td>
    </tr>`).join("");

  _renderHistPagination(total, totalPages);

  document.querySelectorAll(".hist-table th[data-sort-key]").forEach(th => {
    if (th.dataset.sortKey === histSort.key) {
      th.dataset.sortDir = histSort.dir;
    } else {
      delete th.dataset.sortDir;
    }
  });
}

function _renderHistPagination(total, totalPages) {
  const el = document.getElementById("hist-pagination");
  if (!el) return;

  if (totalPages <= 1) { el.innerHTML = ""; return; }

  const start = (histPage - 1) * HIST_PAGE_SIZE + 1;
  const end   = Math.min(histPage * HIST_PAGE_SIZE, total);

  el.innerHTML = `
    <button class="pg-btn" onclick="histGoPage(1)">«</button>
    <button class="pg-btn" onclick="histGoPage(${histPage - 1})">‹</button>
    <span class="pg-info">${start}–${end} de ${total}</span>
    <button class="pg-btn" onclick="histGoPage(${histPage + 1})">›</button>
    <button class="pg-btn" onclick="histGoPage(${totalPages})">»</button>`;
}

function histGoPage(page) {
  const allRows = [...HISTORY, ...INCS];
  const totalPages = Math.max(1, Math.ceil(allRows.length / HIST_PAGE_SIZE));
  histPage = Math.max(1, Math.min(page, totalPages));
  renderHistory();
  document.querySelector(".hist-table-wrap").scrollTop = 0;
}

function openHistModal(id) {
  const allRecords = [...HISTORY, ...INCS];
  const r = allRecords.find(x => x.id === id);
  if (!r) return;

  document.getElementById("hm-id").textContent = r.id;

  document.getElementById("hm-body").innerHTML = `
    <div style="display:flex;gap:13px;align-items:flex-start;margin-bottom:16px;padding-bottom:15px;border-bottom:1px solid var(--ln)">
      <div style="width:3px;align-self:stretch;background:${SEV_C[r.sev]};border-radius:0;flex-shrink:0"></div>
      <div style="flex:1;min-width:0">
        <div style="font-family:var(--cond);font-size:21px;font-weight:700;color:var(--t1);line-height:1.15;margin-bottom:5px">${r.type}</div>
        <div style="font-family:var(--mono);font-size:8px;color:var(--t3);letter-spacing:.08em">${r.date || ""} · ${r.addr}</div>
      </div>
      <span style="font-family:var(--mono);font-size:7px;letter-spacing:.12em;text-transform:uppercase;padding:3px 8px;border:1px solid ${SEV_C[r.sev]};color:${SEV_C[r.sev]};border-radius:2px;flex-shrink:0;margin-top:3px">${SEV_L[r.sev]}</span>
    </div>

    <div class="hm-grid">
      ${[
        ["Severidad",    SEV_L[r.sev],                                       null                    ],
        ["Estado",       statusLabel(r.status),                               null                    ],
        ["Confianza",    (r.conf ? r.conf + "%" : "—"),                       confColor(r.conf || 0)  ],
        ["Víctimas",     r.victims || "—",                                    null                    ],
        ["Unidades",     r.units,                                             null                    ],
        ["T. Respuesta", r.rt ? r.rt + " min" : "—",                         null                    ],
      ].map(([label, val, color]) => `
        <div class="hm-cell">
          <div class="det-cell-lbl">${label}</div>
          <div class="det-cell-val"${color ? ` style="color:${color}"` : ""}>${val}</div>
        </div>`).join("")}
    </div>

    <div class="det-sec">Línea de tiempo</div>
    ${buildTimeline(r)}
  `;

  document.getElementById("hist-modal").classList.add("open");
}

function closeHistModal() {
  document.getElementById("hist-modal").classList.remove("open");
}
