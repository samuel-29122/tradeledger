# Phase 5 — Complete Code

Every file added or changed in Phase 5, in full, generated from the project files. `tradeledger/routes.py` was deleted (replaced by `tradeledger/web/`). Unchanged files from Phases 1–4 are in the earlier code documents.

## Contents

1. `tradeledger/__init__.py`
2. `tradeledger/config.py`
3. `tradeledger/repositories/trades.py`
4. `tradeledger/web/__init__.py`
5. `tradeledger/web/csrf.py`
6. `tradeledger/web/filters.py`
7. `tradeledger/web/forms.py`
8. `tradeledger/web/guard.py`
9. `tradeledger/web/instruments.py`
10. `tradeledger/web/trades.py`
11. `tradeledger/web/views.py`
12. `tradeledger/templates/base.html`
13. `tradeledger/templates/macros/ui.html`
14. `tradeledger/templates/macros/forms.html`
15. `tradeledger/templates/index.html`
16. `tradeledger/templates/home_disabled.html`
17. `tradeledger/templates/trades/journal.html`
18. `tradeledger/templates/trades/form.html`
19. `tradeledger/templates/trades/detail.html`
20. `tradeledger/templates/trades/confirm_delete.html`
21. `tradeledger/templates/instruments/index.html`
22. `tradeledger/templates/analytics.html`
23. `tradeledger/templates/about.html`
24. `tradeledger/templates/errors/400.html`
25. `tradeledger/templates/errors/403.html`
26. `tradeledger/templates/errors/404.html`
27. `tradeledger/templates/errors/500.html`
28. `tradeledger/static/css/style.css`
29. `tradeledger/static/css/base.css`
30. `tradeledger/static/css/layout.css`
31. `tradeledger/static/css/components.css`
32. `tradeledger/static/css/pages.css`
33. `tradeledger/static/js/early.js`
34. `tradeledger/static/js/app.js`
35. `tradeledger/static/js/trade-form.js`
36. `tests/web_helpers.py`
37. `tests/test_web_pages.py`
38. `tests/test_web_forms.py`
39. `tests/test_web_security.py`
40. `tests/test_web_integration.py`
41. `tests/test_browser.py`
42. `tests/test_app.py`
43. `tests/test_openapi.py`


## 1. `tradeledger/__init__.py`

```python
"""TradeLedger application package.

Contains the application factory, create_app(). Flask's command-line tool
finds this function automatically when you run:  flask --app tradeledger run
"""

import logging
import os
import secrets

from flask import Flask, render_template
from werkzeug.exceptions import HTTPException

from .config import CONFIG_BY_NAME


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
    """Make sure a SECRET_KEY exists, or fail loudly where one is required."""
    if app.config.get("SECRET_KEY"):
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


def _register_security_headers(app):
    """Add protective HTTP headers to every response."""

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return response
```


## 2. `tradeledger/config.py`

```python
"""Application configuration.

Settings are read from environment variables so that secrets never live in
source code. Each class represents one environment the app can run in.
"""

import os


def env_flag(name):
    """True only when the environment variable is exactly "1"."""
    return os.environ.get(name) == "1"


class Config:
    """Settings shared by every environment."""

    APP_NAME = "TradeLedger"
    APP_VERSION = "0.5.0"

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


class DevelopmentConfig(Config):
    """Local development on your own machine."""

    # In development a missing key is tolerated: the app generates a
    # temporary one and logs a warning (see create_app).
    REQUIRE_SECRET_KEY = False


class TestingConfig(Config):
    """Used by pytest. Fixed values make tests repeatable."""

    TESTING = True
    SECRET_KEY = "test-secret-key-not-for-real-use"
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


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
```


## 3. `tradeledger/repositories/trades.py`

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

from ..validation import ValidationError, date_range_bounds, validate_trade

_DECIMAL_FIELDS = (
    "entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees",
    "contract_multiplier",
)

# The ONLY columns a caller can write. Anything else in the input is ignored
# by validate_trade() and never reaches SQL (protection against mass assignment).
_WRITE_FIELDS = (
    "instrument_id", "trade_date", "closed_at", "direction", "entry_price", "stop_loss",
    "take_profit", "exit_price", "position_size", "fees", "status",
    "strategy", "session", "notes",
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
        raise ValidationError({"closed_at": "is not consistent with the trade's status or entry time"}) from exc
    raise exc


MAX_SEARCH_LENGTH = 100


def _like_pattern(text):
    """Turn user text into a LIKE pattern that matches it literally.
    % and _ are LIKE wildcards, so they (and the escape character itself)
    are escaped; otherwise searching "50%" would match everything."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(instrument_id, start, end, status, date_field, strategy=None, search=None):
    """Build a WHERE clause from fixed fragments plus a parameter list.
    User values never become part of the SQL text; they go in `params`.

    strategy  exact match on the strategy label
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
                f"INSERT INTO trades ({', '.join(_WRITE_FIELDS)}) VALUES ({placeholders})",
                tuple(clean[field] for field in _WRITE_FIELDS),
            )
    except sqlite3.IntegrityError as exc:
        _raise_friendly(exc)
    return cursor.lastrowid


def get_trade(conn, trade_id):
    row = conn.execute(_SELECT + " WHERE t.id = ?", (trade_id,)).fetchone()
    return _to_dict(row)


def list_trades(conn, *, instrument_id=None, start=None, end=None, status=None,
                date_field="entry", strategy=None, search=None, limit=None, offset=0):
    """List trades, optionally filtered and paginated.

    instrument_id  only this instrument
    start, end     inclusive dates 'YYYY-MM-DD' (UTC)
    date_field     'entry' (trade_date, the default) or 'realised'
    status         'OPEN' or 'CLOSED'
    strategy       exact strategy label
    search         text found in the symbol, strategy or notes
    limit, offset  pagination; limit=None returns every match

    Order is always newest trade_date first, then highest id first, so
    pages never overlap or skip rows between requests.
    """
    where, params = _where(instrument_id, start, end, status, date_field, strategy, search)
    sql = _SELECT + where + " ORDER BY t.trade_date DESC, t.id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, int(limit), int(offset)]
    return [_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def count_trades(conn, *, instrument_id=None, start=None, end=None, status=None,
                 date_field="entry", strategy=None, search=None):
    where, params = _where(instrument_id, start, end, status, date_field, strategy, search)
    # The JOIN is needed because search can match the instrument symbol.
    sql = "SELECT COUNT(*) FROM trades AS t JOIN instruments AS i ON i.id = t.instrument_id" + where
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
    existing = conn.execute(
        "SELECT status, closed_at FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    if existing is None:
        return False
    is_legacy = existing["status"] == "CLOSED" and existing["closed_at"] is None
    clean = validate_trade(data, require_closed_at=not is_legacy)

    assignments = ", ".join(f"{field} = ?" for field in _WRITE_FIELDS)
    try:
        with conn:
            cursor = conn.execute(
                f"""
                UPDATE trades
                SET {assignments},
                    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                WHERE id = ?
                """,
                (*(clean[field] for field in _WRITE_FIELDS), trade_id),
            )
    except sqlite3.IntegrityError as exc:
        _raise_friendly(exc)
    return cursor.rowcount == 1


def delete_trade(conn, trade_id):
    with conn:
        cursor = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    return cursor.rowcount == 1
```


## 4. `tradeledger/web/__init__.py`

```python
"""The TradeLedger website: server-rendered pages and HTML forms.

Blueprint "main" (the same name Phase 1 used, so url_for("main.index") and
url_for("main.health") keep working). Modules:

  guard.py        refuses journal pages when JOURNAL_ENABLED is off
  csrf.py         CSRF tokens for every state-changing form
  filters.py      Jinja filters that format exact Decimals for display
  forms.py        turning request.form into input for validation.py
  views.py        overview, about, analytics preview, health
  trades.py       journal, add, details, edit, delete
  instruments.py  instrument list and add form

Every write goes through the same repository and validation functions as
the JSON API, so there is one set of business rules, not two.
"""

from flask import Blueprint

main_bp = Blueprint("main", __name__)

# Order matters: the guard runs before CSRF checks (both are before_request).
from . import guard, csrf, filters, views, trades, instruments  # noqa: E402,F401
```


## 5. `tradeledger/web/csrf.py`

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


@main_bp.before_request
def check_csrf_token():
    if request.method not in STATE_CHANGING:
        return None
    if token_is_valid(request.form.get(FIELD_NAME)):
        return None
    return render_template("errors/400.html", reason="csrf"), 400


@main_bp.app_context_processor
def inject_csrf_helpers():
    def csrf_field():
        return Markup('<input type="hidden" name="%s" value="%s">') % (
            FIELD_NAME, escape(get_token()))
    return {"csrf_token": get_token, "csrf_field": csrf_field}
```


## 6. `tradeledger/web/filters.py`

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

from datetime import datetime

from ..performance.formatting import (
    NOT_AVAILABLE,
    decimal_to_text,
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
)
from . import main_bp

STORED_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _parse(timestamp):
    try:
        return datetime.strptime(timestamp, STORED_FORMAT)
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


## 7. `tradeledger/web/forms.py`

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
    "instrument_id", "direction", "status", "trade_date", "entry_price",
    "stop_loss", "take_profit", "position_size", "fees",
    "exit_price", "closed_at", "strategy", "session", "notes",
)

INSTRUMENT_FORM_FIELDS = (
    "symbol", "name", "asset_class", "price_increment", "contract_multiplier", "quote_currency",
)

# Fields that have no meaning for an OPEN trade. The browser disables them
# (so they are not sent); the server also blanks them if they arrive anyway.
EXIT_FIELDS = ("exit_price", "closed_at")

BLANK_TRADE_FORM = {
    "instrument_id": "", "direction": "LONG", "status": "CLOSED", "trade_date": "",
    "entry_price": "", "stop_loss": "", "take_profit": "", "position_size": "",
    "fees": "0", "exit_price": "", "closed_at": "", "strategy": "", "session": "", "notes": "",
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


## 8. `tradeledger/web/guard.py`

```python
"""Keep unauthenticated trade management off public servers.

TradeLedger has no login. In development and tests the journal is
available; in production JOURNAL_ENABLED defaults to False, so every page
that reads or changes trading data answers 403. /health, /about and
static files stay available, and the home page (main.index) renders a
short explanation with NO trading data (see views.index).
"""

from flask import current_app, render_template, request

from . import main_bp

ALWAYS_AVAILABLE = {"main.health", "main.about", "main.index", "static"}


@main_bp.before_request
def refuse_when_disabled():
    if current_app.config["JOURNAL_ENABLED"] or request.endpoint in ALWAYS_AVAILABLE:
        return None
    return render_template("errors/403.html"), 403
```


## 9. `tradeledger/web/instruments.py`

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

BLANK_INSTRUMENT_FORM = {name: "" for name in INSTRUMENT_FORM_FIELDS}

INSTRUMENT_LABELS = {
    "symbol": "Symbol", "name": "Name", "asset_class": "Asset class",
    "price_increment": "Price increment", "contract_multiplier": "Contract multiplier",
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


## 10. `tradeledger/web/trades.py`

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
    "instrument_id": "Instrument", "direction": "Direction", "status": "Status",
    "trade_date": "Entry time", "entry_price": "Entry price", "stop_loss": "Stop-loss",
    "take_profit": "Take-profit", "position_size": "Position size", "fees": "Fees",
    "exit_price": "Exit price", "closed_at": "Exit time", "strategy": "Strategy",
    "session": "Session", "notes": "Notes",
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
    return {
        "initial_risk": initial_risk(*args, trade["position_size"], trade["contract_multiplier"]),
        "planned_rr": planned_rr(*args, trade["take_profit"]),
    }


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
            abort(404)          # e.g. an old bookmark to ?page=9 after deletions
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
        except ValidationError as exc:      # e.g. the instrument was deleted meanwhile
            errors = exc.errors
    if errors:
        return render_template("trades/form.html",
                               **form_context(conn, values, errors, mode="new")), 400

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
        return render_template("trades/form.html", **form_context(
            conn, trade_to_form(trade), {}, mode="edit", trade=trade, is_legacy=is_legacy, back=back))

    values = read_form(request.form, TRADE_FORM_FIELDS)
    data = trade_input(values)
    errors = collect_errors(values, data, require_closed_at=not is_legacy)
    if not errors:
        try:
            trade_repo.update_trade(conn, trade_id, data)
        except ValidationError as exc:
            errors = exc.errors
    if errors:
        return render_template("trades/form.html", **form_context(
            conn, values, errors, mode="edit", trade=trade, is_legacy=is_legacy, back=back)), 400

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
        return render_template("trades/confirm_delete.html", trade=trade,
                               result=result_for(trade), back=back)

    trade_repo.delete_trade(conn, trade_id)
    flash(f"Trade #{trade_id} ({trade['symbol']} {trade['direction']}) was deleted.", "success")
    return redirect(back)
```


## 11. `tradeledger/web/views.py`

```python
"""Overview, About, Analytics preview and the health check.

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
    {"phase": 6, "title": "Analytics dashboard", "status": "Next"},
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


@main_bp.route("/analytics")
def analytics_preview():
    """A small, clearly labelled preview built from the real engine.
    The full dashboard with charts is Phase 6."""
    report = build_report(get_db())
    return render_template("analytics.html", report=report)


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


## 12. `tradeledger/templates/base.html`

```html
<!DOCTYPE html>
<html lang="en-GB">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="dark">
    <title>{% block title %}TradeLedger{% endblock %} · TradeLedger</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    {# early.js only adds class="js" to <html>, before the page paints, so
       JavaScript-dependent styles apply without a flash of the no-JS layout. #}
    <script src="{{ url_for('static', filename='js/early.js') }}"></script>
    <script src="{{ url_for('static', filename='js/app.js') }}" defer></script>
    {% block scripts %}{% endblock %}
</head>
<body>
    <a class="skip-link" href="#main">Skip to main content</a>

    {% set endpoint = request.endpoint or "" %}
    {% set nav_items = [
        ("main.index", "Overview", ["main.index"]),
        ("main.journal", "Trade journal", ["main.journal", "main.trade_detail", "main.edit_trade", "main.delete_trade"]),
        ("main.new_trade", "Add trade", ["main.new_trade"]),
        ("main.instruments", "Instruments", ["main.instruments"]),
        ("main.analytics_preview", "Analytics", ["main.analytics_preview"]),
        ("main.about", "About", ["main.about"]),
    ] %}

    <div class="shell">
        <aside class="sidebar" id="sidebar" data-sidebar aria-label="Main navigation">
            <div class="sidebar__top">
                <a class="brand" href="{{ url_for('main.index') }}">
                    <span class="brand__mark" aria-hidden="true">TL</span>
                    <span class="brand__text">
                        <span class="brand__name">TradeLedger</span>
                        <span class="brand__tag">Trading journal</span>
                    </span>
                </a>
                <button class="icon-button sidebar__close" type="button" data-nav-close>
                    <span aria-hidden="true">&times;</span>
                    <span class="visually-hidden">Close menu</span>
                </button>
            </div>
            <nav>
                <ul class="nav" role="list">
                    {% for target, label, matches in nav_items %}
                    <li>
                        <a class="nav__link" href="{{ url_for(target) }}"
                           {% if endpoint in matches %}aria-current="page"{% endif %}>
                            <span>{{ label }}</span>
                            {% if target == "main.analytics_preview" %}<span class="nav__tag">Preview</span>{% endif %}
                        </a>
                    </li>
                    {% endfor %}
                </ul>
            </nav>
            <div class="sidebar__footer">
                <span class="env-badge">{{ config.CONFIG_NAME }} mode</span>
                <p>Local, single-user journal. There is no login, so keep it on your own machine.</p>
            </div>
        </aside>
        <div class="nav-backdrop" data-nav-backdrop hidden></div>

        <div class="main-area">
            <header class="topbar">
                <button class="icon-button nav-toggle" type="button" data-nav-toggle
                        aria-controls="sidebar" aria-expanded="false">
                    <span class="nav-toggle__bars" aria-hidden="true"></span>
                    <span>Menu</span>
                </button>
                <div class="topbar__titles">
                    <h1 class="topbar__title">{% block heading %}TradeLedger{% endblock %}</h1>
                    {% set description %}{% block description %}{% endblock %}{% endset %}
                    {% if description | trim %}<p class="topbar__description">{{ description }}</p>{% endif %}
                </div>
                <div class="topbar__actions">{% block actions %}{% endblock %}</div>
            </header>

            {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
            <div class="flash-stack" role="status" aria-live="polite">
                {% for category, message in messages %}
                <div class="flash flash--{{ category }}" data-flash>
                    <p>{{ message }}</p>
                    <button class="flash__close" type="button" data-flash-close>
                        <span aria-hidden="true">&times;</span>
                        <span class="visually-hidden">Dismiss message</span>
                    </button>
                </div>
                {% endfor %}
            </div>
            {% endif %}
            {% endwith %}

            <main id="main" class="content" tabindex="-1">
                {% block content %}{% endblock %}
            </main>

            <footer class="site-footer">
                TradeLedger v{{ config.APP_VERSION }} · Exact decimal calculations · Times shown in UTC
            </footer>
        </div>
    </div>
</body>
</html>
```


## 13. `tradeledger/templates/macros/ui.html`

```html
{# Small reusable pieces of interface. Import with:
   {% import "macros/ui.html" as ui %} #}

{# OPEN, WIN, LOSS, BREAKEVEN or CLOSED: always a word, never colour alone. #}
{% macro outcome_badge(trade, result) -%}
    {%- if trade.status == "OPEN" -%}
        <span class="badge badge--open">Open</span>
    {%- elif result -%}
        <span class="badge badge--{{ result.outcome | lower }}">{{ result.outcome | title }}</span>
    {%- else -%}
        <span class="badge">Closed</span>
    {%- endif -%}
{%- endmacro %}

{% macro direction_badge(direction) -%}
    <span class="direction direction--{{ direction | lower }}">{{ direction | title }}</span>
{%- endmacro %}

{# Signed value with a class for colour; the sign and text carry the meaning. #}
{% macro signed(value, text) -%}
    {%- if value is none -%}<span class="muted">n/a</span>
    {%- elif value > 0 -%}<span class="num num--positive">{{ text }}</span>
    {%- elif value < 0 -%}<span class="num num--negative">{{ text }}</span>
    {%- else -%}<span class="num">{{ text }}</span>
    {%- endif -%}
{%- endmacro %}

{% macro empty_state(title, text, action_url=None, action_label=None) -%}
    <div class="empty-state">
        <h2 class="empty-state__title">{{ title }}</h2>
        <p>{{ text }}</p>
        {% if action_url %}<a class="button button--primary" href="{{ action_url }}">{{ action_label }}</a>{% endif %}
    </div>
{%- endmacro %}

{# Previous / page x of y / next, keeping the active filters. #}
{% macro pagination(page, total_pages, total, endpoint, params) -%}
    {% if total_pages > 1 %}
    <nav class="pagination" aria-label="Pagination">
        {% if page > 1 %}
            <a class="button button--secondary" rel="prev"
               href="{{ url_for(endpoint, page=page - 1, **params) }}">Previous<span class="visually-hidden"> page</span></a>
        {% else %}
            <span class="button button--secondary is-disabled" aria-disabled="true">Previous</span>
        {% endif %}
        <span class="pagination__status">Page {{ page }} of {{ total_pages }}</span>
        {% if page < total_pages %}
            <a class="button button--secondary" rel="next"
               href="{{ url_for(endpoint, page=page + 1, **params) }}">Next<span class="visually-hidden"> page</span></a>
        {% else %}
            <span class="button button--secondary is-disabled" aria-disabled="true">Next</span>
        {% endif %}
    </nav>
    {% endif %}
{%- endmacro %}
```


## 14. `tradeledger/templates/macros/forms.html`

```html
{# Accessible form fields. Every field:
     * has a visible <label> tied to the input by id;
     * links its hint and error with aria-describedby, so screen readers
       read them when the field is focused;
     * sets aria-invalid="true" when it has an error;
     * shows the error text next to the field AND in the summary at the top.
   Values always come from `values`, so a failed submission keeps what the
   user typed. Jinja escapes every value automatically. #}

{% macro describedby(name, hint, errors) -%}
    {%- set ids = [] -%}
    {%- if hint %}{% set _ = ids.append("field-" ~ name ~ "-hint") %}{% endif -%}
    {%- if errors.get(name) %}{% set _ = ids.append("field-" ~ name ~ "-error") %}{% endif -%}
    {%- if ids %} aria-describedby="{{ ids | join(' ') }}"{% endif -%}
    {%- if errors.get(name) %} aria-invalid="true"{% endif -%}
{%- endmacro %}

{% macro messages(name, hint, errors, labels) -%}
    {% if hint %}<p class="field__hint" id="field-{{ name }}-hint">{{ hint }}</p>{% endif %}
    {% if errors.get(name) %}
        <p class="field__error" id="field-{{ name }}-error">
            <span class="visually-hidden">Error:</span> {{ labels.get(name, name) }} {{ errors[name] }}
        </p>
    {% endif %}
{%- endmacro %}

{% macro text(name, label, values, errors, labels, hint=None, type="text", inputmode=None,
              required=False, placeholder=None, maxlength=None, list=None, step=None, wide=False) -%}
    <div class="field{% if errors.get(name) %} field--error{% endif %}{% if wide %} field--wide{% endif %}">
        <label class="field__label" for="field-{{ name }}">
            {{ label }}{% if not required %} <span class="field__optional">(optional)</span>{% endif %}
        </label>
        <input class="input" id="field-{{ name }}" name="{{ name }}" type="{{ type }}"
               value="{{ values.get(name, '') }}"
               {%- if inputmode %} inputmode="{{ inputmode }}"{% endif %}
               {%- if placeholder %} placeholder="{{ placeholder }}"{% endif %}
               {%- if maxlength %} maxlength="{{ maxlength }}"{% endif %}
               {%- if list %} list="{{ list }}"{% endif %}
               {%- if step %} step="{{ step }}"{% endif %}
               {%- if required %} required{% endif %}
               {%- if inputmode == "decimal" %} autocomplete="off" spellcheck="false"{% endif %}
               {{- describedby(name, hint, errors) }}>
        {{ messages(name, hint, errors, labels) }}
    </div>
{%- endmacro %}

{% macro select(name, label, values, errors, labels, options, hint=None, required=False, blank=None) -%}
    <div class="field{% if errors.get(name) %} field--error{% endif %}">
        <label class="field__label" for="field-{{ name }}">
            {{ label }}{% if not required %} <span class="field__optional">(optional)</span>{% endif %}
        </label>
        <select class="input select" id="field-{{ name }}" name="{{ name }}"
                {%- if required %} required{% endif %}{{ describedby(name, hint, errors) }}>
            {% if blank is not none %}<option value="">{{ blank }}</option>{% endif %}
            {% for value, text in options %}
            <option value="{{ value }}" {% if values.get(name, '') | string == value | string %}selected{% endif %}>{{ text }}</option>
            {% endfor %}
        </select>
        {{ messages(name, hint, errors, labels) }}
    </div>
{%- endmacro %}

{# A group of radio buttons styled as a segmented control. #}
{% macro choice(name, legend, values, errors, labels, options, hint=None, data_attr=None) -%}
    <fieldset class="field choice{% if errors.get(name) %} field--error{% endif %}"
              {%- if data_attr %} {{ data_attr }}{% endif %}{{ describedby(name, hint, errors) }}>
        <legend class="field__label">{{ legend }}</legend>
        <div class="choice__options">
            {% for value, text in options %}
            <label class="choice__option">
                <input type="radio" name="{{ name }}" value="{{ value }}"
                       id="field-{{ name }}{% if not loop.first %}-{{ value | lower }}{% endif %}"
                       {% if values.get(name) == value %}checked{% endif %}>
                <span>{{ text }}</span>
            </label>
            {% endfor %}
        </div>
        {{ messages(name, hint, errors, labels) }}
    </fieldset>
{%- endmacro %}

{% macro textarea(name, label, values, errors, labels, hint=None, maxlength=None, rows=5) -%}
    <div class="field field--wide{% if errors.get(name) %} field--error{% endif %}">
        <label class="field__label" for="field-{{ name }}">{{ label }} <span class="field__optional">(optional)</span></label>
        <textarea class="input textarea" id="field-{{ name }}" name="{{ name }}" rows="{{ rows }}"
                  {%- if maxlength %} maxlength="{{ maxlength }}"{% endif %}
                  {{- describedby(name, hint, errors) }}>{{ values.get(name, '') }}</textarea>
        {{ messages(name, hint, errors, labels) }}
    </div>
{%- endmacro %}

{# List of every problem, linked to the fields. Focused by app.js on load. #}
{% macro error_summary(errors, labels) -%}
    {% if errors %}
    <div class="error-summary" role="alert" tabindex="-1" data-error-summary aria-labelledby="error-summary-title">
        <h2 class="error-summary__title" id="error-summary-title">
            {{ errors | length }} {{ "problem" if errors | length == 1 else "problems" }} to fix before saving
        </h2>
        <ul>
            {% for name, message in errors.items() %}
            <li><a href="#field-{{ name }}">{{ labels.get(name, name) }} {{ message }}</a></li>
            {% endfor %}
        </ul>
    </div>
    {% endif %}
{%- endmacro %}
```


## 15. `tradeledger/templates/index.html`

```html
{% extends "base.html" %}
{% import "macros/ui.html" as ui %}

{% block title %}Overview{% endblock %}
{% block heading %}Overview{% endblock %}
{% block description %}Your journal at a glance. Every figure is calculated from your recorded trades.{% endblock %}
{% block actions %}
    <a class="button button--secondary" href="{{ url_for('main.journal') }}">View journal</a>
    <a class="button button--primary" href="{{ url_for('main.new_trade') }}">Add trade</a>
{% endblock %}

{% block content %}
<section class="welcome card" aria-labelledby="welcome-title">
    <div>
        <h2 class="welcome__title" id="welcome-title">Welcome to TradeLedger</h2>
        <p class="welcome__text">
            Record each trade with its plan (entry, stop-loss, target) and its outcome. TradeLedger works out
            net P&amp;L after fees and the R-multiple of every closed trade, so you can see what your trading
            actually produces.
        </p>
    </div>
    <ul class="welcome__counts" role="list">
        <li><span class="welcome__count">{{ open_count }}</span> open {{ "trade" if open_count == 1 else "trades" }}</li>
        <li><span class="welcome__count">{{ closed_count }}</span> closed {{ "trade" if closed_count == 1 else "trades" }}</li>
        <li><span class="welcome__count">{{ instrument_count }}</span> {{ "instrument" if instrument_count == 1 else "instruments" }}</li>
    </ul>
</section>

<section aria-labelledby="indicators-title">
    <div class="section-heading">
        <h2 id="indicators-title">Realised performance</h2>
        <p class="muted">Closed trades only{% if overall.trades %}, {{ overall.trades }} in total{% endif %}. Open trades are not included.</p>
    </div>
    {% if overall.trades %}
    <div class="stat-grid">
        <div class="stat card">
            <p class="stat__label">Win rate</p>
            <p class="stat__value">{{ overall.win_rate | rate }}</p>
            <p class="stat__note">{{ overall.wins }} won · {{ overall.losses }} lost · {{ overall.breakevens }} breakeven</p>
        </div>
        <div class="stat card">
            <p class="stat__label">Expectancy per trade</p>
            <p class="stat__value">{{ ui.signed(overall.expectancy_r, overall.expectancy_r | r) }}</p>
            <p class="stat__note">Average realised R of {{ overall.r_trades }} trades</p>
        </div>
        <div class="stat card">
            <p class="stat__label">Total realised R</p>
            <p class="stat__value">{{ ui.signed(overall.total_r, overall.total_r | r) }}</p>
            <p class="stat__note">Sum of R-multiples (not account equity)</p>
        </div>
        <div class="stat card">
            <p class="stat__label">Profit factor (R)</p>
            <p class="stat__value">{{ overall.profit_factor_r | ratio }}</p>
            <p class="stat__note">{% if overall.profit_factor_r is none %}Needs at least one losing trade{% else %}Winning R ÷ losing R{% endif %}</p>
        </div>
    </div>
    {% if overall.money %}
    <div class="card money-strip">
        <h3 class="money-strip__title">Net P&amp;L by currency</h3>
        <ul class="money-strip__list" role="list">
            {% for currency, money in overall.money.items() %}
            <li>
                <span class="muted">{{ currency }}</span>
                {{ ui.signed(money.net_pnl, money.net_pnl | money(currency)) }}
                <span class="muted">({{ money.trades }} {{ "trade" if money.trades == 1 else "trades" }})</span>
            </li>
            {% endfor %}
        </ul>
        <p class="money-strip__note">Each currency is shown separately. Amounts in different currencies are never added together.</p>
    </div>
    {% endif %}
    {% if unknown_close_count %}
    <p class="notice notice--info">
        {{ unknown_close_count }} closed {{ "trade has" if unknown_close_count == 1 else "trades have" }} no recorded exit time
        (recorded before exit times were tracked). {{ "It is" if unknown_close_count == 1 else "They are" }} ordered by entry time.
    </p>
    {% endif %}
    {% else %}
    {{ ui.empty_state("No closed trades yet",
                      "Performance figures appear once you record a closed trade. Open trades are tracked but have no realised result.",
                      url_for('main.new_trade'), "Record a trade") }}
    {% endif %}
</section>

<section aria-labelledby="recent-title">
    <div class="section-heading">
        <h2 id="recent-title">Recent trades</h2>
        {% if recent %}<a class="text-link" href="{{ url_for('main.journal') }}">See all trades</a>{% endif %}
    </div>
    {% if recent %}
    <div class="card card--flush">
        <ul class="recent-list" role="list">
            {% for row in recent %}
            {% set t = row.trade %}{% set res = row.result %}
            <li class="recent-list__item">
                <a class="recent-list__link" href="{{ url_for('main.trade_detail', trade_id=t.id) }}">
                    <span class="recent-list__main">
                        <span class="recent-list__symbol">{{ t.symbol }}</span>
                        {{ ui.direction_badge(t.direction) }}
                        {{ ui.outcome_badge(t, res) }}
                    </span>
                    <span class="recent-list__meta">{{ t.trade_date | when }}</span>
                    <span class="recent-list__result">
                        {% if res %}{{ ui.signed(res.realised_r, res.realised_r | r) }}
                        {% else %}<span class="muted">No result yet</span>{% endif %}
                    </span>
                </a>
            </li>
            {% endfor %}
        </ul>
    </div>
    {% else %}
    {{ ui.empty_state("Your journal is empty",
                      "Add your first trade to start building a record you can learn from.",
                      url_for('main.new_trade'), "Add your first trade") }}
    {% endif %}
</section>
{% endblock %}
```


## 16. `tradeledger/templates/home_disabled.html`

```html
{% extends "base.html" %}

{% block title %}Journal unavailable{% endblock %}
{% block heading %}TradeLedger{% endblock %}
{% block description %}Trading journal and performance analytics{% endblock %}

{% block content %}
<section class="card prose" aria-labelledby="disabled-title">
    <h2 id="disabled-title">The journal is switched off on this server</h2>
    <p>TradeLedger has no login yet, so anyone who could reach this server could read and change every trade.
       For that reason the journal pages and the JSON API are disabled in the production configuration.</p>
    <p>Run TradeLedger on your own computer to use it, or enable it only behind your own access control.
       See the README for details.</p>
    <p><a class="text-link" href="{{ url_for('main.about') }}">About TradeLedger</a></p>
</section>
{% endblock %}
```


## 17. `tradeledger/templates/trades/journal.html`

```html
{% extends "base.html" %}
{% import "macros/ui.html" as ui %}

{% block title %}Trade journal{% endblock %}
{% block heading %}Trade journal{% endblock %}
{% block description %}Every recorded trade, newest entry first. Money is shown in each instrument's quote currency.{% endblock %}
{% block actions %}
    <a class="button button--primary" href="{{ url_for('main.new_trade') }}">Add trade</a>
{% endblock %}

{% block content %}
<form class="card filters" method="get" action="{{ url_for('main.journal') }}" role="search" aria-label="Filter trades">
    <div class="filters__grid">
        <div class="field field--wide">
            <label class="field__label" for="filter-q">Search</label>
            <input class="input" id="filter-q" name="q" type="search" value="{{ raw.q }}" maxlength="100"
                   aria-describedby="filter-q-hint{% if errors.q %} filter-q-error{% endif %}"
                   {% if errors.q %}aria-invalid="true"{% endif %}>
            <p class="field__hint" id="filter-q-hint">Searches the symbol, strategy and notes of every trade, not only this page.</p>
            {% if errors.q %}<p class="field__error" id="filter-q-error">{{ errors.q }}</p>{% endif %}
        </div>
        <div class="field">
            <label class="field__label" for="filter-instrument">Instrument</label>
            <select class="input select" id="filter-instrument" name="instrument_id"
                    {% if errors.instrument_id %}aria-invalid="true" aria-describedby="filter-instrument-error"{% endif %}>
                <option value="">All instruments</option>
                {% for i in instruments %}
                <option value="{{ i.id }}" {% if raw.instrument_id == i.id | string %}selected{% endif %}>{{ i.symbol }}</option>
                {% endfor %}
            </select>
            {% if errors.instrument_id %}<p class="field__error" id="filter-instrument-error">{{ errors.instrument_id }}</p>{% endif %}
        </div>
        <div class="field">
            <label class="field__label" for="filter-status">Status</label>
            <select class="input select" id="filter-status" name="status"
                    {% if errors.status %}aria-invalid="true" aria-describedby="filter-status-error"{% endif %}>
                <option value="">Any status</option>
                <option value="OPEN" {% if raw.status | upper == "OPEN" %}selected{% endif %}>Open</option>
                <option value="CLOSED" {% if raw.status | upper == "CLOSED" %}selected{% endif %}>Closed</option>
            </select>
            {% if errors.status %}<p class="field__error" id="filter-status-error">{{ errors.status }}</p>{% endif %}
        </div>
        <div class="field">
            <label class="field__label" for="filter-strategy">Strategy</label>
            <select class="input select" id="filter-strategy" name="strategy">
                <option value="">All strategies</option>
                {% for s in strategies %}
                <option value="{{ s }}" {% if raw.strategy == s %}selected{% endif %}>{{ s }}</option>
                {% endfor %}
            </select>
        </div>
        <div class="field">
            <label class="field__label" for="filter-start">Entry from</label>
            <input class="input" id="filter-start" name="start" type="date" value="{{ raw.start }}"
                   {% if errors.start %}aria-invalid="true" aria-describedby="filter-start-error"{% endif %}>
            {% if errors.start %}<p class="field__error" id="filter-start-error">{{ errors.start }}</p>{% endif %}
        </div>
        <div class="field">
            <label class="field__label" for="filter-end">Entry to</label>
            <input class="input" id="filter-end" name="end" type="date" value="{{ raw.end }}"
                   {% if errors.end %}aria-invalid="true" aria-describedby="filter-end-error"{% endif %}>
            {% if errors.end %}<p class="field__error" id="filter-end-error">{{ errors.end }}</p>{% endif %}
        </div>
    </div>
    <div class="filters__actions">
        <button class="button button--primary" type="submit">Apply filters</button>
        {% if active %}<a class="button button--ghost" href="{{ url_for('main.journal') }}">Reset filters</a>{% endif %}
    </div>
</form>

{% if errors %}
    <p class="notice notice--error" role="alert">The filters could not be applied. Correct the highlighted fields and try again.</p>
{% elif total == 0 and not active %}
    {{ ui.empty_state("No trades recorded yet",
                      "Your journal is empty. Add a trade to start tracking your performance.",
                      url_for('main.new_trade'), "Add your first trade") }}
{% elif total == 0 %}
    {{ ui.empty_state("No trades match these filters",
                      "Try a wider date range, another instrument or different search text.",
                      url_for('main.journal'), "Reset filters") }}
{% elif not rows %}
    {{ ui.empty_state("This page is empty",
                      "There are " ~ total ~ " matching trades, but none on page " ~ page ~ ".",
                      url_for('main.journal', **active), "Go to page 1") }}
{% else %}
    {% set first = (page - 1) * per_page + 1 %}
    {% set last = first + rows | length - 1 %}
    <p class="results-summary" role="status">
        Showing {{ first }}–{{ last }} of {{ total }} {{ "trade" if total == 1 else "trades" }}{% if active %} matching your filters{% endif %}.
    </p>

    <p class="table-scroll-hint" id="journal-scroll-hint">Scroll the table sideways to see every column.</p>
    <div class="table-wrap" role="region" aria-label="Trades table" aria-describedby="journal-scroll-hint" tabindex="0">
        <table class="table trade-table">
            <caption class="visually-hidden">Trades, newest entry first</caption>
            <thead>
                <tr>
                    <th scope="col">Entry time</th>
                    <th scope="col">Instrument</th>
                    <th scope="col">Status</th>
                    <th scope="col" class="num-col">Entry</th>
                    <th scope="col" class="num-col">Stop-loss</th>
                    <th scope="col" class="num-col">Exit</th>
                    <th scope="col" class="num-col">Size</th>
                    <th scope="col" class="num-col">Net P&amp;L / realised R</th>
                    <th scope="col">Strategy</th>
                    <th scope="col"><span class="visually-hidden">Actions</span></th>
                </tr>
            </thead>
            <tbody>
                {% for row in rows %}
                {% set t = row.trade %}{% set res = row.result %}
                <tr class="trade-row trade-row--{{ res.outcome | lower if res else t.status | lower }}">
                    <td data-label="Entry time">
                        <span class="cell-stack"><span>{{ t.trade_date | day }}</span><span class="cell-stack__sub">{{ t.trade_date | clock }}</span></span>
                    </td>
                    <td data-label="Instrument">
                        <span class="cell-stack">
                            <a class="trade-row__symbol" href="{{ url_for('main.trade_detail', trade_id=t.id) }}">{{ t.symbol }}</a>
                            {{ ui.direction_badge(t.direction) }}
                        </span>
                    </td>
                    <td data-label="Status">{{ ui.outcome_badge(t, res) }}</td>
                    <td data-label="Entry" class="num-col">{{ t.entry_price | plain }}</td>
                    <td data-label="Stop-loss" class="num-col">{{ t.stop_loss | plain }}</td>
                    <td data-label="Exit" class="num-col">{{ t.exit_price | plain }}</td>
                    <td data-label="Size" class="num-col">{{ t.position_size | plain }}</td>
                    <td data-label="Net P&amp;L / R" class="num-col">
                        {% if res %}
                        <span class="cell-stack cell-stack--end">
                            {{ ui.signed(res.net_pnl, res.net_pnl | money(t.quote_currency)) }}
                            <span class="cell-stack__sub">{{ ui.signed(res.realised_r, res.realised_r | r) }}</span>
                        </span>
                        {% else %}<span class="muted">No result (open)</span>{% endif %}
                    </td>
                    <td data-label="Strategy">{{ t.strategy or "—" }}</td>
                    <td class="actions-col">
                        <div class="row-actions">
                            <a class="button button--small button--secondary" href="{{ url_for('main.trade_detail', trade_id=t.id) }}">View<span class="visually-hidden"> trade #{{ t.id }}</span></a>
                            <a class="button button--small button--secondary" href="{{ url_for('main.edit_trade', trade_id=t.id, next=here) }}">Edit<span class="visually-hidden"> trade #{{ t.id }}</span></a>
                            <a class="button button--small button--danger-outline" href="{{ url_for('main.delete_trade', trade_id=t.id, next=here) }}">Delete<span class="visually-hidden"> trade #{{ t.id }}</span></a>
                        </div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    {{ ui.pagination(page, total_pages, total, "main.journal", active) }}
{% endif %}
{% endblock %}
```


## 18. `tradeledger/templates/trades/form.html`

```html
{% extends "base.html" %}
{% import "macros/forms.html" as f %}

{% set editing = mode == "edit" %}
{% block title %}{% if editing %}Edit trade #{{ trade.id }}{% else %}Add trade{% endif %}{% endblock %}
{% block heading %}{% if editing %}Edit trade #{{ trade.id }}{% else %}Add a trade{% endif %}{% endblock %}
{% block description %}{% if editing %}{{ trade.symbol }} · {{ trade.direction | title }} · entered {{ trade.trade_date | when }}{% else %}Record the plan and, if it has finished, the outcome. All times are UTC.{% endif %}{% endblock %}
{% block scripts %}<script src="{{ url_for('static', filename='js/trade-form.js') }}" defer></script>{% endblock %}

{% block content %}
{% if not instruments %}
<div class="notice notice--warning">
    <p>There are no instruments yet. A trade must belong to an instrument, so
       <a class="text-link" href="{{ url_for('main.instruments') }}">add an instrument</a> first.</p>
</div>
{% endif %}

{{ f.error_summary(errors, labels) }}

{% if editing and is_legacy %}
<div class="notice notice--info">
    <p><strong>Exit time not recorded.</strong> This trade was closed before TradeLedger recorded exit times.
       You can leave the exit time blank to keep it unknown, or enter the real time if you know it.
       TradeLedger never invents one.</p>
</div>
{% endif %}

<form class="trade-form" method="post" novalidate data-trade-form data-submit-once
      {% if editing and is_legacy %}data-legacy-close{% endif %}
      action="{{ url_for('main.edit_trade', trade_id=trade.id) if editing else url_for('main.new_trade') }}">
    {{ csrf_field() }}
    {% if editing %}<input type="hidden" name="next" value="{{ back }}">{% endif %}

    <section class="card form-section" aria-labelledby="section-instrument">
        <h2 class="form-section__title" id="section-instrument">Instrument and status</h2>
        <div class="form-grid">
            {{ f.select("instrument_id", "Instrument", values, errors, labels,
                        instrument_options,
                        required=True, blank="Choose an instrument",
                        hint="The instrument defines the contract multiplier and quote currency.") }}
            {{ f.choice("direction", "Direction", values, errors, labels, [("LONG", "Long"), ("SHORT", "Short")]) }}
            {{ f.choice("status", "Status", values, errors, labels, [("OPEN", "Open"), ("CLOSED", "Closed")],
                        hint="Open: still running. Closed: finished, with an exit price and time.",
                        data_attr="data-status-control") }}
        </div>
    </section>

    <section class="card form-section" aria-labelledby="section-entry">
        <h2 class="form-section__title" id="section-entry">Entry and plan</h2>
        <div class="form-grid">
            {{ f.text("trade_date", "Entry time (UTC)", values, errors, labels, type="datetime-local", step="1",
                      required=True, hint="Date and time you entered the trade, in UTC.") }}
            {{ f.text("entry_price", "Entry price", values, errors, labels, inputmode="decimal", required=True,
                      hint="Exact price, e.g. 1.08500") }}
            {{ f.text("stop_loss", "Stop-loss", values, errors, labels, inputmode="decimal", required=True,
                      hint="Below entry for a long, above entry for a short.") }}
            {{ f.text("take_profit", "Take-profit", values, errors, labels, inputmode="decimal",
                      hint="Your planned target, if any.") }}
            {{ f.text("position_size", "Position size", values, errors, labels, inputmode="decimal", required=True,
                      hint="In the units the instrument's contract multiplier assumes, e.g. lots.") }}
            {{ f.text("fees", "Fees", values, errors, labels, inputmode="decimal",
                      hint="Total commission and charges, in the quote currency. Blank means 0.") }}
        </div>
    </section>

    <section class="card form-section" aria-labelledby="section-exit">
        <h2 class="form-section__title" id="section-exit">Exit</h2>
        <p class="form-section__note" data-open-note hidden>
            This trade is <strong>open</strong>, so it has no exit yet. Realised results appear once you close it.
        </p>
        <p class="form-section__note no-js-only">Leave these blank if the trade is still open.</p>
        <fieldset class="form-grid exit-fields" data-exit-fields>
            <legend class="visually-hidden">Exit details</legend>
            {{ f.text("exit_price", "Exit price", values, errors, labels, inputmode="decimal",
                      hint="Required for a closed trade.") }}
            {{ f.text("closed_at", "Exit time (UTC)", values, errors, labels, type="datetime-local", step="1",
                      hint=("Leave blank to keep it unknown." if (editing and is_legacy) else
                            "Required for a closed trade. Streaks, drawdown and monthly results follow the order trades closed.")) }}
        </fieldset>
    </section>

    <section class="card form-section" aria-labelledby="section-context">
        <h2 class="form-section__title" id="section-context">Context and notes</h2>
        <div class="form-grid">
            {{ f.text("strategy", "Strategy", values, errors, labels, maxlength=50, list="strategy-options",
                      hint="A short label such as Breakout. Existing labels are suggested as you type.") }}
            <datalist id="strategy-options">
                {% for s in strategies %}<option value="{{ s }}"></option>{% endfor %}
            </datalist>
            {{ f.select("session", "Session", values, errors, labels,
                        [("ASIA", "Asia"), ("LONDON", "London"), ("NEW_YORK", "New York"), ("OTHER", "Other")],
                        blank="Not recorded") }}
            {{ f.textarea("notes", "Notes", values, errors, labels, maxlength=2000,
                          hint="Why you took it, how you managed it, what you learned. Up to 2,000 characters.") }}
        </div>
    </section>

    <div class="form-actions">
        <a class="button button--ghost" href="{{ back if editing else url_for('main.journal') }}">Cancel</a>
        <button class="button button--primary" type="submit" data-busy-label="Saving…">
            {% if editing %}Save changes{% else %}Save trade{% endif %}
        </button>
    </div>
</form>
{% endblock %}
```


## 19. `tradeledger/templates/trades/detail.html`

```html
{% extends "base.html" %}
{% import "macros/ui.html" as ui %}

{% block title %}Trade #{{ trade.id }}{% endblock %}
{% block heading %}Trade #{{ trade.id }} · {{ trade.symbol }}{% endblock %}
{% block description %}{{ trade.direction | title }} · entered {{ trade.trade_date | when }}{% endblock %}
{% block actions %}
    <a class="button button--secondary" href="{{ url_for('main.edit_trade', trade_id=trade.id) }}">Edit</a>
    <a class="button button--danger-outline" href="{{ url_for('main.delete_trade', trade_id=trade.id) }}">Delete</a>
{% endblock %}

{% block content %}
<p class="back-link"><a class="text-link" href="{{ url_for('main.journal') }}">&larr; Back to the journal</a></p>

<div class="detail-grid">
    <section class="card" aria-labelledby="trade-info-title">
        <div class="card__header">
            <h2 id="trade-info-title">Trade</h2>
            <div class="badge-row">{{ ui.direction_badge(trade.direction) }} {{ ui.outcome_badge(trade, result) }}</div>
        </div>
        <dl class="data-list">
            <div><dt>Instrument</dt><dd>{{ trade.symbol }} <span class="muted">(quote currency {{ trade.quote_currency }})</span></dd></div>
            <div><dt>Status</dt><dd>{{ trade.status | title }}</dd></div>
            <div><dt>Entry time</dt><dd>{{ trade.trade_date | when }}</dd></div>
            <div><dt>Exit time</dt><dd>
                {% if trade.status == "OPEN" %}<span class="muted">Not closed yet</span>
                {% elif trade.closed_at %}{{ trade.closed_at | when }}
                {% else %}<span class="muted">Not recorded</span>{% endif %}
            </dd></div>
            <div><dt>Entry price</dt><dd class="num">{{ trade.entry_price | plain }}</dd></div>
            <div><dt>Stop-loss</dt><dd class="num">{{ trade.stop_loss | plain }}</dd></div>
            <div><dt>Take-profit</dt><dd class="num">{{ trade.take_profit | plain }}</dd></div>
            <div><dt>Exit price</dt><dd class="num">{{ trade.exit_price | plain }}</dd></div>
            <div><dt>Position size</dt><dd class="num">{{ trade.position_size | plain }}</dd></div>
            <div><dt>Fees</dt><dd class="num">{{ trade.fees | money(trade.quote_currency) }}</dd></div>
            <div><dt>Strategy</dt><dd>{{ trade.strategy or "Not recorded" }}</dd></div>
            <div><dt>Session</dt><dd>{{ (trade.session | replace("_", " ") | title) if trade.session else "Not recorded" }}</dd></div>
        </dl>
    </section>

    {% if trade.status == "CLOSED" and result %}
    <section class="card result-card result-card--{{ result.outcome | lower }}" aria-labelledby="result-title">
        <div class="card__header">
            <h2 id="result-title">Result</h2>
            {{ ui.outcome_badge(trade, result) }}
        </div>
        <p class="result-card__headline">
            {{ ui.signed(result.net_pnl, result.net_pnl | money(trade.quote_currency)) }}
            <span class="result-card__r">{{ ui.signed(result.realised_r, result.realised_r | r) }}</span>
        </p>
        <dl class="data-list">
            <div><dt>Outcome</dt><dd>{{ result.outcome | title }}</dd></div>
            <div><dt>Gross P&amp;L <span class="muted">(before fees)</span></dt><dd>{{ ui.signed(result.gross_pnl, result.gross_pnl | money(trade.quote_currency)) }}</dd></div>
            <div><dt>Fees</dt><dd class="num">{{ result.fees | money(trade.quote_currency) }}</dd></div>
            <div><dt>Net P&amp;L <span class="muted">(after fees)</span></dt><dd>{{ ui.signed(result.net_pnl, result.net_pnl | money(trade.quote_currency)) }}</dd></div>
            <div><dt>Initial risk</dt><dd class="num">{{ result.initial_risk | money(trade.quote_currency) }}</dd></div>
            <div><dt>Planned R:R</dt><dd class="num">{{ result.planned_rr | ratio }}</dd></div>
            <div><dt>Realised R</dt><dd>{{ ui.signed(result.realised_r, result.realised_r | r) }}</dd></div>
        </dl>
        {% if result.r_unavailable_reason %}<p class="notice notice--warning">Realised R is unavailable: {{ result.r_unavailable_reason }}.</p>{% endif %}
        <p class="result-card__note">Calculated by TradeLedger's analytics engine with exact decimals. Realised R = net P&amp;L ÷ initial risk.</p>
        {% if not trade.closed_at %}
        <p class="notice notice--info">The exit time of this trade was never recorded, so in time-ordered analytics (streaks,
           drawdown, monthly results) it is placed at its entry time. Edit the trade to add the real exit time if you know it.</p>
        {% endif %}
    </section>
    {% elif trade.status == "OPEN" %}
    <section class="card result-card result-card--open" aria-labelledby="plan-title">
        <div class="card__header">
            <h2 id="plan-title">Plan</h2>
            <span class="badge badge--open">Open</span>
        </div>
        <p class="notice notice--info">This trade is still open, so there is no realised result yet. The figures below describe the plan, not an outcome.</p>
        <dl class="data-list">
            <div><dt>Planned risk <span class="muted">(if the stop is hit, before fees)</span></dt><dd class="num">{{ plan.initial_risk | money(trade.quote_currency) }}</dd></div>
            <div><dt>Planned R:R</dt><dd class="num">{{ plan.planned_rr | ratio }}</dd></div>
        </dl>
        <a class="button button--secondary" href="{{ url_for('main.edit_trade', trade_id=trade.id) }}">Close this trade</a>
    </section>
    {% else %}
    <section class="card" aria-labelledby="result-title">
        <h2 id="result-title">Result</h2>
        <p class="notice notice--warning">The result of this trade could not be calculated from the stored data.</p>
    </section>
    {% endif %}

    <section class="card detail-grid__wide" aria-labelledby="notes-title">
        <h2 id="notes-title">Notes</h2>
        {% if trade.notes %}<p class="notes">{{ trade.notes }}</p>{% else %}<p class="muted">No notes recorded.</p>{% endif %}
        <p class="meta">Created {{ trade.created_at | when }} · last updated {{ trade.updated_at | when }}</p>
    </section>
</div>
{% endblock %}
```


## 20. `tradeledger/templates/trades/confirm_delete.html`

```html
{% extends "base.html" %}
{% import "macros/ui.html" as ui %}

{% block title %}Delete trade #{{ trade.id }}{% endblock %}
{% block heading %}Delete trade #{{ trade.id }}?{% endblock %}
{% block description %}Check this is the right trade. Deleting it cannot be undone.{% endblock %}

{% block content %}
<section class="card confirm" aria-labelledby="confirm-title">
    <h2 class="visually-hidden" id="confirm-title">Trade to delete</h2>
    <div class="notice notice--error">
        <p><strong>This permanently removes the trade.</strong> It will disappear from the journal and from every
           performance figure. There is no undo.</p>
    </div>
    <dl class="data-list data-list--compact">
        <div><dt>Trade</dt><dd>#{{ trade.id }} · {{ trade.symbol }} · {{ ui.direction_badge(trade.direction) }} {{ ui.outcome_badge(trade, result) }}</dd></div>
        <div><dt>Entry time</dt><dd>{{ trade.trade_date | when }}</dd></div>
        <div><dt>Entry price</dt><dd class="num">{{ trade.entry_price | plain }}</dd></div>
        {% if result %}<div><dt>Net P&amp;L</dt><dd>{{ ui.signed(result.net_pnl, result.net_pnl | money(trade.quote_currency)) }}</dd></div>{% endif %}
        <div><dt>Strategy</dt><dd>{{ trade.strategy or "Not recorded" }}</dd></div>
    </dl>
    <form method="post" action="{{ url_for('main.delete_trade', trade_id=trade.id) }}" class="confirm__actions" data-submit-once>
        {{ csrf_field() }}
        <input type="hidden" name="next" value="{{ back }}">
        <a class="button button--ghost" href="{{ back }}">Cancel, keep this trade</a>
        <button class="button button--danger" type="submit" data-busy-label="Deleting…">Delete trade #{{ trade.id }}</button>
    </form>
</section>
{% endblock %}
```


## 21. `tradeledger/templates/instruments/index.html`

```html
{% extends "base.html" %}
{% import "macros/forms.html" as f %}
{% import "macros/ui.html" as ui %}

{% block title %}Instruments{% endblock %}
{% block heading %}Instruments{% endblock %}
{% block description %}The markets you trade, and how a price move turns into money for each.{% endblock %}

{% block content %}
<div class="notice notice--warning">
    <p><strong>Check every specification against your broker's documentation.</strong>
       TradeLedger cannot know your broker's contract sizes. A wrong contract multiplier makes every
       money figure for that instrument wrong. The sample instruments use illustrative values only.</p>
</div>

<section aria-labelledby="list-title">
    <div class="section-heading"><h2 id="list-title">Recorded instruments</h2></div>
    {% if instruments %}
    <div class="table-wrap" role="region" aria-label="Instruments table" tabindex="0">
        <table class="table instrument-table">
            <caption class="visually-hidden">Instruments, sorted by symbol</caption>
            <thead>
                <tr>
                    <th scope="col">Symbol</th>
                    <th scope="col">Name</th>
                    <th scope="col">Asset class</th>
                    <th scope="col" class="num-col">Price increment</th>
                    <th scope="col" class="num-col">Contract multiplier</th>
                    <th scope="col">Quote currency</th>
                    <th scope="col"><span class="visually-hidden">Actions</span></th>
                </tr>
            </thead>
            <tbody>
                {% for i in instruments %}
                <tr>
                    <td data-label="Symbol"><strong>{{ i.symbol }}</strong></td>
                    <td data-label="Name">{{ i.name }}</td>
                    <td data-label="Asset class">{{ i.asset_class | title }}</td>
                    <td data-label="Price increment" class="num-col">{{ i.price_increment | plain }}</td>
                    <td data-label="Contract multiplier" class="num-col">{{ i.contract_multiplier | plain }}</td>
                    <td data-label="Quote currency">{{ i.quote_currency }}</td>
                    <td class="actions-col">
                        <div class="row-actions">
                            <a class="button button--small button--secondary" href="{{ url_for('main.journal', instrument_id=i.id) }}">Trades<span class="visually-hidden"> on {{ i.symbol }}</span></a>
                            <a class="button button--small button--secondary" href="{{ url_for('main.new_trade', instrument_id=i.id) }}">Add trade<span class="visually-hidden"> on {{ i.symbol }}</span></a>
                        </div>
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
    {{ ui.empty_state("No instruments yet", "Add the first market you trade using the form below.") }}
    {% endif %}
</section>

<div class="split">
    <section class="card form-section" aria-labelledby="add-title">
        <h2 class="form-section__title" id="add-title">Add an instrument</h2>
        {{ f.error_summary(errors, labels) }}
        <form method="post" action="{{ url_for('main.instruments') }}" novalidate data-submit-once>
            {{ csrf_field() }}
            <div class="form-grid">
                {{ f.text("symbol", "Symbol", values, errors, labels, required=True, maxlength=20,
                          hint="Letters, digits or underscores, e.g. US30. Stored in capitals.") }}
                {{ f.text("name", "Name", values, errors, labels, required=True, maxlength=100,
                          hint="A readable name, e.g. Wall Street 30 CFD.") }}
                {{ f.select("asset_class", "Asset class", values, errors, labels,
                            asset_class_options,
                            required=True, blank="Choose an asset class") }}
                {{ f.text("quote_currency", "Quote currency", values, errors, labels, required=True, maxlength=3,
                          hint="The currency P&L is paid in, e.g. USD.") }}
                {{ f.text("price_increment", "Price increment", values, errors, labels, inputmode="decimal", required=True,
                          hint="The smallest price step, e.g. 0.00001 or 1.") }}
                {{ f.text("contract_multiplier", "Contract multiplier", values, errors, labels, inputmode="decimal", required=True,
                          hint="Money per 1.0 price move per 1 unit of position size. See the explanation.") }}
            </div>
            <div class="form-actions">
                <button class="button button--primary" type="submit" data-busy-label="Adding…">Add instrument</button>
            </div>
        </form>
    </section>

    <section class="card explainer" aria-labelledby="explain-title">
        <h2 id="explain-title">What is a contract multiplier?</h2>
        <p>It converts a price move into money:</p>
        <p class="formula">P&amp;L = price move × position size × contract multiplier</p>
        <p>If you record EUR/USD position size in standard lots and your broker's lot is 100,000 euros, a move from
           1.08500 to 1.08900 (0.004) on 1 lot is 0.004 × 1 × 100,000 = <strong>400 USD</strong>.</p>
        <p>If you record size in single units instead, the multiplier is 1 and the size is 100,000. Either works,
           as long as size and multiplier use the <strong>same definition</strong>.</p>
        <p class="muted">Instruments cannot be edited yet: changing a multiplier would silently change the result of every past trade on it.</p>
    </section>
</div>
{% endblock %}
```


## 22. `tradeledger/templates/analytics.html`

```html
{% extends "base.html" %}
{% import "macros/ui.html" as ui %}

{% block title %}Analytics preview{% endblock %}
{% block heading %}Analytics <span class="heading-tag">Preview</span>{% endblock %}
{% block description %}A first look using your real data. The full dashboard with charts arrives in Phase 6.{% endblock %}

{% block content %}
<div class="notice notice--info">
    <p><strong>This is a preview.</strong> The tables below come from the same analytics engine as the finished
       dashboard will use, but charts, an equity-style curve, filters and comparisons are part of Phase 6.
       Realised results only: open trades are excluded.</p>
</div>

{% macro group_table(title, groups, caption) %}
<section aria-labelledby="{{ caption }}-title">
    <div class="section-heading"><h2 id="{{ caption }}-title">{{ title }}</h2></div>
    {% if groups %}
    <p class="table-scroll-hint" id="{{ caption }}-hint">Scroll the table sideways to see every column.</p>
    <div class="table-wrap" role="region" aria-label="{{ title }} table" aria-describedby="{{ caption }}-hint" tabindex="0">
        <table class="table group-table">
            <caption class="visually-hidden">{{ title }}</caption>
            <thead>
                <tr>
                    <th scope="col">{{ title | replace("By ", "") | capitalize }}</th>
                    <th scope="col" class="num-col">Closed trades</th>
                    <th scope="col">Won / lost / breakeven</th>
                    <th scope="col" class="num-col">Win rate</th>
                    <th scope="col" class="num-col">Expectancy</th>
                    <th scope="col" class="num-col">Total R</th>
                    <th scope="col" class="num-col">Profit factor (R)</th>
                    <th scope="col" class="num-col">Max drawdown</th>
                </tr>
            </thead>
            <tbody>
                {% for label, s in groups.items() %}
                <tr>
                    <td data-label="Group"><strong>{{ label | replace("_", " ") }}</strong></td>
                    <td data-label="Closed trades" class="num-col">{{ s.trades }}</td>
                    <td data-label="Won / lost / breakeven">{{ s.wins }} / {{ s.losses }} / {{ s.breakevens }}</td>
                    <td data-label="Win rate" class="num-col">{{ s.win_rate | rate }}</td>
                    <td data-label="Expectancy" class="num-col">{{ ui.signed(s.expectancy_r, s.expectancy_r | r) }}</td>
                    <td data-label="Total R" class="num-col">{{ ui.signed(s.total_r, s.total_r | r) }}</td>
                    <td data-label="Profit factor (R)" class="num-col">{{ s.profit_factor_r | ratio }}</td>
                    <td data-label="Max drawdown" class="num-col">{{ s.max_drawdown_r.amount | r_amount }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    <p class="table-note">Small samples are unreliable: a group with only a few trades says little about future results.</p>
    {% else %}
    {{ ui.empty_state("Nothing to show yet", "These tables fill in once you have closed trades.",
                      url_for('main.new_trade'), "Add a trade") }}
    {% endif %}
</section>
{% endmacro %}

{{ group_table("By instrument", report.by_instrument, "instrument") }}
{{ group_table("By strategy", report.by_strategy, "strategy") }}
{{ group_table("By session", report.by_session, "session") }}

<p class="muted">The same figures are available as JSON from
   <code>/api/v1/analytics/summary</code> and related endpoints, and in the terminal with
   <code>flask --app tradeledger report</code>.</p>
{% endblock %}
```


## 23. `tradeledger/templates/about.html`

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
            <li>Every form is protected against cross-site request forgery, and pages may only load this site's own scripts and styles.</li>
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
            <li>A versioned JSON API documented with OpenAPI 3.1 (<a class="text-link" href="{{ url_for('api.openapi_spec') }}">specification</a>)</li>
            <li>pytest, with browser tests in Playwright where available</li>
        </ul>
        <p class="muted">Running in <strong>{{ config.CONFIG_NAME }}</strong> mode · version {{ config.APP_VERSION }}</p>
    </section>
</div>
{% endblock %}
```


## 24. `tradeledger/templates/errors/400.html`

```html
{% extends "base.html" %}

{% block title %}Form expired{% endblock %}
{% block heading %}This form could not be accepted{% endblock %}

{% block content %}
<section class="card error-page" aria-labelledby="error-title">
    <p class="error-code" aria-hidden="true">400</p>
    <h2 id="error-title">Form expired or not sent from TradeLedger</h2>
    <p>Nothing was saved or deleted. This usually means the page was open for a long time or your browser's
       cookies were cleared. Go back, reload the page and try again.</p>
    <p class="muted">Every TradeLedger form carries a security token to block forged requests from other websites.
       This request did not include a valid one.</p>
    <div class="error-page__actions">
        <a class="button button--primary" href="{{ url_for('main.index') }}">Back to the overview</a>
    </div>
</section>
{% endblock %}
```


## 25. `tradeledger/templates/errors/403.html`

```html
{% extends "base.html" %}

{% block title %}Journal disabled{% endblock %}
{% block heading %}Journal disabled{% endblock %}

{% block content %}
<section class="card error-page" aria-labelledby="error-title">
    <p class="error-code" aria-hidden="true">403</p>
    <h2 id="error-title">The journal is switched off on this server</h2>
    <p>TradeLedger has no login yet, so the pages that show or change trades are disabled in the production
       configuration. Run it on your own computer to use the journal.</p>
    <div class="error-page__actions">
        <a class="button button--secondary" href="{{ url_for('main.about') }}">About TradeLedger</a>
    </div>
</section>
{% endblock %}
```


## 26. `tradeledger/templates/errors/404.html`

```html
{% extends "base.html" %}

{% block title %}Page not found{% endblock %}
{% block heading %}Page not found{% endblock %}

{% block content %}
<section class="card error-page" aria-labelledby="error-title">
    <p class="error-code" aria-hidden="true">404</p>
    <h2 id="error-title">Page not found</h2>
    <p>The page or trade you asked for does not exist. It may have been deleted, or the address may be mistyped.</p>
    <div class="error-page__actions">
        <a class="button button--primary" href="{{ url_for('main.index') }}">Back to the overview</a>
        <a class="button button--secondary" href="{{ url_for('main.journal') }}">Open the journal</a>
    </div>
</section>
{% endblock %}
```


## 27. `tradeledger/templates/errors/500.html`

```html
{% extends "base.html" %}

{% block title %}Something went wrong{% endblock %}
{% block heading %}Something went wrong{% endblock %}

{% block content %}
<section class="card error-page" aria-labelledby="error-title">
    <p class="error-code" aria-hidden="true">500</p>
    <h2 id="error-title">Something went wrong</h2>
    <p>An unexpected error occurred. It has been logged. Please try again.</p>
    <div class="error-page__actions">
        <a class="button button--primary" href="{{ url_for('main.index') }}">Back to the overview</a>
    </div>
</section>
{% endblock %}
```


## 28. `tradeledger/static/css/style.css`

```css
/* TradeLedger stylesheet entry point.
   Split into four files so each stays focused and readable:
     base.css        design tokens (colours, spacing, type), reset, typography
     layout.css      the application shell: sidebar, top bar, content area
     components.css  reusable pieces: buttons, forms, tables, badges, cards
     pages.css       layouts that belong to one page
   @import keeps a single <link> in the HTML. */
@import url("base.css");
@import url("layout.css");
@import url("components.css");
@import url("pages.css");
```


## 29. `tradeledger/static/css/base.css`

```css
/* ==========================================================================
   Design tokens
   Every colour, size and spacing value used elsewhere comes from here, so
   the whole interface can be adjusted in one place.
   Colour pairs were chosen for WCAG AA contrast (4.5:1 for normal text)
   against the surfaces they sit on.
   ========================================================================== */
:root {
    /* Surfaces, darkest to lightest */
    --color-bg: #0a101d;
    --color-surface: #101a2c;
    --color-surface-raised: #152238;
    --color-surface-hover: #1b2a45;
    --color-border: #24334f;
    --color-border-strong: #364a70;

    /* Text */
    --color-text: #e8eef8;
    --color-text-muted: #a3b1c7;
    --color-text-subtle: #8796af;

    /* Accent (links, primary buttons, focus) */
    --color-accent: #6aa8ff;
    --color-accent-strong: #4b8ff5;
    --color-on-accent: #061022;

    /* Meaning. Always paired with a word (WIN, LOSS...), never colour alone. */
    --color-positive: #4cd79a;
    --color-positive-soft: rgba(76, 215, 154, 0.12);
    --color-negative: #ff8a8a;
    --color-negative-soft: rgba(255, 138, 138, 0.12);
    --color-warning: #f3c969;
    --color-warning-soft: rgba(243, 201, 105, 0.12);
    --color-info: #7cc0ff;
    --color-info-soft: rgba(124, 192, 255, 0.1);
    --color-neutral-soft: rgba(163, 177, 199, 0.12);

    /* Spacing scale (rem-based, so it grows with the user's font size) */
    --space-1: 0.25rem;
    --space-2: 0.5rem;
    --space-3: 0.75rem;
    --space-4: 1rem;
    --space-5: 1.5rem;
    --space-6: 2rem;
    --space-7: 3rem;

    /* Type */
    --font-sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    --font-mono: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
    --text-xs: 0.75rem;
    --text-sm: 0.875rem;
    --text-base: 1rem;
    --text-lg: 1.125rem;
    --text-xl: 1.375rem;
    --text-2xl: 1.75rem;
    --leading: 1.55;

    /* Shape and depth */
    --radius-sm: 6px;
    --radius: 10px;
    --radius-lg: 14px;
    --shadow: 0 1px 2px rgba(0, 0, 0, 0.35), 0 8px 24px rgba(0, 0, 0, 0.18);
    --focus-ring: 0 0 0 3px rgba(106, 168, 255, 0.55);

    /* Layout */
    --sidebar-width: 15.5rem;
    --content-max: 82rem;
    --control-height: 2.5rem;
}

/* ==========================================================================
   Reset and base elements
   ========================================================================== */
*,
*::before,
*::after {
    box-sizing: border-box;
}

html {
    -webkit-text-size-adjust: 100%;
}

body {
    margin: 0;
    background: var(--color-bg);
    color: var(--color-text);
    font-family: var(--font-sans);
    font-size: var(--text-base);
    line-height: var(--leading);
}

h1,
h2,
h3 {
    margin: 0;
    line-height: 1.25;
    font-weight: 650;
    letter-spacing: -0.01em;
}

h2 {
    font-size: var(--text-lg);
}

h3 {
    font-size: var(--text-base);
}

p {
    margin: 0;
}

p + p {
    margin-top: var(--space-3);
}

a {
    color: var(--color-accent);
}

code {
    font-family: var(--font-mono);
    font-size: 0.9em;
    padding: 0.1em 0.35em;
    border-radius: var(--radius-sm);
    background: var(--color-surface-raised);
}

ul[role="list"],
ol[role="list"] {
    list-style: none;
    margin: 0;
    padding: 0;
}

/* One visible focus style everywhere (keyboard users must always see it). */
:focus-visible {
    outline: none;
    box-shadow: var(--focus-ring);
    border-radius: var(--radius-sm);
}

/* The hidden attribute must always win. Without this, any component
   rule that sets display (e.g. fieldset.form-grid { display: grid })
   silently overrides it. This is the one deliberate !important. */
[hidden] {
    display: none !important;
}

/* Hidden visually but still read by screen readers. */
.visually-hidden {
    position: absolute !important;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
}

.muted {
    color: var(--color-text-muted);
}

/* Figures line up in columns when every digit has the same width. */
.num,
.num-col,
.stat__value {
    font-variant-numeric: tabular-nums;
}

.num--positive {
    color: var(--color-positive);
}

.num--negative {
    color: var(--color-negative);
}

/* Shown only when JavaScript is off (early.js adds .js to <html>). */
.js .no-js-only {
    display: none;
}

@media (prefers-reduced-motion: reduce) {
    *,
    *::before,
    *::after {
        transition-duration: 0.01ms !important;
        animation-duration: 0.01ms !important;
    }
}
```


## 30. `tradeledger/static/css/layout.css`

```css
/* ==========================================================================
   Application shell
   Wide screens (64rem / 1024px and up): fixed sidebar + main column (Grid).
   Narrower screens with JavaScript: the sidebar slides in from the left,
   opened by the Menu button.
   Without JavaScript: the sidebar simply sits above the page, so the
   navigation is never lost.
   ========================================================================== */
.skip-link {
    position: absolute;
    left: var(--space-4);
    top: -10rem;
    z-index: 100;
    padding: var(--space-2) var(--space-4);
    background: var(--color-accent);
    color: var(--color-on-accent);
    font-weight: 600;
    border-radius: var(--radius-sm);
}

.skip-link:focus {
    top: var(--space-4);
}

.shell {
    min-height: 100vh;
}

.main-area {
    display: flex;
    flex-direction: column;
    min-width: 0; /* lets wide tables scroll inside instead of stretching the page */
    min-height: 100vh;
}

/* ---------- Sidebar ---------- */
.sidebar {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
    padding: var(--space-5) var(--space-4);
    background: var(--color-surface);
    border-right: 1px solid var(--color-border);
}

.sidebar__top {
    display: flex;
    align-items: center;
    justify-content: space-between;
}

.brand {
    display: flex;
    align-items: center;
    gap: var(--space-3);
    color: var(--color-text);
    text-decoration: none;
}

.brand__mark {
    display: inline-grid;
    place-items: center;
    width: 2.25rem;
    height: 2.25rem;
    border-radius: var(--radius);
    background: var(--color-accent);
    color: var(--color-on-accent);
    font-weight: 750;
    font-size: var(--text-sm);
}

.brand__text {
    display: flex;
    flex-direction: column;
    line-height: 1.2;
}

.brand__name {
    font-weight: 700;
}

.brand__tag {
    font-size: var(--text-xs);
    color: var(--color-text-muted);
}

.nav {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
}

.nav__link {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-2);
    min-height: var(--control-height);
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm);
    color: var(--color-text-muted);
    text-decoration: none;
    font-weight: 500;
}

.nav__link:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
}

.nav__link[aria-current="page"] {
    background: var(--color-surface-raised);
    color: var(--color-text);
    box-shadow: inset 3px 0 0 var(--color-accent);
}

.nav__tag {
    padding: 0 var(--space-2);
    border-radius: 999px;
    border: 1px solid var(--color-border-strong);
    font-size: var(--text-xs);
    color: var(--color-text-muted);
}

.sidebar__footer {
    margin-top: auto;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

.env-badge {
    align-self: flex-start;
    padding: 0.1rem var(--space-2);
    border-radius: 999px;
    background: var(--color-warning-soft);
    color: var(--color-warning);
    font-weight: 600;
}

/* Two classes deep so these beat the generic .icon-button rule in
   components.css, which is loaded later. */
.sidebar .sidebar__close,
.topbar .nav-toggle {
    display: none;
}

.nav-backdrop {
    position: fixed;
    inset: 0;
    z-index: 40;
    background: rgba(3, 7, 15, 0.6);
}

.nav-backdrop[hidden] {
    display: none;
}

/* ---------- Top bar ---------- */
.topbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-4);
    padding: var(--space-5) var(--space-5) var(--space-4);
    border-bottom: 1px solid var(--color-border);
}

.topbar__titles {
    flex: 1 1 18rem;
    min-width: 0;
}

.topbar__title {
    font-size: var(--text-2xl);
}

.topbar__description {
    margin-top: var(--space-1);
    color: var(--color-text-muted);
}

.topbar__actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
}

.topbar__actions:empty {
    display: none;
}

/* ---------- Content ---------- */
.content {
    flex: 1;
    width: 100%;
    max-width: var(--content-max);
    padding: var(--space-5);
    display: flex;
    flex-direction: column;
    gap: var(--space-6);
}

.content:focus {
    outline: none;
}

.site-footer {
    padding: var(--space-4) var(--space-5);
    border-top: 1px solid var(--color-border);
    color: var(--color-text-subtle);
    font-size: var(--text-xs);
}

/* ---------- Wide screens: sidebar beside the content ---------- */
@media (min-width: 64rem) {
    .shell {
        display: grid;
        grid-template-columns: var(--sidebar-width) minmax(0, 1fr);
    }

    .sidebar {
        position: sticky;
        top: 0;
        height: 100vh;
        overflow-y: auto;
    }

    .nav-backdrop {
        display: none;
    }
}

/* ---------- Narrow screens with JavaScript: off-canvas sidebar ---------- */
@media (max-width: 63.99rem) {
    .js .topbar .nav-toggle,
    .js .sidebar .sidebar__close {
        display: inline-flex;
    }

    .js .sidebar {
        position: fixed;
        inset: 0 auto 0 0;
        z-index: 50;
        width: min(var(--sidebar-width), 85vw);
        overflow-y: auto;
        transform: translateX(-100%);
        visibility: hidden; /* closed menu links are not reachable by Tab */
        transition: transform 0.2s ease, visibility 0.2s;
        box-shadow: var(--shadow);
    }

    .js .sidebar[data-open="true"] {
        transform: translateX(0);
        visibility: visible;
        /* Become visible at once when opening, so app.js can move focus
           to the first link (focus() fails on a visibility:hidden
           element). On close, the base rule delays hiding until the
           slide-out has finished. */
        transition: transform 0.2s ease, visibility 0s;
    }

    .js body.nav-open {
        overflow: hidden;
    }

    /* Without JavaScript: navigation stays visible above the page. */
    html:not(.js) .sidebar {
        border-right: 0;
        border-bottom: 1px solid var(--color-border);
    }

    html:not(.js) .sidebar__footer {
        display: none;
    }
}

@media (max-width: 40rem) {
    .topbar {
        padding: var(--space-4);
    }

    .topbar__title {
        font-size: var(--text-xl);
    }

    .content {
        padding: var(--space-4);
        gap: var(--space-5);
    }

    .topbar__actions {
        width: 100%;
    }

    .topbar__actions .button {
        flex: 1 1 auto;
    }
}
```


## 31. `tradeledger/static/css/components.css`

```css
/* ==========================================================================
   Reusable components
   ========================================================================== */

/* ---------- Buttons ---------- */
.button,
.icon-button {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: var(--space-2);
    min-height: var(--control-height);
    padding: 0 var(--space-4);
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    font: inherit;
    font-size: var(--text-sm);
    font-weight: 600;
    line-height: 1.2;
    text-decoration: none;
    white-space: nowrap;
    cursor: pointer;
}

.button--primary {
    background: var(--color-accent);
    color: var(--color-on-accent);
}

.button--primary:hover {
    background: var(--color-accent-strong);
}

.button--secondary,
.icon-button {
    background: var(--color-surface-raised);
    border-color: var(--color-border-strong);
    color: var(--color-text);
}

.button--secondary:hover,
.icon-button:hover {
    background: var(--color-surface-hover);
}

.button--ghost {
    background: transparent;
    color: var(--color-text-muted);
}

.button--ghost:hover {
    color: var(--color-text);
    background: var(--color-surface-hover);
}

.button--danger {
    background: var(--color-negative);
    color: #2a0707;
}

.button--danger:hover {
    background: #ff7070;
}

.button--danger-outline {
    background: transparent;
    border-color: rgba(255, 138, 138, 0.45);
    color: var(--color-negative);
}

.button--danger-outline:hover {
    background: var(--color-negative-soft);
}

.button--small {
    min-height: 2rem;
    padding: 0 var(--space-2);
    font-size: var(--text-xs);
}

.button.is-disabled,
.button:disabled {
    opacity: 0.5;
    cursor: not-allowed;
}

.button[aria-busy="true"] {
    cursor: progress;
}

.icon-button {
    padding: 0 var(--space-3);
}

.nav-toggle__bars,
.nav-toggle__bars::before,
.nav-toggle__bars::after {
    display: block;
    width: 1rem;
    height: 2px;
    background: currentColor;
    border-radius: 2px;
}

.nav-toggle__bars {
    position: relative;
}

.nav-toggle__bars::before,
.nav-toggle__bars::after {
    content: "";
    position: absolute;
    left: 0;
}

.nav-toggle__bars::before {
    top: -5px;
}

.nav-toggle__bars::after {
    top: 5px;
}

.text-link {
    color: var(--color-accent);
    text-underline-offset: 0.2em;
}

/* ---------- Cards ---------- */
.card {
    background: var(--color-surface);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    padding: var(--space-5);
}

.card--flush {
    padding: 0;
    overflow: hidden;
}

.card__header {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    margin-bottom: var(--space-4);
}

.section-heading {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    justify-content: space-between;
    gap: var(--space-2) var(--space-4);
    margin-bottom: var(--space-3);
}

/* ---------- Badges ---------- */
.badge {
    display: inline-flex;
    align-items: center;
    padding: 0.1rem 0.55rem;
    border-radius: 999px;
    border: 1px solid var(--color-border-strong);
    background: var(--color-neutral-soft);
    color: var(--color-text-muted);
    font-size: var(--text-xs);
    font-weight: 700;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    white-space: nowrap;
}

.badge--win {
    background: var(--color-positive-soft);
    border-color: rgba(76, 215, 154, 0.4);
    color: var(--color-positive);
}

.badge--loss {
    background: var(--color-negative-soft);
    border-color: rgba(255, 138, 138, 0.4);
    color: var(--color-negative);
}

.badge--open {
    background: var(--color-info-soft);
    border-color: rgba(124, 192, 255, 0.4);
    color: var(--color-info);
}

.badge-row {
    display: flex;
    gap: var(--space-2);
}

.heading-tag {
    vertical-align: middle;
    margin-left: var(--space-2);
    padding: 0.1rem 0.55rem;
    border-radius: 999px;
    border: 1px solid var(--color-border-strong);
    font-size: var(--text-xs);
    font-weight: 600;
    color: var(--color-text-muted);
}

.direction {
    font-size: var(--text-sm);
    font-weight: 600;
}

.direction--long::before {
    content: "▲ ";
    color: var(--color-positive);
}

.direction--short::before {
    content: "▼ ";
    color: var(--color-negative);
}

/* ---------- Notices and flash messages ---------- */
.notice {
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    border: 1px solid var(--color-border-strong);
    border-left-width: 4px;
    font-size: var(--text-sm);
}

.notice--info {
    background: var(--color-info-soft);
    border-left-color: var(--color-info);
}

.notice--warning {
    background: var(--color-warning-soft);
    border-left-color: var(--color-warning);
}

.notice--error {
    background: var(--color-negative-soft);
    border-left-color: var(--color-negative);
}

.flash-stack {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    padding: var(--space-4) var(--space-5) 0;
    max-width: var(--content-max);
}

.flash {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--space-3);
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    border: 1px solid var(--color-border-strong);
    border-left: 4px solid var(--color-info);
    background: var(--color-surface-raised);
}

.flash--success {
    border-left-color: var(--color-positive);
}

.flash--error {
    border-left-color: var(--color-negative);
}

.flash__close {
    display: none;
    align-items: center;
    justify-content: center;
    width: 2rem;
    height: 2rem;
    border: 0;
    border-radius: var(--radius-sm);
    background: transparent;
    color: var(--color-text-muted);
    font-size: var(--text-lg);
    cursor: pointer;
}

.js .flash__close {
    display: inline-flex;
}

.flash__close:hover {
    background: var(--color-surface-hover);
    color: var(--color-text);
}

/* ---------- Empty states ---------- */
.empty-state {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: var(--space-3);
    padding: var(--space-6);
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius-lg);
    color: var(--color-text-muted);
}

.empty-state__title {
    color: var(--color-text);
}

/* ---------- Forms ---------- */
.form-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(14rem, 1fr));
    gap: var(--space-4) var(--space-5);
}

fieldset.form-grid {
    margin: 0;
    padding: 0;
    border: 0;
    min-width: 0;
}

.field {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    min-width: 0;
    margin: 0;
    padding: 0;
    border: 0;
}

.field--wide {
    grid-column: 1 / -1;
}

.field__label {
    padding: 0;
    font-size: var(--text-sm);
    font-weight: 600;
}

.field__optional {
    font-weight: 400;
    color: var(--color-text-subtle);
}

.field__hint {
    font-size: var(--text-xs);
    color: var(--color-text-muted);
}

.field__error {
    font-size: var(--text-sm);
    font-weight: 600;
    color: var(--color-negative);
}

.field--error .input {
    border-color: var(--color-negative);
}

.input {
    width: 100%;
    min-height: var(--control-height);
    padding: var(--space-2) var(--space-3);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: var(--color-bg);
    color: var(--color-text);
    font: inherit;
    font-variant-numeric: tabular-nums;
}

.input:hover {
    border-color: #4a608a;
}

.input:focus-visible {
    border-color: var(--color-accent);
}

.input::placeholder {
    color: var(--color-text-subtle);
}

.input[type="datetime-local"],
.input[type="date"] {
    color-scheme: dark;
}

.select {
    appearance: none;
    padding-right: var(--space-6);
    background-image: linear-gradient(45deg, transparent 50%, var(--color-text-muted) 50%),
        linear-gradient(135deg, var(--color-text-muted) 50%, transparent 50%);
    background-position: calc(100% - 1.1rem) 55%, calc(100% - 0.75rem) 55%;
    background-size: 0.35rem 0.35rem;
    background-repeat: no-repeat;
}

.textarea {
    min-height: 7rem;
    resize: vertical;
    line-height: var(--leading);
}

/* Segmented radio buttons */
.choice__options {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2);
}

.choice__option {
    position: relative;
    flex: 1 1 6rem;
}

.choice__option input {
    position: absolute;
    opacity: 0;
    inset: 0;
    margin: 0;
    cursor: pointer;
}

.choice__option span {
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: var(--control-height);
    padding: 0 var(--space-3);
    border: 1px solid var(--color-border-strong);
    border-radius: var(--radius-sm);
    background: var(--color-bg);
    font-size: var(--text-sm);
    font-weight: 600;
    color: var(--color-text-muted);
}

.choice__option input:hover + span {
    border-color: #4a608a;
}

.choice__option input:checked + span {
    border-color: var(--color-accent);
    background: rgba(106, 168, 255, 0.14);
    color: var(--color-text);
}

.choice__option input:checked + span::before {
    /* The span is a flex container, which drops trailing spaces, so
       the gap comes from a margin rather than a space character. */
    content: "✓";
    margin-right: var(--space-1);
}

.choice__option input:focus-visible + span {
    box-shadow: var(--focus-ring);
}

.form-section {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
}

.form-section__note {
    font-size: var(--text-sm);
    color: var(--color-text-muted);
}

.form-actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--space-3);
    margin-top: var(--space-4);
}

.error-summary {
    padding: var(--space-4) var(--space-5);
    border: 2px solid var(--color-negative);
    border-radius: var(--radius);
    background: var(--color-negative-soft);
}

.error-summary__title {
    font-size: var(--text-base);
    margin-bottom: var(--space-2);
}

.error-summary ul {
    margin: 0;
    padding-left: var(--space-5);
}

.error-summary a {
    color: var(--color-text);
    font-weight: 600;
}

/* ---------- Tables ---------- */
.table-wrap {
    /* position: relative makes this the containing block for the
       absolutely positioned .visually-hidden labels inside the table;
       without it they escape the scroll box and widen the whole page. */
    position: relative;
    overflow-x: auto;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
}

.table {
    width: 100%;
    border-collapse: collapse;
    font-size: var(--text-sm);
}

.table th,
.table td {
    padding: var(--space-3);
    text-align: left;
    vertical-align: middle;
    border-bottom: 1px solid var(--color-border);
    white-space: nowrap;
}

.table thead th {
    position: sticky;
    top: 0;
    background: var(--color-surface-raised);
    color: var(--color-text-muted);
    font-size: var(--text-xs);
    font-weight: 600;
    letter-spacing: 0.04em;
    text-transform: uppercase;
}

.table tbody tr:last-child td {
    border-bottom: 0;
}

.table tbody tr:hover {
    background: var(--color-surface-hover);
}

.table .num-col {
    text-align: right;
}

.cell-stack {
    display: flex;
    flex-direction: column;
    line-height: 1.3;
}

.cell-stack--end {
    align-items: flex-end;
}

.cell-stack__sub {
    color: var(--color-text-muted);
    font-size: var(--text-xs);
}

.row-actions {
    display: flex;
    justify-content: flex-end;
    gap: var(--space-1);
}

/* A scrollable table region is focusable (tabindex="0") so keyboard users
   can scroll it with the arrow keys. */
.table-wrap:focus-visible {
    outline: none;
    box-shadow: var(--focus-ring);
}

/* The hint only matters where the full table can be wider than its box:
   between the card layout (below 64rem) and wide screens (80rem+). */
.table-scroll-hint {
    display: none;
    margin: 0 0 var(--space-2);
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

@media (min-width: 64rem) and (max-width: 79.99rem) {
    .table-scroll-hint {
        display: block;
    }
}

.table-note {
    margin-top: var(--space-2);
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

/* Below 64rem (1024px), where the sidebar goes off-canvas, each row
   becomes a card: label on the left, value on the right. Every column is
   kept, nothing financial is hidden. */
@media (max-width: 63.99rem) {
    .table-wrap {
        border: 0;
        background: transparent;
        overflow: visible;
    }

    .table,
    .table tbody,
    .table tr,
    .table td {
        display: block;
        width: 100%;
    }

    .table thead {
        position: absolute;
        width: 1px;
        height: 1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
    }

    .table tbody {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(min(100%, 20rem), 1fr));
        align-items: start;
        gap: var(--space-3);
    }

    .table tr {
        border: 1px solid var(--color-border);
        border-radius: var(--radius-lg);
        background: var(--color-surface);
        padding: var(--space-2) var(--space-4);
    }

    .table td {
        display: flex;
        justify-content: space-between;
        gap: var(--space-4);
        padding: var(--space-2) 0;
        white-space: normal;
        text-align: right;
    }

    .table td[data-label]::before {
        content: attr(data-label);
        color: var(--color-text-muted);
        font-weight: 600;
        text-align: left;
    }

    .table tbody tr:last-child td {
        border-bottom: 1px solid var(--color-border);
    }

    .table td:last-child {
        border-bottom: 0;
    }

    .table .actions-col {
        justify-content: flex-start;
        padding-top: var(--space-3);
    }

    .row-actions {
        justify-content: flex-start;
        flex-wrap: wrap;
    }
}

/* ---------- Definition lists (label: value pairs) ---------- */
.data-list {
    display: grid;
    gap: 0;
    margin: 0;
}

.data-list > div {
    display: flex;
    flex-wrap: wrap;
    justify-content: space-between;
    gap: var(--space-1) var(--space-4);
    padding: var(--space-3) 0;
    border-bottom: 1px solid var(--color-border);
}

.data-list > div:last-child {
    border-bottom: 0;
}

.data-list dt {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.data-list dd {
    margin: 0;
    font-weight: 600;
    text-align: right;
}

/* ---------- Pagination ---------- */
.pagination {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: center;
    gap: var(--space-3);
}

.pagination__status {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.results-summary {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}
```


## 32. `tradeledger/static/css/pages.css`

```css
/* ==========================================================================
   Page-specific layouts
   ========================================================================== */

/* ---------- Overview ---------- */
.welcome {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
    align-items: center;
}

.welcome__title {
    font-size: var(--text-xl);
    margin-bottom: var(--space-2);
}

.welcome__text {
    max-width: 62ch;
    color: var(--color-text-muted);
}

.welcome__counts {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-3);
}

.welcome__counts li {
    flex: 1 1 7rem;
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    background: var(--color-surface-raised);
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.welcome__count {
    display: block;
    color: var(--color-text);
    font-size: var(--text-2xl);
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}

@media (min-width: 64rem) {
    .welcome {
        grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
    }
}

.stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
    gap: var(--space-4);
}

.stat {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
}

.stat__label {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
    font-weight: 600;
}

.stat__value {
    font-size: var(--text-2xl);
    font-weight: 700;
}

.stat__note {
    color: var(--color-text-subtle);
    font-size: var(--text-xs);
}

.money-strip {
    margin-top: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
}

.money-strip__title {
    font-size: var(--text-sm);
    color: var(--color-text-muted);
}

.money-strip__list {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2) var(--space-6);
    font-weight: 600;
}

.money-strip__note {
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

.recent-list__item + .recent-list__item {
    border-top: 1px solid var(--color-border);
}

.recent-list__link {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: var(--space-1) var(--space-4);
    align-items: center;
    padding: var(--space-3) var(--space-5);
    color: inherit;
    text-decoration: none;
}

.recent-list__link:hover {
    background: var(--color-surface-hover);
}

.recent-list__main {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2) var(--space-3);
}

.recent-list__symbol {
    font-weight: 700;
}

.recent-list__meta {
    grid-column: 1;
    color: var(--color-text-muted);
    font-size: var(--text-xs);
}

.recent-list__result {
    grid-column: 2;
    grid-row: 1 / span 2;
    font-weight: 700;
    text-align: right;
}

/* ---------- Journal ---------- */
.filters {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
}

.filters__grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
    gap: var(--space-4);
}

.filters__actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-3);
}

.trade-table .direction {
    font-size: var(--text-xs);
}

.trade-row__symbol {
    color: var(--color-text);
    font-weight: 700;
    text-decoration: none;
}

.trade-row__symbol:hover {
    color: var(--color-accent);
    text-decoration: underline;
}

/* A thin coloured edge per outcome; the badge text says the same in words. */
.trade-row--win td:first-child {
    box-shadow: inset 3px 0 0 var(--color-positive);
}

.trade-row--loss td:first-child {
    box-shadow: inset 3px 0 0 var(--color-negative);
}

.trade-row--open td:first-child {
    box-shadow: inset 3px 0 0 var(--color-info);
}

@media (max-width: 63.99rem) {
    .trade-row--win td:first-child,
    .trade-row--loss td:first-child,
    .trade-row--open td:first-child {
        box-shadow: none;
    }

    /* .table .x beats the generic ".table tr" border rule by specificity. */
    .table .trade-row--win {
        border-left: 3px solid var(--color-positive);
    }

    .table .trade-row--loss {
        border-left: 3px solid var(--color-negative);
    }

    .table .trade-row--open {
        border-left: 3px solid var(--color-info);
    }
}

/* ---------- Trade form ---------- */
.trade-form {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
}

.form-section__title {
    font-size: var(--text-base);
}

.exit-fields[disabled] {
    opacity: 0.55;
}

/* ---------- Trade details ---------- */
.back-link {
    font-size: var(--text-sm);
}

.detail-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
    align-items: start;
}

@media (min-width: 60rem) {
    .detail-grid {
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }

    .detail-grid__wide {
        grid-column: 1 / -1;
    }
}

.result-card {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    border-top: 4px solid var(--color-border-strong);
}

.result-card .card__header {
    margin-bottom: 0;
}

.result-card--win {
    border-top-color: var(--color-positive);
}

.result-card--loss {
    border-top-color: var(--color-negative);
}

.result-card--open {
    border-top-color: var(--color-info);
}

.result-card__headline {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--space-2) var(--space-4);
    font-size: var(--text-2xl);
    font-weight: 700;
}

.result-card__r {
    font-size: var(--text-lg);
}

.result-card__note,
.meta {
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

.result-card .button {
    align-self: flex-start;
}

.notes {
    margin-top: var(--space-3);
    margin-bottom: var(--space-4);
    white-space: pre-wrap; /* keep the user's line breaks; text is still escaped */
    overflow-wrap: anywhere;
    max-width: 75ch;
}

/* ---------- Delete confirmation ---------- */
.confirm {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
    max-width: 40rem;
}

.confirm__actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--space-3);
}

.data-list--compact dd {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
    gap: var(--space-2);
}

/* ---------- Instruments ---------- */
.split {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
    align-items: start;
}

@media (min-width: 72rem) {
    .split {
        grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
    }
}

.explainer {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    color: var(--color-text-muted);
}

.explainer h2,
.explainer strong {
    color: var(--color-text);
}

.formula {
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    background: var(--color-bg);
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    overflow-wrap: anywhere;
}

/* ---------- About ---------- */
.prose-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 24rem), 1fr));
    gap: var(--space-5);
    align-items: start;
}

.prose {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    color: var(--color-text-muted);
}

.prose h2,
.prose h3,
.prose strong {
    color: var(--color-text);
}

.prose ul {
    margin: 0;
    padding-left: var(--space-5);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
}

.roadmap {
    display: grid;
    gap: var(--space-2);
}

.roadmap-item {
    display: grid;
    grid-template-columns: 5rem minmax(0, 1fr) auto;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm);
    background: var(--color-surface-raised);
}

.phase-number {
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.phase-title {
    color: var(--color-text);
    font-weight: 600;
}

.phase-status {
    font-size: var(--text-xs);
    font-weight: 700;
}

.status-done .phase-status {
    color: var(--color-positive);
}

.status-next .phase-status {
    color: var(--color-accent);
}

/* ---------- Error pages ---------- */
.error-page {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    max-width: 40rem;
}

.error-code {
    color: var(--color-text-subtle);
    font-size: var(--text-2xl);
    font-weight: 700;
}

.error-page__actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-3);
    margin-top: var(--space-2);
}
```


## 33. `tradeledger/static/js/early.js`

```javascript
// Runs before the page is painted (loaded without "defer" in <head>).
// Adding class="js" lets CSS switch to JavaScript-enhanced layouts, such as
// the collapsible mobile menu, without a visible jump. If JavaScript is off,
// the class is never added and the plain, fully working layout is used.
document.documentElement.classList.add("js");
```


## 34. `tradeledger/static/js/app.js`

```javascript
// TradeLedger: small progressive enhancements used on every page.
//
// Everything here is optional. With JavaScript turned off every page,
// link and form still works; these functions only make the interface
// smoother. No trading calculation happens in the browser.
//
//   initNavigation()     mobile menu: open/close, Escape key, focus handling
//   initFlashMessages()  "Dismiss" buttons on success/error messages
//   initSubmitOnce()     prevents double submission, shows "Saving…"
//   focusErrorSummary()  moves keyboard focus to the list of form errors
(function () {
    "use strict";

    var DESKTOP_QUERY = "(min-width: 64rem)";

    function initNavigation() {
        var sidebar = document.querySelector("[data-sidebar]");
        var toggle = document.querySelector("[data-nav-toggle]");
        var backdrop = document.querySelector("[data-nav-backdrop]");
        if (!sidebar || !toggle) {
            return;
        }

        function isOpen() {
            return sidebar.getAttribute("data-open") === "true";
        }

        function open() {
            sidebar.setAttribute("data-open", "true");
            toggle.setAttribute("aria-expanded", "true");
            document.body.classList.add("nav-open");
            if (backdrop) {
                backdrop.hidden = false;
            }
            var firstLink = sidebar.querySelector(".nav__link");
            if (firstLink) {
                firstLink.focus();
            }
        }

        function close(returnFocus) {
            sidebar.setAttribute("data-open", "false");
            toggle.setAttribute("aria-expanded", "false");
            document.body.classList.remove("nav-open");
            if (backdrop) {
                backdrop.hidden = true;
            }
            if (returnFocus) {
                toggle.focus();
            }
        }

        toggle.addEventListener("click", function () {
            if (isOpen()) {
                close(true);
            } else {
                open();
            }
        });

        document.querySelectorAll("[data-nav-close]").forEach(function (button) {
            button.addEventListener("click", function () {
                close(true);
            });
        });

        if (backdrop) {
            backdrop.addEventListener("click", function () {
                close(true);
            });
        }

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && isOpen()) {
                close(true);
            }
        });

        // If the window grows to desktop width, the menu is always visible,
        // so reset the mobile "open" state.
        var desktop = window.matchMedia(DESKTOP_QUERY);
        desktop.addEventListener("change", function (event) {
            if (event.matches) {
                close(false);
            }
        });
    }

    function initFlashMessages() {
        document.querySelectorAll("[data-flash-close]").forEach(function (button) {
            button.addEventListener("click", function () {
                var message = button.closest("[data-flash]");
                if (message) {
                    message.remove();
                }
            });
        });
    }

    function initSubmitOnce() {
        document.querySelectorAll("form[data-submit-once]").forEach(function (form) {
            form.addEventListener("submit", function (event) {
                if (form.getAttribute("data-submitting") === "true") {
                    event.preventDefault(); // a second click while the first is in flight
                    return;
                }
                form.setAttribute("data-submitting", "true");
                var button = form.querySelector("button[type='submit']");
                if (button) {
                    button.setAttribute("aria-busy", "true");
                    if (button.dataset.busyLabel) {
                        button.dataset.idleLabel = button.textContent;
                        button.textContent = button.dataset.busyLabel;
                    }
                }
            });
        });

        // Returning with the Back button can restore a page from the browser's
        // cache with the form still marked as submitting; reset it.
        window.addEventListener("pageshow", function (event) {
            if (!event.persisted) {
                return;
            }
            document.querySelectorAll("form[data-submitting='true']").forEach(function (form) {
                form.removeAttribute("data-submitting");
                var button = form.querySelector("button[type='submit']");
                if (button) {
                    button.removeAttribute("aria-busy");
                    if (button.dataset.idleLabel) {
                        button.textContent = button.dataset.idleLabel;
                    }
                }
            });
        });
    }

    function focusErrorSummary() {
        var summary = document.querySelector("[data-error-summary]");
        if (summary) {
            summary.focus();
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        initNavigation();
        initFlashMessages();
        initSubmitOnce();
        focusErrorSummary();
    });
})();
```


## 35. `tradeledger/static/js/trade-form.js`

```javascript
// Trade form: show the exit fields only when the trade is CLOSED.
//
// When the user chooses OPEN:
//   * the "Exit" fieldset is disabled and hidden. A disabled fieldset's
//     inputs are not submitted, so stale exit values are never sent;
//   * a note explains that realised results appear after closing.
// When the user chooses CLOSED:
//   * the exit fields are shown and marked required (exit time is not
//     required when editing a legacy trade whose exit time was never
//     recorded: the form carries data-legacy-close).
//
// This is a convenience only. The server applies exactly the same rules
// (validation.py) whether or not this script runs.
(function () {
    "use strict";

    function init(form) {
        var statusControl = form.querySelector("[data-status-control]");
        var exitFields = form.querySelector("[data-exit-fields]");
        var openNote = form.querySelector("[data-open-note]");
        if (!statusControl || !exitFields) {
            return;
        }
        var exitPrice = exitFields.querySelector("[name='exit_price']");
        var closedAt = exitFields.querySelector("[name='closed_at']");
        var legacyClose = form.hasAttribute("data-legacy-close");

        function selectedStatus() {
            var checked = statusControl.querySelector("input[name='status']:checked");
            return checked ? checked.value : null;
        }

        function sync() {
            var isOpen = selectedStatus() === "OPEN";
            exitFields.disabled = isOpen;
            exitFields.hidden = isOpen;
            if (openNote) {
                openNote.hidden = !isOpen;
            }
            if (exitPrice) {
                exitPrice.required = !isOpen;
            }
            if (closedAt) {
                closedAt.required = !isOpen && !legacyClose;
            }
        }

        statusControl.addEventListener("change", sync);
        sync();
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-trade-form]").forEach(init);
    });
})();
```


## 36. `tests/web_helpers.py`

```python
"""Helpers for the Phase 5 website tests.

The website protects every POST with a CSRF token (tradeledger/web/csrf.py).
A real browser receives the token in a hidden form field and sends it back.
These helpers do the same thing with Flask's test client: load a page,
read the token out of the HTML, and include it in the POST.
"""

import re

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
    with app.app_context():
        row = get_db().execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        return dict(row) if row else None


def new_trade_id(response):
    """The id in a redirect such as /trades/17."""
    match = re.search(r"/trades/(\d+)$", response.headers["Location"])
    assert match, response.headers.get("Location")
    return int(match.group(1))
```


## 37. `tests/test_web_pages.py`

```python
"""Phase 5: every website page renders, shows real data, and the shell
(navigation, headings, preview labelling) behaves as documented."""

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
    assert 'id="main"' in html          # skip-link target
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


@pytest.mark.parametrize("url, label", [
    ("/", "Overview"),
    ("/trades", "Trade journal"),
    ("/trades/new", "Add trade"),
    ("/instruments", "Instruments"),
    ("/about", "About"),
])
def test_current_section_is_marked_for_assistive_technology(client, url, label):
    html = client.get(url).get_data(as_text=True)
    marker = html.index('aria-current="page"')
    # The label of the marked link follows the attribute closely.
    assert label in html[marker:marker + 200]
    assert html.count('aria-current="page"') == 1


def test_trade_pages_highlight_the_journal(seeded_client):
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    marker = html.index('aria-current="page"')
    assert "Trade journal" in html[marker:marker + 200]


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


@pytest.mark.parametrize("path", ["/static/js/app.js", "/static/js/early.js",
                                  "/static/js/trade-form.js", "/static/css/style.css",
                                  "/static/css/base.css", "/static/css/layout.css",
                                  "/static/css/components.css", "/static/css/pages.css"])
def test_static_assets_are_served(client, path):
    assert client.get(path).status_code == 200


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
    for heading in ("Entry time", "Instrument", "Status", "Entry", "Stop-loss", "Exit",
                    "Size", "Net P&amp;L", "realised R", "Strategy", "Actions"):
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
    assert "393.00 USD" in html          # sample trade 1
    assert "GBP" in html                 # UK100 trades are in GBP


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


@pytest.mark.parametrize("query", ["status=MAYBE", "start=2026-13-01", "instrument_id=abc",
                                   "start=2026-03-10&end=2026-03-01", "q=" + "x" * 101])
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
    assert "393.00 USD" in html       # net P&L
    assert "400.00 USD" in html       # gross
    assert "200.00 USD" in html       # initial risk
    assert "+1.97R" in html           # realised R, display-rounded
    assert "Edit" in html and "Delete" in html


def test_detail_for_open_trade_has_no_invented_result(seeded_client):
    html = seeded_client.get("/trades/14").get_data(as_text=True)
    assert ">Open</span>" in html
    assert "Not closed yet" in html
    assert "no realised result yet" in html
    assert "(after fees)" not in html                 # the result card is absent
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
    assert "393.00 USD" in html          # the result is still computed


# ---------- Instruments, analytics, about ----------

def test_instruments_page_explains_multiplier_and_warns(seeded_client):
    html = seeded_client.get("/instruments").get_data(as_text=True)
    assert "Contract multiplier" in html
    assert "broker" in html
    assert "EURUSD" in html
    assert "/edit" not in html            # instruments are not editable


def test_analytics_is_clearly_a_preview(seeded_client):
    html = seeded_client.get("/analytics").get_data(as_text=True)
    assert "Preview" in html
    assert "Phase 6" in html
    assert "By instrument" in html and "By strategy" in html


def test_sidebar_labels_analytics_as_preview(client):
    html = client.get("/").get_data(as_text=True)
    analytics = html.index(">Analytics<")
    assert "Preview" in html[analytics:analytics + 200]


def test_about_shows_roadmap_and_boundaries(client):
    html = client.get("/about").get_data(as_text=True)
    assert "Build progress" in html
    assert "Phase 6" in html
```


## 38. `tests/test_web_forms.py`

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
    assert response.status_code == 302                      # POST-Redirect-GET
    trade_id = new_trade_id(response)
    assert count_rows(app, "trades") == before + 1

    stored = fetch_trade(app, trade_id)
    assert stored["status"] == "CLOSED"
    assert stored["trade_date"] == "2026-04-01T08:15:00Z"   # normalised to UTC
    assert stored["closed_at"] == "2026-04-01T11:40:00Z"
    assert stored["entry_price"] == "1.085"                 # canonical decimal text
    assert stored["strategy"] == "Breakout"
    assert stored["notes"] == "Website test trade (synthetic)"

    page = seeded_client.get(response.headers["Location"]).get_data(as_text=True)
    assert f"Trade #{trade_id} saved." in page             # flash message
    assert "393.00 USD" in page and "+1.97R" in page        # engine values


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
    form = closed_trade_form(status="OPEN")      # exit fields still filled in
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
    stored = fetch_trade(app, new_trade_id(post_form(seeded_client, "/trades/new", closed_trade_form(fees=""))))
    assert stored["fees"] == "0"


def test_optional_text_fields_may_be_blank(app, seeded_client):
    form = closed_trade_form(strategy="", notes="", session="", take_profit="")
    stored = fetch_trade(app, new_trade_id(post_form(seeded_client, "/trades/new", form)))
    assert stored["strategy"] is None and stored["notes"] is None
    assert stored["session"] is None and stored["take_profit"] is None


def test_short_trade_with_stop_above_entry(app, seeded_client):
    form = closed_trade_form(direction="SHORT", entry_price="1.08500", stop_loss="1.08700",
                             take_profit="1.08100", exit_price="1.08200")
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
        assert f'href="#field-{field}"' in html                 # summary links to the field
    assert count_rows(app, "trades") == before                  # nothing written
    return html


@pytest.mark.parametrize("field", ["instrument_id", "trade_date", "entry_price", "stop_loss", "position_size"])
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
    response = post_form(seeded_client, "/trades/new",
                         closed_trade_form(closed_at="2026-04-01T08:00"))
    _assert_rejected(app, response, before, "closed_at")


@pytest.mark.parametrize("field, value", [
    ("entry_price", "abc"), ("entry_price", "-1"), ("position_size", "0"),
    ("fees", "-5"), ("instrument_id", "999"), ("direction", "SIDEWAYS"),
    ("status", "MAYBE"), ("trade_date", "yesterday"), ("session", "MOON"),
])
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
    assert "My &lt;b&gt;setup&lt;/b&gt;" in html       # kept, and escaped
    assert "Keep me</textarea>" in html


def test_rejected_form_keeps_the_chosen_radio_buttons(seeded_client):
    import re
    form = closed_trade_form(direction="SHORT", stop_loss="1.08000")   # wrong side for a short
    html = post_form(seeded_client, "/trades/new", form).get_data(as_text=True)
    short = re.search(r'<input[^>]*value="SHORT"[^>]*>', html).group(0)
    long_ = re.search(r'<input[^>]*value="LONG"[^>]*>', html).group(0)
    assert "checked" in short and "checked" not in long_


# ---------- Edit ----------

def test_edit_form_is_prefilled(seeded_client):
    html = seeded_client.get("/trades/1/edit").get_data(as_text=True)
    assert 'value="1.085"' in html
    assert 'value="2026-03-02T08:15:00"' in html           # datetime-local, seconds kept
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
    form = closed_trade_form(trade_date="2026-03-19T14:35", closed_at="2026-03-19T18:00",
                             instrument_id="4", entry_price="18500", stop_loss="18440",
                             take_profit="18620", exit_price="18600", fees="0.75")
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
    response = post_form(seeded_client, "/trades/1/edit",
                         closed_trade_form(closed_at="", notes="Legacy edit"))
    assert response.status_code == 302
    stored = fetch_trade(app, 1)
    assert stored["closed_at"] is None          # still unknown, never invented
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
    "symbol": "USDJPY", "name": "US Dollar / Yen (test)", "asset_class": "FOREX",
    "price_increment": "0.001", "contract_multiplier": "100000", "quote_currency": "JPY",
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
    response = post_form(seeded_client, "/instruments", {**INSTRUMENT, "symbol": "EURUSD"},
                         token_page="/instruments")
    assert response.status_code == 400
    assert b"already recorded" in response.data
    assert count_rows(app, "instruments") == before


@pytest.mark.parametrize("field, value", [
    ("symbol", ""), ("contract_multiplier", "0"), ("price_increment", "-1"),
    ("quote_currency", "dollars"), ("asset_class", "BEANS"),
])
def test_invalid_instrument_is_rejected_and_values_kept(app, seeded_client, field, value):
    before = count_rows(app, "instruments")
    response = post_form(seeded_client, "/instruments", {**INSTRUMENT, field: value},
                         token_page="/instruments")
    assert response.status_code == 400
    assert count_rows(app, "instruments") == before
    assert b"US Dollar / Yen (test)" in response.data       # typed values kept


def test_token_is_the_same_across_pages_in_one_session(seeded_client):
    assert csrf_token(seeded_client, "/trades/new") == csrf_token(seeded_client, "/instruments")
```


## 39. `tests/test_web_security.py`

```python
"""Phase 5 security: CSRF, output escaping, safe redirects, HTTP methods,
security headers, cookies and the production journal guard."""

import logging
import re

import pytest

from tradeledger import create_app
from tradeledger.db import get_db, init_schema
from tradeledger.web.forms import safe_next
from tests.web_helpers import (
    closed_trade_form,
    count_rows,
    csrf_token,
    fetch_trade,
    new_trade_id,
    post_form,
)

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
    csrf_token(seeded_client)                      # the session now has a real token
    trades = count_rows(app, "trades")
    response = seeded_client.post(url, data={**closed_trade_form(), "csrf_token": "forged-value"})
    assert response.status_code == 400
    assert count_rows(app, "trades") == trades


def test_token_from_another_session_is_rejected(app, seeded_client):
    stolen = csrf_token(app.test_client())         # a different browser's token
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
    assert 'href="/trades/1/delete' in html          # a link to the confirmation page...
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


@pytest.mark.parametrize("method, url", [
    ("put", "/trades/1"), ("delete", "/trades/1"), ("post", "/trades/1"),
    ("post", "/trades"), ("post", "/"), ("put", "/instruments"), ("delete", "/trades/1/delete"),
])
def test_unsupported_methods_are_405(seeded_client, method, url):
    response = getattr(seeded_client, method)(url)
    assert response.status_code == 405


# ---------- Escaping (stored XSS) ----------

PAYLOAD = '<script>alert("x")</script><img src=x onerror=alert(1)>'


def test_malicious_text_is_escaped_everywhere(app, seeded_client):
    form = closed_trade_form(strategy='<script>alert("s")</script>', notes=PAYLOAD)  # strategy max 50
    trade_id = new_trade_id(post_form(seeded_client, "/trades/new", form))
    assert fetch_trade(app, trade_id)["notes"] == PAYLOAD          # stored exactly as typed

    for url in ("/", "/trades", f"/trades/{trade_id}", f"/trades/{trade_id}/edit",
                f"/trades/{trade_id}/delete", "/analytics", "/trades?q=alert"):
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
    post_form(seeded_client, "/instruments", {
        "symbol": "EVIL", "name": "<b onmouseover=alert(1)>x</b>", "asset_class": "OTHER",
        "price_increment": "1", "contract_multiplier": "1", "quote_currency": "USD",
    }, token_page="/instruments")
    for url in ("/instruments", "/trades/new"):
        html = seeded_client.get(url).get_data(as_text=True)
        assert "<b onmouseover" not in html
        assert "&lt;b onmouseover=alert(1)&gt;" in html


# ---------- Safe redirects ----------

@pytest.mark.parametrize("target", [
    "https://evil.example/", "//evil.example/", "/\\evil.example", "\\\\evil.example",
    "javascript:alert(1)", "http:/evil.example", "trades", "", None, "///evil.example",
    "//%2Fevil.example",
])
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
    app = create_app("production", overrides={
        "SECRET_KEY": "a-real-secret-from-the-environment", "DATABASE": db_path, **overrides})
    with app.app_context():
        init_schema(get_db())
    return app


def test_production_session_cookie_is_secure(db_path):
    assert _production(db_path).config["SESSION_COOKIE_SECURE"] is True


# ---------- Production journal guard ----------

@pytest.mark.parametrize("url", ["/trades", "/trades/new", "/trades/1", "/trades/1/edit",
                                 "/trades/1/delete", "/instruments", "/analytics"])
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
    assert _production(db_path).test_client().get(url).status_code == 200


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


## 40. `tests/test_web_integration.py`

```python
"""Phase 5 end to end (inside the test client): form -> SQLite -> journal ->
details -> analytics -> API, plus the repository search added for the
journal."""

import pytest

from tradeledger.repositories import trades as trade_repo
from tradeledger.validation import ValidationError
from tests.web_helpers import (
    closed_trade_form,
    count_rows,
    new_trade_id,
    open_trade_form,
    post_form,
)


# ---------- Full journeys ----------

def test_trade_lifecycle_open_close_edit_delete(app, client):
    """Start from an EMPTY database, as a new user would."""
    # 1. Add an instrument through the website.
    response = post_form(client, "/instruments", {
        "symbol": "EURUSD", "name": "Euro / US Dollar (test)", "asset_class": "FOREX",
        "price_increment": "0.00001", "contract_multiplier": "100000", "quote_currency": "USD",
    }, token_page="/instruments")
    assert response.status_code == 302

    # 2. Record an open trade.
    trade_id = new_trade_id(post_form(client, "/trades/new", open_trade_form()))
    journal = client.get("/trades").get_data(as_text=True)
    assert "Showing 1–1 of 1 trade." in journal
    assert ">Open</span>" in journal
    overview = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" in overview          # open trades have no result

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
    web_id = new_trade_id(post_form(seeded_client, "/trades/new", closed_trade_form(
        trade_date="2026-04-01T08:15", closed_at="2026-04-01T11:40",
        notes=trade_body["notes"])))
    api_id = seeded_client.post("/api/v1/trades", json=trade_body).get_json()["data"]["id"]

    web = seeded_client.get(f"/api/v1/trades/{web_id}").get_json()["data"]
    api = seeded_client.get(f"/api/v1/trades/{api_id}").get_json()["data"]
    for volatile in ("id", "created_at", "updated_at"):
        web.pop(volatile, None)
        api.pop(volatile, None)
    assert web == api


def test_trade_added_by_api_appears_on_the_website(seeded_client, trade_body):
    trade_id = seeded_client.post("/api/v1/trades", json={**trade_body, "notes": "from-the-api"}).get_json()["data"]["id"]
    assert f'href="/trades/{trade_id}"' in seeded_client.get("/trades?q=from-the-api").get_data(as_text=True)


def test_website_edits_change_the_analytics(seeded_client):
    before = seeded_client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    post_form(seeded_client, "/trades/1/delete", {})
    after = seeded_client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    assert after["trades"] == before["trades"] - 1
    assert after["wins"] == before["wins"] - 1            # trade 1 was a win


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


## 41. `tests/test_browser.py`

```python
"""Phase 5: optional real-browser checks with Playwright.

These start TradeLedger on a local port in a background thread and drive a
real Chromium browser, so they test what the test client cannot: CSS
layout, JavaScript behaviour, focus handling and the Content-Security-Policy.

They are OPTIONAL. Playwright is not in requirements.txt (it downloads a
~150 MB browser). If it, or its browser, is missing, every test here is
skipped and the rest of the suite is unaffected. To run them:

    pip install playwright
    python -m playwright install chromium
    pytest tests/test_browser.py
"""

import threading

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

WIDTHS = [375, 768, 1024, 1440]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("browser") / "browser.db")
    app = create_app("testing", overrides={"DATABASE": db_path})
    with app.app_context():
        init_schema(get_db())
        seed_sample_data(get_db())
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            launched = p.chromium.launch()
        except Exception as exc:                      # browser binary not installed
            pytest.skip(f"Chromium is not available: {exc}")
        yield launched
        launched.close()


@pytest.fixture
def page_factory(browser):
    contexts = []

    def make(width=1440, height=900):
        context = browser.new_context(viewport={"width": width, "height": height})
        contexts.append(context)
        page = context.new_page()
        page.problems = []
        page.on("pageerror", lambda exc: page.problems.append(str(exc)))
        page.on("console", lambda msg: page.problems.append(msg.text)
                if msg.type == "error" and "status of 4" not in msg.text else None)
        return page

    yield make
    for context in contexts:
        context.close()


def horizontal_overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("path", ["/", "/trades", "/trades/new", "/trades/1", "/trades/1/delete",
                                  "/instruments", "/analytics", "/about"])
def test_no_horizontal_scrolling_and_no_errors(live_server, page_factory, path, width):
    page = page_factory(width)
    page.goto(live_server + path)
    assert horizontal_overflow(page) == 0
    assert page.problems == []                        # includes CSP violations


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
    page.fill("#field-stop_loss", "1.1050")            # wrong side for a long
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
    assert page.evaluate("window.pwned === undefined")             # rendered as text
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
    button.click()                                   # an impatient second click
    assert page.evaluate("window.submissions") == 1
    assert page.locator("form[data-submit-once]").get_attribute("data-submitting") == "true"
    assert button.get_attribute("aria-busy") == "true"
    assert button.inner_text() == "Deleting…"
```


## 42. `tests/test_app.py`

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

    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", "a-real-secret-from-env")
    app = create_app("production")
    assert app.config["SECRET_KEY"] == "a-real-secret-from-env"


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
    response = client.get("/static/css/style.css")
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


## 43. `tests/test_openapi.py`

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
        path = re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", rule.rule)[len("/api/v1"):]
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            routes.add((path, method.lower()))
    return routes


def spec_routes():
    return {(path, method) for path, item in SPEC["paths"].items()
            for method in item if method in ("get", "post", "put", "delete", "patch")}


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
    for name, schema in SCHEMAS.items():
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
    body = {"symbol": "US30", "name": "Example", "asset_class": "INDEX", "price_increment": "1",
            "contract_multiplier": "1", "quote_currency": "USD"}
    validate(body, "InstrumentInput")
    validate(seeded_client.post("/api/v1/instruments", json=body).get_json(), "InstrumentEnvelope")
    validate(seeded_client.get("/api/v1/instruments").get_json(), "InstrumentList")
    validate(seeded_client.get("/api/v1/instruments/1").get_json(), "InstrumentEnvelope")


@pytest.mark.parametrize("endpoint, schema", [
    ("summary", "SummaryAnalytics"), ("instruments", "GroupedAnalytics"),
    ("strategies", "GroupedAnalytics"), ("sessions", "GroupedAnalytics"),
    ("weekday", "GroupedAnalytics"), ("monthly", "GroupedAnalytics"), ("curve", "CurveAnalytics"),
])
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

    trade = make_trade(stop_loss=Decimal("105"), instrument_id=1, closed_at="2026-01-05T11:00:00Z",
                       take_profit=None, notes=None, created_at="2026-01-05T11:00:00Z",
                       updated_at="2026-01-05T11:00:00Z")
    body = trade_json(trade)
    assert body["result"]["realised_r"] is None and body["result"]["initial_risk"] is None
    assert body["result"]["r_unavailable_reason"]
    validate({"data": body}, "TradeEnvelope")


@pytest.mark.parametrize("method, url, kwargs, status", [
    ("post", "/api/v1/trades", {"json": {"position_size": "0"}}, 400),
    ("post", "/api/v1/trades", {"data": "x", "content_type": "text/plain"}, 415),
    ("get", "/api/v1/trades/999", {}, 404),
    ("delete", "/api/v1/instruments", {}, 405),
    ("post", "/api/v1/instruments", {"json": {"symbol": "EURUSD", "name": "x", "asset_class": "FOREX",
                                              "price_increment": "1", "contract_multiplier": "1",
                                              "quote_currency": "USD"}}, 409),
])
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
        "docs/openapi.json is stale: run  flask --app tradeledger openapi")


def test_openapi_cli_writes_the_spec(runner, tmp_path):
    out = tmp_path / "spec.json"
    result = runner.invoke(args=["openapi", "--output", str(out)])
    assert result.exit_code == 0
    assert json.loads(out.read_text(encoding="utf-8")) == SPEC
```
