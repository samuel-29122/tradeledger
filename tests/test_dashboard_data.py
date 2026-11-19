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

from tests.factories import USER_ID, make_trade
from tradeledger.performance import analyse
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


def D(text):
    return Decimal(text)


@pytest.fixture
def hand_conn(conn):
    usd = create_instrument(
        conn,
        {
            "symbol": "TEST",
            "name": "Test USD",
            "asset_class": "OTHER",
            "price_increment": "0.01",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        user_id=USER_ID,
    )
    gbp = create_instrument(
        conn,
        {
            "symbol": "GBPX",
            "name": "Test GBP",
            "asset_class": "OTHER",
            "price_increment": "0.01",
            "contract_multiplier": "1",
            "quote_currency": "GBP",
        },
        user_id=USER_ID,
    )

    def trade(instrument, entered, closed, exit_price, strategy=None, session=None):
        create_trade(
            conn,
            {
                "instrument_id": instrument,
                "direction": "LONG",
                "status": "CLOSED" if closed else "OPEN",
                "trade_date": entered,
                "closed_at": closed,
                "entry_price": "100",
                "stop_loss": "99",
                "exit_price": exit_price,
                "position_size": "1",
                "fees": "0",
                "strategy": strategy,
                "session": session,
            },
            user_id=USER_ID,
        )

    trade(usd, "2025-12-31T10:00:00Z", "2025-12-31T23:30:00Z", "102", "Alpha", "LONDON")
    trade(usd, "2026-01-01T20:00:00Z", "2026-01-02T09:00:00Z", "99", "Beta")
    trade(usd, "2025-12-30T09:00:00Z", "2026-01-02T09:00:00Z", "99", None, "ASIA")
    trade(gbp, "2026-03-06T08:00:00Z", "2026-03-07T12:00:00Z", "100.5", "Alpha", "LONDON")
    trade(usd, "2026-03-09T08:00:00Z", None, None)
    return conn


@pytest.fixture
def board(hand_conn):
    return build_dashboard(hand_conn, ReportFilters(), user_id=USER_ID)


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
    report = analyse(
        [
            make_trade(id=1, exit_price=D("90"), closed_at="2026-01-05T11:00:00Z"),
            make_trade(id=2, exit_price=D("85"), closed_at="2026-01-06T11:00:00Z"),
        ]
    )
    series = drawdown_series(report.cumulative_r)
    assert [p.cumulative_r for p in series] == [D("-1"), D("-2.5")]
    assert [p.peak_r for p in series] == [D("0"), D("0")]
    assert [p.drawdown_r for p in series] == [D("1"), D("2.5")]
    board = make_dashboard(report, ReportFilters())
    assert "from the starting point (0R)" in board.descriptions["drawdown"]


def test_only_winning_trades_have_no_drawdown():
    report = analyse(
        [
            make_trade(id=1, closed_at="2026-01-05T11:00:00Z"),
            make_trade(id=2, closed_at="2026-01-06T11:00:00Z"),
        ]
    )
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
    rows = [
        (m.key, m.label, None if m.summary is None else (m.summary.trades, m.summary.total_r))
        for m in board.months
    ]
    assert rows == [
        ("2025-12", "Dec 2025", (1, D("2"))),
        ("2026-01", "Jan 2026", (2, D("-2"))),
        ("2026-02", "Feb 2026", None),  # no trades: not "0R"
        ("2026-03", "Mar 2026", (1, D("0.5"))),
    ]


def test_weekdays_are_all_seven_in_calendar_order(board):
    rows = [(w.key, None if w.summary is None else w.summary.total_r) for w in board.weekdays]
    assert rows == [
        ("Monday", None),
        ("Tuesday", None),
        ("Wednesday", D("2")),
        ("Thursday", None),
        ("Friday", D("-2")),
        ("Saturday", D("0.5")),
        ("Sunday", None),
    ]


def test_empty_inputs_give_empty_periods():
    assert month_rows({}) == []
    assert [w.summary for w in weekday_rows({})] == [None] * 7


# ---------- Groups ----------


def test_strategies_best_first_with_stable_ties(board):
    assert [(g.label, g.summary.trades, g.summary.total_r) for g in board.strategies] == [
        ("Alpha", 2, D("2.5")),
        ("Beta", 1, D("-1")),
        ("Unspecified", 1, D("-1")),
    ]


def test_sessions_in_trading_day_order_with_readable_labels(board):
    assert [(g.key, g.label, g.summary.total_r) for g in board.sessions] == [
        ("ASIA", "Asia", D("-1")),
        ("LONDON", "London", D("2.5")),
        ("Unspecified", "Unspecified", D("-1")),
    ]


def test_instruments_compared_in_r_not_money(board):
    assert [(g.label, g.summary.total_r) for g in board.instruments] == [("GBPX", D("0.5")), ("TEST", D("0"))]


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
        "average_r": ("+0.13R", "positive"),  # 0.125 rounds half up
        "expectancy_r": ("+0.13R", "positive"),
        "profit_factor_r": ("1.25", "neutral"),
        "total_r": ("+0.50R", "positive"),
        "max_drawdown_r": ("2.00R", "negative"),
        "longest_win_streak": ("1 trade", "neutral"),
        "longest_loss_streak": ("2 trades", "neutral"),
    }
    assert {k.key: k.note for k in board.kpis}["trades"] == "1 open trade not included"


def test_money_stays_per_currency(board):
    assert money_rows(board.report) == [("GBP", "0.50 GBP", "positive", 1), ("USD", "0.00 USD", "neutral", 3)]


def test_empty_report_shows_unavailable_not_zero(conn):
    board = build_dashboard(conn, ReportFilters(), user_id=USER_ID)
    assert not board.has_results
    kpis = {k.key: k.value for k in board.kpis}
    assert kpis["trades"] == "0"
    for key in (
        "wins",
        "win_rate",
        "average_r",
        "expectancy_r",
        "profit_factor_r",
        "total_r",
        "max_drawdown_r",
        "longest_win_streak",
        "longest_loss_streak",
    ):
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
    report = analyse(
        [
            make_trade(id=1, closed_at="2026-01-05T11:00:00Z"),
            make_trade(id=2, stop_loss=D("105"), closed_at="2026-01-06T11:00:00Z"),
        ]
    )
    board = make_dashboard(report, ReportFilters())
    assert board.report.overall.trades == 2
    assert [p.trade_id for p in board.series] == [1]
    assert [e.trade_id for e in board.report.r_unavailable] == [2]


def test_legacy_trade_is_placed_at_entry_time_and_flagged(hand_conn):
    hand_conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 3")
    hand_conn.commit()
    board = build_dashboard(hand_conn, ReportFilters(), user_id=USER_ID)
    # Trade 3 now counts at its ENTRY time, 2025-12-30, so it comes first.
    assert [p.trade_id for p in board.series] == [3, 1, 2, 4]
    legacy = board.series[0]
    assert legacy.close_time_known is False and legacy.realised_at == "2025-12-30T09:00:00Z"
    assert board.report.unknown_close_time_trade_ids == [3]


# ---------- Filters reach the dashboard ----------


def test_filters_change_every_part_of_the_dashboard(hand_conn):
    board = build_dashboard(hand_conn, ReportFilters(strategy="Alpha"), user_id=USER_ID)
    assert [p.trade_id for p in board.series] == [1, 4]
    assert [m.key for m in board.months] == ["2025-12", "2026-01", "2026-02", "2026-03"]
    assert {k.key: k.value for k in board.kpis}["total_r"] == "+2.50R"
    assert [g.label for g in board.strategies] == ["Alpha"]


def test_date_filter_on_realised_date(hand_conn):
    board = build_dashboard(hand_conn, ReportFilters(start="2026-01-01", end="2026-01-31"), user_id=USER_ID)
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
    json.dumps(payload, allow_nan=False)  # raises if NaN/Infinity slipped in


def test_payload_curve_values(board):
    curve = chart_payload(board)["curve"]
    assert curve[0] == {
        "start": True,
        "x": board.series[0].x,
        "cumulative_r": "0",
        "cumulative_text": "0.00R",
        "underwater_r": "0",
        "drawdown_text": "0.00R",
    }
    assert [(p["trade_id"], p["realised_r"], p["cumulative_r"], p["underwater_r"]) for p in curve[1:]] == [
        (1, "2", "2", "0"),
        (2, "-1", "1", "-1"),
        (3, "-1", "0", "-2"),
        (4, "0.5", "0.5", "-1.5"),
    ]
    assert curve[3]["when"] == "02 Jan 2026, 09:00 UTC"
    assert curve[3]["realised_text"] == "-1.00R"


def test_payload_numeric_strings_are_finite_decimals(board):
    payload = chart_payload(board)
    values = [p[k] for p in payload["curve"] for k in ("cumulative_r", "underwater_r")]
    values += [
        row["total_r"]
        for key in ("monthly", "weekday", "instruments", "strategies", "sessions")
        for row in payload[key]
        if row["total_r"] is not None
    ]
    for text in values:
        assert Decimal(text).is_finite(), text


def test_payload_marks_empty_periods_as_null(board):
    monthly = {m["key"]: m for m in chart_payload(board)["monthly"]}
    assert monthly["2026-02"]["total_r"] is None and monthly["2026-02"]["trades"] == 0
    assert monthly["2026-01"]["total_r"] == "-2" and monthly["2026-01"]["total_r_text"] == "-2.00R"


def test_payload_and_tables_agree(board):
    """The chart payload is cut from the same rows the tables render."""
    payload = chart_payload(board)
    for key, rows in (
        ("instruments", board.instruments),
        ("strategies", board.strategies),
        ("sessions", board.sessions),
    ):
        assert [(p["label"], p["trades"], Decimal(p["total_r"])) for p in payload[key]] == [
            (r.label, r.summary.trades, r.summary.total_r) for r in rows
        ]


def test_payload_contains_no_free_text_from_trades(hand_conn):
    hand_conn.execute("UPDATE trades SET notes = 'SECRET-NOTE <script>' WHERE id = 1")
    hand_conn.commit()
    text = json.dumps(chart_payload(build_dashboard(hand_conn, ReportFilters(), user_id=USER_ID)))
    assert "SECRET-NOTE" not in text


# ---------- Sample data and scale ----------


def test_sample_data_series_ends_at_engine_total(seeded_conn):
    board = build_dashboard(seeded_conn, ReportFilters(), user_id=USER_ID)
    assert board.series[-1].cumulative_r == board.report.overall.total_r
    assert max(p.drawdown_r for p in board.series) == board.report.overall.max_drawdown_r.amount


def test_one_thousand_trades(conn):
    """1,000 synthetic trades: the dashboard still adds up. (Timing is
    measured separately by scripts/benchmark_dashboard.py, not asserted.)"""
    instrument = create_instrument(
        conn,
        {
            "symbol": "BULK",
            "name": "Bulk",
            "asset_class": "OTHER",
            "price_increment": "0.01",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        user_id=USER_ID,
    )
    for n in range(1000):
        exit_price = ("102", "99", "100.5", "99")[n % 4]  # +2, -1, +0.5, -1 repeating
        day = 1 + (n // 40)
        create_trade(
            conn,
            {
                "instrument_id": instrument,
                "direction": "LONG",
                "status": "CLOSED",
                "trade_date": f"2026-{1 + (day - 1) // 28:02d}-{1 + (day - 1) % 28:02d}T08:00:00Z",
                "closed_at": f"2026-{1 + (day - 1) // 28:02d}-{1 + (day - 1) % 28:02d}T{(n % 40) // 4 + 9:02d}:{n % 4:02d}:00Z",
                "entry_price": "100",
                "stop_loss": "99",
                "exit_price": exit_price,
                "position_size": "1",
            },
            user_id=USER_ID,
        )
    board = build_dashboard(conn, ReportFilters(), user_id=USER_ID)
    assert board.report.overall.trades == 1000
    assert len(board.series) == 1000
    assert board.series[-1].cumulative_r == D("125")  # 250 cycles of +0.5R
    assert board.report.overall.total_r == D("125")
    # The HTML table keeps only the most recent rows; the chart keeps all.
    assert SERIES_TABLE_LIMIT == 200
    assert board.series_table == board.series[-200:]
