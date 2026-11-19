"""TradeLedger application package.

Contains the application factory, create_app(). Flask's command-line tool
finds this function automatically when you run:  flask --app tradeledger run
"""

import os
import secrets

from flask import Flask, g, render_template
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import (
    CONFIG_BY_NAME,
    MIN_PRODUCTION_SECRET_KEY_LENGTH,
    PLACEHOLDER_SECRET_KEYS,
    REMOVED_UNSAFE_SETTINGS,
)


def create_app(config_name=None, overrides=None):
    """Build and return a configured Flask application.

    config_name: "development", "testing" or "production". If omitted, it is
    read from the TRADELEDGER_CONFIG environment variable, defaulting to
    "development".

    overrides: optional dict of settings applied last. Tests use this to
    point the app at a temporary database, e.g. {"DATABASE": "/tmp/x.db"}.
    """
    if config_name is None:
        config_name = os.environ.get("TRADELEDGER_CONFIG", "development")

    if config_name not in CONFIG_BY_NAME:
        valid = ", ".join(sorted(CONFIG_BY_NAME))
        raise ValueError(f"Unknown config '{config_name}'. Choose one of: {valid}")

    app = Flask(__name__)
    app.config.from_object(CONFIG_BY_NAME[config_name])
    if overrides:
        app.config.update(overrides)
    app.config["CONFIG_NAME"] = config_name

    _configure_logging(app)
    _ensure_secret_key(app)
    _refuse_unsafe_production_settings(app)
    _configure_proxy(app)
    _configure_database(app)
    _register_blueprints(app)
    _register_error_handlers(app)
    _register_security_headers(app)

    from .db import database_label

    app.logger.info(
        "TradeLedger %s started",
        app.config["APP_VERSION"],
        extra={"event": "app.started", "config": config_name, "database": database_label(app)},
    )
    return app


def _configure_logging(app):
    """Structured, redacted logging (see observability.py)."""
    from .observability import configure_logging, register_request_hooks

    configure_logging(app)
    register_request_hooks(app)


def _ensure_secret_key(app):
    """Make sure a SECRET_KEY exists, or fail loudly where one is required.

    Where a key is required (production), it must also be strong: at least
    32 characters and not one of the published placeholder values. The key
    signs the session cookie that holds the signed-in user's id and the
    CSRF token, so a guessable key would let an attacker sign in as anyone.
    """
    key = app.config.get("SECRET_KEY")
    weak = key in PLACEHOLDER_SECRET_KEYS or len(key or "") < MIN_PRODUCTION_SECRET_KEY_LENGTH
    if key and app.config["REQUIRE_SECRET_KEY"] and weak:
        raise RuntimeError(
            "SECRET_KEY is too weak for this environment. Use at least "
            f"{MIN_PRODUCTION_SECRET_KEY_LENGTH} random characters, for example the output of: "
            'python -c "import secrets; print(secrets.token_hex(32))"'
        )
    if key:
        return

    if app.config["REQUIRE_SECRET_KEY"]:
        raise RuntimeError(
            "SECRET_KEY is not set. Set the SECRET_KEY environment variable "
            "before running TradeLedger in this environment."
        )

    # Development only: a random key that changes on every restart.
    app.config["SECRET_KEY"] = secrets.token_hex(32)
    app.logger.warning(
        "SECRET_KEY not set - using a temporary development key. "
        "Set SECRET_KEY in your .env file to silence this warning."
    )


def _refuse_unsafe_production_settings(app):
    """Phase 8: settings that must never reach a production server.

    * The Phase 7 switches that disabled authentication no longer exist.
      If one is still set, it is IGNORED and a warning is logged, so an old
      .env file cannot silently reopen the journal.
    * The Werkzeug debugger runs arbitrary Python from the browser; it must
      never be on in production, so the app refuses to start.
    """
    for name in REMOVED_UNSAFE_SETTINGS:
        if name in os.environ:
            app.logger.warning(
                "%s is no longer supported and is ignored: TradeLedger always requires sign-in.",
                name,
                extra={"event": "config.removed_setting"},
            )
    if app.config["CONFIG_NAME"] == "production" and (app.debug or os.environ.get("FLASK_DEBUG") == "1"):
        raise RuntimeError("Debug mode must not be enabled in production (it allows remote code execution).")


def _configure_proxy(app):
    """Trust X-Forwarded-For/-Proto from exactly PROXY_HOPS proxies.

    Behind an AWS Application Load Balancer set TRADELEDGER_PROXY_HOPS=1:
    request.remote_addr then becomes the real client address (used by the
    sign-in limits) and request.is_secure reflects the original HTTPS
    connection. With 0 (the default) these headers are ignored, because
    anyone can send them. X-Forwarded-Host is never trusted: the Host
    header is checked against TRUSTED_HOSTS instead.
    """
    hops = app.config["PROXY_HOPS"]
    if hops:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=0, x_port=0, x_prefix=0)


def _configure_database(app):
    """Choose SQLite or PostgreSQL (see db.make_source) and register hooks."""
    uses_postgres = bool(app.config.get("DATABASE_URL") or app.config.get("DB_HOST"))
    if not uses_postgres and not app.config.get("DATABASE"):
        if app.config["TESTING"]:
            raise RuntimeError(
                "The testing configuration needs an explicit DATABASE path "
                "so tests never touch development data."
            )
        os.makedirs(app.instance_path, exist_ok=True)
        app.config["DATABASE"] = os.path.join(app.instance_path, "tradeledger.db")

    from . import cli, db

    db.init_app(app)
    cli.init_app(app)


def _register_blueprints(app):
    """Attach groups of routes to the app: the website and the JSON API."""
    from .api import api_bp
    from .web import main_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(api_bp)


def _register_error_handlers(app):
    """Friendly errors instead of Flask's defaults or stack traces.

    Website URLs get HTML pages (Phase 1). URLs under /api/ get the JSON
    error format, including for errors raised before any API code runs:
    unknown URL (404), wrong method (405) and oversized body (413).
    """
    from .api.errors import HTTP_ERRORS, error_response, is_api_request

    @app.errorhandler(404)
    def not_found(error):
        if is_api_request():
            return error_response(404, *HTTP_ERRORS[404])
        return render_template("errors/404.html"), 404

    @app.errorhandler(405)
    def method_not_allowed(error):
        if is_api_request():
            response, status = error_response(405, *HTTP_ERRORS[405])
            response.headers["Allow"] = ", ".join(error.valid_methods or [])
            return response, status
        return error  # website: Flask's standard 405 response

    @app.errorhandler(413)
    def too_large(error):
        if is_api_request():
            return error_response(413, *HTTP_ERRORS[413])
        return error

    @app.errorhandler(500)
    def internal_error(error):
        # The full traceback goes to the server log only, never to the client.
        app.logger.exception("Unhandled server error")
        if is_api_request():
            return error_response(500, *HTTP_ERRORS[500])
        return render_template("errors/500.html"), 500

    @app.errorhandler(HTTPException)
    def other_http_error(error):
        if is_api_request():
            return error_response(error.code, "http_error", error.name)
        return error


# Content Security Policy: the browser may only load scripts, styles and
# images from this site, and forms may only post back to it. Templates
# therefore contain no inline <script> or style="" attributes, which means
# even an HTML-injection bug could not run injected JavaScript.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "form-action 'self'; frame-ancestors 'none'; base-uri 'self'; object-src 'none'"
)


def build_csp(extra_script_sources=()):
    """The policy above, optionally allowing extra script URLs.

    Phase 6: only the /analytics response passes an extra source, the
    exact Chart.js file URL (a full path, not the whole CDN host), so no
    other page can load anything from the CDN, and 'unsafe-inline' and
    'unsafe-eval' are never allowed anywhere.
    """
    if not extra_script_sources:
        return CONTENT_SECURITY_POLICY
    scripts = " ".join(("'self'", *extra_script_sources))
    return CONTENT_SECURITY_POLICY.replace("script-src 'self'", f"script-src {scripts}", 1)


def _register_security_headers(app):
    """Add protective HTTP headers to every response."""

    @app.after_request
    def set_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        # Pages that handle secrets in their own URL (the reset link) send no
        # Referer at all (Phase 8.5).
        response.headers["Referrer-Policy"] = g.get("referrer_policy", "strict-origin-when-cross-origin")
        response.headers["Content-Security-Policy"] = build_csp(g.get("csp_script_sources", ()))
        # Pages show trading records, so browsers and proxies must not store
        # them (Phase 7). Static CSS/JS are not HTML and stay cacheable; the
        # JSON API already sets its own no-store header.
        if response.mimetype == "text/html" and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        if app.config["HSTS_ENABLED"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response
