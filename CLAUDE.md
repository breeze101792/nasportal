# CLAUDE.md — nasportal

Personal NAS start-page web portal: Flask JSON API under `/api` + static vanilla-JS frontend, JSON state in `config/`, no build step.

## Run & test

```bash
./start.sh                       # start on :8000 (creates .venv, installs requirements.txt)
./start.sh --port 9000 --config /etc/nasportal
.venv/bin/python -m pytest tests/test_api.py -m 'not e2e' -q   # backend only (59 cases, in-process, no socket)
.venv/bin/python -m pytest tests/test_pages.py -m e2e -q       # e2e only (13 functions, Chromium via Playwright)
.venv/bin/python -m pytest -q                                  # full suite
```

**e2e sandbox caveat:** the e2e suite launches Chromium and binds a real listening socket (`werkzeug.make_server`). Both are blocked by the Claude Code OS sandbox, so `-m e2e` (and therefore the full suite) will not run cleanly in this harness — disable the OS sandbox (`dangerouslyDisableSandbox: true` / the `/sandbox` command) to run them here. On the user's normal machine they run fine via the project `.venv` (with playwright + a chromium binary; `/usr/bin/chromium` is auto-detected).

Test state is isolated to a session temp dir wiped before each test, so every test starts in first-run setup mode; no real `config/` is touched.

## Architecture

- Flask app factory `create_app()` in `backend/app.py`; Flask's built-in static handling is disabled — assets are served via explicit rules. All API blueprints (`auth_bp`, `apps_bp`, `settings_bp`) mount under `url_prefix="/api"`. Page routes (`/`, `/app`, `/settings`, `/login`) serve HTML from `frontend/` via `send_from_directory`.
- JSON state lives in `config/` (`settings.json`, `apps.json`, `auth.json`, `secret.json`). `config/` is gitignored except `config/.gitkeep`. The config dir is `NASPORTAL_CONFIG` env or `REPO_ROOT/config` (`backend/config.py`).
- No build step. No framework, no bundler, no JS modules — plain `<script>` tags.

## Frontend conventions

- Vanilla JS. Build everything with `document.createElement` + `appendChild`/`replaceChildren`.
- **Never use `innerHTML`.** Use the `el(tag, attrs, ...children)` helper in `frontend/js/api.js`: the `text` attr sets `textContent` (XSS-safe), `on*` attrs with a function value bind via `addEventListener`, `class` -> `className`. For replacing a node's text use `setText(node, text)`.
- Use `safeUrl(u)` for any `href` — returns the URL only if it matches `/^(https?:)?\/\//i`, else `"#"` (defends against `javascript:`/`data:`).
- Use `cssEsc(s)` before interpolating admin-controlled values into CSS `url("...")` (escapes `"` and `\`).
- `theme.js` loads synchronously in `<head>` and applies the theme from `localStorage` before paint — no flash of wrong theme.
- Every page loads `css/style.css` + `js/theme.js` in `<head>`, then `js/api.js` + a page-specific script at the end of `<body>`. Shared chrome: `.brand` + `.toplinks` inside `<header class="top">`, populated in JS (`login.html` is the deliberate exception — a standalone `.login-card` with no shared header chrome).

## Backend conventions

- **Validate ALL client input.** URL scheme allowlists: app urls/icons (`_valid_app_url`, `_valid_icon` in `backend/routes/apps.py`) and search-engine urls (`backend/routes/settings.py`) must be `http(s)://` (icons also allow `data:image/...`). Settings field validation uses named error codes (e.g. `invalid_portal_title`, `invalid_theme`, `invalid_portal_width`); see the inline per-field validation in `settings.py`.
- **Reads are public, mutations are `@login_required`** (view-public / edit-gated, Heimdall-style). `GET /api/apps`, `GET /api/settings`, `GET /api/auth/check` and the HTML/static routes are public. All app mutations, `PUT /api/settings`, `PUT /api/auth/password`, `POST /api/apps/ping`, and `POST /api/scrape` are login-gated. The `/settings` *page* is also login-gated at the frontend (guests bounce to `/login?next=/settings`); only the `GET /api/settings` JSON is public, because the portal/apps pages need it to render theme/wallpaper/title.
- **Live ping is login-gated** (SSRF defense — it's a server-side request to arbitrary app URLs).
- `backend/storage.py`: `save_json` writes atomically (`tempfile.mkstemp` in `CONFIG_DIR` then `os.replace`). `file_lock(name)` takes an `fcntl.flock(LOCK_EX)` on `config/<name>.lock` for cross-process read-modify-write serialization.
- `load_json` returns a `copy.deepcopy` of the shared module-level default when the file is absent — **never hand out the mutable default directly** (callers mutate the returned store in place; that was a real bug). `auth.json` is loaded with `strict=True` so a corrupt auth file raises instead of silently reverting to setup mode.
- First-run setup mode: `auth.setup_required()` is true when `password_hash` is empty; `POST /api/auth/login` then sets the password rather than verifying one.

## UX / JS patterns

- **Add form stays open after save:** on a successful add, `apps.js submitForm` clears title/url/icon/desc/id but leaves `#f-group` as-is (batch-entry convenience), shows "Saved. Add another, or Close when done.", and refocuses `#f-url`. Editing an existing app closes the form.
- **Set "Saving..." synchronously before the `await`** in async save handlers, so the later "Saved" confirmation reflects the current save rather than a stale status (avoids a stale-status race).
- Portal home (`portal.js`) is read-only. Two home layouts: `grouped` (titled section per group) vs `flow` (one continuous grid with group label on each card), switched via `settings.home_layout`.
- 401 handling in `api.js`: redirects to `/login?next=<encoded path>` unless already on `/login`. `/login` and `/settings` setup use raw `fetch` (bypassing `api`) so a 401 there is handled inline. `nextPath()` only returns local `?next=` values (rejects protocol-relative `//`).

## Git / commit workflow

- The user asks to commit; commit on `master`.
- The Claude sandbox injects device-dotfiles (`.bash_profile`, `.bashrc`, `.gitconfig`, `.zshrc`, `.profile`, `.zprofile`, `.ripgreprc`, `.gitmodules`, `.idea`, `.vscode`, `.mcp.json`, etc.) as **character-special nodes** that git cannot add. Stage real files explicitly (e.g. `git add backend/ frontend/ tests/`) and ignore those device nodes — do not `git add -A`/`git add .`.
- The repo may report **"dubious ownership"**; fix with `git config --global --add safe.directory <repo path>` before committing.