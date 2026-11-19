"""Real-browser checks with Playwright (Phases 5-7).

These start TradeLedger on a local port in a background thread and drive a
real Chromium browser, so they test what the test client cannot: CSS
layout, JavaScript behaviour, focus handling and the Content-Security-Policy.

They are OPTIONAL. Playwright is in requirements-browser.txt, not the core
requirements (it downloads a ~150 MB browser). If it, or its browser, is
missing, every test here is skipped and the rest of the suite is unaffected.

Chart.js: the dashboard tests here are marked `chartjs_stub` (Chart.js is
replaced by a recording stand-in) or `chartjs_offline` (Chart.js blocked).
NONE of them shows that the real Chart.js draws correctly; that is
tests/test_chartjs_real.py, which needs the real file (see
tests/browser_support.py for the details).

To run them:

    pip install -r requirements-browser.txt -c constraints.txt
    python -m playwright install chromium
    pytest tests/test_browser.py
"""

import pytest

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    WIDTHS,
    browser,
    horizontal_overflow,
    live_server,
    page_factory,
)

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize(
    "path",
    ["/", "/trades", "/trades/new", "/trades/1", "/trades/1/delete", "/instruments", "/analytics", "/about"],
)
def test_no_horizontal_scrolling_and_no_errors(live_server, page_factory, path, width):
    page = page_factory(width, server=live_server)
    page.goto(live_server + path)
    assert horizontal_overflow(page) == 0
    assert page.problems == []  # includes CSP violations


@pytest.mark.parametrize("width, menu_visible", [(375, True), (768, True), (1024, False), (1440, False)])
def test_menu_button_only_on_narrow_screens(live_server, page_factory, width, menu_visible):
    page = page_factory(width, server=live_server)
    page.goto(live_server + "/")
    assert page.locator("[data-nav-toggle]").is_visible() is menu_visible
    assert page.locator("#sidebar a", has_text="Instruments").is_visible() is not menu_visible


def test_mobile_menu_opens_moves_focus_and_closes_with_escape(live_server, page_factory):
    page = page_factory(375, server=live_server)
    page.goto(live_server + "/trades")
    toggle = page.locator("[data-nav-toggle]")
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    page.wait_for_function("document.activeElement.closest('#sidebar') !== null")
    page.keyboard.press("Escape")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.hasAttribute('data-nav-toggle')")


def test_skip_link_is_first_and_reaches_main(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.classList.contains('skip-link')")
    page.keyboard.press("Enter")
    assert page.evaluate("location.hash") == "#main"


def test_open_status_hides_and_disables_exit_fields(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades/new")
    exit_fields = page.locator("[data-exit-fields]")
    assert exit_fields.is_visible()
    page.locator("input[name=status][value=OPEN]").check(force=True)
    assert not exit_fields.is_visible()
    assert page.locator("#field-exit_price").is_disabled()
    page.locator("input[name=status][value=CLOSED]").check(force=True)
    assert exit_fields.is_visible()
    assert page.locator("#field-exit_price").get_attribute("required") is not None


def test_server_errors_are_shown_and_focused(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades/new")
    page.select_option("#field-instrument_id", "1")
    page.fill("#field-trade_date", "2026-04-01T09:00")
    page.fill("#field-entry_price", "1.1000")
    page.fill("#field-stop_loss", "1.1050")  # wrong side for a long
    page.fill("#field-position_size", "1")
    page.locator("form[data-trade-form] button[type=submit]").click()
    page.wait_for_load_state()
    assert page.locator(".error-summary").is_visible()
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    assert page.input_value("#field-entry_price") == "1.1000"
    assert page.get_attribute("#field-stop_loss", "aria-invalid") == "true"


def test_create_and_delete_through_the_browser(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades/new")
    page.select_option("#field-instrument_id", "1")
    page.fill("#field-trade_date", "2026-04-01T09:00")
    page.fill("#field-entry_price", "1.1000")
    page.fill("#field-stop_loss", "1.0950")
    page.fill("#field-position_size", "1")
    page.fill("#field-exit_price", "1.1100")
    page.fill("#field-closed_at", "2026-04-01T12:00")
    page.fill("#field-notes", "<script>window.pwned = 1</script> browser test")
    page.locator("form[data-trade-form] button[type=submit]").click()
    page.wait_for_url("**/trades/*")
    assert page.locator(".flash", has_text="saved").is_visible()
    assert page.evaluate("window.pwned === undefined")  # rendered as text
    trade_url = page.url

    page.goto(trade_url + "/delete")
    page.locator("form button[type=submit]", has_text="Delete").click()
    page.wait_for_url("**/trades")
    assert page.locator(".flash", has_text="deleted").is_visible()
    assert page.goto(trade_url).status == 404


def test_double_submission_is_blocked(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades/1/delete")
    # Count submissions that get through app.js, and stop the real
    # navigation so the page can be inspected afterwards. This listener is
    # added after app.js's, so it only sees submits app.js let through.
    page.evaluate("""() => {
        window.submissions = 0;
        document.querySelector('form[data-submit-once]').addEventListener('submit', e => {
            if (!e.defaultPrevented) { window.submissions += 1; }
            e.preventDefault();
        });
    }""")
    button = page.locator("form[data-submit-once] button[type=submit]")
    button.click()
    button.click()  # an impatient second click
    assert page.evaluate("window.submissions") == 1
    assert page.locator("form[data-submit-once]").get_attribute("data-submitting") == "true"
    assert button.get_attribute("aria-busy") == "true"
    assert button.inner_text() == "Deleting…"


# ---------- Phase 6: analytics dashboard ----------


def charts_by_name(page):
    return page.evaluate("""() => Object.fromEntries((window.__charts || []).map(
        (c) => [c.canvas.dataset.chart, {type: c.config.type,
                                         points: c.config.data.datasets[0].data.length,
                                         indexAxis: c.config.options.indexAxis || "x"}]))""")


@pytest.mark.chartjs_stub
def test_dashboard_builds_all_seven_charts(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    page.wait_for_function("(window.__charts || []).length === 7")
    charts = charts_by_name(page)
    assert charts == {
        "curve": {"type": "line", "points": 15, "indexAxis": "x"},  # start + 14 trades
        "drawdown": {"type": "line", "points": 15, "indexAxis": "x"},
        "monthly": {"type": "bar", "points": 1, "indexAxis": "x"},  # March 2026 only
        "weekday": {"type": "bar", "points": 7, "indexAxis": "x"},
        "instruments": {"type": "bar", "points": 5, "indexAxis": "y"},
        "strategies": {"type": "bar", "points": 4, "indexAxis": "y"},
        "sessions": {"type": "bar", "points": 4, "indexAxis": "y"},
    }
    assert page.problems == []  # no JS errors, no CSP violations


@pytest.mark.chartjs_stub
def test_csp_violations_would_be_detected(live_server, page_factory):
    """Negative control: proves the console listener above really catches a
    Content-Security-Policy violation, so 'no problems' means something."""
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    page.evaluate("""() => { const s = document.createElement("script");
                             s.src = "https://example.com/evil.js"; document.head.appendChild(s); }""")
    page.wait_for_timeout(300)
    assert any("Content Security Policy" in problem for problem in page.problems)


@pytest.mark.chartjs_stub
def test_chart_tooltips_show_the_server_figures(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    page.wait_for_function("(window.__charts || []).length === 7")
    tooltip = page.evaluate("""() => {
        const chart = (name) => window.__charts.find((c) => c.canvas.dataset.chart === name);
        const label = (name, i) => chart(name).config.options.plugins.tooltip.callbacks.label({dataIndex: i});
        const title = (name, i) => chart(name).config.options.plugins.tooltip.callbacks.title([{dataIndex: i}]);
        return {curveTitle: title("curve", 1), curve: label("curve", 1),
                drawdown: label("drawdown", 9), weekday: label("weekday", 5), session: title("sessions", 2)};
    }""")
    assert tooltip["curveTitle"] == "02 Mar 2026, 11:40 UTC"
    assert tooltip["curve"] == ["Trade #1 · EURUSD", "Trade R: +1.97R", "Cumulative R: +1.97R"]
    assert tooltip["drawdown"][0] == "Drawdown: 1.05R below the running peak"
    assert "Maximum drawdown: 1.05R" in tooltip["drawdown"]
    assert tooltip["weekday"] == ["No closed trades"]  # Saturday
    assert tooltip["session"] == "New York"


@pytest.mark.chartjs_stub
def test_filters_update_cards_and_charts(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    page.select_option("#dash-strategy", "Breakout")
    page.select_option("#dash-session", "LONDON")
    page.locator(".dash-filters button[type=submit]").click()
    page.wait_for_url("**/analytics?*")
    assert "strategy=Breakout" in page.url and "session=LONDON" in page.url
    assert page.locator('[data-kpi="trades"] .kpi__value').inner_text() == "3"
    page.wait_for_function("(window.__charts || []).length === 7")
    assert charts_by_name(page)["curve"]["points"] == 4  # start + trades 1, 3, 10
    page.get_by_role("link", name="Reset filters").click()
    page.wait_for_url(live_server + "/analytics")
    assert page.locator('[data-kpi="trades"] .kpi__value').inner_text() == "14"


def test_invalid_filter_is_explained_in_the_browser(live_server, page_factory):
    page = page_factory(server=live_server)
    response = page.goto(live_server + "/analytics?instrument_id=999")
    assert response.status == 400
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    assert page.locator("canvas").count() == 0


def test_empty_result_shows_a_helpful_state(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics?start=2030-01-01")
    assert page.get_by_text("No closed trades match these filters").is_visible()
    assert page.locator("canvas").count() == 0
    assert page.problems == []


@pytest.mark.chartjs_offline
def test_offline_chart_library_falls_back_to_text(live_server, page_factory):
    page = page_factory(chart_js="blocked", server=live_server)
    page.goto(live_server + "/analytics")
    page.wait_for_selector("[data-chart-fallback]:not([hidden])")
    notes = page.locator("[data-chart-fallback]:not([hidden])")
    assert notes.count() == 7
    assert "could not be loaded" in notes.first.inner_text()
    assert page.locator("canvas:visible").count() == 0
    assert page.locator("#table-instruments").is_visible()
    # The only console error is the blocked download itself.
    assert all("ERR_INTERNET_DISCONNECTED" in p or "Failed to load resource" in p for p in page.problems)


def test_dashboard_without_javascript(live_server, page_factory):
    page = page_factory(javascript=False, server=live_server)
    page.goto(live_server + "/analytics?strategy=Breakout")
    assert page.get_by_text("Charts need JavaScript").is_visible()
    assert page.locator('[data-kpi="trades"] .kpi__value').inner_text() == "5"
    assert page.locator("#table-series").is_visible()
    assert page.locator("[data-chart-box]").first.is_hidden()


@pytest.mark.chartjs_stub
@pytest.mark.parametrize("width", WIDTHS)
def test_dashboard_layout_at_each_width(live_server, page_factory, width):
    page = page_factory(width, server=live_server)
    page.goto(live_server + "/analytics")
    page.wait_for_function("(window.__charts || []).length === 7")
    assert horizontal_overflow(page) == 0
    boxes = page.evaluate("""() => [...document.querySelectorAll('[data-chart-box]')].map((b) => {
        const r = b.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), Math.round(r.height)];
    })""")
    for left, right, height in boxes:
        assert left >= 0 and right <= width and height >= 200
    cards = page.evaluate(
        """() => [...document.querySelectorAll('.kpi')].map((k) => k.getBoundingClientRect().right)"""
    )
    assert len(cards) == 12 and max(cards) <= width


def test_filters_are_keyboard_operable(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    page.focus("#dash-instrument_id")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "dash-strategy"
    page.focus("#table-instruments")
    assert page.evaluate("document.activeElement.id") == "table-instruments"
