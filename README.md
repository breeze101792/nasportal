# NAS Portal

A personal NAS start page / web portal: a multi-engine search bar, an
app/bookmark grid with drag-and-drop adding and auto-scraped metadata,
ping/health checks with sorting and grouping, and a settings page. Viewing the
portal is public; editing is login-gated (Heimdall-style).

- **Backend:** Python + Flask, a small JSON REST API under `/api`.
- **Frontend:** plain HTML/CSS/JS — no build step, no framework.
- **State:** JSON files in `config/` (runtime state, gitignored except a `.gitkeep`).

## Run

```bash
./start.sh
# open http://localhost:8000
```

`start.sh` creates a Python virtualenv at `.venv` (installing `requirements.txt`)
the first time, and reinstalls whenever `requirements.txt` changes, then starts
the Flask server. Options:

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
  SearXNG). Each engine URL uses a `%s` placeholder replaced with your encoded
  query and opened in a new tab. Engines and the default are editable in Settings.

**App grid**
- App/bookmark cards showing an icon (with a letter fallback when none is set)
  and title; cards open the target URL in a new tab.
- Two home layouts, chosen in Settings: **Grouped** (a titled section per group)
  or **Flow** (one continuous grid).

**Adding apps**
- Add manually via a form (title, URL, icon, group, description).
- Drag-and-drop a browser URL onto the page to auto-scrape the target's title,
  favicon, and description and pre-fill the form. A "Fetch title/icon from URL"
  button in the form does the same for a typed URL.

**Ping & status**
- On-demand ping probes each app URL (HEAD, falling back to a streaming GET) and
  reports online/offline, HTTP status, and latency in milliseconds. "Online"
  means any response with status < 500. Pings run automatically when the Apps
  page loads (when logged in) and on demand via the Ping button.
- Sort apps by manual order, name, status, or group. Manual-order and group
  sorts also group apps under titled sections.

**Multi-select & bulk actions**
- Per-app checkboxes, a select-all toggle (with indeterminate partial state),
  and shift-click range select.
- Bulk set group and bulk delete across the current selection.

**Settings**
- Portal: title, wallpaper URL, home layout (grouped/flow).
- Appearance: theme (light / dark / system) and portal width (50-100%, with a
  live preview while dragging).
- Search engines: add, rename, remove, and pick the default. Each engine URL
  must contain `%s` and use `http(s)://`.
- Change password: requires the current password.

**Authentication & access (Heimdall-style)**
- First-run setup mode: before an admin password is set, the Settings page shows
  only the "set admin password" form and all other writes are refused.
- Viewing is public; editing is gated. Anyone can view the portal and the app
  grid, and the public settings JSON the portal needs to render (theme,
  wallpaper, title). The Settings *page* itself (`/settings`) is login-gated —
  guests are bounced to `/login`. Adding, editing, deleting, bulk actions,
  scraping, and live ping all require login.
- Single shared admin password, stored hashed. Session cookie is
  `HttpOnly` + `SameSite=Lax`; the `Secure` flag is opt-in for TLS deployments.

**State & storage**
- All mutable state lives as JSON files under `config/`: `settings.json`,
  `apps.json`, `auth.json`, and `secret.json` (the persisted session secret).
  Writes are atomic (temp file + `os.replace`) and cross-process locked
  (`fcntl.flock`).
- `config/` is gitignored except for a `.gitkeep`, so a fresh checkout boots with
  seeded defaults and a mounted volume carries state across containers.

## Tests

Install dev dependencies once (the backend tests need only pytest; the browser
tests also need Playwright + Chromium):

```bash
.venv/bin/pip install -r requirements-dev.txt
```

Backend API tests run in-process via Flask's test client (no socket, no
browser):

```bash
.venv/bin/python -m pytest tests/test_api.py -m 'not e2e' -q
```

End-to-end page tests drive the real server with Chromium via Playwright. The
system Chromium at `/usr/bin/chromium` is auto-detected; otherwise run
`playwright install chromium` once:

```bash
.venv/bin/python -m pytest tests/test_pages.py -m e2e -q
```

Full suite:

```bash
.venv/bin/python -m pytest -q
```

All test state is isolated to a temp config dir that is wiped before each test,
so every test starts in first-run setup mode — no real `config/` is touched.

**Sandbox note:** the e2e suite launches Chromium and binds a real listening
socket, both of which are blocked inside the Claude Code OS sandbox. On a normal
developer machine the e2e tests run fine via the project `.venv`. Inside this
harness you must disable the OS sandbox to run the e2e (or full) suite.

## Layout

```
backend/     Flask app factory, routes (auth/apps/settings), services, storage
frontend/    HTML/CSS/JS — no build step
  css/       style.css
  js/        api.js (shared helpers), theme.js, portal.js, apps.js, settings.js, login.js
config/      JSON state — settings.json, apps.json, auth.json, secret.json (gitignored except .gitkeep)
tests/       pytest backend API tests + Playwright browser E2E tests
start.sh     venv setup + launcher
requirements.txt        Flask, requests, beautifulsoup4
requirements-dev.txt    pytest, playwright
pytest.ini              testpaths + e2e marker
```