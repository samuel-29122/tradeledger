# TradeLedger — Phase 3 Complete Code Listing

Generated directly from the files that passed the 261-test run, so this listing is identical to the tested code.
Files not listed here are unchanged from Phase 2 (see PHASE_2_CODE.md or the zip).

## Contents

- `tradeledger/performance/__init__.py` — NEW
- `tradeledger/performance/trade_math.py` — NEW
- `tradeledger/performance/metrics.py` — NEW
- `tradeledger/performance/report.py` — NEW
- `tradeledger/performance/formatting.py` — NEW
- `tradeledger/repositories/trades.py` — MODIFIED
- `tradeledger/cli.py` — MODIFIED
- `tradeledger/config.py` — MODIFIED
- `tradeledger/routes.py` — MODIFIED
- `tests/factories.py` — NEW
- `tests/test_trade_math.py` — NEW
- `tests/test_metrics.py` — NEW
- `tests/test_report.py` — NEW
- `tests/test_properties.py` — NEW

---

## `tradeledger/performance/__init__.py` (NEW)

**Purpose:** Public interface of the engine: `analyse()` (pure) and `build_report()` (loads from the database).

```python
"""TradeLedger performance analytics engine.

Layers, from the bottom up:

  trade_math.py   one trade in, one TradeResult out (P&L, risk, R, outcome)
  metrics.py      a list of TradeResults in, one Summary out
  report.py       database rows in, a full PerformanceReport out (grouped)
  formatting.py   reports out to JSON-safe dicts and display strings

Nothing in this package imports Flask, and only report.build_report()
touches the database, so every calculation can be tested on its own.
"""

from .report import PerformanceReport, analyse, build_report

__all__ = ["PerformanceReport", "analyse", "build_report"]
```

---

## `tradeledger/performance/trade_math.py` (NEW)

**Purpose:** One trade in, one `TradeResult` out: gross/net P&L, initial risk, realised R, planned R:R, outcome. Defines the 60-digit `EXACT` Decimal context.

```python
"""Per-trade calculations.

Formulas (all amounts in the instrument's quote currency)
---------------------------------------------------------
  direction sign   LONG = +1, SHORT = -1
  price move       sign x (exit - entry)
  gross P&L        price move x position_size x contract_multiplier
  net P&L          gross P&L - fees
  risk per unit    sign x (entry - stop_loss)        must be > 0
  initial risk     risk per unit x position_size x contract_multiplier
  realised R       net P&L / initial risk
  planned R:R      sign x (take_profit - entry) / risk per unit
  outcome          WIN if net P&L > 0, LOSS if < 0, BREAKEVEN if exactly 0

Conventions
  * Initial risk EXCLUDES fees: it is the money lost if price reaches the
    stop. Fees then reduce net P&L, so a stopped-out trade with fees is
    slightly worse than -1R (e.g. -1.035R). This is intentional: it shows
    the real cost of trading.
  * Planned R:R is price-based and ignores fees. It describes the plan.
  * No rounding happens here. Rounding is for display only (formatting.py).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Context, Decimal, DivisionByZero, InvalidOperation, Overflow, localcontext
from typing import Mapping, Optional

# Products of three 15-digit inputs need up to 45 digits; 60 keeps them
# exact. Only divisions (R, ratios) are rounded, at the 60th digit.
EXACT = Context(prec=60, traps=[InvalidOperation, DivisionByZero, Overflow])

ZERO = Decimal("0")
SIGN = {"LONG": 1, "SHORT": -1}


class IncompleteTradeError(Exception):
    """The trade cannot be evaluated (open, missing or invalid data)."""


@dataclass(frozen=True)
class TradeResult:
    """Everything calculated for one closed trade."""

    trade_id: int
    symbol: str
    quote_currency: str
    trade_date: str
    direction: str
    strategy: Optional[str]
    session: Optional[str]
    gross_pnl: Decimal
    fees: Decimal
    net_pnl: Decimal
    outcome: str
    initial_risk: Optional[Decimal]          # None when the risk is invalid
    realised_r: Optional[Decimal]            # None when the risk is invalid
    planned_rr: Optional[Decimal]            # None without a valid target
    r_unavailable_reason: Optional[str]      # why realised_r is None


# ---------- Small pure functions (each tested on its own) ----------

def price_move(direction, entry, exit_price):
    with localcontext(EXACT):
        return SIGN[direction] * (exit_price - entry)


def gross_pnl(direction, entry, exit_price, size, multiplier):
    with localcontext(EXACT):
        return price_move(direction, entry, exit_price) * size * multiplier


def net_pnl(gross, fees):
    with localcontext(EXACT):
        return gross - fees


def risk_per_unit(direction, entry, stop):
    """Positive when the stop is on the losing side of entry."""
    with localcontext(EXACT):
        return SIGN[direction] * (entry - stop)


def initial_risk(direction, entry, stop, size, multiplier):
    """Money at risk if the stop is hit, or None if the stop is invalid."""
    per_unit = risk_per_unit(direction, entry, stop)
    if per_unit <= 0:
        return None
    with localcontext(EXACT):
        return per_unit * size * multiplier


def realised_r(net, risk):
    """Net P&L expressed in units of initial risk, or None if risk is unusable."""
    if risk is None or risk <= 0:
        return None
    with localcontext(EXACT):
        return net / risk


def planned_rr(direction, entry, stop, target):
    """Planned reward divided by planned risk, or None if not calculable."""
    if target is None:
        return None
    risk = risk_per_unit(direction, entry, stop)
    with localcontext(EXACT):
        reward = SIGN[direction] * (target - entry)
    if risk <= 0 or reward <= 0:
        return None
    with localcontext(EXACT):
        return reward / risk


def classify(net):
    if net > 0:
        return "WIN"
    if net < 0:
        return "LOSS"
    return "BREAKEVEN"


# ---------- Input checking ----------

def _decimal(trade, field, *, required=True):
    """Read a field as Decimal. Floats are refused: they may already be inexact."""
    value = trade.get(field)
    if value is None:
        if required:
            raise IncompleteTradeError(f"missing {field}")
        return None
    if isinstance(value, (float, bool)):
        raise IncompleteTradeError(f"{field} must be Decimal, int or str, not {type(value).__name__}")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise IncompleteTradeError(f"{field} is not a number") from None
    if not number.is_finite():
        raise IncompleteTradeError(f"{field} is not a finite number")
    return number


# ---------- One trade ----------

def evaluate_trade(trade: Mapping) -> TradeResult:
    """Calculate a TradeResult for one CLOSED trade.

    `trade` is a mapping such as a row from repositories.trades.list_trades():
    it needs id, symbol, quote_currency, trade_date, direction, status,
    entry_price, stop_loss, exit_price, position_size, fees,
    contract_multiplier, and optionally take_profit, strategy, session.

    Raises IncompleteTradeError for open trades and for data that cannot
    produce a trustworthy P&L. A trade whose P&L is known but whose risk is
    invalid is NOT an error: it gets realised_r = None plus a reason.
    """
    if trade.get("status") != "CLOSED":
        raise IncompleteTradeError("trade is not closed, so it has no realised result")
    direction = trade.get("direction")
    if direction not in SIGN:
        raise IncompleteTradeError("direction must be LONG or SHORT")
    for field in ("symbol", "quote_currency", "trade_date"):
        if not trade.get(field):
            raise IncompleteTradeError(f"missing {field}")
    try:
        datetime.strptime(trade["trade_date"], "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        raise IncompleteTradeError("trade_date must look like 2026-03-02T08:15:00Z") from None

    with localcontext(EXACT):
        entry = _decimal(trade, "entry_price")
        stop = _decimal(trade, "stop_loss")
        exit_price = _decimal(trade, "exit_price")
        size = _decimal(trade, "position_size")
        multiplier = _decimal(trade, "contract_multiplier")
        target = _decimal(trade, "take_profit", required=False)
        fees = _decimal(trade, "fees", required=False) or ZERO

        if size <= 0:
            raise IncompleteTradeError("position_size must be greater than zero")
        if multiplier <= 0:
            raise IncompleteTradeError("contract_multiplier must be greater than zero")
        if fees < 0:
            raise IncompleteTradeError("fees must not be negative")

        gross = gross_pnl(direction, entry, exit_price, size, multiplier)
        net = net_pnl(gross, fees)
        risk = initial_risk(direction, entry, stop, size, multiplier)
        r_value = realised_r(net, risk)

    reason = None
    if r_value is None:
        reason = "stop-loss is not on the losing side of entry, so initial risk is not positive"

    return TradeResult(
        trade_id=trade.get("id"),
        symbol=trade["symbol"],
        quote_currency=trade["quote_currency"],
        trade_date=trade["trade_date"],
        direction=direction,
        strategy=trade.get("strategy"),
        session=trade.get("session"),
        gross_pnl=gross,
        fees=fees,
        net_pnl=net,
        outcome=classify(net),
        initial_risk=risk,
        realised_r=r_value,
        planned_rr=planned_rr(direction, entry, stop, target),
        r_unavailable_reason=reason,
    )
```

---

## `tradeledger/performance/metrics.py` (NEW)

**Purpose:** Aggregates TradeResults into a `Summary`: rates, R averages, expectancy, profit factor, streaks, drawdown, best/worst, and per-currency `MoneySummary`.

```python
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
  streaks              longest run of consecutive WINs / LOSSes in date
                       order. A breakeven ends both kinds of streak.
  max drawdown         largest fall from a running peak of the cumulative
                       series, starting from 0 before the first trade.
                       Reported as a positive number in the series' unit
                       (R, or money in ONE currency). Never a percentage:
                       a percentage needs an account balance, which
                       TradeLedger does not record.

Money is always summarised per quote currency. Different currencies are
never added together.
"""

from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from typing import Dict, List, Optional

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
    amount: Decimal                    # >= 0, in the unit of the series
    peak_trade_id: Optional[int]       # None = the starting point (0)
    trough_trade_id: Optional[int]     # None when there was no drawdown


@dataclass(frozen=True)
class MoneySummary:
    """Monetary results for ONE quote currency."""

    currency: str
    trades: int
    gross_pnl: Decimal                 # before fees
    fees: Decimal
    net_pnl: Decimal                   # after fees
    winning_pnl: Decimal               # sum of net P&L of winning trades (>= 0)
    losing_pnl: Decimal                # sum of net P&L of losing trades (<= 0)
    profit_factor: Optional[Decimal]
    best_trade: Optional[TradeRef]
    worst_trade: Optional[TradeRef]
    max_drawdown: Drawdown


@dataclass(frozen=True)
class Summary:
    """Every metric for one group of closed trades."""

    trades: int
    wins: int
    losses: int
    breakevens: int
    win_rate: Optional[Decimal]
    loss_rate: Optional[Decimal]
    breakeven_rate: Optional[Decimal]
    r_trades: int                      # trades with a usable realised R
    total_r: Decimal
    average_r: Optional[Decimal]
    average_win_r: Optional[Decimal]
    average_loss_r: Optional[Decimal]
    expectancy_r: Optional[Decimal]
    profit_factor_r: Optional[Decimal]
    planned_rr_trades: int
    average_planned_rr: Optional[Decimal]
    longest_win_streak: int
    longest_loss_streak: int
    max_drawdown_r: Drawdown
    best_trade_r: Optional[TradeRef]
    worst_trade_r: Optional[TradeRef]
    money: Dict[str, MoneySummary] = field(default_factory=dict)


# ---------- Building blocks ----------

def chronological(results):
    """Sort by trade date, then id, so ties always come out in the same order."""
    return sorted(results, key=lambda r: (r.trade_date, r.trade_id if r.trade_id is not None else 0))


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


def summarise(results: List[TradeResult]) -> Summary:
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
            ratio(len(win_rs), len(with_r)), average_win_r,
            ratio(len(loss_rs), len(with_r)), average_loss_r,
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
            currency: summarise_money(rows, currency)
            for currency in sorted({r.quote_currency for r in rows})
        },
    )
```

---

## `tradeledger/performance/report.py` (NEW)

**Purpose:** Splits open/excluded/closed trades, groups by instrument, strategy, session, weekday, date and month, and builds the chart-ready cumulative R series.

```python
"""Build a complete PerformanceReport.

analyse(trades)          pure: list of trade dicts in, report out
build_report(conn, ...)  loads trades from the database, then calls analyse()

Grouping keys (all from trade_date, which is UTC)
  by_weekday   'Monday' ... 'Sunday', in calendar order
  by_date      'YYYY-MM-DD'
  by_month     'YYYY-MM'

Known limitation: TradeLedger records one date per trade (the entry time),
not a separate exit time. Ordering, streaks, drawdown and date groups all
use that date.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from ..repositories.trades import list_trades
from .metrics import Summary, chronological, cumulative, summarise
from .trade_math import IncompleteTradeError, TradeResult, evaluate_trade

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
UNSPECIFIED = "Unspecified"


@dataclass(frozen=True)
class ExcludedTrade:
    trade_id: Optional[int]
    reason: str


@dataclass(frozen=True)
class CurvePoint:
    """One point on the cumulative-R chart. This is NOT account equity."""

    trade_id: int
    trade_date: str
    symbol: str
    realised_r: Decimal
    cumulative_r: Decimal


@dataclass(frozen=True)
class PerformanceReport:
    overall: Summary
    open_trades: int
    excluded: List[ExcludedTrade]
    r_unavailable: List[ExcludedTrade]      # closed trades counted, but with no R
    by_instrument: Dict[str, Summary]
    by_strategy: Dict[str, Summary]
    by_session: Dict[str, Summary]
    by_weekday: Dict[str, Summary]
    by_date: Dict[str, Summary]
    by_month: Dict[str, Summary]
    cumulative_r: List[CurvePoint]


def _weekday(result):
    moment = datetime.strptime(result.trade_date, "%Y-%m-%dT%H:%M:%SZ")
    return WEEKDAYS[moment.weekday()]


def evaluate_trades(trades):
    """Split trades into evaluated results, an open count and exclusions."""
    results, excluded, open_count = [], [], 0
    for trade in trades:
        if trade.get("status") == "OPEN":
            open_count += 1
            continue
        try:
            results.append(evaluate_trade(trade))
        except IncompleteTradeError as exc:
            excluded.append(ExcludedTrade(trade.get("id"), str(exc)))
    return results, open_count, excluded


def group_summaries(results, key, order=None):
    """Summarise each group. `order` fixes the key order (e.g. weekdays);
    otherwise keys are sorted alphabetically."""
    groups: Dict[str, List[TradeResult]] = {}
    for result in results:
        groups.setdefault(key(result), []).append(result)
    keys = [k for k in order if k in groups] if order else sorted(groups)
    return {k: summarise(groups[k]) for k in keys}


def cumulative_r_curve(results):
    """Chart-ready running total of realised R, in date order."""
    rows = [r for r in chronological(results) if r.realised_r is not None]
    totals = cumulative([r.realised_r for r in rows])
    return [
        CurvePoint(r.trade_id, r.trade_date, r.symbol, r.realised_r, total)
        for r, total in zip(rows, totals)
    ]


def analyse(trades) -> PerformanceReport:
    results, open_count, excluded = evaluate_trades(trades)
    return PerformanceReport(
        overall=summarise(results),
        open_trades=open_count,
        excluded=excluded,
        r_unavailable=[
            ExcludedTrade(r.trade_id, r.r_unavailable_reason)
            for r in chronological(results)
            if r.realised_r is None
        ],
        by_instrument=group_summaries(results, lambda r: r.symbol),
        by_strategy=group_summaries(results, lambda r: r.strategy or UNSPECIFIED),
        by_session=group_summaries(results, lambda r: r.session or UNSPECIFIED),
        by_weekday=group_summaries(results, _weekday, order=WEEKDAYS),
        by_date=group_summaries(results, lambda r: r.trade_date[:10]),
        by_month=group_summaries(results, lambda r: r.trade_date[:7]),
        cumulative_r=cumulative_r_curve(results),
    )


def build_report(conn, *, instrument_id=None, start=None, end=None) -> PerformanceReport:
    """Load trades with the Phase 2 repository filters and analyse them."""
    return analyse(list_trades(conn, instrument_id=instrument_id, start=start, end=end))
```

---

## `tradeledger/performance/formatting.py` (NEW)

**Purpose:** Exact JSON conversion (Decimal -> string) and display rounding (`format_r`, `format_money`, `format_rate`, ...). The only place rounding happens.

```python
"""Turning reports into JSON and human-readable text.

Rounding policy
---------------
* Calculations never round (except division at the 60th significant
  digit, see trade_math.EXACT).
* Rounding happens only here, at the very end, for display:
      R values and R:R   2 decimal places    e.g. +1.97R, 2.38
      money              2 decimal places    e.g. 798.50 USD
      rates              1 decimal place     e.g. 50.0%
  using ROUND_HALF_UP, the "school" rule people expect on screen.
  (Currencies without pence/cents, such as JPY, would need 0 places;
  that is a known simplification.)
* JSON output (to_jsonable) does NOT round. Decimals become strings so the
  receiver gets the exact value, and no float is ever created.

Why round late? If you round each trade's R to 2 places and then add 50 of
them, the rounding errors add up. Rounding once, after adding, does not.
"""

from dataclasses import asdict, is_dataclass
from decimal import ROUND_HALF_UP, Decimal

from .trade_math import EXACT

TWO_PLACES = Decimal("0.01")
ONE_PLACE = Decimal("0.1")
NOT_AVAILABLE = "n/a"


def decimal_to_text(value: Decimal) -> str:
    """Exact, plain notation: Decimal('4.0E+2') -> '400', no exponents."""
    # normalize() must use the 60-digit context, or it would round to 28.
    text = format(value.normalize(EXACT), "f")
    return "0" if text in ("-0", "") else text


def to_jsonable(obj):
    """Recursively convert dataclasses, dicts and lists for json.dumps().
    Decimal -> str (exact). Everything else is left as is."""
    if is_dataclass(obj) and not isinstance(obj, type):
        obj = asdict(obj)
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if isinstance(obj, Decimal):
        return decimal_to_text(obj)
    return obj


def round_display(value: Decimal, places: Decimal) -> Decimal:
    return value.quantize(places, rounding=ROUND_HALF_UP)


def format_r(value):
    if value is None:
        return NOT_AVAILABLE
    rounded = round_display(value, TWO_PLACES)
    if rounded == 0:
        return "0.00R"  # never "-0.00R" or "+0.00R"
    return f"{rounded:+}R"


def format_r_amount(value):
    """A size in R with no sign, e.g. a drawdown: '1.05R'."""
    if value is None:
        return NOT_AVAILABLE
    return f"{round_display(abs(value), TWO_PLACES)}R"


def format_ratio(value):
    """Plain number to 2 places, e.g. planned R:R or profit factor."""
    if value is None:
        return NOT_AVAILABLE
    return f"{round_display(value, TWO_PLACES)}"


def format_money(value, currency):
    if value is None:
        return NOT_AVAILABLE
    rounded = round_display(value, TWO_PLACES)
    if rounded == 0:
        rounded = abs(rounded)
    return f"{rounded:,} {currency}"


def format_rate(value):
    """A 0-1 fraction as a percentage."""
    if value is None:
        return NOT_AVAILABLE
    return f"{round_display(value * 100, ONE_PLACE)}%"
```

---

## `tradeledger/repositories/trades.py` (MODIFIED)

**Purpose:** Trade rows now also include `quote_currency` and `contract_multiplier` (joined from instruments). Nothing removed.

```python
"""Trade repository.

Return values
  create_trade   -> new id (int)
  get_trade      -> dict (includes the instrument's symbol, quote_currency
                    and contract_multiplier), or None
  list_trades    -> list of dicts, newest trade_date first (empty list if none)
  update_trade   -> True if a trade was updated, False if the id does not exist
  delete_trade   -> True if a trade was deleted, False if the id does not exist

Errors
  ValidationError  invalid input, including an unknown instrument_id
                   (nothing is written)

Numeric fields come back as decimal.Decimal (or None), never float.
"""

import sqlite3
from decimal import Decimal

from ..validation import ValidationError, date_range_bounds, validate_trade

_DECIMAL_FIELDS = (
    "entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees",
    "contract_multiplier",
)

_WRITE_FIELDS = (
    "instrument_id", "trade_date", "direction", "entry_price", "stop_loss",
    "take_profit", "exit_price", "position_size", "fees", "status",
    "strategy", "session", "notes",
)

_SELECT = """
    SELECT t.id, t.instrument_id, i.symbol, i.quote_currency, i.contract_multiplier,
           t.trade_date, t.direction,
           t.entry_price, t.stop_loss, t.take_profit, t.exit_price,
           t.position_size, t.fees, t.status, t.strategy, t.session,
           t.notes, t.created_at, t.updated_at
    FROM trades AS t
    JOIN instruments AS i ON i.id = t.instrument_id
"""


def _to_dict(row):
    if row is None:
        return None
    record = dict(row)
    for field in _DECIMAL_FIELDS:
        if record[field] is not None:
            record[field] = Decimal(record[field])
    return record


def _raise_if_unknown_instrument(exc):
    """Translate SQLite's foreign-key error into a friendly ValidationError."""
    if "FOREIGN KEY" in str(exc):
        raise ValidationError({"instrument_id": "does not match an existing instrument"}) from exc
    raise exc


def create_trade(conn, data):
    clean = validate_trade(data)
    placeholders = ", ".join("?" for _ in _WRITE_FIELDS)
    try:
        with conn:
            cursor = conn.execute(
                f"INSERT INTO trades ({', '.join(_WRITE_FIELDS)}) VALUES ({placeholders})",
                tuple(clean[field] for field in _WRITE_FIELDS),
            )
    except sqlite3.IntegrityError as exc:
        _raise_if_unknown_instrument(exc)
    return cursor.lastrowid


def get_trade(conn, trade_id):
    row = conn.execute(_SELECT + " WHERE t.id = ?", (trade_id,)).fetchone()
    return _to_dict(row)


def list_trades(conn, *, instrument_id=None, start=None, end=None, status=None):
    """List trades, optionally filtered.

    instrument_id  only this instrument
    start, end     inclusive dates 'YYYY-MM-DD' (UTC) on trade_date
    status         'OPEN' or 'CLOSED'
    """
    lower, upper = date_range_bounds(start, end)

    # Build the WHERE clause from fixed text fragments only. User values
    # never become part of the SQL string; they go in `params`.
    conditions, params = [], []
    if instrument_id is not None:
        conditions.append("t.instrument_id = ?")
        params.append(instrument_id)
    if lower is not None:
        conditions.append("t.trade_date >= ?")
        params.append(lower)
    if upper is not None:
        conditions.append("t.trade_date < ?")
        params.append(upper)
    if status is not None:
        if status not in ("OPEN", "CLOSED"):
            raise ValidationError({"status": "must be one of: OPEN, CLOSED"})
        conditions.append("t.status = ?")
        params.append(status)

    sql = _SELECT
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY t.trade_date DESC, t.id DESC"
    return [_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def update_trade(conn, trade_id, data):
    """Replace every editable field of a trade with validated new values."""
    clean = validate_trade(data)
    assignments = ", ".join(f"{field} = ?" for field in _WRITE_FIELDS)
    try:
        with conn:
            cursor = conn.execute(
                f"""
                UPDATE trades
                SET {assignments},
                    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
                WHERE id = ?
                """,
                (*(clean[field] for field in _WRITE_FIELDS), trade_id),
            )
    except sqlite3.IntegrityError as exc:
        _raise_if_unknown_instrument(exc)
    return cursor.rowcount == 1


def delete_trade(conn, trade_id):
    with conn:
        cursor = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    return cursor.rowcount == 1
```

---

## `tradeledger/cli.py` (MODIFIED)

**Purpose:** New `report` command (`--instrument`, `--start`, `--end`, `--json`). Existing commands unchanged.

```python
"""Command-line commands, run through Flask:

    flask --app tradeledger init-db      create tables (keeps existing data)
    flask --app tradeledger reset-db     delete ALL data and recreate tables
    flask --app tradeledger seed-db      add synthetic sample data (repeatable)
    flask --app tradeledger db-info      show tables, row counts and schema version
    flask --app tradeledger analytics    run the analytical queries
    flask --app tradeledger run-sql "SELECT ..."   run your own read-only query
    flask --app tradeledger report       performance report (Phase 3 engine)
"""

import json
import sqlite3
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from . import analytics
from .db import drop_all, get_db, init_schema, schema_version
from .performance import build_report
from .performance.formatting import format_money, format_r, format_r_amount, format_rate, format_ratio, to_jsonable
from .repositories.instruments import get_instrument_by_symbol
from .seed import seed_sample_data
from .validation import ValidationError


def print_table(rows):
    """Print a list of dicts as an aligned text table."""
    if not rows:
        click.echo("(no rows)")
        return
    headers = list(rows[0].keys())
    cells = [[("NULL" if row[h] is None else str(row[h])) for h in headers] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    click.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths)))
    click.echo("  ".join("-" * w for w in widths))
    for r in cells:
        click.echo("  ".join(value.ljust(w) for value, w in zip(r, widths)))


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create any missing tables. Existing data is kept."""
    init_schema(get_db())
    click.echo(f"Database ready: {current_app.config['DATABASE']}")


@click.command("reset-db")
@with_appcontext
@click.confirmation_option(prompt="This deletes ALL trades and instruments. Continue?")
def reset_db_command():
    """Drop every table and recreate the empty schema."""
    conn = get_db()
    drop_all(conn)
    init_schema(conn)
    click.echo("Database reset. All tables are empty.")


@click.command("seed-db")
@with_appcontext
def seed_db_command():
    """Insert synthetic sample data. Running it twice adds nothing new."""
    conn = get_db()
    init_schema(conn)
    inserted = seed_sample_data(conn)
    click.echo(
        f"Sample data: {inserted['instruments']} instrument(s) and "
        f"{inserted['trades']} trade(s) inserted."
    )


@click.command("db-info")
@with_appcontext
def db_info_command():
    """Show where the database is, its schema version and row counts."""
    conn = get_db()
    click.echo(f"Database file:  {current_app.config['DATABASE']}")
    click.echo(f"Schema version: {schema_version(conn)}")
    tables = [
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        )
    ]
    if not tables:
        click.echo("No tables yet. Run: flask --app tradeledger init-db")
        return
    # Table names come from sqlite_master, not from user input, so it is safe
    # to place them in the SQL text. (Placeholders cannot stand for names.)
    rows = [
        {"table": name, "rows": conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]}
        for name in tables
    ]
    print_table(rows)


@click.command("analytics")
@with_appcontext
@click.argument("name", required=False, type=click.Choice(sorted(analytics.ALL_QUERIES)))
def analytics_command(name):
    """Run one analytical query by NAME, or all of them."""
    conn = get_db()
    names = [name] if name else list(analytics.ALL_QUERIES)
    for query_name in names:
        click.echo(f"\n== {query_name} ==")
        rows = [dict(row) for row in conn.execute(analytics.ALL_QUERIES[query_name])]
        print_table(rows)


@click.command("run-sql")
@with_appcontext
@click.argument("sql")
def run_sql_command(sql):
    """Run your own SQL against a READ-ONLY connection (it cannot change data)."""
    path = Path(current_app.config["DATABASE"]).resolve()
    if not path.exists():
        raise click.ClickException("Database file not found. Run init-db first.")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(sql).fetchall()]
    except sqlite3.Error as exc:
        raise click.ClickException(f"SQL error: {exc}") from exc
    finally:
        conn.close()
    print_table(rows)


def summary_row(label, summary):
    """One display row for a group summary (rounded for display only)."""
    return {
        "group": label,
        "trades": summary.trades,
        "W/L/BE": f"{summary.wins}/{summary.losses}/{summary.breakevens}",
        "win rate": format_rate(summary.win_rate),
        "total R": format_r(summary.total_r),
        "expectancy": format_r(summary.expectancy_r),
        "PF (R)": format_ratio(summary.profit_factor_r),
        "max DD": format_r_amount(summary.max_drawdown_r.amount),
    }


def print_report(report):
    o = report.overall
    click.echo("== Overall (closed trades) ==")
    lines = [
        ("Closed trades", o.trades),
        ("Open trades (not in results)", report.open_trades),
        ("Wins / losses / breakevens", f"{o.wins} / {o.losses} / {o.breakevens}"),
        ("Win rate / loss rate", f"{format_rate(o.win_rate)} / {format_rate(o.loss_rate)}"),
        ("Trades with realised R", o.r_trades),
        ("Total R (cumulative)", format_r(o.total_r)),
        ("Average R", format_r(o.average_r)),
        ("Average winning R", format_r(o.average_win_r)),
        ("Average losing R", format_r(o.average_loss_r)),
        ("Expectancy per trade", format_r(o.expectancy_r)),
        ("Profit factor (R)", format_ratio(o.profit_factor_r)),
        ("Average planned R:R", f"{format_ratio(o.average_planned_rr)} ({o.planned_rr_trades} trades with a target)"),
        ("Longest win / loss streak", f"{o.longest_win_streak} / {o.longest_loss_streak}"),
        ("Max drawdown (R)", format_r_amount(o.max_drawdown_r.amount)),
    ]
    if o.best_trade_r:
        lines.append(("Best trade (R)", f"#{o.best_trade_r.trade_id} {o.best_trade_r.symbol} {format_r(o.best_trade_r.value)}"))
        lines.append(("Worst trade (R)", f"#{o.worst_trade_r.trade_id} {o.worst_trade_r.symbol} {format_r(o.worst_trade_r.value)}"))
    width = max(len(label) for label, _ in lines)
    for label, value in lines:
        click.echo(f"{label.ljust(width)}  {value}")

    click.echo("\n== Money, per quote currency (never added together) ==")
    print_table([
        {
            "currency": m.currency,
            "trades": m.trades,
            "gross P&L": format_money(m.gross_pnl, m.currency),
            "fees": format_money(m.fees, m.currency),
            "net P&L": format_money(m.net_pnl, m.currency),
            "winners": format_money(m.winning_pnl, m.currency),
            "losers": format_money(m.losing_pnl, m.currency),
            "PF": format_ratio(m.profit_factor),
            "max DD": format_money(m.max_drawdown.amount, m.currency),
        }
        for m in o.money.values()
    ])

    for title, groups in (
        ("By instrument", report.by_instrument),
        ("By strategy", report.by_strategy),
        ("By session", report.by_session),
        ("By day of week (UTC)", report.by_weekday),
        ("By month", report.by_month),
    ):
        click.echo(f"\n== {title} ==")
        print_table([summary_row(label, s) for label, s in groups.items()])

    click.echo("\n== Cumulative R (chart data; not account equity) ==")
    print_table([
        {"trade": p.trade_id, "date": p.trade_date[:10], "symbol": p.symbol,
         "R": format_r(p.realised_r), "cumulative": format_r(p.cumulative_r)}
        for p in report.cumulative_r
    ])

    for title, items in (("Excluded trades", report.excluded),
                         ("Closed trades without a realised R", report.r_unavailable)):
        if items:
            click.echo(f"\n== {title} ==")
            for item in items:
                click.echo(f"#{item.trade_id}: {item.reason}")


@click.command("report")
@click.option("--instrument", help="Only this symbol, e.g. XAUUSD.")
@click.option("--start", help="First date to include, YYYY-MM-DD (UTC).")
@click.option("--end", help="Last date to include, YYYY-MM-DD (UTC).")
@click.option("--json", "as_json", is_flag=True, help="Exact JSON output instead of a rounded table.")
@with_appcontext
def report_command(instrument, start, end, as_json):
    """Performance report calculated by the Python analytics engine."""
    conn = get_db()
    instrument_id = None
    if instrument:
        found = get_instrument_by_symbol(conn, instrument)
        if found is None:
            raise click.ClickException(f"No instrument with symbol '{instrument}'.")
        instrument_id = found["id"]
    try:
        report = build_report(conn, instrument_id=instrument_id, start=start, end=end)
    except ValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(to_jsonable(report), indent=2))
    else:
        print_report(report)


def init_app(app):
    for command in (
        init_db_command,
        reset_db_command,
        seed_db_command,
        db_info_command,
        analytics_command,
        run_sql_command,
        report_command,
    ):
        app.cli.add_command(command)
```

---

## `tradeledger/config.py` (MODIFIED)

**Purpose:** Version 0.3.0.

```python
"""Application configuration.

Settings are read from environment variables so that secrets never live in
source code. Each class represents one environment the app can run in.
"""

import os


class Config:
    """Settings shared by every environment."""

    APP_NAME = "TradeLedger"
    APP_VERSION = "0.3.0"

    # Read from the environment. Never hard-code a real key in this file.
    SECRET_KEY = os.environ.get("SECRET_KEY")

    TESTING = False

    # When True, the app refuses to start without a SECRET_KEY.
    REQUIRE_SECRET_KEY = True

    # Path to the SQLite database file. If not set, create_app() uses
    # instance/tradeledger.db inside the project folder.
    DATABASE = os.environ.get("DATABASE_PATH")


class DevelopmentConfig(Config):
    """Local development on your own machine."""

    # In development a missing key is tolerated: the app generates a
    # temporary one and logs a warning (see create_app).
    REQUIRE_SECRET_KEY = False


class TestingConfig(Config):
    """Used by pytest. Fixed values make tests repeatable."""

    TESTING = True
    SECRET_KEY = "test-secret-key-not-for-real-use"
    REQUIRE_SECRET_KEY = False

    # Deliberately no default: tests must pass their own temporary database
    # path, so a test can never touch your development data.
    DATABASE = None


class ProductionConfig(Config):
    """Deployed environments (e.g. AWS in Phase 8)."""

    REQUIRE_SECRET_KEY = True


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
```

---

## `tradeledger/routes.py` (MODIFIED)

**Purpose:** Roadmap: Phase 3 Done, Phase 4 Next.

```python
"""HTTP routes for the main pages of TradeLedger.

Routes stay thin: they receive a request, gather what the page needs and
return a response. Database and calculation code will live in separate
modules (Phases 2 and 3), not here.
"""

from flask import Blueprint, current_app, jsonify, render_template

main_bp = Blueprint("main", __name__)

# What the home page shows about the build plan. Kept as data, not
# hard-coded HTML, so the template simply loops over it.
ROADMAP = [
    {"phase": 1, "title": "Project setup", "status": "Done"},
    {"phase": 2, "title": "SQL database", "status": "Done"},
    {"phase": 3, "title": "Trading calculations", "status": "Done"},
    {"phase": 4, "title": "Flask backend & CRUD", "status": "Next"},
    {"phase": 5, "title": "Frontend pages", "status": "Planned"},
    {"phase": 6, "title": "Analytics dashboard", "status": "Planned"},
]


@main_bp.route("/")
def index():
    """Home page."""
    return render_template(
        "index.html",
        roadmap=ROADMAP,
        environment=current_app.config["CONFIG_NAME"],
    )


@main_bp.route("/health")
def health():
    """Machine-readable health check.

    Used later by AWS load balancers and monitoring to confirm the app is
    alive. Returns JSON only - never secrets or internal details.
    """
    return jsonify(
        status="ok",
        app=current_app.config["APP_NAME"],
        version=current_app.config["APP_VERSION"],
    )
```

---

## `tests/factories.py` (NEW)

**Purpose:** `make_trade()`, `r_trade()`, `dated()`: builders for hand-checkable synthetic trades.

```python
"""Builders for synthetic trade dicts used by the analytics tests.

make_trade() returns a dict shaped like a row from list_trades(). The
default is a CLOSED LONG with simple numbers so expected results are easy
to work out by hand:

    entry 100, stop 90, exit 120, size 1, multiplier 1, fees 0
    risk = (100 - 90) x 1 x 1 = 10      net = (120 - 100) x 1 x 1 - 0 = 20
    realised R = 20 / 10 = 2
"""

from decimal import Decimal

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
```

---

## `tests/test_trade_math.py` (NEW)

**Purpose:** Unit tests for every per-trade formula and every invalid-input path.

```python
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
    assert initial_risk("LONG", D("100"), D("100"), D("1"), D("1")) is None   # zero risk
    assert initial_risk("LONG", D("100"), D("105"), D("1"), D("1")) is None   # wrong side
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
    assert planned_rr("LONG", D("100"), D("90"), D("95")) is None    # target on wrong side
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
    result = evaluate_trade(make_trade(direction="SHORT", stop_loss=D("110"),
                                       exit_price=D("110"), fees=D("0.5")))
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
    result = evaluate_trade(make_trade(entry_price=D("0.1"), stop_loss=D("0.05"),
                                       exit_price=D("0.3"), position_size=D("3")))
    assert result.gross_pnl == D("0.6")   # (0.3 - 0.1) x 3, exactly
    assert result.realised_r == D("4")    # 0.6 / (0.05 x 3)


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
```

---

## `tests/test_metrics.py` (NEW)

**Purpose:** Unit tests for every aggregate metric with hand-worked examples and edge cases.

```python
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
    assert profit_factor([D("2"), D("1")]) is None          # no losses: undefined
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
    s = summarise(results(
        r_trade(2, trade_date="2026-01-05T10:00:00Z"),
        r_trade(-1, trade_date="2026-01-06T10:00:00Z"),
        r_trade(1, trade_date="2026-01-07T10:00:00Z"),
        r_trade(0, trade_date="2026-01-08T10:00:00Z"),
    ))
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
    assert s.profit_factor_r == D("3")          # (2 + 1) / 1
    assert s.longest_win_streak == 1
    assert s.longest_loss_streak == 1
    assert s.max_drawdown_r.amount == D("1")    # 2 -> 1
    assert s.best_trade_r.value == D("2")
    assert s.worst_trade_r.value == D("-1")


def test_summary_all_winners():
    s = summarise(results(r_trade(1), r_trade(2), r_trade(3)))
    assert s.win_rate == D("1")
    assert s.loss_rate == D("0")
    assert s.average_loss_r is None
    assert s.profit_factor_r is None            # undefined without losses
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
    no_r = make_trade(stop_loss=D("105"))     # invalid stop; net +20
    s = summarise(results(no_r, r_trade(-1)))
    assert s.trades == 2
    assert s.wins == 1 and s.losses == 1
    assert s.win_rate == D("0.5")
    assert s.r_trades == 1
    assert s.average_r == D("-1")
    assert s.expectancy_r == D("-1")          # only trades with R


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
    usd = make_trade(quote_currency="USD", exit_price=D("130"), fees=D("2"))   # net 28
    gbp = make_trade(quote_currency="GBP", exit_price=D("95"))                  # net -5
    s = summarise(results(usd, gbp))
    assert set(s.money) == {"GBP", "USD"}
    assert s.money["USD"].net_pnl == D("28")
    assert s.money["GBP"].net_pnl == D("-5")
    # R is currency-neutral, so it CAN be combined.
    # USD: net 28 / risk 10 = +2.8R (fees count). GBP: -5 / 10 = -0.5R.
    assert s.total_r == D("2.3")


def test_money_summary_gross_fees_net_and_profit_factor():
    s = summarise(results(
        make_trade(exit_price=D("130"), fees=D("1")),    # gross 30, net 29
        make_trade(exit_price=D("95"), fees=D("1")),     # gross -5, net -6
        make_trade(exit_price=D("90"), fees=D("1")),     # gross -10, net -11
    )).money["USD"]
    assert s.trades == 3
    assert s.gross_pnl == D("15")
    assert s.fees == D("3")
    assert s.net_pnl == D("12")
    assert s.winning_pnl == D("29")
    assert s.losing_pnl == D("-17")
    assert s.profit_factor == ratio(29, 17)      # same 60-digit division as the engine
    assert s.best_trade.value == D("29")
    assert s.worst_trade.value == D("-11")
    assert s.max_drawdown.amount == D("17")       # 29 -> 12
```

---

## `tests/test_report.py` (NEW)

**Purpose:** Sample-data checks against exact fractions, grouping, filters, JSON, display rounding and the CLI.

```python
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

from tests.factories import dated, make_trade, r_trade
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
    1: F(393, 200),        # +1.965
    2: F(-1035, 1000),     # -103.5 / 100
    3: F(0),               # breakeven
    4: F(293, 200),        # +1.465
    5: F(198, 100),        # +1.98
    6: F(-133, 130),       # -1.0230769...
    7: F(1985, 1000),      # +1.985
    8: F(-815, 800),       # -1.01875
    9: F(-5, 150),         # -0.0333...  (fees turned a price gain into a loss)
    10: F(2),              # +2
    11: F(0),              # breakeven
    12: F(-157, 150),      # -1.04666...
    13: F(158, 80),        # +1.975
    16: F(38, 80),         # +0.475
}
CHRONOLOGICAL_IDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]


def close_to(value, expected, places=40):
    """Decimal from the engine vs an exact Fraction, to `places` decimals."""
    return abs(F(value) - F(expected)) < F(1, 10**places)


@pytest.fixture
def sample_report(seeded_conn):
    return build_report(seeded_conn)


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
    assert sum(HAND_R.values()) == F(239871, 31200)       # 7.68817307...
    assert close_to(o.total_r, F(239871, 31200))
    assert close_to(o.average_r, F(239871, 31200) / 14)   # 0.549155...


def test_sample_average_win_and_loss_r(sample_report):
    o = sample_report.overall
    assert close_to(o.average_win_r, F(2369, 1400))        # 11.845 / 7
    assert close_to(o.average_loss_r, F(-129693, 156000))  # -4.15682... / 5


def test_sample_expectancy_matches_average_r(sample_report):
    o = sample_report.overall
    assert close_to(o.expectancy_r, F(239871, 436800))
    assert close_to(o.expectancy_r, o.average_r)


def test_sample_profit_factor_r(sample_report):
    assert close_to(sample_report.overall.profit_factor_r, F(369564, 129693))  # 2.8495...


def test_sample_planned_rr(sample_report):
    o = sample_report.overall
    assert o.planned_rr_trades == 12                       # trades 11 and 16 have no target
    assert close_to(o.average_planned_rr, F(647, 312))     # 2.0737...


def test_sample_streaks(sample_report):
    # Date order: W L BE W W L W L L W BE L W W
    assert sample_report.overall.longest_win_streak == 2
    assert sample_report.overall.longest_loss_streak == 2


def test_sample_max_drawdown_r(sample_report):
    dd = sample_report.overall.max_drawdown_r
    assert close_to(dd.amount, F(101, 96))                 # 1.01875 + 1/30 = 1.05208...
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
    assert usd.max_drawdown.amount == D("243.5")           # 846 -> 602.5
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
        "EURUSD": 3, "GBPUSD": 3, "NAS100": 2, "UK100": 2, "XAUUSD": 4,
    }
    gold = groups["XAUUSD"]
    assert gold.win_rate == D("0.75")
    assert close_to(gold.total_r, HAND_R[5] + HAND_R[6] + HAND_R[13] + HAND_R[16])  # 3.4069...
    assert list(groups["UK100"].money) == ["GBP"]


def test_groups_by_strategy_and_session(sample_report):
    assert {k: g.trades for k, g in sample_report.by_strategy.items()} == {
        "Breakout": 5, "Pullback": 5, "Reversal": 2, "Unspecified": 2,
    }
    assert {k: g.trades for k, g in sample_report.by_session.items()} == {
        "ASIA": 2, "LONDON": 6, "NEW_YORK": 5, "Unspecified": 1,
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
    assert sample_report.by_date["2026-03-20"].trades == 1   # trade 15 that day is open


# ---------- Filters through build_report ----------

def test_report_filtered_by_instrument(seeded_conn):
    gold = get_instrument_by_symbol(seeded_conn, "XAUUSD")
    report = build_report(seeded_conn, instrument_id=gold["id"])
    assert report.overall.trades == 4
    assert list(report.by_instrument) == ["XAUUSD"]


def test_report_filtered_by_date_range(seeded_conn):
    report = build_report(seeded_conn, start="2026-03-02", end="2026-03-06")
    assert [p.trade_id for p in report.cumulative_r] == [1, 2, 3, 4, 5]


def test_report_on_empty_database(conn):
    report = build_report(conn)
    assert report.overall.trades == 0
    assert report.overall.expectancy_r is None
    assert report.cumulative_r == []
    assert report.by_instrument == {}


# ---------- analyse() with awkward input ----------

def test_open_and_incomplete_trades_are_separated():
    trades = [
        make_trade(),
        make_trade(status="OPEN", exit_price=None),
        make_trade(exit_price=None),                 # "closed" but no exit: excluded
        make_trade(stop_loss=D("105")),              # counted, but no R
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
    assert parsed["cumulative_r"][0] == {
        "trade_id": 1, "trade_date": "2026-03-02T08:15:00Z", "symbol": "EURUSD",
        "realised_r": "1.965", "cumulative_r": "1.965",
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
    runner.invoke(args=["seed-db"])
    result = runner.invoke(args=["report"])
    assert result.exit_code == 0
    assert "Expectancy per trade          +0.55R" in result.output
    assert "798.50 USD" in result.output
    assert "never added together" in result.output


def test_cli_report_json_and_filters(runner):
    runner.invoke(args=["seed-db"])
    data = json.loads(runner.invoke(args=["report", "--json"]).output)
    assert data["overall"]["trades"] == 14
    gold = json.loads(runner.invoke(args=["report", "--json", "--instrument", "xauusd"]).output)
    assert gold["overall"]["trades"] == 4


def test_cli_report_errors(runner):
    runner.invoke(args=["seed-db"])
    unknown = runner.invoke(args=["report", "--instrument", "NOPE"])
    assert unknown.exit_code != 0 and "No instrument" in unknown.output
    bad_dates = runner.invoke(args=["report", "--start", "2026-03-10", "--end", "2026-03-01"])
    assert bad_dates.exit_code != 0 and "end" in bad_dates.output
```

---

## `tests/test_properties.py` (NEW)

**Purpose:** Seeded random property tests over 1,000 generated trades.

```python
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
    entry = Decimal(rng.randint(100, 500_000)) / 100          # 1.00 .. 5000.00
    risk = Decimal(rng.randint(1, 5_000)) / 100               # 0.01 .. 50.00
    move = Decimal(rng.randint(-10_000, 15_000)) / 100        # -100 .. +150
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
```
