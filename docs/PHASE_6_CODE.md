# Phase 6 — Complete Code

Every source file added or changed in Phase 6, in full, generated from the project files.

**DELETED:** `tradeledger/templates/analytics.html` (the Phase 5 preview, replaced by `templates/analytics/dashboard.html`).

**Regenerated, not hand-written:** `docs/openapi.json` (run `flask --app tradeledger openapi`), screenshots in `docs/screenshots/`. Documentation (`README.md`, `docs/PHASE_6_GUIDE.md`, `docs/PHASE_6_QA_REPORT.md`) is delivered separately.

## Contents

1. `tradeledger/report_filters.py` (NEW)
2. `tradeledger/performance/dashboard.py` (NEW)
3. `tradeledger/web/analytics.py` (NEW)
4. `tradeledger/templates/analytics/_macros.html` (NEW)
5. `tradeledger/templates/analytics/dashboard.html` (NEW)
6. `tradeledger/static/css/analytics.css` (NEW)
7. `tradeledger/static/js/analytics.js` (NEW)
8. `tradeledger/static/vendor/chartjs/README.md` (NEW)
9. `scripts/benchmark_dashboard.py` (NEW)
10. `tests/test_report_filters.py` (NEW)
11. `tests/test_dashboard_data.py` (NEW)
12. `tests/test_web_analytics.py` (NEW)
13. `tests/test_javascript.py` (NEW)
14. `tests/js/analytics.test.js` (NEW)
15. `docs/THIRD_PARTY_NOTICES.md` (NEW)
16. `tradeledger/config.py` (MODIFIED)
17. `tradeledger/__init__.py` (MODIFIED)
18. `tradeledger/repositories/trades.py` (MODIFIED)
19. `tradeledger/performance/report.py` (MODIFIED)
20. `tradeledger/api/analytics.py` (MODIFIED)
21. `tradeledger/api/openapi.py` (MODIFIED)
22. `tradeledger/web/__init__.py` (MODIFIED)
23. `tradeledger/web/views.py` (MODIFIED)
24. `tradeledger/templates/base.html` (MODIFIED)
25. `tradeledger/templates/about.html` (MODIFIED)
26. `tradeledger/static/css/style.css` (MODIFIED)
27. `.env.example` (MODIFIED)
28. `tests/test_browser.py` (MODIFIED)
29. `tests/test_web_pages.py` (MODIFIED)
30. `tests/test_api_analytics.py` (MODIFIED)
31. `scripts/qa_dashboard_agreement.py` (NEW, added in the final QA review)


## 1. `tradeledger/report_filters.py` (NEW)

```python
"""Filters for performance reports, shared by the dashboard and the API.

One definition of what each filter means and how it is validated, so the
website (/analytics) and the JSON API (/api/v1/analytics/...) can never
disagree about which trades a report covers.

  instrument_id  only this instrument (must exist: an unknown id would
                 otherwise produce an empty report that looks like "no
                 trades", which is misleading)
  strategy       exact strategy label, as stored on trades
  session        ASIA, LONDON, NEW_YORK or OTHER (case-insensitive input)
  start, end     inclusive UTC dates (YYYY-MM-DD) on the REALISED date:
                 closed_at, or trade_date for legacy trades with no
                 recorded exit time

parse_report_filters() never raises for bad input. It returns every
problem at once as {field: message}, so a form can show them all and the
API can turn them into one 400 response.
"""

from dataclasses import asdict, dataclass
from typing import Dict, Mapping, Optional, Tuple

from .repositories.instruments import get_instrument
from .validation import SESSIONS, parse_date

FILTER_NAMES = ("instrument_id", "strategy", "session", "start", "end")
STRATEGY_MAX_LENGTH = 50          # the same limit validation.py applies to trades


@dataclass(frozen=True)
class ReportFilters:
    instrument_id: Optional[int] = None
    strategy: Optional[str] = None
    session: Optional[str] = None
    start: Optional[str] = None       # 'YYYY-MM-DD'
    end: Optional[str] = None         # 'YYYY-MM-DD'

    def as_kwargs(self) -> Dict[str, object]:
        """Keyword arguments for build_report() and the repository."""
        return asdict(self)

    def active(self) -> Dict[str, object]:
        """Only the filters that are set, e.g. for building URLs."""
        return {name: value for name, value in asdict(self).items() if value is not None}

    @property
    def is_empty(self) -> bool:
        return not self.active()


def parse_report_filters(args: Mapping[str, Optional[str]], conn, *,
                         blank_means_unset: bool) -> Tuple[ReportFilters, Dict[str, str]]:
    """Validate raw query-string values.

    args               name -> raw text (missing names are simply unset)
    conn               database connection, to check the instrument exists
    blank_means_unset  True for HTML forms, where "All" submits an empty
                       value. False for the API, where an empty value is
                       almost certainly a client bug and is rejected.

    Returns (filters, errors). When errors is not empty, filters holds only
    the values that were valid and must not be used to build a report.
    """
    values: Dict[str, object] = {}
    errors: Dict[str, str] = {}

    def raw(name):
        value = args.get(name)
        if value is None:
            return None
        if blank_means_unset and value.strip() == "":
            return None
        return value

    instrument_text = raw("instrument_id")
    if instrument_text is not None:
        text = instrument_text.strip()
        if not text.isdigit() or int(text) < 1:
            errors["instrument_id"] = "must be a whole number of 1 or more"
        elif get_instrument(conn, int(text)) is None:
            errors["instrument_id"] = "does not match an existing instrument"
        else:
            values["instrument_id"] = int(text)

    strategy = raw("strategy")
    if strategy is not None:
        strategy = strategy.strip()
        if not strategy:
            errors["strategy"] = "must not be empty"
        elif len(strategy) > STRATEGY_MAX_LENGTH:
            errors["strategy"] = f"must be {STRATEGY_MAX_LENGTH} characters or fewer"
        else:
            values["strategy"] = strategy

    session = raw("session")
    if session is not None:
        session = session.strip().upper()
        if session not in SESSIONS:
            errors["session"] = f"must be one of: {', '.join(SESSIONS)}"
        else:
            values["session"] = session

    for name in ("start", "end"):
        text = raw(name)
        if text is None:
            continue
        try:
            values[name] = parse_date(text).isoformat()
        except ValueError as exc:
            errors[name] = str(exc)

    if "start" in values and "end" in values and values["start"] > values["end"]:
        errors["end"] = "must be on or after the start date"

    return ReportFilters(**values), errors
```


## 2. `tradeledger/performance/dashboard.py` (NEW)

```python
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
from datetime import date, datetime, timezone
from decimal import Decimal, localcontext
from typing import Dict, List, Optional

from ..report_filters import ReportFilters
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
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

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
SESSION_LABELS = {"ASIA": "Asia", "LONDON": "London", "NEW_YORK": "New York", "OTHER": "Other",
                  UNSPECIFIED: UNSPECIFIED}

PLURALS = {"strategy": "strategies"}


# ---------- Data structures ----------

@dataclass(frozen=True)
class SeriesPoint:
    """One closed trade on the cumulative R and drawdown charts."""

    trade_id: int
    symbol: str
    realised_at: str              # 'YYYY-MM-DDTHH:MM:SSZ' (UTC)
    x: int                        # realised_at as Unix milliseconds (chart axis)
    close_time_known: bool        # False: legacy trade placed at its entry time
    realised_r: Decimal
    cumulative_r: Decimal
    peak_r: Decimal               # highest cumulative R so far, never below 0
    drawdown_r: Decimal           # peak_r - cumulative_r, never below 0


@dataclass(frozen=True)
class PeriodRow:
    """A month or a weekday. summary is None when nothing closed in it, which
    is different from closing trades that netted 0R."""

    key: str                      # '2026-03' or 'Monday'
    label: str                    # 'Mar 2026' or 'Monday'
    summary: Optional[Summary]


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
    tone: str = "neutral"         # 'positive', 'negative' or 'neutral'


@dataclass(frozen=True)
class Dashboard:
    filters: ReportFilters
    report: PerformanceReport
    kpis: List[Kpi]
    series: List[SeriesPoint]
    months: List[PeriodRow]
    weekdays: List[PeriodRow]
    instruments: List[GroupRow]
    strategies: List[GroupRow]
    sessions: List[GroupRow]
    descriptions: Dict[str, str] = field(default_factory=dict)

    @property
    def has_results(self) -> bool:
        return self.report.overall.trades > 0

    @property
    def series_table(self) -> List[SeriesPoint]:
        """The most recent SERIES_TABLE_LIMIT points, for the HTML table."""
        return self.series[-SERIES_TABLE_LIMIT:]


# ---------- Small pure functions ----------

def to_epoch_ms(timestamp: str) -> int:
    """'2026-03-02T11:40:00Z' -> milliseconds since 1970-01-01 UTC (an int,
    exact, which a chart can use as a time axis)."""
    moment = datetime.strptime(timestamp, TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    return int(moment.timestamp()) * 1000


def drawdown_series(curve: List[CurvePoint]) -> List[SeriesPoint]:
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
            points.append(SeriesPoint(
                trade_id=point.trade_id,
                symbol=point.symbol,
                realised_at=point.realised_at,
                x=to_epoch_ms(point.realised_at),
                close_time_known=point.close_time_known,
                realised_r=point.realised_r,
                cumulative_r=point.cumulative_r,
                peak_r=peak,
                drawdown_r=peak - point.cumulative_r,
            ))
    return points


def month_keys(first: str, last: str) -> List[str]:
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


def month_rows(by_month: Dict[str, Summary]) -> List[PeriodRow]:
    """One row per calendar month between the first and last month with a
    closed trade. Gaps are kept, with summary=None, so a quiet month is
    visible as a gap rather than silently disappearing (or, worse, being
    drawn as a 0R month)."""
    if not by_month:
        return []
    keys = sorted(by_month)
    return [PeriodRow(k, month_label(k), by_month.get(k)) for k in month_keys(keys[0], keys[-1])]


def weekday_rows(by_weekday: Dict[str, Summary]) -> List[PeriodRow]:
    """All seven days, Monday first. Days with no closed trades keep a row
    with summary=None."""
    return [PeriodRow(day, day, by_weekday.get(day)) for day in WEEKDAYS]


def group_rows(groups: Dict[str, Summary], order=None, labels=None) -> List[GroupRow]:
    """Groups ordered by `order` when given (sessions), otherwise by total R,
    best first, with the key breaking ties so the order is stable."""
    labels = labels or {}
    rows = [GroupRow(key, labels.get(key, key), summary) for key, summary in groups.items()]
    if order is not None:
        position = {key: index for index, key in enumerate(order)}
        return sorted(rows, key=lambda row: position.get(row.key, len(position)))
    return sorted(rows, key=lambda row: (-row.summary.total_r, row.key))


def tone(value: Optional[Decimal]) -> str:
    if value is None or value == 0:
        return "neutral"
    return "positive" if value > 0 else "negative"


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {PLURALS.get(word, word + 's')}"


def short_time(timestamp: str) -> str:
    return datetime.strptime(timestamp, TIMESTAMP_FORMAT).strftime("%d %b %Y, %H:%M UTC")


# ---------- KPI cards ----------

def build_kpis(report: PerformanceReport) -> List[Kpi]:
    s = report.overall
    none = s.trades == 0
    unavailable = "No closed trades match the filters"

    def count_card(key, label, count, rate):
        note = unavailable if none else f"{format_rate(rate)} of closed trades"
        return Kpi(key, label, NOT_AVAILABLE if none else str(count), note)

    return [
        Kpi("trades", "Closed trades", str(s.trades),
            f"{plural(report.open_trades, 'open trade')} not included" if report.open_trades
            else "No open trades"),
        count_card("wins", "Winning trades", s.wins, s.win_rate),
        count_card("losses", "Losing trades", s.losses, s.loss_rate),
        count_card("breakevens", "Breakeven trades", s.breakevens, s.breakeven_rate),
        Kpi("win_rate", "Win rate", format_rate(s.win_rate),
            unavailable if none else f"{s.wins} of {s.trades} closed trades"),
        Kpi("average_r", "Average realised R", format_r(s.average_r),
            unavailable if s.average_r is None else f"Mean over {plural(s.r_trades, 'trade')} with a usable R",
            tone(s.average_r)),
        Kpi("expectancy_r", "Expectancy per trade", format_r(s.expectancy_r),
            unavailable if s.expectancy_r is None else "Win rate × average win + loss rate × average loss",
            tone(s.expectancy_r)),
        Kpi("profit_factor_r", "Profit factor (R)", format_ratio(s.profit_factor_r),
            unavailable if none else ("Needs at least one losing trade" if s.profit_factor_r is None
                                      else "Winning R ÷ losing R")),
        Kpi("total_r", "Total realised R", NOT_AVAILABLE if none else format_r(s.total_r),
            unavailable if none else "Sum of R-multiples (not account equity)",
            "neutral" if none else tone(s.total_r)),
        Kpi("max_drawdown_r", "Maximum drawdown", NOT_AVAILABLE if none else format_r_amount(s.max_drawdown_r.amount),
            unavailable if none else "Largest fall from a running peak of cumulative R",
            "negative" if not none and s.max_drawdown_r.amount > 0 else "neutral"),
        Kpi("longest_win_streak", "Longest winning streak",
            NOT_AVAILABLE if none else plural(s.longest_win_streak, "trade"),
            unavailable if none else "Consecutive wins, in realised order"),
        Kpi("longest_loss_streak", "Longest losing streak",
            NOT_AVAILABLE if none else plural(s.longest_loss_streak, "trade"),
            unavailable if none else "Consecutive losses, in realised order"),
    ]


# ---------- Text descriptions (the charts' text alternatives) ----------

def describe_curve(series: List[SeriesPoint]) -> str:
    if not series:
        return "There are no closed trades with a usable R to plot."
    last = series[-1]
    high = max(series, key=lambda p: (p.cumulative_r, -p.x))
    low = min(series, key=lambda p: (p.cumulative_r, p.x))
    return (f"Cumulative realised R across {plural(len(series), 'closed trade')}, "
            f"from {short_time(series[0].realised_at)} to {short_time(last.realised_at)}. "
            f"It ended at {format_r(last.cumulative_r)}. The highest point was {format_r(high.cumulative_r)} "
            f"(after trade #{high.trade_id}) and the lowest was {format_r(low.cumulative_r)} "
            f"(after trade #{low.trade_id}).")


def describe_drawdown(series: List[SeriesPoint], report: PerformanceReport) -> str:
    if not series:
        return "There is no cumulative R series, so there is no drawdown to show."
    worst = report.overall.max_drawdown_r
    current = series[-1].drawdown_r
    if worst.amount == 0:
        return "No drawdown: cumulative R never fell below its previous peak."
    start = "the starting point (0R)" if worst.peak_trade_id is None else f"the peak after trade #{worst.peak_trade_id}"
    return (f"Maximum drawdown was {format_r_amount(worst.amount)}, from {start} to the low after trade "
            f"#{worst.trough_trade_id}. Current drawdown: {format_r_amount(current)} below the running peak.")


def describe_periods(rows: List[PeriodRow], noun: str) -> str:
    traded = [row for row in rows if row.summary is not None]
    if not traded:
        return f"No {noun} has closed trades."
    best = max(traded, key=lambda row: row.summary.total_r)
    worst = min(traded, key=lambda row: row.summary.total_r)
    empty = [row.label for row in rows if row.summary is None]
    if len(traded) == 1:
        text = (f"Only {best.label} has closed trades: {format_r(best.summary.total_r)} from "
                f"{plural(best.summary.trades, 'trade')}.")
    else:
        text = (f"{plural(len(traded), noun)} with closed trades. Best: {best.label} "
                f"({format_r(best.summary.total_r)} from {plural(best.summary.trades, 'trade')}). "
                f"Worst: {worst.label} ({format_r(worst.summary.total_r)} from "
                f"{plural(worst.summary.trades, 'trade')}).")
    if empty:
        text += f" No closed trades: {', '.join(empty)}."
    return text


def describe_groups(rows: List[GroupRow], noun: str) -> str:
    if not rows:
        return f"No {noun} has closed trades."
    best = max(rows, key=lambda row: row.summary.total_r)
    worst = min(rows, key=lambda row: row.summary.total_r)
    small = sum(row.small_sample for row in rows)
    if len(rows) == 1:
        text = (f"Only one {noun}, {best.label}: {format_r(best.summary.total_r)} from "
                f"{plural(best.summary.trades, 'trade')}.")
    else:
        text = (f"{plural(len(rows), noun)} compared by total realised R. Best: {best.label} "
                f"({format_r(best.summary.total_r)}, {plural(best.summary.trades, 'trade')}). "
                f"Worst: {worst.label} ({format_r(worst.summary.total_r)}, "
                f"{plural(worst.summary.trades, 'trade')}).")
    if small:
        text += (f" {plural(small, noun)} {'has' if small == 1 else 'have'} fewer than "
                 f"{SMALL_SAMPLE} trades: treat {'it' if small == 1 else 'them'} as a small sample.")
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


def build_dashboard(conn, filters: ReportFilters) -> Dashboard:
    """One database query and one report per page view; every card, chart
    and table below is cut from the same report."""
    return make_dashboard(build_report(conn, **filters.as_kwargs()), filters)


# ---------- Chart payload (JSON-safe) ----------

def exact(value: Optional[Decimal]) -> Optional[str]:
    """Exact decimal text for the chart, or None (JSON null) when absent."""
    return None if value is None else decimal_to_text(value)


def _summary_fields(summary: Optional[Summary]) -> Dict[str, object]:
    if summary is None:
        return {"trades": 0, "wins": 0, "losses": 0, "total_r": None, "total_r_text": "No closed trades",
                "win_rate_text": NOT_AVAILABLE, "average_r_text": NOT_AVAILABLE,
                "expectancy_text": NOT_AVAILABLE}
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


def chart_payload(dashboard: Dashboard) -> Dict[str, object]:
    """Everything analytics.js needs, and nothing else: no notes, no prices,
    no identifiers beyond trade numbers. Exact values are strings; the
    *_text values are what people read."""
    curve = []
    if dashboard.series:
        first = dashboard.series[0]
        curve.append({"start": True, "x": first.x, "cumulative_r": "0", "cumulative_text": "0.00R",
                      "underwater_r": "0", "drawdown_text": "0.00R"})
    for point in dashboard.series:
        with localcontext(EXACT):
            underwater = -point.drawdown_r
        curve.append({
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
        })

    def periods(rows):
        return [{"key": row.key, "label": row.label, **_summary_fields(row.summary)} for row in rows]

    def groups(rows):
        return [{"label": row.label, "small_sample": row.small_sample, **_summary_fields(row.summary)}
                for row in rows]

    worst = dashboard.report.overall.max_drawdown_r
    return {
        "curve": curve,
        "max_drawdown": {"amount": exact(worst.amount), "text": format_r_amount(worst.amount),
                         "peak_trade_id": worst.peak_trade_id, "trough_trade_id": worst.trough_trade_id},
        "monthly": periods(dashboard.months),
        "weekday": periods(dashboard.weekdays),
        "instruments": groups(dashboard.instruments),
        "strategies": groups(dashboard.strategies),
        "sessions": groups(dashboard.sessions),
    }


def money_rows(report: PerformanceReport):
    """(currency, net P&L text, tone, trades) per quote currency. Never summed
    across currencies: there is no exchange-rate data to do that honestly."""
    return [(currency, format_money(m.net_pnl, currency), tone(m.net_pnl), m.trades)
            for currency, m in report.overall.money.items()]
```


## 3. `tradeledger/web/analytics.py` (NEW)

```python
"""The /analytics dashboard (Phase 6).

Request flow:
  1. GET /analytics?instrument_id=1&strategy=Breakout&session=LONDON&start=...&end=...
  2. parse_report_filters() validates the query string (the same function
     the JSON API uses). Any problem -> 400 with the errors shown next to
     the filter fields, and NO report, so a bad filter can never produce a
     misleading page.
  3. build_dashboard() runs ONE query and builds ONE report; every KPI card,
     chart and table on the page is cut from it.
  4. The template renders the numbers as HTML (works without JavaScript)
     plus a JSON copy of the chart data for analytics.js.

Filtering happens in SQL on the server. The browser never receives the
trade history, only the aggregated figures it draws.
"""

from urllib.parse import urlsplit

from flask import current_app, g, render_template, request

from ..db import get_db
from ..performance.dashboard import SESSION_LABELS, SMALL_SAMPLE, build_dashboard, chart_payload, money_rows
from ..report_filters import FILTER_NAMES, parse_report_filters
from ..repositories.instruments import list_instruments
from ..repositories.trades import list_strategies
from ..validation import SESSIONS
from . import main_bp

FILTER_LABELS = {
    "instrument_id": "Instrument", "strategy": "Strategy", "session": "Session",
    "start": "Realised from", "end": "Realised to",
}


def chart_script_sources(url):
    """Content-Security-Policy sources needed to load Chart.js from `url`.

    A same-site path (e.g. /static/vendor/chartjs/chart.umd.min.js) is
    already covered by 'self'. An https URL is allowed as that exact file,
    never as the whole host. Anything else is a configuration mistake.
    """
    if url.startswith("/") and not url.startswith("//"):
        return ()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
        raise ValueError("CHART_JS_URL must be a site path or an https URL without a query string")
    return (url,)


@main_bp.route("/analytics")
def analytics():
    conn = get_db()
    filters, errors = parse_report_filters(request.args, conn, blank_means_unset=True)
    raw = {name: request.args.get(name, "").strip() for name in FILTER_NAMES}

    dashboard = payload = None
    if not errors:
        dashboard = build_dashboard(conn, filters)
        payload = chart_payload(dashboard)
        if dashboard.has_results:      # charts (and so Chart.js) only when there is data
            g.csp_script_sources = chart_script_sources(current_app.config["CHART_JS_URL"])

    strategies = list_strategies(conn)
    if raw["strategy"] and raw["strategy"] not in strategies:
        strategies = [raw["strategy"], *strategies]   # keep a bookmarked choice visible

    return render_template(
        "analytics/dashboard.html",
        dashboard=dashboard,
        payload=payload,
        money=money_rows(dashboard.report) if dashboard else [],
        errors=errors,
        raw=raw,
        labels=FILTER_LABELS,
        active=filters.active() if not errors else {},
        instruments=list_instruments(conn),
        strategies=strategies,
        session_options=[(key, SESSION_LABELS[key]) for key in SESSIONS],
        small_sample=SMALL_SAMPLE,
        chart_js={"url": current_app.config["CHART_JS_URL"],
                  "integrity": current_app.config["CHART_JS_INTEGRITY"],
                  "version": current_app.config["CHART_JS_VERSION"]},
    ), (400 if errors else 200)
```


## 4. `tradeledger/templates/analytics/_macros.html` (NEW)

```html
{# Building blocks for the analytics dashboard.
   Import with {% import "analytics/_macros.html" as dash %} #}
{% import "macros/ui.html" as ui %}

{# One KPI card. The value text always carries the meaning (sign, "n/a",
   words); the tone class only adds colour. #}
{% macro kpi_card(kpi) -%}
    <div class="kpi kpi--{{ kpi.tone }}" data-kpi="{{ kpi.key }}">
        <dt class="kpi__label">{{ kpi.label }}</dt>
        <dd class="kpi__value">{{ kpi.value }}</dd>
        <dd class="kpi__note">{{ kpi.note }}</dd>
    </div>
{%- endmacro %}

{# A chart with its title, explanation, a visible text description and a
   link to the table holding the same numbers. The canvas has role="img"
   and an aria-label, but nobody depends on it: the description and the
   table carry every finding as text. #}
{% macro chart_figure(name, title, explanation, description, table_id, tall=False) -%}
    <figure class="chart-card" aria-labelledby="chart-{{ name }}-title" aria-describedby="chart-{{ name }}-desc">
        <figcaption class="chart-card__head">
            <h3 class="chart-card__title" id="chart-{{ name }}-title">{{ title }}</h3>
            <p class="chart-card__explain">{{ explanation }}</p>
        </figcaption>
        <div class="chart-card__canvas{% if tall %} chart-card__canvas--tall{% endif %}" data-chart-box>
            <canvas id="chart-{{ name }}" data-chart="{{ name }}" role="img" aria-label="{{ title }}. {{ description }}"></canvas>
            <p class="chart-card__fallback" data-chart-fallback hidden></p>
        </div>
        <p class="chart-card__summary" id="chart-{{ name }}-desc">{{ description }}</p>
        <p class="chart-card__table-link"><a class="text-link" href="#{{ table_id }}">Data table<span class="visually-hidden">: {{ title }}</span></a></p>
    </figure>
{%- endmacro %}

{# Shared columns for any table of Summary objects. #}
{% macro summary_cells(s) -%}
    <td data-label="Closed trades" class="num-col">{{ s.trades }}</td>
    <td data-label="Won / lost / breakeven" class="num-col">{{ s.wins }} / {{ s.losses }} / {{ s.breakevens }}</td>
    <td data-label="Win rate" class="num-col">{{ s.win_rate | rate }}</td>
    <td data-label="Average R" class="num-col">{{ ui.signed(s.average_r, s.average_r | r) }}</td>
    <td data-label="Expectancy" class="num-col">{{ ui.signed(s.expectancy_r, s.expectancy_r | r) }}</td>
    <td data-label="Total R" class="num-col">{{ ui.signed(s.total_r, s.total_r | r) }}</td>
    <td data-label="Profit factor (R)" class="num-col">{{ s.profit_factor_r | ratio }}</td>
    <td data-label="Max drawdown" class="num-col">{{ s.max_drawdown_r.amount | r_amount }}</td>
{%- endmacro %}

{% macro summary_head(first) -%}
    <tr>
        <th scope="col">{{ first }}</th>
        <th scope="col" class="num-col">Closed trades</th>
        <th scope="col" class="num-col">Won / lost / breakeven</th>
        <th scope="col" class="num-col">Win rate</th>
        <th scope="col" class="num-col">Average R</th>
        <th scope="col" class="num-col">Expectancy</th>
        <th scope="col" class="num-col">Total R</th>
        <th scope="col" class="num-col">Profit factor (R)</th>
        <th scope="col" class="num-col">Max drawdown</th>
    </tr>
{%- endmacro %}

{# Months or weekdays: rows with no closed trades say so instead of 0. #}
{% macro period_table(table_id, title, first, rows) -%}
    <section class="data-table" aria-labelledby="{{ table_id }}-title">
        <h3 class="data-table__title" id="{{ table_id }}-title">{{ title }}</h3>
        <div class="table-wrap" id="{{ table_id }}" role="region" aria-labelledby="{{ table_id }}-title" tabindex="0">
            <table class="table">
                <caption class="visually-hidden">{{ title }}</caption>
                <thead>{{ summary_head(first) }}</thead>
                <tbody>
                    {% for row in rows %}
                    <tr>
                        <th scope="row" data-label="{{ first }}">{{ row.label }}</th>
                        {% if row.summary %}{{ summary_cells(row.summary) }}
                        {% else %}<td colspan="8" class="muted empty-row">No closed trades</td>{% endif %}
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </section>
{%- endmacro %}

{# Instruments, strategies or sessions, with a small-sample marker. #}
{% macro group_table(table_id, title, first, rows, small_sample) -%}
    <section class="data-table" aria-labelledby="{{ table_id }}-title">
        <h3 class="data-table__title" id="{{ table_id }}-title">{{ title }}</h3>
        <div class="table-wrap" id="{{ table_id }}" role="region" aria-labelledby="{{ table_id }}-title" tabindex="0">
            <table class="table">
                <caption class="visually-hidden">{{ title }}, best total R first</caption>
                <thead>{{ summary_head(first) }}</thead>
                <tbody>
                    {% for row in rows %}
                    <tr>
                        <th scope="row" data-label="{{ first }}">
                            <span>{{ row.label }}{% if row.small_sample %} <span class="sample-tag">Small sample</span>{% endif %}</span>
                        </th>
                        {{ summary_cells(row.summary) }}
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% if rows | selectattr("small_sample") | list %}
        <p class="table-note">"Small sample" marks groups with fewer than {{ small_sample }} closed trades. Their figures can change a lot with one more trade.</p>
        {% endif %}
    </section>
{%- endmacro %}
```


## 5. `tradeledger/templates/analytics/dashboard.html` (NEW)

```html
{% extends "base.html" %}
{% import "macros/ui.html" as ui %}
{% import "analytics/_macros.html" as dash %}

{% block title %}Analytics{% endblock %}
{% block heading %}Analytics{% endblock %}
{% block description %}Realised performance of your closed trades, measured in R. Times are UTC.{% endblock %}
{% block actions %}
    <a class="button button--secondary" href="{{ url_for('main.journal') }}">Trade journal</a>
{% endblock %}
{% block scripts %}
    {% if dashboard and dashboard.has_results %}
    {# Chart.js first, then our code; both "defer", so they run in this order
       after the page is parsed. The Content-Security-Policy of this page
       allows exactly this Chart.js URL and nothing else from outside. #}
    <script src="{{ chart_js.url }}" defer crossorigin="anonymous" referrerpolicy="no-referrer"
            {%- if chart_js.integrity %} integrity="{{ chart_js.integrity }}"{% endif %}></script>
    <script src="{{ url_for('static', filename='js/analytics.js') }}" defer></script>
    {% endif %}
{% endblock %}

{% block content %}
{# ---------- 1. Filters ---------- #}
<form class="card filters dash-filters" method="get" action="{{ url_for('main.analytics') }}" aria-labelledby="filters-title">
    <h2 class="dash-filters__title" id="filters-title">Reporting filters</h2>
    {% if errors %}
    <div class="error-summary" role="alert" tabindex="-1" data-error-summary aria-labelledby="filter-errors-title">
        <h3 class="error-summary__title" id="filter-errors-title">The report was not produced: fix {{ "this filter" if errors | length == 1 else "these filters" }}</h3>
        <ul>
            {% for name, message in errors.items() %}
            <li><a href="#dash-{{ name }}">{{ labels[name] }} {{ message }}</a></li>
            {% endfor %}
        </ul>
    </div>
    {% endif %}
    <div class="filters__grid">
        {% macro filter_error(name) %}{% if errors.get(name) %}<p class="field__error" id="dash-{{ name }}-error"><span class="visually-hidden">Error:</span> {{ labels[name] }} {{ errors[name] }}</p>{% endif %}{% endmacro %}
        {% macro invalid(name) %}{% if errors.get(name) %} aria-invalid="true" aria-describedby="dash-{{ name }}-error"{% endif %}{% endmacro %}
        <div class="field">
            <label class="field__label" for="dash-instrument_id">Instrument</label>
            <select class="input select" id="dash-instrument_id" name="instrument_id"{{ invalid("instrument_id") }}>
                <option value="">All instruments</option>
                {% for i in instruments %}
                <option value="{{ i.id }}" {% if raw.instrument_id == i.id | string %}selected{% endif %}>{{ i.symbol }}</option>
                {% endfor %}
            </select>
            {{ filter_error("instrument_id") }}
        </div>
        <div class="field">
            <label class="field__label" for="dash-strategy">Strategy</label>
            <select class="input select" id="dash-strategy" name="strategy"{{ invalid("strategy") }}>
                <option value="">All strategies</option>
                {% for s in strategies %}
                <option value="{{ s }}" {% if raw.strategy == s %}selected{% endif %}>{{ s }}</option>
                {% endfor %}
            </select>
            {{ filter_error("strategy") }}
        </div>
        <div class="field">
            <label class="field__label" for="dash-session">Session</label>
            <select class="input select" id="dash-session" name="session"{{ invalid("session") }}>
                <option value="">All sessions</option>
                {% for key, label in session_options %}
                <option value="{{ key }}" {% if raw.session | upper == key %}selected{% endif %}>{{ label }}</option>
                {% endfor %}
            </select>
            {{ filter_error("session") }}
        </div>
        <div class="field">
            <label class="field__label" for="dash-start">Realised from</label>
            <input class="input" id="dash-start" name="start" type="date" value="{{ raw.start }}"{{ invalid("start") }}>
            {{ filter_error("start") }}
        </div>
        <div class="field">
            <label class="field__label" for="dash-end">Realised to</label>
            <input class="input" id="dash-end" name="end" type="date" value="{{ raw.end }}"{{ invalid("end") }}>
            {{ filter_error("end") }}
        </div>
    </div>
    <p class="field__hint">Dates select trades by when they were <strong>closed</strong> (UTC), not when they were opened. The address bar keeps your filters, so a filtered report can be bookmarked.</p>
    <div class="filters__actions">
        <button class="button button--primary" type="submit">Apply filters</button>
        {% if active or errors %}<a class="button button--ghost" href="{{ url_for('main.analytics') }}">Reset filters</a>{% endif %}
    </div>
</form>

{% if dashboard %}
{% set report = dashboard.report %}

{% if active %}
<p class="results-summary" role="status">
    Showing results for:
    {% for name, value in active.items() %}<span class="filter-chip">{{ labels[name] }}:
        {% if name == "instrument_id" %}{% for i in instruments if i.id == value %}{{ i.symbol }}{% endfor %}
        {% elif name == "session" %}{% for key, label in session_options if key == value %}{{ label }}{% endfor %}
        {% else %}{{ value }}{% endif %}</span>{% endfor %}
</p>
{% endif %}

{# ---------- 2. KPI cards ---------- #}
<section aria-labelledby="kpi-title">
    <div class="section-heading">
        <h2 id="kpi-title">Summary</h2>
        <p class="muted">Closed trades only. Open trades have no realised result.</p>
    </div>
    <dl class="kpi-grid">
        {% for kpi in dashboard.kpis %}{{ dash.kpi_card(kpi) }}{% endfor %}
    </dl>
    {% if money %}
    <div class="card money-strip">
        <h3 class="money-strip__title">Net P&amp;L by quote currency</h3>
        <ul class="money-strip__list" role="list">
            {% for currency, text, tone, trades in money %}
            <li data-currency="{{ currency }}"><span class="muted">{{ currency }}</span>
                <span class="num{% if tone != 'neutral' %} num--{{ tone }}{% endif %}">{{ text }}</span>
                <span class="muted">({{ trades }} {{ "trade" if trades == 1 else "trades" }})</span></li>
            {% endfor %}
        </ul>
        <p class="money-strip__note">Each currency is reported separately. TradeLedger has no exchange rates, so it never adds USD and GBP together.</p>
    </div>
    {% endif %}
</section>

{% if not dashboard.has_results %}
    {% if report.open_trades %}
    {{ ui.empty_state("No closed trades match these filters",
                      report.open_trades ~ " open " ~ ("trade matches" if report.open_trades == 1 else "trades match") ~ ", but open trades have no realised result yet. Close a trade, or widen the filters.",
                      url_for('main.analytics'), "Reset filters") }}
    {% elif active %}
    {{ ui.empty_state("No closed trades match these filters",
                      "Try a wider date range or another instrument, strategy or session.",
                      url_for('main.analytics'), "Reset filters") }}
    {% else %}
    {{ ui.empty_state("No closed trades yet",
                      "Charts and statistics appear once you record a closed trade.",
                      url_for('main.new_trade'), "Record a trade") }}
    {% endif %}
{% else %}

<p class="notice notice--info no-js-only">Charts need JavaScript. Every figure they show is also in the tables further down this page.</p>

{# ---------- 3. Performance over time ---------- #}
<section aria-labelledby="time-title">
    <div class="section-heading"><h2 id="time-title">Performance over time</h2></div>
    <div class="dash-grid dash-grid--time">
        {{ dash.chart_figure("curve", "Cumulative realised R",
            "Running total of R-multiples, in the order trades closed. A trade opened earlier but closed later appears when it closed. This is not an account equity curve: TradeLedger records no deposits or balances.",
            dashboard.descriptions.curve, "table-series", tall=True) }}
        {{ dash.chart_figure("drawdown", "Drawdown from peak",
            "How far cumulative R sits below its highest point so far (the running peak, which starts at 0R). Measured in R, not as a percentage of an account.",
            dashboard.descriptions.drawdown, "table-series") }}
    </div>
</section>

{# ---------- 4. Breakdowns ---------- #}
<section aria-labelledby="breakdown-title">
    <div class="section-heading">
        <h2 id="breakdown-title">Breakdowns</h2>
        <p class="muted">Historical results, not predictions. Bars show total realised R.</p>
    </div>
    <div class="dash-grid">
        {{ dash.chart_figure("monthly", "Realised R by month",
            "Total R of trades closed in each UTC month. A month with no closed trades is left blank, not drawn as 0R.",
            dashboard.descriptions.monthly, "table-monthly") }}
        {{ dash.chart_figure("weekday", "Realised R by weekday (UTC)",
            "Total R by the UTC weekday each trade closed, Monday to Sunday.",
            dashboard.descriptions.weekday, "table-weekday") }}
        {{ dash.chart_figure("instruments", "Realised R by instrument",
            "Compared in R so instruments quoted in different currencies are comparable. * = fewer than " ~ small_sample ~ " closed trades.",
            dashboard.descriptions.instruments, "table-instruments") }}
        {{ dash.chart_figure("strategies", "Realised R by strategy",
            "Every strategy label stored on your trades; trades without one are 'Unspecified'. * = fewer than " ~ small_sample ~ " closed trades.",
            dashboard.descriptions.strategies, "table-strategies") }}
        {{ dash.chart_figure("sessions", "Realised R by session",
            "The session recorded on each trade. It is never guessed from the trade's time. * = fewer than " ~ small_sample ~ " closed trades.",
            dashboard.descriptions.sessions, "table-sessions") }}
    </div>
</section>

{# ---------- 5. Detailed tables ---------- #}
<section aria-labelledby="tables-title">
    <div class="section-heading">
        <h2 id="tables-title">Detailed tables</h2>
        <p class="muted">The exact figures behind every chart, rounded only for display.</p>
    </div>

    <section class="data-table" aria-labelledby="table-series-title">
        <h3 class="data-table__title" id="table-series-title">Cumulative R and drawdown, trade by trade</h3>
        {% if dashboard.series | length > dashboard.series_table | length %}
        <p class="table-note">Showing the most recent {{ dashboard.series_table | length }} of {{ dashboard.series | length }} trades. The charts and the tables below include every trade; the full series is also available from <code>/api/v1/analytics/curve</code>.</p>
        {% endif %}
        <div class="table-wrap" id="table-series" role="region" aria-labelledby="table-series-title" tabindex="0">
            <table class="table">
                <caption class="visually-hidden">Closed trades in realised order with cumulative R, running peak and drawdown</caption>
                <thead>
                    <tr>
                        <th scope="col">Realised (UTC)</th>
                        <th scope="col">Trade</th>
                        <th scope="col" class="num-col">Trade R</th>
                        <th scope="col" class="num-col">Cumulative R</th>
                        <th scope="col" class="num-col">Running peak</th>
                        <th scope="col" class="num-col">Drawdown</th>
                    </tr>
                </thead>
                <tbody>
                    {% for p in dashboard.series_table %}
                    <tr{% if p.trade_id == report.overall.max_drawdown_r.trough_trade_id %} class="is-max-drawdown"{% endif %}>
                        <td data-label="Realised (UTC)">{{ p.realised_at | when }}{% if not p.close_time_known %} <span class="sample-tag">Entry time: exit not recorded</span>{% endif %}</td>
                        <th scope="row" data-label="Trade"><a class="text-link" href="{{ url_for('main.trade_detail', trade_id=p.trade_id) }}">#{{ p.trade_id }} {{ p.symbol }}</a></th>
                        <td data-label="Trade R" class="num-col">{{ ui.signed(p.realised_r, p.realised_r | r) }}</td>
                        <td data-label="Cumulative R" class="num-col">{{ ui.signed(p.cumulative_r, p.cumulative_r | r) }}</td>
                        <td data-label="Running peak" class="num-col">{{ p.peak_r | r }}</td>
                        <td data-label="Drawdown" class="num-col">{{ p.drawdown_r | r_amount }}{% if p.trade_id == report.overall.max_drawdown_r.trough_trade_id %} <span class="sample-tag">Maximum</span>{% endif %}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </section>

    {{ dash.period_table("table-monthly", "Results by month", "Month", dashboard.months) }}
    {{ dash.period_table("table-weekday", "Results by weekday (UTC)", "Weekday", dashboard.weekdays) }}
    {{ dash.group_table("table-instruments", "Results by instrument", "Instrument", dashboard.instruments, small_sample) }}
    {{ dash.group_table("table-strategies", "Results by strategy", "Strategy", dashboard.strategies, small_sample) }}
    {{ dash.group_table("table-sessions", "Results by session", "Session", dashboard.sessions, small_sample) }}
</section>
{% endif %}

{# ---------- 6. Data quality ---------- #}
<section class="card prose data-quality" aria-labelledby="quality-title">
    <h2 id="quality-title">About these figures</h2>
    <ul>
        <li><strong>Open trades:</strong> {{ report.open_trades }} {{ "trade is" if report.open_trades == 1 else "trades are" }} open and excluded from every figure above.</li>
        {% if report.unknown_close_time_trade_ids %}
        <li><strong>Unknown exit times:</strong> {{ report.unknown_close_time_trade_ids | length }} closed {{ "trade was" if report.unknown_close_time_trade_ids | length == 1 else "trades were" }} recorded before exit times were tracked
            (#{{ report.unknown_close_time_trade_ids | join(", #") }}). {{ "It is" if report.unknown_close_time_trade_ids | length == 1 else "They are" }} placed at {{ "its" if report.unknown_close_time_trade_ids | length == 1 else "their" }} <em>entry</em> time, an approximation that can move {{ "it" if report.unknown_close_time_trade_ids | length == 1 else "them" }} into an earlier day or month.</li>
        {% endif %}
        {% if report.r_unavailable %}
        <li><strong>No usable R:</strong> {% for item in report.r_unavailable %}trade #{{ item.trade_id }} ({{ item.reason }}){{ ", " if not loop.last }}{% endfor %}. These count in win/loss figures and money, but not in R figures or charts.</li>
        {% endif %}
        {% if report.excluded %}
        <li><strong>Could not be evaluated:</strong> {% for item in report.excluded %}trade #{{ item.trade_id }} ({{ item.reason }}){{ ", " if not loop.last }}{% endfor %}.</li>
        {% endif %}
        <li><strong>Running peak and drawdown:</strong> the running peak is the highest cumulative R reached so far, starting from 0R. Drawdown is the running peak minus the current cumulative R. Maximum drawdown is the largest such fall. Total realised R is simply where cumulative R ends.</li>
        <li><strong>Units:</strong> R-multiples compare trades of any size and currency. Money is shown per quote currency only.</li>
        <li><strong>Sample data:</strong> the trades loaded by <code>flask --app tradeledger seed-db</code> are synthetic, for demonstration only.</li>
        <li><strong>Charts:</strong> drawn by Chart.js {{ chart_js.version }} from exact values calculated in Python. Positions on a chart are approximate; the tables and text are the reference.</li>
    </ul>
</section>

{% if dashboard.has_results %}
{# Chart data for analytics.js. type="application/json" is data, not code:
   the browser never executes it, so it is compatible with the
   Content-Security-Policy. tojson escapes <, > and & so the content cannot
   close this element early. #}
<script type="application/json" id="dashboard-data">{{ payload | tojson }}</script>
{% endif %}
{% endif %}
{% endblock %}
```


## 6. `tradeledger/static/css/analytics.css` (NEW)

```css
/* ==========================================================================
   Analytics dashboard (Phase 6)
   Uses only the design tokens from base.css, so the dashboard matches the
   rest of TradeLedger and changes with it.
   ========================================================================== */

/* ---------- Filters ---------- */
.dash-filters__title {
    font-size: var(--text-base);
}

.filter-chip {
    display: inline-block;
    margin: 0 var(--space-1) var(--space-1) 0;
    padding: 0.1rem var(--space-2);
    border: 1px solid var(--color-border-strong);
    border-radius: 999px;
    color: var(--color-text);
}

/* ---------- KPI cards ---------- */
/* A <dl> of cards: auto-fit makes as many columns as fit (two on a phone,
   four at 1024px), and wide screens get exactly six (below). */
.kpi-grid {
    display: grid;
    /* 9.5rem lets two cards sit side by side on a 375px phone. */
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 9.5rem), 1fr));
    gap: var(--space-3);
    margin: 0;
}

/* Wide screens: exactly six columns, so the twelve cards form two even rows. */
@media (min-width: 80rem) {
    .kpi-grid {
        grid-template-columns: repeat(6, minmax(0, 1fr));
    }
}

.kpi {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
    padding: var(--space-4);
    border: 1px solid var(--color-border);
    border-top: 3px solid var(--color-border-strong);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
}

.kpi--positive {
    border-top-color: var(--color-positive);
}

.kpi--negative {
    border-top-color: var(--color-negative);
}

.kpi__label {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
    font-weight: 600;
}

.kpi__value {
    margin: 0;
    font-size: var(--text-xl);
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}

.kpi--positive .kpi__value {
    color: var(--color-positive);
}

.kpi--negative .kpi__value {
    color: var(--color-negative);
}

.kpi__note {
    margin: 0;
    color: var(--color-text-subtle);
    font-size: var(--text-xs);
}

/* ---------- Chart cards ---------- */
.dash-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
}

@media (min-width: 72rem) {
    .dash-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
    }

    /* The cumulative R chart spans the full width; drawdown sits below. */
    .dash-grid--time {
        grid-template-columns: minmax(0, 1fr);
    }
}

.chart-card {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    min-width: 0;
    margin: 0;
    padding: var(--space-5);
    border: 1px solid var(--color-border);
    border-radius: var(--radius-lg);
    background: var(--color-surface);
}

.chart-card__head {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
}

.chart-card__title {
    font-size: var(--text-base);
}

.chart-card__explain {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

/* Chart.js sizes the canvas to its parent when maintainAspectRatio is off,
   so the parent needs a definite height. position: relative is required by
   Chart.js's responsive resizing. */
.chart-card__canvas {
    position: relative;
    height: 16rem;
    min-width: 0;
}

.chart-card__canvas--tall {
    height: 20rem;
}

@media (max-width: 40rem) {
    .chart-card {
        padding: var(--space-4);
    }

    .chart-card__canvas {
        height: 14rem;
    }

    .chart-card__canvas--tall {
        height: 16rem;
    }
}

/* Without JavaScript there is nothing to draw: hide the empty boxes. */
html:not(.js) .chart-card__canvas {
    display: none;
}

.chart-card__canvas.is-unavailable {
    height: auto;
}

.chart-card__fallback {
    padding: var(--space-3) var(--space-4);
    border: 1px dashed var(--color-border-strong);
    border-radius: var(--radius);
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.chart-card__summary {
    font-size: var(--text-sm);
}

.chart-card__table-link {
    font-size: var(--text-sm);
}

/* ---------- Tables ---------- */
.data-table {
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    margin-top: var(--space-5);
}

.data-table__title {
    font-size: var(--text-base);
}

.table tbody th {
    text-align: left;
    font-weight: 600;
}

/* components.css removes the last row's border from <td> only; row
   headers (<th scope="row">) need the same. */
.table tbody tr:last-child th {
    border-bottom: 0;
}

/* Card layout (below 64rem, see components.css): row headers become a
   labelled line like every other cell. */
@media (max-width: 63.99rem) {
    .table tbody th {
        display: flex;
        justify-content: space-between;
        gap: var(--space-4);
        width: 100%;
        padding: var(--space-2) 0;
        border-bottom: 1px solid var(--color-border);
        white-space: normal;
        text-align: right;
    }

    .table tbody th[data-label]::before {
        content: attr(data-label);
        color: var(--color-text-muted);
        font-weight: 600;
        text-align: left;
    }

    .table tbody tr:last-child th {
        border-bottom: 1px solid var(--color-border);
    }

    .table .empty-row {
        justify-content: flex-start;
    }
}

.empty-row {
    font-style: italic;
}

.sample-tag {
    display: inline-block;
    margin-left: var(--space-1);
    padding: 0 var(--space-2);
    border: 1px solid var(--color-border-strong);
    border-radius: 999px;
    color: var(--color-text-muted);
    font-size: var(--text-xs);
    font-weight: 500;
    white-space: nowrap;
}

.table .is-max-drawdown td,
.table .is-max-drawdown th {
    background: var(--color-negative-soft);
}

/* ---------- Data quality ---------- */
.data-quality {
    font-size: var(--text-sm);
}
```


## 7. `tradeledger/static/js/analytics.js` (NEW)

```javascript
// TradeLedger analytics dashboard: draws the seven charts with Chart.js.
//
// This file is PRESENTATION ONLY. It performs no financial calculation:
//   * every figure arrives from Python in the page's #dashboard-data JSON,
//     as an exact decimal string (e.g. "7.688173...") plus display text
//     (e.g. "+7.69R");
//   * the only conversion is toNumber(), which turns an exact string into
//     a JavaScript number to POSITION a point or bar. Tooltips show the
//     Python-formatted text, never a number computed here.
//
// If Chart.js cannot be loaded (offline, CDN blocked) or JavaScript is off,
// the page still shows every figure in its KPI cards, text descriptions and
// tables; each chart area explains why it is empty.
//
// The pure functions (toNumber, buildConfigs, ...) are exported for the
// Node.js tests in tests/js/analytics.test.js.
(function (root, factory) {
    "use strict";
    var api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    } else {
        root.TradeLedgerCharts = api;
    }
    if (typeof document !== "undefined") {
        document.addEventListener("DOMContentLoaded", function () {
            api.instances = api.init(document, root.Chart);
        });
    }
}(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    var UNAVAILABLE_MESSAGE =
        "Chart unavailable: the Chart.js library could not be loaded. It comes from cdn.jsdelivr.net, " +
        "so it needs an internet connection. The table below has the same figures.";
    var FAILED_MESSAGE = "This chart could not be drawn. The table below has the same figures.";

    // ---------- Small pure helpers ----------

    // Exact decimal string -> chart number. Absent, non-numeric or
    // non-finite values become null (Chart.js leaves a gap), never NaN or
    // Infinity.
    function toNumber(text) {
        if (text === null || text === undefined || text === "") {
            return null;
        }
        var value = Number(text);
        return Number.isFinite(value) ? value : null;
    }

    // "#4cd79a" + 0.2 -> "rgba(76, 215, 154, 0.2)" for translucent fills.
    function hexToRgba(hex, alpha) {
        var match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex).trim());
        if (!match) {
            return hex;
        }
        return "rgba(" + parseInt(match[1], 16) + ", " + parseInt(match[2], 16) + ", " +
            parseInt(match[3], 16) + ", " + alpha + ")";
    }

    // Axis label for a UTC time in milliseconds: "2026-03-02".
    function formatAxisDate(ms) {
        var date = new Date(ms);
        return isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 10);
    }

    function signColour(value, theme) {
        if (value === null || value === 0) {
            return theme.muted;
        }
        return value > 0 ? theme.positive : theme.negative;
    }

    // Grid lines, with a stronger line at zero so gains and losses are
    // separated by a clear reference.
    function valueAxis(theme, extra) {
        var axis = {
            grid: {
                color: function (context) {
                    return context.tick && context.tick.value === 0 ? theme.zero : theme.grid;
                },
                lineWidth: function (context) {
                    return context.tick && context.tick.value === 0 ? 2 : 1;
                }
            },
            ticks: {
                callback: function (value) {
                    return value + "R";
                }
            },
            title: {display: true, text: "R (risk multiples)"}
        };
        return Object.assign(axis, extra || {});
    }

    function baseOptions() {
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,                      // no decorative motion
            plugins: {
                legend: {display: false},          // one series per chart; the caption explains it
                tooltip: {}
            }
        };
    }

    // ---------- Line charts: cumulative R and drawdown ----------

    function curveTooltipLines(point) {
        if (point.start) {
            return ["Starting point: " + point.cumulative_text];
        }
        var lines = [
            "Trade #" + point.trade_id + " · " + point.symbol,
            "Trade R: " + point.realised_text,
            "Cumulative R: " + point.cumulative_text
        ];
        if (!point.close_time_known) {
            lines.push("Exit time not recorded: placed at entry time");
        }
        return lines;
    }

    function timeAxis() {
        return {
            type: "linear",
            ticks: {
                maxTicksLimit: 6,
                callback: function (value) {
                    return formatAxisDate(value);
                }
            },
            title: {display: true, text: "Realised date (UTC)"}
        };
    }

    function buildCurveConfig(points, theme) {
        var options = baseOptions();
        options.interaction = {mode: "nearest", intersect: false, axis: "x"};
        options.scales = {x: timeAxis(), y: valueAxis(theme)};
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                var point = points[items[0].dataIndex];
                return point.start ? "Before the first trade" : point.when;
            },
            label: function (item) {
                return curveTooltipLines(points[item.dataIndex]);
            }
        };
        return {
            type: "line",
            data: {
                datasets: [{
                    label: "Cumulative realised R",
                    data: points.map(function (p) {
                        return {x: p.x, y: toNumber(p.cumulative_r)};
                    }),
                    borderColor: theme.accent,
                    backgroundColor: theme.accent,
                    borderWidth: 2,
                    pointRadius: points.length > 60 ? 0 : 3,
                    pointHoverRadius: 5,
                    tension: 0
                }]
            },
            options: options
        };
    }

    function buildDrawdownConfig(points, maxDrawdown, theme) {
        var troughId = maxDrawdown ? maxDrawdown.trough_trade_id : null;
        var isTrough = function (p) {
            return !p.start && troughId !== null && p.trade_id === troughId;
        };
        var options = baseOptions();
        options.interaction = {mode: "nearest", intersect: false, axis: "x"};
        options.scales = {
            x: timeAxis(),
            y: valueAxis(theme, {suggestedMax: 0})
        };
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                var point = points[items[0].dataIndex];
                return point.start ? "Before the first trade" : point.when;
            },
            label: function (item) {
                var point = points[item.dataIndex];
                var lines = ["Drawdown: " + point.drawdown_text + " below the running peak"];
                if (!point.start) {
                    lines.push("Trade #" + point.trade_id + " · " + point.symbol);
                    lines.push("Cumulative R: " + point.cumulative_text);
                }
                if (isTrough(point)) {
                    lines.push("Maximum drawdown: " + maxDrawdown.text);
                }
                return lines;
            }
        };
        return {
            type: "line",
            data: {
                datasets: [{
                    label: "Drawdown (R below the running peak)",
                    data: points.map(function (p) {
                        return {x: p.x, y: toNumber(p.underwater_r)};
                    }),
                    borderColor: theme.negative,
                    backgroundColor: hexToRgba(theme.negative, 0.18),
                    fill: "origin",
                    borderWidth: 2,
                    tension: 0,
                    pointRadius: points.map(function (p) {
                        return isTrough(p) ? 6 : (points.length > 60 ? 0 : 2);
                    }),
                    pointStyle: points.map(function (p) {
                        return isTrough(p) ? "triangle" : "circle";
                    }),
                    pointBackgroundColor: points.map(function (p) {
                        return isTrough(p) ? theme.text : theme.negative;
                    })
                }]
            },
            options: options
        };
    }

    // ---------- Bar charts: months, weekdays, groups ----------

    function barTooltipLines(row) {
        if (row.total_r === null) {
            return ["No closed trades"];
        }
        var lines = [
            "Total R: " + row.total_r_text,
            "Closed trades: " + row.trades + " (won " + row.wins + ", lost " + row.losses + ")",
            "Win rate: " + row.win_rate_text,
            "Average R: " + row.average_r_text,
            "Expectancy: " + row.expectancy_text
        ];
        if (row.small_sample) {
            lines.push("Small sample: treat with caution");
        }
        return lines;
    }

    function buildBarConfig(rows, theme, settings) {
        var horizontal = Boolean(settings.horizontal);
        var values = rows.map(function (row) {
            return toNumber(row.total_r);
        });
        var options = baseOptions();
        if (horizontal) {
            options.indexAxis = "y";
            options.scales = {x: valueAxis(theme), y: {grid: {display: false}}};
        } else {
            options.scales = {x: {grid: {display: false}}, y: valueAxis(theme)};
        }
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                return rows[items[0].dataIndex].label;
            },
            label: function (item) {
                return barTooltipLines(rows[item.dataIndex]);
            }
        };
        return {
            type: "bar",
            data: {
                labels: rows.map(function (row) {
                    return row.small_sample ? row.label + " *" : row.label;
                }),
                datasets: [{
                    label: settings.label,
                    data: values,
                    backgroundColor: values.map(function (value) {
                        return signColour(value, theme);
                    }),
                    borderRadius: 3,
                    maxBarThickness: 36
                }]
            },
            options: options
        };
    }

    // Every chart's configuration from the payload. Pure: no DOM, no Chart.
    function buildConfigs(payload, theme) {
        return {
            curve: buildCurveConfig(payload.curve, theme),
            drawdown: buildDrawdownConfig(payload.curve, payload.max_drawdown, theme),
            monthly: buildBarConfig(payload.monthly, theme, {label: "Total R by month"}),
            weekday: buildBarConfig(payload.weekday, theme, {label: "Total R by weekday"}),
            instruments: buildBarConfig(payload.instruments, theme,
                {label: "Total R by instrument", horizontal: true}),
            strategies: buildBarConfig(payload.strategies, theme,
                {label: "Total R by strategy", horizontal: true}),
            sessions: buildBarConfig(payload.sessions, theme,
                {label: "Total R by session", horizontal: true})
        };
    }

    // ---------- Browser glue ----------

    // Colours come from the CSS design tokens in base.css, so the charts
    // follow the same theme as the rest of the application.
    function readTheme(doc) {
        var styles = doc.defaultView.getComputedStyle(doc.documentElement);
        var token = function (name) {
            return styles.getPropertyValue(name).trim();
        };
        return {
            text: token("--color-text"),
            muted: token("--color-text-muted"),
            grid: token("--color-border"),
            zero: token("--color-text-subtle"),
            accent: token("--color-accent"),
            positive: token("--color-positive"),
            negative: token("--color-negative"),
            font: token("--font-sans")
        };
    }

    function showFallback(canvas, message) {
        var box = canvas.closest("[data-chart-box]");
        var note = box ? box.querySelector("[data-chart-fallback]") : null;
        canvas.hidden = true;
        if (box) {
            box.classList.add("is-unavailable");
        }
        if (note) {
            note.textContent = message;          // textContent: never parsed as HTML
            note.hidden = false;
        }
    }

    function init(doc, ChartLib) {
        var dataElement = doc.getElementById("dashboard-data");
        var canvases = Array.prototype.slice.call(doc.querySelectorAll("canvas[data-chart]"));
        if (!dataElement || canvases.length === 0) {
            return [];
        }
        if (typeof ChartLib !== "function") {
            canvases.forEach(function (canvas) {
                showFallback(canvas, UNAVAILABLE_MESSAGE);
            });
            return [];
        }
        var theme = readTheme(doc);
        if (ChartLib.defaults) {
            ChartLib.defaults.color = theme.muted;
            ChartLib.defaults.borderColor = theme.grid;
            if (ChartLib.defaults.font) {
                ChartLib.defaults.font.family = theme.font;
            }
        }
        var configs = buildConfigs(JSON.parse(dataElement.textContent), theme);
        var charts = [];
        canvases.forEach(function (canvas) {
            var config = configs[canvas.getAttribute("data-chart")];
            if (!config) {
                return;
            }
            try {
                charts.push(new ChartLib(canvas, config));
            } catch (error) {
                showFallback(canvas, FAILED_MESSAGE);
                if (typeof console !== "undefined") {
                    console.error("TradeLedger chart failed:", canvas.id, error);
                }
            }
        });
        return charts;
    }

    return {
        toNumber: toNumber,
        hexToRgba: hexToRgba,
        formatAxisDate: formatAxisDate,
        curveTooltipLines: curveTooltipLines,
        barTooltipLines: barTooltipLines,
        buildConfigs: buildConfigs,
        init: init,
        instances: []
    };
}));
```


## 8. `tradeledger/static/vendor/chartjs/README.md` (NEW)

````markdown
# Self-hosted Chart.js (optional)

This folder is intentionally empty apart from this note. Third-party code
lives here, never mixed with TradeLedger's own files in `static/js/`.

By default the dashboard loads Chart.js 4.5.1 from the jsDelivr CDN. To
host it yourself, put `chart.umd.min.js` and `LICENSE.md` from the 4.5.1
release in this folder and set:

    TRADELEDGER_CHART_JS_URL=/static/vendor/chartjs/chart.umd.min.js

Full instructions: `docs/THIRD_PARTY_NOTICES.md`.
````


## 9. `scripts/benchmark_dashboard.py` (NEW)

```python
"""Time the /analytics dashboard on large synthetic journals.

    python scripts/benchmark_dashboard.py            # 1,000 and 10,000 trades
    python scripts/benchmark_dashboard.py 5000       # a custom size

Creates a throwaway SQLite database in a temporary folder (never your real
one), inserts N synthetic closed trades spread over several instruments,
strategies, sessions and two years, then measures, as the median of 5 runs:

  report     build_report(): SQL query + Decimal calculations
  dashboard  build_dashboard() + chart_payload(): the above plus chart prep
  page       a full GET /analytics through Flask, including the template
  filtered   GET /analytics with strategy + session + date filters

Numbers depend on the machine; the script prints the Python version and
platform so results can be compared honestly.
"""

import os
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema  # noqa: E402
from tradeledger.performance import build_report  # noqa: E402
from tradeledger.performance.dashboard import build_dashboard, chart_payload  # noqa: E402
from tradeledger.report_filters import ReportFilters  # noqa: E402

RUNS = 5
STRATEGIES = ("Breakout", "Pullback", "Reversal", None)
SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "OTHER", None)
EXITS = ("102", "99", "100.5", "99", "101.25", "100")      # +2, -1, +0.5, -1, +1.25, 0 R


def populate(conn, count):
    instruments = []
    for n, currency in enumerate(("USD", "USD", "GBP", "USD", "EUR")):
        cursor = conn.execute(
            "INSERT INTO instruments (symbol, name, asset_class, price_increment, contract_multiplier,"
            " quote_currency) VALUES (?, ?, 'OTHER', '0.01', '1', ?)",
            (f"BENCH{n}", f"Benchmark {n}", currency))
        instruments.append(cursor.lastrowid)
    rows = []
    for n in range(count):
        day = n * 730 // count                                  # spread over two years
        month, dom = 1 + (day // 28) % 12, 1 + day % 28
        year = 2025 + day // 336
        entered = f"{year}-{month:02d}-{dom:02d}T08:{n % 60:02d}:00Z"
        closed = f"{year}-{month:02d}-{dom:02d}T{9 + n % 12:02d}:{n % 60:02d}:00Z"
        rows.append((instruments[n % len(instruments)], entered, closed, EXITS[n % len(EXITS)],
                     STRATEGIES[n % len(STRATEGIES)], SESSIONS[n % len(SESSIONS)]))
    conn.executemany(
        "INSERT INTO trades (instrument_id, trade_date, closed_at, direction, entry_price, stop_loss,"
        " exit_price, position_size, fees, status, strategy, session)"
        " VALUES (?, ?, ?, 'LONG', '100', '99', ?, '1', '0', 'CLOSED', ?, ?)", rows)
    conn.commit()


def median_ms(action):
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        action()
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times)


def benchmark(count):
    with tempfile.TemporaryDirectory() as folder:
        app = create_app("testing", overrides={"DATABASE": os.path.join(folder, "bench.db")})
        with app.app_context():
            conn = get_db()
            init_schema(conn)
            populate(conn, count)
            assert build_report(conn).overall.trades == count
            report_ms = median_ms(lambda: build_report(conn))
            dashboard_ms = median_ms(lambda: chart_payload(build_dashboard(conn, ReportFilters())))
        client = app.test_client()
        assert client.get("/analytics").status_code == 200
        page_ms = median_ms(lambda: client.get("/analytics"))
        filtered_ms = median_ms(lambda: client.get(
            "/analytics?strategy=Breakout&session=LONDON&start=2025-06-01&end=2026-06-30"))
        size_kb = len(client.get("/analytics").data) / 1024
    print(f"{count:>7,} trades | report {report_ms:8.1f} ms | dashboard {dashboard_ms:8.1f} ms | "
          f"page {page_ms:8.1f} ms ({size_kb:,.0f} KB) | filtered page {filtered_ms:8.1f} ms")


if __name__ == "__main__":
    print(f"Python {platform.python_version()} on {platform.platform()}; median of {RUNS} runs")
    for size in [int(arg) for arg in sys.argv[1:]] or [1000, 10000]:
        benchmark(size)
```


## 10. `tests/test_report_filters.py` (NEW)

```python
"""Phase 6: report filters shared by the dashboard and the analytics API.

Three layers are tested separately so a failure points at the right code:
  1. parse_report_filters()  - validation only (no report)
  2. repository + build_report - the filters reach SQL and change results
  3. /api/v1/analytics/...    - the new strategy/session parameters

Sample data facts used below (tradeledger/seed.py, all synthetic):
  14 closed trades, 2 open (ids 14 and 15).
  Closed by strategy:  Breakout 5, Pullback 5, Reversal 2, none 2
  Closed by session:   LONDON 6, NEW_YORK 5, ASIA 2, none 1
  Closed Breakout + LONDON: trades 1, 3 and 10
"""

import pytest

from tradeledger.performance import build_report
from tradeledger.report_filters import FILTER_NAMES, ReportFilters, parse_report_filters
from tradeledger.repositories import trades as trade_repo
from tradeledger.validation import ValidationError
from tests.factories import table_snapshot
from tests.test_openapi import validate

BASE = "/api/v1/analytics"
ENDPOINTS = ["summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve"]


def parse(conn, blank_means_unset=True, **args):
    return parse_report_filters(args, conn, blank_means_unset=blank_means_unset)


# ---------- 1. Validation ----------

def test_no_arguments_means_no_filters(seeded_conn):
    filters, errors = parse(seeded_conn)
    assert errors == {}
    assert filters == ReportFilters()
    assert filters.is_empty and filters.active() == {}


def test_all_filters_valid(seeded_conn):
    filters, errors = parse(seeded_conn, instrument_id="1", strategy=" Breakout ", session="london",
                            start="2026-03-01", end="2026-03-31")
    assert errors == {}
    assert filters == ReportFilters(1, "Breakout", "LONDON", "2026-03-01", "2026-03-31")
    assert list(filters.as_kwargs()) == list(FILTER_NAMES)


def test_blank_values_mean_all_on_the_website(seeded_conn):
    filters, errors = parse(seeded_conn, instrument_id="", strategy="  ", session="", start="", end="")
    assert errors == {} and filters.is_empty


def test_blank_values_are_errors_in_the_api(seeded_conn):
    _, errors = parse(seeded_conn, blank_means_unset=False, instrument_id="", strategy=" ", session="")
    assert set(errors) == {"instrument_id", "strategy", "session"}


@pytest.mark.parametrize("value, message", [
    ("abc", "must be a whole number of 1 or more"),
    ("0", "must be a whole number of 1 or more"),
    ("-1", "must be a whole number of 1 or more"),
    ("1.5", "must be a whole number of 1 or more"),
    ("999", "does not match an existing instrument"),
])
def test_bad_instrument(seeded_conn, value, message):
    filters, errors = parse(seeded_conn, instrument_id=value)
    assert errors == {"instrument_id": message}
    assert filters.instrument_id is None


def test_strategy_length_limit(seeded_conn):
    assert parse(seeded_conn, strategy="x" * 50)[1] == {}
    assert parse(seeded_conn, strategy="x" * 51)[1] == {"strategy": "must be 50 characters or fewer"}


@pytest.mark.parametrize("value", ["MOON", "london-ish", "1"])
def test_unknown_session(seeded_conn, value):
    assert parse(seeded_conn, session=value)[1] == {
        "session": "must be one of: ASIA, LONDON, NEW_YORK, OTHER"}


@pytest.mark.parametrize("args, field", [
    ({"start": "2026-02-30"}, "start"),
    ({"end": "next week"}, "end"),
    ({"start": "2026-03-10", "end": "2026-03-09"}, "end"),
])
def test_bad_dates(seeded_conn, args, field):
    assert field in parse(seeded_conn, **args)[1]


def test_single_day_range_is_valid(seeded_conn):
    assert parse(seeded_conn, start="2026-03-02", end="2026-03-02")[1] == {}


def test_every_problem_is_reported_at_once(seeded_conn):
    _, errors = parse(seeded_conn, instrument_id="x", session="MOON", start="bad")
    assert set(errors) == {"instrument_id", "session", "start"}


# ---------- 2. Repository and build_report ----------

def test_repository_session_filter(seeded_conn):
    rows = trade_repo.list_trades(seeded_conn, session="LONDON")
    assert len(rows) == 7                                  # 6 closed + open trade 15
    assert {r["session"] for r in rows} == {"LONDON"}
    assert trade_repo.count_trades(seeded_conn, session="LONDON") == 7


def test_repository_rejects_unknown_session(seeded_conn):
    with pytest.raises(ValidationError):
        trade_repo.list_trades(seeded_conn, session="MOON")


@pytest.mark.parametrize("kwargs, closed, open_", [
    ({"strategy": "Breakout"}, 5, 1),
    ({"strategy": "Pullback"}, 5, 0),
    ({"session": "ASIA"}, 2, 0),
    ({"session": "LONDON"}, 6, 1),
    ({"strategy": "Breakout", "session": "LONDON"}, 3, 0),
    ({"instrument_id": 1, "strategy": "Pullback"}, 2, 0),
    ({"strategy": "No such strategy"}, 0, 0),
])
def test_build_report_filters(seeded_conn, kwargs, closed, open_):
    report = build_report(seeded_conn, **kwargs)
    assert report.overall.trades == closed
    assert report.open_trades == open_


def test_combined_filters_select_exactly_the_right_trades(seeded_conn):
    report = build_report(seeded_conn, strategy="Breakout", session="LONDON")
    assert [p.trade_id for p in report.cumulative_r] == [1, 3, 10]


def test_date_filter_uses_the_realised_date(app, seeded_conn):
    """Entered on 31 March, closed on 1 April: it belongs to April."""
    trade_repo.create_trade(seeded_conn, {
        "instrument_id": 1, "direction": "LONG", "status": "CLOSED",
        "trade_date": "2026-03-31T22:00:00Z", "closed_at": "2026-04-01T01:00:00Z",
        "entry_price": "1.1", "stop_loss": "1.09", "exit_price": "1.11", "position_size": "1",
    })
    assert build_report(seeded_conn, start="2026-04-01", end="2026-04-30").overall.trades == 1
    assert build_report(seeded_conn, start="2026-03-01", end="2026-03-31").overall.trades == 14


def test_filter_values_are_bound_parameters_not_sql(seeded_conn):
    """A classic injection string is just a strategy name nobody used."""
    before = trade_repo.count_trades(seeded_conn)
    report = build_report(seeded_conn, strategy="' OR 1=1 --")
    assert report.overall.trades == 0 and report.open_trades == 0
    assert trade_repo.count_trades(seeded_conn) == before


# ---------- 3. API ----------

@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_every_analytics_endpoint_accepts_the_new_filters(seeded_client, endpoint):
    response = seeded_client.get(f"{BASE}/{endpoint}?strategy=Breakout&session=london")
    assert response.status_code == 200
    filters = response.get_json()["meta"]["filters"]
    assert filters["strategy"] == "Breakout" and filters["session"] == "LONDON"


def test_api_strategy_filter_changes_the_numbers(seeded_client):
    body = seeded_client.get(f"{BASE}/summary?strategy=Breakout").get_json()
    assert body["data"]["overall"]["trades"] == 5
    assert body["data"]["open_trades"] == 1
    validate(body, "SummaryAnalytics")


def test_api_session_filter_on_grouped_endpoint(seeded_client):
    body = seeded_client.get(f"{BASE}/strategies?session=ASIA").get_json()
    assert [(g["group"], g["trades"]) for g in body["data"]] == [("Pullback", 1), ("Reversal", 1)]
    validate(body, "GroupedAnalytics")


def test_api_curve_with_combined_filters(seeded_client):
    body = seeded_client.get(f"{BASE}/curve?strategy=Breakout&session=LONDON").get_json()
    assert [point["trade_id"] for point in body["data"]] == [1, 3, 10]
    validate(body, "CurveAnalytics")


@pytest.mark.parametrize("query, field", [
    ("session=MOON", "session"), ("session=", "session"),
    ("strategy=", "strategy"), ("strategy=" + "x" * 51, "strategy"),
])
def test_api_rejects_bad_new_filters(seeded_client, query, field):
    response = seeded_client.get(f"{BASE}/summary?{query}")
    assert response.status_code == 400
    assert field in response.get_json()["error"]["details"]
    validate(response.get_json(), "Error")


def test_analytics_requests_never_change_data(app, seeded_client):
    before = (table_snapshot(app, "trades"), table_snapshot(app, "instruments"))
    for endpoint in ENDPOINTS:
        seeded_client.get(f"{BASE}/{endpoint}?strategy=Breakout&session=LONDON&start=2026-03-01")
    seeded_client.get("/analytics?strategy=Breakout&session=LONDON")
    assert (table_snapshot(app, "trades"), table_snapshot(app, "instruments")) == before
```


## 11. `tests/test_dashboard_data.py` (NEW)

```python
"""Phase 6: the dashboard data layer (performance/dashboard.py).

The main dataset below is written into a real SQLite database through the
repository, then read back through build_report() and build_dashboard(),
so these tests cover the whole path from SQL to chart payload.

Every expected number was worked out BY HAND, not by calling the code
under test. Each trade: entry 100, stop 99 (risk 1 per unit), size 1,
multiplier 1, no fees, so realised R = exit - 100.

  id  instrument  entered            closed (realised)     exit   R     strategy  session
  1   TEST (USD)  2025-12-31 10:00   2025-12-31 23:30 Wed  102    +2    Alpha     LONDON
  2   TEST (USD)  2026-01-01 20:00   2026-01-02 09:00 Fri   99    -1    Beta      -
  3   TEST (USD)  2025-12-30 09:00   2026-01-02 09:00 Fri   99    -1    -         ASIA
  4   GBPX (GBP)  2026-03-06 08:00   2026-03-07 12:00 Sat  100.5  +0.5  Alpha     LONDON
  5   TEST (USD)  2026-03-09 08:00   OPEN                   -      -     -         -

Trade 3 opened FIRST but closed after trade 1, so it must appear after 1.
Trades 2 and 3 close at the same second; the lower id (2) comes first.

Realised order 1, 2, 3, 4:
  cumulative R   2, 1, 0, 0.5        running peak  2, 2, 2, 2
  drawdown       0, 1, 2, 1.5        maximum drawdown 2 (peak after #1, low after #3)
  total R 0.5   wins 2   losses 2   win rate 50%   average R 0.125
  expectancy 0.5 x 1.25 + 0.5 x (-1) = 0.125     profit factor 2.5 / 2 = 1.25
  streaks: longest win 1, longest loss 2
  months: Dec 2025 +2 (1 trade), Jan 2026 -2 (2), Feb 2026 none, Mar 2026 +0.5 (1)
  weekdays: Wednesday +2 (1), Friday -2 (2), Saturday +0.5 (1), others none
  money: USD 2 - 1 - 1 = 0.00 USD, GBP +0.50 GBP (never added together)
"""

import json
from decimal import Decimal

import pytest

from tradeledger.performance import analyse, build_report
from tradeledger.performance.dashboard import (
    SERIES_TABLE_LIMIT,
    SMALL_SAMPLE,
    build_dashboard,
    chart_payload,
    drawdown_series,
    make_dashboard,
    money_rows,
    month_keys,
    month_rows,
    to_epoch_ms,
    weekday_rows,
)
from tradeledger.report_filters import ReportFilters
from tradeledger.repositories.instruments import create_instrument
from tradeledger.repositories.trades import create_trade
from tests.factories import make_trade


def D(text):
    return Decimal(text)


@pytest.fixture
def hand_conn(conn):
    usd = create_instrument(conn, {"symbol": "TEST", "name": "Test USD", "asset_class": "OTHER",
                                   "price_increment": "0.01", "contract_multiplier": "1",
                                   "quote_currency": "USD"})
    gbp = create_instrument(conn, {"symbol": "GBPX", "name": "Test GBP", "asset_class": "OTHER",
                                   "price_increment": "0.01", "contract_multiplier": "1",
                                   "quote_currency": "GBP"})

    def trade(instrument, entered, closed, exit_price, strategy=None, session=None):
        create_trade(conn, {
            "instrument_id": instrument, "direction": "LONG",
            "status": "CLOSED" if closed else "OPEN",
            "trade_date": entered, "closed_at": closed,
            "entry_price": "100", "stop_loss": "99", "exit_price": exit_price,
            "position_size": "1", "fees": "0", "strategy": strategy, "session": session,
        })

    trade(usd, "2025-12-31T10:00:00Z", "2025-12-31T23:30:00Z", "102", "Alpha", "LONDON")
    trade(usd, "2026-01-01T20:00:00Z", "2026-01-02T09:00:00Z", "99", "Beta")
    trade(usd, "2025-12-30T09:00:00Z", "2026-01-02T09:00:00Z", "99", None, "ASIA")
    trade(gbp, "2026-03-06T08:00:00Z", "2026-03-07T12:00:00Z", "100.5", "Alpha", "LONDON")
    trade(usd, "2026-03-09T08:00:00Z", None, None)
    return conn


@pytest.fixture
def board(hand_conn):
    return build_dashboard(hand_conn, ReportFilters())


# ---------- Chronology, cumulative R and drawdown ----------

def test_series_follows_realised_order_with_stable_ties(board):
    assert [p.trade_id for p in board.series] == [1, 2, 3, 4]


def test_cumulative_peak_and_drawdown_match_hand_calculation(board):
    assert [p.cumulative_r for p in board.series] == [D("2"), D("1"), D("0"), D("0.5")]
    assert [p.peak_r for p in board.series] == [D("2")] * 4
    assert [p.drawdown_r for p in board.series] == [D("0"), D("1"), D("2"), D("1.5")]


def test_maximum_drawdown_from_both_paths_agrees(board):
    worst = board.report.overall.max_drawdown_r
    assert worst.amount == D("2") == max(p.drawdown_r for p in board.series)
    assert (worst.peak_trade_id, worst.trough_trade_id) == (1, 3)


def test_drawdown_starts_from_zero_when_the_first_trade_loses():
    """Only losing trades: the peak stays at the 0R starting point."""
    report = analyse([make_trade(id=1, exit_price=D("90"), closed_at="2026-01-05T11:00:00Z"),
                      make_trade(id=2, exit_price=D("85"), closed_at="2026-01-06T11:00:00Z")])
    series = drawdown_series(report.cumulative_r)
    assert [p.cumulative_r for p in series] == [D("-1"), D("-2.5")]
    assert [p.peak_r for p in series] == [D("0"), D("0")]
    assert [p.drawdown_r for p in series] == [D("1"), D("2.5")]
    board = make_dashboard(report, ReportFilters())
    assert "from the starting point (0R)" in board.descriptions["drawdown"]


def test_only_winning_trades_have_no_drawdown():
    report = analyse([make_trade(id=1, closed_at="2026-01-05T11:00:00Z"),
                      make_trade(id=2, closed_at="2026-01-06T11:00:00Z")])
    board = make_dashboard(report, ReportFilters())
    assert all(p.drawdown_r == 0 for p in board.series)
    assert board.descriptions["drawdown"] == "No drawdown: cumulative R never fell below its previous peak."
    kpis = {k.key: k for k in board.kpis}
    assert kpis["profit_factor_r"].value == "n/a"
    assert kpis["profit_factor_r"].note == "Needs at least one losing trade"
    assert kpis["max_drawdown_r"].value == "0.00R"


def test_to_epoch_ms_against_a_known_instant():
    # 2026-01-01T00:00Z is 1,767,225,600 s; plus 1 day and 9 hours.
    assert to_epoch_ms("2026-01-02T09:00:00Z") == (1_767_225_600 + 86_400 + 9 * 3_600) * 1000


def test_series_has_epoch_x_values_in_order(board):
    xs = [p.x for p in board.series]
    assert xs == sorted(xs)
    assert xs[1] == xs[2] == to_epoch_ms("2026-01-02T09:00:00Z")


# ---------- Months and weekdays ----------

def test_month_keys_cross_a_year_boundary():
    assert month_keys("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert month_keys("2026-03", "2026-03") == ["2026-03"]


def test_months_keep_an_empty_month_as_a_gap(board):
    rows = [(m.key, m.label, None if m.summary is None else (m.summary.trades, m.summary.total_r))
            for m in board.months]
    assert rows == [
        ("2025-12", "Dec 2025", (1, D("2"))),
        ("2026-01", "Jan 2026", (2, D("-2"))),
        ("2026-02", "Feb 2026", None),                 # no trades: not "0R"
        ("2026-03", "Mar 2026", (1, D("0.5"))),
    ]


def test_weekdays_are_all_seven_in_calendar_order(board):
    rows = [(w.key, None if w.summary is None else w.summary.total_r) for w in board.weekdays]
    assert rows == [("Monday", None), ("Tuesday", None), ("Wednesday", D("2")), ("Thursday", None),
                    ("Friday", D("-2")), ("Saturday", D("0.5")), ("Sunday", None)]


def test_empty_inputs_give_empty_periods():
    assert month_rows({}) == []
    assert [w.summary for w in weekday_rows({})] == [None] * 7


# ---------- Groups ----------

def test_strategies_best_first_with_stable_ties(board):
    assert [(g.label, g.summary.trades, g.summary.total_r) for g in board.strategies] == [
        ("Alpha", 2, D("2.5")), ("Beta", 1, D("-1")), ("Unspecified", 1, D("-1"))]


def test_sessions_in_trading_day_order_with_readable_labels(board):
    assert [(g.key, g.label, g.summary.total_r) for g in board.sessions] == [
        ("ASIA", "Asia", D("-1")), ("LONDON", "London", D("2.5")), ("Unspecified", "Unspecified", D("-1"))]


def test_instruments_compared_in_r_not_money(board):
    assert [(g.label, g.summary.total_r) for g in board.instruments] == [("GBPX", D("0.5")),
                                                                         ("TEST", D("0"))]


def test_small_sample_flag(board):
    assert SMALL_SAMPLE == 10
    assert all(g.small_sample for g in board.instruments)


# ---------- KPI cards ----------

def test_kpis_match_hand_calculation(board):
    kpis = {k.key: (k.value, k.tone) for k in board.kpis}
    assert kpis == {
        "trades": ("4", "neutral"),
        "wins": ("2", "neutral"),
        "losses": ("2", "neutral"),
        "breakevens": ("0", "neutral"),
        "win_rate": ("50.0%", "neutral"),
        "average_r": ("+0.13R", "positive"),          # 0.125 rounds half up
        "expectancy_r": ("+0.13R", "positive"),
        "profit_factor_r": ("1.25", "neutral"),
        "total_r": ("+0.50R", "positive"),
        "max_drawdown_r": ("2.00R", "negative"),
        "longest_win_streak": ("1 trade", "neutral"),
        "longest_loss_streak": ("2 trades", "neutral"),
    }
    assert {k.key: k.note for k in board.kpis}["trades"] == "1 open trade not included"


def test_money_stays_per_currency(board):
    assert money_rows(board.report) == [("GBP", "0.50 GBP", "positive", 1),
                                        ("USD", "0.00 USD", "neutral", 3)]


def test_empty_report_shows_unavailable_not_zero(conn):
    board = build_dashboard(conn, ReportFilters())
    assert not board.has_results
    kpis = {k.key: k.value for k in board.kpis}
    assert kpis["trades"] == "0"
    for key in ("wins", "win_rate", "average_r", "expectancy_r", "profit_factor_r", "total_r",
                "max_drawdown_r", "longest_win_streak", "longest_loss_streak"):
        assert kpis[key] == "n/a", key
    assert board.series == [] and board.months == []
    assert chart_payload(board)["curve"] == []


def test_only_breakeven_trades():
    report = analyse([make_trade(id=1, exit_price=D("100"), closed_at="2026-01-05T11:00:00Z")])
    kpis = {k.key: k.value for k in make_dashboard(report, ReportFilters()).kpis}
    assert kpis["breakevens"] == "1" and kpis["win_rate"] == "0.0%"
    assert kpis["profit_factor_r"] == "n/a" and kpis["total_r"] == "0.00R"


# ---------- Data quality: missing R and unknown exit times ----------

def test_trade_without_usable_r_is_counted_but_not_plotted():
    """A stop on the wrong side cannot reach the database (CHECK constraint),
    but other data sources could produce it, so the engine supports it."""
    report = analyse([make_trade(id=1, closed_at="2026-01-05T11:00:00Z"),
                      make_trade(id=2, stop_loss=D("105"), closed_at="2026-01-06T11:00:00Z")])
    board = make_dashboard(report, ReportFilters())
    assert board.report.overall.trades == 2
    assert [p.trade_id for p in board.series] == [1]
    assert [e.trade_id for e in board.report.r_unavailable] == [2]


def test_legacy_trade_is_placed_at_entry_time_and_flagged(hand_conn):
    hand_conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 3")
    hand_conn.commit()
    board = build_dashboard(hand_conn, ReportFilters())
    # Trade 3 now counts at its ENTRY time, 2025-12-30, so it comes first.
    assert [p.trade_id for p in board.series] == [3, 1, 2, 4]
    legacy = board.series[0]
    assert legacy.close_time_known is False and legacy.realised_at == "2025-12-30T09:00:00Z"
    assert board.report.unknown_close_time_trade_ids == [3]


# ---------- Filters reach the dashboard ----------

def test_filters_change_every_part_of_the_dashboard(hand_conn):
    board = build_dashboard(hand_conn, ReportFilters(strategy="Alpha"))
    assert [p.trade_id for p in board.series] == [1, 4]
    assert [m.key for m in board.months] == ["2025-12", "2026-01", "2026-02", "2026-03"]
    assert {k.key: k.value for k in board.kpis}["total_r"] == "+2.50R"
    assert [g.label for g in board.strategies] == ["Alpha"]


def test_date_filter_on_realised_date(hand_conn):
    board = build_dashboard(hand_conn, ReportFilters(start="2026-01-01", end="2026-01-31"))
    assert [p.trade_id for p in board.series] == [2, 3]


# ---------- Chart payload ----------

def test_payload_has_exact_strings_and_no_floats(board):
    payload = chart_payload(board)

    def walk(value):
        assert not isinstance(value, float), value
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    json.dumps(payload, allow_nan=False)          # raises if NaN/Infinity slipped in


def test_payload_curve_values(board):
    curve = chart_payload(board)["curve"]
    assert curve[0] == {"start": True, "x": board.series[0].x, "cumulative_r": "0",
                        "cumulative_text": "0.00R", "underwater_r": "0", "drawdown_text": "0.00R"}
    assert [(p["trade_id"], p["realised_r"], p["cumulative_r"], p["underwater_r"]) for p in curve[1:]] == [
        (1, "2", "2", "0"), (2, "-1", "1", "-1"), (3, "-1", "0", "-2"), (4, "0.5", "0.5", "-1.5")]
    assert curve[3]["when"] == "02 Jan 2026, 09:00 UTC"
    assert curve[3]["realised_text"] == "-1.00R"


def test_payload_numeric_strings_are_finite_decimals(board):
    payload = chart_payload(board)
    values = [p[k] for p in payload["curve"] for k in ("cumulative_r", "underwater_r")]
    values += [row["total_r"] for key in ("monthly", "weekday", "instruments", "strategies", "sessions")
               for row in payload[key] if row["total_r"] is not None]
    for text in values:
        assert Decimal(text).is_finite(), text


def test_payload_marks_empty_periods_as_null(board):
    monthly = {m["key"]: m for m in chart_payload(board)["monthly"]}
    assert monthly["2026-02"]["total_r"] is None and monthly["2026-02"]["trades"] == 0
    assert monthly["2026-01"]["total_r"] == "-2" and monthly["2026-01"]["total_r_text"] == "-2.00R"


def test_payload_and_tables_agree(board):
    """The chart payload is cut from the same rows the tables render."""
    payload = chart_payload(board)
    for key, rows in (("instruments", board.instruments), ("strategies", board.strategies),
                      ("sessions", board.sessions)):
        assert [(p["label"], p["trades"], Decimal(p["total_r"])) for p in payload[key]] == [
            (r.label, r.summary.trades, r.summary.total_r) for r in rows]


def test_payload_contains_no_free_text_from_trades(hand_conn):
    hand_conn.execute("UPDATE trades SET notes = 'SECRET-NOTE <script>' WHERE id = 1")
    hand_conn.commit()
    text = json.dumps(chart_payload(build_dashboard(hand_conn, ReportFilters())))
    assert "SECRET-NOTE" not in text


# ---------- Sample data and scale ----------

def test_sample_data_series_ends_at_engine_total(seeded_conn):
    board = build_dashboard(seeded_conn, ReportFilters())
    assert board.series[-1].cumulative_r == board.report.overall.total_r
    assert max(p.drawdown_r for p in board.series) == board.report.overall.max_drawdown_r.amount


def test_one_thousand_trades(conn):
    """1,000 synthetic trades: the dashboard still adds up. (Timing is
    measured separately by scripts/benchmark_dashboard.py, not asserted.)"""
    instrument = create_instrument(conn, {"symbol": "BULK", "name": "Bulk", "asset_class": "OTHER",
                                          "price_increment": "0.01", "contract_multiplier": "1",
                                          "quote_currency": "USD"})
    for n in range(1000):
        exit_price = ("102", "99", "100.5", "99")[n % 4]       # +2, -1, +0.5, -1 repeating
        day = 1 + (n // 40)
        create_trade(conn, {"instrument_id": instrument, "direction": "LONG", "status": "CLOSED",
                            "trade_date": f"2026-{1 + (day - 1) // 28:02d}-{1 + (day - 1) % 28:02d}T08:00:00Z",
                            "closed_at": f"2026-{1 + (day - 1) // 28:02d}-{1 + (day - 1) % 28:02d}T{(n % 40) // 4 + 9:02d}:{n % 4:02d}:00Z",
                            "entry_price": "100", "stop_loss": "99", "exit_price": exit_price,
                            "position_size": "1"})
    board = build_dashboard(conn, ReportFilters())
    assert board.report.overall.trades == 1000
    assert len(board.series) == 1000
    assert board.series[-1].cumulative_r == D("125")          # 250 cycles of +0.5R
    assert board.report.overall.total_r == D("125")
    # The HTML table keeps only the most recent rows; the chart keeps all.
    assert SERIES_TABLE_LIMIT == 200
    assert board.series_table == board.series[-200:]
```


## 12. `tests/test_web_analytics.py` (NEW)

```python
"""Phase 6: the /analytics dashboard page (Flask test client, not a browser).

Covers rendering, filters, validation, empty states, legacy data, the chart
payload, agreement with the JSON API, escaping and the Content-Security-
Policy. Real-browser behaviour (Chart.js, layout) is in test_browser.py.
"""

import json
import re
from decimal import Decimal

import pytest

from tradeledger import create_app
from tradeledger.db import get_db, init_schema
from tradeledger.performance.dashboard import build_dashboard, chart_payload
from tradeledger.report_filters import ReportFilters
from tradeledger.repositories.trades import create_trade
from tradeledger.web.analytics import chart_script_sources

CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
CHART_NAMES = ("curve", "drawdown", "monthly", "weekday", "instruments", "strategies", "sessions")


def page(client, query=""):
    response = client.get("/analytics" + (f"?{query}" if query else ""))
    return response, response.get_data(as_text=True)


def kpi(html, key):
    match = re.search(rf'data-kpi="{key}">.*?class="kpi__value">([^<]*)<', html, re.S)
    assert match, key
    return match.group(1)


def payload_of(html):
    match = re.search(r'<script type="application/json" id="dashboard-data">(.*?)</script>', html, re.S)
    return json.loads(match.group(1)) if match else None


# ---------- Rendering with sample data ----------

def test_dashboard_renders_every_section(seeded_client):
    response, html = page(seeded_client)
    assert response.status_code == 200
    for heading in ("Reporting filters", "Summary", "Performance over time", "Breakdowns",
                    "Detailed tables", "About these figures"):
        assert heading in html
    for name in CHART_NAMES:
        assert f'data-chart="{name}"' in html
        assert f'id="chart-{name}-desc"' in html                 # visible text alternative


def test_kpi_cards_show_engine_values(seeded_client):
    _, html = page(seeded_client)
    expected = {"trades": "14", "wins": "7", "losses": "5", "breakevens": "2", "win_rate": "50.0%",
                "average_r": "+0.55R", "expectancy_r": "+0.55R", "profit_factor_r": "2.85",
                "total_r": "+7.69R", "max_drawdown_r": "1.05R",
                "longest_win_streak": "2 trades", "longest_loss_streak": "2 trades"}
    assert {key: kpi(html, key) for key in expected} == expected


def test_money_is_shown_per_currency_never_combined(seeded_client):
    _, html = page(seeded_client)
    assert "798.50 USD" in html and "200.00 GBP" in html
    assert "998.50" not in html                                  # USD + GBP would be meaningless


def test_tables_hold_the_chart_figures(seeded_client):
    _, html = page(seeded_client)
    for table in ("table-series", "table-monthly", "table-weekday", "table-instruments",
                  "table-strategies", "table-sessions"):
        assert f'id="{table}"' in html
    assert html.count("No closed trades") >= 2                   # Saturday and Sunday rows
    assert "Small sample" in html


def test_page_works_without_javascript(seeded_client):
    _, html = page(seeded_client)
    assert "Charts need JavaScript" in html                      # .no-js-only notice
    assert '<form class="card filters dash-filters" method="get"' in html


def test_chart_payload_matches_the_dashboard_layer(app, seeded_client):
    _, html = page(seeded_client, "strategy=Breakout")
    with app.app_context():
        expected = chart_payload(build_dashboard(get_db(), ReportFilters(strategy="Breakout")))
    assert payload_of(html) == expected


def test_dashboard_and_api_agree(seeded_client):
    _, html = page(seeded_client, "session=LONDON")
    api = seeded_client.get("/api/v1/analytics/summary?session=LONDON").get_json()["data"]["overall"]
    curve = payload_of(html)["curve"]
    assert Decimal(curve[-1]["cumulative_r"]) == Decimal(api["total_r"])
    assert kpi(html, "trades") == str(api["trades"])


# ---------- Filters ----------

@pytest.mark.parametrize("query, trades", [
    ("strategy=Breakout", "5"),
    ("session=ASIA", "2"),
    ("session=london", "6"),
    ("instrument_id=1", "3"),
    ("strategy=Breakout&session=LONDON", "3"),
    ("start=2026-03-10&end=2026-03-13", "4"),
    ("instrument_id=&strategy=&session=&start=&end=", "14"),     # "All" everywhere
])
def test_filters_change_the_figures(seeded_client, query, trades):
    response, html = page(seeded_client, query)
    assert response.status_code == 200
    assert kpi(html, "trades") == trades


def test_selected_filters_are_kept_in_the_form(seeded_client):
    _, html = page(seeded_client, "instrument_id=2&strategy=Pullback&session=london&start=2026-03-01&end=2026-03-31")
    assert '<option value="2" selected' in html
    assert '<option value="Pullback" selected' in html
    assert '<option value="LONDON" selected' in html
    assert 'value="2026-03-01"' in html and 'value="2026-03-31"' in html
    assert "Showing results for:" in html


def test_reset_link_only_when_filtering(seeded_client):
    _, plain = page(seeded_client)
    _, filtered = page(seeded_client, "strategy=Breakout")
    assert "Reset filters" not in plain
    assert 'href="/analytics">Reset filters' in filtered


def test_bookmarked_strategy_stays_selectable(seeded_client):
    _, html = page(seeded_client, "strategy=Retired%20setup")
    assert '<option value="Retired setup" selected' in html


@pytest.mark.parametrize("query, field, message", [
    ("instrument_id=999", "instrument_id", "does not match an existing instrument"),
    ("instrument_id=abc", "instrument_id", "must be a whole number of 1 or more"),
    ("session=MOON", "session", "must be one of: ASIA, LONDON, NEW_YORK, OTHER"),
    ("strategy=" + "x" * 51, "strategy", "must be 50 characters or fewer"),
    ("start=2026-02-30", "start", "must be a date in YYYY-MM-DD form"),
    ("start=2026-03-10&end=2026-03-01", "end", "must be on or after the start date"),
])
def test_invalid_filters_give_400_and_no_report(seeded_client, query, field, message):
    response, html = page(seeded_client, query)
    assert response.status_code == 400
    assert "The report was not produced" in html
    assert message in html
    assert f'id="dash-{field}"' in html and 'aria-invalid="true"' in html
    assert 'data-kpi="' not in html and "dashboard-data" not in html     # nothing misleading
    assert CDN not in html


# ---------- Empty and edge cases ----------

def test_empty_database(client):
    response, html = page(client)
    assert response.status_code == 200
    assert "No closed trades yet" in html
    assert kpi(html, "win_rate") == "n/a" and kpi(html, "total_r") == "n/a"
    assert "dashboard-data" not in html and CDN not in html          # nothing to draw
    assert CDN not in response.headers["Content-Security-Policy"]


def test_only_open_trades(app, client):
    with app.app_context():
        conn = get_db()
        conn.execute("INSERT INTO instruments (symbol, name, asset_class, price_increment, contract_multiplier,"
                     " quote_currency) VALUES ('OPN', 'Open only', 'OTHER', '0.01', '1', 'USD')")
        conn.commit()
        create_trade(conn, {"instrument_id": 1, "direction": "LONG", "status": "OPEN",
                            "trade_date": "2026-03-02T08:00:00Z", "entry_price": "100",
                            "stop_loss": "99", "position_size": "1"})
    _, html = page(client)
    assert "1 open trade matches, but open trades have no realised result yet" in html
    assert kpi(html, "trades") == "0"


def test_filters_matching_nothing(seeded_client):
    _, html = page(seeded_client, "start=2030-01-01")
    assert "No closed trades match these filters" in html
    assert "Try a wider date range" in html


def test_instrument_with_only_open_trades(seeded_client):
    """Sample trade 15 (EURUSD, Reversal) is open; nothing else matches."""
    _, html = page(seeded_client, "instrument_id=1&strategy=Reversal")
    assert "1 open trade matches" in html


def test_legacy_trade_is_explained(app, seeded_client):
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 4")
        conn.commit()
    _, html = page(seeded_client)
    assert "Unknown exit times:" in html and "(#4)" in html
    assert "Entry time: exit not recorded" in html
    curve = payload_of(html)["curve"]
    assert [p for p in curve if p.get("trade_id") == 4][0]["close_time_known"] is False


def test_maximum_drawdown_row_is_marked(seeded_client):
    _, html = page(seeded_client)
    row = re.search(r'<tr class="is-max-drawdown">(.*?)</tr>', html, re.S).group(1)
    assert "#9 EURUSD" in row and "Maximum" in row


# ---------- Security ----------

def test_dashboard_csp_allows_only_the_exact_chart_js_file(seeded_client):
    response, html = page(seeded_client)
    csp = response.headers["Content-Security-Policy"]
    assert f"script-src 'self' {CDN};" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "https://cdn.jsdelivr.net;" not in csp and "https://cdn.jsdelivr.net " not in csp
    assert f'<script src="{CDN}" defer crossorigin="anonymous" referrerpolicy="no-referrer">' in html


@pytest.mark.parametrize("url", ["/", "/trades", "/trades/new", "/instruments", "/about",
                                 "/api/v1/analytics/summary"])
def test_other_pages_keep_the_strict_policy(seeded_client, url):
    csp = seeded_client.get(url).headers["Content-Security-Policy"]
    assert "script-src 'self';" in csp and "cdn.jsdelivr.net" not in csp


def test_no_inline_executable_script(seeded_client):
    _, html = page(seeded_client)
    for tag in re.findall(r"<script[^>]*>", html):
        assert "src=" in tag or 'type="application/json"' in tag, tag
    assert " onclick=" not in html and " style=" not in html


def test_self_hosted_chart_js_needs_no_extra_csp_source(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path,
                                           "CHART_JS_URL": "/static/vendor/chartjs/chart.umd.min.js",
                                           "CHART_JS_INTEGRITY": "sha384-abc123"})
    with app.app_context():
        from tradeledger.seed import seed_sample_data
        init_schema(get_db())
        seed_sample_data(get_db())
    response = app.test_client().get("/analytics")
    html = response.get_data(as_text=True)
    assert "script-src 'self';" in response.headers["Content-Security-Policy"]
    assert 'src="/static/vendor/chartjs/chart.umd.min.js"' in html
    assert 'integrity="sha384-abc123"' in html


@pytest.mark.parametrize("url, sources", [
    ("/static/vendor/chartjs/chart.umd.min.js", ()),
    (CDN, (CDN,)),
])
def test_chart_script_sources(url, sources):
    assert chart_script_sources(url) == sources


@pytest.mark.parametrize("url", ["http://cdn.example/chart.js", "//cdn.example/chart.js",
                                 "javascript:alert(1)", "https://cdn.example/chart.js?v=1", "chart.js"])
def test_unsafe_chart_js_urls_are_refused(url):
    with pytest.raises(ValueError):
        chart_script_sources(url)


def test_malicious_strategy_name_is_escaped_everywhere(app, seeded_client):
    evil = "<script>alert(1)</script>"
    with app.app_context():
        get_db().execute("UPDATE trades SET strategy = ? WHERE id IN (1, 2)", (evil,))
        get_db().commit()
    for query in ("", "strategy=" + evil):
        _, html = page(seeded_client, query)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    _, html = page(seeded_client)
    data = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S).group(1)
    assert "<" not in data                                        # tojson escapes it as \\u003c
    assert evil in json.dumps(payload_of(html))                   # ...and it round-trips intact


def test_filter_injection_is_just_text(seeded_client):
    response, html = page(seeded_client, "strategy=%27%20OR%201%3D1%20--")
    assert response.status_code == 200
    assert "No closed trades match these filters" in html


def test_dashboard_is_disabled_in_production(db_path):
    app = create_app("production", overrides={"SECRET_KEY": "real-secret", "DATABASE": db_path})
    with app.app_context():
        init_schema(get_db())
    response = app.test_client().get("/analytics")
    assert response.status_code == 403
    assert "cdn.jsdelivr.net" not in response.headers["Content-Security-Policy"]


def test_post_is_not_allowed(seeded_client):
    token_page = seeded_client.get("/trades/new").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', token_page).group(1)
    assert seeded_client.post("/analytics", data={"csrf_token": token}).status_code == 405


def test_navigation_marks_analytics_current(seeded_client):
    _, html = page(seeded_client)
    marker = html.index('aria-current="page"')
    assert "Analytics" in html[marker:marker + 120]
```


## 13. `tests/test_javascript.py` (NEW)

```python
"""Runs the JavaScript unit tests (tests/js/*.test.js) with Node.js.

Node's built-in test runner needs no npm packages. If Node.js is not
installed, this test is skipped and says so; the Python suite is unaffected.
Run them directly with:   node --test tests/js/*.test.js
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

JS_TESTS = Path(__file__).parent / "js"


def find_node():
    return os.environ.get("NODE") or shutil.which("node") or (
        "/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)


def test_javascript_unit_tests_pass():
    node = find_node()
    if node is None:
        pytest.skip("Node.js is not installed")
    files = sorted(str(path) for path in JS_TESTS.glob("*.test.js"))
    assert files, "no JavaScript test files found"
    result = subprocess.run([node, "--test", *files], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "# fail 0" in result.stdout
```


## 14. `tests/js/analytics.test.js` (NEW)

```javascript
// Unit tests for tradeledger/static/js/analytics.js (Node's built-in test
// runner, no packages needed):   node --test tests/js/*.test.js
//
// They check the PURE parts: number conversion, chart configurations and
// tooltip text. The Playwright tests check the page wiring with a stand-in
// for Chart.js; Chart.js's own drawing is checked by hand (see the guide).
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const charts = require(path.join(__dirname, "..", "..", "tradeledger", "static", "js", "analytics.js"));

const THEME = {
    text: "#e8eef8", muted: "#a3b1c7", grid: "#24334f", zero: "#8796af",
    accent: "#6aa8ff", positive: "#4cd79a", negative: "#ff8a8a", font: "system-ui"
};

// A payload shaped exactly like performance/dashboard.chart_payload().
const PAYLOAD = {
    curve: [
        {start: true, x: 1000, cumulative_r: "0", cumulative_text: "0.00R", underwater_r: "0", drawdown_text: "0.00R"},
        {start: false, trade_id: 1, symbol: "TEST", x: 1000, when: "31 Dec 2025, 23:30 UTC", close_time_known: true,
            realised_r: "2", realised_text: "+2.00R", cumulative_r: "2", cumulative_text: "+2.00R",
            underwater_r: "0", drawdown_text: "0.00R"},
        {start: false, trade_id: 3, symbol: "TEST", x: 2000, when: "02 Jan 2026, 09:00 UTC", close_time_known: false,
            realised_r: "-2", realised_text: "-2.00R", cumulative_r: "0", cumulative_text: "0.00R",
            underwater_r: "-2", drawdown_text: "2.00R"}
    ],
    max_drawdown: {amount: "2", text: "2.00R", peak_trade_id: 1, trough_trade_id: 3},
    monthly: [
        {key: "2025-12", label: "Dec 2025", trades: 1, wins: 1, losses: 0, total_r: "2", total_r_text: "+2.00R",
            win_rate_text: "100.0%", average_r_text: "+2.00R", expectancy_text: "+2.00R"},
        {key: "2026-01", label: "Jan 2026", trades: 0, wins: 0, losses: 0, total_r: null,
            total_r_text: "No closed trades", win_rate_text: "n/a", average_r_text: "n/a", expectancy_text: "n/a"}
    ],
    weekday: [],
    instruments: [
        {label: "TEST", small_sample: true, trades: 2, wins: 1, losses: 1, total_r: "0.333333333333333333333333",
            total_r_text: "+0.33R", win_rate_text: "50.0%", average_r_text: "+0.17R", expectancy_text: "+0.17R"},
        {label: "LOSS", small_sample: false, trades: 12, wins: 2, losses: 10, total_r: "-3.5",
            total_r_text: "-3.50R", win_rate_text: "16.7%", average_r_text: "-0.29R", expectancy_text: "-0.29R"}
    ],
    strategies: [],
    sessions: []
};

test("toNumber converts exact strings and refuses unsafe values", () => {
    assert.equal(charts.toNumber("7.68817307692307692307692"), 7.688173076923077);
    assert.equal(charts.toNumber("-1.5"), -1.5);
    assert.equal(charts.toNumber("0"), 0);
    for (const bad of [null, undefined, "", "abc", "Infinity", "-Infinity", "NaN", "1e400"]) {
        assert.equal(charts.toNumber(bad), null, String(bad));
    }
});

test("hexToRgba builds translucent colours and leaves other values alone", () => {
    assert.equal(charts.hexToRgba("#ff8a8a", 0.18), "rgba(255, 138, 138, 0.18)");
    assert.equal(charts.hexToRgba("red", 0.5), "red");
});

test("formatAxisDate shows the UTC date", () => {
    assert.equal(charts.formatAxisDate(Date.UTC(2026, 0, 2, 23, 59)), "2026-01-02");
    assert.equal(charts.formatAxisDate(NaN), "");
});

test("buildConfigs returns all seven charts", () => {
    const configs = charts.buildConfigs(PAYLOAD, THEME);
    assert.deepEqual(Object.keys(configs).sort(),
        ["curve", "drawdown", "instruments", "monthly", "sessions", "strategies", "weekday"]);
    for (const config of Object.values(configs)) {
        assert.equal(config.options.animation, false);
        assert.equal(config.options.responsive, true);
        assert.equal(config.options.maintainAspectRatio, false);
    }
});

test("cumulative R chart plots the exact values on a time axis", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).curve;
    assert.equal(config.type, "line");
    assert.equal(config.options.scales.x.type, "linear");
    assert.deepEqual(config.data.datasets[0].data, [{x: 1000, y: 0}, {x: 1000, y: 2}, {x: 2000, y: 0}]);
    assert.equal(config.options.scales.x.ticks.callback(Date.UTC(2026, 2, 7)), "2026-03-07");
});

test("cumulative R tooltip uses the server's text, including the legacy warning", () => {
    const callbacks = charts.buildConfigs(PAYLOAD, THEME).curve.options.plugins.tooltip.callbacks;
    assert.equal(callbacks.title([{dataIndex: 0}]), "Before the first trade");
    assert.equal(callbacks.title([{dataIndex: 2}]), "02 Jan 2026, 09:00 UTC");
    assert.deepEqual(callbacks.label({dataIndex: 1}),
        ["Trade #1 · TEST", "Trade R: +2.00R", "Cumulative R: +2.00R"]);
    assert.deepEqual(callbacks.label({dataIndex: 2}),
        ["Trade #3 · TEST", "Trade R: -2.00R", "Cumulative R: 0.00R",
            "Exit time not recorded: placed at entry time"]);
});

test("drawdown chart plots below zero and marks the maximum", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).drawdown;
    const dataset = config.data.datasets[0];
    assert.deepEqual(dataset.data.map((p) => p.y), [0, 0, -2]);
    assert.equal(dataset.fill, "origin");
    assert.deepEqual(dataset.pointStyle, ["circle", "circle", "triangle"]);
    assert.equal(dataset.pointRadius[2], 6);
    const lines = config.options.plugins.tooltip.callbacks.label({dataIndex: 2});
    assert.ok(lines.includes("Maximum drawdown: 2.00R"));
    assert.equal(lines[0], "Drawdown: 2.00R below the running peak");
});

test("zero line is drawn stronger than other grid lines", () => {
    const grid = charts.buildConfigs(PAYLOAD, THEME).monthly.options.scales.y.grid;
    assert.equal(grid.color({tick: {value: 0}}), THEME.zero);
    assert.equal(grid.color({tick: {value: 1}}), THEME.grid);
    assert.equal(grid.lineWidth({tick: {value: 0}}), 2);
});

test("bar chart leaves empty periods blank instead of drawing zero", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).monthly;
    assert.deepEqual(config.data.labels, ["Dec 2025", "Jan 2026"]);
    assert.deepEqual(config.data.datasets[0].data, [2, null]);
    assert.deepEqual(config.options.plugins.tooltip.callbacks.label({dataIndex: 1}), ["No closed trades"]);
});

test("group charts are horizontal, coloured by sign and flag small samples", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).instruments;
    assert.equal(config.options.indexAxis, "y");
    assert.deepEqual(config.data.labels, ["TEST *", "LOSS"]);
    assert.deepEqual(config.data.datasets[0].backgroundColor, [THEME.positive, THEME.negative]);
    const lines = config.options.plugins.tooltip.callbacks.label({dataIndex: 0});
    assert.deepEqual(lines, ["Total R: +0.33R", "Closed trades: 2 (won 1, lost 1)", "Win rate: 50.0%",
        "Average R: +0.17R", "Expectancy: +0.17R", "Small sample: treat with caution"]);
});

test("init without Chart.js shows the fallback message on every chart", () => {
    const made = [];
    const canvas = (name) => {
        const note = {hidden: true, textContent: ""};
        const box = {classList: {add: (c) => made.push(c)}, querySelector: () => note};
        return {name, note, hidden: false, getAttribute: () => name, closest: () => box};
    };
    const canvases = [canvas("curve"), canvas("monthly")];
    const doc = {
        getElementById: () => ({textContent: JSON.stringify(PAYLOAD)}),
        querySelectorAll: () => canvases
    };
    assert.deepEqual(charts.init(doc, undefined), []);
    for (const c of canvases) {
        assert.equal(c.hidden, true);
        assert.equal(c.note.hidden, false);
        assert.match(c.note.textContent, /could not be loaded/);
    }
    assert.deepEqual(made, ["is-unavailable", "is-unavailable"]);
});
```


## 15. `docs/THIRD_PARTY_NOTICES.md` (NEW)

````markdown
# Third-party software

## Chart.js 4.5.1

| | |
|---|---|
| What | JavaScript charting library used by the `/analytics` dashboard |
| Version | 4.5.1, pinned in `tradeledger/config.py` (`CHART_JS_VERSION`, `CHART_JS_URL`) |
| Licence | MIT, copyright the Chart.js contributors (https://www.chartjs.org) |
| How it is loaded | From the jsDelivr CDN: `https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js` |
| Copied into this repository? | **No.** The browser downloads it from the CDN. |

### Why a CDN, and what that means

The project brief preferred a locally hosted copy. The build environment could not download files, and the file could not be supplied by hand, so the dashboard loads Chart.js from the CDN for now. Consequences:

1. **Internet access is needed in the browser** to see the charts. Offline, the dashboard still shows every figure in its KPI cards, chart descriptions and tables, and each chart area says why it is empty.
2. **The Content-Security-Policy is widened on one page only.** The `/analytics` response allows that one exact file URL; every other page keeps `script-src 'self'`. `unsafe-inline` and `unsafe-eval` are never allowed.
3. **Supply-chain trust.** The browser runs whatever the CDN serves at that URL. The version is pinned, and you can add a Subresource Integrity hash (below) so the browser rejects a changed file. No hash is configured by default, because none could be computed from a verified copy of the file in the build environment.
4. **Privacy.** The CDN sees the visitor's IP address when the file is fetched. `referrerpolicy="no-referrer"` stops it from seeing which page asked for it.

### Adding a Subresource Integrity hash (recommended)

On a machine with internet access (macOS/Linux):

```bash
curl -sL https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js \
  | openssl dgst -sha384 -binary | openssl base64 -A
```

Then set, for example in `.env`:

```
TRADELEDGER_CHART_JS_INTEGRITY=sha384-<the output of the command above>
```

The page will then include `integrity="sha384-..."`, and the browser refuses the file if a single byte differs.

### Switching to a locally hosted copy

1. Download the file and its licence from the same release:
   - `https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js`
   - `https://cdn.jsdelivr.net/npm/chart.js@4.5.1/LICENSE.md`
2. Save both in `tradeledger/static/vendor/chartjs/` (keep the file's header comment unchanged).
3. Set `TRADELEDGER_CHART_JS_URL=/static/vendor/chartjs/chart.umd.min.js` (for example in `.env`).
4. Restart Flask and open `/analytics`. In the browser's developer tools, the Network tab should show the file coming from your own server, and the response's `Content-Security-Policy` header should contain `script-src 'self';` with no CDN address. (TradeLedger allows only same-site paths or `https://` URLs here, and refuses anything else at request time.)
5. Optionally set `TRADELEDGER_CHART_JS_INTEGRITY` as above.

Because the MIT licence requires the copyright notice to accompany copies, a self-hosted copy must keep `LICENSE.md` next to it.

### Updating the version safely

1. Read the Chart.js release notes for every version between 4.5.1 and the new one.
2. Change the version in `CHART_JS_VERSION` and `CHART_JS_URL` (or the self-hosted file), and recompute the integrity hash.
3. Run `pytest`, including the browser tests, then open `/analytics` and check every chart and tooltip by hand. The automated browser tests use a stand-in for Chart.js, so they cannot catch drawing changes in a new version.
````


## 16. `tradeledger/config.py` (MODIFIED)

```python
"""Application configuration.

Settings are read from environment variables so that secrets never live in
source code. Each class represents one environment the app can run in.
"""

import os


def env_flag(name):
    """True only when the environment variable is exactly "1"."""
    return os.environ.get(name) == "1"


class Config:
    """Settings shared by every environment."""

    APP_NAME = "TradeLedger"
    APP_VERSION = "0.6.0"

    # Read from the environment. Never hard-code a real key in this file.
    SECRET_KEY = os.environ.get("SECRET_KEY")

    TESTING = False

    # When True, the app refuses to start without a SECRET_KEY.
    REQUIRE_SECRET_KEY = True

    # Path to the SQLite database file. If not set, create_app() uses
    # instance/tradeledger.db inside the project folder.
    DATABASE = os.environ.get("DATABASE_PATH")

    # Largest request body accepted, in bytes. Flask answers 413 above this.
    # A trade is well under 2 KB, so 64 KB is generous.
    MAX_CONTENT_LENGTH = 64 * 1024

    # The JSON API has NO authentication yet. It is enabled for local
    # development and tests, and disabled by default in production (see
    # ProductionConfig). Pagination never returns more than API_MAX_PER_PAGE.
    API_ENABLED = True
    API_MAX_PER_PAGE = 100

    # The trade-management website (journal, forms, instruments) has no
    # login either. Same rule as the API: on for local use, off by default
    # in production (see ProductionConfig).
    JOURNAL_ENABLED = True
    JOURNAL_PER_PAGE = 20

    # Session cookie (holds only the CSRF token). HttpOnly stops JavaScript
    # reading it; SameSite=Lax stops browsers sending it with cross-site
    # form posts, a second layer of CSRF defence.
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Chart.js for the /analytics dashboard (Phase 6).
    # Default: version 4.5.1, pinned, from the jsDelivr CDN. This needs
    # internet access in the BROWSER (not on the server). Only the
    # dashboard's Content-Security-Policy allows this exact URL.
    # To host it yourself instead (see docs/THIRD_PARTY_NOTICES.md), download the
    # file into tradeledger/static/vendor/chartjs/ and set
    #   TRADELEDGER_CHART_JS_URL=/static/vendor/chartjs/chart.umd.min.js
    # TRADELEDGER_CHART_JS_INTEGRITY optionally holds the file's
    # Subresource Integrity hash ("sha384-..."), which makes the browser
    # refuse a file that has been altered. It is not set by default because
    # it could not be computed from a verified copy of the file.
    CHART_JS_VERSION = "4.5.1"
    CHART_JS_URL = os.environ.get(
        "TRADELEDGER_CHART_JS_URL",
        "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js")
    CHART_JS_INTEGRITY = os.environ.get("TRADELEDGER_CHART_JS_INTEGRITY") or None


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

    # Safe default: with no authentication, the API (which exposes and
    # changes trading data) answers 403 in production. Setting
    # TRADELEDGER_ENABLE_UNAUTHENTICATED_API=1 overrides this and logs a
    # warning; it is only acceptable behind your own access control (for
    # example a private network). Real authentication is a later phase.
    API_ENABLED = env_flag("TRADELEDGER_ENABLE_UNAUTHENTICATED_API")

    # The journal website is disabled (403) in production for the same
    # reason. TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL=1 overrides it.
    JOURNAL_ENABLED = env_flag("TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL")

    # Only send the session cookie over HTTPS.
    SESSION_COOKIE_SECURE = True


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
```


## 17. `tradeledger/__init__.py` (MODIFIED)

```python
"""TradeLedger application package.

Contains the application factory, create_app(). Flask's command-line tool
finds this function automatically when you run:  flask --app tradeledger run
"""

import logging
import os
import secrets

from flask import Flask, g, render_template
from werkzeug.exceptions import HTTPException

from .config import CONFIG_BY_NAME


def create_app(config_name=None, overrides=None):
    """Build and return a configured Flask application.

    config_name: "development", "testing" or "production". If omitted, it is
    read from the TRADELEDGER_CONFIG environment variable, defaulting to
    "development".

    overrides: optional dict of settings applied last. Tests use this to
    point the app at a temporary database, e.g. {"DATABASE": "/tmp/x.db"}.
    """
    if config_name is None:
        config_name = os.environ.get("TRADELEDGER_CONFIG", "development")

    if config_name not in CONFIG_BY_NAME:
        valid = ", ".join(sorted(CONFIG_BY_NAME))
        raise ValueError(f"Unknown config '{config_name}'. Choose one of: {valid}")

    app = Flask(__name__)
    app.config.from_object(CONFIG_BY_NAME[config_name])
    if overrides:
        app.config.update(overrides)
    app.config["CONFIG_NAME"] = config_name

    _configure_logging(app)
    _ensure_secret_key(app)
    _warn_if_api_exposed(app)
    _configure_database(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_security_headers(app)

    app.logger.info("TradeLedger started with '%s' configuration", config_name)
    return app


def _configure_logging(app):
    """Send INFO-level messages and above to the terminal."""
    app.logger.setLevel(logging.INFO)


def _ensure_secret_key(app):
    """Make sure a SECRET_KEY exists, or fail loudly where one is required."""
    if app.config.get("SECRET_KEY"):
        return

    if app.config["REQUIRE_SECRET_KEY"]:
        raise RuntimeError(
            "SECRET_KEY is not set. Set the SECRET_KEY environment variable "
            "before running TradeLedger in this environment."
        )

    # Development only: a random key that changes on every restart.
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.logger.warning(
        "SECRET_KEY not set - using a temporary development key. "
        "Set SECRET_KEY in your .env file to silence this warning."
    )


def _warn_if_api_exposed(app):
    """Make an unauthenticated API in production impossible to miss."""
    if app.config["CONFIG_NAME"] == "production" and app.config["API_ENABLED"]:
        app.logger.warning(
            "The unauthenticated JSON API is ENABLED in production. Anyone who can reach "
            "this server can read and change trading data. Only do this behind your own "
            "access control."
        )


def _configure_database(app):
    """Decide where the SQLite file lives and register database hooks."""
    if not app.config.get("DATABASE"):
        if app.config["TESTING"]:
            raise RuntimeError(
                "The testing configuration needs an explicit DATABASE path "
                "so tests never touch development data."
            )
        os.makedirs(app.instance_path, exist_ok=True)
        app.config["DATABASE"] = os.path.join(app.instance_path, "tradeledger.db")

    from . import cli, db

    db.init_app(app)
    cli.init_app(app)


def _register_blueprints(app):
    """Attach groups of routes to the app: the website and the JSON API."""
    from .api import api_bp
    from .web import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)


def _register_error_handlers(app):
    """Friendly errors instead of Flask's defaults or stack traces.

    Website URLs get HTML pages (Phase 1). URLs under /api/ get the JSON
    error format, including for errors raised before any API code runs:
    unknown URL (404), wrong method (405) and oversized body (413).
    """
    from .api.errors import HTTP_ERRORS, error_response, is_api_request

    @app.errorhandler(404)
    def not_found(error):
        if is_api_request():
            return error_response(404, *HTTP_ERRORS[404])
        return render_template("errors/404.html"), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        if is_api_request():
            response, status = error_response(405, *HTTP_ERRORS[405])
            response.headers["Allow"] = ", ".join(error.valid_methods or [])
            return response, status
        return error  # website: Flask's standard 405 response

    @app.errorhandler(413)
    def too_large(error):
        if is_api_request():
            return error_response(413, *HTTP_ERRORS[413])
        return error

    @app.errorhandler(500)
    def internal_error(error):
        # The full traceback goes to the server log only, never to the client.
        app.logger.exception("Unhandled server error")
        if is_api_request():
            return error_response(500, *HTTP_ERRORS[500])
        return render_template("errors/500.html"), 500

    @app.errorhandler(HTTPException)
    def other_http_error(error):
        if is_api_request():
            return error_response(error.code, "http_error", error.name)
        return error


# Content Security Policy: the browser may only load scripts, styles and
# images from this site, and forms may only post back to it. Templates
# therefore contain no inline <script> or style="" attributes, which means
# even an HTML-injection bug could not run injected JavaScript.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
)


def build_csp(extra_script_sources=()):
    """The policy above, optionally allowing extra script URLs.

    Phase 6: only the /analytics response passes an extra source, the
    exact Chart.js file URL (a full path, not the whole CDN host), so no
    other page can load anything from the CDN, and 'unsafe-inline' and
    'unsafe-eval' are never allowed anywhere.
    """
    if not extra_script_sources:
        return CONTENT_SECURITY_POLICY
    scripts = " ".join(("'self'", *extra_script_sources))
    return CONTENT_SECURITY_POLICY.replace("script-src 'self'", f"script-src {scripts}", 1)


def _register_security_headers(app):
    """Add protective HTTP headers to every response."""

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = build_csp(g.get("csp_script_sources", ()))
        return response
```


## 18. `tradeledger/repositories/trades.py` (MODIFIED)

```python
"""Trade repository.

Return values
  create_trade   -> new id (int)
  get_trade      -> dict (includes the instrument's symbol, quote_currency
                    and contract_multiplier), or None
  list_trades    -> list of dicts, newest trade_date first (empty list if none)
  count_trades   -> number of trades matching the same filters as list_trades
  list_strategies-> sorted list of distinct strategy labels in use
  update_trade   -> True if a trade was updated, False if the id does not exist
  delete_trade   -> True if a trade was deleted, False if the id does not exist

Errors
  ValidationError  invalid input, including an unknown instrument_id
                   (nothing is written)

Numeric fields come back as decimal.Decimal (or None), never float.

Dates
  trade_date  entry time (always present)
  closed_at   exit time; None for OPEN trades and for legacy CLOSED trades
              recorded before Phase 4, whose exit time is unknown
"""

import sqlite3
from decimal import Decimal

from ..validation import SESSIONS, ValidationError, date_range_bounds, validate_trade

_DECIMAL_FIELDS = (
    "entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees",
    "contract_multiplier",
)

# The ONLY columns a caller can write. Anything else in the input is ignored
# by validate_trade() and never reaches SQL (protection against mass assignment).
_WRITE_FIELDS = (
    "instrument_id", "trade_date", "closed_at", "direction", "entry_price", "stop_loss",
    "take_profit", "exit_price", "position_size", "fees", "status",
    "strategy", "session", "notes",
)

_SELECT = """
    SELECT t.id, t.instrument_id, i.symbol, i.quote_currency, i.contract_multiplier,
           t.trade_date, t.closed_at, t.direction,
           t.entry_price, t.stop_loss, t.take_profit, t.exit_price,
           t.position_size, t.fees, t.status, t.strategy, t.session,
           t.notes, t.created_at, t.updated_at
    FROM trades AS t
    JOIN instruments AS i ON i.id = t.instrument_id
"""

# Which timestamp a date filter applies to. Both are fixed SQL text written
# here, never user input.
#   entry     the trade's entry time
#   realised  when the result was realised: the exit time if recorded,
#             otherwise (legacy trades) the entry time
_DATE_COLUMNS = {
    "entry": "t.trade_date",
    "realised": "COALESCE(t.closed_at, t.trade_date)",
}


def _to_dict(row):
    if row is None:
        return None
    record = dict(row)
    for field in _DECIMAL_FIELDS:
        if record[field] is not None:
            record[field] = Decimal(record[field])
    return record


def _raise_friendly(exc):
    """Translate SQLite integrity errors into ValidationErrors where we can."""
    message = str(exc)
    if "FOREIGN KEY" in message:
        raise ValidationError({"instrument_id": "does not match an existing instrument"}) from exc
    if "closed_at_rules" in message:
        raise ValidationError({"closed_at": "is not consistent with the trade's status or entry time"}) from exc
    raise exc


MAX_SEARCH_LENGTH = 100


def _like_pattern(text):
    """Turn user text into a LIKE pattern that matches it literally.
    % and _ are LIKE wildcards, so they (and the escape character itself)
    are escaped; otherwise searching "50%" would match everything."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(instrument_id, start, end, status, date_field, strategy=None, search=None, session=None):
    """Build a WHERE clause from fixed fragments plus a parameter list.
    User values never become part of the SQL text; they go in `params`.

    strategy  exact match on the strategy label
    session   exact match on the trading session (ASIA, LONDON, NEW_YORK, OTHER)
    search    case-insensitive substring match on the instrument symbol,
              the strategy or the notes, across the WHOLE table (not just
              one page). SQLite's LIKE ignores case for ASCII letters only.
    """
    if date_field not in _DATE_COLUMNS:
        raise ValueError(f"date_field must be one of {sorted(_DATE_COLUMNS)}")
    date_column = _DATE_COLUMNS[date_field]
    lower, upper = date_range_bounds(start, end)

    conditions, params = [], []
    if instrument_id is not None:
        conditions.append("t.instrument_id = ?")
        params.append(instrument_id)
    if lower is not None:
        conditions.append(f"{date_column} >= ?")
        params.append(lower)
    if upper is not None:
        conditions.append(f"{date_column} < ?")
        params.append(upper)
    if status is not None:
        if status not in ("OPEN", "CLOSED"):
            raise ValidationError({"status": "must be one of: OPEN, CLOSED"})
        conditions.append("t.status = ?")
        params.append(status)
    if strategy is not None:
        conditions.append("t.strategy = ?")
        params.append(strategy)
    if session is not None:
        if session not in SESSIONS:
            raise ValidationError({"session": f"must be one of: {', '.join(SESSIONS)}"})
        conditions.append("t.session = ?")
        params.append(session)
    if search is not None:
        if not isinstance(search, str) or not search.strip():
            raise ValidationError({"q": "must not be empty"})
        if len(search) > MAX_SEARCH_LENGTH:
            raise ValidationError({"q": f"must be {MAX_SEARCH_LENGTH} characters or fewer"})
        pattern = _like_pattern(search.strip())
        conditions.append(
            "(i.symbol LIKE ? ESCAPE '\\' OR t.strategy LIKE ? ESCAPE '\\' OR t.notes LIKE ? ESCAPE '\\')"
        )
        params.extend([pattern, pattern, pattern])
    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    return where, params


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
        _raise_friendly(exc)
    return cursor.lastrowid


def get_trade(conn, trade_id):
    row = conn.execute(_SELECT + " WHERE t.id = ?", (trade_id,)).fetchone()
    return _to_dict(row)


def list_trades(conn, *, instrument_id=None, start=None, end=None, status=None,
                date_field="entry", strategy=None, search=None, session=None, limit=None, offset=0):
    """List trades, optionally filtered and paginated.

    instrument_id  only this instrument
    start, end     inclusive dates 'YYYY-MM-DD' (UTC)
    date_field     'entry' (trade_date, the default) or 'realised'
    status         'OPEN' or 'CLOSED'
    strategy       exact strategy label
    session        exact session (ASIA, LONDON, NEW_YORK, OTHER)
    search         text found in the symbol, strategy or notes
    limit, offset  pagination; limit=None returns every match

    Order is always newest trade_date first, then highest id first, so
    pages never overlap or skip rows between requests.
    """
    where, params = _where(instrument_id, start, end, status, date_field, strategy, search, session)
    sql = _SELECT + where + " ORDER BY t.trade_date DESC, t.id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, int(limit), int(offset)]
    return [_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def count_trades(conn, *, instrument_id=None, start=None, end=None, status=None,
                 date_field="entry", strategy=None, search=None, session=None):
    where, params = _where(instrument_id, start, end, status, date_field, strategy, search, session)
    # The JOIN is needed because search can match the instrument symbol.
    sql = "SELECT COUNT(*) FROM trades AS t JOIN instruments AS i ON i.id = t.instrument_id" + where
    return conn.execute(sql, params).fetchone()[0]


def list_strategies(conn):
    rows = conn.execute(
        "SELECT DISTINCT strategy FROM trades WHERE strategy IS NOT NULL ORDER BY strategy"
    ).fetchall()
    return [row["strategy"] for row in rows]


def update_trade(conn, trade_id, data):
    """Replace every editable field of a trade with validated new values.

    A legacy CLOSED trade (recorded before Phase 4, closed_at unknown) may be
    saved again without a closed_at, so editing it never forces anyone to
    invent an exit time. Every other CLOSED trade must have one.
    """
    existing = conn.execute(
        "SELECT status, closed_at FROM trades WHERE id = ?", (trade_id,)
    ).fetchone()
    if existing is None:
        return False
    is_legacy = existing["status"] == "CLOSED" and existing["closed_at"] is None
    clean = validate_trade(data, require_closed_at=not is_legacy)

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
        _raise_friendly(exc)
    return cursor.rowcount == 1


def delete_trade(conn, trade_id):
    with conn:
        cursor = conn.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    return cursor.rowcount == 1
```


## 19. `tradeledger/performance/report.py` (MODIFIED)

```python
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
    realised_at: str
    close_time_known: bool
    symbol: str
    realised_r: Decimal
    cumulative_r: Decimal


@dataclass(frozen=True)
class PerformanceReport:
    overall: Summary
    open_trades: int
    excluded: List[ExcludedTrade]
    r_unavailable: List[ExcludedTrade]      # closed trades counted, but with no R
    unknown_close_time_trade_ids: List[int]  # legacy: ordered by entry time
    by_instrument: Dict[str, Summary]
    by_strategy: Dict[str, Summary]
    by_session: Dict[str, Summary]
    by_weekday: Dict[str, Summary]
    by_date: Dict[str, Summary]
    by_month: Dict[str, Summary]
    cumulative_r: List[CurvePoint]


def _weekday(result):
    moment = datetime.strptime(result.realised_at, "%Y-%m-%dT%H:%M:%SZ")
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
        CurvePoint(r.trade_id, r.trade_date, r.realised_at, r.close_time_known,
                   r.symbol, r.realised_r, total)
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
        unknown_close_time_trade_ids=[
            r.trade_id for r in chronological(results) if not r.close_time_known
        ],
        by_instrument=group_summaries(results, lambda r: r.symbol),
        by_strategy=group_summaries(results, lambda r: r.strategy or UNSPECIFIED),
        by_session=group_summaries(results, lambda r: r.session or UNSPECIFIED),
        by_weekday=group_summaries(results, _weekday, order=WEEKDAYS),
        by_date=group_summaries(results, lambda r: r.realised_at[:10]),
        by_month=group_summaries(results, lambda r: r.realised_at[:7]),
        cumulative_r=cumulative_r_curve(results),
    )


def build_report(conn, *, instrument_id=None, start=None, end=None,
                 strategy=None, session=None) -> PerformanceReport:
    """Load trades with the repository filters and analyse them.

    start/end are inclusive UTC dates applied to the realised date.
    strategy and session are exact matches (Phase 6). Every filter is a
    parameterised SQL condition in the repository, so filtering happens in
    the database and only matching trades are loaded.
    """
    return analyse(list_trades(conn, instrument_id=instrument_id, start=start, end=end,
                               strategy=strategy, session=session, date_field="realised"))
```


## 20. `tradeledger/api/analytics.py` (MODIFIED)

```python
"""Analytics endpoints: thin wrappers around the Phase 3 engine.

Every endpoint accepts the same optional filters, defined once in
report_filters.py and shared with the /analytics dashboard:
  instrument_id   only this instrument
  strategy        exact strategy label (Phase 6)
  session         ASIA, LONDON, NEW_YORK or OTHER (Phase 6)
  start, end      inclusive UTC dates (YYYY-MM-DD) on the REALISED date:
                  closed_at, or trade_date for legacy trades with no exit time

No formula lives here. Each handler calls build_report() and returns one
part of the PerformanceReport, so the API, the CLI report and the /analytics
dashboard always show the same numbers.
"""

from flask import jsonify

from ..db import get_db
from ..performance import build_report
from ..performance.formatting import to_jsonable
from ..report_filters import FILTER_NAMES, parse_report_filters
from ..validation import ValidationError
from . import api_bp
from .http import query_args
from .serialise import groups_json, summary_json

FILTERS = FILTER_NAMES

NOTES = [
    "Decimal values are strings holding the exact value; null means not calculable.",
    "Rates (win_rate, loss_rate, breakeven_rate) are fractions from 0 to 1.",
    "R values are currency-neutral. Money is reported per quote currency and never summed across currencies.",
    "Open trades are excluded from realised metrics.",
    "Ordering and date filters use the realised time: closed_at, or trade_date for legacy trades "
    "with no recorded exit time (listed in unknown_close_time_trade_ids).",
]


def _report_and_meta():
    conn = get_db()
    filters, errors = parse_report_filters(query_args(FILTERS), conn, blank_means_unset=False)
    if errors:
        raise ValidationError(errors)
    return build_report(conn, **filters.as_kwargs()), {"filters": filters.as_kwargs(), "notes": NOTES}


@api_bp.get("/analytics/summary")
def analytics_summary():
    report, meta = _report_and_meta()
    return jsonify(
        data={
            "overall": summary_json(report.overall),
            "open_trades": report.open_trades,
            "excluded": to_jsonable(report.excluded),
            "r_unavailable": to_jsonable(report.r_unavailable),
            "unknown_close_time_trade_ids": report.unknown_close_time_trade_ids,
        },
        meta=meta,
    )


def _grouped(attribute):
    report, meta = _report_and_meta()
    return jsonify(data=groups_json(getattr(report, attribute)), meta=meta)


@api_bp.get("/analytics/instruments")
def analytics_instruments():
    return _grouped("by_instrument")


@api_bp.get("/analytics/strategies")
def analytics_strategies():
    return _grouped("by_strategy")


@api_bp.get("/analytics/sessions")
def analytics_sessions():
    return _grouped("by_session")


@api_bp.get("/analytics/weekday")
def analytics_weekday():
    return _grouped("by_weekday")


@api_bp.get("/analytics/monthly")
def analytics_monthly():
    return _grouped("by_month")


@api_bp.get("/analytics/curve")
def analytics_curve():
    report, meta = _report_and_meta()
    meta = {**meta, "label": "Cumulative realised R. This is NOT an account equity curve."}
    return jsonify(data=to_jsonable(report.cumulative_r), meta=meta)
```


## 21. `tradeledger/api/openapi.py` (MODIFIED)

```python
"""OpenAPI 3.1 specification for the TradeLedger API.

Built as a Python dict (no extra library) and served at
GET /api/v1/openapi.json. `flask --app tradeledger openapi` writes the same
document to docs/openapi.json.

The tests check that:
  * every API route and method exists in this document, and vice versa;
  * real responses validate against the schemas here (with
    additionalProperties: false, so an undocumented field fails the test).
"""

from flask import current_app, jsonify

from . import api_bp

# ---------- Reusable schema fragments ----------

DECIMAL_PATTERN = r"^-?[0-9]+(\.[0-9]+)?$"
TIMESTAMP_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"


def decimal(description, nullable=False, example=None):
    schema = {
        "type": ["string", "null"] if nullable else "string",
        "pattern": DECIMAL_PATTERN,
        "description": description + " Exact decimal as a string (never a float).",
    }
    if example is not None:
        schema["examples"] = [example]
    return schema


def timestamp(description, nullable=False):
    return {
        "type": ["string", "null"] if nullable else "string",
        "pattern": TIMESTAMP_PATTERN,
        "description": description + " UTC, ISO 8601, e.g. 2026-03-02T08:15:00Z.",
    }


def nullable_string(description, enum=None):
    schema = {"type": ["string", "null"], "description": description}
    if enum:
        schema["enum"] = [*enum, None]
    return schema


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


DECIMAL_INPUT = {
    "description": "Decimal value. Send a string (recommended, e.g. \"1.085\") or a JSON number; "
                   "numbers are read exactly, never as binary floats. NaN and Infinity are rejected.",
    "anyOf": [{"type": "string"}, {"type": "number"}],
}
TIMESTAMP_INPUT = {
    "type": "string",
    "description": "ISO 8601 date-time. A value without an offset is treated as UTC; "
                   "offsets are converted to UTC. A bare date is rejected.",
    "examples": ["2026-03-02T08:15:00Z", "2026-03-02 08:15", "2026-03-02T09:15:00+01:00"],
}

TRADE_PROPERTIES = {
    "id": {"type": "integer"},
    "instrument_id": {"type": "integer"},
    "symbol": {"type": "string"},
    "quote_currency": {"type": "string", "description": "Currency of all money values for this trade."},
    "trade_date": timestamp("Entry time."),
    "closed_at": timestamp("Exit time. null for OPEN trades, and for legacy CLOSED trades recorded "
                           "before the exit time was captured (never invented).", nullable=True),
    "direction": {"type": "string", "enum": ["LONG", "SHORT"]},
    "status": {"type": "string", "enum": ["OPEN", "CLOSED"]},
    "entry_price": decimal("Entry price.", example="1.085"),
    "stop_loss": decimal("Initial stop-loss price."),
    "take_profit": decimal("Target price, if planned.", nullable=True),
    "exit_price": decimal("Exit price. null while OPEN.", nullable=True),
    "position_size": decimal("Position size in the units the instrument's contract_multiplier assumes."),
    "fees": decimal("Total fees, in the quote currency."),
    "strategy": nullable_string("Free-text strategy label (max 50 characters)."),
    "session": nullable_string("Trading session.", ["ASIA", "LONDON", "NEW_YORK", "OTHER"]),
    "notes": nullable_string("Journal notes (max 2000 characters)."),
    "created_at": timestamp("When the record was created."),
    "updated_at": timestamp("When the record was last changed."),
    "result": {"anyOf": [{"$ref": "#/components/schemas/TradeResult"}, {"type": "null"}],
               "description": "Calculated by the analytics engine for CLOSED trades; null for OPEN trades."},
}

TRADE_RESULT = obj({
    "outcome": {"type": "string", "enum": ["WIN", "LOSS", "BREAKEVEN"],
                "description": "From net P&L: > 0 WIN, < 0 LOSS, exactly 0 BREAKEVEN."},
    "gross_pnl": decimal("(exit - entry) x direction x size x contract_multiplier."),
    "fees": decimal("Fees."),
    "net_pnl": decimal("gross_pnl - fees."),
    "initial_risk": decimal("Money lost if the stop is hit (fees excluded). null if the stop is invalid.",
                            nullable=True),
    "realised_r": decimal("net_pnl / initial_risk. null when initial risk is not positive.", nullable=True),
    "planned_rr": decimal("Planned reward / planned risk from prices. null without a valid target.",
                          nullable=True),
    "r_unavailable_reason": nullable_string("Why realised_r is null, if it is."),
})

TRADE_INPUT_PROPERTIES = {
    "instrument_id": {"type": "integer", "minimum": 1, "description": "Must reference an existing instrument."},
    "trade_date": TIMESTAMP_INPUT,
    "closed_at": {**TIMESTAMP_INPUT, "type": ["string", "null"],
                  "description": "Exit time. Required when the trade is CLOSED; must be null/absent when OPEN; "
                                 "must not be before trade_date. Exception: a legacy CLOSED trade whose exit time "
                                 "was never recorded may keep null when replaced with PUT."},
    "direction": {"type": "string", "enum": ["LONG", "SHORT", "long", "short"],
                  "description": "Case-insensitive."},
    "status": {"type": ["string", "null"],
               "description": "OPEN or CLOSED (case-insensitive). If omitted: CLOSED when exit_price is given, "
                              "otherwise OPEN. CLOSED requires exit_price; OPEN must not have one."},
    "entry_price": {**DECIMAL_INPUT, "description": "Greater than 0. " + DECIMAL_INPUT["description"]},
    "stop_loss": {**DECIMAL_INPUT, "description": "Greater than 0. LONG: below entry. SHORT: above entry. "
                                                  "Never equal to entry."},
    "take_profit": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
                    "description": "Optional. LONG: above entry. SHORT: below entry."},
    "exit_price": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
                   "description": "Required for CLOSED, absent/null for OPEN. Greater than 0."},
    "position_size": {**DECIMAL_INPUT, "description": "Greater than 0."},
    "fees": {"anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
             "description": "0 or more. Defaults to 0."},
    "strategy": {"type": ["string", "null"], "maxLength": 50},
    "session": {"type": ["string", "null"], "description": "ASIA, LONDON, NEW_YORK or OTHER (case-insensitive)."},
    "notes": {"type": ["string", "null"], "maxLength": 2000},
}

TRADE_INPUT_REQUIRED = ["instrument_id", "trade_date", "direction", "entry_price", "stop_loss", "position_size"]

INSTRUMENT_PROPERTIES = {
    "id": {"type": "integer"},
    "symbol": {"type": "string", "description": "Unique, upper case, 1-20 letters, digits or underscores."},
    "name": {"type": "string"},
    "asset_class": {"type": "string", "enum": ["FOREX", "COMMODITY", "INDEX", "STOCK", "OTHER"]},
    "price_increment": decimal("Smallest price step quoted, e.g. 0.00001."),
    "contract_multiplier": decimal(
        "Money (in quote_currency) per 1.0 move in price per 1.0 unit of position_size. "
        "Depends on your broker's contract specification; sample instruments use illustrative values."),
    "quote_currency": {"type": "string", "description": "3-letter currency code in which P&L is denominated."},
    "created_at": timestamp("When the record was created."),
}

INSTRUMENT_INPUT = obj({
    "symbol": {"type": "string", "description": "1-20 letters, digits or underscores; stored upper case."},
    "name": {"type": "string", "maxLength": 100},
    "asset_class": {"type": "string", "description": "FOREX, COMMODITY, INDEX, STOCK or OTHER (case-insensitive)."},
    "price_increment": {**DECIMAL_INPUT, "description": "Greater than 0."},
    "contract_multiplier": {**DECIMAL_INPUT, "description": "Greater than 0. See Instrument.contract_multiplier."},
    "quote_currency": {"type": "string", "description": "3 letters, e.g. USD."},
})

TRADE_REF = obj({
    "trade_id": {"type": "integer"},
    "symbol": {"type": "string"},
    "trade_date": timestamp("Entry time."),
    "value": decimal("The value that ranked this trade (R or net P&L)."),
})

DRAWDOWN = obj({
    "amount": decimal("Largest fall from a running peak (>= 0), in R or in one currency. Never a percentage."),
    "peak_trade_id": {"type": ["integer", "null"], "description": "null = the starting point (0)."},
    "trough_trade_id": {"type": ["integer", "null"], "description": "null when there was no drawdown."},
})

MONEY_SUMMARY = obj({
    "currency": {"type": "string"},
    "trades": {"type": "integer"},
    "gross_pnl": decimal("Sum of P&L before fees."),
    "fees": decimal("Sum of fees."),
    "net_pnl": decimal("Sum of P&L after fees."),
    "winning_pnl": decimal("Sum of net P&L of winning trades (>= 0)."),
    "losing_pnl": decimal("Sum of net P&L of losing trades (<= 0)."),
    "profit_factor": decimal("winning_pnl / |losing_pnl|. null with no losing trades.", nullable=True),
    "best_trade": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
    "worst_trade": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
    "max_drawdown": ref("Drawdown"),
})

SUMMARY_PROPERTIES = {
    "trades": {"type": "integer", "description": "Closed trades evaluated."},
    "wins": {"type": "integer"},
    "losses": {"type": "integer"},
    "breakevens": {"type": "integer"},
    "win_rate": decimal("wins / trades, as a fraction 0-1. null with no trades.", nullable=True),
    "loss_rate": decimal("losses / trades, 0-1.", nullable=True),
    "breakeven_rate": decimal("breakevens / trades, 0-1.", nullable=True),
    "r_trades": {"type": "integer", "description": "Trades with a usable realised R."},
    "total_r": decimal("Sum of realised R."),
    "average_r": decimal("Mean realised R.", nullable=True),
    "average_win_r": decimal("Mean R of winning trades.", nullable=True),
    "average_loss_r": decimal("Mean R of losing trades (negative).", nullable=True),
    "expectancy_r": decimal("P(win) x average_win_r + P(loss) x average_loss_r, in R per trade.", nullable=True),
    "profit_factor_r": decimal("Sum of positive R / |sum of negative R|. null with no losses.", nullable=True),
    "planned_rr_trades": {"type": "integer"},
    "average_planned_rr": decimal("Mean planned R:R of trades with a valid target.", nullable=True),
    "longest_win_streak": {"type": "integer"},
    "longest_loss_streak": {"type": "integer"},
    "max_drawdown_r": ref("Drawdown"),
    "best_trade_r": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
    "worst_trade_r": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
    "money": {"type": "object", "additionalProperties": ref("MoneySummary"),
              "description": "One entry per quote currency. Currencies are never added together."},
}

EXCLUDED_TRADE = obj({"trade_id": {"type": ["integer", "null"]}, "reason": {"type": "string"}})

CURVE_POINT = obj({
    "trade_id": {"type": "integer"},
    "trade_date": timestamp("Entry time."),
    "realised_at": timestamp("Exit time, or entry time for legacy trades with no recorded exit."),
    "close_time_known": {"type": "boolean"},
    "symbol": {"type": "string"},
    "realised_r": decimal("This trade's realised R."),
    "cumulative_r": decimal("Running total of realised R. NOT account equity."),
})

ANALYTICS_META = obj({
    "filters": obj({
        "instrument_id": {"type": ["integer", "null"]},
        "strategy": {"type": ["string", "null"]},
        "session": {"type": ["string", "null"], "enum": ["ASIA", "LONDON", "NEW_YORK", "OTHER", None]},
        "start": {"type": ["string", "null"], "format": "date"},
        "end": {"type": ["string", "null"], "format": "date"},
    }),
    "notes": {"type": "array", "items": {"type": "string"}},
    "label": {"type": "string"},
}, required=["filters", "notes"])

ERROR = obj({
    "error": obj({
        "code": {"type": "string", "description": "Stable machine-readable code."},
        "message": {"type": "string", "description": "Human-readable explanation."},
        "details": {"type": "object", "additionalProperties": {"type": "string"},
                    "description": "Field name -> problem. Present only when useful."},
    }, required=["code", "message"]),
})

PAGINATION = obj({
    "page": {"type": "integer", "minimum": 1},
    "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
    "total": {"type": "integer", "description": "Matching trades across all pages."},
    "total_pages": {"type": "integer", "description": "0 when there are no matches."},
})

# ---------- Parameters ----------


def query_param(name, schema, description):
    return {"name": name, "in": "query", "required": False, "schema": schema, "description": description}


PARAMS = {
    "instrument_id": query_param("instrument_id", {"type": "integer", "minimum": 1},
                                 "Only this instrument. 400 if it does not exist."),
    "status": query_param("status", {"type": "string", "enum": ["OPEN", "CLOSED"]},
                          "Only trades with this status (case-insensitive)."),
    "trade_start": query_param("start", {"type": "string", "format": "date"},
                               "First entry date to include (YYYY-MM-DD, UTC, inclusive)."),
    "trade_end": query_param("end", {"type": "string", "format": "date"},
                             "Last entry date to include (YYYY-MM-DD, UTC, inclusive)."),
    "realised_start": query_param("start", {"type": "string", "format": "date"},
                                  "First realised date (closed_at, or trade_date if unknown) to include."),
    "realised_end": query_param("end", {"type": "string", "format": "date"},
                                "Last realised date to include (inclusive)."),
    "strategy": query_param("strategy", {"type": "string", "minLength": 1, "maxLength": 50},
                            "Only trades with exactly this strategy label (case-sensitive). Phase 6."),
    "session": query_param("session", {"type": "string", "enum": ["ASIA", "LONDON", "NEW_YORK", "OTHER"]},
                           "Only trades recorded in this session (case-insensitive). Phase 6."),
    "page": query_param("page", {"type": "integer", "minimum": 1, "default": 1}, "Page number."),
    "per_page": query_param("per_page", {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                            "Trades per page."),
}


def path_id(name, what):
    return {"name": name, "in": "path", "required": True,
            "schema": {"type": "integer", "minimum": 1}, "description": f"{what} id."}


# ---------- Responses ----------


def json_response(description, schema, example=None):
    content = {"schema": schema}
    if example is not None:
        content["example"] = example
    return {"description": description, "content": {"application/json": content}}


def error(description):
    return json_response(description, ref("Error"))


ERRORS = {
    "400": error("Invalid request: malformed JSON, unknown fields or parameters, or failed validation "
                 "(details lists each field)."),
    "403": error("The API is disabled in this environment (no authentication; production default)."),
    "404": error("The resource does not exist."),
    "405": error("Method not allowed for this URL."),
    "409": error("Conflict, e.g. an instrument symbol that already exists."),
    "413": error("Request body larger than 64 KB."),
    "415": error("Request body is not application/json."),
    "500": error("Unexpected error. No internal details are included."),
}


def errors(*codes):
    return {code: ERRORS[code] for code in codes}


def envelope(data_schema, **extra):
    return obj({"data": data_schema, **extra})


EXAMPLE_TRADE_INPUT = {
    "instrument_id": 1,
    "trade_date": "2026-04-01T08:15:00Z",
    "closed_at": "2026-04-01T11:40:00Z",
    "direction": "LONG",
    "entry_price": "1.08500",
    "stop_loss": "1.08300",
    "take_profit": "1.08900",
    "exit_price": "1.08900",
    "position_size": "1",
    "fees": "7",
    "strategy": "Breakout",
    "session": "LONDON",
    "notes": "Synthetic example.",
}

EXAMPLE_TRADE = {
    "id": 17, "instrument_id": 1, "symbol": "EURUSD", "quote_currency": "USD",
    "trade_date": "2026-04-01T08:15:00Z", "closed_at": "2026-04-01T11:40:00Z",
    "direction": "LONG", "status": "CLOSED",
    "entry_price": "1.085", "stop_loss": "1.083", "take_profit": "1.089", "exit_price": "1.089",
    "position_size": "1", "fees": "7", "strategy": "Breakout", "session": "LONDON",
    "notes": "Synthetic example.",
    "created_at": "2026-11-15T09:00:00Z", "updated_at": "2026-11-15T09:00:00Z",
    "result": {"outcome": "WIN", "gross_pnl": "400", "fees": "7", "net_pnl": "393",
               "initial_risk": "200", "realised_r": "1.965", "planned_rr": "2",
               "r_unavailable_reason": None},
}

EXAMPLE_VALIDATION_ERROR = {"error": {
    "code": "validation_error", "message": "The request is invalid.",
    "details": {"position_size": "must be greater than zero",
                "stop_loss": "must be below the entry price for a LONG trade"}}}


# The analytics filters, in the same order as report_filters.FILTER_NAMES.
ANALYTICS_PARAMS = [PARAMS["instrument_id"], PARAMS["strategy"], PARAMS["session"],
                    PARAMS["realised_start"], PARAMS["realised_end"]]


def grouped_path(summary, description):
    return {"get": {
        "tags": ["Analytics"], "summary": summary, "description": description,
        "parameters": ANALYTICS_PARAMS,
        "responses": {"200": json_response("Groups in a stable order.", ref("GroupedAnalytics")),
                      **errors("400", "403", "405")},
    }}


def build_spec():
    group_summary = obj({"group": {"type": "string", "description": "Group label."}, **SUMMARY_PROPERTIES})
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "TradeLedger API",
            "version": "1.0.0",
            "description": (
                "JSON API for the TradeLedger trading journal.\n\n"
                "**Security:** there is no authentication. The API is for local, single-user use. "
                "In the production configuration it is disabled (403) unless explicitly enabled.\n\n"
                "**Conventions:** decimal values are exact strings; timestamps are UTC "
                "`YYYY-MM-DDTHH:MM:SSZ`; unavailable values are `null`; money is per quote currency; "
                "R values are currency-neutral; cumulative R is not account equity. "
                "Unknown body fields and query parameters are rejected with 400."
            ),
        },
        "servers": [{"url": "/api/v1"}],
        "paths": {
            "/trades": {
                "get": {
                    "tags": ["Trades"], "summary": "List trades",
                    "description": "Newest trade_date first, then highest id first. Dates filter on entry time.",
                    "parameters": [PARAMS[p] for p in
                                   ("instrument_id", "status", "trade_start", "trade_end", "page", "per_page")],
                    "responses": {"200": json_response("A page of trades.", ref("TradeList")),
                                  **errors("400", "403", "405")},
                },
                "post": {
                    "tags": ["Trades"], "summary": "Create a trade",
                    "requestBody": {"required": True, "content": {"application/json": {
                        "schema": ref("TradeInput"), "example": EXAMPLE_TRADE_INPUT}}},
                    "responses": {
                        "201": {**json_response("Created. Location header holds the new trade's URL.",
                                                ref("TradeEnvelope"), {"data": EXAMPLE_TRADE}),
                                "headers": {"Location": {"schema": {"type": "string"}}}},
                        "400": json_response("Invalid trade. Nothing was written.", ref("Error"),
                                             EXAMPLE_VALIDATION_ERROR),
                        **errors("403", "413", "415"),
                    },
                },
            },
            "/trades/{trade_id}": {
                "parameters": [path_id("trade_id", "Trade")],
                "get": {"tags": ["Trades"], "summary": "Get a trade",
                        "responses": {"200": json_response("The trade.", ref("TradeEnvelope")),
                                      **errors("403", "404")}},
                "put": {
                    "tags": ["Trades"], "summary": "Replace a trade",
                    "description": "Full replacement: every field in TradeReplace must be present "
                                   "(null for empty optional fields). There are no partial updates.",
                    "requestBody": {"required": True, "content": {"application/json": {
                        "schema": ref("TradeReplace"), "example": EXAMPLE_TRADE_INPUT}}},
                    "responses": {"200": json_response("The updated trade.", ref("TradeEnvelope")),
                                  **errors("400", "403", "404", "413", "415")},
                },
                "delete": {"tags": ["Trades"], "summary": "Delete a trade",
                           "responses": {"204": {"description": "Deleted. No body."},
                                         **errors("403", "404")}},
            },
            "/instruments": {
                "get": {"tags": ["Instruments"], "summary": "List instruments (sorted by symbol)",
                        "responses": {"200": json_response("All instruments.", ref("InstrumentList")),
                                      **errors("400", "403")}},
                "post": {
                    "tags": ["Instruments"], "summary": "Create an instrument",
                    "requestBody": {"required": True, "content": {"application/json": {
                        "schema": ref("InstrumentInput"),
                        "example": {"symbol": "US30", "name": "Wall Street 30 CFD (example values)",
                                    "asset_class": "INDEX", "price_increment": "1",
                                    "contract_multiplier": "1", "quote_currency": "USD"}}}},
                    "responses": {
                        "201": {**json_response("Created.", ref("InstrumentEnvelope")),
                                "headers": {"Location": {"schema": {"type": "string"}}}},
                        **errors("400", "403", "409", "413", "415"),
                    },
                },
            },
            "/instruments/{instrument_id}": {
                "parameters": [path_id("instrument_id", "Instrument")],
                "get": {"tags": ["Instruments"], "summary": "Get an instrument",
                        "responses": {"200": json_response("The instrument.", ref("InstrumentEnvelope")),
                                      **errors("403", "404")}},
            },
            "/analytics/summary": {"get": {
                "tags": ["Analytics"], "summary": "Overall realised performance",
                "parameters": ANALYTICS_PARAMS,
                "responses": {"200": json_response("Overall statistics.", ref("SummaryAnalytics")),
                              **errors("400", "403", "405")},
            }},
            "/analytics/instruments": grouped_path("Performance by instrument", "Sorted by symbol."),
            "/analytics/strategies": grouped_path("Performance by strategy",
                                                  "Sorted alphabetically; untagged trades are 'Unspecified'."),
            "/analytics/sessions": grouped_path("Performance by session",
                                                "Sorted alphabetically; untagged trades are 'Unspecified'."),
            "/analytics/weekday": grouped_path("Performance by realised weekday (UTC)",
                                               "Monday to Sunday; days with no trades are omitted."),
            "/analytics/monthly": grouped_path("Performance by realised month (YYYY-MM, UTC)", "Oldest first."),
            "/analytics/curve": {"get": {
                "tags": ["Analytics"], "summary": "Cumulative realised R (chart data)",
                "description": "In realised order. NOT an account equity curve.",
                "parameters": ANALYTICS_PARAMS,
                "responses": {"200": json_response("Curve points.", ref("CurveAnalytics")),
                              **errors("400", "403", "405")},
            }},
            "/openapi.json": {"get": {
                "tags": ["Documentation"], "summary": "This specification",
                "description": "Available even when the API is disabled.",
                "responses": {"200": {"description": "OpenAPI 3.1 document.",
                                      "content": {"application/json": {"schema": {"type": "object"}}}}},
            }},
        },
        "components": {"schemas": {
            "Trade": obj(TRADE_PROPERTIES),
            "TradeResult": TRADE_RESULT,
            "TradeInput": obj(TRADE_INPUT_PROPERTIES, required=TRADE_INPUT_REQUIRED),
            "TradeReplace": obj(TRADE_INPUT_PROPERTIES),
            "Instrument": obj(INSTRUMENT_PROPERTIES),
            "InstrumentInput": INSTRUMENT_INPUT,
            "Pagination": PAGINATION,
            "TradeEnvelope": envelope(ref("Trade")),
            "TradeList": envelope({"type": "array", "items": ref("Trade")}, pagination=ref("Pagination")),
            "InstrumentEnvelope": envelope(ref("Instrument")),
            "InstrumentList": envelope({"type": "array", "items": ref("Instrument")}),
            "TradeRef": TRADE_REF,
            "Drawdown": DRAWDOWN,
            "MoneySummary": MONEY_SUMMARY,
            "Summary": obj(SUMMARY_PROPERTIES),
            "GroupSummary": group_summary,
            "ExcludedTrade": EXCLUDED_TRADE,
            "CurvePoint": CURVE_POINT,
            "AnalyticsMeta": ANALYTICS_META,
            "SummaryAnalytics": envelope(obj({
                "overall": ref("Summary"),
                "open_trades": {"type": "integer", "description": "Counted, but excluded from realised metrics."},
                "excluded": {"type": "array", "items": ref("ExcludedTrade"),
                             "description": "Closed trades that could not be evaluated, with reasons."},
                "r_unavailable": {"type": "array", "items": ref("ExcludedTrade"),
                                  "description": "Closed trades counted, but with no realised R."},
                "unknown_close_time_trade_ids": {"type": "array", "items": {"type": "integer"},
                                                 "description": "Legacy trades ordered by entry time."},
            }), meta=ref("AnalyticsMeta")),
            "GroupedAnalytics": envelope({"type": "array", "items": ref("GroupSummary")},
                                         meta=ref("AnalyticsMeta")),
            "CurveAnalytics": envelope({"type": "array", "items": ref("CurvePoint")},
                                       meta=ref("AnalyticsMeta")),
            "Error": ERROR,
        }},
    }


@api_bp.get("/openapi.json")
def openapi_spec():
    spec = build_spec()
    spec["info"]["x-app-version"] = current_app.config["APP_VERSION"]
    return jsonify(spec)
```


## 22. `tradeledger/web/__init__.py` (MODIFIED)

```python
"""The TradeLedger website: server-rendered pages and HTML forms.

Blueprint "main" (the same name Phase 1 used, so url_for("main.index") and
url_for("main.health") keep working). Modules:

  guard.py        refuses journal pages when JOURNAL_ENABLED is off
  csrf.py         CSRF tokens for every state-changing form
  filters.py      Jinja filters that format exact Decimals for display
  forms.py        turning request.form into input for validation.py
  views.py        overview, about, health
  analytics.py    the /analytics performance dashboard (Phase 6)
  trades.py       journal, add, details, edit, delete
  instruments.py  instrument list and add form

Every write goes through the same repository and validation functions as
the JSON API, so there is one set of business rules, not two.
"""

from flask import Blueprint

main_bp = Blueprint("main", __name__)

# Order matters: the guard runs before CSRF checks (both are before_request).
from . import guard, csrf, filters, views, trades, instruments, analytics  # noqa: E402,F401
```


## 23. `tradeledger/web/views.py` (MODIFIED)

```python
"""Overview, About and the health check. (The analytics dashboard is in
analytics.py.)

Every number shown comes from the repositories (counts) or the Phase 3
analytics engine (performance). Nothing is hard-coded.
"""

from flask import current_app, jsonify, render_template

from ..db import get_db
from ..performance import build_report
from ..repositories import trades as trade_repo
from ..repositories.instruments import list_instruments
from . import main_bp
from .trades import with_results

RECENT_TRADES = 5

# The build plan, shown on the About page.
ROADMAP = [
    {"phase": 1, "title": "Project setup", "status": "Done"},
    {"phase": 2, "title": "SQL database", "status": "Done"},
    {"phase": 3, "title": "Trading calculations", "status": "Done"},
    {"phase": 4, "title": "Flask backend & API", "status": "Done"},
    {"phase": 5, "title": "Frontend pages", "status": "Done"},
    {"phase": 6, "title": "Analytics dashboard", "status": "Done"},
    {"phase": 7, "title": "Next phase (to be agreed)", "status": "Next"},
]


@main_bp.route("/")
def index():
    """Overview. In production with the journal disabled, a short
    explanation is shown instead, with no trading data."""
    if not current_app.config["JOURNAL_ENABLED"]:
        return render_template("home_disabled.html")
    conn = get_db()
    report = build_report(conn)
    return render_template(
        "index.html",
        open_count=trade_repo.count_trades(conn, status="OPEN"),
        closed_count=trade_repo.count_trades(conn, status="CLOSED"),
        instrument_count=len(list_instruments(conn)),
        overall=report.overall,
        unknown_close_count=len(report.unknown_close_time_trade_ids),
        recent=with_results(trade_repo.list_trades(conn, limit=RECENT_TRADES)),
    )


@main_bp.route("/about")
def about():
    return render_template("about.html", roadmap=ROADMAP)


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


## 24. `tradeledger/templates/base.html` (MODIFIED)

```html
<!DOCTYPE html>
<html lang="en-GB">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="dark">
    <title>{% block title %}TradeLedger{% endblock %} · TradeLedger</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    {# early.js only adds class="js" to <html>, before the page paints, so
       JavaScript-dependent styles apply without a flash of the no-JS layout. #}
    <script src="{{ url_for('static', filename='js/early.js') }}"></script>
    <script src="{{ url_for('static', filename='js/app.js') }}" defer></script>
    {% block scripts %}{% endblock %}
</head>
<body>
    <a class="skip-link" href="#main">Skip to main content</a>

    {% set endpoint = request.endpoint or "" %}
    {% set nav_items = [
        ("main.index", "Overview", ["main.index"]),
        ("main.journal", "Trade journal", ["main.journal", "main.trade_detail", "main.edit_trade", "main.delete_trade"]),
        ("main.new_trade", "Add trade", ["main.new_trade"]),
        ("main.instruments", "Instruments", ["main.instruments"]),
        ("main.analytics", "Analytics", ["main.analytics"]),
        ("main.about", "About", ["main.about"]),
    ] %}

    <div class="shell">
        <aside class="sidebar" id="sidebar" data-sidebar aria-label="Main navigation">
            <div class="sidebar__top">
                <a class="brand" href="{{ url_for('main.index') }}">
                    <span class="brand__mark" aria-hidden="true">TL</span>
                    <span class="brand__text">
                        <span class="brand__name">TradeLedger</span>
                        <span class="brand__tag">Trading journal</span>
                    </span>
                </a>
                <button class="icon-button sidebar__close" type="button" data-nav-close>
                    <span aria-hidden="true">&times;</span>
                    <span class="visually-hidden">Close menu</span>
                </button>
            </div>
            <nav>
                <ul class="nav" role="list">
                    {% for target, label, matches in nav_items %}
                    <li>
                        <a class="nav__link" href="{{ url_for(target) }}"
                           {% if endpoint in matches %}aria-current="page"{% endif %}>
                            <span>{{ label }}</span>
                        </a>
                    </li>
                    {% endfor %}
                </ul>
            </nav>
            <div class="sidebar__footer">
                <span class="env-badge">{{ config.CONFIG_NAME }} mode</span>
                <p>Local, single-user journal. There is no login, so keep it on your own machine.</p>
            </div>
        </aside>
        <div class="nav-backdrop" data-nav-backdrop hidden></div>

        <div class="main-area">
            <header class="topbar">
                <button class="icon-button nav-toggle" type="button" data-nav-toggle
                        aria-controls="sidebar" aria-expanded="false">
                    <span class="nav-toggle__bars" aria-hidden="true"></span>
                    <span>Menu</span>
                </button>
                <div class="topbar__titles">
                    <h1 class="topbar__title">{% block heading %}TradeLedger{% endblock %}</h1>
                    {% set description %}{% block description %}{% endblock %}{% endset %}
                    {% if description | trim %}<p class="topbar__description">{{ description }}</p>{% endif %}
                </div>
                <div class="topbar__actions">{% block actions %}{% endblock %}</div>
            </header>

            {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
            <div class="flash-stack" role="status" aria-live="polite">
                {% for category, message in messages %}
                <div class="flash flash--{{ category }}" data-flash>
                    <p>{{ message }}</p>
                    <button class="flash__close" type="button" data-flash-close>
                        <span aria-hidden="true">&times;</span>
                        <span class="visually-hidden">Dismiss message</span>
                    </button>
                </div>
                {% endfor %}
            </div>
            {% endif %}
            {% endwith %}

            <main id="main" class="content" tabindex="-1">
                {% block content %}{% endblock %}
            </main>

            <footer class="site-footer">
                TradeLedger v{{ config.APP_VERSION }} · Exact decimal calculations · Times shown in UTC
            </footer>
        </div>
    </div>
</body>
</html>
```


## 25. `tradeledger/templates/about.html` (MODIFIED)

```html
{% extends "base.html" %}

{% block title %}About{% endblock %}
{% block heading %}About TradeLedger{% endblock %}
{% block description %}What this application does, how it calculates, and how it is built.{% endblock %}

{% block content %}
<div class="prose-grid">
    <section class="card prose" aria-labelledby="what-title">
        <h2 id="what-title">What it does</h2>
        <p>TradeLedger is a trading journal. You record each trade's plan (entry, stop-loss, optional target, size)
           and, when it finishes, its exit. It then calculates the result of every closed trade and the performance
           of the whole journal.</p>
        <h3>How results are calculated</h3>
        <ul>
            <li><strong>Net P&amp;L</strong> = price move × position size × contract multiplier − fees, in the instrument's quote currency.</li>
            <li><strong>Realised R</strong> = net P&amp;L ÷ initial risk, where initial risk is the money lost if the stop is hit.</li>
            <li>A trade is a <strong>win</strong> if net P&amp;L is above zero, a <strong>loss</strong> if below, and <strong>breakeven</strong> only at exactly zero.</li>
            <li>Money in different currencies is never added together. R-multiples can be, because they are currency-neutral.</li>
            <li>Every calculation uses exact decimal arithmetic in Python. The browser never calculates results.</li>
        </ul>
    </section>

    <section class="card prose" aria-labelledby="security-title">
        <h2 id="security-title">Security and data</h2>
        <p>This is a <strong>single-user application for your own computer</strong>. It has no accounts or login.</p>
        <ul>
            <li>Run it with <code>flask --app tradeledger run</code>, which listens only on 127.0.0.1.</li>
            <li>In the production configuration the journal pages and the JSON API are switched off unless you deliberately enable them.</li>
            <li>Every form is protected against cross-site request forgery, and pages may only load this site's own scripts and styles. The one exception is the analytics page, which may also load the pinned Chart.js file from its CDN.</li>
            <li>Your trades are stored in one SQLite file on your machine (<code>instance/tradeledger.db</code>).</li>
        </ul>
    </section>

    <section class="card prose" aria-labelledby="build-title">
        <h2 id="build-title">Build progress</h2>
        <ol class="roadmap" role="list">
            {% for item in roadmap %}
            <li class="roadmap-item status-{{ item.status | lower }}">
                <span class="phase-number">Phase {{ item.phase }}</span>
                <span class="phase-title">{{ item.title }}</span>
                <span class="phase-status">{{ item.status }}</span>
            </li>
            {% endfor %}
        </ol>
    </section>

    <section class="card prose" aria-labelledby="tech-title">
        <h2 id="tech-title">Technology</h2>
        <ul>
            <li>Python, Flask and Jinja2 templates, rendered on the server</li>
            <li>SQLite with constraints, triggers and versioned migrations</li>
            <li>HTML, CSS and a small amount of vanilla JavaScript for progressive enhancement</li>
            <li>Chart.js {{ config.CHART_JS_VERSION }} for the analytics charts (drawing only; every figure is calculated in Python)</li>
            <li>A versioned JSON API documented with OpenAPI 3.1 (<a class="text-link" href="{{ url_for('api.openapi_spec') }}">specification</a>)</li>
            <li>pytest, with browser tests in Playwright where available</li>
        </ul>
        <p class="muted">Running in <strong>{{ config.CONFIG_NAME }}</strong> mode · version {{ config.APP_VERSION }}</p>
    </section>
</div>
{% endblock %}
```


## 26. `tradeledger/static/css/style.css` (MODIFIED)

```css
/* TradeLedger stylesheet entry point.
   Split into five files so each stays focused and readable:
     base.css        design tokens (colours, spacing, type), reset, typography
     layout.css      the application shell: sidebar, top bar, content area
     components.css  reusable pieces: buttons, forms, tables, badges, cards
     pages.css       layouts that belong to one page
     analytics.css   the /analytics dashboard (Phase 6)
   @import keeps a single <link> in the HTML. */
@import url("base.css");
@import url("layout.css");
@import url("components.css");
@import url("pages.css");
@import url("analytics.css");
```


## 27. `.env.example` (MODIFIED)

```bash
# Copy this file to .env and fill in real values.
# .env is ignored by Git - never commit it.

# Which config class to use: development, testing or production
TRADELEDGER_CONFIG=development

# Generate your own with:  python -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY=replace-me-with-a-long-random-string

# Optional: where the SQLite file lives. Leave commented out to use
# instance/tradeledger.db inside the project folder.
# DATABASE_PATH=/full/path/to/tradeledger.db

# Optional (Phase 6): where the dashboard loads Chart.js from. Default is the
# pinned jsDelivr CDN URL for version 4.5.1 (needs internet in the browser).
# To self-host, see docs/THIRD_PARTY_NOTICES.md, then:
# TRADELEDGER_CHART_JS_URL=/static/vendor/chartjs/chart.umd.min.js
# Optional Subresource Integrity hash for that file ("sha384-..."):
# TRADELEDGER_CHART_JS_INTEGRITY=
```


## 28. `tests/test_browser.py` (MODIFIED)

```python
"""Phase 5: optional real-browser checks with Playwright.

These start TradeLedger on a local port in a background thread and drive a
real Chromium browser, so they test what the test client cannot: CSS
layout, JavaScript behaviour, focus handling and the Content-Security-Policy.

They are OPTIONAL. Playwright is not in requirements.txt (it downloads a
~150 MB browser). If it, or its browser, is missing, every test here is
skipped and the rest of the suite is unaffected.

Chart.js (Phase 6): the dashboard loads Chart.js from the jsDelivr CDN.
Tests must not depend on the internet, so by default every page here
intercepts that exact URL and serves CHART_STUB: a tiny stand-in that
records each `new Chart(canvas, config)` call instead of drawing. The
browser still enforces the real Content-Security-Policy on the real URL,
and our analytics.js really runs. What these tests therefore do NOT show
is Chart.js's own drawing; that is checked by hand (see the Phase 6 guide).
Pages created with chart_js="blocked" simulate an offline browser.

To run them:

    pip install playwright
    python -m playwright install chromium
    pytest tests/test_browser.py
"""

import threading

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

WIDTHS = [375, 768, 1024, 1440]
CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"

# Records chart configurations; draws nothing. Never shipped to users.
CHART_STUB = """
(function () {
    function Chart(canvas, config) {
        this.canvas = canvas;
        this.config = config;
        (window.__charts = window.__charts || []).push(this);
    }
    Chart.defaults = {font: {}};
    window.Chart = Chart;
})();
"""


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("browser") / "browser.db")
    app = create_app("testing", overrides={"DATABASE": db_path})
    with app.app_context():
        init_schema(get_db())
        seed_sample_data(get_db())
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            launched = p.chromium.launch()
        except Exception as exc:                      # browser binary not installed
            pytest.skip(f"Chromium is not available: {exc}")
        yield launched
        launched.close()


@pytest.fixture
def page_factory(browser):
    contexts = []

    def make(width=1440, height=900, chart_js="stub", javascript=True):
        context = browser.new_context(viewport={"width": width, "height": height},
                                      java_script_enabled=javascript)
        contexts.append(context)
        if chart_js == "stub":
            context.route(CHART_JS_URL, lambda route: route.fulfill(
                status=200, content_type="application/javascript", body=CHART_STUB))
        else:
            context.route(CHART_JS_URL, lambda route: route.abort("internetdisconnected"))
        page = context.new_page()
        page.problems = []
        page.on("pageerror", lambda exc: page.problems.append(str(exc)))
        page.on("console", lambda msg: page.problems.append(msg.text)
                if msg.type == "error" and "status of 4" not in msg.text else None)
        return page

    yield make
    for context in contexts:
        context.close()


def horizontal_overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("path", ["/", "/trades", "/trades/new", "/trades/1", "/trades/1/delete",
                                  "/instruments", "/analytics", "/about"])
def test_no_horizontal_scrolling_and_no_errors(live_server, page_factory, path, width):
    page = page_factory(width)
    page.goto(live_server + path)
    assert horizontal_overflow(page) == 0
    assert page.problems == []                        # includes CSP violations


@pytest.mark.parametrize("width, menu_visible", [(375, True), (768, True), (1024, False), (1440, False)])
def test_menu_button_only_on_narrow_screens(live_server, page_factory, width, menu_visible):
    page = page_factory(width)
    page.goto(live_server + "/")
    assert page.locator("[data-nav-toggle]").is_visible() is menu_visible
    assert page.locator("#sidebar a", has_text="Instruments").is_visible() is not menu_visible


def test_mobile_menu_opens_moves_focus_and_closes_with_escape(live_server, page_factory):
    page = page_factory(375)
    page.goto(live_server + "/trades")
    toggle = page.locator("[data-nav-toggle]")
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    page.wait_for_function("document.activeElement.closest('#sidebar') !== null")
    page.keyboard.press("Escape")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.hasAttribute('data-nav-toggle')")


def test_skip_link_is_first_and_reaches_main(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.classList.contains('skip-link')")
    page.keyboard.press("Enter")
    assert page.evaluate("location.hash") == "#main"


def test_open_status_hides_and_disables_exit_fields(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/trades/new")
    exit_fields = page.locator("[data-exit-fields]")
    assert exit_fields.is_visible()
    page.locator("input[name=status][value=OPEN]").check(force=True)
    assert not exit_fields.is_visible()
    assert page.locator("#field-exit_price").is_disabled()
    page.locator("input[name=status][value=CLOSED]").check(force=True)
    assert exit_fields.is_visible()
    assert page.locator("#field-exit_price").get_attribute("required") is not None


def test_server_errors_are_shown_and_focused(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/trades/new")
    page.select_option("#field-instrument_id", "1")
    page.fill("#field-trade_date", "2026-04-01T09:00")
    page.fill("#field-entry_price", "1.1000")
    page.fill("#field-stop_loss", "1.1050")            # wrong side for a long
    page.fill("#field-position_size", "1")
    page.locator("form[data-trade-form] button[type=submit]").click()
    page.wait_for_load_state()
    assert page.locator(".error-summary").is_visible()
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    assert page.input_value("#field-entry_price") == "1.1000"
    assert page.get_attribute("#field-stop_loss", "aria-invalid") == "true"


def test_create_and_delete_through_the_browser(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/trades/new")
    page.select_option("#field-instrument_id", "1")
    page.fill("#field-trade_date", "2026-04-01T09:00")
    page.fill("#field-entry_price", "1.1000")
    page.fill("#field-stop_loss", "1.0950")
    page.fill("#field-position_size", "1")
    page.fill("#field-exit_price", "1.1100")
    page.fill("#field-closed_at", "2026-04-01T12:00")
    page.fill("#field-notes", "<script>window.pwned = 1</script> browser test")
    page.locator("form[data-trade-form] button[type=submit]").click()
    page.wait_for_url("**/trades/*")
    assert page.locator(".flash", has_text="saved").is_visible()
    assert page.evaluate("window.pwned === undefined")             # rendered as text
    trade_url = page.url

    page.goto(trade_url + "/delete")
    page.locator("form button[type=submit]", has_text="Delete").click()
    page.wait_for_url("**/trades")
    assert page.locator(".flash", has_text="deleted").is_visible()
    assert page.goto(trade_url).status == 404


def test_double_submission_is_blocked(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/trades/1/delete")
    # Count submissions that get through app.js, and stop the real
    # navigation so the page can be inspected afterwards. This listener is
    # added after app.js's, so it only sees submits app.js let through.
    page.evaluate("""() => {
        window.submissions = 0;
        document.querySelector('form[data-submit-once]').addEventListener('submit', e => {
            if (!e.defaultPrevented) { window.submissions += 1; }
            e.preventDefault();
        });
    }""")
    button = page.locator("form[data-submit-once] button[type=submit]")
    button.click()
    button.click()                                   # an impatient second click
    assert page.evaluate("window.submissions") == 1
    assert page.locator("form[data-submit-once]").get_attribute("data-submitting") == "true"
    assert button.get_attribute("aria-busy") == "true"
    assert button.inner_text() == "Deleting…"


# ---------- Phase 6: analytics dashboard ----------

def charts_by_name(page):
    return page.evaluate("""() => Object.fromEntries((window.__charts || []).map(
        (c) => [c.canvas.dataset.chart, {type: c.config.type,
                                         points: c.config.data.datasets[0].data.length,
                                         indexAxis: c.config.options.indexAxis || "x"}]))""")


def test_dashboard_builds_all_seven_charts(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/analytics")
    page.wait_for_function("(window.__charts || []).length === 7")
    charts = charts_by_name(page)
    assert charts == {
        "curve": {"type": "line", "points": 15, "indexAxis": "x"},        # start + 14 trades
        "drawdown": {"type": "line", "points": 15, "indexAxis": "x"},
        "monthly": {"type": "bar", "points": 1, "indexAxis": "x"},        # March 2026 only
        "weekday": {"type": "bar", "points": 7, "indexAxis": "x"},
        "instruments": {"type": "bar", "points": 5, "indexAxis": "y"},
        "strategies": {"type": "bar", "points": 4, "indexAxis": "y"},
        "sessions": {"type": "bar", "points": 4, "indexAxis": "y"},
    }
    assert page.problems == []                       # no JS errors, no CSP violations


def test_csp_violations_would_be_detected(live_server, page_factory):
    """Negative control: proves the console listener above really catches a
    Content-Security-Policy violation, so 'no problems' means something."""
    page = page_factory()
    page.goto(live_server + "/analytics")
    page.evaluate("""() => { const s = document.createElement("script");
                             s.src = "https://example.com/evil.js"; document.head.appendChild(s); }""")
    page.wait_for_timeout(300)
    assert any("Content Security Policy" in problem for problem in page.problems)


def test_chart_tooltips_show_the_server_figures(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/analytics")
    page.wait_for_function("(window.__charts || []).length === 7")
    tooltip = page.evaluate("""() => {
        const chart = (name) => window.__charts.find((c) => c.canvas.dataset.chart === name);
        const label = (name, i) => chart(name).config.options.plugins.tooltip.callbacks.label({dataIndex: i});
        const title = (name, i) => chart(name).config.options.plugins.tooltip.callbacks.title([{dataIndex: i}]);
        return {curveTitle: title("curve", 1), curve: label("curve", 1),
                drawdown: label("drawdown", 9), weekday: label("weekday", 5), session: title("sessions", 2)};
    }""")
    assert tooltip["curveTitle"] == "02 Mar 2026, 11:40 UTC"
    assert tooltip["curve"] == ["Trade #1 · EURUSD", "Trade R: +1.97R", "Cumulative R: +1.97R"]
    assert tooltip["drawdown"][0] == "Drawdown: 1.05R below the running peak"
    assert "Maximum drawdown: 1.05R" in tooltip["drawdown"]
    assert tooltip["weekday"] == ["No closed trades"]                 # Saturday
    assert tooltip["session"] == "New York"


def test_filters_update_cards_and_charts(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/analytics")
    page.select_option("#dash-strategy", "Breakout")
    page.select_option("#dash-session", "LONDON")
    page.locator(".dash-filters button[type=submit]").click()
    page.wait_for_url("**/analytics?*")
    assert "strategy=Breakout" in page.url and "session=LONDON" in page.url
    assert page.locator('[data-kpi="trades"] .kpi__value').inner_text() == "3"
    page.wait_for_function("(window.__charts || []).length === 7")
    assert charts_by_name(page)["curve"]["points"] == 4               # start + trades 1, 3, 10
    page.get_by_role("link", name="Reset filters").click()
    page.wait_for_url(live_server + "/analytics")
    assert page.locator('[data-kpi="trades"] .kpi__value').inner_text() == "14"


def test_invalid_filter_is_explained_in_the_browser(live_server, page_factory):
    page = page_factory()
    response = page.goto(live_server + "/analytics?instrument_id=999")
    assert response.status == 400
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    assert page.locator("canvas").count() == 0


def test_empty_result_shows_a_helpful_state(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/analytics?start=2030-01-01")
    assert page.get_by_text("No closed trades match these filters").is_visible()
    assert page.locator("canvas").count() == 0
    assert page.problems == []


def test_offline_chart_library_falls_back_to_text(live_server, page_factory):
    page = page_factory(chart_js="blocked")
    page.goto(live_server + "/analytics")
    page.wait_for_selector("[data-chart-fallback]:not([hidden])")
    notes = page.locator("[data-chart-fallback]:not([hidden])")
    assert notes.count() == 7
    assert "could not be loaded" in notes.first.inner_text()
    assert page.locator("canvas:visible").count() == 0
    assert page.locator("#table-instruments").is_visible()
    # The only console error is the blocked download itself.
    assert all("ERR_INTERNET_DISCONNECTED" in p or "Failed to load resource" in p for p in page.problems)


def test_dashboard_without_javascript(live_server, page_factory):
    page = page_factory(javascript=False)
    page.goto(live_server + "/analytics?strategy=Breakout")
    assert page.get_by_text("Charts need JavaScript").is_visible()
    assert page.locator('[data-kpi="trades"] .kpi__value').inner_text() == "5"
    assert page.locator("#table-series").is_visible()
    assert page.locator("[data-chart-box]").first.is_hidden()


@pytest.mark.parametrize("width", WIDTHS)
def test_dashboard_layout_at_each_width(live_server, page_factory, width):
    page = page_factory(width)
    page.goto(live_server + "/analytics")
    page.wait_for_function("(window.__charts || []).length === 7")
    assert horizontal_overflow(page) == 0
    boxes = page.evaluate("""() => [...document.querySelectorAll('[data-chart-box]')].map((b) => {
        const r = b.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), Math.round(r.height)];
    })""")
    for left, right, height in boxes:
        assert left >= 0 and right <= width and height >= 200
    cards = page.evaluate("""() => [...document.querySelectorAll('.kpi')].map((k) => k.getBoundingClientRect().right)""")
    assert len(cards) == 12 and max(cards) <= width


def test_filters_are_keyboard_operable(live_server, page_factory):
    page = page_factory()
    page.goto(live_server + "/analytics")
    page.focus("#dash-instrument_id")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "dash-strategy"
    page.focus("#table-instruments")
    assert page.evaluate("document.activeElement.id") == "table-instruments"
```


## 29. `tests/test_web_pages.py` (MODIFIED)

```python
"""Phases 5-6: every website page renders, shows real data, and the shell
(navigation, headings, section labelling) behaves as documented."""

import pytest

PAGES = [
    "/",
    "/trades",
    "/trades/new",
    "/trades/1",
    "/trades/1/edit",
    "/trades/1/delete",
    "/instruments",
    "/analytics",
    "/about",
]


@pytest.mark.parametrize("url", PAGES)
def test_every_page_renders_with_sample_data(seeded_client, url):
    response = seeded_client.get(url)
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    html = response.get_data(as_text=True)
    assert "<h1" in html
    assert 'id="main"' in html          # skip-link target
    assert 'class="skip-link"' in html


@pytest.mark.parametrize("url", ["/", "/trades", "/trades/new", "/instruments", "/analytics", "/about"])
def test_pages_render_with_an_empty_database(client, url):
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/no-such-page", "/trades/999", "/trades/999/edit", "/trades/999/delete"])
def test_unknown_pages_and_trades_are_html_404s(seeded_client, url):
    response = seeded_client.get(url)
    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert b"Page not found" in response.data


def test_non_numeric_trade_id_is_404(seeded_client):
    assert seeded_client.get("/trades/abc").status_code == 404


# ---------- Navigation shell ----------

def test_sidebar_lists_every_section(client):
    html = client.get("/").get_data(as_text=True)
    for label in ("Overview", "Trade journal", "Add trade", "Instruments", "Analytics", "About"):
        assert label in html


@pytest.mark.parametrize("url, label", [
    ("/", "Overview"),
    ("/trades", "Trade journal"),
    ("/trades/new", "Add trade"),
    ("/instruments", "Instruments"),
    ("/about", "About"),
])
def test_current_section_is_marked_for_assistive_technology(client, url, label):
    html = client.get(url).get_data(as_text=True)
    marker = html.index('aria-current="page"')
    # The label of the marked link follows the attribute closely.
    assert label in html[marker:marker + 200]
    assert html.count('aria-current="page"') == 1


def test_trade_pages_highlight_the_journal(seeded_client):
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    marker = html.index('aria-current="page"')
    assert "Trade journal" in html[marker:marker + 200]


def test_menu_button_is_wired_for_accessibility(client):
    html = client.get("/").get_data(as_text=True)
    assert 'aria-controls="sidebar"' in html
    assert 'aria-expanded="false"' in html
    assert 'id="sidebar"' in html


def test_no_fake_account_features(client):
    html = client.get("/").get_data(as_text=True).lower()
    for fake in ("log out", "logout", "sign in", "avatar", "notifications"):
        assert fake not in html


def test_scripts_and_styles_are_external_files_only(client):
    """The Content-Security-Policy forbids inline scripts and styles, so
    templates must not contain any."""
    for url in ("/", "/trades/new", "/instruments"):
        html = client.get(url).get_data(as_text=True)
        assert "<script>" not in html
        assert "<style" not in html
        assert " style=" not in html
        assert " onclick=" not in html


@pytest.mark.parametrize("path", ["/static/js/app.js", "/static/js/early.js",
                                  "/static/js/trade-form.js", "/static/css/style.css",
                                  "/static/css/base.css", "/static/css/layout.css",
                                  "/static/css/components.css", "/static/css/pages.css",
                                  "/static/css/analytics.css", "/static/js/analytics.js"])
def test_static_assets_are_served(client, path):
    assert client.get(path).status_code == 200


# ---------- Overview ----------

def test_overview_shows_real_counts_and_recent_trades(seeded_client):
    html = seeded_client.get("/").get_data(as_text=True)
    assert "Recent trades" in html
    # 16 sample trades: 2 open, 14 closed, 5 instruments.
    assert ">2<" in html and ">14<" in html and ">5<" in html
    assert "XAUUSD" in html


def test_overview_empty_state(client):
    html = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" in html
    assert "Your journal is empty" in html


# ---------- Journal ----------

def test_journal_lists_every_column(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    for heading in ("Entry time", "Instrument", "Status", "Entry", "Stop-loss", "Exit",
                    "Size", "Net P&amp;L", "realised R", "Strategy", "Actions"):
        assert heading in html
    assert "Showing 1–16 of 16 trades" in html


def test_journal_labels_outcomes_in_words_not_only_colour(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    # Badges contain the word itself (CSS only changes its letter case).
    for label in ("Win", "Loss", "Breakeven", "Open"):
        assert f">{label}</span>" in html
    assert "Long" in html and "Short" in html


def test_journal_money_carries_its_currency(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    assert "393.00 USD" in html          # sample trade 1
    assert "GBP" in html                 # UK100 trades are in GBP


def test_journal_filters_by_status(seeded_client):
    html = seeded_client.get("/trades?status=OPEN").get_data(as_text=True)
    assert "Showing 1–2 of 2 trades matching your filters" in html


def test_journal_filters_by_strategy(seeded_client):
    html = seeded_client.get("/trades?strategy=Breakout").get_data(as_text=True)
    assert "of 6 trades" in html


def test_journal_search_covers_the_whole_database(seeded_client):
    html = seeded_client.get("/trades?q=xau").get_data(as_text=True)
    assert "of 4 trades" in html
    assert "EURUSD</a>" not in html


def test_journal_search_with_no_matches_shows_empty_state(seeded_client):
    html = seeded_client.get("/trades?q=zzzz-nothing").get_data(as_text=True)
    assert "No trades match" in html


def test_journal_reset_link_present_when_filtering(seeded_client):
    html = seeded_client.get("/trades?status=CLOSED").get_data(as_text=True)
    assert 'href="/trades"' in html
    assert "Reset" in html


@pytest.mark.parametrize("query", ["status=MAYBE", "start=2026-13-01", "instrument_id=abc",
                                   "start=2026-03-10&end=2026-03-01", "q=" + "x" * 101])
def test_invalid_filters_are_400_with_an_explanation(seeded_client, query):
    response = seeded_client.get("/trades?" + query)
    assert response.status_code == 400
    assert b'aria-invalid="true"' in response.data


def test_pagination_keeps_filters(app, seeded_client):
    app.config["JOURNAL_PER_PAGE"] = 5
    html = seeded_client.get("/trades?status=CLOSED").get_data(as_text=True)
    assert "Showing 1–5 of 14 trades" in html
    assert "status=CLOSED" in html and "page=2" in html
    page3 = seeded_client.get("/trades?status=CLOSED&page=3").get_data(as_text=True)
    assert "Showing 11–14 of 14" in page3


def test_out_of_range_page_is_404(app, seeded_client):
    app.config["JOURNAL_PER_PAGE"] = 5
    assert seeded_client.get("/trades?page=99").status_code == 404


# ---------- Trade details ----------

def test_detail_shows_engine_values(seeded_client):
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert "393.00 USD" in html       # net P&L
    assert "400.00 USD" in html       # gross
    assert "200.00 USD" in html       # initial risk
    assert "+1.97R" in html           # realised R, display-rounded
    assert "Edit" in html and "Delete" in html


def test_detail_for_open_trade_has_no_invented_result(seeded_client):
    html = seeded_client.get("/trades/14").get_data(as_text=True)
    assert ">Open</span>" in html
    assert "Not closed yet" in html
    assert "no realised result yet" in html
    assert "(after fees)" not in html                 # the result card is absent
    assert "Planned R:R" in html


def test_detail_for_legacy_trade_says_exit_time_unknown(app, seeded_client):
    with app.app_context():
        from tradeledger.db import get_db
        db = get_db()
        db.execute("UPDATE trades SET closed_at = NULL WHERE id = 1")
        db.commit()
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert "Not recorded" in html
    assert "exit time of this trade was never recorded" in html
    assert "393.00 USD" in html          # the result is still computed


# ---------- Instruments, analytics, about ----------

def test_instruments_page_explains_multiplier_and_warns(seeded_client):
    html = seeded_client.get("/instruments").get_data(as_text=True)
    assert "Contract multiplier" in html
    assert "broker" in html
    assert "EURUSD" in html
    assert "/edit" not in html            # instruments are not editable


def test_analytics_is_the_full_dashboard(seeded_client):
    # Phase 6 replaced the Phase 5 preview with the full dashboard.
    html = seeded_client.get("/analytics").get_data(as_text=True)
    assert "Preview" not in html
    for name in ("curve", "drawdown", "monthly", "weekday", "instruments", "strategies", "sessions"):
        assert f'data-chart="{name}"' in html
    assert "Results by instrument" in html and "Results by strategy" in html


def test_sidebar_no_longer_labels_analytics_as_preview(client):
    # Phase 6: the "Preview" tag was removed when the dashboard was finished.
    html = client.get("/").get_data(as_text=True)
    analytics = html.index(">Analytics<")
    assert "Preview" not in html[analytics:analytics + 200]


def test_about_shows_roadmap_and_boundaries(client):
    html = client.get("/about").get_data(as_text=True)
    assert "Build progress" in html
    assert "Phase 6" in html
```


## 30. `tests/test_api_analytics.py` (MODIFIED)

```python
"""Integration tests for /api/v1/analytics/*.

The expected numbers are the hand-worked sample results from Phase 3 (see
tests/test_report.py). Here we check that the API returns exactly what the
engine calculates, as exact strings, with the documented shapes.
"""

from decimal import Decimal
from fractions import Fraction

import pytest

from tradeledger.db import get_db
from tradeledger.performance import build_report
from tradeledger.performance.formatting import to_jsonable

BASE = "/api/v1/analytics"
ENDPOINTS = ["summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve"]


def contains_float(obj):
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(contains_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(contains_float(v) for v in obj)
    return False


# ---------- Summary ----------

def test_summary_matches_the_engine_exactly(app, seeded_client):
    body = seeded_client.get(f"{BASE}/summary").get_json()
    with app.app_context():
        expected = to_jsonable(build_report(get_db()).overall)
    assert body["data"]["overall"] == expected


def test_summary_hand_worked_values(seeded_client):
    data = seeded_client.get(f"{BASE}/summary").get_json()["data"]
    o = data["overall"]
    assert (o["trades"], o["wins"], o["losses"], o["breakevens"]) == (14, 7, 5, 2)
    assert o["win_rate"] == "0.5"
    expectancy = Fraction(Decimal(o["expectancy_r"]))
    assert abs(expectancy - Fraction(239871, 436800)) < Fraction(1, 10**40)
    assert o["money"]["USD"]["net_pnl"] == "798.5"
    assert o["money"]["GBP"]["profit_factor"] is None      # no GBP losses -> null
    assert data["open_trades"] == 2                         # counted, not in results
    assert data["excluded"] == [] and data["r_unavailable"] == []
    assert data["unknown_close_time_trade_ids"] == []       # sample trades have exit times


def test_summary_meta_explains_conventions(seeded_client):
    meta = seeded_client.get(f"{BASE}/summary").get_json()["meta"]
    # Phase 6 added strategy and session (an additive change to meta).
    assert meta["filters"] == {"instrument_id": None, "strategy": None, "session": None,
                               "start": None, "end": None}
    joined = " ".join(meta["notes"])
    assert "strings" in joined and "never summed across currencies" in joined


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_no_floats_anywhere(seeded_client, endpoint):
    assert not contains_float(seeded_client.get(f"{BASE}/{endpoint}").get_json())


def test_decimal_strings_round_trip_exactly(app, seeded_client):
    total_r = seeded_client.get(f"{BASE}/summary").get_json()["data"]["overall"]["total_r"]
    with app.app_context():
        assert Decimal(total_r) == build_report(get_db()).overall.total_r


# ---------- Groups ----------

def test_by_instrument(seeded_client):
    data = seeded_client.get(f"{BASE}/instruments").get_json()["data"]
    assert [g["group"] for g in data] == ["EURUSD", "GBPUSD", "NAS100", "UK100", "XAUUSD"]
    gold = data[-1]
    assert (gold["trades"], gold["win_rate"]) == (4, "0.75")
    assert list(data[3]["money"]) == ["GBP"]


def test_by_strategy_and_session(seeded_client):
    strategies = seeded_client.get(f"{BASE}/strategies").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in strategies] == [
        ("Breakout", 5), ("Pullback", 5), ("Reversal", 2), ("Unspecified", 2)]
    sessions = seeded_client.get(f"{BASE}/sessions").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in sessions] == [
        ("ASIA", 2), ("LONDON", 6), ("NEW_YORK", 5), ("Unspecified", 1)]


def test_by_weekday_in_calendar_order(seeded_client):
    data = seeded_client.get(f"{BASE}/weekday").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in data] == [
        ("Monday", 3), ("Tuesday", 3), ("Wednesday", 3), ("Thursday", 2), ("Friday", 3)]


def test_monthly(seeded_client):
    data = seeded_client.get(f"{BASE}/monthly").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in data] == [("2026-03", 14)]


# ---------- Curve ----------

def test_curve(seeded_client):
    body = seeded_client.get(f"{BASE}/curve").get_json()
    assert "NOT an account equity curve" in body["meta"]["label"]
    points = body["data"]
    assert [p["trade_id"] for p in points] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]
    assert points[0]["realised_r"] == points[0]["cumulative_r"] == "1.965"
    assert all(p["close_time_known"] for p in points)


# ---------- Filters ----------

def test_instrument_filter_applies_to_every_endpoint(seeded_client):
    for endpoint in ENDPOINTS:
        assert seeded_client.get(f"{BASE}/{endpoint}?instrument_id=3").status_code == 200
    assert seeded_client.get(f"{BASE}/summary?instrument_id=3").get_json()["data"]["overall"]["trades"] == 4
    assert [g["group"] for g in seeded_client.get(f"{BASE}/instruments?instrument_id=3").get_json()["data"]] == ["XAUUSD"]


def test_date_filter_uses_realised_date(app, seeded_client, trade_body):
    # Entered 31 March, closed 1 April: it belongs to April's results.
    seeded_client.post("/api/v1/trades", json={**trade_body, "trade_date": "2026-03-31T22:00:00Z",
                                                "closed_at": "2026-04-01T01:00:00Z"})
    april = seeded_client.get(f"{BASE}/summary?start=2026-04-01&end=2026-04-30").get_json()
    assert april["data"]["overall"]["trades"] == 1
    assert april["meta"]["filters"] == {"instrument_id": None, "strategy": None, "session": None,
                                        "start": "2026-04-01", "end": "2026-04-30"}
    march = seeded_client.get(f"{BASE}/summary?start=2026-03-01&end=2026-03-31").get_json()
    assert march["data"]["overall"]["trades"] == 14


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize("query, field", [
    ("start=2026-02-30", "start"), ("end=soon", "end"),
    ("start=2026-03-10&end=2026-03-01", "end"),
    ("instrument_id=999", "instrument_id"), ("instrument_id=x", "instrument_id"),
])
def test_invalid_filters_are_400(seeded_client, endpoint, query, field):
    response = seeded_client.get(f"{BASE}/{endpoint}?{query}")
    assert response.status_code == 400
    assert field in response.get_json()["error"]["details"]


def test_unknown_analytics_parameter_is_400(seeded_client):
    response = seeded_client.get(f"{BASE}/summary?status=OPEN")
    assert response.get_json()["error"]["code"] == "unknown_parameters"


# ---------- Edge cases ----------

def test_empty_database(client):
    o = client.get(f"{BASE}/summary").get_json()["data"]["overall"]
    assert o["trades"] == 0
    assert o["win_rate"] is None and o["expectancy_r"] is None and o["profit_factor_r"] is None
    assert o["total_r"] == "0" and o["money"] == {}
    for endpoint in ENDPOINTS[1:]:
        assert client.get(f"{BASE}/{endpoint}").get_json()["data"] == []


def test_open_trades_do_not_affect_realised_results(seeded_client, trade_body):
    before = seeded_client.get(f"{BASE}/summary").get_json()["data"]
    open_trade = {k: v for k, v in trade_body.items() if k not in ("exit_price", "closed_at", "status")}
    seeded_client.post("/api/v1/trades", json=open_trade)
    after = seeded_client.get(f"{BASE}/summary").get_json()["data"]
    assert after["overall"] == before["overall"]
    assert after["open_trades"] == before["open_trades"] + 1


def test_breakeven_and_mixed_currencies(client):
    for instrument in (
        {"symbol": "AAA", "name": "Test USD", "asset_class": "OTHER", "price_increment": "1",
         "contract_multiplier": "1", "quote_currency": "USD"},
        {"symbol": "BBB", "name": "Test GBP", "asset_class": "OTHER", "price_increment": "1",
         "contract_multiplier": "1", "quote_currency": "GBP"},
    ):
        assert client.post("/api/v1/instruments", json=instrument).status_code == 201
    common = {"trade_date": "2026-05-04T09:00:00Z", "closed_at": "2026-05-04T10:00:00Z",
              "direction": "LONG", "entry_price": "100", "stop_loss": "90", "position_size": "1"}
    client.post("/api/v1/trades", json={**common, "instrument_id": 1, "exit_price": "120"})  # +20 USD
    client.post("/api/v1/trades", json={**common, "instrument_id": 2, "exit_price": "100"})  # 0 GBP
    o = client.get(f"{BASE}/summary").get_json()["data"]["overall"]
    assert (o["wins"], o["breakevens"]) == (1, 1)
    assert o["money"]["USD"]["net_pnl"] == "20" and o["money"]["GBP"]["net_pnl"] == "0"
    assert "net_pnl" not in o                # no cross-currency total exists
    assert o["total_r"] == "2"               # R can be combined: +2R + 0R


def test_analytics_are_read_only(app, seeded_client):
    from tests.factories import table_snapshot

    before = table_snapshot(app, "trades")
    for endpoint in ENDPOINTS:
        seeded_client.get(f"{BASE}/{endpoint}")
    assert table_snapshot(app, "trades") == before
    assert seeded_client.post(f"{BASE}/summary", json={}).status_code == 405
```


## 31. `scripts/qa_dashboard_agreement.py` (NEW, added in the final QA review)

```python
"""Phase 6 QA: does everything the dashboard shows agree with an
INDEPENDENT recalculation from the raw database rows?

An audit, not part of pytest (it takes ~15 s and needs Node.js). It builds
a throwaway database (the sample data plus 600 random trades in three
currencies, 5% open, 3% with no exit time), recomputes every figure from
the raw SQL rows with its OWN code (it never calls the engine), then
compares four outputs against that for every filter combination:
  1. the HTML page (KPI cards, money strip, all detailed tables)
  2. the chart payload embedded in the page
  3. the JSON API (/api/v1/analytics/*)
  4. the tooltip text produced by the REAL analytics.js (run in Node)

Usage (from the project folder):
    python scripts/qa_dashboard_agreement.py            # finds node on PATH
    python scripts/qa_dashboard_agreement.py --node /path/to/node

Exit code 0 = no disagreements. Precision trap: every Decimal sum here runs
inside localcontext(prec=80); Python's default context keeps only 28 digits.
"""

import itertools
import json
import random
import shutil
import re
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, localcontext
from html import unescape
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema  # noqa: E402
from tradeledger.repositories.instruments import create_instrument  # noqa: E402
from tradeledger.repositories.trades import create_trade  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

TOL = Decimal("1e-30")
SESSION_LABELS = {"ASIA": "Asia", "LONDON": "London", "NEW_YORK": "New York", "OTHER": "Other",
                  None: "Unspecified"}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
problems = []
checks = 0


def check(condition, message):
    global checks
    checks += 1
    if not condition:
        problems.append(message)


# ---------- My own formatting (written from the documented rules) ----------

def q(value, places):
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def fmt_r(v):
    if v is None:
        return "n/a"
    r = q(v, "0.01")
    return "0.00R" if r == 0 else f"{r:+}R"


def fmt_rate(wins, total):
    return "n/a" if total == 0 else f"{q(Decimal(wins) * 100 / Decimal(total), '0.1')}%"


def fmt_ratio(v):
    return "n/a" if v is None else str(q(v, "0.01"))


# ---------- Build a database: sample data + 600 random trades ----------

def build_database(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path})
    rnd = random.Random(20261115)
    with app.app_context():
        conn = get_db()
        init_schema(conn)
        seed_sample_data(conn)
        extra = [create_instrument(conn, {"symbol": sym, "name": sym, "asset_class": "OTHER",
                                          "price_increment": "0.01", "contract_multiplier": mult,
                                          "quote_currency": cur})
                 for sym, mult, cur in (("QAUSD", "10", "USD"), ("QAGBP", "1", "GBP"),
                                        ("QAEUR", "0.5", "EUR"))]
        strategies = ["Breakout", "Pullback", "Scalp", "Fade", None]
        sessions = ["ASIA", "LONDON", "NEW_YORK", "OTHER", None]
        for _ in range(600):
            direction = rnd.choice(["LONG", "SHORT"])
            entry = Decimal(rnd.randint(5000, 20000)) / 100
            risk = Decimal(rnd.randint(10, 300)) / 100
            stop = entry - risk if direction == "LONG" else entry + risk
            move = Decimal(rnd.randint(-400, 400)) / 100
            exit_price = max(Decimal("0.01"), entry + move)
            start = datetime(2025, 11, 1, tzinfo=timezone.utc)
            entered = start + (datetime(2026, 4, 30, tzinfo=timezone.utc) - start) * rnd.random()
            entered = entered.replace(microsecond=0)
            closed = entered.timestamp() + rnd.randint(60, 5 * 86400)
            closed = datetime.fromtimestamp(closed, timezone.utc)
            is_open = rnd.random() < 0.05
            create_trade(conn, {
                "instrument_id": rnd.choice(extra), "direction": direction,
                "status": "OPEN" if is_open else "CLOSED",
                "trade_date": entered.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "closed_at": None if is_open else closed.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "entry_price": str(entry), "stop_loss": str(stop),
                "exit_price": None if is_open else str(exit_price),
                "position_size": str(rnd.choice(["1", "2", "0.5", "3"])),
                "fees": str(Decimal(rnd.randint(0, 300)) / 100),
                "strategy": rnd.choice(strategies), "session": rnd.choice(sessions),
            })
        # 3% legacy trades: exit time never recorded.
        ids = [r[0] for r in conn.execute("SELECT id FROM trades WHERE status='CLOSED' AND id > 16")]
        for trade_id in rnd.sample(ids, len(ids) * 3 // 100):
            conn.execute("UPDATE trades SET closed_at = NULL WHERE id = ?", (trade_id,))
        conn.commit()
    return app


# ---------- Independent recalculation from raw rows ----------

def raw_rows(app):
    with app.app_context():
        return [dict(r) for r in get_db().execute(
            "SELECT t.*, i.symbol, i.contract_multiplier, i.quote_currency "
            "FROM trades t JOIN instruments i ON i.id = t.instrument_id")]


def independent(rows, f):
    with localcontext() as ctx:
        ctx.prec = 80
        out = []
        open_count = 0
        for t in rows:
            if f["instrument_id"] and t["instrument_id"] != f["instrument_id"]:
                continue
            if f["strategy"] and t["strategy"] != f["strategy"]:
                continue
            if f["session"] and t["session"] != f["session"]:
                continue
            if t["status"] == "OPEN":
                open_count += 1 if not (f["start"] or f["end"]) else 0
                continue
            realised = t["closed_at"] or t["trade_date"]
            day = realised[:10]
            if (f["start"] and day < f["start"]) or (f["end"] and day > f["end"]):
                continue
            sign = 1 if t["direction"] == "LONG" else -1
            entry, stop, exit_ = (Decimal(t[k]) for k in ("entry_price", "stop_loss", "exit_price"))
            size, mult = Decimal(t["position_size"]), Decimal(t["contract_multiplier"])
            net = (exit_ - entry) * sign * size * mult - Decimal(t["fees"] or "0")
            risk = abs(entry - stop) * size * mult
            out.append({"id": t["id"], "when": realised, "symbol": t["symbol"], "net": net,
                        "r": net / risk, "cur": t["quote_currency"], "strategy": t["strategy"],
                        "session": t["session"]})
        out.sort(key=lambda x: (x["when"], x["id"]))
        return out, open_count


def summary(trades):
    with localcontext() as ctx:
        ctx.prec = 80
        n = len(trades)
        wins = sum(1 for t in trades if t["net"] > 0)
        losses = sum(1 for t in trades if t["net"] < 0)
        total = sum((t["r"] for t in trades), Decimal(0))
        gains = sum((t["r"] for t in trades if t["r"] > 0), Decimal(0))
        pains = -sum((t["r"] for t in trades if t["r"] < 0), Decimal(0))
        cum, peak, worst = Decimal(0), Decimal(0), Decimal(0)
        for t in trades:
            cum += t["r"]
            peak = max(peak, cum)
            worst = max(worst, peak - cum)
        streaks = {"WIN": 0, "LOSS": 0}
        run_kind, run = None, 0
        for t in trades:
            kind = "WIN" if t["net"] > 0 else "LOSS" if t["net"] < 0 else None
            run = run + 1 if kind and kind == run_kind else (1 if kind else 0)
            run_kind = kind
            if kind:
                streaks[kind] = max(streaks[kind], run)
        return {"trades": n, "wins": wins, "losses": losses, "breakevens": n - wins - losses,
                "total_r": total, "mean_r": total / n if n else None,
                "pf": gains / pains if pains else None, "max_dd": worst,
                "win_streak": streaks["WIN"], "loss_streak": streaks["LOSS"]}


def grouped(trades, key):
    groups = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: summary(v) for k, v in groups.items()}


# ---------- Reading the page ----------

def kpis(html):
    return dict(re.findall(r'data-kpi="([a-z_]+)">.*?class="kpi__value">([^<]*)<', html, re.S))


def strip(fragment):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def table(html, table_id):
    match = re.search(rf'id="{table_id}".*?<tbody>(.*?)</tbody>', html, re.S)
    if not match:
        return None
    rows = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.S):
        head = strip(re.search(r"<th[^>]*>(.*?)</th>", row, re.S).group(1))
        head = head.replace("Small sample", "").strip()
        rows[head] = [strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
    return rows


def payload_of(html):
    m = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


# ---------- Compare one filter combination ----------

def compare(client, rows, f, collected):
    query = "&".join(f"{k}={v}" for k, v in f.items() if v)
    tag = query or "(no filters)"
    response = client.get("/analytics?" + query)
    check(response.status_code == 200, f"{tag}: page status {response.status_code}")
    html = response.get_data(as_text=True)
    trades, _ = independent(rows, f)
    s = summary(trades)
    k = kpis(html)

    expected = {"trades": str(s["trades"])}
    if s["trades"]:
        expected.update({
            "wins": str(s["wins"]), "losses": str(s["losses"]), "breakevens": str(s["breakevens"]),
            "win_rate": fmt_rate(s["wins"], s["trades"]), "average_r": fmt_r(s["mean_r"]),
            "expectancy_r": fmt_r(s["mean_r"]), "profit_factor_r": fmt_ratio(s["pf"]),
            "total_r": fmt_r(s["total_r"]), "max_drawdown_r": f"{q(s['max_dd'], '0.01')}R",
            "longest_win_streak": f"{s['win_streak']} trade" + ("" if s["win_streak"] == 1 else "s"),
            "longest_loss_streak": f"{s['loss_streak']} trade" + ("" if s["loss_streak"] == 1 else "s"),
        })
    else:
        expected.update({key: "n/a" for key in ("wins", "win_rate", "average_r", "expectancy_r",
                                                "profit_factor_r", "total_r", "max_drawdown_r")})
    for key, value in expected.items():
        check(k.get(key) == value, f"{tag}: KPI {key} page={k.get(key)!r} independent={value!r}")

    # Money per currency, never combined.
    money = defaultdict(Decimal)
    for t in trades:
        money[t["cur"]] += t["net"]
    for cur, net in money.items():
        check(f"{q(net, '0.01')} {cur}" in html, f"{tag}: money {q(net, '0.01')} {cur} missing")

    api = {name: client.get(f"/api/v1/analytics/{name}?{query}").get_json()
           for name in ("summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve")}
    o = api["summary"]["data"]["overall"]
    check(o["trades"] == s["trades"], f"{tag}: API trades")
    check(abs(Decimal(o["total_r"]) - s["total_r"]) < TOL, f"{tag}: API total_r")

    if not s["trades"]:
        check(payload_of(html) is None, f"{tag}: payload present with no results")
        return

    payload = payload_of(html)
    collected.append({"tag": tag, "payload": payload})

    # Curve: same trades, same order, same exact cumulative values.
    cum = Decimal(0)
    points = payload["curve"][1:]
    check([p["trade_id"] for p in points] == [t["id"] for t in trades], f"{tag}: curve order")
    check([p["trade_id"] for p in api["curve"]["data"]] == [t["id"] for t in trades], f"{tag}: API curve order")
    for p, t in zip(points, trades):
        with localcontext() as ctx:          # same precision as independent()
            ctx.prec = 80
            cum += t["r"]
        check(abs(Decimal(p["cumulative_r"]) - cum) < TOL, f"{tag}: curve #{t['id']} cumulative")
        check(p["cumulative_text"] == fmt_r(cum), f"{tag}: curve #{t['id']} text")
    check(abs(Decimal(payload["max_drawdown"]["amount"]) - s["max_dd"]) < TOL, f"{tag}: payload max drawdown")

    # Groups: page table, payload and API all equal the independent figures.
    specs = [
        ("instruments", "table-instruments", lambda t: t["symbol"], lambda key: key, "instruments"),
        ("strategies", "table-strategies", lambda t: t["strategy"] or "Unspecified", lambda key: key, "strategies"),
        ("sessions", "table-sessions", lambda t: t["session"], lambda key: SESSION_LABELS[key], "sessions"),
    ]
    for name, table_id, key, label, api_name in specs:
        truth = {label(g): v for g, v in grouped(trades, key).items()}
        page_rows = table(html, table_id)
        check(set(page_rows) == set(truth), f"{tag}: {name} table rows {sorted(page_rows)} vs {sorted(truth)}")
        for group, v in truth.items():
            cells = page_rows.get(group)
            if not cells:
                continue
            check(cells[0] == str(v["trades"]), f"{tag}: {name}/{group} trades")
            check(cells[1] == f"{v['wins']} / {v['losses']} / {v['breakevens']}", f"{tag}: {name}/{group} w/l/b")
            check(cells[2] == fmt_rate(v["wins"], v["trades"]), f"{tag}: {name}/{group} win rate")
            check(cells[5] == fmt_r(v["total_r"]), f"{tag}: {name}/{group} total {cells[5]} vs {fmt_r(v['total_r'])}")
        pay = {p["label"]: p for p in payload[name]}
        check(set(pay) == set(truth), f"{tag}: {name} payload labels")
        for group, v in truth.items():
            if group in pay:
                check(abs(Decimal(pay[group]["total_r"]) - v["total_r"]) < TOL, f"{tag}: {name}/{group} payload")
        api_rows = {(SESSION_LABELS[g["group"]] if name == "sessions" and g["group"] in SESSION_LABELS
                     else g["group"]): g for g in api[api_name]["data"]}
        check(set(api_rows) == set(truth), f"{tag}: {name} API groups {sorted(api_rows)}")
        # Order: best total R first (instruments/strategies).
        if name != "sessions":
            order = [p["label"] for p in payload[name]]
            check(order == sorted(order, key=lambda g: (-truth[g]["total_r"], g)), f"{tag}: {name} order")

    # Months: every month from first to last, gaps kept.
    month_truth = grouped(trades, lambda t: t["when"][:7])
    first, last = min(month_truth), max(month_truth)
    expected_keys, y, m = [], int(first[:4]), int(first[5:])
    while f"{y:04d}-{m:02d}" <= last:
        expected_keys.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    check([p["key"] for p in payload["monthly"]] == expected_keys, f"{tag}: month keys")
    for p in payload["monthly"]:
        v = month_truth.get(p["key"])
        if v is None:
            check(p["total_r"] is None, f"{tag}: empty month {p['key']} not null")
        else:
            check(abs(Decimal(p["total_r"]) - v["total_r"]) < TOL, f"{tag}: month {p['key']}")
    month_table = table(html, "table-monthly")
    check(len(month_table) == len(expected_keys), f"{tag}: month table rows")

    # Weekdays, UTC.
    def weekday(t):
        return WEEKDAYS[datetime.strptime(t["when"], "%Y-%m-%dT%H:%M:%SZ").weekday()]
    day_truth = grouped(trades, weekday)
    check([p["key"] for p in payload["weekday"]] == WEEKDAYS, f"{tag}: weekday order")
    day_table = table(html, "table-weekday")
    for p in payload["weekday"]:
        v = day_truth.get(p["key"])
        cells = day_table[p["key"]]
        if v is None:
            check(p["total_r"] is None and cells == ["No closed trades"], f"{tag}: {p['key']} empty")
        else:
            check(abs(Decimal(p["total_r"]) - v["total_r"]) < TOL, f"{tag}: weekday {p['key']}")
            check(cells[5] == fmt_r(v["total_r"]), f"{tag}: weekday table {p['key']}")


NODE_SCRIPT = r"""
const fs = require("fs");
const charts = require(process.argv[2]);
const items = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const theme = {text: "#fff", muted: "#aaa", grid: "#333", zero: "#999", accent: "#00f",
               positive: "#0f0", negative: "#f00", font: "sans-serif"};
const out = items.map(({tag, payload}) => {
  const c = charts.buildConfigs(payload, theme);
  const res = {tag};
  for (const name of Object.keys(c)) {
    const cb = c[name].options.plugins.tooltip.callbacks;
    const n = c[name].data.datasets[0].data.length;
    res[name] = {labels: c[name].data.labels || null, data: c[name].data.datasets[0].data,
                 tooltips: Array.from({length: n}, (_, i) => cb.label({dataIndex: i})),
                 titles: Array.from({length: n}, (_, i) => cb.title ? cb.title([{dataIndex: i}]) : null)};
  }
  return res;
});
fs.writeFileSync(process.argv[4], JSON.stringify(out));
"""


def check_tooltips(collected, node):
    work = Path(tempfile.mkdtemp())
    (work / "run.js").write_text(NODE_SCRIPT)
    (work / "in.json").write_text(json.dumps(collected))
    subprocess.run([node, str(work / "run.js"), str(PROJECT / "tradeledger/static/js/analytics.js"),
                    str(work / "in.json"), str(work / "out.json")], check=True)
    results = json.loads((work / "out.json").read_text())
    for item, res in zip(collected, results):
        tag, payload = item["tag"], item["payload"]
        # Curve: every point's tooltip quotes the server's exact display text,
        # and the plotted y equals the exact value converted once.
        for i, p in enumerate(payload["curve"]):
            lines = res["curve"]["tooltips"][i]
            check(res["curve"]["data"][i]["y"] == float(Decimal(p["cumulative_r"])), f"{tag}: curve y {i}")
            if not p.get("start"):
                check(f"Cumulative R: {p['cumulative_text']}" in lines, f"{tag}: curve tooltip {i}")
                check(f"Trade R: {p['realised_text']}" in lines, f"{tag}: trade R tooltip {i}")
                check(("Exit time not recorded: placed at entry time" in lines) == (not p["close_time_known"]),
                      f"{tag}: legacy note {i}")
            dd = res["drawdown"]["tooltips"][i][0]
            check(dd == (f"Drawdown: {p['drawdown_text']} below the running peak"
                         if Decimal(p["underwater_r"]) != 0 else dd), f"{tag}: drawdown tooltip {i}")
        # Bars: tooltip text equals payload text; null periods stay null.
        for name in ("monthly", "weekday", "instruments", "strategies", "sessions"):
            for i, row in enumerate(payload[name]):
                lines = res[name]["tooltips"][i]
                if row["total_r"] is None:
                    check(res[name]["data"][i] is None and lines == ["No closed trades"], f"{tag}: {name} gap {i}")
                else:
                    check(lines[0] == f"Total R: {row['total_r_text']}", f"{tag}: {name} tooltip {i}")
                    check(f"Closed trades: {row['trades']} (won {row['wins']}, lost {row['losses']})" in lines,
                          f"{tag}: {name} count tooltip {i}")
                    check(res[name]["data"][i] == float(Decimal(row["total_r"])), f"{tag}: {name} value {i}")
    return len(results)


def main():
    node = sys.argv[sys.argv.index("--node") + 1] if "--node" in sys.argv else shutil.which("node")
    if not node:
        sys.exit("Node.js is needed to run analytics.js; pass --node /path/to/node")
    work = tempfile.mkdtemp()
    app = build_database(str(Path(work) / "qa.db"))
    rows = raw_rows(app)
    client = app.test_client()
    with app.app_context():
        instrument_ids = [r[0] for r in get_db().execute("SELECT id FROM instruments ORDER BY id")]
    strategies = [None, "Breakout", "Pullback", "Scalp", "Reversal"]
    sessions = [None, "ASIA", "LONDON", "NEW_YORK", "OTHER"]
    dates = [(None, None), ("2026-01-01", "2026-02-28"), ("2025-12-15", None), ("2026-03-02", "2026-03-02")]
    collected = []
    combos = 0
    for inst, strat, sess, (start, end) in itertools.product([None] + instrument_ids, strategies, sessions, dates):
        f = {"instrument_id": inst, "strategy": strat, "session": sess, "start": start, "end": end}
        compare(client, rows, f, collected)
        combos += 1
    charted = check_tooltips(collected, node)
    closed = sum(1 for r in rows if r["status"] == "CLOSED")
    legacy = sum(1 for r in rows if r["status"] == "CLOSED" and not r["closed_at"])
    print(f"trades in database: {len(rows)} ({closed} closed, {legacy} with no exit time)")
    print(f"filter combinations: {combos}; with results (charts checked in Node): {charted}")
    print(f"individual comparisons: {checks}; disagreements: {len(problems)}")
    for p in problems[:30]:
        print("  -", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
```
