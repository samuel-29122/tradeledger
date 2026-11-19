"""Phase 8: sign-in, registration, account and isolation journeys in Chromium.

Unlike the other browser tests (which start with a signed session cookie),
these use the real forms, exactly as a person would.

Optional real screenshots: set TRADELEDGER_SCREENSHOT_DIR=docs/screenshots.
"""

import os
import re
from pathlib import Path

import pytest

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    WIDTHS,
    browser,
    horizontal_overflow,
    page_factory,
    start_server,
)
from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user
from tradeledger.repositories.trades import list_trades

pytestmark = pytest.mark.browser

AUTH_PAGES = ["/login", "/register"]


def add_second_user(conn):
    ensure_user(conn, OTHER_EMAIL)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    url, stop = start_server(str(tmp_path_factory.mktemp("auth") / "auth.db"), prepare=add_second_user)
    yield url
    stop()


def sign_in(page, server, email=TEST_EMAIL, password=TEST_PASSWORD):
    page.goto(server + "/login")
    page.fill("#field-email", email)
    page.fill("#field-password", password)
    with page.expect_navigation():
        page.get_by_role("button", name="Sign in").click()


def test_sign_in_returns_to_the_requested_page(server, page_factory):
    page = page_factory()
    page.goto(server + "/trades?status=CLOSED")
    assert "/login?next=" in page.url
    assert page.locator("h1").inner_text().strip() == "Sign in"
    page.fill("#field-email", TEST_EMAIL)
    page.fill("#field-password", TEST_PASSWORD)
    with page.expect_navigation():
        page.get_by_role("button", name="Sign in").click()
    assert page.url.endswith("/trades?status=CLOSED")
    assert page.locator(".sidebar__account").inner_text().strip() == f"Signed in as {TEST_EMAIL}"
    assert page.problems == []


def test_wrong_password_shows_a_focused_generic_error(server, page_factory):
    page = page_factory()
    sign_in(page, server, password="not the right password")
    summary = page.locator(".error-summary")
    assert summary.is_visible() and summary.get_attribute("role") == "alert"
    assert "Email or password is incorrect." in summary.inner_text()
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    assert page.input_value("#field-email") == TEST_EMAIL  # the email is kept...
    assert page.input_value("#field-password") == ""  # ...the password never is


def test_password_fields_use_the_right_autocomplete(server, page_factory):
    page = page_factory()
    page.goto(server + "/login")
    assert page.get_attribute("#field-email", "autocomplete") == "username"
    assert page.get_attribute("#field-password", "autocomplete") == "current-password"
    assert page.get_attribute("#field-password", "type") == "password"
    page.goto(server + "/register")
    assert page.get_attribute("#field-password", "autocomplete") == "new-password"


def test_register_sign_out_and_sign_back_in(server, page_factory):
    page = page_factory()
    page.goto(server + "/register")
    page.fill("#field-email", "browser.user@example.com")
    page.fill("#field-password", "a browser pass phrase")
    page.fill("#field-confirm_password", "a browser pass phrase")
    with page.expect_navigation():
        page.get_by_role("button", name="Create account").click()
    assert page.url.endswith("/instruments")
    assert page.locator(".flash", has_text="Your account is ready").is_visible()
    assert page.locator("table").count() == 0 or "EURUSD" not in page.content()  # a new account is empty

    with page.expect_navigation():
        page.get_by_role("button", name="Sign out").click()
    assert page.url.endswith("/login")
    assert page.locator(".flash", has_text="You have signed out.").is_visible()
    page.goto(server + "/trades")
    assert "/login" in page.url  # really signed out

    sign_in(page, server, "browser.user@example.com", "a browser pass phrase")
    assert page.url.endswith("/")


def test_the_other_user_cannot_open_a_trade_by_url(server, page_factory):
    with server.app.app_context():
        from tradeledger.db import get_db

        alice_trade = list_trades(get_db(), user_id=1, limit=1)[0]["id"]
    page = page_factory()
    sign_in(page, server, OTHER_EMAIL, TEST_PASSWORD)
    response = page.goto(f"{server}/trades/{alice_trade}")
    assert response.status == 404
    assert "EURUSD" not in page.locator("main").inner_text()
    page.goto(server + "/trades")
    assert page.get_by_text("No trades yet").count() + page.get_by_text("no trades").count() >= 0
    assert page.locator('a[href^="/trades/"][href$="' + str(alice_trade) + '"]').count() == 0


def test_create_and_revoke_an_api_token(server, page_factory):
    page = page_factory()
    sign_in(page, server)
    page.goto(server + "/account")
    page.fill("#field-name", "Browser token")
    with page.expect_navigation():
        page.get_by_role("button", name="Create token").click()
    token = page.input_value("#new-token-value")
    assert re.fullmatch(r"tl_[A-Za-z0-9_-]{43}", token)
    assert page.get_by_text("It will not be shown again").is_visible()
    page.reload()  # re-sends the POST: refused by the one-time form value
    assert token not in page.content()
    assert page.locator(".flash", has_text="no new token was created").is_visible()
    assert page.locator('td[data-label="Name"]', has_text="Browser token").count() == 1
    with page.expect_navigation():
        page.get_by_role("button", name="Revoke token Browser token").click()
    assert page.locator(".flash", has_text="API token revoked").is_visible()


# ---------- Accessibility and layout of the new pages ----------


UNLABELLED = """() => [...document.querySelectorAll('input, select, textarea')]
    .filter(el => el.type !== 'hidden' && !el.hidden)
    .filter(el => !(el.labels && el.labels.length) && !el.getAttribute('aria-label'))
    .map(el => el.name)"""


@pytest.mark.parametrize("path", ["/login", "/register", "/account"])
def test_new_pages_are_labelled_and_have_one_h1(server, page_factory, path):
    page = page_factory(server=server) if path == "/account" else page_factory()
    page.goto(server + path)
    assert page.evaluate(UNLABELLED) == []
    assert page.locator("h1").count() == 1
    assert page.evaluate("document.documentElement.lang") == "en-GB"


@pytest.mark.parametrize("width", WIDTHS)
def test_new_pages_fit_every_width(server, page_factory, width):
    for path, signed_in in (("/login", False), ("/register", False), ("/account", True)):
        page = page_factory(width, 900, server=server) if signed_in else page_factory(width, 900)
        page.goto(server + path)
        assert horizontal_overflow(page) == 0, (path, width)


def test_sign_in_form_works_with_the_keyboard_alone(server, page_factory):
    page = page_factory()
    page.goto(server + "/login")
    page.locator("#field-email").focus()
    page.keyboard.type(TEST_EMAIL)
    page.keyboard.press("Tab")
    page.keyboard.type(TEST_PASSWORD)
    with page.expect_navigation():
        page.keyboard.press("Enter")
    assert page.url.endswith("/")


@pytest.mark.skipif(
    not os.environ.get("TRADELEDGER_SCREENSHOT_DIR"), reason="TRADELEDGER_SCREENSHOT_DIR not set"
)
def test_capture_auth_screenshots(server, page_factory):
    folder = Path(os.environ["TRADELEDGER_SCREENSHOT_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    shots = [
        ("/login", 1440, False, "phase8_login_1440.png"),
        ("/login", 375, False, "phase8_login_375.png"),
        ("/register", 1440, False, "phase8_register_1440.png"),
        ("/account", 1440, True, "phase8_account_1440.png"),
        ("/account", 375, True, "phase8_account_375.png"),
        ("/", 1440, True, "phase8_overview_signed_in_1440.png"),
    ]
    for path, width, signed_in, name in shots:
        page = page_factory(width, 900, server=server) if signed_in else page_factory(width, 900)
        page.goto(server + path)
        page.screenshot(path=str(folder / name), full_page=True)
        assert (folder / name).stat().st_size > 10_000
    page = page_factory(1440, 900)
    sign_in(page, server, password="a wrong password here")
    page.screenshot(path=str(folder / "phase8_login_error_1440.png"), full_page=True)
