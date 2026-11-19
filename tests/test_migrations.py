"""Schema migration: a real Phase 3 (version 1) database upgraded to the latest version.

tests/fixtures/schema_v1.sql is an exact copy of the Phase 3 schema.sql,
so these tests start from the same structure an existing user has.

Phase 8 changes (the requirement changed, the checks did not weaken):
* The latest version is now 4 (Phase 8.5 added migration 4, which only
  creates new tables and columns), so a version 1 database receives
  migrations [2, 3, 4] and ends on version 4.
* After migration 3 the old records are UNASSIGNED (user_id NULL) and
  invisible to every account. Tests that read them through the
  repositories first create the test account and claim the records with
  the same function the claim-legacy-data command uses.
* Raw SQL errors are checked as tradeledger.db.IntegrityError.
SQLite-only: PostgreSQL has no Phase 3 history (see migrations.py); the
trigger tests at the end run on both engines.
"""

import sqlite3
from pathlib import Path

import pytest

from tests.conftest import ensure_user
from tests.factories import USER_ID
from tradeledger import create_app, migrations
from tradeledger.db import IntegrityError, connect, drop_all, init_schema, schema_version
from tradeledger.legacy import claim_legacy_data
from tradeledger.performance import build_report
from tradeledger.repositories.trades import create_trade, get_trade, update_trade
from tradeledger.seed import SAMPLE_INSTRUMENTS, SAMPLE_TRADES
from tradeledger.validation import ValidationError, validate_instrument, validate_trade

V1_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text(encoding="utf-8")

V1_TRADE_COLUMNS = (
    "instrument_id",
    "trade_date",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "status",
    "strategy",
    "session",
    "notes",
)


def build_v1_database(path):
    """Create a Phase 3 database holding the 16 sample trades WITHOUT exit
    times, exactly as an existing user's database looks."""
    conn = connect(path)
    conn.executescript(V1_SCHEMA)
    raw_conn = sqlite3.connect(path)  # the Phase 3 code used named parameters
    raw_conn.row_factory = sqlite3.Row
    with raw_conn:
        for raw in SAMPLE_INSTRUMENTS:
            item = validate_instrument(raw)
            raw_conn.execute(
                "INSERT INTO instruments (symbol, name, asset_class, price_increment, "
                "contract_multiplier, quote_currency) VALUES (:symbol, :name, :asset_class, "
                ":price_increment, :contract_multiplier, :quote_currency)",
                item,
            )
        ids = {r["symbol"]: r["id"] for r in raw_conn.execute("SELECT id, symbol FROM instruments")}
        for raw in SAMPLE_TRADES:
            data = {k: v for k, v in raw.items() if k not in ("symbol", "closed_at")}
            data["instrument_id"] = ids[raw["symbol"]]
            trade = validate_trade(data, require_closed_at=False)
            raw_conn.execute(
                f"INSERT INTO trades ({', '.join(V1_TRADE_COLUMNS)}) "
                f"VALUES ({', '.join(':' + c for c in V1_TRADE_COLUMNS)})",
                trade,
            )
    raw_conn.close()
    return conn


def migrate_and_claim(conn):
    """Upgrade, create the test account and give it the legacy records."""
    applied = init_schema(conn)
    user_id = ensure_user(conn)
    assert user_id == USER_ID
    assert claim_legacy_data(conn, user_id=user_id, apply=True)["applied"]
    return applied


def columns(conn, table):
    if conn.dialect == "postgresql":
        sql = "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ? AND table_schema = current_schema()"
        return [row["name"] for row in conn.execute(sql, (table,))]
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def rows(conn, table):
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


@pytest.fixture
def v1_conn(tmp_path, request):
    if request.node.get_closest_marker("sqlite_only") is None:
        pytest.fail("v1_conn tests must be marked sqlite_only")
    conn = build_v1_database(str(tmp_path / "phase3.db"))
    yield conn
    conn.close()


# ---------- The starting point really is Phase 3 ----------


@pytest.mark.sqlite_only
def test_fixture_is_a_version_1_database(v1_conn):
    assert schema_version(v1_conn) == 1
    assert "closed_at" not in columns(v1_conn, "trades")
    assert v1_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 16


# ---------- Migrating ----------


@pytest.mark.sqlite_only
def test_migration_adds_closed_at_and_bumps_version(v1_conn):
    applied = init_schema(v1_conn)
    assert applied == [2, 3, 4]
    assert schema_version(v1_conn) == 4
    assert "closed_at" in columns(v1_conn, "trades")
    names = {r["name"] for r in v1_conn.execute("SELECT name FROM sqlite_master")}
    assert {"idx_trades_closed_at", "trades_closed_at_rules_insert", "trades_closed_at_rules_update"} <= names


@pytest.mark.sqlite_only
def test_migration_preserves_every_existing_value(v1_conn):
    trades_before = rows(v1_conn, "trades")
    instruments_before = rows(v1_conn, "instruments")
    init_schema(v1_conn)
    trades_after = rows(v1_conn, "trades")
    assert [
        {k: v for k, v in r.items() if k != "user_id"} for r in rows(v1_conn, "instruments")
    ] == instruments_before
    assert len(trades_after) == len(trades_before) == 16
    for before, after in zip(trades_before, trades_after, strict=True):
        assert {k: v for k, v in after.items() if k not in ("closed_at", "user_id")} == before
        assert after["closed_at"] is None  # never fabricated
        assert after["user_id"] is None  # never guessed (Phase 8)


@pytest.mark.sqlite_only
def test_migration_is_idempotent(v1_conn):
    assert init_schema(v1_conn) == [2, 3, 4]
    snapshot = rows(v1_conn, "trades")
    assert init_schema(v1_conn) == []
    assert migrations.migrate(v1_conn) == []
    assert schema_version(v1_conn) == 4
    assert rows(v1_conn, "trades") == snapshot


def test_new_database_goes_straight_to_latest(conn):
    assert schema_version(conn) == migrations.LATEST_VERSION
    assert "closed_at" in columns(conn, "trades")


@pytest.mark.sqlite_only
def test_failed_migration_rolls_back_completely(v1_conn, monkeypatch):
    init_schema(v1_conn)
    broken = {
        **migrations.MIGRATIONS,
        5: "CREATE TABLE half_done (x INTEGER); SELECT * FROM table_that_does_not_exist;",
    }
    monkeypatch.setattr(migrations, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        migrations.migrate(v1_conn)
    assert schema_version(v1_conn) == 4
    names = {r["name"] for r in v1_conn.execute("SELECT name FROM sqlite_master")}
    assert "half_done" not in names


def test_reset_then_init_gives_a_fully_migrated_schema(conn):
    """Regression: drop_all must reset the version, or init would skip migration 2."""
    drop_all(conn)
    assert schema_version(conn) == 0
    init_schema(conn)
    assert schema_version(conn) == 4
    assert "closed_at" in columns(conn, "trades")


# ---------- Legacy trades after migration ----------


@pytest.mark.sqlite_only
def test_legacy_trades_are_reported_and_ordered_by_entry_time(v1_conn):
    migrate_and_claim(v1_conn)
    report = build_report(v1_conn, user_id=USER_ID)
    closed_ids = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]
    assert report.unknown_close_time_trade_ids == closed_ids
    assert [p.trade_id for p in report.cumulative_r] == closed_ids
    assert all(not p.close_time_known for p in report.cumulative_r)
    assert all(p.realised_at == p.trade_date for p in report.cumulative_r)
    # The numbers are the same as Phase 3 produced for this data.
    assert report.overall.trades == 14 and report.overall.wins == 7


@pytest.mark.sqlite_only
def test_new_closed_trade_requires_closed_at_after_migration(v1_conn):
    migrate_and_claim(v1_conn)
    trade = {
        "instrument_id": 1,
        "trade_date": "2026-04-01T08:00:00Z",
        "direction": "LONG",
        "entry_price": "1.1",
        "stop_loss": "1.09",
        "exit_price": "1.12",
        "position_size": "1",
    }
    with pytest.raises(ValidationError) as info:
        create_trade(v1_conn, trade, user_id=USER_ID)
    assert info.value.errors == {"closed_at": "is required for a CLOSED trade"}
    new_id = create_trade(v1_conn, dict(trade, closed_at="2026-04-01T09:00:00Z"), user_id=USER_ID)
    assert get_trade(v1_conn, new_id, user_id=USER_ID)["closed_at"] == "2026-04-01T09:00:00Z"


@pytest.mark.sqlite_only
def test_mixed_legacy_and_new_trades_are_in_realised_order(v1_conn):
    migrate_and_claim(v1_conn)
    # Entered BEFORE every sample trade, closed AFTER all of them.
    new_id = create_trade(
        v1_conn,
        {
            "instrument_id": 1,
            "trade_date": "2026-02-27T08:00:00Z",
            "closed_at": "2026-03-25T08:00:00Z",
            "direction": "LONG",
            "entry_price": "1.1",
            "stop_loss": "1.09",
            "exit_price": "1.12",
            "position_size": "1",
        },
        user_id=USER_ID,
    )
    curve = build_report(v1_conn, user_id=USER_ID).cumulative_r
    assert curve[-1].trade_id == new_id and curve[-1].close_time_known


@pytest.mark.sqlite_only
def test_legacy_trade_can_be_edited_without_inventing_a_close_time(v1_conn):
    migrate_and_claim(v1_conn)
    legacy = get_trade(v1_conn, 1, user_id=USER_ID)
    edited = {
        k: legacy[k]
        for k in (
            "instrument_id",
            "trade_date",
            "closed_at",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "exit_price",
            "position_size",
            "fees",
            "status",
            "strategy",
            "session",
        )
    }
    edited["notes"] = "Edited after the migration"
    assert update_trade(v1_conn, 1, edited, user_id=USER_ID) is True
    after = get_trade(v1_conn, 1, user_id=USER_ID)
    assert after["notes"] == "Edited after the migration"
    assert after["closed_at"] is None


@pytest.mark.sqlite_only
def test_legacy_trade_can_be_given_its_real_close_time(v1_conn):
    migrate_and_claim(v1_conn)
    legacy = get_trade(v1_conn, 1, user_id=USER_ID)
    data = {
        k: legacy[k]
        for k in (
            "instrument_id",
            "trade_date",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "exit_price",
            "position_size",
            "fees",
            "status",
            "strategy",
            "session",
            "notes",
        )
    }
    update_trade(v1_conn, 1, dict(data, closed_at="2026-03-02T12:00:00Z"), user_id=USER_ID)
    assert get_trade(v1_conn, 1, user_id=USER_ID)["closed_at"] == "2026-03-02T12:00:00Z"
    # Once known, it cannot be removed again: the trade is no longer legacy.
    with pytest.raises(ValidationError):
        update_trade(v1_conn, 1, dict(data, closed_at=None), user_id=USER_ID)


# ---------- Triggers enforce the rules in SQL ----------

RAW = (
    "INSERT INTO trades (user_id, instrument_id, trade_date, closed_at, direction, entry_price, "
    "stop_loss, exit_price, position_size, fees, status) VALUES (1, 1, ?, ?, 'LONG', '1.1', "
    "'1.09', ?, '1', '0', ?)"
)


def test_trigger_rejects_open_trade_with_closed_at(seeded_conn):
    with pytest.raises(IntegrityError, match="closed_at_rules"):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "2026-04-01T09:00:00Z", None, "OPEN"))


def test_trigger_rejects_close_before_entry(seeded_conn):
    with pytest.raises(IntegrityError, match="closed_at_rules"):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "2026-04-01T07:00:00Z", "1.12", "CLOSED"))


def test_trigger_rejects_bad_update(seeded_conn):
    with pytest.raises(IntegrityError, match="closed_at_rules"):
        seeded_conn.execute("UPDATE trades SET closed_at = '2000-01-01T00:00:00Z' WHERE id = 1")


def test_check_constraint_rejects_badly_formatted_closed_at(seeded_conn):
    with pytest.raises(IntegrityError):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "01/04/2026 09:00", "1.12", "CLOSED"))


def test_sql_still_allows_legacy_style_closed_trade_without_closed_at(seeded_conn):
    seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", None, "1.12", "CLOSED"))


# ---------- migrate-db command ----------


@pytest.mark.sqlite_only
def test_migrate_db_command_backs_up_then_migrates(tmp_path):
    path = tmp_path / "user.db"
    build_v1_database(str(path)).close()
    runner = create_app("testing", overrides={"DATABASE": str(path)}).test_cli_runner()

    result = runner.invoke(args=["migrate-db"])
    assert result.exit_code == 0, result.output
    assert "Schema version 1 -> 4" in result.output
    backups = list(tmp_path.glob("user.db.v1-*.bak"))
    assert len(backups) == 1
    backup = connect(str(backups[0]))
    assert schema_version(backup) == 1
    assert backup.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 16
    backup.close()

    again = runner.invoke(args=["migrate-db"])
    assert "Already at schema version 4" in again.output
    assert len(list(tmp_path.glob("user.db.v1-*.bak"))) == 1
