-- TradeLedger database schema (PostgreSQL), schema version 3 (Phase 8)
--
-- The same structure and rules as the SQLite database after migration 3
-- (tradeledger/migrations.py), written in PostgreSQL's own types:
--
--   * Exact decimals (prices, sizes, fees, multipliers) are NUMERIC with no
--     fixed precision. NUMERIC is exact base-10 arithmetic and keeps the
--     scale you entered ('1.08540' stays '1.08540'), so values read back
--     exactly as written, like the SQLite TEXT columns. They are never REAL
--     or DOUBLE PRECISION (binary floating point). Validation limits values
--     to 15 digits before and 15 after the decimal point; the CHECKs below
--     repeat that limit as a second line of defence.
--   * Timestamps stay TEXT in the exact UTC form 'YYYY-MM-DDTHH:MM:SSZ',
--     checked by a regular expression. TIMESTAMPTZ would be the idiomatic
--     PostgreSQL type; TEXT was kept so both databases return identical
--     values and the Python code has one timestamp format. Text in this
--     form sorts in time order, so ORDER BY and range filters are correct.
--   * Ids are identity columns (GENERATED ALWAYS AS IDENTITY), the modern
--     replacement for SERIAL.
--   * Every user-owned row has user_id NOT NULL. Unlike SQLite, a PostgreSQL
--     database never holds unassigned legacy rows: the import script
--     (scripts/migrate_sqlite_to_postgres.py) assigns an owner explicitly.
--
-- Run by db.init_schema() inside one transaction on an EMPTY database.

CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    applied_at TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
);

CREATE TABLE users (
    id              BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email           TEXT    NOT NULL
                            CONSTRAINT users_email_unique UNIQUE
                            CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
    password_hash   TEXT    NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    session_version INTEGER NOT NULL DEFAULT 1 CHECK (session_version >= 1),
    created_at      TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    updated_at      TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    last_login_at   TEXT
);

CREATE TABLE api_tokens (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name         TEXT   NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    token_hash   TEXT   NOT NULL CONSTRAINT api_tokens_hash_unique UNIQUE CHECK (length(token_hash) = 64),
    token_prefix TEXT   NOT NULL,
    created_at   TEXT   NOT NULL,
    expires_at   TEXT   NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX idx_api_tokens_user ON api_tokens (user_id);

CREATE TABLE login_attempts (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope        TEXT   NOT NULL,
    key_digest   TEXT   NOT NULL,
    attempted_at TEXT   NOT NULL
);
CREATE INDEX idx_login_attempts_lookup ON login_attempts (scope, key_digest, attempted_at);

CREATE TABLE instruments (
    id                  BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             BIGINT  NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    symbol              TEXT    NOT NULL
                                CHECK (symbol = upper(symbol) AND length(symbol) BETWEEN 1 AND 20),
    name                TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    asset_class         TEXT    NOT NULL
                                CHECK (asset_class IN ('FOREX', 'COMMODITY', 'INDEX', 'STOCK', 'OTHER')),
    price_increment     NUMERIC NOT NULL
                                CHECK (price_increment > 0 AND price_increment < 1e15
                                       AND scale(price_increment) <= 15),
    contract_multiplier NUMERIC NOT NULL
                                CHECK (contract_multiplier > 0 AND contract_multiplier < 1e15
                                       AND scale(contract_multiplier) <= 15),
    quote_currency      TEXT    NOT NULL
                                CHECK (length(quote_currency) = 3 AND quote_currency = upper(quote_currency)),
    created_at          TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    CONSTRAINT instruments_user_symbol_unique UNIQUE (user_id, symbol),
    CONSTRAINT instruments_id_user_unique UNIQUE (id, user_id)
);

CREATE TABLE trades (
    id            BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       BIGINT  NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    instrument_id BIGINT  NOT NULL,
    trade_date    TEXT    NOT NULL
                          CHECK (trade_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'),
    closed_at     TEXT    CHECK (closed_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'),
    direction     TEXT    NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price   NUMERIC NOT NULL CHECK (entry_price > 0 AND entry_price < 1e15 AND scale(entry_price) <= 15),
    stop_loss     NUMERIC NOT NULL CHECK (stop_loss > 0 AND stop_loss < 1e15 AND scale(stop_loss) <= 15),
    take_profit   NUMERIC CHECK (take_profit > 0 AND take_profit < 1e15 AND scale(take_profit) <= 15),
    exit_price    NUMERIC CHECK (exit_price > 0 AND exit_price < 1e15 AND scale(exit_price) <= 15),
    position_size NUMERIC NOT NULL
                          CHECK (position_size > 0 AND position_size < 1e15 AND scale(position_size) <= 15),
    fees          NUMERIC NOT NULL DEFAULT 0 CHECK (fees >= 0 AND fees < 1e15 AND scale(fees) <= 15),
    status        TEXT    NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    strategy      TEXT    CHECK (strategy IS NULL OR length(strategy) BETWEEN 1 AND 50),
    session       TEXT    CHECK (session IS NULL OR session IN ('ASIA', 'LONDON', 'NEW_YORK', 'OTHER')),
    notes         TEXT    CHECK (notes IS NULL OR length(notes) <= 2000),
    created_at    TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    updated_at    TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),

    -- A trade can only use one of its OWNER's instruments.
    CONSTRAINT trades_instrument_owner_fk FOREIGN KEY (instrument_id, user_id)
        REFERENCES instruments (id, user_id) ON DELETE RESTRICT,
    CONSTRAINT status_matches_exit CHECK (
        (status = 'OPEN' AND exit_price IS NULL) OR (status = 'CLOSED' AND exit_price IS NOT NULL)
    ),
    CONSTRAINT stop_on_correct_side CHECK (
        (direction = 'LONG' AND stop_loss < entry_price) OR (direction = 'SHORT' AND stop_loss > entry_price)
    ),
    CONSTRAINT target_on_correct_side CHECK (
        take_profit IS NULL
        OR (direction = 'LONG' AND take_profit > entry_price)
        OR (direction = 'SHORT' AND take_profit < entry_price)
    ),
    -- SQLite enforces this rule with two triggers; PostgreSQL can state it
    -- directly. Same name, so error handling is identical.
    CONSTRAINT closed_at_rules CHECK (
        closed_at IS NULL OR (status = 'CLOSED' AND closed_at >= trade_date)
    )
);

CREATE INDEX idx_trades_instrument_id ON trades (instrument_id);
CREATE INDEX idx_trades_user_date     ON trades (user_id, trade_date, id);
CREATE INDEX idx_trades_user_closed   ON trades (user_id, closed_at);
CREATE INDEX idx_trades_user_status   ON trades (user_id, status);
