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
    page = page_factory(server=live_server)

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
    page = page_factory(server=live_server)
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
    page = page_factory(server=live_server)
    page.goto(live_server + path)
    assert page.evaluate(UNLABELLED_CONTROLS) == []


@pytest.mark.parametrize("path", PAGES)
def test_headings_form_an_outline(live_server, page_factory, path):
    page = page_factory(server=live_server)
    page.goto(live_server + path)
    assert page.evaluate(HEADING_PROBLEMS) == []
    assert page.evaluate("document.documentElement.lang") == "en-GB"


@pytest.mark.parametrize("path", ["/", "/trades", "/instruments", "/analytics"])
def test_tables_have_names_and_headers(live_server, page_factory, path):
    page = page_factory(server=live_server)
    page.goto(live_server + path)
    assert page.evaluate(TABLE_PROBLEMS) == []


def test_accessibility_checks_can_fail(live_server, page_factory):
    """Negative control: break the page on purpose and prove each check
    notices, so an empty result above really means "no problems found"."""
    page = page_factory(server=live_server)
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
    page = page_factory(server=live_server)
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
    page = page_factory(server=live_server)
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
    page = page_factory(375, 800, reduced_motion="reduce", server=live_server)
    page.goto(live_server + "/")
    duration = page.evaluate("getComputedStyle(document.querySelector('#sidebar')).transitionDuration")
    assert all(float(part.rstrip("s")) <= 0.001 for part in duration.split(","))


def test_outcomes_are_not_shown_by_colour_alone(live_server, page_factory):
    page = page_factory(server=live_server)
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
    page = page_factory(width, 450, device_scale_factor=2, server=live_server)
    for path in PAGES:
        page.goto(live_server + path)
        assert horizontal_overflow(page) == 0, path


def test_text_stays_readable_on_a_phone(live_server, page_factory):
    page = page_factory(375, 800, server=live_server)
    page.goto(live_server + "/trades/1")
    size = page.evaluate("parseFloat(getComputedStyle(document.body).fontSize)")
    assert size >= 16
