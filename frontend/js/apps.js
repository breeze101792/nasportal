// /app management page: CRUD, drag-and-drop adding, scrape, ping, sort, group.
let apps = [];
let pingResults = {};
let auth = { authed: false, setup_required: false };
let settings = {};
let sortMode = "order";
let editingId = null;

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
  if (settings.wallpaper) document.body.style.backgroundImage = `url("${cssEsc(settings.wallpaper)}")`;

  renderTopLinks();
  renderBanner();

  // Auth-gated controls
  const canEdit = auth.authed;
  document.getElementById("addBtn").hidden = !canEdit;
  document.getElementById("pingBtn").hidden = !canEdit; // ping is an edit-tier action
  document.getElementById("addBtn").addEventListener("click", () => openForm(null));
  document.getElementById("pingBtn").addEventListener("click", pingAll);
  document.getElementById("sort").addEventListener("change", (e) => { sortMode = e.target.value; renderList(); });
  document.getElementById("cancelBtn").addEventListener("click", closeForm);
  document.getElementById("scrapeBtn").addEventListener("click", scrapeFromUrlField);
  document.getElementById("appForm").addEventListener("submit", submitForm);
  wireDropzone();

  renderList();
  if (canEdit && apps.length) pingAll(); // ping on load (login-gated endpoint)
}

function renderTopLinks() {
  const links = document.getElementById("toplinks");
  links.replaceChildren();
  if (auth.authed) {
    links.appendChild(el("a", { href: "/settings", text: "Settings" }));
    links.appendChild(el("a", { href: "#", text: "Logout", onclick: async (e) => { e.preventDefault(); await api.post("/api/auth/logout"); location.reload(); } }));
  } else {
    links.appendChild(el("a", { href: "/login", text: "Login" }));
  }
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
  if (!apps.length) {
    root.appendChild(el("div", { class: "empty", text: "No apps yet." }));
    return;
  }
  const sorted = sortApps([...apps]);

  // Group first when sorting by group or manual; otherwise single flat list.
  const useGroups = sortMode === "group" || sortMode === "order";
  if (useGroups) {
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

  return el("div", { class: "app-row" }, left, mid, right);
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
  try {
    if (editingId) {
      await api.put("/api/apps/" + encodeURIComponent(editingId), payload);
    } else {
      await api.post("/api/apps", payload);
    }
    apps = (await api.get("/api/apps")).apps || [];
    closeForm();
    renderList();
    pingAll();
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
    renderList();
  } catch (e) {
    alert("Delete failed.");
  }
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