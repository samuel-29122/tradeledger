"""Realised chronology: results are ordered and grouped by exit time.

Each scenario is small and worked out by hand. r_trade() trades risk 10
with no fees, so exit_price 120 = +2R and exit_price 90 = -1R.
"""

from decimal import Decimal

import pytest

from tests.factories import USER_ID, make_trade, r_trade
from tradeledger.performance import analyse
from tradeledger.performance.trade_math import IncompleteTradeError, evaluate_trade
from tradeledger.repositories.trades import create_trade, list_trades
from tradeledger.validation import ValidationError, validate_trade

D = Decimal


def test_trade_closed_later_is_realised_later():
    # A is entered first but closes last.
    a = r_trade(2, trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-09T16:00:00Z")
    b = r_trade(-1, trade_date="2026-01-06T09:00:00Z", closed_at="2026-01-06T12:00:00Z")
    c = r_trade(-1, trade_date="2026-01-07T09:00:00Z", closed_at="2026-01-07T12:00:00Z")
    report = analyse([a, b, c])
    # Realised (close) order is B, C, A, so the curve is -1, -2, 0.
    # In entry order it would have been +2, +1, 0.
    assert [p.trade_id for p in report.cumulative_r] == [b["id"], c["id"], a["id"]]
    assert [p.cumulative_r for p in report.cumulative_r] == [D("-1"), D("-2"), D("0")]


def test_streak_depends_on_close_order_not_entry_order():
    # Entry order: W, L, W, L  (longest win streak 1).
    # Close order: W, W, L, L  (longest win streak 2).
    trades = [
        r_trade(1, trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-05T10:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T09:30:00Z", closed_at="2026-01-05T15:00:00Z"),
        r_trade(1, trade_date="2026-01-05T09:45:00Z", closed_at="2026-01-05T11:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T09:50:00Z", closed_at="2026-01-05T16:00:00Z"),
    ]
    s = analyse(trades).overall
    assert (s.longest_win_streak, s.longest_loss_streak) == (2, 2)


def test_drawdown_follows_close_order():
    # Close order: +2, -1, -1, +1 -> curve 2, 1, 0, 1 -> max drawdown 2.
    trades = [
        r_trade(1, trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-08T09:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T10:00:00Z", closed_at="2026-01-07T09:00:00Z"),
        r_trade(2, trade_date="2026-01-05T11:00:00Z", closed_at="2026-01-05T12:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T12:00:00Z", closed_at="2026-01-06T09:00:00Z"),
    ]
    assert analyse(trades).overall.max_drawdown_r.amount == D("2")


def test_month_and_weekday_use_the_close_date():
    # Entered Saturday 31 January, closed Monday 2 February.
    t = r_trade(1, trade_date="2026-01-31T22:00:00Z", closed_at="2026-02-02T08:00:00Z")
    report = analyse([t])
    assert list(report.by_month) == ["2026-02"]
    assert list(report.by_weekday) == ["Monday"]
    assert list(report.by_date) == ["2026-02-02"]


def test_legacy_trade_falls_back_to_entry_time_and_is_flagged():
    legacy = r_trade(1, trade_date="2026-01-05T09:00:00Z")  # no closed_at
    known = r_trade(1, trade_date="2026-01-04T09:00:00Z", closed_at="2026-01-06T09:00:00Z")
    report = analyse([known, legacy])
    assert [p.trade_id for p in report.cumulative_r] == [legacy["id"], known["id"]]
    assert report.unknown_close_time_trade_ids == [legacy["id"]]
    point = report.cumulative_r[0]
    assert point.realised_at == point.trade_date and point.close_time_known is False


def test_equal_realised_times_order_by_id():
    first = make_trade(id=5001, closed_at="2026-01-05T12:00:00Z", exit_price=D("90"))
    second = make_trade(id=5002, closed_at="2026-01-05T12:00:00Z")
    report = analyse([second, first])
    assert [p.trade_id for p in report.cumulative_r] == [5001, 5002]


def test_trade_result_fields():
    r = evaluate_trade(make_trade(trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-05T10:00:00Z"))
    assert (r.closed_at, r.realised_at, r.close_time_known) == (
        "2026-01-05T10:00:00Z",
        "2026-01-05T10:00:00Z",
        True,
    )


@pytest.mark.parametrize(
    "closed_at, message",
    [
        ("2026-01-05T08:00:00Z", "earlier than trade_date"),
        ("05/01/2026", "closed_at must look like"),
    ],
)
def test_engine_rejects_inconsistent_close_times(closed_at, message):
    with pytest.raises(IncompleteTradeError, match=message):
        evaluate_trade(make_trade(trade_date="2026-01-05T09:00:00Z", closed_at=closed_at))


# ---------- Validation of closed_at ----------

BASE = {
    "instrument_id": 1,
    "trade_date": "2026-01-05T09:00:00Z",
    "direction": "LONG",
    "entry_price": "100",
    "stop_loss": "90",
    "position_size": "1",
}


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"exit_price": "120"}, "is required for a CLOSED trade"),
        ({"closed_at": "2026-01-05T10:00:00Z"}, "must be empty for an OPEN trade"),
        ({"exit_price": "120", "closed_at": "2026-01-05T08:59:59Z"}, "must not be earlier than trade_date"),
        ({"exit_price": "120", "closed_at": "not-a-real-timestamp"}, "ISO 8601"),
    ],
)
def test_closed_at_validation_errors(changes, message):
    with pytest.raises(ValidationError) as info:
        validate_trade({**BASE, **changes})
    assert message in info.value.errors["closed_at"]


def test_closed_at_is_normalised_to_utc():
    clean = validate_trade({**BASE, "exit_price": "120", "closed_at": "2026-01-05T11:00:00+01:00"})
    assert clean["closed_at"] == "2026-01-05T10:00:00Z"


def test_closed_at_equal_to_entry_is_allowed():
    assert (
        validate_trade({**BASE, "exit_price": "120", "closed_at": BASE["trade_date"]})["status"] == "CLOSED"
    )


def test_require_closed_at_false_allows_unknown_close():
    assert validate_trade({**BASE, "exit_price": "120"}, require_closed_at=False)["closed_at"] is None


# ---------- Repository date filters ----------


def test_realised_date_filter_uses_close_date(conn, eurusd_id):
    create_trade(
        conn,
        {
            "instrument_id": eurusd_id,
            "trade_date": "2026-03-31T20:00:00Z",
            "closed_at": "2026-04-01T09:00:00Z",
            "direction": "LONG",
            "entry_price": "1.1",
            "stop_loss": "1.09",
            "exit_price": "1.12",
            "position_size": "1",
        },
        user_id=USER_ID,
    )
    assert len(list_trades(conn, start="2026-03-31", end="2026-03-31", user_id=USER_ID)) == 1  # entry
    assert (
        len(list_trades(conn, start="2026-03-31", end="2026-03-31", date_field="realised", user_id=USER_ID))
        == 0
    )
    assert (
        len(list_trades(conn, start="2026-04-01", end="2026-04-01", date_field="realised", user_id=USER_ID))
        == 1
    )


def test_unknown_date_field_is_a_programming_error(conn):
    with pytest.raises(ValueError):
        list_trades(conn, date_field="whenever", user_id=USER_ID)
