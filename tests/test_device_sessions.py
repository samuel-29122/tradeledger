"""Phase 8.5: one server-side record per signed-in browser.

Plain English: each sign-in now leaves a row in the database. These tests
prove that signing out really ends the session on the server (a copied
cookie stops working), that one browser can be signed out from another,
that nobody can sign out someone else's browser, and that password changes
and "sign out everywhere else" behave as the Account page promises.
Runs on SQLite and PostgreSQL.
"""

import pytest

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user, sign_in
from tests.web_helpers import csrf_token
from tradeledger.db import get_db
from tradeledger.repositories import sessions
from tradeledger.repositories.sessions import describe_user_agent, key_hash
from tradeledger.repositories.users import set_active
from tradeledger.web import auth

FIREFOX = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0"
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1"
)


def login(client, email=TEST_EMAIL, password=TEST_PASSWORD, user_agent=FIREFOX):
    data = {"csrf_token": csrf_token(client, "/login"), "email": email, "password": password}
    return client.post("/login", data=data, headers={"User-Agent": user_agent})


def cookie_value(client, app):
    cookie = client.get_cookie(app.config["SESSION_COOKIE_NAME"])
    return None if cookie is None else cookie.value


def session_rows(app, user_id=1):
    with app.app_context():
        return [
            dict(r)
            for r in get_db().execute("SELECT * FROM user_sessions WHERE user_id = ? ORDER BY id", (user_id,))
        ]


def post(client, path, form_page="/account"):
    return client.post(path, data={"csrf_token": csrf_token(client, form_page)})


# ---------- What is stored ----------


def test_signing_in_creates_one_row_holding_only_a_hash(app, anon_client):
    assert login(anon_client).status_code == 303
    rows = session_rows(app)
    assert len(rows) == 1
    row = rows[0]
    with anon_client.session_transaction() as cookie:
        key = cookie["sid"]
    assert row["key_hash"] == key_hash(key) and key not in str(row)
    assert row["user_agent"] == "Firefox on Windows"
    assert row["expires_at"] - row["created_at"] == app.config["SESSION_MAX_AGE_SECONDS"]
    assert row["revoked_at"] is None


def test_no_ip_address_or_full_user_agent_is_stored(app, anon_client):
    login(anon_client, user_agent=SAFARI_IPHONE)
    row = session_rows(app)[0]
    assert set(row) == {
        "id",
        "user_id",
        "key_hash",
        "created_at",
        "last_seen_at",
        "expires_at",
        "user_agent",
        "revoked_at",
    }
    assert row["user_agent"] == "Safari on iOS"


@pytest.mark.parametrize(
    "header, label",
    [
        (FIREFOX, "Firefox on Windows"),
        (SAFARI_IPHONE, "Safari on iOS"),
        (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
            "Chrome on macOS",
        ),
        (
            "Mozilla/5.0 (Linux; Android 15) AppleWebKit/537.36 Chrome/131.0 Mobile Safari/537.36",
            "Chrome on Android",
        ),
        (
            "Mozilla/5.0 (Windows NT 10.0) AppleWebKit/537.36 Chrome/131.0 Safari/537.36 Edg/131.0",
            "Edge on Windows",
        ),
        ("curl/8.5.0", "Script on unknown system"),
        ("", "Unknown browser on unknown system"),
        ("<script>alert(1)</script>", "Unknown browser on unknown system"),
    ],
)
def test_user_agent_is_reduced_to_a_short_label(header, label):
    assert describe_user_agent(header) == label


# ---------- Signing out really ends the session ----------


def test_a_cookie_copied_before_sign_out_is_refused_afterwards(app, anon_client):
    """The Phase 8 weakness this phase fixes: a signed cookie stayed valid
    after sign-out. Now the server's row is crossed out."""
    login(anon_client)
    stolen = cookie_value(anon_client, app)
    assert post(anon_client, "/logout", "/").status_code == 303
    thief = app.test_client()
    thief.set_cookie(app.config["SESSION_COOKIE_NAME"], stolen)
    response = thief.get("/trades")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    assert session_rows(app)[0]["revoked_at"] is not None


def test_a_cookie_without_a_server_session_is_refused(app, anon_client):
    """A correctly signed cookie (as Phase 8 issued) with no session key is
    no longer enough on its own."""
    now = auth.now_seconds()
    with anon_client.session_transaction() as session:
        session.update({"uid": 1, "sv": 1, "auth_at": now, "seen_at": now})
    assert anon_client.get("/").status_code == 302


def test_a_session_key_only_works_for_its_own_account(app, anon_client):
    with app.app_context():
        other = ensure_user(get_db(), OTHER_EMAIL)
    login(anon_client)  # a real session for user 1
    with anon_client.session_transaction() as session:
        session["uid"] = other  # a server bug or a forged cookie pairing user 1's key with user 2
        session["sv"] = 1
    assert anon_client.get("/").status_code == 302


def test_the_server_enforces_the_absolute_limit_even_if_the_cookie_is_edited(app, client, monkeypatch):
    later = auth.now_seconds() + app.config["SESSION_MAX_AGE_SECONDS"] + 5
    monkeypatch.setattr(auth, "now_seconds", lambda: later)
    with client.session_transaction() as session:
        session["auth_at"] = later - 10
        session["seen_at"] = later - 10
    assert client.get("/").status_code == 302


# ---------- Signing out one browser from another ----------


def test_one_browser_can_sign_out_another(app):
    laptop, phone = app.test_client(), app.test_client()
    login(laptop, user_agent=FIREFOX)
    login(phone, user_agent=SAFARI_IPHONE)
    rows = session_rows(app)
    assert len(rows) == 2
    page = phone.get("/account").get_data(as_text=True)
    assert "Firefox on Windows" in page and "Safari on iOS" in page and "This browser" in page
    laptop_id = next(r["id"] for r in rows if r["user_agent"] == "Firefox on Windows")
    response = post(phone, f"/account/sessions/{laptop_id}/revoke")
    assert response.status_code == 303 and response.headers["Location"] == "/account"
    assert laptop.get("/").status_code == 302  # the laptop is signed out
    assert phone.get("/").status_code == 200  # the phone is not


def test_signing_out_this_browser_from_the_list_signs_it_out(app, anon_client):
    login(anon_client)
    own = session_rows(app)[0]["id"]
    response = post(anon_client, f"/account/sessions/{own}/revoke")
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    assert anon_client.get("/").status_code == 302


def test_nobody_can_sign_out_another_users_browser(app):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    victim, attacker = app.test_client(), app.test_client()
    login(victim)
    login(attacker, email=OTHER_EMAIL)
    victim_session = session_rows(app, 1)[0]["id"]
    response = post(attacker, f"/account/sessions/{victim_session}/revoke")
    assert response.status_code == 404
    missing = post(attacker, "/account/sessions/999999/revoke")
    assert missing.status_code == 404 and missing.get_data() == response.get_data()
    assert victim.get("/").status_code == 200


def test_the_account_page_lists_only_your_own_browsers_and_never_a_key(app):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    mine, theirs = app.test_client(), app.test_client()
    login(mine, user_agent=FIREFOX)
    login(theirs, email=OTHER_EMAIL, user_agent=SAFARI_IPHONE)
    page = mine.get("/account").get_data(as_text=True)
    assert "Firefox on Windows" in page and "Safari on iOS" not in page
    with mine.session_transaction() as session:
        assert session["sid"] not in page
    for row in session_rows(app, 1):
        assert row["key_hash"] not in page


def test_revoking_needs_post_and_a_csrf_token(app, client):
    own = session_rows(app)[0]["id"]
    assert client.get(f"/account/sessions/{own}/revoke").status_code == 405
    assert client.post(f"/account/sessions/{own}/revoke").status_code == 400
    assert client.get("/").status_code == 200


# ---------- Password changes and "sign out everywhere else" ----------


def test_changing_the_password_signs_out_other_browsers_but_not_this_one(app):
    here, there = app.test_client(), app.test_client()
    login(here)
    login(there)
    response = here.post(
        "/account/password",
        data={
            "csrf_token": csrf_token(here, "/account"),
            "current_password": TEST_PASSWORD,
            "new_password": "a brand new passphrase",
            "confirm_password": "a brand new passphrase",
        },
    )
    assert response.status_code == 303
    assert here.get("/").status_code == 200
    assert there.get("/").status_code == 302
    active = [r for r in session_rows(app) if r["revoked_at"] is None]
    assert len(active) == 1  # only this browser's NEW session


def test_sign_out_everywhere_else_keeps_only_this_browser(app):
    here, there, elsewhere = app.test_client(), app.test_client(), app.test_client()
    for c in (here, there, elsewhere):
        login(c)
    assert post(here, "/account/sessions/end-others").status_code == 303
    assert here.get("/").status_code == 200
    assert there.get("/").status_code == 302 and elsewhere.get("/").status_code == 302
    assert len([r for r in session_rows(app) if r["revoked_at"] is None]) == 1


def test_deactivating_an_account_revokes_its_sessions(app, client):
    with app.app_context():
        set_active(get_db(), 1, False)
    assert all(r["revoked_at"] is not None for r in session_rows(app))
    assert client.get("/").status_code == 302


def test_old_sessions_are_cleaned_up_at_the_next_sign_in(app, anon_client):
    with app.app_context():
        conn = get_db()
        long_ago = auth.now_seconds() - 3 * 24 * 3600
        sessions.create_session(conn, user_id=1, now=long_ago, max_age_seconds=60)
    assert len(session_rows(app)) == 1
    login(anon_client)
    rows = session_rows(app)
    assert len(rows) == 1 and rows[0]["created_at"] > long_ago


def test_last_seen_is_written_at_most_once_a_minute(app, client, monkeypatch):
    start = auth.now_seconds()
    first = session_rows(app)[0]["last_seen_at"]
    monkeypatch.setattr(auth, "now_seconds", lambda: start + 30)
    client.get("/")
    assert session_rows(app)[0]["last_seen_at"] == first
    monkeypatch.setattr(auth, "now_seconds", lambda: start + 90)
    client.get("/")
    assert session_rows(app)[0]["last_seen_at"] == start + 90


def test_sign_in_helper_matches_the_real_form(app, anon_client):
    """Guard for the test helper itself: sign_in() must create exactly the
    kind of row the real form creates, or other tests would prove nothing."""
    sign_in(anon_client)
    assert anon_client.get("/").status_code == 200
    assert len(session_rows(app)) == 1


# ---------- Each layer holds on its own (defence in depth) ----------
# Phase 8.5 mutation testing showed that, with the new server-side rows,
# the older cookie checks were no longer tested independently: removing
# them changed no test result. These tests keep the server row VALID and
# break only the cookie-side condition, so each layer must work alone.


def test_the_session_version_check_works_even_if_the_row_is_still_valid(app, client):
    with app.app_context():
        conn = get_db()
        with conn:  # a version bump that (hypothetically) forgot to revoke rows
            conn.execute("UPDATE users SET session_version = session_version + 1 WHERE id = 1")
        assert conn.execute("SELECT COUNT(*) FROM user_sessions WHERE revoked_at IS NULL").fetchone()[0] >= 1
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")


def test_the_cookie_age_check_works_even_if_the_row_was_extended(app, client, monkeypatch):
    later = auth.now_seconds() + app.config["SESSION_MAX_AGE_SECONDS"] + 5
    with app.app_context():
        conn = get_db()
        with conn:  # a row whose expiry and activity were (wrongly) pushed forward
            conn.execute("UPDATE user_sessions SET expires_at = ?, last_seen_at = ?", (later + 3600, later))
    monkeypatch.setattr(auth, "now_seconds", lambda: later)
    with client.session_transaction() as session:
        session["seen_at"] = later
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
