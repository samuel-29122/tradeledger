# TradeLedger — Phase 2 Complete Code Listing

Generated directly from the files that passed the 145-test run, so this listing is identical to the tested code.
Unchanged Phase 1 files (templates, CSS, pytest.ini, .gitignore, requirements.txt) are not repeated; see PHASE_1_GUIDE.md or the zip.

## Contents

- `tradeledger/schema.sql` — NEW
- `tradeledger/db.py` — NEW
- `tradeledger/validation.py` — NEW
- `tradeledger/repositories/__init__.py` — NEW
- `tradeledger/repositories/instruments.py` — NEW
- `tradeledger/repositories/trades.py` — NEW
- `tradeledger/seed.py` — NEW
- `tradeledger/analytics.py` — NEW
- `tradeledger/cli.py` — NEW
- `tradeledger/__init__.py` — MODIFIED
- `tradeledger/config.py` — MODIFIED
- `tradeledger/routes.py` — MODIFIED
- `tests/conftest.py` — MODIFIED
- `tests/test_app.py` — MODIFIED
- `tests/test_db.py` — NEW
- `tests/test_validation.py` — NEW
- `tests/test_repositories.py` — NEW
- `tests/test_seed_and_analytics.py` — NEW
- `.env.example` — MODIFIED

---

## `tradeledger/schema.sql` (NEW)

**Purpose:** Defines both tables, every constraint and the indexes. Run by `init_schema()` in db.py.

```sql
-- TradeLedger database schema (SQLite)
--
-- Conventions
--   * Exact decimal values (prices, sizes, fees, multipliers) are stored as
--     canonical decimal TEXT, e.g. '1.08542'. Python converts them to
--     decimal.Decimal for calculations. They are never stored as REAL,
--     because binary floating point cannot represent most decimals exactly.
--   * Every timestamp is UTC in ISO 8601 form: 'YYYY-MM-DDTHH:MM:SSZ'.
--     Text in this form sorts in time order, so ORDER BY and range
--     filters work correctly.
--   * Enumerated values (direction, status, ...) are UPPER CASE.
--
-- Safe to run more than once: IF NOT EXISTS means nothing is dropped.

PRAGMA user_version = 1;  -- schema version, used for future migrations

CREATE TABLE IF NOT EXISTS instruments (
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT    NOT NULL UNIQUE
                                CHECK (symbol = upper(symbol)
                                       AND length(symbol) BETWEEN 1 AND 20),
    name                TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    asset_class         TEXT    NOT NULL
                                CHECK (asset_class IN ('FOREX', 'COMMODITY', 'INDEX', 'STOCK', 'OTHER')),
    price_increment     TEXT    NOT NULL
                                CHECK (price_increment NOT GLOB '*[^0-9.]*'
                                       AND price_increment GLOB '*[0-9]*'
                                       AND CAST(price_increment AS REAL) > 0),
    contract_multiplier TEXT    NOT NULL
                                CHECK (contract_multiplier NOT GLOB '*[^0-9.]*'
                                       AND contract_multiplier GLOB '*[0-9]*'
                                       AND CAST(contract_multiplier AS REAL) > 0),
    quote_currency      TEXT    NOT NULL
                                CHECK (length(quote_currency) = 3
                                       AND quote_currency = upper(quote_currency)),
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY,
    instrument_id   INTEGER NOT NULL
                            REFERENCES instruments (id) ON DELETE RESTRICT,
    trade_date      TEXT    NOT NULL
                            CHECK (trade_date GLOB
                                   '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    direction       TEXT    NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price     TEXT    NOT NULL
                            CHECK (entry_price NOT GLOB '*[^0-9.]*'
                                   AND entry_price GLOB '*[0-9]*'
                                   AND CAST(entry_price AS REAL) > 0),
    stop_loss       TEXT    NOT NULL
                            CHECK (stop_loss NOT GLOB '*[^0-9.]*'
                                   AND stop_loss GLOB '*[0-9]*'
                                   AND CAST(stop_loss AS REAL) > 0),
    take_profit     TEXT
                            CHECK (take_profit IS NULL
                                   OR (take_profit NOT GLOB '*[^0-9.]*'
                                       AND take_profit GLOB '*[0-9]*'
                                       AND CAST(take_profit AS REAL) > 0)),
    exit_price      TEXT
                            CHECK (exit_price IS NULL
                                   OR (exit_price NOT GLOB '*[^0-9.]*'
                                       AND exit_price GLOB '*[0-9]*'
                                       AND CAST(exit_price AS REAL) > 0)),
    position_size   TEXT    NOT NULL
                            CHECK (position_size NOT GLOB '*[^0-9.]*'
                                   AND position_size GLOB '*[0-9]*'
                                   AND CAST(position_size AS REAL) > 0),
    fees            TEXT    NOT NULL DEFAULT '0'
                            CHECK (fees NOT GLOB '*[^0-9.]*'
                                   AND fees GLOB '*[0-9]*'),
    status          TEXT    NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    strategy        TEXT    CHECK (strategy IS NULL OR length(strategy) BETWEEN 1 AND 50),
    session         TEXT    CHECK (session IS NULL
                                   OR session IN ('ASIA', 'LONDON', 'NEW_YORK', 'OTHER')),
    notes           TEXT    CHECK (notes IS NULL OR length(notes) <= 2000),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    -- A closed trade must have an exit price; an open trade must not.
    CONSTRAINT status_matches_exit CHECK (
        (status = 'OPEN'   AND exit_price IS NULL) OR
        (status = 'CLOSED' AND exit_price IS NOT NULL)
    ),

    -- Stop-loss must be on the losing side of entry (never equal: zero risk).
    CONSTRAINT stop_on_correct_side CHECK (
        (direction = 'LONG'  AND CAST(stop_loss AS REAL) < CAST(entry_price AS REAL)) OR
        (direction = 'SHORT' AND CAST(stop_loss AS REAL) > CAST(entry_price AS REAL))
    ),

    -- Take-profit, when given, must be on the winning side of entry.
    CONSTRAINT target_on_correct_side CHECK (
        take_profit IS NULL OR
        (direction = 'LONG'  AND CAST(take_profit AS REAL) > CAST(entry_price AS REAL)) OR
        (direction = 'SHORT' AND CAST(take_profit AS REAL) < CAST(entry_price AS REAL))
    )
);

-- Indexes speed up the filters we use most often.
CREATE INDEX IF NOT EXISTS idx_trades_instrument_id ON trades (instrument_id);
CREATE INDEX IF NOT EXISTS idx_trades_trade_date    ON trades (trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_status        ON trades (status);
```

---

## `tradeledger/db.py` (NEW)

**Purpose:** Opens SQLite connections (foreign keys on, dict-like rows), gives each Flask request one connection via `get_db()`, closes it on teardown, and loads schema.sql.

```python
"""SQLite connection management.

Two ways to get a connection:

* connect(path)  - a plain connection, usable anywhere (scripts, tests).
* get_db()       - inside a Flask request or CLI command: one connection per
                   request, stored on flask.g and closed automatically when
                   the request ends.

All repository functions take a connection as their first argument, so they
never need to know which of these created it.
"""

import sqlite3
from importlib import resources

from flask import current_app, g


def connect(path):
    """Open a SQLite connection configured the way TradeLedger expects."""
    conn = sqlite3.connect(path)

    # Rows behave like dictionaries: row["symbol"] instead of row[1].
    conn.row_factory = sqlite3.Row

    # SQLite does NOT enforce foreign keys unless you switch them on,
    # and the setting applies per connection.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def get_db():
    """Return this request's connection, opening it on first use."""
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(exception=None):
    """Close the request's connection, if one was opened."""
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def read_schema():
    """Return the text of schema.sql, which ships inside the package."""
    return resources.files("tradeledger").joinpath("schema.sql").read_text(encoding="utf-8")


def init_schema(conn):
    """Create any missing tables and indexes. Never deletes data."""
    conn.executescript(read_schema())


def drop_all(conn):
    """Delete every table. Used only by the reset-db command."""
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS trades;
        DROP TABLE IF EXISTS instruments;
        PRAGMA foreign_keys = ON;
        """
    )


def schema_version(conn):
    """The number stored by 'PRAGMA user_version' in schema.sql."""
    return conn.execute("PRAGMA user_version").fetchone()[0]


def init_app(app):
    """Register the close-connection hook with the Flask app."""
    app.teardown_appcontext(close_db)
```

---

## `tradeledger/validation.py` (NEW)

**Purpose:** Turns raw input into clean, canonical values or raises `ValidationError` listing every problem. Used by the repositories and seed.py before anything is written.

```python
"""Input validation for instruments and trades.

Each validate_* function takes raw input (a dict of strings or numbers,
e.g. from a web form) and returns a *clean* dict ready to store, or raises
ValidationError listing every problem found.

Python validation is the first line of defence and gives friendly messages.
The CHECK constraints in schema.sql are the last line of defence: they stop
bad data even if some future code forgets to validate.
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation

ASSET_CLASSES = ("FOREX", "COMMODITY", "INDEX", "STOCK", "OTHER")
DIRECTIONS = ("LONG", "SHORT")
STATUSES = ("OPEN", "CLOSED")
SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "OTHER")

# More significant digits than this cannot be compared exactly by SQLite's
# CAST(... AS REAL) inside the CHECK constraints, so we refuse them.
MAX_SIGNIFICANT_DIGITS = 15

TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


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
    if isinstance(value, bool) or isinstance(value, float):
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
        moment = moment.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
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
        return datetime.combine(day, time(0, 0), tzinfo=timezone.utc).strftime(TIMESTAMP_FORMAT)

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


def validate_trade(data):
    """Validate trade input. Returns a clean dict or raises ValidationError.

    Rules (Python mirrors and extends the SQL constraints):
      * instrument_id is a positive whole number
      * trade_date is a valid date-time, stored as UTC
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
        if isinstance(instrument_id, bool) or isinstance(instrument_id, float):
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

---

## `tradeledger/repositories/__init__.py` (NEW)

**Purpose:** Marks the folder as a package and defines `DuplicateError`.

```python
"""Repository functions: the only code in TradeLedger that writes SQL for
instruments and trades.

Every function takes an open sqlite3 connection as its first argument.
"""


class DuplicateError(Exception):
    """Raised when a record would break a UNIQUE rule (e.g. a repeated symbol)."""
```

---

## `tradeledger/repositories/instruments.py` (NEW)

**Purpose:** Create, fetch and list instruments. Returns Decimals for numeric columns.

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

_COLUMNS = (
    "id, symbol, name, asset_class, price_increment, "
    "contract_multiplier, quote_currency, created_at"
)


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
        f"SELECT {_COLUMNS} FROM instruments WHERE id = ?", (instrument_id,)
    ).fetchone()
    return _to_dict(row)


def get_instrument_by_symbol(conn, symbol):
    if not isinstance(symbol, str):
        return None
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM instruments WHERE symbol = ?", (symbol.strip().upper(),)
    ).fetchone()
    return _to_dict(row)


def list_instruments(conn):
    rows = conn.execute(f"SELECT {_COLUMNS} FROM instruments ORDER BY symbol").fetchall()
    return [_to_dict(row) for row in rows]
```

---

## `tradeledger/repositories/trades.py` (NEW)

**Purpose:** Create, fetch, list/filter, update and delete trades. The only code that writes trade SQL.

```python
"""Trade repository.

Return values
  create_trade   -> new id (int)
  get_trade      -> dict (includes the instrument's symbol), or None
  list_trades    -> list of dicts, newest trade_date first (empty list if none)
  update_trade   -> True if a trade was updated, False if the id does not exist
  delete_trade   -> True if a trade was deleted, False if the id does not exist

Errors
  ValidationError  invalid input, including an unknown instrument_id
                   (nothing is written)

Numeric fields come back as decimal.Decimal (or None), never float.
"""

import sqlite3
from decimal import Decimal

from ..validation import ValidationError, date_range_bounds, validate_trade

_DECIMAL_FIELDS = ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees")

_WRITE_FIELDS = (
    "instrument_id", "trade_date", "direction", "entry_price", "stop_loss",
    "take_profit", "exit_price", "position_size", "fees", "status",
    "strategy", "session", "notes",
)

_SELECT = """
    SELECT t.id, t.instrument_id, i.symbol, t.trade_date, t.direction,
           t.entry_price, t.stop_loss, t.take_profit, t.exit_price,
           t.position_size, t.fees, t.status, t.strategy, t.session,
           t.notes, t.created_at, t.updated_at
    FROM trades AS t
    JOIN instruments AS i ON i.id = t.instrument_id
"""


def _to_dict(row):
    if row is None:
        return None
    record = dict(row)
    for field in _DECIMAL_FIELDS:
        if record[field] is not None:
            record[field] = Decimal(record[field])
    return record


def _raise_if_unknown_instrument(exc):
    """Translate SQLite's foreign-key error into a friendly ValidationError."""
    if "FOREIGN KEY" in str(exc):
        raise ValidationError({"instrument_id": "does not match an existing instrument"}) from exc
    raise exc


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
        _raise_if_unknown_instrument(exc)
    return cursor.lastrowid


def get_trade(conn, trade_id):
    row = conn.execute(_SELECT + " WHERE t.id = ?", (trade_id,)).fetchone()
    return _to_dict(row)


def list_trades(conn, *, instrument_id=None, start=None, end=None, status=None):
    """List trades, optionally filtered.

    instrument_id  only this instrument
    start, end     inclusive dates 'YYYY-MM-DD' (UTC) on trade_date
    status         'OPEN' or 'CLOSED'
    """
    lower, upper = date_range_bounds(start, end)

    # Build the WHERE clause from fixed text fragments only. User values
    # never become part of the SQL string; they go in `params`.
    conditions, params = [], []
    if instrument_id is not None:
        conditions.append("t.instrument_id = ?")
        params.append(instrument_id)
    if lower is not None:
        conditions.append("t.trade_date >= ?")
        params.append(lower)
    if upper is not None:
        conditions.append("t.trade_date < ?")
        params.append(upper)
    if status is not None:
        if status not in ("OPEN", "CLOSED"):
            raise ValidationError({"status": "must be one of: OPEN, CLOSED"})
        conditions.append("t.status = ?")
        params.append(status)

    sql = _SELECT
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY t.trade_date DESC, t.id DESC"
    return [_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def update_trade(conn, trade_id, data):
    """Replace every editable field of a trade with validated new values."""
    clean = validate_trade(data)
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
        _raise_if_unknown_instrument(exc)
    return cursor.rowcount == 1


def delete_trade(conn, trade_id):
    with conn:
        cursor = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    return cursor.rowcount == 1
```

---

## `tradeledger/seed.py` (NEW)

**Purpose:** Synthetic instruments and 16 synthetic trades; idempotent, all-or-nothing insert.

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
"""

from .validation import validate_instrument, validate_trade

SAMPLE_INSTRUMENTS = [
    {"symbol": "EURUSD", "name": "Euro / US Dollar (sample)", "asset_class": "FOREX",
     "price_increment": "0.00001", "contract_multiplier": "100000", "quote_currency": "USD"},
    {"symbol": "GBPUSD", "name": "British Pound / US Dollar (sample)", "asset_class": "FOREX",
     "price_increment": "0.00001", "contract_multiplier": "100000", "quote_currency": "USD"},
    {"symbol": "XAUUSD", "name": "Gold / US Dollar (sample)", "asset_class": "COMMODITY",
     "price_increment": "0.01", "contract_multiplier": "100", "quote_currency": "USD"},
    {"symbol": "NAS100", "name": "US Tech 100 index CFD (sample)", "asset_class": "INDEX",
     "price_increment": "0.1", "contract_multiplier": "1", "quote_currency": "USD"},
    {"symbol": "UK100", "name": "UK 100 index CFD (sample)", "asset_class": "INDEX",
     "price_increment": "0.1", "contract_multiplier": "1", "quote_currency": "GBP"},
]

# Net P&L in the comment = gross - fees, in the instrument's quote currency.
SAMPLE_TRADES = [
    # 1  WIN  +393.00 USD
    {"symbol": "EURUSD", "trade_date": "2026-03-02T08:15:00Z", "direction": "LONG",
     "entry_price": "1.08500", "stop_loss": "1.08300", "take_profit": "1.08900",
     "exit_price": "1.08900", "position_size": "1", "fees": "7",
     "strategy": "Breakout", "session": "LONDON", "notes": "Sample: clean break of Asian range."},
    # 2  LOSS -103.50 USD
    {"symbol": "EURUSD", "trade_date": "2026-03-03T13:40:00Z", "direction": "SHORT",
     "entry_price": "1.08800", "stop_loss": "1.09000", "take_profit": "1.08400",
     "exit_price": "1.09000", "position_size": "0.5", "fees": "3.5",
     "strategy": "Pullback", "session": "NEW_YORK", "notes": "Sample: stopped out on US data."},
    # 3  BREAKEVEN 0.00 USD
    {"symbol": "GBPUSD", "trade_date": "2026-03-04T08:30:00Z", "direction": "LONG",
     "entry_price": "1.27000", "stop_loss": "1.26800", "take_profit": "1.27400",
     "exit_price": "1.27000", "position_size": "1", "fees": "0",
     "strategy": "Breakout", "session": "LONDON", "notes": "Sample: moved stop to entry."},
    # 4  WIN  +293.00 USD
    {"symbol": "GBPUSD", "trade_date": "2026-03-05T14:05:00Z", "direction": "SHORT",
     "entry_price": "1.27500", "stop_loss": "1.27700", "take_profit": "1.27100",
     "exit_price": "1.27200", "position_size": "1", "fees": "7",
     "strategy": "Reversal", "session": "NEW_YORK", "notes": "Sample: closed early before target."},
    # 5  WIN  +198.00 USD
    {"symbol": "XAUUSD", "trade_date": "2026-03-06T14:30:00Z", "direction": "LONG",
     "entry_price": "2150.00", "stop_loss": "2140.00", "take_profit": "2170.00",
     "exit_price": "2170.00", "position_size": "0.1", "fees": "2",
     "strategy": "Breakout", "session": "NEW_YORK", "notes": None},
    # 6  LOSS -133.00 USD
    {"symbol": "XAUUSD", "trade_date": "2026-03-09T02:10:00Z", "direction": "SHORT",
     "entry_price": "2165.50", "stop_loss": "2172.00", "take_profit": "2150.00",
     "exit_price": "2172.00", "position_size": "0.2", "fees": "3",
     "strategy": "Reversal", "session": "ASIA", "notes": None},
    # 7  WIN  +198.50 USD
    {"symbol": "NAS100", "trade_date": "2026-03-10T14:45:00Z", "direction": "LONG",
     "entry_price": "18250.0", "stop_loss": "18200.0", "take_profit": "18350.0",
     "exit_price": "18350.0", "position_size": "2", "fees": "1.5",
     "strategy": "Pullback", "session": "NEW_YORK", "notes": None},
    # 8  LOSS -81.50 USD
    {"symbol": "NAS100", "trade_date": "2026-03-11T15:00:00Z", "direction": "SHORT",
     "entry_price": "18300.0", "stop_loss": "18340.0", "take_profit": "18200.0",
     "exit_price": "18340.0", "position_size": "2", "fees": "1.5",
     "strategy": "Breakout", "session": "NEW_YORK", "notes": None},
    # 9  LOSS -5.00 USD  (exit ABOVE entry on a long, but fees exceed the gain)
    {"symbol": "EURUSD", "trade_date": "2026-03-12T09:00:00Z", "direction": "LONG",
     "entry_price": "1.09100", "stop_loss": "1.08950", "take_profit": "1.09400",
     "exit_price": "1.09102", "position_size": "1", "fees": "7",
     "strategy": "Pullback", "session": "LONDON", "notes": "Sample: scratched; fees made it a loss."},
    # 10 WIN  +200.00 GBP
    {"symbol": "UK100", "trade_date": "2026-03-13T10:30:00Z", "direction": "LONG",
     "entry_price": "7700.0", "stop_loss": "7680.0", "take_profit": "7740.0",
     "exit_price": "7740.0", "position_size": "5", "fees": "0",
     "strategy": "Breakout", "session": "LONDON", "notes": None},
    # 11 BREAKEVEN 0.00 GBP (no strategy, no target)
    {"symbol": "UK100", "trade_date": "2026-03-16T11:00:00Z", "direction": "SHORT",
     "entry_price": "7750.0", "stop_loss": "7770.0", "take_profit": None,
     "exit_price": "7750.0", "position_size": "5", "fees": "0",
     "strategy": None, "session": "LONDON", "notes": None},
    # 12 LOSS -157.00 USD
    {"symbol": "GBPUSD", "trade_date": "2026-03-17T08:05:00Z", "direction": "LONG",
     "entry_price": "1.28000", "stop_loss": "1.27850", "take_profit": "1.28300",
     "exit_price": "1.27850", "position_size": "1", "fees": "7",
     "strategy": "Pullback", "session": "LONDON", "notes": None},
    # 13 WIN  +158.00 USD
    {"symbol": "XAUUSD", "trade_date": "2026-03-18T03:20:00Z", "direction": "LONG",
     "entry_price": "2180.00", "stop_loss": "2172.00", "take_profit": "2196.00",
     "exit_price": "2196.00", "position_size": "0.1", "fees": "2",
     "strategy": "Pullback", "session": "ASIA", "notes": None},
    # 14 OPEN
    {"symbol": "NAS100", "trade_date": "2026-03-19T14:35:00Z", "direction": "LONG",
     "entry_price": "18500.0", "stop_loss": "18440.0", "take_profit": "18620.0",
     "exit_price": None, "position_size": "1", "fees": "0.75",
     "strategy": "Breakout", "session": "NEW_YORK", "notes": "Sample: still running."},
    # 15 OPEN
    {"symbol": "EURUSD", "trade_date": "2026-03-20T08:45:00Z", "direction": "SHORT",
     "entry_price": "1.09300", "stop_loss": "1.09450", "take_profit": None,
     "exit_price": None, "position_size": "1", "fees": "0",
     "strategy": "Reversal", "session": "LONDON", "notes": None},
    # 16 WIN  +38.00 USD (no strategy, no session)
    {"symbol": "XAUUSD", "trade_date": "2026-03-20T15:10:00Z", "direction": "SHORT",
     "entry_price": "2190.00", "stop_loss": "2198.00", "take_profit": None,
     "exit_price": "2186.00", "position_size": "0.1", "fees": "2",
     "strategy": None, "session": None, "notes": None},
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
                    (instrument_id, trade_date, direction, entry_price, stop_loss,
                     take_profit, exit_price, position_size, fees, status,
                     strategy, session, notes)
                VALUES (:instrument_id, :trade_date, :direction, :entry_price, :stop_loss,
                        :take_profit, :exit_price, :position_size, :fees, :status,
                        :strategy, :session, :notes)
                """,
                trade,
            )
            inserted["trades"] += 1

    return inserted
```

---

## `tradeledger/analytics.py` (NEW)

**Purpose:** Six analytical SQL queries and functions that run them.

```python
"""Analytical SQL queries.

Each query is a named SQL string plus a small function that runs it and
returns plain Python values (lists of dicts or an int).

Definitions used here
---------------------
Closed trade     status = 'CLOSED' (so it has an exit price).
Gross P&L        LONG:  (exit - entry) x size x contract_multiplier
                 SHORT: (entry - exit) x size x contract_multiplier
                 in the instrument's quote currency.
Net P&L          gross P&L - fees  (fees assumed to be in the quote currency)
Win / loss / BE  net P&L > 0 / < 0 / = 0. Fees count, so a long closed
                 slightly above entry can still be a LOSS.
Win rate         wins / closed trades x 100. Breakevens stay in the
                 denominator (they are trades you took).

Why the win-rate query is a cross-check, not the final answer
-------------------------------------------------------------
SQLite has no exact decimal type, so arithmetic on our TEXT prices must go
through CAST(... AS REAL), which is binary floating point. The query treats
|net P&L| below 0.000001 as breakeven to absorb that rounding. Phase 3 does
the authoritative calculation in Python with decimal.Decimal; a test checks
that both agree on the sample data.

Currency limitation
-------------------
Win/loss classification is per trade, and converting a P&L to another
currency never changes its sign, so win rates are valid even when
instruments are quoted in different currencies. SUMMING money across
currencies (e.g. USD + GBP) is NOT valid without exchange rates, which is
why no query here adds up P&L across instruments.
"""

CLOSED_TRADE_COUNT = """
SELECT COUNT(*) AS closed_trades
FROM trades
WHERE status = 'CLOSED';
"""

# LEFT JOIN keeps instruments that have no closed trades (count = 0).
# The status filter is in the ON clause, not WHERE, for exactly that reason.
CLOSED_COUNT_BY_INSTRUMENT = """
SELECT i.symbol,
       COUNT(t.id) AS closed_trades
FROM instruments AS i
LEFT JOIN trades AS t
       ON t.instrument_id = i.id
      AND t.status = 'CLOSED'
GROUP BY i.id, i.symbol
ORDER BY closed_trades DESC, i.symbol;
"""

# strftime('%w') gives 0 = Sunday ... 6 = Saturday, from the UTC trade_date.
CLOSED_BY_WEEKDAY = """
SELECT CAST(strftime('%w', trade_date) AS INTEGER) AS weekday_number,
       CASE strftime('%w', trade_date)
            WHEN '0' THEN 'Sunday'    WHEN '1' THEN 'Monday'
            WHEN '2' THEN 'Tuesday'   WHEN '3' THEN 'Wednesday'
            WHEN '4' THEN 'Thursday'  WHEN '5' THEN 'Friday'
            ELSE 'Saturday'
       END AS weekday,
       COUNT(*) AS closed_trades
FROM trades
WHERE status = 'CLOSED'
GROUP BY weekday_number
ORDER BY weekday_number;
"""

TRADES_BY_STRATEGY = """
SELECT COALESCE(strategy, 'Unspecified') AS strategy,
       COUNT(*)                                        AS total_trades,
       SUM(CASE WHEN status = 'OPEN'   THEN 1 ELSE 0 END) AS open_trades,
       SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_trades
FROM trades
GROUP BY COALESCE(strategy, 'Unspecified')
ORDER BY total_trades DESC, strategy;
"""

TRADES_BY_SESSION = """
SELECT COALESCE(session, 'Unspecified') AS session,
       COUNT(*)                                        AS total_trades,
       SUM(CASE WHEN status = 'OPEN'   THEN 1 ELSE 0 END) AS open_trades,
       SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_trades
FROM trades
GROUP BY COALESCE(session, 'Unspecified')
ORDER BY total_trades DESC, session;
"""

# A CTE (WITH ...) works out net P&L per trade, a second classifies it, and
# the final SELECT aggregates per instrument.
WIN_RATE_BY_INSTRUMENT = """
WITH closed AS (
    SELECT i.symbol,
           i.quote_currency,
           CASE t.direction
                WHEN 'LONG'  THEN CAST(t.exit_price AS REAL) - CAST(t.entry_price AS REAL)
                ELSE              CAST(t.entry_price AS REAL) - CAST(t.exit_price AS REAL)
           END
           * CAST(t.position_size AS REAL)
           * CAST(i.contract_multiplier AS REAL)
           - CAST(t.fees AS REAL) AS net_pnl
    FROM trades AS t
    JOIN instruments AS i ON i.id = t.instrument_id
    WHERE t.status = 'CLOSED'
),
classified AS (
    SELECT symbol,
           quote_currency,
           CASE WHEN net_pnl >  0.000001 THEN 'WIN'
                WHEN net_pnl < -0.000001 THEN 'LOSS'
                ELSE 'BREAKEVEN'
           END AS outcome
    FROM closed
)
SELECT symbol,
       quote_currency,
       COUNT(*)                                             AS closed_trades,
       SUM(CASE WHEN outcome = 'WIN'       THEN 1 ELSE 0 END) AS wins,
       SUM(CASE WHEN outcome = 'LOSS'      THEN 1 ELSE 0 END) AS losses,
       SUM(CASE WHEN outcome = 'BREAKEVEN' THEN 1 ELSE 0 END) AS breakevens,
       ROUND(100.0 * SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) / COUNT(*), 1)
                                                            AS win_rate_pct
FROM classified
GROUP BY symbol, quote_currency
ORDER BY win_rate_pct DESC, symbol;
"""

ALL_QUERIES = {
    "closed-count": CLOSED_TRADE_COUNT,
    "by-instrument": CLOSED_COUNT_BY_INSTRUMENT,
    "by-weekday": CLOSED_BY_WEEKDAY,
    "by-strategy": TRADES_BY_STRATEGY,
    "by-session": TRADES_BY_SESSION,
    "win-rate": WIN_RATE_BY_INSTRUMENT,
}


def _rows(conn, sql):
    return [dict(row) for row in conn.execute(sql).fetchall()]


def closed_trade_count(conn):
    return conn.execute(CLOSED_TRADE_COUNT).fetchone()["closed_trades"]


def closed_count_by_instrument(conn):
    return _rows(conn, CLOSED_COUNT_BY_INSTRUMENT)


def closed_by_weekday(conn):
    return _rows(conn, CLOSED_BY_WEEKDAY)


def trades_by_strategy(conn):
    return _rows(conn, TRADES_BY_STRATEGY)


def trades_by_session(conn):
    return _rows(conn, TRADES_BY_SESSION)


def win_rate_by_instrument(conn):
    return _rows(conn, WIN_RATE_BY_INSTRUMENT)
```

---

## `tradeledger/cli.py` (NEW)

**Purpose:** Adds init-db, reset-db, seed-db, db-info, analytics and run-sql to the `flask` command.

```python
"""Command-line commands, run through Flask:

    flask --app tradeledger init-db      create tables (keeps existing data)
    flask --app tradeledger reset-db     delete ALL data and recreate tables
    flask --app tradeledger seed-db      add synthetic sample data (repeatable)
    flask --app tradeledger db-info      show tables, row counts and schema version
    flask --app tradeledger analytics    run the analytical queries
    flask --app tradeledger run-sql "SELECT ..."   run your own read-only query
"""

import sqlite3
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from . import analytics
from .db import drop_all, get_db, init_schema, schema_version
from .seed import seed_sample_data


def print_table(rows):
    """Print a list of dicts as an aligned text table."""
    if not rows:
        click.echo("(no rows)")
        return
    headers = list(rows[0].keys())
    cells = [[("NULL" if row[h] is None else str(row[h])) for h in headers] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    click.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    click.echo("  ".join("-" * w for w in widths))
    for r in cells:
        click.echo("  ".join(value.ljust(w) for value, w in zip(r, widths)))


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
        f"Sample data: {inserted['instruments']} instrument(s) and "
        f"{inserted['trades']} trade(s) inserted."
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
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    if not tables:
        click.echo("No tables yet. Run: flask --app tradeledger init-db")
        return
    # Table names come from sqlite_master, not from user input, so it is safe
    # to place them in the SQL text. (Placeholders cannot stand for names.)
    rows = [
        {"table": name, "rows": conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]}
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


def init_app(app):
    for command in (
        init_db_command,
        reset_db_command,
        seed_db_command,
        db_info_command,
        analytics_command,
        run_sql_command,
    ):
        app.cli.add_command(command)
```

---

## `tradeledger/__init__.py` (MODIFIED)

**Purpose:** Application factory. New: `overrides` argument and `_configure_database()`, which sets the DB path and registers db.py and cli.py.

```python
"""TradeLedger application package.

Contains the application factory, create_app(). Flask's command-line tool
finds this function automatically when you run:  flask --app tradeledger run
"""

import logging
import os
import secrets

from flask import Flask, render_template

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
    """Attach groups of routes to the app."""
    from .routes import main_bp

    app.register_blueprint(main_bp)


def _register_error_handlers(app):
    """Show friendly error pages instead of Flask's defaults or stack traces."""

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def internal_error(error):
        app.logger.exception("Unhandled server error")
        return render_template("errors/500.html"), 500


def _register_security_headers(app):
    """Add basic protective HTTP headers to every response."""

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response
```

---

## `tradeledger/config.py` (MODIFIED)

**Purpose:** New `DATABASE` setting (from `DATABASE_PATH`), testing config requires an explicit path, version 0.2.0.

```python
"""Application configuration.

Settings are read from environment variables so that secrets never live in
source code. Each class represents one environment the app can run in.
"""

import os


class Config:
    """Settings shared by every environment."""

    APP_NAME = "TradeLedger"
    APP_VERSION = "0.2.0"

    # Read from the environment. Never hard-code a real key in this file.
    SECRET_KEY = os.environ.get("SECRET_KEY")

    TESTING = False

    # When True, the app refuses to start without a SECRET_KEY.
    REQUIRE_SECRET_KEY = True

    # Path to the SQLite database file. If not set, create_app() uses
    # instance/tradeledger.db inside the project folder.
    DATABASE = os.environ.get("DATABASE_PATH")


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


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
```

---

## `tradeledger/routes.py` (MODIFIED)

**Purpose:** Only change: roadmap shows Phase 2 Done, Phase 3 Next.

```python
"""HTTP routes for the main pages of TradeLedger.

Routes stay thin: they receive a request, gather what the page needs and
return a response. Database and calculation code will live in separate
modules (Phases 2 and 3), not here.
"""

from flask import Blueprint, current_app, jsonify, render_template

main_bp = Blueprint("main", __name__)

# What the home page shows about the build plan. Kept as data, not
# hard-coded HTML, so the template simply loops over it.
ROADMAP = [
    {"phase": 1, "title": "Project setup", "status": "Done"},
    {"phase": 2, "title": "SQL database", "status": "Done"},
    {"phase": 3, "title": "Trading calculations", "status": "Next"},
    {"phase": 4, "title": "Flask backend & CRUD", "status": "Planned"},
    {"phase": 5, "title": "Frontend pages", "status": "Planned"},
    {"phase": 6, "title": "Analytics dashboard", "status": "Planned"},
]


@main_bp.route("/")
def index():
    """Home page."""
    return render_template(
        "index.html",
        roadmap=ROADMAP,
        environment=current_app.config["CONFIG_NAME"],
    )


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

---

## `tests/conftest.py` (MODIFIED)

**Purpose:** Fixtures now create a temporary database per test: `app`, `client`, `runner`, `conn`, `seeded_conn`, `eurusd_id`, `long_trade`.

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
```

---

## `tests/test_app.py` (MODIFIED)

**Purpose:** Phase 1 tests. Only change: the health test reads the version from config.

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
    response = client.get("/")
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

---

## `tests/test_db.py` (NEW)

**Purpose:** Schema, Flask connection lifecycle, raw-SQL constraint enforcement, transactions.

```python
"""Database setup, Flask integration, constraints and transactions."""

import sqlite3

import pytest
from flask import g

from tradeledger import create_app
from tradeledger.db import connect, get_db, init_schema, schema_version


# ---------- Initialisation ----------

def test_both_tables_are_created(conn):
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"instruments", "trades"} <= names


def test_indexes_are_created(conn):
    names = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    assert {"idx_trades_instrument_id", "idx_trades_trade_date", "idx_trades_status"} <= names


def test_schema_version_is_recorded(conn):
    assert schema_version(conn) == 1


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
        "trade_date": 1, "direction": 2, "entry_price": 3, "stop_loss": 4,
        "exit_price": 5, "position_size": 6, "fees": 7, "status": 8,
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
        {"direction": "BUY"},                         # invalid direction
        {"status": "PENDING"},                        # invalid status
        {"status": "CLOSED", "exit_price": None},     # closed without exit
        {"status": "OPEN"},                           # open WITH an exit price
        {"stop_loss": "1.090"},                       # long stop above entry
        {"stop_loss": "1.085"},                       # stop equal to entry
        {"position_size": "0"},                       # zero size
        {"position_size": "-1"},                      # negative size
        {"entry_price": "abc"},                       # not a number
        {"fees": "-2"},                               # negative fees
        {"trade_date": "02/03/2026 08:15"},           # wrong date format
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
    with pytest.raises(RuntimeError):
        with conn:
            conn.execute(
                "INSERT INTO instruments (symbol, name, asset_class, price_increment, "
                "contract_multiplier, quote_currency) VALUES (?, ?, ?, ?, ?, ?)",
                ("GBPUSD", "test", "FOREX", "0.00001", "100000", "USD"),
            )
            raise RuntimeError("something failed half-way")
    assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 0
```

---

## `tests/test_validation.py` (NEW)

**Purpose:** Unit tests for validation.py (no database).

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
        ("2026-03-02 08:15", "2026-03-02T08:15:00Z"),                # no zone -> UTC
        ("2026-03-02T09:15:00+01:00", "2026-03-02T08:15:00Z"),       # converted to UTC
        ("2026-03-02T08:15:59.900Z", "2026-03-02T08:15:59Z"),        # seconds precision
    ],
)
def test_valid_timestamps_are_normalised_to_utc(raw, expected):
    assert parse_timestamp(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "2026-03-02", "02/03/2026 08:15", "2026-02-30T10:00:00Z", "yesterday", None, "1969-12-31T23:00:00Z"]
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
        {"symbol": " xauusd ", "name": "Gold", "asset_class": "commodity",
         "price_increment": "0.010", "contract_multiplier": "100", "quote_currency": "usd"}
    )
    assert clean == {
        "symbol": "XAUUSD", "name": "Gold", "asset_class": "COMMODITY",
        "price_increment": "0.01", "contract_multiplier": "100", "quote_currency": "USD",
    }


def test_instrument_reports_every_error_at_once():
    with pytest.raises(ValidationError) as info:
        validate_instrument({"symbol": "", "asset_class": "CRYPTO", "quote_currency": "DOLLARS"})
    assert set(info.value.errors) == {
        "symbol", "name", "asset_class", "price_increment", "contract_multiplier", "quote_currency",
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
    assert validate_trade(trade(exit_price="1.089"))["status"] == "CLOSED"


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"direction": "BUY"}, "direction"),
        ({"status": "PENDING"}, "status"),
        ({"status": "CLOSED"}, "exit_price"),                        # closed, no exit
        ({"status": "OPEN", "exit_price": "1.09"}, "exit_price"),    # open with exit
        ({"stop_loss": "1.09"}, "stop_loss"),                        # long stop above entry
        ({"stop_loss": "1.085"}, "stop_loss"),                       # zero risk
        ({"direction": "SHORT"}, "stop_loss"),                       # short stop below entry
        ({"take_profit": "1.08"}, "take_profit"),                    # long target below entry
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
        trade(direction="SHORT", stop_loss="1.08700", take_profit="1.08100", exit_price="1.08100")
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

---

## `tests/test_repositories.py` (NEW)

**Purpose:** Integration tests for instrument and trade repositories, filters, SQL injection.

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
    "symbol": "XAUUSD", "name": "Gold (test)", "asset_class": "COMMODITY",
    "price_increment": "0.01", "contract_multiplier": "100", "quote_currency": "USD",
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
        {"exit_price": None},              # CLOSED without exit
        {"stop_loss": "1.09000"},          # long stop above entry
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
    update_trade(conn, trade_id, dict(long_trade, status="OPEN", exit_price=None))
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

---

## `tests/test_seed_and_analytics.py` (NEW)

**Purpose:** Seeding, idempotency, rollback, every analytics query, SQL vs Decimal cross-check, CLI.

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
    monkeypatch.setattr(seed, "SAMPLE_TRADES", SAMPLE_TRADES + [broken])
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

    create_instrument(seeded_conn, {
        "symbol": "US30", "name": "Wall Street 30 (test)", "asset_class": "INDEX",
        "price_increment": "1", "contract_multiplier": "1", "quote_currency": "USD",
    })
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
        {"symbol": "XAUUSD", "quote_currency": "USD", "closed_trades": 4,
         "wins": 3, "losses": 1, "breakevens": 0, "win_rate_pct": 75.0},
        {"symbol": "NAS100", "quote_currency": "USD", "closed_trades": 2,
         "wins": 1, "losses": 1, "breakevens": 0, "win_rate_pct": 50.0},
        {"symbol": "UK100", "quote_currency": "GBP", "closed_trades": 2,
         "wins": 1, "losses": 0, "breakevens": 1, "win_rate_pct": 50.0},
        {"symbol": "EURUSD", "quote_currency": "USD", "closed_trades": 3,
         "wins": 1, "losses": 2, "breakevens": 0, "win_rate_pct": 33.3},
        {"symbol": "GBPUSD", "quote_currency": "USD", "closed_trades": 3,
         "wins": 1, "losses": 1, "breakevens": 1, "win_rate_pct": 33.3},
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
    assert "Schema version: 1" in info
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

---

## `.env.example` (MODIFIED)

**Purpose:** Adds optional `DATABASE_PATH`.

```bash
# Copy this file to .env and fill in real values.
# .env is ignored by Git - never commit it.

# Which config class to use: development, testing or production
TRADELEDGER_CONFIG=development

# Generate your own with:  python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=replace-me-with-a-long-random-string

# Optional: where the SQLite file lives. Leave commented out to use
# instance/tradeledger.db inside the project folder.
# DATABASE_PATH=/full/path/to/tradeledger.db
```
