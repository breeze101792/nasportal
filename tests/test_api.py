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
def test_add_app_with_urls_field_parses_into_structured(client, monkeypatch):
    """A multi-line 'urls' payload should be split: an on-subnet IP
    becomes a network_ip, an off-subnet one becomes a public_ip."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    login(client)
    r = client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": "http://10.31.1.9:8989\n203.0.113.10",
    })
    assert r.status_code == 201
    app = r.get_json()
    assert "10.31.1.9" in app["network_ips"]
    assert app["public_ip"] == "203.0.113.10"
    assert app["port"] == 8989
    assert app["scheme"] == "http"


def test_add_app_explicit_structured_fields(client, monkeypatch):
    """Admin can pass network_ips / domain / public_ip directly."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    r = client.post("/api/apps", json={
        "title": "Sonarr",
        "network_ips": ["10.31.1.9"],
        "domain": "sonarr.example.com",
        "public_ip": "203.0.113.10",
    })
    assert r.status_code == 201
    app = r.get_json()
    assert app["network_ips"] == ["10.31.1.9"]
    assert app["domain"] == "sonarr.example.com"
    assert app["public_ip"] == "203.0.113.10"


def test_add_app_rejects_invalid_network_ip(client):
    login(client)
    r = client.post("/api/apps", json={
        "title": "x",
        "network_ips": ["not-an-ip"],
    })
    assert r.status_code == 400 and r.get_json()["error"] == "invalid_network_ip"


def test_add_app_preserves_path_from_url(client, monkeypatch):
    """A URL with a non-root path keeps the path on the saved app so the
    resolver produces the right final URL."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    r = client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": "https://sonarr.example.com/sonarr",
    })
    assert r.status_code == 201
    app = r.get_json()
    assert app["path"] == "/sonarr"
    # Resolved URL has the path appended.
    r = client.get("/api/apps/resolved")
    assert r.get_json()["apps"][0]["url"] == "https://sonarr.example.com/sonarr"


def test_add_app_normalizes_root_path(client, monkeypatch):
    """A trailing slash on a domain-only URL is treated as no path."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    r = client.post("/api/apps", json={
        "title": "fn.shaowu",
        "urls": "https://fn.shaowu.org/",
    })
    assert r.status_code == 201
    # The bare-root path "/" normalizes to "" so the saved field is
    # clean and re-editing the app doesn't carry the stray slash.
    assert r.get_json()["path"] == ""


def test_add_app_with_query_string_keeps_path(client, monkeypatch):
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    r = client.post("/api/apps", json={
        "title": "AriaNg",
        "urls": "http://10.31.1.9:50102/#!/downloading",
    })
    assert r.status_code == 201
    # urlparse splits off the fragment as a separate piece; we only
    # preserve the path + query, which is what the resolver can
    # actually append to the chosen host.
    app = r.get_json()
    assert "#!/downloading" not in (app.get("path") or "")


def test_add_app_mixed_paths_in_multi_line_drop_silently(client, monkeypatch):
    """When the user pastes URLs with DIFFERENT paths, the parsed path
    is ambiguous — drop it (don't silently pick one) so the user
    notices the conflict in the form's Path field."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    login(client)
    r = client.post("/api/apps", json={
        "title": "Mixed",
        "urls": "http://10.31.1.9/a\nhttp://10.31.1.9/b",
    })
    assert r.status_code == 201
    assert r.get_json().get("path", "") == ""


def test_add_app_explicit_path_field_wins(client, monkeypatch):
    """Caller can pass an explicit `path` field to set the path
    regardless of the parsed URL."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    r = client.post("/api/apps", json={
        "title": "X",
        "domain": "example.com",
        "path": "/dashboard",
    })
    assert r.status_code == 201
    assert r.get_json()["path"] == "/dashboard"
    r = client.get("/api/apps/resolved")
    assert r.get_json()["apps"][0]["url"] == "http://example.com/dashboard"


def test_add_app_dedupes_network_ips(client, monkeypatch):
    """The same IP across multiple lines shouldn't appear twice in network_ips."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    login(client)
    r = client.post("/api/apps", json={
        "title": "Sonarr",
        "urls": "http://10.31.1.9\nhttps://10.31.1.9",
    })
    assert r.status_code == 201
    assert r.get_json()["network_ips"] == ["10.31.1.9"]


def test_add_app_legacy_url_still_works(client, monkeypatch):
    """An old-style {'url': '...'} payload still saves without structured fields."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    r = client.post("/api/apps", json={"title": "Old", "url": "https://legacy.example.com"})
    assert r.status_code == 201
    app = r.get_json()
    assert app["url"] == "https://legacy.example.com"
    assert app.get("network_ips") == []


def test_update_app_replaces_structured_fields(client, monkeypatch):
    """Updating with a new urls payload fully replaces the structured fields."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    login(client)
    app = client.post("/api/apps", json={
        "title": "T", "urls": "http://10.31.1.9",
    }).get_json()
    # Re-save with a different URL.
    r = client.put(f"/api/apps/{app['id']}", json={
        "title": "T2", "urls": "https://something.example.com",
    })
    assert r.status_code == 200
    out = r.get_json()
    assert out["title"] == "T2"
    assert out["domain"] == "something.example.com"
    assert out["network_ips"] == []


# ---- network awareness: resolved endpoint ----
def test_resolved_prefers_same_network(client, monkeypatch):
    """When the user is on a subnet that contains one of the app's
    network_ips, that IP wins."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("10.31.0.0/16")])
    login(client)
    client.post("/api/apps", json={
        "title": "Sonarr",
        "network_ips": ["10.31.1.9", "192.168.1.50"],
        "domain": "sonarr.example.com",
    })
    # Simulate a visitor from 10.31.x.x.
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "10.31.5.5"})
    apps = r.get_json()["apps"]
    assert len(apps) == 1
    assert apps[0]["url"] == "http://10.31.1.9"
    assert apps[0]["resolved"]["kind"] == "network"


def test_resolved_falls_back_to_domain(client, monkeypatch):
    """No same-network IP -> falls through to the domain."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    client.post("/api/apps", json={
        "title": "Sonarr",
        "network_ips": ["10.31.1.9"],
        "domain": "sonarr.example.com",
    })
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "203.0.113.5"})
    apps = r.get_json()["apps"]
    assert apps[0]["url"] == "http://sonarr.example.com"
    assert apps[0]["resolved"]["kind"] == "domain"


def test_resolved_uses_translation_table(client, monkeypatch):
    """A network_ip with a translation entry that lands on the user's
    network is preferred."""
    import ipaddress
    monkeypatch.setattr("services.networks.get_local_networks",
                        lambda: [ipaddress.IPv4Network("192.168.0.0/16")])
    login(client)
    client.post("/api/apps", json={
        "title": "Sonarr",
        "network_ips": ["10.31.1.9"],  # not on user's network
        "domain": "sonarr.example.com",
    })
    # Admin sets up translation: 10.31.1.9 is really 192.168.1.50 on the user's side.
    client.put("/api/settings", json={"ip_translation": {"10.31.1.9": "192.168.1.50"}})
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "192.168.1.10"})
    apps = r.get_json()["apps"]
    assert apps[0]["url"] == "http://192.168.1.50"
    assert apps[0]["resolved"]["kind"] == "translated"


def test_resolved_filters_untranslatable_when_disabled(client, monkeypatch):
    """show_untranslatable=false hides apps with no reachable IP."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    client.post("/api/apps", json={
        "title": "Reachable", "network_ips": ["10.31.1.9"],
    })
    client.post("/api/apps", json={
        "title": "PublicOnly", "domain": "public.example.com",
    })
    # Default: both visible.
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "203.0.113.5"})
    titles = [a["title"] for a in r.get_json()["apps"]]
    assert set(titles) == {"Reachable", "PublicOnly"}
    # Disabled: only the one with a domain fallback remains? Actually
    # the "PublicOnly" one still has a domain so it should still be
    # shown — the filter only kicks in for apps with NO reachable kind.
    client.put("/api/settings", json={"show_untranslatable": False})
    r = client.get("/api/apps/resolved", environ_overrides={"REMOTE_ADDR": "203.0.113.5"})
    titles = [a["title"] for a in r.get_json()["apps"]]
    assert set(titles) == {"PublicOnly"}


def test_resolved_legacy_url_kept(client, monkeypatch):
    """An app with no structured fields still resolves via its legacy url."""
    monkeypatch.setattr("services.networks.get_local_networks", lambda: [])
    login(client)
    client.post("/api/apps", json={"title": "Old", "url": "https://legacy.example.com/path"})
    r = client.get("/api/apps/resolved")
    apps = r.get_json()["apps"]
    assert apps[0]["url"] == "https://legacy.example.com/path"
    assert apps[0]["resolved"]["kind"] == "legacy"


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
