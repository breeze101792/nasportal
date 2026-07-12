"""Flask entrypoint: app factory, page routes, static serving, blueprints.

Run with:  ``./start.sh``   (or directly: ``python backend/app.py``)
"""
import os
import secrets
import sys

# Ensure backend/ is importable as the package root in script mode too.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, send_from_directory

from config import FRONTEND_DIR, PORT
from routes.apps import apps_bp
from routes.auth import auth_bp
from routes.settings import settings_bp
from storage import load_json, save_json

PAGE_FILES = {
    "/": "index.html",
    "/app": "apps.html",
    "/settings": "settings.html",
    "/login": "login.html",
}


def _load_secret_key() -> str:
    """Persist a random session secret in config/ so sessions survive restarts."""
    data = load_json("secret.json", default=None)
    if data and data.get("key"):
        return data["key"]
    key = secrets.token_hex(32)
    save_json("secret.json", {"key": key})
    return key


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)
    app.secret_key = _load_secret_key()
    # SESSION_COOKIE_SECURE is opt-in: enabling it on a plain-HTTP LAN would
    # stop the browser sending the cookie at all. Set NASPORTAL_SECURE_COOKIE=1
    # when serving behind a TLS-terminating reverse proxy. See README.
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("NASPORTAL_SECURE_COOKIE") == "1",
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )

    app.register_blueprint(auth_bp, url_prefix="/api")
    app.register_blueprint(apps_bp, url_prefix="/api")
    app.register_blueprint(settings_bp, url_prefix="/api")

    # --- page routes (clean URLs) ---
    def serve_page(filename):
        def view():
            return send_from_directory(FRONTEND_DIR, filename)
        return view

    for path, filename in PAGE_FILES.items():
        app.add_url_rule(path, endpoint=filename, view_func=serve_page(filename))

    # --- static asset dirs ---
    for sub in ("css", "js", "assets"):
        def make(sub=sub):
            def view(filename):
                return send_from_directory(FRONTEND_DIR / sub, filename)
            return view
        app.add_url_rule(f"/{sub}/<path:filename>", endpoint=f"static_{sub}", view_func=make())

    # --- favicon ---
    app.add_url_rule("/favicon.svg", endpoint="favicon",
                     view_func=lambda: send_from_directory(FRONTEND_DIR, "favicon.svg"))

    return app


if __name__ == "__main__":
    create_app().run(
        host="0.0.0.0",
        port=PORT,
        debug=os.environ.get("FLASK_DEBUG") == "1",
    )