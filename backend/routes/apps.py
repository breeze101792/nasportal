"""Apps API: CRUD, on-demand ping, and URL scraping for drag-and-drop adding."""
import uuid
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, jsonify, request

from auth import login_required
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


@apps_bp.get("/apps")
def list_apps():
    return jsonify(_load())


@apps_bp.post("/apps")
@login_required
def add_app():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()
    url = (data.get("url") or "").strip()
    icon = (data.get("icon") or "").strip()
    if not title or not url:
        return jsonify({"error": "title_and_url_required"}), 400
    if not _valid_app_url(url):
        return jsonify({"error": "invalid_url_scheme"}), 400
    if not _valid_icon(icon):
        return jsonify({"error": "invalid_icon"}), 400
    with file_lock("apps.json"):
        store = _load()
        app = {
            "id": uuid.uuid4().hex,
            "title": title,
            "url": url,
            "icon": icon,
            "description": (data.get("description") or "").strip(),
            "group": (data.get("group") or "").strip(),
            "order": data.get("order", len(store["apps"])),
        }
        store["apps"].append(app)
        _save(store)
    return jsonify(app), 201


@apps_bp.put("/apps/<app_id>")
@login_required
def update_app(app_id):
    data = request.get_json(silent=True) or {}
    if "url" in data and not _valid_app_url((data["url"] or "").strip()):
        return jsonify({"error": "invalid_url_scheme"}), 400
    if "icon" in data and not _valid_icon((data["icon"] or "").strip()):
        return jsonify({"error": "invalid_icon"}), 400
    with file_lock("apps.json"):
        store = _load()
        app = next((a for a in store["apps"] if a["id"] == app_id), None)
        if app is None:
            return jsonify({"error": "not_found"}), 404
        for key in ("title", "url", "icon", "description", "group"):
            if key in data:
                app[key] = data[key]
        if "order" in data:
            app["order"] = data["order"]
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

    results = {}

    def do(app):
        return app["id"], ping_url(app["url"])

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