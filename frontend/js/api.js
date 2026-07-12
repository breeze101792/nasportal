// Tiny fetch wrapper for the JSON API.
// - JSON in/out
// - on 401 (login_required) it bounces to /login unless we're already there,
//   preserving the page that triggered it via ?next=
const api = (function () {
  async function call(path, opts = {}) {
    const res = await fetch(path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json", ...(opts.headers || {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
      credentials: "same-origin",
    });
    if (res.status === 401 && !location.pathname.startsWith("/login")) {
      const next = encodeURIComponent(location.pathname + location.search);
      location.href = "/login?next=" + next;
      throw new Error("login_required");
    }
    let data = null;
    try { data = await res.json(); } catch (e) { /* non-json */ }
    if (!res.ok) throw Object.assign(new Error((data && data.error) || "request_failed"), { status: res.status, error: data && data.error });
    return data;
  }
  return {
    get: (p) => call(p),
    post: (p, body) => call(p, { method: "POST", body }),
    put: (p, body) => call(p, { method: "PUT", body }),
    del: (p) => call(p, { method: "DELETE" }),
  };
})();

// Fetch auth state once; caches for the page lifetime.
let _authCache = null;
async function authState() {
  if (_authCache) return _authCache;
  _authCache = await api.get("/api/auth/check");
  return _authCache;
}

// Decode ?next= safely to avoid open-redirect (only allow local paths).
function nextPath() {
  const n = new URLSearchParams(location.search).get("next");
  if (n && n.startsWith("/") && !n.startsWith("//")) return n;
  return "/";
}

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on") && typeof v === "function") node.addEventListener(k.slice(2), v);
    else if (v != null) node.setAttribute(k, v);
  }
  for (const c of children) {
    if (c == null) continue;
    node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
  }
  return node;
}

// Set text safely (XSS-safe) on an existing node, replacing its contents.
function setText(node, text) {
  node.replaceChildren(document.createTextNode(text == null ? "" : String(text)));
}

// Only allow http(s) link targets (defends against javascript:/data: URLs an
// admin might store). Falls back to "#" so the link is a no-op, not a sink.
function safeUrl(u) {
  return /^(https?:)?\/\//i.test(String(u || "")) ? u : "#";
}

// Escape backslash and double-quote before interpolating an admin-set value
// into a CSS url("...") string, so a stray quote can't break out and inject CSS.
function cssEsc(s) {
  return String(s || "").replace(/["\\]/g, "\\$&");
}

// Apply the portal content width (a percent of the viewport) from settings.
// Clamped to 50–100; falls back to 80%. Returns the clamped integer used.
function applyPortalWidth(p) {
  const n = Math.max(50, Math.min(100, Math.round(Number(p) || 80)));
  document.documentElement.style.setProperty("--portal-width", n + "%");
  return n;
}

// ---- top-nav icons + renderer ----
// All pages share the same top-right idiom: a back-arrow (where applicable)
// and a gear pointing to the other settings page, plus a text "Logout".
// Icons are Lucide glyphs rendered as inline SVG (namespaced; currentColor
// inherits the .toplinks a muted color). Never innerHTML — every node is
// built with createElement / createElementNS so the class string can never
// contain untrusted data.

function navIcon(name) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("width", "18");
  svg.setAttribute("height", "18");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", "currentColor");
  svg.setAttribute("stroke-width", "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");
  if (name === "gear") {
    const circle = document.createElementNS(ns, "circle");
    circle.setAttribute("cx", "12"); circle.setAttribute("cy", "12"); circle.setAttribute("r", "3");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z");
    svg.appendChild(circle);
    svg.appendChild(path);
  } else if (name === "arrow-left") {
    const line = document.createElementNS(ns, "line");
    line.setAttribute("x1", "19"); line.setAttribute("y1", "12");
    line.setAttribute("x2", "5");  line.setAttribute("y2", "12");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M12 19l-7-7 7-7");
    svg.appendChild(line);
    svg.appendChild(path);
  }
  return svg;
}

function _iconLink(href, label, iconName) {
  const a = el("a", { class: "icon-link", href, "aria-label": label, title: label });
  a.appendChild(navIcon(iconName));
  return a;
}

function _logoutLink() {
  return el("a", { href: "#", text: "Logout",
    onclick: async (e) => { e.preventDefault(); await api.post("/api/auth/logout"); location.href = "/"; } });
}

function _loginLink() {
  const next = encodeURIComponent(location.pathname + location.search);
  return el("a", { href: "/login?next=" + next, text: "Login" });
}

// Render the standard top-right nav for the current page.
//   currentPage: "home" | "settings" | "app"
//   authed:      boolean
// Each page knows its own shape:
//   - home:     [ gear → /settings (or /login?next=/settings for guests) ]
//   - settings: [ arrow-left → /,  gear → /app,  Logout ]   (or Login if guest)
//   - app:      [ arrow-left → /,  gear → /settings, Logout ]  (or Login if guest)
function renderTopLinks(currentPage, authed) {
  const links = document.getElementById("toplinks");
  links.replaceChildren();
  if (currentPage === "home") {
    const href = authed ? "/settings" : "/login?next=" + encodeURIComponent("/settings");
    links.appendChild(_iconLink(href, "Settings", "gear"));
    return;
  }
  // Non-home pages: back-arrow + gear to the OTHER settings page + auth action.
  links.appendChild(_iconLink("/", "Home", "arrow-left"));
  const otherHref = currentPage === "settings" ? "/app" : "/settings";
  const otherLabel = currentPage === "settings" ? "App settings" : "Settings";
  links.appendChild(_iconLink(otherHref, otherLabel, "gear"));
  links.appendChild(authed ? _logoutLink() : _loginLink());
}