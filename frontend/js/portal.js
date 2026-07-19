// Portal home: search bar + grouped app grid (read-only view).
let homeLayout = "grouped"; // "grouped" (a section per group) | "compact" (one grid, grouped sort, per-card label) | "flow" (one continuous grid)
let showResolvedKind = false; // debug toggle: surface the resolver's URL-kind on each card
let openAppsInNewTab = false; // click behavior: true → target=_blank on cards, false → target=_self
let homeAuthed = false; // cached auth state — gates the group drag handles
let homeApps = []; // last-rendered app list, for in-memory reorder on drop

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
  homeLayout = ["grouped", "compact", "flow"].includes(settings.home_layout) ? settings.home_layout : "grouped";
  showResolvedKind = settings.show_resolved_kind === true;
  openAppsInNewTab = settings.open_apps_in_new_tab === true;
  homeAuthed = !!auth.authed;

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
  homeApps = appsData.apps || [];
  renderApps(homeApps);
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
      block.appendChild(groupTitleEl(group, homeAuthed, /*compact*/ true));
      const cards = el("div", { class: "group-cards" });
      for (const a of items) cards.appendChild(card(a, false));
      block.appendChild(cards);
      root.appendChild(block);
    }
    return;
  }

  // Grouped: a titled section per group, stacked top to bottom. Authed
  // visitors get a drag handle on each title so they can reorder whole
  // group blocks; the handle is suppressed for guests (the home page is
  // public — only signed-in admins can edit).
  for (const [group, items] of groups) {
    root.appendChild(groupTitleEl(group, homeAuthed, false));
    const grid = el("div", { class: "grid" });
    for (const a of items) grid.appendChild(card(a, false));
    root.appendChild(grid);
  }
}

// Group-title row for the home page. When the visitor is authed, the
// title gets a 6-dot drag handle (and the row becomes draggable) so
// the whole group block can be reordered. Guests see a plain title —
// no handle, no drag affordance. In compact mode the trailing hairline
// is suppressed (the title sits inside an inline group block, not a
// full-width section).
function groupTitleEl(g, canDrag, compact) {
  const cls = "group-title" + (canDrag ? " draggable" : "") + (compact ? " compact" : "");
  const title = el("div", { class: cls, "data-group": g,
    draggable: canDrag ? "true" : "false" });
  if (canDrag) {
    const handle = el("span", { class: "drag-handle", "aria-label": "Drag to reorder group", title: "Drag to reorder group", draggable: "true" });
    for (let i = 0; i < 6; i++) handle.appendChild(el("span", { class: "dot" }));
    title.appendChild(handle);
    wireGroupDrag(title);
  }
  title.appendChild(document.createTextNode(g));
  return title;
}

// ---- group drag-and-drop (home page) ----
// Authed visitors can drag a group's title onto another group's title
// to swap the whole block. Mirrors the /app page's group drag so the
// behavior is consistent across both views.
let _homeGroupSource = null;

function wireGroupDrag(titleEl) {
  titleEl.addEventListener("dragstart", onHomeGroupDragStart);
  titleEl.addEventListener("dragover", onHomeGroupDragOver);
  titleEl.addEventListener("drop", onHomeGroupDrop);
  titleEl.addEventListener("dragend", onHomeGroupDragEnd);
  titleEl.addEventListener("dragleave", onHomeGroupDragLeave);
}

function onHomeGroupDragStart(e) {
  // Only the handle starts a drag. The handle is a <span> with six
  // child <span class="dot"> elements — when the user grabs a dot,
  // e.target is the dot (not the handle), so we look for the nearest
  // ancestor with the drag-handle class.
  const handle = e.target.closest(".drag-handle");
  if (!handle || !e.currentTarget.contains(handle)) {
    e.preventDefault();
    return;
  }
  const title = e.currentTarget;
  _homeGroupSource = title.dataset.group;
  e.dataTransfer.effectAllowed = "move";
  e.dataTransfer.setData("text/plain", "group:" + _homeGroupSource);
  title.classList.add("dragging");
}

function onHomeGroupDragOver(e) {
  e.preventDefault();
  e.dataTransfer.dropEffect = "move";
  const title = e.currentTarget;
  if (title.dataset.group !== _homeGroupSource) title.classList.add("drop-target");
}

function onHomeGroupDragLeave(e) {
  if (e.currentTarget === e.target) e.currentTarget.classList.remove("drop-target");
}

function onHomeGroupDrop(e) {
  e.preventDefault();
  const targetTitle = e.currentTarget;
  const targetGroup = targetTitle.dataset.group;
  targetTitle.classList.remove("drop-target");
  if (!_homeGroupSource || !targetGroup || _homeGroupSource === targetGroup) return;
  // Collect the source group's apps (in current `homeApps` order).
  const srcBlock = homeApps.filter((a) => (a.group || "Ungrouped") === _homeGroupSource);
  if (!srcBlock.length) return;
  // Remove from the in-memory list, then re-insert before the target
  // group's first app. Apps not in either group keep their relative
  // order, which is the right outcome for the rest of the home view.
  const rest = homeApps.filter((a) => (a.group || "Ungrouped") !== _homeGroupSource);
  const newDstStart = rest.findIndex((a) => (a.group || "Ungrouped") === targetGroup);
  if (newDstStart < 0) {
    // Target group vanished (shouldn't happen — we just saw its title) — bail.
    return;
  }
  homeApps = rest.slice(0, newDstStart).concat(srcBlock, rest.slice(newDstStart));
  // Reassign dense order so the persisted value matches the new layout.
  persistHomeOrder();
}

function onHomeGroupDragEnd(e) {
  document.querySelectorAll("#groups .group-title").forEach((t) => {
    t.classList.remove("dragging", "drop-target");
  });
  _homeGroupSource = null;
}

// Reassign dense order 0..N-1 across the in-memory list and POST it
// to the server. The same endpoint the /app page uses — order is the
// unified sort key for both views. On failure, alert and reload from
// the server so the UI snaps back to server truth.
function persistHomeOrder() {
  for (let i = 0; i < homeApps.length; i++) homeApps[i].order = i;
  const items = homeApps.map((a) => ({ id: a.id, order: a.order }));
  // Optimistic re-render so the drop animation feels instant.
  renderApps(homeApps);
  api.post("/api/apps/bulk/order", { items }).catch(async (err) => {
    alert("Reorder failed: " + (err.message || "error") + " — reloading.");
    try {
      const fresh = await api.get("/api/apps/resolved");
      homeApps = fresh.apps || [];
      renderApps(homeApps);
    } catch (_) { /* network down — leave the optimistic order visible */ }
  });
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