"""Phase 8: production configuration, proxies, logging and health checks."""

import io
import json
import logging

import pytest

from tests.conftest import ensure_user
from tradeledger import create_app
from tradeledger.db import build_conninfo, get_db, init_schema, redact_url
from tradeledger.observability import JsonFormatter, RedactingFilter, redact

STRONG_KEY = "ab" * 32


def production_app(db_path, **overrides):
    app = create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path, **overrides})
    with app.app_context():
        init_schema(get_db())
        ensure_user(get_db())
    return app


# ---------- Production settings ----------


def test_production_refuses_debug_mode(db_path, monkeypatch):
    monkeypatch.setenv("FLASK_DEBUG", "1")
    with pytest.raises(RuntimeError, match="Debug mode must not be enabled in production"):
        create_app("production", overrides={"SECRET_KEY": STRONG_KEY, "DATABASE": db_path})


def test_production_defaults(db_path):
    config = production_app(db_path).config
    assert config["DEBUG"] is False
    assert config["SESSION_COOKIE_SECURE"] is True
    assert config["SESSION_COOKIE_HTTPONLY"] is True
    assert config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert config["SESSION_COOKIE_NAME"].startswith("__Host-")
    assert config["REGISTRATION_ENABLED"] is False
    assert config["HSTS_ENABLED"] is True
    assert config["PROXY_HOPS"] == 0  # forwarded headers are NOT trusted unless configured
    assert config["LOG_FORMAT"] == "json"


def test_compose_example_secret_is_refused(db_path):
    with pytest.raises(RuntimeError, match="SECRET_KEY is too weak"):
        create_app(
            "production",
            overrides={
                "SECRET_KEY": "change-me-to-64-random-hex-characters-before-starting-the-stack",
                "DATABASE": db_path,
            },
        )


def test_errors_never_show_stack_traces_or_settings(db_path):
    app = production_app(db_path, PROPAGATE_EXCEPTIONS=False)

    @app.get("/boom")
    def boom():
        raise RuntimeError("internal detail " + app.config["SECRET_KEY"])

    response = app.test_client().get("/boom", base_url="https://localhost")
    assert response.status_code == 500
    body = response.get_data(as_text=True)
    for leak in ("Traceback", "internal detail", STRONG_KEY, "DATABASE"):
        assert leak not in body


# ---------- Reverse proxy headers ----------


def remote_addr_and_scheme(app, headers):
    @app.get("/whoami-test")
    def whoami():
        from flask import request

        return {"ip": request.remote_addr, "secure": request.is_secure, "host": request.host}

    return (
        app.test_client()
        .get("/whoami-test", headers=headers, environ_base={"REMOTE_ADDR": "10.0.0.5"})
        .get_json()
    )


SPOOFED = {"X-Forwarded-For": "203.0.113.9", "X-Forwarded-Proto": "https", "X-Forwarded-Host": "evil.example"}


def test_forwarded_headers_are_ignored_by_default(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path})
    seen = remote_addr_and_scheme(app, SPOOFED)
    assert seen == {"ip": "10.0.0.5", "secure": False, "host": "localhost"}


def test_one_trusted_proxy_hop(db_path):
    app = create_app("testing", overrides={"DATABASE": db_path, "PROXY_HOPS": 1})
    seen = remote_addr_and_scheme(app, SPOOFED)
    assert seen["ip"] == "203.0.113.9" and seen["secure"] is True
    assert seen["host"] == "localhost"  # X-Forwarded-Host is never trusted


def test_only_the_last_hop_is_trusted(db_path):
    """A client can prepend fake addresses; with 1 hop only the address the
    load balancer appended (the last one) is used."""
    app = create_app("testing", overrides={"DATABASE": db_path, "PROXY_HOPS": 1})
    seen = remote_addr_and_scheme(app, {"X-Forwarded-For": "1.2.3.4, 198.51.100.7"})
    assert seen["ip"] == "198.51.100.7"


# ---------- Health checks ----------


def test_liveness_and_readiness(anon_client):
    assert anon_client.get("/health/live").get_json() == {"status": "ok"}
    assert anon_client.get("/health/ready").get_json() == {"status": "ready"}


def test_readiness_fails_without_the_database_and_reveals_nothing(tmp_path, caplog):
    app = create_app("testing", overrides={"DATABASE": str(tmp_path / "missing-dir" / "db.sqlite")})
    with caplog.at_level(logging.ERROR):
        response = app.test_client().get("/health/ready")
    assert response.status_code == 503
    assert response.get_json() == {"status": "unavailable"}
    assert "missing-dir" not in response.get_data(as_text=True)
    assert "readiness check failed" in caplog.text  # the detail is in the server log
    assert app.test_client().get("/health/live").status_code == 200  # liveness unaffected


# ---------- Logging ----------


def test_request_id_is_returned_and_unsafe_ids_are_replaced(anon_client):
    good = anon_client.get("/health/live", headers={"X-Request-ID": "alb-trace-1234"})
    assert good.headers["X-Request-ID"] == "alb-trace-1234"
    bad = anon_client.get("/health/live", headers={"X-Request-ID": "<script>alert(1)</script>"})
    assert bad.headers["X-Request-ID"] != "<script>alert(1)</script>"
    assert len(bad.headers["X-Request-ID"]) == 32


@pytest.mark.parametrize(
    "text, secret",
    [
        ("token tl_AbCdEfGhIjKlMnOpQrStUvWxYz0123456789", "AbCdEfGhIjKl"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ("connect postgresql://app:Sup3rS3cret@db:5432/x", "Sup3rS3cret"),
        ("password=hunter2hunter2 next", "hunter2hunter2"),
        ("SECRET_KEY: 'deadbeefdeadbeef'", "deadbeefdeadbeef"),
        # Phase 8.5: password-reset links, wherever they appear in a message
        ("link https://x.example/reset-password/tlr_QwErTyUiOpAsDfGhJkLzXcVbNm012345", "QwErTyUiOpAsDf"),
        ("pasted tlr_QwErTyUiOpAsDfGhJkLzXcVbNm012345 into chat", "QwErTyUiOpAsDf"),
    ],
)
def test_redaction(text, secret):
    assert secret not in redact(text)


def test_json_log_line_is_redacted_and_structured():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    logger = logging.getLogger("tradeledger.test-json")
    logger.addHandler(handler)
    logger.propagate = False
    try:
        logger.warning(
            "used tl_0123456789abcdefghijKLMNOP", extra={"event": "x", "url": "postgres://u:pw1234@h/db"}
        )
    finally:
        logger.removeHandler(handler)
    entry = json.loads(stream.getvalue())
    assert entry["level"] == "WARNING" and entry["event"] == "x"
    assert "0123456789abcdef" not in stream.getvalue() and "pw1234" not in stream.getvalue()


def test_access_log_has_no_query_string_or_notes(db_path, caplog):
    app = create_app("testing", overrides={"DATABASE": db_path, "ACCESS_LOG": True})
    with app.app_context():
        init_schema(get_db())
    with caplog.at_level(logging.INFO, logger="tradeledger.access"):
        app.test_client().get("/login?next=/trades?q=MY-PRIVATE-NOTE")
    records = [r for r in caplog.records if r.name == "tradeledger.access"]
    assert records and records[-1].path == "/login" and records[-1].status == 200
    assert "MY-PRIVATE-NOTE" not in caplog.text


def test_database_url_is_logged_without_password(tmp_path, caplog):
    assert redact_url("postgresql://app:pw@db:5432/tl") == "postgresql://app:***@db:5432/tl"
    assert redact_url("postgresql://app@db/tl") == "postgresql://app@db/tl"


def test_conninfo_quotes_every_value():
    info = build_conninfo("db.internal", "5432", "tradeledger", "app", "p'w d\\x", "require")
    assert "password='p\\'w d\\\\x'" in info
    assert "sslmode='require'" in info


# ---------- Security headers still apply to the new pages ----------


@pytest.mark.parametrize("url", ["/login", "/register", "/health/ready"])
def test_new_pages_keep_the_security_headers(anon_client, url):
    response = anon_client.get(url)
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_secrets_can_come_from_mounted_files(tmp_path, monkeypatch):
    """Docker/Compose secrets are files; NAME_FILE wins over NAME."""
    from tradeledger.config import env_secret

    secret_file = tmp_path / "secret_key.txt"
    secret_file.write_text("from-the-file\n", encoding="utf-8")
    monkeypatch.setenv("TL_TEST_SECRET", "from-the-environment")
    assert env_secret("TL_TEST_SECRET") == "from-the-environment"
    monkeypatch.setenv("TL_TEST_SECRET_FILE", str(secret_file))
    assert env_secret("TL_TEST_SECRET") == "from-the-file"  # trailing newline removed
    monkeypatch.delenv("TL_TEST_SECRET")
    monkeypatch.delenv("TL_TEST_SECRET_FILE")
    assert env_secret("TL_TEST_SECRET") is None
