"""Phase 8: signing in, signing out, registration, sessions and throttling.

These tests drive the real forms (GET the page for its CSRF token, POST
the form) with anonymous test clients, so every check runs exactly as it
would for a browser.
"""

import logging

import pytest

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user, sign_in
from tests.web_helpers import count_rows, csrf_token
from tradeledger import create_app
from tradeledger.db import get_db, init_schema
from tradeledger.passwords import OWASP_MINIMUM, stored_parameters
from tradeledger.repositories.users import bump_session_version, get_user, get_user_by_email, set_active
from tradeledger.web import auth

STRONG_KEY = "f00d" * 16


def login(client, email=TEST_EMAIL, password=TEST_PASSWORD, next_path=None, **extra):
    data = {"csrf_token": csrf_token(client, "/login"), "email": email, "password": password, **extra}
    if next_path is not None:
        data["next"] = next_path
    return client.post("/login", data=data)


def session_of(client):
    with client.session_transaction() as session:
        return dict(session)


# ---------- Every private page needs a signed-in user ----------


def test_every_route_is_private_unless_deliberately_public(app, anon_client):
    """Default deny: walk EVERY registered website URL. Anything not in
    PUBLIC_ENDPOINTS must redirect an anonymous visitor to sign-in, so a
    new route added later cannot be public by accident."""
    checked = 0
    for rule in app.url_map.iter_rules():
        if not rule.endpoint.startswith("main."):
            continue
        url = rule.build(dict.fromkeys(rule.arguments, 1))[1]
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            response = anon_client.open(url, method=method)
            if rule.endpoint == "main.login_two_step":
                # Phase 8.5: public, but useless without a pending sign-in
                # (a correct password first), so it sends visitors back to /login.
                assert response.status_code in (303, 400), (method, url)
                if response.status_code == 303:
                    assert response.headers["Location"] == "/login", (method, url)
            elif rule.endpoint in auth.PUBLIC_ENDPOINTS:
                # 200 page; 400 a POST without a CSRF token (or a reset form
                # without a valid link); 404 registration switched off
                assert response.status_code in (200, 400, 404), (method, url)
            else:
                assert response.status_code in (302, 303), (method, url, response.status_code)
                assert response.headers["Location"].startswith("/login"), (method, url)
            checked += 1
    assert checked >= 25


def test_public_endpoints_are_the_expected_small_set():
    # Phase 8.5 added the two-step sign-in page and the reset-link page.
    # Both are useless without a secret: a pending sign-in after a correct
    # password, or a one-time link (tests/test_two_step.py, test_password_reset.py).
    assert {
        "main.login",
        "main.login_two_step",
        "main.reset_password",
        "main.register",
        "main.health",
        "main.health_live",
        "main.health_ready",
        "main.about",
    } == auth.PUBLIC_ENDPOINTS


def test_redirect_to_sign_in_remembers_the_page(anon_client):
    response = anon_client.get("/trades?status=OPEN")
    assert response.headers["Location"] == "/login?next=/trades?status%3DOPEN"


# ---------- Sign in ----------


def test_successful_sign_in(app, anon_client):
    response = login(anon_client)
    assert response.status_code == 303
    assert response.headers["Location"] == "/"
    session = session_of(anon_client)
    assert session["uid"] == 1 and session["sv"] == 1
    assert anon_client.get("/").status_code == 200
    with app.app_context():
        assert get_user(get_db(), 1)["last_login_at"] is not None


def test_email_is_case_and_space_insensitive(anon_client):
    assert login(anon_client, email="  Trader@EXAMPLE.com ").status_code == 303


@pytest.mark.parametrize(
    "email, password",
    [
        (TEST_EMAIL, "wrong password entirely"),  # right account, wrong password
        ("nobody@example.com", TEST_PASSWORD),  # unknown account
        ("", ""),  # empty form
        (TEST_EMAIL, ""),
    ],
)
def test_failed_sign_in_gives_one_generic_message(anon_client, email, password):
    response = login(anon_client, email=email, password=password)
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Email or password is incorrect." in html
    for disclosure in ("not registered", "no account", "unknown", "wrong password"):
        assert disclosure not in html.lower()
    assert "uid" not in session_of(anon_client)


def test_disabled_account_gets_the_same_message(app, anon_client):
    with app.app_context():
        set_active(get_db(), 1, False)
    response = login(anon_client)
    assert "Email or password is incorrect." in response.get_data(as_text=True)
    assert "uid" not in session_of(anon_client)


def test_password_is_never_echoed_or_logged(anon_client, caplog):
    with caplog.at_level(logging.DEBUG):
        response = login(anon_client, password="Secret-Typed-Password-77")
    assert b"Secret-Typed-Password-77" not in response.data
    assert "Secret-Typed-Password-77" not in caplog.text
    assert TEST_EMAIL not in caplog.text  # failures log a keyed digest, not the address


def test_sign_in_without_csrf_token_is_refused(anon_client):
    response = anon_client.post("/login", data={"email": TEST_EMAIL, "password": TEST_PASSWORD})
    assert response.status_code == 400
    assert "uid" not in session_of(anon_client)


def test_sign_in_with_another_browsers_token_is_refused(app, anon_client):
    other_token = csrf_token(app.test_client(), "/login")
    csrf_token(anon_client, "/login")  # this browser has its own token
    response = anon_client.post(
        "/login", data={"csrf_token": other_token, "email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 400


def test_session_is_replaced_at_sign_in(anon_client):
    """Session fixation: nothing planted before sign-in survives it,
    including the CSRF token."""
    with anon_client.session_transaction() as session:
        session["planted"] = "by an attacker"
    before = csrf_token(anon_client, "/login")
    login(anon_client)
    session = session_of(anon_client)
    assert "planted" not in session
    assert session.get("_csrf_token") != before


def test_signed_in_user_visiting_login_goes_home(client):
    response = client.get("/login")
    assert response.status_code == 302 and response.headers["Location"] == "/"


def test_old_password_hash_is_upgraded_after_sign_in(app, anon_client):
    """Hashes created with weaker settings are re-hashed with the current
    ones the next time the user signs in (here: test -> OWASP settings)."""
    app.config["PASSWORD_HASH_MEMORY_KIB"] = OWASP_MINIMUM.memory_kib
    app.config["PASSWORD_HASH_ITERATIONS"] = OWASP_MINIMUM.iterations
    login(anon_client)
    with app.app_context():
        stored = get_user(get_db(), 1)["password_hash"]
    assert stored_parameters(stored) == OWASP_MINIMUM


# ---------- Safe redirects after sign-in ----------


@pytest.mark.parametrize(
    "target, expected",
    [
        ("/trades?status=OPEN", "/trades?status=OPEN"),
        ("/analytics", "/analytics"),
        ("https://evil.example/phish", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("\\\\evil.example", "/"),
        ("/\tevil", "/"),
        ("javascript:alert(1)", "/"),
        ("", "/"),
    ],
)
def test_only_local_paths_are_followed_after_sign_in(anon_client, target, expected):
    response = login(anon_client, next_path=target)
    assert response.headers["Location"] == expected


# ---------- Throttling ----------


def test_account_is_throttled_after_five_failures(app, anon_client):
    for _ in range(5):
        login(anon_client, password="wrong password guess")
    blocked = login(anon_client)  # even the RIGHT password is refused now
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == str(app.config["LOGIN_WINDOW_SECONDS"])
    assert "Too many sign-in attempts" in blocked.get_data(as_text=True)
    assert "uid" not in session_of(anon_client)


def test_throttle_is_per_account(app, anon_client):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    for _ in range(5):
        login(anon_client, password="wrong password guess")
    assert login(app.test_client(), email=OTHER_EMAIL).status_code == 303


def test_ip_is_throttled_across_accounts(app):
    app.config["LOGIN_MAX_FAILURES_PER_IP"] = 3
    client = app.test_client()
    for n in range(3):
        login(client, email=f"guess{n}@example.com", password="wrong password guess")
    assert login(client).status_code == 429  # a different (real) account, same IP


def test_failures_expire_after_the_window(app, anon_client, monkeypatch):
    from datetime import timedelta

    from tradeledger.repositories import throttle

    for _ in range(5):
        login(anon_client, password="wrong password guess")
    assert login(anon_client).status_code == 429
    real_now = throttle.utc_now
    monkeypatch.setattr(
        throttle, "utc_now", lambda: real_now() + timedelta(seconds=app.config["LOGIN_WINDOW_SECONDS"] + 1)
    )
    assert login(anon_client).status_code == 303


def test_successful_sign_in_clears_the_account_counter(app, anon_client):
    for _ in range(4):
        login(anon_client, password="wrong password guess")
    assert login(anon_client).status_code == 303
    other = app.test_client()
    for _ in range(4):
        login(other, password="wrong password guess")
    assert login(other).status_code == 303  # 4 + 4 failures never reached 5 in a row


def test_throttle_table_stores_no_raw_emails_or_addresses(app, anon_client):
    login(anon_client, email="victim@example.com", password="wrong password guess")
    with app.app_context():
        rows = [dict(r) for r in get_db().execute("SELECT scope, key_digest FROM login_attempts")]
    assert {r["scope"] for r in rows} == {"login-email", "login-ip"}
    for row in rows:
        assert "victim" not in row["key_digest"] and "127.0.0.1" not in row["key_digest"]
        assert len(row["key_digest"]) == 64


# ---------- Sign out ----------


def test_sign_out_needs_post(client):
    assert client.get("/logout").status_code == 405
    assert client.get("/").status_code == 200  # still signed in


def test_sign_out_needs_a_csrf_token(client):
    assert client.post("/logout", data={}).status_code == 400
    assert client.get("/").status_code == 200


def test_sign_out_ends_the_session(client):
    token = csrf_token(client, "/")
    response = client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    assert "uid" not in session_of(client)
    assert client.get("/trades").status_code == 302


# ---------- Session lifetime and invalidation ----------


def test_absolute_session_lifetime(app, client, monkeypatch):
    # Phase 8 simulated "recently active" by editing seen_at inside the
    # cookie. Since Phase 8.5 activity is recorded on the SERVER (the
    # user_sessions row), so a forged cookie value no longer counts as
    # activity; the test now stays active through real requests instead.
    start = auth.now_seconds()
    max_age = app.config["SESSION_MAX_AGE_SECONDS"]
    step = app.config["SESSION_IDLE_TIMEOUT_SECONDS"] - 60
    moment = start
    while moment + step < max_age + start - 120:
        moment += step
        monkeypatch.setattr(auth, "now_seconds", lambda m=moment: m)
        assert client.get("/").status_code == 200
    monkeypatch.setattr(auth, "now_seconds", lambda: start + max_age - 120)
    assert client.get("/").status_code == 200  # active all along, still inside 8 hours
    monkeypatch.setattr(auth, "now_seconds", lambda: start + max_age + 1)
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    page = client.get(response.headers["Location"])
    assert "Your session has ended" in page.get_data(as_text=True)


def test_editing_activity_inside_the_cookie_does_not_extend_a_session(app, client, monkeypatch):
    """Phase 8.5: the idle limit is checked against the server's record, so
    a client that rewrites seen_at in its own cookie gains nothing."""
    start = auth.now_seconds()
    later = start + app.config["SESSION_IDLE_TIMEOUT_SECONDS"] + 1
    monkeypatch.setattr(auth, "now_seconds", lambda: later)
    with client.session_transaction() as session:
        session["seen_at"] = later - 5
    assert client.get("/").status_code == 302


def test_idle_timeout(app, client, monkeypatch):
    start = auth.now_seconds()
    monkeypatch.setattr(auth, "now_seconds", lambda: start + app.config["SESSION_IDLE_TIMEOUT_SECONDS"] + 1)
    assert client.get("/").status_code == 302


def test_activity_keeps_an_idle_session_alive(app, client, monkeypatch):
    start = auth.now_seconds()
    step = app.config["SESSION_IDLE_TIMEOUT_SECONDS"] - 60
    for n in (1, 2, 3):  # three visits, each just inside the idle limit
        monkeypatch.setattr(auth, "now_seconds", lambda n=n: start + n * step)
        assert client.get("/").status_code == 200


def test_raising_the_session_version_ends_every_session(app, client):
    with app.app_context():
        bump_session_version(get_db(), 1)
    assert client.get("/").status_code == 302


def test_a_tampered_cookie_is_ignored(app, anon_client):
    """The cookie is signed: changing uid without the key breaks it."""
    sign_in(anon_client, user_id=1)
    cookie = anon_client.get_cookie(app.config["SESSION_COOKIE_NAME"])
    payload, _, signature = cookie.value.rpartition(".")
    anon_client.set_cookie(app.config["SESSION_COOKIE_NAME"], payload + "." + signature[::-1])
    assert anon_client.get("/").status_code == 302


def test_session_for_a_deleted_user_is_ended(app, anon_client):
    sign_in(anon_client, user_id=999)
    assert anon_client.get("/").status_code == 302
    assert "uid" not in session_of(anon_client)


# ---------- Cookie flags ----------


def production_app(db_path, **overrides):
    app = create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path, **overrides})
    with app.app_context():
        init_schema(get_db())
        ensure_user(get_db())
    return app


def test_production_session_cookie_flags(db_path):
    client = production_app(db_path).test_client()
    token = csrf_token_https(client)
    response = client.post(
        "/login",
        data={"csrf_token": token, "email": TEST_EMAIL, "password": TEST_PASSWORD},
        base_url="https://localhost",
    )
    assert response.status_code == 303
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith("__Host-tradeledger_session=")
    for flag in ("Secure", "HttpOnly", "Path=/", "SameSite=Lax"):
        assert flag in cookie
    assert "Domain=" not in cookie  # required by the __Host- prefix
    assert "Expires=" in cookie  # a permanent session, ending with the 8-hour limit


def csrf_token_https(client):
    import re

    html = client.get("/login", base_url="https://localhost").get_data(as_text=True)
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def test_production_registration_is_off_by_default(db_path):
    client = production_app(db_path).test_client()
    assert client.get("/register", base_url="https://localhost").status_code == 404
    page = client.get("/login", base_url="https://localhost").get_data(as_text=True)
    assert 'href="/register"' not in page


# ---------- Registration ----------


def register(client, email="new.trader@example.com", password="four unrelated words here", confirm=None):
    return client.post(
        "/register",
        data={
            "csrf_token": csrf_token(client, "/register"),
            "email": email,
            "password": password,
            "confirm_password": password if confirm is None else confirm,
        },
    )


def test_registration_creates_a_signed_in_account(app, anon_client):
    response = register(anon_client)
    assert response.status_code == 303 and response.headers["Location"] == "/instruments"
    with app.app_context():
        user = get_user_by_email(get_db(), "new.trader@example.com")
    assert user["password_hash"].startswith("$argon2id$")
    assert "four unrelated" not in user["password_hash"]
    assert session_of(anon_client)["uid"] == user["id"]
    page = anon_client.get("/instruments").get_data(as_text=True)
    assert "EURUSD" not in page  # a new account starts empty


@pytest.mark.parametrize(
    "email, password, confirm, field_message",
    [
        ("", "four unrelated words here", None, "Email address is required."),
        ("not-an-email", "four unrelated words here", None, "Email address must look like name@example.com."),
        ("a@b.co", "short", None, "Password must be at least 12 characters."),
        ("a@b.co", "password1234", None, "Password is too common"),
        ("a@b.co", "four unrelated words here", "different words here", "Confirm password does not match"),
        (TEST_EMAIL, "four unrelated words here", None, "Email address is already registered."),
    ],
)
def test_registration_validation(app, anon_client, email, password, confirm, field_message):
    before = count_rows(app, "users")
    response = register(anon_client, email, password, confirm)
    assert response.status_code == 400
    assert field_message in response.get_data(as_text=True)
    assert count_rows(app, "users") == before
    assert "uid" not in session_of(anon_client)


def test_registration_is_rate_limited_per_ip(app, anon_client):
    for n in range(app.config["REGISTRATIONS_PER_IP_PER_HOUR"]):
        register(anon_client, email=f"bot{n}@example.com")
        anon_client.post("/logout", data={"csrf_token": csrf_token(anon_client, "/")})
    response = register(anon_client, email="one.more@example.com")
    assert response.status_code == 429
    assert count_rows(app, "users") == 1 + app.config["REGISTRATIONS_PER_IP_PER_HOUR"]


def test_oversized_registration_is_rejected(app, anon_client):
    response = anon_client.post("/register", data={"email": "x" * (70 * 1024)})
    assert response.status_code == 413
    assert count_rows(app, "users") == 1


def test_registration_ignores_unexpected_fields(app, anon_client):
    """Extra form fields such as is_active=0 or session_version cannot be
    smuggled into the new account: only email and password are read."""
    anon_client.post(
        "/register",
        data={
            "csrf_token": csrf_token(anon_client, "/register"),
            "email": "smuggle@example.com",
            "password": "four unrelated words here",
            "confirm_password": "four unrelated words here",
            "is_active": "0",
            "session_version": "99",
            "id": "1",
        },
    )
    with app.app_context():
        user = get_user_by_email(get_db(), "smuggle@example.com")
    assert user["id"] == 2 and user["is_active"] and user["session_version"] == 1


def test_password_hash_never_appears_in_pages(app, client):
    with app.app_context():
        stored = get_user(get_db(), 1)["password_hash"]
    for url in ("/", "/account", "/trades", "/about"):
        assert stored.encode() not in client.get(url).data
