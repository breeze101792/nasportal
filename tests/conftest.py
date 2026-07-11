"""Shared pytest fixtures.

Backend (API) tests use Flask's in-process test client — no real socket.
End-to-end page tests spin up the real app on a random port (werkzeug
``make_server``) and drive the system Chromium via Playwright.

All JSON state is isolated into a per-session temp dir; ``config_dir`` wipes it
before each test so every test starts in first-run setup mode with default
settings. ``NASPORTAL_CONFIG`` is set at import time (before ``app`` is first
imported, since ``config.py`` reads it at module load).
"""
import os
import pathlib
import sys
import tempfile
import threading

_HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent / "backend"))

_SESSION_CONFIG = tempfile.mkdtemp(prefix="nasportal_test_")
os.environ["NASPORTAL_CONFIG"] = _SESSION_CONFIG

# Prefer the system Chromium (no browser download). If absent, Playwright
# falls back to its bundled browser — run `playwright install chromium`.
if os.path.exists("/usr/bin/chromium"):
    os.environ.setdefault("NASPORTAL_CHROMIUM", "/usr/bin/chromium")

import pytest  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402


def login(client, password="testpw"):
    """Helper for backend tests: run the first-run setup login."""
    r = client.post("/api/auth/login", json={"password": password})
    assert r.status_code == 200, r.get_json()
    return r


@pytest.fixture
def config_dir():
    """Empty config dir per test -> setup mode + default settings."""
    cfg = pathlib.Path(_SESSION_CONFIG)
    for f in cfg.iterdir():
        if f.is_file():
            f.unlink()
    return cfg


@pytest.fixture
def app(config_dir):
    import app as app_module
    return app_module.create_app()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def base_url(app):
    """Real HTTP server on a random port for browser tests."""
    server = make_server("127.0.0.1", 0, app, threaded=True)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


@pytest.fixture
def page(base_url):
    """A Playwright browser page pointed at the running server."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed (see requirements-dev.txt)")
    chromium_path = os.environ.get("NASPORTAL_CHROMIUM") or None
    with sync_playwright() as p:
        launch_kwargs = {"headless": True, "args": ["--no-sandbox", "--disable-crash-reporter"]}
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
        browser = p.chromium.launch(**launch_kwargs)
        pg = browser.new_page()
        yield pg
        browser.close()