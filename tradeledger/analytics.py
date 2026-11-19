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
|net P&L| below 0.000001 as breakeven to absorb that rounding. On
PostgreSQL the same query uses the NUMERIC columns directly, which is exact.
Phase 3 does the authoritative calculation in Python with decimal.Decimal;
a test checks that both agree on the sample data.

Ownership and databases (Phase 8)
---------------------------------
Every query takes the user's id and only counts that user's records. The
SQL is written once with two small dialect-specific fragments: how to turn
a stored price into a number, and how to get the weekday of a timestamp.

Currency limitation
-------------------
Win/loss classification is per trade, and converting a P&L to another
currency never changes its sign, so win rates are valid even when
instruments are quoted in different currencies. SUMMING money across
currencies (e.g. USD + GBP) is NOT valid without exchange rates, which is
why no query here adds up P&L across instruments.
"""

from .repositories import require_user_id

_SQL = {
    # The number of a stored decimal: SQLite TEXT -> REAL; PostgreSQL NUMERIC as is.
    "sqlite": {"num": "CAST({} AS REAL)", "weekday": "CAST(strftime('%w', {}) AS INTEGER)"},
    "postgresql": {"num": "{}", "weekday": "CAST(EXTRACT(DOW FROM CAST({} AS TIMESTAMP)) AS INTEGER)"},
}

CLOSED_TRADE_COUNT = """
SELECT COUNT(*) AS closed_trades
FROM trades
WHERE user_id = ? AND status = 'CLOSED';
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
WHERE i.user_id = ?
GROUP BY i.id, i.symbol
ORDER BY closed_trades DESC, i.symbol;
"""

# Weekday number: 0 = Sunday ... 6 = Saturday, from the UTC trade_date.
CLOSED_BY_WEEKDAY = """
SELECT {weekday} AS weekday_number,
       CASE {weekday}
            WHEN 0 THEN 'Sunday'    WHEN 1 THEN 'Monday'
            WHEN 2 THEN 'Tuesday'   WHEN 3 THEN 'Wednesday'
            WHEN 4 THEN 'Thursday'  WHEN 5 THEN 'Friday'
            ELSE 'Saturday'
       END AS weekday,
       COUNT(*) AS closed_trades
FROM trades
WHERE user_id = ? AND status = 'CLOSED'
GROUP BY 1, 2
ORDER BY 1;
"""

TRADES_BY_STRATEGY = """
SELECT COALESCE(strategy, 'Unspecified') AS strategy,
       COUNT(*)                                        AS total_trades,
       SUM(CASE WHEN status = 'OPEN'   THEN 1 ELSE 0 END) AS open_trades,
       SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_trades
FROM trades
WHERE user_id = ?
GROUP BY COALESCE(strategy, 'Unspecified')
ORDER BY total_trades DESC, strategy;
"""

TRADES_BY_SESSION = """
SELECT COALESCE(session, 'Unspecified') AS session,
       COUNT(*)                                        AS total_trades,
       SUM(CASE WHEN status = 'OPEN'   THEN 1 ELSE 0 END) AS open_trades,
       SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_trades
FROM trades
WHERE user_id = ?
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
                WHEN 'LONG'  THEN {exit} - {entry}
                ELSE              {entry} - {exit}
           END
           * {size}
           * {multiplier}
           - {fees} AS net_pnl
    FROM trades AS t
    JOIN instruments AS i ON i.id = t.instrument_id
    WHERE t.user_id = ? AND t.status = 'CLOSED'
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


def render(sql, dialect):
    """Fill in the dialect-specific fragments of one query."""
    parts = _SQL[dialect]
    num = parts["num"].format
    return sql.format(
        weekday=parts["weekday"].format("trade_date"),
        exit=num("t.exit_price"),
        entry=num("t.entry_price"),
        size=num("t.position_size"),
        multiplier=num("i.contract_multiplier"),
        fees=num("t.fees"),
    )


ALL_QUERIES = {
    "closed-count": CLOSED_TRADE_COUNT,
    "by-instrument": CLOSED_COUNT_BY_INSTRUMENT,
    "by-weekday": CLOSED_BY_WEEKDAY,
    "by-strategy": TRADES_BY_STRATEGY,
    "by-session": TRADES_BY_SESSION,
    "win-rate": WIN_RATE_BY_INSTRUMENT,
}

# Sums of integers come back as Decimal from PostgreSQL; plain ints and
# floats keep the results identical on both databases.
_INTEGER_COLUMNS = {"closed_trades", "total_trades", "open_trades", "wins", "losses", "breakevens"}


def run_query(conn, name, *, user_id):
    """Run one named query for one user; plain Python values back."""
    rows = []
    for row in conn.execute(render(ALL_QUERIES[name], conn.dialect), (require_user_id(user_id),)):
        record = dict(row)
        for key in record.keys() & _INTEGER_COLUMNS:
            record[key] = int(record[key])
        if "win_rate_pct" in record:
            record["win_rate_pct"] = float(record["win_rate_pct"])
        rows.append(record)
    return rows


def closed_trade_count(conn, *, user_id):
    return run_query(conn, "closed-count", user_id=user_id)[0]["closed_trades"]


def closed_count_by_instrument(conn, *, user_id):
    return run_query(conn, "by-instrument", user_id=user_id)


def closed_by_weekday(conn, *, user_id):
    return run_query(conn, "by-weekday", user_id=user_id)


def trades_by_strategy(conn, *, user_id):
    return run_query(conn, "by-strategy", user_id=user_id)


def trades_by_session(conn, *, user_id):
    return run_query(conn, "by-session", user_id=user_id)


def win_rate_by_instrument(conn, *, user_id):
    return run_query(conn, "win-rate", user_id=user_id)
