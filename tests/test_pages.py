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