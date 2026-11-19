"""Shared pytest fixtures.

pytest loads this file automatically. Any test that names a fixture as a
parameter (app, client, conn, seeded_conn, ...) receives it.

Databases (Phase 8)
-------------------
By default every test gets a fresh SQLite file in pytest's tmp_path.
Set TRADELEDGER_TEST_DATABASE_URL to run the SAME tests against PostgreSQL:

    TRADELEDGER_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/tradeledger_test

Each test then gets its own empty PostgreSQL SCHEMA (a separate namespace
inside that database), which is dropped afterwards. Tests never touch
instance/tradeledger.db or any real journal. Tests that only make sense for
one engine (SQLite file tricks, PostgreSQL pooling) carry the markers
sqlite_only / postgres_only and are skipped on the other.

TRADELEDGER_PG_DRIVER=libpq-bridge selects the test-only libpq bridge
(tests/pg_libpq_bridge.py) instead of psycopg; see that file for why.

Accounts (Phase 8)
------------------
Every app and connection fixture creates the default test account
(TEST_EMAIL, user id 1 in a fresh database). `client` is signed in as that
user AND sends that user's API token, so Phase 1-7 tests exercise the same
pages and endpoints as before, now through the real authentication checks.
`anon_client` is a browser that has not signed in.
"""

import os
import time
import uuid
from urllib.parse import quote

import pytest

from tradeledger import create_app
from tradeledger.db import connect, init_schema, register_postgres_driver
from tradeledger.passwords import HashParameters, hash_password
from tradeledger.repositories import api_tokens
from tradeledger.repositories.instruments import create_instrument
from tradeledger.repositories.users import create_user, get_user_by_email
from tradeledger.seed import seed_sample_data

TEST_EMAIL = "trader@example.com"
TEST_PASSWORD = "correct horse battery staple"
OTHER_EMAIL = "other.trader@example.com"
FAST_HASH = HashParameters(memory_kib=1024, iterations=1, lanes=1)  # same as TestingConfig

PG_URL = os.environ.get("TRADELEDGER_TEST_DATABASE_URL")
if PG_URL and os.environ.get("TRADELEDGER_PG_DRIVER") == "libpq-bridge":
    from tests import pg_libpq_bridge

    # Lets the production-settings tests run on PostgreSQL too; without it
    # the adapter refuses a test driver under the production configuration.
    pg_libpq_bridge.ALLOW_IN_PRODUCTION_CONFIG_TESTS = True
    register_postgres_driver(pg_libpq_bridge)

_HASH_CACHE = {}


def password_hash(password=TEST_PASSWORD):
    """Hashing is deliberately slow; reuse one hash per password in tests."""
    if password not in _HASH_CACHE:
        _HASH_CACHE[password] = hash_password(password, FAST_HASH)
    return _HASH_CACHE[password]


def ensure_user(conn, email=TEST_EMAIL, password=TEST_PASSWORD):
    """Create the account if it does not exist yet; return its id."""
    existing = get_user_by_email(conn, email)
    if existing:
        return existing["id"]
    return create_user(conn, email, password_hash(password))


def pytest_collection_modifyitems(config, items):
    engine = "postgresql" if PG_URL else "sqlite"
    for item in items:
        if engine == "postgresql" and item.get_closest_marker("sqlite_only"):
            item.add_marker(pytest.mark.skip(reason="SQLite-specific test (running on PostgreSQL)"))
        if engine == "sqlite" and item.get_closest_marker("postgres_only"):
            item.add_marker(pytest.mark.skip(reason="needs TRADELEDGER_TEST_DATABASE_URL (PostgreSQL)"))


def _with_search_path(url, schema):
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options={quote(f'-csearch_path={schema}')}"


@pytest.fixture
def db_path(tmp_path):
    """Where this test's database lives: a SQLite file path, or (on
    PostgreSQL) a URL whose search_path points at a fresh, empty schema."""
    if not PG_URL:
        yield str(tmp_path / "test.db")
        return
    schema = "t_" + uuid.uuid4().hex[:16]
    admin = connect(PG_URL)
    admin.execute(f"CREATE SCHEMA {schema}")
    try:
        yield _with_search_path(PG_URL, schema)
    finally:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")
        admin.close()


@pytest.fixture
def app(db_path):
    """A fresh app using the testing configuration and a temporary database,
    with the default test account already created."""
    app = create_app("testing", overrides={"DATABASE": db_path})
    with app.app_context():
        from tradeledger.db import get_db

        init_schema(get_db())
        ensure_user(get_db())
    yield app
    app.extensions["tradeledger_db"].close()


@pytest.fixture
def user_id():
    """The default test account's id (the first account in a fresh database)."""
    return 1


def new_server_session(app, user_id, now=None):
    """Create a user_sessions row (Phase 8.5) and return its cookie key.
    For an account that does not exist, return a key with no row (the
    server then refuses it, which is what such tests expect)."""
    from tradeledger.db import IntegrityError, get_db
    from tradeledger.repositories import sessions

    now = int(time.time()) if now is None else now
    with app.app_context():
        try:
            key, _ = sessions.create_session(
                get_db(),
                user_id=user_id,
                now=now,
                max_age_seconds=app.config["SESSION_MAX_AGE_SECONDS"],
                user_agent="pytest",
            )
        except IntegrityError:
            key = "no-such-session-" + uuid.uuid4().hex
    return key


def sign_in(client, user_id=1, session_version=1):
    """Mark a test client as signed in, exactly as a successful login does:
    a signed cookie plus a server-side session row (Phase 8.5)."""
    now = int(time.time())
    key = new_server_session(client.application, user_id, now)
    with client.session_transaction() as session:
        session.clear()
        session.permanent = True
        session.update({"sid": key, "uid": user_id, "sv": session_version, "auth_at": now, "seen_at": now})


def issue_token(app, user_id=1, name="test token"):
    from tradeledger.db import get_db

    with app.app_context():
        token, _ = api_tokens.create_token(
            get_db(), user_id=user_id, name=name, lifetime_days=30, max_active=10
        )
    return token


@pytest.fixture
def client(app):
    """A browser signed in as the default test account, which also sends
    that account's API token on every request (for the JSON API tests)."""
    test_client = app.test_client()
    sign_in(test_client)
    test_client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {issue_token(app)}"
    return test_client


@pytest.fixture
def anon_client(app):
    """A browser that has not signed in and has no API token."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Runs Flask CLI commands (init-db, seed-db, ...) inside tests."""
    return app.test_cli_runner()


@pytest.fixture
def conn(db_path):
    """A plain connection to an empty, initialised temporary database that
    contains the default test account (and nothing else)."""
    connection = connect(db_path)
    init_schema(connection)
    ensure_user(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded_conn(conn, user_id):
    """The same temporary database, with the synthetic sample data in the
    default test account."""
    seed_sample_data(conn, user_id=user_id)
    return conn


@pytest.fixture
def eurusd_id(conn, user_id):
    """Creates one instrument for the default account and returns its id."""
    return create_instrument(
        conn,
        {
            "symbol": "EURUSD",
            "name": "Euro / US Dollar (test)",
            "asset_class": "FOREX",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        user_id=user_id,
    )


@pytest.fixture
def long_trade(eurusd_id):
    """Valid input for a closed LONG trade on EURUSD."""
    return {
        "instrument_id": eurusd_id,
        "trade_date": "2026-03-02T08:15:00Z",
        "closed_at": "2026-03-02T11:40:00Z",  # Phase 4: closed trades record an exit time
        "direction": "LONG",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "status": "CLOSED",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Test trade",
    }


# ---------- Phase 4: API fixtures ----------


@pytest.fixture
def seeded_client(app, client, user_id):
    """A signed-in test client whose account holds the synthetic sample data."""
    from tradeledger.db import get_db

    with app.app_context():
        seed_sample_data(get_db(), user_id=user_id)
    return client


@pytest.fixture
def trade_body():
    """A valid POST /api/v1/trades body for sample instrument 1 (EURUSD).
    Same prices as sample trade 1: net +393 USD, realised R +1.965."""
    return {
        "instrument_id": 1,
        "trade_date": "2026-04-01T08:15:00Z",
        "closed_at": "2026-04-01T11:40:00Z",
        "direction": "LONG",
        "status": "CLOSED",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "API test trade (synthetic)",
    }
