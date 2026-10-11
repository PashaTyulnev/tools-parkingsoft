const API_BASE = "/api";

// Own parking spots — order matters: more specific before less specific
const OWN_PROVIDERS = [
  "Frankfurt Airport Parking",
  "Parke und Fliege Parkhaus",
  "Frapark Airport",
  "Parke und Fliege",
];

const PORTAL_STYLES = {
  parkos:           { bg: "#dbeafe", color: "#1d4ed8" },
  parkenflughafen:  { bg: "#ffedd5", color: "#c2410c" },
  ichparkebilliger: { bg: "#dcfce7", color: "#15803d" },
};

let chartTotal = null;
let lastData   = null;

document.addEventListener("DOMContentLoaded", () => {
  const tomorrow = new Date();
  tomorrow.setDate(tomorrow.getDate() + 1);
  document.getElementById("arrival_date").value = tomorrow.toISOString().slice(0, 10);
});

document.getElementById("btn-compare").addEventListener("click", runCompare);

// ── Matching helpers ───────────────────────────────────────────────────────────

function matchOwnProvider(name) {
  const lower = (name || "").toLowerCase().trim();
  // Check more-specific keys first (array order matters)
  return OWN_PROVIDERS.find(own => lower.startsWith(own.toLowerCase()));
}

function isOwn(name) { return !!matchOwnProvider(name); }

// ── Data helpers ───────────────────────────────────────────────────────────────

function allProvidersForDay(data, days) {
  const out = [];
  for (const [key, portal] of Object.entries(data.portals)) {
    const res = (portal.results || []).find(r => r.days === days);
    if (!res?.providers?.length) continue;
    for (const p of res.providers) {
      out.push({ ...p, portalKey: key, portalName: portal.name });
    }
  }
  return out;
}

// ── Fetch & render flow ────────────────────────────────────────────────────────

async function runCompare() {
  const location       = document.getElementById("location").value;
  const arrival_date   = document.getElementById("arrival_date").value;
  const arrival_time   = document.getElementById("arrival_time").value;
  const departure_time = document.getElementById("departure_time").value;
  const portals        = [...document.querySelectorAll(".checkbox-group input:checked")]
    .map(cb => cb.value).join(",");

  if (!portals)       { showError("Bitte mindestens ein Portal auswählen."); return; }
  if (!arrival_date)  { showError("Bitte Ankunftsdatum auswählen.");          return; }

  hideError();
  setLoading(true);

  const params = new URLSearchParams({ location, arrival_date, arrival_time, departure_time, portals });
  try {
    const resp = await fetch(`${API_BASE}/compare?${params}`);
    if (!resp.ok) { const e = await resp.json(); throw new Error(e.error || `HTTP ${resp.status}`); }
    lastData = await resp.json();
    renderResults(lastData);
  } catch (e) {
    showError(`Fehler: ${e.message}`);
  } finally {
    setLoading(false);
  }
}

function renderResults(data) {
  document.getElementById("results-section").classList.remove("hidden");
  renderSummaryCards(data);
  renderTotalPriceChart(data);
  renderDayTabs(data);
  renderForDay(data, data.day_ranges[0]);
}

function renderForDay(data, days) {
  renderOwnProvidersBar(data, days);
  renderProviderTable(data, days);
}

// ── Summary cards (min across all portals) ─────────────────────────────────────

function renderSummaryCards(data) {
  const grid = document.getElementById("summary-grid");
  grid.innerHTML = "";
  for (const days of data.day_ranges) {
    const allP = allProvidersForDay(data, days);
    if (!allP.length) continue;
    const prices = allP.map(p => p.price).filter(Boolean);
    if (!prices.length) continue;
    const min = Math.min(...prices);
    const card = document.createElement("div");
    card.className = "summary-card";
    card.innerHTML = `
      <div class="days-label">${days} Tag${days > 1 ? "e" : ""}</div>
      <div class="price">€${min.toFixed(2)}</div>
      <div class="sub">Min · ${allP.length} Einträge</div>`;
    grid.appendChild(card);
  }
}

// ── Reference bar: our own spots ───────────────────────────────────────────────

function renderOwnProvidersBar(data, days) {
  const bar = document.getElementById("own-providers-bar");
  const portalKeys = Object.keys(data.portals);
  const allP = allProvidersForDay(data, days);

  // For each own-provider label: cheapest price per portal
  const rows = OWN_PROVIDERS.map(ownLabel => {
    const priceCols = portalKeys.map(pk => {
      const matches = allP.filter(p => p.portalKey === pk && matchOwnProvider(p.name) === ownLabel);
      if (!matches.length) return null;
      return Math.min(...matches.map(p => p.price));
    });
    const hasAny = priceCols.some(p => p !== null);
    return { label: ownLabel, prices: priceCols, hasAny };
  }).filter(r => r.hasAny);

  if (!rows.length) { bar.classList.add("hidden"); return; }

  const headers = portalKeys.map(pk => {
    const s = PORTAL_STYLES[pk] || { bg: "#f3f4f6", color: "#374151" };
    return `<th><span class="portal-badge" style="background:${s.bg};color:${s.color}">${data.portals[pk].name}</span></th>`;
  }).join("");

  const tableRows = rows.map(row => {
    const cells = row.prices.map(price => {
      if (price === null) return `<td class="ref-cell ref-missing">–</td>`;
      const pd = (price / days).toFixed(2);
      return `<td class="ref-cell ref-price">€${price.toFixed(2)}<span class="per-day"> (€${pd}/T)</span></td>`;
    }).join("");
    return `<tr><td class="ref-name">${row.label}</td>${cells}</tr>`;
  }).join("");

  bar.classList.remove("hidden");
  bar.innerHTML = `
    <div class="own-bar-header">
      <span class="own-bar-title">📍 Unsere Parkplätze</span>
      <span class="own-bar-sub">${days} Tag${days > 1 ? "e" : ""}</span>
    </div>
    <table class="ref-table">
      <thead><tr><th></th>${headers}</tr></thead>
      <tbody>${tableRows}</tbody>
    </table>`;
}

// ── Combined provider table ────────────────────────────────────────────────────

function renderProviderTable(data, days) {
  const container  = document.getElementById("provider-table-container");
  const allP       = allProvidersForDay(data, days);
  const portalKeys = Object.keys(data.portals);

  if (!allP.length) {
    container.innerHTML = `<p style="color:var(--muted);padding:16px 0">Keine Daten für ${days} Tag${days > 1 ? "e" : ""} verfügbar.</p>`;
    return;
  }

  // Split own vs others, both sorted by price
  const ownP   = allP.filter(p =>  isOwn(p.name)).sort((a, b) => a.price - b.price);
  const otherP = allP.filter(p => !isOwn(p.name)).sort((a, b) => a.price - b.price);
  const sorted = [...ownP, ...otherP];

  const multiPortal = portalKeys.length > 1;

  const rows = sorted.map((p, i) => {
    const ownRow   = isOwn(p.name);
    const rankIdx  = ownRow ? -1 : i - ownP.length;
    const rankCls  = rankIdx >= 0 && rankIdx < 3 ? ` rank-${rankIdx + 1}` : "";
    const ownCls   = ownRow ? " row-own" : "";
    const s = PORTAL_STYLES[p.portalKey] || { bg: "#f3f4f6", color: "#374151" };
    const portalTd = multiPortal
      ? `<td><span class="portal-badge" style="background:${s.bg};color:${s.color}">${p.portalName}</span></td>`
      : "";
    const typeKey  = (p.type || "").toLowerCase().replace(/\s+/g, "-").replace(/[^a-z-]/g, "").replace("ö", "oe");
    const typeCls  = typeKey.includes("valet") ? "badge-valet" : typeKey.includes("shuttle") ? "badge-shuttle" : "badge-outdoor";
    const pd       = (p.price / days).toFixed(2);
    const score    = p.avg_score ? ` <span class="score">★ ${Number(p.avg_score).toFixed(1)}</span>` : "";
    return `<tr class="${ownCls}${rankCls}">
      <td>${p.name}${score}</td>
      ${portalTd}
      <td><span class="badge-type ${typeCls}">${p.type || "–"}</span></td>
      <td class="price-cell">€${p.price.toFixed(2)} <span class="per-day">(€${pd}/Tag)</span></td>
    </tr>`;
  }).join("");

  const portalTh = multiPortal ? "<th>Portal</th>" : "";
  container.innerHTML = `
    <table class="provider-table">
      <thead><tr>
        <th>Anbieter</th>${portalTh}
        <th>Typ</th>
        <th>Preis (${days} Tag${days > 1 ? "e" : ""})</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <p style="margin-top:12px;font-size:.8rem;color:var(--muted)">
      ${allP.length} Einträge · ${ownP.length} eigene · ${portalKeys.length} Portal${portalKeys.length > 1 ? "e" : ""}
    </p>`;
}

// ── Chart ──────────────────────────────────────────────────────────────────────

function renderTotalPriceChart(data) {
  const ctx = document.getElementById("chart-total-price").getContext("2d");
  if (chartTotal) chartTotal.destroy();

  const labels   = data.day_ranges;
  const datasets = [];
  const palette  = { min: "#16a34a", max: "#dc2626" };
  const lineStyles = [[], [5, 5], [2, 4]];

  let si = 0;
  for (const [key, portal] of Object.entries(data.portals)) {
    const s = PORTAL_STYLES[key] || { color: "#6b7280" };
    const label = Object.keys(data.portals).length > 1 ? ` (${portal.name})` : "";
    const minVals = labels.map(d => portal.results.find(x => x.days === d)?.min_total ?? null);
    const maxVals = labels.map(d => portal.results.find(x => x.days === d)?.max_total ?? null);
    datasets.push({
      label: `Min${label}`, data: minVals,
      borderColor: palette.min, borderDash: lineStyles[si % lineStyles.length],
      fill: false, tension: 0.35, pointRadius: 3, pointHoverRadius: 6, borderWidth: 2, spanGaps: true,
    });
    datasets.push({
      label: `Max${label}`, data: maxVals,
      borderColor: palette.max, borderDash: lineStyles[si % lineStyles.length],
      fill: false, tension: 0.35, pointRadius: 3, pointHoverRadius: 6, borderWidth: 2, spanGaps: true,
    });
    si++;
  }

  chartTotal = new Chart(ctx, {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { position: "top" },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: €${c.parsed.y?.toFixed(2) ?? "N/A"}` } },
      },
      scales: {
        x: { title: { display: true, text: "Aufenthaltsdauer (Tage)" }, ticks: { callback: v => `${labels[v]}T` } },
        y: { title: { display: true, text: "Gesamtpreis (€)" }, ticks: { callback: v => `€${v}` } },
      },
    },
  });
}

// ── Day tabs ───────────────────────────────────────────────────────────────────

function renderDayTabs(data) {
  const tabs = document.getElementById("day-tabs");
  tabs.innerHTML = "";
  for (const days of data.day_ranges) {
    const tab = document.createElement("button");
    tab.className = "day-tab" + (days === data.day_ranges[0] ? " active" : "");
    tab.textContent = `${days} Tag${days > 1 ? "e" : ""}`;
    tab.addEventListener("click", () => {
      document.querySelectorAll(".day-tab").forEach(t => t.classList.remove("active"));
      tab.classList.add("active");
      renderForDay(data, days);
    });
    tabs.appendChild(tab);
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────

function setLoading(on) {
  const btn     = document.getElementById("btn-compare");
  const txt     = document.getElementById("btn-text");
  const spinner = document.getElementById("btn-spinner");
  btn.disabled  = on;
  txt.textContent = on ? "Lädt…" : "Jetzt vergleichen";
  spinner.classList.toggle("hidden", !on);
}

function showError(msg) {
  const box = document.getElementById("error-box");
  box.textContent = msg;
  box.classList.remove("hidden");
}

function hideError() { document.getElementById("error-box").classList.add("hidden"); }
