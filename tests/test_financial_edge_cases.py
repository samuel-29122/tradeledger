"""Phase 7: financial boundary and regression cases.

Gaps found by reviewing the Phase 6 suite (which already covered long and
short trades, fees, breakeven, multipliers, zero risk, close order and
currencies): very small values, fractional multipliers, fees that turn a
SHORT winner into a loser, a loss beyond the stop, drawdown recovery
followed by a larger drawdown, and single-trade datasets.

Every expected value below was worked out by hand (shown in each test)
and compared as an exact fraction. None is produced by calling the code
under test.
"""

from decimal import Decimal
from fractions import Fraction

import pytest

from tests.factories import make_trade, r_trade
from tradeledger.performance.metrics import summarise
from tradeledger.performance.trade_math import evaluate_trade, planned_rr

D = Decimal


def exact(value):
    """A Decimal as an exact Fraction, for comparisons with hand results."""
    return Fraction(value)


def close_to(value, fraction):
    """True when a 60-significant-digit Decimal matches an exact fraction.
    A repeating decimal such as 1/3 cannot be stored exactly, so allow the
    last of 60 digits to differ."""
    return abs(exact(value) - fraction) < Fraction(1, 10**55)


# ---------- Single trades ----------


def test_very_small_prices_keep_full_precision():
    """Crypto-style prices with 8 decimal places and a large size.

    move     = 0.00001300 - 0.00001234 = 0.00000066
    gross    = 0.00000066 x 1,000,000 x 1 = 0.66
    net      = 0.66 - 0.0001 fees = 0.6599
    risk     = (0.00001234 - 0.00001200) x 1,000,000 x 1 = 0.34
    R        = 0.6599 / 0.34 = 6599/3400 (a repeating decimal)
    """
    result = evaluate_trade(
        make_trade(
            entry_price=D("0.00001234"),
            stop_loss=D("0.00001200"),
            exit_price=D("0.00001300"),
            position_size=D("1000000"),
            fees=D("0.0001"),
        )
    )
    assert result.gross_pnl == D("0.66")
    assert result.net_pnl == D("0.6599")
    assert result.initial_risk == D("0.34")
    assert close_to(result.realised_r, Fraction(6599, 3400))
    assert result.outcome == "WIN"


def test_high_precision_short_with_fractional_multiplier():
    """SHORT, 8-decimal prices, multiplier 0.1, size 3.

    move     = entry - exit = 1.23456789 - 1.23356789 = 0.001 (in our favour)
    gross    = 0.001 x 3 x 0.1 = 0.0003
    net      = 0.0003 - 0.0002 = 0.0001
    risk     = (stop - entry) x 3 x 0.1 = 0.001 x 0.3 = 0.0003
    R        = 0.0001 / 0.0003 = 1/3
    """
    result = evaluate_trade(
        make_trade(
            direction="SHORT",
            entry_price=D("1.23456789"),
            stop_loss=D("1.23556789"),
            exit_price=D("1.23356789"),
            position_size=D("3"),
            contract_multiplier=D("0.1"),
            fees=D("0.0002"),
        )
    )
    assert result.gross_pnl == D("0.0003")
    assert result.net_pnl == D("0.0001")
    assert result.initial_risk == D("0.0003")
    assert close_to(result.realised_r, Fraction(1, 3))
    assert result.outcome == "WIN"


def test_fees_turn_a_short_winner_into_a_loss():
    """Same SHORT, but fees 0.0004 exceed the 0.0003 gross gain.

    net = 0.0003 - 0.0004 = -0.0001   R = -0.0001 / 0.0003 = -1/3
    The price moved our way, yet the trade is a LOSS after costs.
    """
    result = evaluate_trade(
        make_trade(
            direction="SHORT",
            entry_price=D("1.23456789"),
            stop_loss=D("1.23556789"),
            exit_price=D("1.23356789"),
            position_size=D("3"),
            contract_multiplier=D("0.1"),
            fees=D("0.0004"),
        )
    )
    assert result.gross_pnl > 0
    assert result.net_pnl == D("-0.0001")
    assert close_to(result.realised_r, Fraction(-1, 3))
    assert result.outcome == "LOSS"


def test_short_exit_at_entry_without_fees_is_breakeven():
    result = evaluate_trade(
        make_trade(direction="SHORT", entry_price=D("50"), stop_loss=D("55"), exit_price=D("50"))
    )
    assert (result.net_pnl, result.realised_r, result.outcome) == (D("0"), D("0"), "BREAKEVEN")


def test_loss_beyond_the_stop_is_more_than_one_r():
    """Slippage: LONG entry 100, stop 90, but the exit was 85.
    net = -15, risk = 10, R = -1.5 (the stop is a plan, not a guarantee)."""
    result = evaluate_trade(make_trade(exit_price=D("85")))
    assert (result.net_pnl, result.realised_r, result.outcome) == (D("-15"), D("-1.5"), "LOSS")


@pytest.mark.parametrize(
    "direction, entry, stop, target, expected",
    [
        ("LONG", "100", "90", None, None),  # no target recorded
        ("LONG", "100", "90", "95", None),  # target on the losing side
        ("LONG", "100", "90", "100", None),  # target equal to entry: no reward
        ("SHORT", "100", "110", "120", None),  # short target above entry
        ("LONG", "100", "90", "125", Fraction(5, 2)),  # reward 25 / risk 10
        ("SHORT", "100", "104", "94", Fraction(3, 2)),  # reward 6 / risk 4
    ],
)
def test_planned_reward_to_risk(direction, entry, stop, target, expected):
    value = planned_rr(direction, D(entry), D(stop), None if target is None else D(target))
    assert (None if value is None else exact(value)) == expected


# ---------- Single-trade datasets ----------


def test_single_winning_trade_summary():
    """One +2R trade: no losses, so profit factor is undefined (None), not
    infinite, and there is no drawdown."""
    s = summarise([evaluate_trade(r_trade(2, id=1))])
    assert (s.trades, s.wins, s.losses, s.breakevens) == (1, 1, 0, 0)
    assert (s.win_rate, s.total_r, s.average_r, s.expectancy_r) == (D("1"), D("2"), D("2"), D("2"))
    assert s.profit_factor_r is None
    assert (s.longest_win_streak, s.longest_loss_streak) == (1, 0)
    assert s.max_drawdown_r.amount == 0
    assert s.max_drawdown_r.trough_trade_id is None


def test_single_losing_trade_summary():
    """One -1R trade: gains 0 / losses 1 = profit factor 0. The drawdown is
    measured from the 0R starting point (peak None) down to this trade."""
    s = summarise([evaluate_trade(r_trade(-1, id=7))])
    assert (s.wins, s.losses, s.win_rate, s.total_r) == (0, 1, D("0"), D("-1"))
    assert s.profit_factor_r == 0
    assert (s.longest_win_streak, s.longest_loss_streak) == (0, 1)
    assert s.max_drawdown_r.amount == D("1")
    assert (s.max_drawdown_r.peak_trade_id, s.max_drawdown_r.trough_trade_id) == (None, 7)


def test_single_breakeven_trade_summary():
    s = summarise([evaluate_trade(r_trade(0, id=3))])
    assert (s.wins, s.losses, s.breakevens, s.win_rate, s.total_r) == (0, 0, 1, D("0"), D("0"))
    assert s.profit_factor_r is None
    assert (s.longest_win_streak, s.longest_loss_streak) == (0, 0)
    assert s.max_drawdown_r.amount == 0


# ---------- Drawdown recovery ----------


def _series(rs):
    """Trades with ids 1..n closing on consecutive days, with the given R."""
    return [
        evaluate_trade(
            r_trade(
                r,
                id=i,
                closed_at=f"2026-02-{i + 1:02d}T12:00:00Z",
                trade_date=f"2026-02-{i + 1:02d}T09:00:00Z",
            )
        )
        for i, r in enumerate(rs, start=1)
    ]


def test_drawdown_recovers_and_a_smaller_one_does_not_replace_it():
    """R: +1, -2, +3, -1
    cumulative 1, -1, 2, 1     running peak 1, 1, 2, 2
    drawdown   0,  2, 0, 1     -> maximum 2, from trade 1 (peak) to trade 2.
    The full recovery at trade 3 and the later dip of 1 do not change it."""
    s = summarise(_series(["1", "-2", "3", "-1"]))
    assert s.total_r == D("1")
    dd = s.max_drawdown_r
    assert (dd.amount, dd.peak_trade_id, dd.trough_trade_id) == (D("2"), 1, 2)


def test_larger_drawdown_after_recovery_replaces_the_first():
    """R: +1, -1, +5, -4
    cumulative 1, 0, 5, 1      running peak 1, 1, 5, 5
    drawdown   0, 1, 0, 4      -> maximum 4, from trade 3 (new peak) to trade 4."""
    dd = summarise(_series(["1", "-1", "5", "-4"])).max_drawdown_r
    assert (dd.amount, dd.peak_trade_id, dd.trough_trade_id) == (D("4"), 3, 4)


def test_streaks_across_a_recovery():
    """W W L L L W -> longest win streak 2, longest loss streak 3."""
    s = summarise(_series(["1", "1", "-1", "-1", "-1", "2"]))
    assert (s.longest_win_streak, s.longest_loss_streak) == (2, 3)
