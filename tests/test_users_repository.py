"""Phase 8.5: edge cases of the users repository found by branch coverage.

Plain English: small rules about email addresses and account look-ups
that the bigger journey tests never happened to reach. Each one matters
because sign-in and the two-step page pass untrusted values into these
functions.
"""

import pytest

from tradeledger.db import get_db
from tradeledger.repositories.users import (
    MAX_EMAIL_LENGTH,
    create_user,
    email_problem,
    get_user,
    get_user_by_email,
)
from tradeledger.validation import ValidationError


@pytest.mark.parametrize(
    "value, problem",
    [
        ("", "is required."),
        (None, "is required."),
        ("   ", "is required."),
        ("a" * MAX_EMAIL_LENGTH + "@example.com", f"must be {MAX_EMAIL_LENGTH} characters or fewer."),
        ("no-at-sign.example.com", "must look like name@example.com."),
        ("two@@example.com", "must look like name@example.com."),
        ("trader@example.com", None),
        ("  Trader@Example.COM ", None),
    ],
)
def test_email_rules(value, problem):
    assert email_problem(value) == problem


def test_create_user_refuses_an_invalid_email_before_touching_the_database(app):
    with app.app_context():
        conn = get_db()
        before = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        with pytest.raises(ValidationError) as caught:
            create_user(conn, "not an email", "$argon2id$v=19$m=1024,t=1,p=1$c2FsdA$aGFzaA")
        assert caught.value.errors == {"email": "must look like name@example.com."}
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == before


@pytest.mark.parametrize("user_id", ["1", None, True, 1.0, [1], {"id": 1}])
def test_get_user_ignores_anything_that_is_not_an_integer_id(app, user_id):
    """A tampered cookie value (for example a string uid in the pending
    two-step note) must find nobody rather than raise or match row 1."""
    with app.app_context():
        assert get_user(get_db(), user_id) is None


@pytest.mark.parametrize("email", ["", None, "   "])
def test_get_user_by_email_with_no_email_finds_nobody(app, email):
    with app.app_context():
        assert get_user_by_email(get_db(), email) is None


def test_get_user_by_email_is_case_insensitive(app):
    with app.app_context():
        assert get_user_by_email(get_db(), "  TRADER@example.COM ")["id"] == 1


def test_a_tampered_pending_two_step_note_is_refused(anon_client):
    with anon_client.session_transaction() as session:
        session["pending_two_step"] = {"uid": "1", "sv": 1, "at": 2_000_000_000, "next": "/"}
    response = anon_client.get("/login/two-step")
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    with anon_client.session_transaction() as session:
        assert "pending_two_step" not in session
