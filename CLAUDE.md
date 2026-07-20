# CLAUDE.md — nasportal

Personal NAS start-page web portal: Flask JSON API under `/api` + static vanilla-JS frontend, JSON state in `config/`, no build step.

## Run & test

```bash
./start.sh                       # start on :8000 (creates .venv_<hostname>, installs requirements.txt)
./start.sh --port 9000 --config /etc/nasportal
.venv_$(hostname)/bin/python -m pytest tests/test_api.py -m 'not e2e' -q   # backend only
.venv_$(hostname)/bin/python -m pytest tests/test_pages.py -m e2e -q       # e2e only
.venv_$(hostname)/bin/python -m pytest -q                                  # full suite
```

**e2e sandbox caveat:** the e2e suite launches Chromium and binds a real listening socket (`werkzeug.make_server`). Both are blocked by the Claude Code OS sandbox, so `-m e2e` (and therefore the full suite) will not run cleanly in this harness — disable the OS sandbox (`dangerouslyDisableSandbox: true` / the `/sandbox` command) to run them here. On the user's normal machine they run fine via the project venv (with playwright + a chromium binary; `/usr/bin/chromium` is auto-detected).

Test state is isolated to a session temp dir wiped before each test, so every test starts in first-run setup mode; no real `config/` is touched.

## Architecture

- Flask app factory `create_app()` in `backend/app.py`; Flask's built-in static handling is disabled — assets are served via explicit rules. All API blueprints (`auth_bp`, `apps_bp`, `settings_bp`, `scan_bp`) mount under `url_prefix="/api"`. Page routes (`/`, `/app`, `/settings`, `/login`) serve HTML from `frontend/` via `send_from_directory`.
- JSON state lives in `config/` (`settings.json`, `apps.json`, `auth.json`, `secret.json`). `config/` is gitignored except `config/.gitkeep`. The config dir is `NASPORTAL_CONFIG` env or `REPO_ROOT/config` (`backend/config.py`).
- No build step. No framework, no bundler, no JS modules — plain `<script>` tags.

## Frontend conventions

- Vanilla JS. Build everything with `document.createElement` + `appendChild`/`replaceChildren`.
- **Never use `innerHTML`.** Use the `el(tag, attrs, ...children)` helper in `frontend/js/api.js`: the `text` attr sets `textContent` (XSS-safe), `on*` attrs with a function value bind via `addEventListener`, `class` -> `className`. For replacing a node's text use `setText(node, text)`.
- Use `safeUrl(u)` for any `href` — returns the URL only if it matches `/^(https?:)?\/\//i`, else `"#"` (defends against `javascript:`/`data:`).
- Use `cssEsc(s)` before interpolating admin-controlled values into CSS `url("...")` (escapes `"` and `\`).
- `theme.js` loads synchronously in `<head>` and applies the theme from `localStorage` before paint — no flash of wrong theme.
- Every page loads `css/style.css` + `js/theme.js` in `<head>`, then `js/api.js` + a page-specific script at the end of `<body>`. Shared chrome: `.brand` + `.toplinks` inside `<header class="top">`, populated in JS (`login.html` is the deliberate exception — a standalone `.login-card` with no shared header chrome).
- **Shared icon resolver:** `resolveIcon(app, placeholder)` in `api.js` is the single place that turns `app.icon` (or, when empty, a live `/api/favicon?url=…` call) into an `<img>` for both the portal home and the `/app` management view. Resolved favicon URLs are cached in `localStorage` (`nasportal.favicons`) with a `none` sentinel for "we tried, got nothing". Per-page in-flight dedup is via a module-level `_inflight` Map. **The placeholder must be mounted to its parent BEFORE calling `resolveIcon`** — `attachIcon` does `placeholder.replaceWith(img)` and bails if `parentNode` is null.
- **Network Scan** (`/settings` → Network Scan tab) is browser-driven. The page (`scan.js`) requests a candidate list from `POST /api/scan/expand` (server validates the target, expands a CIDR or IP range, caps at 1024 ports × 4096 hosts, rejects loopback/multicast/link-local), then probes each `http://…` candidate with `fetch(..., { mode: "no-cors" })` in batches of 16 with a per-scheme 800ms timeout, falling back to `https://` on failure. Hits are bulk-added to apps with `POST /api/apps` (concurrency 4). `scan.js` is wrapped in an IIFE so its `init` doesn't collide with `settings.js`'s; it waits for the `scan:init` custom event dispatched after the auth check, so it never runs for guests or in setup mode.

## Backend conventions

- **Validate ALL client input.** URL scheme allowlists: app urls/icons (`_valid_app_url`, `_valid_icon` in `backend/routes/apps.py`) and search-engine urls (`backend/routes/settings.py`) must be `http(s)://` (icons also allow `data:image/...`). Settings field validation uses named error codes (e.g. `invalid_portal_title`, `invalid_theme`, `invalid_portal_width`); see the inline per-field validation in `settings.py`. `background_color` is a free-form CSS color value validated against a strict regex (3/4/6/8-digit hex, `rgb`/`rgba`/`hsl`/`hsla` functional notation, or a small set of named keywords including `transparent`); empty string = no override.
- **Reads are public, mutations are `@login_required`** (view-public / edit-gated, Heimdall-style). `GET /api/apps`, `GET /api/apps/resolved`, `GET /api/settings`, `GET /api/auth/check`, `GET /api/networks/local`, and the HTML/static routes are public. All app mutations, `PUT /api/settings`, `PUT /api/auth/password`, `POST /api/apps/ping`, `POST /api/apps/bulk/*`, `POST /api/scrape`, and `POST /api/scan/expand` are login-gated. The `/settings` *page* is also login-gated at the frontend (guests bounce to `/login?next=/settings`); only the `GET /api/settings` JSON is public, because the portal/apps pages need it to render theme/wallpaper/title.
- **Live ping is login-gated** (SSRF defense — it's a server-side request to arbitrary app URLs).
- `backend/storage.py`: `save_json` writes atomically (`tempfile.mkstemp` in `CONFIG_DIR` then `os.replace`). `file_lock(name)` takes an `fcntl.flock(LOCK_EX)` on `config/<name>.lock` for cross-process read-modify-write serialization.
- `load_json` returns a `copy.deepcopy` of the shared module-level default when the file is absent — **never hand out the mutable default directly** (callers mutate the returned store in place; that was a real bug). `auth.json` is loaded with `strict=True` so a corrupt auth file raises instead of silently reverting to setup mode.
- First-run setup mode: `auth.setup_required()` is true when `password_hash` is empty; `POST /api/auth/login` then sets the password rather than verifying one.

## App storage shape: per-app URL list + 4-tier resolver

Apps store a `urls: ["https://…", "http://10.x.x.x:8989/…", …]` list (string, list, or newline/comma-separated — deduped on save; legacy structured fields `network_ips` / `domain` / `public_ip` / `scheme` / `port` / `path` are still *accepted* on write and synthesized on read for backward compat). The URL list is the admin's priority chain: when the resolver can't bucket a URL more specifically, the first one in the list wins.

The resolver (`backend/services/networks.py`, `resolve_url`) picks the best URL for each app per visitor using a fixed priority chain (no settings toggle):

1. **Same-network IP** (`kind=network`) — literal IPv4 host on the same local network as the visitor.
2. **Translation** (`kind=translated`) — `ip_translation[host]` is defined AND the translated IP is on the visitor's network. Single-level lookup only; no chain following.
3. **Domain** (`kind=domain`) — first URL with a hostname host.
4. **Public IP** (`kind=public_ip`) — routable IPv4 on no detected local network.
5. **Other-network IP** (`kind=other_network`) — IP on a local network the visitor is *not* on (tunneled / admin-only, kept for completeness).
6. **First URL in the list** (`kind=fallback`).
7. **Legacy `url` field** (`kind=legacy`).

Both `GET /api/apps` (the `/app` management view, never filters) and `GET /api/apps/resolved` (the public portal home, honours `show_untranslatable`) attach the resolver's result as `app.url` (the resolved URL string) and `app.resolved = {url, kind, host, port, scheme, path}` so the Open button on `/app` and the link on the home page point at the URL the visitor would actually use. `is_translatable` filters out apps with no reachable URL when `show_untranslatable` is off.

Local network detection (`get_local_networks`, cached per process): Linux reads `/proc/net/route` and skips the default-route line (`dest=0.0.0.0`) plus loopback — keeping the default route would synthesize a `0.0.0.0/0` entry that matches every IP and collapse "same-network" detection to "always yes". Other OSes fall back to a `getsockname` UDP probe with an assumed `/24`. Test override: `reset_local_networks_cache()` in `services/networks.py`.

## UX / JS patterns

- **Add form stays open after save:** on a successful add, `apps.js submitForm` clears title/url/icon/desc/id but leaves `#f-group` as-is (batch-entry convenience), shows "Saved. Add another, or Close when done.", and refocuses `#f-url`. Editing an existing app closes the form (and refreshes the in-memory form fields from the freshly-saved app so server-side normalization is reflected back).
- **Set "Saving..." synchronously before the `await`** in async save handlers, so the later "Saved" confirmation reflects the current save rather than a stale status (avoids a stale-status race). `settings.js autoSave` uses a `_savingCount` to avoid clobbering a later save's "Saving…" message with a previous save's success.
- Portal home (`portal.js`) is read-only. Three home layouts: `grouped` (titled section per group, stacked), `compact` (each group is an inline labeled block; small groups share rows so 1–2 apps don't waste a full width), and `flow` (one continuous grid with group label on each card), switched via `settings.home_layout`. The portal home uses `GET /api/apps/resolved` (filtered by `show_untranslatable`); `/app` uses `GET /api/apps` (never filtered, so the admin sees everything).
- 401 handling in `api.js`: redirects to `/login?next=<encoded path>` unless already on `/login`. `/login` and `/settings` setup use raw `fetch` (bypassing `api`) so a 401 there is handled inline. `nextPath()` only returns local `?next=` values (rejects protocol-relative `//`).
- The URL form on `/app` (`#f-url`) is a drag target: dropping a URL from a browser tab appends it as a new row. Internal row-reorder drags carry `text/plain=url-row`; external URL drags carry `text/uri-list`. The row's drop handler routes external drops into the append flow (drops on a child row don't bubble, so each row handles it itself).
- The `/app` row drag-to-reorder is only enabled in Manual (`order`) sort mode for authed users — in other sorts the order is computed from name/status/group, so a manual reorder would be silently overwritten on the next sort. Drops across group boundaries are restricted when the `Grouped` toggle is on; group titles are themselves draggable in Manual mode to move an entire group block.

## Git / commit workflow

- The user asks to commit; commit on `main`.
- The Claude sandbox injects device-dotfiles (`.bash_profile`, `.bashrc`, `.gitconfig`, `.zshrc`, `.profile`, `.zprofile`, `.ripgreprc`, `.gitmodules`, `.idea`, `.vscode`, `.mcp.json`, etc.) as **character-special nodes** that git cannot add. Stage real files explicitly (e.g. `git add backend/ frontend/ tests/`) and ignore those device nodes — do not `git add -A`/`git add .`.
- The repo may report **"dubious ownership"**; fix with `git config --global --add safe.directory <repo path>` before committing.
