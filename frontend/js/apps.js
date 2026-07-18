// /app management page: CRUD, drag-and-drop adding, scrape, ping, sort, group.
let apps = [];
let pingResults = {};
let auth = { authed: false, setup_required: false };
let settings = {};
let sortMode = "order";
// Default the Grouped toggle to ON. The toggle's whole purpose is to
// show app groups as titled sections; an admin who takes the time to
// set a group field on their apps almost certainly wants to see them
// grouped. Apps without a group fall into an "Ungrouped" section so
// the list shape is consistent. The admin can still toggle off for
// a flat list.
let groupedView = true;
let editingId = null;
const selected = new Set(); // app ids chosen for bulk actions
let selAnchorId = null; // app id of the last directly-clicked checkbox (for shift-range)

// Debounce timer for the URL preview.
let _previewTimer = null;

async function init() {
  const [settingsData, appsData, authResult] = await Promise.all([
    api.get("/api/settings"),
    api.get("/api/apps"),
    authState(),
  ]);
  settings = settingsData;
  apps = appsData.apps || [];
  auth = authResult;

  setText(document.getElementById("brand"), settings.portal_title || "NAS Portal");
  setText(document.getElementById("brandSub"), "App settings");
  if (settings.wallpaper) document.body.style.backgroundImage = `url("${cssEsc(settings.wallpaper)}")`;
  applyTheme(settings.theme);
  applyBackgroundColor(settings.background_color);
  applyPortalWidth(settings.portal_width);

  renderTopLinks("app", auth.authed);
  renderBanner();

  // Auth-gated controls
  const canEdit = auth.authed;
  document.getElementById("addBtn").hidden = !canEdit;
  document.getElementById("pingBtn").hidden = !canEdit; // ping is an edit-tier action
  document.getElementById("selectBar").hidden = !canEdit;
  document.getElementById("addBtn").addEventListener("click", () => openForm(null));
  document.getElementById("pingBtn").addEventListener("click", pingAll);
  if (canEdit) {
    document.getElementById("selAll").addEventListener("change", onSelAll);
    document.getElementById("selGroupBtn").addEventListener("click", bulkGroup);
    document.getElementById("selDelBtn").addEventListener("click", bulkDelete);
  }
  document.getElementById("sort").addEventListener("change", (e) => { sortMode = e.target.value; renderList(); });
  document.getElementById("groupedBtn").addEventListener("click", () => { groupedView = !groupedView; updateGroupedBtn(); renderList(); });
  // Reflect the current groupedView (default true) in the button's
  // .active class so the toggle is visually correct on first paint.
  updateGroupedBtn();
  document.getElementById("cancelBtn").addEventListener("click", closeForm);
  document.getElementById("scrapeBtn").addEventListener("click", scrapeFromUrlField);
  document.getElementById("appForm").addEventListener("submit", submitForm);
  document.getElementById("addUrlBtn").addEventListener("click", () => addUrlRow("", { focus: true }));
  wireDropzone();
  wireUrlList();
  wireUrlListDrop();
  wireAddUrlBtnDrop();

  renderList();
  if (canEdit && apps.length) pingAll(); // ping on load (login-gated endpoint)
}

function renderBanner() {
  const banner = document.getElementById("banner");
  banner.replaceChildren();
  if (auth.setup_required) {
    banner.appendChild(el("div", { class: "banner",
      text: "First run: no password set. Open Settings to create the admin password before you can add apps." }));
  } else if (!auth.authed) {
    banner.appendChild(el("div", { class: "banner",
      text: "Viewing as a guest. Log in to add or edit apps." }));
  }
}

// ---- list rendering ----
function renderList() {
  const root = document.getElementById("list");
  root.replaceChildren();
  // Drop selections for apps that no longer exist (deleted out-of-band).
  for (const id of [...selected]) {
    if (!apps.some((a) => a.id === id)) selected.delete(id);
  }
  if (!apps.length) {
    root.appendChild(el("div", { class: "empty", text: "No apps yet." }));
    updateSelState();
    return;
  }
  const sorted = sortApps([...apps]);

  // Group-titled sections when the "Grouped" toggle is on, regardless of
  // sort mode. When off, a single flat list — drag-to-reorder in Manual
  // mode works cleanly across group boundaries.
  if (groupedView) {
    const groups = new Map();
    for (const a of sorted) {
      const g = a.group || "Ungrouped";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(a);
    }
    for (const [g, items] of groups) {
      root.appendChild(groupTitleEl(g));
      for (const a of items) root.appendChild(row(a));
    }
  } else {
    for (const a of sorted) root.appendChild(row(a));
  }
  updateSelState();
}

function updateGroupedBtn() {
  const btn = document.getElementById("groupedBtn");
  btn.classList.toggle("active", groupedView);
}

function sortApps(arr) {
  switch (sortMode) {
    case "name": return arr.sort((a, b) => (a.title || "").localeCompare(b.title || ""));
    case "status": return arr.sort((a, b) => statusRank(a) - statusRank(b) || (a.title || "").localeCompare(b.title || ""));
    case "group": return arr.sort((a, b) => (a.group || "").localeCompare(b.group || "") || (a.order ?? 0) - (b.order ?? 0));
    case "order":
    default: return arr.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  }
}

function statusRank(a) {
  const r = pingResults[a.id];
  if (!r) return 2; // unknown / not yet pinged
  return r.online ? 0 : 1;
}

function row(a) {
  const r = pingResults[a.id];
  const dotClass = r ? (r.online ? "ok" : "down") : "";
  const statusText = r ? (r.online ? `up · ${r.latency_ms}ms` : "down") : "—";

  // Icon: shared resolver (api.js → resolveIcon). Same logic as the
  // portal home — stored ``a.icon`` wins when set, otherwise the
  // favicon URL is fetched live via /api/favicon and cached in the
  // shared browser-side faviconCache (localStorage). The placeholder
  // is a title-initial letter until the icon is known, then we swap
  // in <img>. CSS (.app-row .icon) sets the rendered size to 32x32.
  //
  // CRITICAL: resolveIcon → attachIcon does ``placeholder.replaceWith(img)``
  // and bails early if ``placeholder.parentNode`` is null. We must
  // mount the placeholder into ``left`` FIRST so the swap has somewhere
  // to land. (The portal does the same — it appends placeholder to
  // ``.card`` before calling resolveIcon.)
  const left = el("div", {});
  const placeholder = el("div", { class: "icon-fallback", text: (a.title || "?").trim().charAt(0).toUpperCase() || "?" });
  left.appendChild(placeholder);
  resolveIcon(a, placeholder);
  const mid = el("div", {},
    el("div", { style: "font-weight:500", text: a.title }),
    el("div", { class: "meta" },
      el("span", { class: "dot " + dotClass }),
      el("span", { text: statusText }),
      a.group ? el("span", { text: "· " + a.group }) : null,
    ),
  );

  const right = el("div", { style: "display:flex;gap:6px" });
  // Open button follows settings.open_apps_in_new_tab: on → new tab (the
  // portal stays open in the background), off → same-tab navigation.
  // rel="noopener noreferrer" prevents the target page from reaching back
  // to our window via window.opener.
  const openTarget = settings.open_apps_in_new_tab ? "_blank" : "_self";
  right.appendChild(el("a", { class: "btn", href: safeUrl(a.url), target: openTarget, rel: "noopener noreferrer", text: "Open" }));
  if (auth.authed) {
    right.appendChild(el("button", { class: "btn", text: "Edit", onclick: () => openForm(a) }));
    right.appendChild(el("button", { class: "btn danger", text: "Delete", onclick: () => del(a) }));
  }

  const cls = "app-row" + (auth.authed ? " selectable" : "") + (selected.has(a.id) ? " selected" : "");
  // Drag-to-reorder is only enabled in Manual (order) mode for authed users.
  // In other sorts the order is computed from name/status/group, so a manual
  // reorder would be silently overwritten on the next sort.
  const draggable = auth.authed && sortMode === "order";
  if (auth.authed) {
    const cb = el("input", { type: "checkbox", class: "sel", "aria-label": "Select " + (a.title || "app") });
    cb.dataset.id = a.id;
    cb.checked = selected.has(a.id);
    // click (not change) so we can read shiftKey and control the toggle for range select
    cb.addEventListener("click", (e) => onRowCheck(e, cb, a.id));
    const rowEl = el("div", { class: cls + (draggable ? " draggable" : ""), "data-id": a.id, "data-group": a.group || "" });
    if (draggable) {
      // Only the handle is draggable — the rest of the row (checkbox, links,
      // buttons) keeps its normal click behavior. dragstart is wired to the
      // handle, dragover/drop are wired to the row so any drop on the row
      // (not just on the handle) registers.
      rowEl.appendChild(dragHandle(a.id));
      wireRowDrag(rowEl);
    }
    rowEl.appendChild(cb);
    rowEl.appendChild(left);
    rowEl.appendChild(mid);
    rowEl.appendChild(right);
    return rowEl;
  }
  return el("div", { class: cls }, left, mid, right);
}

// 6-dot grip rendered in place of a drag handle. The handle is the only
// draggable element on the row, so a drag only starts from a pointer-down
// on the handle itself.
function dragHandle(id) {
  const h = el("span", { class: "drag-handle", "aria-label": "Drag to reorder", title: "Drag to reorder", draggable: "true" });
  for (let i = 0; i < 6; i++) h.appendChild(el("span", { class: "dot" }));
  if (id != null) h.dataset.id = id;
  return h;
}

// Group-title element. When Grouped + Manual + authed, the title gets a
// drag handle so the entire group block can be reordered.
function groupTitleEl(g) {
  const canDrag = auth.authed && sortMode === "order";
  const title = el("div", { class: "group-title" + (canDrag ? " draggable" : ""), "data-group": g,
    draggable: canDrag ? "true" : "false" });
  if (canDrag) {
    title.appendChild(dragHandle(null));
    wireGroupDrag(title);
  }
  title.appendChild(document.createTextNode(g));
  return title;
}

// ---- multi-select toolbar ----
function onRowCheck(e, cb, id) {
  // Plain click: let the native toggle happen, then mirror it into `selected`.
  // Shift-click: select the whole range from the anchor to this row in the
  // current on-screen order (preventDefault stops this row's own toggle so we
  // can set the entire range consistently).
  const boxes = [...document.querySelectorAll("#list .sel")];
  const idx = boxes.indexOf(cb);
  const anchorIdx = selAnchorId ? boxes.findIndex((b) => b.dataset.id === selAnchorId) : -1;
  if (e.shiftKey && anchorIdx >= 0 && anchorIdx !== idx) {
    e.preventDefault();
    const lo = Math.min(anchorIdx, idx), hi = Math.max(anchorIdx, idx);
    for (let i = lo; i <= hi; i++) {
      const b = boxes[i];
      selected.add(b.dataset.id);
      b.checked = true;
      b.closest(".app-row").classList.add("selected");
    }
    // keep the existing anchor so repeated shift-clicks extend from the start
  } else {
    if (cb.checked) selected.add(id); // native toggle already applied
    else selected.delete(id);
    cb.closest(".app-row").classList.toggle("selected", cb.checked);
    selAnchorId = id;
  }
  updateSelState();
}

// ---- multi-select toolbar ----
function updateSelState() {
  const count = selected.size;
  setText(document.getElementById("selCount"), count === 1 ? "1 selected" : count + " selected");
  document.getElementById("selGroupBtn").disabled = count === 0;
  document.getElementById("selDelBtn").disabled = count === 0;
  const rendered = apps.map((a) => a.id);
  const selAll = document.getElementById("selAll");
  if (!rendered.length) {
    selAll.checked = false;
    selAll.indeterminate = false;
    return;
  }
  const allOn = rendered.every((id) => selected.has(id));
  const someOn = rendered.some((id) => selected.has(id));
  selAll.checked = allOn;
  selAll.indeterminate = someOn && !allOn;
}

function onSelAll(e) {
  const ids = apps.map((a) => a.id);
  if (e.target.checked) ids.forEach((id) => selected.add(id));
  else ids.forEach((id) => selected.delete(id));
  document.querySelectorAll("#list .sel").forEach((cb) => {
    cb.checked = e.target.checked;
    cb.closest(".app-row").classList.toggle("selected", e.target.checked);
  });
  updateSelState();
}

async function bulkGroup() {
  const ids = [...selected];
  if (!ids.length) return;
  // Pre-fill with the shared group if all selected apps have the same one.
  const groups = new Set(apps.filter((a) => selected.has(a.id)).map((a) => a.group || ""));
  const pre = groups.size === 1 ? [...groups][0] : "";
  const group = prompt(`Set group for ${ids.length} app(s). Leave blank to clear:`, pre);
  if (group === null) return; // cancelled
  const g = group.trim();
  if (g.length > 100) { alert("Group name is too long (max 100)."); return; }
  try {
    await api.post("/api/apps/bulk/group", { ids, group: g });
    apps = (await api.get("/api/apps")).apps || [];
    selected.clear();
    selAnchorId = null;
    renderList();
    pingAll();
  } catch (err) {
    alert("Update failed: " + (err.message || "error"));
  }
}

async function bulkDelete() {
  const ids = [...selected];
  if (!ids.length) return;
  if (!confirm(`Delete ${ids.length} app(s)? This can't be undone.`)) return;
  try {
    await api.post("/api/apps/bulk/delete", { ids });
    ids.forEach((id) => delete pingResults[id]);
    apps = apps.filter((a) => !selected.has(a.id));
    selected.clear();
    selAnchorId = null;
    renderList();
  } catch (err) {
    alert("Delete failed: " + (err.message || "error"));
  }
}

// ---- ping ----
async function pingAll() {
  if (!apps.length) return;
  document.getElementById("pingBtn").disabled = true;
  try {
    const data = await api.post("/api/apps/ping", { ids: apps.map((a) => a.id) });
    pingResults = data.results || {};
    renderList();
  } catch (e) {
    console.error(e);
  } finally {
    document.getElementById("pingBtn").disabled = false;
  }
}

// ---- form (add/edit) ----
function openForm(app) {
  editingId = app ? app.id : null;
  const panel = document.getElementById("formPanel");
  panel.hidden = false;
  setText(document.getElementById("formTitle"), app ? "Edit app" : "Add app");
  const set = (id, v) => { document.getElementById(id).value = v || ""; };
  set("f-title", app && app.title);
  set("f-icon", app && app.icon);
  set("f-group", app && app.group);
  set("f-desc", app && app.description);
  set("f-id", app && app.id);
  // URL list: from `app.urls` if present (canonical), otherwise synthesize
  // from the legacy structured fields so an old-shape app pre-fills the
  // form with its URLs (the admin can then re-save to migrate).
  const root = document.getElementById("f-url");
  root.replaceChildren();
  const initial = urlsForForm(app);
  if (initial.length === 0) addUrlRow("", { focus: false });
  else initial.forEach((u) => addUrlRow(u, { focus: false }));
  setText(document.getElementById("f-url-preview"), "");
  document.getElementById("f-url-preview").className = "";
  setText(document.getElementById("formMsg"), "");
  document.getElementById("formMsg").className = "msg";
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  // Focus the first URL row, or the +Add button if there's none.
  const firstInput = root.querySelector("input");
  if (firstInput) firstInput.focus();
  if (initial.length) schedulePreview();
}

// Build the URL list to pre-fill the form. Canonical ``urls`` wins; if
// the app has the legacy structured shape, synthesize a URL list from
// it so the admin can edit/clean before re-saving.
function urlsForForm(app) {
  if (!app) return [];
  if (Array.isArray(app.urls) && app.urls.length) {
    return app.urls.map((u) => String(u));
  }
  const out = [];
  const scheme = app.scheme || "http";
  const port = app.port;
  const path = app.path || "";
  function _one(host) {
    const hp = port ? `${host}:${port}` : host;
    return `${scheme}://${hp}${path}`;
  }
  for (const ip of app.network_ips || []) if (ip) out.push(_one(ip));
  if (app.domain) out.push(_one(app.domain));
  if (app.public_ip) out.push(_one(app.public_ip));
  if (!out.length && app.url) out.push(app.url);
  // Dedupe, preserve order.
  const seen = new Set();
  return out.filter((u) => { if (seen.has(u)) return false; seen.add(u); return true; });
}

function closeForm() {
  document.getElementById("formPanel").hidden = true;
  editingId = null;
}

// Render a single URL row inside #f-url. Each row is a flex container:
// drag handle + <input> + remove button. Empty rows are ignored at save
// time, so the user can leave a row blank without breaking anything.
function addUrlRow(value, opts) {
  const root = document.getElementById("f-url");
  // Only the handle is draggable. The row itself is NOT — if it were,
  // the browser would start a native drag on mousedown anywhere in the
  // row (including over the URL <input>), which prevents the user from
  // selecting text inside the input with the cursor. Same pattern as
  // the main .app-row: handle is the only drag target, the row is just
  // a drop receiver. The input is also explicitly draggable="false"
  // as a belt-and-suspenders defense in case the browser looks at
  // descendants before the parent.
  const row = el("div", { class: "url-line", draggable: "false" });
  const handle = el("span", { class: "url-handle", "aria-label": "Drag to reorder", title: "Drag to reorder", draggable: "true" });
  for (let i = 0; i < 6; i++) handle.appendChild(el("span", { class: "dot" }));
  const inp = el("input", { type: "text", draggable: "false", placeholder: "e.g. https://10.31.1.9:8989/sonarr", value: value || "" });
  inp.addEventListener("input", schedulePreview);
  const rm = el("button", { class: "btn danger", type: "button", text: "Remove",
    onclick: () => { row.remove(); if (!root.children.length) addUrlRow("", { focus: false }); schedulePreview(); } });
  row.append(handle, inp, rm);
  wireUrlRowDrag(row);
  root.appendChild(row);
  if (opts && opts.focus) inp.focus();
}

function getUrls() {
  const root = document.getElementById("f-url");
  return [...root.querySelectorAll("input")].map((i) => i.value.trim()).filter(Boolean);
}

// Drag-to-reorder within the URL list. Same pattern as the row
// drag-to-reorder on the main list (the handle is the only draggable
// element so a drag only starts from a pointer-down on the handle).
let _urlDragSrc = null;

function wireUrlList() {
  // No-op kept for symmetry with the other wire* helpers; the per-row
  // drag listeners are attached in addUrlRow.
}

// Drop target for the URL list itself. Dragging a URL from a browser tab
// (or another app) onto the list appends it as a new row — way faster
// than copy-paste. Internal row-reorder drags already have their own
// handlers on each row, so the container only acts on *external* URL
// drops. We detect that by checking dataTransfer.types: a real URL drag
// from another app/window carries "text/uri-list" (and a "text/plain"
// that is a URL), while our internal reorder only carries
// "text/plain" = "url-row".
//
// Important: drops on a CHILD ROW fire the row's drop handler first
// (because the row is also a drop target for reorders). The row's
// handler bails out for external drags, but the event has already been
// consumed — it does NOT bubble to the container. So the row's drop
// handler also routes external URL drops into the append flow. See
// `handleExternalUrlDrop` below — both call sites use it.
function wireUrlListDrop() {
  const root = document.getElementById("f-url");

  function isExternalUrlDrag(e) {
    if (_urlDragSrc) return false;
    const types = e.dataTransfer && e.dataTransfer.types;
    if (!types) return false;
    return Array.from(types).indexOf("text/uri-list") !== -1;
  }

  root.addEventListener("dragenter", (e) => {
    if (!isExternalUrlDrag(e)) return;
    e.preventDefault();
    root.classList.add("drag-target");
  });
  root.addEventListener("dragover", (e) => {
    if (!isExternalUrlDrag(e)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    root.classList.add("drag-target");
  });
  root.addEventListener("dragleave", (e) => {
    // dragleave fires for every child too; only clear when the pointer
    // actually leaves the container.
    if (e.currentTarget === e.target) root.classList.remove("drag-target");
  });
  root.addEventListener("drop", (e) => {
    if (!isExternalUrlDrag(e)) return;
    e.preventDefault();
    root.classList.remove("drag-target");
    handleExternalUrlDrop(e);
  });
}

// Shared drop logic for an external URL drag. Called by both the
// container's drop handler and a row's drop handler (because drops on
// a child row are consumed by the row and don't bubble). Appends the
// URL as a new row, and — if the list was empty before — also tries
// to auto-fill title/description from the URL (same as the top
// dropzone).
async function handleExternalUrlDrop(e) {
  const url = (e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain") || "").trim();
  if (!url) return;
  const wasEmpty = getUrls().length === 0;
  addUrlRow(url, { focus: true });
  schedulePreview();
  if (!wasEmpty) return;
  const msg = document.getElementById("formMsg");
  msg.className = "msg"; setText(msg, "Fetching metadata…");
  try {
    const s = await api.post("/api/scrape", { url });
    if (!document.getElementById("f-title").value) setField("f-title", s.title);
    if (!document.getElementById("f-desc").value) setField("f-desc", s.description);
    // Replace the row's URL with the canonical (redirect-resolved) one.
    const root = document.getElementById("f-url");
    const first = root.querySelector(".url-line input");
    if (first) first.value = s.url || url;
    schedulePreview();
    msg.className = "msg ok"; setText(msg, "Auto-filled from " + (s.title || url));
  } catch (err) {
    msg.className = "msg err"; setText(msg, "Couldn't auto-fill; enter details manually.");
  }
}

// Drop target on the + Add URL button too — small dedicated hotspot
// that's visually obvious as a drop target. Highlights the URL list
// outline on dragover so the user sees the whole list is fair game.
function wireAddUrlBtnDrop() {
  const btn = document.getElementById("addUrlBtn");
  const root = document.getElementById("f-url");

  function isExternalUrlDrag(e) {
    if (_urlDragSrc) return false;
    const types = e.dataTransfer && e.dataTransfer.types;
    if (!types) return false;
    return Array.from(types).indexOf("text/uri-list") !== -1;
  }

  ["dragenter", "dragover"].forEach((ev) =>
    btn.addEventListener(ev, (e) => {
      if (!isExternalUrlDrag(e)) return;
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      btn.classList.add("drag-target");
      root.classList.add("drag-target");
    }));
  ["dragleave", "drop"].forEach((ev) =>
    btn.addEventListener(ev, (e) => {
      // Only clear on the button itself, not children (it has none, but
      // be consistent with the container).
      if (e.currentTarget === e.target) {
        btn.classList.remove("drag-target");
        root.classList.remove("drag-target");
      }
    }));
  btn.addEventListener("drop", (e) => {
    if (!isExternalUrlDrag(e)) return;
    e.preventDefault();
    btn.classList.remove("drag-target");
    root.classList.remove("drag-target");
    handleExternalUrlDrop(e);
  });
}

function wireUrlRowDrag(row) {
  row.addEventListener("dragstart", onUrlRowDragStart);
  row.addEventListener("dragover", onUrlRowDragOver);
  row.addEventListener("drop", onUrlRowDrop);
  row.addEventListener("dragend", onUrlRowDragEnd);
  row.addEventListener("dragleave", onUrlRowDragLeave);
}

function onUrlRowDragStart(e) {
  if (!e.target.classList.contains("url-handle")) {
    e.preventDefault();
    return;
  }
  _urlDragSrc = e.currentTarget;
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", "url-row");
  e.currentTarget.classList.add("dragging");
}

function onUrlRowDragOver(e) {
  // External URL drag: don't visually mark the row as a reorder
  // target (it's actually an append target). Still need preventDefault
  // so the browser will fire drop.
  if (!_urlDragSrc) {
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    return;
  }
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  if (e.currentTarget === _urlDragSrc) return;
  e.currentTarget.classList.add("drop-target");
}

function onUrlRowDragLeave(e) {
  if (e.currentTarget === e.target) e.currentTarget.classList.remove("drop-target");
}

function onUrlRowDrop(e) {
  e.preventDefault();
  const target = e.currentTarget;
  target.classList.remove("drop-target");
  // External URL drop on a row: the drop fired on the row (not the
  // container), so the container's handler won't see it. Forward.
  if (!_urlDragSrc) {
    handleExternalUrlDrop(e);
    return;
  }
  if (target === _urlDragSrc) return;
  const root = document.getElementById("f-url");
  // Insert the dragged row before the target. After removal, the
  // target's index either shifts down by 1 (src was before) or stays
  // the same (src was after). Mirror the same compensation as the
  // main row drag.
  const src = Array.from(root.children).indexOf(_urlDragSrc);
  const dst = Array.from(root.children).indexOf(target);
  if (src < 0 || dst < 0) return;
  root.insertBefore(_urlDragSrc, src < dst ? target.nextSibling : target);
  schedulePreview();
}

function onUrlRowDragEnd(e) {
  document.querySelectorAll("#f-url .url-line").forEach((r) => {
    r.classList.remove("dragging", "drop-target");
  });
  _urlDragSrc = null;
}

// Debounced live preview. Calls /api/apps/parse and renders a one-line
// summary of what the parser detected. Updates in <300ms after the last
// keystroke — fast enough to feel live, slow enough to not hammer the
// server on every character.
function schedulePreview() {
  clearTimeout(_previewTimer);
  _previewTimer = setTimeout(updatePreview, 250);
}

async function updatePreview() {
  const urls = getUrls();
  const target = document.getElementById("f-url-preview");
  if (urls.length === 0) { target.className = ""; setText(target, ""); return; }
  try {
    const parsed = await api.post("/api/apps/parse", { urls });
    setText(target, describeParsedList(parsed));
    target.className = "detected";
  } catch (e) {
    target.className = "";
    setText(target, "");
  }
}

function describeParsedList(list) {
  if (!Array.isArray(list) || list.length === 0) return "";
  let networkIps = 0, publicIps = 0, domains = 0;
  for (const p of list) {
    if (!p || !p.host) continue;
    if (p.network_ip) networkIps++;
    else if (p.public_ip) publicIps++;
    else if (p.domain) domains++;
  }
  const bits = [];
  bits.push(`${list.length} URL${list.length === 1 ? "" : "s"}: ${networkIps} network IP, ${publicIps} public IP, ${domains} domain${domains === 1 ? "" : "s"}.`);
  const paths = list.map((p) => p && p.path).filter(Boolean);
  if (paths.length) {
    const uniq = Array.from(new Set(paths));
    bits.push(`Path${uniq.length === 1 ? "" : "s"}: ${uniq.join(", ")}`);
  }
  return bits.join("  ");
}

async function scrapeFromUrlField() {
  const urls = getUrls();
  const msg = document.getElementById("formMsg");
  if (!urls.length) { msg.className = "msg err"; setText(msg, "Enter a URL first."); return; }
  const url = urls[0];
  msg.className = "msg"; setText(msg, "Fetching…");
  try {
    const s = await api.post("/api/scrape", { url });
    if (!document.getElementById("f-title").value) setField("f-title", s.title);
    if (!document.getElementById("f-desc").value) setField("f-desc", s.description);
    // Replace the first URL row with the canonical fetched URL so
    // the list reflects what the scraper saw.
    const root = document.getElementById("f-url");
    const first = root.querySelector(".url-line input");
    if (first) first.value = s.url || url;
    msg.className = "msg ok"; setText(msg, "Filled from " + (s.title || url));
  } catch (e) {
    msg.className = "msg err"; setText(msg, "Fetch failed (you can still fill in manually).");
  }
}

function setField(id, v) { document.getElementById(id).value = v || ""; }

async function submitForm(e) {
  e.preventDefault();
  const msg = document.getElementById("formMsg");
  const urls = getUrls();
  const payload = {
    title: document.getElementById("f-title").value.trim(),
    urls,
    icon: document.getElementById("f-icon").value.trim(),
    group: document.getElementById("f-group").value.trim(),
    description: document.getElementById("f-desc").value.trim(),
  };
  if (!payload.title) { msg.className = "msg err"; setText(msg, "Title is required."); return; }
  if (!payload.urls.length) {
    msg.className = "msg err"; setText(msg, "Provide at least one URL."); return;
  }
  // Clear any stale status synchronously (before the await) so the "Saved"
  // confirmation only ever reflects *this* save — and give immediate feedback.
  msg.className = "msg";
  setText(msg, "Saving…");
  try {
    if (editingId) {
      await api.put("/api/apps/" + encodeURIComponent(editingId), payload);
    } else {
      await api.post("/api/apps", payload);
    }
    apps = (await api.get("/api/apps")).apps || [];
    renderList();
    pingAll();

    if (editingId) {
      // Editing a specific app: keep the form open so the admin can
      // make another tweak and save again. The server may have
      // normalized fields on save (e.g. icon auto-fetch from the
      // scraper), so refresh the in-memory form fields from the
      // freshly-saved app. The URL list is left as the user entered
      // it — they can edit further and save again.
      const fresh = apps.find((a) => a.id === editingId);
      if (fresh) {
        document.getElementById("f-title").value = fresh.title || "";
        document.getElementById("f-icon").value = fresh.icon || "";
        document.getElementById("f-group").value = fresh.group || "";
        document.getElementById("f-desc").value = fresh.description || "";
        // Re-render the URL list from the canonical urls[] so any
        // server-side normalization (dedup, parser canonicalization)
        // is reflected back to the form. (Use a fresh name — `urls` is
        // already in scope from the payload above.)
        const root = document.getElementById("f-url");
        root.replaceChildren();
        const freshUrls = (fresh.urls && fresh.urls.length) ? fresh.urls : (fresh.url ? [fresh.url] : []);
        if (freshUrls.length === 0) addUrlRow("", { focus: false });
        else freshUrls.forEach((u) => addUrlRow(u, { focus: false }));
      }
      msg.className = "msg ok";
      setText(msg, "Saved. Edit more, or Close when done.");
      // Keep focus where the user is likely to look next — the title
      // field at the top is the most common follow-up edit.
      document.getElementById("f-title").focus();
    } else {
      // Adding: keep the form open for the next entry. Clear every field
      // except group — the user is usually adding a batch to the same group
      // and doesn't want to retype it each time.
      const set = (id, v) => { document.getElementById(id).value = v || ""; };
      set("f-title", "");
      set("f-icon", "");
      set("f-desc", "");
      set("f-id", "");
      // Reset the URL list to one empty row.
      const root = document.getElementById("f-url");
      root.replaceChildren();
      addUrlRow("", { focus: false });
      setText(document.getElementById("f-url-preview"), "");
      document.getElementById("f-url-preview").className = "";
      // f-group deliberately left as-is.
      msg.className = "msg ok";
      setText(msg, "Saved. Add another, or Close when done.");
      const firstInput = root.querySelector("input");
      if (firstInput) firstInput.focus();
    }
  } catch (err) {
    msg.className = "msg err"; setText(msg, "Save failed: " + (err.message || "error"));
  }
}

async function del(app) {
  if (!confirm("Delete " + app.title + "?")) return;
  try {
    await api.del("/api/apps/" + encodeURIComponent(app.id));
    apps = apps.filter((a) => a.id !== app.id);
    delete pingResults[app.id];
    selected.delete(app.id);
    renderList();
  } catch (e) {
    alert("Delete failed.");
  }
}

// ---- drag-to-reorder (Manual mode only) ----
// Native HTML5 drag and drop. The source row is the one whose dragstart
// fired; the drop row is whatever the pointer is over. We re-order the
// in-memory `apps` array optimistically, then POST the new order to
// /api/apps/bulk/order. If that fails, refresh from the server to roll back.
let _dragSourceId = null;
let _dragSourceGroup = null; // for in-group restriction and group-block moves

function wireRowDrag(rowEl) {
  rowEl.addEventListener("dragstart", onRowDragStart);
  rowEl.addEventListener("dragover", onRowDragOver);
  rowEl.addEventListener("drop", onRowDrop);
  rowEl.addEventListener("dragend", onRowDragEnd);
  rowEl.addEventListener("dragleave", onRowDragLeave);
}

function onRowDragStart(e) {
  // Only the handle starts a drag. The browser fires dragstart on the
  // deepest draggable=true ancestor, which is the handle. So the target
  // is always the handle here.
  if (!e.target.classList.contains("drag-handle")) {
    e.preventDefault();
    return;
  }
  const row = e.currentTarget;
  _dragSourceId = row.dataset.id;
  _dragSourceGroup = row.dataset.group || "";
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", _dragSourceId);
  row.classList.add("dragging");
}

function onRowDragOver(e) {
  // Required to allow drop. Highlight the row under the pointer.
  // When grouped, only rows in the same group are valid drop targets.
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  const row = e.currentTarget;
  if (row.dataset.id === _dragSourceId) return;
  if (groupedView && row.dataset.group !== _dragSourceGroup) return;
  row.classList.add("drop-target");
}

function onRowDragLeave(e) {
  // dragleave fires for every child too; clear when leaving the row itself.
  if (e.currentTarget === e.target) e.currentTarget.classList.remove("drop-target");
}

function onRowDrop(e) {
  e.preventDefault();
  const targetRow = e.currentTarget;
  const targetId = targetRow.dataset.id;
  targetRow.classList.remove("drop-target");
  if (!_dragSourceId || !targetId || _dragSourceId === targetId) return;
  if (groupedView && targetRow.dataset.group !== _dragSourceGroup) return;
  const src = apps.findIndex((a) => a.id === _dragSourceId);
  const dst = apps.findIndex((a) => a.id === targetId);
  if (src < 0 || dst < 0) return;
  // Move the source to the target's position. After removing the source
  // via splice, the target's index either shifts down by one (when the
  // source was before it) or stays the same (when the source was after).
  // We always want the source to land *before* the target — so subtract 1
  // from the insert index in the "src < dst" case to compensate.
  const [moved] = apps.splice(src, 1);
  apps.splice(src < dst ? dst - 1 : dst, 0, moved);
  persistOrder();
}

function onRowDragEnd(e) {
  // Clear visual state on every row in case the drop didn't hit one.
  document.querySelectorAll("#list .app-row").forEach((r) => {
    r.classList.remove("dragging", "drop-target");
  });
  _dragSourceId = null;
  _dragSourceGroup = null;
}

// ---- group-title drag (move entire group block) ----
function wireGroupDrag(titleEl) {
  titleEl.addEventListener("dragstart", onGroupDragStart);
  titleEl.addEventListener("dragover", onGroupDragOver);
  titleEl.addEventListener("drop", onGroupDrop);
  titleEl.addEventListener("dragend", onGroupDragEnd);
  titleEl.addEventListener("dragleave", onGroupDragLeave);
}

function onGroupDragStart(e) {
  if (!e.target.classList.contains("drag-handle")) {
    e.preventDefault();
    return;
  }
  const title = e.currentTarget;
  _dragSourceGroup = title.dataset.group;
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", "group:" + _dragSourceGroup);
  title.classList.add("dragging");
}

function onGroupDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  const title = e.currentTarget;
  if (title.dataset.group !== _dragSourceGroup) title.classList.add("drop-target");
}

function onGroupDragLeave(e) {
  if (e.currentTarget === e.target) e.currentTarget.classList.remove("drop-target");
}

function onGroupDrop(e) {
  e.preventDefault();
  const targetTitle = e.currentTarget;
  const targetGroup = targetTitle.dataset.group;
  targetTitle.classList.remove("drop-target");
  if (!_dragSourceGroup || !targetGroup || _dragSourceGroup === targetGroup) return;
  // Collect the source group's apps (may not be contiguous in the array —
  // in-group drags can interleave apps from different groups). Iterate in
  // reverse so splice indices stay valid; unshift preserves original order.
  const srcBlock = [];
  for (let i = apps.length - 1; i >= 0; i--) {
    if ((apps[i].group || "") === _dragSourceGroup) {
      srcBlock.unshift(apps.splice(i, 1)[0]);
    }
  }
  if (!srcBlock.length) return;
  // Re-find the target block in the now-shorter array.
  const newDstStart = apps.findIndex((a) => (a.group || "") === targetGroup);
  if (newDstStart < 0) return;
  // Insert the source block before the target block.
  apps.splice(newDstStart, 0, ...srcBlock);
  persistOrder();
}

function onGroupDragEnd(e) {
  document.querySelectorAll("#list .group-title").forEach((t) => {
    t.classList.remove("dragging", "drop-target");
  });
  _dragSourceGroup = null;
}

// Reassign dense order 0..N-1 across the in-memory list and POST it.
// Called after every drop. On failure, alert and reload from server so
// the UI snaps back to server truth.
function persistOrder() {
  for (let i = 0; i < apps.length; i++) apps[i].order = i;
  const items = apps.map((a) => ({ id: a.id, order: a.order }));
  // Optimistic re-render so the drop animation feels instant.
  renderList();
  api.post("/api/apps/bulk/order", { items }).catch(async (err) => {
    alert("Reorder failed: " + (err.message || "error") + " — reloading.");
    await refreshApps();
  });
}

async function refreshApps() {
  apps = (await api.get("/api/apps")).apps || [];
  renderList();
}

// ---- drag and drop ----
function wireDropzone() {
  const dz = document.getElementById("dropzone");
  ["dragenter", "dragover"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
  ["dragleave", "drop"].forEach((ev) =>
    dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); }));
  dz.addEventListener("drop", async (e) => {
    e.preventDefault();
    const url = (e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain") || "").trim();
    if (!url) return;
    openForm(null);
    setFirstUrl(url);
    const msg = document.getElementById("formMsg");
    msg.className = "msg"; setText(msg, "Fetching metadata…");
    try {
      const s = await api.post("/api/scrape", { url });
      setField("f-title", s.title);
      setField("f-desc", s.description);
      setFirstUrl(s.url || url);
      msg.className = "msg ok"; setText(msg, "Auto-filled from " + (s.title || url));
    } catch (err) {
      msg.className = "msg err"; setText(msg, "Couldn't auto-fill; enter details manually.");
    }
  });
}

function setFirstUrl(value) {
  const root = document.getElementById("f-url");
  const first = root.querySelector(".url-line input");
  if (first) first.value = value || "";
  schedulePreview();
}

init().catch((err) => console.error(err));