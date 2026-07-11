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
        {"id": "searxng", "name": "SearXNG", "url": "http://localhost:8080/search?q=%s"},
    ],
    "default_engine": "google",
    "theme": "dark",
    "portal_width": 80,
}

DEFAULT_APPS = {"apps": []}

# No password hash -> first-run "setup mode" (settings page sets the password).
DEFAULT_AUTH = {"password_hash": ""}