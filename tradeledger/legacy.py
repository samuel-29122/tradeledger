"""Assigning pre-Phase 8 records to an account (Phase 8).

Plain English
-------------
A Phase 7 journal belonged to "whoever used this computer": its records
have no owner. Migration 3 keeps every record exactly as it was but marks
it UNASSIGNED, and unassigned records are visible to nobody. The person who
owns the data then runs one command to give them to their account:

    flask --app tradeledger create-user --email you@example.com
    flask --app tradeledger claim-legacy-data --email you@example.com          # dry run
    flask --app tradeledger claim-legacy-data --email you@example.com --apply

Why not automatic? Guessing would be a security bug: on a server, giving
old records to "the first account registered" hands your journal to
whoever registers first. The command is run by someone with access to the
server and the database file, which is the same trust as reading the file.

Technical summary
-----------------
* Dry run by default: reports what WOULD change and any conflicts; writes
  nothing.
* Conflicts that stop --apply: the account already has an instrument with
  the same symbol as an unassigned one (the per-user UNIQUE rule would
  fail), or an unassigned trade points at an instrument that already has
  an owner (impossible after a normal migration; checked anyway).
* --apply updates instruments and trades in ONE transaction and checks the
  counts afterwards; any mismatch rolls everything back.
"""

from .repositories import require_user_id


def legacy_counts(conn):
    if conn.dialect == "postgresql":
        return {"instruments": 0, "trades": 0}  # user_id is NOT NULL there
    instruments = conn.execute("SELECT COUNT(*) FROM instruments WHERE user_id IS NULL").fetchone()[0]
    trades = conn.execute("SELECT COUNT(*) FROM trades WHERE user_id IS NULL").fetchone()[0]
    return {"instruments": instruments, "trades": trades}


def find_conflicts(conn, user_id):
    if conn.dialect == "postgresql":
        return []
    conflicts = []
    rows = conn.execute(
        "SELECT legacy.symbol FROM instruments AS legacy "
        "JOIN instruments AS mine ON mine.symbol = legacy.symbol AND mine.user_id = ? "
        "WHERE legacy.user_id IS NULL ORDER BY legacy.symbol",
        (user_id,),
    ).fetchall()
    conflicts += [f"the account already has an instrument with symbol {row['symbol']}" for row in rows]
    rows = conn.execute(
        "SELECT t.id FROM trades AS t JOIN instruments AS i ON i.id = t.instrument_id "
        "WHERE t.user_id IS NULL AND i.user_id IS NOT NULL ORDER BY t.id"
    ).fetchall()
    conflicts += [
        f"unassigned trade #{row['id']} uses an instrument that already has an owner" for row in rows
    ]
    return conflicts


def claim_legacy_data(conn, *, user_id, apply):
    """Assign every unassigned record to `user_id`. Returns a summary dict:
    {"instruments": n, "trades": n, "conflicts": [...], "applied": bool}."""
    require_user_id(user_id)
    counts = legacy_counts(conn)
    conflicts = find_conflicts(conn, user_id)
    summary = {**counts, "conflicts": conflicts, "applied": False}
    if not apply or conflicts or (counts["instruments"] == 0 and counts["trades"] == 0):
        return summary
    with conn:
        moved_instruments = conn.execute(
            "UPDATE instruments SET user_id = ? WHERE user_id IS NULL", (user_id,)
        ).rowcount
        moved_trades = conn.execute(
            "UPDATE trades SET user_id = ? WHERE user_id IS NULL", (user_id,)
        ).rowcount
        if (moved_instruments, moved_trades) != (counts["instruments"], counts["trades"]) or any(
            legacy_counts(conn).values()
        ):
            raise RuntimeError("legacy claim: row counts changed during the update; nothing was changed")
    summary["applied"] = True
    return summary
