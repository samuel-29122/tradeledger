"""Integration tests: repository functions against a real temporary SQLite file."""

from decimal import Decimal

import pytest

from tests.factories import USER_ID
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
    "symbol": "XAUUSD",
    "name": "Gold (test)",
    "asset_class": "COMMODITY",
    "price_increment": "0.01",
    "contract_multiplier": "100",
    "quote_currency": "USD",
}


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------- Instruments ----------


def test_create_and_get_instrument(conn):
    new_id = create_instrument(conn, GOLD, user_id=USER_ID)
    record = get_instrument(conn, new_id, user_id=USER_ID)
    assert record["symbol"] == "XAUUSD"
    assert record["contract_multiplier"] == Decimal("100")
    assert record["created_at"].endswith("Z")


def test_get_instrument_by_symbol_ignores_case(conn):
    create_instrument(conn, GOLD, user_id=USER_ID)
    assert get_instrument_by_symbol(conn, "xauusd", user_id=USER_ID)["name"] == "Gold (test)"


def test_missing_instrument_returns_none(conn):
    assert get_instrument(conn, 999, user_id=USER_ID) is None
    assert get_instrument_by_symbol(conn, "NOPE", user_id=USER_ID) is None


def test_list_instruments_sorted_by_symbol(conn, eurusd_id):
    create_instrument(conn, GOLD, user_id=USER_ID)
    assert [i["symbol"] for i in list_instruments(conn, user_id=USER_ID)] == ["EURUSD", "XAUUSD"]


def test_list_instruments_empty_database(conn):
    assert list_instruments(conn, user_id=USER_ID) == []


def test_duplicate_symbol_is_rejected(conn):
    create_instrument(conn, GOLD, user_id=USER_ID)
    with pytest.raises(DuplicateError):
        create_instrument(conn, dict(GOLD, symbol="xauusd"), user_id=USER_ID)
    assert count(conn, "instruments") == 1


def test_invalid_instrument_writes_nothing(conn):
    with pytest.raises(ValidationError):
        create_instrument(conn, dict(GOLD, contract_multiplier="-100"), user_id=USER_ID)
    assert count(conn, "instruments") == 0


# ---------- Trades: create and read ----------


def test_create_and_get_trade(conn, long_trade):
    trade_id = create_trade(conn, long_trade, user_id=USER_ID)
    record = get_trade(conn, trade_id, user_id=USER_ID)
    assert record["symbol"] == "EURUSD"
    assert record["direction"] == "LONG"
    assert record["entry_price"] == Decimal("1.085")
    assert record["fees"] == Decimal("7")
    assert record["status"] == "CLOSED"
    assert record["created_at"] == record["updated_at"]


def test_get_missing_trade_returns_none(conn):
    assert get_trade(conn, 12345, user_id=USER_ID) is None


def test_trade_with_unknown_instrument_is_rejected(conn, long_trade):
    with pytest.raises(ValidationError) as info:
        create_trade(conn, dict(long_trade, instrument_id=999), user_id=USER_ID)
    assert "instrument_id" in info.value.errors
    assert count(conn, "trades") == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"entry_price": "-1"},
        {"position_size": "0"},
        {"direction": "SIDEWAYS"},
        {"status": "MAYBE"},
        {"exit_price": None},  # CLOSED without exit
        {"stop_loss": "1.09000"},  # long stop above entry
        {"trade_date": "31/12/2026"},
    ],
)
def test_invalid_trades_write_nothing(conn, long_trade, changes):
    with pytest.raises(ValidationError):
        create_trade(conn, dict(long_trade, **changes), user_id=USER_ID)
    assert count(conn, "trades") == 0


# ---------- Trades: update and delete ----------


def test_update_trade_changes_values_and_timestamp(conn, long_trade):
    trade_id = create_trade(conn, long_trade, user_id=USER_ID)
    # Force an older updated_at so the change is visible within one second.
    with conn:
        conn.execute("UPDATE trades SET updated_at = '2000-01-01T00:00:00Z' WHERE id = ?", (trade_id,))

    changed = dict(long_trade, exit_price="1.08700", notes="Closed early")
    assert update_trade(conn, trade_id, changed, user_id=USER_ID) is True

    record = get_trade(conn, trade_id, user_id=USER_ID)
    assert record["exit_price"] == Decimal("1.087")
    assert record["notes"] == "Closed early"
    assert record["updated_at"] != "2000-01-01T00:00:00Z"


def test_update_can_reopen_a_trade(conn, long_trade):
    trade_id = create_trade(conn, long_trade, user_id=USER_ID)
    update_trade(
        conn, trade_id, dict(long_trade, status="OPEN", exit_price=None, closed_at=None), user_id=USER_ID
    )
    record = get_trade(conn, trade_id, user_id=USER_ID)
    assert record["status"] == "OPEN"
    assert record["exit_price"] is None


def test_update_missing_trade_returns_false(conn, long_trade):
    assert update_trade(conn, 999, long_trade, user_id=USER_ID) is False


def test_invalid_update_leaves_trade_unchanged(conn, long_trade):
    trade_id = create_trade(conn, long_trade, user_id=USER_ID)
    with pytest.raises(ValidationError):
        update_trade(conn, trade_id, dict(long_trade, position_size="-5"), user_id=USER_ID)
    assert get_trade(conn, trade_id, user_id=USER_ID)["position_size"] == Decimal("1")


def test_delete_trade(conn, long_trade):
    trade_id = create_trade(conn, long_trade, user_id=USER_ID)
    assert delete_trade(conn, trade_id, user_id=USER_ID) is True
    assert get_trade(conn, trade_id, user_id=USER_ID) is None
    assert delete_trade(conn, trade_id, user_id=USER_ID) is False  # already gone


# ---------- Filtering (uses the sample data) ----------


def test_list_all_trades_newest_first(seeded_conn):
    trades = list_trades(seeded_conn, user_id=USER_ID)
    assert len(trades) == 16
    dates = [t["trade_date"] for t in trades]
    assert dates == sorted(dates, reverse=True)


def test_filter_by_instrument(seeded_conn):
    gold = get_instrument_by_symbol(seeded_conn, "XAUUSD", user_id=USER_ID)
    trades = list_trades(seeded_conn, instrument_id=gold["id"], user_id=USER_ID)
    assert len(trades) == 4
    assert {t["symbol"] for t in trades} == {"XAUUSD"}


def test_filter_by_date_range_is_inclusive(seeded_conn):
    trades = list_trades(seeded_conn, start="2026-03-02", end="2026-03-04", user_id=USER_ID)
    assert sorted(t["trade_date"] for t in trades) == [
        "2026-03-02T08:15:00Z",
        "2026-03-03T13:40:00Z",
        "2026-03-04T08:30:00Z",
    ]


def test_filter_start_only_and_end_only(seeded_conn):
    assert len(list_trades(seeded_conn, start="2026-03-19", user_id=USER_ID)) == 3
    assert len(list_trades(seeded_conn, end="2026-03-02", user_id=USER_ID)) == 1


def test_filters_combine(seeded_conn):
    gold = get_instrument_by_symbol(seeded_conn, "XAUUSD", user_id=USER_ID)
    trades = list_trades(
        seeded_conn, instrument_id=gold["id"], start="2026-03-10", status="CLOSED", user_id=USER_ID
    )
    assert [t["trade_date"] for t in trades] == ["2026-03-20T15:10:00Z", "2026-03-18T03:20:00Z"]


def test_filter_with_no_matches_returns_empty_list(seeded_conn):
    assert list_trades(seeded_conn, start="2030-01-01", user_id=USER_ID) == []


def test_invalid_filters_are_rejected(seeded_conn):
    with pytest.raises(ValidationError):
        list_trades(seeded_conn, start="2026-03-10", end="2026-03-01", user_id=USER_ID)
    with pytest.raises(ValidationError):
        list_trades(seeded_conn, status="closed", user_id=USER_ID)


def test_sql_injection_attempt_is_treated_as_plain_data(seeded_conn):
    """A malicious 'symbol' is just a string that matches nothing."""
    assert get_instrument_by_symbol(seeded_conn, "X' OR '1'='1", user_id=USER_ID) is None
    assert len(list_trades(seeded_conn, user_id=USER_ID)) == 16  # nothing was dropped or altered
