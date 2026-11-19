"""A Phase 7 (schema version 2) SQLite database with the sample data, built
the way Phase 7 stored it: no users and no owners. Used by the upgrade,
legacy-claim and SQLite-to-PostgreSQL import tests."""

import sqlite3
from pathlib import Path

from tradeledger.migrations import MIGRATIONS
from tradeledger.seed import SAMPLE_INSTRUMENTS, SAMPLE_TRADES

V1_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text()


def build_phase7_database(path):
    """A version 2 database exactly as Phase 7 left it, with the sample data."""
    raw = sqlite3.connect(path)
    raw.executescript(V1_SCHEMA)
    raw.executescript("BEGIN;" + MIGRATIONS[2] + "PRAGMA user_version = 2; COMMIT;")
    with raw:
        for i in SAMPLE_INSTRUMENTS:
            raw.execute(
                "INSERT INTO instruments (symbol, name, asset_class, price_increment, contract_multiplier, "
                "quote_currency) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    i["symbol"],
                    i["name"],
                    i["asset_class"],
                    i["price_increment"],
                    i["contract_multiplier"],
                    i["quote_currency"],
                ),
            )
        ids = dict(raw.execute("SELECT symbol, id FROM instruments").fetchall())
        for t in SAMPLE_TRADES:
            raw.execute(
                "INSERT INTO trades (instrument_id, trade_date, closed_at, direction, entry_price, stop_loss, "
                "take_profit, exit_price, position_size, fees, status, strategy, session, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ids[t["symbol"]],
                    t["trade_date"],
                    t.get("closed_at"),
                    t["direction"],
                    t["entry_price"],
                    t["stop_loss"],
                    t.get("take_profit"),
                    t.get("exit_price"),
                    t["position_size"],
                    t.get("fees", "0"),
                    "CLOSED" if t.get("exit_price") else "OPEN",
                    t.get("strategy"),
                    t.get("session"),
                    t.get("notes"),
                ),
            )
    raw.close()
