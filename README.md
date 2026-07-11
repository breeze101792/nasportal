# NAS Portal

A personal NAS start page / web portal: multi-engine search bar, app/bookmark
grid with drag-and-drop adding and auto-scraped metadata, ping/health checks,
sorting and grouping, and a settings page. Login-gated editing; viewing is public
(Heimdall-style).

- **Backend:** Python + Flask, a small JSON REST API.
- **Frontend:** plain HTML/CSS/JS — no build step.
- **State:** JSON files in `config/` (runtime state, gitignored).

## Run

```bash
./start.sh
# open http://localhost:8000
```

`start.sh` creates a Python virtualenv at `.venv` (installing `requirements.txt`)
the first time, then starts the server. It prints `--help` for options:

```bash
./start.sh --help
```

Listen on a different port:

```bash
./start.sh --port 9000
# or:  PORT=9000 ./start.sh
```

Store config somewhere other than `./config`:

```bash
./start.sh --config /etc/nasportal
# or:  NASPORTAL_CONFIG=/etc/nasportal ./start.sh
```

Debug/reload mode: `FLASK_DEBUG=1 ./start.sh`.

On first run there is no password set, so the portal runs in **setup mode**:
open `/settings` to set the initial admin password. After that, editing apps and
settings requires a login; viewing the portal stays public (live pinging of app
URLs is also login-gated, since it's a server-side request to those URLs).

## TLS / secure sessions

The session cookie is `HttpOnly` + `SameSite=Lax` by default and the cookie is
**not** marked `Secure`, so it works over plain HTTP on a LAN. If you serve the
portal behind a TLS-terminating reverse proxy, set `NASPORTAL_SECURE_COOKIE=1`
so the session cookie is only sent over HTTPS:

```bash
NASPORTAL_SECURE_COOKIE=1 ./start.sh
```

## Tests

Install dev dependencies once (the backend tests need only pytest; the browser
tests also need Playwright + Chromium):

```bash
.venv/bin/pip install -r requirements-dev.txt
```

Backend API tests run in-process via Flask's test client (no socket, no
browser):

```bash
.venv/bin/python -m pytest -m "not e2e"
```

End-to-end page tests drive the real server with Chromium via Playwright. The
system Chromium at `/usr/bin/chromium` is auto-detected; otherwise run
`playwright install chromium` once to use Playwright's bundled browser:

```bash
.venv/bin/python -m pytest -m e2e
```

Run everything:

```bash
.venv/bin/python -m pytest
```

All test state is isolated to a temp config dir that is wiped before each test,
so every test starts in first-run setup mode — no real `config/` is touched.

## Layout

```
backend/    Flask app, routes, services
frontend/   HTML/CSS/JS (no build)
config/     JSON state — settings.json, apps.json, auth.json, secret.json
tests/      pytest (backend API) + Playwright (browser E2E)
start.sh    env setup + launcher
```