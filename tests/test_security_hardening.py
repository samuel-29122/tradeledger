"""Phase 7: security fixes found in the Phase 7 security review.

Each block protects one finding (see docs/PHASE_7_SECURITY_REVIEW.md):

  F1  Unbounded decimal magnitude ("1e999999") was accepted, stored as a
      one-million-digit string, and crashed every page that evaluated it.
  F2  No Host header allow-list: DNS rebinding could reach the
      unauthenticated local journal and API through the user's browser.
  F3  Production accepted a short or published placeholder SECRET_KEY.
  F4  HTML pages full of trading data carried no Cache-Control header.
  F5  HSTS was not sent in production.
  F6  The order of the website's request hooks depended on import order.

Phase 8 changed two F6 tests deliberately, because the requirement they
protected changed: the Phase 7 "journal disabled in production" guard
(403) was replaced by real sign-in. The property is the same, the order of
the hooks is explicit and an unauthenticated POST never reaches the CSRF
check, but the first hook is now the login check and the answer is a
redirect to the sign-in page instead of 403.
"""

import pytest

from tests.factories import USER_ID
from tests.web_helpers import closed_trade_form, count_rows
from tradeledger import create_app
from tradeledger.config import env_hosts
from tradeledger.db import get_db, init_schema
from tradeledger.performance import build_report
from tradeledger.seed import seed_sample_data
from tradeledger.validation import parse_decimal
from tradeledger.web.auth import load_logged_in_user, require_login
from tradeledger.web.csrf import check_csrf_token

STRONG_KEY = "c0ffee" * 11  # 66 characters

# ---------- F1: decimal magnitude ----------


@pytest.mark.parametrize(
    "text, message",
    [
        ("1e999999", "must have at most 15 digits before the decimal point"),
        ("1" + "0" * 15, "must have at most 15 digits before the decimal point"),  # 16 digits
        ("1e-999999", "must have at most 15 decimal places"),
        ("0." + "0" * 15 + "1", "must have at most 15 decimal places"),  # 16 places
    ],
)
def test_out_of_range_decimals_are_rejected(text, message):
    with pytest.raises(ValueError, match=message):
        parse_decimal(text)


@pytest.mark.parametrize("text", ["9" * 15, "0." + "0" * 14 + "1", "123456.123456789", "1E+5", "0.00001234"])
def test_values_at_the_limits_are_accepted(text):
    assert parse_decimal(text) > 0


def test_api_rejects_a_huge_exponent_without_storing_it(seeded_client, app, trade_body):
    before = count_rows(app, "trades")
    response = seeded_client.post("/api/v1/trades", json={**trade_body, "entry_price": "1e999999"})
    assert response.status_code == 400
    assert response.get_json()["error"]["details"]["entry_price"] == (
        "must have at most 15 digits before the decimal point"
    )
    assert count_rows(app, "trades") == before


def test_website_form_rejects_a_huge_exponent(seeded_client, app):
    page = seeded_client.get("/trades/new").get_data(as_text=True)
    token = page.split('name="csrf_token" value="')[1].split('"')[0]
    before = count_rows(app, "trades")
    form = {**closed_trade_form(), "position_size": "1e-999999", "csrf_token": token}
    response = seeded_client.post("/trades/new", data=form)
    assert response.status_code == 400
    assert "must have at most 15 decimal places" in response.get_data(as_text=True)
    assert count_rows(app, "trades") == before


def test_instrument_with_huge_multiplier_is_rejected(seeded_client):
    body = {
        "symbol": "HUGE",
        "name": "Huge",
        "asset_class": "OTHER",
        "price_increment": "0.01",
        "contract_multiplier": "1e999999",
        "quote_currency": "USD",
    }
    response = seeded_client.post("/api/v1/instruments", json=body)
    assert response.status_code == 400
    assert "contract_multiplier" in response.get_json()["error"]["details"]


@pytest.mark.sqlite_only  # PostgreSQL's NUMERIC CHECKs (schema_postgres.sql) refuse such a row at all
def test_an_out_of_range_row_already_in_the_database_cannot_break_pages(app, seeded_client):
    """Defence in depth. Validation now blocks such values, but a database
    written before Phase 7 (or edited by hand) might contain one. The
    engine reports it as an excluded trade instead of raising Overflow."""
    huge = "1" + "0" * 600_000  # 10^600000: products overflow Decimal's exponent limit
    with app.app_context():
        conn = get_db()
        # take_profit is cleared too: the schema's own CHECK would otherwise
        # (correctly) refuse a target below such a huge entry price.
        conn.execute(
            "UPDATE trades SET entry_price = ?, position_size = ?, take_profit = NULL WHERE id = 1",
            (huge, huge),
        )
        conn.commit()
        report = build_report(conn, user_id=USER_ID)
    assert [(e.trade_id, e.reason) for e in report.excluded] == [
        (1, "values are too large to calculate exactly")
    ]
    for url in ("/analytics", "/", "/trades", "/trades/1", "/api/v1/trades", "/api/v1/analytics/summary"):
        assert seeded_client.get(url).status_code == 200, url
    assert seeded_client.get("/api/v1/trades/1").get_json()["data"]["result"] is None


# ---------- F2: Host header allow-list ----------


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "localhost:5000", "127.0.0.1:8080"])
def test_local_host_names_are_accepted(anon_client, host):
    # Phase 8: "/" needs a session cookie, which belongs to "localhost"; the
    # public sign-in page tests the Host check on its own.
    assert anon_client.get("/login", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("path", ["/", "/trades", "/api/v1/trades"])
def test_rebound_host_name_is_refused(seeded_client, path):
    """DNS rebinding: an attacker's domain resolves to 127.0.0.1, so the
    browser sends Host: attacker.example. The app must not answer."""
    response = seeded_client.get(path, headers={"Host": "attacker.example"})
    assert response.status_code == 400
    assert b"EURUSD" not in response.data


def test_rebound_host_cannot_change_data(seeded_client, app):
    before = count_rows(app, "trades")
    response = seeded_client.delete("/api/v1/trades/1", headers={"Host": "attacker.example:5000"})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "http_error"
    assert count_rows(app, "trades") == before


def test_env_hosts_parsing(monkeypatch):
    monkeypatch.delenv("TL_TEST_HOSTS", raising=False)
    assert env_hosts("TL_TEST_HOSTS", ["localhost"]) == ["localhost"]
    monkeypatch.setenv("TL_TEST_HOSTS", " Journal.Example.com , 192.168.1.20 ,, ")
    assert env_hosts("TL_TEST_HOSTS", None) == ["journal.example.com", "192.168.1.20"]
    monkeypatch.setenv("TL_TEST_HOSTS", "*")
    assert env_hosts("TL_TEST_HOSTS", ["localhost"]) is None


def test_extra_trusted_host_from_configuration(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path, "TRUSTED_HOSTS": ["192.168.1.20"]})
    with app.app_context():
        init_schema(get_db())
    client = app.test_client()
    assert client.get("/login", headers={"Host": "192.168.1.20:5000"}).status_code == 200
    assert client.get("/login", headers={"Host": "localhost"}).status_code == 400


# ---------- F3: production SECRET_KEY strength ----------


@pytest.mark.parametrize(
    "key",
    [
        "short",
        "x" * 31,
        "replace-me-with-a-long-random-string",  # the .env.example placeholder (36 characters)
        "test-secret-key-not-for-real-use",  # the public testing key (32 characters)
    ],
)
def test_production_refuses_weak_secret_keys(db_path, key):
    with pytest.raises(RuntimeError, match="SECRET_KEY is too weak"):
        create_app("production", overrides={"SECRET_KEY": key, "DATABASE": db_path})


def test_production_accepts_a_strong_key(db_path):
    app = create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path})
    assert app.config["SECRET_KEY"] == STRONG_KEY


def test_weak_key_error_never_contains_the_key(db_path):
    with pytest.raises(RuntimeError) as info:
        create_app("production", overrides={"SECRET_KEY": "my-short-secret", "DATABASE": db_path})
    assert "my-short-secret" not in str(info.value)


def test_development_still_accepts_a_short_key(db_path):
    app = create_app("development", overrides={"SECRET_KEY": "dev", "DATABASE": db_path})
    assert app.config["SECRET_KEY"] == "dev"


# ---------- F4/F5: caching and HSTS headers ----------


@pytest.mark.parametrize("url", ["/", "/trades", "/trades/1", "/analytics", "/about", "/no-such-page"])
def test_html_pages_are_not_stored_by_caches(seeded_client, url):
    assert seeded_client.get(url).headers["Cache-Control"] == "no-store"


def test_static_files_stay_cacheable(client):
    with client.get("/static/css/style.css") as response:
        assert "no-store" not in response.headers.get("Cache-Control", "")


def test_hsts_only_in_production(db_path, client):
    assert "Strict-Transport-Security" not in client.get("/health").headers
    app = create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path})
    response = app.test_client().get("/health")
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"


# ---------- F6: request hook order ----------


def test_login_check_runs_before_csrf_check(app):
    """Registered explicitly in web/__init__.py, not by import order:
    load the session, then require login, then check CSRF."""
    hooks = app.before_request_funcs["main"]
    assert hooks.index(load_logged_in_user) < hooks.index(require_login) < hooks.index(check_csrf_token)


def test_production_anonymous_post_is_sent_to_sign_in_and_deletes_nothing(db_path):
    app = create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path})
    with app.app_context():
        init_schema(get_db())
        from tests.conftest import ensure_user

        seed_sample_data(get_db(), user_id=ensure_user(get_db()))
    with app.test_client() as client:
        response = client.post("/trades/1/delete", data={}, base_url="https://localhost")
    assert response.status_code == 303
    assert response.headers["Location"].startswith("/login")
    assert count_rows(app, "trades") == 16
