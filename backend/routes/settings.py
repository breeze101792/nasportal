"""Settings API: ``GET`` is public (the portal needs it to render); ``PUT`` is
login-gated. Only known fields are accepted and validated so a bad payload
can't corrupt the config or break the portal for every visitor."""
import ipaddress
import re

from flask import Blueprint, jsonify, request

from auth import login_required
from storage import file_lock, load_json, save_json

settings_bp = Blueprint("settings", __name__)

_ALLOWED_FIELDS = (
    "portal_title", "wallpaper", "search_engines", "default_engine",
    "theme", "portal_width", "home_layout",
    # Network awareness:
    "ip_translation", "show_untranslatable", "local_first",
    # Debug toggles (hidden from the default home view):
    "show_resolved_kind",
    # Custom background color override (empty string = no override).
    "background_color",
)
_TITLE_MAX = 200
_WALLPAPER_MAX = 4000
_THEMES = ("light", "dark", "system")
_WIDTH_MIN = 50
_WIDTH_MAX = 100
_HOME_LAYOUTS = ("grouped", "flow")
_BG_COLOR_MAX = 200
_BG_COLOR_KEYWORDS = {
    "transparent", "currentcolor", "inherit", "initial", "unset", "revert",
}


def _validate_background_color(v):
    """A background color is a free-form CSS color value: a 3/4/6/8-digit
    hex (``#abc``, ``#aabbcc``, ``#aabbccdd``), a functional notation
    (``rgb(…)``, ``rgba(…)``, ``hsl(…)``, ``hsla(…)``), a small set of
    CSS color keywords (including ``transparent``), or the special
    `none` we use to mean "no override" (empty string).

    We don't run a full CSS parser — the browser will reject anything
    invalid at paint time. Our job is to reject obvious junk (length,
    control characters, javascript:/expression() injection attempts).
    """
    if not isinstance(v, str):
        return False
    if len(v) > _BG_COLOR_MAX:
        return False
    if not v:
        return True  # empty = no override
    # Reject control characters and characters that could break out of
    # the CSS context. Hex / functional / named values are all ASCII
    # letters, digits, spaces, and the punctuation used in CSS colors.
    if not re.match(r"^[A-Za-z0-9 .,()#/%\-\"'_]+$", v):
        return False
    if v.lower() in _BG_COLOR_KEYWORDS:
        return True
    if v.startswith("#") and re.fullmatch(r"#[0-9a-fA-F]{3,8}", v):
        return True
    if re.fullmatch(r"(rgb|rgba|hsl|hsla)\([^()]*\)", v, re.IGNORECASE):
        return True
    return False


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


def _validate_ip_translation(t):
    """Each key and each value must be a valid IPv4 literal. Empty dict
    is fine. We only model v4 — v6 entries are rejected with the same
    code so the UI gets a clear signal.
    """
    if not isinstance(t, dict):
        return False
    for k, v in t.items():
        if not isinstance(k, str) or not isinstance(v, str):
            return False
        try:
            ipaddress.IPv4Address(k)
        except ipaddress.AddressValueError:
            return False
        try:
            ipaddress.IPv4Address(v)
        except ipaddress.AddressValueError:
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

        if "portal_width" in data:
            v = data["portal_width"]
            # Reject bool (a subclass of int) and non-numbers; store as int.
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not (_WIDTH_MIN <= v <= _WIDTH_MAX):
                return jsonify({"error": "invalid_portal_width"}), 400
            current["portal_width"] = int(v)

        if "home_layout" in data:
            v = data["home_layout"]
            if v not in _HOME_LAYOUTS:
                return jsonify({"error": "invalid_home_layout"}), 400
            current["home_layout"] = v

        if "ip_translation" in data:
            v = data["ip_translation"]
            if v is None:
                v = {}
            if not _validate_ip_translation(v):
                return jsonify({"error": "invalid_ip_translation"}), 400
            current["ip_translation"] = v

        if "show_untranslatable" in data:
            v = data["show_untranslatable"]
            if not isinstance(v, bool):
                return jsonify({"error": "invalid_show_untranslatable"}), 400
            current["show_untranslatable"] = v

        if "local_first" in data:
            v = data["local_first"]
            if not isinstance(v, bool):
                return jsonify({"error": "invalid_local_first"}), 400
            current["local_first"] = v

        if "show_resolved_kind" in data:
            v = data["show_resolved_kind"]
            if not isinstance(v, bool):
                return jsonify({"error": "invalid_show_resolved_kind"}), 400
            current["show_resolved_kind"] = v

        if "background_color" in data:
            v = data["background_color"]
            if not _validate_background_color(v):
                return jsonify({"error": "invalid_background_color"}), 400
            current["background_color"] = v

        save_json("settings.json", current)
    return jsonify(current)
