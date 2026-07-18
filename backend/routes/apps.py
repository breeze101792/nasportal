"""Apps API: CRUD, on-demand ping, URL scraping, and URL parsing/resolution
for the network-aware portal."""
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from auth import login_required
from services.networks import (
    get_local_networks,
    is_translatable,
    parse_url,
    resolve_url,
    reset_local_networks_cache as _reset_net_cache,
)
from services.pinger import ping as ping_url
from services.scraper import scrape as scrape_url
from storage import file_lock, load_json, save_json

apps_bp = Blueprint("apps", __name__)


def _valid_app_url(u):
    # Restrict app links to http(s) so an admin can't store a javascript:/data:
    # URL that the public grid would render as a clickable link.
    return bool(u) and u.lower().startswith(("http://", "https://"))


def _valid_icon(u):
    # Icons may be http(s) or an inline data:image/ URI; anything else (e.g.
    # javascript:) is rejected.
    if not u:
        return True
    low = u.lower()
    return low.startswith(("http://", "https://")) or low.startswith("data:image/")


def _fetch_favicon(urls):
    """Best-effort favicon fetch for the auto-fill case where the admin
    left the icon field blank. Returns a validated icon string (http(s)
    or data:image/...) or "" if nothing usable came back. We always
    return through ``_valid_icon`` so a hostile site that injects
    ``<link rel=icon href=javascript:...>`` can't end up stored.
    The scraper swallows network errors, so this never raises."""
    if not urls:
        return ""
    favicon = (scrape_url(urls[0]).get("favicon") or "").strip()
    if not _valid_icon(favicon):
        return ""
    return favicon


def _load():
    return load_json("apps.json")


def _save(data):
    save_json("apps.json", data)


def _split_url_lines(raw):
    """Split a free-form paste into individual URL strings.

    Accepts newline-separated, comma-separated, or whitespace-separated
    values. The user can paste a single URL or ten — they all go in.
    Returns a list of stripped, non-empty strings preserving order.
    """
    if not raw:
        return []
    out = []
    for line in str(raw).replace(",", "\n").splitlines():
        s = line.strip()
        if s:
            out.append(s)
    return out


def _parse_app_payload(data):
    """Turn a form payload into a normalised dict suitable for storage.

    Canonical input: ``urls`` — a string (one URL per line / comma-
    separated) or a list of strings. The URLs carry the scheme, port,
    and path with them, so we no longer need separate ``network_ips`` /
    ``domain`` / ``public_ip`` / ``scheme`` / ``port`` / ``path`` fields.

    For backward compat we still *accept* the old structured shape and
    collapse it into a URL list, but the canonical write stores only
    the URL list — the admin can hand-clean old apps by opening them
    in Edit and re-saving.

    Returns (parsed_dict, error_response_or_None). If error_response is
    not None, the caller should return it directly.
    """
    out = {}

    # Pass-through fields.
    if "title" in data:
        out["title"] = (data.get("title") or "").strip()
    if "group" in data:
        out["group"] = (data.get("group") or "").strip()
    if "description" in data:
        out["description"] = (data.get("description") or "").strip()
    if "icon" in data:
        out["icon"] = (data.get("icon") or "").strip()
    if "order" in data:
        out["order"] = data["order"]

    # Build the URL list. New shape wins; old shape is a fallback.
    urls = _extract_url_list(data)
    if not urls:
        urls = _urls_from_legacy_shape(data)
    if not urls:
        # We refuse the save later (in the route handler) by returning
        # the error there; here we just leave urls empty so the caller
        # can branch on it.
        return out, None

    # Dedupe preserving order. The user might paste the same URL twice
    # in a multi-line input — collapse to one entry so the resolver
    # doesn't see it twice.
    seen = set()
    deduped = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            deduped.append(u)
    urls = deduped

    # Validate: every URL must be http(s). Reject early with a clear
    # error so the admin doesn't silently lose a bad entry.
    for u in urls:
        if not _valid_app_url(u):
            return None, (jsonify({"error": "invalid_url_scheme"}), 400)

    out["urls"] = urls
    return out, None


def _extract_url_list(data) -> list:
    """Pull the ``urls`` field out of a payload, normalise to a list
    of stripped, non-empty strings. Accepts a string (split on
    newlines/commas) or a list of strings. Returns [] if absent or
    empty."""
    raw = data.get("urls")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(u).strip() for u in raw if str(u).strip()]
    if isinstance(raw, str):
        return _split_url_lines(raw)
    return []


def _urls_from_legacy_shape(data) -> list:
    """Backward-compat: collapse the old structured fields into a
    URL list. We share one (scheme, port, path) across all of them
    — the data-loss this fix exists to prevent — but it keeps old
    apps working until the admin re-saves them with the new shape."""
    urls: list[str] = []
    scheme = data.get("scheme") if data.get("scheme") in ("http", "https") else "http"
    port = data.get("port")
    if port in ("", None):
        port = None
    else:
        try:
            port = int(port)
        except (TypeError, ValueError):
            return []  # bad port -> the caller will reject
    path = (data.get("path") or "").strip() or ""

    def _one(host: str) -> str:
        if port:
            hp = f"{host}:{port}"
        else:
            hp = host
        return f"{scheme}://{hp}{path}"

    nets = data.get("network_ips")
    if isinstance(nets, list):
        for ip in nets:
            if isinstance(ip, str) and ip.strip():
                urls.append(_one(ip.strip()))
    domain = (data.get("domain") or "").strip()
    if domain:
        urls.append(_one(domain))
    public_ip = (data.get("public_ip") or "").strip()
    if public_ip:
        urls.append(_one(public_ip))
    legacy = (data.get("url") or "").strip()
    if legacy and not urls:
        urls.append(legacy)
    # Dedupe, preserve order.
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


@apps_bp.get("/apps")
def list_apps():
    return jsonify(_load())


@apps_bp.post("/apps")
@login_required
def add_app():
    data = request.get_json(silent=True) or {}
    parsed, err = _parse_app_payload(data)
    if err is not None:
        return err

    title = parsed.get("title", "")
    if not title:
        return jsonify({"error": "title_required"}), 400
    if not parsed.get("urls"):
        return jsonify({"error": "url_required"}), 400
    if not _valid_icon(parsed.get("icon", "")):
        return jsonify({"error": "invalid_icon"}), 400

    # Auto-fill the icon when the admin left it blank — the natural meaning
    # of "no icon" is "look it up from the site." We re-validate the result
    # so a hostile <link rel=icon> can't smuggle a javascript: through.
    icon = parsed.get("icon", "")
    if not icon:
        icon = _fetch_favicon(parsed["urls"])

    with file_lock("apps.json"):
        store = _load()
        app = {
            "id": uuid.uuid4().hex,
            "title": title,
            "icon": icon,
            "description": parsed.get("description", ""),
            "group": parsed.get("group", ""),
            "order": parsed.get("order", len(store["apps"])),
            "urls": parsed["urls"],
        }
        store["apps"].append(app)
        _save(store)
    return jsonify(app), 201


@apps_bp.put("/apps/<app_id>")
@login_required
def update_app(app_id):
    data = request.get_json(silent=True) or {}
    parsed, err = _parse_app_payload(data)
    if err is not None:
        return err

    with file_lock("apps.json"):
        store = _load()
        app = next((a for a in store["apps"] if a["id"] == app_id), None)
        if app is None:
            return jsonify({"error": "not_found"}), 404

        if "icon" in parsed and not _valid_icon(parsed["icon"]):
            return jsonify({"error": "invalid_icon"}), 400

        for key in ("title", "icon", "description", "group", "urls"):
            if key in parsed:
                app[key] = parsed[key]
        if "order" in parsed:
            app["order"] = parsed["order"]

        # Explicitly clearing the icon ("") means "look it up again" — fetch
        # from the (now-updated) URL list, falling back to "" if nothing
        # usable came back. We do this after the field-copy above so the
        # URLs are the freshly-saved ones.
        if "icon" in parsed and not parsed["icon"]:
            app["icon"] = _fetch_favicon(app.get("urls") or [])

        _save(store)
    return jsonify(app)


@apps_bp.delete("/apps/<app_id>")
@login_required
def delete_app(app_id):
    with file_lock("apps.json"):
        store = _load()
        before = len(store["apps"])
        store["apps"] = [a for a in store["apps"] if a["id"] != app_id]
        if len(store["apps"]) == before:
            return jsonify({"error": "not_found"}), 404
        _save(store)
    return jsonify({"ok": True})


@apps_bp.post("/apps/bulk/delete")
@login_required
def bulk_delete_apps():
    """Delete several apps by id. Body ``{ids: ["...", ...]}``. Unknown ids are
    reported back in ``missing`` (not an error) so a stale selection on the
    client doesn't fail the whole call. Login-gated like all mutations."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        return jsonify({"error": "ids_required"}), 400
    with file_lock("apps.json"):
        store = _load()
        existing = {a["id"] for a in store["apps"]}
        wanted = set(ids)
        missing = [i for i in ids if i not in existing]
        before = len(store["apps"])
        store["apps"] = [a for a in store["apps"] if a["id"] not in wanted]
        _save(store)
    return jsonify({"deleted": before - len(store["apps"]), "missing": missing})


@apps_bp.post("/apps/bulk/group")
@login_required
def bulk_group_apps():
    """Set the group of several apps at once. Body ``{ids: [...], group: "..."}``.
    An empty string clears the group. ``missing`` lists ids that no longer exist."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    group = (data.get("group") or "").strip()
    if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
        return jsonify({"error": "ids_required"}), 400
    if len(group) > 100:
        return jsonify({"error": "invalid_group"}), 400
    with file_lock("apps.json"):
        store = _load()
        existing = {a["id"] for a in store["apps"]}
        wanted = set(ids)
        missing = [i for i in ids if i not in existing]
        updated = 0
        for a in store["apps"]:
            if a["id"] in wanted:
                a["group"] = group
                updated += 1
        _save(store)
    return jsonify({"updated": updated, "missing": missing})


@apps_bp.post("/apps/bulk/order")
@login_required
def bulk_order_apps():
    """Set the ``order`` field of several apps at once. Body
    ``{items: [{id: str, order: int}, ...]}``. Applied atomically under the
    file lock. Unknown ids are reported in ``missing`` (not an error) so a
    stale drag doesn't fail the whole call. Login-gated like all mutations."""
    data = request.get_json(silent=True) or {}
    items = data.get("items")
    if not isinstance(items, list) or not items:
        return jsonify({"error": "items_required"}), 400
    parsed = []
    for it in items:
        if not isinstance(it, dict):
            return jsonify({"error": "invalid_item"}), 400
        if not isinstance(it.get("id"), str) or not it["id"]:
            return jsonify({"error": "invalid_id"}), 400
        try:
            order = int(it["order"])
        except (TypeError, ValueError):
            return jsonify({"error": "invalid_order"}), 400
        parsed.append((it["id"], order))
    with file_lock("apps.json"):
        store = _load()
        existing = {a["id"]: a for a in store["apps"]}
        missing = [i for i, _ in parsed if i not in existing]
        for i, o in parsed:
            if i in existing:
                existing[i]["order"] = o
        _save(store)
    return jsonify({"updated": len(parsed) - len(missing), "missing": missing})


@apps_bp.post("/apps/ping")
@login_required
def ping_apps():
    """Ping apps concurrently. Body ``{ids: [...]}`` pings a subset; omit for all.
    Login-gated: live probing of (typically internal) app URLs is a server-side
    request triggered by the visitor, so it's an edit-tier action."""
    data = request.get_json(silent=True) or {}
    ids = data.get("ids")
    store = _load()
    apps = store["apps"]
    if ids:
        wanted = set(ids)
        apps = [a for a in apps if a["id"] in wanted]

    # Capture the request-scoped inputs ONCE on the request thread; the
    # threadpool workers can't reach ``flask.request`` (it's thread-local).
    user_ip = (request.remote_addr or "").strip()
    settings = load_json("settings.json")
    translation = settings.get("ip_translation") or {}

    results = {}

    def do(app):
        # Ping the URL the current visitor would actually use, not the
        # legacy single-`url` field. The pinger is login-gated so this
        # runs on behalf of an admin. The resolver picks the best URL
        # for the visitor's source IP using the fixed 4-tier priority
        # (same-net IP > domain > public IP > other-net IP), falling
        # back to the legacy `url` field if the app has nothing
        # structured.
        resolved = resolve_url(app, user_ip, translation)
        target = (resolved or {}).get("url") or app.get("url") or ""
        return app["id"], ping_url(target)

    with ThreadPoolExecutor(max_workers=8) as pool:
        for app_id, result in pool.map(do, apps):
            results[app_id] = result
    return jsonify({"results": results})


@apps_bp.post("/scrape")
@login_required
def scrape():
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "url_required"}), 400
    return jsonify(scrape_url(url))


# ---- network-aware endpoints (read-only, public) --------------------------

@apps_bp.post("/apps/parse")
def parse_endpoint():
    """Parse a free-form URL paste into the structured fields. Public —
    the form needs it to show a live preview as the user types. The
    parser never trusts the input beyond the categories it returns;
    the actual write path still validates the resulting fields.
    Body: ``{url: "..."}`` or ``{urls: "a\\nb\\nc"}``.
    """
    data = request.get_json(silent=True) or {}
    if "url" in data:
        return jsonify(parse_url(data.get("url") or ""))
    raw = data.get("urls", "")
    if isinstance(raw, list):
        return jsonify([parse_url(u) for u in raw])
    return jsonify([parse_url(u) for u in _split_url_lines(raw)])


@apps_bp.get("/favicon")
def favicon_endpoint():
    """Return the favicon URL for a given page URL. Public — the
    portal home calls this once per app to fetch each app's favicon
    at render time. The in-memory cache on the frontend makes
    repeat hits for the same host free, and any visitor can already
    navigate to the same URL via the card link, so the SSRF surface
    is the same as the resolved endpoint's URL surface.

    The URL is validated as http(s) so a malicious caller can't
    probe ``file://`` / ``gopher://`` / etc. The scraper does its
    own timeout/redirect handling and swallows exceptions, so a
    flaky target never breaks the portal home."""
    url = (request.args.get("url") or "").strip()
    if not url:
        return jsonify({"favicon": ""})
    if not _valid_app_url(url):
        return jsonify({"error": "invalid_url_scheme"}), 400
    result = scrape_url(url)
    return jsonify({"favicon": result.get("favicon", "")})


@apps_bp.get("/apps/resolved")
def resolved_apps():
    """Return apps with the best URL pre-resolved for the caller's source IP.

    Honors ``show_untranslatable``: when False, apps with no reachable
    URL for the caller (no same-network IP, no translation entry, no
    domain, no public IP) are filtered out. The kind field
    (network / translated / domain / public_ip / other_network /
    fallback / legacy) is included so the portal can hint at why a
    particular URL was chosen.

    Public read — the portal home needs it for every visitor.
    """
    user_ip = (request.remote_addr or "").strip()
    settings = load_json("settings.json")
    translation = settings.get("ip_translation") or {}
    show_untranslatable = bool(settings.get("show_untranslatable", True))

    store = _load()
    out = []
    for a in store["apps"]:
        if not show_untranslatable and not is_translatable(a, user_ip, translation):
            continue
        resolved = resolve_url(a, user_ip, translation)
        entry = dict(a)
        entry["resolved"] = resolved
        # When the resolver returned a real URL, use it as `url` for the
        # portal's <a href> — the legacy field is still there for the
        # /app management view, which doesn't resolve.
        if resolved:
            entry["url"] = resolved["url"]
        out.append(entry)
    return jsonify({"apps": out, "user_ip": user_ip})


# Test-only helper, not exposed as a route. Used by the test suite to
# inject a fake local network table without touching /proc.
def _reset_for_test():
    _reset_net_cache()
