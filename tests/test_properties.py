"""Property-style tests: rules that must hold for ANY valid trades.

The 'hypothesis' library is the usual tool for this, but it is not a
project dependency. Instead we generate thousands of random (seeded, so
repeatable) trades with Python's random module and check the rules on each.
If a rule ever fails, the seed in the test name reproduces it exactly.
"""

import random
from decimal import Decimal
from fractions import Fraction

import pytest

from tradeledger.performance.metrics import summarise
from tradeledger.performance.trade_math import evaluate_trade

SEEDS = [1, 2, 3, 4, 5]
MULTIPLIERS = ["1", "10", "100", "100000"]


def random_trade(rng, trade_id):
    direction = rng.choice(["LONG", "SHORT"])
    sign = 1 if direction == "LONG" else -1
    entry = Decimal(rng.randint(100, 500_000)) / 100  # 1.00 .. 5000.00
    risk = Decimal(rng.randint(1, 5_000)) / 100  # 0.01 .. 50.00
    move = Decimal(rng.randint(-10_000, 15_000)) / 100  # -100 .. +150
    exit_price = max(entry + sign * move, Decimal("0.01"))
    return {
        "id": trade_id,
        "symbol": rng.choice(["AAA", "BBB", "CCC"]),
        "quote_currency": rng.choice(["USD", "GBP", "EUR"]),
        "contract_multiplier": Decimal(rng.choice(MULTIPLIERS)),
        "trade_date": f"2026-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}T"
        f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}:00Z",
        "direction": direction,
        "status": "CLOSED",
        "entry_price": entry,
        "stop_loss": entry - sign * risk if entry - sign * risk > 0 else entry + risk,
        "take_profit": None,
        "exit_price": exit_price,
        "position_size": Decimal(rng.randint(1, 1_000)) / 100,
        "fees": Decimal(rng.randint(0, 2_000)) / 100,
    }


def random_results(seed, n=200):
    rng = random.Random(seed)
    trades = [random_trade(rng, i) for i in range(1, n + 1)]
    return trades, [evaluate_trade(t) for t in trades]


def close(a, b, places=40):
    return abs(Fraction(a) - Fraction(b)) < Fraction(1, 10**places)


@pytest.mark.parametrize("seed", SEEDS)
def test_r_times_risk_equals_net_pnl(seed):
    for r in random_results(seed)[1]:
        if r.realised_r is not None:
            assert close(r.realised_r * r.initial_risk, r.net_pnl, places=30)


@pytest.mark.parametrize("seed", SEEDS)
def test_net_equals_gross_minus_fees(seed):
    for r in random_results(seed)[1]:
        assert r.net_pnl == r.gross_pnl - r.fees


@pytest.mark.parametrize("seed", SEEDS)
def test_long_and_mirrored_short_give_identical_results(seed):
    """A short is a long seen in a mirror: reflect every price around the
    entry and the money and R must be identical."""
    trades, _ = random_results(seed, n=100)
    for t in trades:
        if t["direction"] != "LONG":
            continue
        entry = t["entry_price"]
        mirrored_exit = 2 * entry - t["exit_price"]
        if mirrored_exit <= 0:
            continue
        short = dict(t, direction="SHORT", stop_loss=2 * entry - t["stop_loss"], exit_price=mirrored_exit)
        long_result, short_result = evaluate_trade(t), evaluate_trade(short)
        assert short_result.net_pnl == long_result.net_pnl
        assert short_result.realised_r == long_result.realised_r


@pytest.mark.parametrize("seed", SEEDS)
def test_summary_identities(seed):
    _, results = random_results(seed)
    s = summarise(results)
    assert s.wins + s.losses + s.breakevens == s.trades
    assert close(s.win_rate + s.loss_rate + s.breakeven_rate, 1)
    assert close(s.expectancy_r, s.average_r)
    # Exact reference sums use Fraction, so the test adds no rounding of its own.
    r_values = [Fraction(r.realised_r) for r in results if r.realised_r is not None]
    assert s.r_trades == len(r_values)
    assert close(s.total_r, sum(r_values))
    total_losing_r = -sum(v for v in r_values if v < 0)
    assert 0 <= Fraction(s.max_drawdown_r.amount) <= total_losing_r + Fraction(1, 10**40)
    assert sum(m.trades for m in s.money.values()) == s.trades
    for m in s.money.values():
        assert m.net_pnl == m.gross_pnl - m.fees
        assert m.net_pnl == m.winning_pnl + m.losing_pnl


@pytest.mark.parametrize("seed", SEEDS)
def test_summary_does_not_depend_on_input_order(seed):
    _, results = random_results(seed)
    shuffled = results[:]
    random.Random(seed + 100).shuffle(shuffled)
    assert summarise(shuffled) == summarise(results)
