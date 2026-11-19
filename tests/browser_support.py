"""Shared helpers for the real-browser (Playwright) tests.

Imported by tests/test_browser*.py and tests/test_chartjs_real.py. Importing
this module skips the importing test file when Playwright is not installed,
so the rest of the suite is unaffected.

Chart.js and the three kinds of browser test (Phase 7)
------------------------------------------------------
A browser test is in exactly one of these groups (pytest markers):

  chartjs_stub     Chart.js is REPLACED by CHART_STUB below, a stand-in that
                   records `new Chart(canvas, config)` calls and draws
                   nothing. Proves OUR page, CSP and analytics.js work.
                   Proves nothing about how Chart.js draws.
  chartjs_offline  The Chart.js URL is blocked, as if the browser had no
                   internet: checks the text fallback.
  chartjs_real     Uses the REAL Chart.js file served from
                   tradeledger/static/vendor/chartjs/. Skipped (and reported
                   as skipped) unless that file is present.

Tests that never open the dashboard carry none of these markers; they
still get the stand-in, because a page might link to the dashboard.

Signing in (Phase 8)
--------------------
Every server has the default test account (conftest.TEST_EMAIL) holding the
sample data. page_factory(server=live_server) gives a page that is already
signed in: the server mints a real signed session cookie, exactly what the
sign-in form would set. The sign-in journey itself is tested through the
real form in tests/test_browser_auth.py.
"""

import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from tests.conftest import ensure_user, new_server_session  # noqa: E402
from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

WIDTHS = [375, 768, 1024, 1440]
CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
LOCAL_CHART_JS = Path(__file__).resolve().parent.parent / "tradeledger/static/vendor/chartjs/chart.umd.min.js"

# Records chart configurations; draws nothing. Never shipped to users.
CHART_STUB = """
(function () {
    function Chart(canvas, config) {
        this.canvas = canvas;
        this.config = config;
        (window.__charts = window.__charts || []).push(this);
    }
    Chart.defaults = {font: {}};
    window.Chart = Chart;
})();
"""


class LiveServer(str):
    """The server's base URL (a str, so `server + "/trades"` works), plus
    what a browser needs to be signed in to it."""

    def __new__(cls, url, app):
        server = super().__new__(cls, url)
        server.app = app
        return server

    def session_cookie(self, user_id=1):
        """A valid signed session cookie for `user_id`, as /login would set."""
        now = int(time.time())
        key = new_server_session(self.app, user_id, now)  # Phase 8.5: server-side row too
        data = {"sid": key, "uid": user_id, "sv": 1, "auth_at": now, "seen_at": now, "_permanent": True}
        value = self.app.session_interface.get_signing_serializer(self.app).dumps(data)
        return {"name": self.app.config["SESSION_COOKIE_NAME"], "value": value, "url": str(self)}


def start_server(db_path, overrides=None, seed=True, prepare=None):
    """Run TradeLedger on a free local port in a background thread.

    The default test account (user 1) always exists; seed=True gives it
    the sample data. prepare: optional function called with the database
    connection afterwards, to add test-specific data.
    Returns (LiveServer, stop_function)."""
    app = create_app("testing", overrides={"DATABASE": db_path, **(overrides or {})})
    with app.app_context():
        init_schema(get_db())
        user_id = ensure_user(get_db())
        if seed:
            seed_sample_data(get_db(), user_id=user_id)
        if prepare:
            prepare(get_db())
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        thread.join(timeout=5)
        app.extensions["tradeledger_db"].close()

    return LiveServer(f"http://127.0.0.1:{server.server_port}", app), stop


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """A fresh server and database for each test MODULE."""
    url, stop = start_server(str(tmp_path_factory.mktemp("browser") / "browser.db"))
    yield url
    stop()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            launched = p.chromium.launch()
        except playwright_api.Error as exc:  # browser binary not installed
            pytest.skip(f"Chromium is not available: {exc}")
        yield launched
        launched.close()


@pytest.fixture
def page_factory(browser):
    """Create pages; every page records JavaScript errors and console errors
    (including Content-Security-Policy violations) in page.problems.

    chart_js: "stub" (default), "blocked" (offline) or "real" (no
    interception: the page loads whatever URL the server configured).
    server:   a LiveServer to be signed in to as the default test account;
              omit it for an anonymous visitor.
    """
    contexts = []

    def make(width=1440, height=900, chart_js="stub", javascript=True, server=None, **context_options):
        context = browser.new_context(
            viewport={"width": width, "height": height}, java_script_enabled=javascript, **context_options
        )
        contexts.append(context)
        if server is not None:
            context.add_cookies([server.session_cookie()])
        if chart_js == "stub":
            context.route(
                CHART_JS_URL,
                lambda route: route.fulfill(
                    status=200, content_type="application/javascript", body=CHART_STUB
                ),
            )
        elif chart_js == "blocked":
            context.route(CHART_JS_URL, lambda route: route.abort("internetdisconnected"))
        page = context.new_page()
        page.problems = []
        page.on("pageerror", lambda exc: page.problems.append(str(exc)))
        page.on(
            "console",
            lambda msg: (
                page.problems.append(msg.text)
                if msg.type == "error" and "status of 4" not in msg.text
                else None
            ),
        )
        return page

    yield make
    for context in contexts:
        context.close()


def horizontal_overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def submit_and_wait(page, selector="form[data-trade-form] button[type=submit]"):
    """Click a form's submit button and wait for the page that comes back."""
    with page.expect_navigation():
        page.locator(selector).click()
