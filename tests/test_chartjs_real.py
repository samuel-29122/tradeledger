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

Since Phase 8.5 the file ships with TradeLedger (built from the official
v4.5.1 source tag; see static/vendor/chartjs/BUILD.md), so these tests run
by default. CI also runs them against the official npm file.

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
from tests.factories import USER_ID
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
        user_id=USER_ID,
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
    page = page_factory(width, 900, chart_js="real", server=real_server)
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


def test_axis_labels_have_no_floating_point_noise(real_server, page_factory):
    """Phase 8.5 regression: the drawdown axis read "-0.20000000000000002R".
    Every R-axis label is now a short, rounded number."""
    page, _ = open_dashboard(page_factory, real_server)
    for name in CHART_NAMES:
        labels = chart_eval(
            page,
            name,
            "const axes = Object.values(chart.scales).filter(s => s.options.title && "
            "/risk multiples/.test(s.options.title.text)); "
            "return axes.flatMap(s => s.ticks.map(t => String(t.label)));",
        )
        assert labels, name
        for label in labels:
            assert label.endswith("R") and len(label) <= 8, (name, label)
            assert label != "-0R", (name, label)


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
