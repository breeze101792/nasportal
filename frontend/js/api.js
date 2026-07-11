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