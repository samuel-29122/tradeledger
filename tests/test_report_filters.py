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

from tests.factories import USER_ID, table_snapshot
from tests.test_openapi import validate
from tradeledger.performance import build_report
from tradeledger.report_filters import FILTER_NAMES, ReportFilters, parse_report_filters
from tradeledger.repositories import trades as trade_repo
from tradeledger.validation import ValidationError

BASE = "/api/v1/analytics"
ENDPOINTS = ["summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve"]


def parse(conn, blank_means_unset=True, **args):
    return parse_report_filters(args, conn, blank_means_unset=blank_means_unset, user_id=USER_ID)


# ---------- 1. Validation ----------


def test_no_arguments_means_no_filters(seeded_conn):
    filters, errors = parse(seeded_conn)
    assert errors == {}
    assert filters == ReportFilters()
    assert filters.is_empty and filters.active() == {}


def test_all_filters_valid(seeded_conn):
    filters, errors = parse(
        seeded_conn,
        instrument_id="1",
        strategy=" Breakout ",
        session="london",
        start="2026-03-01",
        end="2026-03-31",
    )
    assert errors == {}
    assert filters == ReportFilters(1, "Breakout", "LONDON", "2026-03-01", "2026-03-31")
    assert list(filters.as_kwargs()) == list(FILTER_NAMES)


def test_blank_values_mean_all_on_the_website(seeded_conn):
    filters, errors = parse(seeded_conn, instrument_id="", strategy="  ", session="", start="", end="")
    assert errors == {} and filters.is_empty


def test_blank_values_are_errors_in_the_api(seeded_conn):
    _, errors = parse(seeded_conn, blank_means_unset=False, instrument_id="", strategy=" ", session="")
    assert set(errors) == {"instrument_id", "strategy", "session"}


@pytest.mark.parametrize(
    "value, message",
    [
        ("abc", "must be a whole number of 1 or more"),
        ("0", "must be a whole number of 1 or more"),
        ("-1", "must be a whole number of 1 or more"),
        ("1.5", "must be a whole number of 1 or more"),
        ("999", "does not match an existing instrument"),
    ],
)
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
        "session": "must be one of: ASIA, LONDON, NEW_YORK, OTHER"
    }


@pytest.mark.parametrize(
    "args, field",
    [
        ({"start": "2026-02-30"}, "start"),
        ({"end": "next week"}, "end"),
        ({"start": "2026-03-10", "end": "2026-03-09"}, "end"),
    ],
)
def test_bad_dates(seeded_conn, args, field):
    assert field in parse(seeded_conn, **args)[1]


def test_single_day_range_is_valid(seeded_conn):
    assert parse(seeded_conn, start="2026-03-02", end="2026-03-02")[1] == {}


def test_every_problem_is_reported_at_once(seeded_conn):
    _, errors = parse(seeded_conn, instrument_id="x", session="MOON", start="bad")
    assert set(errors) == {"instrument_id", "session", "start"}


# ---------- 2. Repository and build_report ----------


def test_repository_session_filter(seeded_conn):
    rows = trade_repo.list_trades(seeded_conn, session="LONDON", user_id=USER_ID)
    assert len(rows) == 7  # 6 closed + open trade 15
    assert {r["session"] for r in rows} == {"LONDON"}
    assert trade_repo.count_trades(seeded_conn, session="LONDON", user_id=USER_ID) == 7


def test_repository_rejects_unknown_session(seeded_conn):
    with pytest.raises(ValidationError):
        trade_repo.list_trades(seeded_conn, session="MOON", user_id=USER_ID)


@pytest.mark.parametrize(
    "kwargs, closed, open_",
    [
        ({"strategy": "Breakout"}, 5, 1),
        ({"strategy": "Pullback"}, 5, 0),
        ({"session": "ASIA"}, 2, 0),
        ({"session": "LONDON"}, 6, 1),
        ({"strategy": "Breakout", "session": "LONDON"}, 3, 0),
        ({"instrument_id": 1, "strategy": "Pullback"}, 2, 0),
        ({"strategy": "No such strategy"}, 0, 0),
    ],
)
def test_build_report_filters(seeded_conn, kwargs, closed, open_):
    report = build_report(seeded_conn, **kwargs, user_id=USER_ID)
    assert report.overall.trades == closed
    assert report.open_trades == open_


def test_combined_filters_select_exactly_the_right_trades(seeded_conn):
    report = build_report(seeded_conn, strategy="Breakout", session="LONDON", user_id=USER_ID)
    assert [p.trade_id for p in report.cumulative_r] == [1, 3, 10]


def test_date_filter_uses_the_realised_date(app, seeded_conn):
    """Entered on 31 March, closed on 1 April: it belongs to April."""
    trade_repo.create_trade(
        seeded_conn,
        {
            "instrument_id": 1,
            "direction": "LONG",
            "status": "CLOSED",
            "trade_date": "2026-03-31T22:00:00Z",
            "closed_at": "2026-04-01T01:00:00Z",
            "entry_price": "1.1",
            "stop_loss": "1.09",
            "exit_price": "1.11",
            "position_size": "1",
        },
        user_id=USER_ID,
    )
    assert (
        build_report(seeded_conn, start="2026-04-01", end="2026-04-30", user_id=USER_ID).overall.trades == 1
    )
    assert (
        build_report(seeded_conn, start="2026-03-01", end="2026-03-31", user_id=USER_ID).overall.trades == 14
    )


def test_filter_values_are_bound_parameters_not_sql(seeded_conn):
    """A classic injection string is just a strategy name nobody used."""
    before = trade_repo.count_trades(seeded_conn, user_id=USER_ID)
    report = build_report(seeded_conn, strategy="' OR 1=1 --", user_id=USER_ID)
    assert report.overall.trades == 0 and report.open_trades == 0
    assert trade_repo.count_trades(seeded_conn, user_id=USER_ID) == before


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


@pytest.mark.parametrize(
    "query, field",
    [
        ("session=MOON", "session"),
        ("session=", "session"),
        ("strategy=", "strategy"),
        ("strategy=" + "x" * 51, "strategy"),
    ],
)
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
