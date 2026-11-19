# Phase 8 database migration, import, backup and recovery

## 1. What changed in the schema (version 3)

| Table | Change |
|---|---|
| `users` (new) | email (unique, lower-case), Argon2id `password_hash`, `is_active`, `session_version`, created/updated/last sign-in times |
| `api_tokens` (new) | owner, name, SHA-256 of the token, display prefix, created/expires/last used/revoked times |
| `login_attempts` (new) | throttle counters: scope, HMAC digest of the email or IP, time |
| `instruments` | + `user_id`. A symbol is unique **per user** (`UNIQUE (user_id, symbol)`), instead of across the whole database |
| `trades` | + `user_id`, and a two-column foreign key `(instrument_id, user_id) → instruments (id, user_id)`, so a trade can only use its owner's instrument |

Version numbers: SQLite keeps its version in `PRAGMA user_version` (now 3). PostgreSQL keeps it in a `schema_migrations` table and starts directly at 3 (`schema_postgres.sql`), because a PostgreSQL TradeLedger database has no older history.

**Future migrations (4, 5, …) must be written for both engines.** `tests/test_postgres.py` checks that both end on the same version with the same tables and columns.

## 2. How migration 3 protects existing SQLite data

SQLite cannot change a UNIQUE rule or add a foreign key to an existing table, so migration 3 **rebuilds** `instruments` and `trades`:

1. `flask --app tradeledger migrate-db` first copies the database file with SQLite's online backup, to `tradeledger.db.v2-<UTC time>.bak` next to it.
2. Foreign-key enforcement is switched off **before** the transaction. SQLite ignores that setting inside a transaction.
3. In **one** transaction:
   - create the new tables;
   - copy every row unchanged (same ids, same text, same timestamps), with `user_id` set to NULL;
   - drop the old tables, rename the new ones, and recreate the indexes and the two `closed_at` triggers.
4. `PRAGMA foreign_key_check` must report nothing. Otherwise everything is rolled back and the version stays at 2.
5. Commit, set the version to 3, and switch foreign keys back on.

Evidence:
- `tests/test_legacy_and_cli_accounts.py::test_upgrade_from_phase_7_preserves_every_row` compares every column of every row before and after, apart from the new `user_id`.
- The tests in `tests/test_migrations.py` upgrade a real Phase 3 (version 1) database all the way to 3.

## 3. Who owns the old records? (the legacy policy)

After the upgrade, old records have **no owner**, and records with no owner are visible to **nobody**.

Ownership is never guessed. In particular, the first person to register on a server does **not** receive the old journal. The owner of the data assigns it deliberately:

```bash
flask --app tradeledger create-user --email you@example.com
flask --app tradeledger claim-legacy-data --email you@example.com           # dry run: counts and conflicts
flask --app tradeledger claim-legacy-data --email you@example.com --apply   # one transaction
```

The claim stops, and changes nothing, if:
- the account already has an instrument with the same symbol as an unassigned one; or
- an unassigned trade points at an instrument that already has an owner.

Running it again is harmless ("Nothing to assign").

## 4. Copying SQLite data into PostgreSQL

`scripts/migrate_sqlite_to_postgres.py`. Its rules:

- **The SQLite file is opened read-only.**
- **The owner must already exist** in PostgreSQL (`create-user` against the PostgreSQL database).
- **The owner's PostgreSQL journal must be empty.** The script never merges into, overwrites or deletes data.
- **It is a dry run** unless you pass `--apply`.
- **Every row is validated** with the application's own rules first. A bad row stops the import before anything is written, and the message names it.
- **Values are kept as they were:**
  - Decimals are written as exact `NUMERIC`.
  - Timestamps, strategy and session labels, and notes are copied as they are.
  - Legacy trades keep `closed_at` NULL; nothing is invented.
- **Everything is written in one transaction, then re-read and compared:**
  - row counts;
  - every decimal, compared as `Decimal`;
  - every text field;
  - that every trade points at its owner's instrument.

  Any difference rolls the whole import back.
- **A JSON audit file** (`--report`) records the counts, the duplicate warnings and the old→new id mapping. PostgreSQL assigns new ids. The audit never contains a password: the target URL is redacted.

```bash
DATABASE_URL=postgresql://... flask --app tradeledger init-db
DATABASE_URL=postgresql://... flask --app tradeledger create-user --email you@example.com
python scripts/migrate_sqlite_to_postgres.py --sqlite instance/tradeledger.db \
       --postgres "$DATABASE_URL" --owner-email you@example.com                 # dry run
python scripts/migrate_sqlite_to_postgres.py ... --apply --report import-audit.json
```

Which rows are copied:
- **A Phase 7 (version 2) file:** all rows.
- **A Phase 8 (version 3) file:** the unassigned rows, or with `--source-email`, one account's rows.

Tested in `tests/test_postgres.py` (on PostgreSQL 16, through the test-only libpq bridge). The tests cover:
- the dry run writes nothing;
- every value arrives exactly;
- the performance report matches the SQLite source;
- refusal when the target is not empty;
- refusal when the owner is unknown;
- other accounts are not touched;
- passwords never appear in the output.

## 5. Backups

| Environment | Method | Restore |
|---|---|---|
| Local SQLite | `migrate-db` makes a copy before migrating. Copy the file yourself while the app is stopped, or use `sqlite3 tradeledger.db ".backup backup.db"` | Stop the app and put the copy back |
| PostgreSQL (Compose, any server) | `pg_dump --format=custom --no-owner --file=tradeledger.dump "$DATABASE_URL"` | `pg_restore --no-owner --exit-on-error --dbname=NEW_DATABASE tradeledger.dump` into an **empty, new** database, check it, then switch the app over |
| AWS RDS | Automated daily backups with point-in-time recovery for 7 days (`db_backup_retention_days`). The deploy workflow takes a manual snapshot before every migration. A final snapshot is taken if the instance is deleted | Restore to a **new** instance (`aws rds restore-db-instance-to-point-in-time` or `restore-db-instance-from-db-snapshot`), check it, then point `DB_HOST` at it |

**Rules for backup files:**
- Backups contain everything: trades, notes, email addresses, password hashes and token hashes. Treat them like the database itself.
- Store them encrypted. RDS snapshots are encrypted because the instance is. For dump files, use an encrypted S3 bucket or an encrypted disk.
- Never put backups in Git, the Docker image or a public bucket.
- Delete backups you no longer need.

**Restore test (run, on synthetic data):** `scripts/backup_restore_check.py` creates two throwaway databases, fills one with two synthetic accounts, dumps it with `pg_dump`, restores it into the other with `pg_restore`, and compares:
- row counts in every table;
- every trade value;
- each account's performance report.

Result on PostgreSQL 16.15: **all three identical** (backup 20,691 bytes). An RDS restore has **not** been tested: no AWS account was used.

## 6. Rolling back

- **A migration fails:** it rolls back by itself and the version is unchanged. Fix the cause and run it again.
- **A migration succeeded but the new version is wrong:** migrations only go forward. Do not edit the schema by hand.
  - **SQLite:** stop the app and restore the `.bak` copy made by `migrate-db`.
  - **PostgreSQL:** restore the pre-deploy snapshot or dump into a **new** database, point the app at it, and redeploy the previous image tag.
- **A deployment fails its health checks:** ECS's deployment circuit breaker rolls the service back to the previous task definition automatically. To roll back by hand, run `aws ecs update-service --task-definition <previous revision>`.
- **The migration was applied and the old code must run again:** check whether the old code works with the new schema. Version 3 removes nothing, but Phase 7 code would not filter by owner, so **never run Phase 7 code against a multi-user database.**

## 7. Why these choices

- **Why `NUMERIC`, not text, in PostgreSQL?** It is exact, it supports real numeric CHECK constraints (the database itself refuses values with more than 15 digits), and psycopg returns Python `Decimal`.
- **Why are timestamps text, not `TIMESTAMPTZ`?** So both databases return identical values to one Python code path. Moving to `TIMESTAMPTZ` is a sensible later migration.
- **Why a small adapter, not SQLAlchemy?** SQLAlchemy Core is the usual choice and the documented upgrade path. It could not be installed in the build environment, and the existing repository layer needed only three things smoothed over:
  1. the `?` / `%s` placeholders;
  2. explicit transactions with savepoints;
  3. one exception type for constraint failures.
