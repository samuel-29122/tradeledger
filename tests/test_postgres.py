"""Phase 8: PostgreSQL-specific behaviour and the SQLite -> PostgreSQL import.

Marked postgres_only: these run when TRADELEDGER_TEST_DATABASE_URL points at
a PostgreSQL server (CI's postgres service, or a local server) and are
reported as skipped otherwise. Every test works in its own empty schema.
"""

import json
import threading
from decimal import Decimal

import pytest

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, ensure_user
from tests.legacy_fixtures import build_phase7_database
from tradeledger.db import IntegrityError, connect, init_schema, list_tables
from tradeledger.migrations import LATEST_VERSION, POSTGRES_BASELINE_VERSION, current_version
from tradeledger.repositories import instruments as instrument_repo
from tradeledger.repositories import trades as trade_repo
from tradeledger.seed import seed_sample_data

pytestmark = pytest.mark.postgres_only

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "migrate_sqlite_to_postgres",
    Path(__file__).resolve().parent.parent / "scripts" / "migrate_sqlite_to_postgres.py",
)
importer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(importer)


# ---------- Schema ----------


def test_postgres_and_sqlite_end_on_the_same_version(conn):
    assert conn.dialect == "postgresql"
    # Phase 8: the baseline WAS the latest version (3). Phase 8.5 added
    # migration 4, which PostgreSQL applies on top of the baseline, so this
    # now also exercises the PostgreSQL migration loop for real.
    assert POSTGRES_BASELINE_VERSION == 3
    assert current_version(conn) == LATEST_VERSION == 4
    rows = conn.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    assert [r["version"] for r in rows] == [3, 4]


def test_postgres_has_the_same_tables_and_columns_as_sqlite(conn, tmp_path):
    sqlite = connect(str(tmp_path / "compare.db"))
    init_schema(sqlite)
    try:
        assert set(list_tables(conn)) - {"schema_migrations"} == set(list_tables(sqlite))
        for table in (
            "instruments",
            "trades",
            "users",
            "api_tokens",
            "login_attempts",
            "user_sessions",
            "password_resets",
            "recovery_codes",
        ):
            pg_columns = {
                r["name"]
                for r in conn.execute(
                    "SELECT column_name AS name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = ?",
                    (table,),
                )
            }
            lite_columns = {r["name"] for r in sqlite.execute(f"PRAGMA table_info({table})")}
            assert pg_columns == lite_columns, table
    finally:
        sqlite.close()


def test_money_columns_are_exact_numeric(conn):
    types = {
        r["column_name"]: r["data_type"]
        for r in conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'trades'"
        )
    }
    for column in ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees"):
        assert types[column] == "numeric", column  # never real / double precision


def test_init_schema_is_idempotent_and_never_drops_data(seeded_conn, user_id):
    assert init_schema(seeded_conn) == []
    assert len(trade_repo.list_trades(seeded_conn, user_id=user_id)) == 16


def test_two_processes_migrating_at_once_are_safe(db_path):
    """The advisory lock makes concurrent migrations wait, not collide."""
    errors = []

    def migrate():
        try:
            c = connect(db_path)
            init_schema(c)
            c.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=migrate) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    check = connect(db_path)
    assert current_version(check) == LATEST_VERSION
    # One row per version (baseline 3 plus migration 4): nothing applied twice.
    assert check.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 2
    check.close()


# ---------- Decimal precision ----------


@pytest.mark.parametrize(
    "price",
    ["999999999999999.999999999999999", "0.000000000000001", "1.10000", "123456.123456789"],
)
def test_decimals_round_trip_exactly(conn, eurusd_id, user_id, price):
    entry = Decimal(price)
    trade_id = (
        trade_repo.create_trade(
            conn,
            {
                "instrument_id": eurusd_id,
                "trade_date": "2026-03-02T08:15:00Z",
                "direction": "SHORT",
                "entry_price": price,
                "stop_loss": str(entry * 2) if entry < Decimal("1e14") else "999999999999999.999999999999999",
                "position_size": "1",
                "status": "OPEN",
            },
            user_id=user_id,
        )
        if entry < Decimal("1e14")
        else None
    )
    if trade_id is None:
        # The maximum value: store it directly in a NUMERIC column and read it back.
        row = conn.execute("SELECT CAST(? AS NUMERIC) AS v", (conn.numeric(price),)).fetchone()
        assert row["v"] == entry and str(row["v"]) == price
        return
    stored = trade_repo.get_trade(conn, trade_id, user_id=user_id)["entry_price"]
    assert stored == entry  # exactly equal: no binary floating point anywhere
    assert isinstance(stored, Decimal)


def test_database_limits_repeat_the_validation_limits(conn, eurusd_id, user_id):
    """Defence in depth: even raw SQL cannot store 16 integer digits."""
    with pytest.raises(IntegrityError):
        conn.execute(
            "INSERT INTO trades (user_id, instrument_id, trade_date, direction, entry_price, stop_loss, "
            "position_size, status) VALUES (?, ?, '2026-03-02T08:15:00Z', 'LONG', ?, '1', '1', 'OPEN')",
            (user_id, eurusd_id, conn.numeric("1" + "0" * 15)),
        )


def test_closed_at_rule_is_a_named_check_constraint(seeded_conn, user_id):
    with pytest.raises(IntegrityError) as info:
        seeded_conn.execute(
            "UPDATE trades SET closed_at = '2020-01-01T00:00:00Z' WHERE user_id = ? AND status = 'CLOSED'",
            (user_id,),
        )
    assert info.value.constraint == "closed_at_rules"


# ---------- Concurrency ----------


def test_concurrent_edits_are_serialised(db_path, seeded_conn, user_id):
    """update_trade locks the row (SELECT ... FOR UPDATE): a second
    connection's edit waits for the first transaction instead of
    interleaving with it."""
    trade = trade_repo.list_trades(seeded_conn, user_id=user_id, status="CLOSED")[0]
    data = {k: trade[k] for k in trade_repo._WRITE_FIELDS}
    for key in ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees"):
        data[key] = None if data[key] is None else str(data[key])
    first, second = connect(db_path), connect(db_path)
    order = []
    try:
        with first:
            first.execute("SELECT id FROM trades WHERE id = ? FOR UPDATE", (trade["id"],))

            def other_edit():
                trade_repo.update_trade(second, trade["id"], {**data, "notes": "second"}, user_id=user_id)
                order.append("second")

            worker = threading.Thread(target=other_edit)
            worker.start()
            worker.join(timeout=0.5)
            assert worker.is_alive()  # blocked by the first transaction's lock
            first.execute("UPDATE trades SET notes = 'first' WHERE id = ?", (trade["id"],))
            order.append("first")
        worker.join(timeout=5)
        assert order == ["first", "second"]
        assert trade_repo.get_trade(seeded_conn, trade["id"], user_id=user_id)["notes"] == "second"
    finally:
        first.close()
        second.close()


def test_failed_write_rolls_back_the_whole_transaction(conn, user_id):
    with pytest.raises(IntegrityError), conn:
        instrument_repo.create_instrument(
            conn,
            {
                "symbol": "AAA",
                "name": "a",
                "asset_class": "OTHER",
                "price_increment": "0.1",
                "contract_multiplier": "1",
                "quote_currency": "USD",
            },
            user_id=user_id,
        )
        conn.execute("INSERT INTO trades (user_id) VALUES (?)", (user_id,))  # violates NOT NULL
    assert instrument_repo.list_instruments(conn, user_id=user_id) == []


# ---------- SQLite -> PostgreSQL import ----------


@pytest.fixture
def phase7_file(tmp_path):
    path = str(tmp_path / "phase7.db")
    build_phase7_database(path)
    return path


def run_import(phase7_file, db_path, *extra):
    lines = []
    args = ["--sqlite", phase7_file, "--postgres", db_path, "--owner-email", TEST_EMAIL, *extra]
    from tests.conftest import PG_URL  # noqa: F401 - the bridge is registered by conftest when used

    code, summary = importer.run(args, out=lines.append)
    return code, summary, "\n".join(lines)


def test_import_dry_run_writes_nothing(phase7_file, db_path, conn, user_id):
    code, summary, output = run_import(phase7_file, db_path)
    assert code == 0, output
    assert summary["status"] == "dry run: nothing written"
    assert (summary["instruments"], summary["trades"]) == (5, 16)
    assert trade_repo.list_trades(conn, user_id=user_id) == []


def test_import_copies_everything_exactly(phase7_file, db_path, conn, user_id, tmp_path):
    import sqlite3

    report = tmp_path / "audit.json"
    code, summary, output = run_import(phase7_file, db_path, "--apply", "--report", str(report))
    assert code == 0, output
    assert summary["status"] == "imported and verified"
    audit = json.loads(report.read_text())
    assert audit["trades"] == 16 and len(audit["trade_id_map"]) == 16
    assert "@" not in audit["target"] or ":***@" in audit["target"] or "postgres@" in audit["target"]

    source = sqlite3.connect(phase7_file)
    source.row_factory = sqlite3.Row
    for old in source.execute("SELECT * FROM trades ORDER BY id"):
        new = trade_repo.get_trade(conn, audit["trade_id_map"][str(old["id"])], user_id=user_id)
        for field in ("entry_price", "stop_loss", "exit_price", "position_size", "fees"):
            assert (new[field] is None and old[field] is None) or new[field] == Decimal(old[field])
        assert new["closed_at"] == old["closed_at"]
        assert new["created_at"] == old["created_at"]
    source.close()


def test_import_report_matches_the_engine_on_both_databases(phase7_file, db_path, conn, user_id, tmp_path):
    from tradeledger.legacy import claim_legacy_data
    from tradeledger.performance import build_report

    run_import(phase7_file, db_path, "--apply")
    lite = connect(phase7_file)
    init_schema(lite)
    claim_legacy_data(lite, user_id=ensure_user(lite), apply=True)
    a, b = build_report(lite, user_id=1).overall, build_report(conn, user_id=user_id).overall
    lite.close()
    assert (a.trades, a.wins, a.total_r, a.expectancy_r) == (b.trades, b.wins, b.total_r, b.expectancy_r)


def test_import_refuses_a_non_empty_target(phase7_file, db_path, seeded_conn):
    code, _summary, output = run_import(phase7_file, db_path, "--apply")
    assert code == 1
    assert "only writes into an empty journal" in output
    assert len(trade_repo.list_trades(seeded_conn, user_id=1)) == 16  # untouched


def test_import_refuses_an_unknown_owner(phase7_file, db_path, conn):
    lines = []
    code, _ = importer.run(
        ["--sqlite", phase7_file, "--postgres", db_path, "--owner-email", "ghost@example.com", "--apply"],
        out=lines.append,
    )
    assert code == 1 and "create it first" in "\n".join(lines)


def test_import_into_one_account_leaves_others_alone(phase7_file, db_path, conn):
    other = ensure_user(conn, OTHER_EMAIL)
    seed_sample_data(conn, user_id=other)
    code, _, output = run_import(phase7_file, db_path, "--apply")
    assert code == 0, output
    assert len(trade_repo.list_trades(conn, user_id=other)) == 16
    assert len(trade_repo.list_trades(conn, user_id=1)) == 16


def test_import_never_prints_a_password(phase7_file, db_path, conn):
    url_with_password = db_path.replace(
        "postgresql://postgres@", "postgresql://postgres:NotARealPassword@", 1
    )
    _, summary, output = run_import(phase7_file, url_with_password)
    assert "NotARealPassword" not in output and "NotARealPassword" not in json.dumps(summary)
