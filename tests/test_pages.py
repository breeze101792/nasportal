"""End-to-end page tests: drive the real Flask server with Chromium via Playwright.

One test per page. ``test_app_page_shows_added_app`` is the regression for the
bug where /app showed nothing because the raw /api/apps object (not its ``apps``
array) was rendered.
"""
import pytest

pytest.importorskip("playwright")
from playwright.sync_api import expect


def _setup_login(page, base_url, password="e2epw"):
    """Run first-run setup through the login page; lands logged-in on /."""
    page.goto(f"{base_url}/login")
    expect(page.locator("#title")).to_have_text("Set up your portal")
    page.fill("#pw", password)
    page.click("#form button[type=submit]")
    page.wait_for_url(f"{base_url}/")


@pytest.mark.e2e
def test_home_renders_engine_dropdown_and_empty_state(page, base_url):
    page.goto(f"{base_url}/")
    expect(page.locator("#brand")).to_have_text("My NAS")
    assert page.locator("#engine option").all_inner_texts() == ["Google", "Bing", "SearXNG"]
    expect(page.locator("#groups")).to_contain_text("No apps yet")


@pytest.mark.e2e
def test_app_page_shows_added_app(page, base_url):
    """Regression: /app must list an app that exists, not the empty state."""
    _setup_login(page, base_url)
    page.goto(f"{base_url}/app")
    expect(page.locator("#addBtn")).to_be_visible()

    page.click("#addBtn")
    page.fill("#f-title", "Sonarr")
    page.fill("#f-url", base_url)  # portal itself -> ping resolves fast & online
    page.click("#appForm button[type=submit]")

    expect(page.locator("#list")).to_contain_text("Sonarr")
    expect(page.locator("#list")).not_to_contain_text("No apps yet")

    # …and it now shows on the home grid too.
    page.goto(f"{base_url}/")
    expect(page.locator("#groups")).to_contain_text("Sonarr")


@pytest.mark.e2e
def test_app_page_ping_shows_status(page, base_url):
    _setup_login(page, base_url)
    page.goto(f"{base_url}/app")
    page.click("#addBtn")
    page.fill("#f-title", "Portal")
    page.fill("#f-url", base_url)
    page.click("#appForm button[type=submit]")
    # ping fires on add; the row should show an "up" status with latency.
    expect(page.locator("#list")).to_contain_text("up ·")


@pytest.mark.e2e
def test_app_add_form_stays_open_and_keeps_group(page, base_url):
    """Adding keeps the form open for the next entry and retains the group
    field (so a batch to the same group doesn't require retyping it)."""
    _setup_login(page, base_url)
    page.goto(f"{base_url}/app")
    page.click("#addBtn")

    page.fill("#f-title", "Sonarr")
    page.fill("#f-url", base_url)
    page.fill("#f-group", "Media")
    page.click("#appForm button[type=submit]")

    # The panel stays visible (not closed after save)…
    expect(page.locator("#formPanel")).to_be_visible()
    # …the saved app shows in the list…
    expect(page.locator("#list")).to_contain_text("Sonarr")
    # …and the group field is retained while the other fields are cleared.
    expect(page.locator("#f-title")).to_have_value("")
    expect(page.locator("#f-url")).to_have_value("")
    expect(page.locator("#f-group")).to_have_value("Media")

    # A second add to the same group (without re-entering it) lands too.
    page.fill("#f-title", "Radarr")
    page.fill("#f-url", base_url)
    page.click("#appForm button[type=submit]")
    expect(page.locator("#list")).to_contain_text("Radarr")
    expect(page.locator("#f-group")).to_have_value("Media")
    # Closing explicitly still works.
    page.click("#cancelBtn")
    expect(page.locator("#formPanel")).to_be_hidden()


@pytest.mark.e2e
def test_app_multi_select_group_and_delete(page, base_url):
    """Multi-select: pick two apps, set a shared group, then select and delete."""
    _setup_login(page, base_url)
    page.goto(f"{base_url}/app")
    expect(page.locator("#selectBar")).to_be_visible()

    # Add three apps. The form stays open after each save; waiting for the
    # "Saved" message (set at the end of the async handler, after it clears the
    # fields — and cleared to "Saving…" at the start) serializes the adds so a
    # next fill can't race the previous clear. Distinctive names avoid substring
    # matches in the has_text filters below.
    page.click("#addBtn")
    for name in ("AppA", "AppB", "AppC"):
        page.fill("#f-title", name)
        page.fill("#f-url", base_url)
        page.click("#appForm button[type=submit]")
        expect(page.locator("#formMsg")).to_contain_text("Saved")
    page.click("#cancelBtn")

    expect(page.locator("#list")).to_contain_text("AppA")
    expect(page.locator("#list")).to_contain_text("AppB")
    expect(page.locator("#list")).to_contain_text("AppC")
    expect(page.locator("#selCount")).to_have_text("0 selected")

    # Select AppA and AppC (skip AppB) via their checkboxes.
    page.locator("#list .app-row").filter(has_text="AppA").locator(".sel").check()
    page.locator("#list .app-row").filter(has_text="AppC").locator(".sel").check()
    expect(page.locator("#selCount")).to_have_text("2 selected")
    expect(page.locator("#selGroupBtn")).not_to_be_disabled()

    # Set a shared group via the bulk prompt (handler must be registered first).
    page.once("dialog", lambda d: d.accept("Media"))
    page.click("#selGroupBtn")
    page.wait_for_function("() => fetch('/api/apps').then(r=>r.json()).then(d=>d.apps.some(a=>a.group==='Media'))")
    # AppA and AppC now carry the group; AppB does not.
    expect(page.locator("#list .app-row").filter(has_text="AppA")).to_contain_text("Media")
    expect(page.locator("#list .app-row").filter(has_text="AppC")).to_contain_text("Media")

    # Select-all then delete everything.
    page.click("#selAll")
    expect(page.locator("#selCount")).to_have_text("3 selected")
    page.once("dialog", lambda d: d.accept())
    page.click("#selDelBtn")
    page.wait_for_function("() => fetch('/api/apps').then(r=>r.json()).then(d=>d.apps.length===0)")
    expect(page.locator("#list")).to_contain_text("No apps yet")
    expect(page.locator("#selCount")).to_have_text("0 selected")


@pytest.mark.e2e
def test_app_shift_range_select(page, base_url):
    """Shift-click a checkbox to select the whole range from the last clicked
    row to this one, in on-screen order. A bulk group-set clears the selection."""
    _setup_login(page, base_url)
    page.goto(f"{base_url}/app")
    page.click("#addBtn")
    for name in ("R1", "R2", "R3", "R4", "R5"):
        page.fill("#f-title", name)
        page.fill("#f-url", base_url)
        page.click("#appForm button[type=submit]")
        expect(page.locator("#formMsg")).to_contain_text("Saved")
    page.click("#cancelBtn")

    # Plain click selects one; shift-click a later row selects the range between.
    page.locator("#list .app-row").filter(has_text="R1").locator(".sel").click()
    expect(page.locator("#selCount")).to_have_text("1 selected")
    page.locator("#list .app-row").filter(has_text="R3").locator(".sel").click(modifiers=["Shift"])
    expect(page.locator("#selCount")).to_have_text("3 selected")
    # R2 was never clicked directly but is inside the range, so it's selected too.
    expect(page.locator("#list .app-row").filter(has_text="R2").locator(".sel")).to_be_checked()
    expect(page.locator("#list .app-row").filter(has_text="R4").locator(".sel")).not_to_be_checked()

    # Renaming the group of the selection clears the selection afterwards.
    page.once("dialog", lambda d: d.accept("Batch"))
    page.click("#selGroupBtn")
    page.wait_for_function("() => fetch('/api/apps').then(r=>r.json()).then(d=>d.apps.some(a=>a.group==='Batch'))")
    expect(page.locator("#selCount")).to_have_text("0 selected")
    expect(page.locator("#list .app-row").filter(has_text="R2").locator(".sel")).not_to_be_checked()


@pytest.mark.e2e
def test_settings_setup_and_identity_save(page, base_url):
    page.goto(f"{base_url}/settings")
    expect(page.locator("#setupPanel")).to_be_visible()
    page.fill("#setup-pw", "settingspw")
    page.click("#setupForm button[type=submit]")
    page.wait_for_selector("#content")

    expect(page.locator("#s-title")).to_have_value("My NAS")
    page.fill("#s-title", "My Cool NAS")
    page.click("#identityForm button[type=submit]")
    expect(page.locator("#identityMsg")).to_contain_text("Saved")

    page.goto(f"{base_url}/")
    expect(page.locator("#brand")).to_have_text("My Cool NAS")


@pytest.mark.e2e
def test_settings_engines_editor(page, base_url):
    _setup_login(page, base_url, "pw")
    page.goto(f"{base_url}/settings")
    page.wait_for_selector("#content")

    page.click("#addEngine")
    rows = page.locator(".engine-row")
    assert rows.count() == 4  # 3 defaults + 1 new
    last = rows.nth(3)
    last.locator("input").nth(0).fill("DuckDuckGo")
    last.locator("input").nth(1).fill("https://duckduckgo.com/?q=%s")
    page.click("#saveEngines")
    expect(page.locator("#enginesMsg")).to_contain_text("Saved")

    assert "DuckDuckGo" in page.locator("#s-default option").all_inner_texts()
    page.goto(f"{base_url}/")
    assert "DuckDuckGo" in page.locator("#engine option").all_inner_texts()


@pytest.mark.e2e
def test_settings_theme_selector(page, base_url):
    """The Appearance theme selector switches light/dark live and persists."""
    _setup_login(page, base_url)
    page.goto(f"{base_url}/settings")
    page.wait_for_selector("#content")

    sel = page.locator("#s-theme")
    expect(sel).to_have_value("dark")  # default

    sel.select_option("light")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    expect(page.locator("#themeMsg")).to_contain_text("Saved")

    sel.select_option("dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")

    # Persisted across reload (theme.js re-applies from localStorage, then
    # settings.js reconciles with the server).
    page.reload()
    page.wait_for_selector("#content")
    expect(sel).to_have_value("dark")
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")


@pytest.mark.e2e
def test_settings_portal_width(page, base_url):
    """The portal-width slider previews live, persists, and drives --portal-width."""
    _setup_login(page, base_url)
    page.goto(f"{base_url}/settings")
    page.wait_for_selector("#content")

    inp = page.locator("#s-width")
    expect(inp).to_have_value("80")

    inp.fill("90")
    expect(page.locator("#s-width-val")).to_have_text("90%")
    expect(page.locator("#widthMsg")).to_contain_text("Saved")
    assert page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--portal-width').trim()"
    ) == "90%"

    # Persisted across reload.
    page.reload()
    page.wait_for_selector("#content")
    expect(page.locator("#s-width")).to_have_value("90")
    assert page.evaluate(
        "getComputedStyle(document.documentElement).getPropertyValue('--portal-width').trim()"
    ) == "90%"


@pytest.mark.e2e
def test_home_layout_grouped_then_flow(page, base_url):
    """Grouped renders a titled section per group; Flow renders one continuous
    grid that fills each row, with the group shown on each card."""
    _setup_login(page, base_url)

    # Add two apps in different groups.
    page.goto(f"{base_url}/app")
    page.click("#addBtn")
    for name, grp in [("LayoutA", "G1"), ("LayoutB", "G2")]:
        page.fill("#f-title", name)
        page.fill("#f-url", base_url)
        page.fill("#f-group", grp)
        page.click("#appForm button[type=submit]")
        expect(page.locator("#formMsg")).to_contain_text("Saved")
    page.click("#cancelBtn")

    # Default (grouped): two titled sections, two grids.
    page.goto(f"{base_url}/")
    expect(page.locator("#groups .group-title")).to_have_count(2)
    assert page.locator("#groups .grid").count() == 2
    assert page.locator("#groups .card-group").count() == 0  # no per-card labels

    # Switch to flow via Settings (Portal panel).
    page.goto(f"{base_url}/settings")
    page.wait_for_selector("#content")
    page.locator("#s-layout").select_option("flow")
    page.click("#identityForm button[type=submit]")
    expect(page.locator("#identityMsg")).to_contain_text("Saved")

    # Flow: a single grid, no group titles, group shown on each card.
    page.goto(f"{base_url}/")
    assert page.locator("#groups .grid").count() == 1
    expect(page.locator("#groups .group-title")).to_have_count(0)
    expect(page.locator("#groups .card-group")).to_have_count(2)
    expect(page.locator("#groups")).to_contain_text("G1")
    expect(page.locator("#groups")).to_contain_text("G2")


@pytest.mark.e2e
def test_login_wrong_then_correct(page, base_url):
    _setup_login(page, base_url, "secret")
    # drop the session cookie to simulate a logged-out visitor
    page.context.clear_cookies()

    page.goto(f"{base_url}/login")
    expect(page.locator("#title")).to_have_text("Login")
    page.fill("#pw", "wrong")
    page.click("#form button[type=submit]")
    expect(page.locator("#msg")).to_contain_text("Wrong password")

    page.fill("#pw", "secret")
    page.click("#form button[type=submit]")
    page.wait_for_url(f"{base_url}/")


@pytest.mark.e2e
def test_guest_cannot_edit(page, base_url):
    """A logged-out visitor sees the app list read-only with no add/edit controls."""
    # create an app out-of-band via the API (as admin)
    import requests
    s = requests.Session()
    s.post(f"{base_url}/api/auth/login", json={"password": "g"})
    s.post(f"{base_url}/api/apps", json={"title": "GuestVisible", "url": base_url})

    page.goto(f"{base_url}/app")
    expect(page.locator("#list")).to_contain_text("GuestVisible")
    expect(page.locator("#addBtn")).to_be_hidden()
    expect(page.locator("#pingBtn")).to_be_hidden()
    expect(page.locator(".banner")).to_contain_text("guest")


# ---- network scan page ----

@pytest.mark.e2e
def test_scan_tab_renders_and_lists_networks(page, base_url):
    """The Network Scan tab is reachable from /settings, the network
    dropdown populates from /api/networks/local, and the ports field
    has the common preset pre-filled."""
    _setup_login(page, base_url)
    page.goto(f"{base_url}/settings")
    # The Scan tab button is present.
    expect(page.locator('button[data-tab="scan"]')).to_be_visible()
    page.click('button[data-tab="scan"]')
    # The scan panel is now visible; the other tabs are not.
    expect(page.locator('section[data-tab="scan"]')).to_be_visible()
    expect(page.locator('section[data-tab="general"]')).to_be_hidden()
    # The ports field has a sensible default. (We don't pin the exact
    # string — only that it has at least one port and a couple of
    # common ones.)
    ports = page.locator("#scan-ports").input_value()
    assert "80" in ports and "443" in ports
    # The target dropdown has at least the Custom… option (and
    # ideally one or more detected networks). The literal sentinel
    # value ("__custom__") is internal — the option's visible text
    # is "Custom…".
    options = page.locator("#scan-target option").all_inner_texts()
    assert any("Custom" in o for o in options), options
    assert "__custom__" not in " ".join(options), options
    # The protocol selector defaults to "both" (try http first,
    # then https on failure).
    expect(page.locator("#scan-scheme")).to_have_value("both")
    scheme_opts = page.locator("#scan-scheme option").all_inner_texts()
    assert any("Both" in o for o in scheme_opts)
    assert any("HTTP only" in o for o in scheme_opts)
    assert any("HTTPS only" in o for o in scheme_opts)


@pytest.mark.e2e
def test_scan_custom_range_input_accepted(page, base_url):
    """Typing an explicit IP range into the custom input is accepted by
    the Start button (i.e. doesn't reject the format client-side)."""
    _setup_login(page, base_url)
    page.goto(f"{base_url}/settings")
    page.click('button[data-tab="scan"]')
    # The custom input is always visible; just type a tiny range.
    page.select_option("#scan-target", "__custom__")
    page.fill("#scan-cidr", "127.0.0.1-127.0.0.1")
    # The Start button is enabled and doesn't show a validation error.
    expect(page.locator("#scan-start")).to_be_enabled()
    # Clicking it should kick off the scan (the expand endpoint will
    # reject 127.0.0.0/24 as loopback, so we expect a clean error
    # message — not a JS exception).
    page.click("#scan-start")
    expect(page.locator("#scan-msg")).to_contain_text("reserved_range")


@pytest.mark.e2e
def test_scan_starts_and_completes_with_no_hits(page, base_url):
    """A real scan run completes and shows the empty state when the
    target is not loopback and has no services.

    We use a /32 of a non-loopback link-local address (169.254.0.1)
    which the expand endpoint accepts (it's not in the loopback /
    multicast reject list, only 169.254.0.0/16 ranges get the
    link_local reason). The browser will then time out trying to
    reach it, giving us a clean 0-hits scan that exercises the full
    probe loop and the empty state UI."""
    _setup_login(page, base_url)
    page.goto(f"{base_url}/settings")
    page.click('button[data-tab="scan"]')
    # Custom target = a single address that's not loopback. The
    # browser probe will time out, so the scan finishes with no hits
    # but the UI is fully exercised.
    page.select_option("#scan-target", "__custom__")
    page.fill("#scan-cidr", "192.0.2.1/32")
    page.click("#scan-start")
    # Wait for the scan to complete. The progress label switches
    # from "Probing..." to "Done." once the loop exits. We use a
    # generous timeout because of the 1.5s per-probe timeout.
    expect(page.locator("#scan-progress-label")).to_contain_text("Done.", timeout=10_000)
    # The empty state appears since there are no hits.
    expect(page.locator(".scan-empty")).to_be_visible()