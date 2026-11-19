"""Prove a PostgreSQL backup can be restored (Phase 8).

A backup you have never restored is a hope, not a backup. This script:

  1. creates a throwaway source database and fills it with SYNTHETIC data
     (two accounts, the sample trades, one API token);
  2. backs it up with pg_dump (custom format, compressed);
  3. restores the file into a second, empty database with pg_restore;
  4. compares the two: row counts in every table, every trade value, and
     the performance report of each account (the Decimal engine);
  5. drops both throwaway databases.

It needs the PostgreSQL client tools (pg_dump, pg_restore) on PATH and a
server URL with permission to create databases. It never touches an
existing database: the two it creates have random names.

    python scripts/backup_restore_check.py --server postgresql://postgres:secret@localhost:5432/postgres
      (add --pg-bridge to use the test-only libpq bridge where psycopg is not installed)

The same procedure applies to production with pg_dump against RDS, or with
an RDS snapshot restored to a NEW instance (docs/PHASE_8_DATABASE_MIGRATION.md).
"""

import argparse
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from tradeledger.db import (  # noqa: E402
    connect,
    init_schema,
    list_tables,
    redact_url,
    register_postgres_driver,
)
from tradeledger.passwords import HashParameters, hash_password  # noqa: E402
from tradeledger.performance import build_report  # noqa: E402
from tradeledger.repositories import api_tokens  # noqa: E402
from tradeledger.repositories.users import create_user  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

FAST = HashParameters(memory_kib=1024, iterations=1, lanes=1)


def with_database(url, name):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, ""))


def snapshot(conn):
    tables = {}
    for table in list_tables(conn):  # names from the catalogue, not user input
        tables[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    trades = [tuple(row.values()) for row in conn.execute("SELECT * FROM trades ORDER BY id")]
    reports = {}
    for user_id in (1, 2):
        overall = build_report(conn, user_id=user_id).overall
        reports[user_id] = (overall.trades, overall.wins, overall.total_r, overall.expectancy_r)
    return tables, trades, reports


def main(argv=None):
    parser = argparse.ArgumentParser(description="pg_dump / pg_restore round trip with synthetic data")
    parser.add_argument("--server", required=True, help="postgresql:// URL of a maintenance database")
    parser.add_argument("--pg-bridge", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.pg_bridge:
        sys.path.insert(0, str(PROJECT / "tests"))
        import pg_libpq_bridge

        register_postgres_driver(pg_libpq_bridge)
    for tool in ("pg_dump", "pg_restore"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} is not on PATH")

    suffix = secrets.token_hex(4)
    source_name, target_name = f"tl_backup_src_{suffix}", f"tl_backup_dst_{suffix}"
    admin = connect(args.server)
    admin.execute(f"CREATE DATABASE {source_name}")
    admin.execute(f"CREATE DATABASE {target_name}")
    source_url, target_url = with_database(args.server, source_name), with_database(args.server, target_name)
    try:
        src = connect(source_url)
        init_schema(src)
        for email in ("alice@example.com", "bob@example.com"):
            user_id = create_user(src, email, hash_password("synthetic password", FAST))
            seed_sample_data(src, user_id=user_id)
        api_tokens.create_token(src, user_id=1, name="backup check", lifetime_days=1, max_active=10)
        before = snapshot(src)
        src.close()

        with tempfile.TemporaryDirectory() as folder:
            dump = Path(folder) / "tradeledger.dump"
            subprocess.run(
                ["pg_dump", "--format=custom", "--no-owner", f"--file={dump}", source_url], check=True
            )
            print(f"backup written: {dump.stat().st_size:,} bytes (custom format)")
            subprocess.run(
                ["pg_restore", "--no-owner", "--exit-on-error", f"--dbname={target_url}", str(dump)],
                check=True,
            )

        dst = connect(target_url)
        after = snapshot(dst)
        dst.close()
        checks = {
            "row counts per table": before[0] == after[0],
            "every trade value": before[1] == after[1],
            "performance reports": before[2] == after[2],
        }
        print(f"server: {redact_url(args.server)}")
        print(f"tables restored: {after[0]}")
        for name, ok in checks.items():
            print(f"  {'OK  ' if ok else 'FAIL'} {name}")
        return 0 if all(checks.values()) else 1
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {source_name}")
        admin.execute(f"DROP DATABASE IF EXISTS {target_name}")
        admin.close()


if __name__ == "__main__":
    sys.exit(main())
