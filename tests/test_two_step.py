"""Phase 8.5: two-step verification (TOTP codes and recovery codes).

Plain English: with two-step switched on, signing in needs the password
AND a 6-digit code from an authenticator app. These tests check the code
maths against the official RFC test values, then the whole journey:
setting it up, signing in, wrong and replayed codes, recovery codes,
throttling, switching it off, and that the secret is stored encrypted.
"""

import hashlib
import re

import pytest
from click.testing import CliRunner
from flask.cli import ScriptInfo

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user, sign_in
from tests.web_helpers import csrf_token
from tradeledger import totp
from tradeledger.cli import disable_two_step_command
from tradeledger.db import get_db
from tradeledger.repositories.users import get_user, public_user
from tradeledger.web import auth, two_step

# ---------- The maths (RFC 6238 Appendix B, SHA-1 test vectors) ----------

RFC_KEY = b"12345678901234567890"


@pytest.mark.parametrize(
    "unix_time, expected",
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_rfc_6238_test_vectors(unix_time, expected):
    assert totp.hotp(RFC_KEY, unix_time // 30, digits=8, digest=hashlib.sha1) == expected


@pytest.mark.parametrize(
    "counter, expected",
    [(0, "755224"), (1, "287082"), (2, "359152"), (3, "969429"), (9, "520489")],
)
def test_rfc_4226_hotp_test_vectors(counter, expected):
    assert totp.hotp(RFC_KEY, counter) == expected


def test_codes_are_accepted_one_step_either_side_and_not_further():
    secret = totp.new_secret()
    now = 1_800_000_000
    for offset in (-30, 0, 30):
        assert totp.verify_code(secret, totp.code_at(secret, now + offset), now=now) is not None
    for offset in (-90, 90):
        code = totp.code_at(secret, now + offset)
        if all(code != totp.code_at(secret, now + d) for d in (-30, 0, 30)):
            assert totp.verify_code(secret, code, now=now) is None


def test_a_code_cannot_be_used_twice():
    secret = totp.new_secret()
    now = 1_800_000_000
    code = totp.code_at(secret, now)
    step = totp.verify_code(secret, code, now=now)
    assert step is not None
    assert totp.verify_code(secret, code, now=now, last_step=step) is None


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 34 5x", None])
def test_malformed_codes_are_refused(code):
    assert totp.verify_code(totp.new_secret(), code, now=1_800_000_000) is None


def test_spaces_in_a_typed_code_are_ignored():
    secret = totp.new_secret()
    code = totp.code_at(secret, 1_800_000_000)
    assert totp.verify_code(secret, f"{code[:3]} {code[3:]}", now=1_800_000_000) is not None


def test_secret_is_160_bits_of_base32():
    secret = totp.new_secret()
    assert len(secret) == 32 and set(secret) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZ234567")
    assert totp.new_secret() != secret


def test_encrypted_secret_is_bound_to_its_key_and_its_user():
    secret = totp.new_secret()
    sealed = totp.encrypt_secret(secret, secret_key="k" * 64, user_id=1)
    assert secret not in sealed and sealed.startswith("v1:")
    assert totp.decrypt_secret(sealed, secret_key="k" * 64, user_id=1) == secret
    assert totp.decrypt_secret(sealed, secret_key="j" * 64, user_id=1) is None  # rotated SECRET_KEY
    assert totp.decrypt_secret(sealed, secret_key="k" * 64, user_id=2) is None  # copied to another user
    assert totp.decrypt_secret("v1:garbage", secret_key="k" * 64, user_id=1) is None
    assert totp.decrypt_secret(None, secret_key="k" * 64, user_id=1) is None


def test_provisioning_uri_follows_the_key_uri_format():
    uri = totp.provisioning_uri("ABCDEFGH", account="trader@example.com", issuer="TradeLedger")
    assert uri == (
        "otpauth://totp/TradeLedger%3Atrader%40example.com?secret=ABCDEFGH&issuer=TradeLedger"
        "&algorithm=SHA1&digits=6&period=30"
    )


def test_recovery_codes_are_random_and_readable():
    codes = totp.new_recovery_codes()
    assert len(codes) == 10 and len(set(codes)) == 10
    for code in codes:
        assert len(code) == 14 and code[4] == code[9] == "-"
        assert totp.looks_like_recovery_code(code) and totp.looks_like_recovery_code(
            code.upper().replace("-", " ")
        )
        assert not set(code) & set("0o1li")


# ---------- Helpers for the web journey ----------


def shown_codes(html):
    return re.findall(r"<li><code>([a-z0-9-]+)</code></li>", html)


def setup_secret(client):
    page = client.get("/account/two-step").get_data(as_text=True)
    assert "Set up two-step verification" in page
    with client.session_transaction() as session:
        return session[two_step.SETUP_KEY]


def enable(client, secret, password=TEST_PASSWORD, code=None):
    return client.post(
        "/account/two-step/enable",
        data={
            "csrf_token": csrf_token(client, "/account/two-step"),
            "current_password": password,
            "code": code if code is not None else totp.code_at(secret, auth.now_seconds()),
        },
    )


def enabled_account(app, client):
    """Switch two-step on for the default account; return (secret, codes)."""
    secret = setup_secret(client)
    response = enable(client, secret)
    assert response.status_code == 201
    html = response.get_data(as_text=True)
    codes = shown_codes(html)
    assert len(codes) == 10
    return secret, codes


def password_step(client, email=TEST_EMAIL, password=TEST_PASSWORD):
    return client.post(
        "/login", data={"csrf_token": csrf_token(client, "/login"), "email": email, "password": password}
    )


def code_step(client, code):
    return client.post(
        "/login/two-step", data={"csrf_token": csrf_token(client, "/login/two-step"), "code": code}
    )


@pytest.fixture
def clock(monkeypatch):
    """Move the clock in steps of 30 seconds so each test code is fresh."""
    state = {"now": auth.now_seconds()}

    def advance(seconds=30):
        state["now"] += seconds

    for target in ("tradeledger.web.auth.now_seconds", "tradeledger.web.two_step.now_seconds"):
        monkeypatch.setattr(target, lambda: state["now"])
    advance.now = lambda: state["now"]
    return advance


# ---------- Setting it up ----------


def test_setup_needs_the_password_and_a_working_code(app, client, clock):
    secret = setup_secret(client)
    wrong_password = enable(client, secret, password="not my password at all")
    assert wrong_password.status_code == 400 and "is incorrect" in wrong_password.get_data(as_text=True)
    wrong_code = enable(client, secret, code="000000")
    assert wrong_code.status_code == 400 and "Code is not right" in wrong_code.get_data(as_text=True)
    with app.app_context():
        assert get_user(get_db(), 1)["totp_enabled_at"] is None
    clock()
    assert enable(client, secret).status_code == 201
    with app.app_context():
        user = get_user(get_db(), 1)
    assert user["totp_enabled_at"] and secret not in user["totp_secret"]
    assert public_user(user)["two_step_enabled"] is True and "totp_secret" not in public_user(user)


def test_the_setup_page_shows_the_key_but_never_after_it_is_on(app, client, clock):
    secret = setup_secret(client)
    page = client.get("/account/two-step").get_data(as_text=True)
    assert totp.grouped(secret) in page and "otpauth://totp/" in page
    enable(client, secret)
    after = client.get("/account/two-step").get_data(as_text=True)
    assert "Two-step verification is on" in after and secret not in after and "otpauth://" not in after
    with client.session_transaction() as session:
        assert two_step.SETUP_KEY not in session


def test_recovery_codes_are_shown_once_and_stored_hashed(app, client, clock):
    _, codes = enabled_account(app, client)
    assert codes[0] not in client.get("/account/two-step").get_data(as_text=True)
    with app.app_context():
        rows = get_db().execute("SELECT code_hash FROM recovery_codes WHERE user_id = 1").fetchall()
    assert len(rows) == 10
    assert {r["code_hash"] for r in rows} == {totp.recovery_hash(c) for c in codes}
    assert not any(c in r["code_hash"] for c in codes for r in rows)


def test_account_page_shows_the_status(app, client, clock):
    assert "Set up two-step verification" in client.get("/account").get_data(as_text=True)
    enabled_account(app, client)
    assert "Manage two-step verification" in client.get("/account").get_data(as_text=True)


# ---------- Signing in ----------


def test_a_password_alone_no_longer_signs_in(app, client, clock):
    enabled_account(app, client)
    browser = app.test_client()
    response = password_step(browser)
    assert response.status_code == 303 and response.headers["Location"] == "/login/two-step"
    assert browser.get("/").status_code == 302  # not signed in yet
    with browser.session_transaction() as session:
        assert "sid" not in session and "uid" not in session


def test_the_right_code_signs_in_and_returns_to_the_page_asked_for(app, client, clock):
    secret, _ = enabled_account(app, client)
    browser = app.test_client()
    browser.post(
        "/login",
        data={
            "csrf_token": csrf_token(browser, "/login"),
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
            "next": "/trades?status=OPEN",
        },
    )
    clock()
    response = code_step(browser, totp.code_at(secret, clock.now()))
    assert response.status_code == 303 and response.headers["Location"] == "/trades?status=OPEN"
    assert browser.get("/").status_code == 200


def test_a_wrong_code_is_refused_and_counted(app, client, clock):
    enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    response = code_step(browser, "123456")
    assert response.status_code == 400 and "That code is not right" in response.get_data(as_text=True)
    assert browser.get("/").status_code == 302


def test_a_code_cannot_be_replayed_by_someone_else(app, client, clock):
    secret, _ = enabled_account(app, client)
    clock()
    code = totp.code_at(secret, clock.now())
    first, second = app.test_client(), app.test_client()
    password_step(first)
    password_step(second)
    assert code_step(first, code).status_code == 303
    assert code_step(second, code).status_code == 400  # the same code, seen over someone's shoulder


def test_the_code_used_to_switch_it_on_cannot_be_reused_to_sign_in(app, client, clock):
    secret = setup_secret(client)
    code = totp.code_at(secret, clock.now())
    enable(client, secret, code=code)
    browser = app.test_client()
    password_step(browser)
    assert code_step(browser, code).status_code == 400


def test_a_recovery_code_works_once(app, client, clock):
    _, codes = enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    response = code_step(browser, codes[0].upper())
    assert response.status_code == 303
    assert browser.get("/").status_code == 200
    again = app.test_client()
    password_step(again)
    assert code_step(again, codes[0]).status_code == 400


def test_another_users_recovery_code_does_not_work(app, clock):
    with app.app_context():
        other = ensure_user(get_db(), OTHER_EMAIL)
    mine, theirs = app.test_client(), app.test_client()
    sign_in(mine, 1)
    sign_in(theirs, other)
    enabled_account(app, mine)
    secret = setup_secret(theirs)
    clock()
    response = enable(theirs, secret)
    their_codes = shown_codes(response.get_data(as_text=True))
    browser = app.test_client()
    password_step(browser)
    assert code_step(browser, their_codes[0]).status_code == 400


def test_the_second_step_expires_after_five_minutes(app, client, clock):
    secret, _ = enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    token = csrf_token(browser, "/login/two-step")  # the form was open...
    clock(301)  # ...for more than five minutes
    response = browser.post(
        "/login/two-step", data={"csrf_token": token, "code": totp.code_at(secret, clock.now())}
    )
    assert response.status_code == 303 and response.headers["Location"] == "/login"
    assert browser.get("/").status_code == 302


def test_the_second_step_without_a_password_goes_back_to_sign_in(anon_client):
    response = anon_client.get("/login/two-step")
    assert response.status_code == 303 and response.headers["Location"] == "/login"


def test_a_password_change_elsewhere_cancels_a_pending_second_step(app, client, clock):
    secret, _ = enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    token = csrf_token(browser, "/login/two-step")
    from tradeledger.repositories.users import bump_session_version

    with app.app_context():
        bump_session_version(get_db(), 1)
    clock()
    response = browser.post(
        "/login/two-step", data={"csrf_token": token, "code": totp.code_at(secret, clock.now())}
    )
    assert response.headers["Location"] == "/login"


def test_wrong_codes_are_throttled(app, client, clock):
    secret, _ = enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    for _ in range(app.config["LOGIN_MAX_FAILURES_PER_ACCOUNT"]):
        code_step(browser, "000000")
    clock()
    blocked = code_step(browser, totp.code_at(secret, clock.now()))
    assert blocked.status_code == 429  # even the right code waits


def test_the_second_step_needs_a_csrf_token(app, client, clock):
    secret, _ = enabled_account(app, client)
    browser = app.test_client()
    password_step(browser)
    clock()
    response = browser.post("/login/two-step", data={"code": totp.code_at(secret, clock.now())})
    assert response.status_code == 400


# ---------- Switching it off ----------


def test_switching_off_needs_password_and_a_code(app, client, clock):
    secret, _ = enabled_account(app, client)

    def disable(password, code):
        return client.post(
            "/account/two-step/disable",
            data={
                "csrf_token": csrf_token(client, "/account/two-step"),
                "current_password": password,
                "code": code,
            },
        )

    assert disable("wrong password here", totp.code_at(secret, clock.now())).status_code == 400
    assert disable(TEST_PASSWORD, "000000").status_code == 400
    clock()
    assert disable(TEST_PASSWORD, totp.code_at(secret, clock.now())).status_code == 303
    with app.app_context():
        user = get_user(get_db(), 1)
        left = get_db().execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id = 1").fetchone()[0]
    assert user["totp_enabled_at"] is None and user["totp_secret"] is None and left == 0
    browser = app.test_client()
    assert password_step(browser).headers["Location"] == "/"


def test_new_recovery_codes_cancel_the_old_ones(app, client, clock):
    _, old_codes = enabled_account(app, client)
    response = client.post(
        "/account/two-step/recovery-codes",
        data={"csrf_token": csrf_token(client, "/account/two-step"), "current_password": TEST_PASSWORD},
    )
    assert response.status_code == 201
    browser = app.test_client()
    password_step(browser)
    assert code_step(browser, old_codes[1]).status_code == 400


def test_the_operator_can_switch_it_off_for_a_lost_phone(app, client, clock):
    enabled_account(app, client)
    result = CliRunner().invoke(
        disable_two_step_command, ["--email", TEST_EMAIL], obj=ScriptInfo(create_app=lambda: app)
    )
    assert result.exit_code == 0 and "switched off" in result.output
    assert client.get("/").status_code == 302  # sessions ended
    assert password_step(app.test_client()).headers["Location"] == "/"


def test_a_rotated_secret_key_fails_closed(app, client, clock):
    """If SECRET_KEY changes, stored secrets cannot be read. Sign-in must
    then REFUSE codes (never skip the second step); recovery codes still work."""
    secret, codes = enabled_account(app, client)
    app.config["SECRET_KEY"] = "a-completely-different-secret-key-" + "x" * 30
    browser = app.test_client()
    password_step(browser)
    clock()
    assert code_step(browser, totp.code_at(secret, clock.now())).status_code == 400
    assert code_step(browser, codes[2]).status_code == 303


# ---------- Edge cases found by branch coverage (Phase 8.5) ----------


def test_enable_without_a_setup_key_or_twice_just_returns_to_the_page(app, client, clock):
    no_key = client.post(
        "/account/two-step/enable",
        data={
            "csrf_token": csrf_token(client, "/account"),
            "current_password": TEST_PASSWORD,
            "code": "123456",
        },
    )
    assert no_key.status_code == 303 and no_key.headers["Location"] == "/account/two-step"
    enabled_account(app, client)
    again = client.post(
        "/account/two-step/enable",
        data={
            "csrf_token": csrf_token(client, "/account"),
            "current_password": TEST_PASSWORD,
            "code": "123456",
        },
    )
    assert again.status_code == 303
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id = 1").fetchone()[0] == 10


def test_disable_and_new_codes_are_404_when_two_step_is_off(client):
    token = csrf_token(client, "/account")
    for path in ("/account/two-step/disable", "/account/two-step/recovery-codes"):
        response = client.post(
            path, data={"csrf_token": token, "current_password": TEST_PASSWORD, "code": "1"}
        )
        assert response.status_code == 404, path


def test_new_recovery_codes_need_the_password(app, client, clock):
    enabled_account(app, client)
    response = client.post(
        "/account/two-step/recovery-codes",
        data={
            "csrf_token": csrf_token(client, "/account/two-step"),
            "current_password": "not the password here",
        },
    )
    assert response.status_code == 400 and "is incorrect" in response.get_data(as_text=True)


def test_password_guessing_on_the_two_step_page_is_throttled(app, client):
    setup_secret(client)
    for _ in range(app.config["LOGIN_MAX_FAILURES_PER_ACCOUNT"]):
        enable(client, "A" * 32, password="wrong password guess", code="000000")
    blocked = enable(client, "A" * 32, password=TEST_PASSWORD, code="000000")
    assert blocked.status_code == 400 and "Too many sign-in attempts" in blocked.get_data(as_text=True)
