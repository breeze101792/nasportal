"""Settings API: ``GET`` is public (the portal needs it to render); ``PUT`` is
login-gated. Only known fields are accepted and validated so a bad payload
can't corrupt the config or break the portal for every visitor."""
from flask import Blueprint, jsonify, request

from auth import login_required
from storage import file_lock, load_json, save_json

settings_bp = Blueprint("settings", __name__)

_ALLOWED_FIELDS = ("portal_title", "wallpaper", "search_engines", "default_engine", "theme")
_TITLE_MAX = 200
_WALLPAPER_MAX = 4000
_THEMES = ("light", "dark", "system")


def _validate_engines(engines):
    """Each engine must have id/name and an http(s) URL containing %s."""
    if not isinstance(engines, list):
        return False
    for e in engines:
        if not isinstance(e, dict):
            return False
        if not (e.get("id") and e.get("name") and e.get("url")):
            return False
        url = e["url"]
        if "%s" not in url:
            return False
        if not url.lower().startswith(("http://", "https://")):
            return False
    return True


@settings_bp.get("/settings")
def get_settings():
    return jsonify(load_json("settings.json"))


@settings_bp.put("/settings")
@login_required
def put_settings():
    data = request.get_json(silent=True) or {}

    with file_lock("settings.json"):
        current = load_json("settings.json")
        engines = current.get("search_engines", []) or []

        if "search_engines" in data:
            if not _validate_engines(data["search_engines"]):
                return jsonify({"error": "invalid_search_engines"}), 400
            current["search_engines"] = data["search_engines"]
            engines = data["search_engines"]

        if "portal_title" in data:
            v = data["portal_title"]
            if not isinstance(v, str) or len(v) > _TITLE_MAX:
                return jsonify({"error": "invalid_portal_title"}), 400
            current["portal_title"] = v

        if "wallpaper" in data:
            v = data["wallpaper"]
            if not isinstance(v, str) or len(v) > _WALLPAPER_MAX:
                return jsonify({"error": "invalid_wallpaper"}), 400
            current["wallpaper"] = v

        if "default_engine" in data:
            v = data["default_engine"]
            engine_ids = [e["id"] for e in engines]
            if v != "" and v not in engine_ids:
                return jsonify({"error": "invalid_default_engine"}), 400
            current["default_engine"] = v

        if "theme" in data:
            v = data["theme"]
            if v not in _THEMES:
                return jsonify({"error": "invalid_theme"}), 400
            current["theme"] = v

        save_json("settings.json", current)
    return jsonify(current)