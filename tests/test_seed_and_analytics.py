"""Sample data, analytical SQL and the CLI commands.

Expected values were worked out by hand from tradeledger/seed.py
(see the comments beside each sample trade).
"""

from decimal import Decimal

import pytest

from tests.conftest import TEST_EMAIL
from tests.factories import USER_ID
from tradeledger import analytics, seed
from tradeledger.seed import SAMPLE_TRADES, seed_sample_data
from tradeledger.validation import ValidationError


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------- Seeding ----------


def test_seed_inserts_everything_once(conn):
    assert seed_sample_data(conn, user_id=USER_ID) == {"instruments": 5, "trades": 16}


def test_seed_is_idempotent(conn):
    seed_sample_data(conn, user_id=USER_ID)
    assert seed_sample_data(conn, user_id=USER_ID) == {"instruments": 0, "trades": 0}
    assert count(conn, "instruments") == 5
    assert count(conn, "trades") == 16


def test_seed_does_not_touch_existing_user_trades(conn, long_trade):
    from tradeledger.repositories.trades import create_trade

    create_trade(conn, dict(long_trade, trade_date="2026-01-05T10:00:00Z"), user_id=USER_ID)
    seed_sample_data(conn, user_id=USER_ID)
    assert count(conn, "trades") == 17


def test_seed_covers_required_scenarios(seeded_conn):
    rows = seeded_conn.execute("SELECT direction, status, strategy, session FROM trades").fetchall()
    assert {r["direction"] for r in rows} == {"LONG", "SHORT"}
    assert {r["status"] for r in rows} == {"OPEN", "CLOSED"}
    assert None in {r["strategy"] for r in rows}
    assert len({r["session"] for r in rows}) >= 3


def test_seed_is_all_or_nothing(conn, monkeypatch):
    """If one sample record is invalid, the whole seed run is rolled back."""
    broken = dict(SAMPLE_TRADES[0], trade_date="2026-04-01T09:00:00Z", position_size="-1")
    monkeypatch.setattr(seed, "SAMPLE_TRADES", [*SAMPLE_TRADES, broken])
    with pytest.raises(ValidationError):
        seed.seed_sample_data(conn, user_id=USER_ID)
    assert count(conn, "instruments") == 0
    assert count(conn, "trades") == 0


# ---------- Analytics on an empty database ----------


def test_analytics_on_empty_database(conn):
    assert analytics.closed_trade_count(conn, user_id=USER_ID) == 0
    assert analytics.closed_count_by_instrument(conn, user_id=USER_ID) == []
    assert analytics.closed_by_weekday(conn, user_id=USER_ID) == []
    assert analytics.trades_by_strategy(conn, user_id=USER_ID) == []
    assert analytics.trades_by_session(conn, user_id=USER_ID) == []
    assert analytics.win_rate_by_instrument(conn, user_id=USER_ID) == []


# ---------- Analytics on the sample data ----------


def test_closed_trade_count(seeded_conn):
    assert analytics.closed_trade_count(seeded_conn, user_id=USER_ID) == 14


def test_closed_count_by_instrument(seeded_conn):
    assert analytics.closed_count_by_instrument(seeded_conn, user_id=USER_ID) == [
        {"symbol": "XAUUSD", "closed_trades": 4},
        {"symbol": "EURUSD", "closed_trades": 3},
        {"symbol": "GBPUSD", "closed_trades": 3},
        {"symbol": "NAS100", "closed_trades": 2},
        {"symbol": "UK100", "closed_trades": 2},
    ]


def test_instrument_with_no_closed_trades_shows_zero(seeded_conn):
    from tradeledger.repositories.instruments import create_instrument

    create_instrument(
        seeded_conn,
        {
            "symbol": "US30",
            "name": "Wall Street 30 (test)",
            "asset_class": "INDEX",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        user_id=USER_ID,
    )
    rows = analytics.closed_count_by_instrument(seeded_conn, user_id=USER_ID)
    assert {"symbol": "US30", "closed_trades": 0} in rows


def test_closed_by_weekday(seeded_conn):
    assert analytics.closed_by_weekday(seeded_conn, user_id=USER_ID) == [
        {"weekday_number": 1, "weekday": "Monday", "closed_trades": 3},
        {"weekday_number": 2, "weekday": "Tuesday", "closed_trades": 3},
        {"weekday_number": 3, "weekday": "Wednesday", "closed_trades": 3},
        {"weekday_number": 4, "weekday": "Thursday", "closed_trades": 2},
        {"weekday_number": 5, "weekday": "Friday", "closed_trades": 3},
    ]


def test_trades_by_strategy(seeded_conn):
    assert analytics.trades_by_strategy(seeded_conn, user_id=USER_ID) == [
        {"strategy": "Breakout", "total_trades": 6, "open_trades": 1, "closed_trades": 5},
        {"strategy": "Pullback", "total_trades": 5, "open_trades": 0, "closed_trades": 5},
        {"strategy": "Reversal", "total_trades": 3, "open_trades": 1, "closed_trades": 2},
        {"strategy": "Unspecified", "total_trades": 2, "open_trades": 0, "closed_trades": 2},
    ]


def test_trades_by_session(seeded_conn):
    assert analytics.trades_by_session(seeded_conn, user_id=USER_ID) == [
        {"session": "LONDON", "total_trades": 7, "open_trades": 1, "closed_trades": 6},
        {"session": "NEW_YORK", "total_trades": 6, "open_trades": 1, "closed_trades": 5},
        {"session": "ASIA", "total_trades": 2, "open_trades": 0, "closed_trades": 2},
        {"session": "Unspecified", "total_trades": 1, "open_trades": 0, "closed_trades": 1},
    ]


def test_win_rate_by_instrument(seeded_conn):
    assert analytics.win_rate_by_instrument(seeded_conn, user_id=USER_ID) == [
        {
            "symbol": "XAUUSD",
            "quote_currency": "USD",
            "closed_trades": 4,
            "wins": 3,
            "losses": 1,
            "breakevens": 0,
            "win_rate_pct": 75.0,
        },
        {
            "symbol": "NAS100",
            "quote_currency": "USD",
            "closed_trades": 2,
            "wins": 1,
            "losses": 1,
            "breakevens": 0,
            "win_rate_pct": 50.0,
        },
        {
            "symbol": "UK100",
            "quote_currency": "GBP",
            "closed_trades": 2,
            "wins": 1,
            "losses": 0,
            "breakevens": 1,
            "win_rate_pct": 50.0,
        },
        {
            "symbol": "EURUSD",
            "quote_currency": "USD",
            "closed_trades": 3,
            "wins": 1,
            "losses": 2,
            "breakevens": 0,
            "win_rate_pct": 33.3,
        },
        {
            "symbol": "GBPUSD",
            "quote_currency": "USD",
            "closed_trades": 3,
            "wins": 1,
            "losses": 1,
            "breakevens": 1,
            "win_rate_pct": 33.3,
        },
    ]


def test_fees_can_turn_a_price_gain_into_a_loss(seeded_conn):
    """Sample trade 9: long EURUSD, exit 2 points (0.00002) above entry,
    gross +2.00 USD, fees 7.00 -> net -5.00 USD -> counted as a LOSS."""
    eurusd = next(
        r for r in analytics.win_rate_by_instrument(seeded_conn, user_id=USER_ID) if r["symbol"] == "EURUSD"
    )
    assert eurusd["losses"] == 2


def test_sql_win_rate_agrees_with_exact_decimal_calculation(seeded_conn):
    """Cross-check the float-based SQL against exact Decimal arithmetic."""
    rows = seeded_conn.execute(
        """
        SELECT i.symbol, t.direction, t.entry_price, t.exit_price,
               t.position_size, t.fees, i.contract_multiplier
        FROM trades t JOIN instruments i ON i.id = t.instrument_id
        WHERE t.status = 'CLOSED'
        """
    ).fetchall()

    expected = {}
    for r in rows:
        move = Decimal(r["exit_price"]) - Decimal(r["entry_price"])
        if r["direction"] == "SHORT":
            move = -move
        net = move * Decimal(r["position_size"]) * Decimal(r["contract_multiplier"]) - Decimal(r["fees"])
        tally = expected.setdefault(r["symbol"], {"wins": 0, "losses": 0, "breakevens": 0})
        tally["wins" if net > 0 else "losses" if net < 0 else "breakevens"] += 1

    actual = {
        r["symbol"]: {"wins": r["wins"], "losses": r["losses"], "breakevens": r["breakevens"]}
        for r in analytics.win_rate_by_instrument(seeded_conn, user_id=USER_ID)
    }
    assert actual == expected


# ---------- CLI commands ----------


def test_cli_init_seed_and_info(runner):
    assert "Database ready" in runner.invoke(args=["init-db"]).output
    assert "5 instrument(s) and 16 trade(s)" in runner.invoke(args=["seed-db", "--email", TEST_EMAIL]).output
    assert "0 instrument(s) and 0 trade(s)" in runner.invoke(args=["seed-db", "--email", TEST_EMAIL]).output
    info = runner.invoke(args=["db-info"]).output
    assert "Schema version: 4" in info  # 1 before Phase 4, 2 in Phases 4-7, 3 in Phase 8
    assert "trades" in info and "16" in info


def test_cli_analytics_runs_every_query(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    output = runner.invoke(args=["analytics", "--email", TEST_EMAIL]).output
    for name in analytics.ALL_QUERIES:
        assert f"== {name} ==" in output


def test_cli_run_sql_is_read_only(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    ok = runner.invoke(args=["run-sql", "SELECT COUNT(*) AS n FROM trades"])
    assert "16" in ok.output
    blocked = runner.invoke(args=["run-sql", "DELETE FROM trades"])
    assert blocked.exit_code != 0
    assert "readonly" in blocked.output or "read-only" in blocked.output  # SQLite / PostgreSQL wording
    still_there = runner.invoke(args=["run-sql", "SELECT COUNT(*) AS n FROM trades"])
    assert "16" in still_there.output


def test_cli_reset_db_empties_tables(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    result = runner.invoke(args=["reset-db", "--yes"])
    assert "Database reset" in result.output
    for table in ("trades", "instruments"):
        output = runner.invoke(args=["run-sql", f"SELECT COUNT(*) AS n FROM {table}"]).output
        assert output.strip().splitlines()[-1].strip() == "0"
