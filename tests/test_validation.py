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
        ("2026-03-02 08:15", "2026-03-02T08:15:00Z"),  # no zone -> UTC
        ("2026-03-02T09:15:00+01:00", "2026-03-02T08:15:00Z"),  # converted to UTC
        ("2026-03-02T08:15:59.900Z", "2026-03-02T08:15:59Z"),  # seconds precision
    ],
)
def test_valid_timestamps_are_normalised_to_utc(raw, expected):
    assert parse_timestamp(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "2026-03-02", "02/03/2026 08:15", "2026-02-30T10:00:00Z", "yesterday", None, "1969-12-31T23:00:00Z"],
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
        {
            "symbol": " xauusd ",
            "name": "Gold",
            "asset_class": "commodity",
            "price_increment": "0.010",
            "contract_multiplier": "100",
            "quote_currency": "usd",
        }
    )
    assert clean == {
        "symbol": "XAUUSD",
        "name": "Gold",
        "asset_class": "COMMODITY",
        "price_increment": "0.01",
        "contract_multiplier": "100",
        "quote_currency": "USD",
    }


def test_instrument_reports_every_error_at_once():
    with pytest.raises(ValidationError) as info:
        validate_instrument({"symbol": "", "asset_class": "CRYPTO", "quote_currency": "DOLLARS"})
    assert set(info.value.errors) == {
        "symbol",
        "name",
        "asset_class",
        "price_increment",
        "contract_multiplier",
        "quote_currency",
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
    # Phase 4: a closed trade also needs its exit time.
    assert validate_trade(trade(exit_price="1.089", closed_at="2026-03-02T10:00:00Z"))["status"] == "CLOSED"


@pytest.mark.parametrize(
    "changes, field",
    [
        ({"direction": "BUY"}, "direction"),
        ({"status": "PENDING"}, "status"),
        ({"status": "CLOSED"}, "exit_price"),  # closed, no exit
        ({"status": "OPEN", "exit_price": "1.09"}, "exit_price"),  # open with exit
        ({"stop_loss": "1.09"}, "stop_loss"),  # long stop above entry
        ({"stop_loss": "1.085"}, "stop_loss"),  # zero risk
        ({"direction": "SHORT"}, "stop_loss"),  # short stop below entry
        ({"take_profit": "1.08"}, "take_profit"),  # long target below entry
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
        trade(
            direction="SHORT",
            stop_loss="1.08700",
            take_profit="1.08100",
            exit_price="1.08100",
            closed_at="2026-03-02T10:00:00Z",
        )
    )
    assert clean["direction"] == "SHORT"
    assert clean["status"] == "CLOSED"


def test_blank_optional_fields_become_none():
    clean = validate_trade(trade(take_profit="", strategy="  ", session="", notes=""))
    assert clean["take_profit"] is None
    assert clean["strategy"] is None
    assert clean["session"] is None
    assert clean["notes"] is None
