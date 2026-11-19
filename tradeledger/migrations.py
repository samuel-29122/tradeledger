"""Numbered, forward-only database migrations.

How it works (SQLite)
---------------------
SQLite stores a whole number in the database file header, read and written
with PRAGMA user_version. TradeLedger uses it as the schema version:

    0  empty file, nothing created yet
    1  baseline tables from schema.sql (Phases 2 and 3)
    2  trades.closed_at column + its index and integrity triggers (Phase 4)
    3  user accounts and record ownership (Phase 8)
    4  per-device sessions, password-reset links, two-step verification
       (Phase 8.5)

migrate(conn) applies every migration newer than the file's version, in
order, each inside its own transaction together with the version bump. If a
migration fails, it is rolled back and the version is unchanged, so the
database is never left half-migrated. Running migrate() again is harmless.
The migrate-db command copies the database file before migrating.

How it works (PostgreSQL)
-------------------------
PostgreSQL support starts in Phase 8, so a PostgreSQL database has no
history to replay: schema_postgres.sql creates the version 3 structure
directly and records it in the schema_migrations table. Later versions
(4, 5, ...) must be added to BOTH dictionaries below, and the tests check
that the two engines end on the same version. Migrations take an advisory
lock, so two containers starting at the same moment cannot both migrate.

Migration 2: closed_at
----------------------
Adds an optional exit timestamp. Existing rows get NULL: we do NOT invent
exit times for historical trades. The cross-column rules are triggers,
because SQLite's ALTER TABLE cannot add table CHECK constraints.

Migration 3: users and ownership (Phase 8)
------------------------------------------
* New tables: users, api_tokens, login_attempts.
* instruments and trades gain user_id. A symbol is now unique PER USER,
  not across the whole database, so two users can both record EURUSD.
* trades has a two-column foreign key (instrument_id, user_id) ->
  instruments (id, user_id): the database itself refuses a trade that
  points at another user's instrument.
* SQLite cannot change a UNIQUE rule or add a foreign key to an existing
  table, so both tables are REBUILT: create the new table, copy every row
  unchanged (same ids, text, timestamps), drop the old table, rename.
  Foreign-key enforcement is switched off for the rebuild and
  PRAGMA foreign_key_check must report nothing before the commit.
* Existing rows get user_id NULL: "unassigned legacy data". Nobody can
  see unassigned records. The owner of the data assigns them, deliberately,
  with:  flask --app tradeledger claim-legacy-data --email you@example.com
  (a dry run unless --apply is given). Ownership is never guessed, and the
  first person to register on a server does not receive old data.

Migration 4: account security (Phase 8.5)
-----------------------------------------
Only NEW tables and NEW nullable columns, so no table is rebuilt and no
existing row changes (the same structure on both engines):
* user_sessions    one row per signed-in browser. The cookie holds a random
                   session key; the table holds only its SHA-256. Signing
                   out, "sign out this device" and password changes revoke
                   rows, so a copied cookie stops working on the server.
                   Times are whole seconds since 1970 (UTC).
* password_resets  one-time links an operator issues with the CLI
                   (create-reset-link). SHA-256 only; valid for 30 minutes.
* recovery_codes   single-use codes for two-step verification, SHA-256 only.
* users.totp_*     the two-step secret (AES-GCM encrypted, key derived from
                   SECRET_KEY), when it was switched on, and the last time
                   step accepted (so a code cannot be replayed).
"""

import sqlite3

TIMESTAMP_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z"
NOW_SQLITE = "(strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"


def _decimal_check(column, *, positive=True, nullable=False):
    """The Phase 2 rule for an exact decimal stored as TEXT."""
    rule = f"{column} NOT GLOB '*[^0-9.]*' AND {column} GLOB '*[0-9]*'"
    if positive:
        rule += f" AND CAST({column} AS REAL) > 0"
    if nullable:
        return f"CHECK ({column} IS NULL OR ({rule}))"
    return f"CHECK ({rule})"


_CLOSED_AT_TRIGGERS = """
        CREATE TRIGGER IF NOT EXISTS trades_closed_at_rules_insert
        BEFORE INSERT ON trades
        WHEN NEW.closed_at IS NOT NULL
             AND (NEW.status = 'OPEN' OR NEW.closed_at < NEW.trade_date)
        BEGIN
            SELECT RAISE(ABORT, 'closed_at_rules: an OPEN trade cannot have closed_at, and closed_at cannot be before trade_date');
        END;

        CREATE TRIGGER IF NOT EXISTS trades_closed_at_rules_update
        BEFORE UPDATE ON trades
        WHEN NEW.closed_at IS NOT NULL
             AND (NEW.status = 'OPEN' OR NEW.closed_at < NEW.trade_date)
        BEGIN
            SELECT RAISE(ABORT, 'closed_at_rules: an OPEN trade cannot have closed_at, and closed_at cannot be before trade_date');
        END;
"""

_TRADE_COLUMNS = (
    "id, instrument_id, trade_date, direction, entry_price, stop_loss, take_profit, exit_price, "
    "position_size, fees, status, strategy, session, notes, created_at, updated_at, closed_at"
)

MIGRATIONS = {
    2: f"""
        ALTER TABLE trades ADD COLUMN closed_at TEXT
            CHECK (closed_at IS NULL OR closed_at GLOB '{TIMESTAMP_GLOB}');

        CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades (closed_at);
        {_CLOSED_AT_TRIGGERS}
    """,
    3: f"""
        CREATE TABLE users (
            id              INTEGER PRIMARY KEY,
            email           TEXT    NOT NULL UNIQUE
                                    CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
            password_hash   TEXT    NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
            is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            session_version INTEGER NOT NULL DEFAULT 1 CHECK (session_version >= 1),
            created_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            updated_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            last_login_at   TEXT
        );

        CREATE TABLE api_tokens (
            id           INTEGER PRIMARY KEY,
            user_id      INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name         TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
            token_hash   TEXT    NOT NULL UNIQUE CHECK (length(token_hash) = 64),
            token_prefix TEXT    NOT NULL,
            created_at   TEXT    NOT NULL,
            expires_at   TEXT    NOT NULL,
            last_used_at TEXT,
            revoked_at   TEXT
        );
        CREATE INDEX idx_api_tokens_user ON api_tokens (user_id);

        CREATE TABLE login_attempts (
            id           INTEGER PRIMARY KEY,
            scope        TEXT NOT NULL,
            key_digest   TEXT NOT NULL,
            attempted_at TEXT NOT NULL
        );
        CREATE INDEX idx_login_attempts_lookup ON login_attempts (scope, key_digest, attempted_at);

        CREATE TABLE instruments_v3 (
            id                  INTEGER PRIMARY KEY,
            user_id             INTEGER REFERENCES users (id) ON DELETE RESTRICT,
            symbol              TEXT    NOT NULL
                                        CHECK (symbol = upper(symbol)
                                               AND length(symbol) BETWEEN 1 AND 20),
            name                TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
            asset_class         TEXT    NOT NULL
                                        CHECK (asset_class IN ('FOREX', 'COMMODITY', 'INDEX', 'STOCK', 'OTHER')),
            price_increment     TEXT    NOT NULL {_decimal_check("price_increment")},
            contract_multiplier TEXT    NOT NULL {_decimal_check("contract_multiplier")},
            quote_currency      TEXT    NOT NULL
                                        CHECK (length(quote_currency) = 3
                                               AND quote_currency = upper(quote_currency)),
            created_at          TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            CONSTRAINT instruments_user_symbol_unique UNIQUE (user_id, symbol),
            CONSTRAINT instruments_id_user_unique UNIQUE (id, user_id)
        );

        CREATE TABLE trades_v3 (
            id              INTEGER PRIMARY KEY,
            user_id         INTEGER REFERENCES users (id) ON DELETE RESTRICT,
            instrument_id   INTEGER NOT NULL REFERENCES instruments (id) ON DELETE RESTRICT,
            trade_date      TEXT    NOT NULL CHECK (trade_date GLOB '{TIMESTAMP_GLOB}'),
            direction       TEXT    NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
            entry_price     TEXT    NOT NULL {_decimal_check("entry_price")},
            stop_loss       TEXT    NOT NULL {_decimal_check("stop_loss")},
            take_profit     TEXT    {_decimal_check("take_profit", nullable=True)},
            exit_price      TEXT    {_decimal_check("exit_price", nullable=True)},
            position_size   TEXT    NOT NULL {_decimal_check("position_size")},
            fees            TEXT    NOT NULL DEFAULT '0' {_decimal_check("fees", positive=False)},
            status          TEXT    NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
            strategy        TEXT    CHECK (strategy IS NULL OR length(strategy) BETWEEN 1 AND 50),
            session         TEXT    CHECK (session IS NULL
                                           OR session IN ('ASIA', 'LONDON', 'NEW_YORK', 'OTHER')),
            notes           TEXT    CHECK (notes IS NULL OR length(notes) <= 2000),
            created_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            updated_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            closed_at       TEXT    CHECK (closed_at IS NULL OR closed_at GLOB '{TIMESTAMP_GLOB}'),

            CONSTRAINT trades_instrument_owner_fk FOREIGN KEY (instrument_id, user_id)
                REFERENCES instruments (id, user_id) ON DELETE RESTRICT,
            CONSTRAINT status_matches_exit CHECK (
                (status = 'OPEN'   AND exit_price IS NULL) OR
                (status = 'CLOSED' AND exit_price IS NOT NULL)
            ),
            CONSTRAINT stop_on_correct_side CHECK (
                (direction = 'LONG'  AND CAST(stop_loss AS REAL) < CAST(entry_price AS REAL)) OR
                (direction = 'SHORT' AND CAST(stop_loss AS REAL) > CAST(entry_price AS REAL))
            ),
            CONSTRAINT target_on_correct_side CHECK (
                take_profit IS NULL OR
                (direction = 'LONG'  AND CAST(take_profit AS REAL) > CAST(entry_price AS REAL)) OR
                (direction = 'SHORT' AND CAST(take_profit AS REAL) < CAST(entry_price AS REAL))
            )
        );

        INSERT INTO instruments_v3
            (id, user_id, symbol, name, asset_class, price_increment, contract_multiplier,
             quote_currency, created_at)
        SELECT id, NULL, symbol, name, asset_class, price_increment, contract_multiplier,
               quote_currency, created_at
        FROM instruments;

        INSERT INTO trades_v3 (user_id, {_TRADE_COLUMNS})
        SELECT NULL, {_TRADE_COLUMNS} FROM trades;

        DROP TABLE trades;
        DROP TABLE instruments;
        ALTER TABLE instruments_v3 RENAME TO instruments;
        ALTER TABLE trades_v3 RENAME TO trades;

        CREATE INDEX idx_trades_instrument_id ON trades (instrument_id);
        CREATE INDEX idx_trades_trade_date    ON trades (trade_date);
        CREATE INDEX idx_trades_status        ON trades (status);
        CREATE INDEX idx_trades_closed_at     ON trades (closed_at);
        CREATE INDEX idx_trades_user_date     ON trades (user_id, trade_date, id);
        CREATE INDEX idx_trades_user_closed   ON trades (user_id, closed_at);
        {_CLOSED_AT_TRIGGERS}
    """,  # noqa: S608 - constant DDL built from module constants; no user input
    4: """
        CREATE TABLE user_sessions (
            id           INTEGER PRIMARY KEY,
            user_id      INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            key_hash     TEXT    NOT NULL UNIQUE CHECK (length(key_hash) = 64),
            created_at   INTEGER NOT NULL,
            last_seen_at INTEGER NOT NULL,
            expires_at   INTEGER NOT NULL,
            user_agent   TEXT    NOT NULL DEFAULT '' CHECK (length(user_agent) <= 100),
            revoked_at   INTEGER
        );
        CREATE INDEX idx_user_sessions_user ON user_sessions (user_id, revoked_at);

        CREATE TABLE password_resets (
            id         INTEGER PRIMARY KEY,
            user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            token_hash TEXT    NOT NULL UNIQUE CHECK (length(token_hash) = 64),
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            used_at    INTEGER
        );
        CREATE INDEX idx_password_resets_user ON password_resets (user_id);

        CREATE TABLE recovery_codes (
            id        INTEGER PRIMARY KEY,
            user_id   INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            code_hash TEXT    NOT NULL UNIQUE CHECK (length(code_hash) = 64),
            used_at   INTEGER
        );
        CREATE INDEX idx_recovery_codes_user ON recovery_codes (user_id);

        ALTER TABLE users ADD COLUMN totp_secret TEXT;
        ALTER TABLE users ADD COLUMN totp_enabled_at TEXT;
        ALTER TABLE users ADD COLUMN totp_last_step INTEGER;
    """,
}

# Migrations that rebuild tables: foreign keys must be OFF while they run.
REBUILDS = {3}

# PostgreSQL migrations after the baseline in schema_postgres.sql.
POSTGRES_BASELINE_VERSION = 3
POSTGRES_MIGRATIONS = {
    4: """
        CREATE TABLE user_sessions (
            id           BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id      BIGINT  NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            key_hash     TEXT    NOT NULL CONSTRAINT user_sessions_key_unique UNIQUE CHECK (length(key_hash) = 64),
            created_at   BIGINT  NOT NULL,
            last_seen_at BIGINT  NOT NULL,
            expires_at   BIGINT  NOT NULL,
            user_agent   TEXT    NOT NULL DEFAULT '' CHECK (length(user_agent) <= 100),
            revoked_at   BIGINT
        );
        CREATE INDEX idx_user_sessions_user ON user_sessions (user_id, revoked_at);

        CREATE TABLE password_resets (
            id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id    BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            token_hash TEXT   NOT NULL CONSTRAINT password_resets_hash_unique UNIQUE CHECK (length(token_hash) = 64),
            created_at BIGINT NOT NULL,
            expires_at BIGINT NOT NULL,
            used_at    BIGINT
        );
        CREATE INDEX idx_password_resets_user ON password_resets (user_id);

        CREATE TABLE recovery_codes (
            id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            user_id   BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            code_hash TEXT   NOT NULL CONSTRAINT recovery_codes_hash_unique UNIQUE CHECK (length(code_hash) = 64),
            used_at   BIGINT
        );
        CREATE INDEX idx_recovery_codes_user ON recovery_codes (user_id);

        ALTER TABLE users ADD COLUMN totp_secret TEXT;
        ALTER TABLE users ADD COLUMN totp_enabled_at TEXT;
        ALTER TABLE users ADD COLUMN totp_last_step BIGINT;
    """,
}
POSTGRES_MIGRATION_NAMES = {4: "Phase 8.5: per-device sessions, password-reset links, two-step verification"}

LATEST_VERSION = max(MIGRATIONS)

# Any constant works; it only has to be the same for every TradeLedger process.
_POSTGRES_LOCK_ID = 7_340_658_812


def current_version(conn):
    if conn.dialect == "postgresql":
        exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()[0]
        if not exists:
            return 0
        return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    return conn.execute("PRAGMA user_version").fetchone()[0]


def pending_versions(conn):
    version = current_version(conn)
    registry = POSTGRES_MIGRATIONS if conn.dialect == "postgresql" else MIGRATIONS
    return [v for v in sorted(registry) if v > version]


def migrate(conn):
    """Apply all pending SQLite migrations. Returns the versions applied."""
    applied = []
    raw = conn.raw
    for version in pending_versions(conn):
        rebuild = version in REBUILDS
        if rebuild:
            raw.execute("PRAGMA foreign_keys = OFF")  # has no effect inside a transaction
        try:
            raw.executescript(f"BEGIN;\n{MIGRATIONS[version]}")
            problems = raw.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise sqlite3.IntegrityError(f"migration {version}: foreign_key_check failed: {problems[:5]}")
            raw.execute(f"PRAGMA user_version = {int(version)}")
            raw.execute("COMMIT")
        except sqlite3.Error:
            if raw.in_transaction:
                raw.execute("ROLLBACK")
            raise
        finally:
            if rebuild:
                raw.execute("PRAGMA foreign_keys = ON")
        applied.append(version)
    return applied


def migrate_postgres(conn, baseline_sql):
    """Create the PostgreSQL schema on an empty database, then apply any
    later PostgreSQL migrations. Each step is one transaction."""
    applied = []
    with conn:
        conn.execute("SELECT pg_advisory_xact_lock(?)", (_POSTGRES_LOCK_ID,))
        if current_version(conn) == 0:
            conn.executescript(baseline_sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (POSTGRES_BASELINE_VERSION, "PostgreSQL baseline (same structure as SQLite version 3)"),
            )
            applied.append(POSTGRES_BASELINE_VERSION)
    for version in pending_versions(conn):
        with conn:
            conn.execute("SELECT pg_advisory_xact_lock(?)", (_POSTGRES_LOCK_ID,))
            if current_version(conn) >= version:
                continue  # another process applied it while we waited for the lock
            conn.executescript(POSTGRES_MIGRATIONS[version])
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, POSTGRES_MIGRATION_NAMES.get(version, f"migration {version}")),
            )
        applied.append(version)
    return applied


def backup(conn, destination_path):
    """Copy a whole SQLite database to another file (SQLite's online backup).
    PostgreSQL backups use pg_dump or RDS snapshots (see docs)."""
    if conn.dialect != "sqlite":
        raise RuntimeError("backup() copies SQLite files only; use pg_dump for PostgreSQL")
    target = sqlite3.connect(destination_path)
    try:
        conn.backup(target)
    finally:
        target.close()
