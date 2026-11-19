"""Prepare one PerformanceReport for the /analytics dashboard.

This module sits between the engine and the page:

  report.py      calculates (Decimal, exact)          <- source of truth
  dashboard.py   arranges the results for display:     <- this file
                   * KPI cards
                   * the cumulative R series with running peak and drawdown
                   * every month in the range, including months with no trades
                   * all seven weekdays in calendar order
                   * instrument / strategy / session comparisons
                   * text descriptions of each chart (for people who cannot
                     see the charts)
                   * a JSON-safe chart payload
  analytics.js   draws the charts from that payload    <- presentation only

The only arithmetic here is the running peak and drawdown of a series the
engine has already calculated, done in Decimal under the engine's 60-digit
context. Everything else is selection, ordering and formatting.

Exact values and chart numbers
------------------------------
Chart libraries need JavaScript numbers (64-bit floats). Floats cannot hold
values like 0.1 exactly, so the payload never contains them: every value is
an exact decimal STRING (e.g. "7.688173076923..."), next to the text people
read (e.g. "+7.69R"). analytics.js converts the string to a number at the
very last moment, only to position a bar or a point. Tables and tooltips
show the exact-then-rounded text produced here, so the chart can never
become the source of a figure.
"""

from dataclasses import dataclass, field
from datetime import UTC, date
from decimal import Decimal, localcontext

from ..report_filters import ReportFilters
from ..timestamps import parse_stored
from ..validation import SESSIONS
from .formatting import (
    NOT_AVAILABLE,
    decimal_to_text,
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
)
from .metrics import Summary
from .report import UNSPECIFIED, WEEKDAYS, CurvePoint, PerformanceReport, build_report
from .trade_math import EXACT

ZERO = Decimal(0)

# The trade-by-trade table shows at most this many (most recent) rows. A
# 10,000-row HTML table is unusable and made the page ~9.5 MB in the
# benchmark; the charts and grouped tables still cover every trade.
SERIES_TABLE_LIMIT = 200

# Groups with fewer closed trades than this are flagged "small sample".
# A handful of trades says very little about future results.
SMALL_SAMPLE = 10

# Sessions are shown in the order of the trading day, not alphabetically,
# with readable labels.
SESSION_ORDER = (*SESSIONS, UNSPECIFIED)
SESSION_LABELS = {
    "ASIA": "Asia",
    "LONDON": "London",
    "NEW_YORK": "New York",
    "OTHER": "Other",
    UNSPECIFIED: UNSPECIFIED,
}

PLURALS = {"strategy": "strategies"}


# ---------- Data structures ----------


@dataclass(frozen=True)
class SeriesPoint:
    """One closed trade on the cumulative R and drawdown charts."""

    trade_id: int
    symbol: str
    realised_at: str  # 'YYYY-MM-DDTHH:MM:SSZ' (UTC)
    x: int  # realised_at as Unix milliseconds (chart axis)
    close_time_known: bool  # False: legacy trade placed at its entry time
    realised_r: Decimal
    cumulative_r: Decimal
    peak_r: Decimal  # highest cumulative R so far, never below 0
    drawdown_r: Decimal  # peak_r - cumulative_r, never below 0


@dataclass(frozen=True)
class PeriodRow:
    """A month or a weekday. summary is None when nothing closed in it, which
    is different from closing trades that netted 0R."""

    key: str  # '2026-03' or 'Monday'
    label: str  # 'Mar 2026' or 'Monday'
    summary: Summary | None


@dataclass(frozen=True)
class GroupRow:
    """An instrument, strategy or session. key is the stored value (e.g.
    NEW_YORK); label is what people read (e.g. New York)."""

    key: str
    label: str
    summary: Summary

    @property
    def small_sample(self) -> bool:
        return self.summary.trades < SMALL_SAMPLE


@dataclass(frozen=True)
class Kpi:
    """One summary card. value is display text; tone drives colour, but the
    text (sign, 'n/a', words) always carries the meaning on its own."""

    key: str
    label: str
    value: str
    note: str
    tone: str = "neutral"  # 'positive', 'negative' or 'neutral'


@dataclass(frozen=True)
class Dashboard:
    filters: ReportFilters
    report: PerformanceReport
    kpis: list[Kpi]
    series: list[SeriesPoint]
    months: list[PeriodRow]
    weekdays: list[PeriodRow]
    instruments: list[GroupRow]
    strategies: list[GroupRow]
    sessions: list[GroupRow]
    descriptions: dict[str, str] = field(default_factory=dict)

    @property
    def has_results(self) -> bool:
        return self.report.overall.trades > 0

    @property
    def series_table(self) -> list[SeriesPoint]:
        """The most recent SERIES_TABLE_LIMIT points, for the HTML table."""
        return self.series[-SERIES_TABLE_LIMIT:]


# ---------- Small pure functions ----------


def to_epoch_ms(timestamp: str) -> int:
    """'2026-03-02T11:40:00Z' -> milliseconds since 1970-01-01 UTC (an int,
    exact, which a chart can use as a time axis)."""
    moment = parse_stored(timestamp).replace(tzinfo=UTC)
    return int(moment.timestamp()) * 1000


def drawdown_series(curve: list[CurvePoint]) -> list[SeriesPoint]:
    """Add the running peak and drawdown to the engine's cumulative R curve.

    The peak starts at 0 (before any trade the running total is 0R), so a
    first trade that loses 1R is already a 1R drawdown. The largest
    drawdown_r in the result equals the engine's max_drawdown_r; a test
    checks that the two independent paths agree.
    """
    points, peak = [], ZERO
    with localcontext(EXACT):
        for point in curve:
            peak = max(peak, point.cumulative_r)
            points.append(
                SeriesPoint(
                    trade_id=point.trade_id,
                    symbol=point.symbol,
                    realised_at=point.realised_at,
                    x=to_epoch_ms(point.realised_at),
                    close_time_known=point.close_time_known,
                    realised_r=point.realised_r,
                    cumulative_r=point.cumulative_r,
                    peak_r=peak,
                    drawdown_r=peak - point.cumulative_r,
                )
            )
    return points


def month_keys(first: str, last: str) -> list[str]:
    """Every 'YYYY-MM' from first to last inclusive, across year ends."""
    year, month = int(first[:4]), int(first[5:7])
    end = (int(last[:4]), int(last[5:7]))
    keys = []
    while (year, month) <= end:
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return keys


def month_label(key: str) -> str:
    return date(int(key[:4]), int(key[5:7]), 1).strftime("%b %Y")


def month_rows(by_month: dict[str, Summary]) -> list[PeriodRow]:
    """One row per calendar month between the first and last month with a
    closed trade. Gaps are kept, with summary=None, so a quiet month is
    visible as a gap rather than silently disappearing (or, worse, being
    drawn as a 0R month)."""
    if not by_month:
        return []
    keys = sorted(by_month)
    return [PeriodRow(k, month_label(k), by_month.get(k)) for k in month_keys(keys[0], keys[-1])]


def weekday_rows(by_weekday: dict[str, Summary]) -> list[PeriodRow]:
    """All seven days, Monday first. Days with no closed trades keep a row
    with summary=None."""
    return [PeriodRow(day, day, by_weekday.get(day)) for day in WEEKDAYS]


def group_rows(groups: dict[str, Summary], order=None, labels=None) -> list[GroupRow]:
    """Groups ordered by `order` when given (sessions), otherwise by total R,
    best first, with the key breaking ties so the order is stable."""
    labels = labels or {}
    rows = [GroupRow(key, labels.get(key, key), summary) for key, summary in groups.items()]
    if order is not None:
        position = {key: index for index, key in enumerate(order)}
        return sorted(rows, key=lambda row: position.get(row.key, len(position)))
    return sorted(rows, key=lambda row: (-row.summary.total_r, row.key))


def tone(value: Decimal | None) -> str:
    if value is None or value == 0:
        return "neutral"
    return "positive" if value > 0 else "negative"


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {PLURALS.get(word, word + 's')}"


def short_time(timestamp: str) -> str:
    return parse_stored(timestamp).strftime("%d %b %Y, %H:%M UTC")


# ---------- KPI cards ----------


def build_kpis(report: PerformanceReport) -> list[Kpi]:
    s = report.overall
    none = s.trades == 0
    unavailable = "No closed trades match the filters"

    def count_card(key, label, count, rate):
        note = unavailable if none else f"{format_rate(rate)} of closed trades"
        return Kpi(key, label, NOT_AVAILABLE if none else str(count), note)

    return [
        Kpi(
            "trades",
            "Closed trades",
            str(s.trades),
            f"{plural(report.open_trades, 'open trade')} not included"
            if report.open_trades
            else "No open trades",
        ),
        count_card("wins", "Winning trades", s.wins, s.win_rate),
        count_card("losses", "Losing trades", s.losses, s.loss_rate),
        count_card("breakevens", "Breakeven trades", s.breakevens, s.breakeven_rate),
        Kpi(
            "win_rate",
            "Win rate",
            format_rate(s.win_rate),
            unavailable if none else f"{s.wins} of {s.trades} closed trades",
        ),
        Kpi(
            "average_r",
            "Average realised R",
            format_r(s.average_r),
            unavailable
            if s.average_r is None
            else f"Mean over {plural(s.r_trades, 'trade')} with a usable R",
            tone(s.average_r),
        ),
        Kpi(
            "expectancy_r",
            "Expectancy per trade",
            format_r(s.expectancy_r),
            unavailable if s.expectancy_r is None else "Win rate × average win + loss rate × average loss",
            tone(s.expectancy_r),
        ),
        Kpi(
            "profit_factor_r",
            "Profit factor (R)",
            format_ratio(s.profit_factor_r),
            unavailable
            if none
            else ("Needs at least one losing trade" if s.profit_factor_r is None else "Winning R ÷ losing R"),
        ),
        Kpi(
            "total_r",
            "Total realised R",
            NOT_AVAILABLE if none else format_r(s.total_r),
            unavailable if none else "Sum of R-multiples (not account equity)",
            "neutral" if none else tone(s.total_r),
        ),
        Kpi(
            "max_drawdown_r",
            "Maximum drawdown",
            NOT_AVAILABLE if none else format_r_amount(s.max_drawdown_r.amount),
            unavailable if none else "Largest fall from a running peak of cumulative R",
            "negative" if not none and s.max_drawdown_r.amount > 0 else "neutral",
        ),
        Kpi(
            "longest_win_streak",
            "Longest winning streak",
            NOT_AVAILABLE if none else plural(s.longest_win_streak, "trade"),
            unavailable if none else "Consecutive wins, in realised order",
        ),
        Kpi(
            "longest_loss_streak",
            "Longest losing streak",
            NOT_AVAILABLE if none else plural(s.longest_loss_streak, "trade"),
            unavailable if none else "Consecutive losses, in realised order",
        ),
    ]


# ---------- Text descriptions (the charts' text alternatives) ----------


def describe_curve(series: list[SeriesPoint]) -> str:
    if not series:
        return "There are no closed trades with a usable R to plot."
    last = series[-1]
    high = max(series, key=lambda p: (p.cumulative_r, -p.x))
    low = min(series, key=lambda p: (p.cumulative_r, p.x))
    return (
        f"Cumulative realised R across {plural(len(series), 'closed trade')}, "
        f"from {short_time(series[0].realised_at)} to {short_time(last.realised_at)}. "
        f"It ended at {format_r(last.cumulative_r)}. The highest point was {format_r(high.cumulative_r)} "
        f"(after trade #{high.trade_id}) and the lowest was {format_r(low.cumulative_r)} "
        f"(after trade #{low.trade_id})."
    )


def describe_drawdown(series: list[SeriesPoint], report: PerformanceReport) -> str:
    if not series:
        return "There is no cumulative R series, so there is no drawdown to show."
    worst = report.overall.max_drawdown_r
    current = series[-1].drawdown_r
    if worst.amount == 0:
        return "No drawdown: cumulative R never fell below its previous peak."
    start = (
        "the starting point (0R)"
        if worst.peak_trade_id is None
        else f"the peak after trade #{worst.peak_trade_id}"
    )
    return (
        f"Maximum drawdown was {format_r_amount(worst.amount)}, from {start} to the low after trade "
        f"#{worst.trough_trade_id}. Current drawdown: {format_r_amount(current)} below the running peak."
    )


def describe_periods(rows: list[PeriodRow], noun: str) -> str:
    traded = [row for row in rows if row.summary is not None]
    if not traded:
        return f"No {noun} has closed trades."
    best = max(traded, key=lambda row: row.summary.total_r)
    worst = min(traded, key=lambda row: row.summary.total_r)
    empty = [row.label for row in rows if row.summary is None]
    if len(traded) == 1:
        text = (
            f"Only {best.label} has closed trades: {format_r(best.summary.total_r)} from "
            f"{plural(best.summary.trades, 'trade')}."
        )
    else:
        text = (
            f"{plural(len(traded), noun)} with closed trades. Best: {best.label} "
            f"({format_r(best.summary.total_r)} from {plural(best.summary.trades, 'trade')}). "
            f"Worst: {worst.label} ({format_r(worst.summary.total_r)} from "
            f"{plural(worst.summary.trades, 'trade')})."
        )
    if empty:
        text += f" No closed trades: {', '.join(empty)}."
    return text


def describe_groups(rows: list[GroupRow], noun: str) -> str:
    if not rows:
        return f"No {noun} has closed trades."
    best = max(rows, key=lambda row: row.summary.total_r)
    worst = min(rows, key=lambda row: row.summary.total_r)
    small = sum(row.small_sample for row in rows)
    if len(rows) == 1:
        text = (
            f"Only one {noun}, {best.label}: {format_r(best.summary.total_r)} from "
            f"{plural(best.summary.trades, 'trade')}."
        )
    else:
        text = (
            f"{plural(len(rows), noun)} compared by total realised R. Best: {best.label} "
            f"({format_r(best.summary.total_r)}, {plural(best.summary.trades, 'trade')}). "
            f"Worst: {worst.label} ({format_r(worst.summary.total_r)}, "
            f"{plural(worst.summary.trades, 'trade')})."
        )
    if small:
        text += (
            f" {plural(small, noun)} {'has' if small == 1 else 'have'} fewer than "
            f"{SMALL_SAMPLE} trades: treat {'it' if small == 1 else 'them'} as a small sample."
        )
    return text


# ---------- Assembly ----------


def make_dashboard(report: PerformanceReport, filters: ReportFilters) -> Dashboard:
    """Pure: everything the page needs from one report."""
    series = drawdown_series(report.cumulative_r)
    months = month_rows(report.by_month)
    weekdays = weekday_rows(report.by_weekday)
    instruments = group_rows(report.by_instrument)
    strategies = group_rows(report.by_strategy)
    sessions = group_rows(report.by_session, order=SESSION_ORDER, labels=SESSION_LABELS)
    return Dashboard(
        filters=filters,
        report=report,
        kpis=build_kpis(report),
        series=series,
        months=months,
        weekdays=weekdays,
        instruments=instruments,
        strategies=strategies,
        sessions=sessions,
        descriptions={
            "curve": describe_curve(series),
            "drawdown": describe_drawdown(series, report),
            "monthly": describe_periods(months, "month"),
            "weekday": describe_periods(weekdays, "weekday"),
            "instruments": describe_groups(instruments, "instrument"),
            "strategies": describe_groups(strategies, "strategy"),
            "sessions": describe_groups(sessions, "session"),
        },
    )


def build_dashboard(conn, filters: ReportFilters, *, user_id: int) -> Dashboard:
    """One database query and one report per page view; every card, chart
    and table below is cut from the same report. Only the signed-in user's
    trades are loaded (Phase 8)."""
    return make_dashboard(build_report(conn, user_id=user_id, **filters.as_kwargs()), filters)


# ---------- Chart payload (JSON-safe) ----------


def exact(value: Decimal | None) -> str | None:
    """Exact decimal text for the chart, or None (JSON null) when absent."""
    return None if value is None else decimal_to_text(value)


def _summary_fields(summary: Summary | None) -> dict[str, object]:
    if summary is None:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "total_r": None,
            "total_r_text": "No closed trades",
            "win_rate_text": NOT_AVAILABLE,
            "average_r_text": NOT_AVAILABLE,
            "expectancy_text": NOT_AVAILABLE,
        }
    return {
        "trades": summary.trades,
        "wins": summary.wins,
        "losses": summary.losses,
        "total_r": exact(summary.total_r),
        "total_r_text": format_r(summary.total_r),
        "win_rate_text": format_rate(summary.win_rate),
        "average_r_text": format_r(summary.average_r),
        "expectancy_text": format_r(summary.expectancy_r),
    }


def chart_payload(dashboard: Dashboard) -> dict[str, object]:
    """Everything analytics.js needs, and nothing else: no notes, no prices,
    no identifiers beyond trade numbers. Exact values are strings; the
    *_text values are what people read."""
    curve = []
    if dashboard.series:
        first = dashboard.series[0]
        curve.append(
            {
                "start": True,
                "x": first.x,
                "cumulative_r": "0",
                "cumulative_text": "0.00R",
                "underwater_r": "0",
                "drawdown_text": "0.00R",
            }
        )
    for point in dashboard.series:
        with localcontext(EXACT):
            underwater = -point.drawdown_r
        curve.append(
            {
                "start": False,
                "trade_id": point.trade_id,
                "symbol": point.symbol,
                "x": point.x,
                "when": short_time(point.realised_at),
                "close_time_known": point.close_time_known,
                "realised_r": exact(point.realised_r),
                "realised_text": format_r(point.realised_r),
                "cumulative_r": exact(point.cumulative_r),
                "cumulative_text": format_r(point.cumulative_r),
                "underwater_r": exact(underwater),
                "drawdown_text": format_r_amount(point.drawdown_r),
            }
        )

    def periods(rows):
        return [{"key": row.key, "label": row.label, **_summary_fields(row.summary)} for row in rows]

    def groups(rows):
        return [
            {"label": row.label, "small_sample": row.small_sample, **_summary_fields(row.summary)}
            for row in rows
        ]

    worst = dashboard.report.overall.max_drawdown_r
    return {
        "curve": curve,
        "max_drawdown": {
            "amount": exact(worst.amount),
            "text": format_r_amount(worst.amount),
            "peak_trade_id": worst.peak_trade_id,
            "trough_trade_id": worst.trough_trade_id,
        },
        "monthly": periods(dashboard.months),
        "weekday": periods(dashboard.weekdays),
        "instruments": groups(dashboard.instruments),
        "strategies": groups(dashboard.strategies),
        "sessions": groups(dashboard.sessions),
    }


def money_rows(report: PerformanceReport):
    """(currency, net P&L text, tone, trades) per quote currency. Never summed
    across currencies: there is no exchange-rate data to do that honestly."""
    return [
        (currency, format_money(m.net_pnl, currency), tone(m.net_pnl), m.trades)
        for currency, m in report.overall.money.items()
    ]
