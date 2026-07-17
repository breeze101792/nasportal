// Portal home: search bar + grouped app grid (read-only view).
let homeLayout = "grouped"; // "grouped" (a section per group) | "flow" (one continuous grid)
let showResolvedKind = false; // debug toggle: surface the resolver's URL-kind on each card

async function init() {
  const [settings, appsData, auth] = await Promise.all([
    api.get("/api/settings"),
    api.get("/api/apps/resolved"),
    authState(),
  ]);

  // Brand + wallpaper
  setText(document.getElementById("brand"), settings.portal_title || "NAS Portal");
  document.title = (settings.portal_title || "NAS Portal") + " — NAS";
  if (settings.wallpaper) document.body.style.backgroundImage = `url("${cssEsc(settings.wallpaper)}")`;
  applyTheme(settings.theme);
  applyBackgroundColor(settings.background_color);
  applyPortalWidth(settings.portal_width);
  homeLayout = settings.home_layout === "flow" ? "flow" : "grouped";
  showResolvedKind = settings.show_resolved_kind === true;

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

  // App grid, grouped. The resolved endpoint has already filtered out
  // untranslatable apps (when show_untranslatable is off) and replaced
  // each app's `url` with the best URL for our source IP.
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
  // The "kind" field comes from the resolver and tells the user why
  // this URL was chosen. The badge is hidden by default (the home
  // view stays clean) and surfaced only when the admin has flipped
  // ``settings.show_resolved_kind`` on — a debug toggle useful for
  // diagnosing translation / local-first issues. We still skip the
  // "network" kind even when the toggle is on, since "local network"
  // for an on-network app is the boring default the admin can infer.
  const kind = a.resolved && a.resolved.kind;
  const badge = (showResolvedKind && kind && kind !== "network") ? kindLabel(kind) : null;
  const c = el("a", { class: "card", href, target: "_blank", rel: "noopener noreferrer", title: a.description || a.title });
  // Icon priority:
  //   1. stored `a.icon` (admin set it) — use as-is
  //   2. otherwise fetch /api/favicon?url=… at render time, with
  //      an in-memory cache so the same host isn't scraped twice
  //   3. on error / no result, fall back to a letter glyph
  const placeholder = el("div", { class: "icon-fallback", text: (a.title || "?").trim().charAt(0).toUpperCase() || "?" });
  c.appendChild(placeholder);
  resolveIcon(a, placeholder);
  c.appendChild(el("div", { class: "title", text: a.title }));
  if (badge) c.appendChild(el("div", { class: "card-kind", text: badge }));
  if (showGroup && a.group) c.appendChild(el("div", { class: "card-group", text: a.group }));
  return c;
}

function kindLabel(kind) {
  // Short, non-alarming labels. The user already chose to keep
  // untranslatable apps visible (or not) — these are just hints.
  switch (kind) {
    case "translated": return "via translation";
    case "local_fallback": return "local network";
    case "domain": return "public domain";
    case "public_ip": return "public IP";
    case "fallback": return "other network";
    case "legacy": return "";
    default: return "";
  }
}

init().catch((err) => {
  console.error(err);
});