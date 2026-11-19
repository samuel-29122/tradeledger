"""Phase 8: upgrading a Phase 7 database, claiming legacy records, and the
account commands (create-user, set-password, deactivate-user, ...).

The Phase 7 database is built by tests/fixtures/phase7_schema_v2.sql plus
the sample data inserted with Phase 7-shaped SQL (no users, no owners),
so the upgrade path tested here is the one an existing user takes.
"""

import sqlite3

import pytest

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, ensure_user
from tests.legacy_fixtures import build_phase7_database
from tradeledger import create_app
from tradeledger.db import connect, get_db, init_schema, schema_version
from tradeledger.legacy import claim_legacy_data, legacy_counts
from tradeledger.repositories import trades as trade_repo
from tradeledger.repositories.users import get_user_by_email

pytestmark = pytest.mark.sqlite_only  # PostgreSQL databases never had unowned rows


def snapshot(path, table):
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    rows = [dict(r) for r in raw.execute(f"SELECT * FROM {table} ORDER BY id")]
    raw.close()
    return rows


@pytest.fixture
def phase7_db(tmp_path):
    path = str(tmp_path / "phase7.db")
    build_phase7_database(path)
    return path


@pytest.fixture
def open_db(phase7_db):
    """Open connections to the Phase 7 file; all are closed after the test."""
    opened = []

    def open_connection():
        conn = connect(phase7_db)
        opened.append(conn)
        return conn

    yield open_connection
    for conn in opened:
        conn.close()


# ---------- Upgrading keeps every value ----------


def test_upgrade_from_phase_7_preserves_every_row(phase7_db, open_db):
    trades_before, instruments_before = snapshot(phase7_db, "trades"), snapshot(phase7_db, "instruments")
    conn = open_db()
    assert schema_version(conn) == 2
    assert init_schema(conn) == [3, 4]  # Phase 8.5 added migration 4 (new tables only)
    conn.close()
    trades_after, instruments_after = snapshot(phase7_db, "trades"), snapshot(phase7_db, "instruments")
    assert len(trades_after) == len(trades_before) == 16
    for before, after in zip(trades_before, trades_after, strict=True):
        assert after.pop("user_id") is None
        assert after == before  # same ids, same text, same timestamps
    for before, after in zip(instruments_before, instruments_after, strict=True):
        assert after.pop("user_id") is None
        assert after == before


def test_upgrade_keeps_foreign_keys_and_rules(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    assert conn.raw.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.raw.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    triggers = {r[0] for r in conn.raw.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert triggers == {"trades_closed_at_rules_insert", "trades_closed_at_rules_update"}


def test_unassigned_records_are_invisible_to_every_account(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    first = ensure_user(conn)  # even the FIRST account gets nothing automatically
    assert trade_repo.list_trades(conn, user_id=first) == []
    assert legacy_counts(conn) == {"instruments": 5, "trades": 16}


# ---------- Claiming ----------


def test_dry_run_changes_nothing(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    summary = claim_legacy_data(conn, user_id=ensure_user(conn), apply=False)
    assert summary == {"instruments": 5, "trades": 16, "conflicts": [], "applied": False}
    assert legacy_counts(conn) == {"instruments": 5, "trades": 16}


def test_apply_assigns_everything_in_one_go(phase7_db, open_db):
    before = snapshot(phase7_db, "trades")
    conn = open_db()
    init_schema(conn)
    owner = ensure_user(conn)
    assert claim_legacy_data(conn, user_id=owner, apply=True)["applied"]
    assert legacy_counts(conn) == {"instruments": 0, "trades": 0}
    assert len(trade_repo.list_trades(conn, user_id=owner)) == 16
    conn.close()
    after = snapshot(phase7_db, "trades")
    assert [{**r, "user_id": None} for r in after] == [{**r, "user_id": None} for r in before]
    assert {r["user_id"] for r in after} == {owner}


def test_symbol_conflict_blocks_the_claim(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    owner = ensure_user(conn)
    from tradeledger.repositories.instruments import create_instrument

    create_instrument(
        conn,
        {
            "symbol": "EURUSD",
            "name": "already mine",
            "asset_class": "FOREX",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        user_id=owner,
    )
    summary = claim_legacy_data(conn, user_id=owner, apply=True)
    assert summary["applied"] is False
    assert summary["conflicts"] == ["the account already has an instrument with symbol EURUSD"]
    assert legacy_counts(conn) == {"instruments": 5, "trades": 16}


# ---------- The commands ----------


def app_for(path):
    app = create_app("testing", overrides={"DATABASE": path})
    return app, app.test_cli_runner()


def test_migrate_db_command_backs_up_and_reports_unassigned_records(phase7_db, tmp_path):
    _app, runner = app_for(phase7_db)
    result = runner.invoke(args=["migrate-db"])
    assert result.exit_code == 0, result.output
    assert "Schema version 2 -> 4" in result.output
    assert "16 trade(s) are unassigned" in result.output
    backups = list(tmp_path.glob("phase7.db.v2-*.bak"))
    assert len(backups) == 1
    assert (
        snapshot(str(backups[0]), "trades")[0].get("user_id", "absent") == "absent"
    )  # the untouched v2 copy


def test_claim_command_dry_run_then_apply(phase7_db):
    _app, runner = app_for(phase7_db)
    runner.invoke(args=["migrate-db", "--no-backup"])
    created = runner.invoke(
        args=["create-user", "--email", "Owner@Example.com", "--password-stdin"],
        input="my own long passphrase\n",
    )
    assert created.exit_code == 0, created.output
    dry = runner.invoke(args=["claim-legacy-data", "--email", "owner@example.com"])
    assert "Dry run: nothing changed" in dry.output
    applied = runner.invoke(args=["claim-legacy-data", "--email", "owner@example.com", "--apply"])
    assert "every unassigned record now belongs to owner@example.com" in applied.output
    again = runner.invoke(args=["claim-legacy-data", "--email", "owner@example.com", "--apply"])
    assert "Nothing to assign." in again.output


def test_claim_command_for_unknown_account_fails(phase7_db):
    _app, runner = app_for(phase7_db)
    runner.invoke(args=["migrate-db", "--no-backup"])
    result = runner.invoke(args=["claim-legacy-data", "--email", "ghost@example.com", "--apply"])
    assert result.exit_code != 0
    assert "No account with email ghost@example.com" in result.output


def test_create_user_rejects_weak_passwords_and_duplicates(app, runner):
    weak = runner.invoke(args=["create-user", "--email", "a@b.co", "--password-stdin"], input="short\n")
    assert weak.exit_code != 0 and "Password must be at least 12 characters" in weak.output
    dup = runner.invoke(
        args=["create-user", "--email", TEST_EMAIL, "--password-stdin"], input="a perfectly long one\n"
    )
    assert dup.exit_code != 0 and "already exists" in dup.output


def test_create_user_prompt_asks_twice_and_hides_input(app, runner):
    mismatch = runner.invoke(
        args=["create-user", "--email", "p@example.com"], input="first long password\nsecond long password\n"
    )
    assert mismatch.exit_code != 0
    assert "first long password" not in mismatch.output  # hidden input is not echoed


def test_set_password_ends_existing_sessions(app, runner, client):
    result = runner.invoke(
        args=["set-password", "--email", TEST_EMAIL, "--password-stdin"], input="replacement passphrase\n"
    )
    assert result.exit_code == 0, result.output
    assert client.get("/").status_code == 302  # signed out


def test_deactivate_blocks_sign_in_and_tokens(app, runner, client):
    assert runner.invoke(args=["deactivate-user", "--email", TEST_EMAIL]).exit_code == 0
    assert client.get("/").status_code == 302
    assert client.get("/api/v1/trades").status_code == 401
    assert runner.invoke(args=["activate-user", "--email", TEST_EMAIL]).exit_code == 0
    with app.app_context():
        assert get_user_by_email(get_db(), TEST_EMAIL)["is_active"]


def test_list_users_never_prints_hashes(app, runner):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    output = runner.invoke(args=["list-users"]).output
    assert TEST_EMAIL in output and OTHER_EMAIL in output
    assert "$argon2id$" not in output and "password" not in output.lower()


def test_reset_db_is_refused_in_production(db_path):
    app = create_app("production", overrides={"SECRET_KEY": "e" * 64, "DATABASE": db_path})
    result = app.test_cli_runner().invoke(args=["reset-db", "--yes"])
    assert result.exit_code != 0
    assert "reset-db is refused in production" in result.output
