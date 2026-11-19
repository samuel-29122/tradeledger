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
from .auth import current_user_id

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
    user_id = current_user_id()
    filters, errors = parse_report_filters(request.args, conn, user_id=user_id, blank_means_unset=True)
    raw = {name: request.args.get(name, "").strip() for name in FILTER_NAMES}

    dashboard = payload = None
    if not errors:
        dashboard = build_dashboard(conn, filters, user_id=user_id)
        payload = chart_payload(dashboard)
        if dashboard.has_results:  # charts (and so Chart.js) only when there is data
            g.csp_script_sources = chart_script_sources(current_app.config["CHART_JS_URL"])

    strategies = list_strategies(conn, user_id=user_id)
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
        instruments=list_instruments(conn, user_id=user_id),
        strategies=strategies,
        session_options=[(key, SESSION_LABELS[key]) for key in SESSIONS],
        small_sample=SMALL_SAMPLE,
        chart_js={
            "url": current_app.config["CHART_JS_URL"],
            "integrity": current_app.config["CHART_JS_INTEGRITY"],
            "version": current_app.config["CHART_JS_VERSION"],
        },
    ), (400 if errors else 200)
