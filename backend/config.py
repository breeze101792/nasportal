"""Paths and default configuration values.

All mutable state lives under ``CONFIG_DIR`` (env ``NASPORTAL_CONFIG``, default
``<repo>/config``). Defaults are seeded on first access so a fresh checkout —
or a freshly set ``NASPORTAL_CONFIG`` directory — boots straight into a working state.
"""
import os
from pathlib import Path

# <repo>/backend/config.py -> <repo>
REPO_ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = Path(os.environ.get("NASPORTAL_CONFIG", str(REPO_ROOT / "config")))
FRONTEND_DIR = REPO_ROOT / "frontend"
PORT = int(os.environ.get("PORT", "8000"))

DEFAULT_SETTINGS = {
    "portal_title": "My NAS",
    "wallpaper": "",
    "search_engines": [
        {"id": "google", "name": "Google", "url": "https://www.google.com/search?q=%s"},
        {"id": "bing", "name": "Bing", "url": "https://www.bing.com/search?q=%s"},
        {"id": "ddg", "name": "DuckDuckGo", "url": "https://duckduckgo.com/?q=%s"},
        {"id": "searxng", "name": "SearXNG", "url": "http://localhost:8080/search?q=%s"},
    ],
    "default_engine": "google",
    "theme": "dark",
    "portal_width": 80,
    "home_layout": "grouped",
    "background_color": "",
    # --- network awareness ---
    # ip_translation: {from_ip: to_ip}. When the resolver sees a network_ip
    # that has a translation entry AND the translated IP is on the user's
    # network, it serves the translated IP. Single-level lookup only (no
    # chain following) — admin takes responsibility for non-transitive maps.
    "ip_translation": {},
    # show_untranslatable: when False, the portal hides apps that have NO
    # reachable URL for the user (no same-network IP, no translation
    # entry, no domain, no public IP). True by default so existing
    # behaviour is preserved on first run.
    "show_untranslatable": True,
    # show_resolved_kind: a debug toggle for the portal home. When
    # on, every card shows a small badge ("local network", "via
    # translation", "public domain", etc.) explaining why its URL
    # was chosen. Off by default so the home view stays clean.
    "show_resolved_kind": False,
    # open_apps_in_new_tab: when True, clicking an app card on the
    # home page (or the Open button on /app) opens the app in a new
    # browser tab — the portal stays open in the background. When
    # False (the default), the click navigates the same tab, which is
    # the more focused single-tab workflow. Both are valid; the admin
    # picks on /settings.
    "open_apps_in_new_tab": False,
}

DEFAULT_APPS = {"apps": []}

# No password hash -> first-run "setup mode" (settings page sets the password).
DEFAULT_AUTH = {"password_hash": ""}