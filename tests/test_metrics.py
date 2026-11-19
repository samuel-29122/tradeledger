"""Unit tests for aggregate metrics (performance/metrics.py).

Most tests build trades with r_trade(), whose realised R is known exactly,
so every expected value below can be checked with pen and paper.
"""

from decimal import Decimal

from tests.factories import dated, make_trade, r_trade
from tradeledger.performance.metrics import (
    Drawdown,
    cumulative,
    expectancy,
    longest_streaks,
    max_drawdown,
    mean,
    profit_factor,
    ratio,
    summarise,
)
from tradeledger.performance.trade_math import evaluate_trade

D = Decimal


def results(*trades):
    return [evaluate_trade(t) for t in trades]


# ---------- Building blocks ----------


def test_ratio_and_division_by_zero():
    assert ratio(1, 4) == D("0.25")
    assert ratio(3, 0) is None
    assert ratio(0, 5) == D("0")


def test_mean():
    assert mean([D("1"), D("2"), D("4.5")]) == D("2.5")
    assert mean([]) is None


def test_expectancy_formula():
    # 40% wins of +2R, 60% losses of -1R -> 0.8 - 0.6 = +0.2R
    assert expectancy(D("0.4"), D("2"), D("0.6"), D("-1")) == D("0.2")


def test_expectancy_with_no_losses_or_no_wins():
    assert expectancy(D("1"), D("1.5"), D("0"), None) == D("1.5")
    assert expectancy(D("0"), None, D("1"), D("-1")) == D("-1")
    assert expectancy(None, None, None, None) is None


def test_profit_factor():
    assert profit_factor([D("3"), D("-1"), D("1"), D("-1")]) == D("2")
    assert profit_factor([D("2"), D("1")]) is None  # no losses: undefined
    assert profit_factor([D("-2")]) == D("0")
    assert profit_factor([]) is None
    assert profit_factor([D("0"), D("0")]) is None


def test_longest_streaks():
    seq = ["WIN", "WIN", "LOSS", "WIN", "WIN", "WIN", "LOSS", "LOSS"]
    assert longest_streaks(seq) == (3, 2)


def test_breakeven_breaks_a_streak():
    assert longest_streaks(["WIN", "WIN", "BREAKEVEN", "WIN"]) == (2, 0)
    assert longest_streaks(["LOSS", "BREAKEVEN", "LOSS"]) == (0, 1)


def test_streaks_empty():
    assert longest_streaks([]) == (0, 0)


def test_cumulative():
    assert cumulative([D("2"), D("-1"), D("0.5")]) == [D("2"), D("1"), D("1.5")]
    assert cumulative([]) == []


def test_max_drawdown_worked_example():
    # Curve: 2, 1, 3, 0.5, 1.5  -> peak 3 (trade 3), trough 0.5 (trade 4): 2.5
    points = [(1, D("2")), (2, D("-1")), (3, D("2")), (4, D("-2.5")), (5, D("1"))]
    assert max_drawdown(points) == Drawdown(D("2.5"), 3, 4)


def test_max_drawdown_counts_from_zero_before_first_trade():
    # Losing first: 0 -> -1 -> -3 means a drawdown of 3 from the start.
    assert max_drawdown([(1, D("-1")), (2, D("-2"))]) == Drawdown(D("3"), None, 2)


def test_max_drawdown_all_winners_is_zero():
    assert max_drawdown([(1, D("1")), (2, D("2"))]) == Drawdown(D("0"), None, None)
    assert max_drawdown([]) == Drawdown(D("0"), None, None)


def test_max_drawdown_picks_largest_not_latest():
    # Falls of 4 then 1: answer is 4.
    points = [(1, D("5")), (2, D("-4")), (3, D("10")), (4, D("-1"))]
    assert max_drawdown(points).amount == D("4")


# ---------- summarise ----------


def test_summary_of_no_trades():
    s = summarise([])
    assert s.trades == 0
    assert s.win_rate is None and s.loss_rate is None
    assert s.average_r is None and s.expectancy_r is None
    assert s.profit_factor_r is None
    assert s.total_r == D("0")
    assert s.max_drawdown_r.amount == D("0")
    assert s.best_trade_r is None
    assert s.money == {}


def test_summary_mixed_hand_worked():
    # Four trades: +2R, -1R, +1R, 0R (breakeven), in date order.
    s = summarise(
        results(
            r_trade(2, trade_date="2026-01-05T10:00:00Z"),
            r_trade(-1, trade_date="2026-01-06T10:00:00Z"),
            r_trade(1, trade_date="2026-01-07T10:00:00Z"),
            r_trade(0, trade_date="2026-01-08T10:00:00Z"),
        )
    )
    assert (s.trades, s.wins, s.losses, s.breakevens) == (4, 2, 1, 1)
    assert s.win_rate == D("0.5")
    assert s.loss_rate == D("0.25")
    assert s.breakeven_rate == D("0.25")
    assert s.total_r == D("2")
    assert s.average_r == D("0.5")
    assert s.average_win_r == D("1.5")
    assert s.average_loss_r == D("-1")
    # 0.5 x 1.5 + 0.25 x -1 = 0.75 - 0.25 = 0.5  (equals the average R)
    assert s.expectancy_r == D("0.5")
    assert s.profit_factor_r == D("3")  # (2 + 1) / 1
    assert s.longest_win_streak == 1
    assert s.longest_loss_streak == 1
    assert s.max_drawdown_r.amount == D("1")  # 2 -> 1
    assert s.best_trade_r.value == D("2")
    assert s.worst_trade_r.value == D("-1")


def test_summary_all_winners():
    s = summarise(results(r_trade(1), r_trade(2), r_trade(3)))
    assert s.win_rate == D("1")
    assert s.loss_rate == D("0")
    assert s.average_loss_r is None
    assert s.profit_factor_r is None  # undefined without losses
    assert s.expectancy_r == D("2")
    assert s.max_drawdown_r.amount == D("0")
    assert s.longest_win_streak == 3


def test_summary_all_losers():
    s = summarise(results(r_trade(-1), r_trade(-0.5)))
    assert s.win_rate == D("0")
    assert s.average_win_r is None
    assert s.expectancy_r == D("-0.75")
    assert s.profit_factor_r == D("0")
    assert s.max_drawdown_r.amount == D("1.5")
    assert s.longest_loss_streak == 2


def test_summary_sorts_by_date_before_streaks_and_drawdown():
    # Given out of order; in date order they are W, W, L, L.
    trades = [dated(8, exit_price=D("90")), dated(5), dated(7, exit_price=D("90")), dated(6)]
    s = summarise(results(*trades))
    assert s.longest_win_streak == 2
    assert s.longest_loss_streak == 2
    assert s.max_drawdown_r.amount == D("2")


def test_same_date_trades_ordered_by_id():
    first = make_trade(id=1001, exit_price=D("90"))
    second = make_trade(id=1002, exit_price=D("120"))
    s = summarise(results(second, first))
    # Order 1001 (-1R) then 1002 (+2R): drawdown is 1 from the start.
    assert s.max_drawdown_r == Drawdown(D("1"), None, 1001)


def test_trades_without_r_count_in_outcomes_but_not_r_metrics():
    no_r = make_trade(stop_loss=D("105"))  # invalid stop; net +20
    s = summarise(results(no_r, r_trade(-1)))
    assert s.trades == 2
    assert s.wins == 1 and s.losses == 1
    assert s.win_rate == D("0.5")
    assert s.r_trades == 1
    assert s.average_r == D("-1")
    assert s.expectancy_r == D("-1")  # only trades with R


def test_planned_rr_average_only_counts_trades_with_target():
    s = summarise(results(make_trade(take_profit=D("120")), make_trade(take_profit=D("130")), make_trade()))
    assert s.planned_rr_trades == 2
    assert s.average_planned_rr == D("2.5")


def test_best_and_worst_ties_go_to_earliest():
    a = r_trade(2, trade_date="2026-01-05T10:00:00Z")
    b = r_trade(2, trade_date="2026-01-06T10:00:00Z")
    s = summarise(results(b, a))
    assert s.best_trade_r.trade_id == a["id"]


# ---------- Money per currency ----------


def test_money_is_kept_separate_per_currency():
    usd = make_trade(quote_currency="USD", exit_price=D("130"), fees=D("2"))  # net 28
    gbp = make_trade(quote_currency="GBP", exit_price=D("95"))  # net -5
    s = summarise(results(usd, gbp))
    assert set(s.money) == {"GBP", "USD"}
    assert s.money["USD"].net_pnl == D("28")
    assert s.money["GBP"].net_pnl == D("-5")
    # R is currency-neutral, so it CAN be combined.
    # USD: net 28 / risk 10 = +2.8R (fees count). GBP: -5 / 10 = -0.5R.
    assert s.total_r == D("2.3")


def test_money_summary_gross_fees_net_and_profit_factor():
    s = summarise(
        results(
            make_trade(exit_price=D("130"), fees=D("1")),  # gross 30, net 29
            make_trade(exit_price=D("95"), fees=D("1")),  # gross -5, net -6
            make_trade(exit_price=D("90"), fees=D("1")),  # gross -10, net -11
        )
    ).money["USD"]
    assert s.trades == 3
    assert s.gross_pnl == D("15")
    assert s.fees == D("3")
    assert s.net_pnl == D("12")
    assert s.winning_pnl == D("29")
    assert s.losing_pnl == D("-17")
    assert s.profit_factor == ratio(29, 17)  # same 60-digit division as the engine
    assert s.best_trade.value == D("29")
    assert s.worst_trade.value == D("-11")
    assert s.max_drawdown.amount == D("17")  # 29 -> 12
