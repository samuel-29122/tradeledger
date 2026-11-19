"""Phase 5 security: CSRF, output escaping, safe redirects, HTTP methods,
security headers, cookies and production sign-in enforcement."""

import logging
import re

import pytest

from tests.web_helpers import (
    closed_trade_form,
    count_rows,
    csrf_token,
    fetch_trade,
    new_trade_id,
    post_form,
)
from tradeledger import create_app
from tradeledger.db import get_db, init_schema
from tradeledger.web.forms import safe_next

STATE_CHANGING_URLS = ["/trades/new", "/trades/1/edit", "/trades/1/delete", "/instruments"]


# ---------- CSRF ----------


@pytest.mark.parametrize("url", STATE_CHANGING_URLS)
def test_post_without_token_is_rejected_and_writes_nothing(app, seeded_client, url):
    trades, instruments = count_rows(app, "trades"), count_rows(app, "instruments")
    trade_1 = fetch_trade(app, 1)
    response = seeded_client.post(url, data=closed_trade_form(symbol="NEW1", name="x"))
    assert response.status_code == 400
    assert b"This form could not be accepted" in response.data
    assert count_rows(app, "trades") == trades
    assert count_rows(app, "instruments") == instruments
    assert fetch_trade(app, 1) == trade_1


@pytest.mark.parametrize("url", STATE_CHANGING_URLS)
def test_post_with_wrong_token_is_rejected(app, seeded_client, url):
    csrf_token(seeded_client)  # the session now has a real token
    trades = count_rows(app, "trades")
    response = seeded_client.post(url, data={**closed_trade_form(), "csrf_token": "forged-value"})
    assert response.status_code == 400
    assert count_rows(app, "trades") == trades


def test_token_from_another_session_is_rejected(app, seeded_client):
    stolen = csrf_token(app.test_client(), "/login")  # a different (anonymous) browser's token
    trades = count_rows(app, "trades")
    response = seeded_client.post("/trades/1/delete", data={"csrf_token": stolen})
    assert response.status_code == 400
    assert count_rows(app, "trades") == trades


def test_empty_session_rejects_even_an_empty_token(seeded_client):
    response = seeded_client.post("/trades/1/delete", data={"csrf_token": ""})
    assert response.status_code == 400


def test_valid_token_is_accepted(app, seeded_client):
    response = post_form(seeded_client, "/trades/new", closed_trade_form())
    assert response.status_code == 302


def test_every_form_carries_a_token(seeded_client):
    for url in ("/trades/new", "/trades/1/edit", "/trades/1/delete", "/instruments"):
        html = seeded_client.get(url).get_data(as_text=True)
        post_forms = len(re.findall(r'<form[^>]*method="post"', html))
        assert post_forms >= 1
        assert html.count('name="csrf_token"') >= post_forms


def test_journal_row_delete_links_are_get_links_to_the_confirmation(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    assert 'href="/trades/1/delete' in html  # a link to the confirmation page...
    assert 'action="/trades/1/delete"' not in html  # ...not a one-click POST


def test_api_does_not_need_csrf_tokens(seeded_client, trade_body):
    """The JSON API has its own defences (JSON-only, disabled in
    production); the website's CSRF check must not break it."""
    response = seeded_client.post("/api/v1/trades", json=trade_body)
    assert response.status_code == 201


# ---------- HTTP methods ----------


def test_get_delete_never_deletes(app, seeded_client):
    before = count_rows(app, "trades")
    for _ in range(3):
        seeded_client.get("/trades/1/delete")
    assert count_rows(app, "trades") == before


@pytest.mark.parametrize(
    "method, url",
    [
        ("put", "/trades/1"),
        ("delete", "/trades/1"),
        ("post", "/trades/1"),
        ("post", "/trades"),
        ("post", "/"),
        ("put", "/instruments"),
        ("delete", "/trades/1/delete"),
    ],
)
def test_unsupported_methods_are_405(seeded_client, method, url):
    response = getattr(seeded_client, method)(url)
    assert response.status_code == 405


# ---------- Escaping (stored XSS) ----------

PAYLOAD = '<script>alert("x")</script><img src=x onerror=alert(1)>'


def test_malicious_text_is_escaped_everywhere(app, seeded_client):
    form = closed_trade_form(strategy='<script>alert("s")</script>', notes=PAYLOAD)  # strategy max 50
    trade_id = new_trade_id(post_form(seeded_client, "/trades/new", form))
    assert fetch_trade(app, trade_id)["notes"] == PAYLOAD  # stored exactly as typed

    for url in (
        "/",
        "/trades",
        f"/trades/{trade_id}",
        f"/trades/{trade_id}/edit",
        f"/trades/{trade_id}/delete",
        "/analytics",
        "/trades?q=alert",
    ):
        html = seeded_client.get(url).get_data(as_text=True)
        assert "<script>alert" not in html, url
        assert "<img src=x" not in html, url


def test_escaped_payload_is_visible_as_text(app, seeded_client):
    trade_id = new_trade_id(post_form(seeded_client, "/trades/new", closed_trade_form(notes=PAYLOAD)))
    html = seeded_client.get(f"/trades/{trade_id}").get_data(as_text=True)
    assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in html


def test_search_text_is_escaped_when_echoed(seeded_client):
    html = seeded_client.get('/trades?q="><script>alert(1)</script>').get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html


def test_malicious_instrument_name_is_escaped(seeded_client):
    post_form(
        seeded_client,
        "/instruments",
        {
            "symbol": "EVIL",
            "name": "<b onmouseover=alert(1)>x</b>",
            "asset_class": "OTHER",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        token_page="/instruments",
    )
    for url in ("/instruments", "/trades/new"):
        html = seeded_client.get(url).get_data(as_text=True)
        assert "<b onmouseover" not in html
        assert "&lt;b onmouseover=alert(1)&gt;" in html


# ---------- Safe redirects ----------


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.example/",
        "//evil.example/",
        "/\\evil.example",
        "\\\\evil.example",
        "javascript:alert(1)",
        "http:/evil.example",
        "trades",
        "",
        None,
        "///evil.example",
        "//%2Fevil.example",
    ],
)
def test_safe_next_rejects_external_targets(app, target):
    with app.test_request_context():
        assert safe_next(target, "/fallback") == "/fallback"


@pytest.mark.parametrize("target", ["/trades", "/trades?status=CLOSED&page=2", "/trades/3"])
def test_safe_next_accepts_local_paths(app, target):
    with app.test_request_context():
        assert safe_next(target, "/fallback") == target


def test_delete_ignores_an_external_next(seeded_client):
    response = post_form(seeded_client, "/trades/1/delete", {"next": "https://evil.example/"})
    assert response.status_code == 302
    assert "evil" not in response.headers["Location"]
    assert response.headers["Location"].endswith("/trades")


def test_edit_cancel_link_ignores_an_external_next(seeded_client):
    html = seeded_client.get("/trades/1/edit?next=//evil.example").get_data(as_text=True)
    assert "evil.example" not in html


# ---------- Headers and cookies ----------


def test_security_headers_on_pages(client):
    response = client.get("/")
    csp = response.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_session_cookie_is_httponly_and_samesite(client):
    response = client.get("/trades/new")
    cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie
    assert "SameSite=Lax" in cookie


def _production(db_path, **overrides):
    app = create_app(
        "production",
        overrides={"SECRET_KEY": "a-real-secret-from-the-environment", "DATABASE": db_path, **overrides},
    )
    with app.app_context():
        init_schema(get_db())
    return app


def test_production_session_cookie_is_secure(db_path):
    assert _production(db_path).config["SESSION_COOKIE_SECURE"] is True


# ---------- Production requires sign-in (Phase 8) ----------
# Phase 7 had a guard that DISABLED the journal in production (403),
# because there was no login. Phase 8 replaced it with real accounts, so
# the same pages now send anonymous visitors to the sign-in page, and the
# old "enable without login" setting no longer exists.


@pytest.mark.parametrize(
    "url",
    [
        "/",
        "/trades",
        "/trades/new",
        "/trades/1",
        "/trades/1/edit",
        "/trades/1/delete",
        "/instruments",
        "/analytics",
        "/account",
    ],
)
def test_production_journal_requires_sign_in(db_path, url):
    client = _production(db_path).test_client()
    response = client.get(url, base_url="https://localhost")
    assert response.status_code == 302
    assert response.headers["Location"].startswith("/login?next=")


def test_production_anonymous_post_writes_nothing(db_path):
    app = _production(db_path)
    with app.app_context():
        init_schema(get_db())
    response = app.test_client().post("/trades/new", data=closed_trade_form(), base_url="https://localhost")
    assert response.status_code == 303
    assert response.headers["Location"] == "/login"
    assert count_rows(app, "trades") == 0


def test_production_sign_in_page_shows_no_trading_data(db_path):
    from tests.conftest import ensure_user
    from tradeledger.seed import seed_sample_data

    app = _production(db_path)
    with app.app_context():
        init_schema(get_db())
        seed_sample_data(get_db(), user_id=ensure_user(get_db()))
    response = app.test_client().get("/login", base_url="https://localhost")
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    for leak in ("EURUSD", "XAUUSD", "393.00", "Recent trades", "Breakout"):
        assert leak not in html


@pytest.mark.parametrize("url", ["/health", "/health/live", "/about", "/login", "/static/css/style.css"])
def test_production_keeps_harmless_pages_public(db_path, url):
    app = _production(db_path)
    with app.app_context():
        init_schema(get_db())
    with app.test_client().get(url, base_url="https://localhost") as response:  # closes static files
        assert response.status_code == 200


def test_old_journal_flag_cannot_reopen_the_journal(monkeypatch, db_path):
    monkeypatch.setenv("TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL", "1")
    client = _production(db_path).test_client()
    assert client.get("/trades", base_url="https://localhost").status_code == 302


def test_env_flag_accepts_only_exact_values(monkeypatch):
    from tradeledger.config import env_flag

    monkeypatch.setenv("TRADELEDGER_TEST_FLAG", "1")
    assert env_flag("TRADELEDGER_TEST_FLAG") is True
    monkeypatch.setenv("TRADELEDGER_TEST_FLAG", "yes")
    assert env_flag("TRADELEDGER_TEST_FLAG") is False
    assert env_flag("TRADELEDGER_TEST_FLAG", True) is True  # not "0" -> the default


def test_api_requires_a_token_in_production(db_path):
    response = _production(db_path).test_client().get("/api/v1/trades", base_url="https://localhost")
    assert response.status_code == 401


# ---------- Information leakage ----------


def test_form_submissions_are_not_logged(app, seeded_client, caplog):
    with caplog.at_level(logging.DEBUG):
        post_form(seeded_client, "/trades/new", closed_trade_form(notes="PRIVATE-NOTE-123"))
        post_form(seeded_client, "/trades/new", closed_trade_form(notes="PRIVATE-NOTE-456", stop_loss="9"))
    assert "PRIVATE-NOTE" not in caplog.text
    token = csrf_token(seeded_client)
    assert token not in caplog.text


def test_csrf_error_page_reveals_no_token(seeded_client):
    real = csrf_token(seeded_client)
    response = seeded_client.post("/trades/1/delete", data={"csrf_token": "wrong"})
    assert b"wrong" not in response.data  # the submitted value is never echoed
    # Phase 8: the page's own "Sign out" form legitimately carries the real
    # token (it is a same-site page for the signed-in user); nowhere else.
    assert response.data.count(real.encode()) == 1
    assert b'action="/logout"' in response.data


def test_500_page_is_generic(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path, "PROPAGATE_EXCEPTIONS": False})

    @app.route("/boom")
    def boom():
        raise RuntimeError("SELECT secret FROM trades")

    response = app.test_client().get("/boom")
    assert response.status_code == 500
    assert b"Something went wrong" in response.data
    assert b"SELECT" not in response.data
