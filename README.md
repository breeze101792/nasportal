# NAS Portal

A personal NAS start page / web portal: a multi-engine search bar, an
app/bookmark grid with drag-and-drop adding and auto-scraped metadata,
ping/health checks with sorting and grouping, an in-browser network
scanner that discovers local HTTP services, and per-app URL lists that
auto-pick the right URL for each visitor's network. Viewing the portal
is public; editing is login-gated (Heimdall-style).

- **Backend:** Python + Flask, a small JSON REST API under `/api`.
- **Frontend:** plain HTML/CSS/JS — no build step, no framework.
- **State:** JSON files in `config/` (runtime state, gitignored except a `.gitkeep`).

## Run

```bash
./start.sh
# open http://localhost:8000
```

`start.sh` creates a Python virtualenv at `.venv_<hostname>` (installing
`requirements.txt`) the first time, and reinstalls whenever
`requirements.txt` changes, then starts the Flask server. Options:

```bash
./start.sh --help
```

```
Options:
  -p, --port PORT      Port to listen on (default: 8000)
  -c, --config DIR     Config directory for JSON state (default: ./config)
  -h, --help           Show this help and exit

Environment variables (options take precedence):
  PORT                       Port to listen on (default: 8000)
  NASPORTAL_CONFIG           Config directory (default: ./config)
  NASPORTAL_SECURE_COOKIE    Set to "1" to mark the session cookie Secure (HTTPS)
  FLASK_DEBUG                Set to "1" to enable debug / auto-reload mode
```

Listen on a different port (`--port 9000` and `--port=9000` both work):

```bash
./start.sh --port 9000
# or:  PORT=9000 ./start.sh
```

Store config somewhere other than `./config`:

```bash
./start.sh --config /etc/nasportal
# or:  NASPORTAL_CONFIG=/etc/nasportal ./start.sh
```

Debug/reload mode: `FLASK_DEBUG=1 ./start.sh`. The server binds `0.0.0.0:${PORT}`.

On first run there is no admin password set, so the portal runs in **setup
mode**: open `/settings` to set the initial password. After that, editing apps
and settings requires a login; viewing the portal stays public. Live pinging of
app URLs is also login-gated, since it is a server-side request to those URLs.

## TLS / secure sessions

The session cookie is `HttpOnly` + `SameSite=Lax` by default and is **not**
marked `Secure`, so it works over plain HTTP on a LAN. The session secret is
generated on first boot (`secrets.token_hex(32)`) and persisted to
`config/secret.json`, so sessions survive restarts. If you serve the portal
behind a TLS-terminating reverse proxy, set `NASPORTAL_SECURE_COOKIE=1` so the
cookie is only sent over HTTPS:

```bash
NASPORTAL_SECURE_COOKIE=1 ./start.sh
```

## Features

**Search**
- Multi-engine search bar with a dropdown of engines (defaults: Google, Bing,
  DuckDuckGo, SearXNG). Each engine URL uses a `%s` placeholder replaced
  with your encoded query and opened in a new tab. Engines and the default
  are editable in Settings.

**App grid**
- App/bookmark cards showing an icon (stored icon, or a live-fetched
  favicon, or a letter fallback) and title; cards open the target URL.
- Each app stores a **list of URLs** (one per line in the form). The portal
  picks the best URL for every visitor based on their source IP — see
  *Network awareness* below.
- Three home layouts, chosen in Settings:
  - **Grouped** — a titled section per group, stacked top to bottom.
  - **Compact** — each group is an inline labeled block; small groups
    share rows so a 1- or 2-app group doesn't waste a full width.
  - **Flow** — one continuous grid; the group label is shown on each card.
- A per-portal debug toggle (`show_resolved_kind`) adds a small badge to
  each card explaining why its URL was chosen (local network / via
  translation / public domain / public IP / other network) — useful for
  diagnosing IP-translation issues.
- A `open_apps_in_new_tab` toggle (Settings → Portal) controls whether
  clicking a card opens the app in a new tab (portal stays in the
  background) or navigates the same tab.

**Adding apps**
- Add manually via a form (title, URL list, icon, group, description).
- Drag-and-drop a browser URL onto the page to auto-scrape the target's
  title, favicon, and description and pre-fill the form. A "Fetch
  title/icon from URL" button in the form does the same for a typed URL.
- Each app's icon falls back to a live fetch of `/api/favicon?url=…` when
  the admin leaves the icon field blank. Resolved favicon URLs are cached
  in `localStorage` so the same app's icon is reused across the home, the
  `/app` management view, and reloads.

**Network awareness (per-visitor URL resolution)**

The portal detects the host machine's local IPv4 networks
(`/proc/net/route` on Linux, `getsockname` + `/24` fallback elsewhere) and
uses them to pick the best URL for every app, for every visitor:

1. **Same-network IP** — if any of the app's URLs has a host on the same
   local network as the visitor, that URL wins. If an `ip_translation`
   entry maps one of the app's IPs onto the visitor's network, the
   translated URL is also tier 1.
2. **Domain** — the first URL with a hostname host.
3. **Public IP** — a routable IPv4 on no detected local network.
4. **Other-network IP** — an IP on a local network the visitor is *not* on
   (a tunneled / admin-only address kept for completeness).
5. **First URL in the list** as a last-resort fallback.

The `ip_translation` table (Settings → IP Translation) maps a known IP to
its equivalent on another network — e.g. the same NAS showing up as
`192.168.1.10` on Wi-Fi and `10.147.x.x` over a tunnel. Translation is
**single-level only**: a chain like `A→B, B→C` is not followed.

A `show_untranslatable` toggle (Settings → Portal, default ON) controls
whether the home page hides apps that have no reachable URL for the
current visitor. The `/api/apps/resolved` endpoint honours this; the
`/api/apps` endpoint never filters, so the `/app` management view always
shows every app.

**Network Scan (browser-side)**

Settings → Network Scan probes your local network for HTTP services and
bulk-adds the hits to `/app`. The actual probing runs in **your browser**
(using `fetch(..., { mode: "no-cors" })`), not the server — so it sees
what your machine sees, and the server's network is not exposed. Each
candidate is tried as `http://` first and falls back to `https://`. Target
can be a single IP, a CIDR, or a range (`10.0.0.1-10.0.0.254`). Port
lists accept single ports and ranges (`80, 443, 5000-5010, 8080`),
capped at 1024 unique ports and 4096 total candidates per scan. The
server's `/api/scan/expand` endpoint validates the target (rejects
loopback, multicast, link-local, and oversized ranges) and produces the
candidate list; the browser does the probing.

The server also exposes `GET /api/networks/local` (public) which returns
the host's detected local networks as CIDR strings — used by the scan
page to pre-populate the target dropdown.

**Ping & status**
- On-demand ping probes each app URL (HEAD, falling back to a streaming
  GET) and reports online/offline, HTTP status, and latency in
  milliseconds. "Online" means any response with status < 500. Pings run
  automatically when the Apps page loads (when logged in) and on demand
  via the Ping button.
- Pings target the **resolved URL** (the URL the current visitor would
  actually use), not the first raw entry in the URL list.
- Sort apps by manual order, name, status, or group. Manual-order and
  group sorts also group apps under titled sections.

**Multi-select & bulk actions**
- Per-app checkboxes, a select-all toggle (with indeterminate partial
  state), and shift-click range select.
- Bulk set group and bulk delete across the current selection.

**Settings**
- **Portal:** title, wallpaper URL, home layout (grouped / compact /
  flow), background color override, `show_untranslatable`,
  `show_resolved_kind`, `open_apps_in_new_tab`.
- **Appearance:** theme (light / dark / system), background color
  (free-form CSS color: hex, rgb/rgba, hsl/hsla, named keywords, or
  `transparent`), and portal width (50–100%, with a live preview while
  dragging).
- **Search engines:** add, rename, remove, and pick the default. Each
  engine URL must contain `%s` and use `http(s)://`.
- **IP Translation:** ordered `from → to` IPv4 pairs, single-level.
- **Network Scan:** target, ports, scheme, start/stop, results list with
  bulk-add back to apps.
- **Change password:** requires the current password.

**Authentication & access (Heimdall-style)**
- First-run setup mode: before an admin password is set, the Settings
  page shows only the "set admin password" form and all other writes are
  refused.
- Viewing is public; editing is gated. Anyone can view the portal and
  the app grid, and the public settings JSON the portal needs to render
  (theme, wallpaper, title). The Settings *page* itself (`/settings`) is
  login-gated — guests are bounced to `/login`. Adding, editing,
  deleting, bulk actions, scraping, live ping, and scan-expand all
  require login.
- Single shared admin password, stored hashed. Session cookie is
  `HttpOnly` + `SameSite=Lax`; the `Secure` flag is opt-in for TLS
  deployments.

**State & storage**
- All mutable state lives as JSON files under `config/`:
  `settings.json`, `apps.json`, `auth.json`, and `secret.json` (the
  persisted session secret). Writes are atomic (temp file + `os.replace`)
  and cross-process locked (`fcntl.flock`).
- `config/` is gitignored except for a `.gitkeep`, so a fresh checkout
  boots with seeded defaults and a mounted volume carries state across
  containers.

## Tests

Install dev dependencies once (the backend tests need only pytest; the
browser tests also need Playwright + Chromium):

```bash
.venv_<hostname>/bin/pip install -r requirements-dev.txt
```

Backend API tests run in-process via Flask's test client (no socket, no
browser):

```bash
.venv_<hostname>/bin/python -m pytest tests/test_api.py -m 'not e2e' -q
```

End-to-end page tests drive the real server with Chromium via
Playwright. The system Chromium at `/usr/bin/chromium` is auto-detected;
otherwise run `playwright install chromium` once:

```bash
.venv_<hostname>/bin/python -m pytest tests/test_pages.py -m e2e -q
```

Full suite:

```bash
.venv_<hostname>/bin/python -m pytest -q
```

All test state is isolated to a temp config dir that is wiped before
each test, so every test starts in first-run setup mode — no real
`config/` is touched.

**Sandbox note:** the e2e suite launches Chromium and binds a real
listening socket, both of which are blocked inside the Claude Code OS
sandbox. On a normal developer machine the e2e tests run fine via the
project venv. Inside this harness you must disable the OS sandbox to run
the e2e (or full) suite.

## Layout

```
backend/                       Flask app factory, routes, services, storage
  app.py                       create_app() — blueprints, page routes, static
  auth.py                      password hashing, login_required, setup mode
  config.py                    paths + default JSON seeds
  storage.py                   atomic load_json / save_json, fcntl.flock
  routes/
    auth.py                    /api/auth/{check,login,logout,password}
    apps.py                    /api/apps CRUD, /ping, /scrape, /parse,
                               /favicon, /apps/resolved
                               + bulk /apps/bulk/{delete,group,order}
    settings.py                /api/settings GET + PUT (validated)
    scan.py                    /api/networks/local, /api/scan/expand
  services/
    networks.py                local-network detection, URL parser, 4-tier
                               resolver, ip_translation
    pinger.py                  HEAD-then-GET HTTP health check
    scraper.py                 title/description/favicon scrape (best-effort)
frontend/                      HTML/CSS/JS — no build step
  css/style.css
  js/
    api.js                     fetch wrapper, shared el()/safeUrl() helpers,
                               faviconCache, resolveIcon, renderTopLinks
    theme.js                   <head>-loaded theme bootstrap (no flash)
    portal.js                  home: search, grouped/compact/flow grid
    apps.js                    /app: CRUD form, drag-to-reorder, ping,
                               multi-select, scrape, URL list
    settings.js                /settings: auto-save editors, tabs, translation
    scan.js                    /settings → Network Scan: browser-side probing
    login.js                   /login: form + first-run setup
config/                        JSON state — settings.json, apps.json,
                               auth.json, secret.json
                               (gitignored except .gitkeep)
tests/                         pytest backend API tests + Playwright browser
                               E2E tests (test_api.py / test_pages.py)
start.sh                       venv setup + launcher
requirements.txt               Flask, requests, beautifulsoup4
requirements-dev.txt           pytest, playwright
pytest.ini                     testpaths + e2e marker
```
