"""Phase 8.5: one-time password-reset links issued by an operator.

Plain English: TradeLedger cannot email anyone, so the server's operator
prints a link with a command and hands it over. These tests prove the
link works exactly once, expires, cannot be guessed or reused, keeps the
secret out of logs and Referer headers, ends every session and API token,
and never signs the person in by itself.
"""

import logging

import pytest
from click.testing import CliRunner

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user, issue_token
from tests.web_helpers import csrf_token
from tradeledger.cli import create_reset_link_command, set_password_command
from tradeledger.db import get_db
from tradeledger.passwords import verify_password
from tradeledger.repositories import password_resets
from tradeledger.repositories.users import get_user, get_user_by_email
from tradeledger.web import auth

NEW_PASSWORD = "harbour lantern copper meadow"


def make_link(app, email=TEST_EMAIL, base_url="http://localhost"):
    result = CliRunner().invoke(
        create_reset_link_command, ["--email", email, "--base-url", base_url], obj=app_info(app)
    )
    assert result.exit_code == 0, result.output
    return result.output.strip().splitlines()[-1]


def app_info(app):
    from flask.cli import ScriptInfo

    return ScriptInfo(create_app=lambda: app)


def token_of(link):
    return link.split("token=", 1)[1]


def open_link(client, link):
    return client.get(link.replace("http://localhost", ""))


def submit(client, password=NEW_PASSWORD, confirm=None):
    return client.post(
        "/reset-password",
        data={
            "csrf_token": csrf_token(client, "/reset-password"),
            "new_password": password,
            "confirm_password": password if confirm is None else confirm,
        },
    )


def stored_hash(app, email=TEST_EMAIL):
    with app.app_context():
        return get_user_by_email(get_db(), email)["password_hash"]


# ---------- The command ----------


def test_the_command_prints_a_link_and_stores_only_a_hash(app):
    link = make_link(app)
    assert link.startswith("http://localhost/reset-password?token=tlr_")
    token = token_of(link)
    with app.app_context():
        rows = get_db().execute("SELECT * FROM password_resets").fetchall()
    assert len(rows) == 1 and rows[0]["token_hash"] == password_resets.token_hash(token)
    assert token not in str(dict(rows[0]))
    assert rows[0]["expires_at"] - rows[0]["created_at"] == 30 * 60


def test_a_new_link_cancels_the_previous_one(app, anon_client):
    first = make_link(app)
    second = make_link(app)
    assert open_link(anon_client, first).status_code == 400
    assert open_link(app.test_client(), second).status_code == 303


@pytest.mark.parametrize("base_url", ["http://journal.example.com", "ftp://x", "javascript:alert(1)"])
def test_the_command_refuses_insecure_base_urls(app, base_url):
    result = CliRunner().invoke(
        create_reset_link_command, ["--email", TEST_EMAIL, "--base-url", base_url], obj=app_info(app)
    )
    assert result.exit_code != 0 and "https://" in result.output


def test_the_command_refuses_unknown_and_deactivated_accounts(app):
    from tradeledger.repositories.users import set_active

    unknown = CliRunner().invoke(
        create_reset_link_command, ["--email", "nobody@example.com"], obj=app_info(app)
    )
    assert unknown.exit_code != 0
    with app.app_context():
        set_active(get_db(), 1, False)
    inactive = CliRunner().invoke(create_reset_link_command, ["--email", TEST_EMAIL], obj=app_info(app))
    assert inactive.exit_code != 0 and "deactivated" in inactive.output


# ---------- Using the link ----------


def test_opening_the_link_moves_the_token_out_of_the_address_bar(app, anon_client):
    response = open_link(anon_client, make_link(app))
    assert response.status_code == 303 and response.headers["Location"] == "/reset-password"
    page = anon_client.get("/reset-password")
    html = page.get_data(as_text=True)
    assert page.status_code == 200 and "Choose a new password" in html and "tlr_" not in html
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert page.headers["Cache-Control"] == "no-store"


def test_a_good_link_changes_the_password_once_and_does_not_sign_in(app, anon_client):
    old_hash = stored_hash(app)
    link = make_link(app)
    open_link(anon_client, link)
    response = submit(anon_client)
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    assert stored_hash(app) != old_hash
    assert verify_password(NEW_PASSWORD, stored_hash(app))
    assert anon_client.get("/").status_code == 302  # not signed in automatically
    again = app.test_client()
    assert open_link(again, link).status_code == 400  # used up


def test_after_a_reset_the_new_password_works_and_the_old_one_does_not(app, anon_client):
    open_link(anon_client, make_link(app))
    submit(anon_client)
    from tests.test_device_sessions import login

    assert login(app.test_client(), password=TEST_PASSWORD).status_code == 200  # form shown again: refused
    assert login(app.test_client(), password=NEW_PASSWORD).status_code == 303


def test_a_reset_ends_every_session_and_revokes_every_token(app, client):
    token = issue_token(app)
    assert client.get("/api/v1/trades", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    visitor = app.test_client()
    open_link(visitor, make_link(app))
    submit(visitor)
    assert client.get("/").status_code == 302
    assert client.get("/api/v1/trades", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_password_rules_still_apply(app, anon_client):
    open_link(anon_client, make_link(app))
    short = submit(anon_client, password="too short")
    assert short.status_code == 400 and "at least 12 characters" in short.get_data(as_text=True)
    mismatch = submit(anon_client, confirm="something else entirely")
    assert mismatch.status_code == 400 and "does not match" in mismatch.get_data(as_text=True)
    own_email = submit(anon_client, password=TEST_EMAIL)
    assert own_email.status_code == 400
    assert submit(anon_client).status_code == 303  # the link survived the mistakes


def test_an_expired_link_is_refused(app, anon_client, monkeypatch):
    link = make_link(app)
    later = auth.now_seconds() + 30 * 60 + 1
    monkeypatch.setattr(auth, "now_seconds", lambda: later)
    monkeypatch.setattr("tradeledger.web.reset.now_seconds", lambda: later)
    response = open_link(anon_client, link)
    assert (
        response.status_code == 400
        and "invalid, has already been used or has expired" in response.get_data(as_text=True)
    )


def test_a_link_that_expires_while_the_form_is_open_is_refused(app, anon_client, monkeypatch):
    open_link(anon_client, make_link(app))
    token = csrf_token(anon_client, "/reset-password")
    later = auth.now_seconds() + 31 * 60
    monkeypatch.setattr("tradeledger.web.reset.now_seconds", lambda: later)
    response = anon_client.post(
        "/reset-password",
        data={"csrf_token": token, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )
    assert response.status_code == 400
    assert verify_password(TEST_PASSWORD, stored_hash(app))


@pytest.mark.parametrize(
    "token",
    ["", "tlr_short", "tl_" + "a" * 43, "tlr_" + "a" * 43, "../../etc/passwd", "tlr_" + "%00" * 15],
)
def test_wrong_tokens_all_get_the_same_answer(app, anon_client, token):
    make_link(app)
    response = anon_client.get("/reset-password", query_string={"token": token})
    assert response.status_code == 400
    assert password_resets.PREFIX + "a" not in response.get_data(as_text=True)
    assert "invalid, has already been used or has expired" in response.get_data(as_text=True)


def test_the_form_without_a_link_shows_the_same_message(anon_client):
    page = anon_client.get("/reset-password")
    assert "invalid, has already been used or has expired" in page.get_data(as_text=True)
    assert "new_password" not in page.get_data(as_text=True)


def test_a_link_for_one_account_cannot_change_another(app, anon_client):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    other_before = stored_hash(app, OTHER_EMAIL)
    open_link(anon_client, make_link(app, TEST_EMAIL))
    anon_client.post(
        "/reset-password",
        data={
            "csrf_token": csrf_token(anon_client, "/reset-password"),
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
            "email": OTHER_EMAIL,
            "user_id": "2",
        },
    )
    assert stored_hash(app, OTHER_EMAIL) == other_before
    assert verify_password(NEW_PASSWORD, stored_hash(app, TEST_EMAIL))


def test_the_form_needs_a_csrf_token(app, anon_client):
    open_link(anon_client, make_link(app))
    response = anon_client.post(
        "/reset-password", data={"new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD}
    )
    assert response.status_code == 400
    assert verify_password(TEST_PASSWORD, stored_hash(app))


def test_repeated_bad_links_are_throttled(app, anon_client):
    for _ in range(app.config["LOGIN_MAX_FAILURES_PER_IP"]):
        anon_client.get("/reset-password", query_string={"token": "tlr_" + "x" * 43})
    blocked = anon_client.get("/reset-password", query_string={"token": "tlr_" + "y" * 43})
    assert blocked.status_code == 429


def test_the_token_never_reaches_the_logs(app, anon_client, caplog):
    caplog.set_level(logging.DEBUG)
    link = make_link(app)
    open_link(anon_client, link)
    submit(anon_client)
    open_link(app.test_client(), link)
    for record in caplog.records:
        assert token_of(link) not in record.getMessage()
        assert token_of(link) not in str(record.__dict__)


def test_reset_links_are_deleted_with_their_account(app):
    """ON DELETE CASCADE: a link never outlives its account."""
    make_link(app)
    with app.app_context():
        conn = get_db()
        with conn:
            conn.execute("DELETE FROM users WHERE id = 1")  # the test account has no trades here
        assert get_user(conn, 1) is None
        assert conn.execute("SELECT COUNT(*) FROM password_resets").fetchone()[0] == 0


# ---------- set-password (operator chooses the password) ----------


def test_set_password_also_revokes_api_tokens(app):
    """Phase 8.5 fix: the Phase 8 guide said set-password ended sessions
    AND tokens, but the command only ended sessions. A forgotten or leaked
    password should cut off scripts as well."""
    token = issue_token(app)
    result = CliRunner().invoke(
        set_password_command,
        ["--email", TEST_EMAIL, "--password-stdin"],
        input=NEW_PASSWORD + "\n",
        obj=app_info(app),
    )
    assert result.exit_code == 0, result.output
    assert "1 API token(s) revoked" in result.output
    client = app.test_client()
    assert client.get("/api/v1/trades", headers={"Authorization": f"Bearer {token}"}).status_code == 401


# ---------- Edge cases found by branch coverage (Phase 8.5) ----------


def test_a_link_for_an_account_deactivated_later_is_refused(app, anon_client):
    from tradeledger.repositories.users import set_active

    link = make_link(app)
    open_link(anon_client, link)
    with app.app_context():
        set_active(get_db(), 1, False)
    page = anon_client.get("/reset-password")
    assert page.status_code == 400 and "invalid, has already been used or has expired" in page.get_data(
        as_text=True
    )


def test_two_tabs_with_the_same_link_change_the_password_once(app):
    """Both tabs show the form; the first to submit wins, the second is
    refused and does not overwrite the first password."""
    link = make_link(app)
    first, second = app.test_client(), app.test_client()
    open_link(first, link)
    open_link(second, link)
    second_token = csrf_token(second, "/reset-password")
    assert submit(first).status_code == 303
    late = second.post(
        "/reset-password",
        data={
            "csrf_token": second_token,
            "new_password": "second tab password!",
            "confirm_password": "second tab password!",
        },
    )
    assert late.status_code == 400
    assert verify_password(NEW_PASSWORD, stored_hash(app))


def test_the_development_server_access_line_hides_the_reset_token(app, caplog):
    """Phase 8.5 regression, found by the production-settings HTTP check:
    `flask run` logs every request line with its query string through the
    "werkzeug" logger, which bypassed the redaction filter."""
    import logging as stdlib_logging

    token = "tlr_" + "Q" * 43
    caplog.set_level(stdlib_logging.INFO, logger="werkzeug")
    stdlib_logging.getLogger("werkzeug").info(
        '%s - - [16/Nov/2026 04:18:20] "%s" %s -',
        "127.0.0.1",
        f"GET /reset-password?token={token} HTTP/1.1",
        "303",
    )
    messages = [record.getMessage() for record in caplog.records if record.name == "werkzeug"]
    assert messages and all(token not in message for message in messages)
    assert "tlr_***" in messages[0] or "token=***" in messages[0]
