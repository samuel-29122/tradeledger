"""Report building, grouping, serialisation, display formatting and the CLI.

The sample-data tests check the engine against values worked out BY HAND
from tradeledger/seed.py, held as exact fractions (fractions.Fraction), so
they do not reuse the engine's own arithmetic.
"""

import json
import random
from decimal import Decimal
from fractions import Fraction as F

import pytest

from tests.conftest import TEST_EMAIL
from tests.factories import USER_ID, dated, make_trade, r_trade
from tradeledger.performance import analyse, build_report
from tradeledger.performance.formatting import (
    decimal_to_text,
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
    to_jsonable,
)
from tradeledger.performance.metrics import summarise
from tradeledger.performance.trade_math import evaluate_trade
from tradeledger.repositories.instruments import get_instrument_by_symbol

D = Decimal

# Realised R per closed sample trade = net P&L / initial risk, by hand.
# e.g. trade 1: net 393 USD; risk (1.085 - 1.083) x 1 x 100000 = 200 -> 393/200
HAND_R = {
    1: F(393, 200),  # +1.965
    2: F(-1035, 1000),  # -103.5 / 100
    3: F(0),  # breakeven
    4: F(293, 200),  # +1.465
    5: F(198, 100),  # +1.98
    6: F(-133, 130),  # -1.0230769...
    7: F(1985, 1000),  # +1.985
    8: F(-815, 800),  # -1.01875
    9: F(-5, 150),  # -0.0333...  (fees turned a price gain into a loss)
    10: F(2),  # +2
    11: F(0),  # breakeven
    12: F(-157, 150),  # -1.04666...
    13: F(158, 80),  # +1.975
    16: F(38, 80),  # +0.475
}
CHRONOLOGICAL_IDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]


def close_to(value, expected, places=40):
    """Decimal from the engine vs an exact Fraction, to `places` decimals."""
    return abs(F(value) - F(expected)) < F(1, 10**places)


@pytest.fixture
def sample_report(seeded_conn):
    return build_report(seeded_conn, user_id=USER_ID)


# ---------- Sample data: overall ----------


def test_sample_counts(sample_report):
    o = sample_report.overall
    assert (o.trades, o.wins, o.losses, o.breakevens) == (14, 7, 5, 2)
    assert sample_report.open_trades == 2
    assert sample_report.excluded == []
    assert sample_report.r_unavailable == []
    assert o.win_rate == D("0.5")
    assert close_to(o.loss_rate, F(5, 14))


def test_sample_total_and_average_r(sample_report):
    o = sample_report.overall
    assert sum(HAND_R.values()) == F(239871, 31200)  # 7.68817307...
    assert close_to(o.total_r, F(239871, 31200))
    assert close_to(o.average_r, F(239871, 31200) / 14)  # 0.549155...


def test_sample_average_win_and_loss_r(sample_report):
    o = sample_report.overall
    assert close_to(o.average_win_r, F(2369, 1400))  # 11.845 / 7
    assert close_to(o.average_loss_r, F(-129693, 156000))  # -4.15682... / 5


def test_sample_expectancy_matches_average_r(sample_report):
    o = sample_report.overall
    assert close_to(o.expectancy_r, F(239871, 436800))
    assert close_to(o.expectancy_r, o.average_r)


def test_sample_profit_factor_r(sample_report):
    assert close_to(sample_report.overall.profit_factor_r, F(369564, 129693))  # 2.8495...


def test_sample_planned_rr(sample_report):
    o = sample_report.overall
    assert o.planned_rr_trades == 12  # trades 11 and 16 have no target
    assert close_to(o.average_planned_rr, F(647, 312))  # 2.0737...


def test_sample_streaks(sample_report):
    # Date order: W L BE W W L W L L W BE L W W
    assert sample_report.overall.longest_win_streak == 2
    assert sample_report.overall.longest_loss_streak == 2


def test_sample_max_drawdown_r(sample_report):
    dd = sample_report.overall.max_drawdown_r
    assert close_to(dd.amount, F(101, 96))  # 1.01875 + 1/30 = 1.05208...
    assert (dd.peak_trade_id, dd.trough_trade_id) == (7, 9)


def test_sample_best_and_worst_trades(sample_report):
    o = sample_report.overall
    assert (o.best_trade_r.trade_id, o.best_trade_r.value) == (10, D("2"))
    assert o.worst_trade_r.trade_id == 12


def test_sample_money_per_currency(sample_report):
    usd = sample_report.overall.money["USD"]
    gbp = sample_report.overall.money["GBP"]
    assert (usd.trades, usd.gross_pnl, usd.fees, usd.net_pnl) == (12, D("842"), D("43.5"), D("798.5"))
    assert (usd.winning_pnl, usd.losing_pnl) == (D("1278.5"), D("-480"))
    assert close_to(usd.profit_factor, F(12785, 4800))
    assert usd.max_drawdown.amount == D("243.5")  # 846 -> 602.5
    assert (usd.best_trade.trade_id, usd.worst_trade.trade_id) == (1, 12)
    assert (gbp.trades, gbp.net_pnl, gbp.profit_factor) == (2, D("200"), None)


def test_sample_cumulative_r_curve(sample_report):
    curve = sample_report.cumulative_r
    assert [p.trade_id for p in curve] == CHRONOLOGICAL_IDS
    running = F(0)
    for point in curve:
        running += HAND_R[point.trade_id]
        assert close_to(point.realised_r, HAND_R[point.trade_id])
        assert close_to(point.cumulative_r, running)
    assert curve[-1].cumulative_r == sample_report.overall.total_r


# ---------- Sample data: groups ----------


def test_groups_by_instrument(sample_report):
    groups = sample_report.by_instrument
    assert list(groups) == ["EURUSD", "GBPUSD", "NAS100", "UK100", "XAUUSD"]
    assert {k: g.trades for k, g in groups.items()} == {
        "EURUSD": 3,
        "GBPUSD": 3,
        "NAS100": 2,
        "UK100": 2,
        "XAUUSD": 4,
    }
    gold = groups["XAUUSD"]
    assert gold.win_rate == D("0.75")
    assert close_to(gold.total_r, HAND_R[5] + HAND_R[6] + HAND_R[13] + HAND_R[16])  # 3.4069...
    assert list(groups["UK100"].money) == ["GBP"]


def test_groups_by_strategy_and_session(sample_report):
    assert {k: g.trades for k, g in sample_report.by_strategy.items()} == {
        "Breakout": 5,
        "Pullback": 5,
        "Reversal": 2,
        "Unspecified": 2,
    }
    assert {k: g.trades for k, g in sample_report.by_session.items()} == {
        "ASIA": 2,
        "LONDON": 6,
        "NEW_YORK": 5,
        "Unspecified": 1,
    }


def test_groups_by_weekday_in_calendar_order(sample_report):
    groups = sample_report.by_weekday
    assert list(groups) == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    assert groups["Friday"].win_rate == D("1")
    # Thursday: trades 4 (+1.465R) and 9 (-1/30 R): PF = 1.465 x 30 = 43.95.
    # -1/30 cannot be stored exactly in decimal, so compare to 40 places.
    assert close_to(groups["Thursday"].profit_factor_r, F(4395, 100))


def test_groups_by_month_and_date(sample_report):
    assert list(sample_report.by_month) == ["2026-03"]
    assert sample_report.by_month["2026-03"].trades == 14
    dates = list(sample_report.by_date)
    assert len(dates) == 14 and dates == sorted(dates)
    assert sample_report.by_date["2026-03-20"].trades == 1  # trade 15 that day is open


# ---------- Filters through build_report ----------


def test_report_filtered_by_instrument(seeded_conn):
    gold = get_instrument_by_symbol(seeded_conn, "XAUUSD", user_id=USER_ID)
    report = build_report(seeded_conn, instrument_id=gold["id"], user_id=USER_ID)
    assert report.overall.trades == 4
    assert list(report.by_instrument) == ["XAUUSD"]


def test_report_filtered_by_date_range(seeded_conn):
    report = build_report(seeded_conn, start="2026-03-02", end="2026-03-06", user_id=USER_ID)
    assert [p.trade_id for p in report.cumulative_r] == [1, 2, 3, 4, 5]


def test_report_on_empty_database(conn):
    report = build_report(conn, user_id=USER_ID)
    assert report.overall.trades == 0
    assert report.overall.expectancy_r is None
    assert report.cumulative_r == []
    assert report.by_instrument == {}


# ---------- analyse() with awkward input ----------


def test_open_and_incomplete_trades_are_separated():
    trades = [
        make_trade(),
        make_trade(status="OPEN", exit_price=None),
        make_trade(exit_price=None),  # "closed" but no exit: excluded
        make_trade(stop_loss=D("105")),  # counted, but no R
    ]
    report = analyse(trades)
    assert report.overall.trades == 2
    assert report.open_trades == 1
    assert [e.trade_id for e in report.excluded] == [trades[2]["id"]]
    assert "exit_price" in report.excluded[0].reason
    assert [e.trade_id for e in report.r_unavailable] == [trades[3]["id"]]
    assert len(report.cumulative_r) == 1


def test_input_order_does_not_change_results():
    trades = [dated(d, exit_price=D(str(80 + d * 3))) for d in range(5, 20)]
    shuffled = trades[:]
    random.Random(7).shuffle(shuffled)
    assert analyse(shuffled) == analyse(trades)


def test_all_winning_and_all_losing_reports():
    wins = analyse([r_trade(1), r_trade(2)])
    assert wins.overall.loss_rate == D("0") and wins.overall.profit_factor_r is None
    losses = analyse([r_trade(-1), r_trade(-1)])
    assert losses.overall.win_rate == D("0") and losses.overall.profit_factor_r == D("0")


# ---------- JSON serialisation ----------


def _contains_float(obj):
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(_contains_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_float(v) for v in obj)
    return False


def test_report_serialises_to_json_without_floats(sample_report):
    data = to_jsonable(sample_report)
    assert not _contains_float(data)
    text = json.dumps(data)
    parsed = json.loads(text)
    assert parsed["overall"]["trades"] == 14
    assert parsed["overall"]["money"]["USD"]["net_pnl"] == "798.5"
    # Phase 4 added realised_at and close_time_known to each curve point.
    assert parsed["cumulative_r"][0] == {
        "trade_id": 1,
        "trade_date": "2026-03-02T08:15:00Z",
        "realised_at": "2026-03-02T11:40:00Z",
        "close_time_known": True,
        "symbol": "EURUSD",
        "realised_r": "1.965",
        "cumulative_r": "1.965",
    }


def test_json_strings_round_trip_to_the_exact_decimal(sample_report):
    text = to_jsonable(sample_report)["overall"]["total_r"]
    assert D(text) == sample_report.overall.total_r


def test_decimal_to_text_has_no_exponent():
    assert decimal_to_text(D("4.0E+2")) == "400"
    assert decimal_to_text(D("1.9650")) == "1.965"
    assert decimal_to_text(D("-0")) == "0"
    assert decimal_to_text(D("0E-60")) == "0"


# ---------- Display formatting and rounding policy ----------


def test_format_r_rounds_half_up_only_for_display():
    assert format_r(D("1.965")) == "+1.97R"
    assert format_r(D("-1.035")) == "-1.04R"
    assert format_r(D("0")) == "0.00R"
    assert format_r(D("-0.001")) == "0.00R"
    assert format_r(None) == "n/a"
    assert format_r_amount(D("1.0520833")) == "1.05R"


def test_format_money_rate_and_ratio():
    assert format_money(D("1278.5"), "USD") == "1,278.50 USD"
    assert format_money(D("-480"), "USD") == "-480.00 USD"
    assert format_rate(D(5) / D(14)) == "35.7%"
    assert format_rate(None) == "n/a"
    assert format_ratio(D("2.8495")) == "2.85"


def test_rounding_late_is_more_accurate_than_rounding_early():
    """Three trades of +0.005R. Rounding each first gives 0.03R; the true
    total is 0.015R, which displays as 0.02R."""
    s = summarise([evaluate_trade(r_trade("0.005")) for _ in range(3)])
    assert s.total_r == D("0.015")
    assert format_r(s.total_r) == "+0.02R"
    rounded_first = sum(D("0.005").quantize(D("0.01"), rounding="ROUND_HALF_UP") for _ in range(3))
    assert rounded_first == D("0.03")


# ---------- CLI ----------


def test_cli_report_text(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    result = runner.invoke(args=["report", "--email", TEST_EMAIL, "--email", TEST_EMAIL])
    assert result.exit_code == 0
    assert "Expectancy per trade          +0.55R" in result.output
    assert "798.50 USD" in result.output
    assert "never added together" in result.output


def test_cli_report_json_and_filters(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    data = json.loads(runner.invoke(args=["report", "--email", TEST_EMAIL, "--json"]).output)
    assert data["overall"]["trades"] == 14
    gold = json.loads(
        runner.invoke(args=["report", "--email", TEST_EMAIL, "--json", "--instrument", "xauusd"]).output
    )
    assert gold["overall"]["trades"] == 4


def test_cli_report_errors(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    unknown = runner.invoke(args=["report", "--email", TEST_EMAIL, "--instrument", "NOPE"])
    assert unknown.exit_code != 0 and "No instrument" in unknown.output
    bad_dates = runner.invoke(
        args=["report", "--email", TEST_EMAIL, "--start", "2026-03-10", "--end", "2026-03-01"]
    )
    assert bad_dates.exit_code != 0 and "end" in bad_dates.output
