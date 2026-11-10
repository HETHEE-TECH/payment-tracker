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

let noticeTimer = null;
function showNotice(message) {
  const box = document.getElementById("notice");
  box.textContent = message || "";
  box.hidden = !message;
  clearTimeout(noticeTimer);
  if (message) noticeTimer = setTimeout(() => { box.hidden = true; }, 6000);
}

// Tells the user which stored Excel files changed after adding a payment or changing its status.
function reportSync(result) {
  if (!result) return;
  if (result.sync_error) showError("Saved, but the stored Excel could not be updated: " + result.sync_error);
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

const imgVersion = {}; // bumps when an image changes, so the browser shows the new one

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
      acts += btn("edit", "Edit", "") + btn("remove", "Remove", "");
      const imgUrl = `${API}/payments/${i.id}/image?v=${imgVersion[i.id] || 0}`;
      const picker = (label) =>
        `<label class="btn small imgbtn">${label}<input class="visually-hidden" type="file" accept="image/*" data-img="${i.id}"></label>`;
      acts += `<span class="imgctl">` + (i.has_image
        ? `<a class="thumb" href="${imgUrl}" target="_blank" rel="noopener" title="Open image"><img src="${imgUrl}" alt="Image for ${esc(i.name)}" width="36" height="36" loading="lazy"></a>` +
          picker("Change image") + btn("img-remove", "Remove image", "")
        : picker("Upload image")) + `</span>`;
      if (editingId === i.id) {
        return `<form class="item editrow" data-edit-form="${i.id}">` +
          `<div class="addgrid">` +
          `<div class="field"><label>Name</label><input name="name" required value="${esc(i.name)}"></div>` +
          `<div class="field"><label>Amount (₹)</label><input name="amount" inputmode="decimal" required value="${i.amount}"></div>` +
          `<div class="field"><label>Date</label><input name="date" type="date" required value="${i.date}"></div>` +
          `<div class="row-actions"><button class="btn primary small" type="submit">Save</button>` +
          `<button class="btn small" data-act="edit-cancel" data-id="${i.id}" type="button">Cancel</button></div>` +
          `</div></form>`;
      }
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

let editingId = null; // the payment being edited; auto-refresh keeps the form open

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
  if (act === "edit" || act === "edit-cancel") {
    editingId = act === "edit" ? Number(id) : null;
    render();
    return;
  }
  try {
    if (act === "remove") await api("/payments/" + id, { method: "DELETE" });
    else if (act === "img-remove") await api(`/payments/${id}/image`, { method: "DELETE" });
    else reportSync(await api(`/payments/${id}/status`, { method: "PATCH", body: JSON.stringify({ status: act }) }));
    refresh();
  } catch (err) {
    showError(err.message);
  }
});

document.getElementById("list").addEventListener("submit", async (e) => {
  const form = e.target.closest("form[data-edit-form]");
  if (!form) return;
  e.preventDefault();
  const body = {
    name: form.elements.name.value.trim(),
    amount: parseFloat(form.elements.amount.value.replace(/[^0-9.]/g, "")),
    date: form.elements.date.value,
  };
  try {
    reportSync(await api("/payments/" + form.dataset.editForm, { method: "PUT", body: JSON.stringify(body) }));
    editingId = null;
    refresh();
  } catch (err) {
    showError(err.message);
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
  try {
    showError("");
    const blob = await shrinkImage(input.files[0]);
    const form = new FormData();
    form.append("file", blob, "payment-image");
    const res = await fetch(`${API}/payments/${id}/image`, { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail || res.statusText);
    imgVersion[id] = Date.now();
    refresh();
  } catch (err) {
    showError(err.message);
    input.value = "";
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
  if (document.hidden || editingId !== null) return;
  const active = document.activeElement;
  if (active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) return; // do not disturb typing
  refresh();
  loadFiles();
}// If you open index.html from another server (e.g. Live Server on port 5500),
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

let noticeTimer = null;
function showNotice(message) {
  const box = document.getElementById("notice");
  box.textContent = message || "";
  box.hidden = !message;
  clearTimeout(noticeTimer);
  if (message) noticeTimer = setTimeout(() => { box.hidden = true; }, 6000);
}

// Tells the user which stored Excel files changed after adding a payment or changing its status.
function reportSync(result) {
  if (!result) return;
  if (result.sync_error) showError("Saved, but the stored Excel could not be updated: " + result.sync_error);
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

const imgVersion = {}; // bumps when an image changes, so the browser shows the new one

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
      acts += btn("edit", "Edit", "") + btn("remove", "Remove", "");
      const imgUrl = `${API}/payments/${i.id}/image?v=${imgVersion[i.id] || 0}`;
      const picker = (label) =>
        `<label class="btn small imgbtn">${label}<input class="visually-hidden" type="file" accept="image/*" data-img="${i.id}"></label>`;
      acts += `<span class="imgctl">` + (i.has_image
        ? `<a class="thumb" href="${imgUrl}" target="_blank" rel="noopener" title="Open image"><img src="${imgUrl}" alt="Image for ${esc(i.name)}" width="36" height="36" loading="lazy"></a>` +
          picker("Change image") + btn("img-remove", "Remove image", "")
        : picker("Upload image")) + `</span>`;
      if (editingId === i.id) {
        return `<form class="item editrow" data-edit-form="${i.id}">` +
          `<div class="addgrid">` +
          `<div class="field"><label>Name</label><input name="name" required value="${esc(i.name)}"></div>` +
          `<div class="field"><label>Amount (₹)</label><input name="amount" inputmode="decimal" required value="${i.amount}"></div>` +
          `<div class="field"><label>Date</label><input name="date" type="date" required value="${i.date}"></div>` +
          `<div class="row-actions"><button class="btn primary small" type="submit">Save</button>` +
          `<button class="btn small" data-act="edit-cancel" data-id="${i.id}" type="button">Cancel</button></div>` +
          `</div></form>`;
      }
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

let editingId = null; // the payment being edited; auto-refresh keeps the form open

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
  if (act === "edit" || act === "edit-cancel") {
    editingId = act === "edit" ? Number(id) : null;
    render();
    return;
  }
  try {
    if (act === "remove") await api("/payments/" + id, { method: "DELETE" });
    else if (act === "img-remove") await api(`/payments/${id}/image`, { method: "DELETE" });
    else reportSync(await api(`/payments/${id}/status`, { method: "PATCH", body: JSON.stringify({ status: act }) }));
    refresh();
  } catch (err) {
    showError(err.message);
  }
});

document.getElementById("list").addEventListener("submit", async (e) => {
  const form = e.target.closest("form[data-edit-form]");
  if (!form) return;
  e.preventDefault();
  const body = {
    name: form.elements.name.value.trim(),
    amount: parseFloat(form.elements.amount.value.replace(/[^0-9.]/g, "")),
    date: form.elements.date.value,
  };
  try {
    reportSync(await api("/payments/" + form.dataset.editForm, { method: "PUT", body: JSON.stringify(body) }));
    editingId = null;
    refresh();
  } catch (err) {
    showError(err.message);
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
    msg.textContent += stored;
    const finalMessage = msg.textContent;
    document.getElementById("paste").value = "";
    // clear filters so the new payments are not hidden by an old search, month or status
    filters.q = filters.status = filters.year = filters.month = filters.day = "";
    document.getElementById("f-search").value = "";
    document.getElementById("f-status").value = "";
    await refresh();
    if (r.imported) showNotice(finalMessage);
    else showError(finalMessage);
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (err) {
    msg.textContent = err.message;
    showError("Import failed: " + err.message);
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
  try {
    showError("");
    const blob = await shrinkImage(input.files[0]);
    const form = new FormData();
    form.append("file", blob, "payment-image");
    const res = await fetch(`${API}/payments/${id}/image`, { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg).join(", ") : data.detail || res.statusText);
    imgVersion[id] = Date.now();
    refresh();
  } catch (err) {
    showError(err.message);
    input.value = "";
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
  if (document.hidden || editingId !== null) return;
  const active = document.activeElement;
  if (active && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName)) return; // do not disturb typing
  refresh();
  loadFiles();
}
setInterval(refreshQuietly, 15000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshQuietly(); });
setInterval(refreshQuietly, 15000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshQuietly(); });