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
    # IP on the user's network (direct or via translation). True by default
    # so existing behaviour is preserved on first run.
    "show_untranslatable": True,
    # local_first: when True (the default), the resolver prefers ANY
    # network IP over the public domain — even if the network IP is
    # on a different subnet than the user (i.e. traffic will go
    # through whatever tunnel bridges the two networks). When False,
    # the resolver falls through to the public domain before using a
    # non-same-network IP. The user can flip this on /settings.
    "local_first": True,
}

DEFAULT_APPS = {"apps": []}

# No password hash -> first-run "setup mode" (settings page sets the password).
DEFAULT_AUTH = {"password_hash": ""}