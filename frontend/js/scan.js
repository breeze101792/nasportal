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
// Per-probe timeout for the first scheme (http). The fallback (https)
// gets its own SCHEME_TIMEOUT_MS. Worst case per (ip, port) is then
// 2 × SCHEME_TIMEOUT_MS — a dead host on both schemes takes longer
// than an alive host, but the per-batch wait is bounded by the slowest
// member.
const SCHEME_TIMEOUT_MS = 800;
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
  populateTargetDropdown(stored.target, stored.targetDropdown);
  populatePorts(stored.ports);
  populateScheme(stored.scheme);
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
  return { target: "", ports: PRESET_COMMON, scheme: "both" };
}

function persistStored() {
  const data = {
    target: document.getElementById("scan-cidr").value,
    targetDropdown: document.getElementById("scan-target").value,
    ports: document.getElementById("scan-ports").value,
    scheme: document.getElementById("scan-scheme").value,
  };
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(data)); } catch (e) { /* quota */ }
}

// ---- target dropdown / custom input ----
//
// The dropdown lists the host's detected local networks plus a
// "Custom…" option that reveals the text input below. We use
// ``__custom__`` as the sentinel value for the custom option; the
// option's visible text is "Custom…" so the sentinel never shows
// in the UI. ``getTarget`` resolves the actual value to send to
// the server based on which option is selected.
const TARGET_CUSTOM = "__custom__";

function populateTargetDropdown(savedCustom, savedDropdown) {
  const sel = document.getElementById("scan-target");
  sel.replaceChildren();
  if (networks.length) {
    for (const n of networks) sel.appendChild(el("option", { value: n, text: n }));
  } else {
    // No detected networks (e.g. the host has no LAN interfaces).
    // The dropdown is still rendered so the layout doesn't shift, but
    // it has no selectable options.
    sel.appendChild(el("option", { value: "", text: "(no networks detected)" }));
  }
  // The "Custom…" option is always available — the user might want
  // to scan a network that wasn't auto-detected, or a single host.
  sel.appendChild(el("option", { value: TARGET_CUSTOM, text: "Custom…" }));
  // Restore the selection. Saved value may be a network CIDR, the
  // custom sentinel, or empty (no networks detected).
  let dropdown = savedDropdown;
  if (dropdown && dropdown !== TARGET_CUSTOM && !networks.includes(dropdown)) {
    dropdown = networks[0] || TARGET_CUSTOM;
  }
  if (!dropdown) dropdown = networks[0] || TARGET_CUSTOM;
  sel.value = dropdown;
  // Restore the custom input + toggle its visibility.
  document.getElementById("scan-cidr").value = savedCustom || "";
  updateCidrVisibility();
}

function updateCidrVisibility() {
  const wrap = document.getElementById("scan-custom-wrap");
  const isCustom = document.getElementById("scan-target").value === TARGET_CUSTOM;
  wrap.hidden = !isCustom;
}

function getTarget() {
  const sel = document.getElementById("scan-target");
  if (sel.value === TARGET_CUSTOM) {
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

// ---- scheme ----
const VALID_SCHEMES = ["both", "http", "https"];
function populateScheme(saved) {
  const sel = document.getElementById("scan-scheme");
  sel.value = VALID_SCHEMES.includes(saved) ? saved : "both";
}

// Parse a free-form ports string into a sorted, deduped list of
// integer ports. Each token is either a single port ("80") or an
// inclusive range ("5000-6000"). Tokens may be mixed and separated
// by commas or whitespace.
//
// Returns either a list of ports, or ``{error: "..."}`` on a bad
// input. If the input would expand to more than ``MAX_PORTS``, we
// hard-error rather than silently truncating — a silent cap is
// confusing ("I asked for 5000 ports, why did I get 1024?"). The
// server enforces the same cap and would just return
// ``too_many_ports`` anyway, so failing fast gives a clearer
// message with the cap visible in the error.
const MAX_PORTS = 1024;

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
    // ("1-65535") doesn't allocate a quarter million entries. The
    // out-of-range check below then surfaces a clear error.
    for (let p = a; p <= b && out.length < MAX_PORTS + 1; p++) {
      if (!seen.has(p)) { seen.add(p); out.push(p); }
    }
  }
  if (rangeError) return { error: rangeError };
  if (out.length > MAX_PORTS) {
    return { error: `Too many ports (${out.length}). Maximum is ${MAX_PORTS}.` };
  }
  out.sort((a, b) => a - b);
  return out;
}

// ---- event wiring ----
function wireEvents() {
  const sel = document.getElementById("scan-target");
  sel.addEventListener("change", () => { updateCidrVisibility(); persistStored(); });
  document.getElementById("scan-cidr").addEventListener("input", persistStored);
  document.getElementById("scan-ports").addEventListener("input", persistStored);
  document.getElementById("scan-scheme").addEventListener("change", persistStored);
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
  document.getElementById("scan-scheme").disabled = running;
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
  setMsg("scan-msg", "Expanding target…");
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
  // Read the protocol choice once per scan — a scan is a snapshot of
  // the user's intent, and changing the dropdown mid-scan would only
  // affect future batches, which would be surprising.
  const schemeChoice = VALID_SCHEMES.includes(
    document.getElementById("scan-scheme").value
  ) ? document.getElementById("scan-scheme").value : "both";
  setMsg("scan-msg", `Probing ${candidates.length} candidate${candidates.length === 1 ? "" : "s"} (${schemeChoice})…`);
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
    const results = await Promise.allSettled(batch.map((c) => probe(c, probeAbort.signal, schemeChoice)));
    if (probeAbort.signal.aborted) break;
    for (let j = 0; j < batch.length; j++) {
      const c = batch[j];
      const r = results[j];
      // probe() always resolves (never rejects), so we just check
      // the .hit field on the result value.
      if (r.status === "fulfilled" && r.value && r.value.hit) {
        hitsFound++;
        // Use the URL that actually worked (http or https) for the
        // hit's display, the bulk-add payload, and the add POST.
        const hit = createHit({ ...c, url: r.value.url });
        hits.set(r.value.url, hit);
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
    text: "Try adding more ports (the Common preset is a good start) or a wider CIDR. The scan tries http:// first and falls back to https://, but services behind self-signed certs won't respond to the https probe.",
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

// Probe a single (ip, port) candidate. Tries ``http://`` first; on
// failure (timeout, refused, cert error, mixed-content block) falls
// back to ``https://``. The candidate.url from the server is
// ``http://``-shaped, so we derive the host:port and try both
// schemes ourselves.
//
// Returns:
//   { hit: true,  scheme: "http"|"https", url: <working url> }
//   { hit: false, scheme: null,         url: null            }
//
// Aborts are honored via the scan-wide signal; the per-scheme timer
// stops the local fetch if it stalls. Self-signed HTTPS certs will
// still reject — the browser has no opt-out for that, and we don't
// want to fake it.
async function probe(candidate, signal, schemeChoice) {
  const hostPort = candidate.url.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  // "both" tries http first, then https on failure (so an http
  // service isn't masked by a slow/failed https probe). "http" /
  // "https" restrict to that single scheme. The candidate.url from
  // the server is ``http://``-shaped, so we derive host:port and
  // build the URL ourselves for each scheme.
  const schemes = schemeChoice === "http" ? ["http"]
    : schemeChoice === "https" ? ["https"]
    : ["http", "https"];
  for (const scheme of schemes) {
    if (signal.aborted) return { hit: false, scheme: null, url: null };
    const url = scheme + "://" + hostPort + "/";
    const ctrl = new AbortController();
    const onAbort = () => ctrl.abort();
    signal.addEventListener("abort", onAbort, { once: true });
    const timer = setTimeout(() => ctrl.abort(), SCHEME_TIMEOUT_MS);
    try {
      await fetch(url, { mode: "no-cors", signal: ctrl.signal, cache: "no-store" });
      return { hit: true, scheme, url };
    } catch (e) {
      // "both" falls through to https; single-scheme mode ends here
      // with a miss. Any failure (timeout, refused, DNS, cert error,
      // abort) counts as a miss for that scheme.
    } finally {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
    }
  }
  return { hit: false, scheme: null, url: null };
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
  // The URL is a clickable link so the user can preview the service
  // in a new tab before adding it. safeUrl() rejects non-http(s)
  // (we still only ever probe http/https) but defense in depth — if
  // the working URL is somehow weird, the link becomes a no-op "#"
  // rather than a script sink.
  const urlDiv = el("div", { class: "scan-url" });
  const urlLink = el("a", {
    href: safeUrl(c.url),
    target: "_blank",
    rel: "noopener noreferrer",
    title: "Open " + c.url + " in a new tab",
  });
  setText(urlLink, c.url);
  urlDiv.appendChild(urlLink);
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
