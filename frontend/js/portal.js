// Portal home: search bar + grouped app grid (read-only view).
let homeLayout = "grouped"; // "grouped" (a section per group) | "compact" (inline group blocks) | "flow" (one continuous grid)
let showResolvedKind = false; // debug toggle: surface the resolver's URL-kind on each card
let openAppsInNewTab = false; // click behavior: true → target=_blank on cards, false → target=_self

async function init() {
  const [settings, appsData, auth] = await Promise.all([
    api.get("/api/settings"),
    api.get("/api/apps/resolved"),
    authState(),
  ]);

  // Brand + wallpaper
  setText(document.getElementById("brand"), settings.portal_title || "NAS Portal");
  document.title = settings.portal_title || "NAS Portal";
  if (settings.wallpaper) document.body.style.backgroundImage = `url("${cssEsc(settings.wallpaper)}")`;
  applyTheme(settings.theme);
  applyBackgroundColor(settings.background_color);
  applyPortalWidth(settings.portal_width);
  homeLayout = ["grouped", "compact", "flow"].includes(settings.home_layout) ? settings.home_layout : "grouped";
  showResolvedKind = settings.show_resolved_kind === true;
  openAppsInNewTab = settings.open_apps_in_new_tab === true;

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

  // iOS "Add to Home Screen" hint. Safari doesn't fire
  // beforeinstallprompt, so the only way to teach an iPhone user
  // that the portal installs as a web app is an in-app card. Show
  // it once (a localStorage flag), only on iOS Safari (not on the
  // already-installed standalone web app, which sets
  // navigator.standalone), and only when running inside Safari
  // (Chrome-on-iOS reports CriOS in the UA, not Safari).
  showIosInstallHint();
}

function renderApps(apps) {
  const root = document.getElementById("groups");
  root.replaceChildren();
  // The compact layout treats the #groups root itself as a flex
  // container (each group is a child block); the other layouts
  // build their own grids inside #groups. Reset the class so the
  // CSS knows which mode we're in.
  root.className = homeLayout === "compact" ? "compact" : "";
  if (!apps.length) {
    root.appendChild(el("div", { class: "empty", text: "No apps yet. Add some from the Apps page." }));
    return;
  }
  const sorted = [...apps].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));

  if (homeLayout === "flow") {
    // Flow: one continuous grid of cards, sorted by ``order`` only.
    // The group is shown on each card. No clustering by group.
    const grid = el("div", { class: "grid" });
    for (const a of sorted) grid.appendChild(card(a, true));
    root.appendChild(grid);
    return;
  }

  // Build the per-group map. Both ``grouped`` and ``compact`` use it;
  // the difference is just how the groups are laid out around the
  // titles.
  const groups = new Map();
  for (const a of sorted) {
    const g = a.group || "Ungrouped";
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(a);
  }

  if (homeLayout === "compact") {
    // Compact: each group is a labeled inline block — group name on
    // top, cards in a flex row below. Multiple blocks sit side-by-side
    // in one wrap row (CSS flex on the #groups root), so a group with
    // 1–2 apps doesn't waste a full width, and adjacent small groups
    // share a line. The per-group label is preserved (the "area" the
    // user asked for) — cards are NOT shuffled into one mixed grid.
    for (const [group, items] of groups) {
      const block = el("div", { class: "group-block" });
      block.appendChild(groupTitleEl(group, /*compact*/ true));
      const cards = el("div", { class: "group-cards" });
      for (const a of items) cards.appendChild(card(a, false));
      block.appendChild(cards);
      root.appendChild(block);
    }
    return;
  }

  // Grouped: a titled section per group, stacked top to bottom. The
  // home page is read-only — group reordering happens on /app, where
  // the admin edits apps directly.
  for (const [group, items] of groups) {
    root.appendChild(groupTitleEl(group, false));
    const grid = el("div", { class: "grid" });
    for (const a of items) grid.appendChild(card(a, false));
    root.appendChild(grid);
  }
}

// Group-title row for the home page. The home page is read-only
// (guests see it too), so no drag handle — group reordering lives on
// /app. In compact mode the trailing hairline is suppressed (the
// title sits inside an inline group block, not a full-width section).
function groupTitleEl(g, compact) {
  const cls = "group-title" + (compact ? " compact" : "");
  return el("div", { class: cls, "data-group": g }, document.createTextNode(g));
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
  // Click target follows the ``open_apps_in_new_tab`` setting: when on
  // the click opens a new tab and the portal stays open in the
  // background; when off (the default) the click navigates this tab.
  // rel="noopener noreferrer" is set in both cases so the target page
  // can't reach back to our window via window.opener.
  const linkTarget = openAppsInNewTab ? "_blank" : "_self";
  const c = el("a", { class: "card", href, target: linkTarget, rel: "noopener noreferrer", title: a.description || a.title });
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
  // The ``other_network`` kind covers an IP that's on a local
  // network the visitor is NOT on — useful for tunneled / admin-only
  // addresses that were kept for completeness.
  switch (kind) {
    case "translated": return "via translation";
    case "other_network": return "other network";
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

// One-time iOS Add-to-Home-Screen hint. Lives at the bottom of
// <body>, dismissable, hidden after the first dismiss. iOS Safari
// is the only major browser without a programmatic install prompt
// (no beforeinstallprompt equivalent), so the only way to surface
// the option is a one-line card on the home page.
function showIosInstallHint() {
  // Already installed → no hint (the standalone app re-opens at /).
  if (window.navigator.standalone) return;
  // Only Safari on iOS — Chrome/Firefox on iOS use the system
  // WKWebView and don't support the home-screen install flow.
  var ua = navigator.userAgent;
  var isIOS = /iPad|iPhone|iPod/.test(ua) || (ua.includes("Mac") && "ontouchend" in document);
  var isSafari = /Safari/.test(ua) && !/CriOS|FxiOS|EdgiOS|OPiOS/.test(ua);
  if (!isIOS || !isSafari) return;
  // Already dismissed.
  try { if (localStorage.getItem("nasportal.iosInstallHint.dismissed") === "1") return; } catch (e) {}

  var hint = el("div", { class: "ios-install-hint", role: "status" });
  hint.appendChild(el("div", { class: "ios-install-hint-text",
    text: "Install NAS Portal: tap " }));
  var share = el("span", { class: "ios-install-hint-share", "aria-label": "Share button", title: "Share button" });
  share.appendChild(_shareGlyph());
  hint.appendChild(share);
  hint.appendChild(document.createTextNode(", then “Add to Home Screen”."));
  var dismiss = el("button", { class: "ios-install-hint-dismiss", type: "button", "aria-label": "Dismiss", title: "Dismiss" });
  dismiss.appendChild(_closeGlyph());
  dismiss.addEventListener("click", function () {
    try { localStorage.setItem("nasportal.iosInstallHint.dismissed", "1"); } catch (e) {}
    hint.remove();
  });
  hint.appendChild(dismiss);
  document.body.appendChild(hint);
}

// Tiny inline SVG glyphs for the install hint so it doesn't need
// an icon font or extra asset. currentColor inherits the hint's
// text color.
function _shareGlyph() {
  var ns = "http://www.w3.org/2000/svg";
  var svg = document.createElementNS(ns, "svg");
  svg.setAttribute("width", "16");
  svg.setAttribute("height", "16");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  var path1 = document.createElementNS(ns, "path");
  path1.setAttribute("d", "M12 3v12");
  svg.appendChild(path1);
  var path2 = document.createElementNS(ns, "path");
  path2.setAttribute("d", "M7 8l5-5 5 5");
  svg.appendChild(path2);
  var box = document.createElementNS(ns, "path");
  box.setAttribute("d", "M5 12v6a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-6");
  svg.appendChild(box);
  return svg;
}
function _closeGlyph() {
  var ns = "http://www.w3.org/2000/svg";
  var svg = document.createElementNS(ns, "svg");
  svg.setAttribute("width", "14");
  svg.setAttribute("height", "14");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "2");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("aria-hidden", "true");
  var l1 = document.createElementNS(ns, "line");
  l1.setAttribute("x1", "6"); l1.setAttribute("y1", "6"); l1.setAttribute("x2", "18"); l1.setAttribute("y2", "18");
  svg.appendChild(l1);
  var l2 = document.createElementNS(ns, "line");
  l2.setAttribute("x1", "18"); l2.setAttribute("y1", "6"); l2.setAttribute("x2", "6"); l2.setAttribute("y2", "18");
  svg.appendChild(l2);
  return svg;
}