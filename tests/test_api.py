"""Backend API tests via Flask's in-process test client (no browser).

Covers: auth/setup flow, app CRUD + ping + scrape (mocked), settings validation,
config seeding, atomic storage, and the fail-closed corrupt-auth behaviour."""
import json

import pytest

from conftest import login


# ---- auth ----
def test_auth_check_setup_mode(client):
    r = client.get("/api/auth/check")
    assert r.status_code == 200
    assert r.get_json() == {"authed": False, "setup_required": True}


def test_login_sets_password_and_auths(client):
    assert client.post("/api/apps", json={"title": "t", "url": "https://x"}).status_code == 401
    login(client, "secret")
    assert client.get("/api/auth/check").get_json() == {"authed": True, "setup_required": False}


def test_login_wrong_password_after_setup(client):
    login(client, "secret")
    client.post("/api/auth/logout")
    r = client.post("/api/auth/login", json={"password": "wrong"})
    assert r.status_code == 401
    assert r.get_json()["error"] == "invalid_password"


def test_password_change_requires_current(client):
    login(client, "secret")
    # wrong current
    assert client.put("/api/auth/password", json={"current_password": "nope", "new_password": "n"}).status_code == 401
    # correct
    assert client.put("/api/auth/password", json={"current_password": "secret", "new_password": "new"}).status_code == 200
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"password": "new"}).status_code == 200
    assert client.post("/api/auth/login", json={"password": "secret"}).status_code == 401


# ---- apps CRUD ----
def test_add_app_requires_auth(client):
    assert client.post("/api/apps", json={"title": "t", "url": "https://x"}).status_code == 401


def test_add_app_valid(client):
    login(client)
    r = client.post("/api/apps", json={"title": "Sonarr", "url": "http://nas:8989", "group": "Media", "icon": "http://nas:8989/favicon.ico"})
    assert r.status_code == 201
    app = r.get_json()
    assert app["id"] and app["order"] == 0 and app["group"] == "Media"


def test_add_app_missing_fields(client):
    login(client)
    assert client.post("/api/apps", json={"title": "t"}).status_code == 400
    assert client.post("/api/apps", json={"url": "https://x"}).status_code == 400


def test_add_app_rejects_bad_url_scheme(client):
    login(client)
    r = client.post("/api/apps", json={"title": "x", "url": "javascript:alert(1)"})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_url_scheme"


def test_add_app_rejects_bad_icon_scheme(client):
    login(client)
    r = client.post("/api/apps", json={"title": "x", "url": "https://x", "icon": "javascript:alert(1)"})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_icon"


def test_add_app_accepts_data_image_icon(client):
    login(client)
    r = client.post("/api/apps", json={"title": "x", "url": "https://x", "icon": "data:image/png;base64,AAAA"})
    assert r.status_code == 201


def test_update_and_delete_app(client):
    login(client)
    app = client.post("/api/apps", json={"title": "A", "url": "http://a"}).get_json()
    r = client.put(f"/api/apps/{app['id']}", json={"title": "A2", "url": "http://a2"})
    assert r.status_code == 200 and r.get_json()["title"] == "A2"
    # update to javascript url blocked
    assert client.put(f"/api/apps/{app['id']}", json={"url": "javascript:x"}).status_code == 400
    assert client.delete(f"/api/apps/{app['id']}").status_code == 200
    assert client.get("/api/apps").get_json()["apps"] == []


def test_list_apps_public(client):
    login(client)
    client.post("/api/apps", json={"title": "P", "url": "http://p"})
    client.post("/api/auth/logout")
    # guests can still list
    r = client.get("/api/apps")
    assert r.status_code == 200 and len(r.get_json()["apps"]) == 1


# ---- ping + scrape ----
def test_ping_requires_auth(client):
    assert client.post("/api/apps/ping", json={}).status_code == 401


def test_ping_mocked(client, monkeypatch):
    login(client)
    client.post("/api/apps", json={"title": "A", "url": "http://a"})
    client.post("/api/apps", json={"title": "B", "url": "http://b"})
    monkeypatch.setattr("routes.apps.ping_url", lambda url: {"online": True, "status": 200, "latency_ms": 9})
    r = client.post("/api/apps/ping", json={})
    res = r.get_json()["results"]
    assert len(res) == 2 and all(v["online"] for v in res.values())


def test_ping_subset_by_ids(client, monkeypatch):
    login(client)
    a = client.post("/api/apps", json={"title": "A", "url": "http://a"}).get_json()
    client.post("/api/apps", json={"title": "B", "url": "http://b"}).get_json()
    monkeypatch.setattr("routes.apps.ping_url", lambda url: {"online": False, "status": None, "latency_ms": 1})
    res = client.post("/api/apps/ping", json={"ids": [a["id"]]}).get_json()["results"]
    assert list(res.keys()) == [a["id"]]


def test_scrape_requires_auth(client):
    assert client.post("/api/scrape", json={"url": "https://x"}).status_code == 401


def test_scrape_mocked(client, monkeypatch):
    login(client)
    monkeypatch.setattr("routes.apps.scrape_url",
                        lambda url: {"title": "T", "description": "D", "favicon": "F", "url": url})
    r = client.post("/api/scrape", json={"url": "example.com"})
    assert r.status_code == 200
    assert r.get_json() == {"title": "T", "description": "D", "favicon": "F", "url": "example.com"}


def test_scrape_missing_url(client):
    login(client)
    assert client.post("/api/scrape", json={}).status_code == 400


# ---- settings ----
def test_settings_seeded_defaults(client):
    d = client.get("/api/settings").get_json()
    assert d["portal_title"] == "My NAS"
    assert [e["name"] for e in d["search_engines"]] == ["Google", "Bing", "SearXNG"]
    assert all("%s" in e["url"] for e in d["search_engines"])


def test_settings_put_requires_auth(client):
    assert client.put("/api/settings", json={"portal_title": "X"}).status_code == 401


def test_settings_put_title(client):
    login(client)
    r = client.put("/api/settings", json={"portal_title": "My NAS", "wallpaper": "https://w/x.jpg"})
    assert r.status_code == 200 and r.get_json()["portal_title"] == "My NAS"


def test_settings_reject_engine_missing_placeholder(client):
    login(client)
    r = client.put("/api/settings", json={"search_engines": [{"id": "x", "name": "X", "url": "https://x.com"}]})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_search_engines"


def test_settings_reject_engine_bad_scheme(client):
    login(client)
    r = client.put("/api/settings", json={"search_engines": [{"id": "x", "name": "X", "url": "javascript:alert(1)//%s"}]})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_search_engines"


def test_settings_reject_default_engine_not_in_ids(client):
    login(client)
    assert client.put("/api/settings", json={"default_engine": "nonexistent"}).status_code == 400


def test_settings_reject_bad_title_type(client):
    login(client)
    assert client.put("/api/settings", json={"portal_title": 123}).status_code == 400


def test_settings_reject_wallpaper_too_long(client):
    login(client)
    assert client.put("/api/settings", json={"wallpaper": "x" * 4001}).status_code == 400


def test_settings_default_engine_must_match_when_engines_change(client):
    login(client)
    r = client.put("/api/settings", json={
        "search_engines": [{"id": "g", "name": "G", "url": "https://g.com/?q=%s"}],
        "default_engine": "google",  # no longer in the new engine list
    })
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_default_engine"


# ---- storage / config ----
def test_config_files_seeded(client, config_dir):
    client.get("/api/settings")
    client.get("/api/apps")
    assert (config_dir / "settings.json").exists()
    assert (config_dir / "apps.json").exists()


def test_settings_json_is_readable_pretty_json(client, config_dir):
    client.get("/api/settings")
    text = (config_dir / "settings.json").read_text()
    assert json.loads(text)["portal_title"] == "My NAS"  # valid JSON on disk


def test_secret_key_persisted(config_dir):
    import app as app_module
    app_module.create_app()
    assert (config_dir / "secret.json").exists()


def test_corrupt_auth_fails_closed(client, config_dir):
    """A corrupt auth.json must raise (500), not silently revert to setup mode
    (which would let anyone set a new password). The file must not be overwritten."""
    (config_dir / "auth.json").write_text("not json")
    r = client.get("/api/auth/check")
    assert r.status_code == 500
    assert (config_dir / "auth.json").read_text() == "not json"


def test_app_add_uses_cross_process_lock(client, config_dir):
    """Adding an app must take the fcntl lock (the .lock file appears), so
    concurrent edits across workers/threads can't clobber each other."""
    login(client)
    client.post("/api/apps", json={"title": "A", "url": "http://a"})
    assert (config_dir / "apps.json.lock").exists()
    assert len(client.get("/api/apps").get_json()["apps"]) == 1