// Network scan page: probe the user's local network for HTTP services
// from the browser, and bulk-add the discovered services to the apps
// list. The actual TCP/HTTP probing is done in the browser (a raw
// port scan would require server-side network access — we don't want
// to expose the server's environment). The server only validates the
// target range and expands it into a candidate list.
//
// Probe mechanism: fetch(url, { mode: "no-cors" }). The browser will
// complete the request against any HTTP service, even with CORS-
// protected responses (we don't need to read the body — we just need
// to know "is there an HTTP server here?"). Non-HTTP services and
// dead hosts reject the fetch; we treat that as a miss.
//
// Wrapped in an IIFE so our `init` doesn't collide with the one in
// settings.js (both are loaded on the same page).

(function () {

const STORAGE_KEY = "nasportal.scan";
const PROBE_TIMEOUT_MS = 1500;
const PROBE_BATCH = 16;
const SCRAPE_BATCH = 8;
const ADD_BATCH = 4;
const PRESET_COMMON = "80, 443, 8080, 8443, 32400, 8989, 7878, 8686, 8123, 5000, 5001";
const PRESET_WEB = "80, 443, 8080, 8443";

// Module-level state. A single tab can run one scan at a time.
let networks = [];        // ["10.0.0.0/24", ...] from /api/networks/local
let hits = new Map();     // url -> { row, candidate, status, title, description, error }
let selected = new Set(); // urls checked for bulk-add
let added = new Set();    // urls that were already added (from a previous run in this tab)
let probeAbort = null;    // AbortController for the active scan
let isScanning = false;

async function init() {
  // Apply theme + page chrome. settings.js has already done this for
  // the authed view; re-applying is idempotent. We deliberately do NOT
  // touch the brandSub — that should reflect which tab the user is on,
  // and settings.js owns the chrome.
  const settings = await api.get("/api/settings");
  applyTheme(settings.theme);
  applyBackgroundColor(settings.background_color);
  applyPortalWidth(settings.portal_width);

  // Load networks + restore last input from localStorage.
  try {
    const data = await api.get("/api/networks/local");
    networks = data.networks || [];
  } catch (e) {
    networks = [];
  }
  const stored = loadStored();
  populateTargetDropdown(stored.target);
  populatePorts(stored.ports);
  updateCidrVisibility();

  wireEvents();
  updateAddButton();
}

// ---- storage ----
function loadStored() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch (e) { /* ignore */ }
  return { target: "", ports: PRESET_COMMON };
}

function persistStored() {
  const data = {
    target: document.getElementById("scan-target").value,
    ports: document.getElementById("scan-ports").value,
  };
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch (e) { /* quota */ }
}

// ---- target dropdown / CIDR input ----
function populateTargetDropdown(saved) {
  const sel = document.getElementById("scan-target");
  sel.replaceChildren();
  // First detected network is the most likely target — default to it.
  if (networks.length) {
    for (const n of networks) sel.appendChild(el("option", { value: n, text: n }));
    sel.appendChild(el("option", { value: "__custom__", text: "Custom CIDR…" }));
  } else {
    sel.appendChild(el("option", { value: "__custom__", text: "Custom CIDR…" }));
  }
  // Restore saved value if it still applies; otherwise default to the
  // first detected network.
  let target = saved;
  if (target && target !== "__custom__" && !networks.includes(target)) target = "";
  if (!target) target = networks[0] || "__custom__";
  sel.value = target;
  if (target === "__custom__") {
    const cidrEl = document.getElementById("scan-cidr");
    cidrEl.hidden = false;
    cidrEl.value = saved && saved !== "__custom__" ? "" : (saved || "");
  }
}

function updateCidrVisibility() {
  const sel = document.getElementById("scan-target");
  const cidrEl = document.getElementById("scan-cidr");
  cidrEl.hidden = sel.value !== "__custom__";
}

function getTarget() {
  const sel = document.getElementById("scan-target");
  if (sel.value === "__custom__") {
    return (document.getElementById("scan-cidr").value || "").trim();
  }
  return sel.value;
}

// Parse a free-form target string into the shape the server expects.
// Accepts:
//   "10.0.0.0/24"           -> {kind: "cidr", cidr}
//   "10.0.0.5"              -> {kind: "cidr", cidr: "10.0.0.5/32"}
//   "10.0.0.1-10.0.0.254"   -> {kind: "range", start, end}
//   "10.0.0.1 - 10.0.0.254" -> same (whitespace tolerated)
// Returns null on any other input (the caller surfaces a friendly error).
function parseTarget(raw) {
  const s = (raw || "").trim();
  if (!s) return null;
  if (s.includes("/")) return { kind: "cidr", cidr: s };
  // Range form: two dotted-quads joined by "-". Whitespace around the
  // dash is optional. Reject anything with multiple dashes (probably a
  // typo) and anything where either side isn't a plain IP.
  const m = s.match(/^(\d+\.\d+\.\d+\.\d+)\s*-\s*(\d+\.\d+\.\d+\.\d+)$/);
  if (m) return { kind: "range", start: m[1], end: m[2] };
  // Single IP: treat as a /32 CIDR. The server's /32 expansion
  // produces exactly one host, which is what the user wants.
  if (/^\d+\.\d+\.\d+\.\d+$/.test(s)) return { kind: "cidr", cidr: s + "/32" };
  return null;
}

// ---- ports ----
function populatePorts(saved) {
  document.getElementById("scan-ports").value = saved || PRESET_COMMON;
}

// Parse a free-form ports string into a sorted, deduped list of
// integer ports. Each token is either a single port ("80") or an
// inclusive range ("5000-6000"). Tokens may be mixed and separated
// by commas or whitespace.
//
// Returns either a list of ports, or ``{error: "..."}`` on a bad
// input. A range that expands past ``MAX_PORTS`` is silently
// truncated to the first MAX_PORTS — the caller is expected to
// check the returned list's length and warn the user. (Returning
// an error would force the user to retype a narrower range even
// when the over-cap range is exactly what they want to scan.)
const MAX_PORTS = 128;

function parsePorts(text) {
  if (!text) return [];
  const out = [];
  const seen = new Set();
  let rangeError = null;
  for (const raw of String(text).split(/[,\s]+/)) {
    const v = raw.trim();
    if (!v) continue;
    const m = v.match(/^(\d+)(?:\s*-\s*(\d+))?$/);
    if (!m) { rangeError = `Bad port token: "${v}".`; break; }
    const a = parseInt(m[1], 10);
    const b = m[2] === undefined ? a : parseInt(m[2], 10);
    if (!(a >= 1 && a <= 65535) || !(b >= 1 && b <= 65535)) {
      rangeError = "Ports must be 1–65535."; break;
    }
    if (a > b) { rangeError = `Bad range: ${a} > ${b}.`; break; }
    // Expand the range, but stop once we hit MAX_PORTS so a typo
    // ("1-65535") doesn't allocate a quarter million entries.
    for (let p = a; p <= b && out.length < MAX_PORTS; p++) {
      if (!seen.has(p)) { seen.add(p); out.push(p); }
    }
  }
  if (rangeError) return { error: rangeError };
  out.sort((a, b) => a - b);
  return out;
}

// Returns the warning text if the user's input expanded past
// MAX_PORTS, else an empty string. Used to surface a soft cap in
// the UI before the user clicks Start.
function portsOverflowWarning(text) {
  if (!text) return "";
  // Quick check: count any range that could exceed MAX_PORTS
  // without actually materializing it.
  let total = 0;
  for (const raw of String(text).split(/[,\s]+/)) {
    const v = raw.trim();
    if (!v) continue;
    const m = v.match(/^(\d+)(?:\s*-\s*(\d+))?$/);
    if (!m) continue;
    const a = parseInt(m[1], 10);
    const b = m[2] === undefined ? a : parseInt(m[2], 10);
    if (!(a >= 1 && a <= 65535) || !(b >= 1 && b <= 65535)) continue;
    if (a > b) continue;
    total += Math.min(b - a + 1, MAX_PORTS);
    if (total > MAX_PORTS) return `Ports expanded to more than ${MAX_PORTS}; only the first ${MAX_PORTS} will be scanned.`;
  }
  return "";
}

// ---- event wiring ----
function wireEvents() {
  const sel = document.getElementById("scan-target");
  sel.addEventListener("change", () => { updateCidrVisibility(); persistStored(); });
  document.getElementById("scan-cidr").addEventListener("input", persistStored);
  document.getElementById("scan-ports").addEventListener("input", persistStored);
  document.getElementById("scan-preset-common").addEventListener("click", () => {
    document.getElementById("scan-ports").value = PRESET_COMMON;
    persistStored();
  });
  document.getElementById("scan-preset-web").addEventListener("click", () => {
    document.getElementById("scan-ports").value = PRESET_WEB;
    persistStored();
  });
  document.getElementById("scan-start").addEventListener("click", startScan);
  document.getElementById("scan-stop").addEventListener("click", stopScan);
  document.getElementById("scan-clear").addEventListener("click", clearResults);
  document.getElementById("scan-select-all").addEventListener("change", onSelectAll);
  document.getElementById("scan-add-selected").addEventListener("click", addSelected);
}

// ---- status helpers ----
function setMsg(id, text, cls) {
  const el = document.getElementById(id);
  el.className = "msg" + (cls ? " " + cls : "");
  setText(el, text || "");
}

function setScanning(running) {
  isScanning = running;
  document.getElementById("scan-start").disabled = running;
  document.getElementById("scan-stop").disabled = !running;
  document.getElementById("scan-target").disabled = running;
  document.getElementById("scan-cidr").disabled = running;
  document.getElementById("scan-ports").disabled = running;
  document.querySelectorAll("#scan-preset-common, #scan-preset-web")
    .forEach((b) => { b.disabled = running; });
}

function clearResults() {
  if (isScanning) stopScan();
  hits.clear();
  selected.clear();
  added = new Set(); // also forget what was added this session
  document.getElementById("scan-rows").replaceChildren();
  document.getElementById("scan-results").hidden = true;
  document.getElementById("scan-progress-wrap").hidden = true;
  document.getElementById("scan-progress-bar").style.width = "0";
  document.getElementById("scan-progress-label").textContent = "Ready.";
  document.getElementById("scan-select-all").checked = false;
  setMsg("scan-msg", "");
  setMsg("scan-add-msg", "");
  updateAddButton();
}

// ---- scan flow ----
async function startScan() {
  const target = getTarget();
  if (!target) { setMsg("scan-msg", "Pick a target network.", "err"); return; }
  const ports = parsePorts(document.getElementById("scan-ports").value);
  if (Array.isArray(ports) && ports.length === 0) {
    setMsg("scan-msg", "Add at least one port.", "err"); return;
  }
  if (!Array.isArray(ports)) {
    setMsg("scan-msg", ports.error, "err"); return;
  }
  // parsePorts already caps at MAX_PORTS, but if a range truncated we
  // surface that as a soft warning (not a hard error) so the user
  // understands why their scan is narrower than they typed.
  const overflow = portsOverflowWarning(document.getElementById("scan-ports").value);
  if (overflow) setMsg("scan-msg", overflow + " Scanning first " + ports.length + ".");
  else setMsg("scan-msg", "Expanding target…");
  // Clear current results (but keep the "added" set so re-scanning
  // doesn't lose the badge on already-added services).
  hits.clear();
  selected.clear();
  document.getElementById("scan-rows").replaceChildren();
  document.getElementById("scan-select-all").checked = false;
  document.getElementById("scan-progress-bar").style.width = "0";
  document.getElementById("scan-progress-wrap").hidden = false;

  // Parse the target into the shape the server expects. Accepts a
  // single IP ("10.0.0.5"), a CIDR ("10.0.0.0/24"), or a range
  // ("10.0.0.1-10.0.0.254"). For detected-network dropdowns the
  // value is already a valid CIDR (e.g. "10.0.0.0/24") so the
  // parser handles all three forms uniformly.
  const parsed = parseTarget(target);
  if (!parsed) {
    document.getElementById("scan-progress-wrap").hidden = true;
    setMsg("scan-msg",
      "Target must be a single IP (10.0.0.5), a CIDR (10.0.0.0/24), or a range (10.0.0.1-10.0.0.254).",
      "err");
    return;
  }
  let candidates;
  try {
    const body = { ports };
    if (parsed.kind === "cidr") body.cidr = parsed.cidr;
    else { body.start = parsed.start; body.end = parsed.end; }
    const r = await api.post("/api/scan/expand", body);
    candidates = r.candidates;
  } catch (e) {
    document.getElementById("scan-progress-wrap").hidden = true;
    setMsg("scan-msg", "Expand failed: " + (e.message || "error"), "err");
    return;
  }
  if (!candidates || !candidates.length) {
    document.getElementById("scan-progress-wrap").hidden = true;
    setMsg("scan-msg", "No candidates to probe.", "err");
    return;
  }

  setScanning(true);
  probeAbort = new AbortController();
  document.getElementById("scan-results").hidden = false;
  setMsg("scan-msg", `Probing ${candidates.length} candidate${candidates.length === 1 ? "" : "s"}…`);
  // Re-show the bulk bar in case the previous scan ended empty.
  document.querySelector(".scan-bulkbar").hidden = false;
  document.getElementById("scan-select-all").parentElement.hidden = false;
  // Run the probes in batches. Each batch is up to PROBE_BATCH
  // concurrent fetches with a 1.5s timeout. We render hits as they
  // come in (the per-batch "currently probing" line gives a sense
  // of motion even for slow networks).
  let probed = 0;
  let hitsFound = 0;
  const total = candidates.length;
  for (let i = 0; i < total; i += PROBE_BATCH) {
    if (probeAbort.signal.aborted) break;
    const batch = candidates.slice(i, i + PROBE_BATCH);
    const firstIp = batch[0] && batch[0].ip;
    setText(document.getElementById("scan-progress-label"),
      `Probing ${firstIp}:${batch[0].port}…  (${i + 1}–${Math.min(i + PROBE_BATCH, total)} of ${total})`);
    const results = await Promise.allSettled(batch.map((c) => probe(c, probeAbort.signal)));
    if (probeAbort.signal.aborted) break;
    for (let j = 0; j < batch.length; j++) {
      const c = batch[j];
      if (results[j].status === "fulfilled") {
        hitsFound++;
        const hit = createHit(c);
        hits.set(c.url, hit);
        renderHit(hit);
        // Kick off metadata fetch in the background; the row updates
        // when it returns. Throttled by SCRAPE_BATCH inside.
        scrapeHit(hit);
      }
    }
    probed += batch.length;
    const pct = (probed / total) * 100;
    document.getElementById("scan-progress-bar").style.width = pct + "%";
    document.getElementById("scan-results-count").textContent =
      `${hitsFound} hit${hitsFound === 1 ? "" : "s"} so far (${probed}/${total} probed)`;
  }

  setScanning(false);
  probeAbort = null;
  if (hitsFound === 0) {
    setText(document.getElementById("scan-progress-label"),
      `Done. ${total} probed, no HTTP services found.`);
    setMsg("scan-msg", "No services found. Try a wider port range or a different network.", "err");
    showEmptyState();
  } else {
    setText(document.getElementById("scan-progress-label"),
      `Done. ${probed} probed, ${hitsFound} service${hitsFound === 1 ? "" : "s"} found.`);
    setMsg("scan-msg", `Found ${hitsFound} service${hitsFound === 1 ? "" : "s"}.`, "ok");
  }
  updateAddButton();
}

// Show a friendly empty state below the results header when the scan
// finishes with zero hits. The user gets a hint to widen the port
// range or pick a different target.
function showEmptyState() {
  const root = document.getElementById("scan-rows");
  root.replaceChildren();
  const empty = el("div", { class: "scan-empty" });
  const heading = el("strong", { text: "No services found." });
  const body = el("div", {
    text: "Try adding more ports (the Common preset is a good start) or a wider CIDR. Remember: only HTTP services respond to this scan, so HTTPS-only or non-HTTP services won't show up.",
  });
  empty.append(heading, body);
  root.appendChild(empty);
  // No hits -> nothing to add, so hide the bulk-add bar.
  document.querySelector(".scan-bulkbar").hidden = true;
  document.getElementById("scan-select-all").parentElement.hidden = true;
}

function stopScan() {
  if (probeAbort) probeAbort.abort();
  setScanning(false);
  setMsg("scan-msg", "Scan stopped.", "err");
}

async function probe(candidate, signal) {
  // Per-probe timeout layered on top of the scan-wide abort signal:
  // an unresponsive host shouldn't hold up a whole batch.
  const ctrl = new AbortController();
  const onAbort = () => ctrl.abort();
  signal.addEventListener("abort", onAbort, { once: true });
  const timer = setTimeout(() => ctrl.abort(), PROBE_TIMEOUT_MS);
  try {
    await fetch(candidate.url, { mode: "no-cors", signal: ctrl.signal, cache: "no-store" });
    return true;
  } catch (e) {
    return false; // timeout, refused, DNS fail, abort — all treated as miss
  } finally {
    clearTimeout(timer);
    signal.removeEventListener("abort", onAbort);
  }
}

// ---- hit rendering ----
function createHit(candidate) {
  return {
    candidate,
    row: null,
    status: "ready",  // ready | scraping | failed | added
    title: "",
    description: "",
    error: "",
  };
}

function renderHit(hit) {
  const c = hit.candidate;
  const row = el("div", { class: "scan-row", "data-url": c.url });
  const cbWrap = el("div", { class: "scan-checkbox" });
  const cb = el("input", { type: "checkbox", "aria-label": "Select " + c.url });
  cb.addEventListener("change", () => {
    if (cb.checked) selected.add(c.url); else selected.delete(c.url);
    row.classList.toggle("selected", cb.checked);
    updateAddButton();
  });
  if (added.has(c.url)) cb.disabled = true;
  cbWrap.appendChild(cb);
  const body = el("div", { class: "scan-body" });
  const urlDiv = el("div", { class: "scan-url" });
  setText(urlDiv, c.url);
  const meta = el("div", { class: "scan-meta" });
  const titleSpan = el("div", { class: "scan-title" });
  setText(titleSpan, hit.title || c.url);
  const descSpan = el("div");
  setText(descSpan, hit.description || "");
  meta.append(titleSpan, descSpan);
  body.append(urlDiv, meta);
  const status = el("div", { class: "scan-status" });
  setText(status, hit.status === "ready" ? "found" : hit.status);
  row.append(cbWrap, body, status);
  document.getElementById("scan-rows").appendChild(row);
  hit.row = row;
  hit._cb = cb;
  hit._status = status;
  hit._title = titleSpan;
  hit._desc = descSpan;
}

function updateRow(hit) {
  if (!hit.row) return;
  // Status pill.
  hit._status.className = "scan-status " + (hit.status === "ready" ? "ok"
    : hit.status === "added" ? "added" : hit.status);
  setText(hit._status, hit.status === "ready" ? "found"
    : hit.status === "scraping" ? "scraping…"
    : hit.status === "failed" ? "scrape failed"
    : hit.status === "added" ? "added"
    : hit.status);
  // Title (default to URL until scrape returns).
  if (hit.title) setText(hit._title, hit.title);
  if (hit.description) setText(hit._desc, hit.description);
  if (hit.status === "added") {
    hit.row.classList.add("added");
    if (hit._cb) hit._cb.disabled = true;
  }
}

function onSelectAll(e) {
  const checked = e.target.checked;
  selected.clear();
  for (const [url, hit] of hits) {
    if (hit._cb.disabled) continue; // already added
    hit._cb.checked = checked;
    if (checked) selected.add(url); else selected.delete(url);
    if (hit.row) hit.row.classList.toggle("selected", checked);
  }
  updateAddButton();
}

function updateAddButton() {
  const btn = document.getElementById("scan-add-selected");
  const n = selected.size;
  btn.disabled = n === 0;
  setText(btn, `Add ${n} selected to apps`);
}

// ---- scrape loop (throttled) ----
let _scrapeQueue = [];
let _scrapeActive = 0;

function scrapeHit(hit) {
  hit.status = "scraping";
  updateRow(hit);
  _scrapeQueue.push(hit);
  pumpScrape();
}

function pumpScrape() {
  while (_scrapeActive < SCRAPE_BATCH && _scrapeQueue.length) {
    const hit = _scrapeQueue.shift();
    _scrapeActive++;
    api.post("/api/scrape", { url: hit.candidate.url })
      .then((s) => {
        hit.title = s.title || hit.title;
        hit.description = s.description || hit.description;
        // If the scraper resolved a redirect, use the canonical URL.
        if (s.url && s.url !== hit.candidate.url) {
          // Keep the original IP:port as the "what we found" anchor;
          // show the canonical in the title row so the user can decide
          // whether to add the public version instead.
          hit.title = (hit.title || hit.candidate.url) + "  →  " + s.url;
        }
        hit.status = "ready";
        updateRow(hit);
      })
      .catch(() => {
        hit.status = "failed";
        updateRow(hit);
      })
      .finally(() => {
        _scrapeActive--;
        pumpScrape();
      });
  }
}

// ---- add selected ----
async function addSelected() {
  if (!selected.size) return;
  const btn = document.getElementById("scan-add-selected");
  btn.disabled = true;
  setMsg("scan-add-msg", "Adding…");
  const queue = Array.from(selected);
  let done = 0;
  let ok = 0;
  let fail = 0;
  await runWithConcurrency(queue, ADD_BATCH, async (url) => {
    const hit = hits.get(url);
    if (!hit) return;
    const payload = {
      title: (hit.title || url).replace(/\s+→\s+https?:\/\/\S+$/, "").trim() || url,
      urls: [url],
      icon: "",
      group: "",
      description: hit.description || "",
    };
    try {
      await api.post("/api/apps", payload);
      ok++;
      added.add(url);
      hit.status = "added";
      updateRow(hit);
    } catch (e) {
      fail++;
      hit.status = "failed";
      hit.error = e.message || "add failed";
      updateRow(hit);
      // Keep it selected so the user can retry; show the error in the row.
      if (hit._desc) setText(hit._desc, (hit.description || "") + "  —  " + hit.error);
    } finally {
      done++;
      setMsg("scan-add-msg", `Added ${ok}/${done}…`);
    }
  });
  setMsg("scan-add-msg",
    fail === 0
      ? `Added ${ok} app${ok === 1 ? "" : "s"}. View on /app.`
      : `Added ${ok}, ${fail} failed.`,
    fail === 0 ? "ok" : "err");
  selected.clear();
  updateAddButton();
}

async function runWithConcurrency(items, limit, fn) {
  let i = 0;
  const workers = Array.from({ length: Math.min(limit, items.length) }, async () => {
    while (i < items.length) {
      const idx = i++;
      await fn(items[idx]);
    }
  });
  await Promise.all(workers);
}

// Don't auto-init. The settings page gates on auth (setup mode /
// guest -> /login) and only reveals #content to authed admins. We
// hook in via the ``scan:init`` custom event that settings.js
// dispatches after the auth check passes. This way we don't run any
// network work for guests or during setup.
document.addEventListener("scan:init", () => {
  init().catch((err) => console.error(err));
});
})();
