// If you open index.html from another server (e.g. Live Server on port 5500),
// change this to "http://127.0.0.1:8000/api".
const API = "/api";

const STATUS_LABEL = { pending: "Pending", paid: "Paid", delayed: "Delayed", cancelled: "Cancelled" };
const STATUS_CLASS = { pending: "", paid: "ok", delayed: "warn", cancelled: "bad" };

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

function showError(message) {
  const box = document.getElementById("error");
  box.textContent = message || "";
  box.hidden = !message;
}

// ---------- rendering ----------
function renderSummary(s) {
  const stat = (label, part, dot) =>
    `<div class="stat"><span class="k">${dot ? `<span class="dot ${dot}"></span>` : ""}${label}</span>` +
    `<span class="v num">${money(part.amount)}</span>` +
    `<span class="c">${part.count} ${part.count === 1 ? "payment" : "payments"}</span></div>`;
  document.getElementById("stats").innerHTML =
    stat("Total", s.total, "") +
    stat("Paid", s.paid, "ok") +
    stat("Delayed", s.delayed, "warn") +
    stat("Cancelled", s.cancelled, "bad") +
    stat("Still to pay", s.to_pay, "");
  const base = s.total.amount || 1;
  document.getElementById("bar").innerHTML =
    `<span class="ok" style="width:${(s.paid.amount / base) * 100}%"></span>` +
    `<span class="warn" style="width:${(s.delayed.amount / base) * 100}%"></span>` +
    `<span class="bad" style="width:${(s.cancelled.amount / base) * 100}%"></span>`;
}

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
      if (i.status === "pending" && i.date < today) sub = "Past due date";
      const btn = (act, label, cls) => `<button class="btn small ${cls}" data-act="${act}" data-id="${i.id}" type="button">${label}</button>`;
      let acts = "";
      if (i.status === "pending") {
        acts += btn("paid", "Paid", "ok") + btn("delayed", "Delayed", "warn") + btn("cancelled", "Cancel", "bad");
      } else {
        acts += btn("pending", "Undo", "");
        if (i.status === "delayed") acts += btn("paid", "Paid now", "ok");
      }
      acts += btn("remove", "Remove", "");
      return `<div class="item ${STATUS_CLASS[i.status]}${isToday ? " todayrow" : ""}">` +
        `<div class="nm">${esc(i.name)}${sub ? `<span class="sub">${esc(sub)}</span>` : ""}</div>` +
        `<div class="amt num">${money(i.amount)}</div>` +
        `<span class="pill">${STATUS_LABEL[i.status]}</span>` +
        `<div class="acts">${acts}</div></div>`;
    }).join("");
    return monthHead + `<div class="group"><div class="ghead"><h2>${esc(fmtDay(d))}${isToday ? ' <span class="tag">Today</span>' : ""}</h2>` +
      `<span class="gt num">${money(sum)}</span></div>${rows}</div>`;
  }).join("");
}

function summarize(items) {
  const blank = () => ({ count: 0, amount: 0 });
  const s = { pending: blank(), paid: blank(), delayed: blank(), cancelled: blank(), total: blank(), to_pay: blank() };
  items.forEach((i) => {
    s[i.status].count++; s[i.status].amount += i.amount;
    s.total.count++; s.total.amount += i.amount;
    if (i.status === "pending" || i.status === "delayed") { s.to_pay.count++; s.to_pay.amount += i.amount; }
  });
  return s;
}

let allItems = [];
const filters = { q: "", status: "", year: "", month: "", day: "" };

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
    (!filters.status || i.status === filters.status) &&
    (!filters.year || i.date.slice(0, 4) === filters.year) &&
    (!filters.month || i.date.slice(5, 7) === filters.month) &&
    (!filters.day || i.date.slice(8, 10) === filters.day)
  );
}

function render() {
  const items = filteredItems();
  const active = filters.q || filters.status || filters.year || filters.month || filters.day;
  document.getElementById("f-count").textContent = active
    ? `Showing ${items.length} of ${allItems.length} payments`
    : `${allItems.length} ${allItems.length === 1 ? "payment" : "payments"}`;
  document.getElementById("f-clear").hidden = !active;
  renderSummary(summarize(items));
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
document.getElementById("f-clear").addEventListener("click", () => {
  filters.q = filters.status = filters.year = filters.month = filters.day = "";
  document.getElementById("f-search").value = "";
  document.getElementById("f-status").value = "";
  buildFilterOptions();
  render();
});
document.getElementById("list").addEventListener("click", async (e) => {
  const button = e.target.closest("button[data-act]");
  if (!button) return;
  const id = button.dataset.id;
  const act = button.dataset.act;
  try {
    if (act === "remove") await api("/payments/" + id, { method: "DELETE" });
    else await api(`/payments/${id}/status`, { method: "PATCH", body: JSON.stringify({ status: act }) });
    refresh();
  } catch (err) {
    showError(err.message);
  }
});

async function doImport(replace) {
  const msg = document.getElementById("imp-msg");
  const text = document.getElementById("paste").value;
  try {
    const r = await api("/payments/import", { method: "POST", body: JSON.stringify({ text, replace }) });
    const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
    msg.textContent = r.imported
      ? `Imported ${plural(r.imported, "payment")}.`
      : `Nothing new to add. ${plural(r.duplicates, "payment")} already in the list.`;
    if (r.imported && r.duplicates) msg.textContent += ` Skipped ${plural(r.duplicates, "duplicate")} (same name, amount and date).`;
    if (r.skipped) msg.textContent += ` Skipped ${plural(r.skipped, "line")} that could not be read.`;
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

// CSV, TXT, Excel (.xlsx) and PDF files are read by the backend. The rows it finds
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
    msg.textContent = `Found ${data.count} ${data.count === 1 ? "payment" : "payments"}` +
      (data.skipped ? ` (${data.skipped} row${data.skipped === 1 ? "" : "s"} skipped)` : "") +
      ". Check them, fix anything wrong, then choose Add to list or Replace whole list.";
  } catch (err) {
    msg.textContent = err.message;
  } finally {
    input.value = ""; // lets you pick the same file again
  }
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
    await api("/payments", { method: "POST", body: JSON.stringify(body) });
    document.getElementById("a-name").value = "";
    document.getElementById("a-amt").value = "";
    refresh();
  } catch (err) {
    showError(err.message);
  }
});

refresh();