// /settings page — three modes:
//  1. setup_required (no password yet): only the "set admin password" form.
//  2. guest (password exists, not authed): bounce to /login.
//  3. authed: full identity / search-engine / password editors.
let engines = [];

async function init() {
  const auth = await authState();

  if (auth.setup_required) {
    document.getElementById("setupPanel").hidden = false;
    wireSetupForm();
    return;
  }
  if (!auth.authed) {
    const next = encodeURIComponent("/settings");
    location.href = "/login?next=" + next;
    return;
  }

  document.getElementById("content").hidden = false;
  // Load settings first so the brand reflects the user's portal title
  // (rather than the page name), then set the page subtitle, then nav.
  const s = await api.get("/api/settings");
  setText(document.getElementById("brand"), s.portal_title || "NAS Portal");
  setText(document.getElementById("brandSub"), "General settings");
  applyTheme(s.theme);
  applyPortalWidth(s.portal_width);
  renderTopLinks("settings", true);
  loadIdentity(s);
  loadEngines(s);
  loadTheme(s);
  loadWidth(s);
  wireIdentity(s);
  wireEngines();
  wireTheme();
  wireWidth();
  wirePassword();
}

// ---- setup mode ----
function wireSetupForm() {
  document.getElementById("setupForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const pw = document.getElementById("setup-pw").value;
    const msg = document.getElementById("setupMsg");
    if (!pw) { msg.className = "msg err"; setText(msg, "Enter a password."); return; }
    msg.className = "msg"; setText(msg, "Saving…");
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        credentials: "same-origin", body: JSON.stringify({ password: pw }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error);
      location.href = "/settings"; // reload as authed
    } catch (err) {
      msg.className = "msg err"; setText(msg, "Failed: " + (err.message || "error"));
    }
  });
}

// ---- identity ----
function loadIdentity(s) {
  document.getElementById("s-title").value = s.portal_title || "";
  document.getElementById("s-wallpaper").value = s.wallpaper || "";
  document.getElementById("s-layout").value = ["grouped", "flow"].includes(s.home_layout) ? s.home_layout : "grouped";
}

// ---- theme ----
function loadTheme(s) {
  document.getElementById("s-theme").value = ["light", "dark", "system"].includes(s.theme) ? s.theme : "dark";
}
function wireTheme() {
  const sel = document.getElementById("s-theme");
  const msg = document.getElementById("themeMsg");
  sel.addEventListener("change", async () => {
    try {
      const updated = await api.put("/api/settings", { theme: sel.value });
      applyTheme(updated.theme);
      sel.value = updated.theme || "dark";
      msg.className = "msg ok"; setText(msg, "Saved.");
    } catch (err) {
      msg.className = "msg err"; setText(msg, "Save failed: " + (err.message || "error"));
    }
  });
}

// ---- portal width ----
function loadWidth(s) {
  const w = applyPortalWidth(s.portal_width); // clamps + sets the CSS var
  const inp = document.getElementById("s-width");
  inp.value = w;
  setText(document.getElementById("s-width-val"), w + "%");
}
function wireWidth() {
  const inp = document.getElementById("s-width");
  const val = document.getElementById("s-width-val");
  const msg = document.getElementById("widthMsg");
  // Live preview while dragging…
  inp.addEventListener("input", () => {
    const w = +inp.value;
    setText(val, w + "%");
    applyPortalWidth(w);
  });
  // …and persist on release.
  inp.addEventListener("change", async () => {
    const w = +inp.value;
    try {
      const updated = await api.put("/api/settings", { portal_width: w });
      const saved = applyPortalWidth(updated.portal_width);
      inp.value = saved;
      setText(val, saved + "%");
      msg.className = "msg ok"; setText(msg, "Saved.");
    } catch (err) {
      msg.className = "msg err"; setText(msg, "Save failed: " + (err.message || "error"));
    }
  });
}
function wireIdentity(s) {
  document.getElementById("identityForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("identityMsg");
    try {
      const updated = await api.put("/api/settings", {
        portal_title: document.getElementById("s-title").value,
        wallpaper: document.getElementById("s-wallpaper").value.trim(),
        home_layout: document.getElementById("s-layout").value,
        search_engines: engines,
        default_engine: document.getElementById("s-default").value,
      });
      engines = updated.search_engines || [];
      document.getElementById("s-layout").value = updated.home_layout || "grouped";
      msg.className = "msg ok"; setText(msg, "Saved.");
    } catch (err) {
      msg.className = "msg err"; setText(msg, "Save failed: " + (err.message || "error"));
    }
  });
}

// ---- search engines ----
function loadEngines(s) {
  engines = (s.search_engines || []).map((e) => ({ ...e }));
  renderEngineRows();
  renderDefaultSelect();
}

function renderEngineRows() {
  const root = document.getElementById("engineRows");
  root.replaceChildren();
  engines.forEach((e, i) => {
    const row = el("div", { class: "engine-row" });
    const name = el("input", { value: e.name, placeholder: "Name", "data-i": String(i), "data-f": "name" });
    const url = el("input", { value: e.url, placeholder: "https://…/search?q=%s", "data-i": String(i), "data-f": "url" });
    name.addEventListener("input", onEngineInput);
    url.addEventListener("input", onEngineInput);
    const del = el("button", { class: "btn danger", type: "button", text: "Remove",
      onclick: () => { engines.splice(i, 1); renderEngineRows(); renderDefaultSelect(); } });
    row.append(name, url, del);
    root.appendChild(row);
  });
}

function onEngineInput(e) {
  const i = +e.target.dataset.i;
  const f = e.target.dataset.f;
  engines[i][f] = e.target.value;
  if (f === "name") renderDefaultSelect();
}

function renderDefaultSelect() {
  const sel = document.getElementById("s-default");
  const cur = sel.value;
  sel.replaceChildren();
  engines.forEach((e) => sel.appendChild(el("option", { value: e.id, text: e.name || e.id })));
  // Preserve current selection if still present, else pick first.
  sel.value = engines.find((e) => e.id === cur) ? cur : (engines[0] && engines[0].id) || "";
}

function wireEngines() {
  document.getElementById("addEngine").addEventListener("click", () => {
    const base = "engine";
    let id = base, n = 1;
    const ids = new Set(engines.map((e) => e.id));
    while (ids.has(id)) { n++; id = base + "-" + n; }
    engines.push({ id, name: "", url: "https://www.google.com/search?q=%s" });
    renderEngineRows();
    renderDefaultSelect();
  });
  document.getElementById("saveEngines").addEventListener("click", async () => {
    const msg = document.getElementById("enginesMsg");
    // Ensure every engine has a stable id derived from its name if missing.
    const ids = new Set();
    engines.forEach((e) => {
      let id = (e.name || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      if (!id) id = "engine";
      let base = id, n = 1;
      while (ids.has(id)) { n++; id = base + "-" + n; }
      ids.add(id);
      e.id = id;
    });
    try {
      const updated = await api.put("/api/settings", {
        portal_title: document.getElementById("s-title").value,
        wallpaper: document.getElementById("s-wallpaper").value.trim(),
        search_engines: engines,
        default_engine: document.getElementById("s-default").value,
      });
      engines = updated.search_engines || [];
      renderEngineRows();
      renderDefaultSelect();
      msg.className = "msg ok"; setText(msg, "Saved.");
    } catch (err) {
      msg.className = "msg err"; setText(msg, "Save failed: " + (err.error || err.message || "check each URL has %s"));
    }
  });
}

// ---- password ----
function wirePassword() {
  document.getElementById("pwForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = document.getElementById("pwMsg");
    const cur = document.getElementById("pw-current").value;
    const neu = document.getElementById("pw-new").value;
    if (!neu) { msg.className = "msg err"; setText(msg, "Enter a new password."); return; }
    try {
      await api.put("/api/auth/password", { current_password: cur, new_password: neu });
      document.getElementById("pw-current").value = "";
      document.getElementById("pw-new").value = "";
      msg.className = "msg ok"; setText(msg, "Password changed.");
    } catch (err) {
      msg.className = "msg err";
      setText(msg, err.error === "invalid_current_password" ? "Current password is wrong." : "Change failed.");
    }
  });
}

init().catch((err) => console.error(err));