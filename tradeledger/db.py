"""Database connections for SQLite and PostgreSQL (Phase 8).

Plain English
-------------
TradeLedger can store its data in two kinds of database:

* SQLite: one file on your computer. Nothing to install; ideal for local
  development and the automated tests.
* PostgreSQL: a database server. Used by the Docker Compose stack and by
  the AWS design (Amazon RDS), where several application containers share
  one database.

The rest of the application should not care which one it is talking to.
This module gives both the same small interface, so the repositories write
their SQL once.

Technical summary
-----------------
connect(target)   open a connection: a file path means SQLite, a
                  "postgresql://..." URL means PostgreSQL. Scripts and
                  tests use this.
get_db()          inside a Flask request or CLI command: one connection per
                  request, stored on flask.g and released when the request
                  ends. PostgreSQL connections come from a pool.

A Connection offers:
    execute(sql, params)   SQL with "?" placeholders on BOTH databases
                           (converted to psycopg's "%s" for PostgreSQL)
    executescript(sql)     several statements, no parameters
    with conn: ...         a transaction: commit on success, roll back on
                           any exception; nested blocks use SAVEPOINTs
    numeric(text)          the value to bind for an exact decimal column
    dialect                "sqlite" or "postgresql"

Rows behave like sqlite3.Row: row["symbol"], row[0] and dict(row) all work.
Integrity failures from either database are raised as db.IntegrityError
with a `kind` ("unique", "foreign_key", "check", "not_null") and, where
the database reports one, the constraint name.

Why not SQLAlchemy? It is the usual, excellent choice for this job and is
the documented upgrade path (docs/PHASE_8_DATABASE_MIGRATION.md). It is not
installable in the environment this phase was built in, and TradeLedger's
existing repository layer already keeps every SQL statement in a handful of
functions, so a small adapter (this file) was enough. It deliberately does
NOT build SQL, map objects or manage schemas: it only smooths over the
differences between the two drivers.

Connections run in autocommit mode at the driver level, and transactions
are explicit (BEGIN ... COMMIT). That gives SQLite and PostgreSQL the same
behaviour: a statement outside `with conn:` is committed immediately, and
everything inside `with conn:` is all-or-nothing.
"""

import atexit
import functools
import logging
import sqlite3
import threading
from decimal import Decimal
from importlib import resources
from urllib.parse import urlsplit, urlunsplit

from flask import current_app, g

from . import migrations

SQLITE = "sqlite"
POSTGRESQL = "postgresql"

logger = logging.getLogger("tradeledger.db")

# Optional PostgreSQL driver override. Production uses psycopg 3. The test
# suite may register a different module with the same small interface
# (see tests/pg_libpq_bridge.py); application code never does.
_pg_driver_override = None


def register_postgres_driver(module):
    """Use `module` instead of psycopg for PostgreSQL (tests only).

    Phase 8.5 guards, so the test bridge can never serve real users:
      * only a module that declares TEST_ONLY = True is accepted;
      * the production configuration refuses to start while one is
        registered (PostgresSource below), pool or no pool.
    """
    global _pg_driver_override
    if module is not None and getattr(module, "TEST_ONLY", False) is not True:
        raise RuntimeError("only a module marked TEST_ONLY can replace psycopg")
    _pg_driver_override = module


def postgres_test_driver_active():
    """True while a test-only PostgreSQL driver replaces psycopg."""
    return _pg_driver_override is not None


def _postgres_driver():
    if _pg_driver_override is not None:
        return _pg_driver_override
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the installed packages
        raise RuntimeError(
            "PostgreSQL support needs the psycopg package: pip install -r requirements.txt"
        ) from exc
    return psycopg


# ---------- Errors ----------


class DatabaseError(Exception):
    """Base class for errors raised by this module."""


class DataError(DatabaseError):
    """PostgreSQL rejected a value's format, e.g. "abc" for a NUMERIC column.
    (SQLite stores anything and relies on CHECK constraints instead.)"""


class IntegrityError(DatabaseError):
    """A constraint stopped a write. Nothing from that statement was kept.

    kind        "unique", "foreign_key", "check", "not_null" or None
    constraint  the constraint (or trigger rule) name when known
    """

    def __init__(self, message, *, kind=None, constraint=None):
        super().__init__(message)
        self.kind = kind
        self.constraint = constraint


_PG_SQLSTATE_KINDS = {"23505": "unique", "23503": "foreign_key", "23514": "check", "23502": "not_null"}


def _sqlite_integrity(exc):
    message = str(exc)
    if message.startswith("UNIQUE constraint failed"):
        return IntegrityError(message, kind="unique", constraint=message.partition(": ")[2] or None)
    if message.startswith("FOREIGN KEY constraint failed"):
        return IntegrityError(message, kind="foreign_key")
    if message.startswith("CHECK constraint failed"):
        return IntegrityError(message, kind="check", constraint=message.partition(": ")[2] or None)
    if message.startswith("NOT NULL constraint failed"):
        return IntegrityError(message, kind="not_null", constraint=message.partition(": ")[2] or None)
    # Trigger rules raise "rule_name: explanation" (see migrations.py).
    head, sep, _ = message.partition(":")
    if sep and head.replace("_", "").isalnum():
        return IntegrityError(message, kind="check", constraint=head)
    return IntegrityError(message)


def _postgres_integrity(exc):
    diag = getattr(exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    kind = _PG_SQLSTATE_KINDS.get(getattr(exc, "sqlstate", None))
    return IntegrityError(str(exc).strip(), kind=kind, constraint=constraint)


# ---------- Rows and cursors ----------


class Row(dict):
    """A result row: row["name"], row[0] and dict(row) all work."""

    __slots__ = ("_values",)

    def __init__(self, names, values):
        super().__init__(zip(names, values, strict=True))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class Cursor:
    """The result of Connection.execute()."""

    def __init__(self, raw):
        self._raw = raw
        description = raw.description
        self._names = [getattr(col, "name", None) or col[0] for col in description] if description else None

    @property
    def rowcount(self):
        return self._raw.rowcount

    def _row(self, values):
        return None if values is None else Row(self._names, values)

    def fetchone(self):
        if self._names is None:
            return None
        return self._row(self._raw.fetchone())

    def fetchall(self):
        if self._names is None:
            return []
        return [Row(self._names, values) for values in self._raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


# ---------- Connections ----------


class Connection:
    """Common behaviour; see SQLiteConnection and PostgresConnection."""

    dialect = None

    def __init__(self, raw):
        self.raw = raw
        self._depth = 0

    # Subclasses provide _run(sql, params) -> driver cursor, _run_script(sql)
    # for several statements, _control(sql) for one transaction-control
    # statement, _integrity(exc) and _integrity_errors (the driver's class).

    _data_errors = ()

    def execute(self, sql, params=()):
        if isinstance(params, dict):
            raise TypeError("TradeLedger SQL uses positional '?' placeholders, not named ones")
        try:
            return Cursor(self._run(sql, tuple(params)))
        except self._integrity_errors as exc:
            raise self._integrity(exc) from exc
        except self._data_errors as exc:
            raise DataError(str(exc).strip()) from exc

    def executescript(self, sql):
        try:
            self._run_script(sql)
        except self._integrity_errors as exc:
            raise self._integrity(exc) from exc

    def numeric(self, text):
        """The value to bind for an exact decimal column (see subclasses)."""
        return text

    @property
    def in_transaction(self):
        return self._depth > 0

    # Transactions. `with conn:` mirrors sqlite3's behaviour (commit or roll
    # back), and works the same way on PostgreSQL. A nested `with conn:`
    # becomes a SAVEPOINT, so an inner failure that the caller handles does
    # not abort the outer transaction.
    def __enter__(self):
        if self._depth == 0:
            self._control("BEGIN")
        else:
            self._control(f"SAVEPOINT tl_sp_{self._depth}")
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._depth -= 1
        if self._depth == 0:
            self._control("ROLLBACK" if exc_type else "COMMIT")
        else:
            name = f"tl_sp_{self._depth}"
            if exc_type:
                self._control(f"ROLLBACK TO SAVEPOINT {name}")
            self._control(f"RELEASE SAVEPOINT {name}")
        return False

    def commit(self):
        """Statements outside `with conn:` are already committed. Kept so
        older code that calls commit() keeps working."""
        return None

    def rollback(self):
        if self._depth:
            self._depth = 0
            self._control("ROLLBACK")

    def close(self):
        self.raw.close()


class SQLiteConnection(Connection):
    dialect = SQLITE
    _integrity_errors = sqlite3.IntegrityError

    def _run(self, sql, params):
        return self.raw.execute(sql, params)

    def _run_script(self, sql):
        if self._depth:
            # sqlite3's executescript() would silently COMMIT the open transaction.
            raise DatabaseError("executescript() cannot run inside a transaction on SQLite")
        self.raw.executescript(sql)

    def _control(self, sql):
        self.raw.execute(sql)

    def _integrity(self, exc):
        return _sqlite_integrity(exc)

    def rollback(self):
        self._depth = 0
        if self.raw.in_transaction:
            self.raw.execute("ROLLBACK")

    def backup(self, target):
        self.raw.backup(target)


class PostgresConnection(Connection):
    dialect = POSTGRESQL

    def __init__(self, raw, driver):
        super().__init__(raw)
        self._integrity_errors = driver.IntegrityError
        self._data_errors = driver.DataError

    def _run(self, sql, params):
        cursor = self.raw.cursor()
        if params:
            cursor.execute(to_pyformat(sql), params)
        else:
            cursor.execute(sql)
        return cursor

    def _run_script(self, sql):
        self.raw.cursor().execute(sql)

    def _control(self, sql):
        self.raw.cursor().execute(sql)

    def _integrity(self, exc):
        return _postgres_integrity(exc)

    def numeric(self, text):
        """PostgreSQL columns are NUMERIC: bind a Decimal, which the driver
        sends as an exact numeric (never a float)."""
        return None if text is None else Decimal(text)


@functools.lru_cache(maxsize=512)
def to_pyformat(sql):
    """Convert "?" placeholders to psycopg's "%s".

    "?" inside a quoted SQL literal is left alone, and every literal "%" is
    doubled, because psycopg treats "%" as the start of a placeholder.
    """
    out, in_quote = [], False
    for char in sql:
        if char == "'":
            in_quote = not in_quote
            out.append(char)
        elif char == "%":
            out.append("%%")
        elif char == "?" and not in_quote:
            out.append("%s")
        else:
            out.append(char)
    return "".join(out)


def is_postgres_url(target):
    return isinstance(target, str) and target.startswith(("postgresql://", "postgres://"))


def connect(target):
    """Open a connection to a SQLite file path or a PostgreSQL URL."""
    if is_postgres_url(target):
        driver = _postgres_driver()
        return PostgresConnection(driver.connect(target, autocommit=True), driver)
    return connect_sqlite(target)


def connect_sqlite(path):
    # isolation_level=None: autocommit at the driver level; Connection adds
    # explicit transactions. check_same_thread=False is safe because one
    # connection is only ever used by one request at a time.
    raw = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    # SQLite does NOT enforce foreign keys unless you switch them on, per connection.
    raw.execute("PRAGMA foreign_keys = ON")
    # Wait up to 5 s for another writer instead of failing at once.
    raw.execute("PRAGMA busy_timeout = 5000")
    return SQLiteConnection(raw)


def redact_url(url):
    """postgresql://app:secret@db:5432/x -> postgresql://app:***@db:5432/x"""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = f"{parts.username}:***@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# ---------- Per-application connection source ----------


class SQLiteSource:
    dialect = SQLITE

    def __init__(self, path):
        self.path = path

    def acquire(self):
        return connect_sqlite(self.path)

    def release(self, conn):
        conn.rollback()
        conn.close()

    def describe(self):
        return f"sqlite:{self.path}"

    def close(self):
        return None


class PostgresSource:
    """A connection pool shared by the requests of one process.

    Gunicorn runs several worker processes; each gets its own pool, opened
    on first use (after the fork), so sockets are never shared between
    processes. Connections are checked when borrowed, so a database restart
    does not leave broken connections in the pool.
    """

    dialect = POSTGRESQL

    def __init__(self, conninfo, *, min_size, max_size, timeout, require_pool, label=None):
        self._conninfo = conninfo
        self._label = label
        self._min_size, self._max_size, self._timeout = min_size, max_size, timeout
        self._require_pool = require_pool
        self._pool = None
        self._lock = threading.Lock()
        if (
            require_pool
            and _pg_driver_override is not None
            and not getattr(_pg_driver_override, "ALLOW_IN_PRODUCTION_CONFIG_TESTS", False)
        ):
            # require_pool is set by the production configuration. The only
            # exception is the test suite checking production SETTINGS on
            # PostgreSQL: tests/conftest.py sets the flag on the bridge it
            # registers. A deployment never imports tests/ (it is not in the
            # Docker image), so it cannot reach this exception.
            raise RuntimeError(
                "A test-only PostgreSQL driver is registered; production must use psycopg. Refusing to start."
            )
        self._driver = _postgres_driver()

    def _get_pool(self):
        with self._lock:
            if self._pool is None:
                try:
                    from psycopg_pool import ConnectionPool
                except ImportError:
                    # A registered test driver (tests/pg_libpq_bridge.py) has no pool
                    # package. Production reaches this point with a test driver
                    # only in the test suite's production-settings tests (see
                    # __init__); a real deployment without psycopg_pool stops here.
                    if self._require_pool and _pg_driver_override is None:
                        raise RuntimeError(
                            "psycopg_pool is required for PostgreSQL in this environment"
                        ) from None
                    logger.warning("psycopg_pool is not installed: one connection per request")
                    self._pool = _UnpooledConnections(self._driver, self._conninfo)
                else:
                    self._pool = ConnectionPool(
                        self._conninfo,
                        kwargs={"autocommit": True},
                        min_size=self._min_size,
                        max_size=self._max_size,
                        timeout=self._timeout,
                        check=ConnectionPool.check_connection,
                        open=True,
                        name="tradeledger",
                    )
                    # Phase 8.5 final audit: close the pool when the process
                    # exits. Without this, psycopg_pool's four background
                    # threads each wait 5 seconds at interpreter shutdown, so
                    # every Gunicorn worker took about 20 seconds to stop
                    # (found only once the genuine psycopg_pool was tested).
                    atexit.register(self._close_quietly)
            return self._pool

    def _close_quietly(self):
        try:
            self.close()
        except Exception:
            logger.debug("closing the connection pool at exit failed", exc_info=True)

    def acquire(self):
        return PostgresConnection(self._get_pool().getconn(), self._driver)

    def release(self, conn):
        try:
            conn.rollback()
        finally:
            self._get_pool().putconn(conn.raw)

    def describe(self):
        if self._label:
            return self._label
        return redact_url(self._conninfo) if is_postgres_url(self._conninfo) else "postgresql"

    def close(self):
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()


class _UnpooledConnections:
    """Fallback used only when psycopg_pool is missing outside production."""

    def __init__(self, driver, conninfo):
        self._driver, self._conninfo = driver, conninfo

    def getconn(self):
        return self._driver.connect(self._conninfo, autocommit=True)

    def putconn(self, raw):
        raw.close()

    def close(self):
        return None


def build_conninfo(host, port, dbname, user, password, sslmode):
    """A libpq keyword/value connection string with every value quoted."""

    def quote(value):
        return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"

    fields = {"host": host, "port": port, "dbname": dbname, "user": user, "password": password}
    fields["sslmode"] = sslmode or "prefer"
    fields["application_name"] = "tradeledger"
    return " ".join(f"{key}={quote(value)}" for key, value in fields.items() if value not in (None, ""))


def make_source(app):
    """Choose the database from the configuration.

    DATABASE_URL  postgresql://user:password@host:5432/name  (Compose, CI)
    DB_HOST etc.  separate settings (AWS: ECS injects DB_PASSWORD from
                  Secrets Manager, so no URL containing it is ever built
                  in a task definition or logged)
    otherwise     the SQLite file in DATABASE
    """
    config = app.config
    pool = {
        "min_size": config["DB_POOL_MIN_SIZE"],
        "max_size": config["DB_POOL_MAX_SIZE"],
        "timeout": config["DB_POOL_TIMEOUT"],
        "require_pool": config["CONFIG_NAME"] == "production",
    }
    url = config.get("DATABASE_URL")
    if not url and is_postgres_url(config.get("DATABASE")):
        url = config["DATABASE"]  # tests pass a PostgreSQL URL where a file path would go
    if url:
        if not is_postgres_url(url):
            raise RuntimeError("DATABASE_URL must start with postgresql://")
        return PostgresSource(url, **pool)
    if config.get("DB_HOST"):
        conninfo = build_conninfo(
            config["DB_HOST"],
            config.get("DB_PORT"),
            config.get("DB_NAME"),
            config.get("DB_USER"),
            config.get("DB_PASSWORD"),
            config.get("DB_SSLMODE"),
        )
        label = (
            f"postgresql://{config.get('DB_USER')}@{config['DB_HOST']}:"
            f"{config.get('DB_PORT') or 5432}/{config.get('DB_NAME')}"
        )
        return PostgresSource(conninfo, label=label, **pool)
    return SQLiteSource(config["DATABASE"])


def source():
    return current_app.extensions["tradeledger_db"]


def get_db():
    """Return this request's connection, opening (or borrowing) it on first use."""
    if "db" not in g:
        g.db = source().acquire()
    return g.db


def close_db(exception=None):
    """Release the request's connection, if one was opened."""
    conn = g.pop("db", None)
    if conn is not None:
        source().release(conn)


# ---------- Schema ----------


def read_schema(dialect=SQLITE):
    """Return the baseline schema file shipped inside the package."""
    name = "schema.sql" if dialect == SQLITE else "schema_postgres.sql"
    return resources.files("tradeledger").joinpath(name).read_text(encoding="utf-8")


def init_schema(conn):
    """Bring the database up to the latest schema. Never deletes data.

    SQLite: run the version 1 baseline (schema.sql, CREATE ... IF NOT
    EXISTS), then every pending numbered migration (migrations.py).
    PostgreSQL: create the current schema from schema_postgres.sql on an
    empty database, then apply any later PostgreSQL migrations.

    Returns the list of migration versions applied (often empty).
    """
    if conn.dialect == POSTGRESQL:
        return migrations.migrate_postgres(conn, read_schema(POSTGRESQL))
    conn.executescript(read_schema())
    if migrations.current_version(conn) == 0:
        conn.execute("PRAGMA user_version = 1")
    return migrations.migrate(conn)


def drop_all(conn):
    """Delete every table and reset the schema version to 0.
    Used only by the reset-db command, which production refuses."""
    if conn.dialect == POSTGRESQL:
        conn.executescript(
            "DROP TABLE IF EXISTS trades, instruments, api_tokens, login_attempts, user_sessions, "
            "password_resets, recovery_codes, users, schema_migrations CASCADE"
        )
        return
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS trades;
        DROP TABLE IF EXISTS instruments;
        DROP TABLE IF EXISTS api_tokens;
        DROP TABLE IF EXISTS login_attempts;
        DROP TABLE IF EXISTS user_sessions;
        DROP TABLE IF EXISTS password_resets;
        DROP TABLE IF EXISTS recovery_codes;
        DROP TABLE IF EXISTS users;
        PRAGMA foreign_keys = ON;
        PRAGMA user_version = 0;
        """
    )


def schema_version(conn):
    return migrations.current_version(conn)


def list_tables(conn):
    if conn.dialect == POSTGRESQL:
        sql = (
            "SELECT table_name AS name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
    else:
        sql = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    return [row["name"] for row in conn.execute(sql)]


def ping(conn):
    """True when the database answers a trivial query (readiness check)."""
    return conn.execute("SELECT 1").fetchone()[0] == 1


def init_app(app):
    """Create the connection source and register the release hook."""
    app.extensions["tradeledger_db"] = make_source(app)
    app.teardown_appcontext(close_db)


def database_label(app):
    """A description that is safe to log (never contains a password)."""
    return app.extensions["tradeledger_db"].describe()
