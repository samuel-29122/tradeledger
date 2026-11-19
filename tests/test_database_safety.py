"""Phase 8.5: database safety under failure.

Plain English: what happens if the computer crashes in the middle of an
upgrade, if a step fails half-way, or if two people press the same button
at the same moment? These tests force each of those situations and check
the database is left either fully changed or not changed at all, never
half-way. They also prove the test-only PostgreSQL bridge can never run a
production server.
"""

import os
import re
import subprocess
import sys
import textwrap
import threading
import time
import types
from pathlib import Path

import pytest

from tests.conftest import TEST_PASSWORD
from tests.legacy_fixtures import build_phase7_database
from tradeledger import create_app, db
from tradeledger.db import connect, get_db, init_schema, list_tables
from tradeledger.migrations import current_version
from tradeledger.passwords import verify_password
from tradeledger.repositories import password_resets, sessions
from tradeledger.repositories.users import get_user

ROOT = Path(__file__).resolve().parent.parent
STRONG_KEY = "b16b00b5" * 8


# ---------- The test-only bridge can never serve production ----------


def test_only_a_module_marked_test_only_can_replace_psycopg():
    impostor = types.ModuleType("not_a_test_driver")
    with pytest.raises(RuntimeError, match="TEST_ONLY"):
        db.register_postgres_driver(impostor)


def test_production_refuses_to_start_while_a_test_driver_is_registered(monkeypatch):
    fake = types.ModuleType("fake_test_driver")
    fake.TEST_ONLY = True
    previous = db._pg_driver_override
    db.register_postgres_driver(fake)
    try:
        monkeypatch.setenv("SECRET_KEY", STRONG_KEY)
        with pytest.raises(RuntimeError, match="test-only PostgreSQL driver"):
            create_app(
                "production",
                overrides={
                    "SECRET_KEY": STRONG_KEY,
                    "DATABASE": None,
                    "DATABASE_URL": "postgresql://u@db.invalid/x",
                },
            )
    finally:
        db._pg_driver_override = previous


def test_the_bridge_does_not_exempt_itself_from_the_production_check():
    """Only tests/conftest.py may set the exemption, at run time."""
    source = (ROOT / "tests" / "pg_libpq_bridge.py").read_text(encoding="utf-8")
    assert "ALLOW_IN_PRODUCTION_CONFIG_TESTS" not in source


def test_the_application_package_never_imports_the_bridge():
    importing = re.compile(r"^\s*(from|import)\s+\S*pg_libpq_bridge", re.M)
    for path in (ROOT / "tradeledger").rglob("*.py"):
        assert not importing.search(path.read_text(encoding="utf-8")), path


def test_the_bridge_is_not_in_the_docker_build_context():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ignore[ignore.index("*")] == "*"  # deny everything first...
    assert "!tests/" not in ignore and not any(
        line.startswith("!tests") for line in ignore
    )  # ...tests never allowed


# ---------- A crash in the middle of a SQLite migration ----------


@pytest.mark.sqlite_only
def test_a_crash_during_a_migration_leaves_the_previous_version_intact(tmp_path):
    """Kill the process (os._exit, no clean-up, no rollback code) while
    migration 4 is running. SQLite's journal must undo the partial work on
    the next open: version 3, no new tables, every row unchanged. Then a
    normal start-up finishes the upgrade."""
    path = tmp_path / "crash.db"
    build_phase7_database(path)  # version 2 with synthetic data
    conn = connect(str(path))
    from tradeledger import migrations

    for version in (3,):
        conn.raw.execute("PRAGMA foreign_keys = OFF")
        conn.raw.executescript(f"BEGIN;\n{migrations.MIGRATIONS[version]}")
        conn.raw.execute(f"PRAGMA user_version = {version}")
        conn.raw.execute("COMMIT")
        conn.raw.execute("PRAGMA foreign_keys = ON")
    before = [tuple(r) for r in conn.execute("SELECT * FROM trades ORDER BY id").fetchall()]
    conn.close()

    script = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        from tradeledger.db import connect
        from tradeledger import migrations
        conn = connect({str(path)!r})
        steps = {{"n": 0}}
        def crash():
            steps["n"] += 1
            if steps["n"] > 3:
                os._exit(17)   # power cut: no rollback, no close
            return 0
        conn.raw.set_progress_handler(crash, 50)
        migrations.migrate(conn)
        print("migration finished without crashing")
        """
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
    assert result.returncode == 17, result.stdout + result.stderr

    after = connect(str(path))
    try:
        assert current_version(after) == 3
        assert "user_sessions" not in list_tables(after)
        assert [tuple(r) for r in after.execute("SELECT * FROM trades ORDER BY id").fetchall()] == before
        assert after.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert init_schema(after) == [4]  # the next start finishes the job
        assert current_version(after) == 4 and "user_sessions" in list_tables(after)
    finally:
        after.close()


# ---------- An interrupted PostgreSQL migration ----------


@pytest.mark.postgres_only
def test_a_postgres_migration_killed_half_way_changes_nothing(db_path):
    """Start migration 4 in another process, make it pause inside its
    transaction, terminate that server process (as a crash or a deploy
    timeout would), then check nothing of migration 4 remains and that a
    normal start-up applies it cleanly."""
    from tradeledger import migrations

    baseline = connect(db_path)  # build version 3 by hand: the baseline only
    with baseline:
        baseline.execute("SELECT pg_advisory_xact_lock(?)", (migrations._POSTGRES_LOCK_ID,))
        baseline.executescript(db.read_schema(db.POSTGRESQL))
        baseline.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)", (3, "PostgreSQL baseline")
        )
    assert current_version(baseline) == 3

    script = textwrap.dedent(
        f"""
        import os, sys
        sys.path.insert(0, {str(ROOT)!r})
        from tests import pg_libpq_bridge
        from tradeledger import db, migrations
        if os.environ.get("TRADELEDGER_PG_DRIVER") == "libpq-bridge":
            db.register_postgres_driver(pg_libpq_bridge)
        # Some real work first, then a pause (pg_stat_activity shows only
        # the first 1 kB of a statement, so the marker comes early).
        migrations.POSTGRES_MIGRATIONS[4] = (
            "CREATE TABLE half_done_marker (x INTEGER); SELECT pg_sleep(30) /* tl-slow-migration */; "
            + migrations.POSTGRES_MIGRATIONS[4]
        )
        conn = db.connect({db_path!r})
        migrations.migrate_postgres(conn, "")
        """
    )
    worker = subprocess.Popen(
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        killed = False
        for _ in range(100):
            rows = baseline.execute(
                "SELECT pid FROM pg_stat_activity WHERE query LIKE ? AND pid <> pg_backend_pid()",
                ("%tl-slow-migration%",),
            ).fetchall()
            if rows:
                baseline.execute("SELECT pg_terminate_backend(CAST(? AS integer))", (rows[0]["pid"],))
                killed = True
                break
            if worker.poll() is not None:
                break
            threading.Event().wait(0.1)
        assert killed, "the slow migration never started: " + (
            worker.communicate(timeout=5)[0] if worker.poll() is not None else ""
        )
        output = worker.communicate(timeout=30)[0]
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate()
    assert worker.returncode != 0, output  # the worker died with its connection
    assert current_version(baseline) == 3
    assert "user_sessions" not in list_tables(baseline)
    assert "half_done_marker" not in list_tables(baseline)  # the work done before the kill is gone
    assert init_schema(baseline) == [4]
    assert current_version(baseline) == 4
    baseline.close()


# ---------- Failed transactions in the Phase 8.5 flows ----------


def test_a_failure_while_resetting_a_password_leaves_the_link_and_password_unchanged(app, monkeypatch):
    from click.testing import CliRunner
    from flask.cli import ScriptInfo

    from tests.web_helpers import csrf_token
    from tradeledger.cli import create_reset_link_command
    from tradeledger.web import reset

    output = (
        CliRunner()
        .invoke(
            create_reset_link_command,
            ["--email", "trader@example.com", "--base-url", "http://localhost"],
            obj=ScriptInfo(create_app=lambda: app),
        )
        .output
    )
    link = output.strip().splitlines()[-1].replace("http://localhost", "")
    client = app.test_client()
    client.get(link)

    def broken_revoke(*args, **kwargs):
        raise RuntimeError("simulated failure after the link was marked used")

    monkeypatch.setattr(reset.api_tokens, "revoke_all", broken_revoke)
    app.config["PROPAGATE_EXCEPTIONS"] = True
    with pytest.raises(RuntimeError, match="simulated"):
        client.post(
            "/reset-password",
            data={
                "csrf_token": csrf_token(client, "/reset-password"),
                "new_password": "orchard granite velvet comet",
                "confirm_password": "orchard granite velvet comet",
            },
        )
    with app.app_context():
        conn = get_db()
        assert verify_password(TEST_PASSWORD, get_user(conn, 1)["password_hash"])
        assert conn.execute("SELECT used_at FROM password_resets").fetchone()["used_at"] is None
        assert (
            conn.execute("SELECT COUNT(*) FROM user_sessions WHERE revoked_at IS NOT NULL").fetchone()[0] == 0
        )
    monkeypatch.undo()
    retry = app.test_client()
    retry.get(link)
    assert (
        retry.post(
            "/reset-password",
            data={
                "csrf_token": csrf_token(retry, "/reset-password"),
                "new_password": "orchard granite velvet comet",
                "confirm_password": "orchard granite velvet comet",
            },
        ).status_code
        == 303
    )


# ---------- Two requests at the same moment ----------


def _race(db_path, work, count=2):
    """Run `work(conn)` in `count` threads with separate connections, all
    released together. Returns the results."""
    barrier = threading.Barrier(count)
    results, errors = [], []

    def run():
        conn = connect(db_path)
        try:
            barrier.wait()
            results.append(work(conn))
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=run) for _ in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    return results, errors


def test_one_reset_link_used_twice_at_once_works_only_once(app, db_path):
    with app.app_context():
        token = password_resets.create_link(get_db(), user_id=1, now=1_900_000_000, lifetime_seconds=1800)
        link = password_resets.find_valid(get_db(), token, now=1_900_000_001)

    def use(conn):
        with conn:
            return password_resets.consume(conn, link["id"], user_id=1, now=1_900_000_002)

    results, errors = _race(db_path, use, count=4)
    assert sorted(results) == [False, False, False, True] or (errors and results.count(True) == 1)
    assert results.count(True) == 1


def test_one_session_revoked_from_two_places_at_once(app, db_path):
    with app.app_context():
        _, session_id = sessions.create_session(get_db(), user_id=1, now=1_900_000_000, max_age_seconds=3600)

    def revoke(conn):
        with conn:
            return sessions.revoke(conn, session_id, user_id=1, now=1_900_000_010)

    results, _ = _race(db_path, revoke, count=4)
    assert results.count(True) == 1


def test_one_two_step_code_submitted_twice_at_once_is_accepted_once(app, db_path):
    from tradeledger.repositories import two_step

    with app.app_context():
        two_step.enable(get_db(), user_id=1, encrypted_secret="v1:x", accepted_step=100, recovery_codes=[])

    def accept(conn):
        with conn:
            return two_step.accept_step(conn, user_id=1, step=101)

    results, _ = _race(db_path, accept, count=4)
    assert results.count(True) == 1


# ---------- Clean shutdown with the real connection pool ----------


@pytest.mark.postgres_only
def test_a_process_with_an_open_connection_pool_exits_promptly(db_path):
    """Final-audit regression: the psycopg_pool pool was never closed, so
    every process (each Gunicorn worker) waited about 20 seconds at exit for
    the pool's background threads. Needs the genuine psycopg_pool."""
    pytest.importorskip("psycopg_pool", reason="needs the genuine psycopg_pool (CI postgres job)")
    if db._pg_driver_override is not None:
        pytest.skip("a test-only driver is registered; this test is about the genuine pool")
    script = textwrap.dedent(
        f"""
        import sys, time
        sys.path.insert(0, {str(ROOT)!r})
        from tradeledger import create_app
        app = create_app("production", overrides={{
            "SECRET_KEY": {STRONG_KEY!r}, "DATABASE": None, "DATABASE_URL": {db_path!r}}})
        assert app.test_client().get("/health/ready", headers={{"Host": "localhost"}}).status_code == 200
        print("POOL", type(app.extensions["tradeledger_db"]._pool).__name__, flush=True)
        """
    )
    env = {k: v for k, v in os.environ.items() if k != "TRADELEDGER_PG_DRIVER"}
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=60
    )
    elapsed = time.monotonic() - started
    assert result.returncode == 0, result.stderr[-2000:]
    assert "POOL ConnectionPool" in result.stdout
    assert "couldn't stop thread" not in result.stderr
    assert elapsed < 5, f"exit took {elapsed:.1f} s"
