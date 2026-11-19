"""Database setup, Flask integration, constraints and transactions.

Phase 8: these tests run on SQLite by default and on PostgreSQL when
TRADELEDGER_TEST_DATABASE_URL is set (see conftest.py). Errors are checked
as tradeledger.db.IntegrityError, the adapter's engine-neutral class, which
replaced sqlite3.IntegrityError in the Phase 7 version of this file.
"""

import pytest
from flask import g

from tests.factories import USER_ID
from tradeledger import create_app
from tradeledger.db import (
    DataError,
    IntegrityError,
    connect,
    get_db,
    init_schema,
    list_tables,
    schema_version,
)
from tradeledger.migrations import LATEST_VERSION

# ---------- Initialisation ----------


def index_names(conn):
    if conn.dialect == "postgresql":
        sql = "SELECT indexname AS name FROM pg_indexes WHERE schemaname = current_schema()"
    else:
        sql = "SELECT name FROM sqlite_master WHERE type = 'index'"
    return {row["name"] for row in conn.execute(sql)}


def test_every_table_is_created(conn):
    assert {"instruments", "trades", "users", "api_tokens", "login_attempts"} <= set(list_tables(conn))


def test_indexes_are_created(conn):
    assert {"idx_trades_instrument_id", "idx_trades_user_date", "idx_trades_user_closed"} <= index_names(conn)


def test_schema_version_is_recorded(conn):
    # Was "== 1" in Phase 2, "== 2" in Phase 4-7 and "== 3" in Phase 8.
    # Phase 8.5 added migration 4 (sessions, reset links, two-step).
    assert schema_version(conn) == LATEST_VERSION == 4


def test_init_schema_twice_keeps_existing_data(conn, eurusd_id):
    init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1


@pytest.mark.sqlite_only
def test_foreign_keys_are_switched_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ---------- Flask integration ----------


def test_testing_config_without_database_path_refuses_to_start():
    with pytest.raises(RuntimeError, match="DATABASE"):
        create_app("testing")


def test_development_database_defaults_to_instance_folder(monkeypatch):
    from tradeledger.config import DevelopmentConfig

    monkeypatch.setattr(DevelopmentConfig, "DATABASE", None)
    monkeypatch.setattr(DevelopmentConfig, "DATABASE_URL", None)
    monkeypatch.setattr(DevelopmentConfig, "DB_HOST", None)
    app = create_app("development")
    assert app.config["DATABASE"].endswith("tradeledger.db")
    assert "instance" in app.config["DATABASE"]


def test_get_db_reuses_one_connection_per_app_context(app):
    with app.app_context():
        assert get_db() is get_db()


def test_connection_is_released_when_app_context_ends(app):
    with app.app_context():
        conn = get_db()
    # SQLite: the connection is closed. PostgreSQL: it went back to the pool
    # and is no longer this request's. Either way, using it again is a bug
    # that must fail loudly rather than share a connection between requests.
    if conn.dialect == "sqlite":
        with pytest.raises(Exception, match="closed"):
            conn.execute("SELECT 1")


def test_connection_is_removed_from_g_after_teardown(app):
    with app.app_context():
        get_db()
        assert "db" in g
    with app.app_context():
        assert "db" not in g


# ---------- Constraints enforced by SQL itself ----------
# These bypass Python validation on purpose (raw INSERTs) to prove the
# database is a safety net even if application code has a bug.

RAW_TRADE_SQL = """
INSERT INTO trades (user_id, instrument_id, trade_date, direction, entry_price, stop_loss,
                    exit_price, position_size, fees, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

VALID_RAW = [USER_ID, None, "2026-03-02T08:15:00Z", "LONG", "1.085", "1.083", "1.089", "1", "7", "CLOSED"]
POSITIONS = {
    "user_id": 0,
    "trade_date": 2,
    "direction": 3,
    "entry_price": 4,
    "stop_loss": 5,
    "exit_price": 6,
    "position_size": 7,
    "fees": 8,
    "status": 9,
}


def raw_trade(instrument_id, **changes):
    values = list(VALID_RAW)
    values[1] = instrument_id
    for field, value in changes.items():
        values[POSITIONS[field]] = value
    return values


def test_sql_accepts_a_valid_raw_trade(conn, eurusd_id):
    conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id))


def test_sql_rejects_unknown_instrument(conn):
    with pytest.raises(IntegrityError) as info:
        conn.execute(RAW_TRADE_SQL, raw_trade(999))
    assert info.value.kind == "foreign_key"


def test_sql_rejects_another_users_instrument(conn, eurusd_id):
    """Phase 8: the two-column foreign key (instrument_id, user_id) makes the
    database refuse a trade on someone else's instrument, even if the
    application forgot to check."""
    from tests.conftest import OTHER_EMAIL, ensure_user

    other = ensure_user(conn, OTHER_EMAIL)
    with pytest.raises(IntegrityError) as info:
        conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id, user_id=other))
    assert info.value.kind == "foreign_key"


def test_sql_refuses_to_delete_instrument_that_has_trades(conn, eurusd_id):
    conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id))
    with pytest.raises(IntegrityError) as info:
        conn.execute("DELETE FROM instruments WHERE id = ?", (eurusd_id,))
    assert info.value.kind == "foreign_key"


@pytest.mark.parametrize(
    "changes",
    [
        {"direction": "BUY"},  # invalid direction
        {"status": "PENDING"},  # invalid status
        {"status": "CLOSED", "exit_price": None},  # closed without exit
        {"status": "OPEN"},  # open WITH an exit price
        {"stop_loss": "1.090"},  # long stop above entry
        {"stop_loss": "1.085"},  # stop equal to entry
        {"position_size": "0"},  # zero size
        {"position_size": "-1"},  # negative size
        {"entry_price": "abc"},  # not a number
        {"fees": "-2"},  # negative fees
        {"trade_date": "02/03/2026 08:15"},  # wrong date format
        {"user_id": None},  # no owner (PostgreSQL: NOT NULL; SQLite: legacy-only, see below)
    ],
)
def test_sql_rejects_invalid_raw_trades(conn, eurusd_id, changes):
    if changes == {"user_id": None} and conn.dialect == "sqlite":
        pytest.skip("SQLite allows NULL owners for unassigned pre-Phase 8 rows (migrations.py)")
    # PostgreSQL rejects "abc" as invalid NUMERIC input (a data error)
    # before any CHECK runs; SQLite stores text, so its CHECK rejects it.
    with pytest.raises((IntegrityError, DataError)):
        conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id, **changes))


def test_sql_rejects_short_with_stop_below_entry(conn, eurusd_id):
    with pytest.raises(IntegrityError, match="stop_on_correct_side"):
        conn.execute(
            RAW_TRADE_SQL,
            raw_trade(eurusd_id, direction="SHORT", stop_loss="1.080", exit_price="1.080"),
        )


def test_sql_rejects_lowercase_symbol(conn):
    with pytest.raises(IntegrityError) as info:
        conn.execute(
            "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, "
            "contract_multiplier, quote_currency) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (USER_ID, "eurusd", "x", "FOREX", "0.00001", "100000", "USD"),
        )
    assert info.value.kind == "check"


# ---------- Transactions ----------


def test_transaction_commits_on_success(db_path, conn, eurusd_id):
    other = connect(db_path)  # a second connection only sees committed data
    try:
        assert other.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1
    finally:
        other.close()


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError), conn:
        conn.execute(
            "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, "
            "contract_multiplier, quote_currency) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (USER_ID, "GBPUSD", "test", "FOREX", "0.00001", "100000", "USD"),
        )
        raise RuntimeError("something failed half-way")
    assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 0


def test_inner_failure_rolls_back_only_its_savepoint(conn):
    """A nested `with conn:` is a SAVEPOINT: an inner failure that the code
    handles undoes only the inner work, on both databases."""
    insert = (
        "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, "
        "contract_multiplier, quote_currency) VALUES (?, ?, 'x', 'FOREX', '0.1', '1', 'USD')"
    )
    with conn:
        conn.execute(insert, (USER_ID, "KEEP"))
        with pytest.raises(IntegrityError), conn:
            conn.execute(insert, (USER_ID, "KEEP"))  # duplicate symbol: fails inside the savepoint
    symbols = [row["symbol"] for row in conn.execute("SELECT symbol FROM instruments")]
    assert symbols == ["KEEP"]


def test_placeholders_inside_literals_and_percent_signs_are_preserved(conn):
    row = conn.execute("SELECT '?' AS q, '50%' AS pct, ? AS bound", ("a?b",)).fetchone()
    assert (row["q"], row["pct"], row["bound"]) == ("?", "50%", "a?b")


def test_named_parameters_are_refused(conn):
    with pytest.raises(TypeError, match="positional"):
        conn.execute("SELECT :x", {"x": 1})
