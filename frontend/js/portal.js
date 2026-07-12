// Portal home: search bar + grouped app grid (read-only view).
let homeLayout = "grouped"; // "grouped" (a section per group) | "flow" (one continuous grid)

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
  applyTheme(settings.theme);
  applyPortalWidth(settings.portal_width);
  homeLayout = settings.home_layout === "flow" ? "flow" : "grouped";

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

  // Top links — icon-only nav (single gear to /settings for authed users;
  // /login?next=/settings for guests, so the post-login redirect lands on Settings).
  renderTopLinks("home", auth.authed);

  // App grid, grouped
  renderApps(appsData.apps || []);
}

function renderApps(apps) {
  const root = document.getElementById("groups");
  root.replaceChildren();
  if (!apps.length) {
    root.appendChild(el("div", { class: "empty", text: "No apps yet. Add some from the Apps page." }));
    return;
  }
  const sorted = [...apps].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));

  if (homeLayout === "flow") {
    // One continuous grid: cards fill each row before wrapping to the next,
    // so short groups don't leave gaps. The group is shown on each card.
    const grid = el("div", { class: "grid" });
    for (const a of sorted) grid.appendChild(card(a, true));
    root.appendChild(grid);
    return;
  }

  // Grouped: a titled section per group, stacked top to bottom.
  const groups = new Map();
  for (const a of sorted) {
    const g = a.group || "Ungrouped";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(a);
  }
  for (const [group, items] of groups) {
    root.appendChild(el("div", { class: "group-title", text: group }));
    const grid = el("div", { class: "grid" });
    for (const a of items) grid.appendChild(card(a, false));
    root.appendChild(grid);
  }
}

function card(a, showGroup) {
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
  if (showGroup && a.group) c.appendChild(el("div", { class: "card-group", text: a.group }));
  return c;
}

init().catch((err) => {
  console.error(err);
});