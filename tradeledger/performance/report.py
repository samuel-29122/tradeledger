"""Build a complete PerformanceReport.

analyse(trades)          pure: list of trade dicts in, report out
build_report(conn, ...)  loads trades from the database, then calls analyse()

Grouping keys (all from realised_at, which is UTC)
  by_weekday   'Monday' ... 'Sunday', in calendar order
  by_date      'YYYY-MM-DD'
  by_month     'YYYY-MM'

realised_at is the exit time (closed_at). For legacy trades recorded before
Phase 4, whose exit time was never captured, it falls back to the entry
time. Those trades are listed in unknown_close_time_trade_ids so the
fallback is always visible, never silent.

Date filters (start/end) apply to the same realised date, so "March" means
"results realised in March".
"""

from dataclasses import dataclass
from decimal import Decimal

from ..repositories.trades import list_trades
from ..timestamps import parse_stored
from .metrics import Summary, chronological, cumulative, summarise
from .trade_math import IncompleteTradeError, TradeResult, evaluate_trade

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
UNSPECIFIED = "Unspecified"


@dataclass(frozen=True)
class ExcludedTrade:
    trade_id: int | None
    reason: str


@dataclass(frozen=True)
class CurvePoint:
    """One point on the cumulative-R chart. This is NOT account equity."""

    trade_id: int
    trade_date: str
    realised_at: str
    close_time_known: bool
    symbol: str
    realised_r: Decimal
    cumulative_r: Decimal


@dataclass(frozen=True)
class PerformanceReport:
    overall: Summary
    open_trades: int
    excluded: list[ExcludedTrade]
    r_unavailable: list[ExcludedTrade]  # closed trades counted, but with no R
    unknown_close_time_trade_ids: list[int]  # legacy: ordered by entry time
    by_instrument: dict[str, Summary]
    by_strategy: dict[str, Summary]
    by_session: dict[str, Summary]
    by_weekday: dict[str, Summary]
    by_date: dict[str, Summary]
    by_month: dict[str, Summary]
    cumulative_r: list[CurvePoint]


def _weekday(result):
    moment = parse_stored(result.realised_at)
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
    groups: dict[str, list[TradeResult]] = {}
    for result in results:
        groups.setdefault(key(result), []).append(result)
    keys = [k for k in order if k in groups] if order else sorted(groups)
    return {k: summarise(groups[k]) for k in keys}


def cumulative_r_curve(results):
    """Chart-ready running total of realised R, in date order."""
    rows = [r for r in chronological(results) if r.realised_r is not None]
    totals = cumulative([r.realised_r for r in rows])
    return [
        CurvePoint(r.trade_id, r.trade_date, r.realised_at, r.close_time_known, r.symbol, r.realised_r, total)
        for r, total in zip(rows, totals, strict=True)
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
        unknown_close_time_trade_ids=[r.trade_id for r in chronological(results) if not r.close_time_known],
        by_instrument=group_summaries(results, lambda r: r.symbol),
        by_strategy=group_summaries(results, lambda r: r.strategy or UNSPECIFIED),
        by_session=group_summaries(results, lambda r: r.session or UNSPECIFIED),
        by_weekday=group_summaries(results, _weekday, order=WEEKDAYS),
        by_date=group_summaries(results, lambda r: r.realised_at[:10]),
        by_month=group_summaries(results, lambda r: r.realised_at[:7]),
        cumulative_r=cumulative_r_curve(results),
    )


def build_report(
    conn, *, user_id, instrument_id=None, start=None, end=None, strategy=None, session=None
) -> PerformanceReport:
    """Load ONE user's trades with the repository filters and analyse them.

    user_id is required (Phase 8): a report can never mix two users' trades.

    start/end are inclusive UTC dates applied to the realised date.
    strategy and session are exact matches (Phase 6). Every filter is a
    parameterised SQL condition in the repository, so filtering happens in
    the database and only matching trades are loaded.
    """
    return analyse(
        list_trades(
            conn,
            user_id=user_id,
            instrument_id=instrument_id,
            start=start,
            end=end,
            strategy=strategy,
            session=session,
            date_field="realised",
        )
    )
