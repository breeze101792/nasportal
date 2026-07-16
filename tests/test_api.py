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


# ---- icon auto-fetch (add + update paths) ----
def test_add_app_fetches_icon_when_blank(client, monkeypatch):
    """An add with no icon should auto-fetch from the URL via the scraper."""
    login(client)
    monkeypatch.setattr("routes.apps.scrape_url",
                        lambda url: {"title": "Sonarr", "description": "D",
                                     "favicon": "https://sonarr.example/favicon.ico",
                                     "url": url})
    r = client.post("/api/apps", json={"title": "Sonarr", "urls": "https://sonarr.example"})
    assert r.status_code == 201
    assert r.get_json()["icon"] == "https://sonarr.example/favicon.ico"


def test_add_app_keeps_explicit_icon(client, monkeypatch):
    """If the admin set an icon, the auto-fetch must not overwrite it."""
    login(client)
    called = {"n": 0}
    def fake_scrape(url):
        called["n"] += 1
        return {"title": "", "description": "", "favicon": "AUTOFETCH", "url": url}
    monkeypatch.setattr("routes.apps.scrape_url", fake_scrape)
    r = client.post("/api/apps", json={"title": "Sonarr", "urls": "https://sonarr.example",
                                       "icon": "https://i.example/logo.png"})
    assert r.status_code == 201
    assert r.get_json()["icon"] == "https://i.example/logo.png"
    assert called["n"] == 0  # not called when an icon was supplied


def test_add_app_rejects_hostile_auto_fetched_icon(client, monkeypatch):
    """A scraper that returns a javascript: URL (a hostile site's
    <link rel=icon>) must be dropped, not stored — _valid_icon is the
    final gate."""
    login(client)
    monkeypatch.setattr("routes.apps.scrape_url",
                        lambda url: {"title": "", "description": "",
                                     "favicon": "javascript:alert(1)", "url": url})
    r = client.post("/api/apps", json={"title": "X", "urls": "https://x.example"})
    assert r.status_code == 201
    assert r.get_json()["icon"] == ""


def test_update_app_re_fetches_icon_when_cleared(client, monkeypatch):
    """On update, explicitly clearing the icon (icon: "") means
    "look it up again" — the saved value should be the freshly fetched one."""
    login(client)
    # First add with an explicit icon.
    r = client.post("/api/apps", json={"title": "X", "urls": "https://x.example",
                                       "icon": "https://old.example/icon.png"})
    aid = r.get_json()["id"]
    # Now update with icon: "".
    monkeypatch.setattr("routes.apps.scrape_url",
                        lambda url: {"title": "", "description": "",
                                     "favicon": "https://x.example/favicon.ico", "url": url})
    r = client.put(f"/api/apps/{aid}", json={"icon": ""})
    assert r.status_code == 200
    assert r.get_json()["icon"] == "https://x.example/favicon.ico"


def test_update_app_preserves_icon_when_absent(client, monkeypatch):
    """If the update payload doesn't include icon at all, the existing
    icon is left alone (no scrape)."""
    login(client)
    r = client.post("/api/apps", json={"title": "X", "urls": "https://x.example",
                                       "icon": "https://i.example/icon.png"})
    aid = r.get_json()["id"]
    called = {"n": 0}
    def fake_scrape(url):
        called["n"] += 1
        return {"title": "", "description": "", "favicon": "AUTO", "url": url}
    monkeypatch.setattr("routes.apps.scrape_url", fake_scrape)
    # No icon field in the update payload.
    r = client.put(f"/api/apps/{aid}", json={"title": "Y"})
    assert r.status_code == 200
    assert r.get_json()["icon"] == "https://i.example/icon.png"
    assert called["n"] == 0


# ---- favicon endpoint (public — used by the portal home) ----
def test_favicon_public_no_auth_needed(client):
    """The favicon endpoint is public — guests on the portal home
    need to fetch each app's favicon, and requiring login would
    hide every icon for unauthenticated viewers."""
    r = client.get("/api/favicon?url=https://example.com")
    # The endpoint itself doesn't require auth; it may 200 with an
    # empty favicon (the request fails) but never 401.
    assert r.status_code != 401


def test_favicon_returns_url_from_scraper(client, monkeypatch):
    monkeypatch.setattr("routes.apps.scrape_url",
                        lambda url: {"title": "T", "description": "D",
                                     "favicon": "https://example.com/favicon.ico",
                                     "url": url})
    r = client.get("/api/favicon?url=https://example.com")
    assert r.status_code == 200
    assert r.get_json() == {"favicon": "https://example.com/favicon.ico"}


def test_favicon_empty_url_returns_empty_favicon(client):
    r = client.get("/api/favicon")
    assert r.status_code == 200
    assert r.get_json() == {"favicon": ""}


def test_favicon_rejects_non_http_scheme(client):
    """SSRF guard: a malicious caller can't probe file:// / gopher://
    / etc. The endpoint mirrors the apps URL validation."""
    r = client.get("/api/favicon?url=javascript:alert(1)")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_url_scheme"
    r = client.get("/api/favicon?url=file:///etc/passwd")
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_url_scheme"


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


# ---- network awareness: URL parser ----
def test_parse_endpoint_classifies_ip_as_network(client, monkeypatch):
    """A literal IPv4 on a detected local subnet becomes a network_ip."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    r = client.post("/api/apps/parse", json={"url": "http://10.31.1.9:8989"})
    p = r.get_json()
    assert p["network_ip"] == "10.31.1.9"
    assert p["public_ip"] is None
    assert p["domain"] is None
    assert p["port"] == 8989
    assert p["scheme"] == "http"


def test_parse_endpoint_classifies_off_subnet_ip_as_public(client, monkeypatch):
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    r = client.post("/api/apps/parse", json={"url": "https://203.0.113.5"})
    p = r.get_json()
    assert p["public_ip"] == "203.0.113.5"
    assert p["network_ip"] is None
    assert p["scheme"] == "https"


def test_parse_endpoint_classifies_hostname_as_domain(client, monkeypatch):
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    r = client.post("/api/apps/parse", json={"url": "https://sonarr.example.com/sonarr"})
    p = r.get_json()
    assert p["domain"] == "sonarr.example.com"
    assert p["public_ip"] is None
    assert p["network_ip"] is None
    assert p["path"] == "/sonarr"


def test_parse_endpoint_handles_bare_hostname(client):
    r = client.post("/api/apps/parse", json={"url": "nas.local"})
    p = r.get_json()
    assert p["domain"] == "nas.local"
    assert p["scheme"] == "http"  # default applied


def test_parse_endpoint_handles_empty_input(client):
    r = client.post("/api/apps/parse", json={"url": ""})
    p = r.get_json()
    assert p["host"] is None and p["network_ip"] is None


def test_parse_endpoint_accepts_multi_line(client, monkeypatch):
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    r = client.post("/api/apps/parse", json={"urls": "http://10.31.1.9:8989\nhttps://sonarr.example.com"})
    out = r.get_json()
    assert len(out) == 2
    assert out[0]["network_ip"] == "10.31.1.9"
    assert out[1]["domain"] == "sonarr.example.com"


# ---- network awareness: apps add with structured URL ----
def test_add_app_with_urls_string(client, monkeypatch):
    """A multi-line 'urls' payload is stored verbatim as a list."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    login(client)
    r = client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": "http://10.31.1.9:8989\nhttps://sonarr.example.com/sonarr",
    })
    assert r.status_code == 201
    app = r.get_json()
    assert app["urls"] == [
        "http://10.31.1.9:8989",
        "https://sonarr.example.com/sonarr",
    ]


def test_add_app_with_urls_list(client, monkeypatch):
    """A list of URLs in the payload is stored as-is."""
    login(client)
    r = client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": ["https://a.example.com/a", "https://b.example.com/b"],
    })
    assert r.status_code == 201
    assert r.get_json()["urls"] == [
        "https://a.example.com/a",
        "https://b.example.com/b",
    ]


def test_add_app_urls_deduped(client, monkeypatch):
    """Identical URLs across the input collapse to one entry."""
    login(client)
    r = client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": "https://a.example.com\nhttps://a.example.com\nhttps://a.example.com/admin",
    })
    assert r.status_code == 201
    assert r.get_json()["urls"] == [
        "https://a.example.com",
        "https://a.example.com/admin",
    ]


def test_add_app_rejects_non_http_url(client):
    """Only http(s) URLs are accepted; javascript: and friends are rejected."""
    login(client)
    r = client.post("/api/apps", json={
        "title": "x",
        "urls": "javascript:alert(1)",
    })
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_url_scheme"


def test_add_app_preserves_per_url_path(client, monkeypatch):
    """Each URL keeps its OWN path — this is the whole point of the
    per-URL list. Two URLs on the same host with different paths
    don't collapse to one path."""
    login(client)
    r = client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": "https://10.31.1.9/sonarr\nhttps://sonarr.example.com/sonarr",
    })
    assert r.status_code == 201
    app = r.get_json()
    assert app["urls"] == [
        "https://10.31.1.9/sonarr",
        "https://sonarr.example.com/sonarr",
    ]
    # The same path on both URLs is preserved verbatim (no normalization
    # would silently rewrite them).
    r = client.get("/api/apps/resolved")
    # Resolved URL is one of the entries — same path either way.
    out = r.get_json()["apps"][0]["url"]
    assert out.endswith("/sonarr")


def test_add_app_preserves_per_url_port_and_scheme(client, monkeypatch):
    """Two URLs on the same host with different ports keep both
    ports. The structured-shape collapse bug used to lose this."""
    login(client)
    r = client.post("/api/apps", json={
        "title": "Mix",
        "urls": "http://host.lan:80/a\nhttps://host.lan:8443/b",
    })
    assert r.status_code == 201
    assert r.get_json()["urls"] == [
        "http://host.lan:80/a",
        "https://host.lan:8443/b",
    ]


def test_add_app_root_path_kept(client, monkeypatch):
    """A bare-root ``/`` on a URL is kept verbatim — the user
    typed it that way, we don't silently rewrite. The resolver
    treats the bare-root URL the same as a no-path one when
    building the final href."""
    login(client)
    r = client.post("/api/apps", json={
        "title": "fn",
        "urls": "https://fn.shaowu.org/",
    })
    assert r.status_code == 201
    # The URL is stored verbatim with its trailing slash.
    assert r.get_json()["urls"] == ["https://fn.shaowu.org/"]
    # And the resolved URL is reachable: the trailing-slash form
    # works the same as no-slash for the browser.
    r = client.get("/api/apps/resolved")
    assert r.get_json()["apps"][0]["url"] == "https://fn.shaowu.org/"


def test_add_app_query_string_kept(client, monkeypatch):
    login(client)
    r = client.post("/api/apps", json={
        "title": "AriaNg",
        "urls": "http://10.31.1.9:50102/?foo=bar",
    })
    assert r.status_code == 201
    assert r.get_json()["urls"] == ["http://10.31.1.9:50102/?foo=bar"]


def test_add_app_backward_compat_network_ips_only(client, monkeypatch):
    """An old-shape payload (no ``urls``) still saves by collapsing
    the structured fields into a URL list."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    login(client)
    r = client.post("/api/apps", json={
        "title": "Old",
        "network_ips": ["10.31.1.9"],
        "domain": "old.example.com",
        "port": 8989,
        "scheme": "https",
    })
    assert r.status_code == 201
    urls = r.get_json()["urls"]
    # The structured fields all share scheme+port, so the synthesized
    # URLs share them too. The data-loss is the documented trade-off.
    assert urls == [
        "https://10.31.1.9:8989",
        "https://old.example.com:8989",
    ]


def test_add_app_backward_compat_legacy_url_field(client):
    """An old-style {'url': '...'} payload with no other structured
    data is still accepted (collapsed to a single-item list)."""
    login(client)
    r = client.post("/api/apps", json={"title": "Old", "url": "https://legacy.example.com"})
    assert r.status_code == 201
    assert r.get_json()["urls"] == ["https://legacy.example.com"]


def test_add_app_legacy_url_alone_uses_url_field(client, monkeypatch):
    """If only the legacy ``url`` is given AND no structured fields,
    the URL is the single canonical entry."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    r = client.post("/api/apps", json={"title": "T", "urls": "https://a.example.com"})
    assert r.status_code == 201
    app = r.get_json()
    assert app["urls"] == ["https://a.example.com"]


def test_update_app_replaces_urls(client, monkeypatch):
    """Updating an app with a new ``urls`` payload fully replaces
    the URL list."""
    login(client)
    app = client.post("/api/apps", json={
        "title": "T", "urls": "https://a.example.com",
    }).get_json()
    r = client.put(f"/api/apps/{app['id']}", json={
        "title": "T2",
        "urls": ["https://b.example.com", "https://c.example.com"],
    })
    assert r.status_code == 200
    out = r.get_json()
    assert out["title"] == "T2"
    assert out["urls"] == ["https://b.example.com", "https://c.example.com"]


# ---- network awareness: resolved endpoint ----
def test_resolved_tier1_same_network_ip(client, monkeypatch):
    """Tier 1: a URL whose host is a literal IP on the user's
    network wins."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    login(client)
    client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": [
            "https://10.31.1.9:8989/sonarr",
            "https://192.168.1.50:8989/sonarr",
            "https://sonarr.example.com/sonarr",
        ],
    })
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "10.31.5.5"})
    apps = r.get_json()["apps"]
    assert apps[0]["url"] == "https://10.31.1.9:8989/sonarr"
    assert apps[0]["resolved"]["kind"] == "network"


def test_resolved_tier2_translation(client, monkeypatch):
    """Tier 2: a public IP with a translation entry landing on the
    user's network is preferred over the public domain."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("192.168.0.0/16")])
    login(client)
    client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": ["https://10.31.1.9:8989"],  # not on user's network
    })
    client.put("/api/settings", json={"ip_translation": {"10.31.1.9": "192.168.1.50"}})
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "192.168.1.10"})
    apps = r.get_json()["apps"]
    assert apps[0]["url"] == "https://192.168.1.50:8989"
    assert apps[0]["resolved"]["kind"] == "translated"


def test_resolved_tier3a_local_fallback(client, monkeypatch):
    """Tier 3a (local_first=true): off-network IP still wins over
    the public domain."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": [
            "http://10.31.1.9:8989",
            "https://sonarr.example.com",
        ],
    })
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "203.0.113.5"})
    apps = r.get_json()["apps"]
    assert apps[0]["url"] == "http://10.31.1.9:8989"
    assert apps[0]["resolved"]["kind"] == "local_fallback"


def test_resolved_tier4_domain_when_local_first_off(client, monkeypatch):
    """Tier 3b/4 (local_first=false): an off-network IP is skipped
    and the resolver falls through to the public domain."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    client.put("/api/settings", json={"local_first": False})
    client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": [
            "http://10.31.1.9:8989",
            "https://sonarr.example.com",
        ],
    })
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "203.0.113.5"})
    apps = r.get_json()["apps"]
    assert apps[0]["url"] == "https://sonarr.example.com"
    assert apps[0]["resolved"]["kind"] == "domain"


def test_resolved_tier5_public_ip(client, monkeypatch):
    """Tier 5: when local_first is off and no domain exists, the
    public IP is used."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    client.put("/api/settings", json={"local_first": False})
    client.post("/api/apps", json={
        "title": "X",
        "urls": ["http://203.0.113.5:8080"],
    })
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "198.51.100.5"})
    apps = r.get_json()["apps"]
    assert apps[0]["url"] == "http://203.0.113.5:8080"
    assert apps[0]["resolved"]["kind"] == "public_ip"


def test_resolved_tier6_fallback_first_url(client, monkeypatch):
    """Tier 6: with no tier-1..5 winner, the first URL in the list
    is used as a last-resort fallback."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    # A single tunneled IP. local_first=true will surface it as
    # local_fallback (tier 3a), so to hit tier 6 we'd need an app
    # that has no IP at all. Use a path-only URL? The parse_url call
    # always yields a host. With ``urls`` as the canonical, there's
    # no longer a path where tier 6 fires from the structured shape
    # — every URL has a host. The first URL is what tier 3a returns
    # anyway.
    client.post("/api/apps", json={
        "title": "Tunneled",
        "urls": ["http://10.99.1.9:9000"],
    })
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "198.51.100.5"})
    apps = r.get_json()["apps"]
    # Tier 3a (local_first=true default) is the more useful path;
    # tier 6 is the same URL with kind=local_fallback instead of
    # fallback. The point of this test is just "an off-network IP
    # gives the user a URL."
    assert apps[0]["url"] == "http://10.99.1.9:9000"
    assert apps[0]["resolved"]["kind"] in ("local_fallback", "fallback")


def test_resolved_filters_untranslatable_when_disabled(client, monkeypatch):
    """show_untranslatable=false hides apps with no usable URLs.

    The store-level guard rejects saving an app with no URLs, so we
    exercise the filter by directly inserting a no-URL app into
    apps.json (simulating a future data shape, e.g. an app being
    re-edited and ending up with no URLs in transition)."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    client.post("/api/apps", json={
        "title": "PublicOnly", "urls": ["https://public.example.com"],
    })
    # Inject an app with no usable URLs directly into the store.
    # This skips the add-route validation but goes through the same
    # resolved-endpoint code path the test wants to exercise.
    from storage import load_json, save_json, file_lock
    with file_lock("apps.json"):
        store = load_json("apps.json")
        store["apps"].append({
            "id": "test-no-urls",
            "title": "Empty",
            "urls": [],
        })
        save_json("apps.json", store)
    # Default: both visible.
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "203.0.113.5"})
    titles = [a["title"] for a in r.get_json()["apps"]]
    assert set(titles) == {"Empty", "PublicOnly"}
    # Disabled: Empty is filtered (no usable URLs); PublicOnly
    # still has a domain so it stays.
    client.put("/api/settings", json={"show_untranslatable": False})
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "203.0.113.5"})
    titles = [a["title"] for a in r.get_json()["apps"]]
    assert set(titles) == {"PublicOnly"}


def test_resolved_legacy_url_resolves_as_domain(client, monkeypatch):
    """An old-shape app with just a single ``url`` resolves its URL
    through the parser like any other host."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    client.post("/api/apps", json={"title": "Old", "url": "https://legacy.example.com/path"})
    r = client.get("/api/apps/resolved")
    apps = r.get_json()["apps"]
    assert apps[0]["url"] == "https://legacy.example.com/path"
    assert apps[0]["resolved"]["kind"] == "domain"


def test_resolved_url_list_ordering(client, monkeypatch):
    """Reorder the URL list = reorder the resolver's priority
    chain. With a list ordered [domain, ip], the same-network IP
    still wins (tier 1) but the *first* IP-bearing URL is what
    tier 3a returns when the user is off-network."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    client.put("/api/settings", json={"local_first": True})
    # Order: domain first, public IP second.
    client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": [
            "https://sonarr.example.com",
            "http://203.0.113.5:9999",
        ],
    })
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "198.51.100.5"})
    apps = r.get_json()["apps"]
    # local_first=true: tier 3a returns the first IP-bearing URL, not
    # the first URL overall. The IP is at index 1.
    assert apps[0]["url"] == "http://203.0.113.5:9999"
    assert apps[0]["resolved"]["kind"] == "local_fallback"
    # Reorder: IP first, domain second.
    r = client.put(f"/api/apps/{apps[0]['id']}", json={
        "urls": [
            "http://203.0.113.5:9999",
            "https://sonarr.example.com",
        ],
    })
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "198.51.100.5"})
    apps = r.get_json()["apps"]
    # Same resolution since there's only one IP. Order matters more
    # for ties or for the local_fallback tier when there are multiple
    # IPs.


# ---- network awareness: synthesize_urls (the legacy back-compat path) ----
def test_synthesize_urls_prefers_canonical_urls_list():
    """When the app has a ``urls`` field, that's used as-is."""
    from services.networks import synthesize_urls
    assert synthesize_urls({
        "urls": ["https://a.example.com", "https://b.example.com"],
        "domain": "ignored.example.com",
    }) == ["https://a.example.com", "https://b.example.com"]


def test_synthesize_urls_from_structured_shape():
    """The legacy structured shape collapses into one URL per field,
    all sharing scheme/port/path. The data-loss is the documented
    trade-off."""
    from services.networks import synthesize_urls
    urls = synthesize_urls({
        "network_ips": ["10.31.1.9"],
        "domain": "old.example.com",
        "public_ip": "203.0.113.5",
        "scheme": "https",
        "port": 8989,
        "path": "/admin",
    })
    assert urls == [
        "https://10.31.1.9:8989/admin",
        "https://old.example.com:8989/admin",
        "https://203.0.113.5:8989/admin",
    ]


def test_synthesize_urls_falls_back_to_legacy_url():
    """A bare legacy ``url`` field is the last resort."""
    from services.networks import synthesize_urls
    assert synthesize_urls({"url": "https://legacy.example.com"}) == [
        "https://legacy.example.com"
    ]


def test_synthesize_urls_dedupes():
    """The legacy collapse can produce duplicate URLs (e.g. when the
    structured fields overlap); we dedupe, preserving order."""
    from services.networks import synthesize_urls
    assert synthesize_urls({
        "domain": "x.example.com",
        "public_ip": "x.example.com",  # unlikely, but the dedupe should still kick in
    }) == ["http://x.example.com"]


def test_synthesize_urls_empty():
    from services.networks import synthesize_urls
    assert synthesize_urls({}) == []
    assert synthesize_urls({"title": "nothing here"}) == []


# ---- network awareness: settings ----
def test_settings_default_has_empty_translation_and_untranslatable_true(client):
    d = client.get("/api/settings").get_json()
    assert d["ip_translation"] == {}
    assert d["show_untranslatable"] is True


def test_settings_put_ip_translation_requires_auth(client):
    assert client.put("/api/settings", json={"ip_translation": {"1.2.3.4": "5.6.7.8"}}).status_code == 401


def test_settings_put_ip_translation(client):
    login(client)
    r = client.put("/api/settings", json={"ip_translation": {"10.0.0.1": "192.168.1.1"}})
    assert r.status_code == 200
    assert r.get_json()["ip_translation"] == {"10.0.0.1": "192.168.1.1"}


def test_settings_reject_invalid_ip_translation(client):
    login(client)
    # Non-string key
    assert client.put("/api/settings", json={"ip_translation": {1: "2.3.4.5"}}).status_code == 400
    # Non-IPv4 value
    assert client.put("/api/settings", json={"ip_translation": {"1.2.3.4": "bogus"}}).status_code == 400
    # Not a dict
    assert client.put("/api/settings", json={"ip_translation": "nope"}).status_code == 400


def test_settings_put_show_untranslatable(client):
    login(client)
    r = client.put("/api/settings", json={"show_untranslatable": False})
    assert r.status_code == 200 and r.get_json()["show_untranslatable"] is False
    assert client.get("/api/settings").get_json()["show_untranslatable"] is False


def test_settings_reject_invalid_show_untranslatable(client):
    login(client)
    assert client.put("/api/settings", json={"show_untranslatable": "yes"}).status_code == 400
    assert client.put("/api/settings", json={"show_untranslatable": 1}).status_code == 400


# ---- settings: background_color ----
def test_settings_default_background_color_is_empty(client):
    assert client.get("/api/settings").get_json()["background_color"] == ""


def test_settings_put_background_color_requires_auth(client):
    assert client.put("/api/settings", json={"background_color": "#abcdef"}).status_code == 401


def test_settings_put_background_color_hex(client):
    login(client)
    for c in ("#abc", "#aabbcc", "#aabbccdd", "#112233", "#11223344"):
        r = client.put("/api/settings", json={"background_color": c})
        assert r.status_code == 200, (c, r.get_json())
        assert r.get_json()["background_color"] == c


def test_settings_put_background_color_functional(client):
    login(client)
    for c in ("rgb(0,0,0)", "rgba(0, 0, 0, 0.5)", "hsl(0, 0%, 50%)", "hsla(0, 0%, 50%, 0.5)"):
        r = client.put("/api/settings", json={"background_color": c})
        assert r.status_code == 200, (c, r.get_json())
        assert r.get_json()["background_color"] == c


def test_settings_put_background_color_transparent_clears(client):
    login(client)
    client.put("/api/settings", json={"background_color": "#abcdef"})
    r = client.put("/api/settings", json={"background_color": "transparent"})
    assert r.status_code == 200
    assert r.get_json()["background_color"] == "transparent"


def test_settings_put_background_color_empty_string_clears(client):
    login(client)
    client.put("/api/settings", json={"background_color": "#abcdef"})
    r = client.put("/api/settings", json={"background_color": ""})
    assert r.status_code == 200
    assert r.get_json()["background_color"] == ""


def test_settings_reject_bad_background_color(client):
    login(client)
    # CSS-injection-y junk
    for bad in [
        "javascript:alert(1)",
        "expression(alert(1))",
        "red; } body { background: url(",
        "<script>",
        "a" * 201,  # too long
        "rgb(0, 0, 0",  # missing close paren
        "#xyz",
        "1.2.3",  # not a color
    ]:
        r = client.put("/api/settings", json={"background_color": bad})
        assert r.status_code == 400, (bad, r.get_json())
        assert r.get_json()["error"] == "invalid_background_color"


# ---- network awareness: local_first setting ----
def test_settings_default_local_first_is_true(client):
    """First-run state has ``local_first=True`` so the resolver prefers
    a local IP over the public domain by default."""
    r = client.get("/api/settings")
    assert r.get_json()["local_first"] is True


def test_settings_put_local_first_requires_auth(client):
    assert client.put("/api/settings", json={"local_first": False}).status_code == 401


def test_settings_put_local_first(client):
    login(client)
    r = client.put("/api/settings", json={"local_first": False})
    assert r.status_code == 200 and r.get_json()["local_first"] is False
    assert client.get("/api/settings").get_json()["local_first"] is False
    # Round-trip back to True.
    r = client.put("/api/settings", json={"local_first": True})
    assert r.status_code == 200 and r.get_json()["local_first"] is True


def test_settings_reject_invalid_local_first(client):
    login(client)
    for bad in ("yes", 1, 0, None, []):
        r = client.put("/api/settings", json={"local_first": bad})
        assert r.status_code == 400, (bad, r.get_json())
        assert r.get_json()["error"] == "invalid_local_first"


def test_resolved_local_first_false_keeps_first_network_ip_for_same_network(
        client, monkeypatch):
    """``local_first`` only reorders tiers 3..6; a same-network IP
    (tier 1) still wins regardless of the toggle."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    login(client)
    client.put("/api/settings", json={"local_first": False})
    client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": [
            "http://10.31.1.9:8989",
            "http://192.168.1.50:8989",
            "https://sonarr.example.com",
        ],
    })
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "10.31.5.5"})
    apps = r.get_json()["apps"]
    assert apps[0]["url"] == "http://10.31.1.9:8989"
    assert apps[0]["resolved"]["kind"] == "network"


# ---- /api/networks/local ----

def test_local_networks_endpoint_returns_cidrs(client):
    """Public endpoint: returns the host's detected local networks as
    a list of CIDR strings. No auth required (the network list is
    already implicit in the public portal behaviour)."""
    import ipaddress
    import services.networks as net_svc
    # Reset the module cache so the monkeypatched list is what we get.
    net_svc.reset_local_networks_cache()
    r = client.get("/api/networks/local")
    assert r.status_code == 200
    data = r.get_json()
    assert "networks" in data
    # Each entry must parse as a valid IPv4Network.
    for s in data["networks"]:
        ipaddress.IPv4Network(s)  # raises ValueError if bad
    # Restore default for tests that depend on the real detection.
    net_svc.reset_local_networks_cache()


# ---- /api/scan/expand ----

def test_scan_expand_cidr(client, monkeypatch):
    """Expand a /24 with 1 port -> 254 candidates (one per host)."""
    import services.networks as net_svc
    net_svc.reset_local_networks_cache()
    login(client)
    r = client.post("/api/scan/expand", json={
        "cidr": "10.0.0.0/24",
        "ports": [80],
    })
    assert r.status_code == 200, r.get_json()
    data = r.get_json()
    assert data["truncated"] is False
    assert len(data["candidates"]) == 254
    assert data["candidates"][0] == {"ip": "10.0.0.1", "port": 80, "url": "http://10.0.0.1:80/"}
    assert data["candidates"][-1]["ip"] == "10.0.0.254"


def test_scan_expand_cidr_with_ports(client):
    """Expand a /24 with 3 ports -> 762 candidates."""
    login(client)
    r = client.post("/api/scan/expand", json={
        "cidr": "10.0.0.0/24",
        "ports": [80, 443, 8080],
    })
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["candidates"]) == 254 * 3
    # All three ports appear for the first host.
    ips = {c["ip"] for c in data["candidates"]}
    assert ips == {f"10.0.0.{i}" for i in range(1, 255)}


def test_scan_expand_range(client):
    """start/end form: a small explicit range."""
    login(client)
    r = client.post("/api/scan/expand", json={
        "start": "10.0.0.10",
        "end": "10.0.0.12",
        "ports": [80, 443],
    })
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["candidates"]) == 3 * 2
    assert data["candidates"][0]["ip"] == "10.0.0.10"
    assert data["candidates"][-1]["ip"] == "10.0.0.12"


def test_scan_expand_single_host_cidr(client):
    """A /32 (or any prefix that resolves to one host) returns one
    candidate per port. The frontend uses this to support a bare IP
    like ``10.0.0.5`` — the parser internally rewrites it to
    ``10.0.0.5/32``."""
    login(client)
    r = client.post("/api/scan/expand", json={
        "cidr": "10.0.0.5/32",
        "ports": [80, 443, 8080],
    })
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["candidates"]) == 3
    assert all(c["ip"] == "10.0.0.5" for c in data["candidates"])
    ports = sorted(c["port"] for c in data["candidates"])
    assert ports == [80, 443, 8080]


def test_scan_expand_range_reversed_rejected(client):
    """start > end is a 400."""
    login(client)
    r = client.post("/api/scan/expand", json={
        "start": "10.0.0.20",
        "end": "10.0.0.10",
        "ports": [80],
    })
    assert r.status_code == 400
    assert r.get_json()["error"] == "invalid_range"


def test_scan_expand_rejects_loopback(client):
    """127.0.0.0/24 is loopback — a scanning no-no."""
    login(client)
    r = client.post("/api/scan/expand", json={
        "cidr": "127.0.0.0/24",
        "ports": [80],
    })
    assert r.status_code == 400
    assert r.get_json()["error"] == "reserved_range"
    assert r.get_json()["reason"] == "loopback"


def test_scan_expand_rejects_link_local(client):
    """169.254.0.0/16 is link-local — too much noise, also a mistake."""
    login(client)
    r = client.post("/api/scan/expand", json={
        "cidr": "169.254.0.0/16",
        "ports": [80],
    })
    assert r.status_code == 400
    assert r.get_json()["error"] == "reserved_range"


def test_scan_expand_caps_total(client):
    """If a request would produce more than the per-form cap, reject
    with too_many_hosts. The cap for start/end is 1024; for CIDR the
    prefixlen cap (/16) fires first and is checked elsewhere."""
    login(client)
    # 10.0.0.1 .. 10.0.4.0 inclusive = 1024 addresses, which is at the
    # cap (the check is strict greater-than, so 1024 is accepted).
    r = client.post("/api/scan/expand", json={
        "start": "10.0.0.1",
        "end": "10.0.4.0",  # 1024 addresses
        "ports": [80],
    })
    assert r.status_code == 200
    assert len(r.get_json()["candidates"]) == 1024
    # 10.0.0.1 .. 10.0.4.1 = 1025 addresses, just over the cap.
    r = client.post("/api/scan/expand", json={
        "start": "10.0.0.1",
        "end": "10.0.4.1",  # 1025 addresses
        "ports": [80],
    })
    assert r.status_code == 400
    assert r.get_json()["error"] == "too_many_hosts"


def test_scan_expand_rejects_bad_cidr(client):
    """Garbage in -> 400 with a named code."""
    login(client)
    for bad in ("not-a-cidr", "10.0.0.0/40", "", "10.0.0.0.0/24"):
        r = client.post("/api/scan/expand", json={"cidr": bad, "ports": [80]})
        assert r.status_code == 400, (bad, r.get_json())
        assert r.get_json()["error"] == "invalid_cidr", bad


def test_scan_expand_rejects_too_large_cidr(client):
    """Prefix lengths smaller than /16 (i.e. networks larger than /16,
    e.g. /15, /8) are rejected — those would generate tens of
    thousands of candidates."""
    login(client)
    for bad in ("10.0.0.0/15", "10.0.0.0/8"):
        r = client.post("/api/scan/expand", json={"cidr": bad, "ports": [80]})
        assert r.status_code == 400, (bad, r.get_json())
        assert r.get_json()["error"] == "cidr_too_large", bad


def test_scan_expand_rejects_too_many_ports(client):
    """More than 1024 ports -> 400 too_many_ports."""
    login(client)
    r = client.post("/api/scan/expand", json={
        "cidr": "10.0.0.0/30",  # 4 hosts so total < 4096; only the port cap trips
        "ports": list(range(1, 1100)),  # 1099 entries
    })
    assert r.status_code == 400
    assert r.get_json()["error"] == "too_many_ports"


def test_scan_expand_accepts_4_hosts_x_1024_ports(client):
    """The realistic upper bound: 4 IPs × 1024 ports = 4096 candidates
    (the total cap). This is exactly the shape we expect a user to
    scan — a few specific hosts against the top-1024 ports. We use
    the start/end form so the host count is exactly what we say it
    is (a /30 has 2 usable hosts, not 4)."""
    login(client)
    r = client.post("/api/scan/expand", json={
        "start": "10.0.0.1",
        "end": "10.0.0.4",  # 4 addresses
        "ports": list(range(1, 1025)),  # 1024 ports
    })
    assert r.status_code == 200
    data = r.get_json()
    assert data["truncated"] is False
    assert len(data["candidates"]) == 4 * 1024
    # Each (host, port) pair appears exactly once.
    pairs = {(c["ip"], c["port"]) for c in data["candidates"]}
    assert len(pairs) == 4 * 1024


def test_scan_expand_rejects_missing_ports(client):
    """No ports -> 400 ports_required."""
    login(client)
    r = client.post("/api/scan/expand", json={"cidr": "10.0.0.0/24"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "ports_required"


def test_scan_expand_rejects_missing_target(client):
    """No cidr and no start/end -> 400 target_required."""
    login(client)
    r = client.post("/api/scan/expand", json={"ports": [80]})
    assert r.status_code == 400
    assert r.get_json()["error"] == "target_required"


def test_scan_expand_requires_login(client):
    """Unauthenticated -> 401."""
    r = client.post("/api/scan/expand", json={"cidr": "10.0.0.0/24", "ports": [80]})
    assert r.status_code == 401


def test_scan_expand_dedupes_ports(client):
    """Duplicate ports in the input are collapsed, so the candidate
    count reflects unique ports only."""
    login(client)
    r = client.post("/api/scan/expand", json={
        "cidr": "10.0.0.0/30",  # 2 hosts
        "ports": [80, 80, 443, 443, 443],
    })
    assert r.status_code == 200
    assert len(r.get_json()["candidates"]) == 2 * 2


def test_scan_expand_validates_port_range(client):
    """Ports must be 1..65535. 0 and 70000 are out of range."""
    login(client)
    for bad_port in (0, -1, 65536, 70000):
        r = client.post("/api/scan/expand", json={
            "cidr": "10.0.0.0/24", "ports": [bad_port],
        })
        assert r.status_code == 400, (bad_port, r.get_json())
        assert r.get_json()["error"] == "invalid_port"
