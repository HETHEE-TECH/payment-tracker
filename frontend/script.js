// If you open index.html from another server (e.g. Live Server on port 5500),
// change this to "http://127.0.0.1:8000/api".
const API = "/api";

const STATUS_LABEL = { pending: "Pending", paid: "Paid", delayed: "Delayed", cancelled: "Cancelled" };
const STATUS_CLASS = { pending: "", paid: "ok", delayed: "warn", cancelled: "bad" };

// A payment has a status (pending, paid or cancelled) and can also be delayed, which sits next to Paid or Cancel.
// Delayed means the delayed date (new_date) is set. A "delayed" status from an older version counts as delayed too.
const isDelayed = (i) => Boolean(i.new_date) || i.status === "delayed";
const mainStatus = (i) => (i.status === "delayed" ? "pending" : i.status);
// The one place a payment takes in the pie chart, the bar and the month rows: paid or cancelled first, then delayed.
const slice = (i) => (mainStatus(i) !== "pending" ? mainStatus(i) : isDelayed(i) ? "delayed" : "pending");
const rowClass = (i) => ({ paid: "ok", cancelled: "bad", delayed: "warn", pending: "" })[slice(i)];

// A payment can also have a category (free text such as Fees or Rent). Capitals do not matter.
const NO_CATEGORY = "__none__";
const categoryOf = (i) => (i.category || "").trim();
const matchesCategory = (i, wanted) => !wanted || (wanted === NO_CATEGORY ? categoryOf(i) === "" : categoryOf(i).toLowerCase() === wanted.toLowerCase());
const CATEGORY_COLORS = ["#2f7de1", "#e0752b", "#8a5cd0", "#1f9d5a", "#d6453d", "#139c96", "#c2418a", "#a8693a", "#7f8c1f", "#5b6bb5"];

// Three amounts that can be added to a payment next to its status. They are typed in, and they are not
// part of the Total, Paid, Pending, Delayed or Cancelled numbers: each shows only in its own card.
const EXTRA_KINDS = {
  awaiting: { field: "awaiting_amount", short: "Awaiting income", label: "Amount still to come in", cls: "come", words: /await|come|balance|pending|due/i },
  received: { field: "received_amount", short: "Awaiting income received", label: "Amount received", cls: "recv", words: /receiv|got|came|paid/i },
  we_paid: { field: "we_paid_amount", short: "We paid", label: "Amount we paid", cls: "extra", words: /paid|extra/i },
  excess: { field: "excess_amount", short: "Excess paid", label: "Excess amount (what was paid on top)", cls: "excess", words: /excess|extra|more|over|additional/i },
  part_pending: { field: "part_pending_amount", short: "Part paid pending", label: "Pending amount (the part still to pay)", cls: "part", words: /pending|balance|remain|left|due/i },
};

const pad = (n) => String(n).padStart(2, "0");
const today = (() => {
  const t = new Date();
  return `${t.getFullYear()}-${pad(t.getMonth() + 1)}-${pad(t.getDate())}`;
})();

// ---------- helpers ----------
function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
const money = (n) => "₹" + Number(n).toLocaleString("en-IN", { maximumFractionDigits: 2 });
const utc = (s) => { const [y, m, d] = s.split("-").map(Number); return new Date(Date.UTC(y, m - 1, d)); };
const fmtDay = (s) => utc(s).toLocaleDateString("en-IN", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
const fmtShort = (s) => utc(s).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
const fmtMonth = (k) => utc(k + "-01").toLocaleDateString("en-IN", { month: "long", year: "numeric", timeZone: "UTC" });

async function api(path, options = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail || detail;
    } catch (e) { /* keep statusText */ }
    throw new Error(detail);
  }
  return res.status === 204 ? null : res.json();
}

let noticeTimer = null;
function showNotice(message) {
  const box = document.getElementById("notice");
  box.textContent = message || "";
  box.hidden = !message;
  clearTimeout(noticeTimer);
  if (message) noticeTimer = setTimeout(() => { box.hidden = true; }, 6000);
}

// Tells the user which stored Excel files changed after adding, removing or changing the status of a payment.
function reportSync(result) {
  if (!result) return;
  if (result.sync_error) showError("Done, but the stored Excel could not be updated: " + result.sync_error);
  if (result.files_updated && result.files_updated.length) {
    showNotice("Stored Excel updated: " + result.files_updated.join(", "));
    loadFiles();
  }
}

function showError(message) {
  const box = document.getElementById("error");
  box.textContent = message || "";
  box.hidden = !message;
}

// ---------- rendering ----------
// Status order and colours used by the dashboard cards, the pie chart and the bar.
const DASH_STATUSES = [
  ["paid", "Paid", "ok", "--ok-solid"],
  ["pending", "Pending", "pend", "--pend-solid"],
  ["delayed", "Delayed", "warn", "--warn-solid"],
  ["cancelled", "Cancelled", "bad", "--bad-solid"],
];

// What the dashboard is counting, from the filters that are set (for example "November 2026 · Paid").
function scopeLabel() {
  const parts = [];
  const date = [filters.day ? Number(filters.day) : "", filters.month ? MONTH_NAMES[Number(filters.month) - 1] : "", filters.year]
    .filter(Boolean).join(" ");
  if (date) parts.push(date);
  if (filters.status) parts.push(STATUS_LABEL[filters.status] || (EXTRA_KINDS[filters.status] || {}).short || filters.status);
  if (filters.category) parts.push(filters.category === NO_CATEGORY ? "No category" : filters.category);
  if (filters.q.trim()) parts.push(`\u201c${filters.q.trim()}\u201d`);
  return parts.length ? parts.join(" \u00b7 ") : "All payments";
}

// ---------- month-wise trend chart ----------
let trendMode = "bars";
const compact = (n) => new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 }).format(n);
const niceMax = (v) => { // a round number at or above v, for the top of the chart
  if (v <= 0) return 1;
  const unit = Math.pow(10, Math.floor(Math.log10(v)));
  const n = v / unit;
  return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * unit;
};

function renderTrend() {
  const svg = document.getElementById("trend");
  const rows = monthRows().slice(-12); // the latest 12 months
  const legend = document.getElementById("trend-legend");
  if (!rows.length) {
    svg.innerHTML = "";
    svg.removeAttribute("viewBox");
    svg.style.width = "0";
    legend.innerHTML = `<li>No payments yet.</li>`;
    document.getElementById("trend-desc").textContent = "Trend chart. There is nothing to show yet.";
    return;
  }
  const room = svg.parentElement.clientWidth || 600; // the chart uses the whole width; with many months it scrolls
  const W = Math.max(340, room, 64 + rows.length * 62), H = 280, L = 52, R = 12, T = 22, B = 44;
  const top = niceMax(Math.max(...rows.map((m) => m.total)));
  const slot = (W - L - R) / rows.length;
  const x = (n) => L + (n + 0.5) * slot;
  const y = (v) => T + (H - T - B) * (1 - v / top);
  const label = (m) => `${MONTH_NAMES[Number(m.key.slice(5, 7)) - 1].slice(0, 3)} ${m.key.slice(2, 4)}`;
  let out = "";
  for (let t = 0; t <= 4; t++) { // grid lines and the amounts on the left
    const v = (top / 4) * t;
    out += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"></line><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${compact(v)}</text>`;
  }
  const series = [...DASH_STATUSES.map(([key, name, , cssVar]) => ({ key, name, cssVar })), ...(trendMode === "lines" ? [{ key: "total", name: "Total", cssVar: "--fg", dashed: true }] : [])];
  if (trendMode === "bars") {
    const bar = Math.min(54, slot * 0.55);
    rows.forEach((m, n) => {
      let base = 0;
      DASH_STATUSES.forEach(([key, name, , cssVar]) => {
        if (!m[key]) return;
        out += `<rect style="fill:var(${cssVar})" x="${x(n) - bar / 2}" y="${y(base + m[key])}" width="${bar}" height="${y(base) - y(base + m[key])}"><title>${label(m)} ${name}: ${esc(money(m[key]))}</title></rect>`;
        base += m[key];
      });
      out += `<text class="total" x="${x(n)}" y="${y(m.total) - 6}" text-anchor="middle">${compact(m.total)}</text>`;
    });
  } else {
    series.forEach((line) => {
      const value = (m) => (line.key === "total" ? m.total : m[line.key]);
      const dash = line.dashed ? ` stroke-dasharray="6 5"` : "";
      out += `<polyline style="stroke:var(${line.cssVar})"${dash} points="${rows.map((m, n) => `${x(n)},${y(value(m))}`).join(" ")}"></polyline>`;
      rows.forEach((m, n) => { out += `<circle style="fill:var(${line.cssVar})" cx="${x(n)}" cy="${y(value(m))}" r="3.6"><title>${label(m)} ${line.name}: ${esc(money(value(m)))}</title></circle>`; });
    });
  }
  rows.forEach((m, n) => { // month names, and a click area over each month
    const [year, month] = m.key.split("-");
    const on = filters.year === year && filters.month === month && !filters.day;
    out += `<text x="${x(n)}" y="${H - B + 18}" text-anchor="middle">${label(m)}</text>` +
      `<rect class="hit${on ? " on" : ""}" data-month="${m.key}" x="${x(n) - slot / 2}" y="${T}" width="${slot}" height="${H - T - B + 26}"><title>${MONTH_NAMES[Number(month) - 1]} ${year}: ${esc(money(m.total))}. Click to show only this month.</title></rect>`;
  });
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.style.width = `${W}px`;
  svg.style.maxWidth = "none";
  svg.innerHTML = out;
  legend.innerHTML = series.map((line) => `<li><span class="dot" style="background:var(${line.cssVar})"></span>${line.name}</li>`).join("");
  document.getElementById("trend-desc").textContent = "Trend chart of the total of each month. " + rows.map((m) => `${label(m)}: ${money(m.total)}`).join(". ") + ".";
}

document.getElementById("trend").addEventListener("click", (e) => {
  const hit = e.target.closest("[data-month]");
  if (hit) chooseMonth(hit.dataset.month);
});
document.querySelectorAll('input[name="trend-mode"]').forEach((radio) =>
  radio.addEventListener("change", () => { trendMode = radio.value; renderTrend(); }));

// ---------- the PDF report: the same filters as the page ----------
document.getElementById("dash-report").addEventListener("click", async () => {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => { if (String(value).trim()) params.set(key, String(value).trim()); });
  try {
    showError("");
    const res = await fetch(`${API}/report?${params}`);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error((Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail) || res.statusText);
    }
    const blob = await res.blob();
    const name = (/filename="?([^";]+)"?/.exec(res.headers.get("Content-Disposition") || "") || [])[1] || "payment-report.pdf";
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = name;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    showNotice("Report downloaded: " + name);
  } catch (err) {
    showError("Could not make the report: " + err.message);
  }
});

// ---------- pie chart ----------
let lastSummary = null;
let pieMode = "status";

// One slice per entry: { label, amount, count, cssVar, dot }. Angles are drawn clockwise from 12 o'clock.
function pieData(s) {
  if (pieMode === "category") { // the amount of each category (every status counts, like the total)
    const byCategory = new Map();
    filteredItems().forEach((i) => {
      const none = !categoryOf(i);
      const label = none ? "No category" : categoryOf(i);
      if (!byCategory.has(label.toLowerCase())) byCategory.set(label.toLowerCase(), { label, amount: 0, count: 0, none });
      const row = byCategory.get(label.toLowerCase());
      row.amount += i.amount; row.count++;
    });
    return [...byCategory.values()].sort((a, b) => (a.none - b.none) || b.amount - a.amount)
      .map((row, n) => ({ label: row.label, amount: row.amount, count: row.count, color: row.none ? "#9aa5a0" : CATEGORY_COLORS[n % CATEGORY_COLORS.length] }));
  }
  if (pieMode === "amounts") {
    return Object.values(EXTRA_KINDS).map((kind) => ({
      label: kind.short, amount: s.extras[kind.field].amount, count: s.extras[kind.field].count,
      cssVar: { come: "--come-solid", recv: "--recv-solid", extra: "--extra-solid", excess: "--excess-solid", part: "--part-solid" }[kind.cls], dot: kind.cls,
    }));
  }
  return DASH_STATUSES.map(([key, label, dot, cssVar]) => ({ label, amount: s.slices[key].amount, count: s.slices[key].count, cssVar, dot }));
}

const pieFill = (d) => d.color || `var(${d.cssVar})`;

function renderPie() {
  if (!lastSummary) return;
  const data = pieData(lastSummary);
  const total = data.reduce((sum, d) => sum + d.amount, 0);
  const point = (turn, r) => [r * Math.sin(turn * 2 * Math.PI), -r * Math.cos(turn * 2 * Math.PI)];
  const text = (d) => `${d.label}: ${money(d.amount)} (${Math.round((d.amount / total) * 100)}%)`;
  const shown = data.filter((d) => d.amount > 0);
  let svg = "";
  if (!total) {
    svg = `<circle class="empty" r="1"></circle>`;
  } else if (shown.length === 1) { // one slice is the whole circle
    svg = `<circle r="1" style="fill:${pieFill(shown[0])}"><title>${esc(text(shown[0]))}</title></circle>` +
      `<text x="0" y="0">100%</text>`;
  } else {
    let from = 0;
    shown.forEach((d) => {
      const share = d.amount / total;
      const [x0, y0] = point(from, 1);
      const [x1, y1] = point(from + share, 1);
      svg += `<path style="fill:${pieFill(d)}" d="M 0 0 L ${x0} ${y0} A 1 1 0 ${share > 0.5 ? 1 : 0} 1 ${x1} ${y1} Z"><title>${esc(text(d))}</title></path>`;
      if (share >= 0.06) { // the percentage inside the slice, when there is room
        const [tx, ty] = point(from + share / 2, 0.64);
        svg += `<text x="${tx}" y="${ty}">${Math.round(share * 100)}%</text>`;
      }
      from += share;
    });
  }
  document.getElementById("pie").innerHTML = svg;
  const count = (n) => `${n} ${n === 1 ? "payment" : "payments"}`;
  const share = (amount) => (!total || amount <= 0 ? "0%" : amount / total < 0.005 ? "<1%" : `${Math.round((amount / total) * 100)}%`);
  document.getElementById("pie-legend").innerHTML = data.map((d) =>
    `<li><span class="dot ${d.dot || ""}"${d.color ? ` style="background:${d.color}"` : ""}></span><span class="lname">${esc(d.label)}</span>` +
    `<span class="lval num">${money(d.amount)}</span>` +
    `<span class="lsub">${share(d.amount)} \u00b7 ${count(d.count)}</span></li>`).join("");
  document.getElementById("pie-desc").textContent = total
    ? "Pie chart. " + data.map(text).join(". ") + "."
    : "Pie chart. There is nothing to show yet.";
}

// Draws the pie chart, its legend and what it covers on a canvas and saves it as a PNG picture.
function downloadPie() {
  if (!lastSummary) return;
  const data = pieData(lastSummary);
  const total = data.reduce((sum, d) => sum + d.amount, 0);
  const scale = 2;
  const width = 780;
  const height = Math.max(380, 150 + data.length * 52);
  const canvas = document.createElement("canvas");
  canvas.width = width * scale;
  canvas.height = height * scale;
  const ctx = canvas.getContext("2d");
  ctx.scale(scale, scale);
  const font = (size, weight = 400) => `${weight} ${size}px Inter, system-ui, -apple-system, "Segoe UI", sans-serif`;
  const colour = (cssVar) => getComputedStyle(document.documentElement).getPropertyValue(cssVar).trim() || "#888";
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);
  ctx.fillStyle = "#1b2420";
  ctx.font = font(22, 700);
  ctx.fillText("Pie chart: " + ({ status: "Status", amounts: "Typed-in amounts", category: "Category" })[pieMode], 28, 40);
  ctx.fillStyle = "#5b6862";
  ctx.font = font(14);
  ctx.fillText(`${scopeLabel()} \u00b7 ${new Date().toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric" })}`, 28, 64);
  const cx = 190, cy = 220, radius = 120;
  if (!total) {
    ctx.fillStyle = "#dfe5e2";
    ctx.beginPath(); ctx.arc(cx, cy, radius, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#5b6862"; ctx.font = font(15); ctx.textAlign = "center"; ctx.fillText("Nothing to show yet", cx, cy + 5); ctx.textAlign = "left";
  } else {
    let from = -Math.PI / 2; // 12 o'clock, clockwise
    data.filter((d) => d.amount > 0).forEach((d) => {
      const angle = (d.amount / total) * Math.PI * 2;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.arc(cx, cy, radius, from, from + angle); ctx.closePath();
      ctx.fillStyle = (d.color || colour(d.cssVar)); ctx.fill();
      ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2; ctx.stroke();
      if (d.amount / total >= 0.06) {
        const mid = from + angle / 2;
        ctx.font = font(16, 700); ctx.textAlign = "center"; ctx.textBaseline = "middle";
        ctx.lineWidth = 4; ctx.strokeStyle = "rgba(0,0,0,0.55)"; ctx.fillStyle = "#ffffff";
        const label = `${Math.round((d.amount / total) * 100)}%`;
        ctx.strokeText(label, cx + Math.cos(mid) * radius * 0.64, cy + Math.sin(mid) * radius * 0.64);
        ctx.fillText(label, cx + Math.cos(mid) * radius * 0.64, cy + Math.sin(mid) * radius * 0.64);
        ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
      }
      from += angle;
    });
  }
  const left = 360, right = width - 28;
  data.forEach((d, n) => {
    const y = 120 + n * 52;
    ctx.fillStyle = (d.color || colour(d.cssVar)); ctx.fillRect(left, y - 13, 16, 16);
    ctx.fillStyle = "#1b2420"; ctx.font = font(17, 600); ctx.fillText(d.label, left + 28, y);
    ctx.textAlign = "right"; ctx.fillText(money(d.amount), right, y); ctx.textAlign = "left";
    ctx.fillStyle = "#5b6862"; ctx.font = font(13);
    ctx.fillText(`${total ? Math.round((d.amount / total) * 100) : 0}% \u00b7 ${d.count} ${d.count === 1 ? "payment" : "payments"}`, left + 28, y + 20);
  });
  canvas.toBlob((blob) => {
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `pie-chart-${pieMode}-${new Date().toISOString().slice(0, 10)}.png`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 1000);
    showNotice("Pie chart downloaded as a PNG picture.");
  }, "image/png");
}
document.getElementById("pie-download").addEventListener("click", downloadPie);

document.querySelectorAll('input[name="pie-mode"]').forEach((radio) =>
  radio.addEventListener("change", () => { pieMode = radio.value; renderPie(); }));

function renderSummary(s) {
  lastSummary = s;
  renderPie();
  const base = s.total.amount;
  const pct = (part) => (base ? (part.amount / base) * 100 : 0);
  const sliceOf = (key) => s.slices[key];
  const count = (n) => `${n} ${n === 1 ? "payment" : "payments"}`;
  const text = (id, value) => { document.getElementById(id).textContent = value; };
  text("dash-scope", scopeLabel());
  text("dash-total", money(s.total.amount));
  text("dash-total-count", count(s.total.count));
  text("dash-topay", money(s.to_pay.amount));
  text("dash-topay-count", count(s.to_pay.count));
  document.getElementById("dash-cards").innerHTML = DASH_STATUSES.map(([key, label, dot]) =>
    `<div class="card"><span class="k"><span class="dot ${dot}"></span>${label}</span>` +
    `<span class="v num">${money(s[key].amount)}</span>` +
    `<span class="c">${count(s[key].count)} \u00b7 ${Math.round(pct(s[key]))}%</span></div>`
  ).join("") + Object.values(EXTRA_KINDS).map((kind) => {
    const part = s.extras[kind.field];
    return `<div class="card"><span class="k"><span class="dot ${kind.cls}"></span>${kind.short}</span>` +
      `<span class="v num">${money(part.amount)}</span>` +
      `<span class="c">${count(part.count)} \u00b7 not in the total</span></div>`;
  }).join("");
  // The bar: one part per status, sized by amount.
  document.getElementById("bar").innerHTML = DASH_STATUSES.map(([key, , dot]) =>
    `<span class="${dot}" style="width:${pct(sliceOf(key))}%"></span>`
  ).join("");
}

// Month-wise totals: one row per month. Only the search and status filters apply; the date filters do not,
// so every month stays listed. Clicking a month filters the list below to that month.
// One row per month, oldest first. The search, status and category filters apply; the date filters do not,
// so every month is listed.
function monthRows() {
  const q = filters.q.trim().toLowerCase();
  const months = new Map();
  allItems.forEach((i) => {
    if ((q && !i.name.toLowerCase().includes(q)) || !matchesStatus(i, filters.status) || !matchesCategory(i, filters.category)) return;
    const key = i.date.slice(0, 7);
    if (!months.has(key)) months.set(key, { key, total: 0, count: 0, paid: 0, pending: 0, delayed: 0, cancelled: 0 });
    const m = months.get(key);
    m.total += i.amount; m.count++; m[slice(i)] += i.amount;
  });
  return [...months.values()].sort((a, b) => (a.key < b.key ? -1 : 1));
}

// Clicking a month (in the list or in the trend chart) shows only that month; clicking it again shows every month.
function chooseMonth(key) {
  if (key === "all") {
    filters.year = filters.month = filters.day = "";
  } else {
    const [year, month] = key.split("-");
    const same = filters.year === year && filters.month === month && !filters.day;
    filters.year = same ? "" : year;
    filters.month = same ? "" : month;
    filters.day = "";
  }
  buildFilterOptions();
  render();
}

function renderMonths() {
  const rows = monthRows().reverse(); // newest month first
  const box = document.getElementById("dash-months");
  if (!rows.length) { box.innerHTML = `<div class="empty">No payments yet.</div>`; return; }
  const biggest = Math.max(...rows.map((m) => m.total)) || 1;
  const all = { total: 0, count: 0, paid: 0, pending: 0, delayed: 0, cancelled: 0 };
  rows.forEach((m) => { for (const k in all) all[k] += m[k]; });
  const none = !filters.year && !filters.month && !filters.day;
  const sum = DASH_STATUSES.map(([key, , dot]) => `<span class="${dot}" style="width:${all.total ? (all[key] / all.total) * 100 : 0}%"></span>`).join("");
  const grandTotal = `<button type="button" class="mrow mall" data-month="all" aria-pressed="${none}" ` +
    `aria-label="All months: ${money(all.total)}, ${all.count} ${all.count === 1 ? "payment" : "payments"}">` +
    `<span class="mname">All months</span><span class="mtrack" aria-hidden="true">${sum}</span>` +
    `<span class="mval num">${money(all.total)}</span>` +
    `<span class="mcount">${rows.length} ${rows.length === 1 ? "month" : "months"} \u00b7 ${all.count} ${all.count === 1 ? "payment" : "payments"}</span></button>`;
  box.innerHTML = rows.map((m) => {
    const [year, month] = m.key.split("-");
    const label = `${MONTH_NAMES[Number(month) - 1]} ${year}`;
    const on = filters.year === year && filters.month === month && !filters.day;
    const slices = DASH_STATUSES.map(([key, , dot]) => `<span class="${dot}" style="width:${(m[key] / biggest) * 100}%"></span>`).join("");
    return `<button type="button" class="mrow" data-month="${m.key}" aria-pressed="${on}" aria-label="${label}: ${money(m.total)}, ${m.count} ${m.count === 1 ? "payment" : "payments"}">` +
      `<span class="mname">${label}</span><span class="mtrack" aria-hidden="true">${slices}</span>` +
      `<span class="mval num">${money(m.total)}</span><span class="mcount">${m.count} ${m.count === 1 ? "payment" : "payments"}</span></button>`;
  }).join("") + grandTotal;
}

const MAX_IMAGES = 10; // images per payment (the server enforces the same limit)

function renderList(items) {
  const host = document.getElementById("list");
  if (!items.length) {
    host.innerHTML = `<div class="empty">No payments yet. Open "Import payments" above and choose a file or paste name, amount, date lines.</div>`;
    return;
  }
  const groups = {};
  items.forEach((i) => (groups[i.date] = groups[i.date] || []).push(i));
  let lastMonth = "";
  host.innerHTML = Object.keys(groups).sort().map((d) => {
    const g = groups[d];
    let monthHead = "";
    if (d.slice(0, 7) !== lastMonth) {
      lastMonth = d.slice(0, 7);
      monthHead = `<h3 class="monthhead">${esc(fmtMonth(lastMonth))}</h3>`;
    }
    const sum = g.reduce((a, i) => a + i.amount, 0);
    const isToday = d === today;
    const rows = g.map((i) => {
      let sub = "";
      if (mainStatus(i) === "pending" && !isDelayed(i) && i.date < today) sub = "Past due date";
      if (isDelayed(i) && i.new_date) sub = `Delayed to ${fmtDay(i.new_date)}`;
      const btn = (act, label, cls, pressed) =>
        `<button class="btn small ${cls}" data-act="${act}" data-id="${i.id}" type="button" aria-pressed="${pressed}">${pressed ? '<span aria-hidden="true">\u2713 </span>' : ""}${label}</button>`;
      // Paid and Cancel are one or the other. Delayed can be on together with either. Click a button again to switch it off.
      // Also: any of the three amounts, together with all of that.
      const statusBtn = (key, label, cls) => btn(mainStatus(i) === key ? "pending" : key, label, cls, mainStatus(i) === key);
      const delayedBtn = btn(isDelayed(i) ? "delayed:off" : "delayed:on", "Delayed", "warn", isDelayed(i));
      const extraBtn = (kindKey) => {
        const kind = EXTRA_KINDS[kindKey];
        const value = i[kind.field];
        return btn(`extra:${kindKey}`, value ? `${kind.short} ${money(value)}` : kind.short, kind.cls, Boolean(value));
      };
      const acts = `<span class="grp" role="group" aria-label="Status">${statusBtn("paid", "Paid", "ok")}${delayedBtn}${statusBtn("cancelled", "Cancel", "bad")}</span>` +
        `<span class="grp" role="group" aria-label="Also add an amount">${Object.keys(EXTRA_KINDS).map(extraBtn).join("")}</span>` +
        `<button class="btn small" data-act="edit" data-id="${i.id}" type="button">Edit</button>` +
        `<button class="btn small" data-act="remove" data-id="${i.id}" type="button">Remove</button>`;
      const chips = (isDelayed(i) && mainStatus(i) !== "pending" ? `<span class="chip warn">Delayed</span>` : "") +
        Object.values(EXTRA_KINDS).filter((kind) => i[kind.field])
          .map((kind) => `<span class="chip ${kind.cls}">${kind.short} ${money(i[kind.field])}</span>`).join("");
      const images = i.images || [];
      const picker = (label) =>
        `<label class="btn small imgbtn">${label}<input class="visually-hidden" type="file" accept="image/*" multiple data-img="${i.id}"></label>`;
      const thumbs = images.map((imageId, n) => {
        const url = `${API}/images/${imageId}`;
        return `<span class="thumbwrap"><a class="thumb" href="${url}" target="_blank" rel="noopener" data-view="${i.id}" data-n="${n}" title="View image ${n + 1} of ${images.length}">` +
          `<img src="${url}" alt="Image ${n + 1} for ${esc(i.name)}" width="36" height="36" loading="lazy"></a>` +
          `<button type="button" class="thumbx" data-act="img-del" data-id="${imageId}" aria-label="Remove image ${n + 1} of ${esc(i.name)}">\u00d7</button></span>`;
      }).join("");
      const desc = i.description || "";
      const descRow = `<div class="descrow"><div class="descfield"><label class="desclabel" for="desc-${i.id}">Description</label>` +
        `<textarea id="desc-${i.id}" class="descinput${desc ? "" : " need"}" rows="3" maxlength="500" ` +
        `placeholder="Write the description (required)" autocomplete="off" data-desc="${i.id}" required>${esc(desc)}</textarea></div>` +
        `<div class="catfield"><label class="desclabel" for="cat-${i.id}">Category</label>` +
        `<input id="cat-${i.id}" class="catinput" list="category-list" maxlength="60" autocomplete="off" placeholder="" ` +
        `value="${esc(i.category || "")}" data-cat="${i.id}"></div>` +
        `<button class="btn small" data-act="desc-save" data-id="${i.id}" type="button">Save</button>` +
        `<span class="imgctl">${thumbs}${images.length >= MAX_IMAGES ? "" : picker(images.length ? "Add image" : "Upload image")}</span></div>`;
      return `<div class="item ${rowClass(i)}${isToday ? " todayrow" : ""}">` +
        `<div class="nm">${esc(i.name)}${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</div>` +
        `<div class="amt num">${money(i.amount)}</div>` +
        `<span class="pill">${STATUS_LABEL[slice(i)]}</span>` +
        (chips ? `<div class="tags">${chips}</div>` : "") +
        `<div class="acts">${acts}</div>${descRow}</div>`;
    }).join("");
    return monthHead + `<div class="group"><div class="ghead"><h2>${esc(fmtDay(d))}${isToday ? ' <span class="tag">Today</span>' : ""}</h2>` +
      `<span class="gt num">${money(sum)}</span></div>${rows}</div>`;
  }).join("");
}

function summarize(items) {
  const blank = () => ({ count: 0, amount: 0 });
  const s = { pending: blank(), paid: blank(), delayed: blank(), cancelled: blank(), total: blank(), to_pay: blank(), slices: { pending: blank(), paid: blank(), delayed: blank(), cancelled: blank() }, extras: {} };
  Object.values(EXTRA_KINDS).forEach((kind) => { s.extras[kind.field] = blank(); });
  const add = (bucket, i) => { bucket.count++; bucket.amount += i.amount; };
  items.forEach((i) => {
    const status = mainStatus(i);
    const delayed = isDelayed(i);
    add(s.total, i);
    add(s.slices[slice(i)], i); // each payment once: this feeds the pie chart and the bar
    if (status === "paid" || status === "cancelled") add(s[status], i);
    else if (!delayed) add(s.pending, i);
    if (delayed) add(s.delayed, i); // Delayed counts every delayed payment, also one that is Paid or Cancelled as well
    if (status === "pending") add(s.to_pay, i); // not paid and not cancelled, delayed or not
    Object.values(EXTRA_KINDS).forEach((kind) => { // typed-in amounts: only in their own card, never in the numbers above
      if (i[kind.field]) { s.extras[kind.field].count++; s.extras[kind.field].amount += i[kind.field]; }
    });
  });
  return s;
}

// A status filter is Pending, Paid, Delayed, Cancelled, or one of the three typed-in amounts (payments that have it).
function matchesStatus(item, wanted) {
  if (!wanted) return true;
  if (EXTRA_KINDS[wanted]) return Boolean(item[EXTRA_KINDS[wanted].field]);
  if (wanted === "delayed") return isDelayed(item);
  if (wanted === "pending") return mainStatus(item) === "pending" && !isDelayed(item);
  return mainStatus(item) === wanted;
}

let allItems = [];
const filters = { q: "", status: "", category: "", year: "", month: "", day: "" };

const MONTH_NAMES = Array.from({ length: 12 }, (_, i) =>
  utc(`2000-${pad(i + 1)}-01`).toLocaleDateString("en-IN", { month: "long", timeZone: "UTC" })
);

function buildFilterOptions() {
  // Years from FIRST_YEAR to YEARS_AHEAD years after this year. A year that already has
  // payments is always included too. Change the two numbers below if you need more years.
  const FIRST_YEAR = 2015;
  const YEARS_AHEAD = 15;
  const thisYear = Number(today.slice(0, 4));
  const dataYears = allItems.map((i) => Number(i.date.slice(0, 4)));
  const firstYear = Math.min(FIRST_YEAR, ...dataYears);
  const lastYear = Math.max(thisYear + YEARS_AHEAD, ...dataYears);
  const years = [];
  for (let y = firstYear; y <= lastYear; y++) years.push(String(y));
  document.getElementById("f-year").innerHTML =
    `<option value="">All years</option>` +
    years.map((y) => `<option value="${y}"${y === filters.year ? " selected" : ""}>${y}</option>`).join("");
  document.getElementById("f-month").innerHTML =
    `<option value="">All months</option>` +
    MONTH_NAMES.map((n, i) => `<option value="${pad(i + 1)}"${pad(i + 1) === filters.month ? " selected" : ""}>${n}</option>`).join("");
  let dayOptions = `<option value="">All days</option>`;
  for (let d = 1; d <= 31; d++) dayOptions += `<option value="${pad(d)}"${pad(d) === filters.day ? " selected" : ""}>${d}</option>`;
  document.getElementById("f-day").innerHTML = dayOptions;
}

function filteredItems() {
  const q = filters.q.trim().toLowerCase();
  return allItems.filter((i) =>
    (!q || i.name.toLowerCase().includes(q)) &&
    matchesStatus(i, filters.status) &&
    matchesCategory(i, filters.category) &&
    (!filters.year || i.date.slice(0, 4) === filters.year) &&
    (!filters.month || i.date.slice(5, 7) === filters.month) &&
    (!filters.day || i.date.slice(8, 10) === filters.day)
  );
}

// The dashboard: numbers, pie chart, month rows and trend chart. The list is drawn separately.
function renderDashboard(items) {
  renderSummary(summarize(items || filteredItems()));
  renderMonths();
  renderTrend();
}

// The category filter and the list of suggestions in the category boxes: every category that is in use.
function renderCategoryOptions() {
  const seen = new Map();
  allItems.forEach((i) => { const name = categoryOf(i); if (name && !seen.has(name.toLowerCase())) seen.set(name.toLowerCase(), name); });
  const names = [...seen.values()].sort((a, b) => a.localeCompare(b));
  const select = document.getElementById("f-category");
  const noneUsed = allItems.some((i) => !categoryOf(i));
  select.innerHTML = `<option value="">All categories</option>` +
    names.map((n) => `<option value="${esc(n)}"${n.toLowerCase() === filters.category.toLowerCase() ? " selected" : ""}>${esc(n)}</option>`).join("") +
    (noneUsed && names.length ? `<option value="${NO_CATEGORY}"${filters.category === NO_CATEGORY ? " selected" : ""}>No category</option>` : "");
  document.getElementById("category-list").innerHTML = names.map((n) => `<option value="${esc(n)}"></option>`).join("");
}

function render() {
  const items = filteredItems();
  const active = filters.q || filters.status || filters.category || filters.year || filters.month || filters.day;
  document.getElementById("f-count").textContent = active
    ? `Showing ${items.length} of ${allItems.length} payments`
    : `${allItems.length} ${allItems.length === 1 ? "payment" : "payments"}`;
  document.getElementById("f-clear").hidden = !active;
  renderDashboard(items);
  if (active && !items.length && allItems.length) {
    document.getElementById("list").innerHTML = `<div class="empty">No payments match these filters. Click Clear filters to see everything.</div>`;
  } else {
    renderList(items);
  }
}

async function refresh() {
  try {
    showError("");
    allItems = await api("/payments"); // every payment, all months and years, sorted by date
    buildFilterOptions();
    renderCategoryOptions();
    render();
  } catch (err) {
    showError("Could not reach the server: " + err.message);
  }
}

// ---------- events ----------
document.getElementById("f-search").addEventListener("input", (e) => { filters.q = e.target.value; render(); });
document.getElementById("f-status").addEventListener("change", (e) => { filters.status = e.target.value; render(); });
document.getElementById("f-year").addEventListener("change", (e) => { filters.year = e.target.value; render(); });
document.getElementById("f-month").addEventListener("change", (e) => { filters.month = e.target.value; render(); });
document.getElementById("f-day").addEventListener("change", (e) => { filters.day = e.target.value; render(); });
document.getElementById("dash-months").addEventListener("click", (e) => {
  const row = e.target.closest("button[data-month]");
  if (row) chooseMonth(row.dataset.month);
});
document.getElementById("f-category").addEventListener("change", (e) => { filters.category = e.target.value; render(); });
document.getElementById("f-clear").addEventListener("click", () => {
  filters.q = filters.status = filters.category = filters.year = filters.month = filters.day = "";
  document.getElementById("f-search").value = "";
  document.getElementById("f-status").value = "";
  document.getElementById("f-category").value = "";
  buildFilterOptions();
  render();
});
document.getElementById("list").addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-act]");
  if (!button) return;
  const id = button.dataset.id;
  const act = button.dataset.act;
  try {
    if (act === "edit") {
      openEdit(id); // name, amount and date in a small window
      return;
    }
    if (act.startsWith("extra:")) {
      openAmount(act.slice(6), id); // asks for the amount in a small window
      return;
    }
    if (act === "desc-save") {
      await saveDescription(id, document.querySelector(`[data-desc="${id}"]`), true);
      return; // the list is not redrawn, so what you typed in the other rows stays
    } else if (act === "remove") reportSync(await api("/payments/" + id, { method: "DELETE" }));
    else if (act === "img-del") await api(`/images/${id}`, { method: "DELETE" }); // here id is the image's id
    else if (act === "delayed:on" || act === "delayed:off") reportSync(await api(`/payments/${id}/status`, { method: "PATCH", body: JSON.stringify({ delayed: act === "delayed:on" }) }));
    else reportSync(await api(`/payments/${id}/status`, { method: "PATCH", body: JSON.stringify({ status: act }) }));
    refresh();
  } catch (err) {
    showError(err.message);
  }
});

// Saves what is in one description box. An emptied box clears the saved description (and the Excel note).
// The list is not redrawn, so an old description can never come back into another row.
async function saveDescription(id, box, explicit) {
  if (!box || box.dataset.busy) return;
  const text = box.value.trim();
  const saved = (box.defaultValue || "").trim();
  if (text === saved) {
    if (explicit && !text) {
      showError("Description is required. Write a comment for this payment, then click Save.");
      box.focus();
    } else if (explicit) {
      showNotice("Description is already saved.");
    }
    return;
  }
  box.dataset.busy = "1";
  try {
    showError("");
    const result = await api(`/payments/${id}/description`, { method: "PUT", body: JSON.stringify({ description: text }) });
    box.defaultValue = text; // the saved value, used to see later whether the box was changed
    box.value = text;
    box.classList.toggle("need", !text);
    const item = allItems.find((x) => String(x.id) === String(id));
    if (item) item.description = text || null;
    reportSync(result);
  } catch (err) {
    showError(err.message);
  } finally {
    delete box.dataset.busy;
  }
}
document.getElementById("list").addEventListener("keydown", (e) => {
  const box = e.target.closest("[data-desc]");
  if (!box || e.key !== "Enter" || e.shiftKey) return; // Shift+Enter starts a new line
  e.preventDefault();
  saveDescription(box.dataset.desc, box, true);
});
// A category is saved when you leave its box (or press Enter). The list is not redrawn, like the description.
async function saveCategory(id, box) {
  const text = box.value.replace(/\s+/g, " ").trim();
  if (text === (box.defaultValue || "").trim()) return;
  try {
    showError("");
    const result = await api(`/payments/${id}/category`, { method: "PUT", body: JSON.stringify({ category: text || null }) });
    const item = allItems.find((x) => String(x.id) === String(id));
    if (item) item.category = result.category; // the server keeps the spelling of an existing category
    box.value = box.defaultValue = result.category || "";
    renderCategoryOptions();
    renderDashboard();
  } catch (err) {
    showError(err.message);
  }
}
document.getElementById("list").addEventListener("change", (e) => {
  const box = e.target.closest("[data-cat]");
  if (box) saveCategory(box.dataset.cat, box);
});
// Leaving the box (or pressing Enter) saves whatever is in it, including an erased text.
document.getElementById("list").addEventListener("change", (e) => {
  const box = e.target.closest("[data-desc]");
  if (box) saveDescription(box.dataset.desc, box, false);
});
// The image cannot be chosen until the description is filled in.
document.getElementById("list").addEventListener("click", (e) => {
  const picker = e.target.closest("input[data-img]");
  if (!picker) return;
  const box = document.querySelector(`[data-desc="${picker.dataset.img}"]`);
  if (!box || !box.value.trim()) {
    e.preventDefault();
    showError("Write the description first (required), then upload the image.");
    if (box) box.focus();
  }
});

let pendingFile = null; // the CSV / Excel / PDF chosen in the import box, stored when you import

async function doImport(replace) {
  const msg = document.getElementById("imp-msg");
  const text = document.getElementById("paste").value;
  try {
    const r = await api("/payments/import", { method: "POST", body: JSON.stringify({ text, replace }) });
    let stored = "";
    if (pendingFile) {
      try {
        const form = new FormData();
        form.append("file", pendingFile);
        const res = await fetch(API + "/files", { method: "POST", body: form });
        const info = await res.json().catch(() => ({}));
        if (!res.ok) throw new Error(info.detail || res.statusText);
        stored = info.already_stored ? " This file was already stored." : " File stored.";
        pendingFile = null;
        loadFiles();
      } catch (err) {
        stored = " The payments were imported, but the file could not be stored: " + err.message;
      }
    }
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
    msg.textContent = r.imported
      ? `Imported ${plural(r.imported, "payment")}.`
      : `Nothing new to add. ${plural(r.duplicates, "payment")} already in the list.`;
    if (r.imported && r.duplicates) msg.textContent += ` Skipped ${plural(r.duplicates, "duplicate")} (same name, amount and date).`;
    if (r.skipped) msg.textContent += ` Skipped ${plural(r.skipped, "line")} that could not be read.`;
    if (r.with_description) msg.textContent += ` ${plural(r.with_description, "description")} saved.`;
    msg.textContent += stored;
    document.getElementById("paste").value = "";
    refresh();
  } catch (err) {
    msg.textContent = err.message;
  }
}
document.getElementById("del-all").addEventListener("click", async () => {
  const msg = document.getElementById("del-msg");
  if (!allItems.length) { msg.textContent = "There are no payments to delete."; return; }
  if (!window.confirm(`Delete all ${allItems.length} payments? This cannot be undone.\n\nTip: use Download CSV first if you want a backup.`)) return;
  try {
    const r = await api("/payments", { method: "DELETE" });
    msg.textContent = `Deleted ${r.deleted} ${r.deleted === 1 ? "payment" : "payments"}.`;
    refresh();
  } catch (err) {
    msg.textContent = err.message;
  }
});

document.getElementById("imp-add").addEventListener("click", () => doImport(false));
document.getElementById("imp-replace").addEventListener("click", () => doImport(true));

// CSV, TSV, TXT, Excel (.xlsx, .xlsm, .xls), .ods, Word (.docx) and PDF files are read by the backend. The rows it finds
// are put in the box above, so you can check and fix them before importing.
document.getElementById("file").addEventListener("change", async (e) => {
  const input = e.target;
  const file = input.files && input.files[0];
  if (!file) return;
  const msg = document.getElementById("imp-msg");
  msg.textContent = "Reading " + file.name + "...";
  try {
    const form = new FormData();
    form.append("file", file);
    const res = await fetch(API + "/parse-file", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail;
      throw new Error(detail || res.statusText);
    }
    document.getElementById("paste").value = data.text;
    pendingFile = file;
    msg.textContent = `Found ${data.count} ${data.count === 1 ? "payment" : "payments"}` +
      (data.skipped ? ` (${data.skipped} row${data.skipped === 1 ? "" : "s"} skipped)` : "") +
      ". Check them, fix anything wrong, then choose Add to list or Replace whole list.";
  } catch (err) {
    msg.textContent = err.message;
  } finally {
    input.value = ""; // lets you pick the same file again
  }
});

document.getElementById("paste").addEventListener("input", (e) => {
  if (!e.target.value.trim()) pendingFile = null; // cleared by hand: nothing to store
});

// ---------- stored files ----------
const fmtSize = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

async function loadFiles() {
  const list = document.getElementById("filelist");
  try {
    const files = await api("/files");
    list.innerHTML = files.length
      ? files.map((f) =>
          `<li><div class="fname"><a href="${API}/files/${f.id}">${esc(f.filename)}</a>` +
          `<span class="sub">${fmtSize(f.size)} · ${esc(new Date(f.uploaded_at).toLocaleString("en-IN"))}</span></div>` +
          `<button class="btn small" data-file-del="${f.id}" type="button">Delete file</button></li>`).join("")
      : `<li class="empty-note">No files stored yet. Import a CSV, Excel or PDF file and it will appear here.</li>`;
  } catch (err) {
    list.innerHTML = `<li class="empty-note">Could not load files: ${esc(err.message)}</li>`;
  }
}
document.getElementById("filelist").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-file-del]");
  if (!b) return;
  if (!window.confirm("Delete this stored file? The payments already imported from it stay in the list.")) return;
  try { await api("/files/" + b.dataset.fileDel, { method: "DELETE" }); loadFiles(); } catch (err) { showError(err.message); }
});

// ---------- edit a payment: name, amount and date ----------
const editDialog = document.getElementById("edit-dialog");
let editFor = null;

function openEdit(paymentId) {
  const item = allItems.find((x) => String(x.id) === String(paymentId));
  if (!item) return;
  editFor = item.id;
  document.getElementById("edit-name").value = item.name;
  document.getElementById("edit-amount").value = item.amount;
  document.getElementById("edit-date").value = item.date;
  document.getElementById("edit-error").textContent = "";
  editDialog.showModal();
  document.getElementById("edit-name").focus();
  document.getElementById("edit-name").select();
}

document.getElementById("edit-cancel").addEventListener("click", () => editDialog.close());
document.getElementById("edit-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const error = document.getElementById("edit-error");
  const name = document.getElementById("edit-name").value.trim();
  const amount = Number(document.getElementById("edit-amount").value);
  const day = document.getElementById("edit-date").value;
  if (!name) { error.textContent = "Enter a name."; document.getElementById("edit-name").focus(); return; }
  if (!(amount > 0)) { error.textContent = "Enter an amount, more than 0."; document.getElementById("edit-amount").focus(); return; }
  if (!day) { error.textContent = "Choose a date."; document.getElementById("edit-date").focus(); return; }
  try {
    reportSync(await api(`/payments/${editFor}`, { method: "PUT", body: JSON.stringify({ name, amount, date: day }) }));
    editDialog.close();
    await refresh();
  } catch (err) {
    error.textContent = err.message;
  }
});

// ---------- Awaiting income, We paid, Part paid pending: type the amount in a small window ----------
const amountDialog = document.getElementById("amount-dialog");
const amountInput = document.getElementById("amount-input");
let amountFor = null; // { id, kind }

// The amount in a description such as "extra 500 paid" or "Rs.1,500 balance": the number closest to one of the
// words that belong to the kind. Nothing when no such word is there, so a wrong number is never filled in.
function guessAmount(text, words) {
  if (!text) return null;
  const found = [];
  const re = /(?:\u20b9|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d+)?)/gi;
  let m;
  while ((m = re.exec(text))) {
    const n = Number(m[1].replace(/,/g, ""));
    if (n > 0) found.push({ n, at: m.index });
  }
  if (!found.length) return null;
  const word = words.exec(text);
  return word ? found.sort((a, b) => Math.abs(a.at - word.index) - Math.abs(b.at - word.index))[0].n : null;
}

function amountHint() {
  const item = allItems.find((x) => x.id === amountFor.id);
  const value = Number(amountInput.value);
  let text = "";
  if (item && value > 0) {
    if (amountFor.kind === "part_pending" && value < item.amount) text = `Paid so far: ${money(item.amount - value)}`;
  }
  document.getElementById("amount-hint").textContent = text;
}

function openAmount(kind, paymentId) {
  const item = allItems.find((x) => String(x.id) === String(paymentId));
  const info = EXTRA_KINDS[kind];
  if (!item || !info) return;
  amountFor = { id: item.id, kind };
  document.getElementById("amount-title").textContent = info.short;
  document.getElementById("amount-name").textContent = `${item.name} \u00b7 amount ${money(item.amount)}`;
  document.getElementById("amount-desc").textContent = item.description ? `Description: ${item.description}` : "No description yet.";
  document.getElementById("amount-label").textContent = info.label;
  amountInput.value = item[info.field] || guessAmount(item.description, info.words) || "";
  document.getElementById("amount-clear").hidden = !item[info.field];
  document.getElementById("amount-error").textContent = "";
  amountHint();
  amountDialog.showModal();
  amountInput.focus();
  amountInput.select();
}

async function saveAmount(amount) {
  const error = document.getElementById("amount-error");
  try {
    reportSync(await api(`/payments/${amountFor.id}/extra/${amountFor.kind}`, { method: "PUT", body: JSON.stringify({ amount }) }));
    amountDialog.close();
    await refresh();
  } catch (err) {
    error.textContent = err.message;
  }
}

amountInput.addEventListener("input", amountHint);
document.getElementById("amount-cancel").addEventListener("click", () => amountDialog.close());
document.getElementById("amount-clear").addEventListener("click", () => saveAmount(null)); // takes the amount away again
document.getElementById("amount-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const value = Number(amountInput.value);
  if (!(value > 0)) {
    document.getElementById("amount-error").textContent = "Enter the amount, more than 0.";
    amountInput.focus();
    return;
  }
  saveAmount(value);
});

// ---------- image viewer: all images of a payment, one after the other ----------
const viewer = document.getElementById("viewer");
const view = { ids: [], at: 0, name: "", desc: "" };

function showImage(at) {
  const total = view.ids.length;
  if (!total) return;
  view.at = (at + total) % total; // after the last image comes the first
  const url = `${API}/images/${view.ids[view.at]}`;
  const img = document.getElementById("viewer-img");
  img.src = url;
  img.alt = `Image ${view.at + 1} of ${total} for ${view.name}`;
  document.getElementById("viewer-count").textContent = `Image ${view.at + 1} of ${total}`;
  document.getElementById("viewer-open").href = url;
  document.getElementById("viewer-prev").hidden = document.getElementById("viewer-next").hidden = total < 2;
  document.getElementById("viewer-strip").innerHTML = total < 2 ? "" : view.ids.map((imageId, n) =>
    `<button type="button" class="vthumb" data-n="${n}" aria-label="Show image ${n + 1}" aria-current="${n === view.at}">` +
    `<img src="${API}/images/${imageId}" alt="" loading="lazy"></button>`).join("");
}

function openViewer(paymentId, at) {
  const item = allItems.find((x) => String(x.id) === String(paymentId));
  if (!item || !(item.images || []).length) return;
  view.ids = item.images;
  view.name = item.name;
  document.getElementById("viewer-title").textContent = `${item.name} \u00b7 ${money(item.amount)}`;
  document.getElementById("viewer-desc").textContent = item.description || "";
  showImage(at);
  if (!viewer.open) viewer.showModal();
}

document.getElementById("list").addEventListener("click", (e) => {
  const link = e.target.closest("a.thumb[data-view]");
  if (!link || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return; // those open the image in a new tab as before
  e.preventDefault();
  openViewer(link.dataset.view, Number(link.dataset.n));
});
document.getElementById("viewer-prev").addEventListener("click", () => showImage(view.at - 1));
document.getElementById("viewer-next").addEventListener("click", () => showImage(view.at + 1));
document.getElementById("viewer-close").addEventListener("click", () => viewer.close());
document.getElementById("viewer-strip").addEventListener("click", (e) => {
  const thumb = e.target.closest("button[data-n]");
  if (thumb) showImage(Number(thumb.dataset.n));
});
viewer.addEventListener("click", (e) => { if (e.target === viewer) viewer.close(); }); // a click outside the box
viewer.addEventListener("keydown", (e) => {
  if (e.key === "ArrowLeft") { e.preventDefault(); showImage(view.at - 1); }
  if (e.key === "ArrowRight") { e.preventDefault(); showImage(view.at + 1); }
});
let swipeFrom = null; // a swipe on a phone moves to the next or previous image
const stage = document.getElementById("viewer-stage");
stage.addEventListener("touchstart", (e) => { swipeFrom = e.touches[0].clientX; }, { passive: true });
stage.addEventListener("touchend", (e) => {
  if (swipeFrom === null) return;
  const moved = e.changedTouches[0].clientX - swipeFrom;
  swipeFrom = null;
  if (Math.abs(moved) > 50) showImage(view.at + (moved < 0 ? 1 : -1));
}, { passive: true });

// ---------- payment images ----------
// Big photos are shrunk in the browser first (max 1400 px, JPEG), so uploads stay small and fast.
async function shrinkImage(file) {
  if (file.type === "image/gif" || (file.size <= 400 * 1024 && /^image\/(jpeg|png|webp)$/.test(file.type))) return file;
  try {
    const bitmap = await createImageBitmap(file);
    const scale = Math.min(1, 1400 / Math.max(bitmap.width, bitmap.height));
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    const ctx = canvas.getContext("2d");
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.82));
    return blob || file;
  } catch (err) {
    return file; // the server still checks the type and size
  }
}
document.getElementById("list").addEventListener("change", async (e) => {
  const input = e.target.closest("input[data-img]");
  if (!input || !input.files || !input.files[0]) return;
  const id = input.dataset.img;
  let problem = "";
  try {
    showError("");
    const box = document.querySelector(`[data-desc="${id}"]`);
    const text = box ? box.value.trim() : "";
    if (!text) throw new Error("Description is required. Write it first, then upload the image.");
    if (text !== (box.defaultValue || "").trim()) {
      reportSync(await api(`/payments/${id}/description`, { method: "PUT", body: JSON.stringify({ description: text }) }));
    }
    // Several images can be chosen at once; they are uploaded one after the other.
    const item = allItems.find((x) => String(x.id) === String(id));
    const room = Math.max(0, MAX_IMAGES - ((item && item.images) || []).length);
    const files = [...input.files];
    if (files.length > room) problem = `A payment can have up to ${MAX_IMAGES} images, so only the first ${room} were added.`;
    for (const file of files.slice(0, room)) {
      const blob = await shrinkImage(file);
      const form = new FormData();
      form.append("file", blob, "payment-image");
      const res = await fetch(`${API}/payments/${id}/image`, { method: "POST", body: form });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail || res.statusText);
    }
  } catch (err) {
    problem = err.message;
  }
  input.value = "";
  await refresh(); // shows the images that did get uploaded
  if (problem) showError(problem);
});

document.getElementById("a-date").value = today;
document.getElementById("add-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const body = {
    name: document.getElementById("a-name").value.trim(),
    amount: parseFloat(document.getElementById("a-amt").value.replace(/[^0-9.]/g, "")),
    date: document.getElementById("a-date").value,
  };
  try {
    reportSync(await api("/payments", { method: "POST", body: JSON.stringify(body) }));
    document.getElementById("a-name").value = "";
    document.getElementById("a-amt").value = "";
    refresh();
  } catch (err) {
    showError(err.message);
  }
});

refresh();
loadFiles();

// Other people use the same link: pick up their changes without a manual reload.
function refreshQuietly() {
  if (document.hidden) return;
  const active = document.activeElement;
  if (active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) return; // do not disturb typing
  refresh();
  loadFiles();
}
setInterval(refreshQuietly, 15000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshQuietly(); });