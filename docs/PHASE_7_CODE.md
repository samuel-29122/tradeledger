# TradeLedger — Phase 7 code listing
Every source file added, changed or removed in Phase 7, in full, generated from the files in `tradeledger_phase7.zip` so the two always agree. Documentation (README, changelog, guides, reports, `docs/openapi.json`) is delivered as its own files and not repeated here.
## Inventory
- **NEW (16):** `tradeledger/timestamps.py`, `tests/browser_support.py`, `tests/test_browser_journey.py`, `tests/test_chartjs_real.py`, `tests/test_database_integrity.py`, `tests/test_error_paths.py`, `tests/test_financial_edge_cases.py`, `tests/test_security_hardening.py`, `tests/test_timestamps.py`, `.github/ISSUE_TEMPLATE/bug_report.md`, `.github/pull_request_template.md`, `.github/workflows/ci.yml`, `constraints.txt`, `pyproject.toml`, `requirements-browser.txt`, `requirements-dev.txt`
- **MODIFIED, behaviour or tests changed (27):** `tradeledger/__init__.py`, `tradeledger/api/openapi.py`, `tradeledger/config.py`, `tradeledger/performance/dashboard.py`, `tradeledger/performance/report.py`, `tradeledger/performance/trade_math.py`, `tradeledger/repositories/trades.py`, `tradeledger/static/js/analytics.js`, `tradeledger/static/vendor/chartjs/README.md`, `tradeledger/templates/about.html`, `tradeledger/validation.py`, `tradeledger/web/__init__.py`, `tradeledger/web/csrf.py`, `tradeledger/web/filters.py`, `tradeledger/web/guard.py`, `tradeledger/web/trades.py`, `tradeledger/web/views.py`, `tests/test_app.py`, `tests/test_browser.py`, `tests/test_web_analytics.py`, `tests/test_web_pages.py`, `tests/test_web_security.py`, `scripts/benchmark_dashboard.py`, `scripts/qa_dashboard_agreement.py`, `.env.example`, `.gitignore`, `requirements.txt`
- **MODIFIED, lint fixes only, behaviour unchanged (12):** `tradeledger/api/http.py`, `tradeledger/cli.py`, `tradeledger/performance/metrics.py`, `tradeledger/report_filters.py`, `tradeledger/web/instruments.py`, `tests/test_dashboard_data.py`, `tests/test_db.py`, `tests/test_migrations.py`, `tests/test_openapi.py`, `tests/test_report_filters.py`, `tests/test_seed_and_analytics.py`, `tests/test_web_integration.py`
- **MODIFIED, layout only (20):** `tradeledger/api/__init__.py`, `tradeledger/api/serialise.py`, `tradeledger/repositories/instruments.py`, `tradeledger/seed.py`, `tradeledger/web/analytics.py`, `tradeledger/web/forms.py`, `tests/conftest.py`, `tests/test_api_analytics.py`, `tests/test_api_instruments.py`, `tests/test_api_security.py`, `tests/test_api_trades.py`, `tests/test_chronology.py`, `tests/test_javascript.py`, `tests/test_metrics.py`, `tests/test_properties.py`, `tests/test_report.py`, `tests/test_repositories.py`, `tests/test_trade_math.py`, `tests/test_validation.py`, `tests/test_web_forms.py`. Proof: their Python syntax trees are identical before and after `ruff format`.
- **DELETED (1):** `pytest.ini` (its settings moved to `pyproject.toml`).
- **Unchanged:** every other file, for example the templates other than `about.html`, the CSS and `tests/js/analytics.test.js`.

## 1. New files

### `tradeledger/timestamps.py` (NEW)

**Why:** NEW: fast, strict parser for stored UTC timestamps (the performance fix).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Reading TradeLedger's stored UTC timestamps, quickly (Phase 7).

Every stored time has exactly one shape, 'YYYY-MM-DDTHH:MM:SSZ' (UTC). The
database enforces it with CHECK constraints on trade_date and closed_at,
and validation.parse_timestamp() writes nothing else.

Why this module exists: profiling GET /analytics with 10,000 trades showed
datetime.strptime() called about five times per trade, roughly a third of
the request time. strptime re-interprets its format string on every call.
A strict pattern check followed by datetime.fromisoformat() (implemented
in C) gives the same answers for this one shape, several times faster.
tests/test_timestamps.py compares the two on valid and invalid input.
"""

import re
from datetime import datetime

STORED_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# [0-9], not \d: \d would also accept non-ASCII digits such as "٢".
_STORED_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def parse_stored(text):
    """'2026-03-02T08:15:00Z' -> datetime(2026, 3, 2, 8, 15) (naive, meaning UTC).

    Raises TypeError for a non-string and ValueError for anything that is
    not a real date and time in exactly the stored shape, the same
    exceptions datetime.strptime(text, STORED_FORMAT) raises.
    """
    if not isinstance(text, str):
        raise TypeError(f"a stored timestamp must be text, not {type(text).__name__}")
    if not _STORED_SHAPE.fullmatch(text):
        raise ValueError(f"not a stored timestamp (YYYY-MM-DDTHH:MM:SSZ): {text!r}")
    # Dropping the "Z" gives a naive datetime, exactly what strptime returned.
    # fromisoformat still rejects impossible values such as month 13 or 30 February.
    return datetime.fromisoformat(text[:-1])
```

### `tests/browser_support.py` (NEW)

**Why:** NEW: shared Playwright fixtures; Chart.js modes stub / blocked / real; start_server(prepare=...).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
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
"""

import threading
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

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


def start_server(db_path, overrides=None, seed=True, prepare=None):
    """Run TradeLedger on a free local port in a background thread.

    prepare: optional function called with the database connection after
    seeding, to add test-specific data. Returns (base_url, stop_function)."""
    app = create_app("testing", overrides={"DATABASE": db_path, **(overrides or {})})
    with app.app_context():
        init_schema(get_db())
        if seed:
            seed_sample_data(get_db())
        if prepare:
            prepare(get_db())
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        thread.join(timeout=5)

    return f"http://127.0.0.1:{server.server_port}", stop


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
    """
    contexts = []

    def make(width=1440, height=900, chart_js="stub", javascript=True, **context_options):
        context = browser.new_context(
            viewport={"width": width, "height": height}, java_script_enabled=javascript, **context_options
        )
        contexts.append(context)
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
```

### `tests/test_browser_journey.py` (NEW)

**Why:** NEW: 15-step end-to-end journey; browser validation; accessibility checks with a negative control; 200% zoom.  
**How to run or test:** Run: `python -m pytest tests/test_browser_journey.py`

```python
"""Phase 7: an end-to-end user journey and accessibility checks in Chromium.

Uses its own server and database (see tests/browser_support.py), seeded
with the synthetic sample data: 16 trades, 14 of them closed.

The journey's expected figures, worked out by hand:
  instrument QA7: quote currency GBP, contract multiplier 2
  trade: LONG, entry 100, stop 95, size 3
    initial risk = (100 - 95) x 3 x 2 = 30 GBP
  closed at 108 with fees 1.50:
    net = (108 - 100) x 3 x 2 - 1.50 = 46.50 GBP     R = 46.50 / 30 = +1.55R
  edited to fees 4.50:
    net = 48 - 4.50 = 43.50 GBP                      R = 43.50 / 30 = +1.45R

Accessibility: these are automated checks of specific rules (labels,
headings, table headers, text alternatives, focus, reduced motion, zoom).
They do NOT amount to a WCAG audit; see docs/PHASE_7_QA_REPORT.md for the
manual checks that remain (screen readers, contrast review, other browsers).
"""

import re

import pytest

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    WIDTHS,
    browser,
    horizontal_overflow,
    live_server,
    page_factory,
    submit_and_wait,
)

pytestmark = pytest.mark.browser

PAGES = ["/", "/trades", "/trades/new", "/trades/1", "/trades/1/edit", "/instruments", "/analytics", "/about"]


def kpi(page, key):
    return page.locator(f'[data-kpi="{key}"] .kpi__value').inner_text()


def option_value(page, select, text):
    """The value of the <option> whose visible text contains `text`."""
    return page.evaluate(
        "([sel, text]) => [...document.querySelector(sel).options].find(o => o.text.includes(text)).value",
        [select, text],
    )


# ---------- The journey ----------


@pytest.mark.chartjs_stub
def test_full_user_journey(live_server, page_factory):
    page = page_factory()

    # 1-2. Overview, then the sidebar.
    page.goto(live_server + "/")
    assert page.locator("h1").inner_text().strip() != ""
    page.locator("#sidebar a", has_text="Instruments").click()
    page.wait_for_url("**/instruments")

    # 3. Create an instrument.
    page.fill("#field-symbol", "qa7")
    page.fill("#field-name", "QA journey index")
    page.select_option("#field-asset_class", "OTHER")
    page.fill("#field-quote_currency", "GBP")
    page.fill("#field-price_increment", "0.5")
    page.fill("#field-contract_multiplier", "2")
    submit_and_wait(page, "form[data-submit-once] button[type=submit]")
    assert page.locator(".flash", has_text="Instrument QA7 added.").is_visible()

    # 4. Add an OPEN trade.
    page.locator("#sidebar a", has_text="Add trade").click()
    page.wait_for_url("**/trades/new")
    page.select_option("#field-instrument_id", option_value(page, "#field-instrument_id", "QA7"))
    page.locator("input[name=status][value=OPEN]").check(force=True)
    page.fill("#field-trade_date", "2026-06-01T09:00")
    page.fill("#field-entry_price", "100")
    page.fill("#field-stop_loss", "95")
    page.fill("#field-take_profit", "110")
    page.fill("#field-position_size", "3")
    page.fill("#field-fees", "1.50")
    page.fill("#field-strategy", "Journey")
    page.select_option("#field-session", "LONDON")
    submit_and_wait(page)
    trade_url = page.url
    trade_id = int(re.search(r"/trades/(\d+)$", trade_url).group(1))
    assert page.locator(".flash", has_text=f"Trade #{trade_id} saved.").is_visible()
    assert page.get_by_text("This trade is still open").is_visible()

    # 5-6. Close it and check the calculated result.
    page.goto(f"{trade_url}/edit")
    page.locator("input[name=status][value=CLOSED]").check(force=True)
    page.fill("#field-exit_price", "108")
    page.fill("#field-closed_at", "2026-06-02T15:30")
    submit_and_wait(page)
    result = page.locator(".result-card").inner_text()
    assert "46.50 GBP" in result and "+1.55R" in result

    # 7. Search and filter the journal.
    page.goto(live_server + "/trades")
    page.fill("#filter-q", "journey")
    page.select_option("#filter-status", "CLOSED")
    with page.expect_navigation():
        page.get_by_role("button", name="Apply filters").click()
    assert "q=journey" in page.url and "status=CLOSED" in page.url
    assert page.get_by_text("Showing 1–1 of 1 trade matching your filters.").is_visible()

    # 8. Edit the trade: higher fees.
    page.goto(f"{trade_url}/edit")
    page.fill("#field-fees", "4.50")
    submit_and_wait(page)
    assert page.locator(".flash", has_text=f"Trade #{trade_id} updated.").is_visible()
    result = page.locator(".result-card").inner_text()
    assert "43.50 GBP" in result and "+1.45R" in result

    # 9-10. Dashboard with two filters.
    page.goto(live_server + "/analytics")
    assert kpi(page, "trades") == "15"  # 14 sample + ours
    page.select_option("#dash-instrument_id", option_value(page, "#dash-instrument_id", "QA7"))
    page.select_option("#dash-strategy", "Journey")
    with page.expect_navigation():
        page.locator(".dash-filters button[type=submit]").click()
    assert kpi(page, "trades") == "1" and kpi(page, "total_r") == "+1.45R"
    assert "43.50 GBP" in page.locator(".money-strip").inner_text()
    page.wait_for_function("(window.__charts || []).length === 7")

    # 12. The data tables hold the same figure.
    row = page.locator("#table-instruments tbody tr", has_text="QA7")
    assert "+1.45R" in row.inner_text()

    # 11. Reset the filters.
    page.get_by_role("link", name="Reset filters").click()
    page.wait_for_url(live_server + "/analytics")
    assert kpi(page, "trades") == "15"

    # 13. Delete the trade.
    page.goto(f"{trade_url}/delete")
    with page.expect_navigation():
        page.locator("form button[type=submit]", has_text="Delete").click()
    assert page.locator(".flash", has_text=f"Trade #{trade_id} (QA7 LONG) was deleted.").is_visible()

    # 14. Journal and analytics both forget it.
    page.goto(live_server + "/trades?q=journey")
    assert page.get_by_text("No trades match").is_visible()
    page.goto(live_server + "/analytics")
    assert kpi(page, "trades") == "14"
    assert page.goto(trade_url).status == 404
    assert page.problems == []


def test_validation_errors_in_the_browser(live_server, page_factory):
    """15. Wrong-side stop on the trade form; duplicate symbol on the
    instrument form. Both show a focused error summary and keep the input."""
    page = page_factory()
    page.goto(live_server + "/trades/new")
    page.select_option("#field-instrument_id", "1")
    page.fill("#field-trade_date", "2026-06-03T09:00")
    page.fill("#field-entry_price", "1.1000")
    page.fill("#field-stop_loss", "1.1050")  # above entry for a LONG
    page.fill("#field-position_size", "1")
    submit_and_wait(page)
    summary = page.locator(".error-summary")
    assert summary.is_visible() and summary.get_attribute("role") == "alert"
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    for href in summary.locator("a").evaluate_all("links => links.map(a => a.getAttribute('href'))"):
        assert page.locator(href).count() == 1, href  # every summary link reaches its field
    assert page.input_value("#field-entry_price") == "1.1000"

    page.goto(live_server + "/instruments")
    page.fill("#field-symbol", "EURUSD")
    page.fill("#field-name", "Duplicate")
    page.select_option("#field-asset_class", "FOREX")
    page.fill("#field-quote_currency", "USD")
    page.fill("#field-price_increment", "0.00001")
    page.fill("#field-contract_multiplier", "100000")
    submit_and_wait(page, "form[data-submit-once] button[type=submit]")
    assert page.locator(".error-summary").is_visible()
    assert (
        "Symbol is already recorded. Symbols must be unique." in page.locator(".error-summary").inner_text()
    )


# ---------- Accessibility checks ----------

UNLABELLED_CONTROLS = """() => [...document.querySelectorAll('input, select, textarea')]
    .filter(el => el.type !== 'hidden')
    .filter(el => !(el.labels && el.labels.length) && !el.getAttribute('aria-label')
                  && !el.getAttribute('aria-labelledby'))
    .map(el => el.outerHTML.slice(0, 80))"""

HEADING_PROBLEMS = """() => {
    const levels = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6')].map(h => +h.tagName[1]);
    const problems = [];
    if (levels.filter(l => l === 1).length !== 1) problems.push('expected exactly one h1');
    levels.forEach((l, i) => { if (i && l > levels[i - 1] + 1) problems.push(`h${levels[i - 1]} -> h${l}`); });
    return problems;
}"""

TABLE_PROBLEMS = """() => [...document.querySelectorAll('table')].flatMap(t => {
    const name = t.querySelector('caption') || t.getAttribute('aria-label') || t.getAttribute('aria-labelledby');
    const problems = name ? [] : [`table without a caption or label: ${t.id || t.className}`];
    t.querySelectorAll('tbody th').forEach(th => {
        if (!th.getAttribute('scope')) problems.push(`row header without scope in ${t.id || t.className}`);
    });
    if (!t.querySelector('thead th')) problems.push(`table without column headers: ${t.id || t.className}`);
    return problems;
})"""


@pytest.mark.parametrize("path", PAGES)
def test_every_form_control_has_a_label(live_server, page_factory, path):
    page = page_factory()
    page.goto(live_server + path)
    assert page.evaluate(UNLABELLED_CONTROLS) == []


@pytest.mark.parametrize("path", PAGES)
def test_headings_form_an_outline(live_server, page_factory, path):
    page = page_factory()
    page.goto(live_server + path)
    assert page.evaluate(HEADING_PROBLEMS) == []
    assert page.evaluate("document.documentElement.lang") == "en-GB"


@pytest.mark.parametrize("path", ["/", "/trades", "/instruments", "/analytics"])
def test_tables_have_names_and_headers(live_server, page_factory, path):
    page = page_factory()
    page.goto(live_server + path)
    assert page.evaluate(TABLE_PROBLEMS) == []


def test_accessibility_checks_can_fail(live_server, page_factory):
    """Negative control: break the page on purpose and prove each check
    notices, so an empty result above really means "no problems found"."""
    page = page_factory()
    page.goto(live_server + "/about")
    page.evaluate(
        """() => {
            document.querySelector('main').insertAdjacentHTML('beforeend',
                '<input id="nolabel"><h4>Skipped levels</h4><table><tbody><tr><th>x</th></tr></tbody></table>');
        }"""
    )
    assert page.evaluate(UNLABELLED_CONTROLS) != []
    assert any("->" in problem for problem in page.evaluate(HEADING_PROBLEMS))
    assert len(page.evaluate(TABLE_PROBLEMS)) == 3  # no name, no scope, no column headers


@pytest.mark.chartjs_stub
def test_every_chart_has_a_text_alternative_and_a_data_table(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/analytics")
    charts = page.locator("canvas[data-chart]")
    assert charts.count() == 7
    for i in range(7):
        canvas = charts.nth(i)
        name = canvas.get_attribute("data-chart")
        assert canvas.get_attribute("role") == "img"
        assert len(canvas.get_attribute("aria-label") or "") > 20, name
        assert page.locator(f"#chart-{name}-desc").inner_text().strip(), name  # visible description


def test_keyboard_focus_is_always_visible(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/trades")
    invisible = []
    for _ in range(15):
        page.keyboard.press("Tab")
        style = page.evaluate(
            """() => { const el = document.activeElement; const s = getComputedStyle(el);
                       return {tag: el.tagName, text: (el.innerText || el.name || '').slice(0, 30),
                               shadow: s.boxShadow, outline: s.outlineStyle}; }"""
        )
        if style["shadow"] == "none" and style["outline"] == "none":
            invisible.append(style)
    assert invisible == []


def test_reduced_motion_preference_is_respected(live_server, page_factory):
    page = page_factory(375, 800, reduced_motion="reduce")
    page.goto(live_server + "/")
    duration = page.evaluate("getComputedStyle(document.querySelector('#sidebar')).transitionDuration")
    assert all(float(part.rstrip("s")) <= 0.001 for part in duration.split(","))


def test_outcomes_are_not_shown_by_colour_alone(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/trades")
    badges = page.locator("td .badge").all_inner_texts()
    assert badges and all(text.strip() for text in badges)  # every badge has a word in it
    page.goto(live_server + "/analytics?session=ASIA")
    total = page.locator('[data-kpi="total_r"] .kpi__value').inner_text()
    assert total[0] in "+-0"  # the sign is written, not only coloured


@pytest.mark.parametrize("width", [720, 683])
def test_200_percent_zoom_has_no_horizontal_scrolling(live_server, page_factory, width):
    """200% zoom on a 1440 px or 1366 px screen leaves a 720 or 683 CSS-pixel
    viewport; device_scale_factor=2 renders it at double density."""
    page = page_factory(width, 450, device_scale_factor=2)
    for path in PAGES:
        page.goto(live_server + path)
        assert horizontal_overflow(page) == 0, path


def test_text_stays_readable_on_a_phone(live_server, page_factory):
    page = page_factory(375, 800)
    page.goto(live_server + "/trades/1")
    size = page.evaluate("parseFloat(getComputedStyle(document.body).fontSize)")
    assert size >= 16
```

### `tests/test_chartjs_real.py` (NEW)

**Why:** NEW: REAL Chart.js 4.5.1 rendering tests (skipped unless the file is installed; failing in CI if missing).  
**How to run or test:** Run: `python -m pytest tests/test_chartjs_real.py`

```python
"""Phase 7: REAL Chart.js rendering tests (marker: chartjs_real).

These are the only tests that can show the seven charts are actually drawn
by Chart.js. They need the genuine library file:

    tradeledger/static/vendor/chartjs/chart.umd.min.js   (Chart.js 4.5.1)
    tradeledger/static/vendor/chartjs/LICENSE.md          (its MIT licence)

Without those files every test here is SKIPPED, and pytest says so in its
summary ("skipped: real Chart.js file not installed"). A skip is never a
pass: see docs/PHASE_7_QA_REPORT.md for the current status. Setting
TRADELEDGER_REQUIRE_REAL_CHARTJS=1 (as CI does after downloading the file)
turns the skip into a FAILURE, so a missing file cannot pass silently.

What they check, with the library served from TradeLedger itself (CSP stays
"script-src 'self'") and its Subresource Integrity hash enabled:
  * the file really is Chart.js 4.5.1 and its licence is present;
  * the browser accepts the integrity hash and loads the library;
  * all seven charts exist as Chart.js instances and paint pixels;
  * chart data matches the server's JSON payload, point for point;
  * tooltips (Chart.js's own tooltip model) show the same text as the tables;
  * an empty month is a gap (null), weekdays run Monday to Sunday;
  * positive and negative bars get different colours; the maximum
    drawdown point is marked;
  * filters change the charts; resizing keeps charts inside their cards;
  * no console errors and no CSP violations.

Optional real screenshots: set TRADELEDGER_SCREENSHOT_DIR=docs/screenshots.
"""

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    LOCAL_CHART_JS,
    browser,
    horizontal_overflow,
    page_factory,
    start_server,
)
from tradeledger.repositories.trades import create_trade

EXPECTED_VERSION = "4.5.1"
LICENCE = LOCAL_CHART_JS.parent / "LICENSE.md"
LOCAL_URL = "/static/vendor/chartjs/chart.umd.min.js"
CHART_NAMES = ["curve", "drawdown", "monthly", "weekday", "instruments", "strategies", "sessions"]

REQUIRED = os.environ.get("TRADELEDGER_REQUIRE_REAL_CHARTJS") == "1"

pytestmark = [
    pytest.mark.browser,
    pytest.mark.chartjs_real,
    pytest.mark.skipif(
        not LOCAL_CHART_JS.exists() and not REQUIRED, reason="real Chart.js file not installed"
    ),
]


def add_january_trade(conn):
    """One extra LOSING trade in January 2026, in its own strategy "Fade".
    February becomes an empty month between January and the March sample
    data (a gap, not a zero), and the strategy chart gets a negative bar
    (every sample strategy has a positive total)."""
    create_trade(
        conn,
        {
            "instrument_id": 1,
            "direction": "LONG",
            "status": "CLOSED",
            "trade_date": "2026-01-14T09:00:00Z",
            "closed_at": "2026-01-14T15:00:00Z",
            "entry_price": "1.1000",
            "stop_loss": "1.0950",
            "exit_price": "1.0975",
            "position_size": "1",
            "strategy": "Fade",
            "session": "ASIA",
        },
    )


@pytest.fixture(scope="module")
def real_server(tmp_path_factory):
    digest = hashlib.sha384(LOCAL_CHART_JS.read_bytes()).digest()
    integrity = "sha384-" + base64.b64encode(digest).decode()
    url, stop = start_server(
        str(tmp_path_factory.mktemp("chartjs") / "real.db"),
        overrides={"CHART_JS_URL": LOCAL_URL, "CHART_JS_INTEGRITY": integrity},
        prepare=add_january_trade,
    )
    yield url
    stop()


def open_dashboard(page_factory, real_server, query="", width=1440):
    page = page_factory(width, 900, chart_js="real")
    response = page.goto(real_server + "/analytics" + (f"?{query}" if query else ""))
    page.wait_for_function(
        "window.Chart && document.querySelectorAll('canvas[data-chart]').length === 7 && "
        "[...document.querySelectorAll('canvas[data-chart]')].every(c => window.Chart.getChart(c))"
    )
    return page, response


def payload(page):
    return json.loads(page.locator("#dashboard-data").inner_text())


def chart_eval(page, name, script):
    """Run `script` (a JS function body using `chart`) on one chart."""
    return page.evaluate(
        f"""(name) => {{ const chart = Chart.getChart(document.querySelector(`canvas[data-chart="${{name}}"]`));
                        {script} }}""",
        name,
    )


# ---------- The file itself ----------


def test_library_file_is_chart_js_4_5_1_with_its_licence():
    header = LOCAL_CHART_JS.read_text(encoding="utf-8", errors="replace")[:400]
    assert f"Chart.js v{EXPECTED_VERSION}" in header, header[:120]
    assert LICENCE.exists(), "LICENSE.md must sit next to the library file"
    assert "MIT" in LICENCE.read_text(encoding="utf-8")


def test_dashboard_loads_the_self_hosted_file_under_a_strict_policy(real_server, page_factory):
    page, response = open_dashboard(page_factory, real_server)
    csp = response.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "cdn.jsdelivr.net" not in csp
    tag = page.locator(f'script[src="{LOCAL_URL}"]')
    assert tag.get_attribute("integrity").startswith("sha384-")
    assert page.evaluate("Chart.version") == EXPECTED_VERSION
    assert page.problems == []  # no console errors, no CSP violations


# ---------- Drawing ----------


def test_all_seven_charts_draw(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    data = payload(page)
    expected_points = {
        "curve": len(data["curve"]),
        "drawdown": len(data["curve"]),
        "monthly": len(data["monthly"]),
        "weekday": len(data["weekday"]),
        "instruments": len(data["instruments"]),
        "strategies": len(data["strategies"]),
        "sessions": len(data["sessions"]),
    }
    for name in CHART_NAMES:
        info = chart_eval(
            page,
            name,
            """const c = chart.canvas, ctx = c.getContext('2d');
               const pixels = ctx.getImageData(0, 0, c.width, c.height).data;
               let painted = 0; for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 0) painted++;
               return {points: chart.data.datasets[0].data.length, painted, width: chart.width};""",
        )
        assert info["points"] == expected_points[name], name
        assert info["painted"] > 500, f"{name} painted only {info['painted']} pixels"
        assert info["width"] > 100, name
    assert page.problems == []


def test_chart_values_match_the_server_payload(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    data = payload(page)
    curve = chart_eval(page, "curve", "return chart.data.datasets[0].data.map(p => p.y);")
    assert curve == [float(p["cumulative_r"]) for p in data["curve"]]
    for name in ("monthly", "weekday", "instruments", "strategies", "sessions"):
        values = chart_eval(page, name, "return chart.data.datasets[0].data;")
        assert values == [None if r["total_r"] is None else float(r["total_r"]) for r in data[name]], name


def test_empty_month_is_a_gap_and_weekdays_run_monday_to_sunday(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    monthly = chart_eval(
        page, "monthly", "return {labels: chart.data.labels, data: chart.data.datasets[0].data};"
    )
    assert monthly["labels"] == ["Jan 2026", "Feb 2026", "Mar 2026"]
    assert monthly["data"][1] is None  # February: no closed trades, not 0
    weekday = chart_eval(page, "weekday", "return chart.data.labels;")
    assert weekday == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def test_signs_have_different_colours_and_the_maximum_drawdown_is_marked(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    bars = chart_eval(
        page,
        "strategies",
        "return {data: chart.data.datasets[0].data, colours: chart.data.datasets[0].backgroundColor};",
    )
    positive = {c for v, c in zip(bars["data"], bars["colours"], strict=True) if v is not None and v > 0}
    negative = {c for v, c in zip(bars["data"], bars["colours"], strict=True) if v is not None and v < 0}
    assert positive and negative and positive.isdisjoint(negative)
    drawdown = chart_eval(
        page,
        "drawdown",
        "const d = chart.data.datasets[0]; return {y: d.data.map(p => p.y), styles: d.pointStyle};",
    )
    assert all(y <= 0 for y in drawdown["y"])
    deepest = drawdown["y"].index(min(drawdown["y"]))
    assert drawdown["styles"][deepest] == "triangle"


# ---------- Tooltips agree with the tables ----------


def test_chart_js_tooltips_show_the_table_figures(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    for name in ("instruments", "strategies", "sessions"):
        rows = payload(page)[name]
        for index, row in enumerate(rows):
            lines = chart_eval(
                page,
                name,
                f"""chart.tooltip.setActiveElements([{{datasetIndex: 0, index: {index}}}], {{x: 0, y: 0}});
                    chart.update();
                    return chart.tooltip.body.flatMap(b => b.lines);""",
            )
            table_row = page.locator(f"#table-{name} tbody tr", has_text=row["label"]).first
            table_total = table_row.locator("td").nth(5).inner_text().strip()
            assert lines[0] == f"Total R: {table_total}", (name, row["label"])
    assert page.problems == []


# ---------- Filters, resizing ----------


def test_filters_change_the_charts(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server, "strategy=Breakout&session=LONDON")
    points = chart_eval(page, "curve", "return chart.data.datasets[0].data.length;")
    assert points == 4  # the start point plus sample trades 1, 3 and 10


def test_resizing_keeps_charts_inside_their_cards(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    for width in (1440, 768, 375):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(300)  # Chart.js resizes on its ResizeObserver callback
        assert horizontal_overflow(page) == 0, width
        for name in CHART_NAMES:
            fits = chart_eval(
                page,
                name,
                """const box = chart.canvas.closest('[data-chart-box]').getBoundingClientRect();
                   const c = chart.canvas.getBoundingClientRect();
                   return c.width > 0 && c.right <= box.right + 1 && c.left >= box.left - 1;""",
            )
            assert fits, (width, name)


# ---------- Optional real screenshots ----------


@pytest.mark.skipif(
    not os.environ.get("TRADELEDGER_SCREENSHOT_DIR"), reason="TRADELEDGER_SCREENSHOT_DIR not set"
)
def test_capture_real_chart_screenshots(real_server, page_factory):
    folder = Path(os.environ["TRADELEDGER_SCREENSHOT_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    for width, name in ((1440, "chartjs_real_desktop_1440.png"), (375, "chartjs_real_mobile_375.png")):
        page, _ = open_dashboard(page_factory, real_server, width=width)
        page.screenshot(path=str(folder / name), full_page=True)
        assert (folder / name).stat().st_size > 10_000
```

### `tests/test_database_integrity.py` (NEW)

**Why:** NEW: list/count/independent-filter agreement over 486 combinations; pagination; migration registry.  
**How to run or test:** Run: `python -m pytest tests/test_database_integrity.py`

```python
"""Phase 7: database consistency checks across many filter combinations.

The Phase 4-6 tests check individual filters and the API's paging. These
check the REPOSITORY layer as a whole on a larger, deterministic dataset:

  * list_trades() and count_trades() always agree, for every combination,
    and both agree with an independent filter written here in Python;
  * pagination (limit/offset) never skips or repeats a row, including rows
    that share the same entry time;
  * the migration registry is ordered and complete.

Every test uses its own temporary database (see conftest.py).
"""

import itertools
import random
from decimal import Decimal

import pytest

from tradeledger.migrations import LATEST_VERSION, MIGRATIONS, current_version
from tradeledger.repositories import trades as trade_repo
from tradeledger.seed import seed_sample_data

SESSIONS = [None, "ASIA", "LONDON", "NEW_YORK", "OTHER"]
STRATEGIES = [None, "Breakout", "Pullback", "Fade"]


@pytest.fixture
def busy_conn(conn):
    """Sample data plus 150 random trades. Fixed seed: same data every run.
    Ten trades share one entry time so ordering ties are exercised."""
    seed_sample_data(conn)
    rnd = random.Random(7)
    for n in range(150):
        closed = rnd.random() < 0.85
        day = 1 + n % 28
        entered = "2026-05-10T09:30:00Z" if n < 10 else f"2026-0{4 + n % 3}-{day:02d}T{8 + n % 9:02d}:00:00Z"
        exit_day = min(day + rnd.randint(0, 2), 28)
        trade_repo.create_trade(
            conn,
            {
                "instrument_id": rnd.randint(1, 5),
                "direction": "LONG",
                "status": "CLOSED" if closed else "OPEN",
                "trade_date": entered,
                "closed_at": (
                    None
                    if not closed
                    else ("2026-05-11T09:00:00Z" if n < 10 else f"{entered[:8]}{exit_day:02d}T18:00:00Z")
                ),
                "entry_price": "100",
                "stop_loss": "99",
                "exit_price": str(Decimal(rnd.randint(9800, 10200)) / 100) if closed else None,
                "position_size": "1",
                "strategy": rnd.choice(STRATEGIES),
                "session": rnd.choice(SESSIONS),
                "notes": rnd.choice([None, "gap fill", "News spike", "100% planned"]),
            },
        )
    return conn


def independent_filter(rows, *, instrument_id, status, strategy, session, start, end, date_field):
    """The same rules as the repository, written separately in Python."""
    out = []
    for t in rows:
        moment = t["trade_date"] if date_field == "entry" else (t["closed_at"] or t["trade_date"])
        day = moment[:10]
        if instrument_id is not None and t["instrument_id"] != instrument_id:
            continue
        if status is not None and t["status"] != status:
            continue
        if strategy is not None and t["strategy"] != strategy:
            continue
        if session is not None and t["session"] != session:
            continue
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        out.append(t["id"])
    return sorted(out)


def test_list_count_and_independent_filter_always_agree(busy_conn):
    everything = trade_repo.list_trades(busy_conn)
    assert len(everything) == 166  # 16 sample + 150 generated
    combos = itertools.product(
        [None, 1, 3],
        [None, "OPEN", "CLOSED"],
        [None, "Breakout", "Fade"],
        [None, "LONDON", "ASIA"],
        [(None, None), ("2026-05-01", "2026-05-31"), ("2026-05-10", "2026-05-10")],
        ["entry", "realised"],
    )
    checked = 0
    for instrument_id, status, strategy, session, (start, end), date_field in combos:
        kwargs = {
            "instrument_id": instrument_id,
            "status": status,
            "strategy": strategy,
            "session": session,
            "start": start,
            "end": end,
            "date_field": date_field,
        }
        listed = trade_repo.list_trades(busy_conn, **kwargs)
        counted = trade_repo.count_trades(busy_conn, **kwargs)
        expected = independent_filter(everything, **kwargs)
        assert counted == len(listed), kwargs
        assert sorted(t["id"] for t in listed) == expected, kwargs
        checked += 1
    assert checked == 3 * 3 * 3 * 3 * 3 * 2


@pytest.mark.parametrize("page_size", [1, 3, 7, 50, 166, 500])
def test_pages_cover_every_row_exactly_once(busy_conn, page_size):
    full = [t["id"] for t in trade_repo.list_trades(busy_conn)]
    paged, offset = [], 0
    while True:
        page = trade_repo.list_trades(busy_conn, limit=page_size, offset=offset)
        if not page:
            break
        assert len(page) <= page_size
        paged.extend(t["id"] for t in page)
        offset += page_size
    assert paged == full  # same rows, same order, no gaps, no repeats
    assert len(set(paged)) == len(paged)


def test_rows_with_equal_entry_times_have_a_stable_order(busy_conn):
    """Ten trades share 2026-05-10T09:30:00Z (no generated trade uses minute
    30). The documented order is newest entry first, then highest id first,
    so ties can never swap between pages."""
    tied = [t for t in trade_repo.list_trades(busy_conn) if t["trade_date"] == "2026-05-10T09:30:00Z"]
    assert len(tied) == 10
    assert [t["id"] for t in tied] == sorted((t["id"] for t in tied), reverse=True)
    first_half = trade_repo.list_trades(busy_conn, start="2026-05-10", end="2026-05-10", limit=5, offset=0)
    second_half = trade_repo.list_trades(busy_conn, start="2026-05-10", end="2026-05-10", limit=5, offset=5)
    assert {t["id"] for t in first_half}.isdisjoint({t["id"] for t in second_half})


def test_offset_past_the_end_and_zero_limit_return_nothing(busy_conn):
    assert trade_repo.list_trades(busy_conn, limit=10, offset=10_000) == []
    assert trade_repo.list_trades(busy_conn, limit=0) == []


def test_migration_registry_is_ordered_and_complete(conn):
    """Versions must run 2, 3, 4, ... with no gaps (version 1 is schema.sql),
    and a fresh database must end on the latest one."""
    versions = sorted(MIGRATIONS)
    assert versions == list(range(2, LATEST_VERSION + 1))
    assert current_version(conn) == LATEST_VERSION
    for version, script in MIGRATIONS.items():
        assert "PRAGMA user_version" not in script, version  # migrate() sets it, inside the transaction
        assert "BEGIN" not in script.upper().split("TRIGGER")[0], version
```

### `tests/test_error_paths.py` (NEW)

**Why:** NEW: error paths found by coverage (unknown DB errors re-raised, 413, CLI messages, bad data pages).  
**How to run or test:** Run: `python -m pytest tests/test_error_paths.py`

```python
"""Phase 7: error paths that coverage measurement showed were never run.

Each test protects a specific failure mode rather than chasing a number:
an error that is silently swallowed, a page that crashes on bad data, or a
command-line message that would mislead. See docs/PHASE_7_QA_REPORT.md
for the coverage baseline that identified them.
"""

import sqlite3

import pytest

from tests.web_helpers import count_rows
from tradeledger import create_app
from tradeledger.db import get_db
from tradeledger.repositories.trades import _raise_friendly
from tradeledger.validation import ValidationError, validate_trade
from tradeledger.web.filters import clock_filter, day_filter, when_filter

# ---------- Database errors are translated or re-raised, never swallowed ----------


def test_foreign_key_error_becomes_a_field_error():
    with pytest.raises(ValidationError) as info:
        _raise_friendly(sqlite3.IntegrityError("FOREIGN KEY constraint failed"))
    assert info.value.errors == {"instrument_id": "does not match an existing instrument"}


def test_closed_at_trigger_error_becomes_a_field_error():
    with pytest.raises(ValidationError) as info:
        _raise_friendly(sqlite3.IntegrityError("closed_at_rules: an OPEN trade cannot have closed_at"))
    assert set(info.value.errors) == {"closed_at"}


def test_unknown_database_errors_are_re_raised_unchanged():
    """An unexpected constraint failure must surface (and be logged as a
    500), not be disguised as a user mistake or ignored."""
    error = sqlite3.IntegrityError("CHECK constraint failed: something_new")
    with pytest.raises(sqlite3.IntegrityError) as info:
        _raise_friendly(error)
    assert info.value is error


# ---------- Validation messages for rarely used inputs ----------


def _valid_trade(**changes):
    data = {
        "instrument_id": 1,
        "direction": "LONG",
        "status": "CLOSED",
        "trade_date": "2026-04-01T09:00:00Z",
        "closed_at": "2026-04-01T10:00:00Z",
        "entry_price": "100",
        "stop_loss": "90",
        "exit_price": "110",
        "position_size": "1",
    }
    data.update(changes)
    return data


@pytest.mark.parametrize(
    "changes, field, message",
    [
        ({"take_profit": "abc"}, "take_profit", "must be a number"),
        ({"exit_price": "-5"}, "exit_price", "must be greater than zero"),
        ({"instrument_id": True}, "instrument_id", None),
        ({"instrument_id": 1.0}, "instrument_id", None),
        ({"notes": {"text": "hi"}}, "notes", "must be text"),
        ({"strategy": 42}, "strategy", "must be text"),
    ],
)
def test_unusual_values_get_a_field_error(changes, field, message):
    with pytest.raises(ValidationError) as info:
        validate_trade(_valid_trade(**changes))
    assert field in info.value.errors
    if message:
        assert info.value.errors[field] == message


# ---------- Pages keep working with data the engine cannot evaluate ----------


def test_trade_page_for_an_unevaluable_trade_shows_no_result(app, seeded_client):
    """A legacy row out of range (see test_security_hardening.py) must not
    break the trade's own page: it renders without a result card."""
    huge = "1" + "0" * 600_000
    with app.app_context():
        conn = get_db()
        conn.execute(
            "UPDATE trades SET entry_price = ?, position_size = ?, take_profit = NULL WHERE id = 1",
            (huge, huge),
        )
        conn.commit()
    response = seeded_client.get("/trades/1")
    assert response.status_code == 200
    assert "(after fees)" not in response.get_data(as_text=True)  # the result card is absent


def test_oversized_website_form_is_413_not_a_crash(seeded_client, app):
    before = count_rows(app, "trades")
    response = seeded_client.post("/trades/new", data={"notes": "x" * (70 * 1024)})
    assert response.status_code == 413
    assert b"Traceback" not in response.data
    assert count_rows(app, "trades") == before


# ---------- Template filters with unexpected stored values ----------


def test_date_filters_never_crash_on_bad_timestamps():
    assert when_filter("not-a-date") == "not-a-date"  # shown as stored, not hidden
    assert when_filter(None) == "n/a"
    assert day_filter("2026-13-45T00:00:00Z") == "n/a"
    assert clock_filter(None) == ""
    assert clock_filter("2026-03-02T08:15:30Z") == "08:15:30 UTC"
    assert clock_filter("2026-03-02T08:15:00Z") == "08:15 UTC"


# ---------- Configuration and command line ----------


def test_config_name_comes_from_the_environment(monkeypatch, db_path):
    monkeypatch.setenv("TRADELEDGER_CONFIG", "testing")
    app = create_app(overrides={"DATABASE": db_path})
    assert app.config["CONFIG_NAME"] == "testing" and app.config["TESTING"] is True


def test_run_sql_on_a_missing_database_explains_what_to_do(tmp_path):
    app = create_app("testing", overrides={"DATABASE": str(tmp_path / "missing.db")})
    result = app.test_cli_runner().invoke(args=["run-sql", "SELECT 1"])
    assert result.exit_code != 0
    assert "Database file not found. Run init-db first." in result.output


def test_run_sql_with_no_rows_says_so(runner):
    result = runner.invoke(args=["run-sql", "SELECT * FROM trades"])
    assert result.exit_code == 0
    assert "(no rows)" in result.output


def test_db_info_on_an_empty_file(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    app = create_app("testing", overrides={"DATABASE": str(empty)})
    result = app.test_cli_runner().invoke(args=["db-info"])
    assert "No tables yet. Run: flask --app tradeledger init-db" in result.output


def test_cli_report_lists_excluded_and_legacy_trades(app, runner):
    runner.invoke(args=["seed-db"])
    huge = "1" + "0" * 600_000
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 2")
        conn.execute(
            "UPDATE trades SET entry_price = ?, position_size = ?, take_profit = NULL WHERE id = 1",
            (huge, huge),
        )
        conn.commit()
    output = runner.invoke(args=["report"]).output
    assert "== Excluded trades ==" in output
    assert "#1: values are too large to calculate exactly" in output
    assert "== Closed trades with no recorded exit time (ordered by entry time) ==" in output
    assert "#2" in output.split("no recorded exit time")[1]
```

### `tests/test_financial_edge_cases.py` (NEW)

**Why:** NEW: hand-calculated boundary cases (tiny values, fractional multipliers, fees, single trades, drawdown recovery).  
**How to run or test:** Run: `python -m pytest tests/test_financial_edge_cases.py`

```python
"""Phase 7: financial boundary and regression cases.

Gaps found by reviewing the Phase 6 suite (which already covered long and
short trades, fees, breakeven, multipliers, zero risk, close order and
currencies): very small values, fractional multipliers, fees that turn a
SHORT winner into a loser, a loss beyond the stop, drawdown recovery
followed by a larger drawdown, and single-trade datasets.

Every expected value below was worked out by hand (shown in each test)
and compared as an exact fraction. None is produced by calling the code
under test.
"""

from decimal import Decimal
from fractions import Fraction

import pytest

from tests.factories import make_trade, r_trade
from tradeledger.performance.metrics import summarise
from tradeledger.performance.trade_math import evaluate_trade, planned_rr

D = Decimal


def exact(value):
    """A Decimal as an exact Fraction, for comparisons with hand results."""
    return Fraction(value)


def close_to(value, fraction):
    """True when a 60-significant-digit Decimal matches an exact fraction.
    A repeating decimal such as 1/3 cannot be stored exactly, so allow the
    last of 60 digits to differ."""
    return abs(exact(value) - fraction) < Fraction(1, 10**55)


# ---------- Single trades ----------


def test_very_small_prices_keep_full_precision():
    """Crypto-style prices with 8 decimal places and a large size.

    move     = 0.00001300 - 0.00001234 = 0.00000066
    gross    = 0.00000066 x 1,000,000 x 1 = 0.66
    net      = 0.66 - 0.0001 fees = 0.6599
    risk     = (0.00001234 - 0.00001200) x 1,000,000 x 1 = 0.34
    R        = 0.6599 / 0.34 = 6599/3400 (a repeating decimal)
    """
    result = evaluate_trade(
        make_trade(
            entry_price=D("0.00001234"),
            stop_loss=D("0.00001200"),
            exit_price=D("0.00001300"),
            position_size=D("1000000"),
            fees=D("0.0001"),
        )
    )
    assert result.gross_pnl == D("0.66")
    assert result.net_pnl == D("0.6599")
    assert result.initial_risk == D("0.34")
    assert close_to(result.realised_r, Fraction(6599, 3400))
    assert result.outcome == "WIN"


def test_high_precision_short_with_fractional_multiplier():
    """SHORT, 8-decimal prices, multiplier 0.1, size 3.

    move     = entry - exit = 1.23456789 - 1.23356789 = 0.001 (in our favour)
    gross    = 0.001 x 3 x 0.1 = 0.0003
    net      = 0.0003 - 0.0002 = 0.0001
    risk     = (stop - entry) x 3 x 0.1 = 0.001 x 0.3 = 0.0003
    R        = 0.0001 / 0.0003 = 1/3
    """
    result = evaluate_trade(
        make_trade(
            direction="SHORT",
            entry_price=D("1.23456789"),
            stop_loss=D("1.23556789"),
            exit_price=D("1.23356789"),
            position_size=D("3"),
            contract_multiplier=D("0.1"),
            fees=D("0.0002"),
        )
    )
    assert result.gross_pnl == D("0.0003")
    assert result.net_pnl == D("0.0001")
    assert result.initial_risk == D("0.0003")
    assert close_to(result.realised_r, Fraction(1, 3))
    assert result.outcome == "WIN"


def test_fees_turn_a_short_winner_into_a_loss():
    """Same SHORT, but fees 0.0004 exceed the 0.0003 gross gain.

    net = 0.0003 - 0.0004 = -0.0001   R = -0.0001 / 0.0003 = -1/3
    The price moved our way, yet the trade is a LOSS after costs.
    """
    result = evaluate_trade(
        make_trade(
            direction="SHORT",
            entry_price=D("1.23456789"),
            stop_loss=D("1.23556789"),
            exit_price=D("1.23356789"),
            position_size=D("3"),
            contract_multiplier=D("0.1"),
            fees=D("0.0004"),
        )
    )
    assert result.gross_pnl > 0
    assert result.net_pnl == D("-0.0001")
    assert close_to(result.realised_r, Fraction(-1, 3))
    assert result.outcome == "LOSS"


def test_short_exit_at_entry_without_fees_is_breakeven():
    result = evaluate_trade(
        make_trade(direction="SHORT", entry_price=D("50"), stop_loss=D("55"), exit_price=D("50"))
    )
    assert (result.net_pnl, result.realised_r, result.outcome) == (D("0"), D("0"), "BREAKEVEN")


def test_loss_beyond_the_stop_is_more_than_one_r():
    """Slippage: LONG entry 100, stop 90, but the exit was 85.
    net = -15, risk = 10, R = -1.5 (the stop is a plan, not a guarantee)."""
    result = evaluate_trade(make_trade(exit_price=D("85")))
    assert (result.net_pnl, result.realised_r, result.outcome) == (D("-15"), D("-1.5"), "LOSS")


@pytest.mark.parametrize(
    "direction, entry, stop, target, expected",
    [
        ("LONG", "100", "90", None, None),  # no target recorded
        ("LONG", "100", "90", "95", None),  # target on the losing side
        ("LONG", "100", "90", "100", None),  # target equal to entry: no reward
        ("SHORT", "100", "110", "120", None),  # short target above entry
        ("LONG", "100", "90", "125", Fraction(5, 2)),  # reward 25 / risk 10
        ("SHORT", "100", "104", "94", Fraction(3, 2)),  # reward 6 / risk 4
    ],
)
def test_planned_reward_to_risk(direction, entry, stop, target, expected):
    value = planned_rr(direction, D(entry), D(stop), None if target is None else D(target))
    assert (None if value is None else exact(value)) == expected


# ---------- Single-trade datasets ----------


def test_single_winning_trade_summary():
    """One +2R trade: no losses, so profit factor is undefined (None), not
    infinite, and there is no drawdown."""
    s = summarise([evaluate_trade(r_trade(2, id=1))])
    assert (s.trades, s.wins, s.losses, s.breakevens) == (1, 1, 0, 0)
    assert (s.win_rate, s.total_r, s.average_r, s.expectancy_r) == (D("1"), D("2"), D("2"), D("2"))
    assert s.profit_factor_r is None
    assert (s.longest_win_streak, s.longest_loss_streak) == (1, 0)
    assert s.max_drawdown_r.amount == 0
    assert s.max_drawdown_r.trough_trade_id is None


def test_single_losing_trade_summary():
    """One -1R trade: gains 0 / losses 1 = profit factor 0. The drawdown is
    measured from the 0R starting point (peak None) down to this trade."""
    s = summarise([evaluate_trade(r_trade(-1, id=7))])
    assert (s.wins, s.losses, s.win_rate, s.total_r) == (0, 1, D("0"), D("-1"))
    assert s.profit_factor_r == 0
    assert (s.longest_win_streak, s.longest_loss_streak) == (0, 1)
    assert s.max_drawdown_r.amount == D("1")
    assert (s.max_drawdown_r.peak_trade_id, s.max_drawdown_r.trough_trade_id) == (None, 7)


def test_single_breakeven_trade_summary():
    s = summarise([evaluate_trade(r_trade(0, id=3))])
    assert (s.wins, s.losses, s.breakevens, s.win_rate, s.total_r) == (0, 0, 1, D("0"), D("0"))
    assert s.profit_factor_r is None
    assert (s.longest_win_streak, s.longest_loss_streak) == (0, 0)
    assert s.max_drawdown_r.amount == 0


# ---------- Drawdown recovery ----------


def _series(rs):
    """Trades with ids 1..n closing on consecutive days, with the given R."""
    return [
        evaluate_trade(
            r_trade(
                r,
                id=i,
                closed_at=f"2026-02-{i + 1:02d}T12:00:00Z",
                trade_date=f"2026-02-{i + 1:02d}T09:00:00Z",
            )
        )
        for i, r in enumerate(rs, start=1)
    ]


def test_drawdown_recovers_and_a_smaller_one_does_not_replace_it():
    """R: +1, -2, +3, -1
    cumulative 1, -1, 2, 1     running peak 1, 1, 2, 2
    drawdown   0,  2, 0, 1     -> maximum 2, from trade 1 (peak) to trade 2.
    The full recovery at trade 3 and the later dip of 1 do not change it."""
    s = summarise(_series(["1", "-2", "3", "-1"]))
    assert s.total_r == D("1")
    dd = s.max_drawdown_r
    assert (dd.amount, dd.peak_trade_id, dd.trough_trade_id) == (D("2"), 1, 2)


def test_larger_drawdown_after_recovery_replaces_the_first():
    """R: +1, -1, +5, -4
    cumulative 1, 0, 5, 1      running peak 1, 1, 5, 5
    drawdown   0, 1, 0, 4      -> maximum 4, from trade 3 (new peak) to trade 4."""
    dd = summarise(_series(["1", "-1", "5", "-4"])).max_drawdown_r
    assert (dd.amount, dd.peak_trade_id, dd.trough_trade_id) == (D("4"), 3, 4)


def test_streaks_across_a_recovery():
    """W W L L L W -> longest win streak 2, longest loss streak 3."""
    s = summarise(_series(["1", "1", "-1", "-1", "-1", "2"]))
    assert (s.longest_win_streak, s.longest_loss_streak) == (2, 3)
```

### `tests/test_security_hardening.py` (NEW)

**Why:** NEW: regression tests for security findings F1-F6.  
**How to run or test:** Run: `python -m pytest tests/test_security_hardening.py`

```python
"""Phase 7: security fixes found in the Phase 7 security review.

Each block protects one finding (see docs/PHASE_7_SECURITY_REVIEW.md):

  F1  Unbounded decimal magnitude ("1e999999") was accepted, stored as a
      one-million-digit string, and crashed every page that evaluated it.
  F2  No Host header allow-list: DNS rebinding could reach the
      unauthenticated local journal and API through the user's browser.
  F3  Production accepted a short or published placeholder SECRET_KEY.
  F4  HTML pages full of trading data carried no Cache-Control header.
  F5  HSTS was not sent in production.
  F6  The order of the website's request hooks depended on import order.
"""

import pytest

from tests.web_helpers import closed_trade_form, count_rows
from tradeledger import create_app
from tradeledger.config import env_hosts
from tradeledger.db import get_db, init_schema
from tradeledger.performance import build_report
from tradeledger.seed import seed_sample_data
from tradeledger.validation import parse_decimal
from tradeledger.web.csrf import check_csrf_token
from tradeledger.web.guard import refuse_when_disabled

STRONG_KEY = "c0ffee" * 11  # 66 characters

# ---------- F1: decimal magnitude ----------


@pytest.mark.parametrize(
    "text, message",
    [
        ("1e999999", "must have at most 15 digits before the decimal point"),
        ("1" + "0" * 15, "must have at most 15 digits before the decimal point"),  # 16 digits
        ("1e-999999", "must have at most 15 decimal places"),
        ("0." + "0" * 15 + "1", "must have at most 15 decimal places"),  # 16 places
    ],
)
def test_out_of_range_decimals_are_rejected(text, message):
    with pytest.raises(ValueError, match=message):
        parse_decimal(text)


@pytest.mark.parametrize("text", ["9" * 15, "0." + "0" * 14 + "1", "123456.123456789", "1E+5", "0.00001234"])
def test_values_at_the_limits_are_accepted(text):
    assert parse_decimal(text) > 0


def test_api_rejects_a_huge_exponent_without_storing_it(seeded_client, app, trade_body):
    before = count_rows(app, "trades")
    response = seeded_client.post("/api/v1/trades", json={**trade_body, "entry_price": "1e999999"})
    assert response.status_code == 400
    assert response.get_json()["error"]["details"]["entry_price"] == (
        "must have at most 15 digits before the decimal point"
    )
    assert count_rows(app, "trades") == before


def test_website_form_rejects_a_huge_exponent(seeded_client, app):
    page = seeded_client.get("/trades/new").get_data(as_text=True)
    token = page.split('name="csrf_token" value="')[1].split('"')[0]
    before = count_rows(app, "trades")
    form = {**closed_trade_form(), "position_size": "1e-999999", "csrf_token": token}
    response = seeded_client.post("/trades/new", data=form)
    assert response.status_code == 400
    assert "must have at most 15 decimal places" in response.get_data(as_text=True)
    assert count_rows(app, "trades") == before


def test_instrument_with_huge_multiplier_is_rejected(seeded_client):
    body = {
        "symbol": "HUGE",
        "name": "Huge",
        "asset_class": "OTHER",
        "price_increment": "0.01",
        "contract_multiplier": "1e999999",
        "quote_currency": "USD",
    }
    response = seeded_client.post("/api/v1/instruments", json=body)
    assert response.status_code == 400
    assert "contract_multiplier" in response.get_json()["error"]["details"]


def test_an_out_of_range_row_already_in_the_database_cannot_break_pages(app, seeded_client):
    """Defence in depth. Validation now blocks such values, but a database
    written before Phase 7 (or edited by hand) might contain one. The
    engine reports it as an excluded trade instead of raising Overflow."""
    huge = "1" + "0" * 600_000  # 10^600000: products overflow Decimal's exponent limit
    with app.app_context():
        conn = get_db()
        # take_profit is cleared too: the schema's own CHECK would otherwise
        # (correctly) refuse a target below such a huge entry price.
        conn.execute(
            "UPDATE trades SET entry_price = ?, position_size = ?, take_profit = NULL WHERE id = 1",
            (huge, huge),
        )
        conn.commit()
        report = build_report(conn)
    assert [(e.trade_id, e.reason) for e in report.excluded] == [
        (1, "values are too large to calculate exactly")
    ]
    for url in ("/analytics", "/", "/trades", "/trades/1", "/api/v1/trades", "/api/v1/analytics/summary"):
        assert seeded_client.get(url).status_code == 200, url
    assert seeded_client.get("/api/v1/trades/1").get_json()["data"]["result"] is None


# ---------- F2: Host header allow-list ----------


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "localhost:5000", "127.0.0.1:8080"])
def test_local_host_names_are_accepted(client, host):
    assert client.get("/", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("path", ["/", "/trades", "/api/v1/trades"])
def test_rebound_host_name_is_refused(seeded_client, path):
    """DNS rebinding: an attacker's domain resolves to 127.0.0.1, so the
    browser sends Host: attacker.example. The app must not answer."""
    response = seeded_client.get(path, headers={"Host": "attacker.example"})
    assert response.status_code == 400
    assert b"EURUSD" not in response.data


def test_rebound_host_cannot_change_data(seeded_client, app):
    before = count_rows(app, "trades")
    response = seeded_client.delete("/api/v1/trades/1", headers={"Host": "attacker.example:5000"})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "http_error"
    assert count_rows(app, "trades") == before


def test_env_hosts_parsing(monkeypatch):
    monkeypatch.delenv("TL_TEST_HOSTS", raising=False)
    assert env_hosts("TL_TEST_HOSTS", ["localhost"]) == ["localhost"]
    monkeypatch.setenv("TL_TEST_HOSTS", " Journal.Example.com , 192.168.1.20 ,, ")
    assert env_hosts("TL_TEST_HOSTS", None) == ["journal.example.com", "192.168.1.20"]
    monkeypatch.setenv("TL_TEST_HOSTS", "*")
    assert env_hosts("TL_TEST_HOSTS", ["localhost"]) is None


def test_extra_trusted_host_from_configuration(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path, "TRUSTED_HOSTS": ["192.168.1.20"]})
    with app.app_context():
        init_schema(get_db())
    client = app.test_client()
    assert client.get("/", headers={"Host": "192.168.1.20:5000"}).status_code == 200
    assert client.get("/", headers={"Host": "localhost"}).status_code == 400


# ---------- F3: production SECRET_KEY strength ----------


@pytest.mark.parametrize(
    "key",
    [
        "short",
        "x" * 31,
        "replace-me-with-a-long-random-string",  # the .env.example placeholder (36 characters)
        "test-secret-key-not-for-real-use",  # the public testing key (32 characters)
    ],
)
def test_production_refuses_weak_secret_keys(db_path, key):
    with pytest.raises(RuntimeError, match="SECRET_KEY is too weak"):
        create_app("production", overrides={"SECRET_KEY": key, "DATABASE": db_path})


def test_production_accepts_a_strong_key(db_path):
    app = create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path})
    assert app.config["SECRET_KEY"] == STRONG_KEY


def test_weak_key_error_never_contains_the_key(db_path):
    with pytest.raises(RuntimeError) as info:
        create_app("production", overrides={"SECRET_KEY": "my-short-secret", "DATABASE": db_path})
    assert "my-short-secret" not in str(info.value)


def test_development_still_accepts_a_short_key(db_path):
    app = create_app("development", overrides={"SECRET_KEY": "dev", "DATABASE": db_path})
    assert app.config["SECRET_KEY"] == "dev"


# ---------- F4/F5: caching and HSTS headers ----------


@pytest.mark.parametrize("url", ["/", "/trades", "/trades/1", "/analytics", "/about", "/no-such-page"])
def test_html_pages_are_not_stored_by_caches(seeded_client, url):
    assert seeded_client.get(url).headers["Cache-Control"] == "no-store"


def test_static_files_stay_cacheable(client):
    with client.get("/static/css/style.css") as response:
        assert "no-store" not in response.headers.get("Cache-Control", "")


def test_hsts_only_in_production(db_path, client):
    assert "Strict-Transport-Security" not in client.get("/health").headers
    app = create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path})
    response = app.test_client().get("/health")
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"


# ---------- F6: request hook order ----------


def test_guard_runs_before_csrf_check(app):
    """Registered explicitly in web/__init__.py, not by import order."""
    hooks = app.before_request_funcs["main"]
    assert hooks.index(refuse_when_disabled) < hooks.index(check_csrf_token)


def test_production_post_without_token_is_403_not_400(db_path):
    app = create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path})
    with app.app_context():
        init_schema(get_db())
        seed_sample_data(get_db())
    response = app.test_client().post("/trades/1/delete", data={})
    assert response.status_code == 403
```

### `tests/test_timestamps.py` (NEW)

**Why:** NEW: proves parse_stored == strptime; guards against strptime returning.  
**How to run or test:** Run: `python -m pytest tests/test_timestamps.py`

```python
"""Phase 7: the fast stored-timestamp parser (tradeledger/timestamps.py).

The performance fix replaced datetime.strptime with a strict pattern check
plus datetime.fromisoformat. These tests prove the two give the SAME
answer, value for value, on valid and invalid input, so the speed-up
cannot change any financial or date result.
"""

import random
from datetime import datetime

import pytest

from tradeledger.performance.dashboard import build_dashboard
from tradeledger.report_filters import ReportFilters
from tradeledger.timestamps import STORED_FORMAT, parse_stored


def reference(text):
    """The Phase 6 behaviour: what strptime returns, or the exception type."""
    try:
        return datetime.strptime(text, STORED_FORMAT)
    except (TypeError, ValueError) as exc:
        return type(exc)


def fast(text):
    try:
        return parse_stored(text)
    except (TypeError, ValueError) as exc:
        return type(exc)


@pytest.mark.parametrize(
    "text",
    [
        "2026-03-02T08:15:00Z",
        "2024-02-29T23:59:59Z",  # leap day
        "2000-01-01T00:00:00Z",
        "9999-12-31T23:59:59Z",
        "2025-02-29T10:00:00Z",  # not a leap year
        "2026-13-01T10:00:00Z",  # month 13
        "2026-04-31T10:00:00Z",  # 31 April
        "2026-01-01T24:00:00Z",  # hour 24
        "2026-01-01T10:60:00Z",  # minute 60
        "2026-01-01T10:00:60Z",  # second 60
        "0000-01-01T00:00:00Z",  # year 0
    ],
)
def test_same_result_as_strptime_for_well_shaped_text(text):
    assert fast(text) == reference(text)


@pytest.mark.parametrize(
    "text",
    [
        "2026-03-02 08:15:00Z",  # space, not T
        "2026-03-02T08:15:00",  # no Z
        "2026-03-02T08:15:00+00:00",  # offset instead of Z
        "2026-03-02",  # date only
        "",
        "Z",
        " 2026-03-02T08:15:00Z",  # leading space
        "٢٠٢٦-03-02T08:15:00Z",  # non-ASCII digits
        "2026-03-02T08:15:00Z0",  # trailing text that would survive dropping the last character
        "2026-03-02T08:15:00ZZ",
    ],
)
def test_text_outside_the_stored_shape_is_rejected(text):
    """The database CHECK constraints guarantee the stored shape, so the
    parser may be stricter than strptime here; it must never be looser."""
    assert fast(text) is ValueError


@pytest.mark.parametrize("value", [None, 20260302, b"2026-03-02T08:15:00Z"])
def test_non_text_raises_type_error(value):
    assert fast(value) is TypeError


def test_random_valid_timestamps_match_strptime():
    """5,000 random real moments (fixed seed): identical datetimes."""
    rnd = random.Random(11)
    for _ in range(5000):
        moment = datetime(
            rnd.randint(1900, 2100),
            rnd.randint(1, 12),
            rnd.randint(1, 28),
            rnd.randint(0, 23),
            rnd.randint(0, 59),
            rnd.randint(0, 59),
        )
        text = moment.strftime(STORED_FORMAT)
        assert parse_stored(text) == datetime.strptime(text, STORED_FORMAT) == moment


def test_the_dashboard_no_longer_calls_strptime(seeded_conn, monkeypatch):
    """Regression guard for the Phase 7 performance fix. datetime.strptime
    always delegates to the standard library's _strptime module, so counting
    calls there catches strptime used ANYWHERE while the dashboard is built."""
    import _strptime

    calls = []
    real = _strptime._strptime_datetime

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(_strptime, "_strptime_datetime", counting)
    datetime.strptime("2026-01-01T00:00:00Z", STORED_FORMAT)  # proves the counter works
    assert len(calls) == 1
    board = build_dashboard(seeded_conn, ReportFilters())
    assert board.report.overall.trades == 14
    assert len(calls) == 1  # nothing more while building the dashboard
```

### `.github/ISSUE_TEMPLATE/bug_report.md` (NEW)

**Why:** Bug report form (asks for synthetic data only).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```markdown
---
name: Bug report
about: Something TradeLedger does wrong (not for security problems: see SECURITY.md)
labels: bug
---

**What happened**

**What you expected**

**Steps to reproduce**
1.
2.

**Version and environment**
- TradeLedger version (shown at `/health`):
- Python version:
- Operating system and browser:

**Synthetic data only.** Please do not paste real trades: describe them, or reproduce the problem with the sample data (`flask --app tradeledger seed-db`).
```

### `.github/pull_request_template.md` (NEW)

**Why:** Checklist shown on every pull request.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```markdown
## What changed

<!-- One or two sentences. Link the issue if there is one. -->

## Why

<!-- The problem this solves, or the requirement it meets. -->

## How it was tested

- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] `python -m pytest` passes (new behaviour has a test that failed before the change)
- [ ] `node --test tests/js/*.test.js` passes (if JavaScript changed)
- [ ] Browser tests run, or the reason they were not is stated here
- [ ] Any Chart.js browser test is labelled `chartjs_stub`, `chartjs_offline` or `chartjs_real`

## Checks for this kind of change

- [ ] Money and R stay `Decimal` (no floats); expected test values are worked out by hand
- [ ] SQL values are bound parameters
- [ ] No test was weakened or removed to make the suite pass (if an expectation changed, it is explained above)
- [ ] `CHANGELOG.md` updated under "Unreleased" (if a user would notice)
- [ ] No secrets, `.env`, databases or real trading data are included
```

### `.github/workflows/ci.yml` (NEW)

**Why:** GitHub Actions CI: lint, format, tests + coverage (3.11, 3.13), JavaScript, pip-audit, browser (stub / offline / real Chart.js).  
**How to run or test:** Runs on GitHub on every push and pull request. Local rehearsal: see docs/PHASE_7_QA_REPORT.md section 2.5.

```yaml
# TradeLedger continuous integration (Phase 7).
#
# Runs on every push, every pull request, and on demand (Actions tab ->
# CI -> "Run workflow"). It only TESTS the code: nothing is deployed.
#
# Jobs (they run in parallel):
#   python      lint, formatting, the non-browser test suite and coverage,
#               on the oldest and newest supported Python versions
#   javascript  the Node.js unit tests for static/js/analytics.js
#   audit       known-vulnerability check of the pinned Python dependencies
#   browser     Playwright + Chromium, in three clearly separated steps:
#                 1. page tests with Chart.js REPLACED by a stand-in (stub)
#                 2. the offline fallback (Chart.js blocked)
#                 3. REAL Chart.js 4.5.1 rendering (library fetched from npm
#                    into this CI workspace only; never committed)

name: CI

on:
  push:
  pull_request:
  workflow_dispatch:

# Least privilege: the workflow only needs to read the repository.
permissions:
  contents: read

# A newer push to the same branch cancels the older, now-irrelevant run.
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  PIP_DISABLE_PIP_VERSION_CHECK: "1"
  PYTHONDONTWRITEBYTECODE: "1"
  FORCE_COLOR: "1"

jobs:
  python:
    name: Python ${{ matrix.python-version }} · lint, tests, coverage
    runs-on: ubuntu-latest
    timeout-minutes: 15
    strategy:
      fail-fast: false
      matrix:
        # README promises Python 3.11 or newer; test the oldest and the newest used.
        python-version: ["3.11", "3.13"]
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies
        run: python -m pip install -r requirements-dev.txt -c constraints.txt

      - name: Lint (ruff check)
        run: ruff check --output-format=github .

      - name: Formatting (ruff format --check)
        run: ruff format --check .

      - name: Tests and coverage (everything except real-browser tests)
        # Coverage settings, including the minimum (fail_under), are in pyproject.toml.
        run: >
          python -m pytest -m "not browser"
          --cov=tradeledger --cov-report=term-missing
          --cov-report=xml --cov-report=html

      - name: Upload the coverage report
        if: always() && matrix.python-version == '3.13'
        uses: actions/upload-artifact@v6
        with:
          name: coverage-report
          path: |
            htmlcov/
            coverage.xml
          retention-days: 14
          if-no-files-found: ignore

  javascript:
    name: JavaScript unit tests (Node.js)
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Node.js 22
        uses: actions/setup-node@v6
        with:
          node-version: "22"

      # Node's built-in test runner: no npm packages are installed.
      - name: Run the tests
        run: node --test tests/js/*.test.js

  audit:
    name: Dependency vulnerability audit
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies
        run: python -m pip install -r requirements-dev.txt -c constraints.txt

      # Checks every installed package against the Python Packaging Advisory
      # Database and OSV. Fails the job if a known vulnerability is found.
      - name: pip-audit
        run: pip-audit --strict --progress-spinner off

  browser:
    name: Browser tests (Playwright + Chromium)
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies (with Playwright)
        run: python -m pip install -r requirements-browser.txt -c constraints.txt

      - name: Install Chromium and its system libraries
        run: python -m playwright install --with-deps chromium

      - name: "1/3 Page tests, Chart.js replaced by a stand-in (stub: NOT Chart.js rendering)"
        run: python -m pytest -m "browser and not chartjs_offline and not chartjs_real" -ra

      - name: "2/3 Offline fallback (Chart.js blocked)"
        run: python -m pytest -m chartjs_offline -ra

      - name: Set up Node.js (for npm pack)
        uses: actions/setup-node@v6
        with:
          node-version: "22"

      # npm verifies the package against the integrity hash published in the
      # registry. The files are copied into this temporary CI workspace only.
      - name: Fetch Chart.js 4.5.1 from npm (CI workspace only)
        run: |
          npm pack chart.js@4.5.1 --silent
          tar -xzf chart.js-4.5.1.tgz
          cp package/dist/chart.umd.min.js package/LICENSE.md tradeledger/static/vendor/chartjs/
          head -c 200 tradeledger/static/vendor/chartjs/chart.umd.min.js; echo
          sha384sum tradeledger/static/vendor/chartjs/chart.umd.min.js

      - name: "3/3 REAL Chart.js rendering"
        env:
          # A missing library file fails the job instead of being skipped.
          TRADELEDGER_REQUIRE_REAL_CHARTJS: "1"
          TRADELEDGER_SCREENSHOT_DIR: artifacts/screenshots
        run: python -m pytest -m chartjs_real -ra

      - name: Upload real-chart screenshots
        if: always()
        uses: actions/upload-artifact@v6
        with:
          name: chartjs-real-screenshots
          path: artifacts/screenshots/
          retention-days: 14
          if-no-files-found: ignore
```

### `constraints.txt` (NEW)

**Why:** Exact versions of indirect dependencies (`pip install -c constraints.txt`).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```text
# Exact versions of the packages that TradeLedger's own dependencies pull in.
# Use with -c so pip installs these versions without making them direct
# dependencies:   pip install -r requirements-dev.txt -c constraints.txt
#
# These are the versions every Phase 7 test run used (Python 3.13). Update
# them deliberately (see docs/PHASE_7_RELEASE_CHECKLIST.md), never silently.
# Not listed: the dependencies of pytest-cov, coverage and pip-audit, which
# could not be installed in the Phase 7 build environment (no network).

# Flask
Werkzeug==3.1.9
Jinja2==3.1.6
MarkupSafe==3.0.3
itsdangerous==2.2.0
click==8.5.0
blinker==1.9.0

# pytest
iniconfig==2.3.0
packaging==26.3
pluggy==1.6.0
Pygments==2.21.0

# jsonschema
attrs==26.1.0
jsonschema-specifications==2025.9.1
referencing==0.37.0
rpds-py==2026.6.3
typing_extensions==4.16.0

# playwright
greenlet==3.5.6
pyee==13.0.1
```

### `pyproject.toml` (NEW)

**Why:** pytest (markers, warnings as errors), coverage and Ruff configuration. Replaces pytest.ini.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```toml
# Tool configuration for TradeLedger (Phase 7).
#
# TradeLedger is an application, not a library published to PyPI, so this
# file holds tool settings only: no build system and no package metadata.
# Dependencies stay in requirements*.txt (see README "Setup").

# ---------------------------------------------------------------- pytest
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-ra --strict-markers"
markers = [
    "browser: drives a real Chromium browser through Playwright (skipped if Playwright is missing)",
    "chartjs_stub: browser test that replaces Chart.js with a recording stand-in (tests OUR code, not Chart.js drawing)",
    "chartjs_offline: browser test that blocks Chart.js to check the text fallback",
    "chartjs_real: browser test that needs the REAL Chart.js file (skipped unless it is installed locally)",
]
# Turn every warning into a failure, so problems such as unclosed files or
# deprecated calls cannot pile up unnoticed. Narrow exceptions go below,
# each with a reason.
filterwarnings = [
    "error",
]

# ---------------------------------------------------------------- coverage
[tool.coverage.run]
source = ["tradeledger"]
branch = true
# The browser tests run the app in a background thread.
concurrency = ["thread"]

[tool.coverage.report]
show_missing = true
skip_covered = false
precision = 1
# CI fails below this. Measured baseline: see docs/PHASE_7_QA_REPORT.md.
fail_under = 90
exclude_also = [
    # Code that only exists to help a type checker.
    "if TYPE_CHECKING:",
]

[tool.coverage.html]
directory = "htmlcov"

# ---------------------------------------------------------------- ruff
[tool.ruff]
target-version = "py311"
line-length = 110
# docs/ holds historical code listings (PHASE_*_CODE.md) that must stay exactly
# as delivered; Ruff would otherwise reformat Python blocks inside Markdown.
extend-exclude = ["htmlcov", "instance", "docs"]

[tool.ruff.lint]
select = [
    "E", "W",   # pycodestyle errors and warnings
    "F",        # pyflakes: unused imports/variables, undefined names
    "I",        # import order
    "B",        # flake8-bugbear: likely bugs
    "UP",       # pyupgrade: modern syntax for Python 3.11+
    "S",        # flake8-bandit: security checks (Bandit itself is not a dependency)
    "BLE",      # blind `except Exception`
    "SIM",      # simplifications
    "C4",       # comprehensions
    "PIE",      # misc. unnecessary code
    "RUF",      # Ruff's own checks
]
ignore = [
    "E501",     # line length is the formatter's job; long URLs and SQL stay readable
    "RUF001", "RUF002", "RUF003",  # typographic characters (–, …, ×) are intended British UI text
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = [
    "S101",     # pytest uses plain assert
    "S105", "S106", "S107",  # fixed fake secrets in tests
    "S108",     # temporary paths in tests
    "S311",     # random test data, not cryptography
    "S603", "S607",  # tests run node with a fixed argument list
    "S608",     # test SQL interpolates fixed table names only (reviewed in Phase 7)
]
# Browser test modules import shared pytest fixtures from tests/browser_support.py
# by name; pytest then passes them in as arguments, which Ruff reports as F811.
"tests/test_browser*.py" = ["F811"]
"tests/test_chartjs_real.py" = ["F811"]
"scripts/**" = [
    "S311",     # synthetic data, not cryptography
    "S603", "S607",  # the audit runs node with a fixed argument list
]

[tool.ruff.format]
quote-style = "double"
```

### `requirements-browser.txt` (NEW)

**Why:** Adds Playwright for the optional browser tests.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```text
# Optional real-browser tests (tests/test_browser.py).
#   pip install -r requirements-browser.txt -c constraints.txt
#   python -m playwright install chromium
-r requirements-dev.txt
playwright==1.63.0
```

### `requirements-dev.txt` (NEW)

**Why:** Test, coverage, lint and audit tools, pinned.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```text
# Development, testing and quality tools (Phase 7).
#   pip install -r requirements-dev.txt -c constraints.txt
-r requirements.txt

# Tests
pytest==9.1.1
jsonschema==4.26.0          # validates API responses against the OpenAPI schemas

# Coverage (pytest-cov 7.x needs coverage >= 7.10.6)
pytest-cov==7.1.0
coverage==7.17.0

# Lint and format
ruff==0.16.9

# Known-vulnerability check for the pinned dependencies
pip-audit==2.10.0
```

## 2. Modified files: behaviour, configuration or tests changed

### `tradeledger/__init__.py` (MODIFIED)

**Why:** Strong production SECRET_KEY rule (F3); Cache-Control no-store on HTML (F4); HSTS in production (F5).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""TradeLedger application package.

Contains the application factory, create_app(). Flask's command-line tool
finds this function automatically when you run:  flask --app tradeledger run
"""

import logging
import os
import secrets

from flask import Flask, g, render_template
from werkzeug.exceptions import HTTPException

from .config import CONFIG_BY_NAME, MIN_PRODUCTION_SECRET_KEY_LENGTH, PLACEHOLDER_SECRET_KEYS


def create_app(config_name=None, overrides=None):
    """Build and return a configured Flask application.

    config_name: "development", "testing" or "production". If omitted, it is
    read from the TRADELEDGER_CONFIG environment variable, defaulting to
    "development".

    overrides: optional dict of settings applied last. Tests use this to
    point the app at a temporary database, e.g. {"DATABASE": "/tmp/x.db"}.
    """
    if config_name is None:
        config_name = os.environ.get("TRADELEDGER_CONFIG", "development")

    if config_name not in CONFIG_BY_NAME:
        valid = ", ".join(sorted(CONFIG_BY_NAME))
        raise ValueError(f"Unknown config '{config_name}'. Choose one of: {valid}")

    app = Flask(__name__)
    app.config.from_object(CONFIG_BY_NAME[config_name])
    if overrides:
        app.config.update(overrides)
    app.config["CONFIG_NAME"] = config_name

    _configure_logging(app)
    _ensure_secret_key(app)
    _warn_if_api_exposed(app)
    _configure_database(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_security_headers(app)

    app.logger.info("TradeLedger started with '%s' configuration", config_name)
    return app


def _configure_logging(app):
    """Send INFO-level messages and above to the terminal."""
    app.logger.setLevel(logging.INFO)


def _ensure_secret_key(app):
    """Make sure a SECRET_KEY exists, or fail loudly where one is required.

    Where a key is required (production), it must also be strong: at least
    32 characters and not one of the published placeholder values. The key
    signs the session cookie that holds the CSRF token, so a guessable key
    would let an attacker forge that cookie.
    """
    key = app.config.get("SECRET_KEY")
    weak = key in PLACEHOLDER_SECRET_KEYS or len(key or "") < MIN_PRODUCTION_SECRET_KEY_LENGTH
    if key and app.config["REQUIRE_SECRET_KEY"] and weak:
        raise RuntimeError(
            "SECRET_KEY is too weak for this environment. Use at least "
            f"{MIN_PRODUCTION_SECRET_KEY_LENGTH} random characters, for example the output of: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if key:
        return

    if app.config["REQUIRE_SECRET_KEY"]:
        raise RuntimeError(
            "SECRET_KEY is not set. Set the SECRET_KEY environment variable "
            "before running TradeLedger in this environment."
        )

    # Development only: a random key that changes on every restart.
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.logger.warning(
        "SECRET_KEY not set - using a temporary development key. "
        "Set SECRET_KEY in your .env file to silence this warning."
    )


def _warn_if_api_exposed(app):
    """Make an unauthenticated API in production impossible to miss."""
    if app.config["CONFIG_NAME"] == "production" and app.config["API_ENABLED"]:
        app.logger.warning(
            "The unauthenticated JSON API is ENABLED in production. Anyone who can reach "
            "this server can read and change trading data. Only do this behind your own "
            "access control."
        )


def _configure_database(app):
    """Decide where the SQLite file lives and register database hooks."""
    if not app.config.get("DATABASE"):
        if app.config["TESTING"]:
            raise RuntimeError(
                "The testing configuration needs an explicit DATABASE path "
                "so tests never touch development data."
            )
        os.makedirs(app.instance_path, exist_ok=True)
        app.config["DATABASE"] = os.path.join(app.instance_path, "tradeledger.db")

    from . import cli, db

    db.init_app(app)
    cli.init_app(app)


def _register_blueprints(app):
    """Attach groups of routes to the app: the website and the JSON API."""
    from .api import api_bp
    from .web import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)


def _register_error_handlers(app):
    """Friendly errors instead of Flask's defaults or stack traces.

    Website URLs get HTML pages (Phase 1). URLs under /api/ get the JSON
    error format, including for errors raised before any API code runs:
    unknown URL (404), wrong method (405) and oversized body (413).
    """
    from .api.errors import HTTP_ERRORS, error_response, is_api_request

    @app.errorhandler(404)
    def not_found(error):
        if is_api_request():
            return error_response(404, *HTTP_ERRORS[404])
        return render_template("errors/404.html"), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        if is_api_request():
            response, status = error_response(405, *HTTP_ERRORS[405])
            response.headers["Allow"] = ", ".join(error.valid_methods or [])
            return response, status
        return error  # website: Flask's standard 405 response

    @app.errorhandler(413)
    def too_large(error):
        if is_api_request():
            return error_response(413, *HTTP_ERRORS[413])
        return error

    @app.errorhandler(500)
    def internal_error(error):
        # The full traceback goes to the server log only, never to the client.
        app.logger.exception("Unhandled server error")
        if is_api_request():
            return error_response(500, *HTTP_ERRORS[500])
        return render_template("errors/500.html"), 500

    @app.errorhandler(HTTPException)
    def other_http_error(error):
        if is_api_request():
            return error_response(error.code, "http_error", error.name)
        return error


# Content Security Policy: the browser may only load scripts, styles and
# images from this site, and forms may only post back to it. Templates
# therefore contain no inline <script> or style="" attributes, which means
# even an HTML-injection bug could not run injected JavaScript.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
)


def build_csp(extra_script_sources=()):
    """The policy above, optionally allowing extra script URLs.

    Phase 6: only the /analytics response passes an extra source, the
    exact Chart.js file URL (a full path, not the whole CDN host), so no
    other page can load anything from the CDN, and 'unsafe-inline' and
    'unsafe-eval' are never allowed anywhere.
    """
    if not extra_script_sources:
        return CONTENT_SECURITY_POLICY
    scripts = " ".join(("'self'", *extra_script_sources))
    return CONTENT_SECURITY_POLICY.replace("script-src 'self'", f"script-src {scripts}", 1)


def _register_security_headers(app):
    """Add protective HTTP headers to every response."""

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = build_csp(g.get("csp_script_sources", ()))
        # Pages show trading records, so browsers and proxies must not store
        # them (Phase 7). Static CSS/JS are not HTML and stay cacheable; the
        # JSON API already sets its own no-store header.
        if response.mimetype == "text/html" and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        if app.config["HSTS_ENABLED"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response
```

### `tradeledger/api/openapi.py` (MODIFIED)

**Why:** Documents the new decimal limits.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""OpenAPI 3.1 specification for the TradeLedger API.

Built as a Python dict (no extra library) and served at
GET /api/v1/openapi.json. `flask --app tradeledger openapi` writes the same
document to docs/openapi.json.

The tests check that:
  * every API route and method exists in this document, and vice versa;
  * real responses validate against the schemas here (with
    additionalProperties: false, so an undocumented field fails the test).
"""

from flask import current_app, jsonify

from . import api_bp

# ---------- Reusable schema fragments ----------

DECIMAL_PATTERN = r"^-?[0-9]+(\.[0-9]+)?$"
TIMESTAMP_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"


def decimal(description, nullable=False, example=None):
    schema = {
        "type": ["string", "null"] if nullable else "string",
        "pattern": DECIMAL_PATTERN,
        "description": description + " Exact decimal as a string (never a float).",
    }
    if example is not None:
        schema["examples"] = [example]
    return schema


def timestamp(description, nullable=False):
    return {
        "type": ["string", "null"] if nullable else "string",
        "pattern": TIMESTAMP_PATTERN,
        "description": description + " UTC, ISO 8601, e.g. 2026-03-02T08:15:00Z.",
    }


def nullable_string(description, enum=None):
    schema = {"type": ["string", "null"], "description": description}
    if enum:
        schema["enum"] = [*enum, None]
    return schema


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


DECIMAL_INPUT = {
    "description": 'Decimal value. Send a string (recommended, e.g. "1.085") or a JSON number; '
    "numbers are read exactly, never as binary floats. NaN and Infinity are rejected. "
    "At most 15 significant digits, 15 digits before the decimal point and 15 decimal places.",
    "anyOf": [{"type": "string"}, {"type": "number"}],
}
TIMESTAMP_INPUT = {
    "type": "string",
    "description": "ISO 8601 date-time. A value without an offset is treated as UTC; "
    "offsets are converted to UTC. A bare date is rejected.",
    "examples": ["2026-03-02T08:15:00Z", "2026-03-02 08:15", "2026-03-02T09:15:00+01:00"],
}

TRADE_PROPERTIES = {
    "id": {"type": "integer"},
    "instrument_id": {"type": "integer"},
    "symbol": {"type": "string"},
    "quote_currency": {"type": "string", "description": "Currency of all money values for this trade."},
    "trade_date": timestamp("Entry time."),
    "closed_at": timestamp(
        "Exit time. null for OPEN trades, and for legacy CLOSED trades recorded "
        "before the exit time was captured (never invented).",
        nullable=True,
    ),
    "direction": {"type": "string", "enum": ["LONG", "SHORT"]},
    "status": {"type": "string", "enum": ["OPEN", "CLOSED"]},
    "entry_price": decimal("Entry price.", example="1.085"),
    "stop_loss": decimal("Initial stop-loss price."),
    "take_profit": decimal("Target price, if planned.", nullable=True),
    "exit_price": decimal("Exit price. null while OPEN.", nullable=True),
    "position_size": decimal("Position size in the units the instrument's contract_multiplier assumes."),
    "fees": decimal("Total fees, in the quote currency."),
    "strategy": nullable_string("Free-text strategy label (max 50 characters)."),
    "session": nullable_string("Trading session.", ["ASIA", "LONDON", "NEW_YORK", "OTHER"]),
    "notes": nullable_string("Journal notes (max 2000 characters)."),
    "created_at": timestamp("When the record was created."),
    "updated_at": timestamp("When the record was last changed."),
    "result": {
        "anyOf": [{"$ref": "#/components/schemas/TradeResult"}, {"type": "null"}],
        "description": "Calculated by the analytics engine for CLOSED trades; null for OPEN trades.",
    },
}

TRADE_RESULT = obj(
    {
        "outcome": {
            "type": "string",
            "enum": ["WIN", "LOSS", "BREAKEVEN"],
            "description": "From net P&L: > 0 WIN, < 0 LOSS, exactly 0 BREAKEVEN.",
        },
        "gross_pnl": decimal("(exit - entry) x direction x size x contract_multiplier."),
        "fees": decimal("Fees."),
        "net_pnl": decimal("gross_pnl - fees."),
        "initial_risk": decimal(
            "Money lost if the stop is hit (fees excluded). null if the stop is invalid.", nullable=True
        ),
        "realised_r": decimal(
            "net_pnl / initial_risk. null when initial risk is not positive.", nullable=True
        ),
        "planned_rr": decimal(
            "Planned reward / planned risk from prices. null without a valid target.", nullable=True
        ),
        "r_unavailable_reason": nullable_string("Why realised_r is null, if it is."),
    }
)

TRADE_INPUT_PROPERTIES = {
    "instrument_id": {
        "type": "integer",
        "minimum": 1,
        "description": "Must reference an existing instrument.",
    },
    "trade_date": TIMESTAMP_INPUT,
    "closed_at": {
        **TIMESTAMP_INPUT,
        "type": ["string", "null"],
        "description": "Exit time. Required when the trade is CLOSED; must be null/absent when OPEN; "
        "must not be before trade_date. Exception: a legacy CLOSED trade whose exit time "
        "was never recorded may keep null when replaced with PUT.",
    },
    "direction": {
        "type": "string",
        "enum": ["LONG", "SHORT", "long", "short"],
        "description": "Case-insensitive.",
    },
    "status": {
        "type": ["string", "null"],
        "description": "OPEN or CLOSED (case-insensitive). If omitted: CLOSED when exit_price is given, "
        "otherwise OPEN. CLOSED requires exit_price; OPEN must not have one.",
    },
    "entry_price": {**DECIMAL_INPUT, "description": "Greater than 0. " + DECIMAL_INPUT["description"]},
    "stop_loss": {
        **DECIMAL_INPUT,
        "description": "Greater than 0. LONG: below entry. SHORT: above entry. Never equal to entry.",
    },
    "take_profit": {
        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
        "description": "Optional. LONG: above entry. SHORT: below entry.",
    },
    "exit_price": {
        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
        "description": "Required for CLOSED, absent/null for OPEN. Greater than 0.",
    },
    "position_size": {**DECIMAL_INPUT, "description": "Greater than 0."},
    "fees": {
        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
        "description": "0 or more. Defaults to 0.",
    },
    "strategy": {"type": ["string", "null"], "maxLength": 50},
    "session": {
        "type": ["string", "null"],
        "description": "ASIA, LONDON, NEW_YORK or OTHER (case-insensitive).",
    },
    "notes": {"type": ["string", "null"], "maxLength": 2000},
}

TRADE_INPUT_REQUIRED = [
    "instrument_id",
    "trade_date",
    "direction",
    "entry_price",
    "stop_loss",
    "position_size",
]

INSTRUMENT_PROPERTIES = {
    "id": {"type": "integer"},
    "symbol": {"type": "string", "description": "Unique, upper case, 1-20 letters, digits or underscores."},
    "name": {"type": "string"},
    "asset_class": {"type": "string", "enum": ["FOREX", "COMMODITY", "INDEX", "STOCK", "OTHER"]},
    "price_increment": decimal("Smallest price step quoted, e.g. 0.00001."),
    "contract_multiplier": decimal(
        "Money (in quote_currency) per 1.0 move in price per 1.0 unit of position_size. "
        "Depends on your broker's contract specification; sample instruments use illustrative values."
    ),
    "quote_currency": {
        "type": "string",
        "description": "3-letter currency code in which P&L is denominated.",
    },
    "created_at": timestamp("When the record was created."),
}

INSTRUMENT_INPUT = obj(
    {
        "symbol": {
            "type": "string",
            "description": "1-20 letters, digits or underscores; stored upper case.",
        },
        "name": {"type": "string", "maxLength": 100},
        "asset_class": {
            "type": "string",
            "description": "FOREX, COMMODITY, INDEX, STOCK or OTHER (case-insensitive).",
        },
        "price_increment": {**DECIMAL_INPUT, "description": "Greater than 0."},
        "contract_multiplier": {
            **DECIMAL_INPUT,
            "description": "Greater than 0. See Instrument.contract_multiplier.",
        },
        "quote_currency": {"type": "string", "description": "3 letters, e.g. USD."},
    }
)

TRADE_REF = obj(
    {
        "trade_id": {"type": "integer"},
        "symbol": {"type": "string"},
        "trade_date": timestamp("Entry time."),
        "value": decimal("The value that ranked this trade (R or net P&L)."),
    }
)

DRAWDOWN = obj(
    {
        "amount": decimal(
            "Largest fall from a running peak (>= 0), in R or in one currency. Never a percentage."
        ),
        "peak_trade_id": {"type": ["integer", "null"], "description": "null = the starting point (0)."},
        "trough_trade_id": {"type": ["integer", "null"], "description": "null when there was no drawdown."},
    }
)

MONEY_SUMMARY = obj(
    {
        "currency": {"type": "string"},
        "trades": {"type": "integer"},
        "gross_pnl": decimal("Sum of P&L before fees."),
        "fees": decimal("Sum of fees."),
        "net_pnl": decimal("Sum of P&L after fees."),
        "winning_pnl": decimal("Sum of net P&L of winning trades (>= 0)."),
        "losing_pnl": decimal("Sum of net P&L of losing trades (<= 0)."),
        "profit_factor": decimal("winning_pnl / |losing_pnl|. null with no losing trades.", nullable=True),
        "best_trade": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
        "worst_trade": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
        "max_drawdown": ref("Drawdown"),
    }
)

SUMMARY_PROPERTIES = {
    "trades": {"type": "integer", "description": "Closed trades evaluated."},
    "wins": {"type": "integer"},
    "losses": {"type": "integer"},
    "breakevens": {"type": "integer"},
    "win_rate": decimal("wins / trades, as a fraction 0-1. null with no trades.", nullable=True),
    "loss_rate": decimal("losses / trades, 0-1.", nullable=True),
    "breakeven_rate": decimal("breakevens / trades, 0-1.", nullable=True),
    "r_trades": {"type": "integer", "description": "Trades with a usable realised R."},
    "total_r": decimal("Sum of realised R."),
    "average_r": decimal("Mean realised R.", nullable=True),
    "average_win_r": decimal("Mean R of winning trades.", nullable=True),
    "average_loss_r": decimal("Mean R of losing trades (negative).", nullable=True),
    "expectancy_r": decimal(
        "P(win) x average_win_r + P(loss) x average_loss_r, in R per trade.", nullable=True
    ),
    "profit_factor_r": decimal(
        "Sum of positive R / |sum of negative R|. null with no losses.", nullable=True
    ),
    "planned_rr_trades": {"type": "integer"},
    "average_planned_rr": decimal("Mean planned R:R of trades with a valid target.", nullable=True),
    "longest_win_streak": {"type": "integer"},
    "longest_loss_streak": {"type": "integer"},
    "max_drawdown_r": ref("Drawdown"),
    "best_trade_r": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
    "worst_trade_r": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
    "money": {
        "type": "object",
        "additionalProperties": ref("MoneySummary"),
        "description": "One entry per quote currency. Currencies are never added together.",
    },
}

EXCLUDED_TRADE = obj({"trade_id": {"type": ["integer", "null"]}, "reason": {"type": "string"}})

CURVE_POINT = obj(
    {
        "trade_id": {"type": "integer"},
        "trade_date": timestamp("Entry time."),
        "realised_at": timestamp("Exit time, or entry time for legacy trades with no recorded exit."),
        "close_time_known": {"type": "boolean"},
        "symbol": {"type": "string"},
        "realised_r": decimal("This trade's realised R."),
        "cumulative_r": decimal("Running total of realised R. NOT account equity."),
    }
)

ANALYTICS_META = obj(
    {
        "filters": obj(
            {
                "instrument_id": {"type": ["integer", "null"]},
                "strategy": {"type": ["string", "null"]},
                "session": {
                    "type": ["string", "null"],
                    "enum": ["ASIA", "LONDON", "NEW_YORK", "OTHER", None],
                },
                "start": {"type": ["string", "null"], "format": "date"},
                "end": {"type": ["string", "null"], "format": "date"},
            }
        ),
        "notes": {"type": "array", "items": {"type": "string"}},
        "label": {"type": "string"},
    },
    required=["filters", "notes"],
)

ERROR = obj(
    {
        "error": obj(
            {
                "code": {"type": "string", "description": "Stable machine-readable code."},
                "message": {"type": "string", "description": "Human-readable explanation."},
                "details": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Field name -> problem. Present only when useful.",
                },
            },
            required=["code", "message"],
        ),
    }
)

PAGINATION = obj(
    {
        "page": {"type": "integer", "minimum": 1},
        "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
        "total": {"type": "integer", "description": "Matching trades across all pages."},
        "total_pages": {"type": "integer", "description": "0 when there are no matches."},
    }
)

# ---------- Parameters ----------


def query_param(name, schema, description):
    return {"name": name, "in": "query", "required": False, "schema": schema, "description": description}


PARAMS = {
    "instrument_id": query_param(
        "instrument_id", {"type": "integer", "minimum": 1}, "Only this instrument. 400 if it does not exist."
    ),
    "status": query_param(
        "status",
        {"type": "string", "enum": ["OPEN", "CLOSED"]},
        "Only trades with this status (case-insensitive).",
    ),
    "trade_start": query_param(
        "start",
        {"type": "string", "format": "date"},
        "First entry date to include (YYYY-MM-DD, UTC, inclusive).",
    ),
    "trade_end": query_param(
        "end",
        {"type": "string", "format": "date"},
        "Last entry date to include (YYYY-MM-DD, UTC, inclusive).",
    ),
    "realised_start": query_param(
        "start",
        {"type": "string", "format": "date"},
        "First realised date (closed_at, or trade_date if unknown) to include.",
    ),
    "realised_end": query_param(
        "end", {"type": "string", "format": "date"}, "Last realised date to include (inclusive)."
    ),
    "strategy": query_param(
        "strategy",
        {"type": "string", "minLength": 1, "maxLength": 50},
        "Only trades with exactly this strategy label (case-sensitive). Phase 6.",
    ),
    "session": query_param(
        "session",
        {"type": "string", "enum": ["ASIA", "LONDON", "NEW_YORK", "OTHER"]},
        "Only trades recorded in this session (case-insensitive). Phase 6.",
    ),
    "page": query_param("page", {"type": "integer", "minimum": 1, "default": 1}, "Page number."),
    "per_page": query_param(
        "per_page", {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}, "Trades per page."
    ),
}


def path_id(name, what):
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": "integer", "minimum": 1},
        "description": f"{what} id.",
    }


# ---------- Responses ----------


def json_response(description, schema, example=None):
    content = {"schema": schema}
    if example is not None:
        content["example"] = example
    return {"description": description, "content": {"application/json": content}}


def error(description):
    return json_response(description, ref("Error"))


ERRORS = {
    "400": error(
        "Invalid request: malformed JSON, unknown fields or parameters, or failed validation "
        "(details lists each field)."
    ),
    "403": error("The API is disabled in this environment (no authentication; production default)."),
    "404": error("The resource does not exist."),
    "405": error("Method not allowed for this URL."),
    "409": error("Conflict, e.g. an instrument symbol that already exists."),
    "413": error("Request body larger than 64 KB."),
    "415": error("Request body is not application/json."),
    "500": error("Unexpected error. No internal details are included."),
}


def errors(*codes):
    return {code: ERRORS[code] for code in codes}


def envelope(data_schema, **extra):
    return obj({"data": data_schema, **extra})


EXAMPLE_TRADE_INPUT = {
    "instrument_id": 1,
    "trade_date": "2026-04-01T08:15:00Z",
    "closed_at": "2026-04-01T11:40:00Z",
    "direction": "LONG",
    "entry_price": "1.08500",
    "stop_loss": "1.08300",
    "take_profit": "1.08900",
    "exit_price": "1.08900",
    "position_size": "1",
    "fees": "7",
    "strategy": "Breakout",
    "session": "LONDON",
    "notes": "Synthetic example.",
}

EXAMPLE_TRADE = {
    "id": 17,
    "instrument_id": 1,
    "symbol": "EURUSD",
    "quote_currency": "USD",
    "trade_date": "2026-04-01T08:15:00Z",
    "closed_at": "2026-04-01T11:40:00Z",
    "direction": "LONG",
    "status": "CLOSED",
    "entry_price": "1.085",
    "stop_loss": "1.083",
    "take_profit": "1.089",
    "exit_price": "1.089",
    "position_size": "1",
    "fees": "7",
    "strategy": "Breakout",
    "session": "LONDON",
    "notes": "Synthetic example.",
    "created_at": "2026-11-15T09:00:00Z",
    "updated_at": "2026-11-15T09:00:00Z",
    "result": {
        "outcome": "WIN",
        "gross_pnl": "400",
        "fees": "7",
        "net_pnl": "393",
        "initial_risk": "200",
        "realised_r": "1.965",
        "planned_rr": "2",
        "r_unavailable_reason": None,
    },
}

EXAMPLE_VALIDATION_ERROR = {
    "error": {
        "code": "validation_error",
        "message": "The request is invalid.",
        "details": {
            "position_size": "must be greater than zero",
            "stop_loss": "must be below the entry price for a LONG trade",
        },
    }
}


# The analytics filters, in the same order as report_filters.FILTER_NAMES.
ANALYTICS_PARAMS = [
    PARAMS["instrument_id"],
    PARAMS["strategy"],
    PARAMS["session"],
    PARAMS["realised_start"],
    PARAMS["realised_end"],
]


def grouped_path(summary, description):
    return {
        "get": {
            "tags": ["Analytics"],
            "summary": summary,
            "description": description,
            "parameters": ANALYTICS_PARAMS,
            "responses": {
                "200": json_response("Groups in a stable order.", ref("GroupedAnalytics")),
                **errors("400", "403", "405"),
            },
        }
    }


def build_spec():
    group_summary = obj({"group": {"type": "string", "description": "Group label."}, **SUMMARY_PROPERTIES})
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "TradeLedger API",
            "version": "1.0.0",
            "description": (
                "JSON API for the TradeLedger trading journal.\n\n"
                "**Security:** there is no authentication. The API is for local, single-user use. "
                "In the production configuration it is disabled (403) unless explicitly enabled.\n\n"
                "**Conventions:** decimal values are exact strings; timestamps are UTC "
                "`YYYY-MM-DDTHH:MM:SSZ`; unavailable values are `null`; money is per quote currency; "
                "R values are currency-neutral; cumulative R is not account equity. "
                "Unknown body fields and query parameters are rejected with 400."
            ),
        },
        "servers": [{"url": "/api/v1"}],
        "paths": {
            "/trades": {
                "get": {
                    "tags": ["Trades"],
                    "summary": "List trades",
                    "description": "Newest trade_date first, then highest id first. Dates filter on entry time.",
                    "parameters": [
                        PARAMS[p]
                        for p in ("instrument_id", "status", "trade_start", "trade_end", "page", "per_page")
                    ],
                    "responses": {
                        "200": json_response("A page of trades.", ref("TradeList")),
                        **errors("400", "403", "405"),
                    },
                },
                "post": {
                    "tags": ["Trades"],
                    "summary": "Create a trade",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": ref("TradeInput"), "example": EXAMPLE_TRADE_INPUT}
                        },
                    },
                    "responses": {
                        "201": {
                            **json_response(
                                "Created. Location header holds the new trade's URL.",
                                ref("TradeEnvelope"),
                                {"data": EXAMPLE_TRADE},
                            ),
                            "headers": {"Location": {"schema": {"type": "string"}}},
                        },
                        "400": json_response(
                            "Invalid trade. Nothing was written.", ref("Error"), EXAMPLE_VALIDATION_ERROR
                        ),
                        **errors("403", "413", "415"),
                    },
                },
            },
            "/trades/{trade_id}": {
                "parameters": [path_id("trade_id", "Trade")],
                "get": {
                    "tags": ["Trades"],
                    "summary": "Get a trade",
                    "responses": {
                        "200": json_response("The trade.", ref("TradeEnvelope")),
                        **errors("403", "404"),
                    },
                },
                "put": {
                    "tags": ["Trades"],
                    "summary": "Replace a trade",
                    "description": "Full replacement: every field in TradeReplace must be present "
                    "(null for empty optional fields). There are no partial updates.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": ref("TradeReplace"),
                                "example": EXAMPLE_TRADE_INPUT,
                            }
                        },
                    },
                    "responses": {
                        "200": json_response("The updated trade.", ref("TradeEnvelope")),
                        **errors("400", "403", "404", "413", "415"),
                    },
                },
                "delete": {
                    "tags": ["Trades"],
                    "summary": "Delete a trade",
                    "responses": {"204": {"description": "Deleted. No body."}, **errors("403", "404")},
                },
            },
            "/instruments": {
                "get": {
                    "tags": ["Instruments"],
                    "summary": "List instruments (sorted by symbol)",
                    "responses": {
                        "200": json_response("All instruments.", ref("InstrumentList")),
                        **errors("400", "403"),
                    },
                },
                "post": {
                    "tags": ["Instruments"],
                    "summary": "Create an instrument",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": ref("InstrumentInput"),
                                "example": {
                                    "symbol": "US30",
                                    "name": "Wall Street 30 CFD (example values)",
                                    "asset_class": "INDEX",
                                    "price_increment": "1",
                                    "contract_multiplier": "1",
                                    "quote_currency": "USD",
                                },
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            **json_response("Created.", ref("InstrumentEnvelope")),
                            "headers": {"Location": {"schema": {"type": "string"}}},
                        },
                        **errors("400", "403", "409", "413", "415"),
                    },
                },
            },
            "/instruments/{instrument_id}": {
                "parameters": [path_id("instrument_id", "Instrument")],
                "get": {
                    "tags": ["Instruments"],
                    "summary": "Get an instrument",
                    "responses": {
                        "200": json_response("The instrument.", ref("InstrumentEnvelope")),
                        **errors("403", "404"),
                    },
                },
            },
            "/analytics/summary": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "Overall realised performance",
                    "parameters": ANALYTICS_PARAMS,
                    "responses": {
                        "200": json_response("Overall statistics.", ref("SummaryAnalytics")),
                        **errors("400", "403", "405"),
                    },
                }
            },
            "/analytics/instruments": grouped_path("Performance by instrument", "Sorted by symbol."),
            "/analytics/strategies": grouped_path(
                "Performance by strategy", "Sorted alphabetically; untagged trades are 'Unspecified'."
            ),
            "/analytics/sessions": grouped_path(
                "Performance by session", "Sorted alphabetically; untagged trades are 'Unspecified'."
            ),
            "/analytics/weekday": grouped_path(
                "Performance by realised weekday (UTC)", "Monday to Sunday; days with no trades are omitted."
            ),
            "/analytics/monthly": grouped_path(
                "Performance by realised month (YYYY-MM, UTC)", "Oldest first."
            ),
            "/analytics/curve": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "Cumulative realised R (chart data)",
                    "description": "In realised order. NOT an account equity curve.",
                    "parameters": ANALYTICS_PARAMS,
                    "responses": {
                        "200": json_response("Curve points.", ref("CurveAnalytics")),
                        **errors("400", "403", "405"),
                    },
                }
            },
            "/openapi.json": {
                "get": {
                    "tags": ["Documentation"],
                    "summary": "This specification",
                    "description": "Available even when the API is disabled.",
                    "responses": {
                        "200": {
                            "description": "OpenAPI 3.1 document.",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "Trade": obj(TRADE_PROPERTIES),
                "TradeResult": TRADE_RESULT,
                "TradeInput": obj(TRADE_INPUT_PROPERTIES, required=TRADE_INPUT_REQUIRED),
                "TradeReplace": obj(TRADE_INPUT_PROPERTIES),
                "Instrument": obj(INSTRUMENT_PROPERTIES),
                "InstrumentInput": INSTRUMENT_INPUT,
                "Pagination": PAGINATION,
                "TradeEnvelope": envelope(ref("Trade")),
                "TradeList": envelope({"type": "array", "items": ref("Trade")}, pagination=ref("Pagination")),
                "InstrumentEnvelope": envelope(ref("Instrument")),
                "InstrumentList": envelope({"type": "array", "items": ref("Instrument")}),
                "TradeRef": TRADE_REF,
                "Drawdown": DRAWDOWN,
                "MoneySummary": MONEY_SUMMARY,
                "Summary": obj(SUMMARY_PROPERTIES),
                "GroupSummary": group_summary,
                "ExcludedTrade": EXCLUDED_TRADE,
                "CurvePoint": CURVE_POINT,
                "AnalyticsMeta": ANALYTICS_META,
                "SummaryAnalytics": envelope(
                    obj(
                        {
                            "overall": ref("Summary"),
                            "open_trades": {
                                "type": "integer",
                                "description": "Counted, but excluded from realised metrics.",
                            },
                            "excluded": {
                                "type": "array",
                                "items": ref("ExcludedTrade"),
                                "description": "Closed trades that could not be evaluated, with reasons.",
                            },
                            "r_unavailable": {
                                "type": "array",
                                "items": ref("ExcludedTrade"),
                                "description": "Closed trades counted, but with no realised R.",
                            },
                            "unknown_close_time_trade_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Legacy trades ordered by entry time.",
                            },
                        }
                    ),
                    meta=ref("AnalyticsMeta"),
                ),
                "GroupedAnalytics": envelope(
                    {"type": "array", "items": ref("GroupSummary")}, meta=ref("AnalyticsMeta")
                ),
                "CurveAnalytics": envelope(
                    {"type": "array", "items": ref("CurvePoint")}, meta=ref("AnalyticsMeta")
                ),
                "Error": ERROR,
            }
        },
    }


@api_bp.get("/openapi.json")
def openapi_spec():
    spec = build_spec()
    spec["info"]["x-app-version"] = current_app.config["APP_VERSION"]
    return jsonify(spec)
```

### `tradeledger/config.py` (MODIFIED)

**Why:** Version 0.7.0; TRUSTED_HOSTS + env_hosts() (F2); key-strength constants; HSTS_ENABLED.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Application configuration.

Settings are read from environment variables so that secrets never live in
source code. Each class represents one environment the app can run in.
"""

import os


def env_flag(name):
    """True only when the environment variable is exactly "1"."""
    return os.environ.get(name) == "1"


def env_hosts(name, default):
    """A comma-separated list of host names from the environment.

    Not set -> `default`.  "*" -> None, meaning "accept any Host header"
    (Flask's own default). Ports are ignored when hosts are compared.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    if raw.strip() == "*":
        return None
    return [host.strip().lower() for host in raw.split(",") if host.strip()]


# Values that must never be accepted as a production SECRET_KEY: the example
# in .env.example and the public testing key below (Phase 7).
PLACEHOLDER_SECRET_KEYS = frozenset(
    {"replace-me-with-a-long-random-string", "test-secret-key-not-for-real-use"}
)
MIN_PRODUCTION_SECRET_KEY_LENGTH = 32


class Config:
    """Settings shared by every environment."""

    APP_NAME = "TradeLedger"
    APP_VERSION = "0.7.0"

    # Read from the environment. Never hard-code a real key in this file.
    SECRET_KEY = os.environ.get("SECRET_KEY")

    TESTING = False

    # When True, the app refuses to start without a SECRET_KEY.
    REQUIRE_SECRET_KEY = True

    # Path to the SQLite database file. If not set, create_app() uses
    # instance/tradeledger.db inside the project folder.
    DATABASE = os.environ.get("DATABASE_PATH")

    # Largest request body accepted, in bytes. Flask answers 413 above this.
    # A trade is well under 2 KB, so 64 KB is generous.
    MAX_CONTENT_LENGTH = 64 * 1024

    # The JSON API has NO authentication yet. It is enabled for local
    # development and tests, and disabled by default in production (see
    # ProductionConfig). Pagination never returns more than API_MAX_PER_PAGE.
    API_ENABLED = True
    API_MAX_PER_PAGE = 100

    # The trade-management website (journal, forms, instruments) has no
    # login either. Same rule as the API: on for local use, off by default
    # in production (see ProductionConfig).
    JOURNAL_ENABLED = True
    JOURNAL_PER_PAGE = 20

    # Session cookie (holds only the CSRF token). HttpOnly stops JavaScript
    # reading it; SameSite=Lax stops browsers sending it with cross-site
    # form posts, a second layer of CSRF defence.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Host header allow-list (Phase 7). TradeLedger has no login, so it must
    # only answer requests addressed to the machine it runs on. Without this
    # check, a malicious web page could use "DNS rebinding" (pointing its own
    # domain name at 127.0.0.1) to make YOUR browser read or change your
    # local journal through that page's address. Requests with any other
    # Host header get 400 before any route runs.
    # Running on another machine or LAN address? Add its name or IP:
    #   TRADELEDGER_TRUSTED_HOSTS=localhost,127.0.0.1,192.168.1.20
    TRUSTED_HOSTS = env_hosts("TRADELEDGER_TRUSTED_HOSTS", ["localhost", "127.0.0.1"])

    # Strict-Transport-Security (HSTS) tells browsers to use HTTPS only.
    # Off by default; ProductionConfig turns it on (it assumes HTTPS).
    HSTS_ENABLED = False

    # Chart.js for the /analytics dashboard (Phase 6).
    # Default: version 4.5.1, pinned, from the jsDelivr CDN. This needs
    # internet access in the BROWSER (not on the server). Only the
    # dashboard's Content-Security-Policy allows this exact URL.
    # To host it yourself instead (see docs/THIRD_PARTY_NOTICES.md), download the
    # file into tradeledger/static/vendor/chartjs/ and set
    #   TRADELEDGER_CHART_JS_URL=/static/vendor/chartjs/chart.umd.min.js
    # TRADELEDGER_CHART_JS_INTEGRITY optionally holds the file's
    # Subresource Integrity hash ("sha384-..."), which makes the browser
    # refuse a file that has been altered. It is not set by default because
    # it could not be computed from a verified copy of the file.
    CHART_JS_VERSION = "4.5.1"
    CHART_JS_URL = os.environ.get(
        "TRADELEDGER_CHART_JS_URL", "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
    )
    CHART_JS_INTEGRITY = os.environ.get("TRADELEDGER_CHART_JS_INTEGRITY") or None


class DevelopmentConfig(Config):
    """Local development on your own machine."""

    # In development a missing key is tolerated: the app generates a
    # temporary one and logs a warning (see create_app).
    REQUIRE_SECRET_KEY = False


class TestingConfig(Config):
    """Used by pytest. Fixed values make tests repeatable."""

    TESTING = True
    SECRET_KEY = "test-secret-key-not-for-real-use"  # noqa: S105 - fixed, public, tests only
    REQUIRE_SECRET_KEY = False

    # Deliberately no default: tests must pass their own temporary database
    # path, so a test can never touch your development data.
    DATABASE = None


class ProductionConfig(Config):
    """Deployed environments (e.g. AWS in Phase 8)."""

    REQUIRE_SECRET_KEY = True

    # Safe default: with no authentication, the API (which exposes and
    # changes trading data) answers 403 in production. Setting
    # TRADELEDGER_ENABLE_UNAUTHENTICATED_API=1 overrides this and logs a
    # warning; it is only acceptable behind your own access control (for
    # example a private network). Real authentication is a later phase.
    API_ENABLED = env_flag("TRADELEDGER_ENABLE_UNAUTHENTICATED_API")

    # The journal website is disabled (403) in production for the same
    # reason. TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL=1 overrides it.
    JOURNAL_ENABLED = env_flag("TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL")

    # Only send the session cookie over HTTPS.
    SESSION_COOKIE_SECURE = True

    # Production must say which host names it serves (for example
    # "journal.example.com"). Unset means no Host check, which is only safe
    # behind a reverse proxy that already enforces it (see SECURITY.md).
    TRUSTED_HOSTS = env_hosts("TRADELEDGER_TRUSTED_HOSTS", None)

    # Production is assumed to be served over HTTPS (see SESSION_COOKIE_SECURE).
    HSTS_ENABLED = True


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
```

### `tradeledger/performance/dashboard.py` (MODIFIED)

**Why:** parse_stored for chart times; removed the now-unused TIMESTAMP_FORMAT.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Prepare one PerformanceReport for the /analytics dashboard.

This module sits between the engine and the page:

  report.py      calculates (Decimal, exact)          <- source of truth
  dashboard.py   arranges the results for display:     <- this file
                   * KPI cards
                   * the cumulative R series with running peak and drawdown
                   * every month in the range, including months with no trades
                   * all seven weekdays in calendar order
                   * instrument / strategy / session comparisons
                   * text descriptions of each chart (for people who cannot
                     see the charts)
                   * a JSON-safe chart payload
  analytics.js   draws the charts from that payload    <- presentation only

The only arithmetic here is the running peak and drawdown of a series the
engine has already calculated, done in Decimal under the engine's 60-digit
context. Everything else is selection, ordering and formatting.

Exact values and chart numbers
------------------------------
Chart libraries need JavaScript numbers (64-bit floats). Floats cannot hold
values like 0.1 exactly, so the payload never contains them: every value is
an exact decimal STRING (e.g. "7.688173076923..."), next to the text people
read (e.g. "+7.69R"). analytics.js converts the string to a number at the
very last moment, only to position a bar or a point. Tables and tooltips
show the exact-then-rounded text produced here, so the chart can never
become the source of a figure.
"""

from dataclasses import dataclass, field
from datetime import UTC, date
from decimal import Decimal, localcontext

from ..report_filters import ReportFilters
from ..timestamps import parse_stored
from ..validation import SESSIONS
from .formatting import (
    NOT_AVAILABLE,
    decimal_to_text,
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
)
from .metrics import Summary
from .report import UNSPECIFIED, WEEKDAYS, CurvePoint, PerformanceReport, build_report
from .trade_math import EXACT

ZERO = Decimal(0)

# The trade-by-trade table shows at most this many (most recent) rows. A
# 10,000-row HTML table is unusable and made the page ~9.5 MB in the
# benchmark; the charts and grouped tables still cover every trade.
SERIES_TABLE_LIMIT = 200

# Groups with fewer closed trades than this are flagged "small sample".
# A handful of trades says very little about future results.
SMALL_SAMPLE = 10

# Sessions are shown in the order of the trading day, not alphabetically,
# with readable labels.
SESSION_ORDER = (*SESSIONS, UNSPECIFIED)
SESSION_LABELS = {
    "ASIA": "Asia",
    "LONDON": "London",
    "NEW_YORK": "New York",
    "OTHER": "Other",
    UNSPECIFIED: UNSPECIFIED,
}

PLURALS = {"strategy": "strategies"}


# ---------- Data structures ----------


@dataclass(frozen=True)
class SeriesPoint:
    """One closed trade on the cumulative R and drawdown charts."""

    trade_id: int
    symbol: str
    realised_at: str  # 'YYYY-MM-DDTHH:MM:SSZ' (UTC)
    x: int  # realised_at as Unix milliseconds (chart axis)
    close_time_known: bool  # False: legacy trade placed at its entry time
    realised_r: Decimal
    cumulative_r: Decimal
    peak_r: Decimal  # highest cumulative R so far, never below 0
    drawdown_r: Decimal  # peak_r - cumulative_r, never below 0


@dataclass(frozen=True)
class PeriodRow:
    """A month or a weekday. summary is None when nothing closed in it, which
    is different from closing trades that netted 0R."""

    key: str  # '2026-03' or 'Monday'
    label: str  # 'Mar 2026' or 'Monday'
    summary: Summary | None


@dataclass(frozen=True)
class GroupRow:
    """An instrument, strategy or session. key is the stored value (e.g.
    NEW_YORK); label is what people read (e.g. New York)."""

    key: str
    label: str
    summary: Summary

    @property
    def small_sample(self) -> bool:
        return self.summary.trades < SMALL_SAMPLE


@dataclass(frozen=True)
class Kpi:
    """One summary card. value is display text; tone drives colour, but the
    text (sign, 'n/a', words) always carries the meaning on its own."""

    key: str
    label: str
    value: str
    note: str
    tone: str = "neutral"  # 'positive', 'negative' or 'neutral'


@dataclass(frozen=True)
class Dashboard:
    filters: ReportFilters
    report: PerformanceReport
    kpis: list[Kpi]
    series: list[SeriesPoint]
    months: list[PeriodRow]
    weekdays: list[PeriodRow]
    instruments: list[GroupRow]
    strategies: list[GroupRow]
    sessions: list[GroupRow]
    descriptions: dict[str, str] = field(default_factory=dict)

    @property
    def has_results(self) -> bool:
        return self.report.overall.trades > 0

    @property
    def series_table(self) -> list[SeriesPoint]:
        """The most recent SERIES_TABLE_LIMIT points, for the HTML table."""
        return self.series[-SERIES_TABLE_LIMIT:]


# ---------- Small pure functions ----------


def to_epoch_ms(timestamp: str) -> int:
    """'2026-03-02T11:40:00Z' -> milliseconds since 1970-01-01 UTC (an int,
    exact, which a chart can use as a time axis)."""
    moment = parse_stored(timestamp).replace(tzinfo=UTC)
    return int(moment.timestamp()) * 1000


def drawdown_series(curve: list[CurvePoint]) -> list[SeriesPoint]:
    """Add the running peak and drawdown to the engine's cumulative R curve.

    The peak starts at 0 (before any trade the running total is 0R), so a
    first trade that loses 1R is already a 1R drawdown. The largest
    drawdown_r in the result equals the engine's max_drawdown_r; a test
    checks that the two independent paths agree.
    """
    points, peak = [], ZERO
    with localcontext(EXACT):
        for point in curve:
            peak = max(peak, point.cumulative_r)
            points.append(
                SeriesPoint(
                    trade_id=point.trade_id,
                    symbol=point.symbol,
                    realised_at=point.realised_at,
                    x=to_epoch_ms(point.realised_at),
                    close_time_known=point.close_time_known,
                    realised_r=point.realised_r,
                    cumulative_r=point.cumulative_r,
                    peak_r=peak,
                    drawdown_r=peak - point.cumulative_r,
                )
            )
    return points


def month_keys(first: str, last: str) -> list[str]:
    """Every 'YYYY-MM' from first to last inclusive, across year ends."""
    year, month = int(first[:4]), int(first[5:7])
    end = (int(last[:4]), int(last[5:7]))
    keys = []
    while (year, month) <= end:
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return keys


def month_label(key: str) -> str:
    return date(int(key[:4]), int(key[5:7]), 1).strftime("%b %Y")


def month_rows(by_month: dict[str, Summary]) -> list[PeriodRow]:
    """One row per calendar month between the first and last month with a
    closed trade. Gaps are kept, with summary=None, so a quiet month is
    visible as a gap rather than silently disappearing (or, worse, being
    drawn as a 0R month)."""
    if not by_month:
        return []
    keys = sorted(by_month)
    return [PeriodRow(k, month_label(k), by_month.get(k)) for k in month_keys(keys[0], keys[-1])]


def weekday_rows(by_weekday: dict[str, Summary]) -> list[PeriodRow]:
    """All seven days, Monday first. Days with no closed trades keep a row
    with summary=None."""
    return [PeriodRow(day, day, by_weekday.get(day)) for day in WEEKDAYS]


def group_rows(groups: dict[str, Summary], order=None, labels=None) -> list[GroupRow]:
    """Groups ordered by `order` when given (sessions), otherwise by total R,
    best first, with the key breaking ties so the order is stable."""
    labels = labels or {}
    rows = [GroupRow(key, labels.get(key, key), summary) for key, summary in groups.items()]
    if order is not None:
        position = {key: index for index, key in enumerate(order)}
        return sorted(rows, key=lambda row: position.get(row.key, len(position)))
    return sorted(rows, key=lambda row: (-row.summary.total_r, row.key))


def tone(value: Decimal | None) -> str:
    if value is None or value == 0:
        return "neutral"
    return "positive" if value > 0 else "negative"


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {PLURALS.get(word, word + 's')}"


def short_time(timestamp: str) -> str:
    return parse_stored(timestamp).strftime("%d %b %Y, %H:%M UTC")


# ---------- KPI cards ----------


def build_kpis(report: PerformanceReport) -> list[Kpi]:
    s = report.overall
    none = s.trades == 0
    unavailable = "No closed trades match the filters"

    def count_card(key, label, count, rate):
        note = unavailable if none else f"{format_rate(rate)} of closed trades"
        return Kpi(key, label, NOT_AVAILABLE if none else str(count), note)

    return [
        Kpi(
            "trades",
            "Closed trades",
            str(s.trades),
            f"{plural(report.open_trades, 'open trade')} not included"
            if report.open_trades
            else "No open trades",
        ),
        count_card("wins", "Winning trades", s.wins, s.win_rate),
        count_card("losses", "Losing trades", s.losses, s.loss_rate),
        count_card("breakevens", "Breakeven trades", s.breakevens, s.breakeven_rate),
        Kpi(
            "win_rate",
            "Win rate",
            format_rate(s.win_rate),
            unavailable if none else f"{s.wins} of {s.trades} closed trades",
        ),
        Kpi(
            "average_r",
            "Average realised R",
            format_r(s.average_r),
            unavailable
            if s.average_r is None
            else f"Mean over {plural(s.r_trades, 'trade')} with a usable R",
            tone(s.average_r),
        ),
        Kpi(
            "expectancy_r",
            "Expectancy per trade",
            format_r(s.expectancy_r),
            unavailable if s.expectancy_r is None else "Win rate × average win + loss rate × average loss",
            tone(s.expectancy_r),
        ),
        Kpi(
            "profit_factor_r",
            "Profit factor (R)",
            format_ratio(s.profit_factor_r),
            unavailable
            if none
            else ("Needs at least one losing trade" if s.profit_factor_r is None else "Winning R ÷ losing R"),
        ),
        Kpi(
            "total_r",
            "Total realised R",
            NOT_AVAILABLE if none else format_r(s.total_r),
            unavailable if none else "Sum of R-multiples (not account equity)",
            "neutral" if none else tone(s.total_r),
        ),
        Kpi(
            "max_drawdown_r",
            "Maximum drawdown",
            NOT_AVAILABLE if none else format_r_amount(s.max_drawdown_r.amount),
            unavailable if none else "Largest fall from a running peak of cumulative R",
            "negative" if not none and s.max_drawdown_r.amount > 0 else "neutral",
        ),
        Kpi(
            "longest_win_streak",
            "Longest winning streak",
            NOT_AVAILABLE if none else plural(s.longest_win_streak, "trade"),
            unavailable if none else "Consecutive wins, in realised order",
        ),
        Kpi(
            "longest_loss_streak",
            "Longest losing streak",
            NOT_AVAILABLE if none else plural(s.longest_loss_streak, "trade"),
            unavailable if none else "Consecutive losses, in realised order",
        ),
    ]


# ---------- Text descriptions (the charts' text alternatives) ----------


def describe_curve(series: list[SeriesPoint]) -> str:
    if not series:
        return "There are no closed trades with a usable R to plot."
    last = series[-1]
    high = max(series, key=lambda p: (p.cumulative_r, -p.x))
    low = min(series, key=lambda p: (p.cumulative_r, p.x))
    return (
        f"Cumulative realised R across {plural(len(series), 'closed trade')}, "
        f"from {short_time(series[0].realised_at)} to {short_time(last.realised_at)}. "
        f"It ended at {format_r(last.cumulative_r)}. The highest point was {format_r(high.cumulative_r)} "
        f"(after trade #{high.trade_id}) and the lowest was {format_r(low.cumulative_r)} "
        f"(after trade #{low.trade_id})."
    )


def describe_drawdown(series: list[SeriesPoint], report: PerformanceReport) -> str:
    if not series:
        return "There is no cumulative R series, so there is no drawdown to show."
    worst = report.overall.max_drawdown_r
    current = series[-1].drawdown_r
    if worst.amount == 0:
        return "No drawdown: cumulative R never fell below its previous peak."
    start = (
        "the starting point (0R)"
        if worst.peak_trade_id is None
        else f"the peak after trade #{worst.peak_trade_id}"
    )
    return (
        f"Maximum drawdown was {format_r_amount(worst.amount)}, from {start} to the low after trade "
        f"#{worst.trough_trade_id}. Current drawdown: {format_r_amount(current)} below the running peak."
    )


def describe_periods(rows: list[PeriodRow], noun: str) -> str:
    traded = [row for row in rows if row.summary is not None]
    if not traded:
        return f"No {noun} has closed trades."
    best = max(traded, key=lambda row: row.summary.total_r)
    worst = min(traded, key=lambda row: row.summary.total_r)
    empty = [row.label for row in rows if row.summary is None]
    if len(traded) == 1:
        text = (
            f"Only {best.label} has closed trades: {format_r(best.summary.total_r)} from "
            f"{plural(best.summary.trades, 'trade')}."
        )
    else:
        text = (
            f"{plural(len(traded), noun)} with closed trades. Best: {best.label} "
            f"({format_r(best.summary.total_r)} from {plural(best.summary.trades, 'trade')}). "
            f"Worst: {worst.label} ({format_r(worst.summary.total_r)} from "
            f"{plural(worst.summary.trades, 'trade')})."
        )
    if empty:
        text += f" No closed trades: {', '.join(empty)}."
    return text


def describe_groups(rows: list[GroupRow], noun: str) -> str:
    if not rows:
        return f"No {noun} has closed trades."
    best = max(rows, key=lambda row: row.summary.total_r)
    worst = min(rows, key=lambda row: row.summary.total_r)
    small = sum(row.small_sample for row in rows)
    if len(rows) == 1:
        text = (
            f"Only one {noun}, {best.label}: {format_r(best.summary.total_r)} from "
            f"{plural(best.summary.trades, 'trade')}."
        )
    else:
        text = (
            f"{plural(len(rows), noun)} compared by total realised R. Best: {best.label} "
            f"({format_r(best.summary.total_r)}, {plural(best.summary.trades, 'trade')}). "
            f"Worst: {worst.label} ({format_r(worst.summary.total_r)}, "
            f"{plural(worst.summary.trades, 'trade')})."
        )
    if small:
        text += (
            f" {plural(small, noun)} {'has' if small == 1 else 'have'} fewer than "
            f"{SMALL_SAMPLE} trades: treat {'it' if small == 1 else 'them'} as a small sample."
        )
    return text


# ---------- Assembly ----------


def make_dashboard(report: PerformanceReport, filters: ReportFilters) -> Dashboard:
    """Pure: everything the page needs from one report."""
    series = drawdown_series(report.cumulative_r)
    months = month_rows(report.by_month)
    weekdays = weekday_rows(report.by_weekday)
    instruments = group_rows(report.by_instrument)
    strategies = group_rows(report.by_strategy)
    sessions = group_rows(report.by_session, order=SESSION_ORDER, labels=SESSION_LABELS)
    return Dashboard(
        filters=filters,
        report=report,
        kpis=build_kpis(report),
        series=series,
        months=months,
        weekdays=weekdays,
        instruments=instruments,
        strategies=strategies,
        sessions=sessions,
        descriptions={
            "curve": describe_curve(series),
            "drawdown": describe_drawdown(series, report),
            "monthly": describe_periods(months, "month"),
            "weekday": describe_periods(weekdays, "weekday"),
            "instruments": describe_groups(instruments, "instrument"),
            "strategies": describe_groups(strategies, "strategy"),
            "sessions": describe_groups(sessions, "session"),
        },
    )


def build_dashboard(conn, filters: ReportFilters) -> Dashboard:
    """One database query and one report per page view; every card, chart
    and table below is cut from the same report."""
    return make_dashboard(build_report(conn, **filters.as_kwargs()), filters)


# ---------- Chart payload (JSON-safe) ----------


def exact(value: Decimal | None) -> str | None:
    """Exact decimal text for the chart, or None (JSON null) when absent."""
    return None if value is None else decimal_to_text(value)


def _summary_fields(summary: Summary | None) -> dict[str, object]:
    if summary is None:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "total_r": None,
            "total_r_text": "No closed trades",
            "win_rate_text": NOT_AVAILABLE,
            "average_r_text": NOT_AVAILABLE,
            "expectancy_text": NOT_AVAILABLE,
        }
    return {
        "trades": summary.trades,
        "wins": summary.wins,
        "losses": summary.losses,
        "total_r": exact(summary.total_r),
        "total_r_text": format_r(summary.total_r),
        "win_rate_text": format_rate(summary.win_rate),
        "average_r_text": format_r(summary.average_r),
        "expectancy_text": format_r(summary.expectancy_r),
    }


def chart_payload(dashboard: Dashboard) -> dict[str, object]:
    """Everything analytics.js needs, and nothing else: no notes, no prices,
    no identifiers beyond trade numbers. Exact values are strings; the
    *_text values are what people read."""
    curve = []
    if dashboard.series:
        first = dashboard.series[0]
        curve.append(
            {
                "start": True,
                "x": first.x,
                "cumulative_r": "0",
                "cumulative_text": "0.00R",
                "underwater_r": "0",
                "drawdown_text": "0.00R",
            }
        )
    for point in dashboard.series:
        with localcontext(EXACT):
            underwater = -point.drawdown_r
        curve.append(
            {
                "start": False,
                "trade_id": point.trade_id,
                "symbol": point.symbol,
                "x": point.x,
                "when": short_time(point.realised_at),
                "close_time_known": point.close_time_known,
                "realised_r": exact(point.realised_r),
                "realised_text": format_r(point.realised_r),
                "cumulative_r": exact(point.cumulative_r),
                "cumulative_text": format_r(point.cumulative_r),
                "underwater_r": exact(underwater),
                "drawdown_text": format_r_amount(point.drawdown_r),
            }
        )

    def periods(rows):
        return [{"key": row.key, "label": row.label, **_summary_fields(row.summary)} for row in rows]

    def groups(rows):
        return [
            {"label": row.label, "small_sample": row.small_sample, **_summary_fields(row.summary)}
            for row in rows
        ]

    worst = dashboard.report.overall.max_drawdown_r
    return {
        "curve": curve,
        "max_drawdown": {
            "amount": exact(worst.amount),
            "text": format_r_amount(worst.amount),
            "peak_trade_id": worst.peak_trade_id,
            "trough_trade_id": worst.trough_trade_id,
        },
        "monthly": periods(dashboard.months),
        "weekday": periods(dashboard.weekdays),
        "instruments": groups(dashboard.instruments),
        "strategies": groups(dashboard.strategies),
        "sessions": groups(dashboard.sessions),
    }


def money_rows(report: PerformanceReport):
    """(currency, net P&L text, tone, trades) per quote currency. Never summed
    across currencies: there is no exchange-rate data to do that honestly."""
    return [
        (currency, format_money(m.net_pnl, currency), tone(m.net_pnl), m.trades)
        for currency, m in report.overall.money.items()
    ]
```

### `tradeledger/performance/report.py` (MODIFIED)

**Why:** parse_stored for weekdays; zip(strict=True).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Build a complete PerformanceReport.

analyse(trades)          pure: list of trade dicts in, report out
build_report(conn, ...)  loads trades from the database, then calls analyse()

Grouping keys (all from realised_at, which is UTC)
  by_weekday   'Monday' ... 'Sunday', in calendar order
  by_date      'YYYY-MM-DD'
  by_month     'YYYY-MM'

realised_at is the exit time (closed_at). For legacy trades recorded before
Phase 4, whose exit time was never captured, it falls back to the entry
time. Those trades are listed in unknown_close_time_trade_ids so the
fallback is always visible, never silent.

Date filters (start/end) apply to the same realised date, so "March" means
"results realised in March".
"""

from dataclasses import dataclass
from decimal import Decimal

from ..repositories.trades import list_trades
from ..timestamps import parse_stored
from .metrics import Summary, chronological, cumulative, summarise
from .trade_math import IncompleteTradeError, TradeResult, evaluate_trade

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
UNSPECIFIED = "Unspecified"


@dataclass(frozen=True)
class ExcludedTrade:
    trade_id: int | None
    reason: str


@dataclass(frozen=True)
class CurvePoint:
    """One point on the cumulative-R chart. This is NOT account equity."""

    trade_id: int
    trade_date: str
    realised_at: str
    close_time_known: bool
    symbol: str
    realised_r: Decimal
    cumulative_r: Decimal


@dataclass(frozen=True)
class PerformanceReport:
    overall: Summary
    open_trades: int
    excluded: list[ExcludedTrade]
    r_unavailable: list[ExcludedTrade]  # closed trades counted, but with no R
    unknown_close_time_trade_ids: list[int]  # legacy: ordered by entry time
    by_instrument: dict[str, Summary]
    by_strategy: dict[str, Summary]
    by_session: dict[str, Summary]
    by_weekday: dict[str, Summary]
    by_date: dict[str, Summary]
    by_month: dict[str, Summary]
    cumulative_r: list[CurvePoint]


def _weekday(result):
    moment = parse_stored(result.realised_at)
    return WEEKDAYS[moment.weekday()]


def evaluate_trades(trades):
    """Split trades into evaluated results, an open count and exclusions."""
    results, excluded, open_count = [], [], 0
    for trade in trades:
        if trade.get("status") == "OPEN":
            open_count += 1
            continue
        try:
            results.append(evaluate_trade(trade))
        except IncompleteTradeError as exc:
            excluded.append(ExcludedTrade(trade.get("id"), str(exc)))
    return results, open_count, excluded


def group_summaries(results, key, order=None):
    """Summarise each group. `order` fixes the key order (e.g. weekdays);
    otherwise keys are sorted alphabetically."""
    groups: dict[str, list[TradeResult]] = {}
    for result in results:
        groups.setdefault(key(result), []).append(result)
    keys = [k for k in order if k in groups] if order else sorted(groups)
    return {k: summarise(groups[k]) for k in keys}


def cumulative_r_curve(results):
    """Chart-ready running total of realised R, in date order."""
    rows = [r for r in chronological(results) if r.realised_r is not None]
    totals = cumulative([r.realised_r for r in rows])
    return [
        CurvePoint(r.trade_id, r.trade_date, r.realised_at, r.close_time_known, r.symbol, r.realised_r, total)
        for r, total in zip(rows, totals, strict=True)
    ]


def analyse(trades) -> PerformanceReport:
    results, open_count, excluded = evaluate_trades(trades)
    return PerformanceReport(
        overall=summarise(results),
        open_trades=open_count,
        excluded=excluded,
        r_unavailable=[
            ExcludedTrade(r.trade_id, r.r_unavailable_reason)
            for r in chronological(results)
            if r.realised_r is None
        ],
        unknown_close_time_trade_ids=[r.trade_id for r in chronological(results) if not r.close_time_known],
        by_instrument=group_summaries(results, lambda r: r.symbol),
        by_strategy=group_summaries(results, lambda r: r.strategy or UNSPECIFIED),
        by_session=group_summaries(results, lambda r: r.session or UNSPECIFIED),
        by_weekday=group_summaries(results, _weekday, order=WEEKDAYS),
        by_date=group_summaries(results, lambda r: r.realised_at[:10]),
        by_month=group_summaries(results, lambda r: r.realised_at[:7]),
        cumulative_r=cumulative_r_curve(results),
    )


def build_report(
    conn, *, instrument_id=None, start=None, end=None, strategy=None, session=None
) -> PerformanceReport:
    """Load trades with the repository filters and analyse them.

    start/end are inclusive UTC dates applied to the realised date.
    strategy and session are exact matches (Phase 6). Every filter is a
    parameterised SQL condition in the repository, so filtering happens in
    the database and only matching trades are loaded.
    """
    return analyse(
        list_trades(
            conn,
            instrument_id=instrument_id,
            start=start,
            end=end,
            strategy=strategy,
            session=session,
            date_field="realised",
        )
    )
```

### `tradeledger/performance/trade_math.py` (MODIFIED)

**Why:** Overflow guard: decimal errors become an excluded trade (F1); parse_stored instead of strptime.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Per-trade calculations.

Formulas (all amounts in the instrument's quote currency)
---------------------------------------------------------
  direction sign   LONG = +1, SHORT = -1
  price move       sign x (exit - entry)
  gross P&L        price move x position_size x contract_multiplier
  net P&L          gross P&L - fees
  risk per unit    sign x (entry - stop_loss)        must be > 0
  initial risk     risk per unit x position_size x contract_multiplier
  realised R       net P&L / initial risk
  planned R:R      sign x (take_profit - entry) / risk per unit
  outcome          WIN if net P&L > 0, LOSS if < 0, BREAKEVEN if exactly 0

Conventions
  * Initial risk EXCLUDES fees: it is the money lost if price reaches the
    stop. Fees then reduce net P&L, so a stopped-out trade with fees is
    slightly worse than -1R (e.g. -1.035R). This is intentional: it shows
    the real cost of trading.
  * Planned R:R is price-based and ignores fees. It describes the plan.
  * No rounding happens here. Rounding is for display only (formatting.py).

Timing (Phase 4)
  * trade_date is the entry time; closed_at is the exit time.
  * realised_at is when the result is treated as realised: closed_at when
    it was recorded, otherwise trade_date. Only legacy trades recorded
    before Phase 4 lack closed_at; they are flagged close_time_known=False
    so reports can say so. No exit time is ever invented or stored.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import (
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)

from ..timestamps import parse_stored

# Products of three 15-digit inputs need up to 45 digits; 60 keeps them
# exact. Only divisions (R, ratios) are rounded, at the 60th digit.
EXACT = Context(prec=60, traps=[InvalidOperation, DivisionByZero, Overflow])

ZERO = Decimal("0")
SIGN = {"LONG": 1, "SHORT": -1}


class IncompleteTradeError(Exception):
    """The trade cannot be evaluated (open, missing or invalid data)."""


@dataclass(frozen=True)
class TradeResult:
    """Everything calculated for one closed trade."""

    trade_id: int
    symbol: str
    quote_currency: str
    trade_date: str  # entry time
    closed_at: str | None  # exit time; None = not recorded (legacy)
    realised_at: str  # closed_at, or trade_date if unknown
    close_time_known: bool
    direction: str
    strategy: str | None
    session: str | None
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    outcome: str
    initial_risk: Decimal | None  # None when the risk is invalid
    realised_r: Decimal | None  # None when the risk is invalid
    planned_rr: Decimal | None  # None without a valid target
    r_unavailable_reason: str | None  # why realised_r is None


# ---------- Small pure functions (each tested on its own) ----------


def price_move(direction, entry, exit_price):
    with localcontext(EXACT):
        return SIGN[direction] * (exit_price - entry)


def gross_pnl(direction, entry, exit_price, size, multiplier):
    with localcontext(EXACT):
        return price_move(direction, entry, exit_price) * size * multiplier


def net_pnl(gross, fees):
    with localcontext(EXACT):
        return gross - fees


def risk_per_unit(direction, entry, stop):
    """Positive when the stop is on the losing side of entry."""
    with localcontext(EXACT):
        return SIGN[direction] * (entry - stop)


def initial_risk(direction, entry, stop, size, multiplier):
    """Money at risk if the stop is hit, or None if the stop is invalid."""
    per_unit = risk_per_unit(direction, entry, stop)
    if per_unit <= 0:
        return None
    with localcontext(EXACT):
        return per_unit * size * multiplier


def realised_r(net, risk):
    """Net P&L expressed in units of initial risk, or None if risk is unusable."""
    if risk is None or risk <= 0:
        return None
    with localcontext(EXACT):
        return net / risk


def planned_rr(direction, entry, stop, target):
    """Planned reward divided by planned risk, or None if not calculable."""
    if target is None:
        return None
    risk = risk_per_unit(direction, entry, stop)
    with localcontext(EXACT):
        reward = SIGN[direction] * (target - entry)
    if risk <= 0 or reward <= 0:
        return None
    with localcontext(EXACT):
        return reward / risk


def classify(net):
    if net > 0:
        return "WIN"
    if net < 0:
        return "LOSS"
    return "BREAKEVEN"


# ---------- Input checking ----------


def _decimal(trade, field, *, required=True):
    """Read a field as Decimal. Floats are refused: they may already be inexact."""
    value = trade.get(field)
    if value is None:
        if required:
            raise IncompleteTradeError(f"missing {field}")
        return None
    if isinstance(value, (float, bool)):
        raise IncompleteTradeError(f"{field} must be Decimal, int or str, not {type(value).__name__}")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise IncompleteTradeError(f"{field} is not a number") from None
    if not number.is_finite():
        raise IncompleteTradeError(f"{field} is not a finite number")
    return number


# ---------- One trade ----------


def evaluate_trade(trade: Mapping) -> TradeResult:
    """Calculate a TradeResult for one CLOSED trade.

    `trade` is a mapping such as a row from repositories.trades.list_trades():
    it needs id, symbol, quote_currency, trade_date, direction, status,
    entry_price, stop_loss, exit_price, position_size, fees,
    contract_multiplier, and optionally closed_at, take_profit, strategy,
    session.

    Raises IncompleteTradeError for open trades and for data that cannot
    produce a trustworthy P&L. A trade whose P&L is known but whose risk is
    invalid is NOT an error: it gets realised_r = None plus a reason.
    """
    if trade.get("status") != "CLOSED":
        raise IncompleteTradeError("trade is not closed, so it has no realised result")
    direction = trade.get("direction")
    if direction not in SIGN:
        raise IncompleteTradeError("direction must be LONG or SHORT")
    for field in ("symbol", "quote_currency", "trade_date"):
        if not trade.get(field):
            raise IncompleteTradeError(f"missing {field}")
    try:
        parse_stored(trade["trade_date"])
    except (TypeError, ValueError):
        raise IncompleteTradeError("trade_date must look like 2026-03-02T08:15:00Z") from None
    closed_at = trade.get("closed_at")
    if closed_at is not None:
        try:
            parse_stored(closed_at)
        except (TypeError, ValueError):
            raise IncompleteTradeError("closed_at must look like 2026-03-02T08:15:00Z") from None
        if closed_at < trade["trade_date"]:
            raise IncompleteTradeError("closed_at is earlier than trade_date")

    with localcontext(EXACT):
        entry = _decimal(trade, "entry_price")
        stop = _decimal(trade, "stop_loss")
        exit_price = _decimal(trade, "exit_price")
        size = _decimal(trade, "position_size")
        multiplier = _decimal(trade, "contract_multiplier")
        target = _decimal(trade, "take_profit", required=False)
        fees = _decimal(trade, "fees", required=False) or ZERO

        if size <= 0:
            raise IncompleteTradeError("position_size must be greater than zero")
        if multiplier <= 0:
            raise IncompleteTradeError("contract_multiplier must be greater than zero")
        if fees < 0:
            raise IncompleteTradeError("fees must not be negative")

        # Validation keeps stored values small enough for exact arithmetic
        # (Phase 7). This guard is defence in depth: a row that is out of
        # range anyway (older data, a manual SQL edit) is reported as an
        # excluded trade instead of breaking every page that shows it.
        try:
            gross = gross_pnl(direction, entry, exit_price, size, multiplier)
            net = net_pnl(gross, fees)
            risk = initial_risk(direction, entry, stop, size, multiplier)
            r_value = realised_r(net, risk)
        except DecimalException:
            raise IncompleteTradeError("values are too large to calculate exactly") from None

    reason = None
    if r_value is None:
        reason = "stop-loss is not on the losing side of entry, so initial risk is not positive"

    return TradeResult(
        trade_id=trade.get("id"),
        symbol=trade["symbol"],
        quote_currency=trade["quote_currency"],
        trade_date=trade["trade_date"],
        closed_at=closed_at,
        realised_at=closed_at or trade["trade_date"],
        close_time_known=closed_at is not None,
        direction=direction,
        strategy=trade.get("strategy"),
        session=trade.get("session"),
        gross_pnl=gross,
        fees=fees,
        net_pnl=net,
        outcome=classify(net),
        initial_risk=risk,
        realised_r=r_value,
        planned_rr=planned_rr(direction, entry, stop, target),
        r_unavailable_reason=reason,
    )
```

### `tradeledger/repositories/trades.py` (MODIFIED)

**Why:** UPDATE SQL built in one reviewed line; noqa reasons on reviewed SQL (no behaviour change).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Trade repository.

Return values
  create_trade   -> new id (int)
  get_trade      -> dict (includes the instrument's symbol, quote_currency
                    and contract_multiplier), or None
  list_trades    -> list of dicts, newest trade_date first (empty list if none)
  count_trades   -> number of trades matching the same filters as list_trades
  list_strategies-> sorted list of distinct strategy labels in use
  update_trade   -> True if a trade was updated, False if the id does not exist
  delete_trade   -> True if a trade was deleted, False if the id does not exist

Errors
  ValidationError  invalid input, including an unknown instrument_id
                   (nothing is written)

Numeric fields come back as decimal.Decimal (or None), never float.

Dates
  trade_date  entry time (always present)
  closed_at   exit time; None for OPEN trades and for legacy CLOSED trades
              recorded before Phase 4, whose exit time is unknown
"""

import sqlite3
from decimal import Decimal

from ..validation import SESSIONS, ValidationError, date_range_bounds, validate_trade

_DECIMAL_FIELDS = (
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "contract_multiplier",
)

# The ONLY columns a caller can write. Anything else in the input is ignored
# by validate_trade() and never reaches SQL (protection against mass assignment).
_WRITE_FIELDS = (
    "instrument_id",
    "trade_date",
    "closed_at",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "status",
    "strategy",
    "session",
    "notes",
)

_SELECT = """
    SELECT t.id, t.instrument_id, i.symbol, i.quote_currency, i.contract_multiplier,
           t.trade_date, t.closed_at, t.direction,
           t.entry_price, t.stop_loss, t.take_profit, t.exit_price,
           t.position_size, t.fees, t.status, t.strategy, t.session,
           t.notes, t.created_at, t.updated_at
    FROM trades AS t
    JOIN instruments AS i ON i.id = t.instrument_id
"""

# Which timestamp a date filter applies to. Both are fixed SQL text written
# here, never user input.
#   entry     the trade's entry time
#   realised  when the result was realised: the exit time if recorded,
#             otherwise (legacy trades) the entry time
_DATE_COLUMNS = {
    "entry": "t.trade_date",
    "realised": "COALESCE(t.closed_at, t.trade_date)",
}


def _to_dict(row):
    if row is None:
        return None
    record = dict(row)
    for field in _DECIMAL_FIELDS:
        if record[field] is not None:
            record[field] = Decimal(record[field])
    return record


def _raise_friendly(exc):
    """Translate SQLite integrity errors into ValidationErrors where we can."""
    message = str(exc)
    if "FOREIGN KEY" in message:
        raise ValidationError({"instrument_id": "does not match an existing instrument"}) from exc
    if "closed_at_rules" in message:
        raise ValidationError(
            {"closed_at": "is not consistent with the trade's status or entry time"}
        ) from exc
    raise exc


MAX_SEARCH_LENGTH = 100


def _like_pattern(text):
    """Turn user text into a LIKE pattern that matches it literally.
    % and _ are LIKE wildcards, so they (and the escape character itself)
    are escaped; otherwise searching "50%" would match everything."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(instrument_id, start, end, status, date_field, strategy=None, search=None, session=None):
    """Build a WHERE clause from fixed fragments plus a parameter list.
    User values never become part of the SQL text; they go in `params`.

    strategy  exact match on the strategy label
    session   exact match on the trading session (ASIA, LONDON, NEW_YORK, OTHER)
    search    case-insensitive substring match on the instrument symbol,
              the strategy or the notes, across the WHOLE table (not just
              one page). SQLite's LIKE ignores case for ASCII letters only.
    """
    if date_field not in _DATE_COLUMNS:
        raise ValueError(f"date_field must be one of {sorted(_DATE_COLUMNS)}")
    date_column = _DATE_COLUMNS[date_field]
    lower, upper = date_range_bounds(start, end)

    conditions, params = [], []
    if instrument_id is not None:
        conditions.append("t.instrument_id = ?")
        params.append(instrument_id)
    if lower is not None:
        conditions.append(f"{date_column} >= ?")
        params.append(lower)
    if upper is not None:
        conditions.append(f"{date_column} < ?")
        params.append(upper)
    if status is not None:
        if status not in ("OPEN", "CLOSED"):
            raise ValidationError({"status": "must be one of: OPEN, CLOSED"})
        conditions.append("t.status = ?")
        params.append(status)
    if strategy is not None:
        conditions.append("t.strategy = ?")
        params.append(strategy)
    if session is not None:
        if session not in SESSIONS:
            raise ValidationError({"session": f"must be one of: {', '.join(SESSIONS)}"})
        conditions.append("t.session = ?")
        params.append(session)
    if search is not None:
        if not isinstance(search, str) or not search.strip():
            raise ValidationError({"q": "must not be empty"})
        if len(search) > MAX_SEARCH_LENGTH:
            raise ValidationError({"q": f"must be {MAX_SEARCH_LENGTH} characters or fewer"})
        pattern = _like_pattern(search.strip())
        conditions.append(
            "(i.symbol LIKE ? ESCAPE '\\' OR t.strategy LIKE ? ESCAPE '\\' OR t.notes LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern, pattern, pattern])
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


def create_trade(conn, data):
    clean = validate_trade(data)
    placeholders = ", ".join("?" for _ in _WRITE_FIELDS)
    try:
        with conn:
            cursor = conn.execute(
                f"INSERT INTO trades ({', '.join(_WRITE_FIELDS)}) VALUES ({placeholders})",  # noqa: S608 - fixed column names; values are bound
                tuple(clean[field] for field in _WRITE_FIELDS),
            )
    except sqlite3.IntegrityError as exc:
        _raise_friendly(exc)
    return cursor.lastrowid


def get_trade(conn, trade_id):
    row = conn.execute(_SELECT + " WHERE t.id = ?", (trade_id,)).fetchone()
    return _to_dict(row)


def list_trades(
    conn,
    *,
    instrument_id=None,
    start=None,
    end=None,
    status=None,
    date_field="entry",
    strategy=None,
    search=None,
    session=None,
    limit=None,
    offset=0,
):
    """List trades, optionally filtered and paginated.

    instrument_id  only this instrument
    start, end     inclusive dates 'YYYY-MM-DD' (UTC)
    date_field     'entry' (trade_date, the default) or 'realised'
    status         'OPEN' or 'CLOSED'
    strategy       exact strategy label
    session        exact session (ASIA, LONDON, NEW_YORK, OTHER)
    search         text found in the symbol, strategy or notes
    limit, offset  pagination; limit=None returns every match

    Order is always newest trade_date first, then highest id first, so
    pages never overlap or skip rows between requests.
    """
    where, params = _where(instrument_id, start, end, status, date_field, strategy, search, session)
    sql = _SELECT + where + " ORDER BY t.trade_date DESC, t.id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, int(limit), int(offset)]
    return [_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def count_trades(
    conn,
    *,
    instrument_id=None,
    start=None,
    end=None,
    status=None,
    date_field="entry",
    strategy=None,
    search=None,
    session=None,
):
    where, params = _where(instrument_id, start, end, status, date_field, strategy, search, session)
    # The JOIN is needed because search can match the instrument symbol.
    sql = "SELECT COUNT(*) FROM trades AS t JOIN instruments AS i ON i.id = t.instrument_id" + where  # noqa: S608 - `where` holds fixed fragments; values are bound
    return conn.execute(sql, params).fetchone()[0]


def list_strategies(conn):
    rows = conn.execute(
        "SELECT DISTINCT strategy FROM trades WHERE strategy IS NOT NULL ORDER BY strategy"
    ).fetchall()
    return [row["strategy"] for row in rows]


def update_trade(conn, trade_id, data):
    """Replace every editable field of a trade with validated new values.

    A legacy CLOSED trade (recorded before Phase 4, closed_at unknown) may be
    saved again without a closed_at, so editing it never forces anyone to
    invent an exit time. Every other CLOSED trade must have one.
    """
    existing = conn.execute("SELECT status, closed_at FROM trades WHERE id = ?", (trade_id,)).fetchone()
    if existing is None:
        return False
    is_legacy = existing["status"] == "CLOSED" and existing["closed_at"] is None
    clean = validate_trade(data, require_closed_at=not is_legacy)

    # Column names come from _WRITE_FIELDS (a constant in this module), never
    # from the request; every value is a bound "?" parameter.
    assignments = ", ".join(f"{field} = ?" for field in _WRITE_FIELDS)
    sql = (
        f"UPDATE trades SET {assignments}, "  # noqa: S608 - see comment above
        "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?"
    )
    try:
        with conn:
            cursor = conn.execute(sql, (*(clean[field] for field in _WRITE_FIELDS), trade_id))
    except sqlite3.IntegrityError as exc:
        _raise_friendly(exc)
    return cursor.rowcount == 1


def delete_trade(conn, trade_id):
    with conn:
        cursor = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    return cursor.rowcount == 1
```

### `tradeledger/static/js/analytics.js` (MODIFIED)

**Why:** Fallback message no longer names the CDN (the file may be self-hosted).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```javascript
// TradeLedger analytics dashboard: draws the seven charts with Chart.js.
//
// This file is PRESENTATION ONLY. It performs no financial calculation:
//   * every figure arrives from Python in the page's #dashboard-data JSON,
//     as an exact decimal string (e.g. "7.688173...") plus display text
//     (e.g. "+7.69R");
//   * the only conversion is toNumber(), which turns an exact string into
//     a JavaScript number to POSITION a point or bar. Tooltips show the
//     Python-formatted text, never a number computed here.
//
// If Chart.js cannot be loaded (offline, CDN blocked) or JavaScript is off,
// the page still shows every figure in its KPI cards, text descriptions and
// tables; each chart area explains why it is empty.
//
// The pure functions (toNumber, buildConfigs, ...) are exported for the
// Node.js tests in tests/js/analytics.test.js.
(function (root, factory) {
    "use strict";
    var api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    } else {
        root.TradeLedgerCharts = api;
    }
    if (typeof document !== "undefined") {
        document.addEventListener("DOMContentLoaded", function () {
            api.instances = api.init(document, root.Chart);
        });
    }
}(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    var UNAVAILABLE_MESSAGE =
        "Chart unavailable: the Chart.js library could not be loaded (from the CDN it needs an " +
        "internet connection). The table below has the same figures.";
    var FAILED_MESSAGE = "This chart could not be drawn. The table below has the same figures.";

    // ---------- Small pure helpers ----------

    // Exact decimal string -> chart number. Absent, non-numeric or
    // non-finite values become null (Chart.js leaves a gap), never NaN or
    // Infinity.
    function toNumber(text) {
        if (text === null || text === undefined || text === "") {
            return null;
        }
        var value = Number(text);
        return Number.isFinite(value) ? value : null;
    }

    // "#4cd79a" + 0.2 -> "rgba(76, 215, 154, 0.2)" for translucent fills.
    function hexToRgba(hex, alpha) {
        var match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex).trim());
        if (!match) {
            return hex;
        }
        return "rgba(" + parseInt(match[1], 16) + ", " + parseInt(match[2], 16) + ", " +
            parseInt(match[3], 16) + ", " + alpha + ")";
    }

    // Axis label for a UTC time in milliseconds: "2026-03-02".
    function formatAxisDate(ms) {
        var date = new Date(ms);
        return isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 10);
    }

    function signColour(value, theme) {
        if (value === null || value === 0) {
            return theme.muted;
        }
        return value > 0 ? theme.positive : theme.negative;
    }

    // Grid lines, with a stronger line at zero so gains and losses are
    // separated by a clear reference.
    function valueAxis(theme, extra) {
        var axis = {
            grid: {
                color: function (context) {
                    return context.tick && context.tick.value === 0 ? theme.zero : theme.grid;
                },
                lineWidth: function (context) {
                    return context.tick && context.tick.value === 0 ? 2 : 1;
                }
            },
            ticks: {
                callback: function (value) {
                    return value + "R";
                }
            },
            title: {display: true, text: "R (risk multiples)"}
        };
        return Object.assign(axis, extra || {});
    }

    function baseOptions() {
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,                      // no decorative motion
            plugins: {
                legend: {display: false},          // one series per chart; the caption explains it
                tooltip: {}
            }
        };
    }

    // ---------- Line charts: cumulative R and drawdown ----------

    function curveTooltipLines(point) {
        if (point.start) {
            return ["Starting point: " + point.cumulative_text];
        }
        var lines = [
            "Trade #" + point.trade_id + " · " + point.symbol,
            "Trade R: " + point.realised_text,
            "Cumulative R: " + point.cumulative_text
        ];
        if (!point.close_time_known) {
            lines.push("Exit time not recorded: placed at entry time");
        }
        return lines;
    }

    function timeAxis() {
        return {
            type: "linear",
            ticks: {
                maxTicksLimit: 6,
                callback: function (value) {
                    return formatAxisDate(value);
                }
            },
            title: {display: true, text: "Realised date (UTC)"}
        };
    }

    function buildCurveConfig(points, theme) {
        var options = baseOptions();
        options.interaction = {mode: "nearest", intersect: false, axis: "x"};
        options.scales = {x: timeAxis(), y: valueAxis(theme)};
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                var point = points[items[0].dataIndex];
                return point.start ? "Before the first trade" : point.when;
            },
            label: function (item) {
                return curveTooltipLines(points[item.dataIndex]);
            }
        };
        return {
            type: "line",
            data: {
                datasets: [{
                    label: "Cumulative realised R",
                    data: points.map(function (p) {
                        return {x: p.x, y: toNumber(p.cumulative_r)};
                    }),
                    borderColor: theme.accent,
                    backgroundColor: theme.accent,
                    borderWidth: 2,
                    pointRadius: points.length > 60 ? 0 : 3,
                    pointHoverRadius: 5,
                    tension: 0
                }]
            },
            options: options
        };
    }

    function buildDrawdownConfig(points, maxDrawdown, theme) {
        var troughId = maxDrawdown ? maxDrawdown.trough_trade_id : null;
        var isTrough = function (p) {
            return !p.start && troughId !== null && p.trade_id === troughId;
        };
        var options = baseOptions();
        options.interaction = {mode: "nearest", intersect: false, axis: "x"};
        options.scales = {
            x: timeAxis(),
            y: valueAxis(theme, {suggestedMax: 0})
        };
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                var point = points[items[0].dataIndex];
                return point.start ? "Before the first trade" : point.when;
            },
            label: function (item) {
                var point = points[item.dataIndex];
                var lines = ["Drawdown: " + point.drawdown_text + " below the running peak"];
                if (!point.start) {
                    lines.push("Trade #" + point.trade_id + " · " + point.symbol);
                    lines.push("Cumulative R: " + point.cumulative_text);
                }
                if (isTrough(point)) {
                    lines.push("Maximum drawdown: " + maxDrawdown.text);
                }
                return lines;
            }
        };
        return {
            type: "line",
            data: {
                datasets: [{
                    label: "Drawdown (R below the running peak)",
                    data: points.map(function (p) {
                        return {x: p.x, y: toNumber(p.underwater_r)};
                    }),
                    borderColor: theme.negative,
                    backgroundColor: hexToRgba(theme.negative, 0.18),
                    fill: "origin",
                    borderWidth: 2,
                    tension: 0,
                    pointRadius: points.map(function (p) {
                        return isTrough(p) ? 6 : (points.length > 60 ? 0 : 2);
                    }),
                    pointStyle: points.map(function (p) {
                        return isTrough(p) ? "triangle" : "circle";
                    }),
                    pointBackgroundColor: points.map(function (p) {
                        return isTrough(p) ? theme.text : theme.negative;
                    })
                }]
            },
            options: options
        };
    }

    // ---------- Bar charts: months, weekdays, groups ----------

    function barTooltipLines(row) {
        if (row.total_r === null) {
            return ["No closed trades"];
        }
        var lines = [
            "Total R: " + row.total_r_text,
            "Closed trades: " + row.trades + " (won " + row.wins + ", lost " + row.losses + ")",
            "Win rate: " + row.win_rate_text,
            "Average R: " + row.average_r_text,
            "Expectancy: " + row.expectancy_text
        ];
        if (row.small_sample) {
            lines.push("Small sample: treat with caution");
        }
        return lines;
    }

    function buildBarConfig(rows, theme, settings) {
        var horizontal = Boolean(settings.horizontal);
        var values = rows.map(function (row) {
            return toNumber(row.total_r);
        });
        var options = baseOptions();
        if (horizontal) {
            options.indexAxis = "y";
            options.scales = {x: valueAxis(theme), y: {grid: {display: false}}};
        } else {
            options.scales = {x: {grid: {display: false}}, y: valueAxis(theme)};
        }
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                return rows[items[0].dataIndex].label;
            },
            label: function (item) {
                return barTooltipLines(rows[item.dataIndex]);
            }
        };
        return {
            type: "bar",
            data: {
                labels: rows.map(function (row) {
                    return row.small_sample ? row.label + " *" : row.label;
                }),
                datasets: [{
                    label: settings.label,
                    data: values,
                    backgroundColor: values.map(function (value) {
                        return signColour(value, theme);
                    }),
                    borderRadius: 3,
                    maxBarThickness: 36
                }]
            },
            options: options
        };
    }

    // Every chart's configuration from the payload. Pure: no DOM, no Chart.
    function buildConfigs(payload, theme) {
        return {
            curve: buildCurveConfig(payload.curve, theme),
            drawdown: buildDrawdownConfig(payload.curve, payload.max_drawdown, theme),
            monthly: buildBarConfig(payload.monthly, theme, {label: "Total R by month"}),
            weekday: buildBarConfig(payload.weekday, theme, {label: "Total R by weekday"}),
            instruments: buildBarConfig(payload.instruments, theme,
                {label: "Total R by instrument", horizontal: true}),
            strategies: buildBarConfig(payload.strategies, theme,
                {label: "Total R by strategy", horizontal: true}),
            sessions: buildBarConfig(payload.sessions, theme,
                {label: "Total R by session", horizontal: true})
        };
    }

    // ---------- Browser glue ----------

    // Colours come from the CSS design tokens in base.css, so the charts
    // follow the same theme as the rest of the application.
    function readTheme(doc) {
        var styles = doc.defaultView.getComputedStyle(doc.documentElement);
        var token = function (name) {
            return styles.getPropertyValue(name).trim();
        };
        return {
            text: token("--color-text"),
            muted: token("--color-text-muted"),
            grid: token("--color-border"),
            zero: token("--color-text-subtle"),
            accent: token("--color-accent"),
            positive: token("--color-positive"),
            negative: token("--color-negative"),
            font: token("--font-sans")
        };
    }

    function showFallback(canvas, message) {
        var box = canvas.closest("[data-chart-box]");
        var note = box ? box.querySelector("[data-chart-fallback]") : null;
        canvas.hidden = true;
        if (box) {
            box.classList.add("is-unavailable");
        }
        if (note) {
            note.textContent = message;          // textContent: never parsed as HTML
            note.hidden = false;
        }
    }

    function init(doc, ChartLib) {
        var dataElement = doc.getElementById("dashboard-data");
        var canvases = Array.prototype.slice.call(doc.querySelectorAll("canvas[data-chart]"));
        if (!dataElement || canvases.length === 0) {
            return [];
        }
        if (typeof ChartLib !== "function") {
            canvases.forEach(function (canvas) {
                showFallback(canvas, UNAVAILABLE_MESSAGE);
            });
            return [];
        }
        var theme = readTheme(doc);
        if (ChartLib.defaults) {
            ChartLib.defaults.color = theme.muted;
            ChartLib.defaults.borderColor = theme.grid;
            if (ChartLib.defaults.font) {
                ChartLib.defaults.font.family = theme.font;
            }
        }
        var configs = buildConfigs(JSON.parse(dataElement.textContent), theme);
        var charts = [];
        canvases.forEach(function (canvas) {
            var config = configs[canvas.getAttribute("data-chart")];
            if (!config) {
                return;
            }
            try {
                charts.push(new ChartLib(canvas, config));
            } catch (error) {
                showFallback(canvas, FAILED_MESSAGE);
                if (typeof console !== "undefined") {
                    console.error("TradeLedger chart failed:", canvas.id, error);
                }
            }
        });
        return charts;
    }

    return {
        toNumber: toNumber,
        hexToRgba: hexToRgba,
        formatAxisDate: formatAxisDate,
        curveTooltipLines: curveTooltipLines,
        barTooltipLines: barTooltipLines,
        buildConfigs: buildConfigs,
        init: init,
        instances: []
    };
}));
```

### `tradeledger/static/vendor/chartjs/README.md` (MODIFIED)

**Why:** How to run the real Chart.js tests.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```markdown
# Self-hosted Chart.js (optional)

This folder is intentionally empty apart from this note. Third-party code
lives here, never mixed with TradeLedger's own files in `static/js/`.

By default the dashboard loads Chart.js 4.5.1 from the jsDelivr CDN. To
host it yourself, put `chart.umd.min.js` and `LICENSE.md` from the 4.5.1
release in this folder and set:

    TRADELEDGER_CHART_JS_URL=/static/vendor/chartjs/chart.umd.min.js

Then check the real charts with `python -m pytest -m chartjs_real -ra`
(skipped while this folder has no library file).

Full instructions: `docs/THIRD_PARTY_NOTICES.md`.
```

### `tradeledger/templates/about.html` (MODIFIED)

**Why:** Explains the Host check.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```html
{% extends "base.html" %}

{% block title %}About{% endblock %}
{% block heading %}About TradeLedger{% endblock %}
{% block description %}What this application does, how it calculates, and how it is built.{% endblock %}

{% block content %}
<div class="prose-grid">
    <section class="card prose" aria-labelledby="what-title">
        <h2 id="what-title">What it does</h2>
        <p>TradeLedger is a trading journal. You record each trade's plan (entry, stop-loss, optional target, size)
           and, when it finishes, its exit. It then calculates the result of every closed trade and the performance
           of the whole journal.</p>
        <h3>How results are calculated</h3>
        <ul>
            <li><strong>Net P&amp;L</strong> = price move × position size × contract multiplier − fees, in the instrument's quote currency.</li>
            <li><strong>Realised R</strong> = net P&amp;L ÷ initial risk, where initial risk is the money lost if the stop is hit.</li>
            <li>A trade is a <strong>win</strong> if net P&amp;L is above zero, a <strong>loss</strong> if below, and <strong>breakeven</strong> only at exactly zero.</li>
            <li>Money in different currencies is never added together. R-multiples can be, because they are currency-neutral.</li>
            <li>Every calculation uses exact decimal arithmetic in Python. The browser never calculates results.</li>
        </ul>
    </section>

    <section class="card prose" aria-labelledby="security-title">
        <h2 id="security-title">Security and data</h2>
        <p>This is a <strong>single-user application for your own computer</strong>. It has no accounts or login.</p>
        <ul>
            <li>Run it with <code>flask --app tradeledger run</code>, which listens only on 127.0.0.1.</li>
            <li>In the production configuration the journal pages and the JSON API are switched off unless you deliberately enable them.</li>
            <li>Every form is protected against cross-site request forgery, and pages may only load this site's own scripts and styles. The one exception is the analytics page, which may also load the pinned Chart.js file from its CDN.</li>
            <li>It only answers requests addressed to this computer (<code>localhost</code> or <code>127.0.0.1</code>), so another website cannot reach it through your browser.</li>
            <li>Your trades are stored in one SQLite file on your machine (<code>instance/tradeledger.db</code>).</li>
        </ul>
    </section>

    <section class="card prose" aria-labelledby="build-title">
        <h2 id="build-title">Build progress</h2>
        <ol class="roadmap" role="list">
            {% for item in roadmap %}
            <li class="roadmap-item status-{{ item.status | lower }}">
                <span class="phase-number">Phase {{ item.phase }}</span>
                <span class="phase-title">{{ item.title }}</span>
                <span class="phase-status">{{ item.status }}</span>
            </li>
            {% endfor %}
        </ol>
    </section>

    <section class="card prose" aria-labelledby="tech-title">
        <h2 id="tech-title">Technology</h2>
        <ul>
            <li>Python, Flask and Jinja2 templates, rendered on the server</li>
            <li>SQLite with constraints, triggers and versioned migrations</li>
            <li>HTML, CSS and a small amount of vanilla JavaScript for progressive enhancement</li>
            <li>Chart.js {{ config.CHART_JS_VERSION }} for the analytics charts (drawing only; every figure is calculated in Python)</li>
            <li>A versioned JSON API documented with OpenAPI 3.1 (<a class="text-link" href="{{ url_for('api.openapi_spec') }}">specification</a>)</li>
            <li>pytest, with browser tests in Playwright where available</li>
        </ul>
        <p class="muted">Running in <strong>{{ config.CONFIG_NAME }}</strong> mode · version {{ config.APP_VERSION }}</p>
    </section>
</div>
{% endblock %}
```

### `tradeledger/validation.py` (MODIFIED)

**Why:** Decimal magnitude limits (F1); TIMESTAMP_FORMAT now comes from timestamps.py; merged isinstance checks.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Input validation for instruments and trades.

Each validate_* function takes raw input (a dict of strings or numbers,
e.g. from a web form) and returns a *clean* dict ready to store, or raises
ValidationError listing every problem found.

Python validation is the first line of defence and gives friendly messages.
The CHECK constraints in schema.sql are the last line of defence: they stop
bad data even if some future code forgets to validate.
"""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation

from .timestamps import STORED_FORMAT

ASSET_CLASSES = ("FOREX", "COMMODITY", "INDEX", "STOCK", "OTHER")
DIRECTIONS = ("LONG", "SHORT")
STATUSES = ("OPEN", "CLOSED")
SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "OTHER")

# More significant digits than this cannot be compared exactly by SQLite's
# CAST(... AS REAL) inside the CHECK constraints, so we refuse them.
MAX_SIGNIFICANT_DIGITS = 15

# Magnitude limits (Phase 7 security fix). Counting significant digits is not
# enough on its own: "1e999999" has ONE significant digit, yet it is stored as
# a one-million-digit string and overflows Decimal arithmetic. Real prices,
# sizes, fees and multipliers sit far inside these limits.
MAX_INTEGER_DIGITS = 15
MAX_DECIMAL_PLACES = 15

TIMESTAMP_FORMAT = STORED_FORMAT  # the one stored shape, defined in timestamps.py


class ValidationError(Exception):
    """Raised when input is invalid. .errors maps field name -> message."""

    def __init__(self, errors):
        self.errors = dict(errors)
        summary = "; ".join(f"{field}: {msg}" for field, msg in self.errors.items())
        super().__init__(summary)


# ---------- Single-value helpers ----------


def parse_decimal(value, *, allow_zero=False):
    """Convert input to a Decimal, or raise ValueError with a reason.

    Floats are rejected on purpose: by the time a value is a float, it may
    already be inexact (0.1 is really 0.1000000000000000055...).
    """
    if isinstance(value, (bool, float)):
        raise ValueError("must be given as text or Decimal, not a float")
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            raise ValueError("is required")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("must be a number") from None

    if not number.is_finite():
        raise ValueError("must be a finite number")
    if number < 0 or (number == 0 and not allow_zero):
        raise ValueError("must be zero or more" if allow_zero else "must be greater than zero")
    if number != 0:
        # adjusted() is the power of ten of the leading digit: 123.4 -> 2.
        if number.adjusted() >= MAX_INTEGER_DIGITS:
            raise ValueError(f"must have at most {MAX_INTEGER_DIGITS} digits before the decimal point")
        if -number.normalize().as_tuple().exponent > MAX_DECIMAL_PLACES:
            raise ValueError(f"must have at most {MAX_DECIMAL_PLACES} decimal places")
    if len(number.normalize().as_tuple().digits) > MAX_SIGNIFICANT_DIGITS:
        raise ValueError(f"must have at most {MAX_SIGNIFICANT_DIGITS} significant digits")
    return number


def to_db_decimal(number):
    """Canonical text form for storage: no exponent, no trailing zeros.

    Decimal("1.08500") -> "1.085"    Decimal("100") -> "100"
    """
    text = format(number.normalize(), "f")
    return text


def parse_timestamp(value):
    """Convert a date-time input to a canonical UTC string.

    Accepted: any ISO 8601 date-time Python understands, e.g.
      "2026-03-02T08:15:00Z", "2026-03-02 08:15", "2026-03-02T09:15:00+01:00".
    A value without a time-zone offset is treated as UTC.
    Returned: "2026-03-02T08:15:00Z" (seconds precision, always UTC).
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("is required")
    text = value.strip()
    # A date alone ("2026-03-02") is ambiguous for a trade time; require a time.
    if len(text) <= 10:
        raise ValueError("must include a time, e.g. 2026-03-02T08:15:00Z")
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("must be an ISO 8601 date-time, e.g. 2026-03-02T08:15:00Z") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    moment = moment.astimezone(UTC)
    if moment.year < 1970:
        raise ValueError("must be 1970 or later")
    return moment.strftime(TIMESTAMP_FORMAT)


def parse_date(value):
    """Convert 'YYYY-MM-DD' to a date, or raise ValueError."""
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError("must be a date in YYYY-MM-DD form") from None


def date_range_bounds(start=None, end=None):
    """Turn inclusive dates into UTC timestamp bounds for SQL.

    Returns (lower, upper) where the SQL filter is
        trade_date >= lower AND trade_date < upper
    Either may be None. 'end' is inclusive: the whole of that day counts.
    """
    errors = {}
    start_day = end_day = None
    if start is not None:
        try:
            start_day = parse_date(start)
        except ValueError as exc:
            errors["start"] = str(exc)
    if end is not None:
        try:
            end_day = parse_date(end)
        except ValueError as exc:
            errors["end"] = str(exc)
    if not errors and start_day and end_day and start_day > end_day:
        errors["end"] = "must be on or after the start date"
    if errors:
        raise ValidationError(errors)

    def at_midnight(day):
        return datetime.combine(day, time(0, 0), tzinfo=UTC).strftime(TIMESTAMP_FORMAT)

    lower = at_midnight(start_day) if start_day else None
    upper = at_midnight(end_day + timedelta(days=1)) if end_day else None
    return lower, upper


def _clean_choice(value, choices):
    if not isinstance(value, str) or value.strip().upper() not in choices:
        raise ValueError("must be one of: " + ", ".join(choices))
    return value.strip().upper()


def _clean_optional_text(value, max_length):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("must be text")
    value = value.strip()
    if value == "":
        return None
    if len(value) > max_length:
        raise ValueError(f"must be {max_length} characters or fewer")
    return value


def _is_blank(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


# ---------- Record validators ----------


def validate_instrument(data):
    """Validate instrument input. Returns a clean dict or raises ValidationError."""
    errors, clean = {}, {}

    symbol = data.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        errors["symbol"] = "is required"
    elif len(symbol.strip()) > 20 or not symbol.strip().replace("_", "").isalnum():
        errors["symbol"] = "must be 1-20 letters, digits or underscores"
    else:
        clean["symbol"] = symbol.strip().upper()

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        errors["name"] = "is required"
    elif len(name.strip()) > 100:
        errors["name"] = "must be 100 characters or fewer"
    else:
        clean["name"] = name.strip()

    try:
        clean["asset_class"] = _clean_choice(data.get("asset_class"), ASSET_CLASSES)
    except ValueError as exc:
        errors["asset_class"] = str(exc)

    for field in ("price_increment", "contract_multiplier"):
        try:
            clean[field] = to_db_decimal(parse_decimal(data.get(field)))
        except ValueError as exc:
            errors[field] = str(exc)

    currency = data.get("quote_currency")
    if not isinstance(currency, str) or len(currency.strip()) != 3 or not currency.strip().isalpha():
        errors["quote_currency"] = "must be a 3-letter currency code, e.g. USD"
    else:
        clean["quote_currency"] = currency.strip().upper()

    if errors:
        raise ValidationError(errors)
    return clean


def validate_trade(data, *, require_closed_at=True):
    """Validate trade input. Returns a clean dict or raises ValidationError.

    Rules (Python mirrors and extends the SQL constraints):
      * instrument_id is a positive whole number
      * trade_date (entry time) is a valid date-time, stored as UTC
      * closed_at (exit time) is optional input, stored as UTC, and:
          - must be empty for an OPEN trade
          - must not be earlier than trade_date
          - is REQUIRED for a CLOSED trade when require_closed_at is True
            (the default). The only caller that passes False is
            update_trade() for a legacy trade whose close time was never
            recorded, so editing it does not force anyone to invent one.
      * direction is LONG or SHORT; status is OPEN or CLOSED
      * prices and position size are > 0; fees are >= 0 (default 0)
      * LONG: stop_loss < entry_price; SHORT: stop_loss > entry_price
      * LONG: take_profit > entry_price; SHORT: take_profit < entry_price
      * status omitted -> CLOSED if an exit price is given, otherwise OPEN
      * CLOSED requires exit_price; OPEN must not have one
    """
    errors, clean = {}, {}

    instrument_id = data.get("instrument_id")
    try:
        if isinstance(instrument_id, (bool, float)):
            raise ValueError
        instrument_id = int(str(instrument_id).strip())
        if instrument_id <= 0:
            raise ValueError
        clean["instrument_id"] = instrument_id
    except (TypeError, ValueError):
        errors["instrument_id"] = "must be a positive whole number"

    try:
        clean["trade_date"] = parse_timestamp(data.get("trade_date"))
    except ValueError as exc:
        errors["trade_date"] = str(exc)

    try:
        clean["direction"] = _clean_choice(data.get("direction"), DIRECTIONS)
    except ValueError as exc:
        errors["direction"] = str(exc)

    numbers = {}
    for field in ("entry_price", "stop_loss", "position_size"):
        try:
            numbers[field] = parse_decimal(data.get(field))
        except ValueError as exc:
            errors[field] = str(exc)

    for field in ("take_profit", "exit_price"):
        if _is_blank(data.get(field)):
            numbers[field] = None
        else:
            try:
                numbers[field] = parse_decimal(data.get(field))
            except ValueError as exc:
                errors[field] = str(exc)

    fees = data.get("fees")
    try:
        numbers["fees"] = Decimal("0") if _is_blank(fees) else parse_decimal(fees, allow_zero=True)
    except ValueError as exc:
        errors["fees"] = str(exc)

    # Cross-field rules only make sense when the individual fields are valid.
    direction = clean.get("direction")
    entry = numbers.get("entry_price")
    stop = numbers.get("stop_loss")
    target = numbers.get("take_profit")
    if direction and entry is not None and stop is not None:
        if direction == "LONG" and not stop < entry:
            errors["stop_loss"] = "must be below the entry price for a LONG trade"
        if direction == "SHORT" and not stop > entry:
            errors["stop_loss"] = "must be above the entry price for a SHORT trade"
    if direction and entry is not None and target is not None:
        if direction == "LONG" and not target > entry:
            errors["take_profit"] = "must be above the entry price for a LONG trade"
        if direction == "SHORT" and not target < entry:
            errors["take_profit"] = "must be below the entry price for a SHORT trade"

    status = data.get("status")
    has_exit = numbers.get("exit_price") is not None
    if _is_blank(status):
        if "exit_price" not in errors:
            clean["status"] = "CLOSED" if has_exit else "OPEN"
    else:
        try:
            clean["status"] = _clean_choice(status, STATUSES)
        except ValueError as exc:
            errors["status"] = str(exc)
    if clean.get("status") == "CLOSED" and not has_exit and "exit_price" not in errors:
        errors["exit_price"] = "is required for a CLOSED trade"
    if clean.get("status") == "OPEN" and has_exit:
        errors["exit_price"] = "must be empty for an OPEN trade"

    closed_at = data.get("closed_at")
    if _is_blank(closed_at):
        clean["closed_at"] = None
    else:
        try:
            clean["closed_at"] = parse_timestamp(closed_at)
        except ValueError as exc:
            errors["closed_at"] = str(exc)
    if "closed_at" in clean:
        if clean.get("status") == "OPEN" and clean["closed_at"] is not None:
            errors["closed_at"] = "must be empty for an OPEN trade"
        elif clean.get("status") == "CLOSED" and clean["closed_at"] is None and require_closed_at:
            errors["closed_at"] = "is required for a CLOSED trade"
        elif (
            clean["closed_at"] is not None
            and "trade_date" in clean
            and clean["closed_at"] < clean["trade_date"]
        ):
            errors["closed_at"] = "must not be earlier than trade_date"

    for field, max_length in (("strategy", 50), ("notes", 2000)):
        try:
            clean[field] = _clean_optional_text(data.get(field), max_length)
        except ValueError as exc:
            errors[field] = str(exc)

    session = data.get("session")
    if _is_blank(session):
        clean["session"] = None
    else:
        try:
            clean["session"] = _clean_choice(session, SESSIONS)
        except ValueError as exc:
            errors["session"] = str(exc)

    if errors:
        raise ValidationError(errors)

    for field, number in numbers.items():
        clean[field] = None if number is None else to_db_decimal(number)
    return clean
```

### `tradeledger/web/__init__.py` (MODIFIED)

**Why:** Registers the guard and CSRF hooks explicitly, in order (F6).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""The TradeLedger website: server-rendered pages and HTML forms.

Blueprint "main" (the same name Phase 1 used, so url_for("main.index") and
url_for("main.health") keep working). Modules:

  guard.py        refuses journal pages when JOURNAL_ENABLED is off
  csrf.py         CSRF tokens for every state-changing form
  filters.py      Jinja filters that format exact Decimals for display
  forms.py        turning request.form into input for validation.py
  views.py        overview, about, health
  analytics.py    the /analytics performance dashboard (Phase 6)
  trades.py       journal, add, details, edit, delete
  instruments.py  instrument list and add form

Every write goes through the same repository and validation functions as
the JSON API, so there is one set of business rules, not two.
"""

from flask import Blueprint

main_bp = Blueprint("main", __name__)

# Importing these modules registers their routes and template filters.
from . import analytics, csrf, filters, guard, instruments, trades, views  # noqa: E402,F401

# Request hooks are registered HERE, explicitly, because their order
# matters and must not depend on import order (Phase 7: sorting the import
# line above once swapped them silently; tests/test_web_security.py caught it).
#   1. guard: in production, refuse journal pages with 403 before anything else,
#      so even a POST without a CSRF token gets 403, not a "form expired" 400.
#   2. csrf: check the token on every state-changing request.
main_bp.before_request(guard.refuse_when_disabled)
main_bp.before_request(csrf.check_csrf_token)
```

### `tradeledger/web/csrf.py` (MODIFIED)

**Why:** Decorator removed; registered in web/__init__.py (F6).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Cross-Site Request Forgery (CSRF) protection for HTML forms.

The attack: while you have TradeLedger open, a malicious page you visit
contains a hidden form that posts to http://127.0.0.1:5000/trades/3/delete.
Your browser would happily send it. The defence: every form we render
includes a secret token, and every POST must send it back. Another site
cannot read our pages, so it cannot know the token.

How it works here (the "synchroniser token" pattern):
  1. get_token() creates one random token per browser session and stores
     it in Flask's signed session cookie.
  2. Templates put it in a hidden field: {{ csrf_field() }}.
  3. Before any POST/PUT/PATCH/DELETE on this blueprint, the submitted
     token is compared with the session's using hmac.compare_digest
     (constant time, so timing cannot leak how much of it matched).
  4. Missing or wrong token -> 400 with a "form expired" page. Nothing
     is written.

Why not Flask-WTF? It is the usual choice and does the same thing, but it
is not installed in this project and 40 lines of standard library code
are easy to read, test and explain. The JSON API (/api/v1) does not use
these tokens: it only accepts application/json, which a cross-site HTML
form cannot send, and the API is disabled in production.
"""

import hmac
import secrets

from flask import render_template, request, session
from markupsafe import Markup, escape

from . import main_bp

SESSION_KEY = "_csrf_token"
FIELD_NAME = "csrf_token"
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def get_token():
    token = session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def token_is_valid(submitted):
    expected = session.get(SESSION_KEY)
    if not expected or not isinstance(submitted, str) or not submitted:
        return False
    return hmac.compare_digest(expected.encode(), submitted.encode())


def check_csrf_token():
    """before_request hook, registered after the guard in web/__init__.py."""
    if request.method not in STATE_CHANGING:
        return None
    if token_is_valid(request.form.get(FIELD_NAME)):
        return None
    return render_template("errors/400.html", reason="csrf"), 400


@main_bp.app_context_processor
def inject_csrf_helpers():
    def csrf_field():
        return Markup('<input type="hidden" name="%s" value="%s">') % (FIELD_NAME, escape(get_token()))

    return {"csrf_token": get_token, "csrf_field": csrf_field}
```

### `tradeledger/web/filters.py` (MODIFIED)

**Why:** parse_stored instead of strptime.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Jinja filters: display formatting for exact values.

All rounding goes through performance/formatting.py (the Phase 3 rounding
policy), so a value looks the same in the CLI report, the website and any
future page. Templates never do arithmetic on money.

  {{ value | r }}                  +1.97R      (None -> n/a)
  {{ value | r_amount }}           1.05R       (a size, no sign)
  {{ value | money(currency) }}    393.00 USD
  {{ value | rate }}               50.0%
  {{ value | ratio }}              2.85
  {{ value | plain }}              1.085       (exact stored value)
  {{ ts | when }}                  02 Mar 2026, 08:15 UTC
  {{ ts | day }} / {{ ts | clock }} 02 Mar 2026 / 08:15 UTC (for compact tables)
  {{ ts | datetime_local }}        2026-03-02T08:15:00 (for <input type=datetime-local>)
"""

from ..performance.formatting import (
    NOT_AVAILABLE,
    decimal_to_text,
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
)
from ..timestamps import parse_stored
from . import main_bp


def _parse(timestamp):
    try:
        return parse_stored(timestamp)
    except (TypeError, ValueError):
        return None


@main_bp.app_template_filter("r")
def r_filter(value):
    return format_r(value)


@main_bp.app_template_filter("r_amount")
def r_amount_filter(value):
    return format_r_amount(value)


@main_bp.app_template_filter("money")
def money_filter(value, currency):
    return format_money(value, currency)


@main_bp.app_template_filter("rate")
def rate_filter(value):
    return format_rate(value)


@main_bp.app_template_filter("ratio")
def ratio_filter(value):
    return format_ratio(value)


@main_bp.app_template_filter("plain")
def plain_filter(value):
    if value is None:
        return "—"
    return decimal_to_text(value)


@main_bp.app_template_filter("when")
def when_filter(timestamp):
    moment = _parse(timestamp)
    if moment is None:
        return NOT_AVAILABLE if timestamp is None else timestamp
    text = moment.strftime("%d %b %Y, %H:%M")
    if moment.second:
        text += moment.strftime(":%S")
    return text + " UTC"


@main_bp.app_template_filter("day")
def day_filter(timestamp):
    moment = _parse(timestamp)
    return moment.strftime("%d %b %Y") if moment else NOT_AVAILABLE


@main_bp.app_template_filter("clock")
def clock_filter(timestamp):
    moment = _parse(timestamp)
    if moment is None:
        return ""
    return moment.strftime("%H:%M:%S UTC" if moment.second else "%H:%M UTC")


@main_bp.app_template_filter("datetime_local")
def datetime_local_filter(timestamp):
    """Stored '2026-03-02T08:15:00Z' -> '2026-03-02T08:15:00'. Seconds are
    kept so editing a trade never silently changes its time."""
    moment = _parse(timestamp)
    return moment.strftime("%Y-%m-%dT%H:%M:%S") if moment else (timestamp or "")
```

### `tradeledger/web/guard.py` (MODIFIED)

**Why:** Decorator removed; registered in web/__init__.py (F6).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Keep unauthenticated trade management off public servers.

TradeLedger has no login. In development and tests the journal is
available; in production JOURNAL_ENABLED defaults to False, so every page
that reads or changes trading data answers 403. /health, /about and
static files stay available, and the home page (main.index) renders a
short explanation with NO trading data (see views.index).
"""

from flask import current_app, render_template, request

ALWAYS_AVAILABLE = {"main.health", "main.about", "main.index", "static"}


def refuse_when_disabled():
    """before_request hook, registered first in web/__init__.py."""
    if current_app.config["JOURNAL_ENABLED"] or request.endpoint in ALWAYS_AVAILABLE:
        return None
    return render_template("errors/403.html"), 403
```

### `tradeledger/web/trades.py` (MODIFIED)

**Why:** Plan panel shows n/a instead of crashing on out-of-range stored values (F1).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Trade pages: journal, add, details, edit, delete.

Pattern for every form (POST-Redirect-GET):
  GET   shows the form
  POST  validates. If invalid, re-renders the form with the user's values
        and field errors (status 400). If valid, writes inside a
        transaction, flashes a message and REDIRECTS, so refreshing the
        next page cannot submit the form twice.

All rules live in validation.py and the repositories (shared with the
API). All performance numbers come from performance.trade_math.
"""

from decimal import DecimalException

from flask import abort, current_app, flash, redirect, render_template, request, url_for

from ..db import get_db
from ..performance.trade_math import IncompleteTradeError, evaluate_trade, initial_risk, planned_rr
from ..repositories import trades as trade_repo
from ..repositories.instruments import list_instruments
from ..validation import SESSIONS, ValidationError, date_range_bounds, parse_date, validate_trade
from . import main_bp
from .forms import (
    BLANK_TRADE_FORM,
    TRADE_FORM_FIELDS,
    form_errors_for_status,
    read_form,
    safe_next,
    trade_input,
    trade_to_form,
)

FILTER_FIELDS = ("instrument_id", "status", "start", "end", "strategy", "q")

FIELD_LABELS = {
    "instrument_id": "Instrument",
    "direction": "Direction",
    "status": "Status",
    "trade_date": "Entry time",
    "entry_price": "Entry price",
    "stop_loss": "Stop-loss",
    "take_profit": "Take-profit",
    "position_size": "Position size",
    "fees": "Fees",
    "exit_price": "Exit price",
    "closed_at": "Exit time",
    "strategy": "Strategy",
    "session": "Session",
    "notes": "Notes",
}


# ---------- Helpers ----------


def result_for(trade):
    """The engine's TradeResult for a CLOSED trade; None for OPEN trades
    (they have no realised result) or data that cannot be evaluated."""
    if trade["status"] != "CLOSED":
        return None
    try:
        return evaluate_trade(trade)
    except IncompleteTradeError:
        return None


def with_results(trades):
    return [{"trade": t, "result": result_for(t)} for t in trades]


def plan_for(trade):
    """Planned risk and planned R:R from the engine's own functions. These
    describe the PLAN and are shown for open trades; they are not results."""
    args = (trade["direction"], trade["entry_price"], trade["stop_loss"])
    try:
        return {
            "initial_risk": initial_risk(*args, trade["position_size"], trade["contract_multiplier"]),
            "planned_rr": planned_rr(*args, trade["take_profit"]),
        }
    except DecimalException:
        # Out-of-range stored values (Phase 7): show "n/a" instead of a 500.
        return {"initial_risk": None, "planned_rr": None}


def get_trade_or_404(conn, trade_id):
    trade = trade_repo.get_trade(conn, trade_id)
    if trade is None:
        abort(404)
    return trade


def current_path():
    """This page's path and query, for 'return to where I was' links."""
    path = request.full_path
    return path[:-1] if path.endswith("?") else path


def form_context(conn, values, errors, **extra):
    instruments = list_instruments(conn)
    return {
        "values": values,
        "errors": errors,
        "labels": FIELD_LABELS,
        "instruments": instruments,
        "instrument_options": [(i["id"], f"{i['symbol']} · {i['name']}") for i in instruments],
        "strategies": trade_repo.list_strategies(conn),
        "sessions": SESSIONS,
        **extra,
    }


def collect_errors(values, data, *, require_closed_at=True):
    """Every problem with a submitted form, without writing anything."""
    errors = {}
    try:
        validate_trade(data, require_closed_at=require_closed_at)
    except ValidationError as exc:
        errors.update(exc.errors)
    errors.update(form_errors_for_status(values))
    return errors


# ---------- Journal ----------


def read_filters(args):
    """Validate the journal's query string. Returns (filters, raw, errors).
    `raw` holds what the user typed, to refill the filter form."""
    raw = {name: args.get(name, "").strip() for name in FILTER_FIELDS}
    errors, filters = {}, {}

    if raw["instrument_id"]:
        if raw["instrument_id"].isdigit() and int(raw["instrument_id"]) > 0:
            filters["instrument_id"] = int(raw["instrument_id"])
        else:
            errors["instrument_id"] = "Choose an instrument from the list."
    if raw["status"]:
        if raw["status"].upper() in ("OPEN", "CLOSED"):
            filters["status"] = raw["status"].upper()
        else:
            errors["status"] = "Choose OPEN or CLOSED."
    for name in ("start", "end"):
        if raw[name]:
            try:
                filters[name] = parse_date(raw[name]).isoformat()
            except ValueError:
                errors[name] = "Use a date in YYYY-MM-DD form."
    if "start" in filters and "end" in filters:
        try:
            date_range_bounds(filters["start"], filters["end"])
        except ValidationError:
            errors["end"] = "The end date must be on or after the start date."
    if raw["strategy"]:
        filters["strategy"] = raw["strategy"]
    if raw["q"]:
        if len(raw["q"]) > trade_repo.MAX_SEARCH_LENGTH:
            errors["q"] = f"Search text must be {trade_repo.MAX_SEARCH_LENGTH} characters or fewer."
        else:
            filters["search"] = raw["q"]
    return filters, raw, errors


@main_bp.route("/trades")
def journal():
    conn = get_db()
    filters, raw, errors = read_filters(request.args)
    page_text = request.args.get("page", "1")
    page = int(page_text) if page_text.isdigit() and int(page_text) > 0 else 1
    per_page = current_app.config["JOURNAL_PER_PAGE"]

    rows, total = [], 0
    if not errors:
        total = trade_repo.count_trades(conn, **filters)
        if total and page > (total + per_page - 1) // per_page:
            abort(404)  # e.g. an old bookmark to ?page=9 after deletions
        rows = trade_repo.list_trades(conn, **filters, limit=per_page, offset=(page - 1) * per_page)

    active = {name: value for name, value in raw.items() if value}
    return render_template(
        "trades/journal.html",
        rows=with_results(rows),
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        raw=raw,
        active=active,
        errors=errors,
        instruments=list_instruments(conn),
        strategies=trade_repo.list_strategies(conn),
        here=current_path(),
    ), (400 if errors else 200)


# ---------- Add ----------


@main_bp.route("/trades/new", methods=["GET", "POST"])
def new_trade():
    conn = get_db()
    if request.method == "GET":
        values = dict(BLANK_TRADE_FORM)
        if request.args.get("instrument_id", "").isdigit():
            values["instrument_id"] = request.args["instrument_id"]
        return render_template("trades/form.html", **form_context(conn, values, {}, mode="new"))

    values = read_form(request.form, TRADE_FORM_FIELDS)
    data = trade_input(values)
    errors = collect_errors(values, data)
    if not errors:
        try:
            trade_id = trade_repo.create_trade(conn, data)
        except ValidationError as exc:  # e.g. the instrument was deleted meanwhile
            errors = exc.errors
    if errors:
        return render_template("trades/form.html", **form_context(conn, values, errors, mode="new")), 400

    flash(f"Trade #{trade_id} saved.", "success")
    return redirect(url_for("main.trade_detail", trade_id=trade_id))


# ---------- Details ----------


@main_bp.route("/trades/<int:trade_id>")
def trade_detail(trade_id):
    trade = get_trade_or_404(get_db(), trade_id)
    return render_template(
        "trades/detail.html",
        trade=trade,
        result=result_for(trade),
        plan=plan_for(trade),
        here=current_path(),
    )


# ---------- Edit ----------


@main_bp.route("/trades/<int:trade_id>/edit", methods=["GET", "POST"])
def edit_trade(trade_id):
    conn = get_db()
    trade = get_trade_or_404(conn, trade_id)
    is_legacy = trade["status"] == "CLOSED" and trade["closed_at"] is None
    back = safe_next(request.values.get("next"), url_for("main.trade_detail", trade_id=trade_id))

    if request.method == "GET":
        return render_template(
            "trades/form.html",
            **form_context(
                conn, trade_to_form(trade), {}, mode="edit", trade=trade, is_legacy=is_legacy, back=back
            ),
        )

    values = read_form(request.form, TRADE_FORM_FIELDS)
    data = trade_input(values)
    errors = collect_errors(values, data, require_closed_at=not is_legacy)
    if not errors:
        try:
            trade_repo.update_trade(conn, trade_id, data)
        except ValidationError as exc:
            errors = exc.errors
    if errors:
        return render_template(
            "trades/form.html",
            **form_context(conn, values, errors, mode="edit", trade=trade, is_legacy=is_legacy, back=back),
        ), 400

    flash(f"Trade #{trade_id} updated.", "success")
    return redirect(url_for("main.trade_detail", trade_id=trade_id))


# ---------- Delete ----------


@main_bp.route("/trades/<int:trade_id>/delete", methods=["GET", "POST"])
def delete_trade(trade_id):
    """GET shows a confirmation page and changes nothing. Only a POST with
    a valid CSRF token deletes."""
    conn = get_db()
    trade = get_trade_or_404(conn, trade_id)
    back = safe_next(request.values.get("next"), url_for("main.journal"))

    if request.method == "GET":
        return render_template("trades/confirm_delete.html", trade=trade, result=result_for(trade), back=back)

    trade_repo.delete_trade(conn, trade_id)
    flash(f"Trade #{trade_id} ({trade['symbol']} {trade['direction']}) was deleted.", "success")
    return redirect(back)
```

### `tradeledger/web/views.py` (MODIFIED)

**Why:** Roadmap: Phase 7 done, Phase 8 next.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Overview, About and the health check. (The analytics dashboard is in
analytics.py.)

Every number shown comes from the repositories (counts) or the Phase 3
analytics engine (performance). Nothing is hard-coded.
"""

from flask import current_app, jsonify, render_template

from ..db import get_db
from ..performance import build_report
from ..repositories import trades as trade_repo
from ..repositories.instruments import list_instruments
from . import main_bp
from .trades import with_results

RECENT_TRADES = 5

# The build plan, shown on the About page.
ROADMAP = [
    {"phase": 1, "title": "Project setup", "status": "Done"},
    {"phase": 2, "title": "SQL database", "status": "Done"},
    {"phase": 3, "title": "Trading calculations", "status": "Done"},
    {"phase": 4, "title": "Flask backend & API", "status": "Done"},
    {"phase": 5, "title": "Frontend pages", "status": "Done"},
    {"phase": 6, "title": "Analytics dashboard", "status": "Done"},
    {"phase": 7, "title": "Testing, CI, security & quality", "status": "Done"},
    {"phase": 8, "title": "Next phase (to be agreed)", "status": "Next"},
]


@main_bp.route("/")
def index():
    """Overview. In production with the journal disabled, a short
    explanation is shown instead, with no trading data."""
    if not current_app.config["JOURNAL_ENABLED"]:
        return render_template("home_disabled.html")
    conn = get_db()
    report = build_report(conn)
    return render_template(
        "index.html",
        open_count=trade_repo.count_trades(conn, status="OPEN"),
        closed_count=trade_repo.count_trades(conn, status="CLOSED"),
        instrument_count=len(list_instruments(conn)),
        overall=report.overall,
        unknown_close_count=len(report.unknown_close_time_trade_ids),
        recent=with_results(trade_repo.list_trades(conn, limit=RECENT_TRADES)),
    )


@main_bp.route("/about")
def about():
    return render_template("about.html", roadmap=ROADMAP)


@main_bp.route("/health")
def health():
    """Machine-readable health check.

    Used later by AWS load balancers and monitoring to confirm the app is
    alive. Returns JSON only - never secrets or internal details.
    """
    return jsonify(
        status="ok",
        app=current_app.config["APP_NAME"],
        version=current_app.config["APP_VERSION"],
    )
```

### `tests/test_app.py` (MODIFIED)

**Why:** Production test uses a 64-character key (F3); static response closed.  
**How to run or test:** Run: `python -m pytest tests/test_app.py`

```python
"""Phase 1 tests: the app starts, pages load, config is safe."""

import pytest

from tradeledger import create_app

# ---------- Application factory and configuration ----------


def test_testing_config_is_applied(app):
    assert app.config["TESTING"] is True
    assert app.config["CONFIG_NAME"] == "testing"


def test_unknown_config_name_is_rejected():
    with pytest.raises(ValueError):
        create_app("not-a-real-config")


def test_production_without_secret_key_refuses_to_start(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    # ProductionConfig read SECRET_KEY when config.py was imported, so
    # clear it on the class too to simulate "no key set".
    from tradeledger.config import ProductionConfig

    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", None)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app("production")


def test_production_with_secret_key_starts(monkeypatch):
    from tradeledger.config import ProductionConfig

    # Phase 7: production keys must be at least 32 characters (see
    # tests/test_security_hardening.py), so this uses a realistic 64-character key.
    key = "9f2c" * 16
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", key)
    app = create_app("production")
    assert app.config["SECRET_KEY"] == key


def test_development_without_secret_key_generates_temporary_key(monkeypatch):
    from tradeledger.config import DevelopmentConfig

    monkeypatch.setattr(DevelopmentConfig, "SECRET_KEY", None)
    app = create_app("development")
    assert len(app.config["SECRET_KEY"]) == 64  # 32 random bytes as hex


# ---------- Routes ----------


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"TradeLedger" in response.data


def test_home_page_shows_roadmap(client):
    # Phase 5: the build roadmap moved from the home page to About.
    response = client.get("/about")
    assert b"Build progress" in response.data
    assert b"SQL database" in response.data


def test_home_page_shows_environment(client):
    response = client.get("/")
    assert b"testing" in response.data


def test_stylesheet_is_served(client):
    # `with` closes the file-backed response (pytest treats leaks as errors).
    with client.get("/static/css/style.css") as response:
        assert response.status_code == 200
        assert "text/css" in response.content_type


def test_health_endpoint_returns_json(client, app):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.is_json
    # The version is read from config rather than hard-coded, so this test
    # does not break every time the version number is bumped.
    assert response.get_json() == {
        "status": "ok",
        "app": "TradeLedger",
        "version": app.config["APP_VERSION"],
    }


def test_health_endpoint_does_not_leak_secret(client, app):
    response = client.get("/health")
    assert app.config["SECRET_KEY"].encode() not in response.data


# ---------- Error handling ----------


def test_unknown_page_returns_custom_404(client):
    response = client.get("/this-page-does-not-exist")
    assert response.status_code == 404
    assert b"Page not found" in response.data


def test_post_to_get_only_route_is_rejected(client):
    response = client.post("/")
    assert response.status_code == 405


# ---------- Security headers ----------


@pytest.mark.parametrize("path", ["/", "/health", "/missing-page"])
def test_security_headers_present(client, path):
    response = client.get(path)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
```

### `tests/test_browser.py` (MODIFIED)

**Why:** Uses the shared fixtures; dashboard tests labelled chartjs_stub / chartjs_offline; narrower except.  
**How to run or test:** Run: `python -m pytest tests/test_browser.py`

```python
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
    page = page_factory(width)
    page.goto(live_server + path)
    assert horizontal_overflow(page) == 0
    assert page.problems == []  # includes CSP violations


@pytest.mark.parametrize("width, menu_visible", [(375, True), (768, True), (1024, False), (1440, False)])
def test_menu_button_only_on_narrow_screens(live_server, page_factory, width, menu_visible):
    page = page_factory(width)
    page.goto(live_server + "/")
    assert page.locator("[data-nav-toggle]").is_visible() is menu_visible
    assert page.locator("#sidebar a", has_text="Instruments").is_visible() is not menu_visible


def test_mobile_menu_opens_moves_focus_and_closes_with_escape(live_server, page_factory):
    page = page_factory(375)
    page.goto(live_server + "/trades")
    toggle = page.locator("[data-nav-toggle]")
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    page.wait_for_function("document.activeElement.closest('#sidebar') !== null")
    page.keyboard.press("Escape")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.hasAttribute('data-nav-toggle')")


def test_skip_link_is_first_and_reaches_main(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.classList.contains('skip-link')")
    page.keyboard.press("Enter")
    assert page.evaluate("location.hash") == "#main"


def test_open_status_hides_and_disables_exit_fields(live_server, page_factory):
    page = page_factory()
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
    page = page_factory()
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
    page = page_factory()
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
    page = page_factory()
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
    page = page_factory()
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
    page = page_factory()
    page.goto(live_server + "/analytics")
    page.evaluate("""() => { const s = document.createElement("script");
                             s.src = "https://example.com/evil.js"; document.head.appendChild(s); }""")
    page.wait_for_timeout(300)
    assert any("Content Security Policy" in problem for problem in page.problems)


@pytest.mark.chartjs_stub
def test_chart_tooltips_show_the_server_figures(live_server, page_factory):
    page = page_factory()
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
    page = page_factory()
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
    page = page_factory()
    response = page.goto(live_server + "/analytics?instrument_id=999")
    assert response.status == 400
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    assert page.locator("canvas").count() == 0


def test_empty_result_shows_a_helpful_state(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/analytics?start=2030-01-01")
    assert page.get_by_text("No closed trades match these filters").is_visible()
    assert page.locator("canvas").count() == 0
    assert page.problems == []


@pytest.mark.chartjs_offline
def test_offline_chart_library_falls_back_to_text(live_server, page_factory):
    page = page_factory(chart_js="blocked")
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
    page = page_factory(javascript=False)
    page.goto(live_server + "/analytics?strategy=Breakout")
    assert page.get_by_text("Charts need JavaScript").is_visible()
    assert page.locator('[data-kpi="trades"] .kpi__value').inner_text() == "5"
    assert page.locator("#table-series").is_visible()
    assert page.locator("[data-chart-box]").first.is_hidden()


@pytest.mark.chartjs_stub
@pytest.mark.parametrize("width", WIDTHS)
def test_dashboard_layout_at_each_width(live_server, page_factory, width):
    page = page_factory(width)
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
    page = page_factory()
    page.goto(live_server + "/analytics")
    page.focus("#dash-instrument_id")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "dash-strategy"
    page.focus("#table-instruments")
    assert page.evaluate("document.activeElement.id") == "table-instruments"
```

### `tests/test_web_analytics.py` (MODIFIED)

**Why:** Production test uses a 64-character key (F3); next() instead of [...][0].  
**How to run or test:** Run: `python -m pytest tests/test_web_analytics.py`

```python
"""Phase 6: the /analytics dashboard page (Flask test client, not a browser).

Covers rendering, filters, validation, empty states, legacy data, the chart
payload, agreement with the JSON API, escaping and the Content-Security-
Policy. Real-browser behaviour (Chart.js, layout) is in test_browser.py.
"""

import json
import re
from decimal import Decimal

import pytest

from tradeledger import create_app
from tradeledger.db import get_db, init_schema
from tradeledger.performance.dashboard import build_dashboard, chart_payload
from tradeledger.report_filters import ReportFilters
from tradeledger.repositories.trades import create_trade
from tradeledger.web.analytics import chart_script_sources

CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
CHART_NAMES = ("curve", "drawdown", "monthly", "weekday", "instruments", "strategies", "sessions")


def page(client, query=""):
    response = client.get("/analytics" + (f"?{query}" if query else ""))
    return response, response.get_data(as_text=True)


def kpi(html, key):
    match = re.search(rf'data-kpi="{key}">.*?class="kpi__value">([^<]*)<', html, re.S)
    assert match, key
    return match.group(1)


def payload_of(html):
    match = re.search(r'<script type="application/json" id="dashboard-data">(.*?)</script>', html, re.S)
    return json.loads(match.group(1)) if match else None


# ---------- Rendering with sample data ----------


def test_dashboard_renders_every_section(seeded_client):
    response, html = page(seeded_client)
    assert response.status_code == 200
    for heading in (
        "Reporting filters",
        "Summary",
        "Performance over time",
        "Breakdowns",
        "Detailed tables",
        "About these figures",
    ):
        assert heading in html
    for name in CHART_NAMES:
        assert f'data-chart="{name}"' in html
        assert f'id="chart-{name}-desc"' in html  # visible text alternative


def test_kpi_cards_show_engine_values(seeded_client):
    _, html = page(seeded_client)
    expected = {
        "trades": "14",
        "wins": "7",
        "losses": "5",
        "breakevens": "2",
        "win_rate": "50.0%",
        "average_r": "+0.55R",
        "expectancy_r": "+0.55R",
        "profit_factor_r": "2.85",
        "total_r": "+7.69R",
        "max_drawdown_r": "1.05R",
        "longest_win_streak": "2 trades",
        "longest_loss_streak": "2 trades",
    }
    assert {key: kpi(html, key) for key in expected} == expected


def test_money_is_shown_per_currency_never_combined(seeded_client):
    _, html = page(seeded_client)
    assert "798.50 USD" in html and "200.00 GBP" in html
    assert "998.50" not in html  # USD + GBP would be meaningless


def test_tables_hold_the_chart_figures(seeded_client):
    _, html = page(seeded_client)
    for table in (
        "table-series",
        "table-monthly",
        "table-weekday",
        "table-instruments",
        "table-strategies",
        "table-sessions",
    ):
        assert f'id="{table}"' in html
    assert html.count("No closed trades") >= 2  # Saturday and Sunday rows
    assert "Small sample" in html


def test_page_works_without_javascript(seeded_client):
    _, html = page(seeded_client)
    assert "Charts need JavaScript" in html  # .no-js-only notice
    assert '<form class="card filters dash-filters" method="get"' in html


def test_chart_payload_matches_the_dashboard_layer(app, seeded_client):
    _, html = page(seeded_client, "strategy=Breakout")
    with app.app_context():
        expected = chart_payload(build_dashboard(get_db(), ReportFilters(strategy="Breakout")))
    assert payload_of(html) == expected


def test_dashboard_and_api_agree(seeded_client):
    _, html = page(seeded_client, "session=LONDON")
    api = seeded_client.get("/api/v1/analytics/summary?session=LONDON").get_json()["data"]["overall"]
    curve = payload_of(html)["curve"]
    assert Decimal(curve[-1]["cumulative_r"]) == Decimal(api["total_r"])
    assert kpi(html, "trades") == str(api["trades"])


# ---------- Filters ----------


@pytest.mark.parametrize(
    "query, trades",
    [
        ("strategy=Breakout", "5"),
        ("session=ASIA", "2"),
        ("session=london", "6"),
        ("instrument_id=1", "3"),
        ("strategy=Breakout&session=LONDON", "3"),
        ("start=2026-03-10&end=2026-03-13", "4"),
        ("instrument_id=&strategy=&session=&start=&end=", "14"),  # "All" everywhere
    ],
)
def test_filters_change_the_figures(seeded_client, query, trades):
    response, html = page(seeded_client, query)
    assert response.status_code == 200
    assert kpi(html, "trades") == trades


def test_selected_filters_are_kept_in_the_form(seeded_client):
    _, html = page(
        seeded_client, "instrument_id=2&strategy=Pullback&session=london&start=2026-03-01&end=2026-03-31"
    )
    assert '<option value="2" selected' in html
    assert '<option value="Pullback" selected' in html
    assert '<option value="LONDON" selected' in html
    assert 'value="2026-03-01"' in html and 'value="2026-03-31"' in html
    assert "Showing results for:" in html


def test_reset_link_only_when_filtering(seeded_client):
    _, plain = page(seeded_client)
    _, filtered = page(seeded_client, "strategy=Breakout")
    assert "Reset filters" not in plain
    assert 'href="/analytics">Reset filters' in filtered


def test_bookmarked_strategy_stays_selectable(seeded_client):
    _, html = page(seeded_client, "strategy=Retired%20setup")
    assert '<option value="Retired setup" selected' in html


@pytest.mark.parametrize(
    "query, field, message",
    [
        ("instrument_id=999", "instrument_id", "does not match an existing instrument"),
        ("instrument_id=abc", "instrument_id", "must be a whole number of 1 or more"),
        ("session=MOON", "session", "must be one of: ASIA, LONDON, NEW_YORK, OTHER"),
        ("strategy=" + "x" * 51, "strategy", "must be 50 characters or fewer"),
        ("start=2026-02-30", "start", "must be a date in YYYY-MM-DD form"),
        ("start=2026-03-10&end=2026-03-01", "end", "must be on or after the start date"),
    ],
)
def test_invalid_filters_give_400_and_no_report(seeded_client, query, field, message):
    response, html = page(seeded_client, query)
    assert response.status_code == 400
    assert "The report was not produced" in html
    assert message in html
    assert f'id="dash-{field}"' in html and 'aria-invalid="true"' in html
    assert 'data-kpi="' not in html and "dashboard-data" not in html  # nothing misleading
    assert CDN not in html


# ---------- Empty and edge cases ----------


def test_empty_database(client):
    response, html = page(client)
    assert response.status_code == 200
    assert "No closed trades yet" in html
    assert kpi(html, "win_rate") == "n/a" and kpi(html, "total_r") == "n/a"
    assert "dashboard-data" not in html and CDN not in html  # nothing to draw
    assert CDN not in response.headers["Content-Security-Policy"]


def test_only_open_trades(app, client):
    with app.app_context():
        conn = get_db()
        conn.execute(
            "INSERT INTO instruments (symbol, name, asset_class, price_increment, contract_multiplier,"
            " quote_currency) VALUES ('OPN', 'Open only', 'OTHER', '0.01', '1', 'USD')"
        )
        conn.commit()
        create_trade(
            conn,
            {
                "instrument_id": 1,
                "direction": "LONG",
                "status": "OPEN",
                "trade_date": "2026-03-02T08:00:00Z",
                "entry_price": "100",
                "stop_loss": "99",
                "position_size": "1",
            },
        )
    _, html = page(client)
    assert "1 open trade matches, but open trades have no realised result yet" in html
    assert kpi(html, "trades") == "0"


def test_filters_matching_nothing(seeded_client):
    _, html = page(seeded_client, "start=2030-01-01")
    assert "No closed trades match these filters" in html
    assert "Try a wider date range" in html


def test_instrument_with_only_open_trades(seeded_client):
    """Sample trade 15 (EURUSD, Reversal) is open; nothing else matches."""
    _, html = page(seeded_client, "instrument_id=1&strategy=Reversal")
    assert "1 open trade matches" in html


def test_legacy_trade_is_explained(app, seeded_client):
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 4")
        conn.commit()
    _, html = page(seeded_client)
    assert "Unknown exit times:" in html and "(#4)" in html
    assert "Entry time: exit not recorded" in html
    curve = payload_of(html)["curve"]
    assert next(p for p in curve if p.get("trade_id") == 4)["close_time_known"] is False


def test_maximum_drawdown_row_is_marked(seeded_client):
    _, html = page(seeded_client)
    row = re.search(r'<tr class="is-max-drawdown">(.*?)</tr>', html, re.S).group(1)
    assert "#9 EURUSD" in row and "Maximum" in row


# ---------- Security ----------


def test_dashboard_csp_allows_only_the_exact_chart_js_file(seeded_client):
    response, html = page(seeded_client)
    csp = response.headers["Content-Security-Policy"]
    assert f"script-src 'self' {CDN};" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "https://cdn.jsdelivr.net;" not in csp and "https://cdn.jsdelivr.net " not in csp
    assert f'<script src="{CDN}" defer crossorigin="anonymous" referrerpolicy="no-referrer">' in html


@pytest.mark.parametrize(
    "url", ["/", "/trades", "/trades/new", "/instruments", "/about", "/api/v1/analytics/summary"]
)
def test_other_pages_keep_the_strict_policy(seeded_client, url):
    csp = seeded_client.get(url).headers["Content-Security-Policy"]
    assert "script-src 'self';" in csp and "cdn.jsdelivr.net" not in csp


def test_no_inline_executable_script(seeded_client):
    _, html = page(seeded_client)
    for tag in re.findall(r"<script[^>]*>", html):
        assert "src=" in tag or 'type="application/json"' in tag, tag
    assert " onclick=" not in html and " style=" not in html


def test_self_hosted_chart_js_needs_no_extra_csp_source(db_path):
    app = create_app(
        "testing",
        overrides={
            "DATABASE": db_path,
            "CHART_JS_URL": "/static/vendor/chartjs/chart.umd.min.js",
            "CHART_JS_INTEGRITY": "sha384-abc123",
        },
    )
    with app.app_context():
        from tradeledger.seed import seed_sample_data

        init_schema(get_db())
        seed_sample_data(get_db())
    response = app.test_client().get("/analytics")
    html = response.get_data(as_text=True)
    assert "script-src 'self';" in response.headers["Content-Security-Policy"]
    assert 'src="/static/vendor/chartjs/chart.umd.min.js"' in html
    assert 'integrity="sha384-abc123"' in html


@pytest.mark.parametrize(
    "url, sources",
    [
        ("/static/vendor/chartjs/chart.umd.min.js", ()),
        (CDN, (CDN,)),
    ],
)
def test_chart_script_sources(url, sources):
    assert chart_script_sources(url) == sources


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.example/chart.js",
        "//cdn.example/chart.js",
        "javascript:alert(1)",
        "https://cdn.example/chart.js?v=1",
        "chart.js",
    ],
)
def test_unsafe_chart_js_urls_are_refused(url):
    with pytest.raises(ValueError):
        chart_script_sources(url)


def test_malicious_strategy_name_is_escaped_everywhere(app, seeded_client):
    evil = "<script>alert(1)</script>"
    with app.app_context():
        get_db().execute("UPDATE trades SET strategy = ? WHERE id IN (1, 2)", (evil,))
        get_db().commit()
    for query in ("", "strategy=" + evil):
        _, html = page(seeded_client, query)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    _, html = page(seeded_client)
    data = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S).group(1)
    assert "<" not in data  # tojson escapes it as \\u003c
    assert evil in json.dumps(payload_of(html))  # ...and it round-trips intact


def test_filter_injection_is_just_text(seeded_client):
    response, html = page(seeded_client, "strategy=%27%20OR%201%3D1%20--")
    assert response.status_code == 200
    assert "No closed trades match these filters" in html


def test_dashboard_is_disabled_in_production(db_path):
    # Phase 7: a production key must be at least 32 characters.
    app = create_app("production", overrides={"SECRET_KEY": "k" * 64, "DATABASE": db_path})
    with app.app_context():
        init_schema(get_db())
    response = app.test_client().get("/analytics")
    assert response.status_code == 403
    assert "cdn.jsdelivr.net" not in response.headers["Content-Security-Policy"]


def test_post_is_not_allowed(seeded_client):
    token_page = seeded_client.get("/trades/new").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', token_page).group(1)
    assert seeded_client.post("/analytics", data={"csrf_token": token}).status_code == 405


def test_navigation_marks_analytics_current(seeded_client):
    _, html = page(seeded_client)
    marker = html.index('aria-current="page"')
    assert "Analytics" in html[marker : marker + 120]
```

### `tests/test_web_pages.py` (MODIFIED)

**Why:** Static responses closed; About test also checks Phase 7 and the Host-check note.  
**How to run or test:** Run: `python -m pytest tests/test_web_pages.py`

```python
"""Phases 5-6: every website page renders, shows real data, and the shell
(navigation, headings, section labelling) behaves as documented."""

import pytest

PAGES = [
    "/",
    "/trades",
    "/trades/new",
    "/trades/1",
    "/trades/1/edit",
    "/trades/1/delete",
    "/instruments",
    "/analytics",
    "/about",
]


@pytest.mark.parametrize("url", PAGES)
def test_every_page_renders_with_sample_data(seeded_client, url):
    response = seeded_client.get(url)
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    html = response.get_data(as_text=True)
    assert "<h1" in html
    assert 'id="main"' in html  # skip-link target
    assert 'class="skip-link"' in html


@pytest.mark.parametrize("url", ["/", "/trades", "/trades/new", "/instruments", "/analytics", "/about"])
def test_pages_render_with_an_empty_database(client, url):
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/no-such-page", "/trades/999", "/trades/999/edit", "/trades/999/delete"])
def test_unknown_pages_and_trades_are_html_404s(seeded_client, url):
    response = seeded_client.get(url)
    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert b"Page not found" in response.data


def test_non_numeric_trade_id_is_404(seeded_client):
    assert seeded_client.get("/trades/abc").status_code == 404


# ---------- Navigation shell ----------


def test_sidebar_lists_every_section(client):
    html = client.get("/").get_data(as_text=True)
    for label in ("Overview", "Trade journal", "Add trade", "Instruments", "Analytics", "About"):
        assert label in html


@pytest.mark.parametrize(
    "url, label",
    [
        ("/", "Overview"),
        ("/trades", "Trade journal"),
        ("/trades/new", "Add trade"),
        ("/instruments", "Instruments"),
        ("/about", "About"),
    ],
)
def test_current_section_is_marked_for_assistive_technology(client, url, label):
    html = client.get(url).get_data(as_text=True)
    marker = html.index('aria-current="page"')
    # The label of the marked link follows the attribute closely.
    assert label in html[marker : marker + 200]
    assert html.count('aria-current="page"') == 1


def test_trade_pages_highlight_the_journal(seeded_client):
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    marker = html.index('aria-current="page"')
    assert "Trade journal" in html[marker : marker + 200]


def test_menu_button_is_wired_for_accessibility(client):
    html = client.get("/").get_data(as_text=True)
    assert 'aria-controls="sidebar"' in html
    assert 'aria-expanded="false"' in html
    assert 'id="sidebar"' in html


def test_no_fake_account_features(client):
    html = client.get("/").get_data(as_text=True).lower()
    for fake in ("log out", "logout", "sign in", "avatar", "notifications"):
        assert fake not in html


def test_scripts_and_styles_are_external_files_only(client):
    """The Content-Security-Policy forbids inline scripts and styles, so
    templates must not contain any."""
    for url in ("/", "/trades/new", "/instruments"):
        html = client.get(url).get_data(as_text=True)
        assert "<script>" not in html
        assert "<style" not in html
        assert " style=" not in html
        assert " onclick=" not in html


@pytest.mark.parametrize(
    "path",
    [
        "/static/js/app.js",
        "/static/js/early.js",
        "/static/js/trade-form.js",
        "/static/css/style.css",
        "/static/css/base.css",
        "/static/css/layout.css",
        "/static/css/components.css",
        "/static/css/pages.css",
        "/static/css/analytics.css",
        "/static/js/analytics.js",
    ],
)
def test_static_assets_are_served(client, path):
    with client.get(path) as response:  # closes the static file
        assert response.status_code == 200


# ---------- Overview ----------


def test_overview_shows_real_counts_and_recent_trades(seeded_client):
    html = seeded_client.get("/").get_data(as_text=True)
    assert "Recent trades" in html
    # 16 sample trades: 2 open, 14 closed, 5 instruments.
    assert ">2<" in html and ">14<" in html and ">5<" in html
    assert "XAUUSD" in html


def test_overview_empty_state(client):
    html = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" in html
    assert "Your journal is empty" in html


# ---------- Journal ----------


def test_journal_lists_every_column(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    for heading in (
        "Entry time",
        "Instrument",
        "Status",
        "Entry",
        "Stop-loss",
        "Exit",
        "Size",
        "Net P&amp;L",
        "realised R",
        "Strategy",
        "Actions",
    ):
        assert heading in html
    assert "Showing 1–16 of 16 trades" in html


def test_journal_labels_outcomes_in_words_not_only_colour(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    # Badges contain the word itself (CSS only changes its letter case).
    for label in ("Win", "Loss", "Breakeven", "Open"):
        assert f">{label}</span>" in html
    assert "Long" in html and "Short" in html


def test_journal_money_carries_its_currency(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    assert "393.00 USD" in html  # sample trade 1
    assert "GBP" in html  # UK100 trades are in GBP


def test_journal_filters_by_status(seeded_client):
    html = seeded_client.get("/trades?status=OPEN").get_data(as_text=True)
    assert "Showing 1–2 of 2 trades matching your filters" in html


def test_journal_filters_by_strategy(seeded_client):
    html = seeded_client.get("/trades?strategy=Breakout").get_data(as_text=True)
    assert "of 6 trades" in html


def test_journal_search_covers_the_whole_database(seeded_client):
    html = seeded_client.get("/trades?q=xau").get_data(as_text=True)
    assert "of 4 trades" in html
    assert "EURUSD</a>" not in html


def test_journal_search_with_no_matches_shows_empty_state(seeded_client):
    html = seeded_client.get("/trades?q=zzzz-nothing").get_data(as_text=True)
    assert "No trades match" in html


def test_journal_reset_link_present_when_filtering(seeded_client):
    html = seeded_client.get("/trades?status=CLOSED").get_data(as_text=True)
    assert 'href="/trades"' in html
    assert "Reset" in html


@pytest.mark.parametrize(
    "query",
    [
        "status=MAYBE",
        "start=2026-13-01",
        "instrument_id=abc",
        "start=2026-03-10&end=2026-03-01",
        "q=" + "x" * 101,
    ],
)
def test_invalid_filters_are_400_with_an_explanation(seeded_client, query):
    response = seeded_client.get("/trades?" + query)
    assert response.status_code == 400
    assert b'aria-invalid="true"' in response.data


def test_pagination_keeps_filters(app, seeded_client):
    app.config["JOURNAL_PER_PAGE"] = 5
    html = seeded_client.get("/trades?status=CLOSED").get_data(as_text=True)
    assert "Showing 1–5 of 14 trades" in html
    assert "status=CLOSED" in html and "page=2" in html
    page3 = seeded_client.get("/trades?status=CLOSED&page=3").get_data(as_text=True)
    assert "Showing 11–14 of 14" in page3


def test_out_of_range_page_is_404(app, seeded_client):
    app.config["JOURNAL_PER_PAGE"] = 5
    assert seeded_client.get("/trades?page=99").status_code == 404


# ---------- Trade details ----------


def test_detail_shows_engine_values(seeded_client):
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert "393.00 USD" in html  # net P&L
    assert "400.00 USD" in html  # gross
    assert "200.00 USD" in html  # initial risk
    assert "+1.97R" in html  # realised R, display-rounded
    assert "Edit" in html and "Delete" in html


def test_detail_for_open_trade_has_no_invented_result(seeded_client):
    html = seeded_client.get("/trades/14").get_data(as_text=True)
    assert ">Open</span>" in html
    assert "Not closed yet" in html
    assert "no realised result yet" in html
    assert "(after fees)" not in html  # the result card is absent
    assert "Planned R:R" in html


def test_detail_for_legacy_trade_says_exit_time_unknown(app, seeded_client):
    with app.app_context():
        from tradeledger.db import get_db

        db = get_db()
        db.execute("UPDATE trades SET closed_at = NULL WHERE id = 1")
        db.commit()
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert "Not recorded" in html
    assert "exit time of this trade was never recorded" in html
    assert "393.00 USD" in html  # the result is still computed


# ---------- Instruments, analytics, about ----------


def test_instruments_page_explains_multiplier_and_warns(seeded_client):
    html = seeded_client.get("/instruments").get_data(as_text=True)
    assert "Contract multiplier" in html
    assert "broker" in html
    assert "EURUSD" in html
    assert "/edit" not in html  # instruments are not editable


def test_analytics_is_the_full_dashboard(seeded_client):
    # Phase 6 replaced the Phase 5 preview with the full dashboard.
    html = seeded_client.get("/analytics").get_data(as_text=True)
    assert "Preview" not in html
    for name in ("curve", "drawdown", "monthly", "weekday", "instruments", "strategies", "sessions"):
        assert f'data-chart="{name}"' in html
    assert "Results by instrument" in html and "Results by strategy" in html


def test_sidebar_no_longer_labels_analytics_as_preview(client):
    # Phase 6: the "Preview" tag was removed when the dashboard was finished.
    html = client.get("/").get_data(as_text=True)
    analytics = html.index(">Analytics<")
    assert "Preview" not in html[analytics : analytics + 200]


def test_about_shows_roadmap_and_boundaries(client):
    html = client.get("/about").get_data(as_text=True)
    assert "Build progress" in html
    assert "Phase 6" in html and "Phase 7" in html
    assert "127.0.0.1" in html  # Phase 7: the Host check is explained
```

### `tests/test_web_security.py` (MODIFIED)

**Why:** Static responses closed.  
**How to run or test:** Run: `python -m pytest tests/test_web_security.py`

```python
"""Phase 5 security: CSRF, output escaping, safe redirects, HTTP methods,
security headers, cookies and the production journal guard."""

import logging
import re

import pytest

from tests.web_helpers import (
    closed_trade_form,
    count_rows,
    csrf_token,
    fetch_trade,
    new_trade_id,
    post_form,
)
from tradeledger import create_app
from tradeledger.db import get_db, init_schema
from tradeledger.web.forms import safe_next

STATE_CHANGING_URLS = ["/trades/new", "/trades/1/edit", "/trades/1/delete", "/instruments"]


# ---------- CSRF ----------


@pytest.mark.parametrize("url", STATE_CHANGING_URLS)
def test_post_without_token_is_rejected_and_writes_nothing(app, seeded_client, url):
    trades, instruments = count_rows(app, "trades"), count_rows(app, "instruments")
    trade_1 = fetch_trade(app, 1)
    response = seeded_client.post(url, data=closed_trade_form(symbol="NEW1", name="x"))
    assert response.status_code == 400
    assert b"This form could not be accepted" in response.data
    assert count_rows(app, "trades") == trades
    assert count_rows(app, "instruments") == instruments
    assert fetch_trade(app, 1) == trade_1


@pytest.mark.parametrize("url", STATE_CHANGING_URLS)
def test_post_with_wrong_token_is_rejected(app, seeded_client, url):
    csrf_token(seeded_client)  # the session now has a real token
    trades = count_rows(app, "trades")
    response = seeded_client.post(url, data={**closed_trade_form(), "csrf_token": "forged-value"})
    assert response.status_code == 400
    assert count_rows(app, "trades") == trades


def test_token_from_another_session_is_rejected(app, seeded_client):
    stolen = csrf_token(app.test_client())  # a different browser's token
    trades = count_rows(app, "trades")
    response = seeded_client.post("/trades/1/delete", data={"csrf_token": stolen})
    assert response.status_code == 400
    assert count_rows(app, "trades") == trades


def test_empty_session_rejects_even_an_empty_token(seeded_client):
    response = seeded_client.post("/trades/1/delete", data={"csrf_token": ""})
    assert response.status_code == 400


def test_valid_token_is_accepted(app, seeded_client):
    response = post_form(seeded_client, "/trades/new", closed_trade_form())
    assert response.status_code == 302


def test_every_form_carries_a_token(seeded_client):
    for url in ("/trades/new", "/trades/1/edit", "/trades/1/delete", "/instruments"):
        html = seeded_client.get(url).get_data(as_text=True)
        post_forms = len(re.findall(r'<form[^>]*method="post"', html))
        assert post_forms >= 1
        assert html.count('name="csrf_token"') >= post_forms


def test_journal_row_delete_links_are_get_links_to_the_confirmation(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    assert 'href="/trades/1/delete' in html  # a link to the confirmation page...
    assert 'action="/trades/1/delete"' not in html  # ...not a one-click POST


def test_api_does_not_need_csrf_tokens(seeded_client, trade_body):
    """The JSON API has its own defences (JSON-only, disabled in
    production); the website's CSRF check must not break it."""
    response = seeded_client.post("/api/v1/trades", json=trade_body)
    assert response.status_code == 201


# ---------- HTTP methods ----------


def test_get_delete_never_deletes(app, seeded_client):
    before = count_rows(app, "trades")
    for _ in range(3):
        seeded_client.get("/trades/1/delete")
    assert count_rows(app, "trades") == before


@pytest.mark.parametrize(
    "method, url",
    [
        ("put", "/trades/1"),
        ("delete", "/trades/1"),
        ("post", "/trades/1"),
        ("post", "/trades"),
        ("post", "/"),
        ("put", "/instruments"),
        ("delete", "/trades/1/delete"),
    ],
)
def test_unsupported_methods_are_405(seeded_client, method, url):
    response = getattr(seeded_client, method)(url)
    assert response.status_code == 405


# ---------- Escaping (stored XSS) ----------

PAYLOAD = '<script>alert("x")</script><img src=x onerror=alert(1)>'


def test_malicious_text_is_escaped_everywhere(app, seeded_client):
    form = closed_trade_form(strategy='<script>alert("s")</script>', notes=PAYLOAD)  # strategy max 50
    trade_id = new_trade_id(post_form(seeded_client, "/trades/new", form))
    assert fetch_trade(app, trade_id)["notes"] == PAYLOAD  # stored exactly as typed

    for url in (
        "/",
        "/trades",
        f"/trades/{trade_id}",
        f"/trades/{trade_id}/edit",
        f"/trades/{trade_id}/delete",
        "/analytics",
        "/trades?q=alert",
    ):
        html = seeded_client.get(url).get_data(as_text=True)
        assert "<script>alert" not in html, url
        assert "<img src=x" not in html, url


def test_escaped_payload_is_visible_as_text(app, seeded_client):
    trade_id = new_trade_id(post_form(seeded_client, "/trades/new", closed_trade_form(notes=PAYLOAD)))
    html = seeded_client.get(f"/trades/{trade_id}").get_data(as_text=True)
    assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in html


def test_search_text_is_escaped_when_echoed(seeded_client):
    html = seeded_client.get('/trades?q="><script>alert(1)</script>').get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html


def test_malicious_instrument_name_is_escaped(seeded_client):
    post_form(
        seeded_client,
        "/instruments",
        {
            "symbol": "EVIL",
            "name": "<b onmouseover=alert(1)>x</b>",
            "asset_class": "OTHER",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        token_page="/instruments",
    )
    for url in ("/instruments", "/trades/new"):
        html = seeded_client.get(url).get_data(as_text=True)
        assert "<b onmouseover" not in html
        assert "&lt;b onmouseover=alert(1)&gt;" in html


# ---------- Safe redirects ----------


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
        "http:/evil.example",
        "trades",
        "",
        None,
        "///evil.example",
        "//%2Fevil.example",
    ],
)
def test_safe_next_rejects_external_targets(app, target):
    with app.test_request_context():
        assert safe_next(target, "/fallback") == "/fallback"


@pytest.mark.parametrize("target", ["/trades", "/trades?status=CLOSED&page=2", "/trades/3"])
def test_safe_next_accepts_local_paths(app, target):
    with app.test_request_context():
        assert safe_next(target, "/fallback") == target


def test_delete_ignores_an_external_next(seeded_client):
    response = post_form(seeded_client, "/trades/1/delete", {"next": "https://evil.example/"})
    assert response.status_code == 302
    assert "evil" not in response.headers["Location"]
    assert response.headers["Location"].endswith("/trades")


def test_edit_cancel_link_ignores_an_external_next(seeded_client):
    html = seeded_client.get("/trades/1/edit?next=//evil.example").get_data(as_text=True)
    assert "evil.example" not in html


# ---------- Headers and cookies ----------


def test_security_headers_on_pages(client):
    response = client.get("/")
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_session_cookie_is_httponly_and_samesite(client):
    response = client.get("/trades/new")
    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def _production(db_path, **overrides):
    app = create_app(
        "production",
        overrides={"SECRET_KEY": "a-real-secret-from-the-environment", "DATABASE": db_path, **overrides},
    )
    with app.app_context():
        init_schema(get_db())
    return app


def test_production_session_cookie_is_secure(db_path):
    assert _production(db_path).config["SESSION_COOKIE_SECURE"] is True


# ---------- Production journal guard ----------


@pytest.mark.parametrize(
    "url",
    [
        "/trades",
        "/trades/new",
        "/trades/1",
        "/trades/1/edit",
        "/trades/1/delete",
        "/instruments",
        "/analytics",
    ],
)
def test_journal_is_disabled_in_production_by_default(db_path, url):
    client = _production(db_path).test_client()
    response = client.get(url)
    assert response.status_code == 403
    assert b"Journal disabled" in response.data


def test_production_post_is_also_blocked(db_path):
    app = _production(db_path)
    response = app.test_client().post("/trades/new", data=closed_trade_form())
    assert response.status_code == 403
    assert count_rows(app, "trades") == 0


def test_production_home_page_shows_no_trading_data(db_path):
    from tradeledger.seed import seed_sample_data

    app = _production(db_path)
    with app.app_context():
        seed_sample_data(get_db())
    response = app.test_client().get("/")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for leak in ("EURUSD", "XAUUSD", "393.00", "Recent trades", "Breakout"):
        assert leak not in html


@pytest.mark.parametrize("url", ["/health", "/about", "/static/css/style.css"])
def test_production_keeps_harmless_pages(db_path, url):
    with _production(db_path).test_client().get(url) as response:  # closes static files
        assert response.status_code == 200


def test_production_journal_can_be_enabled_explicitly(db_path):
    client = _production(db_path, JOURNAL_ENABLED=True).test_client()
    assert client.get("/trades").status_code == 200


def test_journal_flag_follows_the_environment(monkeypatch, db_path):
    from tradeledger.config import env_flag

    monkeypatch.setenv("TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL", "1")
    assert env_flag("TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL") is True
    monkeypatch.setenv("TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL", "yes")
    assert env_flag("TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL") is False


def test_api_remains_disabled_in_production(db_path):
    """Phase 4's protection is untouched by the new website."""
    response = _production(db_path).test_client().get("/api/v1/trades")
    assert response.status_code == 403


# ---------- Information leakage ----------


def test_form_submissions_are_not_logged(app, seeded_client, caplog):
    with caplog.at_level(logging.DEBUG):
        post_form(seeded_client, "/trades/new", closed_trade_form(notes="PRIVATE-NOTE-123"))
        post_form(seeded_client, "/trades/new", closed_trade_form(notes="PRIVATE-NOTE-456", stop_loss="9"))
    assert "PRIVATE-NOTE" not in caplog.text
    token = csrf_token(seeded_client)
    assert token not in caplog.text


def test_csrf_error_page_reveals_no_token(seeded_client):
    real = csrf_token(seeded_client)
    response = seeded_client.post("/trades/1/delete", data={"csrf_token": "wrong"})
    assert b"wrong" not in response.data
    # The error page has no form, so the real token does not need to appear.
    assert real.encode() not in response.data


def test_500_page_is_generic(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path, "PROPAGATE_EXCEPTIONS": False})

    @app.route("/boom")
    def boom():
        raise RuntimeError("SELECT secret FROM trades")

    response = app.test_client().get("/boom")
    assert response.status_code == 500
    assert b"Something went wrong" in response.data
    assert b"SELECT" not in response.data
```

### `scripts/benchmark_dashboard.py` (MODIFIED)

**Why:** 100-trade size, SQL time, peak memory, chart-data size, --profile; asserts replaced by explicit checks.  
**How to run or test:** Run: `python scripts/benchmark_dashboard.py [--profile]`

```python
"""Time the /analytics dashboard on large synthetic journals.

    python scripts/benchmark_dashboard.py              # 100, 1,000 and 10,000 trades
    python scripts/benchmark_dashboard.py 5000         # a custom size
    python scripts/benchmark_dashboard.py --profile    # also show where the time goes

Creates a throwaway SQLite database in a temporary folder (never your real
one), inserts N synthetic closed trades spread over several instruments,
strategies, sessions and two years, then measures, as the median of 5 runs:

  sql        the database query alone (list_trades with realised dates)
  report     build_report(): SQL query + Decimal calculations
  dashboard  build_dashboard() + chart_payload(): the above plus chart prep
  page       a full GET /analytics through Flask, including the template
  filtered   GET /analytics with strategy + session + date filters
  memory     peak Python memory allocated while rendering one page (tracemalloc)
  size       the HTML page, and the chart data (JSON) inside it

Numbers depend on the machine; the script prints the Python version and
platform so results can be compared honestly. They are not guarantees.
"""

import cProfile
import json
import os
import platform
import pstats
import re
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradeledger import create_app
from tradeledger.db import get_db, init_schema
from tradeledger.performance import build_report
from tradeledger.performance.dashboard import build_dashboard, chart_payload
from tradeledger.report_filters import ReportFilters
from tradeledger.repositories.trades import list_trades

RUNS = 5
STRATEGIES = ("Breakout", "Pullback", "Reversal", None)
SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "OTHER", None)
EXITS = ("102", "99", "100.5", "99", "101.25", "100")  # +2, -1, +0.5, -1, +1.25, 0 R


def populate(conn, count):
    instruments = []
    for n, currency in enumerate(("USD", "USD", "GBP", "USD", "EUR")):
        cursor = conn.execute(
            "INSERT INTO instruments (symbol, name, asset_class, price_increment, contract_multiplier,"
            " quote_currency) VALUES (?, ?, 'OTHER', '0.01', '1', ?)",
            (f"BENCH{n}", f"Benchmark {n}", currency),
        )
        instruments.append(cursor.lastrowid)
    rows = []
    for n in range(count):
        day = n * 730 // count  # spread over two years
        month, dom = 1 + (day // 28) % 12, 1 + day % 28
        year = 2025 + day // 336
        entered = f"{year}-{month:02d}-{dom:02d}T08:{n % 60:02d}:00Z"
        closed = f"{year}-{month:02d}-{dom:02d}T{9 + n % 12:02d}:{n % 60:02d}:00Z"
        rows.append(
            (
                instruments[n % len(instruments)],
                entered,
                closed,
                EXITS[n % len(EXITS)],
                STRATEGIES[n % len(STRATEGIES)],
                SESSIONS[n % len(SESSIONS)],
            )
        )
    conn.executemany(
        "INSERT INTO trades (instrument_id, trade_date, closed_at, direction, entry_price, stop_loss,"
        " exit_price, position_size, fees, status, strategy, session)"
        " VALUES (?, ?, ?, 'LONG', '100', '99', ?, '1', '0', 'CLOSED', ?, ?)",
        rows,
    )
    conn.commit()


def median_ms(action):
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        action()
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times)


def peak_memory_mb(action):
    tracemalloc.start()
    action()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak / (1024 * 1024)


def chart_data_kb(html):
    match = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S)
    return len(match.group(1).encode()) / 1024 if match else 0.0


def profile(action, top=12):
    """Print where `action` spends its time, sorted two ways.

    cumulative: time in a function INCLUDING everything it calls; shows
                which high-level step is slow (e.g. build_dashboard).
    tottime:    time spent INSIDE the function itself; shows the actual
                hot spot. In Phase 7 this view revealed datetime.strptime
                being called ~50,000 times at 10,000 trades.
    """
    profiler = cProfile.Profile()
    profiler.runcall(action)
    for order in ("cumulative", "tottime"):
        print(f"\n--- sorted by {order} ---")
        pstats.Stats(profiler).sort_stats(order).print_stats(top)


def benchmark(count, show_profile=False):
    with tempfile.TemporaryDirectory() as folder:
        app = create_app("testing", overrides={"DATABASE": os.path.join(folder, "bench.db")})
        with app.app_context():
            conn = get_db()
            init_schema(conn)
            populate(conn, count)
            if build_report(conn).overall.trades != count:
                raise SystemExit(f"setup failed: expected {count} closed trades")
            sql_ms = median_ms(lambda: list_trades(conn, date_field="realised"))
            report_ms = median_ms(lambda: build_report(conn))
            dashboard_ms = median_ms(lambda: chart_payload(build_dashboard(conn, ReportFilters())))
        client = app.test_client()
        html = client.get("/analytics").get_data(as_text=True)
        if "dashboard-data" not in html:
            raise SystemExit("the dashboard did not render")
        page_ms = median_ms(lambda: client.get("/analytics"))
        filtered_ms = median_ms(
            lambda: client.get("/analytics?strategy=Breakout&session=LONDON&start=2025-06-01&end=2026-06-30")
        )
        memory = peak_memory_mb(lambda: client.get("/analytics"))
        if show_profile:
            print(f"\n--- where GET /analytics spends its time ({count:,} trades) ---")
            profile(lambda: client.get("/analytics"))
    result = {
        "trades": count,
        "sql_ms": sql_ms,
        "report_ms": report_ms,
        "dashboard_ms": dashboard_ms,
        "page_ms": page_ms,
        "filtered_ms": filtered_ms,
        "peak_mb": memory,
        "page_kb": len(html.encode()) / 1024,
        "chart_kb": chart_data_kb(html),
    }
    print(
        f"{count:>7,} trades | sql {sql_ms:7.1f} ms | report {report_ms:7.1f} ms | "
        f"dashboard {dashboard_ms:7.1f} ms | page {page_ms:7.1f} ms | filtered {filtered_ms:6.1f} ms | "
        f"peak {memory:5.1f} MB | page {result['page_kb']:7,.0f} KB (chart data {result['chart_kb']:6,.0f} KB)"
    )
    return result


if __name__ == "__main__":
    args = [arg for arg in sys.argv[1:] if arg != "--profile"]
    print(f"Python {platform.python_version()} on {platform.platform()}; median of {RUNS} runs")
    results = [
        benchmark(size, "--profile" in sys.argv) for size in [int(a) for a in args] or [100, 1000, 10000]
    ]
    print(json.dumps(results))
```

### `scripts/qa_dashboard_agreement.py` (MODIFIED)

**Why:** zip(strict=...) made explicit; formatting.  
**How to run or test:** Run: `python scripts/qa_dashboard_agreement.py` (needs Node.js)

```python
"""Phase 6 QA: does everything the dashboard shows agree with an
INDEPENDENT recalculation from the raw database rows?

An audit, not part of pytest (it takes ~15 s and needs Node.js). It builds
a throwaway database (the sample data plus 600 random trades in three
currencies, 5% open, 3% with no exit time), recomputes every figure from
the raw SQL rows with its OWN code (it never calls the engine), then
compares four outputs against that for every filter combination:
  1. the HTML page (KPI cards, money strip, all detailed tables)
  2. the chart payload embedded in the page
  3. the JSON API (/api/v1/analytics/*)
  4. the tooltip text produced by the REAL analytics.js (run in Node)

Usage (from the project folder):
    python scripts/qa_dashboard_agreement.py            # finds node on PATH
    python scripts/qa_dashboard_agreement.py --node /path/to/node

Exit code 0 = no disagreements. Precision trap: every Decimal sum here runs
inside localcontext(prec=80); Python's default context keeps only 28 digits.
"""

import itertools
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from html import unescape
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema  # noqa: E402
from tradeledger.repositories.instruments import create_instrument  # noqa: E402
from tradeledger.repositories.trades import create_trade  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

TOL = Decimal("1e-30")
SESSION_LABELS = {
    "ASIA": "Asia",
    "LONDON": "London",
    "NEW_YORK": "New York",
    "OTHER": "Other",
    None: "Unspecified",
}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
problems = []
checks = 0


def check(condition, message):
    global checks
    checks += 1
    if not condition:
        problems.append(message)


# ---------- My own formatting (written from the documented rules) ----------


def q(value, places):
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def fmt_r(v):
    if v is None:
        return "n/a"
    r = q(v, "0.01")
    return "0.00R" if r == 0 else f"{r:+}R"


def fmt_rate(wins, total):
    return "n/a" if total == 0 else f"{q(Decimal(wins) * 100 / Decimal(total), '0.1')}%"


def fmt_ratio(v):
    return "n/a" if v is None else str(q(v, "0.01"))


# ---------- Build a database: sample data + 600 random trades ----------


def build_database(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path})
    rnd = random.Random(20261115)
    with app.app_context():
        conn = get_db()
        init_schema(conn)
        seed_sample_data(conn)
        extra = [
            create_instrument(
                conn,
                {
                    "symbol": sym,
                    "name": sym,
                    "asset_class": "OTHER",
                    "price_increment": "0.01",
                    "contract_multiplier": mult,
                    "quote_currency": cur,
                },
            )
            for sym, mult, cur in (("QAUSD", "10", "USD"), ("QAGBP", "1", "GBP"), ("QAEUR", "0.5", "EUR"))
        ]
        strategies = ["Breakout", "Pullback", "Scalp", "Fade", None]
        sessions = ["ASIA", "LONDON", "NEW_YORK", "OTHER", None]
        for _ in range(600):
            direction = rnd.choice(["LONG", "SHORT"])
            entry = Decimal(rnd.randint(5000, 20000)) / 100
            risk = Decimal(rnd.randint(10, 300)) / 100
            stop = entry - risk if direction == "LONG" else entry + risk
            move = Decimal(rnd.randint(-400, 400)) / 100
            exit_price = max(Decimal("0.01"), entry + move)
            start = datetime(2025, 11, 1, tzinfo=UTC)
            entered = start + (datetime(2026, 4, 30, tzinfo=UTC) - start) * rnd.random()
            entered = entered.replace(microsecond=0)
            closed = entered.timestamp() + rnd.randint(60, 5 * 86400)
            closed = datetime.fromtimestamp(closed, UTC)
            is_open = rnd.random() < 0.05
            create_trade(
                conn,
                {
                    "instrument_id": rnd.choice(extra),
                    "direction": direction,
                    "status": "OPEN" if is_open else "CLOSED",
                    "trade_date": entered.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "closed_at": None if is_open else closed.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "entry_price": str(entry),
                    "stop_loss": str(stop),
                    "exit_price": None if is_open else str(exit_price),
                    "position_size": str(rnd.choice(["1", "2", "0.5", "3"])),
                    "fees": str(Decimal(rnd.randint(0, 300)) / 100),
                    "strategy": rnd.choice(strategies),
                    "session": rnd.choice(sessions),
                },
            )
        # 3% legacy trades: exit time never recorded.
        ids = [r[0] for r in conn.execute("SELECT id FROM trades WHERE status='CLOSED' AND id > 16")]
        for trade_id in rnd.sample(ids, len(ids) * 3 // 100):
            conn.execute("UPDATE trades SET closed_at = NULL WHERE id = ?", (trade_id,))
        conn.commit()
    return app


# ---------- Independent recalculation from raw rows ----------


def raw_rows(app):
    with app.app_context():
        return [
            dict(r)
            for r in get_db().execute(
                "SELECT t.*, i.symbol, i.contract_multiplier, i.quote_currency "
                "FROM trades t JOIN instruments i ON i.id = t.instrument_id"
            )
        ]


def independent(rows, f):
    with localcontext() as ctx:
        ctx.prec = 80
        out = []
        open_count = 0
        for t in rows:
            if f["instrument_id"] and t["instrument_id"] != f["instrument_id"]:
                continue
            if f["strategy"] and t["strategy"] != f["strategy"]:
                continue
            if f["session"] and t["session"] != f["session"]:
                continue
            if t["status"] == "OPEN":
                open_count += 1 if not (f["start"] or f["end"]) else 0
                continue
            realised = t["closed_at"] or t["trade_date"]
            day = realised[:10]
            if (f["start"] and day < f["start"]) or (f["end"] and day > f["end"]):
                continue
            sign = 1 if t["direction"] == "LONG" else -1
            entry, stop, exit_ = (Decimal(t[k]) for k in ("entry_price", "stop_loss", "exit_price"))
            size, mult = Decimal(t["position_size"]), Decimal(t["contract_multiplier"])
            net = (exit_ - entry) * sign * size * mult - Decimal(t["fees"] or "0")
            risk = abs(entry - stop) * size * mult
            out.append(
                {
                    "id": t["id"],
                    "when": realised,
                    "symbol": t["symbol"],
                    "net": net,
                    "r": net / risk,
                    "cur": t["quote_currency"],
                    "strategy": t["strategy"],
                    "session": t["session"],
                }
            )
        out.sort(key=lambda x: (x["when"], x["id"]))
        return out, open_count


def summary(trades):
    with localcontext() as ctx:
        ctx.prec = 80
        n = len(trades)
        wins = sum(1 for t in trades if t["net"] > 0)
        losses = sum(1 for t in trades if t["net"] < 0)
        total = sum((t["r"] for t in trades), Decimal(0))
        gains = sum((t["r"] for t in trades if t["r"] > 0), Decimal(0))
        pains = -sum((t["r"] for t in trades if t["r"] < 0), Decimal(0))
        cum, peak, worst = Decimal(0), Decimal(0), Decimal(0)
        for t in trades:
            cum += t["r"]
            peak = max(peak, cum)
            worst = max(worst, peak - cum)
        streaks = {"WIN": 0, "LOSS": 0}
        run_kind, run = None, 0
        for t in trades:
            kind = "WIN" if t["net"] > 0 else "LOSS" if t["net"] < 0 else None
            run = run + 1 if kind and kind == run_kind else (1 if kind else 0)
            run_kind = kind
            if kind:
                streaks[kind] = max(streaks[kind], run)
        return {
            "trades": n,
            "wins": wins,
            "losses": losses,
            "breakevens": n - wins - losses,
            "total_r": total,
            "mean_r": total / n if n else None,
            "pf": gains / pains if pains else None,
            "max_dd": worst,
            "win_streak": streaks["WIN"],
            "loss_streak": streaks["LOSS"],
        }


def grouped(trades, key):
    groups = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: summary(v) for k, v in groups.items()}


# ---------- Reading the page ----------


def kpis(html):
    return dict(re.findall(r'data-kpi="([a-z_]+)">.*?class="kpi__value">([^<]*)<', html, re.S))


def strip(fragment):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def table(html, table_id):
    match = re.search(rf'id="{table_id}".*?<tbody>(.*?)</tbody>', html, re.S)
    if not match:
        return None
    rows = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.S):
        head = strip(re.search(r"<th[^>]*>(.*?)</th>", row, re.S).group(1))
        head = head.replace("Small sample", "").strip()
        rows[head] = [strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
    return rows


def payload_of(html):
    m = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


# ---------- Compare one filter combination ----------


def compare(client, rows, f, collected):
    query = "&".join(f"{k}={v}" for k, v in f.items() if v)
    tag = query or "(no filters)"
    response = client.get("/analytics?" + query)
    check(response.status_code == 200, f"{tag}: page status {response.status_code}")
    html = response.get_data(as_text=True)
    trades, _ = independent(rows, f)
    s = summary(trades)
    k = kpis(html)

    expected = {"trades": str(s["trades"])}
    if s["trades"]:
        expected.update(
            {
                "wins": str(s["wins"]),
                "losses": str(s["losses"]),
                "breakevens": str(s["breakevens"]),
                "win_rate": fmt_rate(s["wins"], s["trades"]),
                "average_r": fmt_r(s["mean_r"]),
                "expectancy_r": fmt_r(s["mean_r"]),
                "profit_factor_r": fmt_ratio(s["pf"]),
                "total_r": fmt_r(s["total_r"]),
                "max_drawdown_r": f"{q(s['max_dd'], '0.01')}R",
                "longest_win_streak": f"{s['win_streak']} trade" + ("" if s["win_streak"] == 1 else "s"),
                "longest_loss_streak": f"{s['loss_streak']} trade" + ("" if s["loss_streak"] == 1 else "s"),
            }
        )
    else:
        expected.update(
            dict.fromkeys(
                (
                    "wins",
                    "win_rate",
                    "average_r",
                    "expectancy_r",
                    "profit_factor_r",
                    "total_r",
                    "max_drawdown_r",
                ),
                "n/a",
            )
        )
    for key, value in expected.items():
        check(k.get(key) == value, f"{tag}: KPI {key} page={k.get(key)!r} independent={value!r}")

    # Money per currency, never combined.
    money = defaultdict(Decimal)
    for t in trades:
        money[t["cur"]] += t["net"]
    for cur, net in money.items():
        check(f"{q(net, '0.01')} {cur}" in html, f"{tag}: money {q(net, '0.01')} {cur} missing")

    api = {
        name: client.get(f"/api/v1/analytics/{name}?{query}").get_json()
        for name in ("summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve")
    }
    o = api["summary"]["data"]["overall"]
    check(o["trades"] == s["trades"], f"{tag}: API trades")
    check(abs(Decimal(o["total_r"]) - s["total_r"]) < TOL, f"{tag}: API total_r")

    if not s["trades"]:
        check(payload_of(html) is None, f"{tag}: payload present with no results")
        return

    payload = payload_of(html)
    collected.append({"tag": tag, "payload": payload})

    # Curve: same trades, same order, same exact cumulative values.
    cum = Decimal(0)
    points = payload["curve"][1:]
    check([p["trade_id"] for p in points] == [t["id"] for t in trades], f"{tag}: curve order")
    check(
        [p["trade_id"] for p in api["curve"]["data"]] == [t["id"] for t in trades], f"{tag}: API curve order"
    )
    # strict=False: a length mismatch is already reported by the order check above.
    for p, t in zip(points, trades, strict=False):
        with localcontext() as ctx:  # same precision as independent()
            ctx.prec = 80
            cum += t["r"]
        check(abs(Decimal(p["cumulative_r"]) - cum) < TOL, f"{tag}: curve #{t['id']} cumulative")
        check(p["cumulative_text"] == fmt_r(cum), f"{tag}: curve #{t['id']} text")
    check(abs(Decimal(payload["max_drawdown"]["amount"]) - s["max_dd"]) < TOL, f"{tag}: payload max drawdown")

    # Groups: page table, payload and API all equal the independent figures.
    specs = [
        ("instruments", "table-instruments", lambda t: t["symbol"], lambda key: key, "instruments"),
        (
            "strategies",
            "table-strategies",
            lambda t: t["strategy"] or "Unspecified",
            lambda key: key,
            "strategies",
        ),
        ("sessions", "table-sessions", lambda t: t["session"], lambda key: SESSION_LABELS[key], "sessions"),
    ]
    for name, table_id, key, label, api_name in specs:
        truth = {label(g): v for g, v in grouped(trades, key).items()}
        page_rows = table(html, table_id)
        check(
            set(page_rows) == set(truth), f"{tag}: {name} table rows {sorted(page_rows)} vs {sorted(truth)}"
        )
        for group, v in truth.items():
            cells = page_rows.get(group)
            if not cells:
                continue
            check(cells[0] == str(v["trades"]), f"{tag}: {name}/{group} trades")
            check(
                cells[1] == f"{v['wins']} / {v['losses']} / {v['breakevens']}", f"{tag}: {name}/{group} w/l/b"
            )
            check(cells[2] == fmt_rate(v["wins"], v["trades"]), f"{tag}: {name}/{group} win rate")
            check(
                cells[5] == fmt_r(v["total_r"]),
                f"{tag}: {name}/{group} total {cells[5]} vs {fmt_r(v['total_r'])}",
            )
        pay = {p["label"]: p for p in payload[name]}
        check(set(pay) == set(truth), f"{tag}: {name} payload labels")
        for group, v in truth.items():
            if group in pay:
                check(
                    abs(Decimal(pay[group]["total_r"]) - v["total_r"]) < TOL, f"{tag}: {name}/{group} payload"
                )
        api_rows = {
            (
                SESSION_LABELS[g["group"]]
                if name == "sessions" and g["group"] in SESSION_LABELS
                else g["group"]
            ): g
            for g in api[api_name]["data"]
        }
        check(set(api_rows) == set(truth), f"{tag}: {name} API groups {sorted(api_rows)}")
        # Order: best total R first (instruments/strategies).
        if name != "sessions":
            order = [p["label"] for p in payload[name]]
            check(order == sorted(order, key=lambda g: (-truth[g]["total_r"], g)), f"{tag}: {name} order")

    # Months: every month from first to last, gaps kept.
    month_truth = grouped(trades, lambda t: t["when"][:7])
    first, last = min(month_truth), max(month_truth)
    expected_keys, y, m = [], int(first[:4]), int(first[5:])
    while f"{y:04d}-{m:02d}" <= last:
        expected_keys.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    check([p["key"] for p in payload["monthly"]] == expected_keys, f"{tag}: month keys")
    for p in payload["monthly"]:
        v = month_truth.get(p["key"])
        if v is None:
            check(p["total_r"] is None, f"{tag}: empty month {p['key']} not null")
        else:
            check(abs(Decimal(p["total_r"]) - v["total_r"]) < TOL, f"{tag}: month {p['key']}")
    month_table = table(html, "table-monthly")
    check(len(month_table) == len(expected_keys), f"{tag}: month table rows")

    # Weekdays, UTC.
    def weekday(t):
        return WEEKDAYS[datetime.strptime(t["when"], "%Y-%m-%dT%H:%M:%SZ").weekday()]

    day_truth = grouped(trades, weekday)
    check([p["key"] for p in payload["weekday"]] == WEEKDAYS, f"{tag}: weekday order")
    day_table = table(html, "table-weekday")
    for p in payload["weekday"]:
        v = day_truth.get(p["key"])
        cells = day_table[p["key"]]
        if v is None:
            check(p["total_r"] is None and cells == ["No closed trades"], f"{tag}: {p['key']} empty")
        else:
            check(abs(Decimal(p["total_r"]) - v["total_r"]) < TOL, f"{tag}: weekday {p['key']}")
            check(cells[5] == fmt_r(v["total_r"]), f"{tag}: weekday table {p['key']}")


NODE_SCRIPT = r"""
const fs = require("fs");
const charts = require(process.argv[2]);
const items = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const theme = {text: "#fff", muted: "#aaa", grid: "#333", zero: "#999", accent: "#00f",
               positive: "#0f0", negative: "#f00", font: "sans-serif"};
const out = items.map(({tag, payload}) => {
  const c = charts.buildConfigs(payload, theme);
  const res = {tag};
  for (const name of Object.keys(c)) {
    const cb = c[name].options.plugins.tooltip.callbacks;
    const n = c[name].data.datasets[0].data.length;
    res[name] = {labels: c[name].data.labels || null, data: c[name].data.datasets[0].data,
                 tooltips: Array.from({length: n}, (_, i) => cb.label({dataIndex: i})),
                 titles: Array.from({length: n}, (_, i) => cb.title ? cb.title([{dataIndex: i}]) : null)};
  }
  return res;
});
fs.writeFileSync(process.argv[4], JSON.stringify(out));
"""


def check_tooltips(collected, node):
    work = Path(tempfile.mkdtemp())
    (work / "run.js").write_text(NODE_SCRIPT)
    (work / "in.json").write_text(json.dumps(collected))
    subprocess.run(
        [
            node,
            str(work / "run.js"),
            str(PROJECT / "tradeledger/static/js/analytics.js"),
            str(work / "in.json"),
            str(work / "out.json"),
        ],
        check=True,
    )
    results = json.loads((work / "out.json").read_text())
    for item, res in zip(collected, results, strict=True):
        tag, payload = item["tag"], item["payload"]
        # Curve: every point's tooltip quotes the server's exact display text,
        # and the plotted y equals the exact value converted once.
        for i, p in enumerate(payload["curve"]):
            lines = res["curve"]["tooltips"][i]
            check(res["curve"]["data"][i]["y"] == float(Decimal(p["cumulative_r"])), f"{tag}: curve y {i}")
            if not p.get("start"):
                check(f"Cumulative R: {p['cumulative_text']}" in lines, f"{tag}: curve tooltip {i}")
                check(f"Trade R: {p['realised_text']}" in lines, f"{tag}: trade R tooltip {i}")
                check(
                    ("Exit time not recorded: placed at entry time" in lines) == (not p["close_time_known"]),
                    f"{tag}: legacy note {i}",
                )
            dd = res["drawdown"]["tooltips"][i][0]
            check(
                dd
                == (
                    f"Drawdown: {p['drawdown_text']} below the running peak"
                    if Decimal(p["underwater_r"]) != 0
                    else dd
                ),
                f"{tag}: drawdown tooltip {i}",
            )
        # Bars: tooltip text equals payload text; null periods stay null.
        for name in ("monthly", "weekday", "instruments", "strategies", "sessions"):
            for i, row in enumerate(payload[name]):
                lines = res[name]["tooltips"][i]
                if row["total_r"] is None:
                    check(
                        res[name]["data"][i] is None and lines == ["No closed trades"],
                        f"{tag}: {name} gap {i}",
                    )
                else:
                    check(lines[0] == f"Total R: {row['total_r_text']}", f"{tag}: {name} tooltip {i}")
                    check(
                        f"Closed trades: {row['trades']} (won {row['wins']}, lost {row['losses']})" in lines,
                        f"{tag}: {name} count tooltip {i}",
                    )
                    check(res[name]["data"][i] == float(Decimal(row["total_r"])), f"{tag}: {name} value {i}")
    return len(results)


def main():
    node = sys.argv[sys.argv.index("--node") + 1] if "--node" in sys.argv else shutil.which("node")
    if not node:
        sys.exit("Node.js is needed to run analytics.js; pass --node /path/to/node")
    work = tempfile.mkdtemp()
    app = build_database(str(Path(work) / "qa.db"))
    rows = raw_rows(app)
    client = app.test_client()
    with app.app_context():
        instrument_ids = [r[0] for r in get_db().execute("SELECT id FROM instruments ORDER BY id")]
    strategies = [None, "Breakout", "Pullback", "Scalp", "Reversal"]
    sessions = [None, "ASIA", "LONDON", "NEW_YORK", "OTHER"]
    dates = [(None, None), ("2026-01-01", "2026-02-28"), ("2025-12-15", None), ("2026-03-02", "2026-03-02")]
    collected = []
    combos = 0
    for inst, strat, sess, (start, end) in itertools.product(
        [None, *instrument_ids], strategies, sessions, dates
    ):
        f = {"instrument_id": inst, "strategy": strat, "session": sess, "start": start, "end": end}
        compare(client, rows, f, collected)
        combos += 1
    charted = check_tooltips(collected, node)
    closed = sum(1 for r in rows if r["status"] == "CLOSED")
    legacy = sum(1 for r in rows if r["status"] == "CLOSED" and not r["closed_at"])
    print(f"trades in database: {len(rows)} ({closed} closed, {legacy} with no exit time)")
    print(f"filter combinations: {combos}; with results (charts checked in Node): {charted}")
    print(f"individual comparisons: {checks}; disagreements: {len(problems)}")
    for p in problems[:30]:
        print("  -", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
```

### `.env.example` (MODIFIED)

**Why:** Adds TRADELEDGER_TRUSTED_HOSTS.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```bash
# Copy this file to .env and fill in real values.
# .env is ignored by Git - never commit it.

# Which config class to use: development, testing or production
TRADELEDGER_CONFIG=development

# Generate your own with:  python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=replace-me-with-a-long-random-string

# Host names this server answers to (Phase 7; protects against DNS
# rebinding). Default in development: localhost,127.0.0.1. Add your LAN IP
# if you open TradeLedger from another device. "*" switches the check off.
# TRADELEDGER_TRUSTED_HOSTS=localhost,127.0.0.1

# Optional: where the SQLite file lives. Leave commented out to use
# instance/tradeledger.db inside the project folder.
# DATABASE_PATH=/full/path/to/tradeledger.db

# Optional (Phase 6): where the dashboard loads Chart.js from. Default is the
# pinned jsDelivr CDN URL for version 4.5.1 (needs internet in the browser).
# To self-host, see docs/THIRD_PARTY_NOTICES.md, then:
# TRADELEDGER_CHART_JS_URL=/static/vendor/chartjs/chart.umd.min.js
# Optional Subresource Integrity hash for that file ("sha384-..."):
# TRADELEDGER_CHART_JS_INTEGRITY=
```

### `.gitignore` (MODIFIED)

**Why:** Adds coverage.xml, .ruff_cache/, artifacts/ and CI download leftovers.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```gitignore
# Virtual environments
.venv/
venv/
env/

# Secrets and local settings
.env
*.env
!.env.example

# Python caches and build output
__pycache__/
*.py[cod]
*.egg-info/
build/
dist/

# Test, coverage and lint output
.pytest_cache/
.coverage
.coverage.*
coverage.xml
htmlcov/
.ruff_cache/
artifacts/

# Downloaded in CI only (npm pack chart.js), never committed from there
chart.js-*.tgz
/package/

# Databases (may contain personal trade data)
*.db
*.sqlite
*.sqlite3
instance/

# Logs
*.log

# Editor and OS files
.vscode/
.idea/
.DS_Store
Thumbs.db
```

### `requirements.txt` (MODIFIED)

**Why:** Runtime dependencies only (Flask, python-dotenv).  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```text
# Runtime dependencies: what TradeLedger needs to run.
# Exact versions (==) make installs repeatable. Dependencies of these
# packages (Werkzeug, Jinja2, ...) are pinned in constraints.txt.
Flask==3.1.3
python-dotenv==1.2.3
```

## 3. Modified files: lint fixes only (behaviour unchanged)

### `tradeledger/api/http.py` (MODIFIED, lint)

**Why:** Ruff auto-fix: `dict.fromkeys(...)` instead of an equivalent dict comprehension (values are immutable strings). Behaviour unchanged.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Reading and checking HTTP input: JSON bodies and query strings.

These functions only check the SHAPE of a request (is it JSON? are the
field names allowed? is `page` a whole number?). The business rules for a
trade or instrument stay in validation.py, so the web forms in Phase 5 and
this API apply exactly the same rules.
"""

import json
from decimal import Decimal

from flask import current_app, request

from ..validation import ValidationError, parse_date
from .errors import ApiError

# Fields a client may send when creating or replacing a trade. Anything
# else (id, created_at, updated_at, symbol, ...) is rejected, so a client
# can never write a column it should not control ("mass assignment").
TRADE_INPUT_FIELDS = (
    "instrument_id",
    "trade_date",
    "closed_at",
    "direction",
    "status",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "strategy",
    "session",
    "notes",
)

INSTRUMENT_INPUT_FIELDS = (
    "symbol",
    "name",
    "asset_class",
    "price_increment",
    "contract_multiplier",
    "quote_currency",
)

DEFAULT_PER_PAGE = 20


def _reject_constant(name):
    # json.loads accepts NaN and Infinity by default; money must never be those.
    raise ValueError(f"{name} is not allowed")


def json_body():
    """Return the request body as a dict, or raise ApiError.

    * 415 if Content-Type is not application/json
    * 400 if the body is not valid JSON or is not a JSON object
    JSON numbers with a decimal point become Decimal (never float), so
    {"entry_price": 1.085} is read exactly. Strings such as "1.085" are the
    recommended form and are documented in the OpenAPI specification.
    """
    if request.mimetype != "application/json":
        raise ApiError(
            415,
            "unsupported_media_type",
            "Send the request body as JSON with Content-Type: application/json.",
        )
    try:
        text = request.get_data(as_text=False).decode("utf-8")
        body = json.loads(text, parse_float=Decimal, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError):
        raise ApiError(400, "malformed_json", "The request body is not valid JSON.") from None
    if not isinstance(body, dict):
        raise ApiError(400, "malformed_json", "The request body must be a JSON object.")
    return body


def check_fields(body, allowed, *, require_all=False):
    """Reject unknown field names; with require_all, every field must be present.

    require_all is used by PUT: a replacement must state every field
    (null is allowed for optional ones), so nothing is changed by omission.
    """
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise ApiError(
            400,
            "unknown_fields",
            "The request contains fields that cannot be set.",
            dict.fromkeys(unknown, "is not an accepted field"),
        )
    if require_all:
        missing = [name for name in allowed if name not in body]
        if missing:
            raise ApiError(
                400,
                "missing_fields",
                "PUT replaces the whole record, so every field must be supplied "
                "(use null for empty optional fields).",
                dict.fromkeys(missing, "is required for a full replacement"),
            )


def query_args(allowed):
    """Return the query string as a dict, rejecting unknown parameter names.
    Catching typos ('instrument' instead of 'instrument_id') is kinder than
    silently ignoring them and returning unfiltered data."""
    unknown = sorted(set(request.args) - set(allowed))
    if unknown:
        raise ApiError(
            400,
            "unknown_parameters",
            "The query string contains unknown parameters.",
            dict.fromkeys(unknown, "is not a supported parameter"),
        )
    return {name: request.args.get(name) for name in allowed if name in request.args}


def positive_int(args, name, *, default=None, maximum=None):
    raw = args.get(name)
    if raw is None:
        return default
    if not raw.isdigit() or int(raw) < 1:
        raise ValidationError({name: "must be a whole number of 1 or more"})
    value = int(raw)
    if maximum is not None and value > maximum:
        raise ValidationError({name: f"must be {maximum} or less"})
    return value


def date_arg(args, name):
    raw = args.get(name)
    if raw is None:
        return None
    try:
        return parse_date(raw).isoformat()
    except ValueError as exc:
        raise ValidationError({name: str(exc)}) from None


def status_arg(args):
    raw = args.get("status")
    if raw is None:
        return None
    value = raw.strip().upper()
    if value not in ("OPEN", "CLOSED"):
        raise ValidationError({"status": "must be OPEN or CLOSED"})
    return value


def pagination_args(args):
    page = positive_int(args, "page", default=1)
    per_page = positive_int(
        args, "per_page", default=DEFAULT_PER_PAGE, maximum=current_app.config["API_MAX_PER_PAGE"]
    )
    return page, per_page
```

### `tradeledger/cli.py` (MODIFIED, lint)

**Why:** Formatting plus `zip(..., strict=True)` where lengths must match; `noqa` with reason on one reviewed SQL line. Behaviour unchanged.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Command-line commands, run through Flask:

flask --app tradeledger init-db      create tables (keeps existing data)
flask --app tradeledger reset-db     delete ALL data and recreate tables
flask --app tradeledger seed-db      add synthetic sample data (repeatable)
flask --app tradeledger db-info      show tables, row counts and schema version
flask --app tradeledger analytics    run the analytical queries
flask --app tradeledger run-sql "SELECT ..."   run your own read-only query
flask --app tradeledger report       performance report (Phase 3 engine)
flask --app tradeledger migrate-db   back up, then apply pending schema migrations
flask --app tradeledger openapi      write the OpenAPI specification to a file
"""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from . import analytics, migrations
from .api.openapi import build_spec
from .db import drop_all, get_db, init_schema, schema_version
from .performance import build_report
from .performance.formatting import (
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
    to_jsonable,
)
from .repositories.instruments import get_instrument_by_symbol
from .seed import seed_sample_data
from .validation import ValidationError


def print_table(rows):
    """Print a list of dicts as an aligned text table."""
    if not rows:
        click.echo("(no rows)")
        return
    headers = list(rows[0].keys())
    cells = [[("NULL" if row[h] is None else str(row[h])) for h in headers] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    click.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
    click.echo("  ".join("-" * w for w in widths))
    for r in cells:
        click.echo("  ".join(value.ljust(w) for value, w in zip(r, widths, strict=True)))


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create any missing tables. Existing data is kept."""
    init_schema(get_db())
    click.echo(f"Database ready: {current_app.config['DATABASE']}")


@click.command("reset-db")
@with_appcontext
@click.confirmation_option(prompt="This deletes ALL trades and instruments. Continue?")
def reset_db_command():
    """Drop every table and recreate the empty schema."""
    conn = get_db()
    drop_all(conn)
    init_schema(conn)
    click.echo("Database reset. All tables are empty.")


@click.command("seed-db")
@with_appcontext
def seed_db_command():
    """Insert synthetic sample data. Running it twice adds nothing new."""
    conn = get_db()
    init_schema(conn)
    inserted = seed_sample_data(conn)
    click.echo(
        f"Sample data: {inserted['instruments']} instrument(s) and {inserted['trades']} trade(s) inserted."
    )


@click.command("db-info")
@with_appcontext
def db_info_command():
    """Show where the database is, its schema version and row counts."""
    conn = get_db()
    click.echo(f"Database file:  {current_app.config['DATABASE']}")
    click.echo(f"Schema version: {schema_version(conn)}")
    tables = [
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    ]
    if not tables:
        click.echo("No tables yet. Run: flask --app tradeledger init-db")
        return
    # Table names come from sqlite_master, not from user input, so it is safe
    # to place them in the SQL text. (Placeholders cannot stand for names.)
    rows = [
        {"table": name, "rows": conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]}  # noqa: S608 - name from sqlite_master
        for name in tables
    ]
    print_table(rows)


@click.command("analytics")
@with_appcontext
@click.argument("name", required=False, type=click.Choice(sorted(analytics.ALL_QUERIES)))
def analytics_command(name):
    """Run one analytical query by NAME, or all of them."""
    conn = get_db()
    names = [name] if name else list(analytics.ALL_QUERIES)
    for query_name in names:
        click.echo(f"\n== {query_name} ==")
        rows = [dict(row) for row in conn.execute(analytics.ALL_QUERIES[query_name])]
        print_table(rows)


@click.command("run-sql")
@with_appcontext
@click.argument("sql")
def run_sql_command(sql):
    """Run your own SQL against a READ-ONLY connection (it cannot change data)."""
    path = Path(current_app.config["DATABASE"]).resolve()
    if not path.exists():
        raise click.ClickException("Database file not found. Run init-db first.")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(sql).fetchall()]
    except sqlite3.Error as exc:
        raise click.ClickException(f"SQL error: {exc}") from exc
    finally:
        conn.close()
    print_table(rows)


def summary_row(label, summary):
    """One display row for a group summary (rounded for display only)."""
    return {
        "group": label,
        "trades": summary.trades,
        "W/L/BE": f"{summary.wins}/{summary.losses}/{summary.breakevens}",
        "win rate": format_rate(summary.win_rate),
        "total R": format_r(summary.total_r),
        "expectancy": format_r(summary.expectancy_r),
        "PF (R)": format_ratio(summary.profit_factor_r),
        "max DD": format_r_amount(summary.max_drawdown_r.amount),
    }


def print_report(report):
    o = report.overall
    click.echo("== Overall (closed trades) ==")
    lines = [
        ("Closed trades", o.trades),
        ("Open trades (not in results)", report.open_trades),
        ("Wins / losses / breakevens", f"{o.wins} / {o.losses} / {o.breakevens}"),
        ("Win rate / loss rate", f"{format_rate(o.win_rate)} / {format_rate(o.loss_rate)}"),
        ("Trades with realised R", o.r_trades),
        ("Total R (cumulative)", format_r(o.total_r)),
        ("Average R", format_r(o.average_r)),
        ("Average winning R", format_r(o.average_win_r)),
        ("Average losing R", format_r(o.average_loss_r)),
        ("Expectancy per trade", format_r(o.expectancy_r)),
        ("Profit factor (R)", format_ratio(o.profit_factor_r)),
        (
            "Average planned R:R",
            f"{format_ratio(o.average_planned_rr)} ({o.planned_rr_trades} trades with a target)",
        ),
        ("Longest win / loss streak", f"{o.longest_win_streak} / {o.longest_loss_streak}"),
        ("Max drawdown (R)", format_r_amount(o.max_drawdown_r.amount)),
    ]
    if o.best_trade_r:
        lines.append(
            (
                "Best trade (R)",
                f"#{o.best_trade_r.trade_id} {o.best_trade_r.symbol} {format_r(o.best_trade_r.value)}",
            )
        )
        lines.append(
            (
                "Worst trade (R)",
                f"#{o.worst_trade_r.trade_id} {o.worst_trade_r.symbol} {format_r(o.worst_trade_r.value)}",
            )
        )
    width = max(len(label) for label, _ in lines)
    for label, value in lines:
        click.echo(f"{label.ljust(width)}  {value}")

    click.echo("\n== Money, per quote currency (never added together) ==")
    print_table(
        [
            {
                "currency": m.currency,
                "trades": m.trades,
                "gross P&L": format_money(m.gross_pnl, m.currency),
                "fees": format_money(m.fees, m.currency),
                "net P&L": format_money(m.net_pnl, m.currency),
                "winners": format_money(m.winning_pnl, m.currency),
                "losers": format_money(m.losing_pnl, m.currency),
                "PF": format_ratio(m.profit_factor),
                "max DD": format_money(m.max_drawdown.amount, m.currency),
            }
            for m in o.money.values()
        ]
    )

    for title, groups in (
        ("By instrument", report.by_instrument),
        ("By strategy", report.by_strategy),
        ("By session", report.by_session),
        ("By day of week (UTC)", report.by_weekday),
        ("By month", report.by_month),
    ):
        click.echo(f"\n== {title} ==")
        print_table([summary_row(label, s) for label, s in groups.items()])

    click.echo("\n== Cumulative R (chart data; not account equity) ==")
    print_table(
        [
            {
                "trade": p.trade_id,
                "date": p.trade_date[:10],
                "symbol": p.symbol,
                "R": format_r(p.realised_r),
                "cumulative": format_r(p.cumulative_r),
            }
            for p in report.cumulative_r
        ]
    )

    for title, items in (
        ("Excluded trades", report.excluded),
        ("Closed trades without a realised R", report.r_unavailable),
    ):
        if items:
            click.echo(f"\n== {title} ==")
            for item in items:
                click.echo(f"#{item.trade_id}: {item.reason}")

    if report.unknown_close_time_trade_ids:
        ids = ", ".join(f"#{i}" for i in report.unknown_close_time_trade_ids)
        click.echo("\n== Closed trades with no recorded exit time (ordered by entry time) ==")
        click.echo(ids)


@click.command("report")
@click.option("--instrument", help="Only this symbol, e.g. XAUUSD.")
@click.option("--start", help="First date to include, YYYY-MM-DD (UTC).")
@click.option("--end", help="Last date to include, YYYY-MM-DD (UTC).")
@click.option("--json", "as_json", is_flag=True, help="Exact JSON output instead of a rounded table.")
@with_appcontext
def report_command(instrument, start, end, as_json):
    """Performance report calculated by the Python analytics engine."""
    conn = get_db()
    instrument_id = None
    if instrument:
        found = get_instrument_by_symbol(conn, instrument)
        if found is None:
            raise click.ClickException(f"No instrument with symbol '{instrument}'.")
        instrument_id = found["id"]
    try:
        report = build_report(conn, instrument_id=instrument_id, start=start, end=end)
    except ValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(to_jsonable(report), indent=2))
    else:
        print_report(report)


@click.command("migrate-db")
@with_appcontext
@click.option("--no-backup", is_flag=True, help="Skip the safety copy (not recommended).")
def migrate_db_command(no_backup):
    """Back up the database, then apply any pending schema migrations."""
    conn = get_db()
    before = schema_version(conn)
    if not migrations.pending_versions(conn) and before >= 1:
        click.echo(f"Already at schema version {before}. Nothing to do.")
        return
    if not no_backup and before >= 1:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = Path(current_app.config["DATABASE"])
        backup_path = path.with_name(f"{path.name}.v{before}-{stamp}.bak")
        migrations.backup(conn, str(backup_path))
        click.echo(f"Backup written: {backup_path}")
    applied = init_schema(conn)
    click.echo(f"Schema version {before} -> {schema_version(conn)} (applied: {applied or 'none'})")


@click.command("openapi")
@with_appcontext
@click.option(
    "--output", default="docs/openapi.json", show_default=True, help="File to write the specification to."
)
def openapi_command(output):
    """Write the OpenAPI 3.1 specification (the same one served by the API)."""
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(build_spec(), indent=2) + "\n", encoding="utf-8")
    click.echo(f"OpenAPI specification written to {output}")


def init_app(app):
    for command in (
        init_db_command,
        reset_db_command,
        seed_db_command,
        db_info_command,
        analytics_command,
        run_sql_command,
        report_command,
        migrate_db_command,
        openapi_command,
    ):
        app.cli.add_command(command)
```

### `tradeledger/performance/metrics.py` (MODIFIED, lint)

**Why:** Ruff auto-fix: `Optional[X]` → `X | None`, `List`/`Dict` → `list`/`dict` (Python 3.11 syntax). Behaviour unchanged.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Aggregate metrics over a list of TradeResults.

Formulas
--------
  win rate             wins / closed trades           (0-1; None if no trades)
  loss rate            losses / closed trades
  breakeven rate       breakevens / closed trades
  average R            sum(realised R) / trades with R
  average win R        mean R of winning trades
  average loss R       mean R of losing trades (a negative number)
  expectancy (R)       P(win) x avg win R + P(loss) x avg loss R
                       + P(breakeven) x 0, using only trades that have R.
                       Mathematically equal to average R; we compute it
                       from the parts because that is how it is explained.
  profit factor        sum of positive results / |sum of negative results|
                       None when there are no losing trades (division by
                       zero) or no trades at all.
  streaks              longest run of consecutive WINs / LOSSes in the
                       order results were realised. A breakeven ends both
                       kinds of streak.
  max drawdown         largest fall from a running peak of the cumulative
                       series, starting from 0 before the first trade.
                       Reported as a positive number in the series' unit
                       (R, or money in ONE currency). Never a percentage:
                       a percentage needs an account balance, which
                       TradeLedger does not record.

Money is always summarised per quote currency. Different currencies are
never added together.

Order of trades
---------------
Streaks, drawdown and cumulative series follow realised order: by
realised_at (exit time, or entry time for legacy trades with no recorded
exit), then by trade id so equal times always sort the same way.
"""

from dataclasses import dataclass, field
from decimal import Decimal, localcontext

from .trade_math import EXACT, ZERO, TradeResult


@dataclass(frozen=True)
class TradeRef:
    """Points at one trade, e.g. the best trade, with the value that ranked it."""

    trade_id: int
    symbol: str
    trade_date: str
    value: Decimal


@dataclass(frozen=True)
class Drawdown:
    amount: Decimal  # >= 0, in the unit of the series
    peak_trade_id: int | None  # None = the starting point (0)
    trough_trade_id: int | None  # None when there was no drawdown


@dataclass(frozen=True)
class MoneySummary:
    """Monetary results for ONE quote currency."""

    currency: str
    trades: int
    gross_pnl: Decimal  # before fees
    fees: Decimal
    net_pnl: Decimal  # after fees
    winning_pnl: Decimal  # sum of net P&L of winning trades (>= 0)
    losing_pnl: Decimal  # sum of net P&L of losing trades (<= 0)
    profit_factor: Decimal | None
    best_trade: TradeRef | None
    worst_trade: TradeRef | None
    max_drawdown: Drawdown


@dataclass(frozen=True)
class Summary:
    """Every metric for one group of closed trades."""

    trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate: Decimal | None
    loss_rate: Decimal | None
    breakeven_rate: Decimal | None
    r_trades: int  # trades with a usable realised R
    total_r: Decimal
    average_r: Decimal | None
    average_win_r: Decimal | None
    average_loss_r: Decimal | None
    expectancy_r: Decimal | None
    profit_factor_r: Decimal | None
    planned_rr_trades: int
    average_planned_rr: Decimal | None
    longest_win_streak: int
    longest_loss_streak: int
    max_drawdown_r: Drawdown
    best_trade_r: TradeRef | None
    worst_trade_r: TradeRef | None
    money: dict[str, MoneySummary] = field(default_factory=dict)


# ---------- Building blocks ----------


def chronological(results):
    """Sort into realised order: realised_at, then id, so ties always come
    out in the same order."""
    return sorted(results, key=lambda r: (r.realised_at, r.trade_id if r.trade_id is not None else 0))


def ratio(numerator, denominator):
    """numerator / denominator, or None if the denominator is zero."""
    if not denominator:
        return None
    with localcontext(EXACT):
        return Decimal(numerator) / Decimal(denominator)


def mean(values):
    values = list(values)
    if not values:
        return None
    with localcontext(EXACT):
        return sum(values, ZERO) / len(values)


def expectancy(win_rate, average_win, loss_rate, average_loss):
    """Expected R per trade from its parts. Missing parts count as zero
    only when their probability is zero (no wins -> no win term)."""
    if win_rate is None or loss_rate is None:
        return None
    with localcontext(EXACT):
        win_term = win_rate * average_win if average_win is not None else ZERO
        loss_term = loss_rate * average_loss if average_loss is not None else ZERO
        return win_term + loss_term


def profit_factor(values):
    # Everything, including the minus sign, stays inside the 60-digit
    # context: even unary minus rounds to the CURRENT precision (28 by
    # default). A test against exact fractions caught exactly that bug.
    with localcontext(EXACT):
        gains = sum((v for v in values if v > 0), ZERO)
        losses = sum((v for v in values if v < 0), ZERO)
        if losses == 0:
            return None
        return gains / -losses


def longest_streaks(outcomes):
    """Return (longest WIN run, longest LOSS run) for outcomes in order."""
    best = {"WIN": 0, "LOSS": 0}
    current_kind, current_length = None, 0
    for outcome in outcomes:
        if outcome == current_kind:
            current_length += 1
        else:
            current_kind, current_length = outcome, 1
        if outcome in best:
            best[outcome] = max(best[outcome], current_length)
    return best["WIN"], best["LOSS"]


def cumulative(values):
    totals, running = [], ZERO
    with localcontext(EXACT):
        for value in values:
            running += value
            totals.append(running)
    return totals


def max_drawdown(points):
    """points: list of (trade_id, value) in date order.

    Walks the running total, remembering the highest point so far. The
    drawdown at each step is peak - current; we keep the largest.
    """
    peak_value, peak_id = ZERO, None
    worst = Drawdown(ZERO, None, None)
    running = ZERO
    with localcontext(EXACT):
        for trade_id, value in points:
            running += value
            if running > peak_value:
                peak_value, peak_id = running, trade_id
            fall = peak_value - running
            if fall > worst.amount:
                worst = Drawdown(fall, peak_id, trade_id)
    return worst


def _best_and_worst(results, value_of):
    candidates = [(value_of(r), r) for r in results if value_of(r) is not None]
    if not candidates:
        return None, None
    # Ties go to the earliest trade, because candidates are in date order
    # and max()/min() keep the first of equal items.
    best_value, best = max(candidates, key=lambda c: c[0])
    worst_value, worst = min(candidates, key=lambda c: c[0])
    return (
        TradeRef(best.trade_id, best.symbol, best.trade_date, best_value),
        TradeRef(worst.trade_id, worst.symbol, worst.trade_date, worst_value),
    )


# ---------- Summaries ----------


def summarise_money(results, currency):
    """Monetary summary for the trades in ONE currency."""
    rows = [r for r in chronological(results) if r.quote_currency == currency]
    with localcontext(EXACT):
        nets = [r.net_pnl for r in rows]
        best, worst = _best_and_worst(rows, lambda r: r.net_pnl)
        return MoneySummary(
            currency=currency,
            trades=len(rows),
            gross_pnl=sum((r.gross_pnl for r in rows), ZERO),
            fees=sum((r.fees for r in rows), ZERO),
            net_pnl=sum(nets, ZERO),
            winning_pnl=sum((n for n in nets if n > 0), ZERO),
            losing_pnl=sum((n for n in nets if n < 0), ZERO),
            profit_factor=profit_factor(nets),
            best_trade=best,
            worst_trade=worst,
            max_drawdown=max_drawdown([(r.trade_id, r.net_pnl) for r in rows]),
        )


def summarise(results: list[TradeResult]) -> Summary:
    """All metrics for a group of closed trades (any order; sorted here)."""
    rows = chronological(results)
    total = len(rows)
    wins = sum(1 for r in rows if r.outcome == "WIN")
    losses = sum(1 for r in rows if r.outcome == "LOSS")
    breakevens = total - wins - losses

    with_r = [r for r in rows if r.realised_r is not None]
    r_values = [r.realised_r for r in with_r]
    win_rs = [r.realised_r for r in with_r if r.outcome == "WIN"]
    loss_rs = [r.realised_r for r in with_r if r.outcome == "LOSS"]

    average_win_r = mean(win_rs)
    average_loss_r = mean(loss_rs)
    planned = [r.planned_rr for r in rows if r.planned_rr is not None]
    win_streak, loss_streak = longest_streaks(r.outcome for r in rows)
    best_r, worst_r = _best_and_worst(with_r, lambda r: r.realised_r)

    with localcontext(EXACT):
        total_r = sum(r_values, ZERO)

    return Summary(
        trades=total,
        wins=wins,
        losses=losses,
        breakevens=breakevens,
        win_rate=ratio(wins, total),
        loss_rate=ratio(losses, total),
        breakeven_rate=ratio(breakevens, total),
        r_trades=len(with_r),
        total_r=total_r,
        average_r=mean(r_values),
        average_win_r=average_win_r,
        average_loss_r=average_loss_r,
        expectancy_r=expectancy(
            ratio(len(win_rs), len(with_r)),
            average_win_r,
            ratio(len(loss_rs), len(with_r)),
            average_loss_r,
        ),
        profit_factor_r=profit_factor(r_values),
        planned_rr_trades=len(planned),
        average_planned_rr=mean(planned),
        longest_win_streak=win_streak,
        longest_loss_streak=loss_streak,
        max_drawdown_r=max_drawdown([(r.trade_id, r.realised_r) for r in with_r]),
        best_trade_r=best_r,
        worst_trade_r=worst_r,
        money={
            currency: summarise_money(rows, currency) for currency in sorted({r.quote_currency for r in rows})
        },
    )
```

### `tradeledger/report_filters.py` (MODIFIED, lint)

**Why:** Ruff auto-fix: modern type hints; `Mapping` imported from `collections.abc`. Behaviour unchanged.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Filters for performance reports, shared by the dashboard and the API.

One definition of what each filter means and how it is validated, so the
website (/analytics) and the JSON API (/api/v1/analytics/...) can never
disagree about which trades a report covers.

  instrument_id  only this instrument (must exist: an unknown id would
                 otherwise produce an empty report that looks like "no
                 trades", which is misleading)
  strategy       exact strategy label, as stored on trades
  session        ASIA, LONDON, NEW_YORK or OTHER (case-insensitive input)
  start, end     inclusive UTC dates (YYYY-MM-DD) on the REALISED date:
                 closed_at, or trade_date for legacy trades with no
                 recorded exit time

parse_report_filters() never raises for bad input. It returns every
problem at once as {field: message}, so a form can show them all and the
API can turn them into one 400 response.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .repositories.instruments import get_instrument
from .validation import SESSIONS, parse_date

FILTER_NAMES = ("instrument_id", "strategy", "session", "start", "end")
STRATEGY_MAX_LENGTH = 50  # the same limit validation.py applies to trades


@dataclass(frozen=True)
class ReportFilters:
    instrument_id: int | None = None
    strategy: str | None = None
    session: str | None = None
    start: str | None = None  # 'YYYY-MM-DD'
    end: str | None = None  # 'YYYY-MM-DD'

    def as_kwargs(self) -> dict[str, object]:
        """Keyword arguments for build_report() and the repository."""
        return asdict(self)

    def active(self) -> dict[str, object]:
        """Only the filters that are set, e.g. for building URLs."""
        return {name: value for name, value in asdict(self).items() if value is not None}

    @property
    def is_empty(self) -> bool:
        return not self.active()


def parse_report_filters(
    args: Mapping[str, str | None], conn, *, blank_means_unset: bool
) -> tuple[ReportFilters, dict[str, str]]:
    """Validate raw query-string values.

    args               name -> raw text (missing names are simply unset)
    conn               database connection, to check the instrument exists
    blank_means_unset  True for HTML forms, where "All" submits an empty
                       value. False for the API, where an empty value is
                       almost certainly a client bug and is rejected.

    Returns (filters, errors). When errors is not empty, filters holds only
    the values that were valid and must not be used to build a report.
    """
    values: dict[str, object] = {}
    errors: dict[str, str] = {}

    def raw(name):
        value = args.get(name)
        if value is None:
            return None
        if blank_means_unset and value.strip() == "":
            return None
        return value

    instrument_text = raw("instrument_id")
    if instrument_text is not None:
        text = instrument_text.strip()
        if not text.isdigit() or int(text) < 1:
            errors["instrument_id"] = "must be a whole number of 1 or more"
        elif get_instrument(conn, int(text)) is None:
            errors["instrument_id"] = "does not match an existing instrument"
        else:
            values["instrument_id"] = int(text)

    strategy = raw("strategy")
    if strategy is not None:
        strategy = strategy.strip()
        if not strategy:
            errors["strategy"] = "must not be empty"
        elif len(strategy) > STRATEGY_MAX_LENGTH:
            errors["strategy"] = f"must be {STRATEGY_MAX_LENGTH} characters or fewer"
        else:
            values["strategy"] = strategy

    session = raw("session")
    if session is not None:
        session = session.strip().upper()
        if session not in SESSIONS:
            errors["session"] = f"must be one of: {', '.join(SESSIONS)}"
        else:
            values["session"] = session

    for name in ("start", "end"):
        text = raw(name)
        if text is None:
            continue
        try:
            values[name] = parse_date(text).isoformat()
        except ValueError as exc:
            errors[name] = str(exc)

    if "start" in values and "end" in values and values["start"] > values["end"]:
        errors["end"] = "must be on or after the start date"

    return ReportFilters(**values), errors
```

### `tradeledger/web/instruments.py` (MODIFIED, lint)

**Why:** Ruff auto-fix: `dict.fromkeys(INSTRUMENT_FORM_FIELDS, "")`. Behaviour unchanged.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Instrument list and add form.

There is no edit or delete: changing an instrument's contract_multiplier
would silently change the P&L of every past trade on it. That needs
versioned contract specifications first (see the Phase 4 guide).
"""

from flask import flash, redirect, render_template, request, url_for

from ..db import get_db
from ..repositories import DuplicateError
from ..repositories.instruments import create_instrument, list_instruments
from ..validation import ASSET_CLASSES, ValidationError
from . import main_bp
from .forms import INSTRUMENT_FORM_FIELDS, read_form

BLANK_INSTRUMENT_FORM = dict.fromkeys(INSTRUMENT_FORM_FIELDS, "")

INSTRUMENT_LABELS = {
    "symbol": "Symbol",
    "name": "Name",
    "asset_class": "Asset class",
    "price_increment": "Price increment",
    "contract_multiplier": "Contract multiplier",
    "quote_currency": "Quote currency",
}


def page(values, errors, status=200):
    return render_template(
        "instruments/index.html",
        instruments=list_instruments(get_db()),
        values=values,
        errors=errors,
        labels=INSTRUMENT_LABELS,
        asset_class_options=[(value, value.title()) for value in ASSET_CLASSES],
    ), status


@main_bp.route("/instruments", methods=["GET", "POST"])
def instruments():
    if request.method == "GET":
        return page(dict(BLANK_INSTRUMENT_FORM), {})

    values = read_form(request.form, INSTRUMENT_FORM_FIELDS)
    try:
        create_instrument(get_db(), values)
    except ValidationError as exc:
        return page(values, exc.errors, 400)
    except DuplicateError:
        return page(values, {"symbol": "is already recorded. Symbols must be unique."}, 400)

    flash(f"Instrument {values['symbol'].upper()} added.", "success")
    return redirect(url_for("main.instruments"))
```

### `tests/test_dashboard_data.py` (MODIFIED, lint)

**Why:** Removed an unused import (Ruff F401). Same assertions.  
**How to run or test:** Run: `python -m pytest tests/test_dashboard_data.py`

```python
"""Phase 6: the dashboard data layer (performance/dashboard.py).

The main dataset below is written into a real SQLite database through the
repository, then read back through build_report() and build_dashboard(),
so these tests cover the whole path from SQL to chart payload.

Every expected number was worked out BY HAND, not by calling the code
under test. Each trade: entry 100, stop 99 (risk 1 per unit), size 1,
multiplier 1, no fees, so realised R = exit - 100.

  id  instrument  entered            closed (realised)     exit   R     strategy  session
  1   TEST (USD)  2025-12-31 10:00   2025-12-31 23:30 Wed  102    +2    Alpha     LONDON
  2   TEST (USD)  2026-01-01 20:00   2026-01-02 09:00 Fri   99    -1    Beta      -
  3   TEST (USD)  2025-12-30 09:00   2026-01-02 09:00 Fri   99    -1    -         ASIA
  4   GBPX (GBP)  2026-03-06 08:00   2026-03-07 12:00 Sat  100.5  +0.5  Alpha     LONDON
  5   TEST (USD)  2026-03-09 08:00   OPEN                   -      -     -         -

Trade 3 opened FIRST but closed after trade 1, so it must appear after 1.
Trades 2 and 3 close at the same second; the lower id (2) comes first.

Realised order 1, 2, 3, 4:
  cumulative R   2, 1, 0, 0.5        running peak  2, 2, 2, 2
  drawdown       0, 1, 2, 1.5        maximum drawdown 2 (peak after #1, low after #3)
  total R 0.5   wins 2   losses 2   win rate 50%   average R 0.125
  expectancy 0.5 x 1.25 + 0.5 x (-1) = 0.125     profit factor 2.5 / 2 = 1.25
  streaks: longest win 1, longest loss 2
  months: Dec 2025 +2 (1 trade), Jan 2026 -2 (2), Feb 2026 none, Mar 2026 +0.5 (1)
  weekdays: Wednesday +2 (1), Friday -2 (2), Saturday +0.5 (1), others none
  money: USD 2 - 1 - 1 = 0.00 USD, GBP +0.50 GBP (never added together)
"""

import json
from decimal import Decimal

import pytest

from tests.factories import make_trade
from tradeledger.performance import analyse
from tradeledger.performance.dashboard import (
    SERIES_TABLE_LIMIT,
    SMALL_SAMPLE,
    build_dashboard,
    chart_payload,
    drawdown_series,
    make_dashboard,
    money_rows,
    month_keys,
    month_rows,
    to_epoch_ms,
    weekday_rows,
)
from tradeledger.report_filters import ReportFilters
from tradeledger.repositories.instruments import create_instrument
from tradeledger.repositories.trades import create_trade


def D(text):
    return Decimal(text)


@pytest.fixture
def hand_conn(conn):
    usd = create_instrument(
        conn,
        {
            "symbol": "TEST",
            "name": "Test USD",
            "asset_class": "OTHER",
            "price_increment": "0.01",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
    )
    gbp = create_instrument(
        conn,
        {
            "symbol": "GBPX",
            "name": "Test GBP",
            "asset_class": "OTHER",
            "price_increment": "0.01",
            "contract_multiplier": "1",
            "quote_currency": "GBP",
        },
    )

    def trade(instrument, entered, closed, exit_price, strategy=None, session=None):
        create_trade(
            conn,
            {
                "instrument_id": instrument,
                "direction": "LONG",
                "status": "CLOSED" if closed else "OPEN",
                "trade_date": entered,
                "closed_at": closed,
                "entry_price": "100",
                "stop_loss": "99",
                "exit_price": exit_price,
                "position_size": "1",
                "fees": "0",
                "strategy": strategy,
                "session": session,
            },
        )

    trade(usd, "2025-12-31T10:00:00Z", "2025-12-31T23:30:00Z", "102", "Alpha", "LONDON")
    trade(usd, "2026-01-01T20:00:00Z", "2026-01-02T09:00:00Z", "99", "Beta")
    trade(usd, "2025-12-30T09:00:00Z", "2026-01-02T09:00:00Z", "99", None, "ASIA")
    trade(gbp, "2026-03-06T08:00:00Z", "2026-03-07T12:00:00Z", "100.5", "Alpha", "LONDON")
    trade(usd, "2026-03-09T08:00:00Z", None, None)
    return conn


@pytest.fixture
def board(hand_conn):
    return build_dashboard(hand_conn, ReportFilters())


# ---------- Chronology, cumulative R and drawdown ----------


def test_series_follows_realised_order_with_stable_ties(board):
    assert [p.trade_id for p in board.series] == [1, 2, 3, 4]


def test_cumulative_peak_and_drawdown_match_hand_calculation(board):
    assert [p.cumulative_r for p in board.series] == [D("2"), D("1"), D("0"), D("0.5")]
    assert [p.peak_r for p in board.series] == [D("2")] * 4
    assert [p.drawdown_r for p in board.series] == [D("0"), D("1"), D("2"), D("1.5")]


def test_maximum_drawdown_from_both_paths_agrees(board):
    worst = board.report.overall.max_drawdown_r
    assert worst.amount == D("2") == max(p.drawdown_r for p in board.series)
    assert (worst.peak_trade_id, worst.trough_trade_id) == (1, 3)


def test_drawdown_starts_from_zero_when_the_first_trade_loses():
    """Only losing trades: the peak stays at the 0R starting point."""
    report = analyse(
        [
            make_trade(id=1, exit_price=D("90"), closed_at="2026-01-05T11:00:00Z"),
            make_trade(id=2, exit_price=D("85"), closed_at="2026-01-06T11:00:00Z"),
        ]
    )
    series = drawdown_series(report.cumulative_r)
    assert [p.cumulative_r for p in series] == [D("-1"), D("-2.5")]
    assert [p.peak_r for p in series] == [D("0"), D("0")]
    assert [p.drawdown_r for p in series] == [D("1"), D("2.5")]
    board = make_dashboard(report, ReportFilters())
    assert "from the starting point (0R)" in board.descriptions["drawdown"]


def test_only_winning_trades_have_no_drawdown():
    report = analyse(
        [
            make_trade(id=1, closed_at="2026-01-05T11:00:00Z"),
            make_trade(id=2, closed_at="2026-01-06T11:00:00Z"),
        ]
    )
    board = make_dashboard(report, ReportFilters())
    assert all(p.drawdown_r == 0 for p in board.series)
    assert board.descriptions["drawdown"] == "No drawdown: cumulative R never fell below its previous peak."
    kpis = {k.key: k for k in board.kpis}
    assert kpis["profit_factor_r"].value == "n/a"
    assert kpis["profit_factor_r"].note == "Needs at least one losing trade"
    assert kpis["max_drawdown_r"].value == "0.00R"


def test_to_epoch_ms_against_a_known_instant():
    # 2026-01-01T00:00Z is 1,767,225,600 s; plus 1 day and 9 hours.
    assert to_epoch_ms("2026-01-02T09:00:00Z") == (1_767_225_600 + 86_400 + 9 * 3_600) * 1000


def test_series_has_epoch_x_values_in_order(board):
    xs = [p.x for p in board.series]
    assert xs == sorted(xs)
    assert xs[1] == xs[2] == to_epoch_ms("2026-01-02T09:00:00Z")


# ---------- Months and weekdays ----------


def test_month_keys_cross_a_year_boundary():
    assert month_keys("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert month_keys("2026-03", "2026-03") == ["2026-03"]


def test_months_keep_an_empty_month_as_a_gap(board):
    rows = [
        (m.key, m.label, None if m.summary is None else (m.summary.trades, m.summary.total_r))
        for m in board.months
    ]
    assert rows == [
        ("2025-12", "Dec 2025", (1, D("2"))),
        ("2026-01", "Jan 2026", (2, D("-2"))),
        ("2026-02", "Feb 2026", None),  # no trades: not "0R"
        ("2026-03", "Mar 2026", (1, D("0.5"))),
    ]


def test_weekdays_are_all_seven_in_calendar_order(board):
    rows = [(w.key, None if w.summary is None else w.summary.total_r) for w in board.weekdays]
    assert rows == [
        ("Monday", None),
        ("Tuesday", None),
        ("Wednesday", D("2")),
        ("Thursday", None),
        ("Friday", D("-2")),
        ("Saturday", D("0.5")),
        ("Sunday", None),
    ]


def test_empty_inputs_give_empty_periods():
    assert month_rows({}) == []
    assert [w.summary for w in weekday_rows({})] == [None] * 7


# ---------- Groups ----------


def test_strategies_best_first_with_stable_ties(board):
    assert [(g.label, g.summary.trades, g.summary.total_r) for g in board.strategies] == [
        ("Alpha", 2, D("2.5")),
        ("Beta", 1, D("-1")),
        ("Unspecified", 1, D("-1")),
    ]


def test_sessions_in_trading_day_order_with_readable_labels(board):
    assert [(g.key, g.label, g.summary.total_r) for g in board.sessions] == [
        ("ASIA", "Asia", D("-1")),
        ("LONDON", "London", D("2.5")),
        ("Unspecified", "Unspecified", D("-1")),
    ]


def test_instruments_compared_in_r_not_money(board):
    assert [(g.label, g.summary.total_r) for g in board.instruments] == [("GBPX", D("0.5")), ("TEST", D("0"))]


def test_small_sample_flag(board):
    assert SMALL_SAMPLE == 10
    assert all(g.small_sample for g in board.instruments)


# ---------- KPI cards ----------


def test_kpis_match_hand_calculation(board):
    kpis = {k.key: (k.value, k.tone) for k in board.kpis}
    assert kpis == {
        "trades": ("4", "neutral"),
        "wins": ("2", "neutral"),
        "losses": ("2", "neutral"),
        "breakevens": ("0", "neutral"),
        "win_rate": ("50.0%", "neutral"),
        "average_r": ("+0.13R", "positive"),  # 0.125 rounds half up
        "expectancy_r": ("+0.13R", "positive"),
        "profit_factor_r": ("1.25", "neutral"),
        "total_r": ("+0.50R", "positive"),
        "max_drawdown_r": ("2.00R", "negative"),
        "longest_win_streak": ("1 trade", "neutral"),
        "longest_loss_streak": ("2 trades", "neutral"),
    }
    assert {k.key: k.note for k in board.kpis}["trades"] == "1 open trade not included"


def test_money_stays_per_currency(board):
    assert money_rows(board.report) == [("GBP", "0.50 GBP", "positive", 1), ("USD", "0.00 USD", "neutral", 3)]


def test_empty_report_shows_unavailable_not_zero(conn):
    board = build_dashboard(conn, ReportFilters())
    assert not board.has_results
    kpis = {k.key: k.value for k in board.kpis}
    assert kpis["trades"] == "0"
    for key in (
        "wins",
        "win_rate",
        "average_r",
        "expectancy_r",
        "profit_factor_r",
        "total_r",
        "max_drawdown_r",
        "longest_win_streak",
        "longest_loss_streak",
    ):
        assert kpis[key] == "n/a", key
    assert board.series == [] and board.months == []
    assert chart_payload(board)["curve"] == []


def test_only_breakeven_trades():
    report = analyse([make_trade(id=1, exit_price=D("100"), closed_at="2026-01-05T11:00:00Z")])
    kpis = {k.key: k.value for k in make_dashboard(report, ReportFilters()).kpis}
    assert kpis["breakevens"] == "1" and kpis["win_rate"] == "0.0%"
    assert kpis["profit_factor_r"] == "n/a" and kpis["total_r"] == "0.00R"


# ---------- Data quality: missing R and unknown exit times ----------


def test_trade_without_usable_r_is_counted_but_not_plotted():
    """A stop on the wrong side cannot reach the database (CHECK constraint),
    but other data sources could produce it, so the engine supports it."""
    report = analyse(
        [
            make_trade(id=1, closed_at="2026-01-05T11:00:00Z"),
            make_trade(id=2, stop_loss=D("105"), closed_at="2026-01-06T11:00:00Z"),
        ]
    )
    board = make_dashboard(report, ReportFilters())
    assert board.report.overall.trades == 2
    assert [p.trade_id for p in board.series] == [1]
    assert [e.trade_id for e in board.report.r_unavailable] == [2]


def test_legacy_trade_is_placed_at_entry_time_and_flagged(hand_conn):
    hand_conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 3")
    hand_conn.commit()
    board = build_dashboard(hand_conn, ReportFilters())
    # Trade 3 now counts at its ENTRY time, 2025-12-30, so it comes first.
    assert [p.trade_id for p in board.series] == [3, 1, 2, 4]
    legacy = board.series[0]
    assert legacy.close_time_known is False and legacy.realised_at == "2025-12-30T09:00:00Z"
    assert board.report.unknown_close_time_trade_ids == [3]


# ---------- Filters reach the dashboard ----------


def test_filters_change_every_part_of_the_dashboard(hand_conn):
    board = build_dashboard(hand_conn, ReportFilters(strategy="Alpha"))
    assert [p.trade_id for p in board.series] == [1, 4]
    assert [m.key for m in board.months] == ["2025-12", "2026-01", "2026-02", "2026-03"]
    assert {k.key: k.value for k in board.kpis}["total_r"] == "+2.50R"
    assert [g.label for g in board.strategies] == ["Alpha"]


def test_date_filter_on_realised_date(hand_conn):
    board = build_dashboard(hand_conn, ReportFilters(start="2026-01-01", end="2026-01-31"))
    assert [p.trade_id for p in board.series] == [2, 3]


# ---------- Chart payload ----------


def test_payload_has_exact_strings_and_no_floats(board):
    payload = chart_payload(board)

    def walk(value):
        assert not isinstance(value, float), value
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    json.dumps(payload, allow_nan=False)  # raises if NaN/Infinity slipped in


def test_payload_curve_values(board):
    curve = chart_payload(board)["curve"]
    assert curve[0] == {
        "start": True,
        "x": board.series[0].x,
        "cumulative_r": "0",
        "cumulative_text": "0.00R",
        "underwater_r": "0",
        "drawdown_text": "0.00R",
    }
    assert [(p["trade_id"], p["realised_r"], p["cumulative_r"], p["underwater_r"]) for p in curve[1:]] == [
        (1, "2", "2", "0"),
        (2, "-1", "1", "-1"),
        (3, "-1", "0", "-2"),
        (4, "0.5", "0.5", "-1.5"),
    ]
    assert curve[3]["when"] == "02 Jan 2026, 09:00 UTC"
    assert curve[3]["realised_text"] == "-1.00R"


def test_payload_numeric_strings_are_finite_decimals(board):
    payload = chart_payload(board)
    values = [p[k] for p in payload["curve"] for k in ("cumulative_r", "underwater_r")]
    values += [
        row["total_r"]
        for key in ("monthly", "weekday", "instruments", "strategies", "sessions")
        for row in payload[key]
        if row["total_r"] is not None
    ]
    for text in values:
        assert Decimal(text).is_finite(), text


def test_payload_marks_empty_periods_as_null(board):
    monthly = {m["key"]: m for m in chart_payload(board)["monthly"]}
    assert monthly["2026-02"]["total_r"] is None and monthly["2026-02"]["trades"] == 0
    assert monthly["2026-01"]["total_r"] == "-2" and monthly["2026-01"]["total_r_text"] == "-2.00R"


def test_payload_and_tables_agree(board):
    """The chart payload is cut from the same rows the tables render."""
    payload = chart_payload(board)
    for key, rows in (
        ("instruments", board.instruments),
        ("strategies", board.strategies),
        ("sessions", board.sessions),
    ):
        assert [(p["label"], p["trades"], Decimal(p["total_r"])) for p in payload[key]] == [
            (r.label, r.summary.trades, r.summary.total_r) for r in rows
        ]


def test_payload_contains_no_free_text_from_trades(hand_conn):
    hand_conn.execute("UPDATE trades SET notes = 'SECRET-NOTE <script>' WHERE id = 1")
    hand_conn.commit()
    text = json.dumps(chart_payload(build_dashboard(hand_conn, ReportFilters())))
    assert "SECRET-NOTE" not in text


# ---------- Sample data and scale ----------


def test_sample_data_series_ends_at_engine_total(seeded_conn):
    board = build_dashboard(seeded_conn, ReportFilters())
    assert board.series[-1].cumulative_r == board.report.overall.total_r
    assert max(p.drawdown_r for p in board.series) == board.report.overall.max_drawdown_r.amount


def test_one_thousand_trades(conn):
    """1,000 synthetic trades: the dashboard still adds up. (Timing is
    measured separately by scripts/benchmark_dashboard.py, not asserted.)"""
    instrument = create_instrument(
        conn,
        {
            "symbol": "BULK",
            "name": "Bulk",
            "asset_class": "OTHER",
            "price_increment": "0.01",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
    )
    for n in range(1000):
        exit_price = ("102", "99", "100.5", "99")[n % 4]  # +2, -1, +0.5, -1 repeating
        day = 1 + (n // 40)
        create_trade(
            conn,
            {
                "instrument_id": instrument,
                "direction": "LONG",
                "status": "CLOSED",
                "trade_date": f"2026-{1 + (day - 1) // 28:02d}-{1 + (day - 1) % 28:02d}T08:00:00Z",
                "closed_at": f"2026-{1 + (day - 1) // 28:02d}-{1 + (day - 1) % 28:02d}T{(n % 40) // 4 + 9:02d}:{n % 4:02d}:00Z",
                "entry_price": "100",
                "stop_loss": "99",
                "exit_price": exit_price,
                "position_size": "1",
            },
        )
    board = build_dashboard(conn, ReportFilters())
    assert board.report.overall.trades == 1000
    assert len(board.series) == 1000
    assert board.series[-1].cumulative_r == D("125")  # 250 cycles of +0.5R
    assert board.report.overall.total_r == D("125")
    # The HTML table keeps only the most recent rows; the chart keeps all.
    assert SERIES_TABLE_LIMIT == 200
    assert board.series_table == board.series[-200:]
```

### `tests/test_db.py` (MODIFIED, lint)

**Why:** Ruff auto-fix: nested `with` blocks merged into one statement. Same assertions.  
**How to run or test:** Run: `python -m pytest tests/test_db.py`

```python
"""Database setup, Flask integration, constraints and transactions."""

import sqlite3

import pytest
from flask import g

from tradeledger import create_app
from tradeledger.db import connect, get_db, init_schema, schema_version
from tradeledger.migrations import LATEST_VERSION

# ---------- Initialisation ----------


def test_both_tables_are_created(conn):
    names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"instruments", "trades"} <= names


def test_indexes_are_created(conn):
    names = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_trades_instrument_id", "idx_trades_trade_date", "idx_trades_status"} <= names


def test_schema_version_is_recorded(conn):
    # Was "== 1" in Phase 2. A new database is now migrated to the latest version.
    assert schema_version(conn) == LATEST_VERSION == 2


def test_init_schema_twice_keeps_existing_data(conn, eurusd_id):
    init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1


def test_foreign_keys_are_switched_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ---------- Flask integration ----------


def test_testing_config_without_database_path_refuses_to_start():
    with pytest.raises(RuntimeError, match="DATABASE"):
        create_app("testing")


def test_development_database_defaults_to_instance_folder(monkeypatch):
    from tradeledger.config import DevelopmentConfig

    monkeypatch.setattr(DevelopmentConfig, "DATABASE", None)
    app = create_app("development")
    assert app.config["DATABASE"].endswith("tradeledger.db")
    assert "instance" in app.config["DATABASE"]


def test_get_db_reuses_one_connection_per_app_context(app):
    with app.app_context():
        assert get_db() is get_db()


def test_connection_is_closed_when_app_context_ends(app):
    with app.app_context():
        conn = get_db()
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")


def test_connection_is_removed_from_g_after_teardown(app):
    with app.app_context():
        get_db()
        assert "db" in g
    with app.app_context():
        assert "db" not in g


# ---------- Constraints enforced by SQL itself ----------
# These bypass Python validation on purpose (raw INSERTs) to prove the
# database is a safety net even if application code has a bug.

RAW_TRADE_SQL = """
INSERT INTO trades (instrument_id, trade_date, direction, entry_price, stop_loss,
                    exit_price, position_size, fees, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

VALID_RAW = [None, "2026-03-02T08:15:00Z", "LONG", "1.085", "1.083", "1.089", "1", "7", "CLOSED"]


def raw_trade(instrument_id, **changes):
    values = list(VALID_RAW)
    values[0] = instrument_id
    positions = {
        "trade_date": 1,
        "direction": 2,
        "entry_price": 3,
        "stop_loss": 4,
        "exit_price": 5,
        "position_size": 6,
        "fees": 7,
        "status": 8,
    }
    for field, value in changes.items():
        values[positions[field]] = value
    return values


def test_sql_accepts_a_valid_raw_trade(conn, eurusd_id):
    conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id))


def test_sql_rejects_unknown_instrument(conn):
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute(RAW_TRADE_SQL, raw_trade(999))


def test_sql_refuses_to_delete_instrument_that_has_trades(conn, eurusd_id):
    conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
        conn.execute("DELETE FROM instruments WHERE id = ?", (eurusd_id,))


@pytest.mark.parametrize(
    "changes",
    [
        {"direction": "BUY"},  # invalid direction
        {"status": "PENDING"},  # invalid status
        {"status": "CLOSED", "exit_price": None},  # closed without exit
        {"status": "OPEN"},  # open WITH an exit price
        {"stop_loss": "1.090"},  # long stop above entry
        {"stop_loss": "1.085"},  # stop equal to entry
        {"position_size": "0"},  # zero size
        {"position_size": "-1"},  # negative size
        {"entry_price": "abc"},  # not a number
        {"fees": "-2"},  # negative fees
        {"trade_date": "02/03/2026 08:15"},  # wrong date format
    ],
)
def test_sql_rejects_invalid_raw_trades(conn, eurusd_id, changes):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id, **changes))


def test_sql_rejects_short_with_stop_below_entry(conn, eurusd_id):
    with pytest.raises(sqlite3.IntegrityError, match="stop_on_correct_side"):
        conn.execute(
            RAW_TRADE_SQL,
            raw_trade(eurusd_id, direction="SHORT", stop_loss="1.080", exit_price="1.080"),
        )


def test_sql_rejects_lowercase_symbol(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO instruments (symbol, name, asset_class, price_increment, "
            "contract_multiplier, quote_currency) VALUES (?, ?, ?, ?, ?, ?)",
            ("eurusd", "x", "FOREX", "0.00001", "100000", "USD"),
        )


# ---------- Transactions ----------


def test_transaction_commits_on_success(db_path, conn, eurusd_id):
    other = connect(db_path)  # a second connection only sees committed data
    try:
        assert other.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1
    finally:
        other.close()


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError), conn:
        conn.execute(
            "INSERT INTO instruments (symbol, name, asset_class, price_increment, "
            "contract_multiplier, quote_currency) VALUES (?, ?, ?, ?, ?, ?)",
            ("GBPUSD", "test", "FOREX", "0.00001", "100000", "USD"),
        )
        raise RuntimeError("something failed half-way")
    assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 0
```

### `tests/test_migrations.py` (MODIFIED, lint)

**Why:** `zip(..., strict=True)`: the comparison now also fails if row counts differ (stronger).  
**How to run or test:** Run: `python -m pytest tests/test_migrations.py`

```python
"""Schema migration: a real Phase 3 (version 1) database upgraded to version 2.

tests/fixtures/schema_v1.sql is an exact copy of the Phase 3 schema.sql,
so these tests start from the same structure an existing user has.
"""

import sqlite3
from pathlib import Path

import pytest

from tradeledger import create_app, migrations
from tradeledger.db import connect, drop_all, init_schema, schema_version
from tradeledger.performance import build_report
from tradeledger.repositories.trades import create_trade, get_trade, update_trade
from tradeledger.seed import SAMPLE_INSTRUMENTS, SAMPLE_TRADES
from tradeledger.validation import ValidationError, validate_instrument, validate_trade

V1_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text(encoding="utf-8")

V1_TRADE_COLUMNS = (
    "instrument_id",
    "trade_date",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "status",
    "strategy",
    "session",
    "notes",
)


def build_v1_database(path):
    """Create a Phase 3 database holding the 16 sample trades WITHOUT exit
    times, exactly as an existing user's database looks."""
    conn = connect(path)
    conn.executescript(V1_SCHEMA)
    with conn:
        for raw in SAMPLE_INSTRUMENTS:
            item = validate_instrument(raw)
            conn.execute(
                "INSERT INTO instruments (symbol, name, asset_class, price_increment, "
                "contract_multiplier, quote_currency) VALUES (:symbol, :name, :asset_class, "
                ":price_increment, :contract_multiplier, :quote_currency)",
                item,
            )
        ids = {r["symbol"]: r["id"] for r in conn.execute("SELECT id, symbol FROM instruments")}
        for raw in SAMPLE_TRADES:
            data = {k: v for k, v in raw.items() if k not in ("symbol", "closed_at")}
            data["instrument_id"] = ids[raw["symbol"]]
            trade = validate_trade(data, require_closed_at=False)
            conn.execute(
                f"INSERT INTO trades ({', '.join(V1_TRADE_COLUMNS)}) "
                f"VALUES ({', '.join(':' + c for c in V1_TRADE_COLUMNS)})",
                trade,
            )
    return conn


def columns(conn, table):
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def rows(conn, table):
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


@pytest.fixture
def v1_conn(tmp_path):
    conn = build_v1_database(str(tmp_path / "phase3.db"))
    yield conn
    conn.close()


# ---------- The starting point really is Phase 3 ----------


def test_fixture_is_a_version_1_database(v1_conn):
    assert schema_version(v1_conn) == 1
    assert "closed_at" not in columns(v1_conn, "trades")
    assert v1_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 16


# ---------- Migrating ----------


def test_migration_adds_closed_at_and_bumps_version(v1_conn):
    applied = init_schema(v1_conn)
    assert applied == [2]
    assert schema_version(v1_conn) == 2
    assert "closed_at" in columns(v1_conn, "trades")
    names = {r["name"] for r in v1_conn.execute("SELECT name FROM sqlite_master")}
    assert {"idx_trades_closed_at", "trades_closed_at_rules_insert", "trades_closed_at_rules_update"} <= names


def test_migration_preserves_every_existing_value(v1_conn):
    trades_before = rows(v1_conn, "trades")
    instruments_before = rows(v1_conn, "instruments")
    init_schema(v1_conn)
    trades_after = rows(v1_conn, "trades")
    assert rows(v1_conn, "instruments") == instruments_before
    assert len(trades_after) == len(trades_before) == 16
    for before, after in zip(trades_before, trades_after, strict=True):
        assert {k: v for k, v in after.items() if k != "closed_at"} == before
        assert after["closed_at"] is None  # never fabricated


def test_migration_is_idempotent(v1_conn):
    assert init_schema(v1_conn) == [2]
    snapshot = rows(v1_conn, "trades")
    assert init_schema(v1_conn) == []
    assert migrations.migrate(v1_conn) == []
    assert schema_version(v1_conn) == 2
    assert rows(v1_conn, "trades") == snapshot


def test_new_database_goes_straight_to_latest(conn):
    assert schema_version(conn) == migrations.LATEST_VERSION
    assert "closed_at" in columns(conn, "trades")


def test_failed_migration_rolls_back_completely(v1_conn, monkeypatch):
    init_schema(v1_conn)
    broken = {
        **migrations.MIGRATIONS,
        3: "CREATE TABLE half_done (x INTEGER); SELECT * FROM table_that_does_not_exist;",
    }
    monkeypatch.setattr(migrations, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        migrations.migrate(v1_conn)
    assert schema_version(v1_conn) == 2
    names = {r["name"] for r in v1_conn.execute("SELECT name FROM sqlite_master")}
    assert "half_done" not in names


def test_reset_then_init_gives_a_fully_migrated_schema(conn):
    """Regression: drop_all must reset the version, or init would skip migration 2."""
    drop_all(conn)
    assert schema_version(conn) == 0
    init_schema(conn)
    assert schema_version(conn) == 2
    assert "closed_at" in columns(conn, "trades")


# ---------- Legacy trades after migration ----------


def test_legacy_trades_are_reported_and_ordered_by_entry_time(v1_conn):
    init_schema(v1_conn)
    report = build_report(v1_conn)
    closed_ids = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]
    assert report.unknown_close_time_trade_ids == closed_ids
    assert [p.trade_id for p in report.cumulative_r] == closed_ids
    assert all(not p.close_time_known for p in report.cumulative_r)
    assert all(p.realised_at == p.trade_date for p in report.cumulative_r)
    # The numbers are the same as Phase 3 produced for this data.
    assert report.overall.trades == 14 and report.overall.wins == 7


def test_new_closed_trade_requires_closed_at_after_migration(v1_conn):
    init_schema(v1_conn)
    trade = {
        "instrument_id": 1,
        "trade_date": "2026-04-01T08:00:00Z",
        "direction": "LONG",
        "entry_price": "1.1",
        "stop_loss": "1.09",
        "exit_price": "1.12",
        "position_size": "1",
    }
    with pytest.raises(ValidationError) as info:
        create_trade(v1_conn, trade)
    assert info.value.errors == {"closed_at": "is required for a CLOSED trade"}
    new_id = create_trade(v1_conn, dict(trade, closed_at="2026-04-01T09:00:00Z"))
    assert get_trade(v1_conn, new_id)["closed_at"] == "2026-04-01T09:00:00Z"


def test_mixed_legacy_and_new_trades_are_in_realised_order(v1_conn):
    init_schema(v1_conn)
    # Entered BEFORE every sample trade, closed AFTER all of them.
    new_id = create_trade(
        v1_conn,
        {
            "instrument_id": 1,
            "trade_date": "2026-02-27T08:00:00Z",
            "closed_at": "2026-03-25T08:00:00Z",
            "direction": "LONG",
            "entry_price": "1.1",
            "stop_loss": "1.09",
            "exit_price": "1.12",
            "position_size": "1",
        },
    )
    curve = build_report(v1_conn).cumulative_r
    assert curve[-1].trade_id == new_id and curve[-1].close_time_known


def test_legacy_trade_can_be_edited_without_inventing_a_close_time(v1_conn):
    init_schema(v1_conn)
    legacy = get_trade(v1_conn, 1)
    edited = {
        k: legacy[k]
        for k in (
            "instrument_id",
            "trade_date",
            "closed_at",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "exit_price",
            "position_size",
            "fees",
            "status",
            "strategy",
            "session",
        )
    }
    edited["notes"] = "Edited after the migration"
    assert update_trade(v1_conn, 1, edited) is True
    after = get_trade(v1_conn, 1)
    assert after["notes"] == "Edited after the migration"
    assert after["closed_at"] is None


def test_legacy_trade_can_be_given_its_real_close_time(v1_conn):
    init_schema(v1_conn)
    legacy = get_trade(v1_conn, 1)
    data = {
        k: legacy[k]
        for k in (
            "instrument_id",
            "trade_date",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "exit_price",
            "position_size",
            "fees",
            "status",
            "strategy",
            "session",
            "notes",
        )
    }
    update_trade(v1_conn, 1, dict(data, closed_at="2026-03-02T12:00:00Z"))
    assert get_trade(v1_conn, 1)["closed_at"] == "2026-03-02T12:00:00Z"
    # Once known, it cannot be removed again: the trade is no longer legacy.
    with pytest.raises(ValidationError):
        update_trade(v1_conn, 1, dict(data, closed_at=None))


# ---------- Triggers enforce the rules in SQL ----------

RAW = (
    "INSERT INTO trades (instrument_id, trade_date, closed_at, direction, entry_price, "
    "stop_loss, exit_price, position_size, fees, status) VALUES (1, ?, ?, 'LONG', '1.1', "
    "'1.09', ?, '1', '0', ?)"
)


def test_trigger_rejects_open_trade_with_closed_at(seeded_conn):
    with pytest.raises(sqlite3.IntegrityError, match="closed_at_rules"):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "2026-04-01T09:00:00Z", None, "OPEN"))


def test_trigger_rejects_close_before_entry(seeded_conn):
    with pytest.raises(sqlite3.IntegrityError, match="closed_at_rules"):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "2026-04-01T07:00:00Z", "1.12", "CLOSED"))


def test_trigger_rejects_bad_update(seeded_conn):
    with pytest.raises(sqlite3.IntegrityError, match="closed_at_rules"):
        seeded_conn.execute("UPDATE trades SET closed_at = '2000-01-01T00:00:00Z' WHERE id = 1")


def test_check_constraint_rejects_badly_formatted_closed_at(seeded_conn):
    with pytest.raises(sqlite3.IntegrityError):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "01/04/2026 09:00", "1.12", "CLOSED"))


def test_sql_still_allows_legacy_style_closed_trade_without_closed_at(seeded_conn):
    seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", None, "1.12", "CLOSED"))


# ---------- migrate-db command ----------


def test_migrate_db_command_backs_up_then_migrates(tmp_path):
    path = tmp_path / "user.db"
    build_v1_database(str(path)).close()
    runner = create_app("testing", overrides={"DATABASE": str(path)}).test_cli_runner()

    result = runner.invoke(args=["migrate-db"])
    assert result.exit_code == 0, result.output
    assert "Schema version 1 -> 2" in result.output
    backups = list(tmp_path.glob("user.db.v1-*.bak"))
    assert len(backups) == 1
    backup = connect(str(backups[0]))
    assert schema_version(backup) == 1
    assert backup.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 16
    backup.close()

    again = runner.invoke(args=["migrate-db"])
    assert "Already at schema version 2" in again.output
    assert len(list(tmp_path.glob("user.db.v1-*.bak"))) == 1
```

### `tests/test_openapi.py` (MODIFIED, lint)

**Why:** Loop over `SCHEMAS.values()` (the key was unused, Ruff B007). Same assertions.  
**How to run or test:** Run: `python -m pytest tests/test_openapi.py`

```python
"""The OpenAPI specification must match the running application.

1. Every /api/v1 route and method in Flask is documented, and nothing
   documented is missing from Flask.
2. Real responses validate against the documented schemas. Schemas use
   additionalProperties: false, so an undocumented field fails.
3. The examples in the specification are real: the example POST body
   creates a trade.
4. docs/openapi.json is not stale.
"""

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from tradeledger.api.http import INSTRUMENT_INPUT_FIELDS, TRADE_INPUT_FIELDS
from tradeledger.api.openapi import EXAMPLE_TRADE_INPUT, build_spec
from tradeledger.api.serialise import INSTRUMENT_FIELDS, RESULT_FIELDS, TRADE_FIELDS

SPEC = build_spec()
SCHEMAS = SPEC["components"]["schemas"]


def validate(instance, schema_name):
    """Validate against a named component, resolving $refs inside the spec."""
    root = {"$ref": f"#/components/schemas/{schema_name}", "components": SPEC["components"]}
    errors = sorted(Draft202012Validator(root).iter_errors(instance), key=str)
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors[:5])


def flask_api_routes(app):
    routes = set()
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("api."):
            continue
        path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", rule.rule)[len("/api/v1") :]
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            routes.add((path, method.lower()))
    return routes


def spec_routes():
    return {
        (path, method)
        for path, item in SPEC["paths"].items()
        for method in item
        if method in ("get", "post", "put", "delete", "patch")
    }


# ---------- Structure ----------


def test_spec_is_openapi_3_1_and_served(client, app):
    assert SPEC["openapi"] == "3.1.0"
    served = client.get("/api/v1/openapi.json").get_json()
    assert served["openapi"] == "3.1.0"
    # Phase 5: compare with the configured version, not a hard-coded "0.4.0".
    assert served["info"]["x-app-version"] == app.config["APP_VERSION"]
    assert served["paths"] == SPEC["paths"]


def test_every_route_is_documented_and_every_documented_route_exists(app):
    assert flask_api_routes(app) == spec_routes()


def test_component_schemas_are_valid_json_schema():
    for schema in SCHEMAS.values():
        Draft202012Validator.check_schema({**schema, "components": SPEC["components"]})


def test_field_lists_match_the_code():
    assert list(SCHEMAS["Trade"]["properties"]) == [*TRADE_FIELDS, "result"]
    assert list(SCHEMAS["TradeResult"]["properties"]) == list(RESULT_FIELDS)
    assert list(SCHEMAS["TradeInput"]["properties"]) == list(TRADE_INPUT_FIELDS)
    assert SCHEMAS["TradeReplace"]["required"] == list(TRADE_INPUT_FIELDS)
    assert list(SCHEMAS["Instrument"]["properties"]) == list(INSTRUMENT_FIELDS)
    assert list(SCHEMAS["InstrumentInput"]["properties"]) == list(INSTRUMENT_INPUT_FIELDS)


def test_every_path_parameter_is_declared():
    for path, item in SPEC["paths"].items():
        names = set(re.findall(r"{(\w+)}", path))
        declared = {p["name"] for p in item.get("parameters", []) if p["in"] == "path"}
        assert names == declared, path


def test_documented_query_parameters_match_what_the_code_accepts():
    from tradeledger.api.analytics import FILTERS
    from tradeledger.api.trades import LIST_PARAMETERS

    def query_names(path):
        return {p["name"] for p in SPEC["paths"][path]["get"].get("parameters", []) if p["in"] == "query"}

    assert query_names("/trades") == set(LIST_PARAMETERS)
    for path in SPEC["paths"]:
        if path.startswith("/analytics/"):
            assert query_names(path) == set(FILTERS), path


# ---------- Real responses validate ----------


def test_trade_responses_match_schemas(seeded_client, trade_body):
    validate(trade_body, "TradeInput")
    created = seeded_client.post("/api/v1/trades", json=trade_body)
    validate(created.get_json(), "TradeEnvelope")
    trade_id = created.get_json()["data"]["id"]
    validate(seeded_client.get(f"/api/v1/trades/{trade_id}").get_json(), "TradeEnvelope")
    validate(seeded_client.get("/api/v1/trades").get_json(), "TradeList")
    validate(seeded_client.get("/api/v1/trades?status=OPEN").get_json(), "TradeList")
    replacement = {name: trade_body.get(name) for name in TRADE_INPUT_FIELDS}
    validate(replacement, "TradeReplace")
    validate(seeded_client.put(f"/api/v1/trades/{trade_id}", json=replacement).get_json(), "TradeEnvelope")


def test_instrument_responses_match_schemas(seeded_client):
    body = {
        "symbol": "US30",
        "name": "Example",
        "asset_class": "INDEX",
        "price_increment": "1",
        "contract_multiplier": "1",
        "quote_currency": "USD",
    }
    validate(body, "InstrumentInput")
    validate(seeded_client.post("/api/v1/instruments", json=body).get_json(), "InstrumentEnvelope")
    validate(seeded_client.get("/api/v1/instruments").get_json(), "InstrumentList")
    validate(seeded_client.get("/api/v1/instruments/1").get_json(), "InstrumentEnvelope")


@pytest.mark.parametrize(
    "endpoint, schema",
    [
        ("summary", "SummaryAnalytics"),
        ("instruments", "GroupedAnalytics"),
        ("strategies", "GroupedAnalytics"),
        ("sessions", "GroupedAnalytics"),
        ("weekday", "GroupedAnalytics"),
        ("monthly", "GroupedAnalytics"),
        ("curve", "CurveAnalytics"),
    ],
)
@pytest.mark.parametrize("query", ["", "?instrument_id=5", "?start=2026-03-10&end=2026-03-12"])
def test_analytics_responses_match_schemas(seeded_client, endpoint, schema, query):
    validate(seeded_client.get(f"/api/v1/analytics/{endpoint}{query}").get_json(), schema)


def test_empty_analytics_match_schemas(client):
    validate(client.get("/api/v1/analytics/summary").get_json(), "SummaryAnalytics")
    validate(client.get("/api/v1/analytics/curve").get_json(), "CurveAnalytics")


def test_legacy_trade_matches_schemas(app, seeded_client):
    """A legacy trade (no exit time), simulated by clearing closed_at in SQL."""
    from tradeledger.db import get_db

    with app.app_context():
        conn = get_db()
        with conn:
            conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 1")
    body = seeded_client.get("/api/v1/analytics/summary").get_json()
    assert body["data"]["unknown_close_time_trade_ids"] == [1]
    validate(body, "SummaryAnalytics")
    validate(seeded_client.get("/api/v1/trades/1").get_json(), "TradeEnvelope")


def test_unavailable_r_matches_schema():
    """realised_r = null cannot come from the database (its CHECK constraint
    forbids a stop on the wrong side), but the engine supports it for other
    data sources, so the documented null shape is checked directly."""
    from decimal import Decimal

    from tests.factories import make_trade
    from tradeledger.api.serialise import trade_json

    trade = make_trade(
        stop_loss=Decimal("105"),
        instrument_id=1,
        closed_at="2026-01-05T11:00:00Z",
        take_profit=None,
        notes=None,
        created_at="2026-01-05T11:00:00Z",
        updated_at="2026-01-05T11:00:00Z",
    )
    body = trade_json(trade)
    assert body["result"]["realised_r"] is None and body["result"]["initial_risk"] is None
    assert body["result"]["r_unavailable_reason"]
    validate({"data": body}, "TradeEnvelope")


@pytest.mark.parametrize(
    "method, url, kwargs, status",
    [
        ("post", "/api/v1/trades", {"json": {"position_size": "0"}}, 400),
        ("post", "/api/v1/trades", {"data": "x", "content_type": "text/plain"}, 415),
        ("get", "/api/v1/trades/999", {}, 404),
        ("delete", "/api/v1/instruments", {}, 405),
        (
            "post",
            "/api/v1/instruments",
            {
                "json": {
                    "symbol": "EURUSD",
                    "name": "x",
                    "asset_class": "FOREX",
                    "price_increment": "1",
                    "contract_multiplier": "1",
                    "quote_currency": "USD",
                }
            },
            409,
        ),
    ],
)
def test_error_responses_match_schema_and_are_documented(seeded_client, method, url, kwargs, status):
    response = getattr(seeded_client, method)(url, **kwargs)
    assert response.status_code == status
    validate(response.get_json(), "Error")


# ---------- Examples are real ----------


def test_example_post_body_creates_a_trade(seeded_client):
    response = seeded_client.post("/api/v1/trades", json=EXAMPLE_TRADE_INPUT)
    assert response.status_code == 201
    example = SPEC["paths"]["/trades"]["post"]["responses"]["201"]["content"]["application/json"]["example"]
    actual = response.get_json()["data"]
    for field in ("symbol", "entry_price", "exit_price", "status", "closed_at"):
        assert actual[field] == example["data"][field]
    assert actual["result"] == example["data"]["result"]
    validate(example, "TradeEnvelope")


def test_validation_error_example_is_what_the_api_returns(seeded_client):
    example = SPEC["paths"]["/trades"]["post"]["responses"]["400"]["content"]["application/json"]["example"]
    body = {**EXAMPLE_TRADE_INPUT, "position_size": "0", "stop_loss": "1.09"}
    assert seeded_client.post("/api/v1/trades", json=body).get_json() == example


# ---------- Exported file ----------


def test_docs_openapi_json_is_up_to_date():
    path = Path(__file__).parent.parent / "docs" / "openapi.json"
    assert json.loads(path.read_text(encoding="utf-8")) == SPEC, (
        "docs/openapi.json is stale: run  flask --app tradeledger openapi"
    )


def test_openapi_cli_writes_the_spec(runner, tmp_path):
    out = tmp_path / "spec.json"
    result = runner.invoke(args=["openapi", "--output", str(out)])
    assert result.exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8")) == SPEC
```

### `tests/test_report_filters.py` (MODIFIED, lint)

**Why:** Ruff auto-fix: import order and formatting. Same assertions.  
**How to run or test:** Run: `python -m pytest tests/test_report_filters.py`

```python
"""Phase 6: report filters shared by the dashboard and the analytics API.

Three layers are tested separately so a failure points at the right code:
  1. parse_report_filters()  - validation only (no report)
  2. repository + build_report - the filters reach SQL and change results
  3. /api/v1/analytics/...    - the new strategy/session parameters

Sample data facts used below (tradeledger/seed.py, all synthetic):
  14 closed trades, 2 open (ids 14 and 15).
  Closed by strategy:  Breakout 5, Pullback 5, Reversal 2, none 2
  Closed by session:   LONDON 6, NEW_YORK 5, ASIA 2, none 1
  Closed Breakout + LONDON: trades 1, 3 and 10
"""

import pytest

from tests.factories import table_snapshot
from tests.test_openapi import validate
from tradeledger.performance import build_report
from tradeledger.report_filters import FILTER_NAMES, ReportFilters, parse_report_filters
from tradeledger.repositories import trades as trade_repo
from tradeledger.validation import ValidationError

BASE = "/api/v1/analytics"
ENDPOINTS = ["summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve"]


def parse(conn, blank_means_unset=True, **args):
    return parse_report_filters(args, conn, blank_means_unset=blank_means_unset)


# ---------- 1. Validation ----------


def test_no_arguments_means_no_filters(seeded_conn):
    filters, errors = parse(seeded_conn)
    assert errors == {}
    assert filters == ReportFilters()
    assert filters.is_empty and filters.active() == {}


def test_all_filters_valid(seeded_conn):
    filters, errors = parse(
        seeded_conn,
        instrument_id="1",
        strategy=" Breakout ",
        session="london",
        start="2026-03-01",
        end="2026-03-31",
    )
    assert errors == {}
    assert filters == ReportFilters(1, "Breakout", "LONDON", "2026-03-01", "2026-03-31")
    assert list(filters.as_kwargs()) == list(FILTER_NAMES)


def test_blank_values_mean_all_on_the_website(seeded_conn):
    filters, errors = parse(seeded_conn, instrument_id="", strategy="  ", session="", start="", end="")
    assert errors == {} and filters.is_empty


def test_blank_values_are_errors_in_the_api(seeded_conn):
    _, errors = parse(seeded_conn, blank_means_unset=False, instrument_id="", strategy=" ", session="")
    assert set(errors) == {"instrument_id", "strategy", "session"}


@pytest.mark.parametrize(
    "value, message",
    [
        ("abc", "must be a whole number of 1 or more"),
        ("0", "must be a whole number of 1 or more"),
        ("-1", "must be a whole number of 1 or more"),
        ("1.5", "must be a whole number of 1 or more"),
        ("999", "does not match an existing instrument"),
    ],
)
def test_bad_instrument(seeded_conn, value, message):
    filters, errors = parse(seeded_conn, instrument_id=value)
    assert errors == {"instrument_id": message}
    assert filters.instrument_id is None


def test_strategy_length_limit(seeded_conn):
    assert parse(seeded_conn, strategy="x" * 50)[1] == {}
    assert parse(seeded_conn, strategy="x" * 51)[1] == {"strategy": "must be 50 characters or fewer"}


@pytest.mark.parametrize("value", ["MOON", "london-ish", "1"])
def test_unknown_session(seeded_conn, value):
    assert parse(seeded_conn, session=value)[1] == {
        "session": "must be one of: ASIA, LONDON, NEW_YORK, OTHER"
    }


@pytest.mark.parametrize(
    "args, field",
    [
        ({"start": "2026-02-30"}, "start"),
        ({"end": "next week"}, "end"),
        ({"start": "2026-03-10", "end": "2026-03-09"}, "end"),
    ],
)
def test_bad_dates(seeded_conn, args, field):
    assert field in parse(seeded_conn, **args)[1]


def test_single_day_range_is_valid(seeded_conn):
    assert parse(seeded_conn, start="2026-03-02", end="2026-03-02")[1] == {}


def test_every_problem_is_reported_at_once(seeded_conn):
    _, errors = parse(seeded_conn, instrument_id="x", session="MOON", start="bad")
    assert set(errors) == {"instrument_id", "session", "start"}


# ---------- 2. Repository and build_report ----------


def test_repository_session_filter(seeded_conn):
    rows = trade_repo.list_trades(seeded_conn, session="LONDON")
    assert len(rows) == 7  # 6 closed + open trade 15
    assert {r["session"] for r in rows} == {"LONDON"}
    assert trade_repo.count_trades(seeded_conn, session="LONDON") == 7


def test_repository_rejects_unknown_session(seeded_conn):
    with pytest.raises(ValidationError):
        trade_repo.list_trades(seeded_conn, session="MOON")


@pytest.mark.parametrize(
    "kwargs, closed, open_",
    [
        ({"strategy": "Breakout"}, 5, 1),
        ({"strategy": "Pullback"}, 5, 0),
        ({"session": "ASIA"}, 2, 0),
        ({"session": "LONDON"}, 6, 1),
        ({"strategy": "Breakout", "session": "LONDON"}, 3, 0),
        ({"instrument_id": 1, "strategy": "Pullback"}, 2, 0),
        ({"strategy": "No such strategy"}, 0, 0),
    ],
)
def test_build_report_filters(seeded_conn, kwargs, closed, open_):
    report = build_report(seeded_conn, **kwargs)
    assert report.overall.trades == closed
    assert report.open_trades == open_


def test_combined_filters_select_exactly_the_right_trades(seeded_conn):
    report = build_report(seeded_conn, strategy="Breakout", session="LONDON")
    assert [p.trade_id for p in report.cumulative_r] == [1, 3, 10]


def test_date_filter_uses_the_realised_date(app, seeded_conn):
    """Entered on 31 March, closed on 1 April: it belongs to April."""
    trade_repo.create_trade(
        seeded_conn,
        {
            "instrument_id": 1,
            "direction": "LONG",
            "status": "CLOSED",
            "trade_date": "2026-03-31T22:00:00Z",
            "closed_at": "2026-04-01T01:00:00Z",
            "entry_price": "1.1",
            "stop_loss": "1.09",
            "exit_price": "1.11",
            "position_size": "1",
        },
    )
    assert build_report(seeded_conn, start="2026-04-01", end="2026-04-30").overall.trades == 1
    assert build_report(seeded_conn, start="2026-03-01", end="2026-03-31").overall.trades == 14


def test_filter_values_are_bound_parameters_not_sql(seeded_conn):
    """A classic injection string is just a strategy name nobody used."""
    before = trade_repo.count_trades(seeded_conn)
    report = build_report(seeded_conn, strategy="' OR 1=1 --")
    assert report.overall.trades == 0 and report.open_trades == 0
    assert trade_repo.count_trades(seeded_conn) == before


# ---------- 3. API ----------


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_every_analytics_endpoint_accepts_the_new_filters(seeded_client, endpoint):
    response = seeded_client.get(f"{BASE}/{endpoint}?strategy=Breakout&session=london")
    assert response.status_code == 200
    filters = response.get_json()["meta"]["filters"]
    assert filters["strategy"] == "Breakout" and filters["session"] == "LONDON"


def test_api_strategy_filter_changes_the_numbers(seeded_client):
    body = seeded_client.get(f"{BASE}/summary?strategy=Breakout").get_json()
    assert body["data"]["overall"]["trades"] == 5
    assert body["data"]["open_trades"] == 1
    validate(body, "SummaryAnalytics")


def test_api_session_filter_on_grouped_endpoint(seeded_client):
    body = seeded_client.get(f"{BASE}/strategies?session=ASIA").get_json()
    assert [(g["group"], g["trades"]) for g in body["data"]] == [("Pullback", 1), ("Reversal", 1)]
    validate(body, "GroupedAnalytics")


def test_api_curve_with_combined_filters(seeded_client):
    body = seeded_client.get(f"{BASE}/curve?strategy=Breakout&session=LONDON").get_json()
    assert [point["trade_id"] for point in body["data"]] == [1, 3, 10]
    validate(body, "CurveAnalytics")


@pytest.mark.parametrize(
    "query, field",
    [
        ("session=MOON", "session"),
        ("session=", "session"),
        ("strategy=", "strategy"),
        ("strategy=" + "x" * 51, "strategy"),
    ],
)
def test_api_rejects_bad_new_filters(seeded_client, query, field):
    response = seeded_client.get(f"{BASE}/summary?{query}")
    assert response.status_code == 400
    assert field in response.get_json()["error"]["details"]
    validate(response.get_json(), "Error")


def test_analytics_requests_never_change_data(app, seeded_client):
    before = (table_snapshot(app, "trades"), table_snapshot(app, "instruments"))
    for endpoint in ENDPOINTS:
        seeded_client.get(f"{BASE}/{endpoint}?strategy=Breakout&session=LONDON&start=2026-03-01")
    seeded_client.get("/analytics?strategy=Breakout&session=LONDON")
    assert (table_snapshot(app, "trades"), table_snapshot(app, "instruments")) == before
```

### `tests/test_seed_and_analytics.py` (MODIFIED, lint)

**Why:** `[*SAMPLE_TRADES, broken]` instead of list concatenation (Ruff RUF005). Same assertions.  
**How to run or test:** Run: `python -m pytest tests/test_seed_and_analytics.py`

```python
"""Sample data, analytical SQL and the CLI commands.

Expected values were worked out by hand from tradeledger/seed.py
(see the comments beside each sample trade).
"""

from decimal import Decimal

import pytest

from tradeledger import analytics, seed
from tradeledger.seed import SAMPLE_TRADES, seed_sample_data
from tradeledger.validation import ValidationError


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------- Seeding ----------


def test_seed_inserts_everything_once(conn):
    assert seed_sample_data(conn) == {"instruments": 5, "trades": 16}


def test_seed_is_idempotent(conn):
    seed_sample_data(conn)
    assert seed_sample_data(conn) == {"instruments": 0, "trades": 0}
    assert count(conn, "instruments") == 5
    assert count(conn, "trades") == 16


def test_seed_does_not_touch_existing_user_trades(conn, long_trade):
    from tradeledger.repositories.trades import create_trade

    create_trade(conn, dict(long_trade, trade_date="2026-01-05T10:00:00Z"))
    seed_sample_data(conn)
    assert count(conn, "trades") == 17


def test_seed_covers_required_scenarios(seeded_conn):
    rows = seeded_conn.execute("SELECT direction, status, strategy, session FROM trades").fetchall()
    assert {r["direction"] for r in rows} == {"LONG", "SHORT"}
    assert {r["status"] for r in rows} == {"OPEN", "CLOSED"}
    assert None in {r["strategy"] for r in rows}
    assert len({r["session"] for r in rows}) >= 3


def test_seed_is_all_or_nothing(conn, monkeypatch):
    """If one sample record is invalid, the whole seed run is rolled back."""
    broken = dict(SAMPLE_TRADES[0], trade_date="2026-04-01T09:00:00Z", position_size="-1")
    monkeypatch.setattr(seed, "SAMPLE_TRADES", [*SAMPLE_TRADES, broken])
    with pytest.raises(ValidationError):
        seed.seed_sample_data(conn)
    assert count(conn, "instruments") == 0
    assert count(conn, "trades") == 0


# ---------- Analytics on an empty database ----------


def test_analytics_on_empty_database(conn):
    assert analytics.closed_trade_count(conn) == 0
    assert analytics.closed_count_by_instrument(conn) == []
    assert analytics.closed_by_weekday(conn) == []
    assert analytics.trades_by_strategy(conn) == []
    assert analytics.trades_by_session(conn) == []
    assert analytics.win_rate_by_instrument(conn) == []


# ---------- Analytics on the sample data ----------


def test_closed_trade_count(seeded_conn):
    assert analytics.closed_trade_count(seeded_conn) == 14


def test_closed_count_by_instrument(seeded_conn):
    assert analytics.closed_count_by_instrument(seeded_conn) == [
        {"symbol": "XAUUSD", "closed_trades": 4},
        {"symbol": "EURUSD", "closed_trades": 3},
        {"symbol": "GBPUSD", "closed_trades": 3},
        {"symbol": "NAS100", "closed_trades": 2},
        {"symbol": "UK100", "closed_trades": 2},
    ]


def test_instrument_with_no_closed_trades_shows_zero(seeded_conn):
    from tradeledger.repositories.instruments import create_instrument

    create_instrument(
        seeded_conn,
        {
            "symbol": "US30",
            "name": "Wall Street 30 (test)",
            "asset_class": "INDEX",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
    )
    rows = analytics.closed_count_by_instrument(seeded_conn)
    assert {"symbol": "US30", "closed_trades": 0} in rows


def test_closed_by_weekday(seeded_conn):
    assert analytics.closed_by_weekday(seeded_conn) == [
        {"weekday_number": 1, "weekday": "Monday", "closed_trades": 3},
        {"weekday_number": 2, "weekday": "Tuesday", "closed_trades": 3},
        {"weekday_number": 3, "weekday": "Wednesday", "closed_trades": 3},
        {"weekday_number": 4, "weekday": "Thursday", "closed_trades": 2},
        {"weekday_number": 5, "weekday": "Friday", "closed_trades": 3},
    ]


def test_trades_by_strategy(seeded_conn):
    assert analytics.trades_by_strategy(seeded_conn) == [
        {"strategy": "Breakout", "total_trades": 6, "open_trades": 1, "closed_trades": 5},
        {"strategy": "Pullback", "total_trades": 5, "open_trades": 0, "closed_trades": 5},
        {"strategy": "Reversal", "total_trades": 3, "open_trades": 1, "closed_trades": 2},
        {"strategy": "Unspecified", "total_trades": 2, "open_trades": 0, "closed_trades": 2},
    ]


def test_trades_by_session(seeded_conn):
    assert analytics.trades_by_session(seeded_conn) == [
        {"session": "LONDON", "total_trades": 7, "open_trades": 1, "closed_trades": 6},
        {"session": "NEW_YORK", "total_trades": 6, "open_trades": 1, "closed_trades": 5},
        {"session": "ASIA", "total_trades": 2, "open_trades": 0, "closed_trades": 2},
        {"session": "Unspecified", "total_trades": 1, "open_trades": 0, "closed_trades": 1},
    ]


def test_win_rate_by_instrument(seeded_conn):
    assert analytics.win_rate_by_instrument(seeded_conn) == [
        {
            "symbol": "XAUUSD",
            "quote_currency": "USD",
            "closed_trades": 4,
            "wins": 3,
            "losses": 1,
            "breakevens": 0,
            "win_rate_pct": 75.0,
        },
        {
            "symbol": "NAS100",
            "quote_currency": "USD",
            "closed_trades": 2,
            "wins": 1,
            "losses": 1,
            "breakevens": 0,
            "win_rate_pct": 50.0,
        },
        {
            "symbol": "UK100",
            "quote_currency": "GBP",
            "closed_trades": 2,
            "wins": 1,
            "losses": 0,
            "breakevens": 1,
            "win_rate_pct": 50.0,
        },
        {
            "symbol": "EURUSD",
            "quote_currency": "USD",
            "closed_trades": 3,
            "wins": 1,
            "losses": 2,
            "breakevens": 0,
            "win_rate_pct": 33.3,
        },
        {
            "symbol": "GBPUSD",
            "quote_currency": "USD",
            "closed_trades": 3,
            "wins": 1,
            "losses": 1,
            "breakevens": 1,
            "win_rate_pct": 33.3,
        },
    ]


def test_fees_can_turn_a_price_gain_into_a_loss(seeded_conn):
    """Sample trade 9: long EURUSD, exit 2 points (0.00002) above entry,
    gross +2.00 USD, fees 7.00 -> net -5.00 USD -> counted as a LOSS."""
    eurusd = next(r for r in analytics.win_rate_by_instrument(seeded_conn) if r["symbol"] == "EURUSD")
    assert eurusd["losses"] == 2


def test_sql_win_rate_agrees_with_exact_decimal_calculation(seeded_conn):
    """Cross-check the float-based SQL against exact Decimal arithmetic."""
    rows = seeded_conn.execute(
        """
        SELECT i.symbol, t.direction, t.entry_price, t.exit_price,
               t.position_size, t.fees, i.contract_multiplier
        FROM trades t JOIN instruments i ON i.id = t.instrument_id
        WHERE t.status = 'CLOSED'
        """
    ).fetchall()

    expected = {}
    for r in rows:
        move = Decimal(r["exit_price"]) - Decimal(r["entry_price"])
        if r["direction"] == "SHORT":
            move = -move
        net = move * Decimal(r["position_size"]) * Decimal(r["contract_multiplier"]) - Decimal(r["fees"])
        tally = expected.setdefault(r["symbol"], {"wins": 0, "losses": 0, "breakevens": 0})
        tally["wins" if net > 0 else "losses" if net < 0 else "breakevens"] += 1

    actual = {
        r["symbol"]: {"wins": r["wins"], "losses": r["losses"], "breakevens": r["breakevens"]}
        for r in analytics.win_rate_by_instrument(seeded_conn)
    }
    assert actual == expected


# ---------- CLI commands ----------


def test_cli_init_seed_and_info(runner):
    assert "Database ready" in runner.invoke(args=["init-db"]).output
    assert "5 instrument(s) and 16 trade(s)" in runner.invoke(args=["seed-db"]).output
    assert "0 instrument(s) and 0 trade(s)" in runner.invoke(args=["seed-db"]).output
    info = runner.invoke(args=["db-info"]).output
    assert "Schema version: 2" in info  # was 1 before the Phase 4 migration
    assert "trades" in info and "16" in info


def test_cli_analytics_runs_every_query(runner):
    runner.invoke(args=["seed-db"])
    output = runner.invoke(args=["analytics"]).output
    for name in analytics.ALL_QUERIES:
        assert f"== {name} ==" in output


def test_cli_run_sql_is_read_only(runner):
    runner.invoke(args=["seed-db"])
    ok = runner.invoke(args=["run-sql", "SELECT COUNT(*) AS n FROM trades"])
    assert "16" in ok.output
    blocked = runner.invoke(args=["run-sql", "DELETE FROM trades"])
    assert blocked.exit_code != 0
    assert "readonly" in blocked.output
    still_there = runner.invoke(args=["run-sql", "SELECT COUNT(*) AS n FROM trades"])
    assert "16" in still_there.output


def test_cli_reset_db_empties_tables(runner):
    runner.invoke(args=["seed-db"])
    result = runner.invoke(args=["reset-db", "--yes"])
    assert "Database reset" in result.output
    for table in ("trades", "instruments"):
        output = runner.invoke(args=["run-sql", f"SELECT COUNT(*) AS n FROM {table}"]).output
        assert output.strip().splitlines()[-1].strip() == "0"
```

### `tests/test_web_integration.py` (MODIFIED, lint)

**Why:** Ruff auto-fix: import order. Same assertions.  
**How to run or test:** Run: `python -m pytest tests/test_web_integration.py`

```python
"""Phase 5 end to end (inside the test client): form -> SQLite -> journal ->
details -> analytics -> API, plus the repository search added for the
journal."""

import pytest

from tests.web_helpers import (
    closed_trade_form,
    count_rows,
    new_trade_id,
    open_trade_form,
    post_form,
)
from tradeledger.repositories import trades as trade_repo
from tradeledger.validation import ValidationError

# ---------- Full journeys ----------


def test_trade_lifecycle_open_close_edit_delete(app, client):
    """Start from an EMPTY database, as a new user would."""
    # 1. Add an instrument through the website.
    response = post_form(
        client,
        "/instruments",
        {
            "symbol": "EURUSD",
            "name": "Euro / US Dollar (test)",
            "asset_class": "FOREX",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        token_page="/instruments",
    )
    assert response.status_code == 302

    # 2. Record an open trade.
    trade_id = new_trade_id(post_form(client, "/trades/new", open_trade_form()))
    journal = client.get("/trades").get_data(as_text=True)
    assert "Showing 1–1 of 1 trade." in journal
    assert ">Open</span>" in journal
    overview = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" in overview  # open trades have no result

    # 3. Close it via the edit form.
    response = post_form(client, f"/trades/{trade_id}/edit", closed_trade_form())
    assert response.status_code == 302
    detail = client.get(f"/trades/{trade_id}").get_data(as_text=True)
    assert "393.00 USD" in detail and "+1.97R" in detail and ">Win</span>" in detail

    # 4. The overview, analytics preview and API all agree.
    overview = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" not in overview
    assert "393.00 USD" in overview
    analytics = client.get("/analytics").get_data(as_text=True)
    assert "EURUSD" in analytics and "100.0%" in analytics
    api = client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    assert api["trades"] == 1 and api["money"]["USD"]["net_pnl"] == "393"

    # 5. Delete it; everything returns to empty.
    post_form(client, f"/trades/{trade_id}/delete", {})
    assert count_rows(app, "trades") == 0
    assert "Your journal is empty" in client.get("/").get_data(as_text=True)
    assert client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]["trades"] == 0


def test_website_trade_matches_api_trade_exactly(seeded_client, trade_body):
    """The same trade entered through the form and through the API is stored
    identically: one validation layer, one storage format."""
    web_id = new_trade_id(
        post_form(
            seeded_client,
            "/trades/new",
            closed_trade_form(
                trade_date="2026-04-01T08:15", closed_at="2026-04-01T11:40", notes=trade_body["notes"]
            ),
        )
    )
    api_id = seeded_client.post("/api/v1/trades", json=trade_body).get_json()["data"]["id"]

    web = seeded_client.get(f"/api/v1/trades/{web_id}").get_json()["data"]
    api = seeded_client.get(f"/api/v1/trades/{api_id}").get_json()["data"]
    for volatile in ("id", "created_at", "updated_at"):
        web.pop(volatile, None)
        api.pop(volatile, None)
    assert web == api


def test_trade_added_by_api_appears_on_the_website(seeded_client, trade_body):
    trade_id = seeded_client.post("/api/v1/trades", json={**trade_body, "notes": "from-the-api"}).get_json()[
        "data"
    ]["id"]
    assert f'href="/trades/{trade_id}"' in seeded_client.get("/trades?q=from-the-api").get_data(as_text=True)


def test_website_edits_change_the_analytics(seeded_client):
    before = seeded_client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    post_form(seeded_client, "/trades/1/delete", {})
    after = seeded_client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    assert after["trades"] == before["trades"] - 1
    assert after["wins"] == before["wins"] - 1  # trade 1 was a win


def test_detail_values_match_the_api(seeded_client):
    api = seeded_client.get("/api/v1/trades/1").get_json()["data"]
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert api["result"]["net_pnl"] == "393"
    assert "393.00 USD" in html


def test_journal_and_api_list_the_same_trades(seeded_client):
    api_ids = [t["id"] for t in seeded_client.get("/api/v1/trades?per_page=100").get_json()["data"]]
    html = seeded_client.get("/trades").get_data(as_text=True)
    for trade_id in api_ids:
        assert f'href="/trades/{trade_id}"' in html


# ---------- Repository: search and strategy filters ----------


def test_search_matches_symbol_case_insensitively(seeded_conn):
    rows = trade_repo.list_trades(seeded_conn, search="xau")
    assert len(rows) == 4 and {r["symbol"] for r in rows} == {"XAUUSD"}
    assert trade_repo.count_trades(seeded_conn, search="xau") == 4


def test_search_matches_strategy_and_notes(seeded_conn):
    assert trade_repo.count_trades(seeded_conn, search="pull") == 5
    assert trade_repo.count_trades(seeded_conn, search="fees") == 1


@pytest.mark.parametrize("wildcard", ["%", "_", "\\"])
def test_search_wildcards_are_literal(seeded_conn, wildcard):
    """% and _ are LIKE wildcards. They are escaped, so searching for
    them looks for the character itself, which no sample trade contains."""
    assert trade_repo.count_trades(seeded_conn, search=wildcard) == 0


def test_search_injection_attempt_is_plain_text(seeded_conn):
    assert trade_repo.count_trades(seeded_conn, search="' OR 1=1 --") == 0
    assert trade_repo.count_trades(seeded_conn) == 16


def test_search_combines_with_other_filters_and_pagination(seeded_conn):
    closed = trade_repo.count_trades(seeded_conn, search="usd", status="CLOSED")
    page_1 = trade_repo.list_trades(seeded_conn, search="usd", status="CLOSED", limit=3, offset=0)
    page_2 = trade_repo.list_trades(seeded_conn, search="usd", status="CLOSED", limit=3, offset=3)
    assert len(page_1) == 3
    assert not {r["id"] for r in page_1} & {r["id"] for r in page_2}
    assert all(r["status"] == "CLOSED" for r in page_1 + page_2)
    assert closed >= 6


@pytest.mark.parametrize("bad", ["", "   ", "x" * 101])
def test_search_rejects_empty_or_overlong_text(seeded_conn, bad):
    with pytest.raises(ValidationError) as excinfo:
        trade_repo.list_trades(seeded_conn, search=bad)
    assert "q" in excinfo.value.errors


def test_strategy_filter_is_exact(seeded_conn):
    assert trade_repo.count_trades(seeded_conn, strategy="Breakout") == 6
    assert trade_repo.count_trades(seeded_conn, strategy="break") == 0


def test_list_strategies_is_sorted_and_distinct(seeded_conn):
    assert trade_repo.list_strategies(seeded_conn) == ["Breakout", "Pullback", "Reversal"]
```

## 4. Modified files: layout only (`ruff format`)

### `tradeledger/api/__init__.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""TradeLedger JSON API, version 1, mounted at /api/v1.

Modules
  errors.py       ApiError and the single JSON error format
  http.py         reading JSON bodies and query strings safely
  serialise.py    rows and analytics results -> JSON-safe dicts
  trades.py       /trades endpoints
  instruments.py  /instruments endpoints
  analytics.py    /analytics endpoints (thin wrappers around build_report)
  openapi.py      the OpenAPI 3.1 specification, served at /openapi.json

Request flow
  HTTP request -> route (thin) -> http.py checks shape -> repository
  (validation.py rules + parameterised SQL) -> SQLite
  -> performance engine (analytics only) -> serialise.py -> JSON response
"""

from flask import Blueprint, current_app, request

from ..repositories import DuplicateError
from ..validation import ValidationError
from .errors import ApiError, error_response

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

# Endpoints that stay available when the API is disabled: documentation
# only, never trading data.
_ALWAYS_AVAILABLE = {"api.openapi_spec"}


@api_bp.before_request
def refuse_when_disabled():
    if not current_app.config["API_ENABLED"] and request.endpoint not in _ALWAYS_AVAILABLE:
        raise ApiError(
            403,
            "api_disabled",
            "The API is disabled in this environment because it has no authentication.",
        )


@api_bp.after_request
def no_caching(response):
    # Trading data should not be stored by browsers or proxies.
    response.headers["Cache-Control"] = "no-store"
    return response


@api_bp.errorhandler(ApiError)
def handle_api_error(exc):
    return error_response(exc.status, exc.code, exc.message, exc.details)


@api_bp.errorhandler(ValidationError)
def handle_validation_error(exc):
    return error_response(400, "validation_error", "The request is invalid.", exc.errors)


@api_bp.errorhandler(DuplicateError)
def handle_duplicate(exc):
    return error_response(409, "conflict", str(exc))


# Importing the modules registers their routes on api_bp.
from . import analytics, instruments, openapi, trades  # noqa: E402,F401
```

### `tradeledger/api/serialise.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Turning repository rows and analytics results into API JSON.

Rules
  * Every Decimal becomes a string with its exact value ("1.085"), never a
    float. formatting.to_jsonable() does the conversion.
  * Unavailable values are null.
  * The field list for each resource is explicit, so adding a database
    column never leaks into the API by accident.
"""

from ..performance.formatting import to_jsonable
from ..performance.trade_math import IncompleteTradeError, evaluate_trade

TRADE_FIELDS = (
    "id",
    "instrument_id",
    "symbol",
    "quote_currency",
    "trade_date",
    "closed_at",
    "direction",
    "status",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "strategy",
    "session",
    "notes",
    "created_at",
    "updated_at",
)

INSTRUMENT_FIELDS = (
    "id",
    "symbol",
    "name",
    "asset_class",
    "price_increment",
    "contract_multiplier",
    "quote_currency",
    "created_at",
)

RESULT_FIELDS = (
    "outcome",
    "gross_pnl",
    "fees",
    "net_pnl",
    "initial_risk",
    "realised_r",
    "planned_rr",
    "r_unavailable_reason",
)


def trade_result(trade):
    """Calculated result for a CLOSED trade, using the Phase 3 engine.
    None for OPEN trades: they have no realised result yet."""
    if trade["status"] != "CLOSED":
        return None
    try:
        result = evaluate_trade(trade)
    except IncompleteTradeError:
        return None
    return {name: to_jsonable(getattr(result, name)) for name in RESULT_FIELDS}


def trade_json(trade):
    body = {name: to_jsonable(trade[name]) for name in TRADE_FIELDS}
    body["result"] = trade_result(trade)
    return body


def instrument_json(instrument):
    return {name: to_jsonable(instrument[name]) for name in INSTRUMENT_FIELDS}


def summary_json(summary):
    return to_jsonable(summary)


def groups_json(groups):
    """Dict of label -> Summary, as an ordered list (JSON objects have no
    guaranteed order; lists do)."""
    return [{"group": label, **to_jsonable(summary)} for label, summary in groups.items()]
```

### `tradeledger/repositories/instruments.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Instrument repository.

Return values
  create_instrument          -> new id (int)
  get_instrument             -> dict, or None if not found
  get_instrument_by_symbol   -> dict, or None if not found
  list_instruments           -> list of dicts (empty list if none), by symbol

Errors
  ValidationError  invalid input (nothing is written)
  DuplicateError   the symbol already exists (nothing is written)
"""

import sqlite3
from decimal import Decimal

from ..validation import validate_instrument
from . import DuplicateError

_COLUMNS = "id, symbol, name, asset_class, price_increment, contract_multiplier, quote_currency, created_at"


def _to_dict(row):
    if row is None:
        return None
    record = dict(row)
    record["price_increment"] = Decimal(record["price_increment"])
    record["contract_multiplier"] = Decimal(record["contract_multiplier"])
    return record


def create_instrument(conn, data):
    clean = validate_instrument(data)
    try:
        with conn:  # commit on success, roll back on any exception
            cursor = conn.execute(
                """
                INSERT INTO instruments
                    (symbol, name, asset_class, price_increment,
                     contract_multiplier, quote_currency)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    clean["symbol"],
                    clean["name"],
                    clean["asset_class"],
                    clean["price_increment"],
                    clean["contract_multiplier"],
                    clean["quote_currency"],
                ),
            )
    except sqlite3.IntegrityError as exc:
        if "UNIQUE" in str(exc):
            raise DuplicateError(f"Instrument '{clean['symbol']}' already exists") from exc
        raise
    return cursor.lastrowid


def get_instrument(conn, instrument_id):
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM instruments WHERE id = ?",  # noqa: S608 - _COLUMNS is a module constant
        (instrument_id,),
    ).fetchone()
    return _to_dict(row)


def get_instrument_by_symbol(conn, symbol):
    if not isinstance(symbol, str):
        return None
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM instruments WHERE symbol = ?",  # noqa: S608 - _COLUMNS is a module constant
        (symbol.strip().upper(),),
    ).fetchone()
    return _to_dict(row)


def list_instruments(conn):
    rows = conn.execute(f"SELECT {_COLUMNS} FROM instruments ORDER BY symbol").fetchall()  # noqa: S608 - constant
    return [_to_dict(row) for row in rows]
```

### `tradeledger/seed.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Synthetic sample data for development and tests.

EVERYTHING IN THIS FILE IS INVENTED. None of it is anyone's real trading.

About the instrument values
--------------------------
contract_multiplier means "money, in quote_currency, per 1.0 move in price
per 1.0 unit of position_size". Real values depend on the broker's contract
specification and on what one unit of position size means at that broker.
The figures below are ILLUSTRATIVE ASSUMPTIONS for synthetic data only:

  EURUSD, GBPUSD  1 unit = 1 standard lot of 100,000 base currency -> 100000
  XAUUSD          1 unit = 1 lot of 100 troy ounces                -> 100
  NAS100, UK100   1 unit = a CFD paying 1 quote-currency per point  -> 1

Before recording real trades, replace them with your broker's figures.
Fees are assumed to be in the instrument's quote currency.

About closed_at (added in Phase 4)
----------------------------------
Each synthetic closed trade has a synthetic exit time on the same day as its
entry, and the trades close in the same order they were opened. That keeps
every Phase 2 and Phase 3 expected result valid. This is invented sample
data, so giving it exit times fabricates nothing. Real trades recorded
before Phase 4 are never given invented exit times (see migrations.py).
"""

from .validation import validate_instrument, validate_trade

SAMPLE_INSTRUMENTS = [
    {
        "symbol": "EURUSD",
        "name": "Euro / US Dollar (sample)",
        "asset_class": "FOREX",
        "price_increment": "0.00001",
        "contract_multiplier": "100000",
        "quote_currency": "USD",
    },
    {
        "symbol": "GBPUSD",
        "name": "British Pound / US Dollar (sample)",
        "asset_class": "FOREX",
        "price_increment": "0.00001",
        "contract_multiplier": "100000",
        "quote_currency": "USD",
    },
    {
        "symbol": "XAUUSD",
        "name": "Gold / US Dollar (sample)",
        "asset_class": "COMMODITY",
        "price_increment": "0.01",
        "contract_multiplier": "100",
        "quote_currency": "USD",
    },
    {
        "symbol": "NAS100",
        "name": "US Tech 100 index CFD (sample)",
        "asset_class": "INDEX",
        "price_increment": "0.1",
        "contract_multiplier": "1",
        "quote_currency": "USD",
    },
    {
        "symbol": "UK100",
        "name": "UK 100 index CFD (sample)",
        "asset_class": "INDEX",
        "price_increment": "0.1",
        "contract_multiplier": "1",
        "quote_currency": "GBP",
    },
]

# Net P&L in the comment = gross - fees, in the instrument's quote currency.
SAMPLE_TRADES = [
    # 1  WIN  +393.00 USD
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-02T08:15:00Z",
        "closed_at": "2026-03-02T11:40:00Z",
        "direction": "LONG",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Sample: clean break of Asian range.",
    },
    # 2  LOSS -103.50 USD
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-03T13:40:00Z",
        "closed_at": "2026-03-03T15:05:00Z",
        "direction": "SHORT",
        "entry_price": "1.08800",
        "stop_loss": "1.09000",
        "take_profit": "1.08400",
        "exit_price": "1.09000",
        "position_size": "0.5",
        "fees": "3.5",
        "strategy": "Pullback",
        "session": "NEW_YORK",
        "notes": "Sample: stopped out on US data.",
    },
    # 3  BREAKEVEN 0.00 USD
    {
        "symbol": "GBPUSD",
        "trade_date": "2026-03-04T08:30:00Z",
        "closed_at": "2026-03-04T10:10:00Z",
        "direction": "LONG",
        "entry_price": "1.27000",
        "stop_loss": "1.26800",
        "take_profit": "1.27400",
        "exit_price": "1.27000",
        "position_size": "1",
        "fees": "0",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Sample: moved stop to entry.",
    },
    # 4  WIN  +293.00 USD
    {
        "symbol": "GBPUSD",
        "trade_date": "2026-03-05T14:05:00Z",
        "closed_at": "2026-03-05T17:30:00Z",
        "direction": "SHORT",
        "entry_price": "1.27500",
        "stop_loss": "1.27700",
        "take_profit": "1.27100",
        "exit_price": "1.27200",
        "position_size": "1",
        "fees": "7",
        "strategy": "Reversal",
        "session": "NEW_YORK",
        "notes": "Sample: closed early before target.",
    },
    # 5  WIN  +198.00 USD
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-06T14:30:00Z",
        "closed_at": "2026-03-06T16:45:00Z",
        "direction": "LONG",
        "entry_price": "2150.00",
        "stop_loss": "2140.00",
        "take_profit": "2170.00",
        "exit_price": "2170.00",
        "position_size": "0.1",
        "fees": "2",
        "strategy": "Breakout",
        "session": "NEW_YORK",
        "notes": None,
    },
    # 6  LOSS -133.00 USD
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-09T02:10:00Z",
        "closed_at": "2026-03-09T05:20:00Z",
        "direction": "SHORT",
        "entry_price": "2165.50",
        "stop_loss": "2172.00",
        "take_profit": "2150.00",
        "exit_price": "2172.00",
        "position_size": "0.2",
        "fees": "3",
        "strategy": "Reversal",
        "session": "ASIA",
        "notes": None,
    },
    # 7  WIN  +198.50 USD
    {
        "symbol": "NAS100",
        "trade_date": "2026-03-10T14:45:00Z",
        "closed_at": "2026-03-10T16:00:00Z",
        "direction": "LONG",
        "entry_price": "18250.0",
        "stop_loss": "18200.0",
        "take_profit": "18350.0",
        "exit_price": "18350.0",
        "position_size": "2",
        "fees": "1.5",
        "strategy": "Pullback",
        "session": "NEW_YORK",
        "notes": None,
    },
    # 8  LOSS -81.50 USD
    {
        "symbol": "NAS100",
        "trade_date": "2026-03-11T15:00:00Z",
        "closed_at": "2026-03-11T15:50:00Z",
        "direction": "SHORT",
        "entry_price": "18300.0",
        "stop_loss": "18340.0",
        "take_profit": "18200.0",
        "exit_price": "18340.0",
        "position_size": "2",
        "fees": "1.5",
        "strategy": "Breakout",
        "session": "NEW_YORK",
        "notes": None,
    },
    # 9  LOSS -5.00 USD  (exit ABOVE entry on a long, but fees exceed the gain)
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-12T09:00:00Z",
        "closed_at": "2026-03-12T09:35:00Z",
        "direction": "LONG",
        "entry_price": "1.09100",
        "stop_loss": "1.08950",
        "take_profit": "1.09400",
        "exit_price": "1.09102",
        "position_size": "1",
        "fees": "7",
        "strategy": "Pullback",
        "session": "LONDON",
        "notes": "Sample: scratched; fees made it a loss.",
    },
    # 10 WIN  +200.00 GBP
    {
        "symbol": "UK100",
        "trade_date": "2026-03-13T10:30:00Z",
        "closed_at": "2026-03-13T13:15:00Z",
        "direction": "LONG",
        "entry_price": "7700.0",
        "stop_loss": "7680.0",
        "take_profit": "7740.0",
        "exit_price": "7740.0",
        "position_size": "5",
        "fees": "0",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": None,
    },
    # 11 BREAKEVEN 0.00 GBP (no strategy, no target)
    {
        "symbol": "UK100",
        "trade_date": "2026-03-16T11:00:00Z",
        "closed_at": "2026-03-16T12:30:00Z",
        "direction": "SHORT",
        "entry_price": "7750.0",
        "stop_loss": "7770.0",
        "take_profit": None,
        "exit_price": "7750.0",
        "position_size": "5",
        "fees": "0",
        "strategy": None,
        "session": "LONDON",
        "notes": None,
    },
    # 12 LOSS -157.00 USD
    {
        "symbol": "GBPUSD",
        "trade_date": "2026-03-17T08:05:00Z",
        "closed_at": "2026-03-17T08:50:00Z",
        "direction": "LONG",
        "entry_price": "1.28000",
        "stop_loss": "1.27850",
        "take_profit": "1.28300",
        "exit_price": "1.27850",
        "position_size": "1",
        "fees": "7",
        "strategy": "Pullback",
        "session": "LONDON",
        "notes": None,
    },
    # 13 WIN  +158.00 USD
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-18T03:20:00Z",
        "closed_at": "2026-03-18T07:00:00Z",
        "direction": "LONG",
        "entry_price": "2180.00",
        "stop_loss": "2172.00",
        "take_profit": "2196.00",
        "exit_price": "2196.00",
        "position_size": "0.1",
        "fees": "2",
        "strategy": "Pullback",
        "session": "ASIA",
        "notes": None,
    },
    # 14 OPEN
    {
        "symbol": "NAS100",
        "trade_date": "2026-03-19T14:35:00Z",
        "closed_at": None,
        "direction": "LONG",
        "entry_price": "18500.0",
        "stop_loss": "18440.0",
        "take_profit": "18620.0",
        "exit_price": None,
        "position_size": "1",
        "fees": "0.75",
        "strategy": "Breakout",
        "session": "NEW_YORK",
        "notes": "Sample: still running.",
    },
    # 15 OPEN
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-20T08:45:00Z",
        "closed_at": None,
        "direction": "SHORT",
        "entry_price": "1.09300",
        "stop_loss": "1.09450",
        "take_profit": None,
        "exit_price": None,
        "position_size": "1",
        "fees": "0",
        "strategy": "Reversal",
        "session": "LONDON",
        "notes": None,
    },
    # 16 WIN  +38.00 USD (no strategy, no session)
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-20T15:10:00Z",
        "closed_at": "2026-03-20T18:20:00Z",
        "direction": "SHORT",
        "entry_price": "2190.00",
        "stop_loss": "2198.00",
        "take_profit": None,
        "exit_price": "2186.00",
        "position_size": "0.1",
        "fees": "2",
        "strategy": None,
        "session": None,
        "notes": None,
    },
]


def seed_sample_data(conn):
    """Insert the sample instruments and trades. Safe to run repeatedly.

    * An instrument is skipped if its symbol already exists.
    * A trade is skipped if a trade with the same instrument, trade_date,
      direction and entry_price already exists.
    * Everything happens in ONE transaction: if any record fails, nothing
      from this run is kept.

    Returns {"instruments": n_inserted, "trades": n_inserted}.
    """
    inserted = {"instruments": 0, "trades": 0}

    with conn:
        for raw in SAMPLE_INSTRUMENTS:
            item = validate_instrument(raw)
            cursor = conn.execute(
                """
                INSERT INTO instruments
                    (symbol, name, asset_class, price_increment,
                     contract_multiplier, quote_currency)
                VALUES (:symbol, :name, :asset_class, :price_increment,
                        :contract_multiplier, :quote_currency)
                ON CONFLICT (symbol) DO NOTHING
                """,
                item,
            )
            inserted["instruments"] += cursor.rowcount

        ids = {row["symbol"]: row["id"] for row in conn.execute("SELECT id, symbol FROM instruments")}

        for raw in SAMPLE_TRADES:
            data = {key: value for key, value in raw.items() if key != "symbol"}
            data["instrument_id"] = ids[raw["symbol"]]
            trade = validate_trade(data)

            already_there = conn.execute(
                """
                SELECT 1 FROM trades
                WHERE instrument_id = :instrument_id AND trade_date = :trade_date
                  AND direction = :direction AND entry_price = :entry_price
                """,
                trade,
            ).fetchone()
            if already_there:
                continue

            conn.execute(
                """
                INSERT INTO trades
                    (instrument_id, trade_date, closed_at, direction, entry_price, stop_loss,
                     take_profit, exit_price, position_size, fees, status,
                     strategy, session, notes)
                VALUES (:instrument_id, :trade_date, :closed_at, :direction, :entry_price, :stop_loss,
                        :take_profit, :exit_price, :position_size, :fees, :status,
                        :strategy, :session, :notes)
                """,
                trade,
            )
            inserted["trades"] += 1

    return inserted
```

### `tradeledger/web/analytics.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""The /analytics dashboard (Phase 6).

Request flow:
  1. GET /analytics?instrument_id=1&strategy=Breakout&session=LONDON&start=...&end=...
  2. parse_report_filters() validates the query string (the same function
     the JSON API uses). Any problem -> 400 with the errors shown next to
     the filter fields, and NO report, so a bad filter can never produce a
     misleading page.
  3. build_dashboard() runs ONE query and builds ONE report; every KPI card,
     chart and table on the page is cut from it.
  4. The template renders the numbers as HTML (works without JavaScript)
     plus a JSON copy of the chart data for analytics.js.

Filtering happens in SQL on the server. The browser never receives the
trade history, only the aggregated figures it draws.
"""

from urllib.parse import urlsplit

from flask import current_app, g, render_template, request

from ..db import get_db
from ..performance.dashboard import SESSION_LABELS, SMALL_SAMPLE, build_dashboard, chart_payload, money_rows
from ..report_filters import FILTER_NAMES, parse_report_filters
from ..repositories.instruments import list_instruments
from ..repositories.trades import list_strategies
from ..validation import SESSIONS
from . import main_bp

FILTER_LABELS = {
    "instrument_id": "Instrument",
    "strategy": "Strategy",
    "session": "Session",
    "start": "Realised from",
    "end": "Realised to",
}


def chart_script_sources(url):
    """Content-Security-Policy sources needed to load Chart.js from `url`.

    A same-site path (e.g. /static/vendor/chartjs/chart.umd.min.js) is
    already covered by 'self'. An https URL is allowed as that exact file,
    never as the whole host. Anything else is a configuration mistake.
    """
    if url.startswith("/") and not url.startswith("//"):
        return ()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
        raise ValueError("CHART_JS_URL must be a site path or an https URL without a query string")
    return (url,)


@main_bp.route("/analytics")
def analytics():
    conn = get_db()
    filters, errors = parse_report_filters(request.args, conn, blank_means_unset=True)
    raw = {name: request.args.get(name, "").strip() for name in FILTER_NAMES}

    dashboard = payload = None
    if not errors:
        dashboard = build_dashboard(conn, filters)
        payload = chart_payload(dashboard)
        if dashboard.has_results:  # charts (and so Chart.js) only when there is data
            g.csp_script_sources = chart_script_sources(current_app.config["CHART_JS_URL"])

    strategies = list_strategies(conn)
    if raw["strategy"] and raw["strategy"] not in strategies:
        strategies = [raw["strategy"], *strategies]  # keep a bookmarked choice visible

    return render_template(
        "analytics/dashboard.html",
        dashboard=dashboard,
        payload=payload,
        money=money_rows(dashboard.report) if dashboard else [],
        errors=errors,
        raw=raw,
        labels=FILTER_LABELS,
        active=filters.active() if not errors else {},
        instruments=list_instruments(conn),
        strategies=strategies,
        session_options=[(key, SESSION_LABELS[key]) for key in SESSIONS],
        small_sample=SMALL_SAMPLE,
        chart_js={
            "url": current_app.config["CHART_JS_URL"],
            "integrity": current_app.config["CHART_JS_INTEGRITY"],
            "version": current_app.config["CHART_JS_VERSION"],
        },
    ), (400 if errors else 200)
```

### `tradeledger/web/forms.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Covered by `python -m pytest` (see the tests listed above for its behaviour).

```python
"""Helpers that sit between HTML forms and validation.py.

HTML forms send every value as text, and a missing field is simply absent.
These helpers:
  * keep ONLY the fields a form may set (no mass assignment: a crafted
    "id" or "created_at" field is ignored);
  * keep what the user typed, so a failed submission can redisplay it;
  * turn database records back into form values for editing.

They do NOT decide whether a trade is valid. That is validation.py's job,
shared with the JSON API.
"""

from decimal import Decimal
from urllib.parse import urlsplit

from ..performance.formatting import decimal_to_text
from .filters import datetime_local_filter

TRADE_FORM_FIELDS = (
    "instrument_id",
    "direction",
    "status",
    "trade_date",
    "entry_price",
    "stop_loss",
    "take_profit",
    "position_size",
    "fees",
    "exit_price",
    "closed_at",
    "strategy",
    "session",
    "notes",
)

INSTRUMENT_FORM_FIELDS = (
    "symbol",
    "name",
    "asset_class",
    "price_increment",
    "contract_multiplier",
    "quote_currency",
)

# Fields that have no meaning for an OPEN trade. The browser disables them
# (so they are not sent); the server also blanks them if they arrive anyway.
EXIT_FIELDS = ("exit_price", "closed_at")

BLANK_TRADE_FORM = {
    "instrument_id": "",
    "direction": "LONG",
    "status": "CLOSED",
    "trade_date": "",
    "entry_price": "",
    "stop_loss": "",
    "take_profit": "",
    "position_size": "",
    "fees": "0",
    "exit_price": "",
    "closed_at": "",
    "strategy": "",
    "session": "",
    "notes": "",
}


def read_form(form, fields):
    """Text values for the allowed fields only. Absent fields become ""."""
    return {name: form.get(name, "").strip() for name in fields}


def form_errors_for_status(values):
    """The website asks the user to choose a status explicitly (the API
    may infer it). Returns extra errors that validation.py would not raise."""
    if values.get("status") not in ("OPEN", "CLOSED"):
        return {"status": "must be OPEN or CLOSED"}
    return {}


def trade_input(values):
    """Form values -> the dict validate_trade() expects.
    Exit fields of an OPEN trade are ignored rather than rejected: the
    user switched the status to OPEN, so leftover exit values are stale."""
    data = {name: (value if value != "" else None) for name, value in values.items()}
    if values.get("status") == "OPEN":
        for name in EXIT_FIELDS:
            data[name] = None
    return data


def trade_to_form(trade):
    """A stored trade (Decimals, UTC strings) -> text values for the form."""

    def text(value):
        if value is None:
            return ""
        return decimal_to_text(value) if isinstance(value, Decimal) else str(value)

    values = {name: text(trade.get(name)) for name in TRADE_FORM_FIELDS}
    values["trade_date"] = datetime_local_filter(trade["trade_date"])
    values["closed_at"] = datetime_local_filter(trade["closed_at"]) if trade["closed_at"] else ""
    return values


def safe_next(target, fallback):
    """Only allow redirects to a path on this site.

    A `next` value comes from the URL, so an attacker could craft
    ?next=https://evil.example. We accept only relative paths that start
    with a single "/" (not "//", which browsers treat as another host) and
    contain no backslashes or scheme.
    """
    if not target or not isinstance(target, str):
        return fallback
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return fallback
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return fallback
    return target
```

### `tests/conftest.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/conftest.py`

```python
"""Shared pytest fixtures.

pytest loads this file automatically. Any test that names a fixture as a
parameter (app, client, conn, seeded_conn, ...) receives it.

Every database here lives in pytest's tmp_path: a fresh, empty temporary
folder per test. Tests can never touch instance/tradeledger.db.
"""

import pytest

from tradeledger import create_app
from tradeledger.db import connect, init_schema
from tradeledger.repositories.instruments import create_instrument
from tradeledger.seed import seed_sample_data


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def app(db_path):
    """A fresh app using the testing configuration and a temporary database."""
    app = create_app("testing", overrides={"DATABASE": db_path})
    with app.app_context():
        from tradeledger.db import get_db

        init_schema(get_db())
    return app


@pytest.fixture
def client(app):
    """Simulates a browser: sends requests without starting a real server."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Runs Flask CLI commands (init-db, seed-db, ...) inside tests."""
    return app.test_cli_runner()


@pytest.fixture
def conn(db_path):
    """A plain connection to an empty, initialised temporary database."""
    connection = connect(db_path)
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded_conn(conn):
    """The same temporary database, loaded with the synthetic sample data."""
    seed_sample_data(conn)
    return conn


@pytest.fixture
def eurusd_id(conn):
    """Creates one instrument and returns its id."""
    return create_instrument(
        conn,
        {
            "symbol": "EURUSD",
            "name": "Euro / US Dollar (test)",
            "asset_class": "FOREX",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
    )


@pytest.fixture
def long_trade(eurusd_id):
    """Valid input for a closed LONG trade on EURUSD."""
    return {
        "instrument_id": eurusd_id,
        "trade_date": "2026-03-02T08:15:00Z",
        "closed_at": "2026-03-02T11:40:00Z",  # Phase 4: closed trades record an exit time
        "direction": "LONG",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "status": "CLOSED",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Test trade",
    }


# ---------- Phase 4: API fixtures ----------


@pytest.fixture
def seeded_client(app, client):
    """A test client whose temporary database holds the synthetic sample data."""
    from tradeledger.db import get_db

    with app.app_context():
        seed_sample_data(get_db())
    return client


@pytest.fixture
def trade_body():
    """A valid POST /api/v1/trades body for sample instrument 1 (EURUSD).
    Same prices as sample trade 1: net +393 USD, realised R +1.965."""
    return {
        "instrument_id": 1,
        "trade_date": "2026-04-01T08:15:00Z",
        "closed_at": "2026-04-01T11:40:00Z",
        "direction": "LONG",
        "status": "CLOSED",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "API test trade (synthetic)",
    }
```

### `tests/test_api_analytics.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_api_analytics.py`

```python
"""Integration tests for /api/v1/analytics/*.

The expected numbers are the hand-worked sample results from Phase 3 (see
tests/test_report.py). Here we check that the API returns exactly what the
engine calculates, as exact strings, with the documented shapes.
"""

from decimal import Decimal
from fractions import Fraction

import pytest

from tradeledger.db import get_db
from tradeledger.performance import build_report
from tradeledger.performance.formatting import to_jsonable

BASE = "/api/v1/analytics"
ENDPOINTS = ["summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve"]


def contains_float(obj):
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(contains_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(contains_float(v) for v in obj)
    return False


# ---------- Summary ----------


def test_summary_matches_the_engine_exactly(app, seeded_client):
    body = seeded_client.get(f"{BASE}/summary").get_json()
    with app.app_context():
        expected = to_jsonable(build_report(get_db()).overall)
    assert body["data"]["overall"] == expected


def test_summary_hand_worked_values(seeded_client):
    data = seeded_client.get(f"{BASE}/summary").get_json()["data"]
    o = data["overall"]
    assert (o["trades"], o["wins"], o["losses"], o["breakevens"]) == (14, 7, 5, 2)
    assert o["win_rate"] == "0.5"
    expectancy = Fraction(Decimal(o["expectancy_r"]))
    assert abs(expectancy - Fraction(239871, 436800)) < Fraction(1, 10**40)
    assert o["money"]["USD"]["net_pnl"] == "798.5"
    assert o["money"]["GBP"]["profit_factor"] is None  # no GBP losses -> null
    assert data["open_trades"] == 2  # counted, not in results
    assert data["excluded"] == [] and data["r_unavailable"] == []
    assert data["unknown_close_time_trade_ids"] == []  # sample trades have exit times


def test_summary_meta_explains_conventions(seeded_client):
    meta = seeded_client.get(f"{BASE}/summary").get_json()["meta"]
    # Phase 6 added strategy and session (an additive change to meta).
    assert meta["filters"] == {
        "instrument_id": None,
        "strategy": None,
        "session": None,
        "start": None,
        "end": None,
    }
    joined = " ".join(meta["notes"])
    assert "strings" in joined and "never summed across currencies" in joined


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_no_floats_anywhere(seeded_client, endpoint):
    assert not contains_float(seeded_client.get(f"{BASE}/{endpoint}").get_json())


def test_decimal_strings_round_trip_exactly(app, seeded_client):
    total_r = seeded_client.get(f"{BASE}/summary").get_json()["data"]["overall"]["total_r"]
    with app.app_context():
        assert Decimal(total_r) == build_report(get_db()).overall.total_r


# ---------- Groups ----------


def test_by_instrument(seeded_client):
    data = seeded_client.get(f"{BASE}/instruments").get_json()["data"]
    assert [g["group"] for g in data] == ["EURUSD", "GBPUSD", "NAS100", "UK100", "XAUUSD"]
    gold = data[-1]
    assert (gold["trades"], gold["win_rate"]) == (4, "0.75")
    assert list(data[3]["money"]) == ["GBP"]


def test_by_strategy_and_session(seeded_client):
    strategies = seeded_client.get(f"{BASE}/strategies").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in strategies] == [
        ("Breakout", 5),
        ("Pullback", 5),
        ("Reversal", 2),
        ("Unspecified", 2),
    ]
    sessions = seeded_client.get(f"{BASE}/sessions").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in sessions] == [
        ("ASIA", 2),
        ("LONDON", 6),
        ("NEW_YORK", 5),
        ("Unspecified", 1),
    ]


def test_by_weekday_in_calendar_order(seeded_client):
    data = seeded_client.get(f"{BASE}/weekday").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in data] == [
        ("Monday", 3),
        ("Tuesday", 3),
        ("Wednesday", 3),
        ("Thursday", 2),
        ("Friday", 3),
    ]


def test_monthly(seeded_client):
    data = seeded_client.get(f"{BASE}/monthly").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in data] == [("2026-03", 14)]


# ---------- Curve ----------


def test_curve(seeded_client):
    body = seeded_client.get(f"{BASE}/curve").get_json()
    assert "NOT an account equity curve" in body["meta"]["label"]
    points = body["data"]
    assert [p["trade_id"] for p in points] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]
    assert points[0]["realised_r"] == points[0]["cumulative_r"] == "1.965"
    assert all(p["close_time_known"] for p in points)


# ---------- Filters ----------


def test_instrument_filter_applies_to_every_endpoint(seeded_client):
    for endpoint in ENDPOINTS:
        assert seeded_client.get(f"{BASE}/{endpoint}?instrument_id=3").status_code == 200
    assert seeded_client.get(f"{BASE}/summary?instrument_id=3").get_json()["data"]["overall"]["trades"] == 4
    assert [
        g["group"] for g in seeded_client.get(f"{BASE}/instruments?instrument_id=3").get_json()["data"]
    ] == ["XAUUSD"]


def test_date_filter_uses_realised_date(app, seeded_client, trade_body):
    # Entered 31 March, closed 1 April: it belongs to April's results.
    seeded_client.post(
        "/api/v1/trades",
        json={**trade_body, "trade_date": "2026-03-31T22:00:00Z", "closed_at": "2026-04-01T01:00:00Z"},
    )
    april = seeded_client.get(f"{BASE}/summary?start=2026-04-01&end=2026-04-30").get_json()
    assert april["data"]["overall"]["trades"] == 1
    assert april["meta"]["filters"] == {
        "instrument_id": None,
        "strategy": None,
        "session": None,
        "start": "2026-04-01",
        "end": "2026-04-30",
    }
    march = seeded_client.get(f"{BASE}/summary?start=2026-03-01&end=2026-03-31").get_json()
    assert march["data"]["overall"]["trades"] == 14


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize(
    "query, field",
    [
        ("start=2026-02-30", "start"),
        ("end=soon", "end"),
        ("start=2026-03-10&end=2026-03-01", "end"),
        ("instrument_id=999", "instrument_id"),
        ("instrument_id=x", "instrument_id"),
    ],
)
def test_invalid_filters_are_400(seeded_client, endpoint, query, field):
    response = seeded_client.get(f"{BASE}/{endpoint}?{query}")
    assert response.status_code == 400
    assert field in response.get_json()["error"]["details"]


def test_unknown_analytics_parameter_is_400(seeded_client):
    response = seeded_client.get(f"{BASE}/summary?status=OPEN")
    assert response.get_json()["error"]["code"] == "unknown_parameters"


# ---------- Edge cases ----------


def test_empty_database(client):
    o = client.get(f"{BASE}/summary").get_json()["data"]["overall"]
    assert o["trades"] == 0
    assert o["win_rate"] is None and o["expectancy_r"] is None and o["profit_factor_r"] is None
    assert o["total_r"] == "0" and o["money"] == {}
    for endpoint in ENDPOINTS[1:]:
        assert client.get(f"{BASE}/{endpoint}").get_json()["data"] == []


def test_open_trades_do_not_affect_realised_results(seeded_client, trade_body):
    before = seeded_client.get(f"{BASE}/summary").get_json()["data"]
    open_trade = {k: v for k, v in trade_body.items() if k not in ("exit_price", "closed_at", "status")}
    seeded_client.post("/api/v1/trades", json=open_trade)
    after = seeded_client.get(f"{BASE}/summary").get_json()["data"]
    assert after["overall"] == before["overall"]
    assert after["open_trades"] == before["open_trades"] + 1


def test_breakeven_and_mixed_currencies(client):
    for instrument in (
        {
            "symbol": "AAA",
            "name": "Test USD",
            "asset_class": "OTHER",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        {
            "symbol": "BBB",
            "name": "Test GBP",
            "asset_class": "OTHER",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "GBP",
        },
    ):
        assert client.post("/api/v1/instruments", json=instrument).status_code == 201
    common = {
        "trade_date": "2026-05-04T09:00:00Z",
        "closed_at": "2026-05-04T10:00:00Z",
        "direction": "LONG",
        "entry_price": "100",
        "stop_loss": "90",
        "position_size": "1",
    }
    client.post("/api/v1/trades", json={**common, "instrument_id": 1, "exit_price": "120"})  # +20 USD
    client.post("/api/v1/trades", json={**common, "instrument_id": 2, "exit_price": "100"})  # 0 GBP
    o = client.get(f"{BASE}/summary").get_json()["data"]["overall"]
    assert (o["wins"], o["breakevens"]) == (1, 1)
    assert o["money"]["USD"]["net_pnl"] == "20" and o["money"]["GBP"]["net_pnl"] == "0"
    assert "net_pnl" not in o  # no cross-currency total exists
    assert o["total_r"] == "2"  # R can be combined: +2R + 0R


def test_analytics_are_read_only(app, seeded_client):
    from tests.factories import table_snapshot

    before = table_snapshot(app, "trades")
    for endpoint in ENDPOINTS:
        seeded_client.get(f"{BASE}/{endpoint}")
    assert table_snapshot(app, "trades") == before
    assert seeded_client.post(f"{BASE}/summary", json={}).status_code == 405
```

### `tests/test_api_instruments.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_api_instruments.py`

```python
"""Integration tests for /api/v1/instruments."""

import pytest

from tests.factories import table_snapshot

URL = "/api/v1/instruments"

US30 = {
    "symbol": "US30",
    "name": "Wall Street 30 CFD (example values)",
    "asset_class": "INDEX",
    "price_increment": "1",
    "contract_multiplier": "1",
    "quote_currency": "USD",
}


def test_list_instruments_sorted_by_symbol(seeded_client):
    data = seeded_client.get(URL).get_json()["data"]
    assert [i["symbol"] for i in data] == ["EURUSD", "GBPUSD", "NAS100", "UK100", "XAUUSD"]
    eurusd = data[0]
    assert set(eurusd) == {
        "id",
        "symbol",
        "name",
        "asset_class",
        "price_increment",
        "contract_multiplier",
        "quote_currency",
        "created_at",
    }
    assert (eurusd["price_increment"], eurusd["contract_multiplier"]) == ("0.00001", "100000")


def test_list_on_empty_database(client):
    assert client.get(URL).get_json() == {"data": []}


def test_list_rejects_query_parameters(seeded_client):
    assert seeded_client.get(f"{URL}?page=2").status_code == 400


def test_get_instrument(seeded_client):
    data = seeded_client.get(f"{URL}/3").get_json()["data"]
    assert (data["symbol"], data["asset_class"], data["quote_currency"]) == ("XAUUSD", "COMMODITY", "USD")


@pytest.mark.parametrize("path", ["999", "0", "abc"])
def test_missing_instrument_is_404(seeded_client, path):
    response = seeded_client.get(f"{URL}/{path}")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


def test_create_instrument(seeded_client):
    response = seeded_client.post(URL, json={**US30, "symbol": "us30", "asset_class": "index"})
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert response.headers["Location"] == f"{URL}/{data['id']}"
    assert (data["symbol"], data["asset_class"]) == ("US30", "INDEX")
    assert seeded_client.get(f"{URL}/{data['id']}").get_json()["data"] == data


@pytest.mark.parametrize("symbol", ["XAUUSD", "xauusd", " XAUUSD "])
def test_duplicate_symbol_is_409_and_writes_nothing(app, seeded_client, symbol):
    before = table_snapshot(app, "instruments")
    response = seeded_client.post(URL, json={**US30, "symbol": symbol})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "conflict"
    assert table_snapshot(app, "instruments") == before


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"symbol": ""}, "symbol"),
        ({"symbol": "US 30"}, "symbol"),
        ({"asset_class": "CRYPTO"}, "asset_class"),
        ({"contract_multiplier": "0"}, "contract_multiplier"),
        ({"price_increment": "-0.1"}, "price_increment"),
        ({"quote_currency": "DOLLARS"}, "quote_currency"),
        ({"name": "x" * 101}, "name"),
    ],
)
def test_invalid_instrument_is_400(app, seeded_client, changes, field):
    before = table_snapshot(app, "instruments")
    response = seeded_client.post(URL, json={**US30, **changes})
    assert response.status_code == 400
    assert field in response.get_json()["error"]["details"]
    assert table_snapshot(app, "instruments") == before


def test_missing_instrument_fields_are_all_reported(seeded_client):
    details = seeded_client.post(URL, json={}).get_json()["error"]["details"]
    assert set(details) == {
        "symbol",
        "name",
        "asset_class",
        "price_increment",
        "contract_multiplier",
        "quote_currency",
    }


def test_unknown_instrument_field_is_rejected(seeded_client):
    response = seeded_client.post(URL, json={**US30, "id": 99})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "unknown_fields"


def test_create_instrument_requires_json(seeded_client):
    assert seeded_client.post(URL, data="symbol=US30").status_code == 415


@pytest.mark.parametrize("method", ["put", "delete", "patch"])
def test_instruments_cannot_be_changed_or_deleted_yet(seeded_client, method):
    assert getattr(seeded_client, method)(f"{URL}/1", json=US30).status_code == 405
```

### `tests/test_api_security.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_api_security.py`

```python
"""Cross-cutting API behaviour: production safety, error hygiene, limits,
logging and coexistence with the website."""

import json
import logging

import pytest

from tradeledger import create_app


def production_app(db_path, **overrides):
    return create_app(
        "production",
        overrides={"SECRET_KEY": "a-real-secret-from-the-environment", "DATABASE": db_path, **overrides},
    )


# ---------- Production safety ----------


def test_api_is_disabled_in_production_by_default(db_path):
    client = production_app(db_path).test_client()
    for method, url in (
        ("get", "/api/v1/trades"),
        ("post", "/api/v1/trades"),
        ("get", "/api/v1/analytics/summary"),
        ("delete", "/api/v1/trades/1"),
    ):
        response = getattr(client, method)(url, json={})
        assert response.status_code == 403
        assert response.get_json()["error"]["code"] == "api_disabled"


def test_docs_and_website_still_work_when_api_disabled(db_path):
    client = production_app(db_path).test_client()
    assert client.get("/api/v1/openapi.json").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/health").get_json()["status"] == "ok"


def test_production_api_can_be_enabled_explicitly_with_a_warning(db_path, caplog):
    with caplog.at_level(logging.WARNING):
        app = production_app(db_path, API_ENABLED=True)
    assert "unauthenticated JSON API is ENABLED in production" in caplog.text
    from tradeledger.db import get_db, init_schema

    with app.app_context():
        init_schema(get_db())
    assert app.test_client().get("/api/v1/trades").status_code == 200


@pytest.mark.parametrize("value, expected", [("1", True), ("0", False), ("true", False), (None, False)])
def test_opt_in_flag_must_be_exactly_1(monkeypatch, value, expected):
    from tradeledger.config import env_flag

    if value is None:
        monkeypatch.delenv("TRADELEDGER_ENABLE_UNAUTHENTICATED_API", raising=False)
    else:
        monkeypatch.setenv("TRADELEDGER_ENABLE_UNAUTHENTICATED_API", value)
    assert env_flag("TRADELEDGER_ENABLE_UNAUTHENTICATED_API") is expected


# ---------- Error hygiene ----------


@pytest.fixture
def exploding_app(db_path):
    """An app with an extra API route that raises, to test the 500 handler."""
    app = production_app(db_path, API_ENABLED=True, PROPAGATE_EXCEPTIONS=False)

    def boom():
        raise RuntimeError(f"secret detail: SELECT * FROM trades; key={app.config['SECRET_KEY']}")

    app.add_url_rule("/api/v1/_boom", "api_boom", boom)
    app.add_url_rule("/_boom", "site_boom", boom)
    return app


def test_unexpected_api_error_is_generic_json(exploding_app, caplog):
    with caplog.at_level(logging.ERROR):
        response = exploding_app.test_client().get("/api/v1/_boom")
    assert response.status_code == 500
    assert response.get_json() == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred."}
    }
    text = response.get_data(as_text=True)
    for leak in ("secret detail", "SELECT", "Traceback", "a-real-secret", ".py"):
        assert leak not in text
    assert "secret detail" in caplog.text  # the detail goes to the server log only


def test_unexpected_website_error_is_the_html_page(exploding_app):
    response = exploding_app.test_client().get("/_boom")
    assert response.status_code == 500
    assert b"Something went wrong" in response.data
    assert b"secret detail" not in response.data


def test_validation_errors_do_not_echo_sql_or_paths(seeded_client):
    response = seeded_client.post("/api/v1/trades", json={"instrument_id": 999, "trade_date": "x"})
    text = response.get_data(as_text=True)
    for leak in ("SELECT", "INSERT", "sqlite", "FOREIGN KEY", "/home/", "Traceback"):
        assert leak not in text


# ---------- Limits ----------


def test_request_body_over_64kb_is_413(seeded_client, trade_body):
    huge = json.dumps({**trade_body, "notes": "x" * (70 * 1024)})
    response = seeded_client.post("/api/v1/trades", data=huge, content_type="application/json")
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "payload_too_large"


def test_notes_over_2000_characters_is_a_validation_error(seeded_client, trade_body):
    response = seeded_client.post("/api/v1/trades", json={**trade_body, "notes": "x" * 2001})
    assert response.status_code == 400
    assert "notes" in response.get_json()["error"]["details"]


def test_per_page_is_capped(seeded_client):
    assert seeded_client.get("/api/v1/trades?per_page=100").status_code == 200
    assert seeded_client.get("/api/v1/trades?per_page=1000").status_code == 400


# ---------- Headers and logging ----------


def test_api_responses_are_not_cached_and_keep_security_headers(seeded_client):
    response = seeded_client.get("/api/v1/trades")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.mimetype == "application/json"


def test_logs_never_contain_request_bodies(seeded_client, trade_body, caplog):
    with caplog.at_level(logging.INFO):
        seeded_client.post(
            "/api/v1/trades", json={**trade_body, "notes": "PRIVATE-NOTE-123", "position_size": "0"}
        )
    assert "POST /api/v1/trades -> 400 validation_error" in caplog.text
    assert "PRIVATE-NOTE-123" not in caplog.text


# ---------- Website unaffected ----------


def test_website_404_is_still_html(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404 and b"Page not found" in response.data


def test_api_404_is_json(client):
    response = client.get("/api/v1/no-such-endpoint")
    assert response.status_code == 404 and response.get_json()["error"]["code"] == "not_found"


def test_unversioned_api_path_is_404_json(client):
    assert client.get("/api/trades").get_json()["error"]["code"] == "not_found"
```

### `tests/test_api_trades.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_api_trades.py`

```python
"""Integration tests for /api/v1/trades through Flask's test client.

Each test gets its own temporary database (see conftest.py). seeded_client
holds the 16 synthetic sample trades: 14 CLOSED, 2 OPEN, on 5 instruments
(ids 1-5: EURUSD, GBPUSD, XAUUSD, NAS100, UK100).
"""

import json

import pytest

from tests.factories import table_snapshot

URL = "/api/v1/trades"


def post(client, body, url=URL):
    return client.post(url, json=body)


def put(client, trade_id, body):
    return client.put(f"{URL}/{trade_id}", json=body)


def full(body):
    """A complete PUT body: every accepted field present."""
    return {
        "take_profit": None,
        "fees": None,
        "strategy": None,
        "session": None,
        "notes": None,
        "closed_at": None,
        "status": None,
        **body,
    }


# ---------- Create ----------


def test_create_trade_returns_201_location_and_exact_values(seeded_client, trade_body):
    response = post(seeded_client, trade_body)
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert response.headers["Location"] == f"{URL}/{data['id']}"
    assert data["symbol"] == "EURUSD"
    assert data["entry_price"] == "1.085"  # canonical exact string
    assert data["closed_at"] == "2026-04-01T11:40:00Z"
    assert data["result"] == {
        "outcome": "WIN",
        "gross_pnl": "400",
        "fees": "7",
        "net_pnl": "393",
        "initial_risk": "200",
        "realised_r": "1.965",
        "planned_rr": "2",
        "r_unavailable_reason": None,
    }
    assert seeded_client.get(f"{URL}/{data['id']}").status_code == 200


def test_json_numbers_are_read_exactly_not_as_floats(seeded_client, trade_body):
    body = dict(trade_body, entry_price=1.08500, stop_loss=1.083, exit_price=1.089, fees=7)
    # Send the raw text so the test controls exactly what the JSON says.
    text = json.dumps(body).replace("1.085", "1.08500")
    response = seeded_client.post(URL, data=text, content_type="application/json")
    assert response.status_code == 201
    data = response.get_json()["data"]
    assert (data["entry_price"], data["result"]["realised_r"]) == ("1.085", "1.965")


def test_create_open_trade_has_null_result(seeded_client, trade_body):
    body = {k: v for k, v in trade_body.items() if k not in ("exit_price", "closed_at", "status")}
    data = post(seeded_client, body).get_json()["data"]
    assert data["status"] == "OPEN"
    assert data["exit_price"] is None and data["closed_at"] is None
    assert data["result"] is None


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"position_size": "0"}, "position_size"),
        ({"position_size": "-1"}, "position_size"),
        ({"entry_price": "abc"}, "entry_price"),
        ({"direction": "SIDEWAYS"}, "direction"),
        ({"status": "MAYBE"}, "status"),
        ({"stop_loss": "1.09000"}, "stop_loss"),  # long stop above entry
        ({"exit_price": None}, "exit_price"),  # CLOSED without exit
        ({"closed_at": None}, "closed_at"),  # CLOSED without exit time
        ({"closed_at": "2026-04-01T08:00:00Z"}, "closed_at"),  # exit before entry
        ({"fees": "-1"}, "fees"),
        ({"trade_date": "01/04/2026"}, "trade_date"),
        ({"instrument_id": 999}, "instrument_id"),  # does not exist
        ({"instrument_id": "one"}, "instrument_id"),
        ({"session": "SYDNEY"}, "session"),
    ],
)
def test_invalid_trade_is_rejected_and_nothing_is_written(app, seeded_client, trade_body, changes, field):
    before = table_snapshot(app, "trades")
    response = post(seeded_client, {**trade_body, **changes})
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["code"] == "validation_error"
    assert field in error["details"]
    assert table_snapshot(app, "trades") == before


def test_all_problems_are_reported_together(seeded_client, trade_body):
    response = post(seeded_client, {**trade_body, "position_size": "0", "fees": "-1"})
    assert set(response.get_json()["error"]["details"]) == {"position_size", "fees"}


@pytest.mark.parametrize("field", ["id", "created_at", "updated_at", "symbol", "quote_currency"])
def test_mass_assignment_of_protected_fields_is_rejected(app, seeded_client, trade_body, field):
    before = table_snapshot(app, "trades")
    response = post(seeded_client, {**trade_body, field: "1"})
    assert response.status_code == 400
    assert response.get_json()["error"] == {
        "code": "unknown_fields",
        "message": "The request contains fields that cannot be set.",
        "details": {field: "is not an accepted field"},
    }
    assert table_snapshot(app, "trades") == before


@pytest.mark.parametrize(
    "text, code",
    [
        ("{not json", "malformed_json"),
        ("[1, 2, 3]", "malformed_json"),
        ('"just a string"', "malformed_json"),
        ('{"entry_price": NaN}', "malformed_json"),
        ('{"entry_price": Infinity}', "malformed_json"),
        ("", "malformed_json"),
    ],
)
def test_malformed_json_is_rejected(seeded_client, text, code):
    response = seeded_client.post(URL, data=text, content_type="application/json")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == code


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", None])
def test_non_json_content_type_is_415(seeded_client, trade_body, content_type):
    kwargs = {"data": json.dumps(trade_body)}
    if content_type:
        kwargs["content_type"] = content_type
    response = seeded_client.post(URL, **kwargs)
    assert response.status_code == 415
    assert response.get_json()["error"]["code"] == "unsupported_media_type"


def test_json_content_type_with_charset_is_accepted(seeded_client, trade_body):
    response = seeded_client.post(
        URL, data=json.dumps(trade_body), content_type="application/json; charset=utf-8"
    )
    assert response.status_code == 201


# ---------- List, filter, paginate ----------


def test_list_default_page(seeded_client):
    body = seeded_client.get(URL).get_json()
    assert body["pagination"] == {"page": 1, "per_page": 20, "total": 16, "total_pages": 1}
    dates = [t["trade_date"] for t in body["data"]]
    assert len(dates) == 16 and dates == sorted(dates, reverse=True)


def test_pagination_pages_do_not_overlap(seeded_client):
    seen = []
    for page in (1, 2, 3, 4):
        body = seeded_client.get(f"{URL}?per_page=5&page={page}").get_json()
        assert body["pagination"] == {"page": page, "per_page": 5, "total": 16, "total_pages": 4}
        seen += [t["id"] for t in body["data"]]
    assert len(seen) == 16 and len(set(seen)) == 16
    assert seen == [t["id"] for t in seeded_client.get(URL).get_json()["data"]]


def test_page_past_the_end_is_empty_not_an_error(seeded_client):
    body = seeded_client.get(f"{URL}?page=99").get_json()
    assert body["data"] == [] and body["pagination"]["total"] == 16


@pytest.mark.parametrize(
    "query, field",
    [
        ("page=0", "page"),
        ("page=abc", "page"),
        ("page=-1", "page"),
        ("page=1.5", "page"),
        ("per_page=0", "per_page"),
        ("per_page=101", "per_page"),
        ("status=PENDING", "status"),
        ("start=2026-13-01", "start"),
        ("end=yesterday", "end"),
        ("start=2026-03-10&end=2026-03-01", "end"),
        ("instrument_id=999", "instrument_id"),
        ("instrument_id=abc", "instrument_id"),
    ],
)
def test_invalid_query_parameters_are_400(seeded_client, query, field):
    response = seeded_client.get(f"{URL}?{query}")
    assert response.status_code == 400
    assert field in response.get_json()["error"]["details"]


def test_unknown_query_parameter_is_400(seeded_client):
    response = seeded_client.get(f"{URL}?instrument=XAUUSD")
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "unknown_parameters"


def test_filter_by_instrument(seeded_client):
    body = seeded_client.get(f"{URL}?instrument_id=3").get_json()
    assert body["pagination"]["total"] == 4
    assert {t["symbol"] for t in body["data"]} == {"XAUUSD"}


def test_filter_by_status_is_case_insensitive(seeded_client):
    assert seeded_client.get(f"{URL}?status=open").get_json()["pagination"]["total"] == 2
    assert seeded_client.get(f"{URL}?status=CLOSED").get_json()["pagination"]["total"] == 14


def test_filter_by_entry_date_range(seeded_client):
    body = seeded_client.get(f"{URL}?start=2026-03-02&end=2026-03-04").get_json()
    assert sorted(t["trade_date"] for t in body["data"]) == [
        "2026-03-02T08:15:00Z",
        "2026-03-03T13:40:00Z",
        "2026-03-04T08:30:00Z",
    ]


def test_filters_combine_with_pagination(seeded_client):
    body = seeded_client.get(f"{URL}?instrument_id=3&status=CLOSED&per_page=2&page=2").get_json()
    assert body["pagination"] == {"page": 2, "per_page": 2, "total": 4, "total_pages": 2}
    assert len(body["data"]) == 2


@pytest.mark.parametrize(
    "query", ["status=CLOSED' OR '1'='1", "start=2026-01-01' --", "instrument_id=1 OR 1=1"]
)
def test_sql_injection_in_query_string_is_rejected_as_invalid(app, seeded_client, query):
    before = table_snapshot(app, "trades")
    assert seeded_client.get(f"{URL}?{query}").status_code == 400
    assert table_snapshot(app, "trades") == before


def test_empty_database_list(client):
    body = client.get(URL).get_json()
    assert body == {"data": [], "pagination": {"page": 1, "per_page": 20, "total": 0, "total_pages": 0}}


# ---------- Retrieve ----------


def test_get_trade(seeded_client):
    data = seeded_client.get(f"{URL}/9").get_json()["data"]
    assert data["id"] == 9 and data["symbol"] == "EURUSD"
    assert data["result"]["outcome"] == "LOSS"  # fees made a price gain a loss
    assert data["result"]["net_pnl"] == "-5"


@pytest.mark.parametrize("path", ["999", "0", "-1", "abc", "1.5"])
def test_missing_or_invalid_ids_are_404_json(seeded_client, path):
    response = seeded_client.get(f"{URL}/{path}")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


# ---------- Replace (PUT) ----------


def test_put_replaces_the_trade(seeded_client, trade_body):
    trade_id = post(seeded_client, trade_body).get_json()["data"]["id"]
    replacement = full({**trade_body, "exit_price": "1.08700", "notes": "Closed early"})
    response = put(seeded_client, trade_id, replacement)
    assert response.status_code == 200
    data = response.get_json()["data"]
    assert data["exit_price"] == "1.087" and data["notes"] == "Closed early"
    assert data["result"]["net_pnl"] == "193"
    assert seeded_client.get(f"{URL}/{trade_id}").get_json()["data"] == data


def test_put_with_missing_fields_is_400_and_changes_nothing(app, seeded_client, trade_body):
    before = table_snapshot(app, "trades")
    partial = {"notes": "only this"}
    response = put(seeded_client, 1, partial)
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert error["code"] == "missing_fields"
    assert "instrument_id" in error["details"] and "notes" not in error["details"]
    assert table_snapshot(app, "trades") == before


def test_put_invalid_values_is_400_and_changes_nothing(app, seeded_client, trade_body):
    before = table_snapshot(app, "trades")
    response = put(seeded_client, 1, full({**trade_body, "position_size": "-5"}))
    assert response.status_code == 400
    assert table_snapshot(app, "trades") == before


def test_put_missing_trade_is_404(seeded_client, trade_body):
    assert put(seeded_client, 999, full(trade_body)).status_code == 404


def test_put_can_reopen_a_trade(seeded_client, trade_body):
    trade_id = post(seeded_client, trade_body).get_json()["data"]["id"]
    reopened = full({**trade_body, "status": "OPEN", "exit_price": None, "closed_at": None})
    data = put(seeded_client, trade_id, reopened).get_json()["data"]
    assert data["status"] == "OPEN" and data["result"] is None


def test_put_requires_json(seeded_client):
    response = seeded_client.put(f"{URL}/1", data="notes=x", content_type="application/x-www-form-urlencoded")
    assert response.status_code == 415


# ---------- Delete ----------


def test_delete_returns_204_with_no_body(seeded_client):
    response = seeded_client.delete(f"{URL}/16")
    assert response.status_code == 204
    assert response.data == b""
    assert seeded_client.get(f"{URL}/16").status_code == 404
    assert seeded_client.delete(f"{URL}/16").status_code == 404
    assert seeded_client.get(URL).get_json()["pagination"]["total"] == 15


def test_delete_missing_trade_is_404(seeded_client):
    response = seeded_client.delete(f"{URL}/999")
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == "not_found"


# ---------- Methods ----------


@pytest.mark.parametrize("method, url", [("patch", f"{URL}/1"), ("delete", URL), ("put", URL)])
def test_unsupported_methods_are_405_json(seeded_client, method, url):
    response = getattr(seeded_client, method)(url, json={})
    assert response.status_code == 405
    assert response.get_json()["error"]["code"] == "method_not_allowed"
    assert "GET" in response.headers["Allow"]
```

### `tests/test_chronology.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_chronology.py`

```python
"""Realised chronology: results are ordered and grouped by exit time.

Each scenario is small and worked out by hand. r_trade() trades risk 10
with no fees, so exit_price 120 = +2R and exit_price 90 = -1R.
"""

from decimal import Decimal

import pytest

from tests.factories import make_trade, r_trade
from tradeledger.performance import analyse
from tradeledger.performance.trade_math import IncompleteTradeError, evaluate_trade
from tradeledger.repositories.trades import create_trade, list_trades
from tradeledger.validation import ValidationError, validate_trade

D = Decimal


def test_trade_closed_later_is_realised_later():
    # A is entered first but closes last.
    a = r_trade(2, trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-09T16:00:00Z")
    b = r_trade(-1, trade_date="2026-01-06T09:00:00Z", closed_at="2026-01-06T12:00:00Z")
    c = r_trade(-1, trade_date="2026-01-07T09:00:00Z", closed_at="2026-01-07T12:00:00Z")
    report = analyse([a, b, c])
    # Realised (close) order is B, C, A, so the curve is -1, -2, 0.
    # In entry order it would have been +2, +1, 0.
    assert [p.trade_id for p in report.cumulative_r] == [b["id"], c["id"], a["id"]]
    assert [p.cumulative_r for p in report.cumulative_r] == [D("-1"), D("-2"), D("0")]


def test_streak_depends_on_close_order_not_entry_order():
    # Entry order: W, L, W, L  (longest win streak 1).
    # Close order: W, W, L, L  (longest win streak 2).
    trades = [
        r_trade(1, trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-05T10:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T09:30:00Z", closed_at="2026-01-05T15:00:00Z"),
        r_trade(1, trade_date="2026-01-05T09:45:00Z", closed_at="2026-01-05T11:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T09:50:00Z", closed_at="2026-01-05T16:00:00Z"),
    ]
    s = analyse(trades).overall
    assert (s.longest_win_streak, s.longest_loss_streak) == (2, 2)


def test_drawdown_follows_close_order():
    # Close order: +2, -1, -1, +1 -> curve 2, 1, 0, 1 -> max drawdown 2.
    trades = [
        r_trade(1, trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-08T09:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T10:00:00Z", closed_at="2026-01-07T09:00:00Z"),
        r_trade(2, trade_date="2026-01-05T11:00:00Z", closed_at="2026-01-05T12:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T12:00:00Z", closed_at="2026-01-06T09:00:00Z"),
    ]
    assert analyse(trades).overall.max_drawdown_r.amount == D("2")


def test_month_and_weekday_use_the_close_date():
    # Entered Saturday 31 January, closed Monday 2 February.
    t = r_trade(1, trade_date="2026-01-31T22:00:00Z", closed_at="2026-02-02T08:00:00Z")
    report = analyse([t])
    assert list(report.by_month) == ["2026-02"]
    assert list(report.by_weekday) == ["Monday"]
    assert list(report.by_date) == ["2026-02-02"]


def test_legacy_trade_falls_back_to_entry_time_and_is_flagged():
    legacy = r_trade(1, trade_date="2026-01-05T09:00:00Z")  # no closed_at
    known = r_trade(1, trade_date="2026-01-04T09:00:00Z", closed_at="2026-01-06T09:00:00Z")
    report = analyse([known, legacy])
    assert [p.trade_id for p in report.cumulative_r] == [legacy["id"], known["id"]]
    assert report.unknown_close_time_trade_ids == [legacy["id"]]
    point = report.cumulative_r[0]
    assert point.realised_at == point.trade_date and point.close_time_known is False


def test_equal_realised_times_order_by_id():
    first = make_trade(id=5001, closed_at="2026-01-05T12:00:00Z", exit_price=D("90"))
    second = make_trade(id=5002, closed_at="2026-01-05T12:00:00Z")
    report = analyse([second, first])
    assert [p.trade_id for p in report.cumulative_r] == [5001, 5002]


def test_trade_result_fields():
    r = evaluate_trade(make_trade(trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-05T10:00:00Z"))
    assert (r.closed_at, r.realised_at, r.close_time_known) == (
        "2026-01-05T10:00:00Z",
        "2026-01-05T10:00:00Z",
        True,
    )


@pytest.mark.parametrize(
    "closed_at, message",
    [
        ("2026-01-05T08:00:00Z", "earlier than trade_date"),
        ("05/01/2026", "closed_at must look like"),
    ],
)
def test_engine_rejects_inconsistent_close_times(closed_at, message):
    with pytest.raises(IncompleteTradeError, match=message):
        evaluate_trade(make_trade(trade_date="2026-01-05T09:00:00Z", closed_at=closed_at))


# ---------- Validation of closed_at ----------

BASE = {
    "instrument_id": 1,
    "trade_date": "2026-01-05T09:00:00Z",
    "direction": "LONG",
    "entry_price": "100",
    "stop_loss": "90",
    "position_size": "1",
}


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"exit_price": "120"}, "is required for a CLOSED trade"),
        ({"closed_at": "2026-01-05T10:00:00Z"}, "must be empty for an OPEN trade"),
        ({"exit_price": "120", "closed_at": "2026-01-05T08:59:59Z"}, "must not be earlier than trade_date"),
        ({"exit_price": "120", "closed_at": "not-a-real-timestamp"}, "ISO 8601"),
    ],
)
def test_closed_at_validation_errors(changes, message):
    with pytest.raises(ValidationError) as info:
        validate_trade({**BASE, **changes})
    assert message in info.value.errors["closed_at"]


def test_closed_at_is_normalised_to_utc():
    clean = validate_trade({**BASE, "exit_price": "120", "closed_at": "2026-01-05T11:00:00+01:00"})
    assert clean["closed_at"] == "2026-01-05T10:00:00Z"


def test_closed_at_equal_to_entry_is_allowed():
    assert (
        validate_trade({**BASE, "exit_price": "120", "closed_at": BASE["trade_date"]})["status"] == "CLOSED"
    )


def test_require_closed_at_false_allows_unknown_close():
    assert validate_trade({**BASE, "exit_price": "120"}, require_closed_at=False)["closed_at"] is None


# ---------- Repository date filters ----------


def test_realised_date_filter_uses_close_date(conn, eurusd_id):
    create_trade(
        conn,
        {
            "instrument_id": eurusd_id,
            "trade_date": "2026-03-31T20:00:00Z",
            "closed_at": "2026-04-01T09:00:00Z",
            "direction": "LONG",
            "entry_price": "1.1",
            "stop_loss": "1.09",
            "exit_price": "1.12",
            "position_size": "1",
        },
    )
    assert len(list_trades(conn, start="2026-03-31", end="2026-03-31")) == 1  # entry
    assert len(list_trades(conn, start="2026-03-31", end="2026-03-31", date_field="realised")) == 0
    assert len(list_trades(conn, start="2026-04-01", end="2026-04-01", date_field="realised")) == 1


def test_unknown_date_field_is_a_programming_error(conn):
    with pytest.raises(ValueError):
        list_trades(conn, date_field="whenever")
```

### `tests/test_javascript.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_javascript.py`

```python
"""Runs the JavaScript unit tests (tests/js/*.test.js) with Node.js.

Node's built-in test runner needs no npm packages. If Node.js is not
installed, this test is skipped and says so; the Python suite is unaffected.
Run them directly with:   node --test tests/js/*.test.js
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = Path(__file__).parent / "js"


def find_node():
    return (
        os.environ.get("NODE")
        or shutil.which("node")
        or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)
    )


def test_javascript_unit_tests_pass():
    node = find_node()
    if node is None:
        pytest.skip("Node.js is not installed")
    files = sorted(str(path) for path in JS_TESTS.glob("*.test.js"))
    assert files, "no JavaScript test files found"
    result = subprocess.run([node, "--test", *files], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# fail 0" in result.stdout
```

### `tests/test_metrics.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_metrics.py`

```python
"""Unit tests for aggregate metrics (performance/metrics.py).

Most tests build trades with r_trade(), whose realised R is known exactly,
so every expected value below can be checked with pen and paper.
"""

from decimal import Decimal

from tests.factories import dated, make_trade, r_trade
from tradeledger.performance.metrics import (
    Drawdown,
    cumulative,
    expectancy,
    longest_streaks,
    max_drawdown,
    mean,
    profit_factor,
    ratio,
    summarise,
)
from tradeledger.performance.trade_math import evaluate_trade

D = Decimal


def results(*trades):
    return [evaluate_trade(t) for t in trades]


# ---------- Building blocks ----------


def test_ratio_and_division_by_zero():
    assert ratio(1, 4) == D("0.25")
    assert ratio(3, 0) is None
    assert ratio(0, 5) == D("0")


def test_mean():
    assert mean([D("1"), D("2"), D("4.5")]) == D("2.5")
    assert mean([]) is None


def test_expectancy_formula():
    # 40% wins of +2R, 60% losses of -1R -> 0.8 - 0.6 = +0.2R
    assert expectancy(D("0.4"), D("2"), D("0.6"), D("-1")) == D("0.2")


def test_expectancy_with_no_losses_or_no_wins():
    assert expectancy(D("1"), D("1.5"), D("0"), None) == D("1.5")
    assert expectancy(D("0"), None, D("1"), D("-1")) == D("-1")
    assert expectancy(None, None, None, None) is None


def test_profit_factor():
    assert profit_factor([D("3"), D("-1"), D("1"), D("-1")]) == D("2")
    assert profit_factor([D("2"), D("1")]) is None  # no losses: undefined
    assert profit_factor([D("-2")]) == D("0")
    assert profit_factor([]) is None
    assert profit_factor([D("0"), D("0")]) is None


def test_longest_streaks():
    seq = ["WIN", "WIN", "LOSS", "WIN", "WIN", "WIN", "LOSS", "LOSS"]
    assert longest_streaks(seq) == (3, 2)


def test_breakeven_breaks_a_streak():
    assert longest_streaks(["WIN", "WIN", "BREAKEVEN", "WIN"]) == (2, 0)
    assert longest_streaks(["LOSS", "BREAKEVEN", "LOSS"]) == (0, 1)


def test_streaks_empty():
    assert longest_streaks([]) == (0, 0)


def test_cumulative():
    assert cumulative([D("2"), D("-1"), D("0.5")]) == [D("2"), D("1"), D("1.5")]
    assert cumulative([]) == []


def test_max_drawdown_worked_example():
    # Curve: 2, 1, 3, 0.5, 1.5  -> peak 3 (trade 3), trough 0.5 (trade 4): 2.5
    points = [(1, D("2")), (2, D("-1")), (3, D("2")), (4, D("-2.5")), (5, D("1"))]
    assert max_drawdown(points) == Drawdown(D("2.5"), 3, 4)


def test_max_drawdown_counts_from_zero_before_first_trade():
    # Losing first: 0 -> -1 -> -3 means a drawdown of 3 from the start.
    assert max_drawdown([(1, D("-1")), (2, D("-2"))]) == Drawdown(D("3"), None, 2)


def test_max_drawdown_all_winners_is_zero():
    assert max_drawdown([(1, D("1")), (2, D("2"))]) == Drawdown(D("0"), None, None)
    assert max_drawdown([]) == Drawdown(D("0"), None, None)


def test_max_drawdown_picks_largest_not_latest():
    # Falls of 4 then 1: answer is 4.
    points = [(1, D("5")), (2, D("-4")), (3, D("10")), (4, D("-1"))]
    assert max_drawdown(points).amount == D("4")


# ---------- summarise ----------


def test_summary_of_no_trades():
    s = summarise([])
    assert s.trades == 0
    assert s.win_rate is None and s.loss_rate is None
    assert s.average_r is None and s.expectancy_r is None
    assert s.profit_factor_r is None
    assert s.total_r == D("0")
    assert s.max_drawdown_r.amount == D("0")
    assert s.best_trade_r is None
    assert s.money == {}


def test_summary_mixed_hand_worked():
    # Four trades: +2R, -1R, +1R, 0R (breakeven), in date order.
    s = summarise(
        results(
            r_trade(2, trade_date="2026-01-05T10:00:00Z"),
            r_trade(-1, trade_date="2026-01-06T10:00:00Z"),
            r_trade(1, trade_date="2026-01-07T10:00:00Z"),
            r_trade(0, trade_date="2026-01-08T10:00:00Z"),
        )
    )
    assert (s.trades, s.wins, s.losses, s.breakevens) == (4, 2, 1, 1)
    assert s.win_rate == D("0.5")
    assert s.loss_rate == D("0.25")
    assert s.breakeven_rate == D("0.25")
    assert s.total_r == D("2")
    assert s.average_r == D("0.5")
    assert s.average_win_r == D("1.5")
    assert s.average_loss_r == D("-1")
    # 0.5 x 1.5 + 0.25 x -1 = 0.75 - 0.25 = 0.5  (equals the average R)
    assert s.expectancy_r == D("0.5")
    assert s.profit_factor_r == D("3")  # (2 + 1) / 1
    assert s.longest_win_streak == 1
    assert s.longest_loss_streak == 1
    assert s.max_drawdown_r.amount == D("1")  # 2 -> 1
    assert s.best_trade_r.value == D("2")
    assert s.worst_trade_r.value == D("-1")


def test_summary_all_winners():
    s = summarise(results(r_trade(1), r_trade(2), r_trade(3)))
    assert s.win_rate == D("1")
    assert s.loss_rate == D("0")
    assert s.average_loss_r is None
    assert s.profit_factor_r is None  # undefined without losses
    assert s.expectancy_r == D("2")
    assert s.max_drawdown_r.amount == D("0")
    assert s.longest_win_streak == 3


def test_summary_all_losers():
    s = summarise(results(r_trade(-1), r_trade(-0.5)))
    assert s.win_rate == D("0")
    assert s.average_win_r is None
    assert s.expectancy_r == D("-0.75")
    assert s.profit_factor_r == D("0")
    assert s.max_drawdown_r.amount == D("1.5")
    assert s.longest_loss_streak == 2


def test_summary_sorts_by_date_before_streaks_and_drawdown():
    # Given out of order; in date order they are W, W, L, L.
    trades = [dated(8, exit_price=D("90")), dated(5), dated(7, exit_price=D("90")), dated(6)]
    s = summarise(results(*trades))
    assert s.longest_win_streak == 2
    assert s.longest_loss_streak == 2
    assert s.max_drawdown_r.amount == D("2")


def test_same_date_trades_ordered_by_id():
    first = make_trade(id=1001, exit_price=D("90"))
    second = make_trade(id=1002, exit_price=D("120"))
    s = summarise(results(second, first))
    # Order 1001 (-1R) then 1002 (+2R): drawdown is 1 from the start.
    assert s.max_drawdown_r == Drawdown(D("1"), None, 1001)


def test_trades_without_r_count_in_outcomes_but_not_r_metrics():
    no_r = make_trade(stop_loss=D("105"))  # invalid stop; net +20
    s = summarise(results(no_r, r_trade(-1)))
    assert s.trades == 2
    assert s.wins == 1 and s.losses == 1
    assert s.win_rate == D("0.5")
    assert s.r_trades == 1
    assert s.average_r == D("-1")
    assert s.expectancy_r == D("-1")  # only trades with R


def test_planned_rr_average_only_counts_trades_with_target():
    s = summarise(results(make_trade(take_profit=D("120")), make_trade(take_profit=D("130")), make_trade()))
    assert s.planned_rr_trades == 2
    assert s.average_planned_rr == D("2.5")


def test_best_and_worst_ties_go_to_earliest():
    a = r_trade(2, trade_date="2026-01-05T10:00:00Z")
    b = r_trade(2, trade_date="2026-01-06T10:00:00Z")
    s = summarise(results(b, a))
    assert s.best_trade_r.trade_id == a["id"]


# ---------- Money per currency ----------


def test_money_is_kept_separate_per_currency():
    usd = make_trade(quote_currency="USD", exit_price=D("130"), fees=D("2"))  # net 28
    gbp = make_trade(quote_currency="GBP", exit_price=D("95"))  # net -5
    s = summarise(results(usd, gbp))
    assert set(s.money) == {"GBP", "USD"}
    assert s.money["USD"].net_pnl == D("28")
    assert s.money["GBP"].net_pnl == D("-5")
    # R is currency-neutral, so it CAN be combined.
    # USD: net 28 / risk 10 = +2.8R (fees count). GBP: -5 / 10 = -0.5R.
    assert s.total_r == D("2.3")


def test_money_summary_gross_fees_net_and_profit_factor():
    s = summarise(
        results(
            make_trade(exit_price=D("130"), fees=D("1")),  # gross 30, net 29
            make_trade(exit_price=D("95"), fees=D("1")),  # gross -5, net -6
            make_trade(exit_price=D("90"), fees=D("1")),  # gross -10, net -11
        )
    ).money["USD"]
    assert s.trades == 3
    assert s.gross_pnl == D("15")
    assert s.fees == D("3")
    assert s.net_pnl == D("12")
    assert s.winning_pnl == D("29")
    assert s.losing_pnl == D("-17")
    assert s.profit_factor == ratio(29, 17)  # same 60-digit division as the engine
    assert s.best_trade.value == D("29")
    assert s.worst_trade.value == D("-11")
    assert s.max_drawdown.amount == D("17")  # 29 -> 12
```

### `tests/test_properties.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_properties.py`

```python
"""Property-style tests: rules that must hold for ANY valid trades.

The 'hypothesis' library is the usual tool for this, but it is not a
project dependency. Instead we generate thousands of random (seeded, so
repeatable) trades with Python's random module and check the rules on each.
If a rule ever fails, the seed in the test name reproduces it exactly.
"""

import random
from decimal import Decimal
from fractions import Fraction

import pytest

from tradeledger.performance.metrics import summarise
from tradeledger.performance.trade_math import evaluate_trade

SEEDS = [1, 2, 3, 4, 5]
MULTIPLIERS = ["1", "10", "100", "100000"]


def random_trade(rng, trade_id):
    direction = rng.choice(["LONG", "SHORT"])
    sign = 1 if direction == "LONG" else -1
    entry = Decimal(rng.randint(100, 500_000)) / 100  # 1.00 .. 5000.00
    risk = Decimal(rng.randint(1, 5_000)) / 100  # 0.01 .. 50.00
    move = Decimal(rng.randint(-10_000, 15_000)) / 100  # -100 .. +150
    exit_price = max(entry + sign * move, Decimal("0.01"))
    return {
        "id": trade_id,
        "symbol": rng.choice(["AAA", "BBB", "CCC"]),
        "quote_currency": rng.choice(["USD", "GBP", "EUR"]),
        "contract_multiplier": Decimal(rng.choice(MULTIPLIERS)),
        "trade_date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T"
        f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00Z",
        "direction": direction,
        "status": "CLOSED",
        "entry_price": entry,
        "stop_loss": entry - sign * risk if entry - sign * risk > 0 else entry + risk,
        "take_profit": None,
        "exit_price": exit_price,
        "position_size": Decimal(rng.randint(1, 1_000)) / 100,
        "fees": Decimal(rng.randint(0, 2_000)) / 100,
    }


def random_results(seed, n=200):
    rng = random.Random(seed)
    trades = [random_trade(rng, i) for i in range(1, n + 1)]
    return trades, [evaluate_trade(t) for t in trades]


def close(a, b, places=40):
    return abs(Fraction(a) - Fraction(b)) < Fraction(1, 10**places)


@pytest.mark.parametrize("seed", SEEDS)
def test_r_times_risk_equals_net_pnl(seed):
    for r in random_results(seed)[1]:
        if r.realised_r is not None:
            assert close(r.realised_r * r.initial_risk, r.net_pnl, places=30)


@pytest.mark.parametrize("seed", SEEDS)
def test_net_equals_gross_minus_fees(seed):
    for r in random_results(seed)[1]:
        assert r.net_pnl == r.gross_pnl - r.fees


@pytest.mark.parametrize("seed", SEEDS)
def test_long_and_mirrored_short_give_identical_results(seed):
    """A short is a long seen in a mirror: reflect every price around the
    entry and the money and R must be identical."""
    trades, _ = random_results(seed, n=100)
    for t in trades:
        if t["direction"] != "LONG":
            continue
        entry = t["entry_price"]
        mirrored_exit = 2 * entry - t["exit_price"]
        if mirrored_exit <= 0:
            continue
        short = dict(t, direction="SHORT", stop_loss=2 * entry - t["stop_loss"], exit_price=mirrored_exit)
        long_result, short_result = evaluate_trade(t), evaluate_trade(short)
        assert short_result.net_pnl == long_result.net_pnl
        assert short_result.realised_r == long_result.realised_r


@pytest.mark.parametrize("seed", SEEDS)
def test_summary_identities(seed):
    _, results = random_results(seed)
    s = summarise(results)
    assert s.wins + s.losses + s.breakevens == s.trades
    assert close(s.win_rate + s.loss_rate + s.breakeven_rate, 1)
    assert close(s.expectancy_r, s.average_r)
    # Exact reference sums use Fraction, so the test adds no rounding of its own.
    r_values = [Fraction(r.realised_r) for r in results if r.realised_r is not None]
    assert s.r_trades == len(r_values)
    assert close(s.total_r, sum(r_values))
    total_losing_r = -sum(v for v in r_values if v < 0)
    assert 0 <= Fraction(s.max_drawdown_r.amount) <= total_losing_r + Fraction(1, 10**40)
    assert sum(m.trades for m in s.money.values()) == s.trades
    for m in s.money.values():
        assert m.net_pnl == m.gross_pnl - m.fees
        assert m.net_pnl == m.winning_pnl + m.losing_pnl


@pytest.mark.parametrize("seed", SEEDS)
def test_summary_does_not_depend_on_input_order(seed):
    _, results = random_results(seed)
    shuffled = results[:]
    random.Random(seed + 100).shuffle(shuffled)
    assert summarise(shuffled) == summarise(results)
```

### `tests/test_report.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_report.py`

```python
"""Report building, grouping, serialisation, display formatting and the CLI.

The sample-data tests check the engine against values worked out BY HAND
from tradeledger/seed.py, held as exact fractions (fractions.Fraction), so
they do not reuse the engine's own arithmetic.
"""

import json
import random
from decimal import Decimal
from fractions import Fraction as F

import pytest

from tests.factories import dated, make_trade, r_trade
from tradeledger.performance import analyse, build_report
from tradeledger.performance.formatting import (
    decimal_to_text,
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
    to_jsonable,
)
from tradeledger.performance.metrics import summarise
from tradeledger.performance.trade_math import evaluate_trade
from tradeledger.repositories.instruments import get_instrument_by_symbol

D = Decimal

# Realised R per closed sample trade = net P&L / initial risk, by hand.
# e.g. trade 1: net 393 USD; risk (1.085 - 1.083) x 1 x 100000 = 200 -> 393/200
HAND_R = {
    1: F(393, 200),  # +1.965
    2: F(-1035, 1000),  # -103.5 / 100
    3: F(0),  # breakeven
    4: F(293, 200),  # +1.465
    5: F(198, 100),  # +1.98
    6: F(-133, 130),  # -1.0230769...
    7: F(1985, 1000),  # +1.985
    8: F(-815, 800),  # -1.01875
    9: F(-5, 150),  # -0.0333...  (fees turned a price gain into a loss)
    10: F(2),  # +2
    11: F(0),  # breakeven
    12: F(-157, 150),  # -1.04666...
    13: F(158, 80),  # +1.975
    16: F(38, 80),  # +0.475
}
CHRONOLOGICAL_IDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]


def close_to(value, expected, places=40):
    """Decimal from the engine vs an exact Fraction, to `places` decimals."""
    return abs(F(value) - F(expected)) < F(1, 10**places)


@pytest.fixture
def sample_report(seeded_conn):
    return build_report(seeded_conn)


# ---------- Sample data: overall ----------


def test_sample_counts(sample_report):
    o = sample_report.overall
    assert (o.trades, o.wins, o.losses, o.breakevens) == (14, 7, 5, 2)
    assert sample_report.open_trades == 2
    assert sample_report.excluded == []
    assert sample_report.r_unavailable == []
    assert o.win_rate == D("0.5")
    assert close_to(o.loss_rate, F(5, 14))


def test_sample_total_and_average_r(sample_report):
    o = sample_report.overall
    assert sum(HAND_R.values()) == F(239871, 31200)  # 7.68817307...
    assert close_to(o.total_r, F(239871, 31200))
    assert close_to(o.average_r, F(239871, 31200) / 14)  # 0.549155...


def test_sample_average_win_and_loss_r(sample_report):
    o = sample_report.overall
    assert close_to(o.average_win_r, F(2369, 1400))  # 11.845 / 7
    assert close_to(o.average_loss_r, F(-129693, 156000))  # -4.15682... / 5


def test_sample_expectancy_matches_average_r(sample_report):
    o = sample_report.overall
    assert close_to(o.expectancy_r, F(239871, 436800))
    assert close_to(o.expectancy_r, o.average_r)


def test_sample_profit_factor_r(sample_report):
    assert close_to(sample_report.overall.profit_factor_r, F(369564, 129693))  # 2.8495...


def test_sample_planned_rr(sample_report):
    o = sample_report.overall
    assert o.planned_rr_trades == 12  # trades 11 and 16 have no target
    assert close_to(o.average_planned_rr, F(647, 312))  # 2.0737...


def test_sample_streaks(sample_report):
    # Date order: W L BE W W L W L L W BE L W W
    assert sample_report.overall.longest_win_streak == 2
    assert sample_report.overall.longest_loss_streak == 2


def test_sample_max_drawdown_r(sample_report):
    dd = sample_report.overall.max_drawdown_r
    assert close_to(dd.amount, F(101, 96))  # 1.01875 + 1/30 = 1.05208...
    assert (dd.peak_trade_id, dd.trough_trade_id) == (7, 9)


def test_sample_best_and_worst_trades(sample_report):
    o = sample_report.overall
    assert (o.best_trade_r.trade_id, o.best_trade_r.value) == (10, D("2"))
    assert o.worst_trade_r.trade_id == 12


def test_sample_money_per_currency(sample_report):
    usd = sample_report.overall.money["USD"]
    gbp = sample_report.overall.money["GBP"]
    assert (usd.trades, usd.gross_pnl, usd.fees, usd.net_pnl) == (12, D("842"), D("43.5"), D("798.5"))
    assert (usd.winning_pnl, usd.losing_pnl) == (D("1278.5"), D("-480"))
    assert close_to(usd.profit_factor, F(12785, 4800))
    assert usd.max_drawdown.amount == D("243.5")  # 846 -> 602.5
    assert (usd.best_trade.trade_id, usd.worst_trade.trade_id) == (1, 12)
    assert (gbp.trades, gbp.net_pnl, gbp.profit_factor) == (2, D("200"), None)


def test_sample_cumulative_r_curve(sample_report):
    curve = sample_report.cumulative_r
    assert [p.trade_id for p in curve] == CHRONOLOGICAL_IDS
    running = F(0)
    for point in curve:
        running += HAND_R[point.trade_id]
        assert close_to(point.realised_r, HAND_R[point.trade_id])
        assert close_to(point.cumulative_r, running)
    assert curve[-1].cumulative_r == sample_report.overall.total_r


# ---------- Sample data: groups ----------


def test_groups_by_instrument(sample_report):
    groups = sample_report.by_instrument
    assert list(groups) == ["EURUSD", "GBPUSD", "NAS100", "UK100", "XAUUSD"]
    assert {k: g.trades for k, g in groups.items()} == {
        "EURUSD": 3,
        "GBPUSD": 3,
        "NAS100": 2,
        "UK100": 2,
        "XAUUSD": 4,
    }
    gold = groups["XAUUSD"]
    assert gold.win_rate == D("0.75")
    assert close_to(gold.total_r, HAND_R[5] + HAND_R[6] + HAND_R[13] + HAND_R[16])  # 3.4069...
    assert list(groups["UK100"].money) == ["GBP"]


def test_groups_by_strategy_and_session(sample_report):
    assert {k: g.trades for k, g in sample_report.by_strategy.items()} == {
        "Breakout": 5,
        "Pullback": 5,
        "Reversal": 2,
        "Unspecified": 2,
    }
    assert {k: g.trades for k, g in sample_report.by_session.items()} == {
        "ASIA": 2,
        "LONDON": 6,
        "NEW_YORK": 5,
        "Unspecified": 1,
    }


def test_groups_by_weekday_in_calendar_order(sample_report):
    groups = sample_report.by_weekday
    assert list(groups) == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    assert groups["Friday"].win_rate == D("1")
    # Thursday: trades 4 (+1.465R) and 9 (-1/30 R): PF = 1.465 x 30 = 43.95.
    # -1/30 cannot be stored exactly in decimal, so compare to 40 places.
    assert close_to(groups["Thursday"].profit_factor_r, F(4395, 100))


def test_groups_by_month_and_date(sample_report):
    assert list(sample_report.by_month) == ["2026-03"]
    assert sample_report.by_month["2026-03"].trades == 14
    dates = list(sample_report.by_date)
    assert len(dates) == 14 and dates == sorted(dates)
    assert sample_report.by_date["2026-03-20"].trades == 1  # trade 15 that day is open


# ---------- Filters through build_report ----------


def test_report_filtered_by_instrument(seeded_conn):
    gold = get_instrument_by_symbol(seeded_conn, "XAUUSD")
    report = build_report(seeded_conn, instrument_id=gold["id"])
    assert report.overall.trades == 4
    assert list(report.by_instrument) == ["XAUUSD"]


def test_report_filtered_by_date_range(seeded_conn):
    report = build_report(seeded_conn, start="2026-03-02", end="2026-03-06")
    assert [p.trade_id for p in report.cumulative_r] == [1, 2, 3, 4, 5]


def test_report_on_empty_database(conn):
    report = build_report(conn)
    assert report.overall.trades == 0
    assert report.overall.expectancy_r is None
    assert report.cumulative_r == []
    assert report.by_instrument == {}


# ---------- analyse() with awkward input ----------


def test_open_and_incomplete_trades_are_separated():
    trades = [
        make_trade(),
        make_trade(status="OPEN", exit_price=None),
        make_trade(exit_price=None),  # "closed" but no exit: excluded
        make_trade(stop_loss=D("105")),  # counted, but no R
    ]
    report = analyse(trades)
    assert report.overall.trades == 2
    assert report.open_trades == 1
    assert [e.trade_id for e in report.excluded] == [trades[2]["id"]]
    assert "exit_price" in report.excluded[0].reason
    assert [e.trade_id for e in report.r_unavailable] == [trades[3]["id"]]
    assert len(report.cumulative_r) == 1


def test_input_order_does_not_change_results():
    trades = [dated(d, exit_price=D(str(80 + d * 3))) for d in range(5, 20)]
    shuffled = trades[:]
    random.Random(7).shuffle(shuffled)
    assert analyse(shuffled) == analyse(trades)


def test_all_winning_and_all_losing_reports():
    wins = analyse([r_trade(1), r_trade(2)])
    assert wins.overall.loss_rate == D("0") and wins.overall.profit_factor_r is None
    losses = analyse([r_trade(-1), r_trade(-1)])
    assert losses.overall.win_rate == D("0") and losses.overall.profit_factor_r == D("0")


# ---------- JSON serialisation ----------


def _contains_float(obj):
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(_contains_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_float(v) for v in obj)
    return False


def test_report_serialises_to_json_without_floats(sample_report):
    data = to_jsonable(sample_report)
    assert not _contains_float(data)
    text = json.dumps(data)
    parsed = json.loads(text)
    assert parsed["overall"]["trades"] == 14
    assert parsed["overall"]["money"]["USD"]["net_pnl"] == "798.5"
    # Phase 4 added realised_at and close_time_known to each curve point.
    assert parsed["cumulative_r"][0] == {
        "trade_id": 1,
        "trade_date": "2026-03-02T08:15:00Z",
        "realised_at": "2026-03-02T11:40:00Z",
        "close_time_known": True,
        "symbol": "EURUSD",
        "realised_r": "1.965",
        "cumulative_r": "1.965",
    }


def test_json_strings_round_trip_to_the_exact_decimal(sample_report):
    text = to_jsonable(sample_report)["overall"]["total_r"]
    assert D(text) == sample_report.overall.total_r


def test_decimal_to_text_has_no_exponent():
    assert decimal_to_text(D("4.0E+2")) == "400"
    assert decimal_to_text(D("1.9650")) == "1.965"
    assert decimal_to_text(D("-0")) == "0"
    assert decimal_to_text(D("0E-60")) == "0"


# ---------- Display formatting and rounding policy ----------


def test_format_r_rounds_half_up_only_for_display():
    assert format_r(D("1.965")) == "+1.97R"
    assert format_r(D("-1.035")) == "-1.04R"
    assert format_r(D("0")) == "0.00R"
    assert format_r(D("-0.001")) == "0.00R"
    assert format_r(None) == "n/a"
    assert format_r_amount(D("1.0520833")) == "1.05R"


def test_format_money_rate_and_ratio():
    assert format_money(D("1278.5"), "USD") == "1,278.50 USD"
    assert format_money(D("-480"), "USD") == "-480.00 USD"
    assert format_rate(D(5) / D(14)) == "35.7%"
    assert format_rate(None) == "n/a"
    assert format_ratio(D("2.8495")) == "2.85"


def test_rounding_late_is_more_accurate_than_rounding_early():
    """Three trades of +0.005R. Rounding each first gives 0.03R; the true
    total is 0.015R, which displays as 0.02R."""
    s = summarise([evaluate_trade(r_trade("0.005")) for _ in range(3)])
    assert s.total_r == D("0.015")
    assert format_r(s.total_r) == "+0.02R"
    rounded_first = sum(D("0.005").quantize(D("0.01"), rounding="ROUND_HALF_UP") for _ in range(3))
    assert rounded_first == D("0.03")


# ---------- CLI ----------


def test_cli_report_text(runner):
    runner.invoke(args=["seed-db"])
    result = runner.invoke(args=["report"])
    assert result.exit_code == 0
    assert "Expectancy per trade          +0.55R" in result.output
    assert "798.50 USD" in result.output
    assert "never added together" in result.output


def test_cli_report_json_and_filters(runner):
    runner.invoke(args=["seed-db"])
    data = json.loads(runner.invoke(args=["report", "--json"]).output)
    assert data["overall"]["trades"] == 14
    gold = json.loads(runner.invoke(args=["report", "--json", "--instrument", "xauusd"]).output)
    assert gold["overall"]["trades"] == 4


def test_cli_report_errors(runner):
    runner.invoke(args=["seed-db"])
    unknown = runner.invoke(args=["report", "--instrument", "NOPE"])
    assert unknown.exit_code != 0 and "No instrument" in unknown.output
    bad_dates = runner.invoke(args=["report", "--start", "2026-03-10", "--end", "2026-03-01"])
    assert bad_dates.exit_code != 0 and "end" in bad_dates.output
```

### `tests/test_repositories.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_repositories.py`

```python
"""Integration tests: repository functions against a real temporary SQLite file."""

from decimal import Decimal

import pytest

from tradeledger.repositories import DuplicateError
from tradeledger.repositories.instruments import (
    create_instrument,
    get_instrument,
    get_instrument_by_symbol,
    list_instruments,
)
from tradeledger.repositories.trades import (
    create_trade,
    delete_trade,
    get_trade,
    list_trades,
    update_trade,
)
from tradeledger.validation import ValidationError

GOLD = {
    "symbol": "XAUUSD",
    "name": "Gold (test)",
    "asset_class": "COMMODITY",
    "price_increment": "0.01",
    "contract_multiplier": "100",
    "quote_currency": "USD",
}


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------- Instruments ----------


def test_create_and_get_instrument(conn):
    new_id = create_instrument(conn, GOLD)
    record = get_instrument(conn, new_id)
    assert record["symbol"] == "XAUUSD"
    assert record["contract_multiplier"] == Decimal("100")
    assert record["created_at"].endswith("Z")


def test_get_instrument_by_symbol_ignores_case(conn):
    create_instrument(conn, GOLD)
    assert get_instrument_by_symbol(conn, "xauusd")["name"] == "Gold (test)"


def test_missing_instrument_returns_none(conn):
    assert get_instrument(conn, 999) is None
    assert get_instrument_by_symbol(conn, "NOPE") is None


def test_list_instruments_sorted_by_symbol(conn, eurusd_id):
    create_instrument(conn, GOLD)
    assert [i["symbol"] for i in list_instruments(conn)] == ["EURUSD", "XAUUSD"]


def test_list_instruments_empty_database(conn):
    assert list_instruments(conn) == []


def test_duplicate_symbol_is_rejected(conn):
    create_instrument(conn, GOLD)
    with pytest.raises(DuplicateError):
        create_instrument(conn, dict(GOLD, symbol="xauusd"))
    assert count(conn, "instruments") == 1


def test_invalid_instrument_writes_nothing(conn):
    with pytest.raises(ValidationError):
        create_instrument(conn, dict(GOLD, contract_multiplier="-100"))
    assert count(conn, "instruments") == 0


# ---------- Trades: create and read ----------


def test_create_and_get_trade(conn, long_trade):
    trade_id = create_trade(conn, long_trade)
    record = get_trade(conn, trade_id)
    assert record["symbol"] == "EURUSD"
    assert record["direction"] == "LONG"
    assert record["entry_price"] == Decimal("1.085")
    assert record["fees"] == Decimal("7")
    assert record["status"] == "CLOSED"
    assert record["created_at"] == record["updated_at"]


def test_get_missing_trade_returns_none(conn):
    assert get_trade(conn, 12345) is None


def test_trade_with_unknown_instrument_is_rejected(conn, long_trade):
    with pytest.raises(ValidationError) as info:
        create_trade(conn, dict(long_trade, instrument_id=999))
    assert "instrument_id" in info.value.errors
    assert count(conn, "trades") == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"entry_price": "-1"},
        {"position_size": "0"},
        {"direction": "SIDEWAYS"},
        {"status": "MAYBE"},
        {"exit_price": None},  # CLOSED without exit
        {"stop_loss": "1.09000"},  # long stop above entry
        {"trade_date": "31/12/2026"},
    ],
)
def test_invalid_trades_write_nothing(conn, long_trade, changes):
    with pytest.raises(ValidationError):
        create_trade(conn, dict(long_trade, **changes))
    assert count(conn, "trades") == 0


# ---------- Trades: update and delete ----------


def test_update_trade_changes_values_and_timestamp(conn, long_trade):
    trade_id = create_trade(conn, long_trade)
    # Force an older updated_at so the change is visible within one second.
    with conn:
        conn.execute("UPDATE trades SET updated_at = '2000-01-01T00:00:00Z' WHERE id = ?", (trade_id,))

    changed = dict(long_trade, exit_price="1.08700", notes="Closed early")
    assert update_trade(conn, trade_id, changed) is True

    record = get_trade(conn, trade_id)
    assert record["exit_price"] == Decimal("1.087")
    assert record["notes"] == "Closed early"
    assert record["updated_at"] != "2000-01-01T00:00:00Z"


def test_update_can_reopen_a_trade(conn, long_trade):
    trade_id = create_trade(conn, long_trade)
    update_trade(conn, trade_id, dict(long_trade, status="OPEN", exit_price=None, closed_at=None))
    record = get_trade(conn, trade_id)
    assert record["status"] == "OPEN"
    assert record["exit_price"] is None


def test_update_missing_trade_returns_false(conn, long_trade):
    assert update_trade(conn, 999, long_trade) is False


def test_invalid_update_leaves_trade_unchanged(conn, long_trade):
    trade_id = create_trade(conn, long_trade)
    with pytest.raises(ValidationError):
        update_trade(conn, trade_id, dict(long_trade, position_size="-5"))
    assert get_trade(conn, trade_id)["position_size"] == Decimal("1")


def test_delete_trade(conn, long_trade):
    trade_id = create_trade(conn, long_trade)
    assert delete_trade(conn, trade_id) is True
    assert get_trade(conn, trade_id) is None
    assert delete_trade(conn, trade_id) is False  # already gone


# ---------- Filtering (uses the sample data) ----------


def test_list_all_trades_newest_first(seeded_conn):
    trades = list_trades(seeded_conn)
    assert len(trades) == 16
    dates = [t["trade_date"] for t in trades]
    assert dates == sorted(dates, reverse=True)


def test_filter_by_instrument(seeded_conn):
    gold = get_instrument_by_symbol(seeded_conn, "XAUUSD")
    trades = list_trades(seeded_conn, instrument_id=gold["id"])
    assert len(trades) == 4
    assert {t["symbol"] for t in trades} == {"XAUUSD"}


def test_filter_by_date_range_is_inclusive(seeded_conn):
    trades = list_trades(seeded_conn, start="2026-03-02", end="2026-03-04")
    assert sorted(t["trade_date"] for t in trades) == [
        "2026-03-02T08:15:00Z",
        "2026-03-03T13:40:00Z",
        "2026-03-04T08:30:00Z",
    ]


def test_filter_start_only_and_end_only(seeded_conn):
    assert len(list_trades(seeded_conn, start="2026-03-19")) == 3
    assert len(list_trades(seeded_conn, end="2026-03-02")) == 1


def test_filters_combine(seeded_conn):
    gold = get_instrument_by_symbol(seeded_conn, "XAUUSD")
    trades = list_trades(seeded_conn, instrument_id=gold["id"], start="2026-03-10", status="CLOSED")
    assert [t["trade_date"] for t in trades] == ["2026-03-20T15:10:00Z", "2026-03-18T03:20:00Z"]


def test_filter_with_no_matches_returns_empty_list(seeded_conn):
    assert list_trades(seeded_conn, start="2030-01-01") == []


def test_invalid_filters_are_rejected(seeded_conn):
    with pytest.raises(ValidationError):
        list_trades(seeded_conn, start="2026-03-10", end="2026-03-01")
    with pytest.raises(ValidationError):
        list_trades(seeded_conn, status="closed")


def test_sql_injection_attempt_is_treated_as_plain_data(seeded_conn):
    """A malicious 'symbol' is just a string that matches nothing."""
    assert get_instrument_by_symbol(seeded_conn, "X' OR '1'='1") is None
    assert len(list_trades(seeded_conn)) == 16  # nothing was dropped or altered
```

### `tests/test_trade_math.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_trade_math.py`

```python
"""Unit tests for per-trade calculations (performance/trade_math.py)."""

from decimal import Decimal

import pytest

from tests.factories import make_trade
from tradeledger.performance.trade_math import (
    IncompleteTradeError,
    classify,
    evaluate_trade,
    gross_pnl,
    initial_risk,
    net_pnl,
    planned_rr,
    price_move,
    realised_r,
    risk_per_unit,
)

D = Decimal


# ---------- Building blocks ----------


def test_price_move_long_and_short():
    assert price_move("LONG", D("100"), D("120")) == D("20")
    assert price_move("SHORT", D("100"), D("120")) == D("-20")
    assert price_move("SHORT", D("100"), D("80")) == D("20")


def test_gross_pnl_uses_size_and_multiplier():
    # EURUSD example: 0.004 move x 1 lot x 100000 = 400 USD
    assert gross_pnl("LONG", D("1.085"), D("1.089"), D("1"), D("100000")) == D("400")
    # Gold example: short 0.2 lots, price rises 6.5: -6.5 x 0.2 x 100 = -130
    assert gross_pnl("SHORT", D("2165.5"), D("2172"), D("0.2"), D("100")) == D("-130")


def test_net_pnl_subtracts_fees():
    assert net_pnl(D("400"), D("7")) == D("393")
    assert net_pnl(D("-100"), D("3.5")) == D("-103.5")


def test_risk_per_unit_positive_only_when_stop_on_losing_side():
    assert risk_per_unit("LONG", D("100"), D("90")) == D("10")
    assert risk_per_unit("SHORT", D("100"), D("110")) == D("10")
    assert risk_per_unit("LONG", D("100"), D("110")) == D("-10")


def test_initial_risk():
    assert initial_risk("LONG", D("1.085"), D("1.083"), D("1"), D("100000")) == D("200")
    assert initial_risk("SHORT", D("1.088"), D("1.090"), D("0.5"), D("100000")) == D("100")


def test_initial_risk_is_none_for_invalid_stop():
    assert initial_risk("LONG", D("100"), D("100"), D("1"), D("1")) is None  # zero risk
    assert initial_risk("LONG", D("100"), D("105"), D("1"), D("1")) is None  # wrong side
    assert initial_risk("SHORT", D("100"), D("95"), D("1"), D("1")) is None


def test_realised_r():
    assert realised_r(D("393"), D("200")) == D("1.965")
    assert realised_r(D("-103.5"), D("100")) == D("-1.035")
    assert realised_r(D("0"), D("100")) == D("0")


def test_realised_r_unavailable_without_positive_risk():
    assert realised_r(D("50"), None) is None
    assert realised_r(D("50"), D("0")) is None
    assert realised_r(D("50"), D("-10")) is None


def test_planned_rr():
    assert planned_rr("LONG", D("100"), D("90"), D("120")) == D("2")
    assert planned_rr("SHORT", D("18300"), D("18340"), D("18200")) == D("2.5")
    assert planned_rr("LONG", D("100"), D("90"), None) is None
    assert planned_rr("LONG", D("100"), D("90"), D("95")) is None  # target on wrong side
    assert planned_rr("LONG", D("100"), D("100"), D("120")) is None  # zero risk


def test_classify():
    assert classify(D("0.01")) == "WIN"
    assert classify(D("-0.01")) == "LOSS"
    assert classify(D("0")) == "BREAKEVEN"
    assert classify(D("-0.00")) == "BREAKEVEN"


# ---------- evaluate_trade ----------


def test_evaluate_long_winner():
    result = evaluate_trade(make_trade(take_profit=D("130")))
    assert result.gross_pnl == D("20")
    assert result.net_pnl == D("20")
    assert result.initial_risk == D("10")
    assert result.realised_r == D("2")
    assert result.planned_rr == D("3")
    assert result.outcome == "WIN"
    assert result.r_unavailable_reason is None


def test_evaluate_short_loser_with_fees():
    # Short 100 -> 110 (stop 110): gross -10, fees 0.5 -> net -10.5, R -1.05
    result = evaluate_trade(
        make_trade(direction="SHORT", stop_loss=D("110"), exit_price=D("110"), fees=D("0.5"))
    )
    assert result.gross_pnl == D("-10")
    assert result.net_pnl == D("-10.5")
    assert result.realised_r == D("-1.05")
    assert result.outcome == "LOSS"


def test_fees_turn_small_price_gain_into_loss():
    # Price up 0.2 on a long, fees 0.5: gross +0.2, net -0.3
    result = evaluate_trade(make_trade(exit_price=D("100.2"), fees=D("0.5")))
    assert result.gross_pnl == D("0.2")
    assert result.net_pnl == D("-0.3")
    assert result.outcome == "LOSS"
    assert result.realised_r == D("-0.03")


def test_breakeven_is_exactly_zero_net():
    result = evaluate_trade(make_trade(exit_price=D("100")))
    assert result.outcome == "BREAKEVEN"
    assert result.realised_r == D("0")


def test_exit_at_entry_with_fees_is_a_loss_not_breakeven():
    result = evaluate_trade(make_trade(exit_price=D("100"), fees=D("1")))
    assert result.outcome == "LOSS"


def test_contract_multiplier_changes_money_not_r():
    small = evaluate_trade(make_trade(contract_multiplier=D("1")))
    large = evaluate_trade(make_trade(contract_multiplier=D("100000")))
    assert large.net_pnl == small.net_pnl * 100000
    assert large.realised_r == small.realised_r == D("2")


def test_position_size_changes_money_not_r():
    one = evaluate_trade(make_trade(position_size=D("1")))
    three = evaluate_trade(make_trade(position_size=D("3")))
    assert three.net_pnl == D("60")
    assert three.realised_r == one.realised_r


def test_decimal_precision_no_float_error():
    # In floats, 0.3 - 0.1 = 0.19999999999999998. Decimal gets exactly 0.2.
    result = evaluate_trade(
        make_trade(entry_price=D("0.1"), stop_loss=D("0.05"), exit_price=D("0.3"), position_size=D("3"))
    )
    assert result.gross_pnl == D("0.6")  # (0.3 - 0.1) x 3, exactly
    assert result.realised_r == D("4")  # 0.6 / (0.05 x 3)


def test_invalid_risk_keeps_pnl_but_has_no_r():
    result = evaluate_trade(make_trade(stop_loss=D("105")))  # long stop above entry
    assert result.net_pnl == D("20")
    assert result.outcome == "WIN"
    assert result.realised_r is None
    assert result.initial_risk is None
    assert "stop-loss" in result.r_unavailable_reason


def test_zero_risk_has_no_r():
    assert evaluate_trade(make_trade(stop_loss=D("100"))).realised_r is None


def test_strings_and_ints_are_accepted():
    result = evaluate_trade(make_trade(entry_price="100", stop_loss=90, exit_price="120.0"))
    assert result.realised_r == D("2")


def test_missing_fees_count_as_zero():
    assert evaluate_trade(make_trade(fees=None)).net_pnl == D("20")


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"status": "OPEN", "exit_price": None}, "not closed"),
        ({"exit_price": None}, "missing exit_price"),
        ({"entry_price": None}, "missing entry_price"),
        ({"stop_loss": None}, "missing stop_loss"),
        ({"contract_multiplier": None}, "missing contract_multiplier"),
        ({"contract_multiplier": D("0")}, "contract_multiplier"),
        ({"position_size": D("0")}, "position_size"),
        ({"position_size": D("-1")}, "position_size"),
        ({"fees": D("-1")}, "fees"),
        ({"direction": "BUY"}, "direction"),
        ({"entry_price": 100.0}, "not float"),
        ({"entry_price": "abc"}, "not a number"),
        ({"entry_price": "NaN"}, "finite"),
        ({"quote_currency": None}, "quote_currency"),
        ({"trade_date": "05/01/2026"}, "trade_date"),
    ],
)
def test_incomplete_or_invalid_trades_raise(changes, message):
    with pytest.raises(IncompleteTradeError, match=message):
        evaluate_trade(make_trade(**changes))
```

### `tests/test_validation.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_validation.py`

```python
"""Unit tests for validation.py - no database needed."""

from decimal import Decimal

import pytest

from tradeledger.validation import (
    ValidationError,
    date_range_bounds,
    parse_decimal,
    parse_timestamp,
    to_db_decimal,
    validate_instrument,
    validate_trade,
)

# ---------- Decimals ----------


@pytest.mark.parametrize(
    "raw, expected",
    [("1.08500", "1.085"), ("100", "100"), ("0.00001", "0.00001"), (" 2150.00 ", "2150"), (5, "5")],
)
def test_decimal_is_stored_in_canonical_form(raw, expected):
    assert to_db_decimal(parse_decimal(raw)) == expected


@pytest.mark.parametrize("raw", ["", "abc", "1.2.3", "NaN", "Infinity", None])
def test_non_numbers_are_rejected(raw):
    with pytest.raises(ValueError):
        parse_decimal(raw)


def test_floats_are_rejected_to_avoid_hidden_rounding():
    with pytest.raises(ValueError, match="float"):
        parse_decimal(1.085)


def test_zero_and_negative_rejected_unless_zero_allowed():
    with pytest.raises(ValueError):
        parse_decimal("0")
    with pytest.raises(ValueError):
        parse_decimal("-1")
    assert parse_decimal("0", allow_zero=True) == Decimal("0")
    with pytest.raises(ValueError):
        parse_decimal("-1", allow_zero=True)


def test_too_many_significant_digits_rejected():
    with pytest.raises(ValueError, match="significant"):
        parse_decimal("1.234567890123456")  # 16 significant digits


# ---------- Dates and times ----------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("2026-03-02T08:15:00Z", "2026-03-02T08:15:00Z"),
        ("2026-03-02 08:15", "2026-03-02T08:15:00Z"),  # no zone -> UTC
        ("2026-03-02T09:15:00+01:00", "2026-03-02T08:15:00Z"),  # converted to UTC
        ("2026-03-02T08:15:59.900Z", "2026-03-02T08:15:59Z"),  # seconds precision
    ],
)
def test_valid_timestamps_are_normalised_to_utc(raw, expected):
    assert parse_timestamp(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "2026-03-02", "02/03/2026 08:15", "2026-02-30T10:00:00Z", "yesterday", None, "1969-12-31T23:00:00Z"],
)
def test_invalid_timestamps_are_rejected(raw):
    with pytest.raises(ValueError):
        parse_timestamp(raw)


def test_date_range_end_date_is_inclusive():
    assert date_range_bounds("2026-03-02", "2026-03-04") == (
        "2026-03-02T00:00:00Z",
        "2026-03-05T00:00:00Z",
    )


def test_date_range_rejects_end_before_start():
    with pytest.raises(ValidationError) as info:
        date_range_bounds("2026-03-05", "2026-03-01")
    assert "end" in info.value.errors


def test_date_range_rejects_bad_dates():
    with pytest.raises(ValidationError) as info:
        date_range_bounds("2026-13-01", "not-a-date")
    assert set(info.value.errors) == {"start", "end"}


# ---------- Instruments ----------


def test_instrument_is_cleaned():
    clean = validate_instrument(
        {
            "symbol": " xauusd ",
            "name": "Gold",
            "asset_class": "commodity",
            "price_increment": "0.010",
            "contract_multiplier": "100",
            "quote_currency": "usd",
        }
    )
    assert clean == {
        "symbol": "XAUUSD",
        "name": "Gold",
        "asset_class": "COMMODITY",
        "price_increment": "0.01",
        "contract_multiplier": "100",
        "quote_currency": "USD",
    }


def test_instrument_reports_every_error_at_once():
    with pytest.raises(ValidationError) as info:
        validate_instrument({"symbol": "", "asset_class": "CRYPTO", "quote_currency": "DOLLARS"})
    assert set(info.value.errors) == {
        "symbol",
        "name",
        "asset_class",
        "price_increment",
        "contract_multiplier",
        "quote_currency",
    }


# ---------- Trades ----------

BASE_TRADE = {
    "instrument_id": "1",
    "trade_date": "2026-03-02T08:15:00Z",
    "direction": "long",
    "entry_price": "1.08500",
    "stop_loss": "1.08300",
    "position_size": "1",
}


def trade(**changes):
    data = dict(BASE_TRADE)
    data.update(changes)
    return data


def test_minimal_open_trade_gets_defaults():
    clean = validate_trade(trade())
    assert clean["direction"] == "LONG"
    assert clean["status"] == "OPEN"
    assert clean["fees"] == "0"
    assert clean["exit_price"] is None
    assert clean["entry_price"] == "1.085"


def test_status_is_derived_as_closed_when_exit_given():
    # Phase 4: a closed trade also needs its exit time.
    assert validate_trade(trade(exit_price="1.089", closed_at="2026-03-02T10:00:00Z"))["status"] == "CLOSED"


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"direction": "BUY"}, "direction"),
        ({"status": "PENDING"}, "status"),
        ({"status": "CLOSED"}, "exit_price"),  # closed, no exit
        ({"status": "OPEN", "exit_price": "1.09"}, "exit_price"),  # open with exit
        ({"stop_loss": "1.09"}, "stop_loss"),  # long stop above entry
        ({"stop_loss": "1.085"}, "stop_loss"),  # zero risk
        ({"direction": "SHORT"}, "stop_loss"),  # short stop below entry
        ({"take_profit": "1.08"}, "take_profit"),  # long target below entry
        ({"position_size": "0"}, "position_size"),
        ({"position_size": "-0.5"}, "position_size"),
        ({"entry_price": "-1.0"}, "entry_price"),
        ({"entry_price": "abc"}, "entry_price"),
        ({"fees": "-3"}, "fees"),
        ({"instrument_id": "abc"}, "instrument_id"),
        ({"instrument_id": "0"}, "instrument_id"),
        ({"trade_date": "2026-02-30T10:00:00Z"}, "trade_date"),
        ({"session": "SYDNEY"}, "session"),
        ({"strategy": "x" * 51}, "strategy"),
    ],
)
def test_invalid_trades_are_rejected(changes, field):
    with pytest.raises(ValidationError) as info:
        validate_trade(trade(**changes))
    assert field in info.value.errors


def test_valid_short_trade_is_accepted():
    clean = validate_trade(
        trade(
            direction="SHORT",
            stop_loss="1.08700",
            take_profit="1.08100",
            exit_price="1.08100",
            closed_at="2026-03-02T10:00:00Z",
        )
    )
    assert clean["direction"] == "SHORT"
    assert clean["status"] == "CLOSED"


def test_blank_optional_fields_become_none():
    clean = validate_trade(trade(take_profit="", strategy="  ", session="", notes=""))
    assert clean["take_profit"] is None
    assert clean["strategy"] is None
    assert clean["session"] is None
    assert clean["notes"] is None
```

### `tests/test_web_forms.py` (MODIFIED, format only)

**Why:** Layout changed by `ruff format`; the syntax tree is identical.  
**How to run or test:** Run: `python -m pytest tests/test_web_forms.py`

```python
"""Phase 5: the Add, Edit and Delete forms and the Instruments form.

Every test goes through the real HTTP layer (test client) with a valid
CSRF token, exactly like a browser, and then checks what was - or was not -
written to SQLite.
"""

import pytest

from tests.web_helpers import (
    closed_trade_form,
    count_rows,
    csrf_token,
    fetch_trade,
    new_trade_id,
    open_trade_form,
    post_form,
)

# ---------- Add trade: success ----------


def test_create_closed_trade_redirects_to_details(app, seeded_client):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form())
    assert response.status_code == 302  # POST-Redirect-GET
    trade_id = new_trade_id(response)
    assert count_rows(app, "trades") == before + 1

    stored = fetch_trade(app, trade_id)
    assert stored["status"] == "CLOSED"
    assert stored["trade_date"] == "2026-04-01T08:15:00Z"  # normalised to UTC
    assert stored["closed_at"] == "2026-04-01T11:40:00Z"
    assert stored["entry_price"] == "1.085"  # canonical decimal text
    assert stored["strategy"] == "Breakout"
    assert stored["notes"] == "Website test trade (synthetic)"

    page = seeded_client.get(response.headers["Location"]).get_data(as_text=True)
    assert f"Trade #{trade_id} saved." in page  # flash message
    assert "393.00 USD" in page and "+1.97R" in page  # engine values


def test_create_open_trade_stores_no_exit(app, seeded_client):
    response = post_form(seeded_client, "/trades/new", open_trade_form())
    assert response.status_code == 302
    stored = fetch_trade(app, new_trade_id(response))
    assert stored["status"] == "OPEN"
    assert stored["exit_price"] is None
    assert stored["closed_at"] is None


def test_open_trade_ignores_stale_exit_values(app, seeded_client):
    """With JavaScript off, a user can type exit values and then choose
    OPEN. The server discards them instead of storing a half-closed trade."""
    form = closed_trade_form(status="OPEN")  # exit fields still filled in
    response = post_form(seeded_client, "/trades/new", form)
    assert response.status_code == 302
    stored = fetch_trade(app, new_trade_id(response))
    assert stored["exit_price"] is None and stored["closed_at"] is None


def test_seconds_in_timestamps_are_kept(app, seeded_client):
    form = closed_trade_form(trade_date="2026-04-01T08:15:30", closed_at="2026-04-01T11:40:05")
    stored = fetch_trade(app, new_trade_id(post_form(seeded_client, "/trades/new", form)))
    assert stored["trade_date"] == "2026-04-01T08:15:30Z"
    assert stored["closed_at"] == "2026-04-01T11:40:05Z"


def test_blank_fees_mean_zero(app, seeded_client):
    stored = fetch_trade(
        app, new_trade_id(post_form(seeded_client, "/trades/new", closed_trade_form(fees="")))
    )
    assert stored["fees"] == "0"


def test_optional_text_fields_may_be_blank(app, seeded_client):
    form = closed_trade_form(strategy="", notes="", session="", take_profit="")
    stored = fetch_trade(app, new_trade_id(post_form(seeded_client, "/trades/new", form)))
    assert stored["strategy"] is None and stored["notes"] is None
    assert stored["session"] is None and stored["take_profit"] is None


def test_short_trade_with_stop_above_entry(app, seeded_client):
    form = closed_trade_form(
        direction="SHORT",
        entry_price="1.08500",
        stop_loss="1.08700",
        take_profit="1.08100",
        exit_price="1.08200",
    )
    stored = fetch_trade(app, new_trade_id(post_form(seeded_client, "/trades/new", form)))
    assert stored["direction"] == "SHORT"


def test_instrument_can_be_preselected(seeded_client):
    html = seeded_client.get("/trades/new?instrument_id=3").get_data(as_text=True)
    assert '<option value="3" selected' in html


# ---------- Add trade: validation ----------


def _assert_rejected(app, response, before, *fields):
    assert response.status_code == 400
    html = response.get_data(as_text=True)
    assert 'class="error-summary"' in html
    for field in fields:
        assert f'id="field-{field}"' in html
        assert f'href="#field-{field}"' in html  # summary links to the field
    assert count_rows(app, "trades") == before  # nothing written
    return html


@pytest.mark.parametrize(
    "field", ["instrument_id", "trade_date", "entry_price", "stop_loss", "position_size"]
)
def test_missing_required_field_is_rejected(app, seeded_client, field):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form(**{field: ""}))
    _assert_rejected(app, response, before, field)


def test_closed_trade_needs_exit_price_and_exit_time(app, seeded_client):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form(exit_price="", closed_at=""))
    _assert_rejected(app, response, before, "exit_price", "closed_at")


def test_stop_on_wrong_side_is_rejected(app, seeded_client):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form(stop_loss="1.09000"))
    html = _assert_rejected(app, response, before, "stop_loss")
    assert 'aria-invalid="true"' in html


def test_exit_before_entry_is_rejected(app, seeded_client):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form(closed_at="2026-04-01T08:00"))
    _assert_rejected(app, response, before, "closed_at")


@pytest.mark.parametrize(
    "field, value",
    [
        ("entry_price", "abc"),
        ("entry_price", "-1"),
        ("position_size", "0"),
        ("fees", "-5"),
        ("instrument_id", "999"),
        ("direction", "SIDEWAYS"),
        ("status", "MAYBE"),
        ("trade_date", "yesterday"),
        ("session", "MOON"),
    ],
)
def test_invalid_values_are_rejected(app, seeded_client, field, value):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/new", closed_trade_form(**{field: value}))
    assert response.status_code == 400
    assert count_rows(app, "trades") == before


def test_rejected_form_keeps_what_the_user_typed(seeded_client):
    form = closed_trade_form(stop_loss="1.09000", strategy="My <b>setup</b>", notes="Keep me")
    html = post_form(seeded_client, "/trades/new", form).get_data(as_text=True)
    assert 'value="1.09000"' in html
    assert 'value="1.08500"' in html
    assert "My &lt;b&gt;setup&lt;/b&gt;" in html  # kept, and escaped
    assert "Keep me</textarea>" in html


def test_rejected_form_keeps_the_chosen_radio_buttons(seeded_client):
    import re

    form = closed_trade_form(direction="SHORT", stop_loss="1.08000")  # wrong side for a short
    html = post_form(seeded_client, "/trades/new", form).get_data(as_text=True)
    short = re.search(r'<input[^>]*value="SHORT"[^>]*>', html).group(0)
    long_ = re.search(r'<input[^>]*value="LONG"[^>]*>', html).group(0)
    assert "checked" in short and "checked" not in long_


# ---------- Edit ----------


def test_edit_form_is_prefilled(seeded_client):
    html = seeded_client.get("/trades/1/edit").get_data(as_text=True)
    assert 'value="1.085"' in html
    assert 'value="2026-03-02T08:15:00"' in html  # datetime-local, seconds kept
    assert "Breakout" in html


def test_edit_persists_changes(app, seeded_client):
    form = closed_trade_form(exit_price="1.08700", notes="Edited")
    response = post_form(seeded_client, "/trades/1/edit", form)
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/trades/1")
    stored = fetch_trade(app, 1)
    assert stored["exit_price"] == "1.087" and stored["notes"] == "Edited"
    page = seeded_client.get("/trades/1").get_data(as_text=True)
    assert "Trade #1 updated." in page


def test_edit_can_reopen_a_trade(app, seeded_client):
    post_form(seeded_client, "/trades/1/edit", open_trade_form())
    stored = fetch_trade(app, 1)
    assert stored["status"] == "OPEN" and stored["exit_price"] is None and stored["closed_at"] is None


def test_edit_can_close_an_open_trade(app, seeded_client):
    form = closed_trade_form(
        trade_date="2026-03-19T14:35",
        closed_at="2026-03-19T18:00",
        instrument_id="4",
        entry_price="18500",
        stop_loss="18440",
        take_profit="18620",
        exit_price="18600",
        fees="0.75",
    )
    response = post_form(seeded_client, "/trades/14/edit", form)
    assert response.status_code == 302
    assert fetch_trade(app, 14)["status"] == "CLOSED"


def test_invalid_edit_changes_nothing(app, seeded_client):
    before = fetch_trade(app, 1)
    response = post_form(seeded_client, "/trades/1/edit", closed_trade_form(stop_loss="1.09"))
    assert response.status_code == 400
    assert fetch_trade(app, 1) == before


def _make_legacy(app, trade_id=1):
    from tradeledger.db import get_db

    with app.app_context():
        db = get_db()
        db.execute("UPDATE trades SET closed_at = NULL WHERE id = ?", (trade_id,))
        db.commit()


def test_legacy_trade_can_be_edited_without_inventing_an_exit_time(app, seeded_client):
    _make_legacy(app)
    html = seeded_client.get("/trades/1/edit").get_data(as_text=True)
    assert "Exit time not recorded" in html
    assert "data-legacy-close" in html
    response = post_form(
        seeded_client, "/trades/1/edit", closed_trade_form(closed_at="", notes="Legacy edit")
    )
    assert response.status_code == 302
    stored = fetch_trade(app, 1)
    assert stored["closed_at"] is None  # still unknown, never invented
    assert stored["notes"] == "Legacy edit"


def test_legacy_trade_can_gain_a_real_exit_time(app, seeded_client):
    _make_legacy(app)
    post_form(seeded_client, "/trades/1/edit", closed_trade_form(closed_at="2026-04-01T12:00"))
    assert fetch_trade(app, 1)["closed_at"] == "2026-04-01T12:00:00Z"


def test_edit_of_missing_trade_is_404(seeded_client):
    response = post_form(seeded_client, "/trades/999/edit", closed_trade_form())
    assert response.status_code == 404


# ---------- Delete ----------


def test_get_delete_shows_confirmation_and_deletes_nothing(app, seeded_client):
    before = count_rows(app, "trades")
    response = seeded_client.get("/trades/1/delete")
    assert response.status_code == 200
    assert b"Delete trade #1?" in response.data
    assert b'method="post"' in response.data
    assert count_rows(app, "trades") == before


def test_post_delete_removes_trade_and_returns_to_journal(app, seeded_client):
    before = count_rows(app, "trades")
    response = post_form(seeded_client, "/trades/1/delete", {})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/trades")
    assert count_rows(app, "trades") == before - 1
    assert fetch_trade(app, 1) is None
    page = seeded_client.get("/trades").get_data(as_text=True)
    assert "Trade #1 (EURUSD LONG) was deleted." in page


def test_delete_returns_to_a_safe_next_page(seeded_client):
    response = post_form(seeded_client, "/trades/2/delete", {"next": "/trades?status=CLOSED"})
    assert response.headers["Location"].endswith("/trades?status=CLOSED")


def test_delete_missing_trade_is_404(seeded_client):
    assert post_form(seeded_client, "/trades/999/delete", {}).status_code == 404


def test_deleting_twice_is_404_not_an_error(seeded_client):
    post_form(seeded_client, "/trades/3/delete", {})
    assert post_form(seeded_client, "/trades/3/delete", {}).status_code == 404


# ---------- Instruments ----------

INSTRUMENT = {
    "symbol": "USDJPY",
    "name": "US Dollar / Yen (test)",
    "asset_class": "FOREX",
    "price_increment": "0.001",
    "contract_multiplier": "100000",
    "quote_currency": "JPY",
}


def test_add_instrument(app, seeded_client):
    before = count_rows(app, "instruments")
    response = post_form(seeded_client, "/instruments", INSTRUMENT, token_page="/instruments")
    assert response.status_code == 302
    assert count_rows(app, "instruments") == before + 1
    page = seeded_client.get("/instruments").get_data(as_text=True)
    assert "Instrument USDJPY added." in page
    assert "US Dollar / Yen (test)" in page


def test_new_instrument_is_offered_on_the_trade_form(seeded_client):
    post_form(seeded_client, "/instruments", INSTRUMENT, token_page="/instruments")
    assert "USDJPY" in seeded_client.get("/trades/new").get_data(as_text=True)


def test_duplicate_symbol_is_rejected_with_explanation(app, seeded_client):
    before = count_rows(app, "instruments")
    response = post_form(
        seeded_client, "/instruments", {**INSTRUMENT, "symbol": "EURUSD"}, token_page="/instruments"
    )
    assert response.status_code == 400
    assert b"already recorded" in response.data
    assert count_rows(app, "instruments") == before


@pytest.mark.parametrize(
    "field, value",
    [
        ("symbol", ""),
        ("contract_multiplier", "0"),
        ("price_increment", "-1"),
        ("quote_currency", "dollars"),
        ("asset_class", "BEANS"),
    ],
)
def test_invalid_instrument_is_rejected_and_values_kept(app, seeded_client, field, value):
    before = count_rows(app, "instruments")
    response = post_form(
        seeded_client, "/instruments", {**INSTRUMENT, field: value}, token_page="/instruments"
    )
    assert response.status_code == 400
    assert count_rows(app, "instruments") == before
    assert b"US Dollar / Yen (test)" in response.data  # typed values kept


def test_token_is_the_same_across_pages_in_one_session(seeded_client):
    assert csrf_token(seeded_client, "/trades/new") == csrf_token(seeded_client, "/instruments")
```

## 5. Deleted

### `pytest.ini` (DELETED)

Its three settings (`testpaths`, `pythonpath`, `addopts`) now live in `[tool.pytest.ini_options]` in `pyproject.toml`, so the project has one configuration file.
