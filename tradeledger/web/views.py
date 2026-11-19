"""Overview, About and the health checks. (The analytics dashboard is in
analytics.py.)

Every number shown comes from the repositories (counts) or the Phase 3
analytics engine (performance), for the signed-in user only. Nothing is
hard-coded.
"""

import logging

from flask import current_app, jsonify, render_template

from ..db import get_db, ping
from ..performance import build_report
from ..repositories import trades as trade_repo
from ..repositories.instruments import list_instruments
from . import main_bp
from .auth import current_user_id
from .trades import with_results

log = logging.getLogger("tradeledger.health")

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
    {"phase": 8, "title": "Accounts, PostgreSQL, Docker & AWS design", "status": "Done"},
    {"phase": 9, "title": "Next phase (to be agreed)", "status": "Next"},
]


@main_bp.route("/")
def index():
    """Overview of the signed-in user's journal."""
    conn = get_db()
    user_id = current_user_id()
    report = build_report(conn, user_id=user_id)
    return render_template(
        "index.html",
        open_count=trade_repo.count_trades(conn, user_id=user_id, status="OPEN"),
        closed_count=trade_repo.count_trades(conn, user_id=user_id, status="CLOSED"),
        instrument_count=len(list_instruments(conn, user_id=user_id)),
        overall=report.overall,
        unknown_close_count=len(report.unknown_close_time_trade_ids),
        recent=with_results(trade_repo.list_trades(conn, user_id=user_id, limit=RECENT_TRADES)),
    )


@main_bp.route("/about")
def about():
    return render_template("about.html", roadmap=ROADMAP)


@main_bp.route("/health")
def health():
    """Liveness check (kept from Phase 1): the process answers HTTP.
    Returns JSON only - never secrets, hostnames or database details."""
    return jsonify(
        status="ok",
        app=current_app.config["APP_NAME"],
        version=current_app.config["APP_VERSION"],
    )


@main_bp.route("/health/live")
def health_live():
    """Liveness: is the process alive? Never touches the database, so a
    database outage does not make the orchestrator restart healthy
    containers in a loop. Docker's HEALTHCHECK uses this."""
    return jsonify(status="ok")


@main_bp.route("/health/ready")
def health_ready():
    """Readiness: can this instance serve requests right now? Checks the
    database with SELECT 1. The load balancer uses this, so traffic only
    goes to containers that can reach the database. On failure the reason
    is logged on the server; the response says only "unavailable"."""
    try:
        ready = ping(get_db())
    except Exception:
        log.exception("readiness check failed", extra={"event": "health.ready.failed"})
        ready = False
    if not ready:
        return jsonify(status="unavailable"), 503
    return jsonify(status="ready")
