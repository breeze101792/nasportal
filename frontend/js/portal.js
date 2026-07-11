// Portal home: search bar + grouped app grid (read-only view).
async function init() {
  const [settings, appsData, auth] = await Promise.all([
    api.get("/api/settings"),
    api.get("/api/apps"),
    authState(),
  ]);

  // Brand + wallpaper
  setText(document.getElementById("brand"), settings.portal_title || "NAS Portal");
  document.title = (settings.portal_title || "NAS Portal") + " — NAS";
  if (settings.wallpaper) document.body.style.backgroundImage = `url("${cssEsc(settings.wallpaper)}")`;

  // Engine dropdown
  const engineSel = document.getElementById("engine");
  (settings.search_engines || []).forEach((e) => {
    engineSel.appendChild(el("option", { value: e.id, text: e.name }));
  });
  if (settings.default_engine) engineSel.value = settings.default_engine;

  // Search submit: build the engine URL with %s replaced by the encoded query.
  document.getElementById("search").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const q = document.getElementById("q").value.trim();
    if (!q) return;
    const engine = (settings.search_engines || []).find((e) => e.id === engineSel.value);
    // Require an http(s) engine URL so a stored javascript:...%s engine can't
    // be opened as a script URL.
    if (!engine || !engine.url.includes("%s") || !/^https?:\/\//i.test(engine.url)) return;
    const target = engine.url.replace("%s", encodeURIComponent(q));
    window.open(target, "_blank", "noopener");
  });

  // Top links
  const links = document.getElementById("toplinks");
  links.appendChild(el("a", { href: "/app", text: "Apps" }));
  if (auth.authed) {
    links.appendChild(el("a", { href: "/settings", text: "Settings" }));
    links.appendChild(el("a", { href: "#", text: "Logout", onclick: logoutAndReload }));
  } else {
    links.appendChild(el("a", { href: "/login", text: "Login" }));
  }

  // App grid, grouped
  renderApps(appsData.apps || []);
}

async function logoutAndReload(e) {
  e.preventDefault();
  await api.post("/api/auth/logout");
  location.reload();
}

function renderApps(apps) {
  const root = document.getElementById("groups");
  root.replaceChildren();
  if (!apps.length) {
    root.appendChild(el("div", { class: "empty", text: "No apps yet. Open Apps to add some." }));
    return;
  }
  const sorted = [...apps].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const groups = new Map();
  for (const a of sorted) {
    const g = a.group || "Ungrouped";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(a);
  }
  for (const [group, items] of groups) {
    root.appendChild(el("div", { class: "group-title", text: group }));
    const grid = el("div", { class: "grid" });
    for (const a of items) grid.appendChild(card(a));
    root.appendChild(grid);
  }
}

function card(a) {
  const href = safeUrl(a.url);
  const c = el("a", { class: "card", href, target: "_blank", rel: "noopener noreferrer", title: a.description || a.title });
  // icon: <img> if set, else initial fallback
  if (a.icon) {
    const img = el("img", { class: "icon", src: a.icon, alt: "" });
    img.addEventListener("error", () => {
      const fb = el("div", { class: "icon-fallback", text: (a.title || "?").trim().charAt(0).toUpperCase() || "?" });
      img.replaceWith(fb);
    });
    c.appendChild(img);
  } else {
    c.appendChild(el("div", { class: "icon-fallback", text: (a.title || "?").trim().charAt(0).toUpperCase() || "?" }));
  }
  c.appendChild(el("div", { class: "title", text: a.title }));
  return c;
}

init().catch((err) => {
  console.error(err);
});