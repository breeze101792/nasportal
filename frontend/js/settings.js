// /settings page — three modes:
//  1. setup_required (no password yet): only the "set admin password" form.
//  2. guest (password exists, not authed): bounce to /login.
//  3. authed: full editors across two tabs (General / IP Translation).
let engines = [];
let translations = []; // [{from: "1.2.3.4", to: "5.6.7.8"}, ...]

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
  wireTabs();
  loadIdentity(s);
  loadEngines(s);
  loadTheme(s);
  loadWidth(s);
  loadBackgroundColor(s);
  loadShowUntranslatable(s);
  loadLocalFirst(s);
  loadTranslations(s);
  wireIdentity(s);
  wireEngines();
  wireTheme();
  wireWidth();
  wireBackgroundColor();
  wireShowUntranslatable();
  wireLocalFirst();
  wireTranslation();
  wirePassword();
  // Let the (optional) Network Scan tab initialize itself. The
  // scan.js script registers a listener for this event and only runs
  // once it knows the visitor is authed.
  document.dispatchEvent(new CustomEvent("scan:init"));
  // Apply the (possibly customized) background color to the settings
  // page itself so the picker can show a real preview of what the
  // other pages will look like. ``applyTheme`` was already called
  // in ``init``'s caller flow.
  applyBackgroundColor(s.background_color);
}

// ---- tabs ----
function wireTabs() {
  const buttons = document.querySelectorAll(".settings-tabs .tab-btn");
  const panels = document.querySelectorAll(".tab-panel");
  buttons.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      buttons.forEach((b) => b.classList.toggle("active", b === btn));
      panels.forEach((p) => { p.hidden = p.dataset.tab !== target; });
    });
  });
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

// ---- background color ----
function loadBackgroundColor(s) {
  // The setting is a free-form CSS color string. The native color
  // picker only accepts #rrggbb, so when the stored value is anything
  // else (e.g. "transparent", "rgba(...)", a named color) we leave
  // the picker's swatch empty and show the value in the text field.
  const v = s.background_color || "";
  const picker = document.getElementById("s-bg-color");
  const text = document.getElementById("s-bg-color-text");
  text.value = v;
  picker.value = isHexColor(v) ? v : "#000000";
}
function isHexColor(s) {
  return /^#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?$/.test(s || "");
}
function wireBackgroundColor() {
  const picker = document.getElementById("s-bg-color");
  const text = document.getElementById("s-bg-color-text");
  const clear = document.getElementById("s-bg-color-clear");
  // Keep the two inputs in sync: editing the text box updates the
  // picker's swatch (when it's a valid hex) and the live preview;
  // editing the picker updates the text box. Both apply the change
  // to the page immediately for a live preview — the persisted save
  // happens when the user clicks "Save" on the Portal panel.
  function preview(val) {
    applyBackgroundColor(val);
  }
  text.addEventListener("input", () => {
    if (isHexColor(text.value)) picker.value = text.value;
    preview(text.value);
  });
  picker.addEventListener("input", () => {
    text.value = picker.value;
    preview(picker.value);
  });
  clear.addEventListener("click", () => {
    text.value = "";
    picker.value = "#000000";
    applyBackgroundColor("");
  });
}

// ---- show_untranslatable ----
function loadShowUntranslatable(s) {
  document.getElementById("s-show-untranslatable").checked = s.show_untranslatable !== false;
}
function wireShowUntranslatable() {
  const cb = document.getElementById("s-show-untranslatable");
  cb.addEventListener("change", async () => {
    try {
      const updated = await api.put("/api/settings", { show_untranslatable: cb.checked });
      cb.checked = !!updated.show_untranslatable;
    } catch (err) {
      cb.checked = !cb.checked; // revert
    }
  });
}

// ---- local_first ----
function loadLocalFirst(s) {
  // Default to on — matches the server default. The first-run state
  // (no settings.json) hands back DEFAULT_SETTINGS which has it true.
  document.getElementById("s-local-first").checked = s.local_first !== false;
}
function wireLocalFirst() {
  const cb = document.getElementById("s-local-first");
  cb.addEventListener("change", async () => {
    try {
      const updated = await api.put("/api/settings", { local_first: cb.checked });
      cb.checked = !!updated.local_first;
    } catch (err) {
      cb.checked = !cb.checked; // revert
    }
  });
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
        background_color: document.getElementById("s-bg-color-text").value.trim(),
        home_layout: document.getElementById("s-layout").value,
        show_untranslatable: document.getElementById("s-show-untranslatable").checked,
        local_first: document.getElementById("s-local-first").checked,
        search_engines: engines,
        default_engine: document.getElementById("s-default").value,
      });
      engines = updated.search_engines || [];
      document.getElementById("s-layout").value = updated.home_layout || "grouped";
      document.getElementById("s-show-untranslatable").checked = updated.show_untranslatable !== false;
      document.getElementById("s-local-first").checked = updated.local_first !== false;
      loadBackgroundColor(updated);
      applyBackgroundColor(updated.background_color);
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

// ---- IP translation ----
function loadTranslations(s) {
  // ip_translation is a {from: to} object on the server. We represent
  // it in the UI as an ordered list of {from, to} pairs so the admin
  // can edit them in a stable order without worrying about dict key
  // ordering in JSON.
  const t = s.ip_translation || {};
  translations = Object.keys(t).map((k) => ({ from: k, to: t[k] }));
  renderTranslationRows();
}

function renderTranslationRows() {
  const root = document.getElementById("translationRows");
  root.replaceChildren();
  if (translations.length === 0) {
    root.appendChild(el("div", { class: "hint", text: "No translations yet. Add one below." }));
    return;
  }
  translations.forEach((t, i) => {
    const row = el("div", { class: "trans-row" });
    const f = el("input", { type: "text", placeholder: "e.g. 192.168.1.51", value: t.from || "", "data-i": String(i), "data-f": "from" });
    const arrow = el("span", { class: "arrow", text: "→" });
    const to = el("input", { type: "text", placeholder: "e.g. 10.147.20.51", value: t.to || "", "data-i": String(i), "data-f": "to" });
    f.addEventListener("input", onTranslationInput);
    to.addEventListener("input", onTranslationInput);
    const del = el("button", { class: "btn danger", type: "button", text: "Remove",
      onclick: () => { translations.splice(i, 1); renderTranslationRows(); } });
    row.append(f, arrow, to, del);
    root.appendChild(row);
  });
}

function onTranslationInput(e) {
  const i = +e.target.dataset.i;
  const f = e.target.dataset.f;
  translations[i][f] = e.target.value;
}

function wireTranslation() {
  document.getElementById("addTranslation").addEventListener("click", () => {
    translations.push({ from: "", to: "" });
    renderTranslationRows();
    // Focus the new row's "from" input.
    const inputs = document.querySelectorAll("#translationRows .trans-row input");
    if (inputs.length) inputs[inputs.length - 2].focus();
  });
  document.getElementById("saveTranslation").addEventListener("click", async () => {
    const msg = document.getElementById("translationMsg");
    msg.className = "msg"; setText(msg, "Saving…");
    try {
      // Build a clean {from: to} dict, skipping empty rows and
      // resolving duplicate keys (last write wins — the admin will
      // see "Duplicate key" the next time they edit and can fix it).
      const out = {};
      const seen = new Set();
      for (const t of translations) {
        const k = (t.from || "").trim();
        const v = (t.to || "").trim();
        if (!k || !v) continue; // empty row, ignore
        if (seen.has(k)) continue; // duplicate, ignore
        seen.add(k);
        out[k] = v;
      }
      const updated = await api.put("/api/settings", { ip_translation: out });
      loadTranslations(updated);
      msg.className = "msg ok"; setText(msg, "Saved.");
    } catch (err) {
      msg.className = "msg err"; setText(msg, "Save failed: " + (err.message || "error"));
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
