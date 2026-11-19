-- TradeLedger database schema (SQLite)
--
-- Conventions
--   * Exact decimal values (prices, sizes, fees, multipliers) are stored as
--     canonical decimal TEXT, e.g. '1.08542'. Python converts them to
--     decimal.Decimal for calculations. They are never stored as REAL,
--     because binary floating point cannot represent most decimals exactly.
--   * Every timestamp is UTC in ISO 8601 form: 'YYYY-MM-DDTHH:MM:SSZ'.
--     Text in this form sorts in time order, so ORDER BY and range
--     filters work correctly.
--   * Enumerated values (direction, status, ...) are UPPER CASE.
--
-- Safe to run more than once: IF NOT EXISTS means nothing is dropped.

PRAGMA user_version = 1;  -- schema version, used for future migrations

CREATE TABLE IF NOT EXISTS instruments (
    id                  INTEGER PRIMARY KEY,
    symbol              TEXT    NOT NULL UNIQUE
                                CHECK (symbol = upper(symbol)
                                       AND length(symbol) BETWEEN 1 AND 20),
    name                TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    asset_class         TEXT    NOT NULL
                                CHECK (asset_class IN ('FOREX', 'COMMODITY', 'INDEX', 'STOCK', 'OTHER')),
    price_increment     TEXT    NOT NULL
                                CHECK (price_increment NOT GLOB '*[^0-9.]*'
                                       AND price_increment GLOB '*[0-9]*'
                                       AND CAST(price_increment AS REAL) > 0),
    contract_multiplier TEXT    NOT NULL
                                CHECK (contract_multiplier NOT GLOB '*[^0-9.]*'
                                       AND contract_multiplier GLOB '*[0-9]*'
                                       AND CAST(contract_multiplier AS REAL) > 0),
    quote_currency      TEXT    NOT NULL
                                CHECK (length(quote_currency) = 3
                                       AND quote_currency = upper(quote_currency)),
    created_at          TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY,
    instrument_id   INTEGER NOT NULL
                            REFERENCES instruments (id) ON DELETE RESTRICT,
    trade_date      TEXT    NOT NULL
                            CHECK (trade_date GLOB
                                   '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
    direction       TEXT    NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price     TEXT    NOT NULL
                            CHECK (entry_price NOT GLOB '*[^0-9.]*'
                                   AND entry_price GLOB '*[0-9]*'
                                   AND CAST(entry_price AS REAL) > 0),
    stop_loss       TEXT    NOT NULL
                            CHECK (stop_loss NOT GLOB '*[^0-9.]*'
                                   AND stop_loss GLOB '*[0-9]*'
                                   AND CAST(stop_loss AS REAL) > 0),
    take_profit     TEXT
                            CHECK (take_profit IS NULL
                                   OR (take_profit NOT GLOB '*[^0-9.]*'
                                       AND take_profit GLOB '*[0-9]*'
                                       AND CAST(take_profit AS REAL) > 0)),
    exit_price      TEXT
                            CHECK (exit_price IS NULL
                                   OR (exit_price NOT GLOB '*[^0-9.]*'
                                       AND exit_price GLOB '*[0-9]*'
                                       AND CAST(exit_price AS REAL) > 0)),
    position_size   TEXT    NOT NULL
                            CHECK (position_size NOT GLOB '*[^0-9.]*'
                                   AND position_size GLOB '*[0-9]*'
                                   AND CAST(position_size AS REAL) > 0),
    fees            TEXT    NOT NULL DEFAULT '0'
                            CHECK (fees NOT GLOB '*[^0-9.]*'
                                   AND fees GLOB '*[0-9]*'),
    status          TEXT    NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    strategy        TEXT    CHECK (strategy IS NULL OR length(strategy) BETWEEN 1 AND 50),
    session         TEXT    CHECK (session IS NULL
                                   OR session IN ('ASIA', 'LONDON', 'NEW_YORK', 'OTHER')),
    notes           TEXT    CHECK (notes IS NULL OR length(notes) <= 2000),
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),

    -- A closed trade must have an exit price; an open trade must not.
    CONSTRAINT status_matches_exit CHECK (
        (status = 'OPEN'   AND exit_price IS NULL) OR
        (status = 'CLOSED' AND exit_price IS NOT NULL)
    ),

    -- Stop-loss must be on the losing side of entry (never equal: zero risk).
    CONSTRAINT stop_on_correct_side CHECK (
        (direction = 'LONG'  AND CAST(stop_loss AS REAL) < CAST(entry_price AS REAL)) OR
        (direction = 'SHORT' AND CAST(stop_loss AS REAL) > CAST(entry_price AS REAL))
    ),

    -- Take-profit, when given, must be on the winning side of entry.
    CONSTRAINT target_on_correct_side CHECK (
        take_profit IS NULL OR
        (direction = 'LONG'  AND CAST(take_profit AS REAL) > CAST(entry_price AS REAL)) OR
        (direction = 'SHORT' AND CAST(take_profit AS REAL) < CAST(entry_price AS REAL))
    )
);

-- Indexes speed up the filters we use most often.
CREATE INDEX IF NOT EXISTS idx_trades_instrument_id ON trades (instrument_id);
CREATE INDEX IF NOT EXISTS idx_trades_trade_date    ON trades (trade_date);
CREATE INDEX IF NOT EXISTS idx_trades_status        ON trades (status);
