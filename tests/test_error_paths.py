"""Phase 7: error paths that coverage measurement showed were never run.

Each test protects a specific failure mode rather than chasing a number:
an error that is silently swallowed, a page that crashes on bad data, or a
command-line message that would mislead. See docs/PHASE_7_QA_REPORT.md
for the coverage baseline that identified them.
"""

import sqlite3

import pytest

from tests.conftest import TEST_EMAIL
from tests.web_helpers import count_rows
from tradeledger import create_app
from tradeledger.db import IntegrityError, get_db
from tradeledger.repositories.trades import _raise_friendly
from tradeledger.validation import ValidationError, validate_trade
from tradeledger.web.filters import clock_filter, day_filter, when_filter

# ---------- Database errors are translated or re-raised, never swallowed ----------


# Phase 8: the repository now receives the adapter's engine-neutral
# db.IntegrityError (with .kind and .constraint) instead of sqlite3's class.


def test_foreign_key_error_becomes_a_field_error():
    with pytest.raises(ValidationError) as info:
        _raise_friendly(IntegrityError("FOREIGN KEY constraint failed", kind="foreign_key"))
    assert info.value.errors == {"instrument_id": "does not match an existing instrument"}


def test_closed_at_trigger_error_becomes_a_field_error():
    with pytest.raises(ValidationError) as info:
        _raise_friendly(
            IntegrityError(
                "closed_at_rules: an OPEN trade cannot have closed_at",
                kind="check",
                constraint="closed_at_rules",
            )
        )
    assert set(info.value.errors) == {"closed_at"}


def test_unknown_database_errors_are_re_raised_unchanged():
    """An unexpected constraint failure must surface (and be logged as a
    500), not be disguised as a user mistake or ignored."""
    error = IntegrityError("CHECK constraint failed: something_new", kind="check", constraint="something_new")
    with pytest.raises(IntegrityError) as info:
        _raise_friendly(error)
    assert info.value is error


# ---------- Validation messages for rarely used inputs ----------


def _valid_trade(**changes):
    data = {
        "instrument_id": 1,
        "direction": "LONG",
        "status": "CLOSED",
        "trade_date": "2026-04-01T09:00:00Z",
        "closed_at": "2026-04-01T10:00:00Z",
        "entry_price": "100",
        "stop_loss": "90",
        "exit_price": "110",
        "position_size": "1",
    }
    data.update(changes)
    return data


@pytest.mark.parametrize(
    "changes, field, message",
    [
        ({"take_profit": "abc"}, "take_profit", "must be a number"),
        ({"exit_price": "-5"}, "exit_price", "must be greater than zero"),
        ({"instrument_id": True}, "instrument_id", None),
        ({"instrument_id": 1.0}, "instrument_id", None),
        ({"notes": {"text": "hi"}}, "notes", "must be text"),
        ({"strategy": 42}, "strategy", "must be text"),
    ],
)
def test_unusual_values_get_a_field_error(changes, field, message):
    with pytest.raises(ValidationError) as info:
        validate_trade(_valid_trade(**changes))
    assert field in info.value.errors
    if message:
        assert info.value.errors[field] == message


# ---------- Pages keep working with data the engine cannot evaluate ----------


@pytest.mark.sqlite_only  # PostgreSQL's NUMERIC CHECKs refuse the out-of-range value outright
def test_trade_page_for_an_unevaluable_trade_shows_no_result(app, seeded_client):
    """A legacy row out of range (see test_security_hardening.py) must not
    break the trade's own page: it renders without a result card."""
    huge = "1" + "0" * 600_000
    with app.app_context():
        conn = get_db()
        conn.execute(
            "UPDATE trades SET entry_price = ?, position_size = ?, take_profit = NULL WHERE id = 1",
            (huge, huge),
        )
        conn.commit()
    response = seeded_client.get("/trades/1")
    assert response.status_code == 200
    assert "(after fees)" not in response.get_data(as_text=True)  # the result card is absent


def test_oversized_website_form_is_413_not_a_crash(seeded_client, app):
    before = count_rows(app, "trades")
    response = seeded_client.post("/trades/new", data={"notes": "x" * (70 * 1024)})
    assert response.status_code == 413
    assert b"Traceback" not in response.data
    assert count_rows(app, "trades") == before


# ---------- Template filters with unexpected stored values ----------


def test_date_filters_never_crash_on_bad_timestamps():
    assert when_filter("not-a-date") == "not-a-date"  # shown as stored, not hidden
    assert when_filter(None) == "n/a"
    assert day_filter("2026-13-45T00:00:00Z") == "n/a"
    assert clock_filter(None) == ""
    assert clock_filter("2026-03-02T08:15:30Z") == "08:15:30 UTC"
    assert clock_filter("2026-03-02T08:15:00Z") == "08:15 UTC"


# ---------- Configuration and command line ----------


def test_config_name_comes_from_the_environment(monkeypatch, db_path):
    monkeypatch.setenv("TRADELEDGER_CONFIG", "testing")
    app = create_app(overrides={"DATABASE": db_path})
    assert app.config["CONFIG_NAME"] == "testing" and app.config["TESTING"] is True


@pytest.mark.sqlite_only
def test_run_sql_on_a_missing_database_explains_what_to_do(tmp_path):
    app = create_app("testing", overrides={"DATABASE": str(tmp_path / "missing.db")})
    result = app.test_cli_runner().invoke(args=["run-sql", "SELECT 1"])
    assert result.exit_code != 0
    assert "Database file not found. Run init-db first." in result.output


def test_run_sql_with_no_rows_says_so(runner):
    result = runner.invoke(args=["run-sql", "SELECT * FROM trades"])
    assert result.exit_code == 0
    assert "(no rows)" in result.output


@pytest.mark.sqlite_only
def test_db_info_on_an_empty_file(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    app = create_app("testing", overrides={"DATABASE": str(empty)})
    result = app.test_cli_runner().invoke(args=["db-info"])
    assert "No tables yet. Run: flask --app tradeledger init-db" in result.output


@pytest.mark.sqlite_only  # PostgreSQL's NUMERIC CHECKs refuse the out-of-range value outright
def test_cli_report_lists_excluded_and_legacy_trades(app, runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    huge = "1" + "0" * 600_000
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 2")
        conn.execute(
            "UPDATE trades SET entry_price = ?, position_size = ?, take_profit = NULL WHERE id = 1",
            (huge, huge),
        )
        conn.commit()
    output = runner.invoke(args=["report", "--email", TEST_EMAIL]).output
    assert "== Excluded trades ==" in output
    assert "#1: values are too large to calculate exactly" in output
    assert "== Closed trades with no recorded exit time (ordered by entry time) ==" in output
    assert "#2" in output.split("no recorded exit time")[1]
