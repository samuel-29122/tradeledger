"""Phase 8.5: the new account-security journeys in a real browser (Chromium).

Each journey uses the real forms, exactly as a person would:
  * set up two-step verification by reading the setup key from the page,
    then sign out and sign in with a code;
  * use a one-time reset link from the command line;
  * see two signed-in browsers and sign one out from the other.
Every new page is also checked at 375, 768, 1024 and 1440 px for labels,
headings and horizontal scrolling.

Optional screenshots: set TRADELEDGER_SCREENSHOT_DIR=docs/screenshots.
"""

import os
import re
from pathlib import Path

import pytest
from click.testing import CliRunner
from flask.cli import ScriptInfo

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    WIDTHS,
    browser,
    horizontal_overflow,
    page_factory,
    start_server,
)
from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user
from tradeledger import totp
from tradeledger.cli import create_reset_link_command

pytestmark = pytest.mark.browser

THIRD_EMAIL = "reset.user@example.com"


def add_users(conn):
    ensure_user(conn, OTHER_EMAIL)
    ensure_user(conn, THIRD_EMAIL)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    url, stop = start_server(str(tmp_path_factory.mktemp("acctsec") / "acct.db"), prepare=add_users)
    yield url
    stop()


def sign_in(page, server, email, password=TEST_PASSWORD):
    page.goto(server + "/login")
    page.fill("#field-email", email)
    page.fill("#field-password", password)
    with page.expect_navigation():
        page.get_by_role("button", name="Sign in").click()


def sign_out(page):
    with page.expect_navigation():
        page.get_by_role("button", name="Sign out", exact=True).first.click()


def shoot(page, name):
    folder = os.environ.get("TRADELEDGER_SCREENSHOT_DIR")
    if folder:
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder) / name), full_page=True)


def test_two_step_set_up_and_sign_in(server, page_factory):
    page = page_factory()
    sign_in(page, server, OTHER_EMAIL)
    page.goto(server + "/account")
    page.get_by_role("link", name="Set up two-step verification").click()
    assert page.locator("h1").inner_text().strip() == "Two-step verification"
    key = page.locator(".setup-key").inner_text().replace(" ", "")
    assert re.fullmatch(r"[A-Z2-7]{32}", key)
    shoot(page, "final_two_step_setup_1440.png")
    page.fill("#field-current_password", TEST_PASSWORD)
    page.fill("#field-code", totp.code_at(key, __import__("time").time()))
    with page.expect_navigation():
        page.get_by_role("button", name="Turn on two-step verification").click()
    codes = page.locator(".recovery-codes code").all_inner_texts()
    assert len(codes) == 10
    assert "Save these recovery codes now" in page.content()
    shoot(page, "final_two_step_codes_1440.png")

    sign_out(page)
    sign_in(page, server, OTHER_EMAIL)
    assert page.url.endswith("/login/two-step")
    assert page.locator("#code").get_attribute("autocomplete") == "one-time-code"
    shoot(page, "final_two_step_signin_1440.png")
    page.fill("#code", "000000")
    with page.expect_navigation():
        page.get_by_role("button", name="Verify and sign in").click()
    assert "That code is not right" in page.content()
    assert page.evaluate("document.activeElement.hasAttribute('data-error-summary')")
    page.fill("#code", codes[0])  # a recovery code also works, once
    with page.expect_navigation():
        page.get_by_role("button", name="Verify and sign in").click()
    assert page.locator("h1").inner_text().strip() == "Overview"
    assert "You used a recovery code. 9 unused code(s) left" in page.content()
    assert page.problems == []


def test_reset_link_journey(server, page_factory):
    app = server.app
    result = CliRunner().invoke(
        create_reset_link_command,
        ["--email", THIRD_EMAIL, "--base-url", "http://localhost"],
        obj=ScriptInfo(create_app=lambda: app),
    )
    link = result.output.strip().splitlines()[-1].replace("http://localhost", server)
    page = page_factory()
    page.goto(link)
    assert page.url == server + "/reset-password"  # the token has left the address bar
    shoot(page, "final_reset_password_1440.png")
    page.fill("#field-new_password", "lighthouse pebble orbit tangerine")
    page.fill("#field-confirm_password", "lighthouse pebble orbit tangerine")
    with page.expect_navigation():
        page.get_by_role("button", name="Save new password").click()
    assert page.url.endswith("/login")
    assert "Your password has been changed" in page.content()
    sign_in(page, server, THIRD_EMAIL, "lighthouse pebble orbit tangerine")
    assert page.locator("h1").inner_text().strip() == "Overview"
    page.goto(link)  # used up
    assert "invalid, has already been used or has expired" in page.content()
    assert page.problems == []


def test_sign_out_another_browser(server, page_factory):
    laptop = page_factory()
    phone = page_factory(375, 800)
    sign_in(laptop, server, TEST_EMAIL)
    sign_in(phone, server, TEST_EMAIL)
    phone.goto(server + "/account")
    rows = phone.locator("section[aria-labelledby=sessions-title] tbody tr")
    assert rows.count() >= 2
    assert (
        phone.locator("section[aria-labelledby=sessions-title] .badge", has_text="This browser").count() == 1
    )
    shoot(phone, "final_account_sessions_375.png")
    other = phone.locator("section[aria-labelledby=sessions-title] tbody tr:not(.is-current)").first
    with phone.expect_navigation():
        other.get_by_role("button").click()
    assert "That browser has been signed out" in phone.content()
    laptop.goto(server + "/trades")
    assert "/login" in laptop.url
    assert "Your session has ended" in laptop.content()
    assert phone.problems == [] and laptop.problems == []


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("path", ["/account", "/account/two-step", "/reset-password", "/login"])
def test_new_pages_are_accessible_and_fit_the_screen(server, page_factory, width, path):
    page = page_factory(width, 900, server=server if path.startswith("/account") else None)
    page.goto(server + path)
    assert page.locator("h1").count() == 1
    assert not horizontal_overflow(page), (path, width)
    unlabelled = page.evaluate(
        """() => [...document.querySelectorAll('input:not([type=hidden]):not([hidden]), select, textarea')]
                 .filter(e => !(e.labels && e.labels.length) && !e.getAttribute('aria-label')).map(e => e.name)"""
    )
    assert unlabelled == [], (path, unlabelled)
    if path == "/account" and width >= 1024:
        # Regression (Phase 8.5 review): the "Sign out" button sat behind a
        # scroll bar at 1024px. The sessions table must fit its card.
        assert page.evaluate(
            "[...document.querySelectorAll('.table--sessions')].every(t => "
            "t.closest('.table-wrap').scrollWidth <= t.closest('.table-wrap').clientWidth + 1)"
        )
    assert page.problems == []


def test_200_percent_zoom_on_the_account_page(server, page_factory):
    page = page_factory(720, 450, server=server, device_scale_factor=2)
    page.goto(server + "/account")
    assert not horizontal_overflow(page)
