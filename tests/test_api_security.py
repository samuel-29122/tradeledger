"""Cross-cutting API behaviour: production safety, error hygiene, limits,
logging and coexistence with the website."""

import json
import logging

import pytest

from tradeledger import create_app


def production_app(db_path, **overrides):
    return create_app(
        "production",
        overrides={"SECRET_KEY": "a-real-secret-from-the-environment", "DATABASE": db_path, **overrides},
    )


# ---------- Production safety ----------
# Phase 7 tested that production DISABLED the unauthenticated API (403).
# Phase 8 replaced that switch with authentication, so these tests now
# check that production REQUIRES a token, that the API can still be turned
# off, and that the old "enable without authentication" setting is gone.


def test_production_api_requires_a_token(db_path):
    client = production_app(db_path).test_client()
    for method, url in (
        ("get", "/api/v1/trades"),
        ("post", "/api/v1/trades"),
        ("get", "/api/v1/analytics/summary"),
        ("delete", "/api/v1/trades/1"),
    ):
        response = getattr(client, method)(url, json={}, base_url="https://localhost")
        assert response.status_code == 401
        assert response.get_json()["error"]["code"] == "unauthorized"
        assert response.headers["WWW-Authenticate"].startswith("Bearer")


def test_docs_and_health_stay_public_when_the_api_is_switched_off(db_path):
    client = production_app(db_path, API_ENABLED=False).test_client()
    response = client.get("/api/v1/trades", base_url="https://localhost")
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "api_disabled"
    assert client.get("/api/v1/openapi.json", base_url="https://localhost").status_code == 200
    assert client.get("/health", base_url="https://localhost").get_json()["status"] == "ok"
    # The website itself needs a signed-in user: the overview redirects to sign-in.
    home = client.get("/", base_url="https://localhost")
    assert home.status_code == 302 and home.headers["Location"].startswith("/login")


def test_removed_unauthenticated_api_setting_is_ignored_with_a_warning(db_path, monkeypatch, caplog):
    monkeypatch.setenv("TRADELEDGER_ENABLE_UNAUTHENTICATED_API", "1")
    monkeypatch.setenv("TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL", "1")
    with caplog.at_level(logging.WARNING):
        app = production_app(db_path)
    assert "TRADELEDGER_ENABLE_UNAUTHENTICATED_API is no longer supported and is ignored" in caplog.text
    assert "TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL is no longer supported" in caplog.text
    from tradeledger.db import get_db, init_schema

    with app.app_context():
        init_schema(get_db())
    client = app.test_client()
    assert client.get("/api/v1/trades", base_url="https://localhost").status_code == 401
    assert client.get("/trades", base_url="https://localhost").status_code == 302


@pytest.mark.parametrize("value, expected", [("1", True), ("0", False), ("true", False), (None, False)])
def test_opt_in_flag_must_be_exactly_1(monkeypatch, value, expected):
    from tradeledger.config import env_flag

    if value is None:
        monkeypatch.delenv("TRADELEDGER_TEST_FLAG", raising=False)
    else:
        monkeypatch.setenv("TRADELEDGER_TEST_FLAG", value)
    assert env_flag("TRADELEDGER_TEST_FLAG") is expected


# ---------- Error hygiene ----------


@pytest.fixture
def exploding_app(db_path):
    """An app with an extra API route that raises, to test the 500 handler."""
    app = production_app(db_path, API_ENABLED=True, PROPAGATE_EXCEPTIONS=False)

    def boom():
        raise RuntimeError(f"secret detail: SELECT * FROM trades; key={app.config['SECRET_KEY']}")

    app.add_url_rule("/api/v1/_boom", "api_boom", boom)
    app.add_url_rule("/_boom", "site_boom", boom)
    return app


def test_unexpected_api_error_is_generic_json(exploding_app, caplog):
    with caplog.at_level(logging.ERROR):
        response = exploding_app.test_client().get("/api/v1/_boom")
    assert response.status_code == 500
    assert response.get_json() == {
        "error": {"code": "internal_error", "message": "An unexpected error occurred."}
    }
    text = response.get_data(as_text=True)
    for leak in ("secret detail", "SELECT", "Traceback", "a-real-secret", ".py"):
        assert leak not in text
    assert "secret detail" in caplog.text  # the detail goes to the server log only


def test_unexpected_website_error_is_the_html_page(exploding_app):
    response = exploding_app.test_client().get("/_boom")
    assert response.status_code == 500
    assert b"Something went wrong" in response.data
    assert b"secret detail" not in response.data


def test_validation_errors_do_not_echo_sql_or_paths(seeded_client):
    response = seeded_client.post("/api/v1/trades", json={"instrument_id": 999, "trade_date": "x"})
    text = response.get_data(as_text=True)
    for leak in ("SELECT", "INSERT", "sqlite", "FOREIGN KEY", "/home/", "Traceback"):
        assert leak not in text


# ---------- Limits ----------


def test_request_body_over_64kb_is_413(seeded_client, trade_body):
    huge = json.dumps({**trade_body, "notes": "x" * (70 * 1024)})
    response = seeded_client.post("/api/v1/trades", data=huge, content_type="application/json")
    assert response.status_code == 413
    assert response.get_json()["error"]["code"] == "payload_too_large"


def test_notes_over_2000_characters_is_a_validation_error(seeded_client, trade_body):
    response = seeded_client.post("/api/v1/trades", json={**trade_body, "notes": "x" * 2001})
    assert response.status_code == 400
    assert "notes" in response.get_json()["error"]["details"]


def test_per_page_is_capped(seeded_client):
    assert seeded_client.get("/api/v1/trades?per_page=100").status_code == 200
    assert seeded_client.get("/api/v1/trades?per_page=1000").status_code == 400


# ---------- Headers and logging ----------


def test_api_responses_are_not_cached_and_keep_security_headers(seeded_client):
    response = seeded_client.get("/api/v1/trades")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.mimetype == "application/json"


def test_logs_never_contain_request_bodies(seeded_client, trade_body, caplog):
    with caplog.at_level(logging.INFO):
        seeded_client.post(
            "/api/v1/trades", json={**trade_body, "notes": "PRIVATE-NOTE-123", "position_size": "0"}
        )
    assert "POST /api/v1/trades -> 400 validation_error" in caplog.text
    assert "PRIVATE-NOTE-123" not in caplog.text


# ---------- Website unaffected ----------


def test_website_404_is_still_html(client):
    response = client.get("/no-such-page")
    assert response.status_code == 404 and b"Page not found" in response.data


def test_api_404_is_json(client):
    response = client.get("/api/v1/no-such-endpoint")
    assert response.status_code == 404 and response.get_json()["error"]["code"] == "not_found"


def test_unversioned_api_path_is_404_json(client):
    assert client.get("/api/trades").get_json()["error"]["code"] == "not_found"
