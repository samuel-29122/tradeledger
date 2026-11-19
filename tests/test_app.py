"""Phase 1 tests: the app starts, pages load, config is safe."""

import pytest

from tradeledger import create_app

# ---------- Application factory and configuration ----------


def test_testing_config_is_applied(app):
    assert app.config["TESTING"] is True
    assert app.config["CONFIG_NAME"] == "testing"


def test_unknown_config_name_is_rejected():
    with pytest.raises(ValueError):
        create_app("not-a-real-config")


def test_production_without_secret_key_refuses_to_start(monkeypatch):
    monkeypatch.delenv("SECRET_KEY", raising=False)
    # ProductionConfig read SECRET_KEY when config.py was imported, so
    # clear it on the class too to simulate "no key set".
    from tradeledger.config import ProductionConfig

    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", None)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app("production")


def test_production_with_secret_key_starts(monkeypatch):
    from tradeledger.config import ProductionConfig

    # Phase 7: production keys must be at least 32 characters (see
    # tests/test_security_hardening.py), so this uses a realistic 64-character key.
    key = "9f2c" * 16
    monkeypatch.setattr(ProductionConfig, "SECRET_KEY", key)
    app = create_app("production")
    assert app.config["SECRET_KEY"] == key


def test_development_without_secret_key_generates_temporary_key(monkeypatch):
    from tradeledger.config import DevelopmentConfig

    monkeypatch.setattr(DevelopmentConfig, "SECRET_KEY", None)
    app = create_app("development")
    assert len(app.config["SECRET_KEY"]) == 64  # 32 random bytes as hex


# ---------- Routes ----------


def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"TradeLedger" in response.data


def test_home_page_shows_roadmap(client):
    # Phase 5: the build roadmap moved from the home page to About.
    response = client.get("/about")
    assert b"Build progress" in response.data
    assert b"SQL database" in response.data


def test_home_page_shows_environment(client):
    response = client.get("/")
    assert b"testing" in response.data


def test_stylesheet_is_served(client):
    # `with` closes the file-backed response (pytest treats leaks as errors).
    with client.get("/static/css/style.css") as response:
        assert response.status_code == 200
        assert "text/css" in response.content_type


def test_health_endpoint_returns_json(client, app):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.is_json
    # The version is read from config rather than hard-coded, so this test
    # does not break every time the version number is bumped.
    assert response.get_json() == {
        "status": "ok",
        "app": "TradeLedger",
        "version": app.config["APP_VERSION"],
    }


def test_health_endpoint_does_not_leak_secret(client, app):
    response = client.get("/health")
    assert app.config["SECRET_KEY"].encode() not in response.data


# ---------- Error handling ----------


def test_unknown_page_returns_custom_404(client):
    response = client.get("/this-page-does-not-exist")
    assert response.status_code == 404
    assert b"Page not found" in response.data


def test_post_to_get_only_route_is_rejected(client):
    response = client.post("/")
    assert response.status_code == 405


# ---------- Security headers ----------


@pytest.mark.parametrize("path", ["/", "/health", "/missing-page"])
def test_security_headers_present(client, path):
    response = client.get(path)
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
