"""Unit tests for per-trade calculations (performance/trade_math.py)."""

from decimal import Decimal

import pytest

from tests.factories import make_trade
from tradeledger.performance.trade_math import (
    IncompleteTradeError,
    classify,
    evaluate_trade,
    gross_pnl,
    initial_risk,
    net_pnl,
    planned_rr,
    price_move,
    realised_r,
    risk_per_unit,
)

D = Decimal


# ---------- Building blocks ----------


def test_price_move_long_and_short():
    assert price_move("LONG", D("100"), D("120")) == D("20")
    assert price_move("SHORT", D("100"), D("120")) == D("-20")
    assert price_move("SHORT", D("100"), D("80")) == D("20")


def test_gross_pnl_uses_size_and_multiplier():
    # EURUSD example: 0.004 move x 1 lot x 100000 = 400 USD
    assert gross_pnl("LONG", D("1.085"), D("1.089"), D("1"), D("100000")) == D("400")
    # Gold example: short 0.2 lots, price rises 6.5: -6.5 x 0.2 x 100 = -130
    assert gross_pnl("SHORT", D("2165.5"), D("2172"), D("0.2"), D("100")) == D("-130")


def test_net_pnl_subtracts_fees():
    assert net_pnl(D("400"), D("7")) == D("393")
    assert net_pnl(D("-100"), D("3.5")) == D("-103.5")


def test_risk_per_unit_positive_only_when_stop_on_losing_side():
    assert risk_per_unit("LONG", D("100"), D("90")) == D("10")
    assert risk_per_unit("SHORT", D("100"), D("110")) == D("10")
    assert risk_per_unit("LONG", D("100"), D("110")) == D("-10")


def test_initial_risk():
    assert initial_risk("LONG", D("1.085"), D("1.083"), D("1"), D("100000")) == D("200")
    assert initial_risk("SHORT", D("1.088"), D("1.090"), D("0.5"), D("100000")) == D("100")


def test_initial_risk_is_none_for_invalid_stop():
    assert initial_risk("LONG", D("100"), D("100"), D("1"), D("1")) is None  # zero risk
    assert initial_risk("LONG", D("100"), D("105"), D("1"), D("1")) is None  # wrong side
    assert initial_risk("SHORT", D("100"), D("95"), D("1"), D("1")) is None


def test_realised_r():
    assert realised_r(D("393"), D("200")) == D("1.965")
    assert realised_r(D("-103.5"), D("100")) == D("-1.035")
    assert realised_r(D("0"), D("100")) == D("0")


def test_realised_r_unavailable_without_positive_risk():
    assert realised_r(D("50"), None) is None
    assert realised_r(D("50"), D("0")) is None
    assert realised_r(D("50"), D("-10")) is None


def test_planned_rr():
    assert planned_rr("LONG", D("100"), D("90"), D("120")) == D("2")
    assert planned_rr("SHORT", D("18300"), D("18340"), D("18200")) == D("2.5")
    assert planned_rr("LONG", D("100"), D("90"), None) is None
    assert planned_rr("LONG", D("100"), D("90"), D("95")) is None  # target on wrong side
    assert planned_rr("LONG", D("100"), D("100"), D("120")) is None  # zero risk


def test_classify():
    assert classify(D("0.01")) == "WIN"
    assert classify(D("-0.01")) == "LOSS"
    assert classify(D("0")) == "BREAKEVEN"
    assert classify(D("-0.00")) == "BREAKEVEN"


# ---------- evaluate_trade ----------


def test_evaluate_long_winner():
    result = evaluate_trade(make_trade(take_profit=D("130")))
    assert result.gross_pnl == D("20")
    assert result.net_pnl == D("20")
    assert result.initial_risk == D("10")
    assert result.realised_r == D("2")
    assert result.planned_rr == D("3")
    assert result.outcome == "WIN"
    assert result.r_unavailable_reason is None


def test_evaluate_short_loser_with_fees():
    # Short 100 -> 110 (stop 110): gross -10, fees 0.5 -> net -10.5, R -1.05
    result = evaluate_trade(
        make_trade(direction="SHORT", stop_loss=D("110"), exit_price=D("110"), fees=D("0.5"))
    )
    assert result.gross_pnl == D("-10")
    assert result.net_pnl == D("-10.5")
    assert result.realised_r == D("-1.05")
    assert result.outcome == "LOSS"


def test_fees_turn_small_price_gain_into_loss():
    # Price up 0.2 on a long, fees 0.5: gross +0.2, net -0.3
    result = evaluate_trade(make_trade(exit_price=D("100.2"), fees=D("0.5")))
    assert result.gross_pnl == D("0.2")
    assert result.net_pnl == D("-0.3")
    assert result.outcome == "LOSS"
    assert result.realised_r == D("-0.03")


def test_breakeven_is_exactly_zero_net():
    result = evaluate_trade(make_trade(exit_price=D("100")))
    assert result.outcome == "BREAKEVEN"
    assert result.realised_r == D("0")


def test_exit_at_entry_with_fees_is_a_loss_not_breakeven():
    result = evaluate_trade(make_trade(exit_price=D("100"), fees=D("1")))
    assert result.outcome == "LOSS"


def test_contract_multiplier_changes_money_not_r():
    small = evaluate_trade(make_trade(contract_multiplier=D("1")))
    large = evaluate_trade(make_trade(contract_multiplier=D("100000")))
    assert large.net_pnl == small.net_pnl * 100000
    assert large.realised_r == small.realised_r == D("2")


def test_position_size_changes_money_not_r():
    one = evaluate_trade(make_trade(position_size=D("1")))
    three = evaluate_trade(make_trade(position_size=D("3")))
    assert three.net_pnl == D("60")
    assert three.realised_r == one.realised_r


def test_decimal_precision_no_float_error():
    # In floats, 0.3 - 0.1 = 0.19999999999999998. Decimal gets exactly 0.2.
    result = evaluate_trade(
        make_trade(entry_price=D("0.1"), stop_loss=D("0.05"), exit_price=D("0.3"), position_size=D("3"))
    )
    assert result.gross_pnl == D("0.6")  # (0.3 - 0.1) x 3, exactly
    assert result.realised_r == D("4")  # 0.6 / (0.05 x 3)


def test_invalid_risk_keeps_pnl_but_has_no_r():
    result = evaluate_trade(make_trade(stop_loss=D("105")))  # long stop above entry
    assert result.net_pnl == D("20")
    assert result.outcome == "WIN"
    assert result.realised_r is None
    assert result.initial_risk is None
    assert "stop-loss" in result.r_unavailable_reason


def test_zero_risk_has_no_r():
    assert evaluate_trade(make_trade(stop_loss=D("100"))).realised_r is None


def test_strings_and_ints_are_accepted():
    result = evaluate_trade(make_trade(entry_price="100", stop_loss=90, exit_price="120.0"))
    assert result.realised_r == D("2")


def test_missing_fees_count_as_zero():
    assert evaluate_trade(make_trade(fees=None)).net_pnl == D("20")


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"status": "OPEN", "exit_price": None}, "not closed"),
        ({"exit_price": None}, "missing exit_price"),
        ({"entry_price": None}, "missing entry_price"),
        ({"stop_loss": None}, "missing stop_loss"),
        ({"contract_multiplier": None}, "missing contract_multiplier"),
        ({"contract_multiplier": D("0")}, "contract_multiplier"),
        ({"position_size": D("0")}, "position_size"),
        ({"position_size": D("-1")}, "position_size"),
        ({"fees": D("-1")}, "fees"),
        ({"direction": "BUY"}, "direction"),
        ({"entry_price": 100.0}, "not float"),
        ({"entry_price": "abc"}, "not a number"),
        ({"entry_price": "NaN"}, "finite"),
        ({"quote_currency": None}, "quote_currency"),
        ({"trade_date": "05/01/2026"}, "trade_date"),
    ],
)
def test_incomplete_or_invalid_trades_raise(changes, message):
    with pytest.raises(IncompleteTradeError, match=message):
        evaluate_trade(make_trade(**changes))
