// /settings page — three modes:
//  1. setup_required (no password yet): only the "set admin password" form.
//  2. guest (password exists, not authed): bounce to /login.
//  3. authed: full editors across two tabs (General / IP Translation).
//
// All field-level editors auto-save on change. The only remaining
// submit button is the password form — changing a credential deserves
// an explicit confirm step, not a per-keystroke save.
let engines = [];
let translations = []; // [{from: "1.2.3.4", to: "5.6.7.8"}, ...]
let _savingCount = 0;

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
  setText(document.getElementById("brandSub"), "Settings");
  applyTheme(s.theme);
  applyPortalWidth(s.portal_width);
  applyBackgroundColor(s.background_color);
  renderTopLinks("settings", true);
  wireTabs();
  loadIdentity(s);
  loadEngines(s);
  loadTheme(s);
  loadWidth(s);
  loadBackgroundColor(s);
  loadShowUntranslatable(s);
  loadShowResolvedKind(s);
  loadOpenAppsInNewTab(s);
  loadTranslations(s);
  wireTheme();
  wireWidth();
  wireBackgroundColor();
  wireIdentity();
  wireEngines();
  wireShowUntranslatable();
  wireShowResolvedKind();
  wireOpenAppsInNewTab();
  wireTranslation();
  wirePassword();
  // Let the (optional) Network Scan tab initialize itself. The
  // scan.js script registers a listener for this event and only runs
  // once it knows the visitor is authed.
  document.dispatchEvent(new CustomEvent("scan:init"));
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

// ---- generic auto-save helper ----
// PUT a partial settings update. Status messages show only on error
// (auto-save is silent on success — the field's own visual change
// already tells the user it took). ``_savingCount`` lets us show
// "Saving…" while in flight so the user has feedback for slow saves.
function autoSave(patch, msgEl) {
  _savingCount++;
  if (msgEl) {
    msgEl.className = "msg";
    setText(msgEl, "Saving…");
  }
  return api.put("/api/settings", patch)
    .then((updated) => {
      _savingCount--;
      if (msgEl && _savingCount === 0) {
        msgEl.className = "msg";
        setText(msgEl, "");
      }
      return updated;
    })
    .catch((err) => {
      _savingCount--;
      if (msgEl) {
        msgEl.className = "msg err";
        setText(msgEl, "Save failed: " + (err.message || "error"));
      }
      throw err;
    });
}

// Debounce a function so rapid input events collapse into one call.
function debounce(fn, ms) {
  let t = null;
  return function (...args) {
    if (t) clearTimeout(t);
    t = setTimeout(() => { t = null; fn.apply(this, args); }, ms);
  };
}

// ---- identity ----
function loadIdentity(s) {
  document.getElementById("s-title").value = s.portal_title || "";
  document.getElementById("s-wallpaper").value = s.wallpaper || "";
  document.getElementById("s-layout").value = ["grouped", "compact", "flow"].includes(s.home_layout) ? s.home_layout : "grouped";
}
function wireIdentity() {
  const title = document.getElementById("s-title");
  const wallpaper = document.getElementById("s-wallpaper");
  const layout = document.getElementById("s-layout");
  const msg = document.getElementById("identityMsg");
  // Title and wallpaper are text fields — debounce so we don't fire
  // a PUT on every keystroke. Layout is a select; persist on change.
  const saveTitle = debounce(() => {
    const sent = title.value;
    autoSave({ portal_title: sent }, msg).then((updated) => {
      // Reconcile in case the server trimmed/rejected the value. Only
      // overwrite the field if the user hasn't kept typing — otherwise
      // we'd clobber a newer value with an older response.
      if (title.value === sent) {
        title.value = updated.portal_title || "";
      }
      setText(document.getElementById("brand"), updated.portal_title || "NAS Portal");
      document.title = updated.portal_title || "NAS Portal";
    });
  }, 400);
  title.addEventListener("input", saveTitle);
  const saveWallpaper = debounce(() => {
    const sent = wallpaper.value.trim();
    autoSave({ wallpaper: sent }, msg).then((updated) => {
      if (wallpaper.value.trim() === sent) {
        wallpaper.value = updated.wallpaper || "";
      }
    });
  }, 400);
  wallpaper.addEventListener("input", saveWallpaper);
  layout.addEventListener("change", () => {
    autoSave({ home_layout: layout.value }, msg).then((updated) => {
      layout.value = ["grouped", "compact", "flow"].includes(updated.home_layout) ? updated.home_layout : "grouped";
    });
  });
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
  const msg = document.getElementById("appearanceMsg");
  // Keep the two inputs in sync: editing the text box updates the
  // picker's swatch (when it's a valid hex); editing the picker
  // updates the text box. Both apply the change to the page
  // immediately for a live preview AND persist to the server on
  // a debounce — the value is the source of truth as soon as the
  // user stops typing for a moment.
  function preview(val) {
    applyBackgroundColor(val);
  }
  const persist = debounce((val) => {
    autoSave({ background_color: val }, msg).then((updated) => {
      // Only reconcile if the user hasn't kept typing — otherwise we'd
      // clobber a newer value with an older response.
      if (text.value.trim() === val) {
        loadBackgroundColor(updated);
      }
    });
  }, 400);
  text.addEventListener("input", () => {
    if (isHexColor(text.value)) picker.value = text.value;
    preview(text.value);
    persist(text.value.trim());
  });
  picker.addEventListener("input", () => {
    text.value = picker.value;
    preview(picker.value);
    persist(picker.value);
  });
  clear.addEventListener("click", () => {
    text.value = "";
    picker.value = "#000000";
    applyBackgroundColor("");
    autoSave({ background_color: "" }, msg).then((updated) => {
      loadBackgroundColor(updated);
    });
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

// ---- show_resolved_kind (debug badge toggle) ----
function loadShowResolvedKind(s) {
  // Default to off — the home view is clean until the admin opts in.
  document.getElementById("s-show-resolved-kind").checked = s.show_resolved_kind === true;
}
function wireShowResolvedKind() {
  const cb = document.getElementById("s-show-resolved-kind");
  cb.addEventListener("change", async () => {
    try {
      const updated = await api.put("/api/settings", { show_resolved_kind: cb.checked });
      cb.checked = !!updated.show_resolved_kind;
    } catch (err) {
      cb.checked = !cb.checked; // revert
    }
  });
}

// ---- open_apps_in_new_tab (click behavior) ----
function loadOpenAppsInNewTab(s) {
  // Default to off — same-tab navigation. The admin can flip on /settings
  // if they prefer the portal to stay open in a background tab.
  document.getElementById("s-open-apps-in-new-tab").checked = s.open_apps_in_new_tab === true;
}
function wireOpenAppsInNewTab() {
  const cb = document.getElementById("s-open-apps-in-new-tab");
  cb.addEventListener("change", async () => {
    try {
      const updated = await api.put("/api/settings", { open_apps_in_new_tab: cb.checked });
      cb.checked = !!updated.open_apps_in_new_tab;
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
  // Auto-save on change (the theme is the source of truth the moment
  // the admin picks one — there's no preview/commit distinction to
  // maintain).
  const sel = document.getElementById("s-theme");
  const msg = document.getElementById("appearanceMsg");
  sel.addEventListener("change", () => {
    applyTheme(sel.value);
    autoSave({ theme: sel.value }, msg).then((updated) => {
      sel.value = ["light", "dark", "system"].includes(updated.theme) ? updated.theme : "dark";
    });
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
  // Live-preview the portal width as the admin drags the slider; the
  // server save is debounced so we don't fire a PUT on every drag tick.
  const inp = document.getElementById("s-width");
  const val = document.getElementById("s-width-val");
  const msg = document.getElementById("appearanceMsg");
  inp.addEventListener("input", () => {
    const w = +inp.value;
    setText(val, w + "%");
    applyPortalWidth(w);
  });
  const persist = debounce(() => {
    autoSave({ portal_width: +inp.value }, msg).then((updated) => {
      const w = applyPortalWidth(updated.portal_width);
      inp.value = w;
      setText(val, w + "%");
    });
  }, 250);
  inp.addEventListener("input", persist);
  // ``change`` fires once when the user releases the slider, which is
  // also a good place to flush the debounced save immediately.
  inp.addEventListener("change", persist);
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
      onclick: () => { engines.splice(i, 1); renderEngineRows(); renderDefaultSelect(); persistEngines(); } });
    row.append(name, url, del);
    root.appendChild(row);
  });
}

function onEngineInput(e) {
  const i = +e.target.dataset.i;
  const f = e.target.dataset.f;
  engines[i][f] = e.target.value;
  if (f === "name") renderDefaultSelect();
  persistEngines();
}

function renderDefaultSelect() {
  const sel = document.getElementById("s-default");
  const cur = sel.value;
  sel.replaceChildren();
  engines.forEach((e) => sel.appendChild(el("option", { value: e.id, text: e.name || e.id })));
  // Preserve current selection if still present, else pick first.
  sel.value = engines.find((e) => e.id === cur) ? cur : (engines[0] && engines[0].id) || "";
}

// Debounced auto-save: typing into a name/url field would otherwise
// fire a PUT on every keystroke. Coalesce into one save per quiet
// stretch. Add/remove/dropdown-change bypass the debounce.
const persistEngines = debounce(async () => {
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
    const updated = await autoSave({ search_engines: engines, default_engine: document.getElementById("s-default").value }, msg);
    engines = updated.search_engines || [];
    renderEngineRows();
    renderDefaultSelect();
  } catch (err) {
    // autoSave already wrote the error into msg; nothing to do here.
  }
}, 400);

function wireEngines() {
  document.getElementById("addEngine").addEventListener("click", () => {
    const base = "engine";
    let id = base, n = 1;
    const ids = new Set(engines.map((e) => e.id));
    while (ids.has(id)) { n++; id = base + "-" + n; }
    engines.push({ id, name: "", url: "https://www.google.com/search?q=%s" });
    renderEngineRows();
    renderDefaultSelect();
    persistEngines();
  });
  document.getElementById("s-default").addEventListener("change", () => {
    persistEngines();
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
      onclick: () => { translations.splice(i, 1); renderTranslationRows(); persistTranslations(); } });
    row.append(f, arrow, to, del);
    root.appendChild(row);
  });
}

function onTranslationInput(e) {
  const i = +e.target.dataset.i;
  const f = e.target.dataset.f;
  translations[i][f] = e.target.value;
  persistTranslations();
}

// Debounced auto-save: typing into a from/to field would otherwise
// fire a PUT on every keystroke. Coalesce into one save per quiet
// stretch. Add/remove bypass the debounce.
const persistTranslations = debounce(async () => {
  const msg = document.getElementById("translationMsg");
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
  try {
    const updated = await autoSave({ ip_translation: out }, msg);
    loadTranslations(updated);
  } catch (err) {
    // autoSave already wrote the error into msg; nothing to do here.
  }
}, 400);

function wireTranslation() {
  document.getElementById("addTranslation").addEventListener("click", () => {
    translations.push({ from: "", to: "" });
    renderTranslationRows();
    // Focus the new row's "from" input.
    const inputs = document.querySelectorAll("#translationRows .trans-row input");
    if (inputs.length) inputs[inputs.length - 2].focus();
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
