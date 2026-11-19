"""Phase 6: the /analytics dashboard page (Flask test client, not a browser).

Covers rendering, filters, validation, empty states, legacy data, the chart
payload, agreement with the JSON API, escaping and the Content-Security-
Policy. Real-browser behaviour (Chart.js, layout) is in test_browser.py.
"""

import json
import re
from decimal import Decimal

import pytest

from tests.conftest import ensure_user, sign_in
from tests.factories import USER_ID
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
        expected = chart_payload(
            build_dashboard(get_db(), ReportFilters(strategy="Breakout"), user_id=USER_ID)
        )
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
            "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, contract_multiplier,"
            " quote_currency) VALUES (1, 'OPN', 'Open only', 'OTHER', '0.01', '1', 'USD')"
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
            user_id=USER_ID,
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
        seed_sample_data(get_db(), user_id=ensure_user(get_db()))
    client = app.test_client()
    sign_in(client)
    response = client.get("/analytics")
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


def test_dashboard_requires_sign_in_in_production(db_path):
    # Phase 7 checked a 403 here (journal disabled); Phase 8 requires sign-in.
    # Either way no dashboard and no Chart.js permission for anonymous visitors.
    app = create_app("production", overrides={"SECRET_KEY": "k" * 64, "DATABASE": db_path})
    with app.app_context():
        init_schema(get_db())
    response = app.test_client().get("/analytics", base_url="https://localhost")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    assert "cdn.jsdelivr.net" not in response.headers["Content-Security-Policy"]


def test_post_is_not_allowed(seeded_client):
    token_page = seeded_client.get("/trades/new").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', token_page).group(1)
    assert seeded_client.post("/analytics", data={"csrf_token": token}).status_code == 405


def test_navigation_marks_analytics_current(seeded_client):
    _, html = page(seeded_client)
    marker = html.index('aria-current="page"')
    assert "Analytics" in html[marker : marker + 120]
