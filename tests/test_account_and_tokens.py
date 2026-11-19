"""Phase 8: account settings (password change, sign out elsewhere) and
personal API tokens (creation, storage, verification, expiry, revocation)."""

import logging
import re
from datetime import timedelta

import pytest

from tests.conftest import OTHER_EMAIL, TEST_PASSWORD, ensure_user, issue_token, sign_in
from tradeledger.db import get_db
from tradeledger.passwords import verify_password
from tradeledger.repositories import api_tokens
from tradeledger.repositories.users import get_user, set_active

NEW_PASSWORD = "a brand new pass phrase"


def post(client, url, data):
    page = client.get("/account").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    nonce = re.search(r'name="form_nonce" value="([^"]+)"', page)
    extra = {"form_nonce": nonce.group(1)} if nonce else {}
    return client.post(url, data={"csrf_token": token, **extra, **data})


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- Account page ----------


def test_account_page_shows_the_account_but_no_secrets(app, client):
    html = client.get("/account").get_data(as_text=True)
    assert "trader@example.com" in html
    with app.app_context():
        assert get_user(get_db(), 1)["password_hash"] not in html
    # Only 10-character prefixes (e.g. "tl_Ab3dE6f…"), never a whole token.
    assert re.search(r"tl_[A-Za-z0-9_-]{12,}", html) is None


def test_password_change_requires_the_current_password(app, client):
    response = post(
        client,
        "/account/password",
        {
            "current_password": "not my password",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        },
    )
    assert response.status_code == 400
    assert "Current password is incorrect." in response.get_data(as_text=True)
    with app.app_context():
        assert verify_password(TEST_PASSWORD, get_user(get_db(), 1)["password_hash"])


@pytest.mark.parametrize(
    "new, confirm, message",
    [
        ("short", "short", "New password must be at least 12 characters."),
        (NEW_PASSWORD, "something else entirely", "Confirm new password does not match"),
        (TEST_PASSWORD, TEST_PASSWORD, "New password must be different from your current password."),
    ],
)
def test_password_change_validation(client, new, confirm, message):
    response = post(
        client,
        "/account/password",
        {"current_password": TEST_PASSWORD, "new_password": new, "confirm_password": confirm},
    )
    assert response.status_code == 400
    assert message in response.get_data(as_text=True)


def test_password_change_signs_out_other_browsers_and_revokes_tokens(app, client):
    other_browser = app.test_client()
    sign_in(other_browser)
    token = issue_token(app, name="script")
    assert app.test_client().get("/api/v1/trades", headers=bearer(token)).status_code == 200

    response = post(
        client,
        "/account/password",
        {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )
    assert response.status_code == 303
    assert client.get("/account").status_code == 200  # this browser stays signed in
    assert other_browser.get("/").status_code == 302  # the other one does not
    assert app.test_client().get("/api/v1/trades", headers=bearer(token)).status_code == 401
    with app.app_context():
        assert verify_password(NEW_PASSWORD, get_user(get_db(), 1)["password_hash"])


def test_wrong_current_password_counts_towards_the_throttle(app, client):
    for _ in range(app.config["LOGIN_MAX_FAILURES_PER_ACCOUNT"]):
        post(
            client,
            "/account/password",
            {"current_password": "guess guess guess", "new_password": NEW_PASSWORD},
        )
    response = post(
        client,
        "/account/password",
        {"current_password": TEST_PASSWORD, "new_password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD},
    )
    assert response.status_code == 429


def test_sign_out_everywhere_else(app, client):
    other_browser = app.test_client()
    sign_in(other_browser)
    response = post(client, "/account/sessions/end-others", {})
    assert response.status_code == 303
    assert client.get("/").status_code == 200
    assert other_browser.get("/").status_code == 302


# ---------- Creating and listing tokens ----------


def create_via_page(client, name="Laptop script", days="90"):
    return post(client, "/account/tokens", {"name": name, "lifetime_days": days})


def test_token_is_shown_once_and_stored_only_as_a_hash(app, client):
    response = create_via_page(client)
    assert response.status_code == 201
    html = response.get_data(as_text=True)
    token = re.search(r'id="new-token-value"[^>]*value="(tl_[A-Za-z0-9_-]+)"', html, re.S).group(1)
    assert response.headers["Cache-Control"] == "no-store"
    with app.app_context():
        row = dict(get_db().execute("SELECT * FROM api_tokens WHERE name = 'Laptop script'").fetchone())
    assert token not in str(row)
    assert row["token_hash"] == api_tokens.token_digest(token)
    assert row["token_prefix"] == token[:10]
    # Never again: the account page now shows only the prefix.
    assert token not in client.get("/account").get_data(as_text=True)
    assert app.test_client().get("/api/v1/trades", headers=bearer(token)).status_code == 200


@pytest.mark.parametrize(
    "name, days, message",
    [
        ("", "90", "Token name Give the token a name"),
        ("x" * 61, "90", "Use 60 characters or fewer."),
        ("ok", "0", "Choose between 1 and 365 days."),
        ("ok", "366", "Choose between 1 and 365 days."),
        ("ok", "ninety", "Choose between 1 and 365 days."),
    ],
)
def test_token_form_validation(client, name, days, message):
    response = create_via_page(client, name, days)
    assert response.status_code == 400
    assert message in response.get_data(as_text=True)


def test_number_of_active_tokens_is_limited(app, client):
    app.config["API_TOKEN_MAX_ACTIVE"] = 2  # the client fixture already holds one token
    assert create_via_page(client, "two").status_code == 201
    response = create_via_page(client, "three")
    assert response.status_code == 400
    assert "already have 2 active tokens" in response.get_data(as_text=True)


def test_token_creation_is_not_logged(app, client, caplog):
    with caplog.at_level(logging.DEBUG):
        html = create_via_page(client).get_data(as_text=True)
    token = re.search(r'value="(tl_[A-Za-z0-9_-]+)"', html).group(1)
    assert token not in caplog.text


# ---------- Verifying tokens ----------


def test_api_ignores_session_cookies(seeded_client):
    """A signed-in BROWSER without a token gets 401: the API's only
    credential is the Authorization header, which no other website can
    make a browser send. This is what makes the API immune to CSRF."""
    seeded_client.environ_base.pop("HTTP_AUTHORIZATION")
    assert seeded_client.get("/").status_code == 200  # the browser IS signed in
    for method in ("get", "post", "delete"):
        response = getattr(seeded_client, method)(
            "/api/v1/trades/1" if method == "delete" else "/api/v1/trades"
        )
        assert response.status_code == 401


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Basic dHJhZGVyOnBhc3M=",
        "Bearer tl_" + "A" * 43,  # well-formed, but no such token
        "Bearer not-a-token",
        "Token tl_" + "A" * 43,
    ],
)
def test_invalid_credentials_are_rejected(app, header):
    headers = {} if header is None else {"Authorization": header}
    response = app.test_client().get("/api/v1/trades", headers=headers)
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "unauthorized"
    assert response.headers["WWW-Authenticate"] == 'Bearer realm="TradeLedger API"'


def test_scheme_name_is_case_insensitive(app):
    token = issue_token(app)
    assert (
        app.test_client().get("/api/v1/trades", headers={"Authorization": f"bearer {token}"}).status_code
        == 200
    )


def test_revoked_token_is_rejected_immediately(app):
    token = issue_token(app, name="to revoke")
    client = app.test_client()
    assert client.get("/api/v1/instruments", headers=bearer(token)).status_code == 200
    with app.app_context():
        record = next(t for t in api_tokens.list_tokens(get_db(), user_id=1) if t["name"] == "to revoke")
        assert api_tokens.revoke_token(get_db(), record["id"], user_id=1)
    assert client.get("/api/v1/instruments", headers=bearer(token)).status_code == 401


def test_expired_token_is_rejected(app, monkeypatch):
    token = issue_token(app)
    from tradeledger.repositories import api_tokens as module

    real_now = module.utc_now
    monkeypatch.setattr(module, "utc_now", lambda: real_now() + timedelta(days=31))  # issued for 30 days
    assert app.test_client().get("/api/v1/trades", headers=bearer(token)).status_code == 401


def test_token_of_a_disabled_account_is_rejected(app):
    token = issue_token(app)
    with app.app_context():
        set_active(get_db(), 1, False)
    assert app.test_client().get("/api/v1/trades", headers=bearer(token)).status_code == 401


def test_last_used_is_recorded(app):
    token = issue_token(app, name="used")
    app.test_client().get("/api/v1/trades", headers=bearer(token))
    with app.app_context():
        record = next(t for t in api_tokens.list_tokens(get_db(), user_id=1) if t["name"] == "used")
    assert record["last_used_at"] is not None


def test_rejected_tokens_are_not_logged(app, caplog):
    secret = "tl_" + "S" * 43
    with caplog.at_level(logging.DEBUG):
        app.test_client().get("/api/v1/trades", headers=bearer(secret))
    assert secret not in caplog.text
    assert "S" * 20 not in caplog.text


# ---------- Revoking through the website ----------


def test_revoke_through_the_account_page(app, client):
    html = create_via_page(client, "page token").get_data(as_text=True)
    token = re.search(r'value="(tl_[A-Za-z0-9_-]+)"', html).group(1)
    token_id = int(re.search(r"/account/tokens/(\d+)/revoke", html).group(1))
    response = post(client, f"/account/tokens/{token_id}/revoke", {})
    assert response.status_code == 303
    assert app.test_client().get("/api/v1/trades", headers=bearer(token)).status_code == 401


def test_cannot_revoke_another_users_token(app, client):
    with app.app_context():
        other = ensure_user(get_db(), OTHER_EMAIL)
    other_token = issue_token(app, user_id=other, name="theirs")
    with app.app_context():
        their_id = api_tokens.list_tokens(get_db(), user_id=other)[0]["id"]
    response = post(client, f"/account/tokens/{their_id}/revoke", {})
    assert response.status_code == 404  # same answer as a token that does not exist
    assert app.test_client().get("/api/v1/instruments", headers=bearer(other_token)).status_code == 200


def test_resubmitting_the_create_form_does_not_create_a_second_token(app, client):
    """A browser refresh re-sends the POST that displayed the token."""
    page = client.get("/account").get_data(as_text=True)
    form = {
        "csrf_token": re.search(r'name="csrf_token" value="([^"]+)"', page).group(1),
        "form_nonce": re.search(r'name="form_nonce" value="([^"]+)"', page).group(1),
        "name": "once only",
        "lifetime_days": "30",
    }
    assert client.post("/account/tokens", data=form).status_code == 201
    again = client.post("/account/tokens", data=form)
    assert again.status_code == 303
    with app.app_context():
        names = [t["name"] for t in api_tokens.list_tokens(get_db(), user_id=1)]
    assert names.count("once only") == 1


def test_revoke_requires_post_and_csrf(client):
    assert client.get("/account/tokens/1/revoke").status_code == 405
    assert client.post("/account/tokens/1/revoke", data={}).status_code == 400
