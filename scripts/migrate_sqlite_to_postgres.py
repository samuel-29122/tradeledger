"""Copy a TradeLedger SQLite journal into a PostgreSQL database (Phase 8).

Plain English
-------------
You have been using TradeLedger on your own computer (SQLite). Now you want
the same journal in PostgreSQL (Docker Compose or AWS RDS). This script
copies your instruments and trades across, gives them to ONE account that
you name, checks that every number arrived exactly, and prints an audit
summary. By default it only reports what it WOULD do.

    # 1. Create the account in PostgreSQL first (asks for a password):
    DATABASE_URL=postgresql://... flask --app tradeledger create-user --email you@example.com
    # 2. Dry run (reads both databases, writes nothing):
    python scripts/migrate_sqlite_to_postgres.py --sqlite instance/tradeledger.db \\
        --postgres "$DATABASE_URL" --owner-email you@example.com
    # 3. The real copy, with a JSON audit file:
    python scripts/migrate_sqlite_to_postgres.py ... --apply --report import-audit.json

Rules (why the script is strict)
--------------------------------
* The SQLite file is opened READ-ONLY; it is never changed.
* Ownership is explicit: --owner-email must name an existing PostgreSQL
  account. Nothing is ever given to "the first user".
* The owner's PostgreSQL journal must be EMPTY. The script never merges
  into, overwrites or deletes existing data. To redo an import, restore
  the target from a backup or use a fresh account.
* Which SQLite rows are copied:
    - a Phase 7 (schema version 2) file: every row (they have no owner);
    - a Phase 8 (version 3) file: the unassigned legacy rows, or with
      --source-email, the rows of that SQLite account.
* Every row goes through the same validation as the application. A row
  that would be refused today stops the import, with its id, before
  anything is written.
* Legacy closed trades with no recorded exit time keep closed_at NULL.
  created_at and updated_at are copied as they are.
* Everything is written in ONE transaction. After writing, the script
  re-reads every row and compares counts, every decimal (exactly, as
  Decimal) and every text field; any difference rolls the whole import
  back. Ids are NOT kept (PostgreSQL assigns new ones); the audit file
  lists the old -> new id mapping.
* Passwords and full connection strings are never printed.

Exit codes: 0 done (or dry run OK), 1 refused (nothing written), 2 error.
"""

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from tradeledger.db import connect, init_schema, redact_url, register_postgres_driver  # noqa: E402
from tradeledger.migrations import current_version  # noqa: E402
from tradeledger.observability import redact  # noqa: E402
from tradeledger.repositories.users import get_user_by_email, normalise_email  # noqa: E402
from tradeledger.validation import ValidationError, validate_instrument, validate_trade  # noqa: E402

INSTRUMENT_FIELDS = (
    "symbol",
    "name",
    "asset_class",
    "price_increment",
    "contract_multiplier",
    "quote_currency",
)
TRADE_FIELDS = (
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
    "notes",
)
DECIMALS = {
    "price_increment",
    "contract_multiplier",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
}


class Refused(Exception):
    """A safety rule stopped the import before anything was written."""


def read_source(path, source_email):
    """Read instruments and trades from the SQLite file (read-only)."""
    if not Path(path).is_file():
        raise Refused(f"SQLite file not found: {path}")
    raw = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    raw.row_factory = sqlite3.Row
    try:
        version = raw.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            raise Refused(
                f"SQLite schema version {version}: run 'flask --app tradeledger migrate-db' on it first"
            )
        if version == 2:
            if source_email:
                raise Refused(
                    "--source-email needs a Phase 8 (version 3) file; a version 2 file has no accounts"
                )
            owner_clause, params, label = "", (), "all rows (Phase 7 file, no owners)"
        elif source_email:
            row = raw.execute(
                "SELECT id FROM users WHERE email = ?", (normalise_email(source_email),)
            ).fetchone()
            if row is None:
                raise Refused(f"no account {normalise_email(source_email)} in the SQLite file")
            owner_clause, params, label = (
                "WHERE user_id = ?",
                (row["id"],),
                f"rows of {normalise_email(source_email)}",
            )
        else:
            owner_clause, params, label = "WHERE user_id IS NULL", (), "unassigned legacy rows"
        instruments = [
            dict(r)
            for r in raw.execute(
                f"SELECT id, {', '.join(INSTRUMENT_FIELDS)}, created_at FROM instruments {owner_clause} ORDER BY id",  # noqa: S608 - constant fragments
                params,
            )
        ]
        trades = [
            dict(r)
            for r in raw.execute(
                f"SELECT id, instrument_id, {', '.join(TRADE_FIELDS)}, created_at, updated_at "  # noqa: S608 - constant fragments
                f"FROM trades {owner_clause.replace('user_id', 'trades.user_id')} ORDER BY id",
                params,
            )
        ]
    finally:
        raw.close()
    return {"version": version, "selection": label, "instruments": instruments, "trades": trades}


def check_source(source):
    """Validate every row with the application's own rules; find duplicates."""
    problems, warnings = [], []
    known = {i["id"] for i in source["instruments"]}
    for instrument in source["instruments"]:
        try:
            validate_instrument({k: instrument[k] for k in INSTRUMENT_FIELDS})
        except ValidationError as exc:
            problems.append(f"instrument #{instrument['id']}: {exc.errors}")
    seen = {}
    for trade in source["trades"]:
        if trade["instrument_id"] not in known:
            problems.append(
                f"trade #{trade['id']}: its instrument #{trade['instrument_id']} is not being imported"
            )
            continue
        legacy = trade["status"] == "CLOSED" and trade["closed_at"] is None
        data = {k: trade[k] for k in TRADE_FIELDS if k != "status"} | {
            "status": trade["status"],
            "instrument_id": trade["instrument_id"],
        }
        try:
            validate_trade(data, require_closed_at=not legacy)
        except ValidationError as exc:
            problems.append(f"trade #{trade['id']}: {exc.errors}")
        key = (trade["instrument_id"], trade["trade_date"], trade["direction"], Decimal(trade["entry_price"]))
        if key in seen:
            warnings.append(
                f"trade #{trade['id']} looks like a duplicate of trade #{seen[key]} (both are copied)"
            )
        seen[key] = trade["id"]
    return problems, warnings


def check_target(conn, owner_email):
    owner = get_user_by_email(conn, owner_email)
    if owner is None:
        raise Refused(
            f"no PostgreSQL account {normalise_email(owner_email)}: create it first with "
            "'flask --app tradeledger create-user --email ...'"
        )
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (owner["id"],)).fetchone()[0]  # noqa: S608 - fixed names
        for table in ("instruments", "trades")
    }
    if any(counts.values()):
        raise Refused(
            f"the account {owner['email']} already has {counts['instruments']} instrument(s) and "
            f"{counts['trades']} trade(s) in PostgreSQL; the import only writes into an empty journal"
        )
    return owner


def write_and_verify(conn, source, owner_id):
    """Insert everything and verify it, in ONE transaction."""
    instrument_map, trade_map = {}, {}
    with conn:
        for i in source["instruments"]:
            clean = validate_instrument({k: i[k] for k in INSTRUMENT_FIELDS})
            row = conn.execute(
                "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, contract_multiplier, "
                "quote_currency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (
                    owner_id,
                    clean["symbol"],
                    clean["name"],
                    clean["asset_class"],
                    conn.numeric(i["price_increment"]),
                    conn.numeric(i["contract_multiplier"]),
                    clean["quote_currency"],
                    i["created_at"],
                ),
            ).fetchone()
            instrument_map[i["id"]] = row["id"]
        for t in source["trades"]:
            values = [owner_id, instrument_map[t["instrument_id"]]]
            values += [conn.numeric(t[f]) if f in DECIMALS else t[f] for f in TRADE_FIELDS]
            values += [t["created_at"], t["updated_at"]]
            row = conn.execute(
                f"INSERT INTO trades (user_id, instrument_id, {', '.join(TRADE_FIELDS)}, created_at, updated_at) "  # noqa: S608 - constant columns
                f"VALUES ({', '.join('?' for _ in values)}) RETURNING id",
                values,
            ).fetchone()
            trade_map[t["id"]] = row["id"]
        mismatches = verify(conn, source, owner_id, instrument_map, trade_map)
        if mismatches:
            raise RuntimeError("verification failed, nothing was kept: " + "; ".join(mismatches[:10]))
    return instrument_map, trade_map


def same(field, before, after):
    if before is None or after is None:
        return before is None and after is None
    if field in DECIMALS:
        return Decimal(str(before)) == Decimal(str(after))  # exact: no float anywhere
    return before == after


def verify(conn, source, owner_id, instrument_map, trade_map):
    problems = []
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (owner_id,)).fetchone()[0]  # noqa: S608 - fixed names
        for table in ("instruments", "trades")
    }
    if counts != {"instruments": len(source["instruments"]), "trades": len(source["trades"])}:
        problems.append(f"row counts differ: {counts}")
    for i in source["instruments"]:
        row = conn.execute("SELECT * FROM instruments WHERE id = ?", (instrument_map[i["id"]],)).fetchone()
        for field in (*INSTRUMENT_FIELDS, "created_at"):
            if not same(field, i[field], row[field]):
                problems.append(f"instrument #{i['id']} {field}: {i[field]!r} -> {row[field]!r}")
    for t in source["trades"]:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_map[t["id"]],)).fetchone()
        if row["instrument_id"] != instrument_map[t["instrument_id"]] or row["user_id"] != owner_id:
            problems.append(f"trade #{t['id']}: wrong instrument or owner")
        for field in (*TRADE_FIELDS, "created_at", "updated_at"):
            if not same(field, t[field], row[field]):
                problems.append(f"trade #{t['id']} {field}: {t[field]!r} -> {row[field]!r}")
    orphans = conn.execute(
        "SELECT COUNT(*) FROM trades t JOIN instruments i ON i.id = t.instrument_id "
        "WHERE t.user_id = ? AND i.user_id <> t.user_id",
        (owner_id,),
    ).fetchone()[0]
    if orphans:
        problems.append(f"{orphans} trade(s) point at another account's instrument")
    return problems


def run(argv=None, out=print):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sqlite", required=True, help="path to the SQLite file (opened read-only)")
    parser.add_argument("--postgres", required=True, help="postgresql:// URL of the target database")
    parser.add_argument(
        "--owner-email", required=True, help="existing PostgreSQL account that will own the rows"
    )
    parser.add_argument("--source-email", help="Phase 8 SQLite files: copy this account's rows")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run, writes nothing)")
    parser.add_argument("--report", help="write the audit summary to this JSON file")
    parser.add_argument("--pg-bridge", action="store_true", help=argparse.SUPPRESS)  # test-only libpq bridge
    args = parser.parse_args(argv)

    if args.pg_bridge:
        sys.path.insert(0, str(PROJECT / "tests"))
        import pg_libpq_bridge

        register_postgres_driver(pg_libpq_bridge)

    summary = {
        "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(Path(args.sqlite).resolve()),
        "target": redact_url(args.postgres),
        "mode": "apply" if args.apply else "dry-run",
    }
    try:
        source = read_source(args.sqlite, args.source_email)
        problems, warnings = check_source(source)
        legacy = sum(1 for t in source["trades"] if t["status"] == "CLOSED" and t["closed_at"] is None)
        summary |= {
            "source_schema_version": source["version"],
            "selection": source["selection"],
            "instruments": len(source["instruments"]),
            "trades": len(source["trades"]),
            "legacy_trades_without_exit_time": legacy,
            "warnings": warnings,
        }
        if problems:
            raise Refused("rows that the application would refuse:\n  " + "\n  ".join(problems))
        conn = connect(args.postgres)
        try:
            if current_version(conn) == 0 and not args.apply:
                raise Refused(
                    "the PostgreSQL database has no TradeLedger schema yet (run init-db, then create-user)"
                )
            if args.apply:
                init_schema(conn)  # creates or upgrades the schema; never deletes data
            owner = check_target(conn, args.owner_email)
            summary["owner"] = owner["email"]
            if args.apply:
                instrument_map, trade_map = write_and_verify(conn, source, owner["id"])
                summary |= {
                    "status": "imported and verified",
                    "instrument_id_map": {str(k): v for k, v in instrument_map.items()},
                    "trade_id_map": {str(k): v for k, v in trade_map.items()},
                }
            else:
                summary["status"] = "dry run: nothing written"
        finally:
            conn.close()
        code = 0
    except Refused as exc:
        summary |= {"status": "refused: nothing written", "reason": str(exc)}
        code = 1
    except Exception as exc:  # noqa: BLE001 - reported with a non-zero exit code; nothing was committed
        summary |= {"status": "error: nothing written", "reason": redact(str(exc))}
        code = 2
    summary["finished_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for key in ("status", "selection", "instruments", "trades", "legacy_trades_without_exit_time", "owner"):
        if key in summary:
            out(f"{key:>32}: {summary[key]}")
    for warning in summary.get("warnings", []):
        out(f"WARNING: {warning}")
    if "reason" in summary:
        out(summary["reason"])
    if args.report:
        Path(args.report).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        out(f"Audit summary written to {args.report}")
    return code, summary


if __name__ == "__main__":
    sys.exit(run()[0])
