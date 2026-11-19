"""Integration tests for /api/v1/analytics/*.

The expected numbers are the hand-worked sample results from Phase 3 (see
tests/test_report.py). Here we check that the API returns exactly what the
engine calculates, as exact strings, with the documented shapes.
"""

from decimal import Decimal
from fractions import Fraction

import pytest

from tests.factories import USER_ID
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
        expected = to_jsonable(build_report(get_db(), user_id=USER_ID).overall)
    assert body["data"]["overall"] == expected


def test_summary_hand_worked_values(seeded_client):
    data = seeded_client.get(f"{BASE}/summary").get_json()["data"]
    o = data["overall"]
    assert (o["trades"], o["wins"], o["losses"], o["breakevens"]) == (14, 7, 5, 2)
    assert o["win_rate"] == "0.5"
    expectancy = Fraction(Decimal(o["expectancy_r"]))
    assert abs(expectancy - Fraction(239871, 436800)) < Fraction(1, 10**40)
    assert o["money"]["USD"]["net_pnl"] == "798.5"
    assert o["money"]["GBP"]["profit_factor"] is None  # no GBP losses -> null
    assert data["open_trades"] == 2  # counted, not in results
    assert data["excluded"] == [] and data["r_unavailable"] == []
    assert data["unknown_close_time_trade_ids"] == []  # sample trades have exit times


def test_summary_meta_explains_conventions(seeded_client):
    meta = seeded_client.get(f"{BASE}/summary").get_json()["meta"]
    # Phase 6 added strategy and session (an additive change to meta).
    assert meta["filters"] == {
        "instrument_id": None,
        "strategy": None,
        "session": None,
        "start": None,
        "end": None,
    }
    joined = " ".join(meta["notes"])
    assert "strings" in joined and "never summed across currencies" in joined


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_no_floats_anywhere(seeded_client, endpoint):
    assert not contains_float(seeded_client.get(f"{BASE}/{endpoint}").get_json())


def test_decimal_strings_round_trip_exactly(app, seeded_client):
    total_r = seeded_client.get(f"{BASE}/summary").get_json()["data"]["overall"]["total_r"]
    with app.app_context():
        assert Decimal(total_r) == build_report(get_db(), user_id=USER_ID).overall.total_r


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
        ("Breakout", 5),
        ("Pullback", 5),
        ("Reversal", 2),
        ("Unspecified", 2),
    ]
    sessions = seeded_client.get(f"{BASE}/sessions").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in sessions] == [
        ("ASIA", 2),
        ("LONDON", 6),
        ("NEW_YORK", 5),
        ("Unspecified", 1),
    ]


def test_by_weekday_in_calendar_order(seeded_client):
    data = seeded_client.get(f"{BASE}/weekday").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in data] == [
        ("Monday", 3),
        ("Tuesday", 3),
        ("Wednesday", 3),
        ("Thursday", 2),
        ("Friday", 3),
    ]


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
    assert [
        g["group"] for g in seeded_client.get(f"{BASE}/instruments?instrument_id=3").get_json()["data"]
    ] == ["XAUUSD"]


def test_date_filter_uses_realised_date(app, seeded_client, trade_body):
    # Entered 31 March, closed 1 April: it belongs to April's results.
    seeded_client.post(
        "/api/v1/trades",
        json={**trade_body, "trade_date": "2026-03-31T22:00:00Z", "closed_at": "2026-04-01T01:00:00Z"},
    )
    april = seeded_client.get(f"{BASE}/summary?start=2026-04-01&end=2026-04-30").get_json()
    assert april["data"]["overall"]["trades"] == 1
    assert april["meta"]["filters"] == {
        "instrument_id": None,
        "strategy": None,
        "session": None,
        "start": "2026-04-01",
        "end": "2026-04-30",
    }
    march = seeded_client.get(f"{BASE}/summary?start=2026-03-01&end=2026-03-31").get_json()
    assert march["data"]["overall"]["trades"] == 14


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize(
    "query, field",
    [
        ("start=2026-02-30", "start"),
        ("end=soon", "end"),
        ("start=2026-03-10&end=2026-03-01", "end"),
        ("instrument_id=999", "instrument_id"),
        ("instrument_id=x", "instrument_id"),
    ],
)
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
        {
            "symbol": "AAA",
            "name": "Test USD",
            "asset_class": "OTHER",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        {
            "symbol": "BBB",
            "name": "Test GBP",
            "asset_class": "OTHER",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "GBP",
        },
    ):
        assert client.post("/api/v1/instruments", json=instrument).status_code == 201
    common = {
        "trade_date": "2026-05-04T09:00:00Z",
        "closed_at": "2026-05-04T10:00:00Z",
        "direction": "LONG",
        "entry_price": "100",
        "stop_loss": "90",
        "position_size": "1",
    }
    client.post("/api/v1/trades", json={**common, "instrument_id": 1, "exit_price": "120"})  # +20 USD
    client.post("/api/v1/trades", json={**common, "instrument_id": 2, "exit_price": "100"})  # 0 GBP
    o = client.get(f"{BASE}/summary").get_json()["data"]["overall"]
    assert (o["wins"], o["breakevens"]) == (1, 1)
    assert o["money"]["USD"]["net_pnl"] == "20" and o["money"]["GBP"]["net_pnl"] == "0"
    assert "net_pnl" not in o  # no cross-currency total exists
    assert o["total_r"] == "2"  # R can be combined: +2R + 0R


def test_analytics_are_read_only(app, seeded_client):
    from tests.factories import table_snapshot

    before = table_snapshot(app, "trades")
    for endpoint in ENDPOINTS:
        seeded_client.get(f"{BASE}/{endpoint}")
    assert table_snapshot(app, "trades") == before
    assert seeded_client.post(f"{BASE}/summary", json={}).status_code == 405
