"""Builders for synthetic trade dicts used by the analytics tests.

make_trade() returns a dict shaped like a row from list_trades(). The
default is a CLOSED LONG with simple numbers so expected results are easy
to work out by hand:

    entry 100, stop 90, exit 120, size 1, multiplier 1, fees 0
    risk = (100 - 90) x 1 x 1 = 10      net = (120 - 100) x 1 x 1 - 0 = 20
    realised R = 20 / 10 = 2
"""

from decimal import Decimal

# The default test account: conftest.py creates it first in every fresh
# database, so its id is always 1 (Phase 8: every repository call names its owner).
USER_ID = 1

_next_id = [0]


def make_trade(**changes):
    _next_id[0] += 1
    trade = {
        "id": _next_id[0],
        "symbol": "TEST",
        "quote_currency": "USD",
        "contract_multiplier": Decimal("1"),
        "trade_date": "2026-01-05T10:00:00Z",
        "direction": "LONG",
        "status": "CLOSED",
        "entry_price": Decimal("100"),
        "stop_loss": Decimal("90"),
        "take_profit": None,
        "exit_price": Decimal("120"),
        "position_size": Decimal("1"),
        "fees": Decimal("0"),
        "strategy": None,
        "session": None,
    }
    trade.update(changes)
    return trade


def r_trade(r, **changes):
    """A LONG trade whose realised R is exactly `r` (risk 10, no fees)."""
    r = Decimal(str(r))
    return make_trade(exit_price=Decimal("100") + r * 10, **changes)


def dated(day, **changes):
    """make_trade on 2026-01-<day> (2026-01-05 is a Monday)."""
    return make_trade(trade_date=f"2026-01-{day:02d}T10:00:00Z", **changes)


def table_snapshot(app, table):
    """Every row of a table (as tuples), for 'nothing changed' assertions.
    `table` is always a literal name written in a test, never user input."""
    from tradeledger.db import get_db

    with app.app_context():
        return [tuple(row) for row in get_db().execute(f"SELECT * FROM {table} ORDER BY id")]
