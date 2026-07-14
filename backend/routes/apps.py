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
    """Turn a form payload (either legacy ``url`` or new multi-line
    ``urls`` / structured ``network_ips`` / ``domain`` / ``public_ip``)
    into a normalised dict suitable for storage.

    Returns (parsed_dict, error_response_or_None). If error_response is
    not None, the caller should return it directly.
    """
    out = {}

    # Title / group / description / icon / order — pass through.
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

    # URL ingest. Three possible input shapes:
    #  1. legacy single string in ``url``
    #  2. multi-line in ``urls`` (a string of newlines/commas) or a list
    #  3. structured fields: network_ips, domain, public_ip, scheme, port
    legacy_url = (data.get("url") or "").strip()
    urls_raw = data.get("urls", "")
    if isinstance(urls_raw, list):
        url_list = [str(u).strip() for u in urls_raw if str(u).strip()]
    else:
        url_list = _split_url_lines(urls_raw)

    parsed_lines = [parse_url(u) for u in url_list]
    # Drop empty lines silently; the user might leave a trailing newline.
    parsed_lines = [p for p in parsed_lines if p.get("host")]

    # Network IPs — explicit list wins, otherwise accumulate from parsed
    # multi-line input (preserving order, deduped).
    explicit_nets = data.get("network_ips")
    if isinstance(explicit_nets, list):
        nets = []
        for ip in explicit_nets:
            if not isinstance(ip, str):
                return None, (jsonify({"error": "invalid_network_ip"}), 400)
            try:
                ipaddress.IPv4Address(ip)  # noqa: F821
            except Exception:
                return None, (jsonify({"error": "invalid_network_ip"}), 400)
            nets.append(ip)
        out["network_ips"] = nets
    else:
        nets = []
        for p in parsed_lines:
            if p.get("network_ip") and p["network_ip"] not in nets:
                nets.append(p["network_ip"])
        out["network_ips"] = nets

    # Domain — explicit field wins, otherwise take the first parsed domain.
    if "domain" in data and data["domain"]:
        out["domain"] = str(data["domain"]).strip()
    else:
        dom = next((p["domain"] for p in parsed_lines if p.get("domain")), None)
        if dom:
            out["domain"] = dom

    # Public IP — explicit field wins, otherwise the first parsed public IP.
    if "public_ip" in data and data["public_ip"]:
        out["public_ip"] = str(data["public_ip"]).strip()
    else:
        pub = next((p["public_ip"] for p in parsed_lines if p.get("public_ip")), None)
        if pub:
            out["public_ip"] = pub

    # Scheme / port — only set if at least one parsed line carried them.
    schemes = {p["scheme"] for p in parsed_lines if p.get("scheme")}
    ports = {p["port"] for p in parsed_lines if p.get("port")}
    if "scheme" in data and data["scheme"] in ("http", "https"):
        out["scheme"] = data["scheme"]
    elif len(schemes) == 1:
        out["scheme"] = next(iter(schemes))
    elif "scheme" in data and not data["scheme"]:
        pass  # explicit empty -> clear
    if "port" in data and data["port"]:
        try:
            out["port"] = int(data["port"])
        except (TypeError, ValueError):
            return None, (jsonify({"error": "invalid_port"}), 400)
    elif len(ports) == 1:
        out["port"] = next(iter(ports))

    # Path — take from the first parsed line that has a non-empty path.
    # We only accept a path when ALL parsed lines agree on it (or all
    # are empty); otherwise the user pasted URLs with different paths,
    # and we'd silently pick one and surprise the user. The app gets
    # an explicit "path" field that the resolver concatenates onto the
    # chosen host. Explicit body field wins when the caller sends one.
    if "path" in data and data["path"]:
        out["path"] = str(data["path"])
    else:
        paths = [p["path"] for p in parsed_lines if p.get("path")]
        if paths and all(x == paths[0] for x in paths):
            out["path"] = paths[0]

    # Preserve the legacy single-`url` field for the resolver's tier 6
    # fallback when no structured fields are present. We don't *write*
    # it on new saves (the resolver handles everything), but if a
    # caller still sends it verbatim we keep it as-is for compatibility.
    if legacy_url and not (out.get("network_ips") or out.get("domain")
                          or out.get("public_ip")):
        out["url"] = legacy_url

    return out, None


# Lazy import inside the function to keep the module-level import block
# tight (and to dodge a top-level ipaddress import for cold paths).
import ipaddress  # noqa: E402


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
    if not (parsed.get("url") or parsed.get("network_ips")
            or parsed.get("domain") or parsed.get("public_ip")):
        return jsonify({"error": "url_required"}), 400
    if not _valid_icon(parsed.get("icon", "")):
        return jsonify({"error": "invalid_icon"}), 400

    with file_lock("apps.json"):
        store = _load()
        app = {
            "id": uuid.uuid4().hex,
            "title": title,
            "icon": parsed.get("icon", ""),
            "description": parsed.get("description", ""),
            "group": parsed.get("group", ""),
            "order": parsed.get("order", len(store["apps"])),
            # Structured URL fields. The legacy single-`url` is only
            # stored when nothing structured is set (preserves the
            # fallback path for callers still using the old shape).
            "network_ips": parsed.get("network_ips", []),
            "domain": parsed.get("domain", ""),
            "public_ip": parsed.get("public_ip", ""),
            "scheme": parsed.get("scheme", "http"),
            "port": parsed.get("port"),
            "path": parsed.get("path", ""),
        }
        if parsed.get("url"):
            app["url"] = parsed["url"]
        # A missing scheme/port on a fully-structured payload is fine;
        # the resolver falls back to http. But when only the legacy
        # ``url`` is present, validate its scheme to match the old
        # behaviour.
        if "url" in app and not _valid_app_url(app["url"]):
            return jsonify({"error": "invalid_url_scheme"}), 400

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
        if "url" in parsed and not _valid_app_url(parsed["url"]):
            return jsonify({"error": "invalid_url_scheme"}), 400

        for key in ("title", "icon", "description", "group",
                    "network_ips", "domain", "public_ip", "scheme", "port",
                    "path", "url"):
            if key in parsed:
                app[key] = parsed[key]
        if "order" in parsed:
            app["order"] = parsed["order"]
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
        # runs on behalf of an admin — we use the server's own public IP
        # as the "user IP" for resolution, which means: prefer network
        # IPs reachable from the server itself, then public. Falls back
        # to the legacy url if the app has nothing structured.
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


@apps_bp.get("/apps/resolved")
def resolved_apps():
    """Return apps with the best URL pre-resolved for the caller's source IP.

    Honors ``show_untranslatable``: when False, apps with NO same-network
    IP (direct or via translation) are filtered out. The kind field
    (network / translated / domain / public_ip / fallback / legacy) is
    included so the portal can hint at why a particular URL was chosen.

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
