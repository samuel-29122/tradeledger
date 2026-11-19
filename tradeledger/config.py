"""Application configuration.

Settings are read from environment variables so that secrets never live in
source code. Each class represents one environment the app can run in:

  development  your own machine (flask run); SQLite by default
  testing      pytest; every test passes its own temporary database
  production   Gunicorn in Docker or on AWS; PostgreSQL expected

Phase 8 removed the Phase 7 switches TRADELEDGER_ENABLE_UNAUTHENTICATED_API
and TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL. Every page with trading
data now requires a signed-in user and the API requires a token, in EVERY
environment, so there is no setting that turns authentication off. If one
of those old variables is still set, the app logs a warning and ignores it.
"""

import os
from datetime import timedelta


def env_flag(name, default=False):
    """True when the variable is "1", False when it is "0", else `default`."""
    value = os.environ.get(name)
    if value == "1":
        return True
    if value == "0":
        return False
    return default


def env_int(name, default):
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def env_secret(name):
    """A secret from NAME, or from the file named by NAME_FILE.

    Docker and Compose "secrets" are mounted as files (e.g.
    /run/secrets/secret_key), which keeps them out of `docker inspect` and
    process listings. The file wins when both are set. Surrounding
    whitespace (a trailing newline) is removed.
    """
    path = os.environ.get(f"{name}_FILE")
    if path:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    return os.environ.get(name)


def env_hosts(name, default):
    """A comma-separated list of host names from the environment.

    Not set -> `default`.  "*" -> None, meaning "accept any Host header"
    (Flask's own default). Ports are ignored when hosts are compared.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    if raw.strip() == "*":
        return None
    return [host.strip().lower() for host in raw.split(",") if host.strip()]


# Values that must never be accepted as a production SECRET_KEY: the example
# in .env.example, the Compose example and the public testing key below.
PLACEHOLDER_SECRET_KEYS = frozenset(
    {
        "replace-me-with-a-long-random-string",
        "test-secret-key-not-for-real-use",
        "change-me-to-64-random-hex-characters-before-starting-the-stack",
    }
)
MIN_PRODUCTION_SECRET_KEY_LENGTH = 32

# Phase 7 settings that turned authentication off. Ignored since Phase 8.
REMOVED_UNSAFE_SETTINGS = (
    "TRADELEDGER_ENABLE_UNAUTHENTICATED_API",
    "TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL",
)


class Config:
    """Settings shared by every environment."""

    APP_NAME = "TradeLedger"
    APP_VERSION = "0.8.5"

    DEBUG = False
    TESTING = False

    # Read from the environment. Never hard-code a real key in this file.
    # It signs the session cookie, so anyone who knows it could forge a login.
    SECRET_KEY = env_secret("SECRET_KEY")

    # When True, the app refuses to start without a strong SECRET_KEY.
    REQUIRE_SECRET_KEY = True

    # ---------- Database ----------
    # SQLite file path. If neither this nor PostgreSQL is configured,
    # create_app() uses instance/tradeledger.db inside the project folder.
    DATABASE = os.environ.get("DATABASE_PATH")
    # PostgreSQL, as one URL (Compose, CI)...
    DATABASE_URL = os.environ.get("DATABASE_URL")
    # ...or as separate settings (AWS ECS: DB_PASSWORD comes from Secrets Manager).
    DB_HOST = os.environ.get("DB_HOST")
    DB_PORT = os.environ.get("DB_PORT", "5432")
    DB_NAME = os.environ.get("DB_NAME", "tradeledger")
    DB_USER = os.environ.get("DB_USER", "tradeledger")
    DB_PASSWORD = env_secret("DB_PASSWORD")
    # "require" encrypts the connection; "verify-full" also checks the server
    # certificate and needs the RDS certificate bundle (docs/PHASE_8_AWS_DEPLOYMENT.md).
    DB_SSLMODE = os.environ.get("DB_SSLMODE", "prefer")
    # Connections per process. Gunicorn workers x containers x max must stay
    # under the database's max_connections (db.t4g.micro: about 80).
    DB_POOL_MIN_SIZE = env_int("TRADELEDGER_DB_POOL_MIN", 1)
    DB_POOL_MAX_SIZE = env_int("TRADELEDGER_DB_POOL_MAX", 5)
    DB_POOL_TIMEOUT = env_int("TRADELEDGER_DB_POOL_TIMEOUT", 10)

    # Largest request body accepted, in bytes. Flask answers 413 above this.
    # A trade is well under 2 KB, so 64 KB is generous.
    MAX_CONTENT_LENGTH = 64 * 1024

    # ---------- Authentication (Phase 8) ----------
    # Argon2id cost (see passwords.py): OWASP's recommended minimum.
    PASSWORD_HASH_MEMORY_KIB = 19456
    PASSWORD_HASH_ITERATIONS = 2
    PASSWORD_HASH_LANES = 1

    # Self-service registration. On for local use. Production turns it OFF
    # by default: without email verification anyone could create accounts.
    REGISTRATION_ENABLED = env_flag("TRADELEDGER_REGISTRATION_ENABLED", True)
    REGISTRATIONS_PER_IP_PER_HOUR = 5

    # A session ends 8 hours after sign-in, or after 2 hours without activity,
    # whichever comes first. Both are checked on the server for every request.
    SESSION_MAX_AGE_SECONDS = env_int("TRADELEDGER_SESSION_MAX_AGE", 8 * 3600)
    SESSION_IDLE_TIMEOUT_SECONDS = env_int("TRADELEDGER_SESSION_IDLE_TIMEOUT", 2 * 3600)
    PERMANENT_SESSION_LIFETIME = timedelta(seconds=SESSION_MAX_AGE_SECONDS)

    # Failed sign-ins allowed per email address and per IP address in the window.
    LOGIN_MAX_FAILURES_PER_ACCOUNT = 5
    LOGIN_MAX_FAILURES_PER_IP = 20
    LOGIN_WINDOW_SECONDS = 15 * 60

    # Personal API tokens (Authorization: Bearer tl_...).
    API_ENABLED = env_flag("TRADELEDGER_API_ENABLED", True)
    API_MAX_PER_PAGE = 100
    # Phase 8.5: two-step verification and one-time password-reset links.
    TOTP_ISSUER = os.environ.get("TRADELEDGER_TOTP_ISSUER", "TradeLedger")
    PASSWORD_RESET_LIFETIME_SECONDS = 30 * 60
    # The address people use to reach TradeLedger, for links printed by the
    # create-reset-link command (e.g. https://journal.example.com).
    PUBLIC_URL = os.environ.get("TRADELEDGER_PUBLIC_URL", "http://127.0.0.1:5000")

    API_TOKEN_DEFAULT_LIFETIME_DAYS = 90
    API_TOKEN_MAX_ACTIVE = 10

    JOURNAL_PER_PAGE = 20

    # Session cookie: holds the signed-in user's id and the CSRF token, and
    # is signed with SECRET_KEY (see web/auth.py). HttpOnly stops JavaScript
    # reading it; SameSite=Lax stops browsers sending it with cross-site
    # form posts, a second layer of CSRF defence.
    SESSION_COOKIE_NAME = "tradeledger_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"

    # Host header allow-list (Phase 7). Requests addressed to any other host
    # name get 400 before any route runs, which defeats DNS rebinding.
    #   TRADELEDGER_TRUSTED_HOSTS=localhost,127.0.0.1,192.168.1.20
    TRUSTED_HOSTS = env_hosts("TRADELEDGER_TRUSTED_HOSTS", ["localhost", "127.0.0.1"])

    # Number of reverse proxies in front of the app that set X-Forwarded-*
    # headers (an AWS Application Load Balancer is 1). 0 means trust none:
    # a client could otherwise fake its IP address (defeating the login
    # limits) or claim the request used HTTPS.
    PROXY_HOPS = env_int("TRADELEDGER_PROXY_HOPS", 0)

    # Strict-Transport-Security (HSTS) tells browsers to use HTTPS only.
    HSTS_ENABLED = False

    # "text" for people, "json" for log collectors such as CloudWatch.
    LOG_FORMAT = os.environ.get("TRADELEDGER_LOG_FORMAT", "text")
    # One line per request (method, path without query string, status, time).
    ACCESS_LOG = env_flag("TRADELEDGER_ACCESS_LOG", False)

    # Chart.js for the /analytics dashboard (Phase 6). Since Phase 8.5 the
    # library is SELF-HOSTED by default: the file ships in
    # static/vendor/chartjs/ (built from the official v4.5.1 source tag; see
    # its BUILD.md) and the browser checks it against CHART_JS_BUNDLED_INTEGRITY.
    # No third-party host is contacted and the CSP stays "script-src 'self'".
    # TRADELEDGER_CHART_JS_URL can still point at a CDN (then set
    # TRADELEDGER_CHART_JS_INTEGRITY for that file too).
    CHART_JS_VERSION = "4.5.1"
    CHART_JS_BUNDLED_URL = "/static/vendor/chartjs/chart.umd.min.js"
    CHART_JS_BUNDLED_INTEGRITY = "sha384-rBeQHxx+fD4yTWwymMLsGUggrWAk1ssEcKZuxBdYt+43t//g2T2q49rj78sPe7D3"
    CHART_JS_CDN_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
    CHART_JS_URL = os.environ.get("TRADELEDGER_CHART_JS_URL") or CHART_JS_BUNDLED_URL
    CHART_JS_INTEGRITY = os.environ.get("TRADELEDGER_CHART_JS_INTEGRITY") or (
        CHART_JS_BUNDLED_INTEGRITY if CHART_JS_URL == CHART_JS_BUNDLED_URL else None
    )


class DevelopmentConfig(Config):
    """Local development on your own machine."""

    # In development a missing key is tolerated: the app generates a
    # temporary one and logs a warning (see create_app).
    REQUIRE_SECRET_KEY = False


class TestingConfig(Config):
    """Used by pytest. Fixed values make tests repeatable."""

    TESTING = True
    SECRET_KEY = "test-secret-key-not-for-real-use"  # noqa: S105 - fixed, public, tests only
    REQUIRE_SECRET_KEY = False

    # Deliberately no default: tests must pass their own temporary database,
    # so a test can never touch your development data.
    DATABASE = None
    DATABASE_URL = None
    DB_HOST = None

    # Cheap hashing keeps 1,000+ tests fast. tests/test_passwords.py checks
    # that the production settings are the OWASP values and hashes with them.
    PASSWORD_HASH_MEMORY_KIB = 1024
    PASSWORD_HASH_ITERATIONS = 1

    REGISTRATION_ENABLED = True

    # The Phase 6/7 page and browser tests exercise the CDN mode (and replace
    # the CDN file with a stub or block it to test the offline fallback).
    # The self-hosted default is tested separately: tests/test_chartjs_bundle.py
    # and the real-library tests in tests/test_chartjs_real.py.
    CHART_JS_URL = Config.CHART_JS_CDN_URL
    CHART_JS_INTEGRITY = None


class ProductionConfig(Config):
    """Deployed environments (Docker Compose, AWS ECS)."""

    REQUIRE_SECRET_KEY = True

    REGISTRATION_ENABLED = env_flag("TRADELEDGER_REGISTRATION_ENABLED", False)

    # Only send the session cookie over HTTPS. The "__Host-" prefix makes
    # browsers refuse the cookie unless it is Secure, has Path=/ and no
    # Domain, so a sibling sub-domain cannot overwrite it.
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_NAME = "__Host-tradeledger_session"
    PREFERRED_URL_SCHEME = "https"

    # Production must say which host names it serves (for example
    # "journal.example.com"). Unset means no Host check, which is only safe
    # behind a load balancer that already enforces it (see SECURITY.md).
    TRUSTED_HOSTS = env_hosts("TRADELEDGER_TRUSTED_HOSTS", None)

    HSTS_ENABLED = True
    LOG_FORMAT = os.environ.get("TRADELEDGER_LOG_FORMAT", "json")
    ACCESS_LOG = env_flag("TRADELEDGER_ACCESS_LOG", True)


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}
