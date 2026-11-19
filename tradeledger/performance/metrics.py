"""Aggregate metrics over a list of TradeResults.

Formulas
--------
  win rate             wins / closed trades           (0-1; None if no trades)
  loss rate            losses / closed trades
  breakeven rate       breakevens / closed trades
  average R            sum(realised R) / trades with R
  average win R        mean R of winning trades
  average loss R       mean R of losing trades (a negative number)
  expectancy (R)       P(win) x avg win R + P(loss) x avg loss R
                       + P(breakeven) x 0, using only trades that have R.
                       Mathematically equal to average R; we compute it
                       from the parts because that is how it is explained.
  profit factor        sum of positive results / |sum of negative results|
                       None when there are no losing trades (division by
                       zero) or no trades at all.
  streaks              longest run of consecutive WINs / LOSSes in the
                       order results were realised. A breakeven ends both
                       kinds of streak.
  max drawdown         largest fall from a running peak of the cumulative
                       series, starting from 0 before the first trade.
                       Reported as a positive number in the series' unit
                       (R, or money in ONE currency). Never a percentage:
                       a percentage needs an account balance, which
                       TradeLedger does not record.

Money is always summarised per quote currency. Different currencies are
never added together.

Order of trades
---------------
Streaks, drawdown and cumulative series follow realised order: by
realised_at (exit time, or entry time for legacy trades with no recorded
exit), then by trade id so equal times always sort the same way.
"""

from dataclasses import dataclass, field
from decimal import Decimal, localcontext

from .trade_math import EXACT, ZERO, TradeResult


@dataclass(frozen=True)
class TradeRef:
    """Points at one trade, e.g. the best trade, with the value that ranked it."""

    trade_id: int
    symbol: str
    trade_date: str
    value: Decimal


@dataclass(frozen=True)
class Drawdown:
    amount: Decimal  # >= 0, in the unit of the series
    peak_trade_id: int | None  # None = the starting point (0)
    trough_trade_id: int | None  # None when there was no drawdown


@dataclass(frozen=True)
class MoneySummary:
    """Monetary results for ONE quote currency."""

    currency: str
    trades: int
    gross_pnl: Decimal  # before fees
    fees: Decimal
    net_pnl: Decimal  # after fees
    winning_pnl: Decimal  # sum of net P&L of winning trades (>= 0)
    losing_pnl: Decimal  # sum of net P&L of losing trades (<= 0)
    profit_factor: Decimal | None
    best_trade: TradeRef | None
    worst_trade: TradeRef | None
    max_drawdown: Drawdown


@dataclass(frozen=True)
class Summary:
    """Every metric for one group of closed trades."""

    trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate: Decimal | None
    loss_rate: Decimal | None
    breakeven_rate: Decimal | None
    r_trades: int  # trades with a usable realised R
    total_r: Decimal
    average_r: Decimal | None
    average_win_r: Decimal | None
    average_loss_r: Decimal | None
    expectancy_r: Decimal | None
    profit_factor_r: Decimal | None
    planned_rr_trades: int
    average_planned_rr: Decimal | None
    longest_win_streak: int
    longest_loss_streak: int
    max_drawdown_r: Drawdown
    best_trade_r: TradeRef | None
    worst_trade_r: TradeRef | None
    money: dict[str, MoneySummary] = field(default_factory=dict)


# ---------- Building blocks ----------


def chronological(results):
    """Sort into realised order: realised_at, then id, so ties always come
    out in the same order."""
    return sorted(results, key=lambda r: (r.realised_at, r.trade_id if r.trade_id is not None else 0))


def ratio(numerator, denominator):
    """numerator / denominator, or None if the denominator is zero."""
    if not denominator:
        return None
    with localcontext(EXACT):
        return Decimal(numerator) / Decimal(denominator)


def mean(values):
    values = list(values)
    if not values:
        return None
    with localcontext(EXACT):
        return sum(values, ZERO) / len(values)


def expectancy(win_rate, average_win, loss_rate, average_loss):
    """Expected R per trade from its parts. Missing parts count as zero
    only when their probability is zero (no wins -> no win term)."""
    if win_rate is None or loss_rate is None:
        return None
    with localcontext(EXACT):
        win_term = win_rate * average_win if average_win is not None else ZERO
        loss_term = loss_rate * average_loss if average_loss is not None else ZERO
        return win_term + loss_term


def profit_factor(values):
    # Everything, including the minus sign, stays inside the 60-digit
    # context: even unary minus rounds to the CURRENT precision (28 by
    # default). A test against exact fractions caught exactly that bug.
    with localcontext(EXACT):
        gains = sum((v for v in values if v > 0), ZERO)
        losses = sum((v for v in values if v < 0), ZERO)
        if losses == 0:
            return None
        return gains / -losses


def longest_streaks(outcomes):
    """Return (longest WIN run, longest LOSS run) for outcomes in order."""
    best = {"WIN": 0, "LOSS": 0}
    current_kind, current_length = None, 0
    for outcome in outcomes:
        if outcome == current_kind:
            current_length += 1
        else:
            current_kind, current_length = outcome, 1
        if outcome in best:
            best[outcome] = max(best[outcome], current_length)
    return best["WIN"], best["LOSS"]


def cumulative(values):
    totals, running = [], ZERO
    with localcontext(EXACT):
        for value in values:
            running += value
            totals.append(running)
    return totals


def max_drawdown(points):
    """points: list of (trade_id, value) in date order.

    Walks the running total, remembering the highest point so far. The
    drawdown at each step is peak - current; we keep the largest.
    """
    peak_value, peak_id = ZERO, None
    worst = Drawdown(ZERO, None, None)
    running = ZERO
    with localcontext(EXACT):
        for trade_id, value in points:
            running += value
            if running > peak_value:
                peak_value, peak_id = running, trade_id
            fall = peak_value - running
            if fall > worst.amount:
                worst = Drawdown(fall, peak_id, trade_id)
    return worst


def _best_and_worst(results, value_of):
    candidates = [(value_of(r), r) for r in results if value_of(r) is not None]
    if not candidates:
        return None, None
    # Ties go to the earliest trade, because candidates are in date order
    # and max()/min() keep the first of equal items.
    best_value, best = max(candidates, key=lambda c: c[0])
    worst_value, worst = min(candidates, key=lambda c: c[0])
    return (
        TradeRef(best.trade_id, best.symbol, best.trade_date, best_value),
        TradeRef(worst.trade_id, worst.symbol, worst.trade_date, worst_value),
    )


# ---------- Summaries ----------


def summarise_money(results, currency):
    """Monetary summary for the trades in ONE currency."""
    rows = [r for r in chronological(results) if r.quote_currency == currency]
    with localcontext(EXACT):
        nets = [r.net_pnl for r in rows]
        best, worst = _best_and_worst(rows, lambda r: r.net_pnl)
        return MoneySummary(
            currency=currency,
            trades=len(rows),
            gross_pnl=sum((r.gross_pnl for r in rows), ZERO),
            fees=sum((r.fees for r in rows), ZERO),
            net_pnl=sum(nets, ZERO),
            winning_pnl=sum((n for n in nets if n > 0), ZERO),
            losing_pnl=sum((n for n in nets if n < 0), ZERO),
            profit_factor=profit_factor(nets),
            best_trade=best,
            worst_trade=worst,
            max_drawdown=max_drawdown([(r.trade_id, r.net_pnl) for r in rows]),
        )


def summarise(results: list[TradeResult]) -> Summary:
    """All metrics for a group of closed trades (any order; sorted here)."""
    rows = chronological(results)
    total = len(rows)
    wins = sum(1 for r in rows if r.outcome == "WIN")
    losses = sum(1 for r in rows if r.outcome == "LOSS")
    breakevens = total - wins - losses

    with_r = [r for r in rows if r.realised_r is not None]
    r_values = [r.realised_r for r in with_r]
    win_rs = [r.realised_r for r in with_r if r.outcome == "WIN"]
    loss_rs = [r.realised_r for r in with_r if r.outcome == "LOSS"]

    average_win_r = mean(win_rs)
    average_loss_r = mean(loss_rs)
    planned = [r.planned_rr for r in rows if r.planned_rr is not None]
    win_streak, loss_streak = longest_streaks(r.outcome for r in rows)
    best_r, worst_r = _best_and_worst(with_r, lambda r: r.realised_r)

    with localcontext(EXACT):
        total_r = sum(r_values, ZERO)

    return Summary(
        trades=total,
        wins=wins,
        losses=losses,
        breakevens=breakevens,
        win_rate=ratio(wins, total),
        loss_rate=ratio(losses, total),
        breakeven_rate=ratio(breakevens, total),
        r_trades=len(with_r),
        total_r=total_r,
        average_r=mean(r_values),
        average_win_r=average_win_r,
        average_loss_r=average_loss_r,
        expectancy_r=expectancy(
            ratio(len(win_rs), len(with_r)),
            average_win_r,
            ratio(len(loss_rs), len(with_r)),
            average_loss_r,
        ),
        profit_factor_r=profit_factor(r_values),
        planned_rr_trades=len(planned),
        average_planned_rr=mean(planned),
        longest_win_streak=win_streak,
        longest_loss_streak=loss_streak,
        max_drawdown_r=max_drawdown([(r.trade_id, r.realised_r) for r in with_r]),
        best_trade_r=best_r,
        worst_trade_r=worst_r,
        money={
            currency: summarise_money(rows, currency) for currency in sorted({r.quote_currency for r in rows})
        },
    )
