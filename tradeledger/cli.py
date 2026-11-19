"""Command-line commands, run through Flask:

flask --app tradeledger init-db      create tables (keeps existing data)
flask --app tradeledger reset-db     delete ALL data and recreate tables (refused in production)
flask --app tradeledger seed-db --email E      add synthetic sample data to an account
flask --app tradeledger db-info      show tables, row counts and schema version
flask --app tradeledger analytics --email E    run the analytical SQL queries for an account
flask --app tradeledger run-sql "SELECT ..."   run your own read-only query
flask --app tradeledger report --email E       performance report (Phase 3 engine)
flask --app tradeledger migrate-db   back up, then apply pending schema migrations
flask --app tradeledger openapi      write the OpenAPI specification to a file

Accounts (Phase 8)
flask --app tradeledger create-user --email E          asks for the password twice
flask --app tradeledger set-password --email E         reset a forgotten password
flask --app tradeledger deactivate-user --email E      block sign-in and API tokens
flask --app tradeledger activate-user --email E
flask --app tradeledger list-users
flask --app tradeledger claim-legacy-data --email E [--apply]
                                     give pre-Phase 8 records to an account

Account security (Phase 8.5)
flask --app tradeledger create-reset-link --email E [--base-url URL]
                                     one-time 30-minute link to choose a new password
flask --app tradeledger disable-two-step --email E   lost phone and recovery codes
flask --app tradeledger grant-app-role --role NAME    PostgreSQL: row access only for the web app

Passwords are typed at a hidden prompt (or piped with --password-stdin for
automation). They are never accepted as a command-line argument, because
arguments are visible to other users of the machine and kept in shell history.
"""

import json
import re
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from . import analytics, migrations
from .api.openapi import build_spec
from .db import POSTGRESQL, database_label, drop_all, get_db, init_schema, list_tables, schema_version
from .legacy import claim_legacy_data, legacy_counts
from .passwords import hash_password, parameters_from_config, password_problem
from .performance import build_report
from .performance.formatting import (
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
    to_jsonable,
)
from .repositories import DuplicateError, api_tokens, password_resets, two_step
from .repositories.instruments import get_instrument_by_symbol
from .repositories.users import (
    bump_session_version,
    count_users,
    create_user,
    email_problem,
    get_user_by_email,
    normalise_email,
    set_active,
    set_password_hash,
)
from .seed import seed_sample_data
from .validation import ValidationError


def print_table(rows):
    """Print a list of dicts as an aligned text table."""
    if not rows:
        click.echo("(no rows)")
        return
    headers = list(rows[0].keys())
    cells = [[("NULL" if row[h] is None else str(row[h])) for h in headers] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    click.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
    click.echo("  ".join("-" * w for w in widths))
    for r in cells:
        click.echo("  ".join(value.ljust(w) for value, w in zip(r, widths, strict=True)))


def user_from_email(conn, email):
    user = get_user_by_email(conn, email)
    if user is None:
        raise click.ClickException(f"No account with email {normalise_email(email)}.")
    return user


def read_new_password(from_stdin, email):
    """A new password from a hidden prompt (typed twice) or from standard input."""
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    problem = password_problem(password, email=email)
    if problem:
        raise click.ClickException(f"Password {problem}")
    return password


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create any missing tables. Existing data is kept."""
    init_schema(get_db())
    click.echo(f"Database ready: {database_label(current_app)}")


@click.command("reset-db")
@with_appcontext
@click.confirmation_option(prompt="This deletes ALL accounts, trades and instruments. Continue?")
def reset_db_command():
    """Drop every table and recreate the empty schema."""
    if current_app.config["CONFIG_NAME"] == "production":
        raise click.ClickException("reset-db is refused in production. Restore from a backup instead.")
    conn = get_db()
    drop_all(conn)
    init_schema(conn)
    click.echo("Database reset. All tables are empty.")


@click.command("seed-db")
@click.option("--email", required=True, help="The account that receives the sample data.")
@with_appcontext
def seed_db_command(email):
    """Insert synthetic sample data into one account. Running it twice adds nothing new."""
    conn = get_db()
    init_schema(conn)
    user = user_from_email(conn, email)
    inserted = seed_sample_data(conn, user_id=user["id"])
    click.echo(
        f"Sample data: {inserted['instruments']} instrument(s) and {inserted['trades']} trade(s) inserted "
        f"for {user['email']}."
    )


@click.command("db-info")
@with_appcontext
def db_info_command():
    """Show which database is used, its schema version and row counts."""
    conn = get_db()
    click.echo(f"Database:       {database_label(current_app)}")
    click.echo(f"Schema version: {schema_version(conn)}")
    tables = list_tables(conn)
    if not tables:
        click.echo("No tables yet. Run: flask --app tradeledger init-db")
        return
    # Table names come from the database catalogue, not from user input, so it
    # is safe to place them in the SQL text. (Placeholders cannot stand for names.)
    rows = [
        {"table": name, "rows": conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]}  # noqa: S608 - name from the catalogue
        for name in tables
    ]
    print_table(rows)
    if "users" in tables and "trades" in tables:
        legacy = legacy_counts(conn)
        if legacy["instruments"] or legacy["trades"]:
            click.echo(
                f"Unassigned legacy records: {legacy['instruments']} instrument(s), {legacy['trades']} trade(s). "
                "Assign them with: flask --app tradeledger claim-legacy-data --email you@example.com"
            )


@click.command("analytics")
@click.option("--email", required=True, help="Whose records to analyse.")
@click.argument("name", required=False, type=click.Choice(sorted(analytics.ALL_QUERIES)))
@with_appcontext
def analytics_command(email, name):
    """Run one analytical query by NAME, or all of them, for one account."""
    conn = get_db()
    user = user_from_email(conn, email)
    names = [name] if name else list(analytics.ALL_QUERIES)
    for query_name in names:
        click.echo(f"\n== {query_name} ==")
        print_table(analytics.run_query(conn, query_name, user_id=user["id"]))


@click.command("run-sql")
@with_appcontext
@click.argument("sql")
def run_sql_command(sql):
    """Run your own SQL against a READ-ONLY connection (it cannot change data).

    This reads EVERY account's data: it is an operator tool for whoever
    already has the database credentials, not a feature for users."""
    if current_app.extensions["tradeledger_db"].dialect == POSTGRESQL:
        conn = get_db()
        try:
            with conn:
                conn.execute("SET TRANSACTION READ ONLY")
                rows = [dict(row) for row in conn.execute(sql).fetchall()]
        except Exception as exc:
            raise click.ClickException(f"SQL error: {exc}") from exc
        print_table(rows)
        return
    path = Path(current_app.config["DATABASE"]).resolve()
    if not path.exists():
        raise click.ClickException("Database file not found. Run init-db first.")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(sql).fetchall()]
    except sqlite3.Error as exc:
        raise click.ClickException(f"SQL error: {exc}") from exc
    finally:
        conn.close()
    print_table(rows)


def summary_row(label, summary):
    """One display row for a group summary (rounded for display only)."""
    return {
        "group": label,
        "trades": summary.trades,
        "W/L/BE": f"{summary.wins}/{summary.losses}/{summary.breakevens}",
        "win rate": format_rate(summary.win_rate),
        "total R": format_r(summary.total_r),
        "expectancy": format_r(summary.expectancy_r),
        "PF (R)": format_ratio(summary.profit_factor_r),
        "max DD": format_r_amount(summary.max_drawdown_r.amount),
    }


def print_report(report):
    o = report.overall
    click.echo("== Overall (closed trades) ==")
    lines = [
        ("Closed trades", o.trades),
        ("Open trades (not in results)", report.open_trades),
        ("Wins / losses / breakevens", f"{o.wins} / {o.losses} / {o.breakevens}"),
        ("Win rate / loss rate", f"{format_rate(o.win_rate)} / {format_rate(o.loss_rate)}"),
        ("Trades with realised R", o.r_trades),
        ("Total R (cumulative)", format_r(o.total_r)),
        ("Average R", format_r(o.average_r)),
        ("Average winning R", format_r(o.average_win_r)),
        ("Average losing R", format_r(o.average_loss_r)),
        ("Expectancy per trade", format_r(o.expectancy_r)),
        ("Profit factor (R)", format_ratio(o.profit_factor_r)),
        (
            "Average planned R:R",
            f"{format_ratio(o.average_planned_rr)} ({o.planned_rr_trades} trades with a target)",
        ),
        ("Longest win / loss streak", f"{o.longest_win_streak} / {o.longest_loss_streak}"),
        ("Max drawdown (R)", format_r_amount(o.max_drawdown_r.amount)),
    ]
    if o.best_trade_r:
        lines.append(
            (
                "Best trade (R)",
                f"#{o.best_trade_r.trade_id} {o.best_trade_r.symbol} {format_r(o.best_trade_r.value)}",
            )
        )
        lines.append(
            (
                "Worst trade (R)",
                f"#{o.worst_trade_r.trade_id} {o.worst_trade_r.symbol} {format_r(o.worst_trade_r.value)}",
            )
        )
    width = max(len(label) for label, _ in lines)
    for label, value in lines:
        click.echo(f"{label.ljust(width)}  {value}")

    click.echo("\n== Money, per quote currency (never added together) ==")
    print_table(
        [
            {
                "currency": m.currency,
                "trades": m.trades,
                "gross P&L": format_money(m.gross_pnl, m.currency),
                "fees": format_money(m.fees, m.currency),
                "net P&L": format_money(m.net_pnl, m.currency),
                "winners": format_money(m.winning_pnl, m.currency),
                "losers": format_money(m.losing_pnl, m.currency),
                "PF": format_ratio(m.profit_factor),
                "max DD": format_money(m.max_drawdown.amount, m.currency),
            }
            for m in o.money.values()
        ]
    )

    for title, groups in (
        ("By instrument", report.by_instrument),
        ("By strategy", report.by_strategy),
        ("By session", report.by_session),
        ("By day of week (UTC)", report.by_weekday),
        ("By month", report.by_month),
    ):
        click.echo(f"\n== {title} ==")
        print_table([summary_row(label, s) for label, s in groups.items()])

    click.echo("\n== Cumulative R (chart data; not account equity) ==")
    print_table(
        [
            {
                "trade": p.trade_id,
                "date": p.trade_date[:10],
                "symbol": p.symbol,
                "R": format_r(p.realised_r),
                "cumulative": format_r(p.cumulative_r),
            }
            for p in report.cumulative_r
        ]
    )

    for title, items in (
        ("Excluded trades", report.excluded),
        ("Closed trades without a realised R", report.r_unavailable),
    ):
        if items:
            click.echo(f"\n== {title} ==")
            for item in items:
                click.echo(f"#{item.trade_id}: {item.reason}")

    if report.unknown_close_time_trade_ids:
        ids = ", ".join(f"#{i}" for i in report.unknown_close_time_trade_ids)
        click.echo("\n== Closed trades with no recorded exit time (ordered by entry time) ==")
        click.echo(ids)


@click.command("report")
@click.option("--email", required=True, help="Whose trades to report on.")
@click.option("--instrument", help="Only this symbol, e.g. XAUUSD.")
@click.option("--start", help="First date to include, YYYY-MM-DD (UTC).")
@click.option("--end", help="Last date to include, YYYY-MM-DD (UTC).")
@click.option("--json", "as_json", is_flag=True, help="Exact JSON output instead of a rounded table.")
@with_appcontext
def report_command(email, instrument, start, end, as_json):
    """Performance report calculated by the Python analytics engine."""
    conn = get_db()
    user_id = user_from_email(conn, email)["id"]
    instrument_id = None
    if instrument:
        found = get_instrument_by_symbol(conn, instrument, user_id=user_id)
        if found is None:
            raise click.ClickException(f"No instrument with symbol '{instrument}'.")
        instrument_id = found["id"]
    try:
        report = build_report(conn, user_id=user_id, instrument_id=instrument_id, start=start, end=end)
    except ValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(to_jsonable(report), indent=2))
    else:
        print_report(report)


@click.command("migrate-db")
@with_appcontext
@click.option("--no-backup", is_flag=True, help="SQLite: skip the safety copy (not recommended).")
@click.option(
    "--backed-up", is_flag=True, help="PostgreSQL: confirm a backup (pg_dump or RDS snapshot) exists."
)
def migrate_db_command(no_backup, backed_up):
    """Back up the database, then apply any pending schema migrations."""
    conn = get_db()
    before = schema_version(conn)
    if conn.dialect == POSTGRESQL:
        if migrations.pending_versions(conn) and before >= 1 and not backed_up:
            raise click.ClickException(
                "Pending PostgreSQL migrations. Take a backup first (pg_dump or an RDS snapshot, see "
                "docs/PHASE_8_DATABASE_MIGRATION.md), then run again with --backed-up."
            )
        applied = init_schema(conn)
        click.echo(f"Schema version {before} -> {schema_version(conn)} (applied: {applied or 'none'})")
        return
    if not migrations.pending_versions(conn) and before >= 1:
        click.echo(f"Already at schema version {before}. Nothing to do.")
        return
    if not no_backup and before >= 1:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = Path(current_app.config["DATABASE"])
        backup_path = path.with_name(f"{path.name}.v{before}-{stamp}.bak")
        migrations.backup(conn, str(backup_path))
        click.echo(f"Backup written: {backup_path}")
    applied = init_schema(conn)
    click.echo(f"Schema version {before} -> {schema_version(conn)} (applied: {applied or 'none'})")
    legacy = legacy_counts(conn)
    if legacy["instruments"] or legacy["trades"]:
        click.echo(
            f"{legacy['instruments']} instrument(s) and {legacy['trades']} trade(s) are unassigned and hidden "
            "until you run: flask --app tradeledger claim-legacy-data --email you@example.com"
        )


# ---------- Accounts (Phase 8) ----------


@click.command("create-user")
@click.option("--email", required=True)
@click.option("--password-stdin", is_flag=True, help="Read the password from standard input (one line).")
@with_appcontext
def create_user_command(email, password_stdin):
    """Create an account. Works even when self-registration is disabled."""
    conn = get_db()
    problem = email_problem(email)
    if problem:
        raise click.ClickException(f"Email address {problem}")
    password = read_new_password(password_stdin, normalise_email(email))
    try:
        user_id = create_user(
            conn, email, hash_password(password, parameters_from_config(current_app.config))
        )
    except DuplicateError as exc:
        raise click.ClickException(str(exc)) from exc
    current_app.logger.info(
        "account created by operator", extra={"event": "auth.user.created", "user_id": user_id}
    )
    click.echo(f"Account created for {normalise_email(email)} (user id {user_id}).")


@click.command("set-password")
@click.option("--email", required=True)
@click.option("--password-stdin", is_flag=True, help="Read the password from standard input (one line).")
@with_appcontext
def set_password_command(email, password_stdin):
    """Set a new password (e.g. a forgotten one) and sign the account out everywhere."""
    conn = get_db()
    user = user_from_email(conn, email)
    password = read_new_password(password_stdin, user["email"])
    hashed = hash_password(password, parameters_from_config(current_app.config))
    set_password_hash(conn, user["id"], hashed, end_other_sessions=True)
    revoked = api_tokens.revoke_all(conn, user_id=user["id"])  # Phase 8.5: tokens too
    current_app.logger.info(
        "password set by operator",
        extra={"event": "auth.password.reset", "user_id": user["id"], "tokens_revoked": revoked},
    )
    click.echo(
        f"Password updated for {user['email']}. Existing sessions have been ended"
        f" and {revoked} API token(s) revoked."
    )


@click.command("create-reset-link")
@click.option("--email", required=True)
@click.option("--base-url", help="Address people use to reach TradeLedger (default: TRADELEDGER_PUBLIC_URL).")
@with_appcontext
def create_reset_link_command(email, base_url):
    """Print a one-time link (30 minutes) the account holder uses to choose a
    new password. Hand it over by a channel you trust; you never see their
    password. Creating a link cancels any earlier one."""
    conn = get_db()
    user = user_from_email(conn, email)
    if not user["is_active"]:
        raise click.ClickException(f"{user['email']} is deactivated. Run activate-user first.")
    base = (base_url or current_app.config["PUBLIC_URL"]).rstrip("/")
    if not base.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise click.ClickException("--base-url must start with https:// (or http://localhost for testing).")
    lifetime = current_app.config["PASSWORD_RESET_LIFETIME_SECONDS"]
    token = password_resets.create_link(
        conn, user_id=user["id"], now=int(time.time()), lifetime_seconds=lifetime
    )
    current_app.logger.info(
        "password reset link created", extra={"event": "auth.reset.created", "user_id": user["id"]}
    )
    click.echo(f"One-time link for {user['email']} (valid for {lifetime // 60} minutes, works once):")
    click.echo(f"{base}/reset-password?token={token}")


@click.command("disable-two-step")
@click.option("--email", required=True)
@with_appcontext
def disable_two_step_command(email):
    """Switch off two-step verification for someone who lost their phone AND
    their recovery codes. Check who is asking first. Also ends their sessions."""
    conn = get_db()
    user = user_from_email(conn, email)
    if not user["totp_enabled_at"]:
        click.echo(f"Two-step verification is already off for {user['email']}.")
        return
    two_step.disable(conn, user_id=user["id"])
    bump_session_version(conn, user["id"])
    current_app.logger.info(
        "two-step disabled by operator",
        extra={"event": "auth.two_step.disabled_by_operator", "user_id": user["id"]},
    )
    click.echo(f"Two-step verification switched off for {user['email']}; their sessions have been ended.")


APP_TABLES = (
    "users",
    "api_tokens",
    "login_attempts",
    "user_sessions",
    "password_resets",
    "recovery_codes",
    "instruments",
    "trades",
)


@click.command("grant-app-role")
@click.option("--role", required=True, help="An existing PostgreSQL role the web app will log in as.")
@with_appcontext
def grant_app_role_command(role):
    """PostgreSQL only: give an existing role exactly what the running app
    needs (read and write rows, use the id sequences) and nothing else (no
    CREATE, DROP, ALTER or TRUNCATE). Run it as the schema owner after every
    migration. See docs/TRADELEDGER_FINAL_SECURITY_REVIEW.md (least privilege)."""
    conn = get_db()
    if conn.dialect != POSTGRESQL:
        raise click.ClickException("grant-app-role is for PostgreSQL; SQLite has no database users.")
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise click.ClickException("Role names may use lower-case letters, digits and underscores only.")
    found = conn.execute(
        "SELECT rolsuper, rolcreaterole, rolcreatedb, rolname = current_user AS is_me FROM pg_roles WHERE rolname = ?",
        (role,),
    ).fetchone()
    if found is None:
        raise click.ClickException(f"Role {role} does not exist. Create it first (see the security review).")
    if found["is_me"]:
        raise click.ClickException("Use a separate role for the app, not the one that owns the tables.")
    if found["rolsuper"] or found["rolcreaterole"] or found["rolcreatedb"]:
        raise click.ClickException(f"Role {role} has administrative rights; the app role must not.")
    schema = conn.execute("SELECT current_schema()").fetchone()[0]
    quoted_role, quoted_schema = f'"{role}"', '"' + schema.replace('"', '""') + '"'
    tables = ", ".join(f"{quoted_schema}.{name}" for name in APP_TABLES)
    statements = [
        f"REVOKE ALL ON ALL TABLES IN SCHEMA {quoted_schema} FROM {quoted_role}",
        f"REVOKE CREATE ON SCHEMA {quoted_schema} FROM {quoted_role}",
        f"GRANT USAGE ON SCHEMA {quoted_schema} TO {quoted_role}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON {tables} TO {quoted_role}",
        f"GRANT SELECT ON {quoted_schema}.schema_migrations TO {quoted_role}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {quoted_schema} TO {quoted_role}",
    ]
    with conn:
        for statement in statements:
            conn.execute(statement)  # identifiers validated above; no user text reaches SQL unquoted
    click.echo(
        f"Role {role}: SELECT/INSERT/UPDATE/DELETE on {len(APP_TABLES)} tables, read-only schema_migrations,"
    )
    click.echo("sequence use for new ids; no CREATE, DROP, ALTER or TRUNCATE. Re-run after each migration.")


@click.command("deactivate-user")
@click.option("--email", required=True)
@with_appcontext
def deactivate_user_command(email):
    """Block sign-in and API access for an account (its data is kept)."""
    conn = get_db()
    user = user_from_email(conn, email)
    set_active(conn, user["id"], False)
    click.echo(f"{user['email']} deactivated: sessions ended, sign-in and API tokens refused.")


@click.command("activate-user")
@click.option("--email", required=True)
@with_appcontext
def activate_user_command(email):
    conn = get_db()
    user = user_from_email(conn, email)
    set_active(conn, user["id"], True)
    click.echo(f"{user['email']} can sign in again.")


@click.command("list-users")
@with_appcontext
def list_users_command():
    """Accounts with their status (never password hashes)."""
    rows = (
        get_db()
        .execute("SELECT id, email, is_active, created_at, last_login_at FROM users ORDER BY id")
        .fetchall()
    )
    print_table([{**dict(row), "is_active": bool(row["is_active"])} for row in rows])
    click.echo(f"{count_users(get_db())} account(s).")


@click.command("claim-legacy-data")
@click.option("--email", required=True, help="The account that becomes the owner.")
@click.option("--apply", "apply_changes", is_flag=True, help="Make the change (default: dry run).")
@with_appcontext
def claim_legacy_data_command(email, apply_changes):
    """Give unassigned pre-Phase 8 records to one account (dry run unless --apply)."""
    conn = get_db()
    user = user_from_email(conn, email)
    summary = claim_legacy_data(conn, user_id=user["id"], apply=apply_changes)
    click.echo(f"Unassigned records: {summary['instruments']} instrument(s), {summary['trades']} trade(s).")
    for conflict in summary["conflicts"]:
        click.echo(f"CONFLICT: {conflict}")
    if summary["conflicts"]:
        raise click.ClickException("Nothing was changed. Resolve the conflicts above first.")
    if summary["applied"]:
        current_app.logger.info(
            "legacy records assigned",
            extra={"event": "legacy.claimed", "user_id": user["id"], "trades": summary["trades"]},
        )
        click.echo(f"Done: every unassigned record now belongs to {user['email']}.")
    elif apply_changes:
        click.echo("Nothing to assign.")
    else:
        click.echo(f"Dry run: nothing changed. Re-run with --apply to assign them to {user['email']}.")


@click.command("openapi")
@with_appcontext
@click.option(
    "--output", default="docs/openapi.json", show_default=True, help="File to write the specification to."
)
def openapi_command(output):
    """Write the OpenAPI 3.1 specification (the same one served by the API)."""
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(build_spec(), indent=2) + "\n", encoding="utf-8")
    click.echo(f"OpenAPI specification written to {output}")


def init_app(app):
    for command in (
        init_db_command,
        reset_db_command,
        seed_db_command,
        db_info_command,
        analytics_command,
        run_sql_command,
        report_command,
        migrate_db_command,
        openapi_command,
        create_user_command,
        set_password_command,
        deactivate_user_command,
        activate_user_command,
        list_users_command,
        claim_legacy_data_command,
        create_reset_link_command,
        disable_two_step_command,
        grant_app_role_command,
    ):
        app.cli.add_command(command)
