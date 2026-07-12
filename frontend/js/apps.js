// /app management page: CRUD, drag-and-drop adding, scrape, ping, sort, group.
let apps = [];
let pingResults = {};
let auth = { authed: false, setup_required: false };
let settings = {};
let sortMode = "order";
let editingId = null;
const selected = new Set(); // app ids chosen for bulk actions
let selAnchorId = null; // app id of the last directly-clicked checkbox (for shift-range)

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
  document.getElementById("cancelBtn").addEventListener("click", closeForm);
  document.getElementById("scrapeBtn").addEventListener("click", scrapeFromUrlField);
  document.getElementById("appForm").addEventListener("submit", submitForm);
  wireDropzone();

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

  // Group-titled sections only when sorting by group. Manual (order) and
  // the other sorts render as a single flat list, so drag-to-reorder in
  // Manual mode works cleanly across group boundaries.
  if (sortMode === "group") {
    const groups = new Map();
    for (const a of sorted) {
      const g = a.group || "Ungrouped";
      if (!groups.has(g)) groups.set(g, []);
      groups.get(g).push(a);
    }
    for (const [g, items] of groups) {
      root.appendChild(el("div", { class: "group-title", text: g }));
      for (const a of items) root.appendChild(row(a));
    }
  } else {
    for (const a of sorted) root.appendChild(row(a));
  }
  updateSelState();
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

  const icon = a.icon
    ? (() => { const img = el("img", { src: a.icon, alt: "", style: "width:32px;height:32px;border-radius:8px;object-fit:contain;background:rgba(255,255,255,0.06)" });
                img.addEventListener("error", () => img.replaceWith(el("div", { class: "icon-fallback", style: "width:32px;height:32px;font-size:1rem", text: (a.title || "?").charAt(0).toUpperCase() || "?" })));
                return img; })()
    : el("div", { class: "icon-fallback", style: "width:32px;height:32px;font-size:1rem", text: (a.title || "?").charAt(0).toUpperCase() || "?" });

  const left = el("div", {}, icon);
  const mid = el("div", {},
    el("div", { style: "font-weight:500", text: a.title }),
    el("div", { class: "meta" },
      el("span", { class: "dot " + dotClass }),
      el("span", { text: statusText }),
      a.group ? el("span", { text: "· " + a.group }) : null,
    ),
  );

  const right = el("div", { style: "display:flex;gap:6px" });
  right.appendChild(el("a", { class: "btn", href: safeUrl(a.url), target: "_blank", rel: "noopener noreferrer", text: "Open" }));
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
    const rowEl = el("div", { class: cls + (draggable ? " draggable" : ""), "data-id": a.id });
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
  h.dataset.id = id;
  return h;
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
  set("f-url", app && app.url);
  set("f-icon", app && app.icon);
  set("f-group", app && app.group);
  set("f-desc", app && app.description);
  set("f-id", app && app.id);
  setText(document.getElementById("formMsg"), "");
  document.getElementById("formMsg").className = "msg";
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
  document.getElementById("f-url").focus();
}

function closeForm() {
  document.getElementById("formPanel").hidden = true;
  editingId = null;
}

async function scrapeFromUrlField() {
  const url = document.getElementById("f-url").value.trim();
  const msg = document.getElementById("formMsg");
  if (!url) { msg.className = "msg err"; setText(msg, "Enter a URL first."); return; }
  msg.className = "msg"; setText(msg, "Fetching…");
  try {
    const s = await api.post("/api/scrape", { url });
    if (!document.getElementById("f-title").value) setField("f-title", s.title);
    if (!document.getElementById("f-icon").value) setField("f-icon", s.favicon);
    if (!document.getElementById("f-desc").value) setField("f-desc", s.description);
    setField("f-url", s.url);
    msg.className = "msg ok"; setText(msg, "Filled from " + (s.title || url));
  } catch (e) {
    msg.className = "msg err"; setText(msg, "Fetch failed (you can still fill in manually).");
  }
}

function setField(id, v) { document.getElementById(id).value = v || ""; }

async function submitForm(e) {
  e.preventDefault();
  const msg = document.getElementById("formMsg");
  const payload = {
    title: document.getElementById("f-title").value.trim(),
    url: document.getElementById("f-url").value.trim(),
    icon: document.getElementById("f-icon").value.trim(),
    group: document.getElementById("f-group").value.trim(),
    description: document.getElementById("f-desc").value.trim(),
  };
  if (!payload.title || !payload.url) { msg.className = "msg err"; setText(msg, "Title and URL are required."); return; }
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
      // Editing a specific app: close the form once saved.
      closeForm();
    } else {
      // Adding: keep the form open for the next entry. Clear every field
      // except group — the user is usually adding a batch to the same group
      // and doesn't want to retype it each time.
      const set = (id, v) => { document.getElementById(id).value = v || ""; };
      set("f-title", "");
      set("f-url", "");
      set("f-icon", "");
      set("f-desc", "");
      set("f-id", "");
      // f-group deliberately left as-is.
      msg.className = "msg ok";
      setText(msg, "Saved. Add another, or Close when done.");
      document.getElementById("f-url").focus();
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
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", _dragSourceId);
  row.classList.add("dragging");
}

function onRowDragOver(e) {
  // Required to allow drop. Highlight the row under the pointer.
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  const row = e.currentTarget;
  if (row.dataset.id !== _dragSourceId) row.classList.add("drop-target");
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
    setField("f-url", url);
    const msg = document.getElementById("formMsg");
    msg.className = "msg"; setText(msg, "Fetching metadata…");
    try {
      const s = await api.post("/api/scrape", { url });
      setField("f-title", s.title);
      setField("f-icon", s.favicon);
      setField("f-desc", s.description);
      setField("f-url", s.url);
      msg.className = "msg ok"; setText(msg, "Auto-filled from " + (s.title || url));
    } catch (err) {
      msg.className = "msg err"; setText(msg, "Couldn't auto-fill; enter details manually.");
    }
  });
}

init().catch((err) => console.error(err));