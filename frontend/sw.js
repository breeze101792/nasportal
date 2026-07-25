// nasportal service worker — installable PWA offline shell.
// Strategy:
//   - precache the static shell at install (same-origin only)
//   - same-origin GETs: stale-while-revalidate, so the app opens
//     instantly and updates silently in the background
//   - everything else (api calls, app launches, cross-origin): pass
//     straight through to the network. We never cache /api/* because
//     the JSON is the live source of truth.
//   - never intercept the service worker file itself or the manifest
//     (the browser re-fetches them when the SW updates)
//
// No versioning: the SW is intentionally tiny and static. When we
// ship a change that needs a cache bust, bump the install event's
// precache list — the new SW replaces the old one and the new
// precache wins. (Adding cache versioning is straightforward but
// adds a code path that has no current user.)
const PRECACHE = [
  "/",
  "/css/style.css",
  "/js/theme.js",
  "/js/api.js",
  "/js/portal.js",
  "/manifest.json",
  "/favicon.svg",
];
const SHELL_HOST = self.location.host;

self.addEventListener("install", (event) => {
  // Precache the static shell. addAll fails the whole install on any
  // miss, which is what we want: a broken install means the user
  // gets the previous SW (or no SW) rather than a half-cached app.
  event.waitUntil(
    caches.open("nasportal-shell-v1").then((cache) => cache.addAll(PRECACHE))
  );
  // Take over without waiting for old tabs to close — a long-running
  // portal should pick up the new SW promptly.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  // Sweep caches we no longer use. Right now we only have one; this
  // loop is here so future versions can bump the cache name without
  // leaving the old one behind.
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== "nasportal-shell-v1").map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;          // never cache mutations
  const url = new URL(req.url);
  if (url.host !== SHELL_HOST) return;        // external: pass through
  if (url.pathname === "/sw.js" || url.pathname === "/manifest.json") return;
  if (url.pathname.startsWith("/api/")) return; // JSON is live; never cache
  // Same-origin GET: stale-while-revalidate. Cache hit → respond
  // immediately, then re-fetch in the background to refresh. Cache
  // miss → fetch, then put a copy in the cache for next time.
  event.respondWith(
    caches.match(req).then((cached) => {
      const network = fetch(req)
        .then((res) => {
          // Only cache successful basic responses; opaque (0) and
          // errors would poison the cache and break offline loads.
          if (res && res.status === 200 && res.type === "basic") {
            const copy = res.clone();
            caches.open("nasportal-shell-v1").then((cache) => cache.put(req, copy));
          }
          return res;
        })
        .catch(() => cached); // offline + cache miss: best-effort fallback
      return cached || network;
    })
  );
});
