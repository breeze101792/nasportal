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


# ---- bulk operations ----
def _add_n(client, n, prefix="A"):
    return [client.post("/api/apps", json={"title": f"{prefix}{i}", "url": f"http://{prefix}{i}"}).get_json()
            for i in range(n)]


def test_bulk_delete_requires_auth(client):
    assert client.post("/api/apps/bulk/delete", json={"ids": ["x"]}).status_code == 401


def test_bulk_delete_apps(client):
    login(client)
    a, b, c = _add_n(client, 3)
    r = client.post("/api/apps/bulk/delete", json={"ids": [a["id"], c["id"]]})
    assert r.status_code == 200
    assert r.get_json() == {"deleted": 2, "missing": []}
    remaining = client.get("/api/apps").get_json()["apps"]
    assert [x["id"] for x in remaining] == [b["id"]]


def test_bulk_delete_reports_missing_ids(client):
    login(client)
    a = _add_n(client, 1)[0]
    r = client.post("/api/apps/bulk/delete", json={"ids": [a["id"], "does-not-exist"]})
    assert r.status_code == 200
    assert r.get_json()["deleted"] == 1
    assert r.get_json()["missing"] == ["does-not-exist"]


def test_bulk_delete_requires_ids(client):
    login(client)
    assert client.post("/api/apps/bulk/delete", json={}).status_code == 400
    assert client.post("/api/apps/bulk/delete", json={"ids": []}).status_code == 400
    assert client.post("/api/apps/bulk/delete", json={"ids": ["", ""]}).status_code == 400


def test_bulk_group_requires_auth(client):
    assert client.post("/api/apps/bulk/group", json={"ids": ["x"], "group": "g"}).status_code == 401


def test_bulk_group_apps(client):
    login(client)
    a, b, c = _add_n(client, 3)
    r = client.post("/api/apps/bulk/group", json={"ids": [a["id"], b["id"]], "group": "Media"})
    assert r.status_code == 200
    assert r.get_json() == {"updated": 2, "missing": []}
    by_id = {x["id"]: x for x in client.get("/api/apps").get_json()["apps"]}
    assert by_id[a["id"]]["group"] == "Media"
    assert by_id[b["id"]]["group"] == "Media"
    assert by_id[c["id"]]["group"] == ""  # untouched


def test_bulk_group_clears_with_empty(client):
    login(client)
    a = client.post("/api/apps", json={"title": "A", "url": "http://a", "group": "Media"}).get_json()
    r = client.post("/api/apps/bulk/group", json={"ids": [a["id"]], "group": "  "})
    assert r.status_code == 200
    assert client.get("/api/apps").get_json()["apps"][0]["group"] == ""


def test_bulk_group_rejects_too_long(client):
    login(client)
    a = _add_n(client, 1)[0]
    r = client.post("/api/apps/bulk/group", json={"ids": [a["id"]], "group": "x" * 101})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_group"


def test_bulk_group_requires_ids(client):
    login(client)
    assert client.post("/api/apps/bulk/group", json={"group": "g"}).status_code == 400


def test_bulk_order_requires_auth(client):
    assert client.post("/api/apps/bulk/order", json={"items": [{"id": "x", "order": 0}]}).status_code == 401


def test_bulk_order_apps(client):
    login(client)
    a, b, c = _add_n(client, 3)
    # Each add gets the next sequential order: a=0, b=1, c=2. Reverse them.
    items = [{"id": a["id"], "order": 2}, {"id": b["id"], "order": 1}, {"id": c["id"], "order": 0}]
    r = client.post("/api/apps/bulk/order", json={"items": items})
    assert r.status_code == 200
    body = r.get_json()
    assert body["updated"] == 3
    assert body["missing"] == []
    by_id = {x["id"]: x for x in client.get("/api/apps").get_json()["apps"]}
    assert by_id[a["id"]]["order"] == 2
    assert by_id[b["id"]]["order"] == 1
    assert by_id[c["id"]]["order"] == 0


def test_bulk_order_reports_missing_ids(client):
    login(client)
    r = client.post("/api/apps/bulk/order", json={
        "items": [{"id": "nope", "order": 0}, {"id": "also-nope", "order": 1}]
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body["updated"] == 0
    assert set(body["missing"]) == {"nope", "also-nope"}


def test_bulk_order_rejects_bad_input(client):
    login(client)
    # No items at all
    assert client.post("/api/apps/bulk/order", json={}).status_code == 400
    # Empty list
    assert client.post("/api/apps/bulk/order", json={"items": []}).status_code == 400
    # Non-int order
    r = client.post("/api/apps/bulk/order", json={"items": [{"id": "x", "order": "bad"}]})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_order"
    # Empty id
    r = client.post("/api/apps/bulk/order", json={"items": [{"id": "", "order": 0}]})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_id"
    # Non-dict item
    r = client.post("/api/apps/bulk/order", json={"items": ["nope"]})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_item"


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
    assert [e["name"] for e in d["search_engines"]] == ["Google", "Bing", "DuckDuckGo", "SearXNG"]
    assert all("%s" in e["url"] for e in d["search_engines"])


def test_settings_theme_default_is_dark(client):
    assert client.get("/api/settings").get_json()["theme"] == "dark"


def test_settings_put_theme_requires_auth(client):
    assert client.put("/api/settings", json={"theme": "light"}).status_code == 401


def test_settings_put_theme(client):
    login(client)
    r = client.put("/api/settings", json={"theme": "light"})
    assert r.status_code == 200 and r.get_json()["theme"] == "light"
    # persisted
    assert client.get("/api/settings").get_json()["theme"] == "light"
    # system accepted
    assert client.put("/api/settings", json={"theme": "system"}).get_json()["theme"] == "system"
    # other fields are preserved when only theme is sent
    d = client.get("/api/settings").get_json()
    assert d["portal_title"] == "My NAS"


def test_settings_reject_invalid_theme(client):
    login(client)
    r = client.put("/api/settings", json={"theme": "neon"})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_theme"


def test_settings_portal_width_default(client):
    assert client.get("/api/settings").get_json()["portal_width"] == 80


def test_settings_put_portal_width_requires_auth(client):
    assert client.put("/api/settings", json={"portal_width": 90}).status_code == 401


def test_settings_put_portal_width(client):
    login(client)
    r = client.put("/api/settings", json={"portal_width": 90})
    assert r.status_code == 200 and r.get_json()["portal_width"] == 90
    assert client.get("/api/settings").get_json()["portal_width"] == 90
    # floats are accepted and stored as int
    assert client.put("/api/settings", json={"portal_width": 75.0}).get_json()["portal_width"] == 75


@pytest.mark.parametrize("bad", [49, 101, 0, 200, "80", True, None])
def test_settings_reject_invalid_portal_width(client, bad):
    login(client)
    r = client.put("/api/settings", json={"portal_width": bad})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_portal_width"


def test_settings_home_layout_default(client):
    assert client.get("/api/settings").get_json()["home_layout"] == "grouped"


def test_settings_put_home_layout_requires_auth(client):
    assert client.put("/api/settings", json={"home_layout": "flow"}).status_code == 401


def test_settings_put_home_layout(client):
    login(client)
    r = client.put("/api/settings", json={"home_layout": "flow"})
    assert r.status_code == 200 and r.get_json()["home_layout"] == "flow"
    assert client.get("/api/settings").get_json()["home_layout"] == "flow"


def test_settings_reject_invalid_home_layout(client):
    login(client)
    r = client.put("/api/settings", json={"home_layout": "masonry"})
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_home_layout"


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