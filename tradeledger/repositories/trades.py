"""Trade repository.

Ownership (Phase 8): every function takes the owner's user id as a REQUIRED
keyword argument, and every statement includes "t.user_id = ?". Another
user's trade is indistinguishable from a trade that does not exist: get
returns None, update and delete return False. The database adds a second
line of defence: a trade's (instrument_id, user_id) must match one of that
user's instruments (foreign key trades_instrument_owner_fk).

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

from decimal import Decimal

from ..db import IntegrityError
from ..timestamps import utc_now_text
from ..validation import SESSIONS, ValidationError, date_range_bounds, validate_trade
from . import require_user_id

# Exact decimal columns: TEXT on SQLite, NUMERIC on PostgreSQL (conn.numeric()).
_WRITE_DECIMALS = ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees")

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
    """Translate database integrity errors into ValidationErrors where we can.

    A foreign-key failure means the instrument does not exist OR belongs to
    someone else; the message is the same for both, so it reveals nothing
    about other users' instruments."""
    if exc.kind == "foreign_key":
        raise ValidationError({"instrument_id": "does not match an existing instrument"}) from exc
    if exc.constraint == "closed_at_rules":
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


def _where(user_id, instrument_id, start, end, status, date_field, strategy=None, search=None, session=None):
    """Build a WHERE clause from fixed fragments plus a parameter list.
    User values never become part of the SQL text; they go in `params`.
    The owner condition always comes first and is never optional.

    strategy  exact match on the strategy label
    session   exact match on the trading session (ASIA, LONDON, NEW_YORK, OTHER)
    search    case-insensitive substring match on the instrument symbol,
              the strategy or the notes, across ALL of the user's trades
              (not just one page). LOWER() on both sides makes it ignore
              case on both databases (PostgreSQL's LIKE is case-sensitive).
    """
    if date_field not in _DATE_COLUMNS:
        raise ValueError(f"date_field must be one of {sorted(_DATE_COLUMNS)}")
    date_column = _DATE_COLUMNS[date_field]
    lower, upper = date_range_bounds(start, end)

    conditions, params = ["t.user_id = ?"], [require_user_id(user_id)]
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
            "(LOWER(i.symbol) LIKE LOWER(?) ESCAPE '\\' OR LOWER(t.strategy) LIKE LOWER(?) ESCAPE '\\'"
            " OR LOWER(t.notes) LIKE LOWER(?) ESCAPE '\\')"
        )
        params.extend([pattern, pattern, pattern])
    return " WHERE " + " AND ".join(conditions), params


def _bind(conn, clean, field):
    value = clean[field]
    return conn.numeric(value) if field in _WRITE_DECIMALS and value is not None else value


def create_trade(conn, data, *, user_id):
    require_user_id(user_id)
    clean = validate_trade(data)
    columns = ("user_id", *_WRITE_FIELDS)
    placeholders = ", ".join("?" for _ in columns)
    try:
        with conn:
            row = conn.execute(
                f"INSERT INTO trades ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id",  # noqa: S608 - fixed column names; values are bound
                (user_id, *(_bind(conn, clean, field) for field in _WRITE_FIELDS)),
            ).fetchone()
    except IntegrityError as exc:
        _raise_friendly(exc)
    return row["id"]


def get_trade(conn, trade_id, *, user_id):
    require_user_id(user_id)
    row = conn.execute(_SELECT + " WHERE t.id = ? AND t.user_id = ?", (trade_id, user_id)).fetchone()
    return _to_dict(row)


def list_trades(
    conn,
    *,
    user_id,
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
    """List one user's trades, optionally filtered and paginated.

    user_id        the owner (required)
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
    where, params = _where(user_id, instrument_id, start, end, status, date_field, strategy, search, session)
    sql = _SELECT + where + " ORDER BY t.trade_date DESC, t.id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, int(limit), int(offset)]
    return [_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def count_trades(
    conn,
    *,
    user_id,
    instrument_id=None,
    start=None,
    end=None,
    status=None,
    date_field="entry",
    strategy=None,
    search=None,
    session=None,
):
    where, params = _where(user_id, instrument_id, start, end, status, date_field, strategy, search, session)
    # The JOIN is needed because search can match the instrument symbol.
    sql = "SELECT COUNT(*) FROM trades AS t JOIN instruments AS i ON i.id = t.instrument_id" + where  # noqa: S608 - `where` holds fixed fragments; values are bound
    return conn.execute(sql, params).fetchone()[0]


def list_strategies(conn, *, user_id):
    rows = conn.execute(
        "SELECT DISTINCT strategy FROM trades WHERE user_id = ? AND strategy IS NOT NULL ORDER BY strategy",
        (require_user_id(user_id),),
    ).fetchall()
    return [row["strategy"] for row in rows]


def update_trade(conn, trade_id, data, *, user_id):
    """Replace every editable field of one of the user's trades.

    A legacy CLOSED trade (recorded before Phase 4, closed_at unknown) may be
    saved again without a closed_at, so editing it never forces anyone to
    invent an exit time. Every other CLOSED trade must have one.

    The read and the write happen in ONE transaction; on PostgreSQL the row
    is locked (FOR UPDATE) so two simultaneous edits cannot interleave.
    Returns False when the trade does not exist or belongs to someone else.
    """
    require_user_id(user_id)
    lock = " FOR UPDATE" if conn.dialect == "postgresql" else ""
    # Column names come from _WRITE_FIELDS (a constant in this module), never
    # from the request; every value is a bound "?" parameter.
    assignments = ", ".join(f"{field} = ?" for field in _WRITE_FIELDS)
    sql = f"UPDATE trades SET {assignments}, updated_at = ? WHERE id = ? AND user_id = ?"  # noqa: S608 - see comment above
    try:
        with conn:
            existing = conn.execute(
                "SELECT status, closed_at FROM trades WHERE id = ? AND user_id = ?" + lock,  # noqa: S608 - `lock` is a constant
                (trade_id, user_id),
            ).fetchone()
            if existing is None:
                return False
            is_legacy = existing["status"] == "CLOSED" and existing["closed_at"] is None
            clean = validate_trade(data, require_closed_at=not is_legacy)
            cursor = conn.execute(
                sql,
                (
                    *(_bind(conn, clean, field) for field in _WRITE_FIELDS),
                    utc_now_text(),
                    trade_id,
                    user_id,
                ),
            )
    except IntegrityError as exc:
        _raise_friendly(exc)
    return cursor.rowcount == 1


def delete_trade(conn, trade_id, *, user_id):
    require_user_id(user_id)
    with conn:
        cursor = conn.execute("DELETE FROM trades WHERE id = ? AND user_id = ?", (trade_id, user_id))
    return cursor.rowcount == 1
