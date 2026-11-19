"""Helpers for the Phase 5 website tests.

The website protects every POST with a CSRF token (tradeledger/web/csrf.py).
A real browser receives the token in a hidden form field and sends it back.
These helpers do the same thing with Flask's test client: load a page,
read the token out of the HTML, and include it in the POST.
"""

import re
from decimal import Decimal

from tradeledger.db import get_db

TOKEN_PATTERN = re.compile(rb'name="csrf_token" value="([^"]+)"')


def csrf_token(client, page="/trades/new"):
    """GET a page that contains a form and return its CSRF token."""
    response = client.get(page)
    match = TOKEN_PATTERN.search(response.data)
    assert match, f"no CSRF token found on {page}"
    return match.group(1).decode()


def post_form(client, url, data, *, token_page="/trades/new", follow_redirects=False):
    """POST form data the way a browser would, including a valid token."""
    body = {"csrf_token": csrf_token(client, token_page), **data}
    return client.post(url, data=body, follow_redirects=follow_redirects)


def closed_trade_form(**changes):
    """A valid "Add trade" submission for sample instrument 1 (EURUSD).

    Values use the browser's datetime-local format (no seconds, no "Z"),
    which is exactly what the form sends. Same prices as sample trade 1:
    net +393 USD, realised R +1.965 (shown as +1.97R).
    """
    form = {
        "instrument_id": "1",
        "direction": "LONG",
        "status": "CLOSED",
        "trade_date": "2026-04-01T08:15",
        "closed_at": "2026-04-01T11:40",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Website test trade (synthetic)",
    }
    form.update(changes)
    return form


def open_trade_form(**changes):
    """A valid OPEN trade: no exit price, no exit time."""
    return closed_trade_form(status="OPEN", exit_price="", closed_at="", **changes)


def count_rows(app, table):
    with app.app_context():
        return get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def fetch_trade(app, trade_id):
    """The stored row, with exact decimals as text: SQLite stores TEXT and
    PostgreSQL returns NUMERIC as Decimal; str() gives the same digits."""
    with app.app_context():
        row = get_db().execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            return None
        return {key: str(value) if isinstance(value, Decimal) else value for key, value in dict(row).items()}


def new_trade_id(response):
    """The id in a redirect such as /trades/17."""
    match = re.search(r"/trades/(\d+)$", response.headers["Location"])
    assert match, response.headers.get("Location")
    return int(match.group(1))
