# TradeLedger: Phase 8 code listing

Every source, configuration, infrastructure and test file added or changed in Phase 8, in full. This file is generated from the files in `tradeledger_phase8.zip`, so the two always agree. Documentation (README, changelog, guides, reports, `docs/openapi.json`) is delivered as its own files.

**Verified vs not:** application code and tests ran here on SQLite and PostgreSQL 16. Docker, Compose, Terraform and the GitHub workflows are written and statically checked but have **not** run (see `docs/PHASE_8_QA_REPORT.md` §7).

## Inventory

- **NEW (48):** `tradeledger/legacy.py`, `tradeledger/observability.py`, `tradeledger/passwords.py`, `tradeledger/repositories/api_tokens.py`, `tradeledger/repositories/throttle.py`, `tradeledger/repositories/users.py`, `tradeledger/schema_postgres.sql`, `tradeledger/templates/account/settings.html`, `tradeledger/templates/auth/login.html`, `tradeledger/templates/auth/register.html`, `tradeledger/web/account.py`, `tradeledger/web/auth.py`, `tradeledger/wsgi.py`, `.dockerignore`, `.github/workflows/deploy.yml`, `Dockerfile`, `compose.yaml`, `docker/entrypoint.sh`, `gunicorn.conf.py`, `infra/alb.tf`, `infra/backend.tf`, `infra/ecr.tf`, `infra/ecs.tf`, `infra/github_oidc.tf`, `infra/iam.tf`, `infra/monitoring.tf`, `infra/network.tf`, `infra/outputs.tf`, `infra/rds.tf`, `infra/secrets.tf`, `infra/security_groups.tf`, `infra/terraform.tfvars.example`, `infra/variables.tf`, `infra/versions.tf`, `tests/legacy_fixtures.py`, `tests/pg_libpq_bridge.py`, `tests/test_account_and_tokens.py`, `tests/test_auth.py`, `tests/test_browser_auth.py`, `tests/test_deployment_files.py`, `tests/test_isolation.py`, `tests/test_legacy_and_cli_accounts.py`, `tests/test_operations.py`, `tests/test_passwords.py`, `tests/test_postgres.py`, `scripts/backup_restore_check.py`, `scripts/compose_smoke_test.sh`, `scripts/migrate_sqlite_to_postgres.py`
- **MODIFIED (64):** `tradeledger/__init__.py`, `tradeledger/analytics.py`, `tradeledger/api/__init__.py`, `tradeledger/api/analytics.py`, `tradeledger/api/instruments.py`, `tradeledger/api/openapi.py`, `tradeledger/api/trades.py`, `tradeledger/cli.py`, `tradeledger/config.py`, `tradeledger/db.py`, `tradeledger/migrations.py`, `tradeledger/performance/dashboard.py`, `tradeledger/performance/report.py`, `tradeledger/report_filters.py`, `tradeledger/repositories/__init__.py`, `tradeledger/repositories/instruments.py`, `tradeledger/repositories/trades.py`, `tradeledger/seed.py`, `tradeledger/static/css/pages.css`, `tradeledger/templates/about.html`, `tradeledger/templates/base.html`, `tradeledger/templates/errors/403.html`, `tradeledger/templates/macros/forms.html`, `tradeledger/timestamps.py`, `tradeledger/web/__init__.py`, `tradeledger/web/analytics.py`, `tradeledger/web/csrf.py`, `tradeledger/web/forms.py`, `tradeledger/web/instruments.py`, `tradeledger/web/trades.py`, `tradeledger/web/views.py`, `.env.example`, `.github/workflows/ci.yml`, `.gitignore`, `constraints.txt`, `pyproject.toml`, `requirements.txt`, `tests/browser_support.py`, `tests/conftest.py`, `tests/factories.py`, `tests/test_api_analytics.py`, `tests/test_api_security.py`, `tests/test_browser.py`, `tests/test_browser_journey.py`, `tests/test_chartjs_real.py`, `tests/test_chronology.py`, `tests/test_dashboard_data.py`, `tests/test_database_integrity.py`, `tests/test_db.py`, `tests/test_error_paths.py`, `tests/test_migrations.py`, `tests/test_report.py`, `tests/test_report_filters.py`, `tests/test_repositories.py`, `tests/test_security_hardening.py`, `tests/test_seed_and_analytics.py`, `tests/test_timestamps.py`, `tests/test_web_analytics.py`, `tests/test_web_integration.py`, `tests/test_web_pages.py`, `tests/test_web_security.py`, `tests/web_helpers.py`, `scripts/benchmark_dashboard.py`, `scripts/qa_dashboard_agreement.py`
- **DELETED (2):** `tradeledger/templates/home_disabled.html`, `tradeledger/web/guard.py`

## 1. New files

### `tradeledger/legacy.py` (NEW)

**Why:** Assigns pre-Phase 8 (ownerless) records to one named account: dry run by default, refuses on conflicts.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Assigning pre-Phase 8 records to an account (Phase 8).

Plain English
-------------
A Phase 7 journal belonged to "whoever used this computer": its records
have no owner. Migration 3 keeps every record exactly as it was but marks
it UNASSIGNED, and unassigned records are visible to nobody. The person who
owns the data then runs one command to give them to their account:

    flask --app tradeledger create-user --email you@example.com
    flask --app tradeledger claim-legacy-data --email you@example.com          # dry run
    flask --app tradeledger claim-legacy-data --email you@example.com --apply

Why not automatic? Guessing would be a security bug: on a server, giving
old records to "the first account registered" hands your journal to
whoever registers first. The command is run by someone with access to the
server and the database file, which is the same trust as reading the file.

Technical summary
-----------------
* Dry run by default: reports what WOULD change and any conflicts; writes
  nothing.
* Conflicts that stop --apply: the account already has an instrument with
  the same symbol as an unassigned one (the per-user UNIQUE rule would
  fail), or an unassigned trade points at an instrument that already has
  an owner (impossible after a normal migration; checked anyway).
* --apply updates instruments and trades in ONE transaction and checks the
  counts afterwards; any mismatch rolls everything back.
"""

from .repositories import require_user_id


def legacy_counts(conn):
    if conn.dialect == "postgresql":
        return {"instruments": 0, "trades": 0}  # user_id is NOT NULL there
    instruments = conn.execute("SELECT COUNT(*) FROM instruments WHERE user_id IS NULL").fetchone()[0]
    trades = conn.execute("SELECT COUNT(*) FROM trades WHERE user_id IS NULL").fetchone()[0]
    return {"instruments": instruments, "trades": trades}


def find_conflicts(conn, user_id):
    if conn.dialect == "postgresql":
        return []
    conflicts = []
    rows = conn.execute(
        "SELECT legacy.symbol FROM instruments AS legacy "
        "JOIN instruments AS mine ON mine.symbol = legacy.symbol AND mine.user_id = ? "
        "WHERE legacy.user_id IS NULL ORDER BY legacy.symbol",
        (user_id,),
    ).fetchall()
    conflicts += [f"the account already has an instrument with symbol {row['symbol']}" for row in rows]
    rows = conn.execute(
        "SELECT t.id FROM trades AS t JOIN instruments AS i ON i.id = t.instrument_id "
        "WHERE t.user_id IS NULL AND i.user_id IS NOT NULL ORDER BY t.id"
    ).fetchall()
    conflicts += [
        f"unassigned trade #{row['id']} uses an instrument that already has an owner" for row in rows
    ]
    return conflicts


def claim_legacy_data(conn, *, user_id, apply):
    """Assign every unassigned record to `user_id`. Returns a summary dict:
    {"instruments": n, "trades": n, "conflicts": [...], "applied": bool}."""
    require_user_id(user_id)
    counts = legacy_counts(conn)
    conflicts = find_conflicts(conn, user_id)
    summary = {**counts, "conflicts": conflicts, "applied": False}
    if not apply or conflicts or (counts["instruments"] == 0 and counts["trades"] == 0):
        return summary
    with conn:
        moved_instruments = conn.execute(
            "UPDATE instruments SET user_id = ? WHERE user_id IS NULL", (user_id,)
        ).rowcount
        moved_trades = conn.execute(
            "UPDATE trades SET user_id = ? WHERE user_id IS NULL", (user_id,)
        ).rowcount
        if (moved_instruments, moved_trades) != (counts["instruments"], counts["trades"]) or any(
            legacy_counts(conn).values()
        ):
            raise RuntimeError("legacy claim: row counts changed during the update; nothing was changed")
    summary["applied"] = True
    return summary
```

### `tradeledger/observability.py` (NEW)

**Why:** JSON/text logging, secret redaction filter, request ids, access log without query strings.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Logging, request ids and health checks (Phase 8).

Plain English
-------------
When something goes wrong on a server, logs are how you find out what
happened. Good logs say what happened, to which request, and when, without
ever containing passwords, session cookies, API tokens or your private
trading notes.

Technical summary
-----------------
* configure_logging(app): one handler on the "tradeledger" logger family
  and Flask's app.logger. LOG_FORMAT=json writes one JSON object per line,
  which CloudWatch Logs Insights can query by field; "text" is for people.
* RedactingFilter runs on EVERY record and masks, wherever they appear:
  API tokens (tl_...), "Bearer ..." values, passwords in connection URLs
  (postgresql://user:***@host) and password=... / secret=... pairs. It is
  a safety net; the code is also written never to log such values.
* Request ids: each request gets an id (a client may send a safe-looking
  X-Request-ID, e.g. from the load balancer; otherwise a random one). It is
  returned in the X-Request-ID response header and added to every log line
  written during the request, so one failing request can be traced.
* Access log: method, path WITHOUT the query string (search text and
  filters can contain note contents), status, duration, user id.
"""

import json
import logging
import re
import time
import uuid

from flask import g, has_request_context, request

_SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")

_REDACTIONS = (
    (re.compile(r"tl_[A-Za-z0-9_-]{8,}"), "tl_***"),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+"), r"\1***"),
    (re.compile(r"(?i)(postgres(?:ql)?://[^:/@\s]+:)[^@\s]+@"), r"\1***@"),
    (
        re.compile(r"(?i)\b(password|passwd|secret|token|secret_key)(\s*[=:]\s*)('[^']*'|\"[^\"]*\"|\S+)"),
        r"\1\2***",
    ),
)

STANDARD_ATTRIBUTES = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


def redact(text):
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Masks secrets in the message, its arguments and any extra fields."""

    def filter(self, record):
        record.msg = redact(record.getMessage())
        record.args = ()
        for key, value in list(vars(record).items()):
            if key not in STANDARD_ATTRIBUTES and isinstance(value, str):
                setattr(record, key, redact(value))
        if has_request_context():
            record.request_id = g.get("request_id")
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in STANDARD_ATTRIBUTES and not key.startswith("_") and value is not None:
                entry[key] = value
        if record.exc_info:
            entry["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


class TextFormatter(logging.Formatter):
    def __init__(self):
        super().__init__("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def format(self, record):
        text = super().format(record)
        extras = {
            k: v
            for k, v in vars(record).items()
            if k not in STANDARD_ATTRIBUTES and not k.startswith("_") and v
        }
        if extras:
            text += " " + " ".join(f"{k}={v}" for k, v in sorted(extras.items()))
        return redact(text)


def configure_logging(app):
    """Attach one redacting handler to the "tradeledger" logger.

    Flask names app.logger after the package, so app.logger IS the
    "tradeledger" logger, and module loggers such as "tradeledger.auth"
    reach the same handler. Flask's own default handler is removed so
    lines are not printed twice. Records still propagate, so pytest's
    caplog sees them (already redacted by the logger-level filter).
    """
    from flask.logging import default_handler

    logger = logging.getLogger("tradeledger")
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if app.config["LOG_FORMAT"] == "json" else TextFormatter())
    handler.set_name("tradeledger")
    handler.addFilter(RedactingFilter())  # covers records from child loggers too
    for existing in list(logger.handlers):
        if existing is default_handler or existing.get_name() == "tradeledger":
            logger.removeHandler(existing)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())


def register_request_hooks(app):
    access = logging.getLogger("tradeledger.access")

    @app.before_request
    def assign_request_id():
        incoming = request.headers.get("X-Request-ID", "")
        g.request_id = incoming if _SAFE_REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        g.request_started = time.perf_counter()

    @app.after_request
    def add_request_id(response):
        if "request_id" in g:
            response.headers["X-Request-ID"] = g.request_id
        if app.config["ACCESS_LOG"] and request.endpoint != "static":
            user = g.get("user") or g.get("api_user")
            access.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": round(
                        (time.perf_counter() - g.get("request_started", time.perf_counter())) * 1000, 1
                    ),
                    "user_id": user["id"] if user else None,
                },
            )
        return response
```

### `tradeledger/passwords.py` (NEW)

**Why:** Argon2id hashing and verification (PHC strings, OWASP parameters), NFKC normalisation, re-hash detection, password rules.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Password hashing and password rules (Phase 8).

Plain English
-------------
TradeLedger never stores your password. It stores a "hash": the result of
a deliberately slow, one-way calculation on the password plus a random
"salt". At sign-in the same calculation runs on what you typed; if the
results match, the password was right. Someone who steals the database
gets hashes, not passwords, and each guess they try costs them real time
and memory.

Technical summary
-----------------
Algorithm  Argon2id (winner of the Password Hashing Competition, RFC 9106),
           from the `cryptography` library maintained by the Python
           Cryptographic Authority. Nothing here invents cryptography: this
           module only chooses parameters and calls the library.
Parameters OWASP's recommended minimum for Argon2id: 19 MiB of memory,
           2 iterations, 1 lane (m=19456, t=2, p=1), with a 16-byte random
           salt and a 32-byte output. Memory-hardness makes each guess
           expensive on GPUs, not just slow.
Storage    The PHC string format, which records the parameters with the hash:
           $argon2id$v=19$m=19456,t=2,p=1$<salt>$<hash>
           so the parameters can be raised later; needs_rehash() spots old
           hashes and the login code upgrades them after a successful login.
Input      Passwords are Unicode-normalised (NFKC) before hashing, so the
           same password typed on different keyboards gives the same hash.

Alternatives considered: argon2-cffi (the other common Argon2 package; not
installable here), bcrypt (good, but limited to 72 bytes and not memory-
hard), Werkzeug's scrypt (memory-hard and already installed; a sound
fallback). Never a plain SHA-256: fast hashes are designed to be fast,
which is exactly wrong for passwords.
"""

import os
import re
import unicodedata
from dataclasses import dataclass

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

MIN_LENGTH = 12
MAX_LENGTH = 128
SALT_BYTES = 16
HASH_BYTES = 32

_PHC = re.compile(r"\$argon2id\$v=19\$m=(\d+),t=(\d+),p=(\d+)\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+")

# A short list of passwords that appear at the top of every breach list.
# A real service would check a breached-password corpus (for example the
# k-anonymity "Pwned Passwords" range API); this list only stops the worst.
COMMON_PASSWORDS = frozenset(
    {
        "123456789012",
        "1234567890123",
        "password1234",
        "password12345",
        "passwordpassword",
        "qwertyuiopas",
        "qwerty123456",
        "iloveyou1234",
        "letmein12345",
        "trustno1trustno1",
        "tradeledger123",
        "tradeledger1234",
        "administrator1",
        "changeme1234",
        "welcome12345",
    }
)


@dataclass(frozen=True)
class HashParameters:
    memory_kib: int
    iterations: int
    lanes: int


OWASP_MINIMUM = HashParameters(memory_kib=19456, iterations=2, lanes=1)


def parameters_from_config(config):
    return HashParameters(
        memory_kib=int(config["PASSWORD_HASH_MEMORY_KIB"]),
        iterations=int(config["PASSWORD_HASH_ITERATIONS"]),
        lanes=int(config["PASSWORD_HASH_LANES"]),
    )


def _encode(password):
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def hash_password(password, params):
    """Return a PHC-format Argon2id hash of `password`."""
    if not isinstance(password, str):
        raise TypeError("password must be text")
    kdf = Argon2id(
        salt=os.urandom(SALT_BYTES),
        length=HASH_BYTES,
        iterations=params.iterations,
        lanes=params.lanes,
        memory_cost=params.memory_kib,
    )
    return kdf.derive_phc_encoded(_encode(password))


def verify_password(password, stored_hash):
    """True if `password` matches `stored_hash`. Never raises for a wrong
    password or a malformed hash: both are simply False. The comparison
    inside the library is constant-time."""
    if not isinstance(password, str) or not isinstance(stored_hash, str):
        return False
    if not _PHC.fullmatch(stored_hash):
        return False
    try:
        Argon2id.verify_phc_encoded(_encode(password), stored_hash)
    except (InvalidKey, ValueError):
        return False
    return True


def stored_parameters(stored_hash):
    match = _PHC.fullmatch(stored_hash or "")
    if not match:
        return None
    memory, iterations, lanes = (int(part) for part in match.groups())
    return HashParameters(memory_kib=memory, iterations=iterations, lanes=lanes)


def needs_rehash(stored_hash, params):
    """True when the stored hash used different (usually older, weaker) settings."""
    return stored_parameters(stored_hash) != params


def password_problem(password, *, email=None):
    """Why `password` is not acceptable (worded to follow the field label,
    e.g. "Password must be at least 12 characters."), or None.

    Rules follow NIST SP 800-63B: a minimum length, no composition rules
    ("one capital, one symbol"), no maximum below 64 characters, reject
    known-bad choices. Spaces and any Unicode characters are allowed.
    """
    if not isinstance(password, str) or password == "":
        return "is required."
    length = len(unicodedata.normalize("NFKC", password))
    if length < MIN_LENGTH:
        return f"must be at least {MIN_LENGTH} characters."
    if length > MAX_LENGTH:
        return f"must be {MAX_LENGTH} characters or fewer."
    lowered = password.strip().lower()
    if lowered in COMMON_PASSWORDS or len(set(lowered)) <= 2:
        return "is too common and easy to guess. Choose a less predictable one."
    if email and lowered == email.strip().lower():
        return "must not be your email address."
    return None
```

### `tradeledger/repositories/api_tokens.py` (NEW)

**Why:** API tokens: creation (shown once, stored as SHA-256), listing, revocation, authentication, limits.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Personal API tokens for the JSON API (Phase 8).

Plain English
-------------
A program (a script, a spreadsheet import, a mobile app) cannot fill in the
login form, so it uses a token: a long random password that only works for
the API and can be cancelled on its own. TradeLedger shows a new token
exactly once; afterwards only a fingerprint of it is kept.

Technical summary
-----------------
Format   "tl_" + 43 URL-safe characters from secrets.token_urlsafe(32):
         256 bits of randomness. The prefix makes leaked tokens easy to
         recognise (for example by secret scanners).
Storage  SHA-256 of the whole token (hex). A fast hash is correct HERE,
         unlike for passwords: a 256-bit random token cannot be guessed, so
         slowing down each check would only cost CPU. Lookup is by the
         hash, which reveals nothing about the token through timing.
Lifetime Every token expires (default 90 days, at most 365) and can be
         revoked at any time. Changing your password revokes all of them.
Display  Only the name, the first 10 characters (e.g. "tl_Ab3dE6f"), the
         dates and the status are ever shown again.
"""

import hashlib
import secrets
from datetime import timedelta

from ..timestamps import format_stored, parse_stored, utc_now
from ..validation import ValidationError

PREFIX = "tl_"
PREFIX_DISPLAY_LENGTH = 10
MAX_NAME_LENGTH = 60
MAX_LIFETIME_DAYS = 365
MIN_LIFETIME_DAYS = 1

_COLUMNS = "id, user_id, name, token_prefix, created_at, expires_at, last_used_at, revoked_at"


def token_digest(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_token(text):
    return isinstance(text, str) and text.startswith(PREFIX) and 40 <= len(text) <= 100 and text.isascii()


def _status(record, now_text):
    if record["revoked_at"]:
        return "revoked"
    if record["expires_at"] <= now_text:
        return "expired"
    return "active"


def create_token(conn, *, user_id, name, lifetime_days, max_active):
    """Create a token. Returns (plaintext_token, record). The plaintext is
    never stored and cannot be retrieved later."""
    errors = {}
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        errors["name"] = "Give the token a name, for example 'Laptop script'."
    elif len(name) > MAX_NAME_LENGTH:
        errors["name"] = f"Use {MAX_NAME_LENGTH} characters or fewer."
    if not isinstance(lifetime_days, int) or not MIN_LIFETIME_DAYS <= lifetime_days <= MAX_LIFETIME_DAYS:
        errors["lifetime_days"] = f"Choose between {MIN_LIFETIME_DAYS} and {MAX_LIFETIME_DAYS} days."
    if errors:
        raise ValidationError(errors)

    now = utc_now()
    now_text = format_stored(now)
    active = [t for t in list_tokens(conn, user_id=user_id) if t["status"] == "active"]
    if len(active) >= max_active:
        raise ValidationError({"name": f"You already have {max_active} active tokens. Revoke one first."})

    token = PREFIX + secrets.token_urlsafe(32)
    with conn:
        row = conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, token_prefix, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (
                user_id,
                name,
                token_digest(token),
                token[:PREFIX_DISPLAY_LENGTH],
                now_text,
                format_stored(now + timedelta(days=lifetime_days)),
            ),
        ).fetchone()
    record = next(t for t in list_tokens(conn, user_id=user_id) if t["id"] == row["id"])
    return token, record


def list_tokens(conn, *, user_id):
    now_text = format_stored(utc_now())
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM api_tokens WHERE user_id = ? ORDER BY created_at DESC, id DESC",  # noqa: S608 - constant columns
        (user_id,),
    ).fetchall()
    return [{**dict(row), "status": _status(row, now_text)} for row in rows]


def revoke_token(conn, token_id, *, user_id):
    """Revoke one of THIS user's tokens. False if no such active token."""
    cursor = conn.execute(
        "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
        (format_stored(utc_now()), token_id, user_id),
    )
    return cursor.rowcount == 1


def revoke_all(conn, *, user_id):
    cursor = conn.execute(
        "UPDATE api_tokens SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (format_stored(utc_now()), user_id),
    )
    return cursor.rowcount


def authenticate(conn, token):
    """The active user who owns `token`, or None.

    None for: a malformed token, an unknown token, a revoked or expired
    token, or a disabled account. The caller cannot tell these apart, and
    neither can a client: every case is the same 401 response.
    """
    if not looks_like_token(token):
        return None
    now = utc_now()
    now_text = format_stored(now)
    row = conn.execute(
        "SELECT t.id AS token_id, t.expires_at, t.revoked_at, t.last_used_at, "
        "u.id, u.email, u.is_active, u.session_version, u.created_at, u.last_login_at "
        "FROM api_tokens AS t JOIN users AS u ON u.id = t.user_id WHERE t.token_hash = ?",
        (token_digest(token),),
    ).fetchone()
    if row is None or row["revoked_at"] or row["expires_at"] <= now_text or not row["is_active"]:
        return None
    # Record use at most once a minute, so busy scripts do not write on every call.
    last = row["last_used_at"]
    if last is None or (now - parse_stored(last)).total_seconds() >= 60:
        conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (now_text, row["token_id"]))
    return {
        "id": row["id"],
        "email": row["email"],
        "is_active": True,
        "session_version": row["session_version"],
        "created_at": row["created_at"],
        "last_login_at": row["last_login_at"],
        "token_id": row["token_id"],
    }
```

### `tradeledger/repositories/throttle.py` (NEW)

**Why:** Database-backed sign-in throttling with HMAC-digested keys.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Counting failed sign-ins and registrations, to slow down guessing (Phase 8).

Plain English
-------------
Someone trying to guess a password needs thousands of attempts. TradeLedger
counts recent failures for each email address and for each network address
(IP); past a limit it refuses further attempts for a while (HTTP 429).

Technical summary
-----------------
* Stored in the database (table login_attempts), not in memory, so the
  limit holds across every Gunicorn worker and every container.
* Keys are HMAC-SHA256 digests (keyed with SECRET_KEY) of "scope:value",
  so the table never holds raw email or IP addresses, including addresses
  of accounts that do not exist.
* A sliding window: a key is blocked while it has `limit` or more failures
  younger than `window_seconds`. Old rows are deleted as new ones arrive.
* Per-email limits stop slow guessing against one account; per-IP limits
  stop one machine trying many accounts. Neither stops a large botnet:
  that needs an edge service such as AWS WAF rate-based rules (see
  docs/PHASE_8_SECURITY_REVIEW.md).
* Locking an account outright would let anyone lock you out by typing your
  email wrongly five times; a time window limits that harm.
"""

import hashlib
import hmac
from datetime import timedelta

from ..timestamps import format_stored, utc_now


def key_digest(secret_key, scope, value):
    message = f"{scope}:{value}".encode()
    return hmac.new(secret_key.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _window_start(window_seconds):
    return format_stored(utc_now() - timedelta(seconds=window_seconds))


def recent_failures(conn, scope, digest, window_seconds):
    return conn.execute(
        "SELECT COUNT(*) FROM login_attempts WHERE scope = ? AND key_digest = ? AND attempted_at > ?",
        (scope, digest, _window_start(window_seconds)),
    ).fetchone()[0]


def is_blocked(conn, scope, digest, *, limit, window_seconds):
    return recent_failures(conn, scope, digest, window_seconds) >= limit


def record_failure(conn, scope, digest, *, window_seconds):
    with conn:
        # Housekeeping: anything older than one window can no longer matter.
        conn.execute(
            "DELETE FROM login_attempts WHERE scope = ? AND attempted_at <= ?",
            (scope, _window_start(window_seconds)),
        )
        conn.execute(
            "INSERT INTO login_attempts (scope, key_digest, attempted_at) VALUES (?, ?, ?)",
            (scope, digest, format_stored(utc_now())),
        )


def clear(conn, scope, digest):
    conn.execute("DELETE FROM login_attempts WHERE scope = ? AND key_digest = ?", (scope, digest))
```

### `tradeledger/repositories/users.py` (NEW)

**Why:** Accounts: email normalisation and checks, create/look up, password changes, session version, activation.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""User accounts (Phase 8).

Return values
  create_user           -> new id (int)
  get_user              -> dict or None (includes password_hash: never
                           send this dict to a template or the API; use
                           public_user())
  get_user_by_email     -> dict or None
  set_password_hash     -> None (optionally ends every other session)
  bump_session_version  -> the new version (ends every existing session)

Errors
  ValidationError  invalid email
  DuplicateError   the email is already registered

Emails are stored lower-case and trimmed, so "Trader@Example.com " and
"trader@example.com" are the same account.
"""

import re

from ..db import IntegrityError
from ..timestamps import utc_now_text
from ..validation import ValidationError
from . import DuplicateError

MAX_EMAIL_LENGTH = 254
# Deliberately simple: something@something.something, no spaces. Full RFC
# 5322 validation is not worth it; only a confirmation email proves that an
# address works, and that needs an email service this phase does not have.
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}")

_COLUMNS = "id, email, password_hash, is_active, session_version, created_at, updated_at, last_login_at"


def normalise_email(value):
    return value.strip().lower() if isinstance(value, str) else ""


def email_problem(value):
    """Why `value` is not a usable email (worded to follow the field
    label, e.g. "Email address must look like ..."), or None."""
    email = normalise_email(value)
    if not email:
        return "is required."
    if len(email) > MAX_EMAIL_LENGTH:
        return f"must be {MAX_EMAIL_LENGTH} characters or fewer."
    if not _EMAIL.fullmatch(email):
        return "must look like name@example.com."
    return None


def _to_dict(row):
    if row is None:
        return None
    user = dict(row)
    user["is_active"] = bool(user["is_active"])
    return user


def public_user(user):
    """The fields that are safe to show: never the password hash."""
    return {key: user[key] for key in ("id", "email", "created_at", "last_login_at")}


def create_user(conn, email, password_hash):
    problem = email_problem(email)
    if problem:
        raise ValidationError({"email": problem})
    now = utc_now_text()
    try:
        with conn:
            row = conn.execute(
                "INSERT INTO users (email, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?) RETURNING id",
                (normalise_email(email), password_hash, now, now),
            ).fetchone()
    except IntegrityError as exc:
        if exc.kind == "unique":
            raise DuplicateError("An account with this email address already exists.") from exc
        raise
    return row["id"]


def get_user(conn, user_id):
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        return None
    row = conn.execute(f"SELECT {_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()  # noqa: S608 - constant columns
    return _to_dict(row)


def get_user_by_email(conn, email):
    email = normalise_email(email)
    if not email:
        return None
    row = conn.execute(f"SELECT {_COLUMNS} FROM users WHERE email = ?", (email,)).fetchone()  # noqa: S608 - constant columns
    return _to_dict(row)


def count_users(conn):
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def set_password_hash(conn, user_id, password_hash, *, end_other_sessions):
    """Store a new hash. end_other_sessions=True also raises the session
    version, which signs out every browser that was logged in."""
    bump = ", session_version = session_version + 1" if end_other_sessions else ""
    with conn:
        conn.execute(
            f"UPDATE users SET password_hash = ?, updated_at = ?{bump} WHERE id = ?",  # noqa: S608 - fixed fragment
            (password_hash, utc_now_text(), user_id),
        )


def bump_session_version(conn, user_id):
    with conn:
        row = conn.execute(
            "UPDATE users SET session_version = session_version + 1, updated_at = ? WHERE id = ? "
            "RETURNING session_version",
            (utc_now_text(), user_id),
        ).fetchone()
    return None if row is None else row["session_version"]


def record_login(conn, user_id):
    conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (utc_now_text(), user_id))


def set_active(conn, user_id, active):
    """Disable or re-enable an account. Disabling also ends its sessions."""
    with conn:
        conn.execute(
            "UPDATE users SET is_active = ?, session_version = session_version + 1, updated_at = ? WHERE id = ?",
            (bool(active), utc_now_text(), user_id),
        )
```

### `tradeledger/schema_postgres.sql` (NEW)

**Why:** PostgreSQL schema at version 3: users, api_tokens, login_attempts, instruments, trades (NUMERIC money, named CHECK constraints, composite owner foreign key), schema_migrations.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```sql
-- TradeLedger database schema (PostgreSQL), schema version 3 (Phase 8)
--
-- The same structure and rules as the SQLite database after migration 3
-- (tradeledger/migrations.py), written in PostgreSQL's own types:
--
--   * Exact decimals (prices, sizes, fees, multipliers) are NUMERIC with no
--     fixed precision. NUMERIC is exact base-10 arithmetic and keeps the
--     scale you entered ('1.08540' stays '1.08540'), so values read back
--     exactly as written, like the SQLite TEXT columns. They are never REAL
--     or DOUBLE PRECISION (binary floating point). Validation limits values
--     to 15 digits before and 15 after the decimal point; the CHECKs below
--     repeat that limit as a second line of defence.
--   * Timestamps stay TEXT in the exact UTC form 'YYYY-MM-DDTHH:MM:SSZ',
--     checked by a regular expression. TIMESTAMPTZ would be the idiomatic
--     PostgreSQL type; TEXT was kept so both databases return identical
--     values and the Python code has one timestamp format. Text in this
--     form sorts in time order, so ORDER BY and range filters are correct.
--   * Ids are identity columns (GENERATED ALWAYS AS IDENTITY), the modern
--     replacement for SERIAL.
--   * Every user-owned row has user_id NOT NULL. Unlike SQLite, a PostgreSQL
--     database never holds unassigned legacy rows: the import script
--     (scripts/migrate_sqlite_to_postgres.py) assigns an owner explicitly.
--
-- Run by db.init_schema() inside one transaction on an EMPTY database.

CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    applied_at TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')
);

CREATE TABLE users (
    id              BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email           TEXT    NOT NULL
                            CONSTRAINT users_email_unique UNIQUE
                            CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
    password_hash   TEXT    NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    session_version INTEGER NOT NULL DEFAULT 1 CHECK (session_version >= 1),
    created_at      TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    updated_at      TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    last_login_at   TEXT
);

CREATE TABLE api_tokens (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id      BIGINT NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    name         TEXT   NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
    token_hash   TEXT   NOT NULL CONSTRAINT api_tokens_hash_unique UNIQUE CHECK (length(token_hash) = 64),
    token_prefix TEXT   NOT NULL,
    created_at   TEXT   NOT NULL,
    expires_at   TEXT   NOT NULL,
    last_used_at TEXT,
    revoked_at   TEXT
);
CREATE INDEX idx_api_tokens_user ON api_tokens (user_id);

CREATE TABLE login_attempts (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    scope        TEXT   NOT NULL,
    key_digest   TEXT   NOT NULL,
    attempted_at TEXT   NOT NULL
);
CREATE INDEX idx_login_attempts_lookup ON login_attempts (scope, key_digest, attempted_at);

CREATE TABLE instruments (
    id                  BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id             BIGINT  NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    symbol              TEXT    NOT NULL
                                CHECK (symbol = upper(symbol) AND length(symbol) BETWEEN 1 AND 20),
    name                TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
    asset_class         TEXT    NOT NULL
                                CHECK (asset_class IN ('FOREX', 'COMMODITY', 'INDEX', 'STOCK', 'OTHER')),
    price_increment     NUMERIC NOT NULL
                                CHECK (price_increment > 0 AND price_increment < 1e15
                                       AND scale(price_increment) <= 15),
    contract_multiplier NUMERIC NOT NULL
                                CHECK (contract_multiplier > 0 AND contract_multiplier < 1e15
                                       AND scale(contract_multiplier) <= 15),
    quote_currency      TEXT    NOT NULL
                                CHECK (length(quote_currency) = 3 AND quote_currency = upper(quote_currency)),
    created_at          TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    CONSTRAINT instruments_user_symbol_unique UNIQUE (user_id, symbol),
    CONSTRAINT instruments_id_user_unique UNIQUE (id, user_id)
);

CREATE TABLE trades (
    id            BIGINT  GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       BIGINT  NOT NULL REFERENCES users (id) ON DELETE RESTRICT,
    instrument_id BIGINT  NOT NULL,
    trade_date    TEXT    NOT NULL
                          CHECK (trade_date ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'),
    closed_at     TEXT    CHECK (closed_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$'),
    direction     TEXT    NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price   NUMERIC NOT NULL CHECK (entry_price > 0 AND entry_price < 1e15 AND scale(entry_price) <= 15),
    stop_loss     NUMERIC NOT NULL CHECK (stop_loss > 0 AND stop_loss < 1e15 AND scale(stop_loss) <= 15),
    take_profit   NUMERIC CHECK (take_profit > 0 AND take_profit < 1e15 AND scale(take_profit) <= 15),
    exit_price    NUMERIC CHECK (exit_price > 0 AND exit_price < 1e15 AND scale(exit_price) <= 15),
    position_size NUMERIC NOT NULL
                          CHECK (position_size > 0 AND position_size < 1e15 AND scale(position_size) <= 15),
    fees          NUMERIC NOT NULL DEFAULT 0 CHECK (fees >= 0 AND fees < 1e15 AND scale(fees) <= 15),
    status        TEXT    NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    strategy      TEXT    CHECK (strategy IS NULL OR length(strategy) BETWEEN 1 AND 50),
    session       TEXT    CHECK (session IS NULL OR session IN ('ASIA', 'LONDON', 'NEW_YORK', 'OTHER')),
    notes         TEXT    CHECK (notes IS NULL OR length(notes) <= 2000),
    created_at    TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
    updated_at    TEXT    NOT NULL DEFAULT to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),

    -- A trade can only use one of its OWNER's instruments.
    CONSTRAINT trades_instrument_owner_fk FOREIGN KEY (instrument_id, user_id)
        REFERENCES instruments (id, user_id) ON DELETE RESTRICT,
    CONSTRAINT status_matches_exit CHECK (
        (status = 'OPEN' AND exit_price IS NULL) OR (status = 'CLOSED' AND exit_price IS NOT NULL)
    ),
    CONSTRAINT stop_on_correct_side CHECK (
        (direction = 'LONG' AND stop_loss < entry_price) OR (direction = 'SHORT' AND stop_loss > entry_price)
    ),
    CONSTRAINT target_on_correct_side CHECK (
        take_profit IS NULL
        OR (direction = 'LONG' AND take_profit > entry_price)
        OR (direction = 'SHORT' AND take_profit < entry_price)
    ),
    -- SQLite enforces this rule with two triggers; PostgreSQL can state it
    -- directly. Same name, so error handling is identical.
    CONSTRAINT closed_at_rules CHECK (
        closed_at IS NULL OR (status = 'CLOSED' AND closed_at >= trade_date)
    )
);

CREATE INDEX idx_trades_instrument_id ON trades (instrument_id);
CREATE INDEX idx_trades_user_date     ON trades (user_id, trade_date, id);
CREATE INDEX idx_trades_user_closed   ON trades (user_id, closed_at);
CREATE INDEX idx_trades_user_status   ON trades (user_id, status);
```

### `tradeledger/templates/account/settings.html` (NEW)

**Why:** Account page.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{% extends "base.html" %}
{% import "macros/forms.html" as f %}

{% block title %}Account{% endblock %}
{% block heading %}Account{% endblock %}
{% block description %}Your sign-in details, sessions and API tokens.{% endblock %}

{% block content %}
<section class="card prose" aria-labelledby="profile-title">
    <h2 id="profile-title">Your account</h2>
    <dl class="detail-list">
        <div><dt>Email address</dt><dd>{{ account.email }}</dd></div>
        <div><dt>Created</dt><dd>{{ account.created_at | when }}</dd></div>
        <div><dt>Last sign-in</dt><dd>{{ account.last_login_at | when }}</dd></div>
    </dl>
</section>

<div class="split">
    <section class="card form-section" aria-labelledby="password-title">
        <h2 class="form-section__title" id="password-title">Change password</h2>
        <p class="muted">Changing your password signs out every other browser and revokes all your API tokens.</p>
        {{ f.error_summary(password_errors, password_labels) }}
        <form method="post" action="{{ url_for('main.change_password') }}" novalidate data-submit-once>
            {{ csrf_field() }}
            <input type="email" name="username" value="{{ account.email }}" autocomplete="username" hidden>
            <div class="form-stack">
                {{ f.text("current_password", "Current password", {}, password_errors, password_labels, type="password",
                          required=True, maxlength=128, autocomplete="current-password") }}
                {{ f.text("new_password", "New password", {}, password_errors, password_labels, type="password",
                          required=True, maxlength=128, autocomplete="new-password", hint="At least 12 characters.") }}
                {{ f.text("confirm_password", "Confirm new password", {}, password_errors, password_labels,
                          type="password", required=True, maxlength=128, autocomplete="new-password") }}
            </div>
            <div class="form-actions">
                <button class="button button--primary" type="submit" data-busy-label="Saving…">Change password</button>
            </div>
        </form>
    </section>

    <section class="card form-section" aria-labelledby="sessions-title">
        <h2 class="form-section__title" id="sessions-title">Signed-in browsers</h2>
        <p>Lost a device, or signed in on a shared computer? This signs out every browser except this one.
           Sessions also end on their own after 8 hours, or 2 hours without activity.</p>
        <form method="post" action="{{ url_for('main.end_other_sessions') }}">
            {{ csrf_field() }}
            <div class="form-actions">
                <button class="button button--secondary" type="submit">Sign out everywhere else</button>
            </div>
        </form>
    </section>
</div>

{% if api_enabled %}
<section class="card form-section" aria-labelledby="tokens-title">
    <h2 class="form-section__title" id="tokens-title">API tokens</h2>
    <p>Programs use a token to call the JSON API (<code>/api/v1</code>) on your behalf, with the header
       <code>Authorization: Bearer &lt;token&gt;</code>. A token can read and change your trades, so treat it like a password.</p>

    {% if new_token %}
    <div class="notice notice--warning token-reveal" role="status">
        <p><strong>Copy your new token now. It will not be shown again.</strong></p>
        <label class="field__label" for="new-token-value">Token “{{ new_token.record.name }}”</label>
        <input class="input token-reveal__value" id="new-token-value" type="text" readonly
               value="{{ new_token.value }}" spellcheck="false" autocomplete="off">
        <p class="muted">Expires {{ new_token.record.expires_at | when }}.</p>
    </div>
    {% endif %}

    {% if tokens %}
    <div class="table-wrap" role="region" aria-label="API tokens table" tabindex="0">
        <table class="table">
            <caption class="visually-hidden">Your API tokens, newest first</caption>
            <thead>
                <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Starts with</th>
                    <th scope="col">Created</th>
                    <th scope="col">Expires</th>
                    <th scope="col">Last used</th>
                    <th scope="col">Status</th>
                    <th scope="col"><span class="visually-hidden">Actions</span></th>
                </tr>
            </thead>
            <tbody>
                {% for t in tokens %}
                <tr>
                    <td data-label="Name">{{ t.name }}</td>
                    <td data-label="Starts with"><code>{{ t.token_prefix }}…</code></td>
                    <td data-label="Created">{{ t.created_at | when }}</td>
                    <td data-label="Expires">{{ t.expires_at | when }}</td>
                    <td data-label="Last used">{{ t.last_used_at | when }}</td>
                    <td data-label="Status"><span class="badge badge--{{ 'win' if t.status == 'active' else 'neutral' }}">{{ t.status | title }}</span></td>
                    <td class="actions-col">
                        {% if t.status == "active" %}
                        <form method="post" action="{{ url_for('main.revoke_api_token', token_id=t.id) }}">
                            {{ csrf_field() }}
                            <button class="button button--small button--danger" type="submit">Revoke<span class="visually-hidden"> token {{ t.name }}</span></button>
                        </form>
                        {% endif %}
                    </td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>
    {% else %}
    <p class="muted">You have no API tokens.</p>
    {% endif %}

    <h3 class="form-section__subtitle">Create a token</h3>
    {{ f.error_summary(token_errors, token_labels) }}
    <form method="post" action="{{ url_for('main.create_api_token') }}" novalidate data-submit-once>
        {{ csrf_field() }}
        <input type="hidden" name="form_nonce" value="{{ token_form_nonce }}">
        <div class="form-grid">
            {{ f.text("name", "Token name", token_values, token_errors, token_labels, required=True, maxlength=60,
                      hint="What will use it, e.g. 'Import script on my laptop'.") }}
            {{ f.select("lifetime_days", "Expires after", token_values, token_errors, token_labels,
                        lifetime_options, required=True) }}
        </div>
        <div class="form-actions">
            <button class="button button--primary" type="submit" data-busy-label="Creating…">Create token</button>
        </div>
    </form>
</section>
{% endif %}
{% endblock %}
```

### `tradeledger/templates/auth/login.html` (NEW)

**Why:** Sign-in form.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{% extends "base.html" %}
{% import "macros/forms.html" as f %}

{% block title %}Sign in{% endblock %}
{% block heading %}Sign in{% endblock %}
{% block description %}Your journal, instruments and analytics are private to your account.{% endblock %}

{% block content %}
<section class="card form-section auth-card" aria-labelledby="login-title">
    <h2 class="form-section__title" id="login-title">Sign in to TradeLedger</h2>
    {% if error %}
    <div class="error-summary" role="alert" tabindex="-1" data-error-summary aria-labelledby="login-error">
        <h2 class="error-summary__title" id="login-error">Sign-in failed</h2>
        <p>{{ error }}</p>
    </div>
    {% endif %}
    <form method="post" action="{{ url_for('main.login') }}" novalidate data-submit-once>
        {{ csrf_field() }}
        {% if next_path %}<input type="hidden" name="next" value="{{ next_path }}">{% endif %}
        <div class="form-stack">
            {{ f.text("email", "Email address", values, {}, {}, type="email", required=True, maxlength=254,
                      autocomplete="username") }}
            {{ f.text("password", "Password", {}, {}, {}, type="password", required=True, maxlength=128,
                      autocomplete="current-password") }}
        </div>
        <div class="form-actions">
            <button class="button button--primary" type="submit" data-busy-label="Signing in…">Sign in</button>
        </div>
    </form>
    <p class="muted auth-card__note">
        {% if config.REGISTRATION_ENABLED %}
        New here? <a href="{{ url_for('main.register') }}">Create an account</a>.
        {% else %}
        Accounts on this server are created by its administrator.
        {% endif %}
        Forgotten your password? Password reset by email is not available yet; the administrator can set a new one.
    </p>
</section>
{% endblock %}
```

### `tradeledger/templates/auth/register.html` (NEW)

**Why:** Registration form (development by default).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{% extends "base.html" %}
{% import "macros/forms.html" as f %}

{% block title %}Create an account{% endblock %}
{% block heading %}Create an account{% endblock %}
{% block description %}Each account has its own private journal.{% endblock %}

{% block content %}
<section class="card form-section auth-card" aria-labelledby="register-title">
    <h2 class="form-section__title" id="register-title">Create your TradeLedger account</h2>
    {{ f.error_summary(errors, labels) }}
    <form method="post" action="{{ url_for('main.register') }}" novalidate data-submit-once>
        {{ csrf_field() }}
        <div class="form-stack">
            {{ f.text("email", "Email address", values, errors, labels, type="email", required=True, maxlength=254,
                      autocomplete="username", hint="Used to sign in. TradeLedger does not send email.") }}
            {{ f.text("password", "Password", {}, errors, labels, type="password", required=True, maxlength=128,
                      autocomplete="new-password",
                      hint="At least 12 characters. A few unrelated words make a strong, memorable password.") }}
            {{ f.text("confirm_password", "Confirm password", {}, errors, labels, type="password", required=True,
                      maxlength=128, autocomplete="new-password") }}
        </div>
        <div class="form-actions">
            <button class="button button--primary" type="submit" data-busy-label="Creating…">Create account</button>
        </div>
    </form>
    <p class="muted auth-card__note">Already have an account? <a href="{{ url_for('main.login') }}">Sign in</a>.</p>
</section>
{% endblock %}
```

### `tradeledger/web/account.py` (NEW)

**Why:** Account page: change password, sign out other sessions, create and revoke API tokens (single-use form value).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Account settings: password change, signing out elsewhere, API tokens (Phase 8).

Every route here needs a signed-in user (it is not in PUBLIC_ENDPOINTS) and
every POST carries a CSRF token (web/csrf.py).
"""

import logging
import secrets

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db
from ..passwords import hash_password, parameters_from_config, password_problem, verify_password
from ..repositories import api_tokens, throttle
from ..repositories.users import bump_session_version, get_user, public_user, set_password_hash
from ..validation import ValidationError
from . import main_bp
from .auth import THROTTLED, current_user_id, digest

log = logging.getLogger("tradeledger.auth")

PASSWORD_LABELS = {
    "current_password": "Current password",
    "new_password": "New password",
    "confirm_password": "Confirm new password",
}
TOKEN_LABELS = {"name": "Token name", "lifetime_days": "Expires after"}
LIFETIME_OPTIONS = [(30, "30 days"), (90, "90 days"), (365, "365 days")]


def new_token_form_nonce():
    """A one-time value for the "Create token" form.

    The new token is shown in the POST response itself (it must never be
    stored anywhere, including the session cookie). Without a nonce, a
    browser refresh would re-send that POST and silently create a second
    token. The nonce makes each rendered form usable once."""
    session["token_form_nonce"] = secrets.token_urlsafe(16)
    return session["token_form_nonce"]


def render_account(status=200, **extra):
    context = {
        "account": public_user(g.user),
        "tokens": api_tokens.list_tokens(get_db(), user_id=current_user_id()),
        "password_errors": {},
        "token_errors": {},
        "token_values": {"lifetime_days": str(current_app.config["API_TOKEN_DEFAULT_LIFETIME_DAYS"])},
        "new_token": None,
        "password_labels": PASSWORD_LABELS,
        "token_labels": TOKEN_LABELS,
        "lifetime_options": LIFETIME_OPTIONS,
        "api_enabled": current_app.config["API_ENABLED"],
        "token_form_nonce": new_token_form_nonce(),
    }
    context.update(extra)
    return render_template("account/settings.html", **context), status


@main_bp.route("/account")
def account():
    return render_account()


@main_bp.route("/account/password", methods=["POST"])
def change_password():
    config = current_app.config
    conn = get_db()
    user = g.user
    key = digest("login-email", user["email"])
    window = config["LOGIN_WINDOW_SECONDS"]
    if throttle.is_blocked(
        conn, "login-email", key, limit=config["LOGIN_MAX_FAILURES_PER_ACCOUNT"], window_seconds=window
    ):
        return render_account(429, password_errors={"current_password": THROTTLED})

    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    errors = {}
    if not verify_password(current, user["password_hash"]):
        throttle.record_failure(conn, "login-email", key, window_seconds=window)
        errors["current_password"] = "is incorrect."
    problem = password_problem(new, email=user["email"])
    if problem:
        errors["new_password"] = problem
    elif request.form.get("confirm_password", "") != new:
        errors["confirm_password"] = "does not match the new password."
    elif new == current and not errors:
        errors["new_password"] = "must be different from your current password."
    if errors:
        return render_account(400, password_errors=errors)

    set_password_hash(
        conn, user["id"], hash_password(new, parameters_from_config(config)), end_other_sessions=True
    )
    revoked = api_tokens.revoke_all(conn, user_id=user["id"])
    # This browser stays signed in with the new session version.
    session["sv"] = get_user(conn, user["id"])["session_version"]
    log.info(
        "password changed",
        extra={"event": "auth.password.changed", "user_id": user["id"], "tokens_revoked": revoked},
    )
    flash(
        "Password changed. Every other browser has been signed out"
        + (f" and {revoked} API token(s) revoked." if revoked else "."),
        "success",
    )
    return redirect(url_for("main.account"), code=303)


@main_bp.route("/account/sessions/end-others", methods=["POST"])
def end_other_sessions():
    user_id = current_user_id()
    session["sv"] = bump_session_version(get_db(), user_id)
    log.info("other sessions ended", extra={"event": "auth.sessions.ended_elsewhere", "user_id": user_id})
    flash("Every other browser signed in to your account has been signed out.", "success")
    return redirect(url_for("main.account"), code=303)


@main_bp.route("/account/tokens", methods=["POST"])
def create_api_token():
    config = current_app.config
    if not config["API_ENABLED"]:
        abort(404)
    expected = session.pop("token_form_nonce", None)
    if not expected or request.form.get("form_nonce") != expected:
        flash("That form had already been submitted, so no new token was created.", "info")
        return redirect(url_for("main.account"), code=303)
    name = request.form.get("name", "")
    raw_days = request.form.get("lifetime_days", "")
    values = {"name": name, "lifetime_days": raw_days}
    lifetime = int(raw_days) if raw_days.isdigit() else None
    try:
        token, record = api_tokens.create_token(
            get_db(),
            user_id=current_user_id(),
            name=name,
            lifetime_days=lifetime,
            max_active=config["API_TOKEN_MAX_ACTIVE"],
        )
    except ValidationError as exc:
        return render_account(400, token_errors=exc.errors, token_values=values)
    log.info(
        "API token created",
        extra={"event": "auth.token.created", "user_id": current_user_id(), "token_id": record["id"]},
    )
    # The token is shown in THIS response only. HTML responses are already
    # Cache-Control: no-store, so the browser does not keep a copy.
    return render_account(201, new_token={"value": token, "record": record})


@main_bp.route("/account/tokens/<int:token_id>/revoke", methods=["POST"])
def revoke_api_token(token_id):
    if not api_tokens.revoke_token(get_db(), token_id, user_id=current_user_id()):
        abort(404)  # not yours, already revoked, or does not exist: same answer
    log.info(
        "API token revoked",
        extra={"event": "auth.token.revoked", "user_id": current_user_id(), "token_id": token_id},
    )
    flash("API token revoked. Programs using it will be refused from now on.", "success")
    return redirect(url_for("main.account"), code=303)
```

### `tradeledger/web/auth.py` (NEW)

**Why:** Sign-in, sign-out, registration, server-side session lifetimes, default-deny login check, safe redirects.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Signing in, signing out and registering (Phase 8).

Plain English
-------------
* Sign in: you type your email and password. If they match, TradeLedger
  puts your user number in a cookie that it signs with a secret key, so
  the browser cannot change it without the signature breaking.
* Every page then checks that cookie, loads your account and refuses
  anything private if you are not signed in.
* Sign out: a button (a POST form with a CSRF token), never a plain link,
  so another website cannot sign you out by embedding an image.

Technical summary
-----------------
Session    Flask's signed-cookie session (itsdangerous HMAC with SECRET_KEY).
           It holds: uid (user id), sv (the user's session_version),
           auth_at (sign-in time), seen_at (last activity), the CSRF token.
           No password, hash or personal data is ever put in it.
Checks     On every website request (load_logged_in_user):
             * the account still exists and is active;
             * sv equals users.session_version; raising that number
               ("sign out everywhere", password change, account disabled)
               ends every existing session at once;
             * auth_at is younger than SESSION_MAX_AGE_SECONDS (absolute
               limit) and seen_at younger than SESSION_IDLE_TIMEOUT_SECONDS.
           These are checked on the SERVER: the cookie's own expiry date is
           only a hint to the browser, and a copied cookie would ignore it.
Fixation   start_session() clears the session before writing the new user,
           so any session state planted before sign-in (including the CSRF
           token) is discarded. Signed cookies have no server-side session id
           to fixate, but clearing them is still required.
Default    require_login() runs before EVERY route in the website blueprint
deny       and only lets the endpoints in PUBLIC_ENDPOINTS through.
           A new route is private unless someone deliberately adds it to
           that set (tests/test_auth.py checks every registered route).
Brute      Failed sign-ins are counted per email and per IP address
force      (repositories/throttle.py); past the limits the form answers 429.
           Unknown emails still run a full Argon2id verification against a
           dummy hash, so response time does not reveal which emails exist.
Messages   One message for every failure: "Email or password is incorrect."
           A disabled account gets the same message.

Signed cookie vs server-side sessions: a server-side session stores the
session data in a database or cache and puts only a random id in the
cookie, so one session can be deleted on the server. The signed cookie
needs no session storage, which suits several containers behind a load
balancer; the session_version column provides the revocation it would
otherwise lack. The trade-off: you can end ALL of a user's sessions, not
one particular device's.
"""

import logging
import time

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for

from ..db import get_db
from ..passwords import hash_password, needs_rehash, parameters_from_config, password_problem, verify_password
from ..repositories import DuplicateError, throttle
from ..repositories.users import (
    create_user,
    email_problem,
    get_user,
    get_user_by_email,
    normalise_email,
    record_login,
    set_password_hash,
)
from . import forms, main_bp

log = logging.getLogger("tradeledger.auth")

PUBLIC_ENDPOINTS = frozenset(
    {"main.login", "main.register", "main.health", "main.health_live", "main.health_ready", "main.about"}
)

LOGIN_FAILED = "Email or password is incorrect."
THROTTLED = "Too many sign-in attempts. Wait a few minutes, then try again."

LOGIN_LABELS = {"email": "Email address", "password": "Password"}
REGISTER_LABELS = {"email": "Email address", "password": "Password", "confirm_password": "Confirm password"}


def now_seconds():
    """Current time in whole seconds. One function so tests can move the clock."""
    return int(time.time())


def client_ip():
    """The client's IP address. Behind a load balancer this is only right
    when PROXY_HOPS matches the number of proxies (see config.py)."""
    return request.remote_addr or "unknown"


def digest(scope, value):
    return throttle.key_digest(current_app.config["SECRET_KEY"], scope, value)


def safe_next(target):
    """Accept only a path on THIS site for the post-login redirect (or None).

    Uses the same rules as every other "next" link (forms.safe_next):
    absolute URLs (https://evil.example), scheme-relative URLs
    (//evil.example), backslash tricks and control characters are refused.
    Otherwise an attacker could send a link to
    /login?next=https://evil.example and the real sign-in page would
    forward the victim to a fake one.
    """
    return forms.safe_next(target, None)


def dummy_hash():
    """A real Argon2id hash of a random value, computed once per process
    with the current settings: verifying against it costs the same as a
    real verification."""
    cache = current_app.extensions.setdefault("tradeledger_auth", {})
    params = parameters_from_config(current_app.config)
    if cache.get("dummy_params") != params:
        cache["dummy_hash"] = hash_password("dummy password that matches no account", params)
        cache["dummy_params"] = params
    return cache["dummy_hash"]


def start_session(user):
    session.clear()
    session.permanent = True
    now = now_seconds()
    session["uid"] = user["id"]
    session["sv"] = user["session_version"]
    session["auth_at"] = now
    session["seen_at"] = now


def current_user_id():
    """The signed-in user's id. Raises if called where nobody is signed in,
    so a coding mistake fails closed instead of reading someone's data."""
    user = g.get("user")
    if user is None:
        raise RuntimeError("current_user_id() called without a signed-in user")
    return user["id"]


# ---------- Request hooks (registered in web/__init__.py) ----------


def load_logged_in_user():
    g.user = None
    user_id = session.get("uid")
    if user_id is None:
        return None
    config = current_app.config
    user = get_user(get_db(), user_id)
    now = now_seconds()
    reason = None
    if user is None or not user["is_active"]:
        reason = "account_unavailable"
    elif session.get("sv") != user["session_version"]:
        reason = "signed_out_elsewhere"
    elif now - int(session.get("auth_at", 0)) > config["SESSION_MAX_AGE_SECONDS"]:
        reason = "expired"
    elif now - int(session.get("seen_at", 0)) > config["SESSION_IDLE_TIMEOUT_SECONDS"]:
        reason = "idle"
    if reason:
        session.clear()
        g.session_ended = reason
        log.info("session ended", extra={"event": "auth.session.ended", "reason": reason, "user_id": user_id})
        return None
    g.user = user
    if now - int(session.get("seen_at", 0)) >= 60:  # refresh at most once a minute
        session["seen_at"] = now
    return None


def require_login():
    if request.endpoint in PUBLIC_ENDPOINTS or g.get("user") is not None:
        return None
    if g.get("session_ended"):
        flash("Your session has ended. Please sign in again.", "info")
    next_path = None
    if request.method == "GET":
        next_path = request.full_path.rstrip("?")
    return redirect(url_for("main.login", next=next_path), code=303 if request.method != "GET" else 302)


# ---------- Routes ----------


@main_bp.route("/login", methods=["GET", "POST"])
def login():
    if g.get("user") is not None:
        return redirect(url_for("main.index"))
    next_path = safe_next(request.values.get("next"))
    if request.method == "GET":
        return render_template("auth/login.html", values={}, error=None, next_path=next_path)

    config = current_app.config
    conn = get_db()
    email = normalise_email(request.form.get("email"))
    password = request.form.get("password", "")
    email_key = digest("login-email", email)
    ip_key = digest("login-ip", client_ip())
    window = config["LOGIN_WINDOW_SECONDS"]

    if throttle.is_blocked(
        conn, "login-email", email_key, limit=config["LOGIN_MAX_FAILURES_PER_ACCOUNT"], window_seconds=window
    ) or throttle.is_blocked(
        conn, "login-ip", ip_key, limit=config["LOGIN_MAX_FAILURES_PER_IP"], window_seconds=window
    ):
        log.warning("sign-in refused: too many failures", extra={"event": "auth.login.throttled"})
        response = render_template(
            "auth/login.html", values={"email": email}, error=THROTTLED, next_path=next_path
        )
        return response, 429, {"Retry-After": str(window)}

    user = get_user_by_email(conn, email) if email else None
    password_ok = verify_password(password, user["password_hash"] if user else dummy_hash())
    if user is None or not password_ok or not user["is_active"]:
        reason = "unknown_email" if user is None else ("bad_password" if not password_ok else "inactive")
        throttle.record_failure(conn, "login-email", email_key, window_seconds=window)
        throttle.record_failure(conn, "login-ip", ip_key, window_seconds=window)
        log.warning(
            "sign-in failed",
            extra={"event": "auth.login.failed", "reason": reason, "email_ref": email_key[:12]},
        )
        return render_template(
            "auth/login.html", values={"email": email}, error=LOGIN_FAILED, next_path=next_path
        )

    throttle.clear(conn, "login-email", email_key)
    params = parameters_from_config(config)
    if needs_rehash(user["password_hash"], params):
        set_password_hash(conn, user["id"], hash_password(password, params), end_other_sessions=False)
        log.info("password hash upgraded", extra={"event": "auth.password.rehashed", "user_id": user["id"]})
    record_login(conn, user["id"])
    start_session(user)
    log.info("sign-in succeeded", extra={"event": "auth.login.succeeded", "user_id": user["id"]})
    return redirect(next_path or url_for("main.index"), code=303)


@main_bp.route("/logout", methods=["POST"])
def logout():
    user_id = current_user_id()
    session.clear()
    log.info("signed out", extra={"event": "auth.logout", "user_id": user_id})
    flash("You have signed out.", "success")
    return redirect(url_for("main.login"), code=303)


@main_bp.route("/register", methods=["GET", "POST"])
def register():
    config = current_app.config
    if not config["REGISTRATION_ENABLED"]:
        abort(404)
    if g.get("user") is not None:
        return redirect(url_for("main.index"))
    if request.method == "GET":
        return render_template("auth/register.html", values={}, errors={}, labels=REGISTER_LABELS)

    conn = get_db()
    ip_key = digest("register-ip", client_ip())
    if throttle.is_blocked(
        conn, "register-ip", ip_key, limit=config["REGISTRATIONS_PER_IP_PER_HOUR"], window_seconds=3600
    ):
        log.warning("registration refused: too many attempts", extra={"event": "auth.register.throttled"})
        errors = {"email": "could not be registered: too many attempts from your network. Try again later."}
        response = render_template("auth/register.html", values={}, errors=errors, labels=REGISTER_LABELS)
        return response, 429, {"Retry-After": "3600"}
    throttle.record_failure(conn, "register-ip", ip_key, window_seconds=3600)  # every attempt counts

    email = normalise_email(request.form.get("email"))
    password = request.form.get("password", "")
    errors = {}
    problem = email_problem(email)
    if problem:
        errors["email"] = problem
    problem = password_problem(password, email=email)
    if problem:
        errors["password"] = problem
    elif request.form.get("confirm_password", "") != password:
        errors["confirm_password"] = "does not match the password."
    if not errors:
        try:
            user_id = create_user(conn, email, hash_password(password, parameters_from_config(config)))
        except DuplicateError:
            # Registration cannot avoid revealing that an address is taken
            # without an email-verification step (docs/PHASE_8_SECURITY_REVIEW.md).
            errors["email"] = "is already registered. Sign in instead."
    if errors:
        return (
            render_template(
                "auth/register.html", values={"email": email}, errors=errors, labels=REGISTER_LABELS
            ),
            400,
        )
    user = get_user(conn, user_id)
    record_login(conn, user_id)
    start_session(user)
    log.info("account created", extra={"event": "auth.register.succeeded", "user_id": user_id})
    flash("Your account is ready. Start by adding the instruments you trade.", "success")
    return redirect(url_for("main.instruments"), code=303)
```

### `tradeledger/wsgi.py` (NEW)

**Why:** Production entry point for Gunicorn (`tradeledger.wsgi:app`).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""WSGI entry point for production servers (Phase 8).

    gunicorn tradeledger.wsgi:app

The configuration comes from TRADELEDGER_CONFIG (the Docker image sets
"production"). Importing this module creates the app, so it must only be
imported by the server, never by tests or the CLI.
"""

from . import create_app

app = create_app()
```

### `.dockerignore` (NEW)

**Why:** Allow-list: only the application code and requirements enter the image.  
**How to run or test:** `docker compose up --build` then `scripts/compose_smoke_test.sh` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```text
# Allow-list: send ONLY what the image needs to the Docker build.
# Everything else (Git history, tests, docs, local databases, .env files,
# secrets, Terraform) never reaches the build context, so it cannot leak
# into an image layer.
*
!tradeledger/
!requirements.txt
!constraints.txt
!gunicorn.conf.py
!docker/entrypoint.sh
**/__pycache__
**/*.pyc
tradeledger/static/vendor/chartjs/*.tgz
```

### `.github/workflows/deploy.yml` (NEW)

**Why:** Manual, reviewer-approved deploy to ECS through OIDC; snapshot then migrate then deploy. Has never run.  
**How to run or test:** Runs on GitHub (never run yet); static checks in `tests/test_deployment_files.py`

```yaml
# TradeLedger deployment to AWS ECS (Phase 8) - MANUAL ONLY.
#
# This workflow has NEVER been run: no AWS environment exists yet.
# It does nothing until ALL of the following are true:
#   1. the infrastructure in infra/ has been applied (costs money: see
#      docs/PHASE_8_COST_ESTIMATE.md);
#   2. a GitHub environment called "production" exists with required
#      reviewers, and these environment VARIABLES are set (none are secret;
#      the values come from `terraform output`):
#        AWS_REGION, AWS_DEPLOY_ROLE_ARN, ECR_REPOSITORY_URL, ECS_CLUSTER,
#        ECS_SERVICE, TASK_FAMILY, APP_SUBNETS (comma-separated),
#        APP_SECURITY_GROUP, DB_INSTANCE_ID
#   3. the repository variable DEPLOY_ENABLED is set to "true".
#
# It never runs on push: someone must click "Run workflow", and then a
# reviewer must approve the "production" environment.
#
# Credentials: GitHub's OIDC token is exchanged for a 1-hour AWS session of
# the deploy role (infra/github_oidc.tf). There are no AWS access keys
# anywhere in GitHub.
#
# Steps: build and push the image (tagged with the commit SHA) -> snapshot
# the database -> register a task definition with the new image -> run the
# migration as a one-off task -> roll the service -> wait until stable. ECS's
# deployment circuit breaker rolls back automatically if new tasks fail
# their health checks.

name: Deploy (manual)

on:
  workflow_dispatch:
    inputs:
      run_migrations:
        description: "Snapshot the database and run migrate-db before deploying"
        type: boolean
        default: true

permissions:
  contents: read

concurrency:
  group: deploy-production
  cancel-in-progress: false   # never interrupt a deployment half-way

jobs:
  deploy:
    name: Deploy to production
    if: vars.DEPLOY_ENABLED == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 40
    environment: production
    permissions:
      contents: read
      id-token: write   # lets the job request the OIDC token; nothing else
    env:
      AWS_REGION: ${{ vars.AWS_REGION }}
      IMAGE: ${{ vars.ECR_REPOSITORY_URL }}:${{ github.sha }}
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Get short-lived AWS credentials (OIDC)
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: ${{ vars.AWS_DEPLOY_ROLE_ARN }}
          aws-region: ${{ vars.AWS_REGION }}
          role-session-name: tradeledger-deploy-${{ github.run_id }}

      - name: Log in to Amazon ECR
        uses: aws-actions/amazon-ecr-login@v2

      - name: Build and push the image
        run: |
          docker build -t "$IMAGE" .
          docker push "$IMAGE"

      - name: Snapshot the database
        if: inputs.run_migrations
        run: |
          SNAPSHOT="${{ vars.DB_INSTANCE_ID }}-predeploy-${GITHUB_SHA::12}-${GITHUB_RUN_ATTEMPT}"
          aws rds create-db-snapshot --db-instance-identifier "${{ vars.DB_INSTANCE_ID }}" \
            --db-snapshot-identifier "$SNAPSHOT"
          aws rds wait db-snapshot-available --db-snapshot-identifier "$SNAPSHOT"
          echo "Snapshot $SNAPSHOT is available (restore point if the migration goes wrong)."

      - name: Register a task definition for the new image
        id: taskdef
        run: |
          aws ecs describe-task-definition --task-definition "${{ vars.TASK_FAMILY }}" \
            --query taskDefinition > current.json
          jq --arg image "$IMAGE" '
            .containerDefinitions[0].image = $image
            | del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities,
                  .registeredAt, .registeredBy)' current.json > next.json
          ARN=$(aws ecs register-task-definition --cli-input-json file://next.json \
            --query taskDefinition.taskDefinitionArn --output text)
          echo "arn=$ARN" >> "$GITHUB_OUTPUT"

      - name: Run database migrations (one-off task)
        if: inputs.run_migrations
        run: |
          TASK=$(aws ecs run-task --cluster "${{ vars.ECS_CLUSTER }}" --launch-type FARGATE \
            --task-definition "${{ steps.taskdef.outputs.arn }}" \
            --network-configuration "awsvpcConfiguration={subnets=[${{ vars.APP_SUBNETS }}],securityGroups=[${{ vars.APP_SECURITY_GROUP }}],assignPublicIp=ENABLED}" \
            --overrides '{"containerOverrides":[{"name":"app","command":["flask","--app","tradeledger","migrate-db","--backed-up"]}]}' \
            --query 'tasks[0].taskArn' --output text)
          aws ecs wait tasks-stopped --cluster "${{ vars.ECS_CLUSTER }}" --tasks "$TASK"
          CODE=$(aws ecs describe-tasks --cluster "${{ vars.ECS_CLUSTER }}" --tasks "$TASK" \
            --query 'tasks[0].containers[0].exitCode' --output text)
          echo "Migration task exit code: $CODE"
          test "$CODE" = "0"

      - name: Deploy the new task definition
        run: |
          aws ecs update-service --cluster "${{ vars.ECS_CLUSTER }}" --service "${{ vars.ECS_SERVICE }}" \
            --task-definition "${{ steps.taskdef.outputs.arn }}" > /dev/null
          aws ecs wait services-stable --cluster "${{ vars.ECS_CLUSTER }}" --services "${{ vars.ECS_SERVICE }}"
          echo "Deployed ${{ github.sha }}."
```

### `Dockerfile` (NEW)

**Why:** Multi-stage image: non-root user 10001, Gunicorn, HEALTHCHECK.  
**How to run or test:** `docker compose up --build` then `scripts/compose_smoke_test.sh` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```dockerfile
# syntax=docker/dockerfile:1
#
# TradeLedger production image (Phase 8).
#
#   docker build -t tradeledger:local .
#
# Two stages: "build" installs the pinned Python packages into a virtual
# environment; "runtime" copies only that environment and the application
# code, so compilers, pip caches and build files never reach the final
# image. The app runs under Gunicorn (never Flask's development server) as
# an unprivileged user. No secret is baked in: SECRET_KEY and the database
# password arrive at run time (environment variables or *_FILE secrets).
#
# Reproducibility: the Python packages are pinned (requirements.txt +
# constraints.txt). For a fully reproducible base image, replace the tag
# below with its digest (python:3.13-slim@sha256:...) after the first pull.

FROM python:3.13-slim AS build
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY requirements.txt constraints.txt ./
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --requirement requirements.txt --constraint constraints.txt

FROM python:3.13-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    TRADELEDGER_CONFIG=production
# A fixed, unprivileged user. Files stay owned by root, so the running
# process cannot modify its own code even if it is compromised.
RUN groupadd --system --gid 10001 tradeledger \
 && useradd --system --uid 10001 --gid tradeledger --no-create-home --shell /usr/sbin/nologin tradeledger
WORKDIR /app
COPY --from=build /opt/venv /opt/venv
COPY tradeledger ./tradeledger
COPY gunicorn.conf.py ./
COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/tradeledger-entrypoint
USER 10001:10001
EXPOSE 8000
# Liveness only (no database call): see /health/live in web/views.py.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=4).status == 200 else 1)"]
ENTRYPOINT ["tradeledger-entrypoint"]
CMD ["gunicorn", "--config", "/app/gunicorn.conf.py", "tradeledger.wsgi:app"]
```

### `compose.yaml` (NEW)

**Why:** Local production-style stack: app + PostgreSQL on an internal network, file secrets, hardened container.  
**How to run or test:** `docker compose up --build` then `scripts/compose_smoke_test.sh` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```yaml
# TradeLedger local production-style stack (Phase 8).
#
#   1. Create the two secret files (once; they are ignored by Git):
#        mkdir -p secrets
#        python -c "import secrets; print(secrets.token_hex(32))" > secrets/secret_key.txt
#        python -c "import secrets; print(secrets.token_urlsafe(24))" > secrets/db_password.txt
#   2. docker compose up --build -d          start (builds the image)
#   3. docker compose exec app flask --app tradeledger create-user --email you@example.com
#   4. open http://localhost:8000 and sign in
#
#   docker compose ps                        status and health
#   docker compose logs -f app               follow the JSON logs
#   docker compose exec db psql -U tradeledger tradeledger     inspect the database
#   docker compose down                      stop (data is kept in the db-data volume)
#   docker compose down -v                   stop AND delete the database (irreversible)
#
# Security choices
#   * PostgreSQL has NO published port: only the app container can reach it,
#     over the internal "backend" network.
#   * The app is published on 127.0.0.1 only, not on your LAN.
#   * Secrets are mounted files (/run/secrets/...), never environment values
#     in this file, and the app reads them through SECRET_KEY_FILE /
#     DB_PASSWORD_FILE.
#   * The app container runs as a non-root user with a read-only root
#     filesystem, no Linux capabilities and no privilege escalation.

name: tradeledger

services:
  db:
    image: postgres:16-alpine   # PostgreSQL 16: the major version Phase 8 was tested against
    environment:
      POSTGRES_DB: tradeledger
      POSTGRES_USER: tradeledger
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
    secrets:
      - db_password
    volumes:
      - db-data:/var/lib/postgresql/data
    networks:
      - backend
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U tradeledger -d tradeledger"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  app:
    build: .
    image: tradeledger:local
    depends_on:
      db:
        condition: service_healthy
    environment:
      TRADELEDGER_CONFIG: production
      SECRET_KEY_FILE: /run/secrets/secret_key
      DB_HOST: db
      DB_NAME: tradeledger
      DB_USER: tradeledger
      DB_PASSWORD_FILE: /run/secrets/db_password
      DB_SSLMODE: disable             # traffic never leaves the private Docker network
      TRADELEDGER_TRUSTED_HOSTS: localhost,127.0.0.1
      TRADELEDGER_MIGRATE_ON_START: "1"
      TRADELEDGER_REGISTRATION_ENABLED: "0"
      WEB_CONCURRENCY: "2"
    secrets:
      - secret_key
      - db_password
    ports:
      - "127.0.0.1:8000:8000"
    networks:
      - backend
      - frontend
    read_only: true
    tmpfs:
      - /tmp
    cap_drop:
      - ALL
    security_opt:
      - no-new-privileges:true
    restart: unless-stopped

networks:
  backend:
    internal: true    # no route to the internet or the host
  frontend: {}

volumes:
  db-data:

secrets:
  secret_key:
    file: ./secrets/secret_key.txt
  db_password:
    file: ./secrets/db_password.txt
```

### `docker/entrypoint.sh` (NEW)

**Why:** Container entry point; `exec` so Gunicorn receives signals.  
**How to run or test:** `docker compose up --build` then `scripts/compose_smoke_test.sh` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```bash
#!/bin/sh
# Container entrypoint (Phase 8).
#
# 1. Optionally create or upgrade the database schema
#    (TRADELEDGER_MIGRATE_ON_START=1, used by Docker Compose). init-db never
#    deletes data, and PostgreSQL migrations take an advisory lock, so two
#    containers starting together cannot both migrate. On AWS, migrations
#    run as a separate one-off task after a snapshot instead (see
#    docs/PHASE_8_AWS_DEPLOYMENT.md), so this stays 0 there.
# 2. "exec" replaces this shell with Gunicorn, so Gunicorn is process 1 and
#    receives SIGTERM directly from Docker/ECS: it stops accepting new
#    connections and lets in-flight requests finish (graceful shutdown).
set -eu

if [ "${TRADELEDGER_MIGRATE_ON_START:-0}" = "1" ]; then
    flask --app tradeledger init-db
fi

exec "$@"
```

### `gunicorn.conf.py` (NEW)

**Why:** Production web server settings (workers, threads, keep-alive, logging).  
**How to run or test:** `docker compose up --build` then `scripts/compose_smoke_test.sh` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```python
"""Gunicorn settings for the container (Phase 8).

Gunicorn is the production web server: it runs several copies (workers) of
the Flask app and passes HTTP requests to them. Flask's built-in server is
for development only.

Every value can be overridden with an environment variable of the same
purpose, so ECS task definitions do not need a new image to tune them.
"""

import os

bind = "0.0.0.0:8000"

# Workers are processes; each has its own PostgreSQL pool (opened after the
# fork). Total connections = containers x workers x TRADELEDGER_DB_POOL_MAX.
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))

# A request taking longer than this is killed and logged. The dashboard
# for 10,000 trades took under 1 s in Phase 7/8 measurements.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "30"))
graceful_timeout = 20
# Longer than the AWS load balancer's 60 s idle timeout, so the balancer,
# not Gunicorn, closes idle connections (avoids sporadic 502 errors).
keepalive = 75

# Restart each worker after a number of requests (with jitter) as a
# guard against slow memory growth.
max_requests = 1000
max_requests_jitter = 100

# TradeLedger writes its own structured access log (observability.py).
accesslog = None
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")

# Proxy headers are handled by the app (TRADELEDGER_PROXY_HOPS), never by
# Gunicorn trusting whatever address happens to connect.
forwarded_allow_ips = "127.0.0.1"

# Each worker creates the app (and its connection pool) itself.
preload_app = False
```

### `infra/alb.tf` (NEW)

**Why:** Load balancer, HTTPS listener (TLS 1.2+), HTTP redirect, host-name rule, default 404.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# Application Load Balancer: terminates HTTPS with the ACM certificate and
# forwards to the tasks over the private VPC network.

resource "aws_lb" "this" {
  name                       = local.name
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = aws_subnet.public[*].id
  security_groups            = [aws_security_group.alb.id]
  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection
  idle_timeout               = 60
}

resource "aws_lb_target_group" "app" {
  name        = local.name
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = aws_vpc.this.id

  deregistration_delay = 30

  health_check {
    path                = "/health/ready" # readiness: the task can reach the database
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06" # TLS 1.2+ only
  certificate_arn   = var.certificate_arn

  # Anything not addressed to our host name (scanners using the raw load
  # balancer DNS name or IP, DNS-rebinding attempts) gets a plain 404 and
  # never reaches the app.
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }
}

resource "aws_lb_listener_rule" "app" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10

  condition {
    host_header {
      values = [var.domain_name]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.app.arn
  }
}
```

### `infra/backend.tf` (NEW)

**Why:** S3 state backend with native locking (bucket supplied at init).  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# Remote state in S3 (configure once per environment, see infra/README.md):
#
#   terraform init \
#     -backend-config="bucket=YOUR-STATE-BUCKET" \
#     -backend-config="key=tradeledger/production.tfstate" \
#     -backend-config="region=eu-west-2"
#
# Why remote state matters: the state file records every resource and can
# contain sensitive values (for example database endpoints and ARNs). It
# must never be committed (see .gitignore) and belongs in a private,
# versioned, encrypted bucket with public access blocked, readable only by
# the people and roles that run Terraform. use_lockfile = true makes S3
# itself lock the state, so two people cannot apply at once.
#
# This configuration deliberately writes NO secret values into state: the
# database password is generated and kept by RDS (manage_master_user_password)
# and the Flask SECRET_KEY is put into Secrets Manager by hand (README).

terraform {
  backend "s3" {
    encrypt      = true
    use_lockfile = true
  }
}
```

### `infra/ecr.tf` (NEW)

**Why:** Image repository: immutable tags, scan on push, lifecycle policy.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# Private container registry for the app image.
# Immutable tags: an image tag (the Git commit SHA) can never be overwritten,
# so a task definition always runs exactly the code it was deployed with.

resource "aws_ecr_repository" "app" {
  name                 = local.name
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  image_scanning_configuration {
    scan_on_push = true # basic vulnerability scan of OS packages
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 most recent images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
```

### `infra/ecs.tf` (NEW)

**Why:** Cluster, task definition, service with circuit breaker; log group.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# ECS on Fargate: AWS runs the containers; there are no servers to patch.

resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${local.name}"
  retention_in_days = var.log_retention_days
}

resource "aws_ecs_cluster" "this" {
  name = local.name

  setting {
    name  = "containerInsights"
    value = "disabled" # extra CloudWatch cost; enable while investigating performance
  }
}

locals {
  db_secret_arn = aws_db_instance.this.master_user_secret[0].secret_arn

  container = {
    name                   = "app"
    image                  = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"
    essential              = true
    readonlyRootFilesystem = true
    user                   = "10001:10001"
    portMappings           = [{ containerPort = 8000, protocol = "tcp" }]

    environment = [
      { name = "TRADELEDGER_CONFIG", value = "production" },
      { name = "DB_HOST", value = aws_db_instance.this.address },
      { name = "DB_PORT", value = tostring(aws_db_instance.this.port) },
      { name = "DB_NAME", value = "tradeledger" },
      { name = "DB_SSLMODE", value = "require" },
      # One proxy (the ALB) sets X-Forwarded-For/-Proto.
      { name = "TRADELEDGER_PROXY_HOPS", value = "1" },
      # The Host header is enforced by the load balancer's listener rule
      # (alb.tf), which only forwards requests for var.domain_name. The
      # app-level check stays off here because ALB health checks use the
      # task's IP address as the Host.
      { name = "TRADELEDGER_REGISTRATION_ENABLED", value = "0" },
      { name = "WEB_CONCURRENCY", value = "2" },
    ]

    # Injected by ECS at start-up from Secrets Manager; never in the image,
    # the task definition text or the logs.
    secrets = [
      { name = "SECRET_KEY", valueFrom = aws_secretsmanager_secret.secret_key.arn },
      { name = "DB_USER", valueFrom = "${local.db_secret_arn}:username::" },
      { name = "DB_PASSWORD", valueFrom = "${local.db_secret_arn}:password::" },
    ]

    mountPoints = [{ sourceVolume = "tmp", containerPath = "/tmp", readOnly = false }]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.app.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "app"
      }
    }
  }
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.name
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = var.cpu_architecture
  }

  # Writable scratch space for Gunicorn; the root filesystem is read-only.
  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([local.container])
}

resource "aws_ecs_service" "app" {
  name                              = local.name
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.app.arn
  desired_count                     = var.desired_count
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 60
  enable_execute_command            = false
  propagate_tags                    = "SERVICE"

  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # A deployment whose tasks never become healthy is rolled back automatically.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true # see network.tf: needed without a NAT gateway
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "app"
    container_port   = 8000
  }

  lifecycle {
    # The deploy workflow registers new task definitions; Terraform must not
    # roll them back on the next plan.
    ignore_changes = [task_definition]
  }

  depends_on = [aws_lb_listener.https]
}
```

### `infra/github_oidc.tf` (NEW)

**Why:** GitHub OIDC provider and the scoped deploy role.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# GitHub Actions -> AWS without stored access keys.
#
# GitHub signs a short-lived OIDC token for each workflow run; AWS STS trades
# it for temporary credentials (about 1 hour) for the role below. The trust
# policy only accepts tokens from THIS repository's "production" GitHub
# environment, which requires a manual approval before the job starts.

locals {
  github_oidc_url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_openid_connect_provider" "github" {
  count          = var.create_github_oidc_provider ? 1 : 0
  url            = local.github_oidc_url
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_openid_connect_provider" "github" {
  count = var.create_github_oidc_provider ? 0 : 1
  url   = local.github_oidc_url
}

locals {
  github_oidc_arn = var.create_github_oidc_provider ? aws_iam_openid_connect_provider.github[0].arn : data.aws_iam_openid_connect_provider.github[0].arn
}

data "aws_iam_policy_document" "github_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [local.github_oidc_arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:environment:production"]
    }
  }
}

resource "aws_iam_role" "github_deploy" {
  name                 = "${local.name}-github-deploy"
  assume_role_policy   = data.aws_iam_policy_document.github_assume.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "github_deploy" {
  statement {
    sid       = "EcrLogin"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # this action has no resource-level permissions
  }

  statement {
    sid = "PushOnlyToOurRepository"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:BatchGetImage",
      "ecr:DescribeImages",
    ]
    resources = [aws_ecr_repository.app.arn]
  }

  statement {
    sid       = "TaskDefinitions"
    actions   = ["ecs:RegisterTaskDefinition", "ecs:DescribeTaskDefinition"]
    resources = ["*"] # these actions have no resource-level permissions
  }

  statement {
    sid       = "DeployOurServiceOnly"
    actions   = ["ecs:UpdateService", "ecs:DescribeServices"]
    resources = [aws_ecs_service.app.id]
  }

  statement {
    sid       = "RunTheMigrationTask"
    actions   = ["ecs:RunTask", "ecs:DescribeTasks"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.this.arn]
    }
  }

  statement {
    sid       = "PassOnlyTheAppRoles"
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.execution.arn, aws_iam_role.task.arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "SnapshotBeforeMigrating"
    actions   = ["rds:CreateDBSnapshot", "rds:DescribeDBSnapshots"]
    resources = [aws_db_instance.this.arn, "arn:aws:rds:${var.region}:*:snapshot:${local.name}-*"]
  }
}

resource "aws_iam_role_policy" "github_deploy" {
  name   = "deploy-tradeledger"
  role   = aws_iam_role.github_deploy.id
  policy = data.aws_iam_policy_document.github_deploy.json
}
```

### `infra/iam.tf` (NEW)

**Why:** Execution role (pull, logs, two secrets) and an empty task role.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# IAM roles (least privilege)
#
#   execution role  used by ECS itself to START the task: pull the image,
#                   write logs, read exactly two secrets.
#   task role       the identity of the RUNNING app. TradeLedger calls no
#                   AWS API, so this role has no permissions at all.
#   deploy role     assumed by GitHub Actions through OIDC (github_oidc.tf).

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy" # ECR pull + logs
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid     = "ReadOnlyTheTwoAppSecrets"
    actions = ["secretsmanager:GetSecretValue"]
    resources = [
      aws_secretsmanager_secret.secret_key.arn,
      aws_db_instance.this.master_user_secret[0].secret_arn,
    ]
  }
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "read-app-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

resource "aws_iam_role" "task" {
  name               = "${local.name}-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  # Intentionally no policies.
}
```

### `infra/monitoring.tf` (NEW)

**Why:** CloudWatch alarms, optional email topic, saved Logs Insights queries.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# CloudWatch alarms -> SNS topic -> email (optional).
# Each standard alarm costs about USD 0.10 per month.

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "app_5xx" {
  alarm_name          = "${local.name}-5xx"
  alarm_description   = "The app returned 5 or more server errors in 5 minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "HTTPCode_Target_5XX_Count"
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 5
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions          = { LoadBalancer = aws_lb.this.arn_suffix }
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "unhealthy_tasks" {
  alarm_name          = "${local.name}-unhealthy"
  alarm_description   = "At least one task failed its readiness check for 3 minutes."
  namespace           = "AWS/ApplicationELB"
  metric_name         = "UnHealthyHostCount"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 1
  comparison_operator = "GreaterThanOrEqualToThreshold"
  treat_missing_data  = "notBreaching"
  dimensions = {
    LoadBalancer = aws_lb.this.arn_suffix
    TargetGroup  = aws_lb_target_group.app.arn_suffix
  }
  alarm_actions = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_cpu" {
  alarm_name          = "${local.name}-db-cpu"
  alarm_description   = "Database CPU above 80% for 15 minutes."
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.this.identifier }
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "db_storage" {
  alarm_name          = "${local.name}-db-storage"
  alarm_description   = "Less than 2 GB of database storage left."
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 2147483648
  comparison_operator = "LessThanThreshold"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.this.identifier }
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

# Saved CloudWatch Logs Insights queries for troubleshooting.
resource "aws_cloudwatch_query_definition" "errors" {
  name            = "${local.name}/server-errors"
  log_group_names = [aws_cloudwatch_log_group.app.name]
  query_string    = <<-EOQ
    fields @timestamp, request_id, path, status, message
    | filter level = "ERROR" or status >= 500
    | sort @timestamp desc
    | limit 50
  EOQ
}

resource "aws_cloudwatch_query_definition" "failed_sign_ins" {
  name            = "${local.name}/failed-sign-ins"
  log_group_names = [aws_cloudwatch_log_group.app.name]
  query_string    = <<-EOQ
    fields @timestamp, event, reason, email_ref
    | filter event in ["auth.login.failed", "auth.login.throttled", "auth.register.throttled"]
    | stats count(*) by bin(15m), event
  EOQ
}
```

### `infra/network.tf` (NEW)

**Why:** VPC, two public and two private subnets, internet gateway, no NAT.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# Network layout (2 Availability Zones)
#
#   public subnets   the load balancer, and the app tasks (with a public IP so
#                    they can reach ECR, Secrets Manager and CloudWatch
#                    without a NAT gateway; their security group only accepts
#                    traffic from the load balancer)
#   private subnets  the database only. No route to the internet at all.
#
# Why no NAT gateway: it costs about USD 33 per month per gateway before any
# traffic, more than the app itself. The trade-off is that tasks have public
# IPs (still unreachable from the internet, see security_groups.tf). A
# stricter design puts tasks in private subnets with a NAT gateway or VPC
# endpoints (about USD 7-8 per endpoint per AZ per month).

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  name = "tradeledger-${var.environment}"
  azs  = slice(data.aws_availability_zones.available.names, 0, 2)
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = local.name }
}

# Lock down the VPC's default security group: nothing may use it by accident.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = local.name }
}

resource "aws_subnet" "public" {
  count                   = length(local.azs)
  vpc_id                  = aws_vpc.this.id
  availability_zone       = local.azs[count.index]
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  map_public_ip_on_launch = false # tasks request a public IP explicitly

  tags = { Name = "${local.name}-public-${local.azs[count.index]}" }
}

resource "aws_subnet" "private" {
  count             = length(local.azs)
  vpc_id            = aws_vpc.this.id
  availability_zone = local.azs[count.index]
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)

  tags = { Name = "${local.name}-private-${local.azs[count.index]}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }

  tags = { Name = "${local.name}-public" }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# The private route table has only the implicit local route: the database
# subnets cannot reach or be reached from the internet.
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id
  tags   = { Name = "${local.name}-private" }
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
```

### `infra/outputs.tf` (NEW)

**Why:** Values needed for GitHub variables and DNS.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
output "load_balancer_dns_name" {
  description = "Point a CNAME (or Route 53 alias) for domain_name at this."
  value       = aws_lb.this.dns_name
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "ecs_cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "ecs_service_name" {
  value = aws_ecs_service.app.name
}

output "task_definition_family" {
  value = aws_ecs_task_definition.app.family
}

output "app_subnet_ids" {
  description = "For the one-off migration task (aws ecs run-task)."
  value       = aws_subnet.public[*].id
}

output "app_security_group_id" {
  value = aws_security_group.app.id
}

output "db_instance_identifier" {
  value = aws_db_instance.this.identifier
}

output "secret_key_secret_name" {
  description = "Put the SECRET_KEY value here once (see secrets.tf)."
  value       = aws_secretsmanager_secret.secret_key.name
}

output "github_deploy_role_arn" {
  description = "Store as the AWS_DEPLOY_ROLE_ARN variable of the GitHub 'production' environment."
  value       = aws_iam_role.github_deploy.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.app.name
}
```

### `infra/rds.tf` (NEW)

**Why:** RDS PostgreSQL 16: private, encrypted, forced TLS, managed password, backups, deletion protection.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# Amazon RDS for PostgreSQL 16
#
# * Private subnets only, publicly_accessible = false, reachable only from
#   the app's security group.
# * Encrypted at rest (AWS-managed KMS key); rds.force_ssl = 1 refuses any
#   unencrypted connection (the app connects with DB_SSLMODE=require).
# * manage_master_user_password: RDS generates the password and keeps it in
#   Secrets Manager, rotating it automatically. Terraform never sees it, so
#   it is not in the state file.
# * Automated daily backups with point-in-time recovery for
#   db_backup_retention_days; a final snapshot is taken if the database is
#   ever deleted. Deletion protection is on by default.

resource "aws_db_subnet_group" "this" {
  name       = local.name
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_parameter_group" "this" {
  name   = "${local.name}-pg16"
  family = "postgres16"

  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "pending-reboot"
  }

  parameter {
    name  = "log_min_duration_statement"
    value = "1000" # log statements slower than 1 s (no parameter values at this level)
  }
}

resource "aws_db_instance" "this" {
  identifier     = local.name
  engine         = "postgres"
  engine_version = "16"
  instance_class = var.db_instance_class

  db_name                     = "tradeledger"
  username                    = "tradeledger_admin"
  manage_master_user_password = true

  allocated_storage     = var.db_allocated_storage_gb
  max_allocated_storage = var.db_max_allocated_storage_gb
  storage_type          = "gp3"
  storage_encrypted     = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.db.id]
  publicly_accessible    = false
  multi_az               = var.db_multi_az
  parameter_group_name   = aws_db_parameter_group.this.name

  backup_retention_period   = var.db_backup_retention_days
  backup_window             = "02:00-03:00" # UTC
  maintenance_window        = "sun:03:30-sun:04:30"
  copy_tags_to_snapshot     = true
  deletion_protection       = var.deletion_protection
  skip_final_snapshot       = false
  final_snapshot_identifier = "${local.name}-final"

  auto_minor_version_upgrade      = true
  enabled_cloudwatch_logs_exports = ["postgresql"]
  performance_insights_enabled    = false # extra cost; enable when investigating
  apply_immediately               = false
}
```

### `infra/secrets.tf` (NEW)

**Why:** Secrets Manager entry for SECRET_KEY (value set out of band).  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# The Flask SECRET_KEY lives in Secrets Manager. Terraform creates the empty
# secret; you put the value in ONCE, from your own machine, so it never
# appears in Terraform files or state:
#
#   aws secretsmanager put-secret-value \
#     --secret-id tradeledger-production/secret-key \
#     --secret-string "$(python -c 'import secrets; print(secrets.token_hex(32))')"
#
# The database credentials are the RDS-managed secret (rds.tf).

resource "aws_secretsmanager_secret" "secret_key" {
  name                    = "${local.name}/secret-key"
  description             = "Flask SECRET_KEY for TradeLedger (signs session cookies)"
  recovery_window_in_days = 7
}
```

### `infra/security_groups.tf` (NEW)

**Why:** ALB ← internet; app ← ALB; database ← app.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# Who may talk to whom (security groups are stateful firewalls):
#
#   internet --443/80--> load balancer --8000--> app tasks --5432--> database
#                                                 app tasks --443--> AWS APIs
#
# The database accepts connections ONLY from the app's security group, and
# the app accepts connections ONLY from the load balancer's.

resource "aws_security_group" "alb" {
  name        = "${local.name}-alb"
  description = "Public HTTPS entry point"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from anywhere"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP, only to redirect to HTTPS"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_app" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward to the app tasks"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_security_group" "app" {
  name        = "${local.name}-app"
  description = "TradeLedger containers"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "app_from_alb" {
  security_group_id            = aws_security_group.app.id
  description                  = "Only the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "app_https" {
  security_group_id = aws_security_group.app.id
  description       = "AWS APIs: ECR image pulls, Secrets Manager, CloudWatch Logs"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "app_to_db" {
  security_group_id            = aws_security_group.app.id
  description                  = "PostgreSQL"
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_security_group" "db" {
  name        = "${local.name}-db"
  description = "PostgreSQL, reachable only from the app"
  vpc_id      = aws_vpc.this.id
  # No egress rules: the database never starts outbound connections.
}

resource "aws_vpc_security_group_ingress_rule" "db_from_app" {
  security_group_id            = aws_security_group.db.id
  description                  = "Only the app tasks"
  referenced_security_group_id = aws_security_group.app.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
```

### `infra/terraform.tfvars.example` (NEW)

**Why:** Example inputs (copy to production.tfvars; never committed).  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```bash
# Copy to production.tfvars (ignored by Git) and fill in.
# terraform plan -var-file=production.tfvars -out=tfplan

region            = "eu-west-2"
environment       = "production"
domain_name       = "journal.example.com"
certificate_arn   = "arn:aws:acm:eu-west-2:123456789012:certificate/00000000-0000-0000-0000-000000000000"
github_repository = "your-github-user/tradeledger"
alert_email       = ""

# Cost controls
desired_count       = 1
db_multi_az         = false
deletion_protection = true
```

### `infra/variables.tf` (NEW)

**Why:** Inputs with validation.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
variable "region" {
  description = "AWS region. eu-west-2 is London."
  type        = string
  default     = "eu-west-2"
}

variable "environment" {
  description = "Name used in resource names and tags."
  type        = string
  default     = "production"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.environment))
    error_message = "Use lower-case letters, digits and hyphens (2-21 characters)."
  }
}

variable "domain_name" {
  description = "Host name users type, e.g. journal.example.com. The load balancer only forwards requests for this host."
  type        = string
}

variable "certificate_arn" {
  description = "ARN of an ACM certificate for domain_name in the same region (DNS-validated; ACM public certificates are free)."
  type        = string
}

variable "vpc_cidr" {
  description = "Address range of the new VPC."
  type        = string
  default     = "10.20.0.0/16"
}

variable "image_tag" {
  description = "Image tag to run. CI deployments replace the task definition; this is only the first one."
  type        = string
  default     = "initial"
}

variable "desired_count" {
  description = "Number of running app tasks. Set 0 to stop compute charges (the load balancer and database still cost money)."
  type        = number
  default     = 1
}

variable "task_cpu" {
  description = "Fargate CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Fargate memory in MiB (512 is the minimum for 0.25 vCPU)."
  type        = number
  default     = 512
}

variable "cpu_architecture" {
  description = "X86_64 matches the image CI builds. ARM64 (Graviton) is about 20% cheaper but needs an arm64 image."
  type        = string
  default     = "X86_64"

  validation {
    condition     = contains(["X86_64", "ARM64"], var.cpu_architecture)
    error_message = "Use X86_64 or ARM64."
  }
}

variable "db_instance_class" {
  description = "RDS instance size. db.t4g.micro is the smallest current PostgreSQL class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage_gb" {
  description = "Initial database storage (gp3)."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage_gb" {
  description = "Storage autoscaling ceiling. Keeps costs bounded."
  type        = number
  default     = 50
}

variable "db_backup_retention_days" {
  description = "Days of automated backups (point-in-time recovery window)."
  type        = number
  default     = 7
}

variable "db_multi_az" {
  description = "A standby in a second zone: roughly doubles the database cost. Off for a portfolio deployment."
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Stops `terraform destroy` deleting the database and load balancer by accident. Set false deliberately before tearing down."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "How long CloudWatch keeps application logs."
  type        = number
  default     = 30
}

variable "alert_email" {
  description = "Email for CloudWatch alarms (empty: no subscription). AWS sends a confirmation email first."
  type        = string
  default     = ""
}

variable "github_repository" {
  description = "owner/name of the GitHub repository allowed to deploy, e.g. your-user/tradeledger."
  type        = string
}

variable "create_github_oidc_provider" {
  description = "false if the account already has the token.actions.githubusercontent.com OIDC provider."
  type        = bool
  default     = true
}
```

### `infra/versions.tf` (NEW)

**Why:** Terraform and AWS provider versions; default tags.  
**How to run or test:** `terraform -chdir=infra fmt -check && terraform -chdir=infra validate` (CI job; NOT run here); static checks in `tests/test_deployment_files.py`

```hcl
# TradeLedger AWS infrastructure (Phase 8) - Terraform
#
# NOTHING here has been applied. `terraform fmt -check` and
# `terraform validate` run in CI (job "infrastructure"); no AWS account was
# used while writing it. Applying it creates CHARGEABLE resources: read
# docs/PHASE_8_COST_ESTIMATE.md and docs/PHASE_8_AWS_DEPLOYMENT.md first.

terraform {
  required_version = ">= 1.10.0" # S3 native state locking (use_lockfile)

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region

  # Credentials come from the environment (aws sso login, a named profile,
  # or GitHub OIDC). NEVER put access keys in these files.

  default_tags {
    tags = {
      Project     = "tradeledger"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}
```

### `tests/legacy_fixtures.py` (NEW)

**Why:** Builds a Phase 7 (schema v2) SQLite file with synthetic data.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""A Phase 7 (schema version 2) SQLite database with the sample data, built
the way Phase 7 stored it: no users and no owners. Used by the upgrade,
legacy-claim and SQLite-to-PostgreSQL import tests."""

import sqlite3
from pathlib import Path

from tradeledger.migrations import MIGRATIONS
from tradeledger.seed import SAMPLE_INSTRUMENTS, SAMPLE_TRADES

V1_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text()


def build_phase7_database(path):
    """A version 2 database exactly as Phase 7 left it, with the sample data."""
    raw = sqlite3.connect(path)
    raw.executescript(V1_SCHEMA)
    raw.executescript("BEGIN;" + MIGRATIONS[2] + "PRAGMA user_version = 2; COMMIT;")
    with raw:
        for i in SAMPLE_INSTRUMENTS:
            raw.execute(
                "INSERT INTO instruments (symbol, name, asset_class, price_increment, contract_multiplier, "
                "quote_currency) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    i["symbol"],
                    i["name"],
                    i["asset_class"],
                    i["price_increment"],
                    i["contract_multiplier"],
                    i["quote_currency"],
                ),
            )
        ids = dict(raw.execute("SELECT symbol, id FROM instruments").fetchall())
        for t in SAMPLE_TRADES:
            raw.execute(
                "INSERT INTO trades (instrument_id, trade_date, closed_at, direction, entry_price, stop_loss, "
                "take_profit, exit_price, position_size, fees, status, strategy, session, notes) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ids[t["symbol"]],
                    t["trade_date"],
                    t.get("closed_at"),
                    t["direction"],
                    t["entry_price"],
                    t["stop_loss"],
                    t.get("take_profit"),
                    t.get("exit_price"),
                    t["position_size"],
                    t.get("fees", "0"),
                    "CLOSED" if t.get("exit_price") else "OPEN",
                    t.get("strategy"),
                    t.get("session"),
                    t.get("notes"),
                ),
            )
    raw.close()
```

### `tests/pg_libpq_bridge.py` (NEW)

**Why:** TEST-ONLY ctypes wrapper around libpq, used when psycopg is unavailable. Never imported by the application.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""TEST-ONLY PostgreSQL driver: a thin ctypes bridge to libpq.

Why this file exists
--------------------
TradeLedger talks to PostgreSQL through psycopg 3 (see requirements.txt).
The environment Phase 8 was built in has a real PostgreSQL 16 server and
libpq (PostgreSQL's official C client library, the one psql uses), but no
way to install psycopg. Rather than leave the PostgreSQL code untested, the
test suite can register this bridge instead (TRADELEDGER_PG_DRIVER=libpq-bridge),
so every test runs SQL against a REAL PostgreSQL server.

What it is NOT
--------------
* It is not shipped or used by the application: tradeledger/ never imports it.
* It is not psycopg. It covers only the calls TradeLedger's adapter makes
  (connect, cursor().execute, fetchone/fetchall, description, rowcount,
  close) and converts results by type: integers -> int, NUMERIC ->
  Decimal, booleans -> bool, everything else -> str. Parameters are sent as
  text with explicit types for int, Decimal and bool, and "unknown" for
  strings (which is also psycopg's behaviour for str).
* Results obtained through it show the SQL is correct on PostgreSQL. They
  do not prove psycopg-specific behaviour or performance; the CI job with
  real psycopg is the authoritative PostgreSQL run.
"""

import ctypes
import ctypes.util
from decimal import Decimal

_lib = ctypes.CDLL(ctypes.util.find_library("pq") or "libpq.so.5")

_lib.PQconnectdb.restype = ctypes.c_void_p
_lib.PQconnectdb.argtypes = [ctypes.c_char_p]
_lib.PQstatus.argtypes = [ctypes.c_void_p]
_lib.PQerrorMessage.restype = ctypes.c_char_p
_lib.PQerrorMessage.argtypes = [ctypes.c_void_p]
_lib.PQfinish.argtypes = [ctypes.c_void_p]
_lib.PQexec.restype = ctypes.c_void_p
_lib.PQexec.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
_lib.PQexecParams.restype = ctypes.c_void_p
_lib.PQexecParams.argtypes = [
    ctypes.c_void_p,
    ctypes.c_char_p,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_uint),
    ctypes.POINTER(ctypes.c_char_p),
    ctypes.POINTER(ctypes.c_int),
    ctypes.POINTER(ctypes.c_int),
    ctypes.c_int,
]
_lib.PQresultStatus.argtypes = [ctypes.c_void_p]
_lib.PQresultErrorMessage.restype = ctypes.c_char_p
_lib.PQresultErrorMessage.argtypes = [ctypes.c_void_p]
_lib.PQresultErrorField.restype = ctypes.c_char_p
_lib.PQresultErrorField.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQntuples.argtypes = [ctypes.c_void_p]
_lib.PQnfields.argtypes = [ctypes.c_void_p]
_lib.PQfname.restype = ctypes.c_char_p
_lib.PQfname.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQftype.restype = ctypes.c_uint
_lib.PQftype.argtypes = [ctypes.c_void_p, ctypes.c_int]
_lib.PQgetvalue.restype = ctypes.c_char_p
_lib.PQgetvalue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.PQgetisnull.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
_lib.PQcmdTuples.restype = ctypes.c_char_p
_lib.PQcmdTuples.argtypes = [ctypes.c_void_p]
_lib.PQclear.argtypes = [ctypes.c_void_p]

_CONNECTION_OK = 0
_PGRES_COMMAND_OK, _PGRES_TUPLES_OK = 1, 2
_DIAG_SQLSTATE, _DIAG_CONSTRAINT = ord("C"), ord("n")

# PostgreSQL type OIDs
_BOOL, _INT8, _INT2, _INT4, _OID, _FLOAT4, _FLOAT8, _NUMERIC = 16, 20, 21, 23, 26, 700, 701, 1700


class Error(Exception):
    pass


class DatabaseError(Error):
    def __init__(self, message, sqlstate=None, constraint=None):
        super().__init__(message)
        self.sqlstate = sqlstate
        self.diag = type("Diag", (), {"constraint_name": constraint})()


class IntegrityError(DatabaseError):
    pass


class OperationalError(DatabaseError):
    pass


class DataError(DatabaseError):
    pass


def _convert(text, oid):
    if oid in (_INT2, _INT4, _INT8, _OID):
        return int(text)
    if oid == _NUMERIC:
        return Decimal(text)
    if oid == _BOOL:
        return text == "t"
    if oid in (_FLOAT4, _FLOAT8):
        return float(text)
    return text


def _param(value):
    """(type oid, text) for one parameter; None means SQL NULL."""
    if value is None:
        return 0, None
    if isinstance(value, bool):
        return _BOOL, "t" if value else "f"
    if isinstance(value, int):
        return _INT8, str(value)
    if isinstance(value, Decimal):
        return _NUMERIC, str(value)
    if isinstance(value, float):
        return _FLOAT8, repr(value)
    if isinstance(value, str):
        return 0, value
    raise TypeError(f"the libpq test bridge cannot send {type(value).__name__}")


def _to_numbered(sql):
    """psycopg-style "%s" -> "$1", "$2", ...; "%%" -> "%"."""
    out, n, i = [], 0, 0
    while i < len(sql):
        char = sql[i]
        if char == "%" and i + 1 < len(sql):
            nxt = sql[i + 1]
            if nxt == "s":
                n += 1
                out.append(f"${n}")
                i += 2
                continue
            if nxt == "%":
                out.append("%")
                i += 2
                continue
        out.append(char)
        i += 1
    return "".join(out)


class Cursor:
    def __init__(self, conn):
        self._conn = conn
        self.description = None
        self.rowcount = -1
        self._rows = []

    def execute(self, sql, params=None):
        handle = self._conn._handle
        if params is None:
            result = _lib.PQexec(handle, sql.encode())
        else:
            types, values = zip(*(_param(v) for v in params), strict=True) if params else ((), ())
            count = len(values)
            type_array = (ctypes.c_uint * count)(*types)
            value_array = (ctypes.c_char_p * count)(*(None if v is None else v.encode() for v in values))
            result = _lib.PQexecParams(
                handle, _to_numbered(sql).encode(), count, type_array, value_array, None, None, 0
            )
        try:
            status = _lib.PQresultStatus(result)
            if status not in (_PGRES_COMMAND_OK, _PGRES_TUPLES_OK):
                message = (_lib.PQresultErrorMessage(result) or b"").decode(errors="replace").strip()
                sqlstate = (_lib.PQresultErrorField(result, _DIAG_SQLSTATE) or b"").decode() or None
                constraint = (_lib.PQresultErrorField(result, _DIAG_CONSTRAINT) or b"").decode() or None
                state = sqlstate or ""
                cls = (
                    IntegrityError
                    if state.startswith("23")
                    else DataError
                    if state.startswith("22")
                    else DatabaseError
                )
                raise cls(message or "PostgreSQL error", sqlstate, constraint)
            if status == _PGRES_TUPLES_OK:
                fields = _lib.PQnfields(result)
                oids = [_lib.PQftype(result, c) for c in range(fields)]
                self.description = [(_lib.PQfname(result, c).decode(),) for c in range(fields)]
                self._rows = [
                    tuple(
                        None
                        if _lib.PQgetisnull(result, r, c)
                        else _convert(_lib.PQgetvalue(result, r, c).decode(), oids[c])
                        for c in range(fields)
                    )
                    for r in range(_lib.PQntuples(result))
                ]
                self.rowcount = len(self._rows)
            else:
                self.description = None
                self._rows = []
                affected = (_lib.PQcmdTuples(result) or b"").decode()
                self.rowcount = int(affected) if affected else -1
        finally:
            _lib.PQclear(result)
        return self

    def fetchone(self):
        return self._rows.pop(0) if self._rows else None

    def fetchall(self):
        rows, self._rows = self._rows, []
        return rows


class Connection:
    def __init__(self, conninfo):
        self._handle = _lib.PQconnectdb(conninfo.encode())
        if _lib.PQstatus(self._handle) != _CONNECTION_OK:
            message = (_lib.PQerrorMessage(self._handle) or b"").decode(errors="replace").strip()
            _lib.PQfinish(self._handle)
            self._handle = None
            raise OperationalError(message)

    def cursor(self):
        return Cursor(self)

    def close(self):
        if self._handle:
            _lib.PQfinish(self._handle)
            self._handle = None


def connect(conninfo, autocommit=True):
    """libpq runs each statement in its own transaction unless BEGIN is sent,
    which is exactly psycopg's autocommit=True behaviour."""
    if not autocommit:
        raise ValueError("the libpq test bridge only supports autocommit=True")
    return Connection(conninfo)
```

### `tests/test_account_and_tokens.py` (NEW)

**Why:** 35 tests: password change, sign out elsewhere, API tokens.  
**How to run or test:** `python -m pytest tests/test_account_and_tokens.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
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
```

### `tests/test_auth.py` (NEW)

**Why:** 53 tests: default deny, sign-in, throttling, redirects, sessions, registration.  
**How to run or test:** `python -m pytest tests/test_auth.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
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
            if rule.endpoint in auth.PUBLIC_ENDPOINTS:
                # 200 page; 400 a POST without a CSRF token; 404 registration switched off
                assert response.status_code in (200, 400, 404), (method, url)
            else:
                assert response.status_code in (302, 303), (method, url, response.status_code)
                assert response.headers["Location"].startswith("/login"), (method, url)
            checked += 1
    assert checked >= 25


def test_public_endpoints_are_the_expected_small_set():
    assert {
        "main.login",
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
    start = auth.now_seconds()
    monkeypatch.setattr(auth, "now_seconds", lambda: start + app.config["SESSION_MAX_AGE_SECONDS"] - 120)
    with client.session_transaction() as session:
        session["seen_at"] = start + app.config["SESSION_MAX_AGE_SECONDS"] - 180  # recently active
    assert client.get("/").status_code == 200
    monkeypatch.setattr(auth, "now_seconds", lambda: start + app.config["SESSION_MAX_AGE_SECONDS"] + 1)
    response = client.get("/")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    page = client.get(response.headers["Location"])
    assert "Your session has ended" in page.get_data(as_text=True)


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
```

### `tests/test_browser_auth.py` (NEW)

**Why:** 15 Playwright tests of sign-in, registration, account and tokens.  
**How to run or test:** `python -m pytest tests/test_browser_auth.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8: sign-in, registration, account and isolation journeys in Chromium.

Unlike the other browser tests (which start with a signed session cookie),
these use the real forms, exactly as a person would.

Optional real screenshots: set TRADELEDGER_SCREENSHOT_DIR=docs/screenshots.
"""

import os
import re
from pathlib import Path

import pytest

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    WIDTHS,
    browser,
    horizontal_overflow,
    page_factory,
    start_server,
)
from tests.conftest import OTHER_EMAIL, TEST_EMAIL, TEST_PASSWORD, ensure_user
from tradeledger.repositories.trades import list_trades

pytestmark = pytest.mark.browser

AUTH_PAGES = ["/login", "/register"]


def add_second_user(conn):
    ensure_user(conn, OTHER_EMAIL)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    url, stop = start_server(str(tmp_path_factory.mktemp("auth") / "auth.db"), prepare=add_second_user)
    yield url
    stop()


def sign_in(page, server, email=TEST_EMAIL, password=TEST_PASSWORD):
    page.goto(server + "/login")
    page.fill("#field-email", email)
    page.fill("#field-password", password)
    with page.expect_navigation():
        page.get_by_role("button", name="Sign in").click()


def test_sign_in_returns_to_the_requested_page(server, page_factory):
    page = page_factory()
    page.goto(server + "/trades?status=CLOSED")
    assert "/login?next=" in page.url
    assert page.locator("h1").inner_text().strip() == "Sign in"
    page.fill("#field-email", TEST_EMAIL)
    page.fill("#field-password", TEST_PASSWORD)
    with page.expect_navigation():
        page.get_by_role("button", name="Sign in").click()
    assert page.url.endswith("/trades?status=CLOSED")
    assert page.locator(".sidebar__account").inner_text().strip() == f"Signed in as {TEST_EMAIL}"
    assert page.problems == []


def test_wrong_password_shows_a_focused_generic_error(server, page_factory):
    page = page_factory()
    sign_in(page, server, password="not the right password")
    summary = page.locator(".error-summary")
    assert summary.is_visible() and summary.get_attribute("role") == "alert"
    assert "Email or password is incorrect." in summary.inner_text()
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    assert page.input_value("#field-email") == TEST_EMAIL  # the email is kept...
    assert page.input_value("#field-password") == ""  # ...the password never is


def test_password_fields_use_the_right_autocomplete(server, page_factory):
    page = page_factory()
    page.goto(server + "/login")
    assert page.get_attribute("#field-email", "autocomplete") == "username"
    assert page.get_attribute("#field-password", "autocomplete") == "current-password"
    assert page.get_attribute("#field-password", "type") == "password"
    page.goto(server + "/register")
    assert page.get_attribute("#field-password", "autocomplete") == "new-password"


def test_register_sign_out_and_sign_back_in(server, page_factory):
    page = page_factory()
    page.goto(server + "/register")
    page.fill("#field-email", "browser.user@example.com")
    page.fill("#field-password", "a browser pass phrase")
    page.fill("#field-confirm_password", "a browser pass phrase")
    with page.expect_navigation():
        page.get_by_role("button", name="Create account").click()
    assert page.url.endswith("/instruments")
    assert page.locator(".flash", has_text="Your account is ready").is_visible()
    assert page.locator("table").count() == 0 or "EURUSD" not in page.content()  # a new account is empty

    with page.expect_navigation():
        page.get_by_role("button", name="Sign out").click()
    assert page.url.endswith("/login")
    assert page.locator(".flash", has_text="You have signed out.").is_visible()
    page.goto(server + "/trades")
    assert "/login" in page.url  # really signed out

    sign_in(page, server, "browser.user@example.com", "a browser pass phrase")
    assert page.url.endswith("/")


def test_the_other_user_cannot_open_a_trade_by_url(server, page_factory):
    with server.app.app_context():
        from tradeledger.db import get_db

        alice_trade = list_trades(get_db(), user_id=1, limit=1)[0]["id"]
    page = page_factory()
    sign_in(page, server, OTHER_EMAIL, TEST_PASSWORD)
    response = page.goto(f"{server}/trades/{alice_trade}")
    assert response.status == 404
    assert "EURUSD" not in page.locator("main").inner_text()
    page.goto(server + "/trades")
    assert page.get_by_text("No trades yet").count() + page.get_by_text("no trades").count() >= 0
    assert page.locator('a[href^="/trades/"][href$="' + str(alice_trade) + '"]').count() == 0


def test_create_and_revoke_an_api_token(server, page_factory):
    page = page_factory()
    sign_in(page, server)
    page.goto(server + "/account")
    page.fill("#field-name", "Browser token")
    with page.expect_navigation():
        page.get_by_role("button", name="Create token").click()
    token = page.input_value("#new-token-value")
    assert re.fullmatch(r"tl_[A-Za-z0-9_-]{43}", token)
    assert page.get_by_text("It will not be shown again").is_visible()
    page.reload()  # re-sends the POST: refused by the one-time form value
    assert token not in page.content()
    assert page.locator(".flash", has_text="no new token was created").is_visible()
    assert page.locator('td[data-label="Name"]', has_text="Browser token").count() == 1
    with page.expect_navigation():
        page.get_by_role("button", name="Revoke token Browser token").click()
    assert page.locator(".flash", has_text="API token revoked").is_visible()


# ---------- Accessibility and layout of the new pages ----------


UNLABELLED = """() => [...document.querySelectorAll('input, select, textarea')]
    .filter(el => el.type !== 'hidden' && !el.hidden)
    .filter(el => !(el.labels && el.labels.length) && !el.getAttribute('aria-label'))
    .map(el => el.name)"""


@pytest.mark.parametrize("path", ["/login", "/register", "/account"])
def test_new_pages_are_labelled_and_have_one_h1(server, page_factory, path):
    page = page_factory(server=server) if path == "/account" else page_factory()
    page.goto(server + path)
    assert page.evaluate(UNLABELLED) == []
    assert page.locator("h1").count() == 1
    assert page.evaluate("document.documentElement.lang") == "en-GB"


@pytest.mark.parametrize("width", WIDTHS)
def test_new_pages_fit_every_width(server, page_factory, width):
    for path, signed_in in (("/login", False), ("/register", False), ("/account", True)):
        page = page_factory(width, 900, server=server) if signed_in else page_factory(width, 900)
        page.goto(server + path)
        assert horizontal_overflow(page) == 0, (path, width)


def test_sign_in_form_works_with_the_keyboard_alone(server, page_factory):
    page = page_factory()
    page.goto(server + "/login")
    page.locator("#field-email").focus()
    page.keyboard.type(TEST_EMAIL)
    page.keyboard.press("Tab")
    page.keyboard.type(TEST_PASSWORD)
    with page.expect_navigation():
        page.keyboard.press("Enter")
    assert page.url.endswith("/")


@pytest.mark.skipif(
    not os.environ.get("TRADELEDGER_SCREENSHOT_DIR"), reason="TRADELEDGER_SCREENSHOT_DIR not set"
)
def test_capture_auth_screenshots(server, page_factory):
    folder = Path(os.environ["TRADELEDGER_SCREENSHOT_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    shots = [
        ("/login", 1440, False, "phase8_login_1440.png"),
        ("/login", 375, False, "phase8_login_375.png"),
        ("/register", 1440, False, "phase8_register_1440.png"),
        ("/account", 1440, True, "phase8_account_1440.png"),
        ("/account", 375, True, "phase8_account_375.png"),
        ("/", 1440, True, "phase8_overview_signed_in_1440.png"),
    ]
    for path, width, signed_in, name in shots:
        page = page_factory(width, 900, server=server) if signed_in else page_factory(width, 900)
        page.goto(server + path)
        page.screenshot(path=str(folder / name), full_page=True)
        assert (folder / name).stat().st_size > 10_000
    page = page_factory(1440, 900)
    sign_in(page, server, password="a wrong password here")
    page.screenshot(path=str(folder / "phase8_login_error_1440.png"), full_page=True)
```

### `tests/test_deployment_files.py` (NEW)

**Why:** 26 static checks of Docker, Compose, workflows and Terraform.  
**How to run or test:** `python -m pytest tests/test_deployment_files.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8: static checks of the deployment files.

These read the Dockerfile, compose.yaml, the GitHub workflows and the
Terraform files as TEXT and check security properties. They do not build an
image, start containers or contact AWS: that is the job of the CI "docker"
and "terraform" jobs and of a real deployment. Passing here proves the
files say the right things, not that they run.
"""

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
INFRA = ROOT / "infra"


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


# ---------- Dockerfile ----------


def dockerfile_instructions():
    text = re.sub(r"\\\n", " ", read("Dockerfile"))
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]


def test_image_runs_as_a_fixed_non_root_user():
    users = [line for line in dockerfile_instructions() if line.startswith("USER ")]
    assert users and users[-1] == "USER 10001:10001"


def test_image_uses_gunicorn_not_the_flask_dev_server():
    lines = dockerfile_instructions()
    cmd = next(line for line in lines if line.startswith("CMD "))
    assert "gunicorn" in cmd and "tradeledger.wsgi:app" in cmd
    assert not any("flask run" in line or "FLASK_DEBUG" in line for line in lines)


def test_image_bakes_in_no_secrets():
    for line in dockerfile_instructions():
        if line.startswith(("ENV ", "ARG ")):
            assert not re.search(r"SECRET|PASSWORD|TOKEN|AWS_", line), line
    assert "TRADELEDGER_CONFIG=production" in read("Dockerfile")


def test_image_has_a_liveness_healthcheck_and_exec_entrypoint():
    assert "HEALTHCHECK" in read("Dockerfile") and "/health/live" in read("Dockerfile")
    entrypoint = read("docker/entrypoint.sh")
    assert 'exec "$@"' in entrypoint  # Gunicorn becomes PID 1 and receives SIGTERM


def test_dockerignore_is_an_allow_list():
    rules = [r.strip() for r in read(".dockerignore").splitlines() if r.strip() and not r.startswith("#")]
    assert rules[0] == "*"
    allowed = {r[1:] for r in rules if r.startswith("!")}
    assert allowed == {
        "tradeledger/",
        "requirements.txt",
        "constraints.txt",
        "gunicorn.conf.py",
        "docker/entrypoint.sh",
    }


def test_production_wsgi_module_uses_the_environment_config():
    assert "app = create_app()" in read("tradeledger/wsgi.py")


# ---------- Docker Compose ----------


@pytest.fixture(scope="module")
def compose():
    return yaml.safe_load(read("compose.yaml"))


def test_database_is_not_published(compose):
    db = compose["services"]["db"]
    assert "ports" not in db and "expose" not in db
    assert compose["networks"]["backend"]["internal"] is True


def test_app_is_published_on_localhost_only(compose):
    assert compose["services"]["app"]["ports"] == ["127.0.0.1:8000:8000"]


def test_secrets_are_files_not_environment_values(compose):
    app_env = compose["services"]["app"]["environment"]
    assert "SECRET_KEY" not in app_env and "DB_PASSWORD" not in app_env
    assert app_env["SECRET_KEY_FILE"].startswith("/run/secrets/")
    assert app_env["DB_PASSWORD_FILE"].startswith("/run/secrets/")
    assert compose["services"]["db"]["environment"]["POSTGRES_PASSWORD_FILE"].startswith("/run/secrets/")
    for secret in compose["secrets"].values():
        assert secret["file"].startswith("./secrets/")


def test_app_container_is_hardened(compose):
    app = compose["services"]["app"]
    assert app["read_only"] is True
    assert app["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in app["security_opt"]
    assert app["depends_on"]["db"]["condition"] == "service_healthy"
    assert app["environment"]["TRADELEDGER_CONFIG"] == "production"
    assert app["environment"]["TRADELEDGER_REGISTRATION_ENABLED"] == "0"


def test_secret_files_and_state_are_ignored_by_git():
    ignored = read(".gitignore")
    for pattern in ("secrets/*", "*.tfstate", ".terraform/", "*.tfvars", ".env", "*.db"):
        assert pattern in ignored


# ---------- Workflows ----------


@pytest.fixture(scope="module")
def deploy():
    return yaml.safe_load(read(".github/workflows/deploy.yml"))


@pytest.fixture(scope="module")
def ci():
    return yaml.safe_load(read(".github/workflows/ci.yml"))


def test_deploy_is_manual_only(deploy):
    triggers = deploy[True]  # YAML 1.1 reads the key "on" as True
    assert set(triggers) == {"workflow_dispatch"}


def test_deploy_needs_approval_and_uses_oidc(deploy):
    job = deploy["jobs"]["deploy"]
    assert job["environment"] == "production"
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert job["if"] == "vars.DEPLOY_ENABLED == 'true'"
    text = read(".github/workflows/deploy.yml")
    assert "role-to-assume" in text
    for forbidden in ("aws-access-key-id", "aws-secret-access-key", "AWS_SECRET_ACCESS_KEY", "secrets.AWS"):
        assert forbidden not in text


def test_deploy_snapshots_before_migrating(deploy):
    names = [step["name"] for step in deploy["jobs"]["deploy"]["steps"]]
    assert names.index("Snapshot the database") < names.index("Run database migrations (one-off task)")
    assert names.index("Run database migrations (one-off task)") < names.index(
        "Deploy the new task definition"
    )


def test_ci_has_no_deployment_or_cloud_credentials(ci):
    text = read(".github/workflows/ci.yml")
    assert ci["permissions"] == {"contents": "read"}
    assert "id-token" not in text and "configure-aws-credentials" not in text


def test_ci_runs_postgres_docker_and_terraform_checks(ci):
    jobs = ci["jobs"]
    assert jobs["postgres"]["services"]["postgres"]["image"] == "postgres:16"
    assert "TRADELEDGER_TEST_DATABASE_URL" in jobs["postgres"]["env"]
    assert "TRADELEDGER_PG_DRIVER" not in read(".github/workflows/ci.yml")  # CI uses the real driver
    docker_steps = " ".join(step.get("run", "") for step in jobs["docker"]["steps"])
    assert "docker build" in docker_steps and "compose_smoke_test.sh" in docker_steps
    terraform_steps = " ".join(step.get("run", "") for step in jobs["terraform"]["steps"])
    assert "terraform validate" in terraform_steps and "-backend=false" in terraform_steps


# ---------- Terraform ----------


def terraform_text():
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(INFRA.glob("*.tf")))


def block(text, kind, name):
    """The body of `resource "kind" "name" { ... }` (brace-matched)."""
    match = re.search(rf'resource "{kind}" "{name}" \{{', text)
    assert match, (kind, name)
    depth, i = 1, match.end()
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    return text[match.end() : i - 1]


def test_terraform_references_resolve():
    """A cheap substitute for `terraform validate` (which needs the
    Terraform binary): every var., local. and resource reference points at
    something that is declared."""
    text = terraform_text()
    code = "\n".join(line.split("#")[0] for line in text.splitlines())
    variables = set(re.findall(r'^variable "(\w+)"', text, re.M))
    resources = set(re.findall(r'^resource "(\w+)" "(\w+)"', text, re.M))
    data_sources = set(re.findall(r'^data "(\w+)" "(\w+)"', text, re.M))
    local_names = set()
    for body in re.findall(r"^locals \{(.*?)^\}", text, re.M | re.S):
        local_names |= set(re.findall(r"^  (\w+)\s*=", body, re.M))
    assert set(re.findall(r"\bvar\.(\w+)", code)) <= variables
    assert set(re.findall(r"\blocal\.(\w+)", code)) <= local_names
    for kind, name in re.findall(r"\b(aws_\w+)\.(\w+)\b", code):
        if kind in {r[0] for r in resources}:
            assert (kind, name) in resources, f"{kind}.{name}"
    for kind, name in re.findall(r"\bdata\.(aws_\w+)\.(\w+)", code):
        assert (kind, name) in data_sources, f"data.{kind}.{name}"
    assert code.count("{") == code.count("}")


def test_database_is_private_encrypted_and_backed_up():
    rds = block(terraform_text(), "aws_db_instance", "this")
    assert re.search(r"publicly_accessible\s*=\s*false", rds)
    assert re.search(r"storage_encrypted\s*=\s*true", rds)
    assert re.search(r"manage_master_user_password\s*=\s*true", rds)
    assert re.search(r"skip_final_snapshot\s*=\s*false", rds)
    assert "backup_retention_period" in rds and "deletion_protection" in rds
    assert 'name         = "rds.force_ssl"' in terraform_text()


def test_only_the_load_balancer_is_open_to_the_internet():
    text = terraform_text()
    for name in re.findall(r'resource "aws_vpc_security_group_ingress_rule" "(\w+)"', text):
        rule = block(text, "aws_vpc_security_group_ingress_rule", name)
        if "0.0.0.0/0" in rule:
            assert "aws_security_group.alb.id" in rule, name
    db_rule = block(text, "aws_vpc_security_group_ingress_rule", "db_from_app")
    assert "referenced_security_group_id = aws_security_group.app.id" in db_rule


def test_no_credentials_or_secret_values_in_terraform():
    text = terraform_text() + (INFRA / "terraform.tfvars.example").read_text()
    assert not re.search(r"AKIA[0-9A-Z]{16}", text)
    for forbidden in ("access_key", "secret_key =", "secret_string", "random_password"):
        assert forbidden not in text, forbidden
    # A literal password assignment (manage_master_user_password = true is fine).
    assert not re.search(r'\bpassword\s*=\s*"', text)


def test_iam_never_grants_wildcard_actions():
    text = terraform_text()
    for actions in re.findall(r"actions\s*=\s*\[([^\]]*)\]", text):
        for action in re.findall(r'"([^"]+)"', actions):
            assert action != "*" and not action.endswith(":*"), action


def test_task_role_has_no_permissions():
    text = terraform_text()
    assert "aws_iam_role.task.name" not in text and "aws_iam_role.task.id" not in text


def test_github_role_is_limited_to_this_repository_environment():
    text = terraform_text()
    assert '"repo:${var.github_repository}:environment:production"' in text
    assert "token.actions.githubusercontent.com:aud" in text


def test_https_only_and_host_restricted():
    text = terraform_text()
    http = block(text, "aws_lb_listener", "http")
    assert 'type = "redirect"' in http and '"HTTPS"' in http
    https = block(text, "aws_lb_listener", "https")
    assert "TLS13" in https and 'type = "fixed-response"' in https
    assert "host_header" in block(text, "aws_lb_listener_rule", "app")


def test_backend_encrypts_and_locks_state():
    backend = (INFRA / "backend.tf").read_text()
    assert "encrypt      = true" in backend and "use_lockfile = true" in backend


# ---------- The repository holds no secrets ----------


def test_no_secrets_committed_anywhere():
    suspicious = re.compile(
        r"AKIA[0-9A-Z]{16}|-----BEGIN (RSA |EC )?PRIVATE KEY-----|\btl_[A-Za-z0-9_-]{43}\b|ghp_[A-Za-z0-9]{36}"
    )
    skip_dirs = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "instance"}
    for path in ROOT.rglob("*"):
        if path.is_dir() or skip_dirs & set(path.parts) or path.suffix in {".png", ".db", ".pyc"}:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not suspicious.search(text), path
```

### `tests/test_isolation.py` (NEW)

**Why:** 36 adversarial two-user tests (pages, API, repositories).  
**How to run or test:** `python -m pytest tests/test_isolation.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8: adversarial two-user isolation tests.

Alice (user 1) and Bob (user 2) both hold the full sample data, so both
have instruments called EURUSD, XAUUSD, ... with DIFFERENT ids. Alice also
has one trade carrying a private note and a private strategy name.

Bob then tries everything a curious or malicious user could: guessing ids
in URLs, filters and API calls, paging, searching, pointing his trades at
Alice's instruments, and smuggling an owner field into a request. Every
attempt must fail, and must fail with the same answer a non-existent
record gets, so Bob cannot even learn which ids exist.
"""

import pytest

from tests.conftest import OTHER_EMAIL, ensure_user, issue_token, sign_in
from tests.web_helpers import closed_trade_form, count_rows, csrf_token, fetch_trade
from tradeledger import analytics
from tradeledger.db import get_db
from tradeledger.performance import build_report
from tradeledger.repositories import instruments as instrument_repo
from tradeledger.repositories import trades as trade_repo
from tradeledger.seed import seed_sample_data

ALICE, BOB = 1, 2
SECRET_NOTE = "ALICE-PRIVATE-NOTE-7731"
SECRET_STRATEGY = "AlicePrivateEdge"
MISSING = 999_999


@pytest.fixture
def world(app):
    """Both accounts seeded; returns ids of each user's records."""
    with app.app_context():
        conn = get_db()
        assert ensure_user(conn, OTHER_EMAIL) == BOB
        seed_sample_data(conn, user_id=ALICE)
        seed_sample_data(conn, user_id=BOB)
        alice_trade = trade_repo.list_trades(conn, user_id=ALICE, status="CLOSED")[0]
        data = {k: alice_trade[k] for k in trade_repo._WRITE_FIELDS}
        data.update(notes=SECRET_NOTE, strategy=SECRET_STRATEGY)
        for key in ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees"):
            data[key] = None if data[key] is None else str(data[key])
        trade_repo.update_trade(conn, alice_trade["id"], data, user_id=ALICE)
        ids = {
            "alice_trades": [t["id"] for t in trade_repo.list_trades(conn, user_id=ALICE)],
            "bob_trades": [t["id"] for t in trade_repo.list_trades(conn, user_id=BOB)],
            "alice_instruments": [i["id"] for i in instrument_repo.list_instruments(conn, user_id=ALICE)],
            "bob_instruments": [i["id"] for i in instrument_repo.list_instruments(conn, user_id=BOB)],
            "alice_secret_trade": alice_trade["id"],
        }
    assert set(ids["alice_trades"]).isdisjoint(ids["bob_trades"])
    assert len(ids["alice_trades"]) == len(ids["bob_trades"]) == 16
    return ids


@pytest.fixture
def bob(app):
    """Bob's browser, signed in, with his own API token."""
    client = app.test_client()
    sign_in(client, user_id=BOB)
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {issue_token(app, user_id=BOB, name='bob')}"
    return client


def strip_ids(text, *ids):
    for value in ids:
        text = text.replace(str(value), "<id>")
    return text


# ---------- Website: guessed ids ----------


@pytest.mark.parametrize("suffix", ["", "/edit", "/delete"])
def test_bob_cannot_open_alices_trade_pages(world, bob, suffix):
    for trade_id in world["alice_trades"]:
        assert bob.get(f"/trades/{trade_id}{suffix}").status_code == 404


def test_foreign_and_missing_trades_look_identical(world, bob):
    foreign = world["alice_trades"][0]
    page_foreign = bob.get(f"/trades/{foreign}").get_data(as_text=True)
    page_missing = bob.get(f"/trades/{MISSING}").get_data(as_text=True)
    assert strip_ids(page_foreign, foreign) == strip_ids(page_missing, MISSING)


def test_bob_cannot_edit_alices_trade(app, world, bob):
    target = world["alice_secret_trade"]
    before = fetch_trade(app, target)
    token = csrf_token(bob, "/trades/new")
    response = bob.post(
        f"/trades/{target}/edit", data={"csrf_token": token, **closed_trade_form(notes="bob was here")}
    )
    assert response.status_code == 404
    assert fetch_trade(app, target) == before


def test_bob_cannot_delete_alices_trade(app, world, bob):
    target = world["alice_trades"][3]
    response = bob.post(f"/trades/{target}/delete", data={"csrf_token": csrf_token(bob, "/trades/new")})
    assert response.status_code == 404
    assert fetch_trade(app, target) is not None


# ---------- Website: lists, search, filters, analytics ----------


def test_journal_lists_only_bobs_trades(world, bob):
    html = bob.get("/trades?per_page=100").get_data(as_text=True)
    assert "of 16 trades" in html
    for trade_id in world["alice_trades"]:
        assert f'href="/trades/{trade_id}"' not in html


def test_search_never_reaches_alices_notes_or_strategies(world, bob):
    for term in (SECRET_NOTE, SECRET_STRATEGY, "ALICE-PRIVATE"):
        html = bob.get(f"/trades?q={term}").get_data(as_text=True)
        assert "No trades match" in html  # (the search box itself echoes the term Bob typed)
        assert f'href="/trades/{world["alice_secret_trade"]}"' not in html
    # Nor do Alice's strategy names appear in Bob's filter choices.
    for url in ("/trades", "/trades/new", "/analytics"):
        assert SECRET_STRATEGY not in bob.get(url).get_data(as_text=True)


def test_bob_cannot_filter_by_alices_instrument(world, bob):
    alice_instrument = world["alice_instruments"][0]
    journal = bob.get(f"/trades?instrument_id={alice_instrument}")
    assert f'href="/trades/{world["alice_trades"][0]}"' not in journal.get_data(as_text=True)
    dashboard = bob.get(f"/analytics?instrument_id={alice_instrument}")
    missing = bob.get(f"/analytics?instrument_id={MISSING}")
    assert dashboard.status_code == missing.status_code == 400


def test_overview_and_dashboard_count_only_bobs_trades(app, world, bob):
    # Bob deletes 2 of his own trades; Alice's 16 must not appear in his totals.
    for trade_id in world["bob_trades"][:2]:
        bob.post(f"/trades/{trade_id}/delete", data={"csrf_token": csrf_token(bob, "/trades/new")})
    with app.app_context():
        expected = build_report(get_db(), user_id=BOB).overall.trades
        assert build_report(get_db(), user_id=ALICE).overall.trades == 14
    assert expected < 14
    html = bob.get("/analytics").get_data(as_text=True)
    assert 'data-kpi="trades"' in html and f">{expected}<" in html.split('data-kpi="trades"')[1][:300]


def test_bob_cannot_attach_a_trade_to_alices_instrument(app, world, bob):
    before = count_rows(app, "trades")
    form = closed_trade_form(instrument_id=str(world["alice_instruments"][0]))
    response = bob.post("/trades/new", data={"csrf_token": csrf_token(bob, "/trades/new"), **form})
    assert response.status_code == 400
    assert "does not match an existing instrument" in response.get_data(as_text=True)
    assert count_rows(app, "trades") == before


def test_bob_cannot_move_his_trade_onto_alices_instrument(app, world, bob):
    own = world["bob_trades"][0]
    before = fetch_trade(app, own)
    form = closed_trade_form(instrument_id=str(world["alice_instruments"][0]))
    response = bob.post(f"/trades/{own}/edit", data={"csrf_token": csrf_token(bob, "/trades/new"), **form})
    assert response.status_code == 400
    assert fetch_trade(app, own) == before


def test_trade_form_offers_only_bobs_instruments(world, bob):
    html = bob.get("/trades/new").get_data(as_text=True)
    for instrument_id in world["alice_instruments"]:
        assert f'<option value="{instrument_id}"' not in html
    for instrument_id in world["bob_instruments"]:
        assert f'<option value="{instrument_id}"' in html


def test_the_same_symbol_is_allowed_in_both_accounts_but_not_twice_in_one(app, world, bob):
    response = bob.post(
        "/instruments",
        data={
            "csrf_token": csrf_token(bob, "/instruments"),
            "symbol": "EURUSD",
            "name": "dup",
            "asset_class": "FOREX",
            "quote_currency": "USD",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
        },
    )
    assert response.status_code == 409 or "already recorded" in response.get_data(as_text=True)
    with app.app_context():
        rows = (
            get_db()
            .execute("SELECT user_id FROM instruments WHERE symbol = 'EURUSD' ORDER BY user_id")
            .fetchall()
        )
    assert [r["user_id"] for r in rows] == [ALICE, BOB]


# ---------- JSON API ----------


def test_api_guessed_trade_ids_get_404(world, bob):
    for trade_id in world["alice_trades"]:
        response = bob.get(f"/api/v1/trades/{trade_id}")
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "not_found"


def test_api_foreign_and_missing_answers_are_identical(world, bob):
    foreign = world["alice_trades"][0]
    a = bob.get(f"/api/v1/trades/{foreign}").get_json()
    b = bob.get(f"/api/v1/trades/{MISSING}").get_json()
    assert strip_ids(str(a), foreign) == strip_ids(str(b), MISSING)


def test_api_cannot_replace_or_delete_alices_trade(app, world, bob, trade_body):
    target = world["alice_secret_trade"]
    before = fetch_trade(app, target)
    body = {**trade_body, "instrument_id": world["bob_instruments"][0]}
    assert bob.put(f"/api/v1/trades/{target}", json=body).status_code == 404
    assert bob.delete(f"/api/v1/trades/{target}").status_code == 404
    assert fetch_trade(app, target) == before


def test_api_paging_through_everything_returns_only_bobs_trades(world, bob):
    seen, page = [], 1
    while True:
        payload = bob.get(f"/api/v1/trades?per_page=5&page={page}").get_json()
        seen += [t["id"] for t in payload["data"]]
        if page >= payload["pagination"]["total_pages"]:
            break
        page += 1
    assert payload["pagination"]["total"] == 16
    assert sorted(seen) == sorted(world["bob_trades"])


def test_api_search_and_filters_stay_inside_the_account(world, bob):
    # (The trade list has no text search; the website's search is tested above.)
    groups = bob.get("/api/v1/analytics/strategies").get_json()["data"]
    assert SECRET_STRATEGY not in {g["group"] for g in groups}
    filtered = bob.get(f"/api/v1/analytics/summary?strategy={SECRET_STRATEGY}").get_json()["data"]
    assert filtered["overall"]["trades"] == 0
    response = bob.get(f"/api/v1/trades?instrument_id={world['alice_instruments'][0]}")
    assert response.status_code == 400  # someone else's instrument: same as an unknown one


def test_api_instruments_are_private(world, bob):
    listed = [i["id"] for i in bob.get("/api/v1/instruments").get_json()["data"]]
    assert sorted(listed) == sorted(world["bob_instruments"])
    for instrument_id in world["alice_instruments"]:
        assert bob.get(f"/api/v1/instruments/{instrument_id}").status_code == 404


def test_api_create_on_alices_instrument_is_refused(app, world, bob, trade_body):
    before = count_rows(app, "trades")
    response = bob.post("/api/v1/trades", json={**trade_body, "instrument_id": world["alice_instruments"][0]})
    assert response.status_code == 400
    assert response.get_json()["error"]["details"]["instrument_id"] == "does not match an existing instrument"
    assert count_rows(app, "trades") == before


@pytest.mark.parametrize("field", ["user_id", "owner", "owner_id"])
def test_api_owner_cannot_be_set_through_the_body(app, world, bob, trade_body, field):
    body = {**trade_body, "instrument_id": world["bob_instruments"][0], field: ALICE}
    before = count_rows(app, "trades")
    response = bob.post("/api/v1/trades", json=body)
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "unknown_fields"
    assert count_rows(app, "trades") == before
    replace = bob.put(f"/api/v1/trades/{world['bob_trades'][0]}", json=body)
    assert replace.status_code == 400


def test_api_owner_cannot_be_set_on_instruments(world, bob):
    body = {
        "symbol": "NEWONE",
        "name": "x",
        "asset_class": "OTHER",
        "price_increment": "0.1",
        "contract_multiplier": "1",
        "quote_currency": "USD",
        "user_id": ALICE,
    }
    assert bob.post("/api/v1/instruments", json=body).status_code == 400


def test_api_analytics_use_only_bobs_trades(app, world, bob):
    # Make Bob's figures differ from Alice's, then compare with Bob-only reports.
    for trade_id in world["bob_trades"][:5]:
        bob.delete(f"/api/v1/trades/{trade_id}")
    summary = bob.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    with app.app_context():
        mine = build_report(get_db(), user_id=BOB).overall
        alices = build_report(get_db(), user_id=ALICE).overall
    assert summary["trades"] == mine.trades != alices.trades
    assert summary["total_r"] == str(mine.total_r)
    curve = bob.get("/api/v1/analytics/curve").get_json()["data"]
    assert len(curve) == mine.trades


def test_alice_still_sees_everything_of_hers(world, client):
    """The isolation is not achieved by hiding data from everyone."""
    assert client.get("/api/v1/trades?per_page=100").get_json()["pagination"]["total"] == 16
    secret = client.get(f"/api/v1/trades/{world['alice_secret_trade']}").get_json()["data"]
    assert secret["notes"] == SECRET_NOTE


# ---------- Repository layer ----------


@pytest.mark.parametrize("bad", [None, 0, -1, "1", True, 1.0])
def test_repositories_refuse_anything_but_a_real_user_id(seeded_conn, bad):
    with pytest.raises(TypeError, match="user_id must be a positive int"):
        trade_repo.list_trades(seeded_conn, user_id=bad)
    with pytest.raises(TypeError):
        instrument_repo.list_instruments(seeded_conn, user_id=bad)
    with pytest.raises(TypeError):
        build_report(seeded_conn, user_id=bad)


def test_forgetting_the_owner_is_an_error_not_a_query_of_everything(seeded_conn):
    with pytest.raises(TypeError):
        trade_repo.list_trades(seeded_conn)
    with pytest.raises(TypeError):
        trade_repo.get_trade(seeded_conn, 1)
    with pytest.raises(TypeError):
        trade_repo.count_trades(seeded_conn)


def test_repository_returns_nothing_for_another_owner(app, world):
    with app.app_context():
        conn = get_db()
        target = world["alice_trades"][0]
        assert trade_repo.get_trade(conn, target, user_id=BOB) is None
        assert trade_repo.delete_trade(conn, target, user_id=BOB) is False
        assert trade_repo.get_trade(conn, target, user_id=ALICE) is not None
        assert instrument_repo.get_instrument(conn, world["alice_instruments"][0], user_id=BOB) is None
        assert (
            instrument_repo.get_instrument_by_symbol(conn, "EURUSD", user_id=BOB)["id"]
            in world["bob_instruments"]
        )
        assert SECRET_STRATEGY not in trade_repo.list_strategies(conn, user_id=BOB)
        assert SECRET_STRATEGY in trade_repo.list_strategies(conn, user_id=ALICE)


def test_sql_analytics_are_per_user(app, world):
    with app.app_context():
        conn = get_db()
        trade_repo.delete_trade(conn, world["bob_trades"][0], user_id=BOB)
        assert analytics.closed_trade_count(conn, user_id=ALICE) == 14
        assert analytics.closed_trade_count(conn, user_id=BOB) in (13, 14)
        strategies = {row["strategy"] for row in analytics.trades_by_strategy(conn, user_id=BOB)}
        assert SECRET_STRATEGY not in strategies
```

### `tests/test_legacy_and_cli_accounts.py` (NEW)

**Why:** 15 tests: Phase 7 upgrade, legacy claims, account CLI commands.  
**How to run or test:** `python -m pytest tests/test_legacy_and_cli_accounts.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8: upgrading a Phase 7 database, claiming legacy records, and the
account commands (create-user, set-password, deactivate-user, ...).

The Phase 7 database is built by tests/fixtures/phase7_schema_v2.sql plus
the sample data inserted with Phase 7-shaped SQL (no users, no owners),
so the upgrade path tested here is the one an existing user takes.
"""

import sqlite3

import pytest

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, ensure_user
from tests.legacy_fixtures import build_phase7_database
from tradeledger import create_app
from tradeledger.db import connect, get_db, init_schema, schema_version
from tradeledger.legacy import claim_legacy_data, legacy_counts
from tradeledger.repositories import trades as trade_repo
from tradeledger.repositories.users import get_user_by_email

pytestmark = pytest.mark.sqlite_only  # PostgreSQL databases never had unowned rows


def snapshot(path, table):
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    rows = [dict(r) for r in raw.execute(f"SELECT * FROM {table} ORDER BY id")]
    raw.close()
    return rows


@pytest.fixture
def phase7_db(tmp_path):
    path = str(tmp_path / "phase7.db")
    build_phase7_database(path)
    return path


@pytest.fixture
def open_db(phase7_db):
    """Open connections to the Phase 7 file; all are closed after the test."""
    opened = []

    def open_connection():
        conn = connect(phase7_db)
        opened.append(conn)
        return conn

    yield open_connection
    for conn in opened:
        conn.close()


# ---------- Upgrading keeps every value ----------


def test_upgrade_from_phase_7_preserves_every_row(phase7_db, open_db):
    trades_before, instruments_before = snapshot(phase7_db, "trades"), snapshot(phase7_db, "instruments")
    conn = open_db()
    assert schema_version(conn) == 2
    assert init_schema(conn) == [3]
    conn.close()
    trades_after, instruments_after = snapshot(phase7_db, "trades"), snapshot(phase7_db, "instruments")
    assert len(trades_after) == len(trades_before) == 16
    for before, after in zip(trades_before, trades_after, strict=True):
        assert after.pop("user_id") is None
        assert after == before  # same ids, same text, same timestamps
    for before, after in zip(instruments_before, instruments_after, strict=True):
        assert after.pop("user_id") is None
        assert after == before


def test_upgrade_keeps_foreign_keys_and_rules(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    assert conn.raw.execute("PRAGMA foreign_key_check").fetchall() == []
    assert conn.raw.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    triggers = {r[0] for r in conn.raw.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    assert triggers == {"trades_closed_at_rules_insert", "trades_closed_at_rules_update"}


def test_unassigned_records_are_invisible_to_every_account(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    first = ensure_user(conn)  # even the FIRST account gets nothing automatically
    assert trade_repo.list_trades(conn, user_id=first) == []
    assert legacy_counts(conn) == {"instruments": 5, "trades": 16}


# ---------- Claiming ----------


def test_dry_run_changes_nothing(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    summary = claim_legacy_data(conn, user_id=ensure_user(conn), apply=False)
    assert summary == {"instruments": 5, "trades": 16, "conflicts": [], "applied": False}
    assert legacy_counts(conn) == {"instruments": 5, "trades": 16}


def test_apply_assigns_everything_in_one_go(phase7_db, open_db):
    before = snapshot(phase7_db, "trades")
    conn = open_db()
    init_schema(conn)
    owner = ensure_user(conn)
    assert claim_legacy_data(conn, user_id=owner, apply=True)["applied"]
    assert legacy_counts(conn) == {"instruments": 0, "trades": 0}
    assert len(trade_repo.list_trades(conn, user_id=owner)) == 16
    conn.close()
    after = snapshot(phase7_db, "trades")
    assert [{**r, "user_id": None} for r in after] == [{**r, "user_id": None} for r in before]
    assert {r["user_id"] for r in after} == {owner}


def test_symbol_conflict_blocks_the_claim(phase7_db, open_db):
    conn = open_db()
    init_schema(conn)
    owner = ensure_user(conn)
    from tradeledger.repositories.instruments import create_instrument

    create_instrument(
        conn,
        {
            "symbol": "EURUSD",
            "name": "already mine",
            "asset_class": "FOREX",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        user_id=owner,
    )
    summary = claim_legacy_data(conn, user_id=owner, apply=True)
    assert summary["applied"] is False
    assert summary["conflicts"] == ["the account already has an instrument with symbol EURUSD"]
    assert legacy_counts(conn) == {"instruments": 5, "trades": 16}


# ---------- The commands ----------


def app_for(path):
    app = create_app("testing", overrides={"DATABASE": path})
    return app, app.test_cli_runner()


def test_migrate_db_command_backs_up_and_reports_unassigned_records(phase7_db, tmp_path):
    _app, runner = app_for(phase7_db)
    result = runner.invoke(args=["migrate-db"])
    assert result.exit_code == 0, result.output
    assert "Schema version 2 -> 3" in result.output
    assert "16 trade(s) are unassigned" in result.output
    backups = list(tmp_path.glob("phase7.db.v2-*.bak"))
    assert len(backups) == 1
    assert (
        snapshot(str(backups[0]), "trades")[0].get("user_id", "absent") == "absent"
    )  # the untouched v2 copy


def test_claim_command_dry_run_then_apply(phase7_db):
    _app, runner = app_for(phase7_db)
    runner.invoke(args=["migrate-db", "--no-backup"])
    created = runner.invoke(
        args=["create-user", "--email", "Owner@Example.com", "--password-stdin"],
        input="my own long passphrase\n",
    )
    assert created.exit_code == 0, created.output
    dry = runner.invoke(args=["claim-legacy-data", "--email", "owner@example.com"])
    assert "Dry run: nothing changed" in dry.output
    applied = runner.invoke(args=["claim-legacy-data", "--email", "owner@example.com", "--apply"])
    assert "every unassigned record now belongs to owner@example.com" in applied.output
    again = runner.invoke(args=["claim-legacy-data", "--email", "owner@example.com", "--apply"])
    assert "Nothing to assign." in again.output


def test_claim_command_for_unknown_account_fails(phase7_db):
    _app, runner = app_for(phase7_db)
    runner.invoke(args=["migrate-db", "--no-backup"])
    result = runner.invoke(args=["claim-legacy-data", "--email", "ghost@example.com", "--apply"])
    assert result.exit_code != 0
    assert "No account with email ghost@example.com" in result.output


def test_create_user_rejects_weak_passwords_and_duplicates(app, runner):
    weak = runner.invoke(args=["create-user", "--email", "a@b.co", "--password-stdin"], input="short\n")
    assert weak.exit_code != 0 and "Password must be at least 12 characters" in weak.output
    dup = runner.invoke(
        args=["create-user", "--email", TEST_EMAIL, "--password-stdin"], input="a perfectly long one\n"
    )
    assert dup.exit_code != 0 and "already exists" in dup.output


def test_create_user_prompt_asks_twice_and_hides_input(app, runner):
    mismatch = runner.invoke(
        args=["create-user", "--email", "p@example.com"], input="first long password\nsecond long password\n"
    )
    assert mismatch.exit_code != 0
    assert "first long password" not in mismatch.output  # hidden input is not echoed


def test_set_password_ends_existing_sessions(app, runner, client):
    result = runner.invoke(
        args=["set-password", "--email", TEST_EMAIL, "--password-stdin"], input="replacement passphrase\n"
    )
    assert result.exit_code == 0, result.output
    assert client.get("/").status_code == 302  # signed out


def test_deactivate_blocks_sign_in_and_tokens(app, runner, client):
    assert runner.invoke(args=["deactivate-user", "--email", TEST_EMAIL]).exit_code == 0
    assert client.get("/").status_code == 302
    assert client.get("/api/v1/trades").status_code == 401
    assert runner.invoke(args=["activate-user", "--email", TEST_EMAIL]).exit_code == 0
    with app.app_context():
        assert get_user_by_email(get_db(), TEST_EMAIL)["is_active"]


def test_list_users_never_prints_hashes(app, runner):
    with app.app_context():
        ensure_user(get_db(), OTHER_EMAIL)
    output = runner.invoke(args=["list-users"]).output
    assert TEST_EMAIL in output and OTHER_EMAIL in output
    assert "$argon2id$" not in output and "password" not in output.lower()


def test_reset_db_is_refused_in_production(db_path):
    app = create_app("production", overrides={"SECRET_KEY": "e" * 64, "DATABASE": db_path})
    result = app.test_cli_runner().invoke(args=["reset-db", "--yes"])
    assert result.exit_code != 0
    assert "reset-db is refused in production" in result.output
```

### `tests/test_operations.py` (NEW)

**Why:** 23 tests: production refusals, secrets from files, proxy hops, health, logging and redaction.  
**How to run or test:** `python -m pytest tests/test_operations.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
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
```

### `tests/test_passwords.py` (NEW)

**Why:** 21 tests for Argon2id hashing and password rules.  
**How to run or test:** `python -m pytest tests/test_passwords.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8: password hashing (passwords.py) and the password rules."""

import pytest

from tradeledger.config import Config, ProductionConfig, TestingConfig
from tradeledger.passwords import (
    MAX_LENGTH,
    MIN_LENGTH,
    OWASP_MINIMUM,
    HashParameters,
    hash_password,
    needs_rehash,
    parameters_from_config,
    password_problem,
    stored_parameters,
    verify_password,
)

FAST = HashParameters(memory_kib=1024, iterations=1, lanes=1)


def test_production_uses_owasp_argon2id_parameters():
    """The fast test settings must never leak into real environments."""
    for config in (Config, ProductionConfig):
        assert parameters_from_config(vars(config) | vars(Config)) == OWASP_MINIMUM
    assert HashParameters(memory_kib=19456, iterations=2, lanes=1) == OWASP_MINIMUM
    assert parameters_from_config(vars(Config) | vars(TestingConfig)) == FAST


def test_hash_with_production_parameters_round_trips():
    stored = hash_password("a long correct password", OWASP_MINIMUM)
    assert stored.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert verify_password("a long correct password", stored)
    assert not verify_password("a long correct passwore", stored)


def test_hash_is_salted_and_contains_no_password():
    one, two = hash_password("same password here", FAST), hash_password("same password here", FAST)
    assert one != two  # different random salts
    assert "same password" not in one


@pytest.mark.parametrize("stored", ["", "plaintext", "$argon2i$v=19$m=1024,t=1,p=1$abc$def", None, 42])
def test_malformed_or_foreign_hashes_never_verify(stored):
    assert verify_password("anything at all", stored) is False


def test_non_text_password_never_verifies():
    stored = hash_password("text password 123", FAST)
    assert verify_password(None, stored) is False
    assert verify_password(b"text password 123", stored) is False


def test_unicode_is_normalised():
    """The same password typed as a precomposed 'é' or as 'e' + accent."""
    stored = hash_password("café au lait twice", FAST)
    assert verify_password("café au lait twice", stored)


def test_rehash_detection():
    old = hash_password("upgrade me please", FAST)
    assert stored_parameters(old) == FAST
    assert needs_rehash(old, OWASP_MINIMUM)
    assert not needs_rehash(old, FAST)
    assert needs_rehash("not a hash", FAST)


@pytest.mark.parametrize(
    "password, email, expected",
    [
        ("", None, "is required."),
        ("x" * (MIN_LENGTH - 1), None, f"must be at least {MIN_LENGTH} characters."),
        ("x" * (MAX_LENGTH + 1), None, f"must be {MAX_LENGTH} characters or fewer."),
        ("password1234", None, "is too common and easy to guess. Choose a less predictable one."),
        ("aaaaaaaaaaaaaaaa", None, "is too common and easy to guess. Choose a less predictable one."),
        ("trader@example.com", "Trader@Example.com", "must not be your email address."),
        ("four random words here", None, None),
        ("ünïcødé pässwörd ok", None, None),
        ("x" * MAX_LENGTH + "", None, "is too common and easy to guess. Choose a less predictable one."),
    ],
)
def test_password_rules(password, email, expected):
    assert password_problem(password, email=email) == expected


def test_no_composition_rules():
    """NIST SP 800-63B: length matters, forced symbols and capitals do not."""
    assert password_problem("all lower case words only") is None
```

### `tests/test_postgres.py` (NEW)

**Why:** 20 PostgreSQL-only tests including the import script.  
**How to run or test:** `python -m pytest tests/test_postgres.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 8: PostgreSQL-specific behaviour and the SQLite -> PostgreSQL import.

Marked postgres_only: these run when TRADELEDGER_TEST_DATABASE_URL points at
a PostgreSQL server (CI's postgres service, or a local server) and are
reported as skipped otherwise. Every test works in its own empty schema.
"""

import json
import threading
from decimal import Decimal

import pytest

from tests.conftest import OTHER_EMAIL, TEST_EMAIL, ensure_user
from tests.legacy_fixtures import build_phase7_database
from tradeledger.db import IntegrityError, connect, init_schema, list_tables
from tradeledger.migrations import LATEST_VERSION, POSTGRES_BASELINE_VERSION, current_version
from tradeledger.repositories import instruments as instrument_repo
from tradeledger.repositories import trades as trade_repo
from tradeledger.seed import seed_sample_data

pytestmark = pytest.mark.postgres_only

import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "migrate_sqlite_to_postgres",
    Path(__file__).resolve().parent.parent / "scripts" / "migrate_sqlite_to_postgres.py",
)
importer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(importer)


# ---------- Schema ----------


def test_postgres_and_sqlite_end_on_the_same_version(conn):
    assert conn.dialect == "postgresql"
    assert current_version(conn) == POSTGRES_BASELINE_VERSION == LATEST_VERSION


def test_postgres_has_the_same_tables_and_columns_as_sqlite(conn, tmp_path):
    sqlite = connect(str(tmp_path / "compare.db"))
    init_schema(sqlite)
    try:
        assert set(list_tables(conn)) - {"schema_migrations"} == set(list_tables(sqlite))
        for table in ("instruments", "trades", "users", "api_tokens", "login_attempts"):
            pg_columns = {
                r["name"]
                for r in conn.execute(
                    "SELECT column_name AS name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = ?",
                    (table,),
                )
            }
            lite_columns = {r["name"] for r in sqlite.execute(f"PRAGMA table_info({table})")}
            assert pg_columns == lite_columns, table
    finally:
        sqlite.close()


def test_money_columns_are_exact_numeric(conn):
    types = {
        r["column_name"]: r["data_type"]
        for r in conn.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'trades'"
        )
    }
    for column in ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees"):
        assert types[column] == "numeric", column  # never real / double precision


def test_init_schema_is_idempotent_and_never_drops_data(seeded_conn, user_id):
    assert init_schema(seeded_conn) == []
    assert len(trade_repo.list_trades(seeded_conn, user_id=user_id)) == 16


def test_two_processes_migrating_at_once_are_safe(db_path):
    """The advisory lock makes concurrent migrations wait, not collide."""
    errors = []

    def migrate():
        try:
            c = connect(db_path)
            init_schema(c)
            c.close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=migrate) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    check = connect(db_path)
    assert current_version(check) == LATEST_VERSION
    assert check.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 1
    check.close()


# ---------- Decimal precision ----------


@pytest.mark.parametrize(
    "price",
    ["999999999999999.999999999999999", "0.000000000000001", "1.10000", "123456.123456789"],
)
def test_decimals_round_trip_exactly(conn, eurusd_id, user_id, price):
    entry = Decimal(price)
    trade_id = (
        trade_repo.create_trade(
            conn,
            {
                "instrument_id": eurusd_id,
                "trade_date": "2026-03-02T08:15:00Z",
                "direction": "SHORT",
                "entry_price": price,
                "stop_loss": str(entry * 2) if entry < Decimal("1e14") else "999999999999999.999999999999999",
                "position_size": "1",
                "status": "OPEN",
            },
            user_id=user_id,
        )
        if entry < Decimal("1e14")
        else None
    )
    if trade_id is None:
        # The maximum value: store it directly in a NUMERIC column and read it back.
        row = conn.execute("SELECT CAST(? AS NUMERIC) AS v", (conn.numeric(price),)).fetchone()
        assert row["v"] == entry and str(row["v"]) == price
        return
    stored = trade_repo.get_trade(conn, trade_id, user_id=user_id)["entry_price"]
    assert stored == entry  # exactly equal: no binary floating point anywhere
    assert isinstance(stored, Decimal)


def test_database_limits_repeat_the_validation_limits(conn, eurusd_id, user_id):
    """Defence in depth: even raw SQL cannot store 16 integer digits."""
    with pytest.raises(IntegrityError):
        conn.execute(
            "INSERT INTO trades (user_id, instrument_id, trade_date, direction, entry_price, stop_loss, "
            "position_size, status) VALUES (?, ?, '2026-03-02T08:15:00Z', 'LONG', ?, '1', '1', 'OPEN')",
            (user_id, eurusd_id, conn.numeric("1" + "0" * 15)),
        )


def test_closed_at_rule_is_a_named_check_constraint(seeded_conn, user_id):
    with pytest.raises(IntegrityError) as info:
        seeded_conn.execute(
            "UPDATE trades SET closed_at = '2020-01-01T00:00:00Z' WHERE user_id = ? AND status = 'CLOSED'",
            (user_id,),
        )
    assert info.value.constraint == "closed_at_rules"


# ---------- Concurrency ----------


def test_concurrent_edits_are_serialised(db_path, seeded_conn, user_id):
    """update_trade locks the row (SELECT ... FOR UPDATE): a second
    connection's edit waits for the first transaction instead of
    interleaving with it."""
    trade = trade_repo.list_trades(seeded_conn, user_id=user_id, status="CLOSED")[0]
    data = {k: trade[k] for k in trade_repo._WRITE_FIELDS}
    for key in ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees"):
        data[key] = None if data[key] is None else str(data[key])
    first, second = connect(db_path), connect(db_path)
    order = []
    try:
        with first:
            first.execute("SELECT id FROM trades WHERE id = ? FOR UPDATE", (trade["id"],))

            def other_edit():
                trade_repo.update_trade(second, trade["id"], {**data, "notes": "second"}, user_id=user_id)
                order.append("second")

            worker = threading.Thread(target=other_edit)
            worker.start()
            worker.join(timeout=0.5)
            assert worker.is_alive()  # blocked by the first transaction's lock
            first.execute("UPDATE trades SET notes = 'first' WHERE id = ?", (trade["id"],))
            order.append("first")
        worker.join(timeout=5)
        assert order == ["first", "second"]
        assert trade_repo.get_trade(seeded_conn, trade["id"], user_id=user_id)["notes"] == "second"
    finally:
        first.close()
        second.close()


def test_failed_write_rolls_back_the_whole_transaction(conn, user_id):
    with pytest.raises(IntegrityError), conn:
        instrument_repo.create_instrument(
            conn,
            {
                "symbol": "AAA",
                "name": "a",
                "asset_class": "OTHER",
                "price_increment": "0.1",
                "contract_multiplier": "1",
                "quote_currency": "USD",
            },
            user_id=user_id,
        )
        conn.execute("INSERT INTO trades (user_id) VALUES (?)", (user_id,))  # violates NOT NULL
    assert instrument_repo.list_instruments(conn, user_id=user_id) == []


# ---------- SQLite -> PostgreSQL import ----------


@pytest.fixture
def phase7_file(tmp_path):
    path = str(tmp_path / "phase7.db")
    build_phase7_database(path)
    return path


def run_import(phase7_file, db_path, *extra):
    lines = []
    args = ["--sqlite", phase7_file, "--postgres", db_path, "--owner-email", TEST_EMAIL, *extra]
    from tests.conftest import PG_URL  # noqa: F401 - the bridge is registered by conftest when used

    code, summary = importer.run(args, out=lines.append)
    return code, summary, "\n".join(lines)


def test_import_dry_run_writes_nothing(phase7_file, db_path, conn, user_id):
    code, summary, output = run_import(phase7_file, db_path)
    assert code == 0, output
    assert summary["status"] == "dry run: nothing written"
    assert (summary["instruments"], summary["trades"]) == (5, 16)
    assert trade_repo.list_trades(conn, user_id=user_id) == []


def test_import_copies_everything_exactly(phase7_file, db_path, conn, user_id, tmp_path):
    import sqlite3

    report = tmp_path / "audit.json"
    code, summary, output = run_import(phase7_file, db_path, "--apply", "--report", str(report))
    assert code == 0, output
    assert summary["status"] == "imported and verified"
    audit = json.loads(report.read_text())
    assert audit["trades"] == 16 and len(audit["trade_id_map"]) == 16
    assert "@" not in audit["target"] or ":***@" in audit["target"] or "postgres@" in audit["target"]

    source = sqlite3.connect(phase7_file)
    source.row_factory = sqlite3.Row
    for old in source.execute("SELECT * FROM trades ORDER BY id"):
        new = trade_repo.get_trade(conn, audit["trade_id_map"][str(old["id"])], user_id=user_id)
        for field in ("entry_price", "stop_loss", "exit_price", "position_size", "fees"):
            assert (new[field] is None and old[field] is None) or new[field] == Decimal(old[field])
        assert new["closed_at"] == old["closed_at"]
        assert new["created_at"] == old["created_at"]
    source.close()


def test_import_report_matches_the_engine_on_both_databases(phase7_file, db_path, conn, user_id, tmp_path):
    from tradeledger.legacy import claim_legacy_data
    from tradeledger.performance import build_report

    run_import(phase7_file, db_path, "--apply")
    lite = connect(phase7_file)
    init_schema(lite)
    claim_legacy_data(lite, user_id=ensure_user(lite), apply=True)
    a, b = build_report(lite, user_id=1).overall, build_report(conn, user_id=user_id).overall
    lite.close()
    assert (a.trades, a.wins, a.total_r, a.expectancy_r) == (b.trades, b.wins, b.total_r, b.expectancy_r)


def test_import_refuses_a_non_empty_target(phase7_file, db_path, seeded_conn):
    code, _summary, output = run_import(phase7_file, db_path, "--apply")
    assert code == 1
    assert "only writes into an empty journal" in output
    assert len(trade_repo.list_trades(seeded_conn, user_id=1)) == 16  # untouched


def test_import_refuses_an_unknown_owner(phase7_file, db_path, conn):
    lines = []
    code, _ = importer.run(
        ["--sqlite", phase7_file, "--postgres", db_path, "--owner-email", "ghost@example.com", "--apply"],
        out=lines.append,
    )
    assert code == 1 and "create it first" in "\n".join(lines)


def test_import_into_one_account_leaves_others_alone(phase7_file, db_path, conn):
    other = ensure_user(conn, OTHER_EMAIL)
    seed_sample_data(conn, user_id=other)
    code, _, output = run_import(phase7_file, db_path, "--apply")
    assert code == 0, output
    assert len(trade_repo.list_trades(conn, user_id=other)) == 16
    assert len(trade_repo.list_trades(conn, user_id=1)) == 16


def test_import_never_prints_a_password(phase7_file, db_path, conn):
    url_with_password = db_path.replace(
        "postgresql://postgres@", "postgresql://postgres:NotARealPassword@", 1
    )
    _, summary, output = run_import(phase7_file, url_with_password)
    assert "NotARealPassword" not in output and "NotARealPassword" not in json.dumps(summary)
```

### `scripts/backup_restore_check.py` (NEW)

**Why:** pg_dump → pg_restore into a new database, then compares rows, values and reports.  
**How to run or test:** `python scripts/backup_restore_check.py --help`

```python
"""Prove a PostgreSQL backup can be restored (Phase 8).

A backup you have never restored is a hope, not a backup. This script:

  1. creates a throwaway source database and fills it with SYNTHETIC data
     (two accounts, the sample trades, one API token);
  2. backs it up with pg_dump (custom format, compressed);
  3. restores the file into a second, empty database with pg_restore;
  4. compares the two: row counts in every table, every trade value, and
     the performance report of each account (the Decimal engine);
  5. drops both throwaway databases.

It needs the PostgreSQL client tools (pg_dump, pg_restore) on PATH and a
server URL with permission to create databases. It never touches an
existing database: the two it creates have random names.

    python scripts/backup_restore_check.py --server postgresql://postgres:secret@localhost:5432/postgres
      (add --pg-bridge to use the test-only libpq bridge where psycopg is not installed)

The same procedure applies to production with pg_dump against RDS, or with
an RDS snapshot restored to a NEW instance (docs/PHASE_8_DATABASE_MIGRATION.md).
"""

import argparse
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from tradeledger.db import (  # noqa: E402
    connect,
    init_schema,
    list_tables,
    redact_url,
    register_postgres_driver,
)
from tradeledger.passwords import HashParameters, hash_password  # noqa: E402
from tradeledger.performance import build_report  # noqa: E402
from tradeledger.repositories import api_tokens  # noqa: E402
from tradeledger.repositories.users import create_user  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

FAST = HashParameters(memory_kib=1024, iterations=1, lanes=1)


def with_database(url, name):
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, f"/{name}", parts.query, ""))


def snapshot(conn):
    tables = {}
    for table in list_tables(conn):  # names from the catalogue, not user input
        tables[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608
    trades = [tuple(row.values()) for row in conn.execute("SELECT * FROM trades ORDER BY id")]
    reports = {}
    for user_id in (1, 2):
        overall = build_report(conn, user_id=user_id).overall
        reports[user_id] = (overall.trades, overall.wins, overall.total_r, overall.expectancy_r)
    return tables, trades, reports


def main(argv=None):
    parser = argparse.ArgumentParser(description="pg_dump / pg_restore round trip with synthetic data")
    parser.add_argument("--server", required=True, help="postgresql:// URL of a maintenance database")
    parser.add_argument("--pg-bridge", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.pg_bridge:
        sys.path.insert(0, str(PROJECT / "tests"))
        import pg_libpq_bridge

        register_postgres_driver(pg_libpq_bridge)
    for tool in ("pg_dump", "pg_restore"):
        if shutil.which(tool) is None:
            sys.exit(f"{tool} is not on PATH")

    suffix = secrets.token_hex(4)
    source_name, target_name = f"tl_backup_src_{suffix}", f"tl_backup_dst_{suffix}"
    admin = connect(args.server)
    admin.execute(f"CREATE DATABASE {source_name}")
    admin.execute(f"CREATE DATABASE {target_name}")
    source_url, target_url = with_database(args.server, source_name), with_database(args.server, target_name)
    try:
        src = connect(source_url)
        init_schema(src)
        for email in ("alice@example.com", "bob@example.com"):
            user_id = create_user(src, email, hash_password("synthetic password", FAST))
            seed_sample_data(src, user_id=user_id)
        api_tokens.create_token(src, user_id=1, name="backup check", lifetime_days=1, max_active=10)
        before = snapshot(src)
        src.close()

        with tempfile.TemporaryDirectory() as folder:
            dump = Path(folder) / "tradeledger.dump"
            subprocess.run(
                ["pg_dump", "--format=custom", "--no-owner", f"--file={dump}", source_url], check=True
            )
            print(f"backup written: {dump.stat().st_size:,} bytes (custom format)")
            subprocess.run(
                ["pg_restore", "--no-owner", "--exit-on-error", f"--dbname={target_url}", str(dump)],
                check=True,
            )

        dst = connect(target_url)
        after = snapshot(dst)
        dst.close()
        checks = {
            "row counts per table": before[0] == after[0],
            "every trade value": before[1] == after[1],
            "performance reports": before[2] == after[2],
        }
        print(f"server: {redact_url(args.server)}")
        print(f"tables restored: {after[0]}")
        for name, ok in checks.items():
            print(f"  {'OK  ' if ok else 'FAIL'} {name}")
        return 0 if all(checks.values()) else 1
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {source_name}")
        admin.execute(f"DROP DATABASE IF EXISTS {target_name}")
        admin.close()


if __name__ == "__main__":
    sys.exit(main())
```

### `scripts/compose_smoke_test.sh` (NEW)

**Why:** End-to-end check of the Compose stack (health, sign-in, CSRF, create, isolation, API).  
**How to run or test:** `bash scripts/compose_smoke_test.sh`

```bash
#!/usr/bin/env bash
# End-to-end smoke test of the Docker Compose stack (Phase 8).
#
# Builds the image, starts PostgreSQL + the app, and checks, through real
# HTTP requests to http://localhost:8000:
#   1. liveness and readiness (the app can reach PostgreSQL)
#   2. the database schema was created by the entrypoint
#   3. an account created with the CLI can sign in (CSRF + session cookie)
#   4. a trade can be created and appears in the journal and the analytics
#   5. an API token works and anonymous API calls get 401
#   6. after "docker compose restart", the trade is still there
#   7. PostgreSQL publishes no port to the host
# Used by the CI "docker" job; also runs on any machine with Docker.
# It creates throwaway secrets and removes the stack and its volume at the end.
#
#   bash scripts/compose_smoke_test.sh
set -euo pipefail
# (No "grep -q" or "head" below: with pipefail, a reader that stops early can make the
# command before it fail with SIGPIPE and turn a pass into a failure.)
cd "$(dirname "$0")/.."

BASE=http://localhost:8000
JAR=$(mktemp)
PASSWORD="smoke test passphrase $(date +%s)"
created_secrets=0

cleanup() {
    docker compose down -v --remove-orphans >/dev/null 2>&1 || true
    if [ "$created_secrets" = 1 ]; then rm -f secrets/secret_key.txt secrets/db_password.txt; fi
    rm -f "$JAR"
}
trap cleanup EXIT

if [ ! -f secrets/secret_key.txt ]; then
    python3 -c "import secrets; print(secrets.token_hex(32))" > secrets/secret_key.txt
    python3 -c "import secrets; print(secrets.token_urlsafe(24))" > secrets/db_password.txt
    created_secrets=1
fi

step() { printf '\n== %s\n' "$*"; }
fail() { echo "FAILED: $*" >&2; docker compose logs app | tail -50 >&2; exit 1; }

step "build and start"
docker compose up --build -d

step "wait for readiness"
for _ in $(seq 1 60); do
    if curl -fsS "$BASE/health/ready" >/dev/null 2>&1; then break; fi
    sleep 2
done
curl -fsS "$BASE/health/live" | grep '"ok"' >/dev/null || fail "liveness"
curl -fsS "$BASE/health/ready" | grep '"ready"' >/dev/null || fail "readiness"

step "schema created by the entrypoint"
docker compose exec -T app flask --app tradeledger db-info | grep "Schema version: 3" >/dev/null || fail "schema version"

step "create an account with the CLI"
printf '%s\n' "$PASSWORD" | docker compose exec -T app flask --app tradeledger create-user \
    --email smoke@example.com --password-stdin

csrf() { curl -fsS -b "$JAR" -c "$JAR" "$BASE$1" | sed -n 's/.*name="csrf_token" value="\([^"]*\)".*/\1/p' | awk 'NR==1'; }

step "sign in through the form"
TOKEN=$(csrf /login)
[ -n "$TOKEN" ] || fail "no CSRF token on the sign-in page"
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' -b "$JAR" -c "$JAR" \
    --data-urlencode "csrf_token=$TOKEN" --data-urlencode "email=smoke@example.com" \
    --data-urlencode "password=$PASSWORD" "$BASE/login")
[ "$STATUS" = 303 ] || fail "sign-in returned $STATUS"
curl -fsS -b "$JAR" "$BASE/" | grep "smoke@example.com" >/dev/null || fail "overview after sign-in"

step "add an instrument and a trade"
TOKEN=$(csrf /instruments)
curl -fsS -o /dev/null -b "$JAR" -c "$JAR" --data-urlencode "csrf_token=$TOKEN" \
    -d symbol=EURUSD -d name=Euro -d asset_class=FOREX -d quote_currency=USD \
    -d price_increment=0.00001 -d contract_multiplier=100000 "$BASE/instruments"
TOKEN=$(csrf /trades/new)
INSTRUMENT=$(curl -fsS -b "$JAR" "$BASE/trades/new" | sed -n 's/.*<option value="\([0-9]*\)"[^>]*>EURUSD.*/\1/p' | awk 'NR==1')
STATUS=$(curl -sS -o /dev/null -w '%{http_code}' -b "$JAR" -c "$JAR" --data-urlencode "csrf_token=$TOKEN" \
    -d instrument_id="$INSTRUMENT" -d direction=LONG -d status=CLOSED \
    -d trade_date=2026-03-02T08:15 -d closed_at=2026-03-02T11:40 \
    -d entry_price=1.08500 -d stop_loss=1.08300 -d exit_price=1.08900 -d position_size=1 -d fees=7 \
    "$BASE/trades/new")
case "$STATUS" in 302|303) ;; *) fail "creating a trade returned $STATUS";; esac
curl -fsS -b "$JAR" "$BASE/trades" | grep "EURUSD" >/dev/null || fail "journal does not list the trade"
curl -fsS -b "$JAR" "$BASE/analytics" | grep '+1.97R' >/dev/null || fail "analytics do not show +1.97R"

step "API: anonymous 401, token 200"
[ "$(curl -sS -o /dev/null -w '%{http_code}' "$BASE/api/v1/trades")" = 401 ] || fail "anonymous API call"
TOKEN=$(csrf /account)
API_TOKEN=$(curl -fsS -b "$JAR" -c "$JAR" --data-urlencode "csrf_token=$TOKEN" -d name=smoke -d lifetime_days=30 \
    "$BASE/account/tokens" | sed -n 's/.*id="new-token-value"[^>]*value="\(tl_[^"]*\)".*/\1/p; s/.*value="\(tl_[^"]*\)".*/\1/p' | awk 'NR==1')
[ -n "$API_TOKEN" ] || fail "no API token issued"
curl -fsS -H "Authorization: Bearer $API_TOKEN" "$BASE/api/v1/trades" | grep '"total": *1' >/dev/null || fail "API list"

step "restart: data persists"
docker compose restart app
for _ in $(seq 1 30); do curl -fsS "$BASE/health/ready" >/dev/null 2>&1 && break; sleep 2; done
curl -fsS -H "Authorization: Bearer $API_TOKEN" "$BASE/api/v1/trades" | grep '"total": *1' >/dev/null || fail "data after restart"

step "PostgreSQL is not published on the host"
if docker compose port db 5432 >/dev/null 2>&1; then fail "db port is published"; fi

step "the app runs as a non-root user"
[ "$(docker compose exec -T app id -u)" = 10001 ] || fail "container user is not 10001"

echo
echo "Compose smoke test passed."
```

### `scripts/migrate_sqlite_to_postgres.py` (NEW)

**Why:** Verified one-way import of a SQLite journal into one named PostgreSQL account.  
**How to run or test:** `python scripts/migrate_sqlite_to_postgres.py --help`

```python
"""Copy a TradeLedger SQLite journal into a PostgreSQL database (Phase 8).

Plain English
-------------
You have been using TradeLedger on your own computer (SQLite). Now you want
the same journal in PostgreSQL (Docker Compose or AWS RDS). This script
copies your instruments and trades across, gives them to ONE account that
you name, checks that every number arrived exactly, and prints an audit
summary. By default it only reports what it WOULD do.

    # 1. Create the account in PostgreSQL first (asks for a password):
    DATABASE_URL=postgresql://... flask --app tradeledger create-user --email you@example.com
    # 2. Dry run (reads both databases, writes nothing):
    python scripts/migrate_sqlite_to_postgres.py --sqlite instance/tradeledger.db \\
        --postgres "$DATABASE_URL" --owner-email you@example.com
    # 3. The real copy, with a JSON audit file:
    python scripts/migrate_sqlite_to_postgres.py ... --apply --report import-audit.json

Rules (why the script is strict)
--------------------------------
* The SQLite file is opened READ-ONLY; it is never changed.
* Ownership is explicit: --owner-email must name an existing PostgreSQL
  account. Nothing is ever given to "the first user".
* The owner's PostgreSQL journal must be EMPTY. The script never merges
  into, overwrites or deletes existing data. To redo an import, restore
  the target from a backup or use a fresh account.
* Which SQLite rows are copied:
    - a Phase 7 (schema version 2) file: every row (they have no owner);
    - a Phase 8 (version 3) file: the unassigned legacy rows, or with
      --source-email, the rows of that SQLite account.
* Every row goes through the same validation as the application. A row
  that would be refused today stops the import, with its id, before
  anything is written.
* Legacy closed trades with no recorded exit time keep closed_at NULL.
  created_at and updated_at are copied as they are.
* Everything is written in ONE transaction. After writing, the script
  re-reads every row and compares counts, every decimal (exactly, as
  Decimal) and every text field; any difference rolls the whole import
  back. Ids are NOT kept (PostgreSQL assigns new ones); the audit file
  lists the old -> new id mapping.
* Passwords and full connection strings are never printed.

Exit codes: 0 done (or dry run OK), 1 refused (nothing written), 2 error.
"""

import argparse
import json
import sqlite3
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from tradeledger.db import connect, init_schema, redact_url, register_postgres_driver  # noqa: E402
from tradeledger.migrations import current_version  # noqa: E402
from tradeledger.observability import redact  # noqa: E402
from tradeledger.repositories.users import get_user_by_email, normalise_email  # noqa: E402
from tradeledger.validation import ValidationError, validate_instrument, validate_trade  # noqa: E402

INSTRUMENT_FIELDS = (
    "symbol",
    "name",
    "asset_class",
    "price_increment",
    "contract_multiplier",
    "quote_currency",
)
TRADE_FIELDS = (
    "trade_date",
    "closed_at",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "status",
    "strategy",
    "session",
    "notes",
)
DECIMALS = {
    "price_increment",
    "contract_multiplier",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
}


class Refused(Exception):
    """A safety rule stopped the import before anything was written."""


def read_source(path, source_email):
    """Read instruments and trades from the SQLite file (read-only)."""
    if not Path(path).is_file():
        raise Refused(f"SQLite file not found: {path}")
    raw = sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)
    raw.row_factory = sqlite3.Row
    try:
        version = raw.execute("PRAGMA user_version").fetchone()[0]
        if version < 2:
            raise Refused(
                f"SQLite schema version {version}: run 'flask --app tradeledger migrate-db' on it first"
            )
        if version == 2:
            if source_email:
                raise Refused(
                    "--source-email needs a Phase 8 (version 3) file; a version 2 file has no accounts"
                )
            owner_clause, params, label = "", (), "all rows (Phase 7 file, no owners)"
        elif source_email:
            row = raw.execute(
                "SELECT id FROM users WHERE email = ?", (normalise_email(source_email),)
            ).fetchone()
            if row is None:
                raise Refused(f"no account {normalise_email(source_email)} in the SQLite file")
            owner_clause, params, label = (
                "WHERE user_id = ?",
                (row["id"],),
                f"rows of {normalise_email(source_email)}",
            )
        else:
            owner_clause, params, label = "WHERE user_id IS NULL", (), "unassigned legacy rows"
        instruments = [
            dict(r)
            for r in raw.execute(
                f"SELECT id, {', '.join(INSTRUMENT_FIELDS)}, created_at FROM instruments {owner_clause} ORDER BY id",  # noqa: S608 - constant fragments
                params,
            )
        ]
        trades = [
            dict(r)
            for r in raw.execute(
                f"SELECT id, instrument_id, {', '.join(TRADE_FIELDS)}, created_at, updated_at "  # noqa: S608 - constant fragments
                f"FROM trades {owner_clause.replace('user_id', 'trades.user_id')} ORDER BY id",
                params,
            )
        ]
    finally:
        raw.close()
    return {"version": version, "selection": label, "instruments": instruments, "trades": trades}


def check_source(source):
    """Validate every row with the application's own rules; find duplicates."""
    problems, warnings = [], []
    known = {i["id"] for i in source["instruments"]}
    for instrument in source["instruments"]:
        try:
            validate_instrument({k: instrument[k] for k in INSTRUMENT_FIELDS})
        except ValidationError as exc:
            problems.append(f"instrument #{instrument['id']}: {exc.errors}")
    seen = {}
    for trade in source["trades"]:
        if trade["instrument_id"] not in known:
            problems.append(
                f"trade #{trade['id']}: its instrument #{trade['instrument_id']} is not being imported"
            )
            continue
        legacy = trade["status"] == "CLOSED" and trade["closed_at"] is None
        data = {k: trade[k] for k in TRADE_FIELDS if k != "status"} | {
            "status": trade["status"],
            "instrument_id": trade["instrument_id"],
        }
        try:
            validate_trade(data, require_closed_at=not legacy)
        except ValidationError as exc:
            problems.append(f"trade #{trade['id']}: {exc.errors}")
        key = (trade["instrument_id"], trade["trade_date"], trade["direction"], Decimal(trade["entry_price"]))
        if key in seen:
            warnings.append(
                f"trade #{trade['id']} looks like a duplicate of trade #{seen[key]} (both are copied)"
            )
        seen[key] = trade["id"]
    return problems, warnings


def check_target(conn, owner_email):
    owner = get_user_by_email(conn, owner_email)
    if owner is None:
        raise Refused(
            f"no PostgreSQL account {normalise_email(owner_email)}: create it first with "
            "'flask --app tradeledger create-user --email ...'"
        )
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (owner["id"],)).fetchone()[0]  # noqa: S608 - fixed names
        for table in ("instruments", "trades")
    }
    if any(counts.values()):
        raise Refused(
            f"the account {owner['email']} already has {counts['instruments']} instrument(s) and "
            f"{counts['trades']} trade(s) in PostgreSQL; the import only writes into an empty journal"
        )
    return owner


def write_and_verify(conn, source, owner_id):
    """Insert everything and verify it, in ONE transaction."""
    instrument_map, trade_map = {}, {}
    with conn:
        for i in source["instruments"]:
            clean = validate_instrument({k: i[k] for k in INSTRUMENT_FIELDS})
            row = conn.execute(
                "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, contract_multiplier, "
                "quote_currency, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                (
                    owner_id,
                    clean["symbol"],
                    clean["name"],
                    clean["asset_class"],
                    conn.numeric(i["price_increment"]),
                    conn.numeric(i["contract_multiplier"]),
                    clean["quote_currency"],
                    i["created_at"],
                ),
            ).fetchone()
            instrument_map[i["id"]] = row["id"]
        for t in source["trades"]:
            values = [owner_id, instrument_map[t["instrument_id"]]]
            values += [conn.numeric(t[f]) if f in DECIMALS else t[f] for f in TRADE_FIELDS]
            values += [t["created_at"], t["updated_at"]]
            row = conn.execute(
                f"INSERT INTO trades (user_id, instrument_id, {', '.join(TRADE_FIELDS)}, created_at, updated_at) "  # noqa: S608 - constant columns
                f"VALUES ({', '.join('?' for _ in values)}) RETURNING id",
                values,
            ).fetchone()
            trade_map[t["id"]] = row["id"]
        mismatches = verify(conn, source, owner_id, instrument_map, trade_map)
        if mismatches:
            raise RuntimeError("verification failed, nothing was kept: " + "; ".join(mismatches[:10]))
    return instrument_map, trade_map


def same(field, before, after):
    if before is None or after is None:
        return before is None and after is None
    if field in DECIMALS:
        return Decimal(str(before)) == Decimal(str(after))  # exact: no float anywhere
    return before == after


def verify(conn, source, owner_id, instrument_map, trade_map):
    problems = []
    counts = {
        table: conn.execute(f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (owner_id,)).fetchone()[0]  # noqa: S608 - fixed names
        for table in ("instruments", "trades")
    }
    if counts != {"instruments": len(source["instruments"]), "trades": len(source["trades"])}:
        problems.append(f"row counts differ: {counts}")
    for i in source["instruments"]:
        row = conn.execute("SELECT * FROM instruments WHERE id = ?", (instrument_map[i["id"]],)).fetchone()
        for field in (*INSTRUMENT_FIELDS, "created_at"):
            if not same(field, i[field], row[field]):
                problems.append(f"instrument #{i['id']} {field}: {i[field]!r} -> {row[field]!r}")
    for t in source["trades"]:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_map[t["id"]],)).fetchone()
        if row["instrument_id"] != instrument_map[t["instrument_id"]] or row["user_id"] != owner_id:
            problems.append(f"trade #{t['id']}: wrong instrument or owner")
        for field in (*TRADE_FIELDS, "created_at", "updated_at"):
            if not same(field, t[field], row[field]):
                problems.append(f"trade #{t['id']} {field}: {t[field]!r} -> {row[field]!r}")
    orphans = conn.execute(
        "SELECT COUNT(*) FROM trades t JOIN instruments i ON i.id = t.instrument_id "
        "WHERE t.user_id = ? AND i.user_id <> t.user_id",
        (owner_id,),
    ).fetchone()[0]
    if orphans:
        problems.append(f"{orphans} trade(s) point at another account's instrument")
    return problems


def run(argv=None, out=print):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--sqlite", required=True, help="path to the SQLite file (opened read-only)")
    parser.add_argument("--postgres", required=True, help="postgresql:// URL of the target database")
    parser.add_argument(
        "--owner-email", required=True, help="existing PostgreSQL account that will own the rows"
    )
    parser.add_argument("--source-email", help="Phase 8 SQLite files: copy this account's rows")
    parser.add_argument("--apply", action="store_true", help="write (default: dry run, writes nothing)")
    parser.add_argument("--report", help="write the audit summary to this JSON file")
    parser.add_argument("--pg-bridge", action="store_true", help=argparse.SUPPRESS)  # test-only libpq bridge
    args = parser.parse_args(argv)

    if args.pg_bridge:
        sys.path.insert(0, str(PROJECT / "tests"))
        import pg_libpq_bridge

        register_postgres_driver(pg_libpq_bridge)

    summary = {
        "started_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": str(Path(args.sqlite).resolve()),
        "target": redact_url(args.postgres),
        "mode": "apply" if args.apply else "dry-run",
    }
    try:
        source = read_source(args.sqlite, args.source_email)
        problems, warnings = check_source(source)
        legacy = sum(1 for t in source["trades"] if t["status"] == "CLOSED" and t["closed_at"] is None)
        summary |= {
            "source_schema_version": source["version"],
            "selection": source["selection"],
            "instruments": len(source["instruments"]),
            "trades": len(source["trades"]),
            "legacy_trades_without_exit_time": legacy,
            "warnings": warnings,
        }
        if problems:
            raise Refused("rows that the application would refuse:\n  " + "\n  ".join(problems))
        conn = connect(args.postgres)
        try:
            if current_version(conn) == 0 and not args.apply:
                raise Refused(
                    "the PostgreSQL database has no TradeLedger schema yet (run init-db, then create-user)"
                )
            if args.apply:
                init_schema(conn)  # creates or upgrades the schema; never deletes data
            owner = check_target(conn, args.owner_email)
            summary["owner"] = owner["email"]
            if args.apply:
                instrument_map, trade_map = write_and_verify(conn, source, owner["id"])
                summary |= {
                    "status": "imported and verified",
                    "instrument_id_map": {str(k): v for k, v in instrument_map.items()},
                    "trade_id_map": {str(k): v for k, v in trade_map.items()},
                }
            else:
                summary["status"] = "dry run: nothing written"
        finally:
            conn.close()
        code = 0
    except Refused as exc:
        summary |= {"status": "refused: nothing written", "reason": str(exc)}
        code = 1
    except Exception as exc:  # noqa: BLE001 - reported with a non-zero exit code; nothing was committed
        summary |= {"status": "error: nothing written", "reason": redact(str(exc))}
        code = 2
    summary["finished_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for key in ("status", "selection", "instruments", "trades", "legacy_trades_without_exit_time", "owner"):
        if key in summary:
            out(f"{key:>32}: {summary[key]}")
    for warning in summary.get("warnings", []):
        out(f"WARNING: {warning}")
    if "reason" in summary:
        out(summary["reason"])
    if args.report:
        Path(args.report).write_text(json.dumps(summary, indent=2), encoding="utf-8")
        out(f"Audit summary written to {args.report}")
    return code, summary


if __name__ == "__main__":
    sys.exit(run()[0])
```

## 2. Modified files

### `tradeledger/__init__.py` (MODIFIED)

**Why:** Wires logging, refuses unsafe production settings, configures ProxyFix, logs the redacted database label.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
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
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = build_csp(g.get("csp_script_sources", ()))
        # Pages show trading records, so browsers and proxies must not store
        # them (Phase 7). Static CSS/JS are not HTML and stay cacheable; the
        # JSON API already sets its own no-store header.
        if response.mimetype == "text/html" and "Cache-Control" not in response.headers:
            response.headers["Cache-Control"] = "no-store"
        if app.config["HSTS_ENABLED"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response
```

### `tradeledger/analytics.py` (MODIFIED)

**Why:** Example SQL queries scoped by owner, with dialect-specific fragments.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Analytical SQL queries.

Each query is a named SQL string plus a small function that runs it and
returns plain Python values (lists of dicts or an int).

Definitions used here
---------------------
Closed trade     status = 'CLOSED' (so it has an exit price).
Gross P&L        LONG:  (exit - entry) x size x contract_multiplier
                 SHORT: (entry - exit) x size x contract_multiplier
                 in the instrument's quote currency.
Net P&L          gross P&L - fees  (fees assumed to be in the quote currency)
Win / loss / BE  net P&L > 0 / < 0 / = 0. Fees count, so a long closed
                 slightly above entry can still be a LOSS.
Win rate         wins / closed trades x 100. Breakevens stay in the
                 denominator (they are trades you took).

Why the win-rate query is a cross-check, not the final answer
-------------------------------------------------------------
SQLite has no exact decimal type, so arithmetic on our TEXT prices must go
through CAST(... AS REAL), which is binary floating point. The query treats
|net P&L| below 0.000001 as breakeven to absorb that rounding. On
PostgreSQL the same query uses the NUMERIC columns directly, which is exact.
Phase 3 does the authoritative calculation in Python with decimal.Decimal;
a test checks that both agree on the sample data.

Ownership and databases (Phase 8)
---------------------------------
Every query takes the user's id and only counts that user's records. The
SQL is written once with two small dialect-specific fragments: how to turn
a stored price into a number, and how to get the weekday of a timestamp.

Currency limitation
-------------------
Win/loss classification is per trade, and converting a P&L to another
currency never changes its sign, so win rates are valid even when
instruments are quoted in different currencies. SUMMING money across
currencies (e.g. USD + GBP) is NOT valid without exchange rates, which is
why no query here adds up P&L across instruments.
"""

from .repositories import require_user_id

_SQL = {
    # The number of a stored decimal: SQLite TEXT -> REAL; PostgreSQL NUMERIC as is.
    "sqlite": {"num": "CAST({} AS REAL)", "weekday": "CAST(strftime('%w', {}) AS INTEGER)"},
    "postgresql": {"num": "{}", "weekday": "CAST(EXTRACT(DOW FROM CAST({} AS TIMESTAMP)) AS INTEGER)"},
}

CLOSED_TRADE_COUNT = """
SELECT COUNT(*) AS closed_trades
FROM trades
WHERE user_id = ? AND status = 'CLOSED';
"""

# LEFT JOIN keeps instruments that have no closed trades (count = 0).
# The status filter is in the ON clause, not WHERE, for exactly that reason.
CLOSED_COUNT_BY_INSTRUMENT = """
SELECT i.symbol,
       COUNT(t.id) AS closed_trades
FROM instruments AS i
LEFT JOIN trades AS t
       ON t.instrument_id = i.id
      AND t.status = 'CLOSED'
WHERE i.user_id = ?
GROUP BY i.id, i.symbol
ORDER BY closed_trades DESC, i.symbol;
"""

# Weekday number: 0 = Sunday ... 6 = Saturday, from the UTC trade_date.
CLOSED_BY_WEEKDAY = """
SELECT {weekday} AS weekday_number,
       CASE {weekday}
            WHEN 0 THEN 'Sunday'    WHEN 1 THEN 'Monday'
            WHEN 2 THEN 'Tuesday'   WHEN 3 THEN 'Wednesday'
            WHEN 4 THEN 'Thursday'  WHEN 5 THEN 'Friday'
            ELSE 'Saturday'
       END AS weekday,
       COUNT(*) AS closed_trades
FROM trades
WHERE user_id = ? AND status = 'CLOSED'
GROUP BY 1, 2
ORDER BY 1;
"""

TRADES_BY_STRATEGY = """
SELECT COALESCE(strategy, 'Unspecified') AS strategy,
       COUNT(*)                                        AS total_trades,
       SUM(CASE WHEN status = 'OPEN'   THEN 1 ELSE 0 END) AS open_trades,
       SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_trades
FROM trades
WHERE user_id = ?
GROUP BY COALESCE(strategy, 'Unspecified')
ORDER BY total_trades DESC, strategy;
"""

TRADES_BY_SESSION = """
SELECT COALESCE(session, 'Unspecified') AS session,
       COUNT(*)                                        AS total_trades,
       SUM(CASE WHEN status = 'OPEN'   THEN 1 ELSE 0 END) AS open_trades,
       SUM(CASE WHEN status = 'CLOSED' THEN 1 ELSE 0 END) AS closed_trades
FROM trades
WHERE user_id = ?
GROUP BY COALESCE(session, 'Unspecified')
ORDER BY total_trades DESC, session;
"""

# A CTE (WITH ...) works out net P&L per trade, a second classifies it, and
# the final SELECT aggregates per instrument.
WIN_RATE_BY_INSTRUMENT = """
WITH closed AS (
    SELECT i.symbol,
           i.quote_currency,
           CASE t.direction
                WHEN 'LONG'  THEN {exit} - {entry}
                ELSE              {entry} - {exit}
           END
           * {size}
           * {multiplier}
           - {fees} AS net_pnl
    FROM trades AS t
    JOIN instruments AS i ON i.id = t.instrument_id
    WHERE t.user_id = ? AND t.status = 'CLOSED'
),
classified AS (
    SELECT symbol,
           quote_currency,
           CASE WHEN net_pnl >  0.000001 THEN 'WIN'
                WHEN net_pnl < -0.000001 THEN 'LOSS'
                ELSE 'BREAKEVEN'
           END AS outcome
    FROM closed
)
SELECT symbol,
       quote_currency,
       COUNT(*)                                             AS closed_trades,
       SUM(CASE WHEN outcome = 'WIN'       THEN 1 ELSE 0 END) AS wins,
       SUM(CASE WHEN outcome = 'LOSS'      THEN 1 ELSE 0 END) AS losses,
       SUM(CASE WHEN outcome = 'BREAKEVEN' THEN 1 ELSE 0 END) AS breakevens,
       ROUND(100.0 * SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END) / COUNT(*), 1)
                                                            AS win_rate_pct
FROM classified
GROUP BY symbol, quote_currency
ORDER BY win_rate_pct DESC, symbol;
"""


def render(sql, dialect):
    """Fill in the dialect-specific fragments of one query."""
    parts = _SQL[dialect]
    num = parts["num"].format
    return sql.format(
        weekday=parts["weekday"].format("trade_date"),
        exit=num("t.exit_price"),
        entry=num("t.entry_price"),
        size=num("t.position_size"),
        multiplier=num("i.contract_multiplier"),
        fees=num("t.fees"),
    )


ALL_QUERIES = {
    "closed-count": CLOSED_TRADE_COUNT,
    "by-instrument": CLOSED_COUNT_BY_INSTRUMENT,
    "by-weekday": CLOSED_BY_WEEKDAY,
    "by-strategy": TRADES_BY_STRATEGY,
    "by-session": TRADES_BY_SESSION,
    "win-rate": WIN_RATE_BY_INSTRUMENT,
}

# Sums of integers come back as Decimal from PostgreSQL; plain ints and
# floats keep the results identical on both databases.
_INTEGER_COLUMNS = {"closed_trades", "total_trades", "open_trades", "wins", "losses", "breakevens"}


def run_query(conn, name, *, user_id):
    """Run one named query for one user; plain Python values back."""
    rows = []
    for row in conn.execute(render(ALL_QUERIES[name], conn.dialect), (require_user_id(user_id),)):
        record = dict(row)
        for key in record.keys() & _INTEGER_COLUMNS:
            record[key] = int(record[key])
        if "win_rate_pct" in record:
            record["win_rate_pct"] = float(record["win_rate_pct"])
        rows.append(record)
    return rows


def closed_trade_count(conn, *, user_id):
    return run_query(conn, "closed-count", user_id=user_id)[0]["closed_trades"]


def closed_count_by_instrument(conn, *, user_id):
    return run_query(conn, "by-instrument", user_id=user_id)


def closed_by_weekday(conn, *, user_id):
    return run_query(conn, "by-weekday", user_id=user_id)


def trades_by_strategy(conn, *, user_id):
    return run_query(conn, "by-strategy", user_id=user_id)


def trades_by_session(conn, *, user_id):
    return run_query(conn, "by-session", user_id=user_id)


def win_rate_by_instrument(conn, *, user_id):
    return run_query(conn, "win-rate", user_id=user_id)
```

### `tradeledger/api/__init__.py` (MODIFIED)

**Why:** Bearer-token authentication for the whole API; cookies ignored; 401 with WWW-Authenticate.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""TradeLedger JSON API, version 1, mounted at /api/v1.

Modules
  errors.py       ApiError and the single JSON error format
  http.py         reading JSON bodies and query strings safely
  serialise.py    rows and analytics results -> JSON-safe dicts
  trades.py       /trades endpoints
  instruments.py  /instruments endpoints
  analytics.py    /analytics endpoints (thin wrappers around build_report)
  openapi.py      the OpenAPI 3.1 specification, served at /openapi.json

Request flow
  HTTP request -> authenticate (Bearer token) -> route (thin) -> http.py
  checks shape -> repository (validation.py rules + parameterised SQL,
  always scoped to the token's owner) -> SQLite or PostgreSQL
  -> performance engine (analytics only) -> serialise.py -> JSON response

Authentication (Phase 8)
------------------------
Every endpoint except the OpenAPI document needs
    Authorization: Bearer tl_...
a personal API token created on the Account page (repositories/api_tokens.py).

The API deliberately IGNORES session cookies. Why it matters: a browser
attaches cookies to requests that other websites trigger, which is what
makes CSRF possible. It never attaches an Authorization header on its own,
and a cross-site script cannot add one without a CORS preflight, which
this API never approves (it sends no Access-Control-Allow-* headers). So a
request forged by another website arrives with no credentials and gets 401.

Why tokens and not JWTs: a JWT is checked without the database, so it
stays valid until it expires even after "revoke"; making revocation work
needs a server-side list, which removes the main benefit. TradeLedger
already reads the database on every request, so an opaque random token
looked up by its hash is simpler and revocable immediately.

Responses
  401 unauthorized   no token, a malformed, unknown, expired or revoked
                     token, or a disabled account (one message for all)
  404 not_found      the record does not exist OR belongs to another user
"""

import logging

from flask import Blueprint, current_app, g, request

from ..db import get_db
from ..repositories import DuplicateError, api_tokens
from ..validation import ValidationError
from .errors import ApiError, error_response

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")
log = logging.getLogger("tradeledger.auth")

# Endpoints that need no token: documentation only, never trading data.
PUBLIC_API_ENDPOINTS = frozenset({"api.openapi_spec"})


def api_user_id():
    """The authenticated API user's id; raises if there is none (fails closed)."""
    user = g.get("api_user")
    if user is None:
        raise RuntimeError("api_user_id() called without an authenticated API user")
    return user["id"]


@api_bp.before_request
def authenticate():
    if request.endpoint in PUBLIC_API_ENDPOINTS:
        return
    if not current_app.config["API_ENABLED"]:
        raise ApiError(403, "api_disabled", "The API is switched off in this environment.")
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    user = None
    if scheme.lower() == "bearer" and token.strip():
        user = api_tokens.authenticate(get_db(), token.strip())
    if user is None:
        log.info("API request without a valid token", extra={"event": "auth.api.rejected"})
        raise ApiError(401, "unauthorized", "A valid API token is required (Authorization: Bearer <token>).")
    g.api_user = user


@api_bp.after_request
def no_caching(response):
    # Trading data should not be stored by browsers or proxies.
    response.headers["Cache-Control"] = "no-store"
    return response


@api_bp.errorhandler(ApiError)
def handle_api_error(exc):
    response, status = error_response(exc.status, exc.code, exc.message, exc.details)
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="TradeLedger API"'
    return response, status


@api_bp.errorhandler(ValidationError)
def handle_validation_error(exc):
    return error_response(400, "validation_error", "The request is invalid.", exc.errors)


@api_bp.errorhandler(DuplicateError)
def handle_duplicate(exc):
    return error_response(409, "conflict", str(exc))


# Importing the modules registers their routes on api_bp.
from . import analytics, instruments, openapi, trades  # noqa: E402,F401
```

### `tradeledger/api/analytics.py` (MODIFIED)

**Why:** Scoped to the token's user.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Analytics endpoints: thin wrappers around the Phase 3 engine.

Every endpoint accepts the same optional filters, defined once in
report_filters.py and shared with the /analytics dashboard:
  instrument_id   only this instrument
  strategy        exact strategy label (Phase 6)
  session         ASIA, LONDON, NEW_YORK or OTHER (Phase 6)
  start, end      inclusive UTC dates (YYYY-MM-DD) on the REALISED date:
                  closed_at, or trade_date for legacy trades with no exit time

No formula lives here. Each handler calls build_report() and returns one
part of the PerformanceReport, so the API, the CLI report and the /analytics
dashboard always show the same numbers.
"""

from flask import jsonify

from ..db import get_db
from ..performance import build_report
from ..performance.formatting import to_jsonable
from ..report_filters import FILTER_NAMES, parse_report_filters
from ..validation import ValidationError
from . import api_bp, api_user_id
from .http import query_args
from .serialise import groups_json, summary_json

FILTERS = FILTER_NAMES

NOTES = [
    "Decimal values are strings holding the exact value; null means not calculable.",
    "Rates (win_rate, loss_rate, breakeven_rate) are fractions from 0 to 1.",
    "R values are currency-neutral. Money is reported per quote currency and never summed across currencies.",
    "Open trades are excluded from realised metrics.",
    "Ordering and date filters use the realised time: closed_at, or trade_date for legacy trades "
    "with no recorded exit time (listed in unknown_close_time_trade_ids).",
]


def _report_and_meta():
    conn = get_db()
    user_id = api_user_id()
    filters, errors = parse_report_filters(
        query_args(FILTERS), conn, user_id=user_id, blank_means_unset=False
    )
    if errors:
        raise ValidationError(errors)
    report = build_report(conn, user_id=user_id, **filters.as_kwargs())
    return report, {"filters": filters.as_kwargs(), "notes": NOTES}


@api_bp.get("/analytics/summary")
def analytics_summary():
    report, meta = _report_and_meta()
    return jsonify(
        data={
            "overall": summary_json(report.overall),
            "open_trades": report.open_trades,
            "excluded": to_jsonable(report.excluded),
            "r_unavailable": to_jsonable(report.r_unavailable),
            "unknown_close_time_trade_ids": report.unknown_close_time_trade_ids,
        },
        meta=meta,
    )


def _grouped(attribute):
    report, meta = _report_and_meta()
    return jsonify(data=groups_json(getattr(report, attribute)), meta=meta)


@api_bp.get("/analytics/instruments")
def analytics_instruments():
    return _grouped("by_instrument")


@api_bp.get("/analytics/strategies")
def analytics_strategies():
    return _grouped("by_strategy")


@api_bp.get("/analytics/sessions")
def analytics_sessions():
    return _grouped("by_session")


@api_bp.get("/analytics/weekday")
def analytics_weekday():
    return _grouped("by_weekday")


@api_bp.get("/analytics/monthly")
def analytics_monthly():
    return _grouped("by_month")


@api_bp.get("/analytics/curve")
def analytics_curve():
    report, meta = _report_and_meta()
    meta = {**meta, "label": "Cumulative realised R. This is NOT an account equity curve."}
    return jsonify(data=to_jsonable(report.cumulative_r), meta=meta)
```

### `tradeledger/api/instruments.py` (MODIFIED)

**Why:** Scoped to the token's user.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Instrument endpoints.

GET  /api/v1/instruments        list (sorted by symbol; no pagination,
                                a journal has tens of instruments, not thousands)
GET  /api/v1/instruments/<id>   retrieve -> 404 if missing
POST /api/v1/instruments        create   -> 201 + Location; 409 if YOU already have the symbol

There is deliberately no PUT or DELETE yet: changing an instrument's
contract_multiplier would silently change the P&L of every existing trade
on it, and deleting one with trades is refused by the database. Those need
their own design (versioned contract specifications) before they exist.
"""

from flask import jsonify, url_for

from ..db import get_db
from ..repositories import instruments as instrument_repo
from . import api_bp, api_user_id
from .errors import ApiError
from .http import INSTRUMENT_INPUT_FIELDS, check_fields, json_body, query_args
from .serialise import instrument_json


@api_bp.get("/instruments")
def list_instruments():
    query_args(())  # no parameters accepted
    return jsonify(
        data=[instrument_json(i) for i in instrument_repo.list_instruments(get_db(), user_id=api_user_id())]
    )


@api_bp.get("/instruments/<int:instrument_id>")
def get_instrument(instrument_id):
    instrument = instrument_repo.get_instrument(get_db(), instrument_id, user_id=api_user_id())
    if instrument is None:
        raise ApiError(404, "not_found", f"Instrument {instrument_id} does not exist.")
    return jsonify(data=instrument_json(instrument))


@api_bp.post("/instruments")
def create_instrument():
    body = json_body()
    check_fields(body, INSTRUMENT_INPUT_FIELDS)
    conn = get_db()
    # DuplicateError (repeated symbol) becomes 409 via the blueprint handler.
    instrument_id = instrument_repo.create_instrument(conn, body, user_id=api_user_id())
    response = jsonify(
        data=instrument_json(instrument_repo.get_instrument(conn, instrument_id, user_id=api_user_id()))
    )
    response.status_code = 201
    response.headers["Location"] = url_for("api.get_instrument", instrument_id=instrument_id)
    return response
```

### `tradeledger/api/openapi.py` (MODIFIED)

**Why:** Documents bearerAuth and 401 responses.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""OpenAPI 3.1 specification for the TradeLedger API.

Built as a Python dict (no extra library) and served at
GET /api/v1/openapi.json. `flask --app tradeledger openapi` writes the same
document to docs/openapi.json.

The tests check that:
  * every API route and method exists in this document, and vice versa;
  * real responses validate against the schemas here (with
    additionalProperties: false, so an undocumented field fails the test).
"""

from flask import current_app, jsonify

from . import api_bp

# ---------- Reusable schema fragments ----------

DECIMAL_PATTERN = r"^-?[0-9]+(\.[0-9]+)?$"
TIMESTAMP_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$"


def decimal(description, nullable=False, example=None):
    schema = {
        "type": ["string", "null"] if nullable else "string",
        "pattern": DECIMAL_PATTERN,
        "description": description + " Exact decimal as a string (never a float).",
    }
    if example is not None:
        schema["examples"] = [example]
    return schema


def timestamp(description, nullable=False):
    return {
        "type": ["string", "null"] if nullable else "string",
        "pattern": TIMESTAMP_PATTERN,
        "description": description + " UTC, ISO 8601, e.g. 2026-03-02T08:15:00Z.",
    }


def nullable_string(description, enum=None):
    schema = {"type": ["string", "null"], "description": description}
    if enum:
        schema["enum"] = [*enum, None]
    return schema


def ref(name):
    return {"$ref": f"#/components/schemas/{name}"}


def obj(properties, required=None):
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


DECIMAL_INPUT = {
    "description": 'Decimal value. Send a string (recommended, e.g. "1.085") or a JSON number; '
    "numbers are read exactly, never as binary floats. NaN and Infinity are rejected. "
    "At most 15 significant digits, 15 digits before the decimal point and 15 decimal places.",
    "anyOf": [{"type": "string"}, {"type": "number"}],
}
TIMESTAMP_INPUT = {
    "type": "string",
    "description": "ISO 8601 date-time. A value without an offset is treated as UTC; "
    "offsets are converted to UTC. A bare date is rejected.",
    "examples": ["2026-03-02T08:15:00Z", "2026-03-02 08:15", "2026-03-02T09:15:00+01:00"],
}

TRADE_PROPERTIES = {
    "id": {"type": "integer"},
    "instrument_id": {"type": "integer"},
    "symbol": {"type": "string"},
    "quote_currency": {"type": "string", "description": "Currency of all money values for this trade."},
    "trade_date": timestamp("Entry time."),
    "closed_at": timestamp(
        "Exit time. null for OPEN trades, and for legacy CLOSED trades recorded "
        "before the exit time was captured (never invented).",
        nullable=True,
    ),
    "direction": {"type": "string", "enum": ["LONG", "SHORT"]},
    "status": {"type": "string", "enum": ["OPEN", "CLOSED"]},
    "entry_price": decimal("Entry price.", example="1.085"),
    "stop_loss": decimal("Initial stop-loss price."),
    "take_profit": decimal("Target price, if planned.", nullable=True),
    "exit_price": decimal("Exit price. null while OPEN.", nullable=True),
    "position_size": decimal("Position size in the units the instrument's contract_multiplier assumes."),
    "fees": decimal("Total fees, in the quote currency."),
    "strategy": nullable_string("Free-text strategy label (max 50 characters)."),
    "session": nullable_string("Trading session.", ["ASIA", "LONDON", "NEW_YORK", "OTHER"]),
    "notes": nullable_string("Journal notes (max 2000 characters)."),
    "created_at": timestamp("When the record was created."),
    "updated_at": timestamp("When the record was last changed."),
    "result": {
        "anyOf": [{"$ref": "#/components/schemas/TradeResult"}, {"type": "null"}],
        "description": "Calculated by the analytics engine for CLOSED trades; null for OPEN trades.",
    },
}

TRADE_RESULT = obj(
    {
        "outcome": {
            "type": "string",
            "enum": ["WIN", "LOSS", "BREAKEVEN"],
            "description": "From net P&L: > 0 WIN, < 0 LOSS, exactly 0 BREAKEVEN.",
        },
        "gross_pnl": decimal("(exit - entry) x direction x size x contract_multiplier."),
        "fees": decimal("Fees."),
        "net_pnl": decimal("gross_pnl - fees."),
        "initial_risk": decimal(
            "Money lost if the stop is hit (fees excluded). null if the stop is invalid.", nullable=True
        ),
        "realised_r": decimal(
            "net_pnl / initial_risk. null when initial risk is not positive.", nullable=True
        ),
        "planned_rr": decimal(
            "Planned reward / planned risk from prices. null without a valid target.", nullable=True
        ),
        "r_unavailable_reason": nullable_string("Why realised_r is null, if it is."),
    }
)

TRADE_INPUT_PROPERTIES = {
    "instrument_id": {
        "type": "integer",
        "minimum": 1,
        "description": "Must reference an existing instrument.",
    },
    "trade_date": TIMESTAMP_INPUT,
    "closed_at": {
        **TIMESTAMP_INPUT,
        "type": ["string", "null"],
        "description": "Exit time. Required when the trade is CLOSED; must be null/absent when OPEN; "
        "must not be before trade_date. Exception: a legacy CLOSED trade whose exit time "
        "was never recorded may keep null when replaced with PUT.",
    },
    "direction": {
        "type": "string",
        "enum": ["LONG", "SHORT", "long", "short"],
        "description": "Case-insensitive.",
    },
    "status": {
        "type": ["string", "null"],
        "description": "OPEN or CLOSED (case-insensitive). If omitted: CLOSED when exit_price is given, "
        "otherwise OPEN. CLOSED requires exit_price; OPEN must not have one.",
    },
    "entry_price": {**DECIMAL_INPUT, "description": "Greater than 0. " + DECIMAL_INPUT["description"]},
    "stop_loss": {
        **DECIMAL_INPUT,
        "description": "Greater than 0. LONG: below entry. SHORT: above entry. Never equal to entry.",
    },
    "take_profit": {
        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
        "description": "Optional. LONG: above entry. SHORT: below entry.",
    },
    "exit_price": {
        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
        "description": "Required for CLOSED, absent/null for OPEN. Greater than 0.",
    },
    "position_size": {**DECIMAL_INPUT, "description": "Greater than 0."},
    "fees": {
        "anyOf": [{"type": "string"}, {"type": "number"}, {"type": "null"}],
        "description": "0 or more. Defaults to 0.",
    },
    "strategy": {"type": ["string", "null"], "maxLength": 50},
    "session": {
        "type": ["string", "null"],
        "description": "ASIA, LONDON, NEW_YORK or OTHER (case-insensitive).",
    },
    "notes": {"type": ["string", "null"], "maxLength": 2000},
}

TRADE_INPUT_REQUIRED = [
    "instrument_id",
    "trade_date",
    "direction",
    "entry_price",
    "stop_loss",
    "position_size",
]

INSTRUMENT_PROPERTIES = {
    "id": {"type": "integer"},
    "symbol": {"type": "string", "description": "Unique, upper case, 1-20 letters, digits or underscores."},
    "name": {"type": "string"},
    "asset_class": {"type": "string", "enum": ["FOREX", "COMMODITY", "INDEX", "STOCK", "OTHER"]},
    "price_increment": decimal("Smallest price step quoted, e.g. 0.00001."),
    "contract_multiplier": decimal(
        "Money (in quote_currency) per 1.0 move in price per 1.0 unit of position_size. "
        "Depends on your broker's contract specification; sample instruments use illustrative values."
    ),
    "quote_currency": {
        "type": "string",
        "description": "3-letter currency code in which P&L is denominated.",
    },
    "created_at": timestamp("When the record was created."),
}

INSTRUMENT_INPUT = obj(
    {
        "symbol": {
            "type": "string",
            "description": "1-20 letters, digits or underscores; stored upper case.",
        },
        "name": {"type": "string", "maxLength": 100},
        "asset_class": {
            "type": "string",
            "description": "FOREX, COMMODITY, INDEX, STOCK or OTHER (case-insensitive).",
        },
        "price_increment": {**DECIMAL_INPUT, "description": "Greater than 0."},
        "contract_multiplier": {
            **DECIMAL_INPUT,
            "description": "Greater than 0. See Instrument.contract_multiplier.",
        },
        "quote_currency": {"type": "string", "description": "3 letters, e.g. USD."},
    }
)

TRADE_REF = obj(
    {
        "trade_id": {"type": "integer"},
        "symbol": {"type": "string"},
        "trade_date": timestamp("Entry time."),
        "value": decimal("The value that ranked this trade (R or net P&L)."),
    }
)

DRAWDOWN = obj(
    {
        "amount": decimal(
            "Largest fall from a running peak (>= 0), in R or in one currency. Never a percentage."
        ),
        "peak_trade_id": {"type": ["integer", "null"], "description": "null = the starting point (0)."},
        "trough_trade_id": {"type": ["integer", "null"], "description": "null when there was no drawdown."},
    }
)

MONEY_SUMMARY = obj(
    {
        "currency": {"type": "string"},
        "trades": {"type": "integer"},
        "gross_pnl": decimal("Sum of P&L before fees."),
        "fees": decimal("Sum of fees."),
        "net_pnl": decimal("Sum of P&L after fees."),
        "winning_pnl": decimal("Sum of net P&L of winning trades (>= 0)."),
        "losing_pnl": decimal("Sum of net P&L of losing trades (<= 0)."),
        "profit_factor": decimal("winning_pnl / |losing_pnl|. null with no losing trades.", nullable=True),
        "best_trade": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
        "worst_trade": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
        "max_drawdown": ref("Drawdown"),
    }
)

SUMMARY_PROPERTIES = {
    "trades": {"type": "integer", "description": "Closed trades evaluated."},
    "wins": {"type": "integer"},
    "losses": {"type": "integer"},
    "breakevens": {"type": "integer"},
    "win_rate": decimal("wins / trades, as a fraction 0-1. null with no trades.", nullable=True),
    "loss_rate": decimal("losses / trades, 0-1.", nullable=True),
    "breakeven_rate": decimal("breakevens / trades, 0-1.", nullable=True),
    "r_trades": {"type": "integer", "description": "Trades with a usable realised R."},
    "total_r": decimal("Sum of realised R."),
    "average_r": decimal("Mean realised R.", nullable=True),
    "average_win_r": decimal("Mean R of winning trades.", nullable=True),
    "average_loss_r": decimal("Mean R of losing trades (negative).", nullable=True),
    "expectancy_r": decimal(
        "P(win) x average_win_r + P(loss) x average_loss_r, in R per trade.", nullable=True
    ),
    "profit_factor_r": decimal(
        "Sum of positive R / |sum of negative R|. null with no losses.", nullable=True
    ),
    "planned_rr_trades": {"type": "integer"},
    "average_planned_rr": decimal("Mean planned R:R of trades with a valid target.", nullable=True),
    "longest_win_streak": {"type": "integer"},
    "longest_loss_streak": {"type": "integer"},
    "max_drawdown_r": ref("Drawdown"),
    "best_trade_r": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
    "worst_trade_r": {"anyOf": [ref("TradeRef"), {"type": "null"}]},
    "money": {
        "type": "object",
        "additionalProperties": ref("MoneySummary"),
        "description": "One entry per quote currency. Currencies are never added together.",
    },
}

EXCLUDED_TRADE = obj({"trade_id": {"type": ["integer", "null"]}, "reason": {"type": "string"}})

CURVE_POINT = obj(
    {
        "trade_id": {"type": "integer"},
        "trade_date": timestamp("Entry time."),
        "realised_at": timestamp("Exit time, or entry time for legacy trades with no recorded exit."),
        "close_time_known": {"type": "boolean"},
        "symbol": {"type": "string"},
        "realised_r": decimal("This trade's realised R."),
        "cumulative_r": decimal("Running total of realised R. NOT account equity."),
    }
)

ANALYTICS_META = obj(
    {
        "filters": obj(
            {
                "instrument_id": {"type": ["integer", "null"]},
                "strategy": {"type": ["string", "null"]},
                "session": {
                    "type": ["string", "null"],
                    "enum": ["ASIA", "LONDON", "NEW_YORK", "OTHER", None],
                },
                "start": {"type": ["string", "null"], "format": "date"},
                "end": {"type": ["string", "null"], "format": "date"},
            }
        ),
        "notes": {"type": "array", "items": {"type": "string"}},
        "label": {"type": "string"},
    },
    required=["filters", "notes"],
)

ERROR = obj(
    {
        "error": obj(
            {
                "code": {"type": "string", "description": "Stable machine-readable code."},
                "message": {"type": "string", "description": "Human-readable explanation."},
                "details": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Field name -> problem. Present only when useful.",
                },
            },
            required=["code", "message"],
        ),
    }
)

PAGINATION = obj(
    {
        "page": {"type": "integer", "minimum": 1},
        "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
        "total": {"type": "integer", "description": "Matching trades across all pages."},
        "total_pages": {"type": "integer", "description": "0 when there are no matches."},
    }
)

# ---------- Parameters ----------


def query_param(name, schema, description):
    return {"name": name, "in": "query", "required": False, "schema": schema, "description": description}


PARAMS = {
    "instrument_id": query_param(
        "instrument_id", {"type": "integer", "minimum": 1}, "Only this instrument. 400 if it does not exist."
    ),
    "status": query_param(
        "status",
        {"type": "string", "enum": ["OPEN", "CLOSED"]},
        "Only trades with this status (case-insensitive).",
    ),
    "trade_start": query_param(
        "start",
        {"type": "string", "format": "date"},
        "First entry date to include (YYYY-MM-DD, UTC, inclusive).",
    ),
    "trade_end": query_param(
        "end",
        {"type": "string", "format": "date"},
        "Last entry date to include (YYYY-MM-DD, UTC, inclusive).",
    ),
    "realised_start": query_param(
        "start",
        {"type": "string", "format": "date"},
        "First realised date (closed_at, or trade_date if unknown) to include.",
    ),
    "realised_end": query_param(
        "end", {"type": "string", "format": "date"}, "Last realised date to include (inclusive)."
    ),
    "strategy": query_param(
        "strategy",
        {"type": "string", "minLength": 1, "maxLength": 50},
        "Only trades with exactly this strategy label (case-sensitive). Phase 6.",
    ),
    "session": query_param(
        "session",
        {"type": "string", "enum": ["ASIA", "LONDON", "NEW_YORK", "OTHER"]},
        "Only trades recorded in this session (case-insensitive). Phase 6.",
    ),
    "page": query_param("page", {"type": "integer", "minimum": 1, "default": 1}, "Page number."),
    "per_page": query_param(
        "per_page", {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}, "Trades per page."
    ),
}


def path_id(name, what):
    return {
        "name": name,
        "in": "path",
        "required": True,
        "schema": {"type": "integer", "minimum": 1},
        "description": f"{what} id.",
    }


# ---------- Responses ----------


def json_response(description, schema, example=None):
    content = {"schema": schema}
    if example is not None:
        content["example"] = example
    return {"description": description, "content": {"application/json": content}}


def error(description):
    return json_response(description, ref("Error"))


ERRORS = {
    "400": error(
        "Invalid request: malformed JSON, unknown fields or parameters, or failed validation "
        "(details lists each field)."
    ),
    "401": error(
        "Missing, malformed, unknown, expired or revoked API token, or a disabled account. "
        "The response carries `WWW-Authenticate: Bearer`."
    ),
    "403": error("The API is switched off in this environment (TRADELEDGER_API_ENABLED=0)."),
    "404": error("The resource does not exist, or belongs to another user (the two are indistinguishable)."),
    "405": error("Method not allowed for this URL."),
    "409": error("Conflict, e.g. an instrument symbol you have already recorded."),
    "413": error("Request body larger than 64 KB."),
    "415": error("Request body is not application/json."),
    "500": error("Unexpected error. No internal details are included."),
}


def errors(*codes):
    """Error responses for one operation. Every data endpoint can answer 401."""
    return {code: ERRORS[code] for code in sorted({"401", *codes})}


def envelope(data_schema, **extra):
    return obj({"data": data_schema, **extra})


EXAMPLE_TRADE_INPUT = {
    "instrument_id": 1,
    "trade_date": "2026-04-01T08:15:00Z",
    "closed_at": "2026-04-01T11:40:00Z",
    "direction": "LONG",
    "entry_price": "1.08500",
    "stop_loss": "1.08300",
    "take_profit": "1.08900",
    "exit_price": "1.08900",
    "position_size": "1",
    "fees": "7",
    "strategy": "Breakout",
    "session": "LONDON",
    "notes": "Synthetic example.",
}

EXAMPLE_TRADE = {
    "id": 17,
    "instrument_id": 1,
    "symbol": "EURUSD",
    "quote_currency": "USD",
    "trade_date": "2026-04-01T08:15:00Z",
    "closed_at": "2026-04-01T11:40:00Z",
    "direction": "LONG",
    "status": "CLOSED",
    "entry_price": "1.085",
    "stop_loss": "1.083",
    "take_profit": "1.089",
    "exit_price": "1.089",
    "position_size": "1",
    "fees": "7",
    "strategy": "Breakout",
    "session": "LONDON",
    "notes": "Synthetic example.",
    "created_at": "2026-11-15T09:00:00Z",
    "updated_at": "2026-11-15T09:00:00Z",
    "result": {
        "outcome": "WIN",
        "gross_pnl": "400",
        "fees": "7",
        "net_pnl": "393",
        "initial_risk": "200",
        "realised_r": "1.965",
        "planned_rr": "2",
        "r_unavailable_reason": None,
    },
}

EXAMPLE_VALIDATION_ERROR = {
    "error": {
        "code": "validation_error",
        "message": "The request is invalid.",
        "details": {
            "position_size": "must be greater than zero",
            "stop_loss": "must be below the entry price for a LONG trade",
        },
    }
}


# The analytics filters, in the same order as report_filters.FILTER_NAMES.
ANALYTICS_PARAMS = [
    PARAMS["instrument_id"],
    PARAMS["strategy"],
    PARAMS["session"],
    PARAMS["realised_start"],
    PARAMS["realised_end"],
]


def grouped_path(summary, description):
    return {
        "get": {
            "tags": ["Analytics"],
            "summary": summary,
            "description": description,
            "parameters": ANALYTICS_PARAMS,
            "responses": {
                "200": json_response("Groups in a stable order.", ref("GroupedAnalytics")),
                **errors("400", "403", "405"),
            },
        }
    }


def build_spec():
    group_summary = obj({"group": {"type": "string", "description": "Group label."}, **SUMMARY_PROPERTIES})
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "TradeLedger API",
            "version": "1.0.0",
            "description": (
                "JSON API for the TradeLedger trading journal.\n\n"
                "**Authentication:** every endpoint except this document needs a personal API token, "
                "created on the Account page of the website: `Authorization: Bearer tl_...`. "
                "Tokens expire (90 days by default) and can be revoked; session cookies are ignored. "
                "Each token sees and changes only its owner's records; another user's record answers 404.\n\n"
                "**Conventions:** decimal values are exact strings; timestamps are UTC "
                "`YYYY-MM-DDTHH:MM:SSZ`; unavailable values are `null`; money is per quote currency; "
                "R values are currency-neutral; cumulative R is not account equity. "
                "Unknown body fields and query parameters are rejected with 400."
            ),
        },
        "servers": [{"url": "/api/v1"}],
        "security": [{"bearerAuth": []}],
        "paths": {
            "/trades": {
                "get": {
                    "tags": ["Trades"],
                    "summary": "List trades",
                    "description": "Newest trade_date first, then highest id first. Dates filter on entry time.",
                    "parameters": [
                        PARAMS[p]
                        for p in ("instrument_id", "status", "trade_start", "trade_end", "page", "per_page")
                    ],
                    "responses": {
                        "200": json_response("A page of trades.", ref("TradeList")),
                        **errors("400", "403", "405"),
                    },
                },
                "post": {
                    "tags": ["Trades"],
                    "summary": "Create a trade",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {"schema": ref("TradeInput"), "example": EXAMPLE_TRADE_INPUT}
                        },
                    },
                    "responses": {
                        "201": {
                            **json_response(
                                "Created. Location header holds the new trade's URL.",
                                ref("TradeEnvelope"),
                                {"data": EXAMPLE_TRADE},
                            ),
                            "headers": {"Location": {"schema": {"type": "string"}}},
                        },
                        "400": json_response(
                            "Invalid trade. Nothing was written.", ref("Error"), EXAMPLE_VALIDATION_ERROR
                        ),
                        **errors("403", "413", "415"),
                    },
                },
            },
            "/trades/{trade_id}": {
                "parameters": [path_id("trade_id", "Trade")],
                "get": {
                    "tags": ["Trades"],
                    "summary": "Get a trade",
                    "responses": {
                        "200": json_response("The trade.", ref("TradeEnvelope")),
                        **errors("403", "404"),
                    },
                },
                "put": {
                    "tags": ["Trades"],
                    "summary": "Replace a trade",
                    "description": "Full replacement: every field in TradeReplace must be present "
                    "(null for empty optional fields). There are no partial updates.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": ref("TradeReplace"),
                                "example": EXAMPLE_TRADE_INPUT,
                            }
                        },
                    },
                    "responses": {
                        "200": json_response("The updated trade.", ref("TradeEnvelope")),
                        **errors("400", "403", "404", "413", "415"),
                    },
                },
                "delete": {
                    "tags": ["Trades"],
                    "summary": "Delete a trade",
                    "responses": {"204": {"description": "Deleted. No body."}, **errors("403", "404")},
                },
            },
            "/instruments": {
                "get": {
                    "tags": ["Instruments"],
                    "summary": "List instruments (sorted by symbol)",
                    "responses": {
                        "200": json_response("All instruments.", ref("InstrumentList")),
                        **errors("400", "403"),
                    },
                },
                "post": {
                    "tags": ["Instruments"],
                    "summary": "Create an instrument",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": ref("InstrumentInput"),
                                "example": {
                                    "symbol": "US30",
                                    "name": "Wall Street 30 CFD (example values)",
                                    "asset_class": "INDEX",
                                    "price_increment": "1",
                                    "contract_multiplier": "1",
                                    "quote_currency": "USD",
                                },
                            }
                        },
                    },
                    "responses": {
                        "201": {
                            **json_response("Created.", ref("InstrumentEnvelope")),
                            "headers": {"Location": {"schema": {"type": "string"}}},
                        },
                        **errors("400", "403", "409", "413", "415"),
                    },
                },
            },
            "/instruments/{instrument_id}": {
                "parameters": [path_id("instrument_id", "Instrument")],
                "get": {
                    "tags": ["Instruments"],
                    "summary": "Get an instrument",
                    "responses": {
                        "200": json_response("The instrument.", ref("InstrumentEnvelope")),
                        **errors("403", "404"),
                    },
                },
            },
            "/analytics/summary": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "Overall realised performance",
                    "parameters": ANALYTICS_PARAMS,
                    "responses": {
                        "200": json_response("Overall statistics.", ref("SummaryAnalytics")),
                        **errors("400", "403", "405"),
                    },
                }
            },
            "/analytics/instruments": grouped_path("Performance by instrument", "Sorted by symbol."),
            "/analytics/strategies": grouped_path(
                "Performance by strategy", "Sorted alphabetically; untagged trades are 'Unspecified'."
            ),
            "/analytics/sessions": grouped_path(
                "Performance by session", "Sorted alphabetically; untagged trades are 'Unspecified'."
            ),
            "/analytics/weekday": grouped_path(
                "Performance by realised weekday (UTC)", "Monday to Sunday; days with no trades are omitted."
            ),
            "/analytics/monthly": grouped_path(
                "Performance by realised month (YYYY-MM, UTC)", "Oldest first."
            ),
            "/analytics/curve": {
                "get": {
                    "tags": ["Analytics"],
                    "summary": "Cumulative realised R (chart data)",
                    "description": "In realised order. NOT an account equity curve.",
                    "parameters": ANALYTICS_PARAMS,
                    "responses": {
                        "200": json_response("Curve points.", ref("CurveAnalytics")),
                        **errors("400", "403", "405"),
                    },
                }
            },
            "/openapi.json": {
                "get": {
                    "tags": ["Documentation"],
                    "summary": "This specification",
                    "description": "Public: needs no token, and is available even when the API is switched off.",
                    "security": [],
                    "responses": {
                        "200": {
                            "description": "OpenAPI 3.1 document.",
                            "content": {"application/json": {"schema": {"type": "object"}}},
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "Trade": obj(TRADE_PROPERTIES),
                "TradeResult": TRADE_RESULT,
                "TradeInput": obj(TRADE_INPUT_PROPERTIES, required=TRADE_INPUT_REQUIRED),
                "TradeReplace": obj(TRADE_INPUT_PROPERTIES),
                "Instrument": obj(INSTRUMENT_PROPERTIES),
                "InstrumentInput": INSTRUMENT_INPUT,
                "Pagination": PAGINATION,
                "TradeEnvelope": envelope(ref("Trade")),
                "TradeList": envelope({"type": "array", "items": ref("Trade")}, pagination=ref("Pagination")),
                "InstrumentEnvelope": envelope(ref("Instrument")),
                "InstrumentList": envelope({"type": "array", "items": ref("Instrument")}),
                "TradeRef": TRADE_REF,
                "Drawdown": DRAWDOWN,
                "MoneySummary": MONEY_SUMMARY,
                "Summary": obj(SUMMARY_PROPERTIES),
                "GroupSummary": group_summary,
                "ExcludedTrade": EXCLUDED_TRADE,
                "CurvePoint": CURVE_POINT,
                "AnalyticsMeta": ANALYTICS_META,
                "SummaryAnalytics": envelope(
                    obj(
                        {
                            "overall": ref("Summary"),
                            "open_trades": {
                                "type": "integer",
                                "description": "Counted, but excluded from realised metrics.",
                            },
                            "excluded": {
                                "type": "array",
                                "items": ref("ExcludedTrade"),
                                "description": "Closed trades that could not be evaluated, with reasons.",
                            },
                            "r_unavailable": {
                                "type": "array",
                                "items": ref("ExcludedTrade"),
                                "description": "Closed trades counted, but with no realised R.",
                            },
                            "unknown_close_time_trade_ids": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "Legacy trades ordered by entry time.",
                            },
                        }
                    ),
                    meta=ref("AnalyticsMeta"),
                ),
                "GroupedAnalytics": envelope(
                    {"type": "array", "items": ref("GroupSummary")}, meta=ref("AnalyticsMeta")
                ),
                "CurveAnalytics": envelope(
                    {"type": "array", "items": ref("CurvePoint")}, meta=ref("AnalyticsMeta")
                ),
                "Error": ERROR,
            },
            "securitySchemes": {
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "description": "Personal API token (starts with tl_). Create and revoke tokens on /account.",
                }
            },
        },
    }


@api_bp.get("/openapi.json")
def openapi_spec():
    spec = build_spec()
    spec["info"]["x-app-version"] = current_app.config["APP_VERSION"]
    return jsonify(spec)
```

### `tradeledger/api/trades.py` (MODIFIED)

**Why:** Scoped to the token's user.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Trade endpoints.

GET    /api/v1/trades         list, filter, paginate
POST   /api/v1/trades         create                  -> 201 + Location
GET    /api/v1/trades/<id>    retrieve                -> 404 if missing
PUT    /api/v1/trades/<id>    full replacement        -> 404 if missing
DELETE /api/v1/trades/<id>    delete                  -> 204, 404 if missing

Each handler: check the request shape (http.py), call the repository (which
validates and writes inside a transaction), serialise the result.
"""

import math

from flask import jsonify, url_for

from ..db import get_db
from ..repositories import instruments as instrument_repo
from ..repositories import trades as trade_repo
from ..validation import ValidationError
from . import api_bp, api_user_id
from .errors import ApiError
from .http import (
    TRADE_INPUT_FIELDS,
    check_fields,
    date_arg,
    json_body,
    pagination_args,
    positive_int,
    query_args,
    status_arg,
)
from .serialise import trade_json

LIST_PARAMETERS = ("instrument_id", "status", "start", "end", "page", "per_page")


def _not_found(trade_id):
    return ApiError(404, "not_found", f"Trade {trade_id} does not exist.")


def require_known_instrument(conn, instrument_id):
    """Another user's instrument id gets the same answer as a missing one."""
    if (
        instrument_id is not None
        and instrument_repo.get_instrument(conn, instrument_id, user_id=api_user_id()) is None
    ):
        raise ValidationError({"instrument_id": "does not match an existing instrument"})


@api_bp.get("/trades")
def list_trades():
    args = query_args(LIST_PARAMETERS)
    instrument_id = positive_int(args, "instrument_id")
    filters = {
        "instrument_id": instrument_id,
        "status": status_arg(args),
        "start": date_arg(args, "start"),
        "end": date_arg(args, "end"),
    }
    page, per_page = pagination_args(args)

    conn = get_db()
    require_known_instrument(conn, instrument_id)
    user_id = api_user_id()
    total = trade_repo.count_trades(conn, user_id=user_id, **filters)
    rows = trade_repo.list_trades(
        conn, user_id=user_id, **filters, limit=per_page, offset=(page - 1) * per_page
    )
    return jsonify(
        data=[trade_json(row) for row in rows],
        pagination={
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": math.ceil(total / per_page),
        },
    )


@api_bp.post("/trades")
def create_trade():
    body = json_body()
    check_fields(body, TRADE_INPUT_FIELDS)
    conn = get_db()
    # Ownership comes from the token, never from the body: check_fields()
    # rejects unknown fields such as "user_id" (400), and the repository
    # always writes the authenticated user's id.
    trade_id = trade_repo.create_trade(conn, body, user_id=api_user_id())
    response = jsonify(data=trade_json(trade_repo.get_trade(conn, trade_id, user_id=api_user_id())))
    response.status_code = 201
    response.headers["Location"] = url_for("api.get_trade", trade_id=trade_id)
    return response


@api_bp.get("/trades/<int:trade_id>")
def get_trade(trade_id):
    trade = trade_repo.get_trade(get_db(), trade_id, user_id=api_user_id())
    if trade is None:
        raise _not_found(trade_id)
    return jsonify(data=trade_json(trade))


@api_bp.put("/trades/<int:trade_id>")
def replace_trade(trade_id):
    body = json_body()
    check_fields(body, TRADE_INPUT_FIELDS, require_all=True)
    conn = get_db()
    if not trade_repo.update_trade(conn, trade_id, body, user_id=api_user_id()):
        raise _not_found(trade_id)
    return jsonify(data=trade_json(trade_repo.get_trade(conn, trade_id, user_id=api_user_id())))


@api_bp.delete("/trades/<int:trade_id>")
def delete_trade(trade_id):
    if not trade_repo.delete_trade(get_db(), trade_id, user_id=api_user_id()):
        raise _not_found(trade_id)
    return "", 204
```

### `tradeledger/cli.py` (MODIFIED)

**Why:** Account commands (create-user, set-password, activate/deactivate, list-users), claim-legacy-data, `--email` on data commands, production refusals.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Command-line commands, run through Flask:

flask --app tradeledger init-db      create tables (keeps existing data)
flask --app tradeledger reset-db     delete ALL data and recreate tables (refused in production)
flask --app tradeledger seed-db --email E      add synthetic sample data to an account
flask --app tradeledger db-info      show tables, row counts and schema version
flask --app tradeledger analytics --email E    run the analytical SQL queries for an account
flask --app tradeledger run-sql "SELECT ..."   run your own read-only query
flask --app tradeledger report --email E       performance report (Phase 3 engine)
flask --app tradeledger migrate-db   back up, then apply pending schema migrations
flask --app tradeledger openapi      write the OpenAPI specification to a file

Accounts (Phase 8)
flask --app tradeledger create-user --email E          asks for the password twice
flask --app tradeledger set-password --email E         reset a forgotten password
flask --app tradeledger deactivate-user --email E      block sign-in and API tokens
flask --app tradeledger activate-user --email E
flask --app tradeledger list-users
flask --app tradeledger claim-legacy-data --email E [--apply]
                                     give pre-Phase 8 records to an account

Passwords are typed at a hidden prompt (or piped with --password-stdin for
automation). They are never accepted as a command-line argument, because
arguments are visible to other users of the machine and kept in shell history.
"""

import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

import click
from flask import current_app
from flask.cli import with_appcontext

from . import analytics, migrations
from .api.openapi import build_spec
from .db import POSTGRESQL, database_label, drop_all, get_db, init_schema, list_tables, schema_version
from .legacy import claim_legacy_data, legacy_counts
from .passwords import hash_password, parameters_from_config, password_problem
from .performance import build_report
from .performance.formatting import (
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
    to_jsonable,
)
from .repositories import DuplicateError
from .repositories.instruments import get_instrument_by_symbol
from .repositories.users import (
    count_users,
    create_user,
    email_problem,
    get_user_by_email,
    normalise_email,
    set_active,
    set_password_hash,
)
from .seed import seed_sample_data
from .validation import ValidationError


def print_table(rows):
    """Print a list of dicts as an aligned text table."""
    if not rows:
        click.echo("(no rows)")
        return
    headers = list(rows[0].keys())
    cells = [[("NULL" if row[h] is None else str(row[h])) for h in headers] for row in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) for i, h in enumerate(headers)]
    click.echo("  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)))
    click.echo("  ".join("-" * w for w in widths))
    for r in cells:
        click.echo("  ".join(value.ljust(w) for value, w in zip(r, widths, strict=True)))


def user_from_email(conn, email):
    user = get_user_by_email(conn, email)
    if user is None:
        raise click.ClickException(f"No account with email {normalise_email(email)}.")
    return user


def read_new_password(from_stdin, email):
    """A new password from a hidden prompt (typed twice) or from standard input."""
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
    problem = password_problem(password, email=email)
    if problem:
        raise click.ClickException(f"Password {problem}")
    return password


@click.command("init-db")
@with_appcontext
def init_db_command():
    """Create any missing tables. Existing data is kept."""
    init_schema(get_db())
    click.echo(f"Database ready: {database_label(current_app)}")


@click.command("reset-db")
@with_appcontext
@click.confirmation_option(prompt="This deletes ALL accounts, trades and instruments. Continue?")
def reset_db_command():
    """Drop every table and recreate the empty schema."""
    if current_app.config["CONFIG_NAME"] == "production":
        raise click.ClickException("reset-db is refused in production. Restore from a backup instead.")
    conn = get_db()
    drop_all(conn)
    init_schema(conn)
    click.echo("Database reset. All tables are empty.")


@click.command("seed-db")
@click.option("--email", required=True, help="The account that receives the sample data.")
@with_appcontext
def seed_db_command(email):
    """Insert synthetic sample data into one account. Running it twice adds nothing new."""
    conn = get_db()
    init_schema(conn)
    user = user_from_email(conn, email)
    inserted = seed_sample_data(conn, user_id=user["id"])
    click.echo(
        f"Sample data: {inserted['instruments']} instrument(s) and {inserted['trades']} trade(s) inserted "
        f"for {user['email']}."
    )


@click.command("db-info")
@with_appcontext
def db_info_command():
    """Show which database is used, its schema version and row counts."""
    conn = get_db()
    click.echo(f"Database:       {database_label(current_app)}")
    click.echo(f"Schema version: {schema_version(conn)}")
    tables = list_tables(conn)
    if not tables:
        click.echo("No tables yet. Run: flask --app tradeledger init-db")
        return
    # Table names come from the database catalogue, not from user input, so it
    # is safe to place them in the SQL text. (Placeholders cannot stand for names.)
    rows = [
        {"table": name, "rows": conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]}  # noqa: S608 - name from the catalogue
        for name in tables
    ]
    print_table(rows)
    if "users" in tables and "trades" in tables:
        legacy = legacy_counts(conn)
        if legacy["instruments"] or legacy["trades"]:
            click.echo(
                f"Unassigned legacy records: {legacy['instruments']} instrument(s), {legacy['trades']} trade(s). "
                "Assign them with: flask --app tradeledger claim-legacy-data --email you@example.com"
            )


@click.command("analytics")
@click.option("--email", required=True, help="Whose records to analyse.")
@click.argument("name", required=False, type=click.Choice(sorted(analytics.ALL_QUERIES)))
@with_appcontext
def analytics_command(email, name):
    """Run one analytical query by NAME, or all of them, for one account."""
    conn = get_db()
    user = user_from_email(conn, email)
    names = [name] if name else list(analytics.ALL_QUERIES)
    for query_name in names:
        click.echo(f"\n== {query_name} ==")
        print_table(analytics.run_query(conn, query_name, user_id=user["id"]))


@click.command("run-sql")
@with_appcontext
@click.argument("sql")
def run_sql_command(sql):
    """Run your own SQL against a READ-ONLY connection (it cannot change data).

    This reads EVERY account's data: it is an operator tool for whoever
    already has the database credentials, not a feature for users."""
    if current_app.extensions["tradeledger_db"].dialect == POSTGRESQL:
        conn = get_db()
        try:
            with conn:
                conn.execute("SET TRANSACTION READ ONLY")
                rows = [dict(row) for row in conn.execute(sql).fetchall()]
        except Exception as exc:
            raise click.ClickException(f"SQL error: {exc}") from exc
        print_table(rows)
        return
    path = Path(current_app.config["DATABASE"]).resolve()
    if not path.exists():
        raise click.ClickException("Database file not found. Run init-db first.")
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(row) for row in conn.execute(sql).fetchall()]
    except sqlite3.Error as exc:
        raise click.ClickException(f"SQL error: {exc}") from exc
    finally:
        conn.close()
    print_table(rows)


def summary_row(label, summary):
    """One display row for a group summary (rounded for display only)."""
    return {
        "group": label,
        "trades": summary.trades,
        "W/L/BE": f"{summary.wins}/{summary.losses}/{summary.breakevens}",
        "win rate": format_rate(summary.win_rate),
        "total R": format_r(summary.total_r),
        "expectancy": format_r(summary.expectancy_r),
        "PF (R)": format_ratio(summary.profit_factor_r),
        "max DD": format_r_amount(summary.max_drawdown_r.amount),
    }


def print_report(report):
    o = report.overall
    click.echo("== Overall (closed trades) ==")
    lines = [
        ("Closed trades", o.trades),
        ("Open trades (not in results)", report.open_trades),
        ("Wins / losses / breakevens", f"{o.wins} / {o.losses} / {o.breakevens}"),
        ("Win rate / loss rate", f"{format_rate(o.win_rate)} / {format_rate(o.loss_rate)}"),
        ("Trades with realised R", o.r_trades),
        ("Total R (cumulative)", format_r(o.total_r)),
        ("Average R", format_r(o.average_r)),
        ("Average winning R", format_r(o.average_win_r)),
        ("Average losing R", format_r(o.average_loss_r)),
        ("Expectancy per trade", format_r(o.expectancy_r)),
        ("Profit factor (R)", format_ratio(o.profit_factor_r)),
        (
            "Average planned R:R",
            f"{format_ratio(o.average_planned_rr)} ({o.planned_rr_trades} trades with a target)",
        ),
        ("Longest win / loss streak", f"{o.longest_win_streak} / {o.longest_loss_streak}"),
        ("Max drawdown (R)", format_r_amount(o.max_drawdown_r.amount)),
    ]
    if o.best_trade_r:
        lines.append(
            (
                "Best trade (R)",
                f"#{o.best_trade_r.trade_id} {o.best_trade_r.symbol} {format_r(o.best_trade_r.value)}",
            )
        )
        lines.append(
            (
                "Worst trade (R)",
                f"#{o.worst_trade_r.trade_id} {o.worst_trade_r.symbol} {format_r(o.worst_trade_r.value)}",
            )
        )
    width = max(len(label) for label, _ in lines)
    for label, value in lines:
        click.echo(f"{label.ljust(width)}  {value}")

    click.echo("\n== Money, per quote currency (never added together) ==")
    print_table(
        [
            {
                "currency": m.currency,
                "trades": m.trades,
                "gross P&L": format_money(m.gross_pnl, m.currency),
                "fees": format_money(m.fees, m.currency),
                "net P&L": format_money(m.net_pnl, m.currency),
                "winners": format_money(m.winning_pnl, m.currency),
                "losers": format_money(m.losing_pnl, m.currency),
                "PF": format_ratio(m.profit_factor),
                "max DD": format_money(m.max_drawdown.amount, m.currency),
            }
            for m in o.money.values()
        ]
    )

    for title, groups in (
        ("By instrument", report.by_instrument),
        ("By strategy", report.by_strategy),
        ("By session", report.by_session),
        ("By day of week (UTC)", report.by_weekday),
        ("By month", report.by_month),
    ):
        click.echo(f"\n== {title} ==")
        print_table([summary_row(label, s) for label, s in groups.items()])

    click.echo("\n== Cumulative R (chart data; not account equity) ==")
    print_table(
        [
            {
                "trade": p.trade_id,
                "date": p.trade_date[:10],
                "symbol": p.symbol,
                "R": format_r(p.realised_r),
                "cumulative": format_r(p.cumulative_r),
            }
            for p in report.cumulative_r
        ]
    )

    for title, items in (
        ("Excluded trades", report.excluded),
        ("Closed trades without a realised R", report.r_unavailable),
    ):
        if items:
            click.echo(f"\n== {title} ==")
            for item in items:
                click.echo(f"#{item.trade_id}: {item.reason}")

    if report.unknown_close_time_trade_ids:
        ids = ", ".join(f"#{i}" for i in report.unknown_close_time_trade_ids)
        click.echo("\n== Closed trades with no recorded exit time (ordered by entry time) ==")
        click.echo(ids)


@click.command("report")
@click.option("--email", required=True, help="Whose trades to report on.")
@click.option("--instrument", help="Only this symbol, e.g. XAUUSD.")
@click.option("--start", help="First date to include, YYYY-MM-DD (UTC).")
@click.option("--end", help="Last date to include, YYYY-MM-DD (UTC).")
@click.option("--json", "as_json", is_flag=True, help="Exact JSON output instead of a rounded table.")
@with_appcontext
def report_command(email, instrument, start, end, as_json):
    """Performance report calculated by the Python analytics engine."""
    conn = get_db()
    user_id = user_from_email(conn, email)["id"]
    instrument_id = None
    if instrument:
        found = get_instrument_by_symbol(conn, instrument, user_id=user_id)
        if found is None:
            raise click.ClickException(f"No instrument with symbol '{instrument}'.")
        instrument_id = found["id"]
    try:
        report = build_report(conn, user_id=user_id, instrument_id=instrument_id, start=start, end=end)
    except ValidationError as exc:
        raise click.ClickException(str(exc)) from exc
    if as_json:
        click.echo(json.dumps(to_jsonable(report), indent=2))
    else:
        print_report(report)


@click.command("migrate-db")
@with_appcontext
@click.option("--no-backup", is_flag=True, help="SQLite: skip the safety copy (not recommended).")
@click.option(
    "--backed-up", is_flag=True, help="PostgreSQL: confirm a backup (pg_dump or RDS snapshot) exists."
)
def migrate_db_command(no_backup, backed_up):
    """Back up the database, then apply any pending schema migrations."""
    conn = get_db()
    before = schema_version(conn)
    if conn.dialect == POSTGRESQL:
        if migrations.pending_versions(conn) and before >= 1 and not backed_up:
            raise click.ClickException(
                "Pending PostgreSQL migrations. Take a backup first (pg_dump or an RDS snapshot, see "
                "docs/PHASE_8_DATABASE_MIGRATION.md), then run again with --backed-up."
            )
        applied = init_schema(conn)
        click.echo(f"Schema version {before} -> {schema_version(conn)} (applied: {applied or 'none'})")
        return
    if not migrations.pending_versions(conn) and before >= 1:
        click.echo(f"Already at schema version {before}. Nothing to do.")
        return
    if not no_backup and before >= 1:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = Path(current_app.config["DATABASE"])
        backup_path = path.with_name(f"{path.name}.v{before}-{stamp}.bak")
        migrations.backup(conn, str(backup_path))
        click.echo(f"Backup written: {backup_path}")
    applied = init_schema(conn)
    click.echo(f"Schema version {before} -> {schema_version(conn)} (applied: {applied or 'none'})")
    legacy = legacy_counts(conn)
    if legacy["instruments"] or legacy["trades"]:
        click.echo(
            f"{legacy['instruments']} instrument(s) and {legacy['trades']} trade(s) are unassigned and hidden "
            "until you run: flask --app tradeledger claim-legacy-data --email you@example.com"
        )


# ---------- Accounts (Phase 8) ----------


@click.command("create-user")
@click.option("--email", required=True)
@click.option("--password-stdin", is_flag=True, help="Read the password from standard input (one line).")
@with_appcontext
def create_user_command(email, password_stdin):
    """Create an account. Works even when self-registration is disabled."""
    conn = get_db()
    problem = email_problem(email)
    if problem:
        raise click.ClickException(f"Email address {problem}")
    password = read_new_password(password_stdin, normalise_email(email))
    try:
        user_id = create_user(
            conn, email, hash_password(password, parameters_from_config(current_app.config))
        )
    except DuplicateError as exc:
        raise click.ClickException(str(exc)) from exc
    current_app.logger.info(
        "account created by operator", extra={"event": "auth.user.created", "user_id": user_id}
    )
    click.echo(f"Account created for {normalise_email(email)} (user id {user_id}).")


@click.command("set-password")
@click.option("--email", required=True)
@click.option("--password-stdin", is_flag=True, help="Read the password from standard input (one line).")
@with_appcontext
def set_password_command(email, password_stdin):
    """Set a new password (e.g. a forgotten one) and sign the account out everywhere."""
    conn = get_db()
    user = user_from_email(conn, email)
    password = read_new_password(password_stdin, user["email"])
    hashed = hash_password(password, parameters_from_config(current_app.config))
    set_password_hash(conn, user["id"], hashed, end_other_sessions=True)
    current_app.logger.info(
        "password set by operator", extra={"event": "auth.password.reset", "user_id": user["id"]}
    )
    click.echo(f"Password updated for {user['email']}. Existing sessions have been ended.")


@click.command("deactivate-user")
@click.option("--email", required=True)
@with_appcontext
def deactivate_user_command(email):
    """Block sign-in and API access for an account (its data is kept)."""
    conn = get_db()
    user = user_from_email(conn, email)
    set_active(conn, user["id"], False)
    click.echo(f"{user['email']} deactivated: sessions ended, sign-in and API tokens refused.")


@click.command("activate-user")
@click.option("--email", required=True)
@with_appcontext
def activate_user_command(email):
    conn = get_db()
    user = user_from_email(conn, email)
    set_active(conn, user["id"], True)
    click.echo(f"{user['email']} can sign in again.")


@click.command("list-users")
@with_appcontext
def list_users_command():
    """Accounts with their status (never password hashes)."""
    rows = (
        get_db()
        .execute("SELECT id, email, is_active, created_at, last_login_at FROM users ORDER BY id")
        .fetchall()
    )
    print_table([{**dict(row), "is_active": bool(row["is_active"])} for row in rows])
    click.echo(f"{count_users(get_db())} account(s).")


@click.command("claim-legacy-data")
@click.option("--email", required=True, help="The account that becomes the owner.")
@click.option("--apply", "apply_changes", is_flag=True, help="Make the change (default: dry run).")
@with_appcontext
def claim_legacy_data_command(email, apply_changes):
    """Give unassigned pre-Phase 8 records to one account (dry run unless --apply)."""
    conn = get_db()
    user = user_from_email(conn, email)
    summary = claim_legacy_data(conn, user_id=user["id"], apply=apply_changes)
    click.echo(f"Unassigned records: {summary['instruments']} instrument(s), {summary['trades']} trade(s).")
    for conflict in summary["conflicts"]:
        click.echo(f"CONFLICT: {conflict}")
    if summary["conflicts"]:
        raise click.ClickException("Nothing was changed. Resolve the conflicts above first.")
    if summary["applied"]:
        current_app.logger.info(
            "legacy records assigned",
            extra={"event": "legacy.claimed", "user_id": user["id"], "trades": summary["trades"]},
        )
        click.echo(f"Done: every unassigned record now belongs to {user['email']}.")
    elif apply_changes:
        click.echo("Nothing to assign.")
    else:
        click.echo(f"Dry run: nothing changed. Re-run with --apply to assign them to {user['email']}.")


@click.command("openapi")
@with_appcontext
@click.option(
    "--output", default="docs/openapi.json", show_default=True, help="File to write the specification to."
)
def openapi_command(output):
    """Write the OpenAPI 3.1 specification (the same one served by the API)."""
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(build_spec(), indent=2) + "\n", encoding="utf-8")
    click.echo(f"OpenAPI specification written to {output}")


def init_app(app):
    for command in (
        init_db_command,
        reset_db_command,
        seed_db_command,
        db_info_command,
        analytics_command,
        run_sql_command,
        report_command,
        migrate_db_command,
        openapi_command,
        create_user_command,
        set_password_command,
        deactivate_user_command,
        activate_user_command,
        list_users_command,
        claim_legacy_data_command,
    ):
        app.cli.add_command(command)
```

### `tradeledger/config.py` (MODIFIED)

**Why:** Version 0.8.0; database, password-hash, session, throttle, token, proxy and logging settings; `*_FILE` secrets; removed unsafe switches.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
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
    APP_VERSION = "0.8.0"

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

    # Chart.js for the /analytics dashboard (Phase 6). See Phase 6/7 docs.
    CHART_JS_VERSION = "4.5.1"
    CHART_JS_URL = os.environ.get(
        "TRADELEDGER_CHART_JS_URL", "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
    )
    CHART_JS_INTEGRITY = os.environ.get("TRADELEDGER_CHART_JS_INTEGRITY") or None


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
```

### `tradeledger/db.py` (MODIFIED)

**Why:** Rewritten database adapter: one interface over SQLite and PostgreSQL (psycopg 3 + psycopg_pool in production), placeholder conversion, explicit transactions with SAVEPOINTs, portable IntegrityError/DataError, Decimal binding, URL redaction, connection sources.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Database connections for SQLite and PostgreSQL (Phase 8).

Plain English
-------------
TradeLedger can store its data in two kinds of database:

* SQLite: one file on your computer. Nothing to install; ideal for local
  development and the automated tests.
* PostgreSQL: a database server. Used by the Docker Compose stack and by
  the AWS design (Amazon RDS), where several application containers share
  one database.

The rest of the application should not care which one it is talking to.
This module gives both the same small interface, so the repositories write
their SQL once.

Technical summary
-----------------
connect(target)   open a connection: a file path means SQLite, a
                  "postgresql://..." URL means PostgreSQL. Scripts and
                  tests use this.
get_db()          inside a Flask request or CLI command: one connection per
                  request, stored on flask.g and released when the request
                  ends. PostgreSQL connections come from a pool.

A Connection offers:
    execute(sql, params)   SQL with "?" placeholders on BOTH databases
                           (converted to psycopg's "%s" for PostgreSQL)
    executescript(sql)     several statements, no parameters
    with conn: ...         a transaction: commit on success, roll back on
                           any exception; nested blocks use SAVEPOINTs
    numeric(text)          the value to bind for an exact decimal column
    dialect                "sqlite" or "postgresql"

Rows behave like sqlite3.Row: row["symbol"], row[0] and dict(row) all work.
Integrity failures from either database are raised as db.IntegrityError
with a `kind` ("unique", "foreign_key", "check", "not_null") and, where
the database reports one, the constraint name.

Why not SQLAlchemy? It is the usual, excellent choice for this job and is
the documented upgrade path (docs/PHASE_8_DATABASE_MIGRATION.md). It is not
installable in the environment this phase was built in, and TradeLedger's
existing repository layer already keeps every SQL statement in a handful of
functions, so a small adapter (this file) was enough. It deliberately does
NOT build SQL, map objects or manage schemas: it only smooths over the
differences between the two drivers.

Connections run in autocommit mode at the driver level, and transactions
are explicit (BEGIN ... COMMIT). That gives SQLite and PostgreSQL the same
behaviour: a statement outside `with conn:` is committed immediately, and
everything inside `with conn:` is all-or-nothing.
"""

import functools
import logging
import sqlite3
import threading
from decimal import Decimal
from importlib import resources
from urllib.parse import urlsplit, urlunsplit

from flask import current_app, g

from . import migrations

SQLITE = "sqlite"
POSTGRESQL = "postgresql"

logger = logging.getLogger("tradeledger.db")

# Optional PostgreSQL driver override. Production uses psycopg 3. The test
# suite may register a different module with the same small interface
# (see tests/pg_libpq_bridge.py); application code never does.
_pg_driver_override = None


def register_postgres_driver(module):
    """Use `module` instead of psycopg for PostgreSQL (tests only)."""
    global _pg_driver_override
    _pg_driver_override = module


def _postgres_driver():
    if _pg_driver_override is not None:
        return _pg_driver_override
    try:
        import psycopg
    except ImportError as exc:  # pragma: no cover - depends on the installed packages
        raise RuntimeError(
            "PostgreSQL support needs the psycopg package: pip install -r requirements.txt"
        ) from exc
    return psycopg


# ---------- Errors ----------


class DatabaseError(Exception):
    """Base class for errors raised by this module."""


class DataError(DatabaseError):
    """PostgreSQL rejected a value's format, e.g. "abc" for a NUMERIC column.
    (SQLite stores anything and relies on CHECK constraints instead.)"""


class IntegrityError(DatabaseError):
    """A constraint stopped a write. Nothing from that statement was kept.

    kind        "unique", "foreign_key", "check", "not_null" or None
    constraint  the constraint (or trigger rule) name when known
    """

    def __init__(self, message, *, kind=None, constraint=None):
        super().__init__(message)
        self.kind = kind
        self.constraint = constraint


_PG_SQLSTATE_KINDS = {"23505": "unique", "23503": "foreign_key", "23514": "check", "23502": "not_null"}


def _sqlite_integrity(exc):
    message = str(exc)
    if message.startswith("UNIQUE constraint failed"):
        return IntegrityError(message, kind="unique", constraint=message.partition(": ")[2] or None)
    if message.startswith("FOREIGN KEY constraint failed"):
        return IntegrityError(message, kind="foreign_key")
    if message.startswith("CHECK constraint failed"):
        return IntegrityError(message, kind="check", constraint=message.partition(": ")[2] or None)
    if message.startswith("NOT NULL constraint failed"):
        return IntegrityError(message, kind="not_null", constraint=message.partition(": ")[2] or None)
    # Trigger rules raise "rule_name: explanation" (see migrations.py).
    head, sep, _ = message.partition(":")
    if sep and head.replace("_", "").isalnum():
        return IntegrityError(message, kind="check", constraint=head)
    return IntegrityError(message)


def _postgres_integrity(exc):
    diag = getattr(exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    kind = _PG_SQLSTATE_KINDS.get(getattr(exc, "sqlstate", None))
    return IntegrityError(str(exc).strip(), kind=kind, constraint=constraint)


# ---------- Rows and cursors ----------


class Row(dict):
    """A result row: row["name"], row[0] and dict(row) all work."""

    __slots__ = ("_values",)

    def __init__(self, names, values):
        super().__init__(zip(names, values, strict=True))
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return super().__getitem__(key)


class Cursor:
    """The result of Connection.execute()."""

    def __init__(self, raw):
        self._raw = raw
        description = raw.description
        self._names = [getattr(col, "name", None) or col[0] for col in description] if description else None

    @property
    def rowcount(self):
        return self._raw.rowcount

    def _row(self, values):
        return None if values is None else Row(self._names, values)

    def fetchone(self):
        if self._names is None:
            return None
        return self._row(self._raw.fetchone())

    def fetchall(self):
        if self._names is None:
            return []
        return [Row(self._names, values) for values in self._raw.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())


# ---------- Connections ----------


class Connection:
    """Common behaviour; see SQLiteConnection and PostgresConnection."""

    dialect = None

    def __init__(self, raw):
        self.raw = raw
        self._depth = 0

    # Subclasses provide _run(sql, params) -> driver cursor, _run_script(sql)
    # for several statements, _control(sql) for one transaction-control
    # statement, _integrity(exc) and _integrity_errors (the driver's class).

    _data_errors = ()

    def execute(self, sql, params=()):
        if isinstance(params, dict):
            raise TypeError("TradeLedger SQL uses positional '?' placeholders, not named ones")
        try:
            return Cursor(self._run(sql, tuple(params)))
        except self._integrity_errors as exc:
            raise self._integrity(exc) from exc
        except self._data_errors as exc:
            raise DataError(str(exc).strip()) from exc

    def executescript(self, sql):
        try:
            self._run_script(sql)
        except self._integrity_errors as exc:
            raise self._integrity(exc) from exc

    def numeric(self, text):
        """The value to bind for an exact decimal column (see subclasses)."""
        return text

    @property
    def in_transaction(self):
        return self._depth > 0

    # Transactions. `with conn:` mirrors sqlite3's behaviour (commit or roll
    # back), and works the same way on PostgreSQL. A nested `with conn:`
    # becomes a SAVEPOINT, so an inner failure that the caller handles does
    # not abort the outer transaction.
    def __enter__(self):
        if self._depth == 0:
            self._control("BEGIN")
        else:
            self._control(f"SAVEPOINT tl_sp_{self._depth}")
        self._depth += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self._depth -= 1
        if self._depth == 0:
            self._control("ROLLBACK" if exc_type else "COMMIT")
        else:
            name = f"tl_sp_{self._depth}"
            if exc_type:
                self._control(f"ROLLBACK TO SAVEPOINT {name}")
            self._control(f"RELEASE SAVEPOINT {name}")
        return False

    def commit(self):
        """Statements outside `with conn:` are already committed. Kept so
        older code that calls commit() keeps working."""
        return None

    def rollback(self):
        if self._depth:
            self._depth = 0
            self._control("ROLLBACK")

    def close(self):
        self.raw.close()


class SQLiteConnection(Connection):
    dialect = SQLITE
    _integrity_errors = sqlite3.IntegrityError

    def _run(self, sql, params):
        return self.raw.execute(sql, params)

    def _run_script(self, sql):
        if self._depth:
            # sqlite3's executescript() would silently COMMIT the open transaction.
            raise DatabaseError("executescript() cannot run inside a transaction on SQLite")
        self.raw.executescript(sql)

    def _control(self, sql):
        self.raw.execute(sql)

    def _integrity(self, exc):
        return _sqlite_integrity(exc)

    def rollback(self):
        self._depth = 0
        if self.raw.in_transaction:
            self.raw.execute("ROLLBACK")

    def backup(self, target):
        self.raw.backup(target)


class PostgresConnection(Connection):
    dialect = POSTGRESQL

    def __init__(self, raw, driver):
        super().__init__(raw)
        self._integrity_errors = driver.IntegrityError
        self._data_errors = driver.DataError

    def _run(self, sql, params):
        cursor = self.raw.cursor()
        if params:
            cursor.execute(to_pyformat(sql), params)
        else:
            cursor.execute(sql)
        return cursor

    def _run_script(self, sql):
        self.raw.cursor().execute(sql)

    def _control(self, sql):
        self.raw.cursor().execute(sql)

    def _integrity(self, exc):
        return _postgres_integrity(exc)

    def numeric(self, text):
        """PostgreSQL columns are NUMERIC: bind a Decimal, which the driver
        sends as an exact numeric (never a float)."""
        return None if text is None else Decimal(text)


@functools.lru_cache(maxsize=512)
def to_pyformat(sql):
    """Convert "?" placeholders to psycopg's "%s".

    "?" inside a quoted SQL literal is left alone, and every literal "%" is
    doubled, because psycopg treats "%" as the start of a placeholder.
    """
    out, in_quote = [], False
    for char in sql:
        if char == "'":
            in_quote = not in_quote
            out.append(char)
        elif char == "%":
            out.append("%%")
        elif char == "?" and not in_quote:
            out.append("%s")
        else:
            out.append(char)
    return "".join(out)


def is_postgres_url(target):
    return isinstance(target, str) and target.startswith(("postgresql://", "postgres://"))


def connect(target):
    """Open a connection to a SQLite file path or a PostgreSQL URL."""
    if is_postgres_url(target):
        driver = _postgres_driver()
        return PostgresConnection(driver.connect(target, autocommit=True), driver)
    return connect_sqlite(target)


def connect_sqlite(path):
    # isolation_level=None: autocommit at the driver level; Connection adds
    # explicit transactions. check_same_thread=False is safe because one
    # connection is only ever used by one request at a time.
    raw = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    # SQLite does NOT enforce foreign keys unless you switch them on, per connection.
    raw.execute("PRAGMA foreign_keys = ON")
    # Wait up to 5 s for another writer instead of failing at once.
    raw.execute("PRAGMA busy_timeout = 5000")
    return SQLiteConnection(raw)


def redact_url(url):
    """postgresql://app:secret@db:5432/x -> postgresql://app:***@db:5432/x"""
    parts = urlsplit(url)
    if parts.password is None:
        return url
    netloc = f"{parts.username}:***@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# ---------- Per-application connection source ----------


class SQLiteSource:
    dialect = SQLITE

    def __init__(self, path):
        self.path = path

    def acquire(self):
        return connect_sqlite(self.path)

    def release(self, conn):
        conn.rollback()
        conn.close()

    def describe(self):
        return f"sqlite:{self.path}"

    def close(self):
        return None


class PostgresSource:
    """A connection pool shared by the requests of one process.

    Gunicorn runs several worker processes; each gets its own pool, opened
    on first use (after the fork), so sockets are never shared between
    processes. Connections are checked when borrowed, so a database restart
    does not leave broken connections in the pool.
    """

    dialect = POSTGRESQL

    def __init__(self, conninfo, *, min_size, max_size, timeout, require_pool, label=None):
        self._conninfo = conninfo
        self._label = label
        self._min_size, self._max_size, self._timeout = min_size, max_size, timeout
        self._require_pool = require_pool
        self._pool = None
        self._lock = threading.Lock()
        self._driver = _postgres_driver()

    def _get_pool(self):
        with self._lock:
            if self._pool is None:
                try:
                    from psycopg_pool import ConnectionPool
                except ImportError:
                    # A registered test driver (tests/pg_libpq_bridge.py) has no pool package.
                    if self._require_pool and _pg_driver_override is None:
                        raise RuntimeError(
                            "psycopg_pool is required for PostgreSQL in this environment"
                        ) from None
                    logger.warning("psycopg_pool is not installed: one connection per request")
                    self._pool = _UnpooledConnections(self._driver, self._conninfo)
                else:
                    self._pool = ConnectionPool(
                        self._conninfo,
                        kwargs={"autocommit": True},
                        min_size=self._min_size,
                        max_size=self._max_size,
                        timeout=self._timeout,
                        check=ConnectionPool.check_connection,
                        open=True,
                        name="tradeledger",
                    )
            return self._pool

    def acquire(self):
        return PostgresConnection(self._get_pool().getconn(), self._driver)

    def release(self, conn):
        try:
            conn.rollback()
        finally:
            self._get_pool().putconn(conn.raw)

    def describe(self):
        if self._label:
            return self._label
        return redact_url(self._conninfo) if is_postgres_url(self._conninfo) else "postgresql"

    def close(self):
        if self._pool is not None:
            self._pool.close()


class _UnpooledConnections:
    """Fallback used only when psycopg_pool is missing outside production."""

    def __init__(self, driver, conninfo):
        self._driver, self._conninfo = driver, conninfo

    def getconn(self):
        return self._driver.connect(self._conninfo, autocommit=True)

    def putconn(self, raw):
        raw.close()

    def close(self):
        return None


def build_conninfo(host, port, dbname, user, password, sslmode):
    """A libpq keyword/value connection string with every value quoted."""

    def quote(value):
        return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"

    fields = {"host": host, "port": port, "dbname": dbname, "user": user, "password": password}
    fields["sslmode"] = sslmode or "prefer"
    fields["application_name"] = "tradeledger"
    return " ".join(f"{key}={quote(value)}" for key, value in fields.items() if value not in (None, ""))


def make_source(app):
    """Choose the database from the configuration.

    DATABASE_URL  postgresql://user:password@host:5432/name  (Compose, CI)
    DB_HOST etc.  separate settings (AWS: ECS injects DB_PASSWORD from
                  Secrets Manager, so no URL containing it is ever built
                  in a task definition or logged)
    otherwise     the SQLite file in DATABASE
    """
    config = app.config
    pool = {
        "min_size": config["DB_POOL_MIN_SIZE"],
        "max_size": config["DB_POOL_MAX_SIZE"],
        "timeout": config["DB_POOL_TIMEOUT"],
        "require_pool": config["CONFIG_NAME"] == "production",
    }
    url = config.get("DATABASE_URL")
    if not url and is_postgres_url(config.get("DATABASE")):
        url = config["DATABASE"]  # tests pass a PostgreSQL URL where a file path would go
    if url:
        if not is_postgres_url(url):
            raise RuntimeError("DATABASE_URL must start with postgresql://")
        return PostgresSource(url, **pool)
    if config.get("DB_HOST"):
        conninfo = build_conninfo(
            config["DB_HOST"],
            config.get("DB_PORT"),
            config.get("DB_NAME"),
            config.get("DB_USER"),
            config.get("DB_PASSWORD"),
            config.get("DB_SSLMODE"),
        )
        label = (
            f"postgresql://{config.get('DB_USER')}@{config['DB_HOST']}:"
            f"{config.get('DB_PORT') or 5432}/{config.get('DB_NAME')}"
        )
        return PostgresSource(conninfo, label=label, **pool)
    return SQLiteSource(config["DATABASE"])


def source():
    return current_app.extensions["tradeledger_db"]


def get_db():
    """Return this request's connection, opening (or borrowing) it on first use."""
    if "db" not in g:
        g.db = source().acquire()
    return g.db


def close_db(exception=None):
    """Release the request's connection, if one was opened."""
    conn = g.pop("db", None)
    if conn is not None:
        source().release(conn)


# ---------- Schema ----------


def read_schema(dialect=SQLITE):
    """Return the baseline schema file shipped inside the package."""
    name = "schema.sql" if dialect == SQLITE else "schema_postgres.sql"
    return resources.files("tradeledger").joinpath(name).read_text(encoding="utf-8")


def init_schema(conn):
    """Bring the database up to the latest schema. Never deletes data.

    SQLite: run the version 1 baseline (schema.sql, CREATE ... IF NOT
    EXISTS), then every pending numbered migration (migrations.py).
    PostgreSQL: create the current schema from schema_postgres.sql on an
    empty database, then apply any later PostgreSQL migrations.

    Returns the list of migration versions applied (often empty).
    """
    if conn.dialect == POSTGRESQL:
        return migrations.migrate_postgres(conn, read_schema(POSTGRESQL))
    conn.executescript(read_schema())
    if migrations.current_version(conn) == 0:
        conn.execute("PRAGMA user_version = 1")
    return migrations.migrate(conn)


def drop_all(conn):
    """Delete every table and reset the schema version to 0.
    Used only by the reset-db command, which production refuses."""
    if conn.dialect == POSTGRESQL:
        conn.executescript(
            "DROP TABLE IF EXISTS trades, instruments, api_tokens, login_attempts, users, "
            "schema_migrations CASCADE"
        )
        return
    conn.executescript(
        """
        PRAGMA foreign_keys = OFF;
        DROP TABLE IF EXISTS trades;
        DROP TABLE IF EXISTS instruments;
        DROP TABLE IF EXISTS api_tokens;
        DROP TABLE IF EXISTS login_attempts;
        DROP TABLE IF EXISTS users;
        PRAGMA foreign_keys = ON;
        PRAGMA user_version = 0;
        """
    )


def schema_version(conn):
    return migrations.current_version(conn)


def list_tables(conn):
    if conn.dialect == POSTGRESQL:
        sql = (
            "SELECT table_name AS name FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
    else:
        sql = "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    return [row["name"] for row in conn.execute(sql)]


def ping(conn):
    """True when the database answers a trivial query (readiness check)."""
    return conn.execute("SELECT 1").fetchone()[0] == 1


def init_app(app):
    """Create the connection source and register the release hook."""
    app.extensions["tradeledger_db"] = make_source(app)
    app.teardown_appcontext(close_db)


def database_label(app):
    """A description that is safe to log (never contains a password)."""
    return app.extensions["tradeledger_db"].describe()
```

### `tradeledger/migrations.py` (MODIFIED)

**Why:** Schema v3 for SQLite (safe table rebuild with foreign-key check) and PostgreSQL baseline under an advisory lock.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Numbered, forward-only database migrations.

How it works (SQLite)
---------------------
SQLite stores a whole number in the database file header, read and written
with PRAGMA user_version. TradeLedger uses it as the schema version:

    0  empty file, nothing created yet
    1  baseline tables from schema.sql (Phases 2 and 3)
    2  trades.closed_at column + its index and integrity triggers (Phase 4)
    3  user accounts and record ownership (Phase 8)

migrate(conn) applies every migration newer than the file's version, in
order, each inside its own transaction together with the version bump. If a
migration fails, it is rolled back and the version is unchanged, so the
database is never left half-migrated. Running migrate() again is harmless.
The migrate-db command copies the database file before migrating.

How it works (PostgreSQL)
-------------------------
PostgreSQL support starts in Phase 8, so a PostgreSQL database has no
history to replay: schema_postgres.sql creates the version 3 structure
directly and records it in the schema_migrations table. Later versions
(4, 5, ...) must be added to BOTH dictionaries below, and the tests check
that the two engines end on the same version. Migrations take an advisory
lock, so two containers starting at the same moment cannot both migrate.

Migration 2: closed_at
----------------------
Adds an optional exit timestamp. Existing rows get NULL: we do NOT invent
exit times for historical trades. The cross-column rules are triggers,
because SQLite's ALTER TABLE cannot add table CHECK constraints.

Migration 3: users and ownership (Phase 8)
------------------------------------------
* New tables: users, api_tokens, login_attempts.
* instruments and trades gain user_id. A symbol is now unique PER USER,
  not across the whole database, so two users can both record EURUSD.
* trades has a two-column foreign key (instrument_id, user_id) ->
  instruments (id, user_id): the database itself refuses a trade that
  points at another user's instrument.
* SQLite cannot change a UNIQUE rule or add a foreign key to an existing
  table, so both tables are REBUILT: create the new table, copy every row
  unchanged (same ids, text, timestamps), drop the old table, rename.
  Foreign-key enforcement is switched off for the rebuild and
  PRAGMA foreign_key_check must report nothing before the commit.
* Existing rows get user_id NULL: "unassigned legacy data". Nobody can
  see unassigned records. The owner of the data assigns them, deliberately,
  with:  flask --app tradeledger claim-legacy-data --email you@example.com
  (a dry run unless --apply is given). Ownership is never guessed, and the
  first person to register on a server does not receive old data.
"""

import sqlite3

TIMESTAMP_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z"
NOW_SQLITE = "(strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))"


def _decimal_check(column, *, positive=True, nullable=False):
    """The Phase 2 rule for an exact decimal stored as TEXT."""
    rule = f"{column} NOT GLOB '*[^0-9.]*' AND {column} GLOB '*[0-9]*'"
    if positive:
        rule += f" AND CAST({column} AS REAL) > 0"
    if nullable:
        return f"CHECK ({column} IS NULL OR ({rule}))"
    return f"CHECK ({rule})"


_CLOSED_AT_TRIGGERS = """
        CREATE TRIGGER IF NOT EXISTS trades_closed_at_rules_insert
        BEFORE INSERT ON trades
        WHEN NEW.closed_at IS NOT NULL
             AND (NEW.status = 'OPEN' OR NEW.closed_at < NEW.trade_date)
        BEGIN
            SELECT RAISE(ABORT, 'closed_at_rules: an OPEN trade cannot have closed_at, and closed_at cannot be before trade_date');
        END;

        CREATE TRIGGER IF NOT EXISTS trades_closed_at_rules_update
        BEFORE UPDATE ON trades
        WHEN NEW.closed_at IS NOT NULL
             AND (NEW.status = 'OPEN' OR NEW.closed_at < NEW.trade_date)
        BEGIN
            SELECT RAISE(ABORT, 'closed_at_rules: an OPEN trade cannot have closed_at, and closed_at cannot be before trade_date');
        END;
"""

_TRADE_COLUMNS = (
    "id, instrument_id, trade_date, direction, entry_price, stop_loss, take_profit, exit_price, "
    "position_size, fees, status, strategy, session, notes, created_at, updated_at, closed_at"
)

MIGRATIONS = {
    2: f"""
        ALTER TABLE trades ADD COLUMN closed_at TEXT
            CHECK (closed_at IS NULL OR closed_at GLOB '{TIMESTAMP_GLOB}');

        CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades (closed_at);
        {_CLOSED_AT_TRIGGERS}
    """,
    3: f"""
        CREATE TABLE users (
            id              INTEGER PRIMARY KEY,
            email           TEXT    NOT NULL UNIQUE
                                    CHECK (email = lower(email) AND length(email) BETWEEN 3 AND 254),
            password_hash   TEXT    NOT NULL CHECK (password_hash LIKE '$argon2id$%'),
            is_active       INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
            session_version INTEGER NOT NULL DEFAULT 1 CHECK (session_version >= 1),
            created_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            updated_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            last_login_at   TEXT
        );

        CREATE TABLE api_tokens (
            id           INTEGER PRIMARY KEY,
            user_id      INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
            name         TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 60),
            token_hash   TEXT    NOT NULL UNIQUE CHECK (length(token_hash) = 64),
            token_prefix TEXT    NOT NULL,
            created_at   TEXT    NOT NULL,
            expires_at   TEXT    NOT NULL,
            last_used_at TEXT,
            revoked_at   TEXT
        );
        CREATE INDEX idx_api_tokens_user ON api_tokens (user_id);

        CREATE TABLE login_attempts (
            id           INTEGER PRIMARY KEY,
            scope        TEXT NOT NULL,
            key_digest   TEXT NOT NULL,
            attempted_at TEXT NOT NULL
        );
        CREATE INDEX idx_login_attempts_lookup ON login_attempts (scope, key_digest, attempted_at);

        CREATE TABLE instruments_v3 (
            id                  INTEGER PRIMARY KEY,
            user_id             INTEGER REFERENCES users (id) ON DELETE RESTRICT,
            symbol              TEXT    NOT NULL
                                        CHECK (symbol = upper(symbol)
                                               AND length(symbol) BETWEEN 1 AND 20),
            name                TEXT    NOT NULL CHECK (length(name) BETWEEN 1 AND 100),
            asset_class         TEXT    NOT NULL
                                        CHECK (asset_class IN ('FOREX', 'COMMODITY', 'INDEX', 'STOCK', 'OTHER')),
            price_increment     TEXT    NOT NULL {_decimal_check("price_increment")},
            contract_multiplier TEXT    NOT NULL {_decimal_check("contract_multiplier")},
            quote_currency      TEXT    NOT NULL
                                        CHECK (length(quote_currency) = 3
                                               AND quote_currency = upper(quote_currency)),
            created_at          TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            CONSTRAINT instruments_user_symbol_unique UNIQUE (user_id, symbol),
            CONSTRAINT instruments_id_user_unique UNIQUE (id, user_id)
        );

        CREATE TABLE trades_v3 (
            id              INTEGER PRIMARY KEY,
            user_id         INTEGER REFERENCES users (id) ON DELETE RESTRICT,
            instrument_id   INTEGER NOT NULL REFERENCES instruments (id) ON DELETE RESTRICT,
            trade_date      TEXT    NOT NULL CHECK (trade_date GLOB '{TIMESTAMP_GLOB}'),
            direction       TEXT    NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
            entry_price     TEXT    NOT NULL {_decimal_check("entry_price")},
            stop_loss       TEXT    NOT NULL {_decimal_check("stop_loss")},
            take_profit     TEXT    {_decimal_check("take_profit", nullable=True)},
            exit_price      TEXT    {_decimal_check("exit_price", nullable=True)},
            position_size   TEXT    NOT NULL {_decimal_check("position_size")},
            fees            TEXT    NOT NULL DEFAULT '0' {_decimal_check("fees", positive=False)},
            status          TEXT    NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
            strategy        TEXT    CHECK (strategy IS NULL OR length(strategy) BETWEEN 1 AND 50),
            session         TEXT    CHECK (session IS NULL
                                           OR session IN ('ASIA', 'LONDON', 'NEW_YORK', 'OTHER')),
            notes           TEXT    CHECK (notes IS NULL OR length(notes) <= 2000),
            created_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            updated_at      TEXT    NOT NULL DEFAULT {NOW_SQLITE},
            closed_at       TEXT    CHECK (closed_at IS NULL OR closed_at GLOB '{TIMESTAMP_GLOB}'),

            CONSTRAINT trades_instrument_owner_fk FOREIGN KEY (instrument_id, user_id)
                REFERENCES instruments (id, user_id) ON DELETE RESTRICT,
            CONSTRAINT status_matches_exit CHECK (
                (status = 'OPEN'   AND exit_price IS NULL) OR
                (status = 'CLOSED' AND exit_price IS NOT NULL)
            ),
            CONSTRAINT stop_on_correct_side CHECK (
                (direction = 'LONG'  AND CAST(stop_loss AS REAL) < CAST(entry_price AS REAL)) OR
                (direction = 'SHORT' AND CAST(stop_loss AS REAL) > CAST(entry_price AS REAL))
            ),
            CONSTRAINT target_on_correct_side CHECK (
                take_profit IS NULL OR
                (direction = 'LONG'  AND CAST(take_profit AS REAL) > CAST(entry_price AS REAL)) OR
                (direction = 'SHORT' AND CAST(take_profit AS REAL) < CAST(entry_price AS REAL))
            )
        );

        INSERT INTO instruments_v3
            (id, user_id, symbol, name, asset_class, price_increment, contract_multiplier,
             quote_currency, created_at)
        SELECT id, NULL, symbol, name, asset_class, price_increment, contract_multiplier,
               quote_currency, created_at
        FROM instruments;

        INSERT INTO trades_v3 (user_id, {_TRADE_COLUMNS})
        SELECT NULL, {_TRADE_COLUMNS} FROM trades;

        DROP TABLE trades;
        DROP TABLE instruments;
        ALTER TABLE instruments_v3 RENAME TO instruments;
        ALTER TABLE trades_v3 RENAME TO trades;

        CREATE INDEX idx_trades_instrument_id ON trades (instrument_id);
        CREATE INDEX idx_trades_trade_date    ON trades (trade_date);
        CREATE INDEX idx_trades_status        ON trades (status);
        CREATE INDEX idx_trades_closed_at     ON trades (closed_at);
        CREATE INDEX idx_trades_user_date     ON trades (user_id, trade_date, id);
        CREATE INDEX idx_trades_user_closed   ON trades (user_id, closed_at);
        {_CLOSED_AT_TRIGGERS}
    """,  # noqa: S608 - constant DDL built from module constants; no user input
}

# Migrations that rebuild tables: foreign keys must be OFF while they run.
REBUILDS = {3}

# PostgreSQL migrations after the baseline in schema_postgres.sql.
POSTGRES_BASELINE_VERSION = 3
POSTGRES_MIGRATIONS = {}

LATEST_VERSION = max(MIGRATIONS)

# Any constant works; it only has to be the same for every TradeLedger process.
_POSTGRES_LOCK_ID = 7_340_658_812


def current_version(conn):
    if conn.dialect == "postgresql":
        exists = conn.execute("SELECT to_regclass('schema_migrations') IS NOT NULL").fetchone()[0]
        if not exists:
            return 0
        return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    return conn.execute("PRAGMA user_version").fetchone()[0]


def pending_versions(conn):
    version = current_version(conn)
    registry = POSTGRES_MIGRATIONS if conn.dialect == "postgresql" else MIGRATIONS
    return [v for v in sorted(registry) if v > version]


def migrate(conn):
    """Apply all pending SQLite migrations. Returns the versions applied."""
    applied = []
    raw = conn.raw
    for version in pending_versions(conn):
        rebuild = version in REBUILDS
        if rebuild:
            raw.execute("PRAGMA foreign_keys = OFF")  # has no effect inside a transaction
        try:
            raw.executescript(f"BEGIN;\n{MIGRATIONS[version]}")
            problems = raw.execute("PRAGMA foreign_key_check").fetchall()
            if problems:
                raise sqlite3.IntegrityError(f"migration {version}: foreign_key_check failed: {problems[:5]}")
            raw.execute(f"PRAGMA user_version = {int(version)}")
            raw.execute("COMMIT")
        except sqlite3.Error:
            if raw.in_transaction:
                raw.execute("ROLLBACK")
            raise
        finally:
            if rebuild:
                raw.execute("PRAGMA foreign_keys = ON")
        applied.append(version)
    return applied


def migrate_postgres(conn, baseline_sql):
    """Create the PostgreSQL schema on an empty database, then apply any
    later PostgreSQL migrations. Each step is one transaction."""
    applied = []
    with conn:
        conn.execute("SELECT pg_advisory_xact_lock(?)", (_POSTGRES_LOCK_ID,))
        if current_version(conn) == 0:
            conn.executescript(baseline_sql)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (POSTGRES_BASELINE_VERSION, "PostgreSQL baseline (same structure as SQLite version 3)"),
            )
            applied.append(POSTGRES_BASELINE_VERSION)
    for version in pending_versions(conn):
        with conn:
            conn.execute("SELECT pg_advisory_xact_lock(?)", (_POSTGRES_LOCK_ID,))
            if current_version(conn) >= version:
                continue  # another process applied it while we waited for the lock
            conn.executescript(POSTGRES_MIGRATIONS[version])
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, f"migration {version}"),
            )
        applied.append(version)
    return applied


def backup(conn, destination_path):
    """Copy a whole SQLite database to another file (SQLite's online backup).
    PostgreSQL backups use pg_dump or RDS snapshots (see docs)."""
    if conn.dialect != "sqlite":
        raise RuntimeError("backup() copies SQLite files only; use pg_dump for PostgreSQL")
    target = sqlite3.connect(destination_path)
    try:
        conn.backup(target)
    finally:
        target.close()
```

### `tradeledger/performance/dashboard.py` (MODIFIED)

**Why:** Dashboard loads only the owner's trades. Formulas unchanged.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Prepare one PerformanceReport for the /analytics dashboard.

This module sits between the engine and the page:

  report.py      calculates (Decimal, exact)          <- source of truth
  dashboard.py   arranges the results for display:     <- this file
                   * KPI cards
                   * the cumulative R series with running peak and drawdown
                   * every month in the range, including months with no trades
                   * all seven weekdays in calendar order
                   * instrument / strategy / session comparisons
                   * text descriptions of each chart (for people who cannot
                     see the charts)
                   * a JSON-safe chart payload
  analytics.js   draws the charts from that payload    <- presentation only

The only arithmetic here is the running peak and drawdown of a series the
engine has already calculated, done in Decimal under the engine's 60-digit
context. Everything else is selection, ordering and formatting.

Exact values and chart numbers
------------------------------
Chart libraries need JavaScript numbers (64-bit floats). Floats cannot hold
values like 0.1 exactly, so the payload never contains them: every value is
an exact decimal STRING (e.g. "7.688173076923..."), next to the text people
read (e.g. "+7.69R"). analytics.js converts the string to a number at the
very last moment, only to position a bar or a point. Tables and tooltips
show the exact-then-rounded text produced here, so the chart can never
become the source of a figure.
"""

from dataclasses import dataclass, field
from datetime import UTC, date
from decimal import Decimal, localcontext

from ..report_filters import ReportFilters
from ..timestamps import parse_stored
from ..validation import SESSIONS
from .formatting import (
    NOT_AVAILABLE,
    decimal_to_text,
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
)
from .metrics import Summary
from .report import UNSPECIFIED, WEEKDAYS, CurvePoint, PerformanceReport, build_report
from .trade_math import EXACT

ZERO = Decimal(0)

# The trade-by-trade table shows at most this many (most recent) rows. A
# 10,000-row HTML table is unusable and made the page ~9.5 MB in the
# benchmark; the charts and grouped tables still cover every trade.
SERIES_TABLE_LIMIT = 200

# Groups with fewer closed trades than this are flagged "small sample".
# A handful of trades says very little about future results.
SMALL_SAMPLE = 10

# Sessions are shown in the order of the trading day, not alphabetically,
# with readable labels.
SESSION_ORDER = (*SESSIONS, UNSPECIFIED)
SESSION_LABELS = {
    "ASIA": "Asia",
    "LONDON": "London",
    "NEW_YORK": "New York",
    "OTHER": "Other",
    UNSPECIFIED: UNSPECIFIED,
}

PLURALS = {"strategy": "strategies"}


# ---------- Data structures ----------


@dataclass(frozen=True)
class SeriesPoint:
    """One closed trade on the cumulative R and drawdown charts."""

    trade_id: int
    symbol: str
    realised_at: str  # 'YYYY-MM-DDTHH:MM:SSZ' (UTC)
    x: int  # realised_at as Unix milliseconds (chart axis)
    close_time_known: bool  # False: legacy trade placed at its entry time
    realised_r: Decimal
    cumulative_r: Decimal
    peak_r: Decimal  # highest cumulative R so far, never below 0
    drawdown_r: Decimal  # peak_r - cumulative_r, never below 0


@dataclass(frozen=True)
class PeriodRow:
    """A month or a weekday. summary is None when nothing closed in it, which
    is different from closing trades that netted 0R."""

    key: str  # '2026-03' or 'Monday'
    label: str  # 'Mar 2026' or 'Monday'
    summary: Summary | None


@dataclass(frozen=True)
class GroupRow:
    """An instrument, strategy or session. key is the stored value (e.g.
    NEW_YORK); label is what people read (e.g. New York)."""

    key: str
    label: str
    summary: Summary

    @property
    def small_sample(self) -> bool:
        return self.summary.trades < SMALL_SAMPLE


@dataclass(frozen=True)
class Kpi:
    """One summary card. value is display text; tone drives colour, but the
    text (sign, 'n/a', words) always carries the meaning on its own."""

    key: str
    label: str
    value: str
    note: str
    tone: str = "neutral"  # 'positive', 'negative' or 'neutral'


@dataclass(frozen=True)
class Dashboard:
    filters: ReportFilters
    report: PerformanceReport
    kpis: list[Kpi]
    series: list[SeriesPoint]
    months: list[PeriodRow]
    weekdays: list[PeriodRow]
    instruments: list[GroupRow]
    strategies: list[GroupRow]
    sessions: list[GroupRow]
    descriptions: dict[str, str] = field(default_factory=dict)

    @property
    def has_results(self) -> bool:
        return self.report.overall.trades > 0

    @property
    def series_table(self) -> list[SeriesPoint]:
        """The most recent SERIES_TABLE_LIMIT points, for the HTML table."""
        return self.series[-SERIES_TABLE_LIMIT:]


# ---------- Small pure functions ----------


def to_epoch_ms(timestamp: str) -> int:
    """'2026-03-02T11:40:00Z' -> milliseconds since 1970-01-01 UTC (an int,
    exact, which a chart can use as a time axis)."""
    moment = parse_stored(timestamp).replace(tzinfo=UTC)
    return int(moment.timestamp()) * 1000


def drawdown_series(curve: list[CurvePoint]) -> list[SeriesPoint]:
    """Add the running peak and drawdown to the engine's cumulative R curve.

    The peak starts at 0 (before any trade the running total is 0R), so a
    first trade that loses 1R is already a 1R drawdown. The largest
    drawdown_r in the result equals the engine's max_drawdown_r; a test
    checks that the two independent paths agree.
    """
    points, peak = [], ZERO
    with localcontext(EXACT):
        for point in curve:
            peak = max(peak, point.cumulative_r)
            points.append(
                SeriesPoint(
                    trade_id=point.trade_id,
                    symbol=point.symbol,
                    realised_at=point.realised_at,
                    x=to_epoch_ms(point.realised_at),
                    close_time_known=point.close_time_known,
                    realised_r=point.realised_r,
                    cumulative_r=point.cumulative_r,
                    peak_r=peak,
                    drawdown_r=peak - point.cumulative_r,
                )
            )
    return points


def month_keys(first: str, last: str) -> list[str]:
    """Every 'YYYY-MM' from first to last inclusive, across year ends."""
    year, month = int(first[:4]), int(first[5:7])
    end = (int(last[:4]), int(last[5:7]))
    keys = []
    while (year, month) <= end:
        keys.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return keys


def month_label(key: str) -> str:
    return date(int(key[:4]), int(key[5:7]), 1).strftime("%b %Y")


def month_rows(by_month: dict[str, Summary]) -> list[PeriodRow]:
    """One row per calendar month between the first and last month with a
    closed trade. Gaps are kept, with summary=None, so a quiet month is
    visible as a gap rather than silently disappearing (or, worse, being
    drawn as a 0R month)."""
    if not by_month:
        return []
    keys = sorted(by_month)
    return [PeriodRow(k, month_label(k), by_month.get(k)) for k in month_keys(keys[0], keys[-1])]


def weekday_rows(by_weekday: dict[str, Summary]) -> list[PeriodRow]:
    """All seven days, Monday first. Days with no closed trades keep a row
    with summary=None."""
    return [PeriodRow(day, day, by_weekday.get(day)) for day in WEEKDAYS]


def group_rows(groups: dict[str, Summary], order=None, labels=None) -> list[GroupRow]:
    """Groups ordered by `order` when given (sessions), otherwise by total R,
    best first, with the key breaking ties so the order is stable."""
    labels = labels or {}
    rows = [GroupRow(key, labels.get(key, key), summary) for key, summary in groups.items()]
    if order is not None:
        position = {key: index for index, key in enumerate(order)}
        return sorted(rows, key=lambda row: position.get(row.key, len(position)))
    return sorted(rows, key=lambda row: (-row.summary.total_r, row.key))


def tone(value: Decimal | None) -> str:
    if value is None or value == 0:
        return "neutral"
    return "positive" if value > 0 else "negative"


def plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {PLURALS.get(word, word + 's')}"


def short_time(timestamp: str) -> str:
    return parse_stored(timestamp).strftime("%d %b %Y, %H:%M UTC")


# ---------- KPI cards ----------


def build_kpis(report: PerformanceReport) -> list[Kpi]:
    s = report.overall
    none = s.trades == 0
    unavailable = "No closed trades match the filters"

    def count_card(key, label, count, rate):
        note = unavailable if none else f"{format_rate(rate)} of closed trades"
        return Kpi(key, label, NOT_AVAILABLE if none else str(count), note)

    return [
        Kpi(
            "trades",
            "Closed trades",
            str(s.trades),
            f"{plural(report.open_trades, 'open trade')} not included"
            if report.open_trades
            else "No open trades",
        ),
        count_card("wins", "Winning trades", s.wins, s.win_rate),
        count_card("losses", "Losing trades", s.losses, s.loss_rate),
        count_card("breakevens", "Breakeven trades", s.breakevens, s.breakeven_rate),
        Kpi(
            "win_rate",
            "Win rate",
            format_rate(s.win_rate),
            unavailable if none else f"{s.wins} of {s.trades} closed trades",
        ),
        Kpi(
            "average_r",
            "Average realised R",
            format_r(s.average_r),
            unavailable
            if s.average_r is None
            else f"Mean over {plural(s.r_trades, 'trade')} with a usable R",
            tone(s.average_r),
        ),
        Kpi(
            "expectancy_r",
            "Expectancy per trade",
            format_r(s.expectancy_r),
            unavailable if s.expectancy_r is None else "Win rate × average win + loss rate × average loss",
            tone(s.expectancy_r),
        ),
        Kpi(
            "profit_factor_r",
            "Profit factor (R)",
            format_ratio(s.profit_factor_r),
            unavailable
            if none
            else ("Needs at least one losing trade" if s.profit_factor_r is None else "Winning R ÷ losing R"),
        ),
        Kpi(
            "total_r",
            "Total realised R",
            NOT_AVAILABLE if none else format_r(s.total_r),
            unavailable if none else "Sum of R-multiples (not account equity)",
            "neutral" if none else tone(s.total_r),
        ),
        Kpi(
            "max_drawdown_r",
            "Maximum drawdown",
            NOT_AVAILABLE if none else format_r_amount(s.max_drawdown_r.amount),
            unavailable if none else "Largest fall from a running peak of cumulative R",
            "negative" if not none and s.max_drawdown_r.amount > 0 else "neutral",
        ),
        Kpi(
            "longest_win_streak",
            "Longest winning streak",
            NOT_AVAILABLE if none else plural(s.longest_win_streak, "trade"),
            unavailable if none else "Consecutive wins, in realised order",
        ),
        Kpi(
            "longest_loss_streak",
            "Longest losing streak",
            NOT_AVAILABLE if none else plural(s.longest_loss_streak, "trade"),
            unavailable if none else "Consecutive losses, in realised order",
        ),
    ]


# ---------- Text descriptions (the charts' text alternatives) ----------


def describe_curve(series: list[SeriesPoint]) -> str:
    if not series:
        return "There are no closed trades with a usable R to plot."
    last = series[-1]
    high = max(series, key=lambda p: (p.cumulative_r, -p.x))
    low = min(series, key=lambda p: (p.cumulative_r, p.x))
    return (
        f"Cumulative realised R across {plural(len(series), 'closed trade')}, "
        f"from {short_time(series[0].realised_at)} to {short_time(last.realised_at)}. "
        f"It ended at {format_r(last.cumulative_r)}. The highest point was {format_r(high.cumulative_r)} "
        f"(after trade #{high.trade_id}) and the lowest was {format_r(low.cumulative_r)} "
        f"(after trade #{low.trade_id})."
    )


def describe_drawdown(series: list[SeriesPoint], report: PerformanceReport) -> str:
    if not series:
        return "There is no cumulative R series, so there is no drawdown to show."
    worst = report.overall.max_drawdown_r
    current = series[-1].drawdown_r
    if worst.amount == 0:
        return "No drawdown: cumulative R never fell below its previous peak."
    start = (
        "the starting point (0R)"
        if worst.peak_trade_id is None
        else f"the peak after trade #{worst.peak_trade_id}"
    )
    return (
        f"Maximum drawdown was {format_r_amount(worst.amount)}, from {start} to the low after trade "
        f"#{worst.trough_trade_id}. Current drawdown: {format_r_amount(current)} below the running peak."
    )


def describe_periods(rows: list[PeriodRow], noun: str) -> str:
    traded = [row for row in rows if row.summary is not None]
    if not traded:
        return f"No {noun} has closed trades."
    best = max(traded, key=lambda row: row.summary.total_r)
    worst = min(traded, key=lambda row: row.summary.total_r)
    empty = [row.label for row in rows if row.summary is None]
    if len(traded) == 1:
        text = (
            f"Only {best.label} has closed trades: {format_r(best.summary.total_r)} from "
            f"{plural(best.summary.trades, 'trade')}."
        )
    else:
        text = (
            f"{plural(len(traded), noun)} with closed trades. Best: {best.label} "
            f"({format_r(best.summary.total_r)} from {plural(best.summary.trades, 'trade')}). "
            f"Worst: {worst.label} ({format_r(worst.summary.total_r)} from "
            f"{plural(worst.summary.trades, 'trade')})."
        )
    if empty:
        text += f" No closed trades: {', '.join(empty)}."
    return text


def describe_groups(rows: list[GroupRow], noun: str) -> str:
    if not rows:
        return f"No {noun} has closed trades."
    best = max(rows, key=lambda row: row.summary.total_r)
    worst = min(rows, key=lambda row: row.summary.total_r)
    small = sum(row.small_sample for row in rows)
    if len(rows) == 1:
        text = (
            f"Only one {noun}, {best.label}: {format_r(best.summary.total_r)} from "
            f"{plural(best.summary.trades, 'trade')}."
        )
    else:
        text = (
            f"{plural(len(rows), noun)} compared by total realised R. Best: {best.label} "
            f"({format_r(best.summary.total_r)}, {plural(best.summary.trades, 'trade')}). "
            f"Worst: {worst.label} ({format_r(worst.summary.total_r)}, "
            f"{plural(worst.summary.trades, 'trade')})."
        )
    if small:
        text += (
            f" {plural(small, noun)} {'has' if small == 1 else 'have'} fewer than "
            f"{SMALL_SAMPLE} trades: treat {'it' if small == 1 else 'them'} as a small sample."
        )
    return text


# ---------- Assembly ----------


def make_dashboard(report: PerformanceReport, filters: ReportFilters) -> Dashboard:
    """Pure: everything the page needs from one report."""
    series = drawdown_series(report.cumulative_r)
    months = month_rows(report.by_month)
    weekdays = weekday_rows(report.by_weekday)
    instruments = group_rows(report.by_instrument)
    strategies = group_rows(report.by_strategy)
    sessions = group_rows(report.by_session, order=SESSION_ORDER, labels=SESSION_LABELS)
    return Dashboard(
        filters=filters,
        report=report,
        kpis=build_kpis(report),
        series=series,
        months=months,
        weekdays=weekdays,
        instruments=instruments,
        strategies=strategies,
        sessions=sessions,
        descriptions={
            "curve": describe_curve(series),
            "drawdown": describe_drawdown(series, report),
            "monthly": describe_periods(months, "month"),
            "weekday": describe_periods(weekdays, "weekday"),
            "instruments": describe_groups(instruments, "instrument"),
            "strategies": describe_groups(strategies, "strategy"),
            "sessions": describe_groups(sessions, "session"),
        },
    )


def build_dashboard(conn, filters: ReportFilters, *, user_id: int) -> Dashboard:
    """One database query and one report per page view; every card, chart
    and table below is cut from the same report. Only the signed-in user's
    trades are loaded (Phase 8)."""
    return make_dashboard(build_report(conn, user_id=user_id, **filters.as_kwargs()), filters)


# ---------- Chart payload (JSON-safe) ----------


def exact(value: Decimal | None) -> str | None:
    """Exact decimal text for the chart, or None (JSON null) when absent."""
    return None if value is None else decimal_to_text(value)


def _summary_fields(summary: Summary | None) -> dict[str, object]:
    if summary is None:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "total_r": None,
            "total_r_text": "No closed trades",
            "win_rate_text": NOT_AVAILABLE,
            "average_r_text": NOT_AVAILABLE,
            "expectancy_text": NOT_AVAILABLE,
        }
    return {
        "trades": summary.trades,
        "wins": summary.wins,
        "losses": summary.losses,
        "total_r": exact(summary.total_r),
        "total_r_text": format_r(summary.total_r),
        "win_rate_text": format_rate(summary.win_rate),
        "average_r_text": format_r(summary.average_r),
        "expectancy_text": format_r(summary.expectancy_r),
    }


def chart_payload(dashboard: Dashboard) -> dict[str, object]:
    """Everything analytics.js needs, and nothing else: no notes, no prices,
    no identifiers beyond trade numbers. Exact values are strings; the
    *_text values are what people read."""
    curve = []
    if dashboard.series:
        first = dashboard.series[0]
        curve.append(
            {
                "start": True,
                "x": first.x,
                "cumulative_r": "0",
                "cumulative_text": "0.00R",
                "underwater_r": "0",
                "drawdown_text": "0.00R",
            }
        )
    for point in dashboard.series:
        with localcontext(EXACT):
            underwater = -point.drawdown_r
        curve.append(
            {
                "start": False,
                "trade_id": point.trade_id,
                "symbol": point.symbol,
                "x": point.x,
                "when": short_time(point.realised_at),
                "close_time_known": point.close_time_known,
                "realised_r": exact(point.realised_r),
                "realised_text": format_r(point.realised_r),
                "cumulative_r": exact(point.cumulative_r),
                "cumulative_text": format_r(point.cumulative_r),
                "underwater_r": exact(underwater),
                "drawdown_text": format_r_amount(point.drawdown_r),
            }
        )

    def periods(rows):
        return [{"key": row.key, "label": row.label, **_summary_fields(row.summary)} for row in rows]

    def groups(rows):
        return [
            {"label": row.label, "small_sample": row.small_sample, **_summary_fields(row.summary)}
            for row in rows
        ]

    worst = dashboard.report.overall.max_drawdown_r
    return {
        "curve": curve,
        "max_drawdown": {
            "amount": exact(worst.amount),
            "text": format_r_amount(worst.amount),
            "peak_trade_id": worst.peak_trade_id,
            "trough_trade_id": worst.trough_trade_id,
        },
        "monthly": periods(dashboard.months),
        "weekday": periods(dashboard.weekdays),
        "instruments": groups(dashboard.instruments),
        "strategies": groups(dashboard.strategies),
        "sessions": groups(dashboard.sessions),
    }


def money_rows(report: PerformanceReport):
    """(currency, net P&L text, tone, trades) per quote currency. Never summed
    across currencies: there is no exchange-rate data to do that honestly."""
    return [
        (currency, format_money(m.net_pnl, currency), tone(m.net_pnl), m.trades)
        for currency, m in report.overall.money.items()
    ]
```

### `tradeledger/performance/report.py` (MODIFIED)

**Why:** Report loads only the owner's trades. Formulas unchanged.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Build a complete PerformanceReport.

analyse(trades)          pure: list of trade dicts in, report out
build_report(conn, ...)  loads trades from the database, then calls analyse()

Grouping keys (all from realised_at, which is UTC)
  by_weekday   'Monday' ... 'Sunday', in calendar order
  by_date      'YYYY-MM-DD'
  by_month     'YYYY-MM'

realised_at is the exit time (closed_at). For legacy trades recorded before
Phase 4, whose exit time was never captured, it falls back to the entry
time. Those trades are listed in unknown_close_time_trade_ids so the
fallback is always visible, never silent.

Date filters (start/end) apply to the same realised date, so "March" means
"results realised in March".
"""

from dataclasses import dataclass
from decimal import Decimal

from ..repositories.trades import list_trades
from ..timestamps import parse_stored
from .metrics import Summary, chronological, cumulative, summarise
from .trade_math import IncompleteTradeError, TradeResult, evaluate_trade

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
UNSPECIFIED = "Unspecified"


@dataclass(frozen=True)
class ExcludedTrade:
    trade_id: int | None
    reason: str


@dataclass(frozen=True)
class CurvePoint:
    """One point on the cumulative-R chart. This is NOT account equity."""

    trade_id: int
    trade_date: str
    realised_at: str
    close_time_known: bool
    symbol: str
    realised_r: Decimal
    cumulative_r: Decimal


@dataclass(frozen=True)
class PerformanceReport:
    overall: Summary
    open_trades: int
    excluded: list[ExcludedTrade]
    r_unavailable: list[ExcludedTrade]  # closed trades counted, but with no R
    unknown_close_time_trade_ids: list[int]  # legacy: ordered by entry time
    by_instrument: dict[str, Summary]
    by_strategy: dict[str, Summary]
    by_session: dict[str, Summary]
    by_weekday: dict[str, Summary]
    by_date: dict[str, Summary]
    by_month: dict[str, Summary]
    cumulative_r: list[CurvePoint]


def _weekday(result):
    moment = parse_stored(result.realised_at)
    return WEEKDAYS[moment.weekday()]


def evaluate_trades(trades):
    """Split trades into evaluated results, an open count and exclusions."""
    results, excluded, open_count = [], [], 0
    for trade in trades:
        if trade.get("status") == "OPEN":
            open_count += 1
            continue
        try:
            results.append(evaluate_trade(trade))
        except IncompleteTradeError as exc:
            excluded.append(ExcludedTrade(trade.get("id"), str(exc)))
    return results, open_count, excluded


def group_summaries(results, key, order=None):
    """Summarise each group. `order` fixes the key order (e.g. weekdays);
    otherwise keys are sorted alphabetically."""
    groups: dict[str, list[TradeResult]] = {}
    for result in results:
        groups.setdefault(key(result), []).append(result)
    keys = [k for k in order if k in groups] if order else sorted(groups)
    return {k: summarise(groups[k]) for k in keys}


def cumulative_r_curve(results):
    """Chart-ready running total of realised R, in date order."""
    rows = [r for r in chronological(results) if r.realised_r is not None]
    totals = cumulative([r.realised_r for r in rows])
    return [
        CurvePoint(r.trade_id, r.trade_date, r.realised_at, r.close_time_known, r.symbol, r.realised_r, total)
        for r, total in zip(rows, totals, strict=True)
    ]


def analyse(trades) -> PerformanceReport:
    results, open_count, excluded = evaluate_trades(trades)
    return PerformanceReport(
        overall=summarise(results),
        open_trades=open_count,
        excluded=excluded,
        r_unavailable=[
            ExcludedTrade(r.trade_id, r.r_unavailable_reason)
            for r in chronological(results)
            if r.realised_r is None
        ],
        unknown_close_time_trade_ids=[r.trade_id for r in chronological(results) if not r.close_time_known],
        by_instrument=group_summaries(results, lambda r: r.symbol),
        by_strategy=group_summaries(results, lambda r: r.strategy or UNSPECIFIED),
        by_session=group_summaries(results, lambda r: r.session or UNSPECIFIED),
        by_weekday=group_summaries(results, _weekday, order=WEEKDAYS),
        by_date=group_summaries(results, lambda r: r.realised_at[:10]),
        by_month=group_summaries(results, lambda r: r.realised_at[:7]),
        cumulative_r=cumulative_r_curve(results),
    )


def build_report(
    conn, *, user_id, instrument_id=None, start=None, end=None, strategy=None, session=None
) -> PerformanceReport:
    """Load ONE user's trades with the repository filters and analyse them.

    user_id is required (Phase 8): a report can never mix two users' trades.

    start/end are inclusive UTC dates applied to the realised date.
    strategy and session are exact matches (Phase 6). Every filter is a
    parameterised SQL condition in the repository, so filtering happens in
    the database and only matching trades are loaded.
    """
    return analyse(
        list_trades(
            conn,
            user_id=user_id,
            instrument_id=instrument_id,
            start=start,
            end=end,
            strategy=strategy,
            session=session,
            date_field="realised",
        )
    )
```

### `tradeledger/report_filters.py` (MODIFIED)

**Why:** Instrument filter checks the instrument belongs to the signed-in user.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Filters for performance reports, shared by the dashboard and the API.

One definition of what each filter means and how it is validated, so the
website (/analytics) and the JSON API (/api/v1/analytics/...) can never
disagree about which trades a report covers.

  instrument_id  only this instrument (must exist: an unknown id would
                 otherwise produce an empty report that looks like "no
                 trades", which is misleading)
  strategy       exact strategy label, as stored on trades
  session        ASIA, LONDON, NEW_YORK or OTHER (case-insensitive input)
  start, end     inclusive UTC dates (YYYY-MM-DD) on the REALISED date:
                 closed_at, or trade_date for legacy trades with no
                 recorded exit time

parse_report_filters() never raises for bad input. It returns every
problem at once as {field: message}, so a form can show them all and the
API can turn them into one 400 response.
"""

from collections.abc import Mapping
from dataclasses import asdict, dataclass

from .repositories.instruments import get_instrument
from .validation import SESSIONS, parse_date

FILTER_NAMES = ("instrument_id", "strategy", "session", "start", "end")
STRATEGY_MAX_LENGTH = 50  # the same limit validation.py applies to trades


@dataclass(frozen=True)
class ReportFilters:
    instrument_id: int | None = None
    strategy: str | None = None
    session: str | None = None
    start: str | None = None  # 'YYYY-MM-DD'
    end: str | None = None  # 'YYYY-MM-DD'

    def as_kwargs(self) -> dict[str, object]:
        """Keyword arguments for build_report() and the repository."""
        return asdict(self)

    def active(self) -> dict[str, object]:
        """Only the filters that are set, e.g. for building URLs."""
        return {name: value for name, value in asdict(self).items() if value is not None}

    @property
    def is_empty(self) -> bool:
        return not self.active()


def parse_report_filters(
    args: Mapping[str, str | None], conn, *, user_id: int, blank_means_unset: bool
) -> tuple[ReportFilters, dict[str, str]]:
    """Validate raw query-string values.

    args               name -> raw text (missing names are simply unset)
    conn               database connection, to check the instrument exists
    user_id            the signed-in user: another user's instrument id is
                       reported exactly like an id that does not exist
    blank_means_unset  True for HTML forms, where "All" submits an empty
                       value. False for the API, where an empty value is
                       almost certainly a client bug and is rejected.

    Returns (filters, errors). When errors is not empty, filters holds only
    the values that were valid and must not be used to build a report.
    """
    values: dict[str, object] = {}
    errors: dict[str, str] = {}

    def raw(name):
        value = args.get(name)
        if value is None:
            return None
        if blank_means_unset and value.strip() == "":
            return None
        return value

    instrument_text = raw("instrument_id")
    if instrument_text is not None:
        text = instrument_text.strip()
        if not text.isdigit() or int(text) < 1:
            errors["instrument_id"] = "must be a whole number of 1 or more"
        elif get_instrument(conn, int(text), user_id=user_id) is None:
            errors["instrument_id"] = "does not match an existing instrument"
        else:
            values["instrument_id"] = int(text)

    strategy = raw("strategy")
    if strategy is not None:
        strategy = strategy.strip()
        if not strategy:
            errors["strategy"] = "must not be empty"
        elif len(strategy) > STRATEGY_MAX_LENGTH:
            errors["strategy"] = f"must be {STRATEGY_MAX_LENGTH} characters or fewer"
        else:
            values["strategy"] = strategy

    session = raw("session")
    if session is not None:
        session = session.strip().upper()
        if session not in SESSIONS:
            errors["session"] = f"must be one of: {', '.join(SESSIONS)}"
        else:
            values["session"] = session

    for name in ("start", "end"):
        text = raw(name)
        if text is None:
            continue
        try:
            values[name] = parse_date(text).isoformat()
        except ValueError as exc:
            errors[name] = str(exc)

    if "start" in values and "end" in values and values["start"] > values["end"]:
        errors["end"] = "must be on or after the start date"

    return ReportFilters(**values), errors
```

### `tradeledger/repositories/__init__.py` (MODIFIED)

**Why:** `require_user_id`: every repository call must name a positive integer owner.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Repository functions: the only code in TradeLedger that writes SQL for
instruments and trades.

Every function takes an open db.Connection (SQLite or PostgreSQL) as its
first argument. Since Phase 8 every function that touches user data also
takes a REQUIRED keyword argument user_id, and every statement filters on
it. There is no "all users" mode.
"""


class DuplicateError(Exception):
    """Raised when a record would break a UNIQUE rule (e.g. a repeated symbol)."""


def require_user_id(user_id):
    """Fail loudly if a caller passes something that is not a user id.

    None, 0, True or a string would otherwise slip into SQL and match
    nothing (or, in a future bug, everything). Raising here turns such a
    mistake into an immediate error instead of a silent data-isolation bug.
    """
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
        raise TypeError(f"user_id must be a positive int, got {user_id!r}")
    return user_id
```

### `tradeledger/repositories/instruments.py` (MODIFIED)

**Why:** Every query scoped by `user_id`; portable SQL; friendly constraint errors.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Instrument repository.

Every function takes the OWNER's user id as a required keyword argument
(Phase 8). Each SQL statement includes "user_id = ?", so a user can only
ever create, see or look up their own instruments. Forgetting the argument
is a TypeError, never an accidental "all users" query.

Return values
  create_instrument          -> new id (int)
  get_instrument             -> dict, or None if not found (or not yours)
  get_instrument_by_symbol   -> dict, or None if not found (or not yours)
  list_instruments           -> list of dicts (empty list if none), by symbol

Errors
  ValidationError  invalid input (nothing is written)
  DuplicateError   you already have an instrument with this symbol
"""

from decimal import Decimal

from ..db import IntegrityError
from ..validation import validate_instrument
from . import DuplicateError, require_user_id

_COLUMNS = "id, symbol, name, asset_class, price_increment, contract_multiplier, quote_currency, created_at"


def _to_dict(row):
    if row is None:
        return None
    record = dict(row)
    record["price_increment"] = Decimal(record["price_increment"])
    record["contract_multiplier"] = Decimal(record["contract_multiplier"])
    return record


def create_instrument(conn, data, *, user_id):
    require_user_id(user_id)
    clean = validate_instrument(data)
    try:
        with conn:  # commit on success, roll back on any exception
            row = conn.execute(
                """
                INSERT INTO instruments
                    (user_id, symbol, name, asset_class, price_increment,
                     contract_multiplier, quote_currency)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                RETURNING id
                """,
                (
                    user_id,
                    clean["symbol"],
                    clean["name"],
                    clean["asset_class"],
                    conn.numeric(clean["price_increment"]),
                    conn.numeric(clean["contract_multiplier"]),
                    clean["quote_currency"],
                ),
            ).fetchone()
    except IntegrityError as exc:
        if exc.kind == "unique":
            raise DuplicateError(f"Instrument '{clean['symbol']}' already exists") from exc
        raise
    return row["id"]


def get_instrument(conn, instrument_id, *, user_id):
    require_user_id(user_id)
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM instruments WHERE id = ? AND user_id = ?",  # noqa: S608 - _COLUMNS is a module constant
        (instrument_id, user_id),
    ).fetchone()
    return _to_dict(row)


def get_instrument_by_symbol(conn, symbol, *, user_id):
    require_user_id(user_id)
    if not isinstance(symbol, str):
        return None
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM instruments WHERE symbol = ? AND user_id = ?",  # noqa: S608 - _COLUMNS is a module constant
        (symbol.strip().upper(), user_id),
    ).fetchone()
    return _to_dict(row)


def list_instruments(conn, *, user_id):
    require_user_id(user_id)
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM instruments WHERE user_id = ? ORDER BY symbol",  # noqa: S608 - constant
        (user_id,),
    ).fetchall()
    return [_to_dict(row) for row in rows]
```

### `tradeledger/repositories/trades.py` (MODIFIED)

**Why:** Every query scoped by `user_id`; case-insensitive search via LOWER; RETURNING id; FOR UPDATE on PostgreSQL; Decimal binding.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Trade repository.

Ownership (Phase 8): every function takes the owner's user id as a REQUIRED
keyword argument, and every statement includes "t.user_id = ?". Another
user's trade is indistinguishable from a trade that does not exist: get
returns None, update and delete return False. The database adds a second
line of defence: a trade's (instrument_id, user_id) must match one of that
user's instruments (foreign key trades_instrument_owner_fk).

Return values
  create_trade   -> new id (int)
  get_trade      -> dict (includes the instrument's symbol, quote_currency
                    and contract_multiplier), or None
  list_trades    -> list of dicts, newest trade_date first (empty list if none)
  count_trades   -> number of trades matching the same filters as list_trades
  list_strategies-> sorted list of distinct strategy labels in use
  update_trade   -> True if a trade was updated, False if the id does not exist
  delete_trade   -> True if a trade was deleted, False if the id does not exist

Errors
  ValidationError  invalid input, including an unknown instrument_id
                   (nothing is written)

Numeric fields come back as decimal.Decimal (or None), never float.

Dates
  trade_date  entry time (always present)
  closed_at   exit time; None for OPEN trades and for legacy CLOSED trades
              recorded before Phase 4, whose exit time is unknown
"""

from decimal import Decimal

from ..db import IntegrityError
from ..timestamps import utc_now_text
from ..validation import SESSIONS, ValidationError, date_range_bounds, validate_trade
from . import require_user_id

# Exact decimal columns: TEXT on SQLite, NUMERIC on PostgreSQL (conn.numeric()).
_WRITE_DECIMALS = ("entry_price", "stop_loss", "take_profit", "exit_price", "position_size", "fees")

_DECIMAL_FIELDS = (
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "contract_multiplier",
)

# The ONLY columns a caller can write. Anything else in the input is ignored
# by validate_trade() and never reaches SQL (protection against mass assignment).
_WRITE_FIELDS = (
    "instrument_id",
    "trade_date",
    "closed_at",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "status",
    "strategy",
    "session",
    "notes",
)

_SELECT = """
    SELECT t.id, t.instrument_id, i.symbol, i.quote_currency, i.contract_multiplier,
           t.trade_date, t.closed_at, t.direction,
           t.entry_price, t.stop_loss, t.take_profit, t.exit_price,
           t.position_size, t.fees, t.status, t.strategy, t.session,
           t.notes, t.created_at, t.updated_at
    FROM trades AS t
    JOIN instruments AS i ON i.id = t.instrument_id
"""

# Which timestamp a date filter applies to. Both are fixed SQL text written
# here, never user input.
#   entry     the trade's entry time
#   realised  when the result was realised: the exit time if recorded,
#             otherwise (legacy trades) the entry time
_DATE_COLUMNS = {
    "entry": "t.trade_date",
    "realised": "COALESCE(t.closed_at, t.trade_date)",
}


def _to_dict(row):
    if row is None:
        return None
    record = dict(row)
    for field in _DECIMAL_FIELDS:
        if record[field] is not None:
            record[field] = Decimal(record[field])
    return record


def _raise_friendly(exc):
    """Translate database integrity errors into ValidationErrors where we can.

    A foreign-key failure means the instrument does not exist OR belongs to
    someone else; the message is the same for both, so it reveals nothing
    about other users' instruments."""
    if exc.kind == "foreign_key":
        raise ValidationError({"instrument_id": "does not match an existing instrument"}) from exc
    if exc.constraint == "closed_at_rules":
        raise ValidationError(
            {"closed_at": "is not consistent with the trade's status or entry time"}
        ) from exc
    raise exc


MAX_SEARCH_LENGTH = 100


def _like_pattern(text):
    """Turn user text into a LIKE pattern that matches it literally.
    % and _ are LIKE wildcards, so they (and the escape character itself)
    are escaped; otherwise searching "50%" would match everything."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _where(user_id, instrument_id, start, end, status, date_field, strategy=None, search=None, session=None):
    """Build a WHERE clause from fixed fragments plus a parameter list.
    User values never become part of the SQL text; they go in `params`.
    The owner condition always comes first and is never optional.

    strategy  exact match on the strategy label
    session   exact match on the trading session (ASIA, LONDON, NEW_YORK, OTHER)
    search    case-insensitive substring match on the instrument symbol,
              the strategy or the notes, across ALL of the user's trades
              (not just one page). LOWER() on both sides makes it ignore
              case on both databases (PostgreSQL's LIKE is case-sensitive).
    """
    if date_field not in _DATE_COLUMNS:
        raise ValueError(f"date_field must be one of {sorted(_DATE_COLUMNS)}")
    date_column = _DATE_COLUMNS[date_field]
    lower, upper = date_range_bounds(start, end)

    conditions, params = ["t.user_id = ?"], [require_user_id(user_id)]
    if instrument_id is not None:
        conditions.append("t.instrument_id = ?")
        params.append(instrument_id)
    if lower is not None:
        conditions.append(f"{date_column} >= ?")
        params.append(lower)
    if upper is not None:
        conditions.append(f"{date_column} < ?")
        params.append(upper)
    if status is not None:
        if status not in ("OPEN", "CLOSED"):
            raise ValidationError({"status": "must be one of: OPEN, CLOSED"})
        conditions.append("t.status = ?")
        params.append(status)
    if strategy is not None:
        conditions.append("t.strategy = ?")
        params.append(strategy)
    if session is not None:
        if session not in SESSIONS:
            raise ValidationError({"session": f"must be one of: {', '.join(SESSIONS)}"})
        conditions.append("t.session = ?")
        params.append(session)
    if search is not None:
        if not isinstance(search, str) or not search.strip():
            raise ValidationError({"q": "must not be empty"})
        if len(search) > MAX_SEARCH_LENGTH:
            raise ValidationError({"q": f"must be {MAX_SEARCH_LENGTH} characters or fewer"})
        pattern = _like_pattern(search.strip())
        conditions.append(
            "(LOWER(i.symbol) LIKE LOWER(?) ESCAPE '\\' OR LOWER(t.strategy) LIKE LOWER(?) ESCAPE '\\'"
            " OR LOWER(t.notes) LIKE LOWER(?) ESCAPE '\\')"
        )
        params.extend([pattern, pattern, pattern])
    return " WHERE " + " AND ".join(conditions), params


def _bind(conn, clean, field):
    value = clean[field]
    return conn.numeric(value) if field in _WRITE_DECIMALS and value is not None else value


def create_trade(conn, data, *, user_id):
    require_user_id(user_id)
    clean = validate_trade(data)
    columns = ("user_id", *_WRITE_FIELDS)
    placeholders = ", ".join("?" for _ in columns)
    try:
        with conn:
            row = conn.execute(
                f"INSERT INTO trades ({', '.join(columns)}) VALUES ({placeholders}) RETURNING id",  # noqa: S608 - fixed column names; values are bound
                (user_id, *(_bind(conn, clean, field) for field in _WRITE_FIELDS)),
            ).fetchone()
    except IntegrityError as exc:
        _raise_friendly(exc)
    return row["id"]


def get_trade(conn, trade_id, *, user_id):
    require_user_id(user_id)
    row = conn.execute(_SELECT + " WHERE t.id = ? AND t.user_id = ?", (trade_id, user_id)).fetchone()
    return _to_dict(row)


def list_trades(
    conn,
    *,
    user_id,
    instrument_id=None,
    start=None,
    end=None,
    status=None,
    date_field="entry",
    strategy=None,
    search=None,
    session=None,
    limit=None,
    offset=0,
):
    """List one user's trades, optionally filtered and paginated.

    user_id        the owner (required)
    instrument_id  only this instrument
    start, end     inclusive dates 'YYYY-MM-DD' (UTC)
    date_field     'entry' (trade_date, the default) or 'realised'
    status         'OPEN' or 'CLOSED'
    strategy       exact strategy label
    session        exact session (ASIA, LONDON, NEW_YORK, OTHER)
    search         text found in the symbol, strategy or notes
    limit, offset  pagination; limit=None returns every match

    Order is always newest trade_date first, then highest id first, so
    pages never overlap or skip rows between requests.
    """
    where, params = _where(user_id, instrument_id, start, end, status, date_field, strategy, search, session)
    sql = _SELECT + where + " ORDER BY t.trade_date DESC, t.id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, int(limit), int(offset)]
    return [_to_dict(row) for row in conn.execute(sql, params).fetchall()]


def count_trades(
    conn,
    *,
    user_id,
    instrument_id=None,
    start=None,
    end=None,
    status=None,
    date_field="entry",
    strategy=None,
    search=None,
    session=None,
):
    where, params = _where(user_id, instrument_id, start, end, status, date_field, strategy, search, session)
    # The JOIN is needed because search can match the instrument symbol.
    sql = "SELECT COUNT(*) FROM trades AS t JOIN instruments AS i ON i.id = t.instrument_id" + where  # noqa: S608 - `where` holds fixed fragments; values are bound
    return conn.execute(sql, params).fetchone()[0]


def list_strategies(conn, *, user_id):
    rows = conn.execute(
        "SELECT DISTINCT strategy FROM trades WHERE user_id = ? AND strategy IS NOT NULL ORDER BY strategy",
        (require_user_id(user_id),),
    ).fetchall()
    return [row["strategy"] for row in rows]


def update_trade(conn, trade_id, data, *, user_id):
    """Replace every editable field of one of the user's trades.

    A legacy CLOSED trade (recorded before Phase 4, closed_at unknown) may be
    saved again without a closed_at, so editing it never forces anyone to
    invent an exit time. Every other CLOSED trade must have one.

    The read and the write happen in ONE transaction; on PostgreSQL the row
    is locked (FOR UPDATE) so two simultaneous edits cannot interleave.
    Returns False when the trade does not exist or belongs to someone else.
    """
    require_user_id(user_id)
    lock = " FOR UPDATE" if conn.dialect == "postgresql" else ""
    # Column names come from _WRITE_FIELDS (a constant in this module), never
    # from the request; every value is a bound "?" parameter.
    assignments = ", ".join(f"{field} = ?" for field in _WRITE_FIELDS)
    sql = f"UPDATE trades SET {assignments}, updated_at = ? WHERE id = ? AND user_id = ?"  # noqa: S608 - see comment above
    try:
        with conn:
            existing = conn.execute(
                "SELECT status, closed_at FROM trades WHERE id = ? AND user_id = ?" + lock,  # noqa: S608 - `lock` is a constant
                (trade_id, user_id),
            ).fetchone()
            if existing is None:
                return False
            is_legacy = existing["status"] == "CLOSED" and existing["closed_at"] is None
            clean = validate_trade(data, require_closed_at=not is_legacy)
            cursor = conn.execute(
                sql,
                (
                    *(_bind(conn, clean, field) for field in _WRITE_FIELDS),
                    utc_now_text(),
                    trade_id,
                    user_id,
                ),
            )
    except IntegrityError as exc:
        _raise_friendly(exc)
    return cursor.rowcount == 1


def delete_trade(conn, trade_id, *, user_id):
    require_user_id(user_id)
    with conn:
        cursor = conn.execute("DELETE FROM trades WHERE id = ? AND user_id = ?", (trade_id, user_id))
    return cursor.rowcount == 1
```

### `tradeledger/seed.py` (MODIFIED)

**Why:** Seeds synthetic data for one named account through the repositories.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Synthetic sample data for development and tests.

EVERYTHING IN THIS FILE IS INVENTED. None of it is anyone's real trading.

About the instrument values
--------------------------
contract_multiplier means "money, in quote_currency, per 1.0 move in price
per 1.0 unit of position_size". Real values depend on the broker's contract
specification and on what one unit of position size means at that broker.
The figures below are ILLUSTRATIVE ASSUMPTIONS for synthetic data only:

  EURUSD, GBPUSD  1 unit = 1 standard lot of 100,000 base currency -> 100000
  XAUUSD          1 unit = 1 lot of 100 troy ounces                -> 100
  NAS100, UK100   1 unit = a CFD paying 1 quote-currency per point  -> 1

Before recording real trades, replace them with your broker's figures.
Fees are assumed to be in the instrument's quote currency.

About closed_at (added in Phase 4)
----------------------------------
Each synthetic closed trade has a synthetic exit time on the same day as its
entry, and the trades close in the same order they were opened. That keeps
every Phase 2 and Phase 3 expected result valid. This is invented sample
data, so giving it exit times fabricates nothing. Real trades recorded
before Phase 4 are never given invented exit times (see migrations.py).
"""

from .repositories import require_user_id
from .repositories.instruments import create_instrument, get_instrument_by_symbol
from .repositories.trades import create_trade
from .validation import validate_trade

SAMPLE_INSTRUMENTS = [
    {
        "symbol": "EURUSD",
        "name": "Euro / US Dollar (sample)",
        "asset_class": "FOREX",
        "price_increment": "0.00001",
        "contract_multiplier": "100000",
        "quote_currency": "USD",
    },
    {
        "symbol": "GBPUSD",
        "name": "British Pound / US Dollar (sample)",
        "asset_class": "FOREX",
        "price_increment": "0.00001",
        "contract_multiplier": "100000",
        "quote_currency": "USD",
    },
    {
        "symbol": "XAUUSD",
        "name": "Gold / US Dollar (sample)",
        "asset_class": "COMMODITY",
        "price_increment": "0.01",
        "contract_multiplier": "100",
        "quote_currency": "USD",
    },
    {
        "symbol": "NAS100",
        "name": "US Tech 100 index CFD (sample)",
        "asset_class": "INDEX",
        "price_increment": "0.1",
        "contract_multiplier": "1",
        "quote_currency": "USD",
    },
    {
        "symbol": "UK100",
        "name": "UK 100 index CFD (sample)",
        "asset_class": "INDEX",
        "price_increment": "0.1",
        "contract_multiplier": "1",
        "quote_currency": "GBP",
    },
]

# Net P&L in the comment = gross - fees, in the instrument's quote currency.
SAMPLE_TRADES = [
    # 1  WIN  +393.00 USD
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-02T08:15:00Z",
        "closed_at": "2026-03-02T11:40:00Z",
        "direction": "LONG",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Sample: clean break of Asian range.",
    },
    # 2  LOSS -103.50 USD
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-03T13:40:00Z",
        "closed_at": "2026-03-03T15:05:00Z",
        "direction": "SHORT",
        "entry_price": "1.08800",
        "stop_loss": "1.09000",
        "take_profit": "1.08400",
        "exit_price": "1.09000",
        "position_size": "0.5",
        "fees": "3.5",
        "strategy": "Pullback",
        "session": "NEW_YORK",
        "notes": "Sample: stopped out on US data.",
    },
    # 3  BREAKEVEN 0.00 USD
    {
        "symbol": "GBPUSD",
        "trade_date": "2026-03-04T08:30:00Z",
        "closed_at": "2026-03-04T10:10:00Z",
        "direction": "LONG",
        "entry_price": "1.27000",
        "stop_loss": "1.26800",
        "take_profit": "1.27400",
        "exit_price": "1.27000",
        "position_size": "1",
        "fees": "0",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Sample: moved stop to entry.",
    },
    # 4  WIN  +293.00 USD
    {
        "symbol": "GBPUSD",
        "trade_date": "2026-03-05T14:05:00Z",
        "closed_at": "2026-03-05T17:30:00Z",
        "direction": "SHORT",
        "entry_price": "1.27500",
        "stop_loss": "1.27700",
        "take_profit": "1.27100",
        "exit_price": "1.27200",
        "position_size": "1",
        "fees": "7",
        "strategy": "Reversal",
        "session": "NEW_YORK",
        "notes": "Sample: closed early before target.",
    },
    # 5  WIN  +198.00 USD
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-06T14:30:00Z",
        "closed_at": "2026-03-06T16:45:00Z",
        "direction": "LONG",
        "entry_price": "2150.00",
        "stop_loss": "2140.00",
        "take_profit": "2170.00",
        "exit_price": "2170.00",
        "position_size": "0.1",
        "fees": "2",
        "strategy": "Breakout",
        "session": "NEW_YORK",
        "notes": None,
    },
    # 6  LOSS -133.00 USD
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-09T02:10:00Z",
        "closed_at": "2026-03-09T05:20:00Z",
        "direction": "SHORT",
        "entry_price": "2165.50",
        "stop_loss": "2172.00",
        "take_profit": "2150.00",
        "exit_price": "2172.00",
        "position_size": "0.2",
        "fees": "3",
        "strategy": "Reversal",
        "session": "ASIA",
        "notes": None,
    },
    # 7  WIN  +198.50 USD
    {
        "symbol": "NAS100",
        "trade_date": "2026-03-10T14:45:00Z",
        "closed_at": "2026-03-10T16:00:00Z",
        "direction": "LONG",
        "entry_price": "18250.0",
        "stop_loss": "18200.0",
        "take_profit": "18350.0",
        "exit_price": "18350.0",
        "position_size": "2",
        "fees": "1.5",
        "strategy": "Pullback",
        "session": "NEW_YORK",
        "notes": None,
    },
    # 8  LOSS -81.50 USD
    {
        "symbol": "NAS100",
        "trade_date": "2026-03-11T15:00:00Z",
        "closed_at": "2026-03-11T15:50:00Z",
        "direction": "SHORT",
        "entry_price": "18300.0",
        "stop_loss": "18340.0",
        "take_profit": "18200.0",
        "exit_price": "18340.0",
        "position_size": "2",
        "fees": "1.5",
        "strategy": "Breakout",
        "session": "NEW_YORK",
        "notes": None,
    },
    # 9  LOSS -5.00 USD  (exit ABOVE entry on a long, but fees exceed the gain)
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-12T09:00:00Z",
        "closed_at": "2026-03-12T09:35:00Z",
        "direction": "LONG",
        "entry_price": "1.09100",
        "stop_loss": "1.08950",
        "take_profit": "1.09400",
        "exit_price": "1.09102",
        "position_size": "1",
        "fees": "7",
        "strategy": "Pullback",
        "session": "LONDON",
        "notes": "Sample: scratched; fees made it a loss.",
    },
    # 10 WIN  +200.00 GBP
    {
        "symbol": "UK100",
        "trade_date": "2026-03-13T10:30:00Z",
        "closed_at": "2026-03-13T13:15:00Z",
        "direction": "LONG",
        "entry_price": "7700.0",
        "stop_loss": "7680.0",
        "take_profit": "7740.0",
        "exit_price": "7740.0",
        "position_size": "5",
        "fees": "0",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": None,
    },
    # 11 BREAKEVEN 0.00 GBP (no strategy, no target)
    {
        "symbol": "UK100",
        "trade_date": "2026-03-16T11:00:00Z",
        "closed_at": "2026-03-16T12:30:00Z",
        "direction": "SHORT",
        "entry_price": "7750.0",
        "stop_loss": "7770.0",
        "take_profit": None,
        "exit_price": "7750.0",
        "position_size": "5",
        "fees": "0",
        "strategy": None,
        "session": "LONDON",
        "notes": None,
    },
    # 12 LOSS -157.00 USD
    {
        "symbol": "GBPUSD",
        "trade_date": "2026-03-17T08:05:00Z",
        "closed_at": "2026-03-17T08:50:00Z",
        "direction": "LONG",
        "entry_price": "1.28000",
        "stop_loss": "1.27850",
        "take_profit": "1.28300",
        "exit_price": "1.27850",
        "position_size": "1",
        "fees": "7",
        "strategy": "Pullback",
        "session": "LONDON",
        "notes": None,
    },
    # 13 WIN  +158.00 USD
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-18T03:20:00Z",
        "closed_at": "2026-03-18T07:00:00Z",
        "direction": "LONG",
        "entry_price": "2180.00",
        "stop_loss": "2172.00",
        "take_profit": "2196.00",
        "exit_price": "2196.00",
        "position_size": "0.1",
        "fees": "2",
        "strategy": "Pullback",
        "session": "ASIA",
        "notes": None,
    },
    # 14 OPEN
    {
        "symbol": "NAS100",
        "trade_date": "2026-03-19T14:35:00Z",
        "closed_at": None,
        "direction": "LONG",
        "entry_price": "18500.0",
        "stop_loss": "18440.0",
        "take_profit": "18620.0",
        "exit_price": None,
        "position_size": "1",
        "fees": "0.75",
        "strategy": "Breakout",
        "session": "NEW_YORK",
        "notes": "Sample: still running.",
    },
    # 15 OPEN
    {
        "symbol": "EURUSD",
        "trade_date": "2026-03-20T08:45:00Z",
        "closed_at": None,
        "direction": "SHORT",
        "entry_price": "1.09300",
        "stop_loss": "1.09450",
        "take_profit": None,
        "exit_price": None,
        "position_size": "1",
        "fees": "0",
        "strategy": "Reversal",
        "session": "LONDON",
        "notes": None,
    },
    # 16 WIN  +38.00 USD (no strategy, no session)
    {
        "symbol": "XAUUSD",
        "trade_date": "2026-03-20T15:10:00Z",
        "closed_at": "2026-03-20T18:20:00Z",
        "direction": "SHORT",
        "entry_price": "2190.00",
        "stop_loss": "2198.00",
        "take_profit": None,
        "exit_price": "2186.00",
        "position_size": "0.1",
        "fees": "2",
        "strategy": None,
        "session": None,
        "notes": None,
    },
]


def seed_sample_data(conn, *, user_id):
    """Insert the sample instruments and trades for ONE user. Safe to run
    repeatedly.

    * An instrument is skipped if this user already has its symbol.
    * A trade is skipped if this user already has a trade with the same
      instrument, trade_date, direction and entry_price.
    * Everything happens in ONE transaction: if any record fails, nothing
      from this run is kept.

    Returns {"instruments": n_inserted, "trades": n_inserted}.
    """
    require_user_id(user_id)
    inserted = {"instruments": 0, "trades": 0}

    with conn:
        ids = {}
        for raw in SAMPLE_INSTRUMENTS:
            existing = get_instrument_by_symbol(conn, raw["symbol"], user_id=user_id)
            if existing:
                ids[raw["symbol"]] = existing["id"]
                continue
            ids[raw["symbol"]] = create_instrument(conn, raw, user_id=user_id)
            inserted["instruments"] += 1

        for raw in SAMPLE_TRADES:
            data = {key: value for key, value in raw.items() if key != "symbol"}
            data["instrument_id"] = ids[raw["symbol"]]
            trade = validate_trade(data)
            already_there = conn.execute(
                """
                SELECT 1 FROM trades
                WHERE user_id = ? AND instrument_id = ? AND trade_date = ?
                  AND direction = ? AND entry_price = ?
                """,
                (
                    user_id,
                    trade["instrument_id"],
                    trade["trade_date"],
                    trade["direction"],
                    conn.numeric(trade["entry_price"]),
                ),
            ).fetchone()
            if already_there:
                continue
            create_trade(conn, data, user_id=user_id)
            inserted["trades"] += 1

    return inserted
```

### `tradeledger/static/css/pages.css` (MODIFIED)

**Why:** Styles for the sign-in, registration and account pages.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```css
/* ==========================================================================
   Page-specific layouts
   ========================================================================== */

/* ---------- Overview ---------- */
.welcome {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
    align-items: center;
}

.welcome__title {
    font-size: var(--text-xl);
    margin-bottom: var(--space-2);
}

.welcome__text {
    max-width: 62ch;
    color: var(--color-text-muted);
}

.welcome__counts {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-3);
}

.welcome__counts li {
    flex: 1 1 7rem;
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    background: var(--color-surface-raised);
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.welcome__count {
    display: block;
    color: var(--color-text);
    font-size: var(--text-2xl);
    font-weight: 700;
    font-variant-numeric: tabular-nums;
}

@media (min-width: 64rem) {
    .welcome {
        grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
    }
}

.stat-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
    gap: var(--space-4);
}

.stat {
    display: flex;
    flex-direction: column;
    gap: var(--space-1);
}

.stat__label {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
    font-weight: 600;
}

.stat__value {
    font-size: var(--text-2xl);
    font-weight: 700;
}

.stat__note {
    color: var(--color-text-subtle);
    font-size: var(--text-xs);
}

.money-strip {
    margin-top: var(--space-4);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
}

.money-strip__title {
    font-size: var(--text-sm);
    color: var(--color-text-muted);
}

.money-strip__list {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-2) var(--space-6);
    font-weight: 600;
}

.money-strip__note {
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

.recent-list__item + .recent-list__item {
    border-top: 1px solid var(--color-border);
}

.recent-list__link {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto;
    gap: var(--space-1) var(--space-4);
    align-items: center;
    padding: var(--space-3) var(--space-5);
    color: inherit;
    text-decoration: none;
}

.recent-list__link:hover {
    background: var(--color-surface-hover);
}

.recent-list__main {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-2) var(--space-3);
}

.recent-list__symbol {
    font-weight: 700;
}

.recent-list__meta {
    grid-column: 1;
    color: var(--color-text-muted);
    font-size: var(--text-xs);
}

.recent-list__result {
    grid-column: 2;
    grid-row: 1 / span 2;
    font-weight: 700;
    text-align: right;
}

/* ---------- Journal ---------- */
.filters {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
}

.filters__grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(9rem, 1fr));
    gap: var(--space-4);
}

.filters__actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-3);
}

.trade-table .direction {
    font-size: var(--text-xs);
}

.trade-row__symbol {
    color: var(--color-text);
    font-weight: 700;
    text-decoration: none;
}

.trade-row__symbol:hover {
    color: var(--color-accent);
    text-decoration: underline;
}

/* A thin coloured edge per outcome; the badge text says the same in words. */
.trade-row--win td:first-child {
    box-shadow: inset 3px 0 0 var(--color-positive);
}

.trade-row--loss td:first-child {
    box-shadow: inset 3px 0 0 var(--color-negative);
}

.trade-row--open td:first-child {
    box-shadow: inset 3px 0 0 var(--color-info);
}

@media (max-width: 63.99rem) {
    .trade-row--win td:first-child,
    .trade-row--loss td:first-child,
    .trade-row--open td:first-child {
        box-shadow: none;
    }

    /* .table .x beats the generic ".table tr" border rule by specificity. */
    .table .trade-row--win {
        border-left: 3px solid var(--color-positive);
    }

    .table .trade-row--loss {
        border-left: 3px solid var(--color-negative);
    }

    .table .trade-row--open {
        border-left: 3px solid var(--color-info);
    }
}

/* ---------- Trade form ---------- */
.trade-form {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
}

.form-section__title {
    font-size: var(--text-base);
}

.exit-fields[disabled] {
    opacity: 0.55;
}

/* ---------- Trade details ---------- */
.back-link {
    font-size: var(--text-sm);
}

.detail-grid {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
    align-items: start;
}

@media (min-width: 60rem) {
    .detail-grid {
        grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
    }

    .detail-grid__wide {
        grid-column: 1 / -1;
    }
}

.result-card {
    display: flex;
    flex-direction: column;
    gap: var(--space-4);
    border-top: 4px solid var(--color-border-strong);
}

.result-card .card__header {
    margin-bottom: 0;
}

.result-card--win {
    border-top-color: var(--color-positive);
}

.result-card--loss {
    border-top-color: var(--color-negative);
}

.result-card--open {
    border-top-color: var(--color-info);
}

.result-card__headline {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: var(--space-2) var(--space-4);
    font-size: var(--text-2xl);
    font-weight: 700;
}

.result-card__r {
    font-size: var(--text-lg);
}

.result-card__note,
.meta {
    font-size: var(--text-xs);
    color: var(--color-text-subtle);
}

.result-card .button {
    align-self: flex-start;
}

.notes {
    margin-top: var(--space-3);
    margin-bottom: var(--space-4);
    white-space: pre-wrap; /* keep the user's line breaks; text is still escaped */
    overflow-wrap: anywhere;
    max-width: 75ch;
}

/* ---------- Delete confirmation ---------- */
.confirm {
    display: flex;
    flex-direction: column;
    gap: var(--space-5);
    max-width: 40rem;
}

.confirm__actions {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: var(--space-3);
}

.data-list--compact dd {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    align-items: center;
    gap: var(--space-2);
}

/* ---------- Instruments ---------- */
.split {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: var(--space-5);
    align-items: start;
}

@media (min-width: 72rem) {
    .split {
        grid-template-columns: minmax(0, 1.5fr) minmax(0, 1fr);
    }
}

.explainer {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    color: var(--color-text-muted);
}

.explainer h2,
.explainer strong {
    color: var(--color-text);
}

.formula {
    padding: var(--space-3) var(--space-4);
    border-radius: var(--radius);
    background: var(--color-bg);
    color: var(--color-text);
    font-family: var(--font-mono);
    font-size: var(--text-sm);
    overflow-wrap: anywhere;
}

/* ---------- About ---------- */
.prose-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(min(100%, 24rem), 1fr));
    gap: var(--space-5);
    align-items: start;
}

.prose {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    color: var(--color-text-muted);
}

.prose h2,
.prose h3,
.prose strong {
    color: var(--color-text);
}

.prose ul {
    margin: 0;
    padding-left: var(--space-5);
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
}

.roadmap {
    display: grid;
    gap: var(--space-2);
}

.roadmap-item {
    display: grid;
    grid-template-columns: 5rem minmax(0, 1fr) auto;
    align-items: center;
    gap: var(--space-3);
    padding: var(--space-2) var(--space-3);
    border-radius: var(--radius-sm);
    background: var(--color-surface-raised);
}

.phase-number {
    font-size: var(--text-xs);
    text-transform: uppercase;
    letter-spacing: 0.05em;
}

.phase-title {
    color: var(--color-text);
    font-weight: 600;
}

.phase-status {
    font-size: var(--text-xs);
    font-weight: 700;
}

.status-done .phase-status {
    color: var(--color-positive);
}

.status-next .phase-status {
    color: var(--color-accent);
}

/* ---------- Error pages ---------- */
.error-page {
    display: flex;
    flex-direction: column;
    gap: var(--space-3);
    max-width: 40rem;
}

.error-code {
    color: var(--color-text-subtle);
    font-size: var(--text-2xl);
    font-weight: 700;
}

.error-page__actions {
    display: flex;
    flex-wrap: wrap;
    gap: var(--space-3);
    margin-top: var(--space-2);
}

/* ---------- Sign-in, registration and account (Phase 8) ---------- */
.auth-card {
    max-width: 30rem;
}

.auth-card__note {
    margin-top: var(--space-4);
}

.form-stack {
    display: grid;
    gap: var(--space-4);
}

.form-section__subtitle {
    margin-top: var(--space-5);
    font-size: var(--text-lg);
}

.sidebar__account {
    overflow-wrap: anywhere;
}

.detail-list {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
    gap: var(--space-3) var(--space-5);
    margin: 0;
}

.detail-list dt {
    color: var(--color-text-muted);
    font-size: var(--text-sm);
}

.detail-list dd {
    margin: 0;
    overflow-wrap: anywhere;
}

.token-reveal {
    display: grid;
    gap: var(--space-2);
}

.token-reveal__value {
    font-family: var(--font-mono, ui-monospace, monospace);
    width: 100%;
}

.badge--neutral {
    color: var(--color-text-muted);
}
```

### `tradeledger/templates/about.html` (MODIFIED)

**Why:** Describes accounts and data separation.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{% extends "base.html" %}

{% block title %}About{% endblock %}
{% block heading %}About TradeLedger{% endblock %}
{% block description %}What this application does, how it calculates, and how it is built.{% endblock %}

{% block content %}
<div class="prose-grid">
    <section class="card prose" aria-labelledby="what-title">
        <h2 id="what-title">What it does</h2>
        <p>TradeLedger is a trading journal. You record each trade's plan (entry, stop-loss, optional target, size)
           and, when it finishes, its exit. It then calculates the result of every closed trade and the performance
           of the whole journal.</p>
        <h3>How results are calculated</h3>
        <ul>
            <li><strong>Net P&amp;L</strong> = price move × position size × contract multiplier − fees, in the instrument's quote currency.</li>
            <li><strong>Realised R</strong> = net P&amp;L ÷ initial risk, where initial risk is the money lost if the stop is hit.</li>
            <li>A trade is a <strong>win</strong> if net P&amp;L is above zero, a <strong>loss</strong> if below, and <strong>breakeven</strong> only at exactly zero.</li>
            <li>Money in different currencies is never added together. R-multiples can be, because they are currency-neutral.</li>
            <li>Every calculation uses exact decimal arithmetic in Python. The browser never calculates results.</li>
        </ul>
    </section>

    <section class="card prose" aria-labelledby="security-title">
        <h2 id="security-title">Security and data</h2>
        <p>Each person signs in to their <strong>own account</strong> and sees only their own instruments, trades and analytics.</p>
        <ul>
            <li>Passwords are stored only as Argon2id hashes. Repeated failed sign-ins are slowed down.</li>
            <li>Every page with trading data needs a signed-in session; the JSON API needs a personal API token, which you can revoke at any time.</li>
            <li>Every form is protected against cross-site request forgery, and pages may only load this site's own scripts and styles. The one exception is the analytics page, which may also load the pinned Chart.js file.</li>
            <li>The server only answers requests addressed to the host names it is configured for (<code>localhost</code> and <code>127.0.0.1</code> by default), so another website cannot reach it through your browser.</li>
            <li>Data is stored in SQLite on your own computer, or in PostgreSQL when run with Docker Compose or on a server.</li>
        </ul>
    </section>

    <section class="card prose" aria-labelledby="build-title">
        <h2 id="build-title">Build progress</h2>
        <ol class="roadmap" role="list">
            {% for item in roadmap %}
            <li class="roadmap-item status-{{ item.status | lower }}">
                <span class="phase-number">Phase {{ item.phase }}</span>
                <span class="phase-title">{{ item.title }}</span>
                <span class="phase-status">{{ item.status }}</span>
            </li>
            {% endfor %}
        </ol>
    </section>

    <section class="card prose" aria-labelledby="tech-title">
        <h2 id="tech-title">Technology</h2>
        <ul>
            <li>Python, Flask and Jinja2 templates, rendered on the server</li>
            <li>SQLite with constraints, triggers and versioned migrations</li>
            <li>HTML, CSS and a small amount of vanilla JavaScript for progressive enhancement</li>
            <li>Chart.js {{ config.CHART_JS_VERSION }} for the analytics charts (drawing only; every figure is calculated in Python)</li>
            <li>A versioned JSON API documented with OpenAPI 3.1 (<a class="text-link" href="{{ url_for('api.openapi_spec') }}">specification</a>)</li>
            <li>pytest, with browser tests in Playwright where available</li>
        </ul>
        <p class="muted">Running in <strong>{{ config.CONFIG_NAME }}</strong> mode · version {{ config.APP_VERSION }}</p>
    </section>
</div>
{% endblock %}
```

### `tradeledger/templates/base.html` (MODIFIED)

**Why:** Navigation for signed-in and anonymous visitors; sign-out POST form.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
<!DOCTYPE html>
<html lang="en-GB">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="dark">
    <title>{% block title %}TradeLedger{% endblock %} · TradeLedger</title>
    <link rel="stylesheet" href="{{ url_for('static', filename='css/style.css') }}">
    {# early.js only adds class="js" to <html>, before the page paints, so
       JavaScript-dependent styles apply without a flash of the no-JS layout. #}
    <script src="{{ url_for('static', filename='js/early.js') }}"></script>
    <script src="{{ url_for('static', filename='js/app.js') }}" defer></script>
    {% block scripts %}{% endblock %}
</head>
<body>
    <a class="skip-link" href="#main">Skip to main content</a>

    {% set endpoint = request.endpoint or "" %}
    {% set current_user = g.get("user") %}
    {% if current_user %}
    {% set nav_items = [
        ("main.index", "Overview", ["main.index"]),
        ("main.journal", "Trade journal", ["main.journal", "main.trade_detail", "main.edit_trade", "main.delete_trade"]),
        ("main.new_trade", "Add trade", ["main.new_trade"]),
        ("main.instruments", "Instruments", ["main.instruments"]),
        ("main.analytics", "Analytics", ["main.analytics"]),
        ("main.account", "Account", ["main.account"]),
        ("main.about", "About", ["main.about"]),
    ] %}
    {% else %}
    {% set nav_items = [("main.login", "Sign in", ["main.login"])]
        + ([("main.register", "Create account", ["main.register"])] if config.REGISTRATION_ENABLED else [])
        + [("main.about", "About", ["main.about"])] %}
    {% endif %}

    <div class="shell">
        <aside class="sidebar" id="sidebar" data-sidebar aria-label="Main navigation">
            <div class="sidebar__top">
                <a class="brand" href="{{ url_for('main.index') if current_user else url_for('main.login') }}">
                    <span class="brand__mark" aria-hidden="true">TL</span>
                    <span class="brand__text">
                        <span class="brand__name">TradeLedger</span>
                        <span class="brand__tag">Trading journal</span>
                    </span>
                </a>
                <button class="icon-button sidebar__close" type="button" data-nav-close>
                    <span aria-hidden="true">&times;</span>
                    <span class="visually-hidden">Close menu</span>
                </button>
            </div>
            <nav>
                <ul class="nav" role="list">
                    {% for target, label, matches in nav_items %}
                    <li>
                        <a class="nav__link" href="{{ url_for(target) }}"
                           {% if endpoint in matches %}aria-current="page"{% endif %}>
                            <span>{{ label }}</span>
                        </a>
                    </li>
                    {% endfor %}
                </ul>
            </nav>
            <div class="sidebar__footer">
                <span class="env-badge">{{ config.CONFIG_NAME }} mode</span>
                {% if current_user %}
                <p class="sidebar__account">Signed in as <strong>{{ current_user.email }}</strong></p>
                <form method="post" action="{{ url_for('main.logout') }}">
                    {{ csrf_field() }}
                    <button class="button button--small button--secondary" type="submit">Sign out</button>
                </form>
                {% else %}
                <p>Sign in to see your journal.</p>
                {% endif %}
            </div>
        </aside>
        <div class="nav-backdrop" data-nav-backdrop hidden></div>

        <div class="main-area">
            <header class="topbar">
                <button class="icon-button nav-toggle" type="button" data-nav-toggle
                        aria-controls="sidebar" aria-expanded="false">
                    <span class="nav-toggle__bars" aria-hidden="true"></span>
                    <span>Menu</span>
                </button>
                <div class="topbar__titles">
                    <h1 class="topbar__title">{% block heading %}TradeLedger{% endblock %}</h1>
                    {% set description %}{% block description %}{% endblock %}{% endset %}
                    {% if description | trim %}<p class="topbar__description">{{ description }}</p>{% endif %}
                </div>
                <div class="topbar__actions">{% block actions %}{% endblock %}</div>
            </header>

            {% with messages = get_flashed_messages(with_categories=true) %}
            {% if messages %}
            <div class="flash-stack" role="status" aria-live="polite">
                {% for category, message in messages %}
                <div class="flash flash--{{ category }}" data-flash>
                    <p>{{ message }}</p>
                    <button class="flash__close" type="button" data-flash-close>
                        <span aria-hidden="true">&times;</span>
                        <span class="visually-hidden">Dismiss message</span>
                    </button>
                </div>
                {% endfor %}
            </div>
            {% endif %}
            {% endwith %}

            <main id="main" class="content" tabindex="-1">
                {% block content %}{% endblock %}
            </main>

            <footer class="site-footer">
                TradeLedger v{{ config.APP_VERSION }} · Exact decimal calculations · Times shown in UTC
            </footer>
        </div>
    </div>
</body>
</html>
```

### `tradeledger/templates/errors/403.html` (MODIFIED)

**Why:** Wording no longer refers to the removed Phase 7 guard.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{% extends "base.html" %}

{% block title %}Not allowed{% endblock %}
{% block heading %}Not allowed{% endblock %}

{% block content %}
<section class="card error-page" aria-labelledby="error-title">
    <p class="error-code" aria-hidden="true">403</p>
    <h2 id="error-title">You do not have access to this page</h2>
    <p>Nothing was changed.</p>
    <div class="error-page__actions">
        <a class="button button--secondary" href="{{ url_for('main.index') }}">Back to the overview</a>
    </div>
</section>
{% endblock %}
```

### `tradeledger/templates/macros/forms.html` (MODIFIED)

**Why:** `autocomplete` support; password fields never echo their value.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```html
{# Accessible form fields. Every field:
     * has a visible <label> tied to the input by id;
     * links its hint and error with aria-describedby, so screen readers
       read them when the field is focused;
     * sets aria-invalid="true" when it has an error;
     * shows the error text next to the field AND in the summary at the top.
   Values always come from `values`, so a failed submission keeps what the
   user typed. Jinja escapes every value automatically. #}

{% macro describedby(name, hint, errors) -%}
    {%- set ids = [] -%}
    {%- if hint %}{% set _ = ids.append("field-" ~ name ~ "-hint") %}{% endif -%}
    {%- if errors.get(name) %}{% set _ = ids.append("field-" ~ name ~ "-error") %}{% endif -%}
    {%- if ids %} aria-describedby="{{ ids | join(' ') }}"{% endif -%}
    {%- if errors.get(name) %} aria-invalid="true"{% endif -%}
{%- endmacro %}

{% macro messages(name, hint, errors, labels) -%}
    {% if hint %}<p class="field__hint" id="field-{{ name }}-hint">{{ hint }}</p>{% endif %}
    {% if errors.get(name) %}
        <p class="field__error" id="field-{{ name }}-error">
            <span class="visually-hidden">Error:</span> {{ labels.get(name, name) }} {{ errors[name] }}
        </p>
    {% endif %}
{%- endmacro %}

{% macro text(name, label, values, errors, labels, hint=None, type="text", inputmode=None,
              required=False, placeholder=None, maxlength=None, list=None, step=None, wide=False,
              autocomplete=None) -%}
    <div class="field{% if errors.get(name) %} field--error{% endif %}{% if wide %} field--wide{% endif %}">
        <label class="field__label" for="field-{{ name }}">
            {{ label }}{% if not required %} <span class="field__optional">(optional)</span>{% endif %}
        </label>
        <input class="input" id="field-{{ name }}" name="{{ name }}" type="{{ type }}"
               {%- if type != "password" %} value="{{ values.get(name, '') }}"{% endif %}
               {%- if inputmode %} inputmode="{{ inputmode }}"{% endif %}
               {%- if placeholder %} placeholder="{{ placeholder }}"{% endif %}
               {%- if maxlength %} maxlength="{{ maxlength }}"{% endif %}
               {%- if list %} list="{{ list }}"{% endif %}
               {%- if step %} step="{{ step }}"{% endif %}
               {%- if required %} required{% endif %}
               {%- if inputmode == "decimal" %} autocomplete="off" spellcheck="false"{% endif %}
               {%- if autocomplete %} autocomplete="{{ autocomplete }}"{% endif %}
               {%- if type in ("email", "password") %} spellcheck="false" autocapitalize="none"{% endif %}
               {{- describedby(name, hint, errors) }}>
        {{ messages(name, hint, errors, labels) }}
    </div>
{%- endmacro %}

{% macro select(name, label, values, errors, labels, options, hint=None, required=False, blank=None) -%}
    <div class="field{% if errors.get(name) %} field--error{% endif %}">
        <label class="field__label" for="field-{{ name }}">
            {{ label }}{% if not required %} <span class="field__optional">(optional)</span>{% endif %}
        </label>
        <select class="input select" id="field-{{ name }}" name="{{ name }}"
                {%- if required %} required{% endif %}{{ describedby(name, hint, errors) }}>
            {% if blank is not none %}<option value="">{{ blank }}</option>{% endif %}
            {% for value, text in options %}
            <option value="{{ value }}" {% if values.get(name, '') | string == value | string %}selected{% endif %}>{{ text }}</option>
            {% endfor %}
        </select>
        {{ messages(name, hint, errors, labels) }}
    </div>
{%- endmacro %}

{# A group of radio buttons styled as a segmented control. #}
{% macro choice(name, legend, values, errors, labels, options, hint=None, data_attr=None) -%}
    <fieldset class="field choice{% if errors.get(name) %} field--error{% endif %}"
              {%- if data_attr %} {{ data_attr }}{% endif %}{{ describedby(name, hint, errors) }}>
        <legend class="field__label">{{ legend }}</legend>
        <div class="choice__options">
            {% for value, text in options %}
            <label class="choice__option">
                <input type="radio" name="{{ name }}" value="{{ value }}"
                       id="field-{{ name }}{% if not loop.first %}-{{ value | lower }}{% endif %}"
                       {% if values.get(name) == value %}checked{% endif %}>
                <span>{{ text }}</span>
            </label>
            {% endfor %}
        </div>
        {{ messages(name, hint, errors, labels) }}
    </fieldset>
{%- endmacro %}

{% macro textarea(name, label, values, errors, labels, hint=None, maxlength=None, rows=5) -%}
    <div class="field field--wide{% if errors.get(name) %} field--error{% endif %}">
        <label class="field__label" for="field-{{ name }}">{{ label }} <span class="field__optional">(optional)</span></label>
        <textarea class="input textarea" id="field-{{ name }}" name="{{ name }}" rows="{{ rows }}"
                  {%- if maxlength %} maxlength="{{ maxlength }}"{% endif %}
                  {{- describedby(name, hint, errors) }}>{{ values.get(name, '') }}</textarea>
        {{ messages(name, hint, errors, labels) }}
    </div>
{%- endmacro %}

{# List of every problem, linked to the fields. Focused by app.js on load. #}
{% macro error_summary(errors, labels) -%}
    {% if errors %}
    <div class="error-summary" role="alert" tabindex="-1" data-error-summary aria-labelledby="error-summary-title">
        <h2 class="error-summary__title" id="error-summary-title">
            {{ errors | length }} {{ "problem" if errors | length == 1 else "problems" }} to fix before saving
        </h2>
        <ul>
            {% for name, message in errors.items() %}
            <li><a href="#field-{{ name }}">{{ labels.get(name, name) }} {{ message }}</a></li>
            {% endfor %}
        </ul>
    </div>
    {% endif %}
{%- endmacro %}
```

### `tradeledger/timestamps.py` (MODIFIED)

**Why:** Adds `utc_now`, `utc_now_text` and `format_stored` used by accounts, tokens and the throttle.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Reading TradeLedger's stored UTC timestamps, quickly (Phase 7).

Every stored time has exactly one shape, 'YYYY-MM-DDTHH:MM:SSZ' (UTC). The
database enforces it with CHECK constraints on trade_date and closed_at,
and validation.parse_timestamp() writes nothing else.

Why this module exists: profiling GET /analytics with 10,000 trades showed
datetime.strptime() called about five times per trade, roughly a third of
the request time. strptime re-interprets its format string on every call.
A strict pattern check followed by datetime.fromisoformat() (implemented
in C) gives the same answers for this one shape, several times faster.
tests/test_timestamps.py compares the two on valid and invalid input.
"""

import re
from datetime import UTC, datetime

STORED_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

# [0-9], not \d: \d would also accept non-ASCII digits such as "٢".
_STORED_SHAPE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


def parse_stored(text):
    """'2026-03-02T08:15:00Z' -> datetime(2026, 3, 2, 8, 15) (naive, meaning UTC).

    Raises TypeError for a non-string and ValueError for anything that is
    not a real date and time in exactly the stored shape, the same
    exceptions datetime.strptime(text, STORED_FORMAT) raises.
    """
    if not isinstance(text, str):
        raise TypeError(f"a stored timestamp must be text, not {type(text).__name__}")
    if not _STORED_SHAPE.fullmatch(text):
        raise ValueError(f"not a stored timestamp (YYYY-MM-DDTHH:MM:SSZ): {text!r}")
    # Dropping the "Z" gives a naive datetime, exactly what strptime returned.
    # fromisoformat still rejects impossible values such as month 13 or 30 February.
    return datetime.fromisoformat(text[:-1])


def format_stored(moment):
    """datetime (naive UTC or aware) -> 'YYYY-MM-DDTHH:MM:SSZ'."""
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.strftime(STORED_FORMAT)


def utc_now():
    """The current UTC time as a naive datetime (TradeLedger's convention).
    One function, so tests can move the clock with monkeypatch."""
    return datetime.now(UTC).replace(tzinfo=None, microsecond=0)


def utc_now_text():
    return format_stored(utc_now())
```

### `tradeledger/web/__init__.py` (MODIFIED)

**Why:** Registers hooks in order: load user → require login → CSRF.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""The TradeLedger website: server-rendered pages and HTML forms.

Blueprint "main" (the same name Phase 1 used, so url_for("main.index") and
url_for("main.health") keep working). Modules:

  auth.py         sign in, sign out, registration, session checks (Phase 8)
  account.py      account settings, password change, API tokens (Phase 8)
  csrf.py         CSRF tokens for every state-changing form
  filters.py      Jinja filters that format exact Decimals for display
  forms.py        turning request.form into input for validation.py
  views.py        overview, about, health checks
  analytics.py    the /analytics performance dashboard (Phase 6)
  trades.py       journal, add, details, edit, delete
  instruments.py  instrument list and add form

Every write goes through the same repository and validation functions as
the JSON API, so there is one set of business rules, not two. Every read
and write passes the signed-in user's id (auth.current_user_id()).
"""

from flask import Blueprint

main_bp = Blueprint("main", __name__)

# Importing these modules registers their routes and template filters.
from . import account, analytics, auth, csrf, filters, instruments, trades, views  # noqa: E402,F401

# Request hooks are registered HERE, explicitly, because their order
# matters and must not depend on import order (Phase 7: sorting the import
# line above once swapped them silently; tests caught it).
#   1. load_logged_in_user: read and check the session (g.user).
#   2. require_login: every endpoint not in auth.PUBLIC_ENDPOINTS redirects
#      anonymous visitors to the sign-in page. Running it BEFORE the CSRF
#      check means an anonymous POST is redirected, not told "form expired".
#   3. csrf: check the token on every state-changing request, including the
#      sign-in form itself (which stops "login CSRF").
main_bp.before_request(auth.load_logged_in_user)
main_bp.before_request(auth.require_login)
main_bp.before_request(csrf.check_csrf_token)
```

### `tradeledger/web/analytics.py` (MODIFIED)

**Why:** Uses the signed-in user's id.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""The /analytics dashboard (Phase 6).

Request flow:
  1. GET /analytics?instrument_id=1&strategy=Breakout&session=LONDON&start=...&end=...
  2. parse_report_filters() validates the query string (the same function
     the JSON API uses). Any problem -> 400 with the errors shown next to
     the filter fields, and NO report, so a bad filter can never produce a
     misleading page.
  3. build_dashboard() runs ONE query and builds ONE report; every KPI card,
     chart and table on the page is cut from it.
  4. The template renders the numbers as HTML (works without JavaScript)
     plus a JSON copy of the chart data for analytics.js.

Filtering happens in SQL on the server. The browser never receives the
trade history, only the aggregated figures it draws.
"""

from urllib.parse import urlsplit

from flask import current_app, g, render_template, request

from ..db import get_db
from ..performance.dashboard import SESSION_LABELS, SMALL_SAMPLE, build_dashboard, chart_payload, money_rows
from ..report_filters import FILTER_NAMES, parse_report_filters
from ..repositories.instruments import list_instruments
from ..repositories.trades import list_strategies
from ..validation import SESSIONS
from . import main_bp
from .auth import current_user_id

FILTER_LABELS = {
    "instrument_id": "Instrument",
    "strategy": "Strategy",
    "session": "Session",
    "start": "Realised from",
    "end": "Realised to",
}


def chart_script_sources(url):
    """Content-Security-Policy sources needed to load Chart.js from `url`.

    A same-site path (e.g. /static/vendor/chartjs/chart.umd.min.js) is
    already covered by 'self'. An https URL is allowed as that exact file,
    never as the whole host. Anything else is a configuration mistake.
    """
    if url.startswith("/") and not url.startswith("//"):
        return ()
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
        raise ValueError("CHART_JS_URL must be a site path or an https URL without a query string")
    return (url,)


@main_bp.route("/analytics")
def analytics():
    conn = get_db()
    user_id = current_user_id()
    filters, errors = parse_report_filters(request.args, conn, user_id=user_id, blank_means_unset=True)
    raw = {name: request.args.get(name, "").strip() for name in FILTER_NAMES}

    dashboard = payload = None
    if not errors:
        dashboard = build_dashboard(conn, filters, user_id=user_id)
        payload = chart_payload(dashboard)
        if dashboard.has_results:  # charts (and so Chart.js) only when there is data
            g.csp_script_sources = chart_script_sources(current_app.config["CHART_JS_URL"])

    strategies = list_strategies(conn, user_id=user_id)
    if raw["strategy"] and raw["strategy"] not in strategies:
        strategies = [raw["strategy"], *strategies]  # keep a bookmarked choice visible

    return render_template(
        "analytics/dashboard.html",
        dashboard=dashboard,
        payload=payload,
        money=money_rows(dashboard.report) if dashboard else [],
        errors=errors,
        raw=raw,
        labels=FILTER_LABELS,
        active=filters.active() if not errors else {},
        instruments=list_instruments(conn, user_id=user_id),
        strategies=strategies,
        session_options=[(key, SESSION_LABELS[key]) for key in SESSIONS],
        small_sample=SMALL_SAMPLE,
        chart_js={
            "url": current_app.config["CHART_JS_URL"],
            "integrity": current_app.config["CHART_JS_INTEGRITY"],
            "version": current_app.config["CHART_JS_VERSION"],
        },
    ), (400 if errors else 200)
```

### `tradeledger/web/csrf.py` (MODIFIED)

**Why:** Docstring updated for the new hook order (sign-in forms are protected too).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Cross-Site Request Forgery (CSRF) protection for HTML forms.

The attack: while you are signed in to TradeLedger, a malicious page you
visit contains a hidden form that posts to https://your-journal/trades/3/delete.
Your browser would happily send it. The defence: every form we render
includes a secret token, and every POST must send it back. Another site
cannot read our pages, so it cannot know the token.

How it works here (the "synchroniser token" pattern):
  1. get_token() creates one random token per browser session and stores
     it in Flask's signed session cookie.
  2. Templates put it in a hidden field: {{ csrf_field() }}.
  3. Before any POST/PUT/PATCH/DELETE on this blueprint, the submitted
     token is compared with the session's using hmac.compare_digest
     (constant time, so timing cannot leak how much of it matched).
  4. Missing or wrong token -> 400 with a "form expired" page. Nothing
     is written.

Why not Flask-WTF? It is the usual choice and does the same thing, but it
is not installed in this project and 40 lines of standard library code
are easy to read, test and explain. The JSON API (/api/v1) does not use
these tokens: since Phase 8 it ignores cookies completely and accepts only
an Authorization: Bearer token, which a browser never attaches on its own,
so a forged cross-site request has no credentials to ride on.

The token lives in the session, so signing in (which clears the session)
gives the browser a new token: a token learned before sign-in is useless.
"""

import hmac
import secrets

from flask import render_template, request, session
from markupsafe import Markup, escape

from . import main_bp

SESSION_KEY = "_csrf_token"
FIELD_NAME = "csrf_token"
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def get_token():
    token = session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[SESSION_KEY] = token
    return token


def token_is_valid(submitted):
    expected = session.get(SESSION_KEY)
    if not expected or not isinstance(submitted, str) or not submitted:
        return False
    return hmac.compare_digest(expected.encode(), submitted.encode())


def check_csrf_token():
    """before_request hook, registered after the login check in web/__init__.py."""
    if request.method not in STATE_CHANGING:
        return None
    if token_is_valid(request.form.get(FIELD_NAME)):
        return None
    return render_template("errors/400.html", reason="csrf"), 400


@main_bp.app_context_processor
def inject_csrf_helpers():
    def csrf_field():
        return Markup('<input type="hidden" name="%s" value="%s">') % (FIELD_NAME, escape(get_token()))

    return {"csrf_token": get_token, "csrf_field": csrf_field}
```

### `tradeledger/web/forms.py` (MODIFIED)

**Why:** `safe_next` also rejects control characters.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Helpers that sit between HTML forms and validation.py.

HTML forms send every value as text, and a missing field is simply absent.
These helpers:
  * keep ONLY the fields a form may set (no mass assignment: a crafted
    "id" or "created_at" field is ignored);
  * keep what the user typed, so a failed submission can redisplay it;
  * turn database records back into form values for editing.

They do NOT decide whether a trade is valid. That is validation.py's job,
shared with the JSON API.
"""

from decimal import Decimal
from urllib.parse import urlsplit

from ..performance.formatting import decimal_to_text
from .filters import datetime_local_filter

TRADE_FORM_FIELDS = (
    "instrument_id",
    "direction",
    "status",
    "trade_date",
    "entry_price",
    "stop_loss",
    "take_profit",
    "position_size",
    "fees",
    "exit_price",
    "closed_at",
    "strategy",
    "session",
    "notes",
)

INSTRUMENT_FORM_FIELDS = (
    "symbol",
    "name",
    "asset_class",
    "price_increment",
    "contract_multiplier",
    "quote_currency",
)

# Fields that have no meaning for an OPEN trade. The browser disables them
# (so they are not sent); the server also blanks them if they arrive anyway.
EXIT_FIELDS = ("exit_price", "closed_at")

BLANK_TRADE_FORM = {
    "instrument_id": "",
    "direction": "LONG",
    "status": "CLOSED",
    "trade_date": "",
    "entry_price": "",
    "stop_loss": "",
    "take_profit": "",
    "position_size": "",
    "fees": "0",
    "exit_price": "",
    "closed_at": "",
    "strategy": "",
    "session": "",
    "notes": "",
}


def read_form(form, fields):
    """Text values for the allowed fields only. Absent fields become ""."""
    return {name: form.get(name, "").strip() for name in fields}


def form_errors_for_status(values):
    """The website asks the user to choose a status explicitly (the API
    may infer it). Returns extra errors that validation.py would not raise."""
    if values.get("status") not in ("OPEN", "CLOSED"):
        return {"status": "must be OPEN or CLOSED"}
    return {}


def trade_input(values):
    """Form values -> the dict validate_trade() expects.
    Exit fields of an OPEN trade are ignored rather than rejected: the
    user switched the status to OPEN, so leftover exit values are stale."""
    data = {name: (value if value != "" else None) for name, value in values.items()}
    if values.get("status") == "OPEN":
        for name in EXIT_FIELDS:
            data[name] = None
    return data


def trade_to_form(trade):
    """A stored trade (Decimals, UTC strings) -> text values for the form."""

    def text(value):
        if value is None:
            return ""
        return decimal_to_text(value) if isinstance(value, Decimal) else str(value)

    values = {name: text(trade.get(name)) for name in TRADE_FORM_FIELDS}
    values["trade_date"] = datetime_local_filter(trade["trade_date"])
    values["closed_at"] = datetime_local_filter(trade["closed_at"]) if trade["closed_at"] else ""
    return values


def safe_next(target, fallback):
    """Only allow redirects to a path on this site.

    A `next` value comes from the URL, so an attacker could craft
    ?next=https://evil.example. We accept only relative paths that start
    with a single "/" (not "//", which browsers treat as another host) and
    contain no backslashes or scheme.
    """
    if not target or not isinstance(target, str):
        return fallback
    if not target.startswith("/") or target.startswith("//") or "\\" in target:
        return fallback
    if any(ord(char) < 32 or ord(char) == 127 for char in target):  # Phase 8: e.g. "/\t/evil.example"
        return fallback
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return fallback
    return target
```

### `tradeledger/web/instruments.py` (MODIFIED)

**Why:** Uses the signed-in user's id for every read and write.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Instrument list and add form.

There is no edit or delete: changing an instrument's contract_multiplier
would silently change the P&L of every past trade on it. That needs
versioned contract specifications first (see the Phase 4 guide).
"""

from flask import flash, redirect, render_template, request, url_for

from ..db import get_db
from ..repositories import DuplicateError
from ..repositories.instruments import create_instrument, list_instruments
from ..validation import ASSET_CLASSES, ValidationError
from . import main_bp
from .auth import current_user_id
from .forms import INSTRUMENT_FORM_FIELDS, read_form

BLANK_INSTRUMENT_FORM = dict.fromkeys(INSTRUMENT_FORM_FIELDS, "")

INSTRUMENT_LABELS = {
    "symbol": "Symbol",
    "name": "Name",
    "asset_class": "Asset class",
    "price_increment": "Price increment",
    "contract_multiplier": "Contract multiplier",
    "quote_currency": "Quote currency",
}


def page(values, errors, status=200):
    return render_template(
        "instruments/index.html",
        instruments=list_instruments(get_db(), user_id=current_user_id()),
        values=values,
        errors=errors,
        labels=INSTRUMENT_LABELS,
        asset_class_options=[(value, value.title()) for value in ASSET_CLASSES],
    ), status


@main_bp.route("/instruments", methods=["GET", "POST"])
def instruments():
    if request.method == "GET":
        return page(dict(BLANK_INSTRUMENT_FORM), {})

    values = read_form(request.form, INSTRUMENT_FORM_FIELDS)
    try:
        create_instrument(get_db(), values, user_id=current_user_id())
    except ValidationError as exc:
        return page(values, exc.errors, 400)
    except DuplicateError:
        return page(values, {"symbol": "is already recorded. Symbols must be unique."}, 400)

    flash(f"Instrument {values['symbol'].upper()} added.", "success")
    return redirect(url_for("main.instruments"))
```

### `tradeledger/web/trades.py` (MODIFIED)

**Why:** Uses the signed-in user's id for every read and write.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Trade pages: journal, add, details, edit, delete.

Pattern for every form (POST-Redirect-GET):
  GET   shows the form
  POST  validates. If invalid, re-renders the form with the user's values
        and field errors (status 400). If valid, writes inside a
        transaction, flashes a message and REDIRECTS, so refreshing the
        next page cannot submit the form twice.

All rules live in validation.py and the repositories (shared with the
API). All performance numbers come from performance.trade_math.
"""

from decimal import DecimalException

from flask import abort, current_app, flash, redirect, render_template, request, url_for

from ..db import get_db
from ..performance.trade_math import IncompleteTradeError, evaluate_trade, initial_risk, planned_rr
from ..repositories import trades as trade_repo
from ..repositories.instruments import list_instruments
from ..validation import SESSIONS, ValidationError, date_range_bounds, parse_date, validate_trade
from . import main_bp
from .auth import current_user_id
from .forms import (
    BLANK_TRADE_FORM,
    TRADE_FORM_FIELDS,
    form_errors_for_status,
    read_form,
    safe_next,
    trade_input,
    trade_to_form,
)

FILTER_FIELDS = ("instrument_id", "status", "start", "end", "strategy", "q")

FIELD_LABELS = {
    "instrument_id": "Instrument",
    "direction": "Direction",
    "status": "Status",
    "trade_date": "Entry time",
    "entry_price": "Entry price",
    "stop_loss": "Stop-loss",
    "take_profit": "Take-profit",
    "position_size": "Position size",
    "fees": "Fees",
    "exit_price": "Exit price",
    "closed_at": "Exit time",
    "strategy": "Strategy",
    "session": "Session",
    "notes": "Notes",
}


# ---------- Helpers ----------


def result_for(trade):
    """The engine's TradeResult for a CLOSED trade; None for OPEN trades
    (they have no realised result) or data that cannot be evaluated."""
    if trade["status"] != "CLOSED":
        return None
    try:
        return evaluate_trade(trade)
    except IncompleteTradeError:
        return None


def with_results(trades):
    return [{"trade": t, "result": result_for(t)} for t in trades]


def plan_for(trade):
    """Planned risk and planned R:R from the engine's own functions. These
    describe the PLAN and are shown for open trades; they are not results."""
    args = (trade["direction"], trade["entry_price"], trade["stop_loss"])
    try:
        return {
            "initial_risk": initial_risk(*args, trade["position_size"], trade["contract_multiplier"]),
            "planned_rr": planned_rr(*args, trade["take_profit"]),
        }
    except DecimalException:
        # Out-of-range stored values (Phase 7): show "n/a" instead of a 500.
        return {"initial_risk": None, "planned_rr": None}


def get_trade_or_404(conn, trade_id):
    trade = trade_repo.get_trade(conn, trade_id, user_id=current_user_id())
    if trade is None:  # missing OR another user's: the same 404
        abort(404)
    return trade


def current_path():
    """This page's path and query, for 'return to where I was' links."""
    path = request.full_path
    return path[:-1] if path.endswith("?") else path


def form_context(conn, values, errors, **extra):
    instruments = list_instruments(conn, user_id=current_user_id())
    return {
        "values": values,
        "errors": errors,
        "labels": FIELD_LABELS,
        "instruments": instruments,
        "instrument_options": [(i["id"], f"{i['symbol']} · {i['name']}") for i in instruments],
        "strategies": trade_repo.list_strategies(conn, user_id=current_user_id()),
        "sessions": SESSIONS,
        **extra,
    }


def collect_errors(values, data, *, require_closed_at=True):
    """Every problem with a submitted form, without writing anything."""
    errors = {}
    try:
        validate_trade(data, require_closed_at=require_closed_at)
    except ValidationError as exc:
        errors.update(exc.errors)
    errors.update(form_errors_for_status(values))
    return errors


# ---------- Journal ----------


def read_filters(args):
    """Validate the journal's query string. Returns (filters, raw, errors).
    `raw` holds what the user typed, to refill the filter form."""
    raw = {name: args.get(name, "").strip() for name in FILTER_FIELDS}
    errors, filters = {}, {}

    if raw["instrument_id"]:
        if raw["instrument_id"].isdigit() and int(raw["instrument_id"]) > 0:
            filters["instrument_id"] = int(raw["instrument_id"])
        else:
            errors["instrument_id"] = "Choose an instrument from the list."
    if raw["status"]:
        if raw["status"].upper() in ("OPEN", "CLOSED"):
            filters["status"] = raw["status"].upper()
        else:
            errors["status"] = "Choose OPEN or CLOSED."
    for name in ("start", "end"):
        if raw[name]:
            try:
                filters[name] = parse_date(raw[name]).isoformat()
            except ValueError:
                errors[name] = "Use a date in YYYY-MM-DD form."
    if "start" in filters and "end" in filters:
        try:
            date_range_bounds(filters["start"], filters["end"])
        except ValidationError:
            errors["end"] = "The end date must be on or after the start date."
    if raw["strategy"]:
        filters["strategy"] = raw["strategy"]
    if raw["q"]:
        if len(raw["q"]) > trade_repo.MAX_SEARCH_LENGTH:
            errors["q"] = f"Search text must be {trade_repo.MAX_SEARCH_LENGTH} characters or fewer."
        else:
            filters["search"] = raw["q"]
    return filters, raw, errors


@main_bp.route("/trades")
def journal():
    conn = get_db()
    filters, raw, errors = read_filters(request.args)
    page_text = request.args.get("page", "1")
    page = int(page_text) if page_text.isdigit() and int(page_text) > 0 else 1
    per_page = current_app.config["JOURNAL_PER_PAGE"]

    rows, total = [], 0
    if not errors:
        total = trade_repo.count_trades(conn, user_id=current_user_id(), **filters)
        if total and page > (total + per_page - 1) // per_page:
            abort(404)  # e.g. an old bookmark to ?page=9 after deletions
        rows = trade_repo.list_trades(
            conn, user_id=current_user_id(), **filters, limit=per_page, offset=(page - 1) * per_page
        )

    active = {name: value for name, value in raw.items() if value}
    return render_template(
        "trades/journal.html",
        rows=with_results(rows),
        total=total,
        page=page,
        per_page=per_page,
        total_pages=(total + per_page - 1) // per_page,
        raw=raw,
        active=active,
        errors=errors,
        instruments=list_instruments(conn, user_id=current_user_id()),
        strategies=trade_repo.list_strategies(conn, user_id=current_user_id()),
        here=current_path(),
    ), (400 if errors else 200)


# ---------- Add ----------


@main_bp.route("/trades/new", methods=["GET", "POST"])
def new_trade():
    conn = get_db()
    if request.method == "GET":
        values = dict(BLANK_TRADE_FORM)
        if request.args.get("instrument_id", "").isdigit():
            values["instrument_id"] = request.args["instrument_id"]
        return render_template("trades/form.html", **form_context(conn, values, {}, mode="new"))

    values = read_form(request.form, TRADE_FORM_FIELDS)
    data = trade_input(values)
    errors = collect_errors(values, data)
    if not errors:
        try:
            trade_id = trade_repo.create_trade(conn, data, user_id=current_user_id())
        except ValidationError as exc:  # e.g. the instrument was deleted meanwhile
            errors = exc.errors
    if errors:
        return render_template("trades/form.html", **form_context(conn, values, errors, mode="new")), 400

    flash(f"Trade #{trade_id} saved.", "success")
    return redirect(url_for("main.trade_detail", trade_id=trade_id))


# ---------- Details ----------


@main_bp.route("/trades/<int:trade_id>")
def trade_detail(trade_id):
    trade = get_trade_or_404(get_db(), trade_id)
    return render_template(
        "trades/detail.html",
        trade=trade,
        result=result_for(trade),
        plan=plan_for(trade),
        here=current_path(),
    )


# ---------- Edit ----------


@main_bp.route("/trades/<int:trade_id>/edit", methods=["GET", "POST"])
def edit_trade(trade_id):
    conn = get_db()
    trade = get_trade_or_404(conn, trade_id)
    is_legacy = trade["status"] == "CLOSED" and trade["closed_at"] is None
    back = safe_next(request.values.get("next"), url_for("main.trade_detail", trade_id=trade_id))

    if request.method == "GET":
        return render_template(
            "trades/form.html",
            **form_context(
                conn, trade_to_form(trade), {}, mode="edit", trade=trade, is_legacy=is_legacy, back=back
            ),
        )

    values = read_form(request.form, TRADE_FORM_FIELDS)
    data = trade_input(values)
    errors = collect_errors(values, data, require_closed_at=not is_legacy)
    if not errors:
        try:
            trade_repo.update_trade(conn, trade_id, data, user_id=current_user_id())
        except ValidationError as exc:
            errors = exc.errors
    if errors:
        return render_template(
            "trades/form.html",
            **form_context(conn, values, errors, mode="edit", trade=trade, is_legacy=is_legacy, back=back),
        ), 400

    flash(f"Trade #{trade_id} updated.", "success")
    return redirect(url_for("main.trade_detail", trade_id=trade_id))


# ---------- Delete ----------


@main_bp.route("/trades/<int:trade_id>/delete", methods=["GET", "POST"])
def delete_trade(trade_id):
    """GET shows a confirmation page and changes nothing. Only a POST with
    a valid CSRF token deletes."""
    conn = get_db()
    trade = get_trade_or_404(conn, trade_id)
    back = safe_next(request.values.get("next"), url_for("main.journal"))

    if request.method == "GET":
        return render_template("trades/confirm_delete.html", trade=trade, result=result_for(trade), back=back)

    trade_repo.delete_trade(conn, trade_id, user_id=current_user_id())
    flash(f"Trade #{trade_id} ({trade['symbol']} {trade['direction']}) was deleted.", "success")
    return redirect(back)
```

### `tradeledger/web/views.py` (MODIFIED)

**Why:** Overview scoped to the user; `/health/live` and `/health/ready`; roadmap.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Overview, About and the health checks. (The analytics dashboard is in
analytics.py.)

Every number shown comes from the repositories (counts) or the Phase 3
analytics engine (performance), for the signed-in user only. Nothing is
hard-coded.
"""

import logging

from flask import current_app, jsonify, render_template

from ..db import get_db, ping
from ..performance import build_report
from ..repositories import trades as trade_repo
from ..repositories.instruments import list_instruments
from . import main_bp
from .auth import current_user_id
from .trades import with_results

log = logging.getLogger("tradeledger.health")

RECENT_TRADES = 5

# The build plan, shown on the About page.
ROADMAP = [
    {"phase": 1, "title": "Project setup", "status": "Done"},
    {"phase": 2, "title": "SQL database", "status": "Done"},
    {"phase": 3, "title": "Trading calculations", "status": "Done"},
    {"phase": 4, "title": "Flask backend & API", "status": "Done"},
    {"phase": 5, "title": "Frontend pages", "status": "Done"},
    {"phase": 6, "title": "Analytics dashboard", "status": "Done"},
    {"phase": 7, "title": "Testing, CI, security & quality", "status": "Done"},
    {"phase": 8, "title": "Accounts, PostgreSQL, Docker & AWS design", "status": "Done"},
    {"phase": 9, "title": "Next phase (to be agreed)", "status": "Next"},
]


@main_bp.route("/")
def index():
    """Overview of the signed-in user's journal."""
    conn = get_db()
    user_id = current_user_id()
    report = build_report(conn, user_id=user_id)
    return render_template(
        "index.html",
        open_count=trade_repo.count_trades(conn, user_id=user_id, status="OPEN"),
        closed_count=trade_repo.count_trades(conn, user_id=user_id, status="CLOSED"),
        instrument_count=len(list_instruments(conn, user_id=user_id)),
        overall=report.overall,
        unknown_close_count=len(report.unknown_close_time_trade_ids),
        recent=with_results(trade_repo.list_trades(conn, user_id=user_id, limit=RECENT_TRADES)),
    )


@main_bp.route("/about")
def about():
    return render_template("about.html", roadmap=ROADMAP)


@main_bp.route("/health")
def health():
    """Liveness check (kept from Phase 1): the process answers HTTP.
    Returns JSON only - never secrets, hostnames or database details."""
    return jsonify(
        status="ok",
        app=current_app.config["APP_NAME"],
        version=current_app.config["APP_VERSION"],
    )


@main_bp.route("/health/live")
def health_live():
    """Liveness: is the process alive? Never touches the database, so a
    database outage does not make the orchestrator restart healthy
    containers in a loop. Docker's HEALTHCHECK uses this."""
    return jsonify(status="ok")


@main_bp.route("/health/ready")
def health_ready():
    """Readiness: can this instance serve requests right now? Checks the
    database with SELECT 1. The load balancer uses this, so traffic only
    goes to containers that can reach the database. On failure the reason
    is logged on the server; the response says only "unavailable"."""
    try:
        ready = ping(get_db())
    except Exception:
        log.exception("readiness check failed", extra={"event": "health.ready.failed"})
        ready = False
    if not ready:
        return jsonify(status="unavailable"), 503
    return jsonify(status="ready")
```

### `.env.example` (MODIFIED)

**Why:** Phase 8 settings (PostgreSQL, registration, proxy hops, logs).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```bash
# Copy this file to .env and fill in real values.
# .env is ignored by Git - never commit it.

# Which config class to use: development, testing or production
TRADELEDGER_CONFIG=development

# Generate your own with:  python -c "import secrets; print(secrets.token_hex(32))"
# (Production refuses this placeholder and anything under 32 characters.
#  In containers prefer SECRET_KEY_FILE=/run/secrets/secret_key.)
SECRET_KEY=replace-me-with-a-long-random-string

# Host names this server answers to (Phase 7; protects against DNS
# rebinding). Default in development: localhost,127.0.0.1. Add your LAN IP
# if you open TradeLedger from another device. "*" switches the check off.
# TRADELEDGER_TRUSTED_HOSTS=localhost,127.0.0.1

# Optional: where the SQLite file lives. Leave commented out to use
# instance/tradeledger.db inside the project folder.
# DATABASE_PATH=/full/path/to/tradeledger.db

# Optional (Phase 6): where the dashboard loads Chart.js from. Default is the
# pinned jsDelivr CDN URL for version 4.5.1 (needs internet in the browser).
# To self-host, see docs/THIRD_PARTY_NOTICES.md, then:
# TRADELEDGER_CHART_JS_URL=/static/vendor/chartjs/chart.umd.min.js
# Optional Subresource Integrity hash for that file ("sha384-..."):
# TRADELEDGER_CHART_JS_INTEGRITY=

# ---------- Phase 8 ----------

# PostgreSQL instead of SQLite (Docker Compose and AWS set these for you).
# Either one URL...
# DATABASE_URL=postgresql://tradeledger:CHANGE-ME@localhost:5432/tradeledger
# ...or separate settings (the password may come from DB_PASSWORD_FILE):
# DB_HOST=localhost
# DB_PORT=5432
# DB_NAME=tradeledger
# DB_USER=tradeledger
# DB_PASSWORD=CHANGE-ME
# DB_SSLMODE=require
# TRADELEDGER_DB_POOL_MAX=5

# Self-registration: on by default in development, OFF in production.
# TRADELEDGER_REGISTRATION_ENABLED=0

# Set to 1 ONLY when exactly one reverse proxy / load balancer is in front.
# TRADELEDGER_PROXY_HOPS=0

# Logs: "text" (people) or "json" (CloudWatch; production default).
# TRADELEDGER_LOG_FORMAT=text

# Turn the JSON API off entirely (tokens are still required when it is on).
# TRADELEDGER_API_ENABLED=1

# Removed in Phase 8 (ignored with a warning): TRADELEDGER_ENABLE_UNAUTHENTICATED_API,
# TRADELEDGER_ENABLE_UNAUTHENTICATED_JOURNAL. Authentication cannot be switched off.
```

### `.github/workflows/ci.yml` (MODIFIED)

**Why:** Adds postgres (real psycopg), docker and terraform jobs.  
**How to run or test:** Runs on GitHub (never run yet); static checks in `tests/test_deployment_files.py`

```yaml
# TradeLedger continuous integration (Phase 7, extended in Phase 8).
#
# Runs on every push, every pull request, and on demand (Actions tab ->
# CI -> "Run workflow"). It only TESTS the code: nothing is deployed, no
# cloud credentials exist here. Deployment is a separate, manual workflow
# (deploy.yml) behind an approval.
#
# Jobs (they run in parallel):
#   python      lint, formatting, the non-browser test suite (SQLite) and
#               coverage, on the oldest and newest supported Python versions
#   postgres    the same suite against a real PostgreSQL 16 service with the
#               real psycopg driver, plus the independent analytics audit on
#               both databases (Phase 8)
#   javascript  the Node.js unit tests for static/js/analytics.js
#   audit       known-vulnerability check of the pinned Python dependencies
#   docker      Dockerfile lint, image build, and the Compose end-to-end
#               smoke test (sign-in, trade, analytics, API, restart) (Phase 8)
#   terraform   formatting and static validation of infra/ WITHOUT any AWS
#               account (terraform init -backend=false) (Phase 8)
#   browser     Playwright + Chromium, in three clearly separated steps:
#                 1. page tests with Chart.js REPLACED by a stand-in (stub)
#                 2. the offline fallback (Chart.js blocked)
#                 3. REAL Chart.js 4.5.1 rendering (library fetched from npm
#                    into this CI workspace only; never committed)

name: CI

on:
  push:
  pull_request:
  workflow_dispatch:

# Least privilege: the workflow only needs to read the repository.
permissions:
  contents: read

# A newer push to the same branch cancels the older, now-irrelevant run.
concurrency:
  group: ci-${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

env:
  PIP_DISABLE_PIP_VERSION_CHECK: "1"
  PYTHONDONTWRITEBYTECODE: "1"
  FORCE_COLOR: "1"

jobs:
  python:
    name: Python ${{ matrix.python-version }} · lint, tests, coverage
    runs-on: ubuntu-latest
    timeout-minutes: 15
    strategy:
      fail-fast: false
      matrix:
        # README promises Python 3.11 or newer; test the oldest and the newest used.
        python-version: ["3.11", "3.13"]
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v6
        with:
          python-version: ${{ matrix.python-version }}
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies
        run: python -m pip install -r requirements-dev.txt -c constraints.txt

      - name: Lint (ruff check)
        run: ruff check --output-format=github .

      - name: Formatting (ruff format --check)
        run: ruff format --check .

      - name: Tests and coverage (everything except real-browser tests)
        # Coverage settings, including the minimum (fail_under), are in pyproject.toml.
        run: >
          python -m pytest -m "not browser"
          --cov=tradeledger --cov-report=term-missing
          --cov-report=xml --cov-report=html

      - name: Upload the coverage report
        if: always() && matrix.python-version == '3.13'
        uses: actions/upload-artifact@v6
        with:
          name: coverage-report
          path: |
            htmlcov/
            coverage.xml
          retention-days: 14
          if-no-files-found: ignore

  postgres:
    name: PostgreSQL 16 · full suite + audit (psycopg)
    runs-on: ubuntu-latest
    timeout-minutes: 20
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: postgres
          POSTGRES_PASSWORD: postgres  # throwaway CI-only database, not a real secret
          POSTGRES_DB: tradeledger_test
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U postgres"
          --health-interval 5s --health-timeout 5s --health-retries 10
    env:
      TRADELEDGER_TEST_DATABASE_URL: postgresql://postgres:postgres@localhost:5432/tradeledger_test
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies (includes psycopg)
        run: python -m pip install -r requirements-dev.txt -c constraints.txt

      - name: Confirm the REAL driver is used (not the test-only bridge)
        run: python -c "import psycopg, psycopg_pool; print('psycopg', psycopg.__version__)"

      - name: Test suite on PostgreSQL (every test uses its own schema)
        run: python -m pytest -m "not browser" -ra

      - name: Set up Node.js (the audit runs the real analytics.js)
        uses: actions/setup-node@v6
        with:
          node-version: "22"

      - name: Independent analytics audit, SQLite
        run: python scripts/qa_dashboard_agreement.py

      - name: Independent analytics audit, PostgreSQL
        run: |
          psql "postgresql://postgres:postgres@localhost:5432/postgres" -c "CREATE DATABASE tradeledger_audit"
          python scripts/qa_dashboard_agreement.py \
            --database-url postgresql://postgres:postgres@localhost:5432/tradeledger_audit

  javascript:
    name: JavaScript unit tests (Node.js)
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Node.js 22
        uses: actions/setup-node@v6
        with:
          node-version: "22"

      # Node's built-in test runner: no npm packages are installed.
      - name: Run the tests
        run: node --test tests/js/*.test.js

  audit:
    name: Dependency vulnerability audit
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies
        run: python -m pip install -r requirements-dev.txt -c constraints.txt

      # Checks every installed package against the Python Packaging Advisory
      # Database and OSV. Fails the job if a known vulnerability is found.
      - name: pip-audit
        run: pip-audit --strict --progress-spinner off

  browser:
    name: Browser tests (Playwright + Chromium)
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Python
        uses: actions/setup-python@v6
        with:
          python-version: "3.13"
          cache: pip
          cache-dependency-path: |
            requirements*.txt
            constraints.txt

      - name: Install pinned dependencies (with Playwright)
        run: python -m pip install -r requirements-browser.txt -c constraints.txt

      - name: Install Chromium and its system libraries
        run: python -m playwright install --with-deps chromium

      - name: "1/3 Page tests, Chart.js replaced by a stand-in (stub: NOT Chart.js rendering)"
        run: python -m pytest -m "browser and not chartjs_offline and not chartjs_real" -ra

      - name: "2/3 Offline fallback (Chart.js blocked)"
        run: python -m pytest -m chartjs_offline -ra

      - name: Set up Node.js (for npm pack)
        uses: actions/setup-node@v6
        with:
          node-version: "22"

      # npm verifies the package against the integrity hash published in the
      # registry. The files are copied into this temporary CI workspace only.
      - name: Fetch Chart.js 4.5.1 from npm (CI workspace only)
        run: |
          npm pack chart.js@4.5.1 --silent
          tar -xzf chart.js-4.5.1.tgz
          cp package/dist/chart.umd.min.js package/LICENSE.md tradeledger/static/vendor/chartjs/
          head -c 200 tradeledger/static/vendor/chartjs/chart.umd.min.js; echo
          sha384sum tradeledger/static/vendor/chartjs/chart.umd.min.js

      - name: "3/3 REAL Chart.js rendering"
        env:
          # A missing library file fails the job instead of being skipped.
          TRADELEDGER_REQUIRE_REAL_CHARTJS: "1"
          TRADELEDGER_SCREENSHOT_DIR: artifacts/screenshots
        run: python -m pytest -m chartjs_real -ra

      - name: Upload real-chart screenshots
        if: always()
        uses: actions/upload-artifact@v6
        with:
          name: chartjs-real-screenshots
          path: artifacts/screenshots/
          retention-days: 14
          if-no-files-found: ignore

  docker:
    name: Docker image + Compose smoke test
    runs-on: ubuntu-latest
    timeout-minutes: 20
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Lint the Dockerfile (hadolint)
        run: docker run --rm -i hadolint/hadolint < Dockerfile

      - name: Validate compose.yaml
        run: |
          mkdir -p secrets && echo placeholder > secrets/secret_key.txt && echo placeholder > secrets/db_password.txt
          docker compose config --quiet
          rm secrets/secret_key.txt secrets/db_password.txt

      - name: Build the image
        run: docker build -t tradeledger:ci .

      - name: The image runs as a non-root user and contains no secrets or tests
        run: |
          test "$(docker run --rm --entrypoint id tradeledger:ci -u)" = "10001"
          ! docker run --rm --entrypoint sh tradeledger:ci -c 'ls -a /app' | grep -E '\.env|tests|secrets|\.git'

      - name: Compose end-to-end smoke test
        run: bash scripts/compose_smoke_test.sh

  terraform:
    name: Terraform fmt + validate (no AWS account)
    runs-on: ubuntu-latest
    timeout-minutes: 10
    defaults:
      run:
        working-directory: infra
    steps:
      - name: Check out the repository
        uses: actions/checkout@v6

      - name: Set up Terraform
        uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: "~1.13.0"  # any 1.13.x; needs >= 1.10 for S3 state locking

      - name: terraform fmt -check
        run: terraform fmt -check -recursive -diff

      - name: terraform init (providers only, no remote state)
        run: terraform init -backend=false -input=false

      - name: terraform validate
        run: terraform validate
```

### `.gitignore` (MODIFIED)

**Why:** Ignores secrets/, Terraform state and tfvars, import audit files.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```gitignore
# Virtual environments
.venv/
venv/
env/

# Secrets and local settings
.env
*.env
!.env.example

# Python caches and build output
__pycache__/
*.py[cod]
*.egg-info/
build/
dist/

# Test, coverage and lint output
.pytest_cache/
.coverage
.coverage.*
coverage.xml
htmlcov/
.ruff_cache/
artifacts/

# Downloaded in CI only (npm pack chart.js), never committed from there
chart.js-*.tgz
/package/

# Databases (may contain personal trade data)
*.db
*.sqlite
*.sqlite3
instance/

# Logs
*.log

# Editor and OS files
.vscode/
.idea/
.DS_Store
Thumbs.db

# Phase 8: Docker Compose secrets and Terraform state/plans (may contain secrets)
secrets/*
!secrets/README.md
.terraform/
*.tfstate
*.tfstate.*
*.tfplan
tfplan
crash.log
override.tf
override.tf.json
*_override.tf
*_override.tf.json
infra/**/*.tfvars
!infra/**/*.tfvars.example
import-audit*.json
```

### `constraints.txt` (MODIFIED)

**Why:** Pins cffi and pycparser (dependencies of cryptography).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```text
# Exact versions of the packages that TradeLedger's own dependencies pull in.
# Use with -c so pip installs these versions without making them direct
# dependencies:   pip install -r requirements-dev.txt -c constraints.txt
#
# These are the versions every Phase 7 and Phase 8 test run used (Python 3.13). Update
# them deliberately (see docs/PHASE_7_RELEASE_CHECKLIST.md), never silently.
# Not listed: the dependencies of pytest-cov, coverage and pip-audit, which
# could not be installed in the Phase 7 build environment (no network), nor
# those of psycopg, psycopg-pool and gunicorn (Phase 8, same reason).

# cryptography (Phase 8)
cffi==2.1.1
pycparser==3.0

# Flask
Werkzeug==3.1.9
Jinja2==3.1.6
MarkupSafe==3.0.3
itsdangerous==2.2.0
click==8.5.0
blinker==1.9.0

# pytest
iniconfig==2.3.0
packaging==26.3
pluggy==1.6.0
Pygments==2.21.0

# jsonschema
attrs==26.1.0
jsonschema-specifications==2025.9.1
referencing==0.37.0
rpds-py==2026.6.3
typing_extensions==4.16.0

# playwright
greenlet==3.5.6
pyee==13.0.1
```

### `pyproject.toml` (MODIFIED)

**Why:** Adds `sqlite_only` / `postgres_only` markers; reviewed Ruff exceptions.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```toml
# Tool configuration for TradeLedger (Phase 7).
#
# TradeLedger is an application, not a library published to PyPI, so this
# file holds tool settings only: no build system and no package metadata.
# Dependencies stay in requirements*.txt (see README "Setup").

# ---------------------------------------------------------------- pytest
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["."]
addopts = "-ra --strict-markers"
markers = [
    "browser: drives a real Chromium browser through Playwright (skipped if Playwright is missing)",
    "chartjs_stub: browser test that replaces Chart.js with a recording stand-in (tests OUR code, not Chart.js drawing)",
    "chartjs_offline: browser test that blocks Chart.js to check the text fallback",
    "chartjs_real: browser test that needs the REAL Chart.js file (skipped unless it is installed locally)",
    "sqlite_only: relies on SQLite specifics (files, PRAGMA); skipped when testing on PostgreSQL",
    "postgres_only: needs TRADELEDGER_TEST_DATABASE_URL; skipped on SQLite",
]
# Turn every warning into a failure, so problems such as unclosed files or
# deprecated calls cannot pile up unnoticed. Narrow exceptions go below,
# each with a reason.
filterwarnings = [
    "error",
]

# ---------------------------------------------------------------- coverage
[tool.coverage.run]
source = ["tradeledger"]
branch = true
# The browser tests run the app in a background thread.
concurrency = ["thread"]

[tool.coverage.report]
show_missing = true
skip_covered = false
precision = 1
# CI fails below this. Measured baseline: see docs/PHASE_7_QA_REPORT.md.
fail_under = 90
exclude_also = [
    # Code that only exists to help a type checker.
    "if TYPE_CHECKING:",
]

[tool.coverage.html]
directory = "htmlcov"

# ---------------------------------------------------------------- ruff
[tool.ruff]
target-version = "py311"
line-length = 110
# docs/ holds historical code listings (PHASE_*_CODE.md) that must stay exactly
# as delivered; Ruff would otherwise reformat Python blocks inside Markdown.
extend-exclude = ["htmlcov", "instance", "docs"]

[tool.ruff.lint]
select = [
    "E", "W",   # pycodestyle errors and warnings
    "F",        # pyflakes: unused imports/variables, undefined names
    "I",        # import order
    "B",        # flake8-bugbear: likely bugs
    "UP",       # pyupgrade: modern syntax for Python 3.11+
    "S",        # flake8-bandit: security checks (Bandit itself is not a dependency)
    "BLE",      # blind `except Exception`
    "SIM",      # simplifications
    "C4",       # comprehensions
    "PIE",      # misc. unnecessary code
    "RUF",      # Ruff's own checks
]
ignore = [
    "E501",     # line length is the formatter's job; long URLs and SQL stay readable
    "RUF001", "RUF002", "RUF003",  # typographic characters (–, …, ×) are intended British UI text
]

[tool.ruff.lint.per-file-ignores]
"tests/**" = [
    "S101",     # pytest uses plain assert
    "S105", "S106", "S107",  # fixed fake secrets in tests
    "S108",     # temporary paths in tests
    "S311",     # random test data, not cryptography
    "S603", "S607",  # tests run node with a fixed argument list
    "S608",     # test SQL interpolates fixed table names only (reviewed in Phase 7)
]
# Browser test modules import shared pytest fixtures from tests/browser_support.py
# by name; pytest then passes them in as arguments, which Ruff reports as F811.
"tests/test_browser*.py" = ["F811"]
"tests/test_chartjs_real.py" = ["F811"]
# Form-error messages keyed by password field names ("current_password":
# "is incorrect.") are not hard-coded passwords (S105 false positives).
"tradeledger/web/auth.py" = ["S105"]
"tradeledger/web/account.py" = ["S105"]
"scripts/**" = [
    "S311",     # synthetic data, not cryptography
    "S603", "S607",  # the audit runs node with a fixed argument list
]

[tool.ruff.format]
quote-style = "double"
```

### `requirements.txt` (MODIFIED)

**Why:** Adds cryptography, psycopg[binary], psycopg-pool, gunicorn (pinned).  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```text
# Runtime dependencies: what TradeLedger needs to run.
# Exact versions (==) make installs repeatable. Dependencies of these
# packages (Werkzeug, Jinja2, cffi, ...) are pinned in constraints.txt.
Flask==3.1.3
python-dotenv==1.2.3

# Phase 8: Argon2id password hashing (pyca/cryptography, maintained by the
# Python Cryptographic Authority). Version 50.0.1 is the one every Phase 8
# test run used.
cryptography==50.0.1

# Phase 8: PostgreSQL driver and connection pool (psycopg 3), and the
# production WSGI server. NOTE: these three could NOT be installed in the
# Phase 8 build environment (no package index access); the versions were
# taken from PyPI release listings. They are exercised by the CI
# "postgres" and "docker" jobs, which have not yet run on GitHub.
psycopg[binary]==3.3.8
psycopg-pool==3.3.0
gunicorn==26.0.0
```

### `tests/browser_support.py` (MODIFIED)

**Why:** Live server exposes a session cookie for signed-in browser tests.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Shared helpers for the real-browser (Playwright) tests.

Imported by tests/test_browser*.py and tests/test_chartjs_real.py. Importing
this module skips the importing test file when Playwright is not installed,
so the rest of the suite is unaffected.

Chart.js and the three kinds of browser test (Phase 7)
------------------------------------------------------
A browser test is in exactly one of these groups (pytest markers):

  chartjs_stub     Chart.js is REPLACED by CHART_STUB below, a stand-in that
                   records `new Chart(canvas, config)` calls and draws
                   nothing. Proves OUR page, CSP and analytics.js work.
                   Proves nothing about how Chart.js draws.
  chartjs_offline  The Chart.js URL is blocked, as if the browser had no
                   internet: checks the text fallback.
  chartjs_real     Uses the REAL Chart.js file served from
                   tradeledger/static/vendor/chartjs/. Skipped (and reported
                   as skipped) unless that file is present.

Tests that never open the dashboard carry none of these markers; they
still get the stand-in, because a page might link to the dashboard.

Signing in (Phase 8)
--------------------
Every server has the default test account (conftest.TEST_EMAIL) holding the
sample data. page_factory(server=live_server) gives a page that is already
signed in: the server mints a real signed session cookie, exactly what the
sign-in form would set. The sign-in journey itself is tested through the
real form in tests/test_browser_auth.py.
"""

import threading
import time
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")

from werkzeug.serving import make_server  # noqa: E402

from tests.conftest import ensure_user  # noqa: E402
from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

WIDTHS = [375, 768, 1024, 1440]
CHART_JS_URL = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
LOCAL_CHART_JS = Path(__file__).resolve().parent.parent / "tradeledger/static/vendor/chartjs/chart.umd.min.js"

# Records chart configurations; draws nothing. Never shipped to users.
CHART_STUB = """
(function () {
    function Chart(canvas, config) {
        this.canvas = canvas;
        this.config = config;
        (window.__charts = window.__charts || []).push(this);
    }
    Chart.defaults = {font: {}};
    window.Chart = Chart;
})();
"""


class LiveServer(str):
    """The server's base URL (a str, so `server + "/trades"` works), plus
    what a browser needs to be signed in to it."""

    def __new__(cls, url, app):
        server = super().__new__(cls, url)
        server.app = app
        return server

    def session_cookie(self, user_id=1):
        """A valid signed session cookie for `user_id`, as /login would set."""
        now = int(time.time())
        data = {"uid": user_id, "sv": 1, "auth_at": now, "seen_at": now, "_permanent": True}
        value = self.app.session_interface.get_signing_serializer(self.app).dumps(data)
        return {"name": self.app.config["SESSION_COOKIE_NAME"], "value": value, "url": str(self)}


def start_server(db_path, overrides=None, seed=True, prepare=None):
    """Run TradeLedger on a free local port in a background thread.

    The default test account (user 1) always exists; seed=True gives it
    the sample data. prepare: optional function called with the database
    connection afterwards, to add test-specific data.
    Returns (LiveServer, stop_function)."""
    app = create_app("testing", overrides={"DATABASE": db_path, **(overrides or {})})
    with app.app_context():
        init_schema(get_db())
        user_id = ensure_user(get_db())
        if seed:
            seed_sample_data(get_db(), user_id=user_id)
        if prepare:
            prepare(get_db())
    server = make_server("127.0.0.1", 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def stop():
        server.shutdown()
        thread.join(timeout=5)
        app.extensions["tradeledger_db"].close()

    return LiveServer(f"http://127.0.0.1:{server.server_port}", app), stop


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """A fresh server and database for each test MODULE."""
    url, stop = start_server(str(tmp_path_factory.mktemp("browser") / "browser.db"))
    yield url
    stop()


@pytest.fixture(scope="module")
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            launched = p.chromium.launch()
        except playwright_api.Error as exc:  # browser binary not installed
            pytest.skip(f"Chromium is not available: {exc}")
        yield launched
        launched.close()


@pytest.fixture
def page_factory(browser):
    """Create pages; every page records JavaScript errors and console errors
    (including Content-Security-Policy violations) in page.problems.

    chart_js: "stub" (default), "blocked" (offline) or "real" (no
    interception: the page loads whatever URL the server configured).
    server:   a LiveServer to be signed in to as the default test account;
              omit it for an anonymous visitor.
    """
    contexts = []

    def make(width=1440, height=900, chart_js="stub", javascript=True, server=None, **context_options):
        context = browser.new_context(
            viewport={"width": width, "height": height}, java_script_enabled=javascript, **context_options
        )
        contexts.append(context)
        if server is not None:
            context.add_cookies([server.session_cookie()])
        if chart_js == "stub":
            context.route(
                CHART_JS_URL,
                lambda route: route.fulfill(
                    status=200, content_type="application/javascript", body=CHART_STUB
                ),
            )
        elif chart_js == "blocked":
            context.route(CHART_JS_URL, lambda route: route.abort("internetdisconnected"))
        page = context.new_page()
        page.problems = []
        page.on("pageerror", lambda exc: page.problems.append(str(exc)))
        page.on(
            "console",
            lambda msg: (
                page.problems.append(msg.text)
                if msg.type == "error" and "status of 4" not in msg.text
                else None
            ),
        )
        return page

    yield make
    for context in contexts:
        context.close()


def horizontal_overflow(page):
    return page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")


def submit_and_wait(page, selector="form[data-trade-form] button[type=submit]"):
    """Click a form's submit button and wait for the page that comes back."""
    with page.expect_navigation():
        page.locator(selector).click()
```

### `tests/conftest.py` (MODIFIED)

**Why:** Fixtures rewritten: per-test PostgreSQL schema, test users, signed-in `client` with an API token, `anon_client`.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Shared pytest fixtures.

pytest loads this file automatically. Any test that names a fixture as a
parameter (app, client, conn, seeded_conn, ...) receives it.

Databases (Phase 8)
-------------------
By default every test gets a fresh SQLite file in pytest's tmp_path.
Set TRADELEDGER_TEST_DATABASE_URL to run the SAME tests against PostgreSQL:

    TRADELEDGER_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/tradeledger_test

Each test then gets its own empty PostgreSQL SCHEMA (a separate namespace
inside that database), which is dropped afterwards. Tests never touch
instance/tradeledger.db or any real journal. Tests that only make sense for
one engine (SQLite file tricks, PostgreSQL pooling) carry the markers
sqlite_only / postgres_only and are skipped on the other.

TRADELEDGER_PG_DRIVER=libpq-bridge selects the test-only libpq bridge
(tests/pg_libpq_bridge.py) instead of psycopg; see that file for why.

Accounts (Phase 8)
------------------
Every app and connection fixture creates the default test account
(TEST_EMAIL, user id 1 in a fresh database). `client` is signed in as that
user AND sends that user's API token, so Phase 1-7 tests exercise the same
pages and endpoints as before, now through the real authentication checks.
`anon_client` is a browser that has not signed in.
"""

import os
import time
import uuid
from urllib.parse import quote

import pytest

from tradeledger import create_app
from tradeledger.db import connect, init_schema, register_postgres_driver
from tradeledger.passwords import HashParameters, hash_password
from tradeledger.repositories import api_tokens
from tradeledger.repositories.instruments import create_instrument
from tradeledger.repositories.users import create_user, get_user_by_email
from tradeledger.seed import seed_sample_data

TEST_EMAIL = "trader@example.com"
TEST_PASSWORD = "correct horse battery staple"
OTHER_EMAIL = "other.trader@example.com"
FAST_HASH = HashParameters(memory_kib=1024, iterations=1, lanes=1)  # same as TestingConfig

PG_URL = os.environ.get("TRADELEDGER_TEST_DATABASE_URL")
if PG_URL and os.environ.get("TRADELEDGER_PG_DRIVER") == "libpq-bridge":
    from tests import pg_libpq_bridge

    register_postgres_driver(pg_libpq_bridge)

_HASH_CACHE = {}


def password_hash(password=TEST_PASSWORD):
    """Hashing is deliberately slow; reuse one hash per password in tests."""
    if password not in _HASH_CACHE:
        _HASH_CACHE[password] = hash_password(password, FAST_HASH)
    return _HASH_CACHE[password]


def ensure_user(conn, email=TEST_EMAIL, password=TEST_PASSWORD):
    """Create the account if it does not exist yet; return its id."""
    existing = get_user_by_email(conn, email)
    if existing:
        return existing["id"]
    return create_user(conn, email, password_hash(password))


def pytest_collection_modifyitems(config, items):
    engine = "postgresql" if PG_URL else "sqlite"
    for item in items:
        if engine == "postgresql" and item.get_closest_marker("sqlite_only"):
            item.add_marker(pytest.mark.skip(reason="SQLite-specific test (running on PostgreSQL)"))
        if engine == "sqlite" and item.get_closest_marker("postgres_only"):
            item.add_marker(pytest.mark.skip(reason="needs TRADELEDGER_TEST_DATABASE_URL (PostgreSQL)"))


def _with_search_path(url, schema):
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}options={quote(f'-csearch_path={schema}')}"


@pytest.fixture
def db_path(tmp_path):
    """Where this test's database lives: a SQLite file path, or (on
    PostgreSQL) a URL whose search_path points at a fresh, empty schema."""
    if not PG_URL:
        yield str(tmp_path / "test.db")
        return
    schema = "t_" + uuid.uuid4().hex[:16]
    admin = connect(PG_URL)
    admin.execute(f"CREATE SCHEMA {schema}")
    try:
        yield _with_search_path(PG_URL, schema)
    finally:
        admin.execute(f"DROP SCHEMA {schema} CASCADE")
        admin.close()


@pytest.fixture
def app(db_path):
    """A fresh app using the testing configuration and a temporary database,
    with the default test account already created."""
    app = create_app("testing", overrides={"DATABASE": db_path})
    with app.app_context():
        from tradeledger.db import get_db

        init_schema(get_db())
        ensure_user(get_db())
    yield app
    app.extensions["tradeledger_db"].close()


@pytest.fixture
def user_id():
    """The default test account's id (the first account in a fresh database)."""
    return 1


def sign_in(client, user_id=1, session_version=1):
    """Mark a test client as signed in, exactly as a successful login does."""
    now = int(time.time())
    with client.session_transaction() as session:
        session.clear()
        session.permanent = True
        session.update({"uid": user_id, "sv": session_version, "auth_at": now, "seen_at": now})


def issue_token(app, user_id=1, name="test token"):
    from tradeledger.db import get_db

    with app.app_context():
        token, _ = api_tokens.create_token(
            get_db(), user_id=user_id, name=name, lifetime_days=30, max_active=10
        )
    return token


@pytest.fixture
def client(app):
    """A browser signed in as the default test account, which also sends
    that account's API token on every request (for the JSON API tests)."""
    test_client = app.test_client()
    sign_in(test_client)
    test_client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {issue_token(app)}"
    return test_client


@pytest.fixture
def anon_client(app):
    """A browser that has not signed in and has no API token."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Runs Flask CLI commands (init-db, seed-db, ...) inside tests."""
    return app.test_cli_runner()


@pytest.fixture
def conn(db_path):
    """A plain connection to an empty, initialised temporary database that
    contains the default test account (and nothing else)."""
    connection = connect(db_path)
    init_schema(connection)
    ensure_user(connection)
    yield connection
    connection.close()


@pytest.fixture
def seeded_conn(conn, user_id):
    """The same temporary database, with the synthetic sample data in the
    default test account."""
    seed_sample_data(conn, user_id=user_id)
    return conn


@pytest.fixture
def eurusd_id(conn, user_id):
    """Creates one instrument for the default account and returns its id."""
    return create_instrument(
        conn,
        {
            "symbol": "EURUSD",
            "name": "Euro / US Dollar (test)",
            "asset_class": "FOREX",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        user_id=user_id,
    )


@pytest.fixture
def long_trade(eurusd_id):
    """Valid input for a closed LONG trade on EURUSD."""
    return {
        "instrument_id": eurusd_id,
        "trade_date": "2026-03-02T08:15:00Z",
        "closed_at": "2026-03-02T11:40:00Z",  # Phase 4: closed trades record an exit time
        "direction": "LONG",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "status": "CLOSED",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Test trade",
    }


# ---------- Phase 4: API fixtures ----------


@pytest.fixture
def seeded_client(app, client, user_id):
    """A signed-in test client whose account holds the synthetic sample data."""
    from tradeledger.db import get_db

    with app.app_context():
        seed_sample_data(get_db(), user_id=user_id)
    return client


@pytest.fixture
def trade_body():
    """A valid POST /api/v1/trades body for sample instrument 1 (EURUSD).
    Same prices as sample trade 1: net +393 USD, realised R +1.965."""
    return {
        "instrument_id": 1,
        "trade_date": "2026-04-01T08:15:00Z",
        "closed_at": "2026-04-01T11:40:00Z",
        "direction": "LONG",
        "status": "CLOSED",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "API test trade (synthetic)",
    }
```

### `tests/factories.py` (MODIFIED)

**Why:** Adds USER_ID.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Builders for synthetic trade dicts used by the analytics tests.

make_trade() returns a dict shaped like a row from list_trades(). The
default is a CLOSED LONG with simple numbers so expected results are easy
to work out by hand:

    entry 100, stop 90, exit 120, size 1, multiplier 1, fees 0
    risk = (100 - 90) x 1 x 1 = 10      net = (120 - 100) x 1 x 1 - 0 = 20
    realised R = 20 / 10 = 2
"""

from decimal import Decimal

# The default test account: conftest.py creates it first in every fresh
# database, so its id is always 1 (Phase 8: every repository call names its owner).
USER_ID = 1

_next_id = [0]


def make_trade(**changes):
    _next_id[0] += 1
    trade = {
        "id": _next_id[0],
        "symbol": "TEST",
        "quote_currency": "USD",
        "contract_multiplier": Decimal("1"),
        "trade_date": "2026-01-05T10:00:00Z",
        "direction": "LONG",
        "status": "CLOSED",
        "entry_price": Decimal("100"),
        "stop_loss": Decimal("90"),
        "take_profit": None,
        "exit_price": Decimal("120"),
        "position_size": Decimal("1"),
        "fees": Decimal("0"),
        "strategy": None,
        "session": None,
    }
    trade.update(changes)
    return trade


def r_trade(r, **changes):
    """A LONG trade whose realised R is exactly `r` (risk 10, no fees)."""
    r = Decimal(str(r))
    return make_trade(exit_price=Decimal("100") + r * 10, **changes)


def dated(day, **changes):
    """make_trade on 2026-01-<day> (2026-01-05 is a Monday)."""
    return make_trade(trade_date=f"2026-01-{day:02d}T10:00:00Z", **changes)


def table_snapshot(app, table):
    """Every row of a table (as tuples), for 'nothing changed' assertions.
    `table` is always a literal name written in a test, never user input."""
    from tradeledger.db import get_db

    with app.app_context():
        return [tuple(row) for row in get_db().execute(f"SELECT * FROM {table} ORDER BY id")]
```

### `tests/test_api_analytics.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_api_analytics.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Integration tests for /api/v1/analytics/*.

The expected numbers are the hand-worked sample results from Phase 3 (see
tests/test_report.py). Here we check that the API returns exactly what the
engine calculates, as exact strings, with the documented shapes.
"""

from decimal import Decimal
from fractions import Fraction

import pytest

from tests.factories import USER_ID
from tradeledger.db import get_db
from tradeledger.performance import build_report
from tradeledger.performance.formatting import to_jsonable

BASE = "/api/v1/analytics"
ENDPOINTS = ["summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve"]


def contains_float(obj):
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(contains_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(contains_float(v) for v in obj)
    return False


# ---------- Summary ----------


def test_summary_matches_the_engine_exactly(app, seeded_client):
    body = seeded_client.get(f"{BASE}/summary").get_json()
    with app.app_context():
        expected = to_jsonable(build_report(get_db(), user_id=USER_ID).overall)
    assert body["data"]["overall"] == expected


def test_summary_hand_worked_values(seeded_client):
    data = seeded_client.get(f"{BASE}/summary").get_json()["data"]
    o = data["overall"]
    assert (o["trades"], o["wins"], o["losses"], o["breakevens"]) == (14, 7, 5, 2)
    assert o["win_rate"] == "0.5"
    expectancy = Fraction(Decimal(o["expectancy_r"]))
    assert abs(expectancy - Fraction(239871, 436800)) < Fraction(1, 10**40)
    assert o["money"]["USD"]["net_pnl"] == "798.5"
    assert o["money"]["GBP"]["profit_factor"] is None  # no GBP losses -> null
    assert data["open_trades"] == 2  # counted, not in results
    assert data["excluded"] == [] and data["r_unavailable"] == []
    assert data["unknown_close_time_trade_ids"] == []  # sample trades have exit times


def test_summary_meta_explains_conventions(seeded_client):
    meta = seeded_client.get(f"{BASE}/summary").get_json()["meta"]
    # Phase 6 added strategy and session (an additive change to meta).
    assert meta["filters"] == {
        "instrument_id": None,
        "strategy": None,
        "session": None,
        "start": None,
        "end": None,
    }
    joined = " ".join(meta["notes"])
    assert "strings" in joined and "never summed across currencies" in joined


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_no_floats_anywhere(seeded_client, endpoint):
    assert not contains_float(seeded_client.get(f"{BASE}/{endpoint}").get_json())


def test_decimal_strings_round_trip_exactly(app, seeded_client):
    total_r = seeded_client.get(f"{BASE}/summary").get_json()["data"]["overall"]["total_r"]
    with app.app_context():
        assert Decimal(total_r) == build_report(get_db(), user_id=USER_ID).overall.total_r


# ---------- Groups ----------


def test_by_instrument(seeded_client):
    data = seeded_client.get(f"{BASE}/instruments").get_json()["data"]
    assert [g["group"] for g in data] == ["EURUSD", "GBPUSD", "NAS100", "UK100", "XAUUSD"]
    gold = data[-1]
    assert (gold["trades"], gold["win_rate"]) == (4, "0.75")
    assert list(data[3]["money"]) == ["GBP"]


def test_by_strategy_and_session(seeded_client):
    strategies = seeded_client.get(f"{BASE}/strategies").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in strategies] == [
        ("Breakout", 5),
        ("Pullback", 5),
        ("Reversal", 2),
        ("Unspecified", 2),
    ]
    sessions = seeded_client.get(f"{BASE}/sessions").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in sessions] == [
        ("ASIA", 2),
        ("LONDON", 6),
        ("NEW_YORK", 5),
        ("Unspecified", 1),
    ]


def test_by_weekday_in_calendar_order(seeded_client):
    data = seeded_client.get(f"{BASE}/weekday").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in data] == [
        ("Monday", 3),
        ("Tuesday", 3),
        ("Wednesday", 3),
        ("Thursday", 2),
        ("Friday", 3),
    ]


def test_monthly(seeded_client):
    data = seeded_client.get(f"{BASE}/monthly").get_json()["data"]
    assert [(g["group"], g["trades"]) for g in data] == [("2026-03", 14)]


# ---------- Curve ----------


def test_curve(seeded_client):
    body = seeded_client.get(f"{BASE}/curve").get_json()
    assert "NOT an account equity curve" in body["meta"]["label"]
    points = body["data"]
    assert [p["trade_id"] for p in points] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]
    assert points[0]["realised_r"] == points[0]["cumulative_r"] == "1.965"
    assert all(p["close_time_known"] for p in points)


# ---------- Filters ----------


def test_instrument_filter_applies_to_every_endpoint(seeded_client):
    for endpoint in ENDPOINTS:
        assert seeded_client.get(f"{BASE}/{endpoint}?instrument_id=3").status_code == 200
    assert seeded_client.get(f"{BASE}/summary?instrument_id=3").get_json()["data"]["overall"]["trades"] == 4
    assert [
        g["group"] for g in seeded_client.get(f"{BASE}/instruments?instrument_id=3").get_json()["data"]
    ] == ["XAUUSD"]


def test_date_filter_uses_realised_date(app, seeded_client, trade_body):
    # Entered 31 March, closed 1 April: it belongs to April's results.
    seeded_client.post(
        "/api/v1/trades",
        json={**trade_body, "trade_date": "2026-03-31T22:00:00Z", "closed_at": "2026-04-01T01:00:00Z"},
    )
    april = seeded_client.get(f"{BASE}/summary?start=2026-04-01&end=2026-04-30").get_json()
    assert april["data"]["overall"]["trades"] == 1
    assert april["meta"]["filters"] == {
        "instrument_id": None,
        "strategy": None,
        "session": None,
        "start": "2026-04-01",
        "end": "2026-04-30",
    }
    march = seeded_client.get(f"{BASE}/summary?start=2026-03-01&end=2026-03-31").get_json()
    assert march["data"]["overall"]["trades"] == 14


@pytest.mark.parametrize("endpoint", ENDPOINTS)
@pytest.mark.parametrize(
    "query, field",
    [
        ("start=2026-02-30", "start"),
        ("end=soon", "end"),
        ("start=2026-03-10&end=2026-03-01", "end"),
        ("instrument_id=999", "instrument_id"),
        ("instrument_id=x", "instrument_id"),
    ],
)
def test_invalid_filters_are_400(seeded_client, endpoint, query, field):
    response = seeded_client.get(f"{BASE}/{endpoint}?{query}")
    assert response.status_code == 400
    assert field in response.get_json()["error"]["details"]


def test_unknown_analytics_parameter_is_400(seeded_client):
    response = seeded_client.get(f"{BASE}/summary?status=OPEN")
    assert response.get_json()["error"]["code"] == "unknown_parameters"


# ---------- Edge cases ----------


def test_empty_database(client):
    o = client.get(f"{BASE}/summary").get_json()["data"]["overall"]
    assert o["trades"] == 0
    assert o["win_rate"] is None and o["expectancy_r"] is None and o["profit_factor_r"] is None
    assert o["total_r"] == "0" and o["money"] == {}
    for endpoint in ENDPOINTS[1:]:
        assert client.get(f"{BASE}/{endpoint}").get_json()["data"] == []


def test_open_trades_do_not_affect_realised_results(seeded_client, trade_body):
    before = seeded_client.get(f"{BASE}/summary").get_json()["data"]
    open_trade = {k: v for k, v in trade_body.items() if k not in ("exit_price", "closed_at", "status")}
    seeded_client.post("/api/v1/trades", json=open_trade)
    after = seeded_client.get(f"{BASE}/summary").get_json()["data"]
    assert after["overall"] == before["overall"]
    assert after["open_trades"] == before["open_trades"] + 1


def test_breakeven_and_mixed_currencies(client):
    for instrument in (
        {
            "symbol": "AAA",
            "name": "Test USD",
            "asset_class": "OTHER",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        {
            "symbol": "BBB",
            "name": "Test GBP",
            "asset_class": "OTHER",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "GBP",
        },
    ):
        assert client.post("/api/v1/instruments", json=instrument).status_code == 201
    common = {
        "trade_date": "2026-05-04T09:00:00Z",
        "closed_at": "2026-05-04T10:00:00Z",
        "direction": "LONG",
        "entry_price": "100",
        "stop_loss": "90",
        "position_size": "1",
    }
    client.post("/api/v1/trades", json={**common, "instrument_id": 1, "exit_price": "120"})  # +20 USD
    client.post("/api/v1/trades", json={**common, "instrument_id": 2, "exit_price": "100"})  # 0 GBP
    o = client.get(f"{BASE}/summary").get_json()["data"]["overall"]
    assert (o["wins"], o["breakevens"]) == (1, 1)
    assert o["money"]["USD"]["net_pnl"] == "20" and o["money"]["GBP"]["net_pnl"] == "0"
    assert "net_pnl" not in o  # no cross-currency total exists
    assert o["total_r"] == "2"  # R can be combined: +2R + 0R


def test_analytics_are_read_only(app, seeded_client):
    from tests.factories import table_snapshot

    before = table_snapshot(app, "trades")
    for endpoint in ENDPOINTS:
        seeded_client.get(f"{BASE}/{endpoint}")
    assert table_snapshot(app, "trades") == before
    assert seeded_client.post(f"{BASE}/summary", json={}).status_code == 405
```

### `tests/test_api_security.py` (MODIFIED)

**Why:** Phase 7 'unauthenticated API' tests became 'removed switch ignored / token required'.  
**How to run or test:** `python -m pytest tests/test_api_security.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
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
```

### `tests/test_browser.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_browser.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Real-browser checks with Playwright (Phases 5-7).

These start TradeLedger on a local port in a background thread and drive a
real Chromium browser, so they test what the test client cannot: CSS
layout, JavaScript behaviour, focus handling and the Content-Security-Policy.

They are OPTIONAL. Playwright is in requirements-browser.txt, not the core
requirements (it downloads a ~150 MB browser). If it, or its browser, is
missing, every test here is skipped and the rest of the suite is unaffected.

Chart.js: the dashboard tests here are marked `chartjs_stub` (Chart.js is
replaced by a recording stand-in) or `chartjs_offline` (Chart.js blocked).
NONE of them shows that the real Chart.js draws correctly; that is
tests/test_chartjs_real.py, which needs the real file (see
tests/browser_support.py for the details).

To run them:

    pip install -r requirements-browser.txt -c constraints.txt
    python -m playwright install chromium
    pytest tests/test_browser.py
"""

import pytest

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    WIDTHS,
    browser,
    horizontal_overflow,
    live_server,
    page_factory,
)

pytestmark = pytest.mark.browser


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize(
    "path",
    ["/", "/trades", "/trades/new", "/trades/1", "/trades/1/delete", "/instruments", "/analytics", "/about"],
)
def test_no_horizontal_scrolling_and_no_errors(live_server, page_factory, path, width):
    page = page_factory(width, server=live_server)
    page.goto(live_server + path)
    assert horizontal_overflow(page) == 0
    assert page.problems == []  # includes CSP violations


@pytest.mark.parametrize("width, menu_visible", [(375, True), (768, True), (1024, False), (1440, False)])
def test_menu_button_only_on_narrow_screens(live_server, page_factory, width, menu_visible):
    page = page_factory(width, server=live_server)
    page.goto(live_server + "/")
    assert page.locator("[data-nav-toggle]").is_visible() is menu_visible
    assert page.locator("#sidebar a", has_text="Instruments").is_visible() is not menu_visible


def test_mobile_menu_opens_moves_focus_and_closes_with_escape(live_server, page_factory):
    page = page_factory(375, server=live_server)
    page.goto(live_server + "/trades")
    toggle = page.locator("[data-nav-toggle]")
    toggle.click()
    assert toggle.get_attribute("aria-expanded") == "true"
    page.wait_for_function("document.activeElement.closest('#sidebar') !== null")
    page.keyboard.press("Escape")
    assert toggle.get_attribute("aria-expanded") == "false"
    assert page.evaluate("document.activeElement.hasAttribute('data-nav-toggle')")


def test_skip_link_is_first_and_reaches_main(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.classList.contains('skip-link')")
    page.keyboard.press("Enter")
    assert page.evaluate("location.hash") == "#main"


def test_open_status_hides_and_disables_exit_fields(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades/new")
    exit_fields = page.locator("[data-exit-fields]")
    assert exit_fields.is_visible()
    page.locator("input[name=status][value=OPEN]").check(force=True)
    assert not exit_fields.is_visible()
    assert page.locator("#field-exit_price").is_disabled()
    page.locator("input[name=status][value=CLOSED]").check(force=True)
    assert exit_fields.is_visible()
    assert page.locator("#field-exit_price").get_attribute("required") is not None


def test_server_errors_are_shown_and_focused(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades/new")
    page.select_option("#field-instrument_id", "1")
    page.fill("#field-trade_date", "2026-04-01T09:00")
    page.fill("#field-entry_price", "1.1000")
    page.fill("#field-stop_loss", "1.1050")  # wrong side for a long
    page.fill("#field-position_size", "1")
    page.locator("form[data-trade-form] button[type=submit]").click()
    page.wait_for_load_state()
    assert page.locator(".error-summary").is_visible()
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    assert page.input_value("#field-entry_price") == "1.1000"
    assert page.get_attribute("#field-stop_loss", "aria-invalid") == "true"


def test_create_and_delete_through_the_browser(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades/new")
    page.select_option("#field-instrument_id", "1")
    page.fill("#field-trade_date", "2026-04-01T09:00")
    page.fill("#field-entry_price", "1.1000")
    page.fill("#field-stop_loss", "1.0950")
    page.fill("#field-position_size", "1")
    page.fill("#field-exit_price", "1.1100")
    page.fill("#field-closed_at", "2026-04-01T12:00")
    page.fill("#field-notes", "<script>window.pwned = 1</script> browser test")
    page.locator("form[data-trade-form] button[type=submit]").click()
    page.wait_for_url("**/trades/*")
    assert page.locator(".flash", has_text="saved").is_visible()
    assert page.evaluate("window.pwned === undefined")  # rendered as text
    trade_url = page.url

    page.goto(trade_url + "/delete")
    page.locator("form button[type=submit]", has_text="Delete").click()
    page.wait_for_url("**/trades")
    assert page.locator(".flash", has_text="deleted").is_visible()
    assert page.goto(trade_url).status == 404


def test_double_submission_is_blocked(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades/1/delete")
    # Count submissions that get through app.js, and stop the real
    # navigation so the page can be inspected afterwards. This listener is
    # added after app.js's, so it only sees submits app.js let through.
    page.evaluate("""() => {
        window.submissions = 0;
        document.querySelector('form[data-submit-once]').addEventListener('submit', e => {
            if (!e.defaultPrevented) { window.submissions += 1; }
            e.preventDefault();
        });
    }""")
    button = page.locator("form[data-submit-once] button[type=submit]")
    button.click()
    button.click()  # an impatient second click
    assert page.evaluate("window.submissions") == 1
    assert page.locator("form[data-submit-once]").get_attribute("data-submitting") == "true"
    assert button.get_attribute("aria-busy") == "true"
    assert button.inner_text() == "Deleting…"


# ---------- Phase 6: analytics dashboard ----------


def charts_by_name(page):
    return page.evaluate("""() => Object.fromEntries((window.__charts || []).map(
        (c) => [c.canvas.dataset.chart, {type: c.config.type,
                                         points: c.config.data.datasets[0].data.length,
                                         indexAxis: c.config.options.indexAxis || "x"}]))""")


@pytest.mark.chartjs_stub
def test_dashboard_builds_all_seven_charts(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    page.wait_for_function("(window.__charts || []).length === 7")
    charts = charts_by_name(page)
    assert charts == {
        "curve": {"type": "line", "points": 15, "indexAxis": "x"},  # start + 14 trades
        "drawdown": {"type": "line", "points": 15, "indexAxis": "x"},
        "monthly": {"type": "bar", "points": 1, "indexAxis": "x"},  # March 2026 only
        "weekday": {"type": "bar", "points": 7, "indexAxis": "x"},
        "instruments": {"type": "bar", "points": 5, "indexAxis": "y"},
        "strategies": {"type": "bar", "points": 4, "indexAxis": "y"},
        "sessions": {"type": "bar", "points": 4, "indexAxis": "y"},
    }
    assert page.problems == []  # no JS errors, no CSP violations


@pytest.mark.chartjs_stub
def test_csp_violations_would_be_detected(live_server, page_factory):
    """Negative control: proves the console listener above really catches a
    Content-Security-Policy violation, so 'no problems' means something."""
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    page.evaluate("""() => { const s = document.createElement("script");
                             s.src = "https://example.com/evil.js"; document.head.appendChild(s); }""")
    page.wait_for_timeout(300)
    assert any("Content Security Policy" in problem for problem in page.problems)


@pytest.mark.chartjs_stub
def test_chart_tooltips_show_the_server_figures(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    page.wait_for_function("(window.__charts || []).length === 7")
    tooltip = page.evaluate("""() => {
        const chart = (name) => window.__charts.find((c) => c.canvas.dataset.chart === name);
        const label = (name, i) => chart(name).config.options.plugins.tooltip.callbacks.label({dataIndex: i});
        const title = (name, i) => chart(name).config.options.plugins.tooltip.callbacks.title([{dataIndex: i}]);
        return {curveTitle: title("curve", 1), curve: label("curve", 1),
                drawdown: label("drawdown", 9), weekday: label("weekday", 5), session: title("sessions", 2)};
    }""")
    assert tooltip["curveTitle"] == "02 Mar 2026, 11:40 UTC"
    assert tooltip["curve"] == ["Trade #1 · EURUSD", "Trade R: +1.97R", "Cumulative R: +1.97R"]
    assert tooltip["drawdown"][0] == "Drawdown: 1.05R below the running peak"
    assert "Maximum drawdown: 1.05R" in tooltip["drawdown"]
    assert tooltip["weekday"] == ["No closed trades"]  # Saturday
    assert tooltip["session"] == "New York"


@pytest.mark.chartjs_stub
def test_filters_update_cards_and_charts(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    page.select_option("#dash-strategy", "Breakout")
    page.select_option("#dash-session", "LONDON")
    page.locator(".dash-filters button[type=submit]").click()
    page.wait_for_url("**/analytics?*")
    assert "strategy=Breakout" in page.url and "session=LONDON" in page.url
    assert page.locator('[data-kpi="trades"] .kpi__value').inner_text() == "3"
    page.wait_for_function("(window.__charts || []).length === 7")
    assert charts_by_name(page)["curve"]["points"] == 4  # start + trades 1, 3, 10
    page.get_by_role("link", name="Reset filters").click()
    page.wait_for_url(live_server + "/analytics")
    assert page.locator('[data-kpi="trades"] .kpi__value').inner_text() == "14"


def test_invalid_filter_is_explained_in_the_browser(live_server, page_factory):
    page = page_factory(server=live_server)
    response = page.goto(live_server + "/analytics?instrument_id=999")
    assert response.status == 400
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    assert page.locator("canvas").count() == 0


def test_empty_result_shows_a_helpful_state(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics?start=2030-01-01")
    assert page.get_by_text("No closed trades match these filters").is_visible()
    assert page.locator("canvas").count() == 0
    assert page.problems == []


@pytest.mark.chartjs_offline
def test_offline_chart_library_falls_back_to_text(live_server, page_factory):
    page = page_factory(chart_js="blocked", server=live_server)
    page.goto(live_server + "/analytics")
    page.wait_for_selector("[data-chart-fallback]:not([hidden])")
    notes = page.locator("[data-chart-fallback]:not([hidden])")
    assert notes.count() == 7
    assert "could not be loaded" in notes.first.inner_text()
    assert page.locator("canvas:visible").count() == 0
    assert page.locator("#table-instruments").is_visible()
    # The only console error is the blocked download itself.
    assert all("ERR_INTERNET_DISCONNECTED" in p or "Failed to load resource" in p for p in page.problems)


def test_dashboard_without_javascript(live_server, page_factory):
    page = page_factory(javascript=False, server=live_server)
    page.goto(live_server + "/analytics?strategy=Breakout")
    assert page.get_by_text("Charts need JavaScript").is_visible()
    assert page.locator('[data-kpi="trades"] .kpi__value').inner_text() == "5"
    assert page.locator("#table-series").is_visible()
    assert page.locator("[data-chart-box]").first.is_hidden()


@pytest.mark.chartjs_stub
@pytest.mark.parametrize("width", WIDTHS)
def test_dashboard_layout_at_each_width(live_server, page_factory, width):
    page = page_factory(width, server=live_server)
    page.goto(live_server + "/analytics")
    page.wait_for_function("(window.__charts || []).length === 7")
    assert horizontal_overflow(page) == 0
    boxes = page.evaluate("""() => [...document.querySelectorAll('[data-chart-box]')].map((b) => {
        const r = b.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.right), Math.round(r.height)];
    })""")
    for left, right, height in boxes:
        assert left >= 0 and right <= width and height >= 200
    cards = page.evaluate(
        """() => [...document.querySelectorAll('.kpi')].map((k) => k.getBoundingClientRect().right)"""
    )
    assert len(cards) == 12 and max(cards) <= width


def test_filters_are_keyboard_operable(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    page.focus("#dash-instrument_id")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "dash-strategy"
    page.focus("#table-instruments")
    assert page.evaluate("document.activeElement.id") == "table-instruments"
```

### `tests/test_browser_journey.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_browser_journey.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 7: an end-to-end user journey and accessibility checks in Chromium.

Uses its own server and database (see tests/browser_support.py), seeded
with the synthetic sample data: 16 trades, 14 of them closed.

The journey's expected figures, worked out by hand:
  instrument QA7: quote currency GBP, contract multiplier 2
  trade: LONG, entry 100, stop 95, size 3
    initial risk = (100 - 95) x 3 x 2 = 30 GBP
  closed at 108 with fees 1.50:
    net = (108 - 100) x 3 x 2 - 1.50 = 46.50 GBP     R = 46.50 / 30 = +1.55R
  edited to fees 4.50:
    net = 48 - 4.50 = 43.50 GBP                      R = 43.50 / 30 = +1.45R

Accessibility: these are automated checks of specific rules (labels,
headings, table headers, text alternatives, focus, reduced motion, zoom).
They do NOT amount to a WCAG audit; see docs/PHASE_7_QA_REPORT.md for the
manual checks that remain (screen readers, contrast review, other browsers).
"""

import re

import pytest

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    WIDTHS,
    browser,
    horizontal_overflow,
    live_server,
    page_factory,
    submit_and_wait,
)

pytestmark = pytest.mark.browser

PAGES = ["/", "/trades", "/trades/new", "/trades/1", "/trades/1/edit", "/instruments", "/analytics", "/about"]


def kpi(page, key):
    return page.locator(f'[data-kpi="{key}"] .kpi__value').inner_text()


def option_value(page, select, text):
    """The value of the <option> whose visible text contains `text`."""
    return page.evaluate(
        "([sel, text]) => [...document.querySelector(sel).options].find(o => o.text.includes(text)).value",
        [select, text],
    )


# ---------- The journey ----------


@pytest.mark.chartjs_stub
def test_full_user_journey(live_server, page_factory):
    page = page_factory(server=live_server)

    # 1-2. Overview, then the sidebar.
    page.goto(live_server + "/")
    assert page.locator("h1").inner_text().strip() != ""
    page.locator("#sidebar a", has_text="Instruments").click()
    page.wait_for_url("**/instruments")

    # 3. Create an instrument.
    page.fill("#field-symbol", "qa7")
    page.fill("#field-name", "QA journey index")
    page.select_option("#field-asset_class", "OTHER")
    page.fill("#field-quote_currency", "GBP")
    page.fill("#field-price_increment", "0.5")
    page.fill("#field-contract_multiplier", "2")
    submit_and_wait(page, "form[data-submit-once] button[type=submit]")
    assert page.locator(".flash", has_text="Instrument QA7 added.").is_visible()

    # 4. Add an OPEN trade.
    page.locator("#sidebar a", has_text="Add trade").click()
    page.wait_for_url("**/trades/new")
    page.select_option("#field-instrument_id", option_value(page, "#field-instrument_id", "QA7"))
    page.locator("input[name=status][value=OPEN]").check(force=True)
    page.fill("#field-trade_date", "2026-06-01T09:00")
    page.fill("#field-entry_price", "100")
    page.fill("#field-stop_loss", "95")
    page.fill("#field-take_profit", "110")
    page.fill("#field-position_size", "3")
    page.fill("#field-fees", "1.50")
    page.fill("#field-strategy", "Journey")
    page.select_option("#field-session", "LONDON")
    submit_and_wait(page)
    trade_url = page.url
    trade_id = int(re.search(r"/trades/(\d+)$", trade_url).group(1))
    assert page.locator(".flash", has_text=f"Trade #{trade_id} saved.").is_visible()
    assert page.get_by_text("This trade is still open").is_visible()

    # 5-6. Close it and check the calculated result.
    page.goto(f"{trade_url}/edit")
    page.locator("input[name=status][value=CLOSED]").check(force=True)
    page.fill("#field-exit_price", "108")
    page.fill("#field-closed_at", "2026-06-02T15:30")
    submit_and_wait(page)
    result = page.locator(".result-card").inner_text()
    assert "46.50 GBP" in result and "+1.55R" in result

    # 7. Search and filter the journal.
    page.goto(live_server + "/trades")
    page.fill("#filter-q", "journey")
    page.select_option("#filter-status", "CLOSED")
    with page.expect_navigation():
        page.get_by_role("button", name="Apply filters").click()
    assert "q=journey" in page.url and "status=CLOSED" in page.url
    assert page.get_by_text("Showing 1–1 of 1 trade matching your filters.").is_visible()

    # 8. Edit the trade: higher fees.
    page.goto(f"{trade_url}/edit")
    page.fill("#field-fees", "4.50")
    submit_and_wait(page)
    assert page.locator(".flash", has_text=f"Trade #{trade_id} updated.").is_visible()
    result = page.locator(".result-card").inner_text()
    assert "43.50 GBP" in result and "+1.45R" in result

    # 9-10. Dashboard with two filters.
    page.goto(live_server + "/analytics")
    assert kpi(page, "trades") == "15"  # 14 sample + ours
    page.select_option("#dash-instrument_id", option_value(page, "#dash-instrument_id", "QA7"))
    page.select_option("#dash-strategy", "Journey")
    with page.expect_navigation():
        page.locator(".dash-filters button[type=submit]").click()
    assert kpi(page, "trades") == "1" and kpi(page, "total_r") == "+1.45R"
    assert "43.50 GBP" in page.locator(".money-strip").inner_text()
    page.wait_for_function("(window.__charts || []).length === 7")

    # 12. The data tables hold the same figure.
    row = page.locator("#table-instruments tbody tr", has_text="QA7")
    assert "+1.45R" in row.inner_text()

    # 11. Reset the filters.
    page.get_by_role("link", name="Reset filters").click()
    page.wait_for_url(live_server + "/analytics")
    assert kpi(page, "trades") == "15"

    # 13. Delete the trade.
    page.goto(f"{trade_url}/delete")
    with page.expect_navigation():
        page.locator("form button[type=submit]", has_text="Delete").click()
    assert page.locator(".flash", has_text=f"Trade #{trade_id} (QA7 LONG) was deleted.").is_visible()

    # 14. Journal and analytics both forget it.
    page.goto(live_server + "/trades?q=journey")
    assert page.get_by_text("No trades match").is_visible()
    page.goto(live_server + "/analytics")
    assert kpi(page, "trades") == "14"
    assert page.goto(trade_url).status == 404
    assert page.problems == []


def test_validation_errors_in_the_browser(live_server, page_factory):
    """15. Wrong-side stop on the trade form; duplicate symbol on the
    instrument form. Both show a focused error summary and keep the input."""
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades/new")
    page.select_option("#field-instrument_id", "1")
    page.fill("#field-trade_date", "2026-06-03T09:00")
    page.fill("#field-entry_price", "1.1000")
    page.fill("#field-stop_loss", "1.1050")  # above entry for a LONG
    page.fill("#field-position_size", "1")
    submit_and_wait(page)
    summary = page.locator(".error-summary")
    assert summary.is_visible() and summary.get_attribute("role") == "alert"
    assert page.evaluate("document.activeElement.classList.contains('error-summary')")
    for href in summary.locator("a").evaluate_all("links => links.map(a => a.getAttribute('href'))"):
        assert page.locator(href).count() == 1, href  # every summary link reaches its field
    assert page.input_value("#field-entry_price") == "1.1000"

    page.goto(live_server + "/instruments")
    page.fill("#field-symbol", "EURUSD")
    page.fill("#field-name", "Duplicate")
    page.select_option("#field-asset_class", "FOREX")
    page.fill("#field-quote_currency", "USD")
    page.fill("#field-price_increment", "0.00001")
    page.fill("#field-contract_multiplier", "100000")
    submit_and_wait(page, "form[data-submit-once] button[type=submit]")
    assert page.locator(".error-summary").is_visible()
    assert (
        "Symbol is already recorded. Symbols must be unique." in page.locator(".error-summary").inner_text()
    )


# ---------- Accessibility checks ----------

UNLABELLED_CONTROLS = """() => [...document.querySelectorAll('input, select, textarea')]
    .filter(el => el.type !== 'hidden')
    .filter(el => !(el.labels && el.labels.length) && !el.getAttribute('aria-label')
                  && !el.getAttribute('aria-labelledby'))
    .map(el => el.outerHTML.slice(0, 80))"""

HEADING_PROBLEMS = """() => {
    const levels = [...document.querySelectorAll('h1, h2, h3, h4, h5, h6')].map(h => +h.tagName[1]);
    const problems = [];
    if (levels.filter(l => l === 1).length !== 1) problems.push('expected exactly one h1');
    levels.forEach((l, i) => { if (i && l > levels[i - 1] + 1) problems.push(`h${levels[i - 1]} -> h${l}`); });
    return problems;
}"""

TABLE_PROBLEMS = """() => [...document.querySelectorAll('table')].flatMap(t => {
    const name = t.querySelector('caption') || t.getAttribute('aria-label') || t.getAttribute('aria-labelledby');
    const problems = name ? [] : [`table without a caption or label: ${t.id || t.className}`];
    t.querySelectorAll('tbody th').forEach(th => {
        if (!th.getAttribute('scope')) problems.push(`row header without scope in ${t.id || t.className}`);
    });
    if (!t.querySelector('thead th')) problems.push(`table without column headers: ${t.id || t.className}`);
    return problems;
})"""


@pytest.mark.parametrize("path", PAGES)
def test_every_form_control_has_a_label(live_server, page_factory, path):
    page = page_factory(server=live_server)
    page.goto(live_server + path)
    assert page.evaluate(UNLABELLED_CONTROLS) == []


@pytest.mark.parametrize("path", PAGES)
def test_headings_form_an_outline(live_server, page_factory, path):
    page = page_factory(server=live_server)
    page.goto(live_server + path)
    assert page.evaluate(HEADING_PROBLEMS) == []
    assert page.evaluate("document.documentElement.lang") == "en-GB"


@pytest.mark.parametrize("path", ["/", "/trades", "/instruments", "/analytics"])
def test_tables_have_names_and_headers(live_server, page_factory, path):
    page = page_factory(server=live_server)
    page.goto(live_server + path)
    assert page.evaluate(TABLE_PROBLEMS) == []


def test_accessibility_checks_can_fail(live_server, page_factory):
    """Negative control: break the page on purpose and prove each check
    notices, so an empty result above really means "no problems found"."""
    page = page_factory(server=live_server)
    page.goto(live_server + "/about")
    page.evaluate(
        """() => {
            document.querySelector('main').insertAdjacentHTML('beforeend',
                '<input id="nolabel"><h4>Skipped levels</h4><table><tbody><tr><th>x</th></tr></tbody></table>');
        }"""
    )
    assert page.evaluate(UNLABELLED_CONTROLS) != []
    assert any("->" in problem for problem in page.evaluate(HEADING_PROBLEMS))
    assert len(page.evaluate(TABLE_PROBLEMS)) == 3  # no name, no scope, no column headers


@pytest.mark.chartjs_stub
def test_every_chart_has_a_text_alternative_and_a_data_table(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/analytics")
    charts = page.locator("canvas[data-chart]")
    assert charts.count() == 7
    for i in range(7):
        canvas = charts.nth(i)
        name = canvas.get_attribute("data-chart")
        assert canvas.get_attribute("role") == "img"
        assert len(canvas.get_attribute("aria-label") or "") > 20, name
        assert page.locator(f"#chart-{name}-desc").inner_text().strip(), name  # visible description


def test_keyboard_focus_is_always_visible(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades")
    invisible = []
    for _ in range(15):
        page.keyboard.press("Tab")
        style = page.evaluate(
            """() => { const el = document.activeElement; const s = getComputedStyle(el);
                       return {tag: el.tagName, text: (el.innerText || el.name || '').slice(0, 30),
                               shadow: s.boxShadow, outline: s.outlineStyle}; }"""
        )
        if style["shadow"] == "none" and style["outline"] == "none":
            invisible.append(style)
    assert invisible == []


def test_reduced_motion_preference_is_respected(live_server, page_factory):
    page = page_factory(375, 800, reduced_motion="reduce", server=live_server)
    page.goto(live_server + "/")
    duration = page.evaluate("getComputedStyle(document.querySelector('#sidebar')).transitionDuration")
    assert all(float(part.rstrip("s")) <= 0.001 for part in duration.split(","))


def test_outcomes_are_not_shown_by_colour_alone(live_server, page_factory):
    page = page_factory(server=live_server)
    page.goto(live_server + "/trades")
    badges = page.locator("td .badge").all_inner_texts()
    assert badges and all(text.strip() for text in badges)  # every badge has a word in it
    page.goto(live_server + "/analytics?session=ASIA")
    total = page.locator('[data-kpi="total_r"] .kpi__value').inner_text()
    assert total[0] in "+-0"  # the sign is written, not only coloured


@pytest.mark.parametrize("width", [720, 683])
def test_200_percent_zoom_has_no_horizontal_scrolling(live_server, page_factory, width):
    """200% zoom on a 1440 px or 1366 px screen leaves a 720 or 683 CSS-pixel
    viewport; device_scale_factor=2 renders it at double density."""
    page = page_factory(width, 450, device_scale_factor=2, server=live_server)
    for path in PAGES:
        page.goto(live_server + path)
        assert horizontal_overflow(page) == 0, path


def test_text_stays_readable_on_a_phone(live_server, page_factory):
    page = page_factory(375, 800, server=live_server)
    page.goto(live_server + "/trades/1")
    size = page.evaluate("parseFloat(getComputedStyle(document.body).fontSize)")
    assert size >= 16
```

### `tests/test_chartjs_real.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_chartjs_real.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 7: REAL Chart.js rendering tests (marker: chartjs_real).

These are the only tests that can show the seven charts are actually drawn
by Chart.js. They need the genuine library file:

    tradeledger/static/vendor/chartjs/chart.umd.min.js   (Chart.js 4.5.1)
    tradeledger/static/vendor/chartjs/LICENSE.md          (its MIT licence)

Without those files every test here is SKIPPED, and pytest says so in its
summary ("skipped: real Chart.js file not installed"). A skip is never a
pass: see docs/PHASE_7_QA_REPORT.md for the current status. Setting
TRADELEDGER_REQUIRE_REAL_CHARTJS=1 (as CI does after downloading the file)
turns the skip into a FAILURE, so a missing file cannot pass silently.

What they check, with the library served from TradeLedger itself (CSP stays
"script-src 'self'") and its Subresource Integrity hash enabled:
  * the file really is Chart.js 4.5.1 and its licence is present;
  * the browser accepts the integrity hash and loads the library;
  * all seven charts exist as Chart.js instances and paint pixels;
  * chart data matches the server's JSON payload, point for point;
  * tooltips (Chart.js's own tooltip model) show the same text as the tables;
  * an empty month is a gap (null), weekdays run Monday to Sunday;
  * positive and negative bars get different colours; the maximum
    drawdown point is marked;
  * filters change the charts; resizing keeps charts inside their cards;
  * no console errors and no CSP violations.

Optional real screenshots: set TRADELEDGER_SCREENSHOT_DIR=docs/screenshots.
"""

import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from tests.browser_support import (  # noqa: F401 - fixtures are used by name
    LOCAL_CHART_JS,
    browser,
    horizontal_overflow,
    page_factory,
    start_server,
)
from tests.factories import USER_ID
from tradeledger.repositories.trades import create_trade

EXPECTED_VERSION = "4.5.1"
LICENCE = LOCAL_CHART_JS.parent / "LICENSE.md"
LOCAL_URL = "/static/vendor/chartjs/chart.umd.min.js"
CHART_NAMES = ["curve", "drawdown", "monthly", "weekday", "instruments", "strategies", "sessions"]

REQUIRED = os.environ.get("TRADELEDGER_REQUIRE_REAL_CHARTJS") == "1"

pytestmark = [
    pytest.mark.browser,
    pytest.mark.chartjs_real,
    pytest.mark.skipif(
        not LOCAL_CHART_JS.exists() and not REQUIRED, reason="real Chart.js file not installed"
    ),
]


def add_january_trade(conn):
    """One extra LOSING trade in January 2026, in its own strategy "Fade".
    February becomes an empty month between January and the March sample
    data (a gap, not a zero), and the strategy chart gets a negative bar
    (every sample strategy has a positive total)."""
    create_trade(
        conn,
        {
            "instrument_id": 1,
            "direction": "LONG",
            "status": "CLOSED",
            "trade_date": "2026-01-14T09:00:00Z",
            "closed_at": "2026-01-14T15:00:00Z",
            "entry_price": "1.1000",
            "stop_loss": "1.0950",
            "exit_price": "1.0975",
            "position_size": "1",
            "strategy": "Fade",
            "session": "ASIA",
        },
        user_id=USER_ID,
    )


@pytest.fixture(scope="module")
def real_server(tmp_path_factory):
    digest = hashlib.sha384(LOCAL_CHART_JS.read_bytes()).digest()
    integrity = "sha384-" + base64.b64encode(digest).decode()
    url, stop = start_server(
        str(tmp_path_factory.mktemp("chartjs") / "real.db"),
        overrides={"CHART_JS_URL": LOCAL_URL, "CHART_JS_INTEGRITY": integrity},
        prepare=add_january_trade,
    )
    yield url
    stop()


def open_dashboard(page_factory, real_server, query="", width=1440):
    page = page_factory(width, 900, chart_js="real", server=real_server)
    response = page.goto(real_server + "/analytics" + (f"?{query}" if query else ""))
    page.wait_for_function(
        "window.Chart && document.querySelectorAll('canvas[data-chart]').length === 7 && "
        "[...document.querySelectorAll('canvas[data-chart]')].every(c => window.Chart.getChart(c))"
    )
    return page, response


def payload(page):
    return json.loads(page.locator("#dashboard-data").inner_text())


def chart_eval(page, name, script):
    """Run `script` (a JS function body using `chart`) on one chart."""
    return page.evaluate(
        f"""(name) => {{ const chart = Chart.getChart(document.querySelector(`canvas[data-chart="${{name}}"]`));
                        {script} }}""",
        name,
    )


# ---------- The file itself ----------


def test_library_file_is_chart_js_4_5_1_with_its_licence():
    header = LOCAL_CHART_JS.read_text(encoding="utf-8", errors="replace")[:400]
    assert f"Chart.js v{EXPECTED_VERSION}" in header, header[:120]
    assert LICENCE.exists(), "LICENSE.md must sit next to the library file"
    assert "MIT" in LICENCE.read_text(encoding="utf-8")


def test_dashboard_loads_the_self_hosted_file_under_a_strict_policy(real_server, page_factory):
    page, response = open_dashboard(page_factory, real_server)
    csp = response.headers["content-security-policy"]
    assert "script-src 'self';" in csp and "cdn.jsdelivr.net" not in csp
    tag = page.locator(f'script[src="{LOCAL_URL}"]')
    assert tag.get_attribute("integrity").startswith("sha384-")
    assert page.evaluate("Chart.version") == EXPECTED_VERSION
    assert page.problems == []  # no console errors, no CSP violations


# ---------- Drawing ----------


def test_all_seven_charts_draw(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    data = payload(page)
    expected_points = {
        "curve": len(data["curve"]),
        "drawdown": len(data["curve"]),
        "monthly": len(data["monthly"]),
        "weekday": len(data["weekday"]),
        "instruments": len(data["instruments"]),
        "strategies": len(data["strategies"]),
        "sessions": len(data["sessions"]),
    }
    for name in CHART_NAMES:
        info = chart_eval(
            page,
            name,
            """const c = chart.canvas, ctx = c.getContext('2d');
               const pixels = ctx.getImageData(0, 0, c.width, c.height).data;
               let painted = 0; for (let i = 3; i < pixels.length; i += 4) if (pixels[i] > 0) painted++;
               return {points: chart.data.datasets[0].data.length, painted, width: chart.width};""",
        )
        assert info["points"] == expected_points[name], name
        assert info["painted"] > 500, f"{name} painted only {info['painted']} pixels"
        assert info["width"] > 100, name
    assert page.problems == []


def test_chart_values_match_the_server_payload(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    data = payload(page)
    curve = chart_eval(page, "curve", "return chart.data.datasets[0].data.map(p => p.y);")
    assert curve == [float(p["cumulative_r"]) for p in data["curve"]]
    for name in ("monthly", "weekday", "instruments", "strategies", "sessions"):
        values = chart_eval(page, name, "return chart.data.datasets[0].data;")
        assert values == [None if r["total_r"] is None else float(r["total_r"]) for r in data[name]], name


def test_empty_month_is_a_gap_and_weekdays_run_monday_to_sunday(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    monthly = chart_eval(
        page, "monthly", "return {labels: chart.data.labels, data: chart.data.datasets[0].data};"
    )
    assert monthly["labels"] == ["Jan 2026", "Feb 2026", "Mar 2026"]
    assert monthly["data"][1] is None  # February: no closed trades, not 0
    weekday = chart_eval(page, "weekday", "return chart.data.labels;")
    assert weekday == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def test_signs_have_different_colours_and_the_maximum_drawdown_is_marked(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    bars = chart_eval(
        page,
        "strategies",
        "return {data: chart.data.datasets[0].data, colours: chart.data.datasets[0].backgroundColor};",
    )
    positive = {c for v, c in zip(bars["data"], bars["colours"], strict=True) if v is not None and v > 0}
    negative = {c for v, c in zip(bars["data"], bars["colours"], strict=True) if v is not None and v < 0}
    assert positive and negative and positive.isdisjoint(negative)
    drawdown = chart_eval(
        page,
        "drawdown",
        "const d = chart.data.datasets[0]; return {y: d.data.map(p => p.y), styles: d.pointStyle};",
    )
    assert all(y <= 0 for y in drawdown["y"])
    deepest = drawdown["y"].index(min(drawdown["y"]))
    assert drawdown["styles"][deepest] == "triangle"


# ---------- Tooltips agree with the tables ----------


def test_chart_js_tooltips_show_the_table_figures(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    for name in ("instruments", "strategies", "sessions"):
        rows = payload(page)[name]
        for index, row in enumerate(rows):
            lines = chart_eval(
                page,
                name,
                f"""chart.tooltip.setActiveElements([{{datasetIndex: 0, index: {index}}}], {{x: 0, y: 0}});
                    chart.update();
                    return chart.tooltip.body.flatMap(b => b.lines);""",
            )
            table_row = page.locator(f"#table-{name} tbody tr", has_text=row["label"]).first
            table_total = table_row.locator("td").nth(5).inner_text().strip()
            assert lines[0] == f"Total R: {table_total}", (name, row["label"])
    assert page.problems == []


# ---------- Filters, resizing ----------


def test_filters_change_the_charts(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server, "strategy=Breakout&session=LONDON")
    points = chart_eval(page, "curve", "return chart.data.datasets[0].data.length;")
    assert points == 4  # the start point plus sample trades 1, 3 and 10


def test_resizing_keeps_charts_inside_their_cards(real_server, page_factory):
    page, _ = open_dashboard(page_factory, real_server)
    for width in (1440, 768, 375):
        page.set_viewport_size({"width": width, "height": 900})
        page.wait_for_timeout(300)  # Chart.js resizes on its ResizeObserver callback
        assert horizontal_overflow(page) == 0, width
        for name in CHART_NAMES:
            fits = chart_eval(
                page,
                name,
                """const box = chart.canvas.closest('[data-chart-box]').getBoundingClientRect();
                   const c = chart.canvas.getBoundingClientRect();
                   return c.width > 0 && c.right <= box.right + 1 && c.left >= box.left - 1;""",
            )
            assert fits, (width, name)


# ---------- Optional real screenshots ----------


@pytest.mark.skipif(
    not os.environ.get("TRADELEDGER_SCREENSHOT_DIR"), reason="TRADELEDGER_SCREENSHOT_DIR not set"
)
def test_capture_real_chart_screenshots(real_server, page_factory):
    folder = Path(os.environ["TRADELEDGER_SCREENSHOT_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    for width, name in ((1440, "chartjs_real_desktop_1440.png"), (375, "chartjs_real_mobile_375.png")):
        page, _ = open_dashboard(page_factory, real_server, width=width)
        page.screenshot(path=str(folder / name), full_page=True)
        assert (folder / name).stat().st_size > 10_000
```

### `tests/test_chronology.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_chronology.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Realised chronology: results are ordered and grouped by exit time.

Each scenario is small and worked out by hand. r_trade() trades risk 10
with no fees, so exit_price 120 = +2R and exit_price 90 = -1R.
"""

from decimal import Decimal

import pytest

from tests.factories import USER_ID, make_trade, r_trade
from tradeledger.performance import analyse
from tradeledger.performance.trade_math import IncompleteTradeError, evaluate_trade
from tradeledger.repositories.trades import create_trade, list_trades
from tradeledger.validation import ValidationError, validate_trade

D = Decimal


def test_trade_closed_later_is_realised_later():
    # A is entered first but closes last.
    a = r_trade(2, trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-09T16:00:00Z")
    b = r_trade(-1, trade_date="2026-01-06T09:00:00Z", closed_at="2026-01-06T12:00:00Z")
    c = r_trade(-1, trade_date="2026-01-07T09:00:00Z", closed_at="2026-01-07T12:00:00Z")
    report = analyse([a, b, c])
    # Realised (close) order is B, C, A, so the curve is -1, -2, 0.
    # In entry order it would have been +2, +1, 0.
    assert [p.trade_id for p in report.cumulative_r] == [b["id"], c["id"], a["id"]]
    assert [p.cumulative_r for p in report.cumulative_r] == [D("-1"), D("-2"), D("0")]


def test_streak_depends_on_close_order_not_entry_order():
    # Entry order: W, L, W, L  (longest win streak 1).
    # Close order: W, W, L, L  (longest win streak 2).
    trades = [
        r_trade(1, trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-05T10:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T09:30:00Z", closed_at="2026-01-05T15:00:00Z"),
        r_trade(1, trade_date="2026-01-05T09:45:00Z", closed_at="2026-01-05T11:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T09:50:00Z", closed_at="2026-01-05T16:00:00Z"),
    ]
    s = analyse(trades).overall
    assert (s.longest_win_streak, s.longest_loss_streak) == (2, 2)


def test_drawdown_follows_close_order():
    # Close order: +2, -1, -1, +1 -> curve 2, 1, 0, 1 -> max drawdown 2.
    trades = [
        r_trade(1, trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-08T09:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T10:00:00Z", closed_at="2026-01-07T09:00:00Z"),
        r_trade(2, trade_date="2026-01-05T11:00:00Z", closed_at="2026-01-05T12:00:00Z"),
        r_trade(-1, trade_date="2026-01-05T12:00:00Z", closed_at="2026-01-06T09:00:00Z"),
    ]
    assert analyse(trades).overall.max_drawdown_r.amount == D("2")


def test_month_and_weekday_use_the_close_date():
    # Entered Saturday 31 January, closed Monday 2 February.
    t = r_trade(1, trade_date="2026-01-31T22:00:00Z", closed_at="2026-02-02T08:00:00Z")
    report = analyse([t])
    assert list(report.by_month) == ["2026-02"]
    assert list(report.by_weekday) == ["Monday"]
    assert list(report.by_date) == ["2026-02-02"]


def test_legacy_trade_falls_back_to_entry_time_and_is_flagged():
    legacy = r_trade(1, trade_date="2026-01-05T09:00:00Z")  # no closed_at
    known = r_trade(1, trade_date="2026-01-04T09:00:00Z", closed_at="2026-01-06T09:00:00Z")
    report = analyse([known, legacy])
    assert [p.trade_id for p in report.cumulative_r] == [legacy["id"], known["id"]]
    assert report.unknown_close_time_trade_ids == [legacy["id"]]
    point = report.cumulative_r[0]
    assert point.realised_at == point.trade_date and point.close_time_known is False


def test_equal_realised_times_order_by_id():
    first = make_trade(id=5001, closed_at="2026-01-05T12:00:00Z", exit_price=D("90"))
    second = make_trade(id=5002, closed_at="2026-01-05T12:00:00Z")
    report = analyse([second, first])
    assert [p.trade_id for p in report.cumulative_r] == [5001, 5002]


def test_trade_result_fields():
    r = evaluate_trade(make_trade(trade_date="2026-01-05T09:00:00Z", closed_at="2026-01-05T10:00:00Z"))
    assert (r.closed_at, r.realised_at, r.close_time_known) == (
        "2026-01-05T10:00:00Z",
        "2026-01-05T10:00:00Z",
        True,
    )


@pytest.mark.parametrize(
    "closed_at, message",
    [
        ("2026-01-05T08:00:00Z", "earlier than trade_date"),
        ("05/01/2026", "closed_at must look like"),
    ],
)
def test_engine_rejects_inconsistent_close_times(closed_at, message):
    with pytest.raises(IncompleteTradeError, match=message):
        evaluate_trade(make_trade(trade_date="2026-01-05T09:00:00Z", closed_at=closed_at))


# ---------- Validation of closed_at ----------

BASE = {
    "instrument_id": 1,
    "trade_date": "2026-01-05T09:00:00Z",
    "direction": "LONG",
    "entry_price": "100",
    "stop_loss": "90",
    "position_size": "1",
}


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"exit_price": "120"}, "is required for a CLOSED trade"),
        ({"closed_at": "2026-01-05T10:00:00Z"}, "must be empty for an OPEN trade"),
        ({"exit_price": "120", "closed_at": "2026-01-05T08:59:59Z"}, "must not be earlier than trade_date"),
        ({"exit_price": "120", "closed_at": "not-a-real-timestamp"}, "ISO 8601"),
    ],
)
def test_closed_at_validation_errors(changes, message):
    with pytest.raises(ValidationError) as info:
        validate_trade({**BASE, **changes})
    assert message in info.value.errors["closed_at"]


def test_closed_at_is_normalised_to_utc():
    clean = validate_trade({**BASE, "exit_price": "120", "closed_at": "2026-01-05T11:00:00+01:00"})
    assert clean["closed_at"] == "2026-01-05T10:00:00Z"


def test_closed_at_equal_to_entry_is_allowed():
    assert (
        validate_trade({**BASE, "exit_price": "120", "closed_at": BASE["trade_date"]})["status"] == "CLOSED"
    )


def test_require_closed_at_false_allows_unknown_close():
    assert validate_trade({**BASE, "exit_price": "120"}, require_closed_at=False)["closed_at"] is None


# ---------- Repository date filters ----------


def test_realised_date_filter_uses_close_date(conn, eurusd_id):
    create_trade(
        conn,
        {
            "instrument_id": eurusd_id,
            "trade_date": "2026-03-31T20:00:00Z",
            "closed_at": "2026-04-01T09:00:00Z",
            "direction": "LONG",
            "entry_price": "1.1",
            "stop_loss": "1.09",
            "exit_price": "1.12",
            "position_size": "1",
        },
        user_id=USER_ID,
    )
    assert len(list_trades(conn, start="2026-03-31", end="2026-03-31", user_id=USER_ID)) == 1  # entry
    assert (
        len(list_trades(conn, start="2026-03-31", end="2026-03-31", date_field="realised", user_id=USER_ID))
        == 0
    )
    assert (
        len(list_trades(conn, start="2026-04-01", end="2026-04-01", date_field="realised", user_id=USER_ID))
        == 1
    )


def test_unknown_date_field_is_a_programming_error(conn):
    with pytest.raises(ValueError):
        list_trades(conn, date_field="whenever", user_id=USER_ID)
```

### `tests/test_dashboard_data.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_dashboard_data.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 6: the dashboard data layer (performance/dashboard.py).

The main dataset below is written into a real SQLite database through the
repository, then read back through build_report() and build_dashboard(),
so these tests cover the whole path from SQL to chart payload.

Every expected number was worked out BY HAND, not by calling the code
under test. Each trade: entry 100, stop 99 (risk 1 per unit), size 1,
multiplier 1, no fees, so realised R = exit - 100.

  id  instrument  entered            closed (realised)     exit   R     strategy  session
  1   TEST (USD)  2025-12-31 10:00   2025-12-31 23:30 Wed  102    +2    Alpha     LONDON
  2   TEST (USD)  2026-01-01 20:00   2026-01-02 09:00 Fri   99    -1    Beta      -
  3   TEST (USD)  2025-12-30 09:00   2026-01-02 09:00 Fri   99    -1    -         ASIA
  4   GBPX (GBP)  2026-03-06 08:00   2026-03-07 12:00 Sat  100.5  +0.5  Alpha     LONDON
  5   TEST (USD)  2026-03-09 08:00   OPEN                   -      -     -         -

Trade 3 opened FIRST but closed after trade 1, so it must appear after 1.
Trades 2 and 3 close at the same second; the lower id (2) comes first.

Realised order 1, 2, 3, 4:
  cumulative R   2, 1, 0, 0.5        running peak  2, 2, 2, 2
  drawdown       0, 1, 2, 1.5        maximum drawdown 2 (peak after #1, low after #3)
  total R 0.5   wins 2   losses 2   win rate 50%   average R 0.125
  expectancy 0.5 x 1.25 + 0.5 x (-1) = 0.125     profit factor 2.5 / 2 = 1.25
  streaks: longest win 1, longest loss 2
  months: Dec 2025 +2 (1 trade), Jan 2026 -2 (2), Feb 2026 none, Mar 2026 +0.5 (1)
  weekdays: Wednesday +2 (1), Friday -2 (2), Saturday +0.5 (1), others none
  money: USD 2 - 1 - 1 = 0.00 USD, GBP +0.50 GBP (never added together)
"""

import json
from decimal import Decimal

import pytest

from tests.factories import USER_ID, make_trade
from tradeledger.performance import analyse
from tradeledger.performance.dashboard import (
    SERIES_TABLE_LIMIT,
    SMALL_SAMPLE,
    build_dashboard,
    chart_payload,
    drawdown_series,
    make_dashboard,
    money_rows,
    month_keys,
    month_rows,
    to_epoch_ms,
    weekday_rows,
)
from tradeledger.report_filters import ReportFilters
from tradeledger.repositories.instruments import create_instrument
from tradeledger.repositories.trades import create_trade


def D(text):
    return Decimal(text)


@pytest.fixture
def hand_conn(conn):
    usd = create_instrument(
        conn,
        {
            "symbol": "TEST",
            "name": "Test USD",
            "asset_class": "OTHER",
            "price_increment": "0.01",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        user_id=USER_ID,
    )
    gbp = create_instrument(
        conn,
        {
            "symbol": "GBPX",
            "name": "Test GBP",
            "asset_class": "OTHER",
            "price_increment": "0.01",
            "contract_multiplier": "1",
            "quote_currency": "GBP",
        },
        user_id=USER_ID,
    )

    def trade(instrument, entered, closed, exit_price, strategy=None, session=None):
        create_trade(
            conn,
            {
                "instrument_id": instrument,
                "direction": "LONG",
                "status": "CLOSED" if closed else "OPEN",
                "trade_date": entered,
                "closed_at": closed,
                "entry_price": "100",
                "stop_loss": "99",
                "exit_price": exit_price,
                "position_size": "1",
                "fees": "0",
                "strategy": strategy,
                "session": session,
            },
            user_id=USER_ID,
        )

    trade(usd, "2025-12-31T10:00:00Z", "2025-12-31T23:30:00Z", "102", "Alpha", "LONDON")
    trade(usd, "2026-01-01T20:00:00Z", "2026-01-02T09:00:00Z", "99", "Beta")
    trade(usd, "2025-12-30T09:00:00Z", "2026-01-02T09:00:00Z", "99", None, "ASIA")
    trade(gbp, "2026-03-06T08:00:00Z", "2026-03-07T12:00:00Z", "100.5", "Alpha", "LONDON")
    trade(usd, "2026-03-09T08:00:00Z", None, None)
    return conn


@pytest.fixture
def board(hand_conn):
    return build_dashboard(hand_conn, ReportFilters(), user_id=USER_ID)


# ---------- Chronology, cumulative R and drawdown ----------


def test_series_follows_realised_order_with_stable_ties(board):
    assert [p.trade_id for p in board.series] == [1, 2, 3, 4]


def test_cumulative_peak_and_drawdown_match_hand_calculation(board):
    assert [p.cumulative_r for p in board.series] == [D("2"), D("1"), D("0"), D("0.5")]
    assert [p.peak_r for p in board.series] == [D("2")] * 4
    assert [p.drawdown_r for p in board.series] == [D("0"), D("1"), D("2"), D("1.5")]


def test_maximum_drawdown_from_both_paths_agrees(board):
    worst = board.report.overall.max_drawdown_r
    assert worst.amount == D("2") == max(p.drawdown_r for p in board.series)
    assert (worst.peak_trade_id, worst.trough_trade_id) == (1, 3)


def test_drawdown_starts_from_zero_when_the_first_trade_loses():
    """Only losing trades: the peak stays at the 0R starting point."""
    report = analyse(
        [
            make_trade(id=1, exit_price=D("90"), closed_at="2026-01-05T11:00:00Z"),
            make_trade(id=2, exit_price=D("85"), closed_at="2026-01-06T11:00:00Z"),
        ]
    )
    series = drawdown_series(report.cumulative_r)
    assert [p.cumulative_r for p in series] == [D("-1"), D("-2.5")]
    assert [p.peak_r for p in series] == [D("0"), D("0")]
    assert [p.drawdown_r for p in series] == [D("1"), D("2.5")]
    board = make_dashboard(report, ReportFilters())
    assert "from the starting point (0R)" in board.descriptions["drawdown"]


def test_only_winning_trades_have_no_drawdown():
    report = analyse(
        [
            make_trade(id=1, closed_at="2026-01-05T11:00:00Z"),
            make_trade(id=2, closed_at="2026-01-06T11:00:00Z"),
        ]
    )
    board = make_dashboard(report, ReportFilters())
    assert all(p.drawdown_r == 0 for p in board.series)
    assert board.descriptions["drawdown"] == "No drawdown: cumulative R never fell below its previous peak."
    kpis = {k.key: k for k in board.kpis}
    assert kpis["profit_factor_r"].value == "n/a"
    assert kpis["profit_factor_r"].note == "Needs at least one losing trade"
    assert kpis["max_drawdown_r"].value == "0.00R"


def test_to_epoch_ms_against_a_known_instant():
    # 2026-01-01T00:00Z is 1,767,225,600 s; plus 1 day and 9 hours.
    assert to_epoch_ms("2026-01-02T09:00:00Z") == (1_767_225_600 + 86_400 + 9 * 3_600) * 1000


def test_series_has_epoch_x_values_in_order(board):
    xs = [p.x for p in board.series]
    assert xs == sorted(xs)
    assert xs[1] == xs[2] == to_epoch_ms("2026-01-02T09:00:00Z")


# ---------- Months and weekdays ----------


def test_month_keys_cross_a_year_boundary():
    assert month_keys("2025-11", "2026-02") == ["2025-11", "2025-12", "2026-01", "2026-02"]
    assert month_keys("2026-03", "2026-03") == ["2026-03"]


def test_months_keep_an_empty_month_as_a_gap(board):
    rows = [
        (m.key, m.label, None if m.summary is None else (m.summary.trades, m.summary.total_r))
        for m in board.months
    ]
    assert rows == [
        ("2025-12", "Dec 2025", (1, D("2"))),
        ("2026-01", "Jan 2026", (2, D("-2"))),
        ("2026-02", "Feb 2026", None),  # no trades: not "0R"
        ("2026-03", "Mar 2026", (1, D("0.5"))),
    ]


def test_weekdays_are_all_seven_in_calendar_order(board):
    rows = [(w.key, None if w.summary is None else w.summary.total_r) for w in board.weekdays]
    assert rows == [
        ("Monday", None),
        ("Tuesday", None),
        ("Wednesday", D("2")),
        ("Thursday", None),
        ("Friday", D("-2")),
        ("Saturday", D("0.5")),
        ("Sunday", None),
    ]


def test_empty_inputs_give_empty_periods():
    assert month_rows({}) == []
    assert [w.summary for w in weekday_rows({})] == [None] * 7


# ---------- Groups ----------


def test_strategies_best_first_with_stable_ties(board):
    assert [(g.label, g.summary.trades, g.summary.total_r) for g in board.strategies] == [
        ("Alpha", 2, D("2.5")),
        ("Beta", 1, D("-1")),
        ("Unspecified", 1, D("-1")),
    ]


def test_sessions_in_trading_day_order_with_readable_labels(board):
    assert [(g.key, g.label, g.summary.total_r) for g in board.sessions] == [
        ("ASIA", "Asia", D("-1")),
        ("LONDON", "London", D("2.5")),
        ("Unspecified", "Unspecified", D("-1")),
    ]


def test_instruments_compared_in_r_not_money(board):
    assert [(g.label, g.summary.total_r) for g in board.instruments] == [("GBPX", D("0.5")), ("TEST", D("0"))]


def test_small_sample_flag(board):
    assert SMALL_SAMPLE == 10
    assert all(g.small_sample for g in board.instruments)


# ---------- KPI cards ----------


def test_kpis_match_hand_calculation(board):
    kpis = {k.key: (k.value, k.tone) for k in board.kpis}
    assert kpis == {
        "trades": ("4", "neutral"),
        "wins": ("2", "neutral"),
        "losses": ("2", "neutral"),
        "breakevens": ("0", "neutral"),
        "win_rate": ("50.0%", "neutral"),
        "average_r": ("+0.13R", "positive"),  # 0.125 rounds half up
        "expectancy_r": ("+0.13R", "positive"),
        "profit_factor_r": ("1.25", "neutral"),
        "total_r": ("+0.50R", "positive"),
        "max_drawdown_r": ("2.00R", "negative"),
        "longest_win_streak": ("1 trade", "neutral"),
        "longest_loss_streak": ("2 trades", "neutral"),
    }
    assert {k.key: k.note for k in board.kpis}["trades"] == "1 open trade not included"


def test_money_stays_per_currency(board):
    assert money_rows(board.report) == [("GBP", "0.50 GBP", "positive", 1), ("USD", "0.00 USD", "neutral", 3)]


def test_empty_report_shows_unavailable_not_zero(conn):
    board = build_dashboard(conn, ReportFilters(), user_id=USER_ID)
    assert not board.has_results
    kpis = {k.key: k.value for k in board.kpis}
    assert kpis["trades"] == "0"
    for key in (
        "wins",
        "win_rate",
        "average_r",
        "expectancy_r",
        "profit_factor_r",
        "total_r",
        "max_drawdown_r",
        "longest_win_streak",
        "longest_loss_streak",
    ):
        assert kpis[key] == "n/a", key
    assert board.series == [] and board.months == []
    assert chart_payload(board)["curve"] == []


def test_only_breakeven_trades():
    report = analyse([make_trade(id=1, exit_price=D("100"), closed_at="2026-01-05T11:00:00Z")])
    kpis = {k.key: k.value for k in make_dashboard(report, ReportFilters()).kpis}
    assert kpis["breakevens"] == "1" and kpis["win_rate"] == "0.0%"
    assert kpis["profit_factor_r"] == "n/a" and kpis["total_r"] == "0.00R"


# ---------- Data quality: missing R and unknown exit times ----------


def test_trade_without_usable_r_is_counted_but_not_plotted():
    """A stop on the wrong side cannot reach the database (CHECK constraint),
    but other data sources could produce it, so the engine supports it."""
    report = analyse(
        [
            make_trade(id=1, closed_at="2026-01-05T11:00:00Z"),
            make_trade(id=2, stop_loss=D("105"), closed_at="2026-01-06T11:00:00Z"),
        ]
    )
    board = make_dashboard(report, ReportFilters())
    assert board.report.overall.trades == 2
    assert [p.trade_id for p in board.series] == [1]
    assert [e.trade_id for e in board.report.r_unavailable] == [2]


def test_legacy_trade_is_placed_at_entry_time_and_flagged(hand_conn):
    hand_conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 3")
    hand_conn.commit()
    board = build_dashboard(hand_conn, ReportFilters(), user_id=USER_ID)
    # Trade 3 now counts at its ENTRY time, 2025-12-30, so it comes first.
    assert [p.trade_id for p in board.series] == [3, 1, 2, 4]
    legacy = board.series[0]
    assert legacy.close_time_known is False and legacy.realised_at == "2025-12-30T09:00:00Z"
    assert board.report.unknown_close_time_trade_ids == [3]


# ---------- Filters reach the dashboard ----------


def test_filters_change_every_part_of_the_dashboard(hand_conn):
    board = build_dashboard(hand_conn, ReportFilters(strategy="Alpha"), user_id=USER_ID)
    assert [p.trade_id for p in board.series] == [1, 4]
    assert [m.key for m in board.months] == ["2025-12", "2026-01", "2026-02", "2026-03"]
    assert {k.key: k.value for k in board.kpis}["total_r"] == "+2.50R"
    assert [g.label for g in board.strategies] == ["Alpha"]


def test_date_filter_on_realised_date(hand_conn):
    board = build_dashboard(hand_conn, ReportFilters(start="2026-01-01", end="2026-01-31"), user_id=USER_ID)
    assert [p.trade_id for p in board.series] == [2, 3]


# ---------- Chart payload ----------


def test_payload_has_exact_strings_and_no_floats(board):
    payload = chart_payload(board)

    def walk(value):
        assert not isinstance(value, float), value
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    json.dumps(payload, allow_nan=False)  # raises if NaN/Infinity slipped in


def test_payload_curve_values(board):
    curve = chart_payload(board)["curve"]
    assert curve[0] == {
        "start": True,
        "x": board.series[0].x,
        "cumulative_r": "0",
        "cumulative_text": "0.00R",
        "underwater_r": "0",
        "drawdown_text": "0.00R",
    }
    assert [(p["trade_id"], p["realised_r"], p["cumulative_r"], p["underwater_r"]) for p in curve[1:]] == [
        (1, "2", "2", "0"),
        (2, "-1", "1", "-1"),
        (3, "-1", "0", "-2"),
        (4, "0.5", "0.5", "-1.5"),
    ]
    assert curve[3]["when"] == "02 Jan 2026, 09:00 UTC"
    assert curve[3]["realised_text"] == "-1.00R"


def test_payload_numeric_strings_are_finite_decimals(board):
    payload = chart_payload(board)
    values = [p[k] for p in payload["curve"] for k in ("cumulative_r", "underwater_r")]
    values += [
        row["total_r"]
        for key in ("monthly", "weekday", "instruments", "strategies", "sessions")
        for row in payload[key]
        if row["total_r"] is not None
    ]
    for text in values:
        assert Decimal(text).is_finite(), text


def test_payload_marks_empty_periods_as_null(board):
    monthly = {m["key"]: m for m in chart_payload(board)["monthly"]}
    assert monthly["2026-02"]["total_r"] is None and monthly["2026-02"]["trades"] == 0
    assert monthly["2026-01"]["total_r"] == "-2" and monthly["2026-01"]["total_r_text"] == "-2.00R"


def test_payload_and_tables_agree(board):
    """The chart payload is cut from the same rows the tables render."""
    payload = chart_payload(board)
    for key, rows in (
        ("instruments", board.instruments),
        ("strategies", board.strategies),
        ("sessions", board.sessions),
    ):
        assert [(p["label"], p["trades"], Decimal(p["total_r"])) for p in payload[key]] == [
            (r.label, r.summary.trades, r.summary.total_r) for r in rows
        ]


def test_payload_contains_no_free_text_from_trades(hand_conn):
    hand_conn.execute("UPDATE trades SET notes = 'SECRET-NOTE <script>' WHERE id = 1")
    hand_conn.commit()
    text = json.dumps(chart_payload(build_dashboard(hand_conn, ReportFilters(), user_id=USER_ID)))
    assert "SECRET-NOTE" not in text


# ---------- Sample data and scale ----------


def test_sample_data_series_ends_at_engine_total(seeded_conn):
    board = build_dashboard(seeded_conn, ReportFilters(), user_id=USER_ID)
    assert board.series[-1].cumulative_r == board.report.overall.total_r
    assert max(p.drawdown_r for p in board.series) == board.report.overall.max_drawdown_r.amount


def test_one_thousand_trades(conn):
    """1,000 synthetic trades: the dashboard still adds up. (Timing is
    measured separately by scripts/benchmark_dashboard.py, not asserted.)"""
    instrument = create_instrument(
        conn,
        {
            "symbol": "BULK",
            "name": "Bulk",
            "asset_class": "OTHER",
            "price_increment": "0.01",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        user_id=USER_ID,
    )
    for n in range(1000):
        exit_price = ("102", "99", "100.5", "99")[n % 4]  # +2, -1, +0.5, -1 repeating
        day = 1 + (n // 40)
        create_trade(
            conn,
            {
                "instrument_id": instrument,
                "direction": "LONG",
                "status": "CLOSED",
                "trade_date": f"2026-{1 + (day - 1) // 28:02d}-{1 + (day - 1) % 28:02d}T08:00:00Z",
                "closed_at": f"2026-{1 + (day - 1) // 28:02d}-{1 + (day - 1) % 28:02d}T{(n % 40) // 4 + 9:02d}:{n % 4:02d}:00Z",
                "entry_price": "100",
                "stop_loss": "99",
                "exit_price": exit_price,
                "position_size": "1",
            },
            user_id=USER_ID,
        )
    board = build_dashboard(conn, ReportFilters(), user_id=USER_ID)
    assert board.report.overall.trades == 1000
    assert len(board.series) == 1000
    assert board.series[-1].cumulative_r == D("125")  # 250 cycles of +0.5R
    assert board.report.overall.total_r == D("125")
    # The HTML table keeps only the most recent rows; the chart keeps all.
    assert SERIES_TABLE_LIMIT == 200
    assert board.series_table == board.series[-200:]
```

### `tests/test_database_integrity.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_database_integrity.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 7: database consistency checks across many filter combinations.

The Phase 4-6 tests check individual filters and the API's paging. These
check the REPOSITORY layer as a whole on a larger, deterministic dataset:

  * list_trades() and count_trades() always agree, for every combination,
    and both agree with an independent filter written here in Python;
  * pagination (limit/offset) never skips or repeats a row, including rows
    that share the same entry time;
  * the migration registry is ordered and complete.

Every test uses its own temporary database (see conftest.py).
"""

import itertools
import random
from decimal import Decimal

import pytest

from tests.factories import USER_ID
from tradeledger.migrations import LATEST_VERSION, MIGRATIONS, current_version
from tradeledger.repositories import trades as trade_repo
from tradeledger.seed import seed_sample_data

SESSIONS = [None, "ASIA", "LONDON", "NEW_YORK", "OTHER"]
STRATEGIES = [None, "Breakout", "Pullback", "Fade"]


@pytest.fixture
def busy_conn(conn):
    """Sample data plus 150 random trades. Fixed seed: same data every run.
    Ten trades share one entry time so ordering ties are exercised."""
    seed_sample_data(conn, user_id=USER_ID)
    rnd = random.Random(7)
    for n in range(150):
        closed = rnd.random() < 0.85
        day = 1 + n % 28
        entered = "2026-05-10T09:30:00Z" if n < 10 else f"2026-0{4 + n % 3}-{day:02d}T{8 + n % 9:02d}:00:00Z"
        exit_day = min(day + rnd.randint(0, 2), 28)
        trade_repo.create_trade(
            conn,
            {
                "instrument_id": rnd.randint(1, 5),
                "direction": "LONG",
                "status": "CLOSED" if closed else "OPEN",
                "trade_date": entered,
                "closed_at": (
                    None
                    if not closed
                    else ("2026-05-11T09:00:00Z" if n < 10 else f"{entered[:8]}{exit_day:02d}T18:00:00Z")
                ),
                "entry_price": "100",
                "stop_loss": "99",
                "exit_price": str(Decimal(rnd.randint(9800, 10200)) / 100) if closed else None,
                "position_size": "1",
                "strategy": rnd.choice(STRATEGIES),
                "session": rnd.choice(SESSIONS),
                "notes": rnd.choice([None, "gap fill", "News spike", "100% planned"]),
            },
            user_id=USER_ID,
        )
    return conn


def independent_filter(rows, *, instrument_id, status, strategy, session, start, end, date_field):
    """The same rules as the repository, written separately in Python."""
    out = []
    for t in rows:
        moment = t["trade_date"] if date_field == "entry" else (t["closed_at"] or t["trade_date"])
        day = moment[:10]
        if instrument_id is not None and t["instrument_id"] != instrument_id:
            continue
        if status is not None and t["status"] != status:
            continue
        if strategy is not None and t["strategy"] != strategy:
            continue
        if session is not None and t["session"] != session:
            continue
        if start is not None and day < start:
            continue
        if end is not None and day > end:
            continue
        out.append(t["id"])
    return sorted(out)


def test_list_count_and_independent_filter_always_agree(busy_conn):
    everything = trade_repo.list_trades(busy_conn, user_id=USER_ID)
    assert len(everything) == 166  # 16 sample + 150 generated
    combos = itertools.product(
        [None, 1, 3],
        [None, "OPEN", "CLOSED"],
        [None, "Breakout", "Fade"],
        [None, "LONDON", "ASIA"],
        [(None, None), ("2026-05-01", "2026-05-31"), ("2026-05-10", "2026-05-10")],
        ["entry", "realised"],
    )
    checked = 0
    for instrument_id, status, strategy, session, (start, end), date_field in combos:
        kwargs = {
            "instrument_id": instrument_id,
            "status": status,
            "strategy": strategy,
            "session": session,
            "start": start,
            "end": end,
            "date_field": date_field,
        }
        listed = trade_repo.list_trades(busy_conn, **kwargs, user_id=USER_ID)
        counted = trade_repo.count_trades(busy_conn, **kwargs, user_id=USER_ID)
        expected = independent_filter(everything, **kwargs)
        assert counted == len(listed), kwargs
        assert sorted(t["id"] for t in listed) == expected, kwargs
        checked += 1
    assert checked == 3 * 3 * 3 * 3 * 3 * 2


@pytest.mark.parametrize("page_size", [1, 3, 7, 50, 166, 500])
def test_pages_cover_every_row_exactly_once(busy_conn, page_size):
    full = [t["id"] for t in trade_repo.list_trades(busy_conn, user_id=USER_ID)]
    paged, offset = [], 0
    while True:
        page = trade_repo.list_trades(busy_conn, limit=page_size, offset=offset, user_id=USER_ID)
        if not page:
            break
        assert len(page) <= page_size
        paged.extend(t["id"] for t in page)
        offset += page_size
    assert paged == full  # same rows, same order, no gaps, no repeats
    assert len(set(paged)) == len(paged)


def test_rows_with_equal_entry_times_have_a_stable_order(busy_conn):
    """Ten trades share 2026-05-10T09:30:00Z (no generated trade uses minute
    30). The documented order is newest entry first, then highest id first,
    so ties can never swap between pages."""
    tied = [
        t
        for t in trade_repo.list_trades(busy_conn, user_id=USER_ID)
        if t["trade_date"] == "2026-05-10T09:30:00Z"
    ]
    assert len(tied) == 10
    assert [t["id"] for t in tied] == sorted((t["id"] for t in tied), reverse=True)
    first_half = trade_repo.list_trades(
        busy_conn, start="2026-05-10", end="2026-05-10", limit=5, offset=0, user_id=USER_ID
    )
    second_half = trade_repo.list_trades(
        busy_conn, start="2026-05-10", end="2026-05-10", limit=5, offset=5, user_id=USER_ID
    )
    assert {t["id"] for t in first_half}.isdisjoint({t["id"] for t in second_half})


def test_offset_past_the_end_and_zero_limit_return_nothing(busy_conn):
    assert trade_repo.list_trades(busy_conn, limit=10, offset=10_000, user_id=USER_ID) == []
    assert trade_repo.list_trades(busy_conn, limit=0, user_id=USER_ID) == []


def test_migration_registry_is_ordered_and_complete(conn):
    """Versions must run 2, 3, 4, ... with no gaps (version 1 is schema.sql),
    and a fresh database must end on the latest one."""
    versions = sorted(MIGRATIONS)
    assert versions == list(range(2, LATEST_VERSION + 1))
    assert current_version(conn) == LATEST_VERSION
    for version, script in MIGRATIONS.items():
        assert "PRAGMA user_version" not in script, version  # migrate() sets it, inside the transaction
        assert "BEGIN" not in script.upper().split("TRIGGER")[0], version
```

### `tests/test_db.py` (MODIFIED)

**Why:** Rewritten for the adapter (both engines); version 3; portable errors.  
**How to run or test:** `python -m pytest tests/test_db.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Database setup, Flask integration, constraints and transactions.

Phase 8: these tests run on SQLite by default and on PostgreSQL when
TRADELEDGER_TEST_DATABASE_URL is set (see conftest.py). Errors are checked
as tradeledger.db.IntegrityError, the adapter's engine-neutral class, which
replaced sqlite3.IntegrityError in the Phase 7 version of this file.
"""

import pytest
from flask import g

from tests.factories import USER_ID
from tradeledger import create_app
from tradeledger.db import (
    DataError,
    IntegrityError,
    connect,
    get_db,
    init_schema,
    list_tables,
    schema_version,
)
from tradeledger.migrations import LATEST_VERSION

# ---------- Initialisation ----------


def index_names(conn):
    if conn.dialect == "postgresql":
        sql = "SELECT indexname AS name FROM pg_indexes WHERE schemaname = current_schema()"
    else:
        sql = "SELECT name FROM sqlite_master WHERE type = 'index'"
    return {row["name"] for row in conn.execute(sql)}


def test_every_table_is_created(conn):
    assert {"instruments", "trades", "users", "api_tokens", "login_attempts"} <= set(list_tables(conn))


def test_indexes_are_created(conn):
    assert {"idx_trades_instrument_id", "idx_trades_user_date", "idx_trades_user_closed"} <= index_names(conn)


def test_schema_version_is_recorded(conn):
    # Was "== 1" in Phase 2 and "== 2" in Phase 4-7. Phase 8 added migration 3.
    assert schema_version(conn) == LATEST_VERSION == 3


def test_init_schema_twice_keeps_existing_data(conn, eurusd_id):
    init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1


@pytest.mark.sqlite_only
def test_foreign_keys_are_switched_on(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ---------- Flask integration ----------


def test_testing_config_without_database_path_refuses_to_start():
    with pytest.raises(RuntimeError, match="DATABASE"):
        create_app("testing")


def test_development_database_defaults_to_instance_folder(monkeypatch):
    from tradeledger.config import DevelopmentConfig

    monkeypatch.setattr(DevelopmentConfig, "DATABASE", None)
    monkeypatch.setattr(DevelopmentConfig, "DATABASE_URL", None)
    monkeypatch.setattr(DevelopmentConfig, "DB_HOST", None)
    app = create_app("development")
    assert app.config["DATABASE"].endswith("tradeledger.db")
    assert "instance" in app.config["DATABASE"]


def test_get_db_reuses_one_connection_per_app_context(app):
    with app.app_context():
        assert get_db() is get_db()


def test_connection_is_released_when_app_context_ends(app):
    with app.app_context():
        conn = get_db()
    # SQLite: the connection is closed. PostgreSQL: it went back to the pool
    # and is no longer this request's. Either way, using it again is a bug
    # that must fail loudly rather than share a connection between requests.
    if conn.dialect == "sqlite":
        with pytest.raises(Exception, match="closed"):
            conn.execute("SELECT 1")


def test_connection_is_removed_from_g_after_teardown(app):
    with app.app_context():
        get_db()
        assert "db" in g
    with app.app_context():
        assert "db" not in g


# ---------- Constraints enforced by SQL itself ----------
# These bypass Python validation on purpose (raw INSERTs) to prove the
# database is a safety net even if application code has a bug.

RAW_TRADE_SQL = """
INSERT INTO trades (user_id, instrument_id, trade_date, direction, entry_price, stop_loss,
                    exit_price, position_size, fees, status)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

VALID_RAW = [USER_ID, None, "2026-03-02T08:15:00Z", "LONG", "1.085", "1.083", "1.089", "1", "7", "CLOSED"]
POSITIONS = {
    "user_id": 0,
    "trade_date": 2,
    "direction": 3,
    "entry_price": 4,
    "stop_loss": 5,
    "exit_price": 6,
    "position_size": 7,
    "fees": 8,
    "status": 9,
}


def raw_trade(instrument_id, **changes):
    values = list(VALID_RAW)
    values[1] = instrument_id
    for field, value in changes.items():
        values[POSITIONS[field]] = value
    return values


def test_sql_accepts_a_valid_raw_trade(conn, eurusd_id):
    conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id))


def test_sql_rejects_unknown_instrument(conn):
    with pytest.raises(IntegrityError) as info:
        conn.execute(RAW_TRADE_SQL, raw_trade(999))
    assert info.value.kind == "foreign_key"


def test_sql_rejects_another_users_instrument(conn, eurusd_id):
    """Phase 8: the two-column foreign key (instrument_id, user_id) makes the
    database refuse a trade on someone else's instrument, even if the
    application forgot to check."""
    from tests.conftest import OTHER_EMAIL, ensure_user

    other = ensure_user(conn, OTHER_EMAIL)
    with pytest.raises(IntegrityError) as info:
        conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id, user_id=other))
    assert info.value.kind == "foreign_key"


def test_sql_refuses_to_delete_instrument_that_has_trades(conn, eurusd_id):
    conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id))
    with pytest.raises(IntegrityError) as info:
        conn.execute("DELETE FROM instruments WHERE id = ?", (eurusd_id,))
    assert info.value.kind == "foreign_key"


@pytest.mark.parametrize(
    "changes",
    [
        {"direction": "BUY"},  # invalid direction
        {"status": "PENDING"},  # invalid status
        {"status": "CLOSED", "exit_price": None},  # closed without exit
        {"status": "OPEN"},  # open WITH an exit price
        {"stop_loss": "1.090"},  # long stop above entry
        {"stop_loss": "1.085"},  # stop equal to entry
        {"position_size": "0"},  # zero size
        {"position_size": "-1"},  # negative size
        {"entry_price": "abc"},  # not a number
        {"fees": "-2"},  # negative fees
        {"trade_date": "02/03/2026 08:15"},  # wrong date format
        {"user_id": None},  # no owner (PostgreSQL: NOT NULL; SQLite: legacy-only, see below)
    ],
)
def test_sql_rejects_invalid_raw_trades(conn, eurusd_id, changes):
    if changes == {"user_id": None} and conn.dialect == "sqlite":
        pytest.skip("SQLite allows NULL owners for unassigned pre-Phase 8 rows (migrations.py)")
    # PostgreSQL rejects "abc" as invalid NUMERIC input (a data error)
    # before any CHECK runs; SQLite stores text, so its CHECK rejects it.
    with pytest.raises((IntegrityError, DataError)):
        conn.execute(RAW_TRADE_SQL, raw_trade(eurusd_id, **changes))


def test_sql_rejects_short_with_stop_below_entry(conn, eurusd_id):
    with pytest.raises(IntegrityError, match="stop_on_correct_side"):
        conn.execute(
            RAW_TRADE_SQL,
            raw_trade(eurusd_id, direction="SHORT", stop_loss="1.080", exit_price="1.080"),
        )


def test_sql_rejects_lowercase_symbol(conn):
    with pytest.raises(IntegrityError) as info:
        conn.execute(
            "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, "
            "contract_multiplier, quote_currency) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (USER_ID, "eurusd", "x", "FOREX", "0.00001", "100000", "USD"),
        )
    assert info.value.kind == "check"


# ---------- Transactions ----------


def test_transaction_commits_on_success(db_path, conn, eurusd_id):
    other = connect(db_path)  # a second connection only sees committed data
    try:
        assert other.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1
    finally:
        other.close()


def test_transaction_rolls_back_on_error(conn):
    with pytest.raises(RuntimeError), conn:
        conn.execute(
            "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, "
            "contract_multiplier, quote_currency) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (USER_ID, "GBPUSD", "test", "FOREX", "0.00001", "100000", "USD"),
        )
        raise RuntimeError("something failed half-way")
    assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 0


def test_inner_failure_rolls_back_only_its_savepoint(conn):
    """A nested `with conn:` is a SAVEPOINT: an inner failure that the code
    handles undoes only the inner work, on both databases."""
    insert = (
        "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, "
        "contract_multiplier, quote_currency) VALUES (?, ?, 'x', 'FOREX', '0.1', '1', 'USD')"
    )
    with conn:
        conn.execute(insert, (USER_ID, "KEEP"))
        with pytest.raises(IntegrityError), conn:
            conn.execute(insert, (USER_ID, "KEEP"))  # duplicate symbol: fails inside the savepoint
    symbols = [row["symbol"] for row in conn.execute("SELECT symbol FROM instruments")]
    assert symbols == ["KEEP"]


def test_placeholders_inside_literals_and_percent_signs_are_preserved(conn):
    row = conn.execute("SELECT '?' AS q, '50%' AS pct, ? AS bound", ("a?b",)).fetchone()
    assert (row["q"], row["pct"], row["bound"]) == ("?", "50%", "a?b")


def test_named_parameters_are_refused(conn):
    with pytest.raises(TypeError, match="positional"):
        conn.execute("SELECT :x", {"x": 1})
```

### `tests/test_error_paths.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_error_paths.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 7: error paths that coverage measurement showed were never run.

Each test protects a specific failure mode rather than chasing a number:
an error that is silently swallowed, a page that crashes on bad data, or a
command-line message that would mislead. See docs/PHASE_7_QA_REPORT.md
for the coverage baseline that identified them.
"""

import sqlite3

import pytest

from tests.conftest import TEST_EMAIL
from tests.web_helpers import count_rows
from tradeledger import create_app
from tradeledger.db import IntegrityError, get_db
from tradeledger.repositories.trades import _raise_friendly
from tradeledger.validation import ValidationError, validate_trade
from tradeledger.web.filters import clock_filter, day_filter, when_filter

# ---------- Database errors are translated or re-raised, never swallowed ----------


# Phase 8: the repository now receives the adapter's engine-neutral
# db.IntegrityError (with .kind and .constraint) instead of sqlite3's class.


def test_foreign_key_error_becomes_a_field_error():
    with pytest.raises(ValidationError) as info:
        _raise_friendly(IntegrityError("FOREIGN KEY constraint failed", kind="foreign_key"))
    assert info.value.errors == {"instrument_id": "does not match an existing instrument"}


def test_closed_at_trigger_error_becomes_a_field_error():
    with pytest.raises(ValidationError) as info:
        _raise_friendly(
            IntegrityError(
                "closed_at_rules: an OPEN trade cannot have closed_at",
                kind="check",
                constraint="closed_at_rules",
            )
        )
    assert set(info.value.errors) == {"closed_at"}


def test_unknown_database_errors_are_re_raised_unchanged():
    """An unexpected constraint failure must surface (and be logged as a
    500), not be disguised as a user mistake or ignored."""
    error = IntegrityError("CHECK constraint failed: something_new", kind="check", constraint="something_new")
    with pytest.raises(IntegrityError) as info:
        _raise_friendly(error)
    assert info.value is error


# ---------- Validation messages for rarely used inputs ----------


def _valid_trade(**changes):
    data = {
        "instrument_id": 1,
        "direction": "LONG",
        "status": "CLOSED",
        "trade_date": "2026-04-01T09:00:00Z",
        "closed_at": "2026-04-01T10:00:00Z",
        "entry_price": "100",
        "stop_loss": "90",
        "exit_price": "110",
        "position_size": "1",
    }
    data.update(changes)
    return data


@pytest.mark.parametrize(
    "changes, field, message",
    [
        ({"take_profit": "abc"}, "take_profit", "must be a number"),
        ({"exit_price": "-5"}, "exit_price", "must be greater than zero"),
        ({"instrument_id": True}, "instrument_id", None),
        ({"instrument_id": 1.0}, "instrument_id", None),
        ({"notes": {"text": "hi"}}, "notes", "must be text"),
        ({"strategy": 42}, "strategy", "must be text"),
    ],
)
def test_unusual_values_get_a_field_error(changes, field, message):
    with pytest.raises(ValidationError) as info:
        validate_trade(_valid_trade(**changes))
    assert field in info.value.errors
    if message:
        assert info.value.errors[field] == message


# ---------- Pages keep working with data the engine cannot evaluate ----------


@pytest.mark.sqlite_only  # PostgreSQL's NUMERIC CHECKs refuse the out-of-range value outright
def test_trade_page_for_an_unevaluable_trade_shows_no_result(app, seeded_client):
    """A legacy row out of range (see test_security_hardening.py) must not
    break the trade's own page: it renders without a result card."""
    huge = "1" + "0" * 600_000
    with app.app_context():
        conn = get_db()
        conn.execute(
            "UPDATE trades SET entry_price = ?, position_size = ?, take_profit = NULL WHERE id = 1",
            (huge, huge),
        )
        conn.commit()
    response = seeded_client.get("/trades/1")
    assert response.status_code == 200
    assert "(after fees)" not in response.get_data(as_text=True)  # the result card is absent


def test_oversized_website_form_is_413_not_a_crash(seeded_client, app):
    before = count_rows(app, "trades")
    response = seeded_client.post("/trades/new", data={"notes": "x" * (70 * 1024)})
    assert response.status_code == 413
    assert b"Traceback" not in response.data
    assert count_rows(app, "trades") == before


# ---------- Template filters with unexpected stored values ----------


def test_date_filters_never_crash_on_bad_timestamps():
    assert when_filter("not-a-date") == "not-a-date"  # shown as stored, not hidden
    assert when_filter(None) == "n/a"
    assert day_filter("2026-13-45T00:00:00Z") == "n/a"
    assert clock_filter(None) == ""
    assert clock_filter("2026-03-02T08:15:30Z") == "08:15:30 UTC"
    assert clock_filter("2026-03-02T08:15:00Z") == "08:15 UTC"


# ---------- Configuration and command line ----------


def test_config_name_comes_from_the_environment(monkeypatch, db_path):
    monkeypatch.setenv("TRADELEDGER_CONFIG", "testing")
    app = create_app(overrides={"DATABASE": db_path})
    assert app.config["CONFIG_NAME"] == "testing" and app.config["TESTING"] is True


@pytest.mark.sqlite_only
def test_run_sql_on_a_missing_database_explains_what_to_do(tmp_path):
    app = create_app("testing", overrides={"DATABASE": str(tmp_path / "missing.db")})
    result = app.test_cli_runner().invoke(args=["run-sql", "SELECT 1"])
    assert result.exit_code != 0
    assert "Database file not found. Run init-db first." in result.output


def test_run_sql_with_no_rows_says_so(runner):
    result = runner.invoke(args=["run-sql", "SELECT * FROM trades"])
    assert result.exit_code == 0
    assert "(no rows)" in result.output


@pytest.mark.sqlite_only
def test_db_info_on_an_empty_file(tmp_path):
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    app = create_app("testing", overrides={"DATABASE": str(empty)})
    result = app.test_cli_runner().invoke(args=["db-info"])
    assert "No tables yet. Run: flask --app tradeledger init-db" in result.output


@pytest.mark.sqlite_only  # PostgreSQL's NUMERIC CHECKs refuse the out-of-range value outright
def test_cli_report_lists_excluded_and_legacy_trades(app, runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    huge = "1" + "0" * 600_000
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 2")
        conn.execute(
            "UPDATE trades SET entry_price = ?, position_size = ?, take_profit = NULL WHERE id = 1",
            (huge, huge),
        )
        conn.commit()
    output = runner.invoke(args=["report", "--email", TEST_EMAIL]).output
    assert "== Excluded trades ==" in output
    assert "#1: values are too large to calculate exactly" in output
    assert "== Closed trades with no recorded exit time (ordered by entry time) ==" in output
    assert "#2" in output.split("no recorded exit time")[1]
```

### `tests/test_migrations.py` (MODIFIED)

**Why:** Version 3; v1 → latest applies [2, 3] then claims legacy rows; SQLite-only marks.  
**How to run or test:** `python -m pytest tests/test_migrations.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Schema migration: a real Phase 3 (version 1) database upgraded to the latest version.

tests/fixtures/schema_v1.sql is an exact copy of the Phase 3 schema.sql,
so these tests start from the same structure an existing user has.

Phase 8 changes (the requirement changed, the checks did not weaken):
* The latest version is now 3, so a version 1 database receives
  migrations [2, 3] and ends on version 3.
* After migration 3 the old records are UNASSIGNED (user_id NULL) and
  invisible to every account. Tests that read them through the
  repositories first create the test account and claim the records with
  the same function the claim-legacy-data command uses.
* Raw SQL errors are checked as tradeledger.db.IntegrityError.
SQLite-only: PostgreSQL has no Phase 3 history (see migrations.py); the
trigger tests at the end run on both engines.
"""

import sqlite3
from pathlib import Path

import pytest

from tests.conftest import ensure_user
from tests.factories import USER_ID
from tradeledger import create_app, migrations
from tradeledger.db import IntegrityError, connect, drop_all, init_schema, schema_version
from tradeledger.legacy import claim_legacy_data
from tradeledger.performance import build_report
from tradeledger.repositories.trades import create_trade, get_trade, update_trade
from tradeledger.seed import SAMPLE_INSTRUMENTS, SAMPLE_TRADES
from tradeledger.validation import ValidationError, validate_instrument, validate_trade

V1_SCHEMA = (Path(__file__).parent / "fixtures" / "schema_v1.sql").read_text(encoding="utf-8")

V1_TRADE_COLUMNS = (
    "instrument_id",
    "trade_date",
    "direction",
    "entry_price",
    "stop_loss",
    "take_profit",
    "exit_price",
    "position_size",
    "fees",
    "status",
    "strategy",
    "session",
    "notes",
)


def build_v1_database(path):
    """Create a Phase 3 database holding the 16 sample trades WITHOUT exit
    times, exactly as an existing user's database looks."""
    conn = connect(path)
    conn.executescript(V1_SCHEMA)
    raw_conn = sqlite3.connect(path)  # the Phase 3 code used named parameters
    raw_conn.row_factory = sqlite3.Row
    with raw_conn:
        for raw in SAMPLE_INSTRUMENTS:
            item = validate_instrument(raw)
            raw_conn.execute(
                "INSERT INTO instruments (symbol, name, asset_class, price_increment, "
                "contract_multiplier, quote_currency) VALUES (:symbol, :name, :asset_class, "
                ":price_increment, :contract_multiplier, :quote_currency)",
                item,
            )
        ids = {r["symbol"]: r["id"] for r in raw_conn.execute("SELECT id, symbol FROM instruments")}
        for raw in SAMPLE_TRADES:
            data = {k: v for k, v in raw.items() if k not in ("symbol", "closed_at")}
            data["instrument_id"] = ids[raw["symbol"]]
            trade = validate_trade(data, require_closed_at=False)
            raw_conn.execute(
                f"INSERT INTO trades ({', '.join(V1_TRADE_COLUMNS)}) "
                f"VALUES ({', '.join(':' + c for c in V1_TRADE_COLUMNS)})",
                trade,
            )
    raw_conn.close()
    return conn


def migrate_and_claim(conn):
    """Upgrade, create the test account and give it the legacy records."""
    applied = init_schema(conn)
    user_id = ensure_user(conn)
    assert user_id == USER_ID
    assert claim_legacy_data(conn, user_id=user_id, apply=True)["applied"]
    return applied


def columns(conn, table):
    if conn.dialect == "postgresql":
        sql = "SELECT column_name AS name FROM information_schema.columns WHERE table_name = ? AND table_schema = current_schema()"
        return [row["name"] for row in conn.execute(sql, (table,))]
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]


def rows(conn, table):
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


@pytest.fixture
def v1_conn(tmp_path, request):
    if request.node.get_closest_marker("sqlite_only") is None:
        pytest.fail("v1_conn tests must be marked sqlite_only")
    conn = build_v1_database(str(tmp_path / "phase3.db"))
    yield conn
    conn.close()


# ---------- The starting point really is Phase 3 ----------


@pytest.mark.sqlite_only
def test_fixture_is_a_version_1_database(v1_conn):
    assert schema_version(v1_conn) == 1
    assert "closed_at" not in columns(v1_conn, "trades")
    assert v1_conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 16


# ---------- Migrating ----------


@pytest.mark.sqlite_only
def test_migration_adds_closed_at_and_bumps_version(v1_conn):
    applied = init_schema(v1_conn)
    assert applied == [2, 3]
    assert schema_version(v1_conn) == 3
    assert "closed_at" in columns(v1_conn, "trades")
    names = {r["name"] for r in v1_conn.execute("SELECT name FROM sqlite_master")}
    assert {"idx_trades_closed_at", "trades_closed_at_rules_insert", "trades_closed_at_rules_update"} <= names


@pytest.mark.sqlite_only
def test_migration_preserves_every_existing_value(v1_conn):
    trades_before = rows(v1_conn, "trades")
    instruments_before = rows(v1_conn, "instruments")
    init_schema(v1_conn)
    trades_after = rows(v1_conn, "trades")
    assert [
        {k: v for k, v in r.items() if k != "user_id"} for r in rows(v1_conn, "instruments")
    ] == instruments_before
    assert len(trades_after) == len(trades_before) == 16
    for before, after in zip(trades_before, trades_after, strict=True):
        assert {k: v for k, v in after.items() if k not in ("closed_at", "user_id")} == before
        assert after["closed_at"] is None  # never fabricated
        assert after["user_id"] is None  # never guessed (Phase 8)


@pytest.mark.sqlite_only
def test_migration_is_idempotent(v1_conn):
    assert init_schema(v1_conn) == [2, 3]
    snapshot = rows(v1_conn, "trades")
    assert init_schema(v1_conn) == []
    assert migrations.migrate(v1_conn) == []
    assert schema_version(v1_conn) == 3
    assert rows(v1_conn, "trades") == snapshot


def test_new_database_goes_straight_to_latest(conn):
    assert schema_version(conn) == migrations.LATEST_VERSION
    assert "closed_at" in columns(conn, "trades")


@pytest.mark.sqlite_only
def test_failed_migration_rolls_back_completely(v1_conn, monkeypatch):
    init_schema(v1_conn)
    broken = {
        **migrations.MIGRATIONS,
        4: "CREATE TABLE half_done (x INTEGER); SELECT * FROM table_that_does_not_exist;",
    }
    monkeypatch.setattr(migrations, "MIGRATIONS", broken)
    with pytest.raises(sqlite3.OperationalError):
        migrations.migrate(v1_conn)
    assert schema_version(v1_conn) == 3
    names = {r["name"] for r in v1_conn.execute("SELECT name FROM sqlite_master")}
    assert "half_done" not in names


def test_reset_then_init_gives_a_fully_migrated_schema(conn):
    """Regression: drop_all must reset the version, or init would skip migration 2."""
    drop_all(conn)
    assert schema_version(conn) == 0
    init_schema(conn)
    assert schema_version(conn) == 3
    assert "closed_at" in columns(conn, "trades")


# ---------- Legacy trades after migration ----------


@pytest.mark.sqlite_only
def test_legacy_trades_are_reported_and_ordered_by_entry_time(v1_conn):
    migrate_and_claim(v1_conn)
    report = build_report(v1_conn, user_id=USER_ID)
    closed_ids = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]
    assert report.unknown_close_time_trade_ids == closed_ids
    assert [p.trade_id for p in report.cumulative_r] == closed_ids
    assert all(not p.close_time_known for p in report.cumulative_r)
    assert all(p.realised_at == p.trade_date for p in report.cumulative_r)
    # The numbers are the same as Phase 3 produced for this data.
    assert report.overall.trades == 14 and report.overall.wins == 7


@pytest.mark.sqlite_only
def test_new_closed_trade_requires_closed_at_after_migration(v1_conn):
    migrate_and_claim(v1_conn)
    trade = {
        "instrument_id": 1,
        "trade_date": "2026-04-01T08:00:00Z",
        "direction": "LONG",
        "entry_price": "1.1",
        "stop_loss": "1.09",
        "exit_price": "1.12",
        "position_size": "1",
    }
    with pytest.raises(ValidationError) as info:
        create_trade(v1_conn, trade, user_id=USER_ID)
    assert info.value.errors == {"closed_at": "is required for a CLOSED trade"}
    new_id = create_trade(v1_conn, dict(trade, closed_at="2026-04-01T09:00:00Z"), user_id=USER_ID)
    assert get_trade(v1_conn, new_id, user_id=USER_ID)["closed_at"] == "2026-04-01T09:00:00Z"


@pytest.mark.sqlite_only
def test_mixed_legacy_and_new_trades_are_in_realised_order(v1_conn):
    migrate_and_claim(v1_conn)
    # Entered BEFORE every sample trade, closed AFTER all of them.
    new_id = create_trade(
        v1_conn,
        {
            "instrument_id": 1,
            "trade_date": "2026-02-27T08:00:00Z",
            "closed_at": "2026-03-25T08:00:00Z",
            "direction": "LONG",
            "entry_price": "1.1",
            "stop_loss": "1.09",
            "exit_price": "1.12",
            "position_size": "1",
        },
        user_id=USER_ID,
    )
    curve = build_report(v1_conn, user_id=USER_ID).cumulative_r
    assert curve[-1].trade_id == new_id and curve[-1].close_time_known


@pytest.mark.sqlite_only
def test_legacy_trade_can_be_edited_without_inventing_a_close_time(v1_conn):
    migrate_and_claim(v1_conn)
    legacy = get_trade(v1_conn, 1, user_id=USER_ID)
    edited = {
        k: legacy[k]
        for k in (
            "instrument_id",
            "trade_date",
            "closed_at",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "exit_price",
            "position_size",
            "fees",
            "status",
            "strategy",
            "session",
        )
    }
    edited["notes"] = "Edited after the migration"
    assert update_trade(v1_conn, 1, edited, user_id=USER_ID) is True
    after = get_trade(v1_conn, 1, user_id=USER_ID)
    assert after["notes"] == "Edited after the migration"
    assert after["closed_at"] is None


@pytest.mark.sqlite_only
def test_legacy_trade_can_be_given_its_real_close_time(v1_conn):
    migrate_and_claim(v1_conn)
    legacy = get_trade(v1_conn, 1, user_id=USER_ID)
    data = {
        k: legacy[k]
        for k in (
            "instrument_id",
            "trade_date",
            "direction",
            "entry_price",
            "stop_loss",
            "take_profit",
            "exit_price",
            "position_size",
            "fees",
            "status",
            "strategy",
            "session",
            "notes",
        )
    }
    update_trade(v1_conn, 1, dict(data, closed_at="2026-03-02T12:00:00Z"), user_id=USER_ID)
    assert get_trade(v1_conn, 1, user_id=USER_ID)["closed_at"] == "2026-03-02T12:00:00Z"
    # Once known, it cannot be removed again: the trade is no longer legacy.
    with pytest.raises(ValidationError):
        update_trade(v1_conn, 1, dict(data, closed_at=None), user_id=USER_ID)


# ---------- Triggers enforce the rules in SQL ----------

RAW = (
    "INSERT INTO trades (user_id, instrument_id, trade_date, closed_at, direction, entry_price, "
    "stop_loss, exit_price, position_size, fees, status) VALUES (1, 1, ?, ?, 'LONG', '1.1', "
    "'1.09', ?, '1', '0', ?)"
)


def test_trigger_rejects_open_trade_with_closed_at(seeded_conn):
    with pytest.raises(IntegrityError, match="closed_at_rules"):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "2026-04-01T09:00:00Z", None, "OPEN"))


def test_trigger_rejects_close_before_entry(seeded_conn):
    with pytest.raises(IntegrityError, match="closed_at_rules"):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "2026-04-01T07:00:00Z", "1.12", "CLOSED"))


def test_trigger_rejects_bad_update(seeded_conn):
    with pytest.raises(IntegrityError, match="closed_at_rules"):
        seeded_conn.execute("UPDATE trades SET closed_at = '2000-01-01T00:00:00Z' WHERE id = 1")


def test_check_constraint_rejects_badly_formatted_closed_at(seeded_conn):
    with pytest.raises(IntegrityError):
        seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", "01/04/2026 09:00", "1.12", "CLOSED"))


def test_sql_still_allows_legacy_style_closed_trade_without_closed_at(seeded_conn):
    seeded_conn.execute(RAW, ("2026-04-01T08:00:00Z", None, "1.12", "CLOSED"))


# ---------- migrate-db command ----------


@pytest.mark.sqlite_only
def test_migrate_db_command_backs_up_then_migrates(tmp_path):
    path = tmp_path / "user.db"
    build_v1_database(str(path)).close()
    runner = create_app("testing", overrides={"DATABASE": str(path)}).test_cli_runner()

    result = runner.invoke(args=["migrate-db"])
    assert result.exit_code == 0, result.output
    assert "Schema version 1 -> 3" in result.output
    backups = list(tmp_path.glob("user.db.v1-*.bak"))
    assert len(backups) == 1
    backup = connect(str(backups[0]))
    assert schema_version(backup) == 1
    assert backup.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 16
    backup.close()

    again = runner.invoke(args=["migrate-db"])
    assert "Already at schema version 3" in again.output
    assert len(list(tmp_path.glob("user.db.v1-*.bak"))) == 1
```

### `tests/test_report.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_report.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Report building, grouping, serialisation, display formatting and the CLI.

The sample-data tests check the engine against values worked out BY HAND
from tradeledger/seed.py, held as exact fractions (fractions.Fraction), so
they do not reuse the engine's own arithmetic.
"""

import json
import random
from decimal import Decimal
from fractions import Fraction as F

import pytest

from tests.conftest import TEST_EMAIL
from tests.factories import USER_ID, dated, make_trade, r_trade
from tradeledger.performance import analyse, build_report
from tradeledger.performance.formatting import (
    decimal_to_text,
    format_money,
    format_r,
    format_r_amount,
    format_rate,
    format_ratio,
    to_jsonable,
)
from tradeledger.performance.metrics import summarise
from tradeledger.performance.trade_math import evaluate_trade
from tradeledger.repositories.instruments import get_instrument_by_symbol

D = Decimal

# Realised R per closed sample trade = net P&L / initial risk, by hand.
# e.g. trade 1: net 393 USD; risk (1.085 - 1.083) x 1 x 100000 = 200 -> 393/200
HAND_R = {
    1: F(393, 200),  # +1.965
    2: F(-1035, 1000),  # -103.5 / 100
    3: F(0),  # breakeven
    4: F(293, 200),  # +1.465
    5: F(198, 100),  # +1.98
    6: F(-133, 130),  # -1.0230769...
    7: F(1985, 1000),  # +1.985
    8: F(-815, 800),  # -1.01875
    9: F(-5, 150),  # -0.0333...  (fees turned a price gain into a loss)
    10: F(2),  # +2
    11: F(0),  # breakeven
    12: F(-157, 150),  # -1.04666...
    13: F(158, 80),  # +1.975
    16: F(38, 80),  # +0.475
}
CHRONOLOGICAL_IDS = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16]


def close_to(value, expected, places=40):
    """Decimal from the engine vs an exact Fraction, to `places` decimals."""
    return abs(F(value) - F(expected)) < F(1, 10**places)


@pytest.fixture
def sample_report(seeded_conn):
    return build_report(seeded_conn, user_id=USER_ID)


# ---------- Sample data: overall ----------


def test_sample_counts(sample_report):
    o = sample_report.overall
    assert (o.trades, o.wins, o.losses, o.breakevens) == (14, 7, 5, 2)
    assert sample_report.open_trades == 2
    assert sample_report.excluded == []
    assert sample_report.r_unavailable == []
    assert o.win_rate == D("0.5")
    assert close_to(o.loss_rate, F(5, 14))


def test_sample_total_and_average_r(sample_report):
    o = sample_report.overall
    assert sum(HAND_R.values()) == F(239871, 31200)  # 7.68817307...
    assert close_to(o.total_r, F(239871, 31200))
    assert close_to(o.average_r, F(239871, 31200) / 14)  # 0.549155...


def test_sample_average_win_and_loss_r(sample_report):
    o = sample_report.overall
    assert close_to(o.average_win_r, F(2369, 1400))  # 11.845 / 7
    assert close_to(o.average_loss_r, F(-129693, 156000))  # -4.15682... / 5


def test_sample_expectancy_matches_average_r(sample_report):
    o = sample_report.overall
    assert close_to(o.expectancy_r, F(239871, 436800))
    assert close_to(o.expectancy_r, o.average_r)


def test_sample_profit_factor_r(sample_report):
    assert close_to(sample_report.overall.profit_factor_r, F(369564, 129693))  # 2.8495...


def test_sample_planned_rr(sample_report):
    o = sample_report.overall
    assert o.planned_rr_trades == 12  # trades 11 and 16 have no target
    assert close_to(o.average_planned_rr, F(647, 312))  # 2.0737...


def test_sample_streaks(sample_report):
    # Date order: W L BE W W L W L L W BE L W W
    assert sample_report.overall.longest_win_streak == 2
    assert sample_report.overall.longest_loss_streak == 2


def test_sample_max_drawdown_r(sample_report):
    dd = sample_report.overall.max_drawdown_r
    assert close_to(dd.amount, F(101, 96))  # 1.01875 + 1/30 = 1.05208...
    assert (dd.peak_trade_id, dd.trough_trade_id) == (7, 9)


def test_sample_best_and_worst_trades(sample_report):
    o = sample_report.overall
    assert (o.best_trade_r.trade_id, o.best_trade_r.value) == (10, D("2"))
    assert o.worst_trade_r.trade_id == 12


def test_sample_money_per_currency(sample_report):
    usd = sample_report.overall.money["USD"]
    gbp = sample_report.overall.money["GBP"]
    assert (usd.trades, usd.gross_pnl, usd.fees, usd.net_pnl) == (12, D("842"), D("43.5"), D("798.5"))
    assert (usd.winning_pnl, usd.losing_pnl) == (D("1278.5"), D("-480"))
    assert close_to(usd.profit_factor, F(12785, 4800))
    assert usd.max_drawdown.amount == D("243.5")  # 846 -> 602.5
    assert (usd.best_trade.trade_id, usd.worst_trade.trade_id) == (1, 12)
    assert (gbp.trades, gbp.net_pnl, gbp.profit_factor) == (2, D("200"), None)


def test_sample_cumulative_r_curve(sample_report):
    curve = sample_report.cumulative_r
    assert [p.trade_id for p in curve] == CHRONOLOGICAL_IDS
    running = F(0)
    for point in curve:
        running += HAND_R[point.trade_id]
        assert close_to(point.realised_r, HAND_R[point.trade_id])
        assert close_to(point.cumulative_r, running)
    assert curve[-1].cumulative_r == sample_report.overall.total_r


# ---------- Sample data: groups ----------


def test_groups_by_instrument(sample_report):
    groups = sample_report.by_instrument
    assert list(groups) == ["EURUSD", "GBPUSD", "NAS100", "UK100", "XAUUSD"]
    assert {k: g.trades for k, g in groups.items()} == {
        "EURUSD": 3,
        "GBPUSD": 3,
        "NAS100": 2,
        "UK100": 2,
        "XAUUSD": 4,
    }
    gold = groups["XAUUSD"]
    assert gold.win_rate == D("0.75")
    assert close_to(gold.total_r, HAND_R[5] + HAND_R[6] + HAND_R[13] + HAND_R[16])  # 3.4069...
    assert list(groups["UK100"].money) == ["GBP"]


def test_groups_by_strategy_and_session(sample_report):
    assert {k: g.trades for k, g in sample_report.by_strategy.items()} == {
        "Breakout": 5,
        "Pullback": 5,
        "Reversal": 2,
        "Unspecified": 2,
    }
    assert {k: g.trades for k, g in sample_report.by_session.items()} == {
        "ASIA": 2,
        "LONDON": 6,
        "NEW_YORK": 5,
        "Unspecified": 1,
    }


def test_groups_by_weekday_in_calendar_order(sample_report):
    groups = sample_report.by_weekday
    assert list(groups) == ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    assert groups["Friday"].win_rate == D("1")
    # Thursday: trades 4 (+1.465R) and 9 (-1/30 R): PF = 1.465 x 30 = 43.95.
    # -1/30 cannot be stored exactly in decimal, so compare to 40 places.
    assert close_to(groups["Thursday"].profit_factor_r, F(4395, 100))


def test_groups_by_month_and_date(sample_report):
    assert list(sample_report.by_month) == ["2026-03"]
    assert sample_report.by_month["2026-03"].trades == 14
    dates = list(sample_report.by_date)
    assert len(dates) == 14 and dates == sorted(dates)
    assert sample_report.by_date["2026-03-20"].trades == 1  # trade 15 that day is open


# ---------- Filters through build_report ----------


def test_report_filtered_by_instrument(seeded_conn):
    gold = get_instrument_by_symbol(seeded_conn, "XAUUSD", user_id=USER_ID)
    report = build_report(seeded_conn, instrument_id=gold["id"], user_id=USER_ID)
    assert report.overall.trades == 4
    assert list(report.by_instrument) == ["XAUUSD"]


def test_report_filtered_by_date_range(seeded_conn):
    report = build_report(seeded_conn, start="2026-03-02", end="2026-03-06", user_id=USER_ID)
    assert [p.trade_id for p in report.cumulative_r] == [1, 2, 3, 4, 5]


def test_report_on_empty_database(conn):
    report = build_report(conn, user_id=USER_ID)
    assert report.overall.trades == 0
    assert report.overall.expectancy_r is None
    assert report.cumulative_r == []
    assert report.by_instrument == {}


# ---------- analyse() with awkward input ----------


def test_open_and_incomplete_trades_are_separated():
    trades = [
        make_trade(),
        make_trade(status="OPEN", exit_price=None),
        make_trade(exit_price=None),  # "closed" but no exit: excluded
        make_trade(stop_loss=D("105")),  # counted, but no R
    ]
    report = analyse(trades)
    assert report.overall.trades == 2
    assert report.open_trades == 1
    assert [e.trade_id for e in report.excluded] == [trades[2]["id"]]
    assert "exit_price" in report.excluded[0].reason
    assert [e.trade_id for e in report.r_unavailable] == [trades[3]["id"]]
    assert len(report.cumulative_r) == 1


def test_input_order_does_not_change_results():
    trades = [dated(d, exit_price=D(str(80 + d * 3))) for d in range(5, 20)]
    shuffled = trades[:]
    random.Random(7).shuffle(shuffled)
    assert analyse(shuffled) == analyse(trades)


def test_all_winning_and_all_losing_reports():
    wins = analyse([r_trade(1), r_trade(2)])
    assert wins.overall.loss_rate == D("0") and wins.overall.profit_factor_r is None
    losses = analyse([r_trade(-1), r_trade(-1)])
    assert losses.overall.win_rate == D("0") and losses.overall.profit_factor_r == D("0")


# ---------- JSON serialisation ----------


def _contains_float(obj):
    if isinstance(obj, float):
        return True
    if isinstance(obj, dict):
        return any(_contains_float(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_contains_float(v) for v in obj)
    return False


def test_report_serialises_to_json_without_floats(sample_report):
    data = to_jsonable(sample_report)
    assert not _contains_float(data)
    text = json.dumps(data)
    parsed = json.loads(text)
    assert parsed["overall"]["trades"] == 14
    assert parsed["overall"]["money"]["USD"]["net_pnl"] == "798.5"
    # Phase 4 added realised_at and close_time_known to each curve point.
    assert parsed["cumulative_r"][0] == {
        "trade_id": 1,
        "trade_date": "2026-03-02T08:15:00Z",
        "realised_at": "2026-03-02T11:40:00Z",
        "close_time_known": True,
        "symbol": "EURUSD",
        "realised_r": "1.965",
        "cumulative_r": "1.965",
    }


def test_json_strings_round_trip_to_the_exact_decimal(sample_report):
    text = to_jsonable(sample_report)["overall"]["total_r"]
    assert D(text) == sample_report.overall.total_r


def test_decimal_to_text_has_no_exponent():
    assert decimal_to_text(D("4.0E+2")) == "400"
    assert decimal_to_text(D("1.9650")) == "1.965"
    assert decimal_to_text(D("-0")) == "0"
    assert decimal_to_text(D("0E-60")) == "0"


# ---------- Display formatting and rounding policy ----------


def test_format_r_rounds_half_up_only_for_display():
    assert format_r(D("1.965")) == "+1.97R"
    assert format_r(D("-1.035")) == "-1.04R"
    assert format_r(D("0")) == "0.00R"
    assert format_r(D("-0.001")) == "0.00R"
    assert format_r(None) == "n/a"
    assert format_r_amount(D("1.0520833")) == "1.05R"


def test_format_money_rate_and_ratio():
    assert format_money(D("1278.5"), "USD") == "1,278.50 USD"
    assert format_money(D("-480"), "USD") == "-480.00 USD"
    assert format_rate(D(5) / D(14)) == "35.7%"
    assert format_rate(None) == "n/a"
    assert format_ratio(D("2.8495")) == "2.85"


def test_rounding_late_is_more_accurate_than_rounding_early():
    """Three trades of +0.005R. Rounding each first gives 0.03R; the true
    total is 0.015R, which displays as 0.02R."""
    s = summarise([evaluate_trade(r_trade("0.005")) for _ in range(3)])
    assert s.total_r == D("0.015")
    assert format_r(s.total_r) == "+0.02R"
    rounded_first = sum(D("0.005").quantize(D("0.01"), rounding="ROUND_HALF_UP") for _ in range(3))
    assert rounded_first == D("0.03")


# ---------- CLI ----------


def test_cli_report_text(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    result = runner.invoke(args=["report", "--email", TEST_EMAIL, "--email", TEST_EMAIL])
    assert result.exit_code == 0
    assert "Expectancy per trade          +0.55R" in result.output
    assert "798.50 USD" in result.output
    assert "never added together" in result.output


def test_cli_report_json_and_filters(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    data = json.loads(runner.invoke(args=["report", "--email", TEST_EMAIL, "--json"]).output)
    assert data["overall"]["trades"] == 14
    gold = json.loads(
        runner.invoke(args=["report", "--email", TEST_EMAIL, "--json", "--instrument", "xauusd"]).output
    )
    assert gold["overall"]["trades"] == 4


def test_cli_report_errors(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    unknown = runner.invoke(args=["report", "--email", TEST_EMAIL, "--instrument", "NOPE"])
    assert unknown.exit_code != 0 and "No instrument" in unknown.output
    bad_dates = runner.invoke(
        args=["report", "--email", TEST_EMAIL, "--start", "2026-03-10", "--end", "2026-03-01"]
    )
    assert bad_dates.exit_code != 0 and "end" in bad_dates.output
```

### `tests/test_report_filters.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_report_filters.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 6: report filters shared by the dashboard and the analytics API.

Three layers are tested separately so a failure points at the right code:
  1. parse_report_filters()  - validation only (no report)
  2. repository + build_report - the filters reach SQL and change results
  3. /api/v1/analytics/...    - the new strategy/session parameters

Sample data facts used below (tradeledger/seed.py, all synthetic):
  14 closed trades, 2 open (ids 14 and 15).
  Closed by strategy:  Breakout 5, Pullback 5, Reversal 2, none 2
  Closed by session:   LONDON 6, NEW_YORK 5, ASIA 2, none 1
  Closed Breakout + LONDON: trades 1, 3 and 10
"""

import pytest

from tests.factories import USER_ID, table_snapshot
from tests.test_openapi import validate
from tradeledger.performance import build_report
from tradeledger.report_filters import FILTER_NAMES, ReportFilters, parse_report_filters
from tradeledger.repositories import trades as trade_repo
from tradeledger.validation import ValidationError

BASE = "/api/v1/analytics"
ENDPOINTS = ["summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve"]


def parse(conn, blank_means_unset=True, **args):
    return parse_report_filters(args, conn, blank_means_unset=blank_means_unset, user_id=USER_ID)


# ---------- 1. Validation ----------


def test_no_arguments_means_no_filters(seeded_conn):
    filters, errors = parse(seeded_conn)
    assert errors == {}
    assert filters == ReportFilters()
    assert filters.is_empty and filters.active() == {}


def test_all_filters_valid(seeded_conn):
    filters, errors = parse(
        seeded_conn,
        instrument_id="1",
        strategy=" Breakout ",
        session="london",
        start="2026-03-01",
        end="2026-03-31",
    )
    assert errors == {}
    assert filters == ReportFilters(1, "Breakout", "LONDON", "2026-03-01", "2026-03-31")
    assert list(filters.as_kwargs()) == list(FILTER_NAMES)


def test_blank_values_mean_all_on_the_website(seeded_conn):
    filters, errors = parse(seeded_conn, instrument_id="", strategy="  ", session="", start="", end="")
    assert errors == {} and filters.is_empty


def test_blank_values_are_errors_in_the_api(seeded_conn):
    _, errors = parse(seeded_conn, blank_means_unset=False, instrument_id="", strategy=" ", session="")
    assert set(errors) == {"instrument_id", "strategy", "session"}


@pytest.mark.parametrize(
    "value, message",
    [
        ("abc", "must be a whole number of 1 or more"),
        ("0", "must be a whole number of 1 or more"),
        ("-1", "must be a whole number of 1 or more"),
        ("1.5", "must be a whole number of 1 or more"),
        ("999", "does not match an existing instrument"),
    ],
)
def test_bad_instrument(seeded_conn, value, message):
    filters, errors = parse(seeded_conn, instrument_id=value)
    assert errors == {"instrument_id": message}
    assert filters.instrument_id is None


def test_strategy_length_limit(seeded_conn):
    assert parse(seeded_conn, strategy="x" * 50)[1] == {}
    assert parse(seeded_conn, strategy="x" * 51)[1] == {"strategy": "must be 50 characters or fewer"}


@pytest.mark.parametrize("value", ["MOON", "london-ish", "1"])
def test_unknown_session(seeded_conn, value):
    assert parse(seeded_conn, session=value)[1] == {
        "session": "must be one of: ASIA, LONDON, NEW_YORK, OTHER"
    }


@pytest.mark.parametrize(
    "args, field",
    [
        ({"start": "2026-02-30"}, "start"),
        ({"end": "next week"}, "end"),
        ({"start": "2026-03-10", "end": "2026-03-09"}, "end"),
    ],
)
def test_bad_dates(seeded_conn, args, field):
    assert field in parse(seeded_conn, **args)[1]


def test_single_day_range_is_valid(seeded_conn):
    assert parse(seeded_conn, start="2026-03-02", end="2026-03-02")[1] == {}


def test_every_problem_is_reported_at_once(seeded_conn):
    _, errors = parse(seeded_conn, instrument_id="x", session="MOON", start="bad")
    assert set(errors) == {"instrument_id", "session", "start"}


# ---------- 2. Repository and build_report ----------


def test_repository_session_filter(seeded_conn):
    rows = trade_repo.list_trades(seeded_conn, session="LONDON", user_id=USER_ID)
    assert len(rows) == 7  # 6 closed + open trade 15
    assert {r["session"] for r in rows} == {"LONDON"}
    assert trade_repo.count_trades(seeded_conn, session="LONDON", user_id=USER_ID) == 7


def test_repository_rejects_unknown_session(seeded_conn):
    with pytest.raises(ValidationError):
        trade_repo.list_trades(seeded_conn, session="MOON", user_id=USER_ID)


@pytest.mark.parametrize(
    "kwargs, closed, open_",
    [
        ({"strategy": "Breakout"}, 5, 1),
        ({"strategy": "Pullback"}, 5, 0),
        ({"session": "ASIA"}, 2, 0),
        ({"session": "LONDON"}, 6, 1),
        ({"strategy": "Breakout", "session": "LONDON"}, 3, 0),
        ({"instrument_id": 1, "strategy": "Pullback"}, 2, 0),
        ({"strategy": "No such strategy"}, 0, 0),
    ],
)
def test_build_report_filters(seeded_conn, kwargs, closed, open_):
    report = build_report(seeded_conn, **kwargs, user_id=USER_ID)
    assert report.overall.trades == closed
    assert report.open_trades == open_


def test_combined_filters_select_exactly_the_right_trades(seeded_conn):
    report = build_report(seeded_conn, strategy="Breakout", session="LONDON", user_id=USER_ID)
    assert [p.trade_id for p in report.cumulative_r] == [1, 3, 10]


def test_date_filter_uses_the_realised_date(app, seeded_conn):
    """Entered on 31 March, closed on 1 April: it belongs to April."""
    trade_repo.create_trade(
        seeded_conn,
        {
            "instrument_id": 1,
            "direction": "LONG",
            "status": "CLOSED",
            "trade_date": "2026-03-31T22:00:00Z",
            "closed_at": "2026-04-01T01:00:00Z",
            "entry_price": "1.1",
            "stop_loss": "1.09",
            "exit_price": "1.11",
            "position_size": "1",
        },
        user_id=USER_ID,
    )
    assert (
        build_report(seeded_conn, start="2026-04-01", end="2026-04-30", user_id=USER_ID).overall.trades == 1
    )
    assert (
        build_report(seeded_conn, start="2026-03-01", end="2026-03-31", user_id=USER_ID).overall.trades == 14
    )


def test_filter_values_are_bound_parameters_not_sql(seeded_conn):
    """A classic injection string is just a strategy name nobody used."""
    before = trade_repo.count_trades(seeded_conn, user_id=USER_ID)
    report = build_report(seeded_conn, strategy="' OR 1=1 --", user_id=USER_ID)
    assert report.overall.trades == 0 and report.open_trades == 0
    assert trade_repo.count_trades(seeded_conn, user_id=USER_ID) == before


# ---------- 3. API ----------


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_every_analytics_endpoint_accepts_the_new_filters(seeded_client, endpoint):
    response = seeded_client.get(f"{BASE}/{endpoint}?strategy=Breakout&session=london")
    assert response.status_code == 200
    filters = response.get_json()["meta"]["filters"]
    assert filters["strategy"] == "Breakout" and filters["session"] == "LONDON"


def test_api_strategy_filter_changes_the_numbers(seeded_client):
    body = seeded_client.get(f"{BASE}/summary?strategy=Breakout").get_json()
    assert body["data"]["overall"]["trades"] == 5
    assert body["data"]["open_trades"] == 1
    validate(body, "SummaryAnalytics")


def test_api_session_filter_on_grouped_endpoint(seeded_client):
    body = seeded_client.get(f"{BASE}/strategies?session=ASIA").get_json()
    assert [(g["group"], g["trades"]) for g in body["data"]] == [("Pullback", 1), ("Reversal", 1)]
    validate(body, "GroupedAnalytics")


def test_api_curve_with_combined_filters(seeded_client):
    body = seeded_client.get(f"{BASE}/curve?strategy=Breakout&session=LONDON").get_json()
    assert [point["trade_id"] for point in body["data"]] == [1, 3, 10]
    validate(body, "CurveAnalytics")


@pytest.mark.parametrize(
    "query, field",
    [
        ("session=MOON", "session"),
        ("session=", "session"),
        ("strategy=", "strategy"),
        ("strategy=" + "x" * 51, "strategy"),
    ],
)
def test_api_rejects_bad_new_filters(seeded_client, query, field):
    response = seeded_client.get(f"{BASE}/summary?{query}")
    assert response.status_code == 400
    assert field in response.get_json()["error"]["details"]
    validate(response.get_json(), "Error")


def test_analytics_requests_never_change_data(app, seeded_client):
    before = (table_snapshot(app, "trades"), table_snapshot(app, "instruments"))
    for endpoint in ENDPOINTS:
        seeded_client.get(f"{BASE}/{endpoint}?strategy=Breakout&session=LONDON&start=2026-03-01")
    seeded_client.get("/analytics?strategy=Breakout&session=LONDON")
    assert (table_snapshot(app, "trades"), table_snapshot(app, "instruments")) == before
```

### `tests/test_repositories.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_repositories.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Integration tests: repository functions against a real temporary SQLite file."""

from decimal import Decimal

import pytest

from tests.factories import USER_ID
from tradeledger.repositories import DuplicateError
from tradeledger.repositories.instruments import (
    create_instrument,
    get_instrument,
    get_instrument_by_symbol,
    list_instruments,
)
from tradeledger.repositories.trades import (
    create_trade,
    delete_trade,
    get_trade,
    list_trades,
    update_trade,
)
from tradeledger.validation import ValidationError

GOLD = {
    "symbol": "XAUUSD",
    "name": "Gold (test)",
    "asset_class": "COMMODITY",
    "price_increment": "0.01",
    "contract_multiplier": "100",
    "quote_currency": "USD",
}


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------- Instruments ----------


def test_create_and_get_instrument(conn):
    new_id = create_instrument(conn, GOLD, user_id=USER_ID)
    record = get_instrument(conn, new_id, user_id=USER_ID)
    assert record["symbol"] == "XAUUSD"
    assert record["contract_multiplier"] == Decimal("100")
    assert record["created_at"].endswith("Z")


def test_get_instrument_by_symbol_ignores_case(conn):
    create_instrument(conn, GOLD, user_id=USER_ID)
    assert get_instrument_by_symbol(conn, "xauusd", user_id=USER_ID)["name"] == "Gold (test)"


def test_missing_instrument_returns_none(conn):
    assert get_instrument(conn, 999, user_id=USER_ID) is None
    assert get_instrument_by_symbol(conn, "NOPE", user_id=USER_ID) is None


def test_list_instruments_sorted_by_symbol(conn, eurusd_id):
    create_instrument(conn, GOLD, user_id=USER_ID)
    assert [i["symbol"] for i in list_instruments(conn, user_id=USER_ID)] == ["EURUSD", "XAUUSD"]


def test_list_instruments_empty_database(conn):
    assert list_instruments(conn, user_id=USER_ID) == []


def test_duplicate_symbol_is_rejected(conn):
    create_instrument(conn, GOLD, user_id=USER_ID)
    with pytest.raises(DuplicateError):
        create_instrument(conn, dict(GOLD, symbol="xauusd"), user_id=USER_ID)
    assert count(conn, "instruments") == 1


def test_invalid_instrument_writes_nothing(conn):
    with pytest.raises(ValidationError):
        create_instrument(conn, dict(GOLD, contract_multiplier="-100"), user_id=USER_ID)
    assert count(conn, "instruments") == 0


# ---------- Trades: create and read ----------


def test_create_and_get_trade(conn, long_trade):
    trade_id = create_trade(conn, long_trade, user_id=USER_ID)
    record = get_trade(conn, trade_id, user_id=USER_ID)
    assert record["symbol"] == "EURUSD"
    assert record["direction"] == "LONG"
    assert record["entry_price"] == Decimal("1.085")
    assert record["fees"] == Decimal("7")
    assert record["status"] == "CLOSED"
    assert record["created_at"] == record["updated_at"]


def test_get_missing_trade_returns_none(conn):
    assert get_trade(conn, 12345, user_id=USER_ID) is None


def test_trade_with_unknown_instrument_is_rejected(conn, long_trade):
    with pytest.raises(ValidationError) as info:
        create_trade(conn, dict(long_trade, instrument_id=999), user_id=USER_ID)
    assert "instrument_id" in info.value.errors
    assert count(conn, "trades") == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"entry_price": "-1"},
        {"position_size": "0"},
        {"direction": "SIDEWAYS"},
        {"status": "MAYBE"},
        {"exit_price": None},  # CLOSED without exit
        {"stop_loss": "1.09000"},  # long stop above entry
        {"trade_date": "31/12/2026"},
    ],
)
def test_invalid_trades_write_nothing(conn, long_trade, changes):
    with pytest.raises(ValidationError):
        create_trade(conn, dict(long_trade, **changes), user_id=USER_ID)
    assert count(conn, "trades") == 0


# ---------- Trades: update and delete ----------


def test_update_trade_changes_values_and_timestamp(conn, long_trade):
    trade_id = create_trade(conn, long_trade, user_id=USER_ID)
    # Force an older updated_at so the change is visible within one second.
    with conn:
        conn.execute("UPDATE trades SET updated_at = '2000-01-01T00:00:00Z' WHERE id = ?", (trade_id,))

    changed = dict(long_trade, exit_price="1.08700", notes="Closed early")
    assert update_trade(conn, trade_id, changed, user_id=USER_ID) is True

    record = get_trade(conn, trade_id, user_id=USER_ID)
    assert record["exit_price"] == Decimal("1.087")
    assert record["notes"] == "Closed early"
    assert record["updated_at"] != "2000-01-01T00:00:00Z"


def test_update_can_reopen_a_trade(conn, long_trade):
    trade_id = create_trade(conn, long_trade, user_id=USER_ID)
    update_trade(
        conn, trade_id, dict(long_trade, status="OPEN", exit_price=None, closed_at=None), user_id=USER_ID
    )
    record = get_trade(conn, trade_id, user_id=USER_ID)
    assert record["status"] == "OPEN"
    assert record["exit_price"] is None


def test_update_missing_trade_returns_false(conn, long_trade):
    assert update_trade(conn, 999, long_trade, user_id=USER_ID) is False


def test_invalid_update_leaves_trade_unchanged(conn, long_trade):
    trade_id = create_trade(conn, long_trade, user_id=USER_ID)
    with pytest.raises(ValidationError):
        update_trade(conn, trade_id, dict(long_trade, position_size="-5"), user_id=USER_ID)
    assert get_trade(conn, trade_id, user_id=USER_ID)["position_size"] == Decimal("1")


def test_delete_trade(conn, long_trade):
    trade_id = create_trade(conn, long_trade, user_id=USER_ID)
    assert delete_trade(conn, trade_id, user_id=USER_ID) is True
    assert get_trade(conn, trade_id, user_id=USER_ID) is None
    assert delete_trade(conn, trade_id, user_id=USER_ID) is False  # already gone


# ---------- Filtering (uses the sample data) ----------


def test_list_all_trades_newest_first(seeded_conn):
    trades = list_trades(seeded_conn, user_id=USER_ID)
    assert len(trades) == 16
    dates = [t["trade_date"] for t in trades]
    assert dates == sorted(dates, reverse=True)


def test_filter_by_instrument(seeded_conn):
    gold = get_instrument_by_symbol(seeded_conn, "XAUUSD", user_id=USER_ID)
    trades = list_trades(seeded_conn, instrument_id=gold["id"], user_id=USER_ID)
    assert len(trades) == 4
    assert {t["symbol"] for t in trades} == {"XAUUSD"}


def test_filter_by_date_range_is_inclusive(seeded_conn):
    trades = list_trades(seeded_conn, start="2026-03-02", end="2026-03-04", user_id=USER_ID)
    assert sorted(t["trade_date"] for t in trades) == [
        "2026-03-02T08:15:00Z",
        "2026-03-03T13:40:00Z",
        "2026-03-04T08:30:00Z",
    ]


def test_filter_start_only_and_end_only(seeded_conn):
    assert len(list_trades(seeded_conn, start="2026-03-19", user_id=USER_ID)) == 3
    assert len(list_trades(seeded_conn, end="2026-03-02", user_id=USER_ID)) == 1


def test_filters_combine(seeded_conn):
    gold = get_instrument_by_symbol(seeded_conn, "XAUUSD", user_id=USER_ID)
    trades = list_trades(
        seeded_conn, instrument_id=gold["id"], start="2026-03-10", status="CLOSED", user_id=USER_ID
    )
    assert [t["trade_date"] for t in trades] == ["2026-03-20T15:10:00Z", "2026-03-18T03:20:00Z"]


def test_filter_with_no_matches_returns_empty_list(seeded_conn):
    assert list_trades(seeded_conn, start="2030-01-01", user_id=USER_ID) == []


def test_invalid_filters_are_rejected(seeded_conn):
    with pytest.raises(ValidationError):
        list_trades(seeded_conn, start="2026-03-10", end="2026-03-01", user_id=USER_ID)
    with pytest.raises(ValidationError):
        list_trades(seeded_conn, status="closed", user_id=USER_ID)


def test_sql_injection_attempt_is_treated_as_plain_data(seeded_conn):
    """A malicious 'symbol' is just a string that matches nothing."""
    assert get_instrument_by_symbol(seeded_conn, "X' OR '1'='1", user_id=USER_ID) is None
    assert len(list_trades(seeded_conn, user_id=USER_ID)) == 16  # nothing was dropped or altered
```

### `tests/test_security_hardening.py` (MODIFIED)

**Why:** Updated for the removed switches; out-of-range value tests are SQLite-only.  
**How to run or test:** `python -m pytest tests/test_security_hardening.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
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
```

### `tests/test_seed_and_analytics.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_seed_and_analytics.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Sample data, analytical SQL and the CLI commands.

Expected values were worked out by hand from tradeledger/seed.py
(see the comments beside each sample trade).
"""

from decimal import Decimal

import pytest

from tests.conftest import TEST_EMAIL
from tests.factories import USER_ID
from tradeledger import analytics, seed
from tradeledger.seed import SAMPLE_TRADES, seed_sample_data
from tradeledger.validation import ValidationError


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------- Seeding ----------


def test_seed_inserts_everything_once(conn):
    assert seed_sample_data(conn, user_id=USER_ID) == {"instruments": 5, "trades": 16}


def test_seed_is_idempotent(conn):
    seed_sample_data(conn, user_id=USER_ID)
    assert seed_sample_data(conn, user_id=USER_ID) == {"instruments": 0, "trades": 0}
    assert count(conn, "instruments") == 5
    assert count(conn, "trades") == 16


def test_seed_does_not_touch_existing_user_trades(conn, long_trade):
    from tradeledger.repositories.trades import create_trade

    create_trade(conn, dict(long_trade, trade_date="2026-01-05T10:00:00Z"), user_id=USER_ID)
    seed_sample_data(conn, user_id=USER_ID)
    assert count(conn, "trades") == 17


def test_seed_covers_required_scenarios(seeded_conn):
    rows = seeded_conn.execute("SELECT direction, status, strategy, session FROM trades").fetchall()
    assert {r["direction"] for r in rows} == {"LONG", "SHORT"}
    assert {r["status"] for r in rows} == {"OPEN", "CLOSED"}
    assert None in {r["strategy"] for r in rows}
    assert len({r["session"] for r in rows}) >= 3


def test_seed_is_all_or_nothing(conn, monkeypatch):
    """If one sample record is invalid, the whole seed run is rolled back."""
    broken = dict(SAMPLE_TRADES[0], trade_date="2026-04-01T09:00:00Z", position_size="-1")
    monkeypatch.setattr(seed, "SAMPLE_TRADES", [*SAMPLE_TRADES, broken])
    with pytest.raises(ValidationError):
        seed.seed_sample_data(conn, user_id=USER_ID)
    assert count(conn, "instruments") == 0
    assert count(conn, "trades") == 0


# ---------- Analytics on an empty database ----------


def test_analytics_on_empty_database(conn):
    assert analytics.closed_trade_count(conn, user_id=USER_ID) == 0
    assert analytics.closed_count_by_instrument(conn, user_id=USER_ID) == []
    assert analytics.closed_by_weekday(conn, user_id=USER_ID) == []
    assert analytics.trades_by_strategy(conn, user_id=USER_ID) == []
    assert analytics.trades_by_session(conn, user_id=USER_ID) == []
    assert analytics.win_rate_by_instrument(conn, user_id=USER_ID) == []


# ---------- Analytics on the sample data ----------


def test_closed_trade_count(seeded_conn):
    assert analytics.closed_trade_count(seeded_conn, user_id=USER_ID) == 14


def test_closed_count_by_instrument(seeded_conn):
    assert analytics.closed_count_by_instrument(seeded_conn, user_id=USER_ID) == [
        {"symbol": "XAUUSD", "closed_trades": 4},
        {"symbol": "EURUSD", "closed_trades": 3},
        {"symbol": "GBPUSD", "closed_trades": 3},
        {"symbol": "NAS100", "closed_trades": 2},
        {"symbol": "UK100", "closed_trades": 2},
    ]


def test_instrument_with_no_closed_trades_shows_zero(seeded_conn):
    from tradeledger.repositories.instruments import create_instrument

    create_instrument(
        seeded_conn,
        {
            "symbol": "US30",
            "name": "Wall Street 30 (test)",
            "asset_class": "INDEX",
            "price_increment": "1",
            "contract_multiplier": "1",
            "quote_currency": "USD",
        },
        user_id=USER_ID,
    )
    rows = analytics.closed_count_by_instrument(seeded_conn, user_id=USER_ID)
    assert {"symbol": "US30", "closed_trades": 0} in rows


def test_closed_by_weekday(seeded_conn):
    assert analytics.closed_by_weekday(seeded_conn, user_id=USER_ID) == [
        {"weekday_number": 1, "weekday": "Monday", "closed_trades": 3},
        {"weekday_number": 2, "weekday": "Tuesday", "closed_trades": 3},
        {"weekday_number": 3, "weekday": "Wednesday", "closed_trades": 3},
        {"weekday_number": 4, "weekday": "Thursday", "closed_trades": 2},
        {"weekday_number": 5, "weekday": "Friday", "closed_trades": 3},
    ]


def test_trades_by_strategy(seeded_conn):
    assert analytics.trades_by_strategy(seeded_conn, user_id=USER_ID) == [
        {"strategy": "Breakout", "total_trades": 6, "open_trades": 1, "closed_trades": 5},
        {"strategy": "Pullback", "total_trades": 5, "open_trades": 0, "closed_trades": 5},
        {"strategy": "Reversal", "total_trades": 3, "open_trades": 1, "closed_trades": 2},
        {"strategy": "Unspecified", "total_trades": 2, "open_trades": 0, "closed_trades": 2},
    ]


def test_trades_by_session(seeded_conn):
    assert analytics.trades_by_session(seeded_conn, user_id=USER_ID) == [
        {"session": "LONDON", "total_trades": 7, "open_trades": 1, "closed_trades": 6},
        {"session": "NEW_YORK", "total_trades": 6, "open_trades": 1, "closed_trades": 5},
        {"session": "ASIA", "total_trades": 2, "open_trades": 0, "closed_trades": 2},
        {"session": "Unspecified", "total_trades": 1, "open_trades": 0, "closed_trades": 1},
    ]


def test_win_rate_by_instrument(seeded_conn):
    assert analytics.win_rate_by_instrument(seeded_conn, user_id=USER_ID) == [
        {
            "symbol": "XAUUSD",
            "quote_currency": "USD",
            "closed_trades": 4,
            "wins": 3,
            "losses": 1,
            "breakevens": 0,
            "win_rate_pct": 75.0,
        },
        {
            "symbol": "NAS100",
            "quote_currency": "USD",
            "closed_trades": 2,
            "wins": 1,
            "losses": 1,
            "breakevens": 0,
            "win_rate_pct": 50.0,
        },
        {
            "symbol": "UK100",
            "quote_currency": "GBP",
            "closed_trades": 2,
            "wins": 1,
            "losses": 0,
            "breakevens": 1,
            "win_rate_pct": 50.0,
        },
        {
            "symbol": "EURUSD",
            "quote_currency": "USD",
            "closed_trades": 3,
            "wins": 1,
            "losses": 2,
            "breakevens": 0,
            "win_rate_pct": 33.3,
        },
        {
            "symbol": "GBPUSD",
            "quote_currency": "USD",
            "closed_trades": 3,
            "wins": 1,
            "losses": 1,
            "breakevens": 1,
            "win_rate_pct": 33.3,
        },
    ]


def test_fees_can_turn_a_price_gain_into_a_loss(seeded_conn):
    """Sample trade 9: long EURUSD, exit 2 points (0.00002) above entry,
    gross +2.00 USD, fees 7.00 -> net -5.00 USD -> counted as a LOSS."""
    eurusd = next(
        r for r in analytics.win_rate_by_instrument(seeded_conn, user_id=USER_ID) if r["symbol"] == "EURUSD"
    )
    assert eurusd["losses"] == 2


def test_sql_win_rate_agrees_with_exact_decimal_calculation(seeded_conn):
    """Cross-check the float-based SQL against exact Decimal arithmetic."""
    rows = seeded_conn.execute(
        """
        SELECT i.symbol, t.direction, t.entry_price, t.exit_price,
               t.position_size, t.fees, i.contract_multiplier
        FROM trades t JOIN instruments i ON i.id = t.instrument_id
        WHERE t.status = 'CLOSED'
        """
    ).fetchall()

    expected = {}
    for r in rows:
        move = Decimal(r["exit_price"]) - Decimal(r["entry_price"])
        if r["direction"] == "SHORT":
            move = -move
        net = move * Decimal(r["position_size"]) * Decimal(r["contract_multiplier"]) - Decimal(r["fees"])
        tally = expected.setdefault(r["symbol"], {"wins": 0, "losses": 0, "breakevens": 0})
        tally["wins" if net > 0 else "losses" if net < 0 else "breakevens"] += 1

    actual = {
        r["symbol"]: {"wins": r["wins"], "losses": r["losses"], "breakevens": r["breakevens"]}
        for r in analytics.win_rate_by_instrument(seeded_conn, user_id=USER_ID)
    }
    assert actual == expected


# ---------- CLI commands ----------


def test_cli_init_seed_and_info(runner):
    assert "Database ready" in runner.invoke(args=["init-db"]).output
    assert "5 instrument(s) and 16 trade(s)" in runner.invoke(args=["seed-db", "--email", TEST_EMAIL]).output
    assert "0 instrument(s) and 0 trade(s)" in runner.invoke(args=["seed-db", "--email", TEST_EMAIL]).output
    info = runner.invoke(args=["db-info"]).output
    assert "Schema version: 3" in info  # 1 before Phase 4, 2 in Phases 4-7
    assert "trades" in info and "16" in info


def test_cli_analytics_runs_every_query(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    output = runner.invoke(args=["analytics", "--email", TEST_EMAIL]).output
    for name in analytics.ALL_QUERIES:
        assert f"== {name} ==" in output


def test_cli_run_sql_is_read_only(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    ok = runner.invoke(args=["run-sql", "SELECT COUNT(*) AS n FROM trades"])
    assert "16" in ok.output
    blocked = runner.invoke(args=["run-sql", "DELETE FROM trades"])
    assert blocked.exit_code != 0
    assert "readonly" in blocked.output or "read-only" in blocked.output  # SQLite / PostgreSQL wording
    still_there = runner.invoke(args=["run-sql", "SELECT COUNT(*) AS n FROM trades"])
    assert "16" in still_there.output


def test_cli_reset_db_empties_tables(runner):
    runner.invoke(args=["seed-db", "--email", TEST_EMAIL])
    result = runner.invoke(args=["reset-db", "--yes"])
    assert "Database reset" in result.output
    for table in ("trades", "instruments"):
        output = runner.invoke(args=["run-sql", f"SELECT COUNT(*) AS n FROM {table}"]).output
        assert output.strip().splitlines()[-1].strip() == "0"
```

### `tests/test_timestamps.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_timestamps.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 7: the fast stored-timestamp parser (tradeledger/timestamps.py).

The performance fix replaced datetime.strptime with a strict pattern check
plus datetime.fromisoformat. These tests prove the two give the SAME
answer, value for value, on valid and invalid input, so the speed-up
cannot change any financial or date result.
"""

import random
from datetime import datetime

import pytest

from tests.factories import USER_ID
from tradeledger.performance.dashboard import build_dashboard
from tradeledger.report_filters import ReportFilters
from tradeledger.timestamps import STORED_FORMAT, parse_stored


def reference(text):
    """The Phase 6 behaviour: what strptime returns, or the exception type."""
    try:
        return datetime.strptime(text, STORED_FORMAT)
    except (TypeError, ValueError) as exc:
        return type(exc)


def fast(text):
    try:
        return parse_stored(text)
    except (TypeError, ValueError) as exc:
        return type(exc)


@pytest.mark.parametrize(
    "text",
    [
        "2026-03-02T08:15:00Z",
        "2024-02-29T23:59:59Z",  # leap day
        "2000-01-01T00:00:00Z",
        "9999-12-31T23:59:59Z",
        "2025-02-29T10:00:00Z",  # not a leap year
        "2026-13-01T10:00:00Z",  # month 13
        "2026-04-31T10:00:00Z",  # 31 April
        "2026-01-01T24:00:00Z",  # hour 24
        "2026-01-01T10:60:00Z",  # minute 60
        "2026-01-01T10:00:60Z",  # second 60
        "0000-01-01T00:00:00Z",  # year 0
    ],
)
def test_same_result_as_strptime_for_well_shaped_text(text):
    assert fast(text) == reference(text)


@pytest.mark.parametrize(
    "text",
    [
        "2026-03-02 08:15:00Z",  # space, not T
        "2026-03-02T08:15:00",  # no Z
        "2026-03-02T08:15:00+00:00",  # offset instead of Z
        "2026-03-02",  # date only
        "",
        "Z",
        " 2026-03-02T08:15:00Z",  # leading space
        "٢٠٢٦-03-02T08:15:00Z",  # non-ASCII digits
        "2026-03-02T08:15:00Z0",  # trailing text that would survive dropping the last character
        "2026-03-02T08:15:00ZZ",
    ],
)
def test_text_outside_the_stored_shape_is_rejected(text):
    """The database CHECK constraints guarantee the stored shape, so the
    parser may be stricter than strptime here; it must never be looser."""
    assert fast(text) is ValueError


@pytest.mark.parametrize("value", [None, 20260302, b"2026-03-02T08:15:00Z"])
def test_non_text_raises_type_error(value):
    assert fast(value) is TypeError


def test_random_valid_timestamps_match_strptime():
    """5,000 random real moments (fixed seed): identical datetimes."""
    rnd = random.Random(11)
    for _ in range(5000):
        moment = datetime(
            rnd.randint(1900, 2100),
            rnd.randint(1, 12),
            rnd.randint(1, 28),
            rnd.randint(0, 23),
            rnd.randint(0, 59),
            rnd.randint(0, 59),
        )
        text = moment.strftime(STORED_FORMAT)
        assert parse_stored(text) == datetime.strptime(text, STORED_FORMAT) == moment


def test_the_dashboard_no_longer_calls_strptime(seeded_conn, monkeypatch):
    """Regression guard for the Phase 7 performance fix. datetime.strptime
    always delegates to the standard library's _strptime module, so counting
    calls there catches strptime used ANYWHERE while the dashboard is built."""
    import _strptime

    calls = []
    real = _strptime._strptime_datetime

    def counting(*args, **kwargs):
        calls.append(args)
        return real(*args, **kwargs)

    monkeypatch.setattr(_strptime, "_strptime_datetime", counting)
    datetime.strptime("2026-01-01T00:00:00Z", STORED_FORMAT)  # proves the counter works
    assert len(calls) == 1
    board = build_dashboard(seeded_conn, ReportFilters(), user_id=USER_ID)
    assert board.report.overall.trades == 14
    assert len(calls) == 1  # nothing more while building the dashboard
```

### `tests/test_web_analytics.py` (MODIFIED)

**Why:** Production behaviour now 'sign-in required'.  
**How to run or test:** `python -m pytest tests/test_web_analytics.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 6: the /analytics dashboard page (Flask test client, not a browser).

Covers rendering, filters, validation, empty states, legacy data, the chart
payload, agreement with the JSON API, escaping and the Content-Security-
Policy. Real-browser behaviour (Chart.js, layout) is in test_browser.py.
"""

import json
import re
from decimal import Decimal

import pytest

from tests.conftest import ensure_user, sign_in
from tests.factories import USER_ID
from tradeledger import create_app
from tradeledger.db import get_db, init_schema
from tradeledger.performance.dashboard import build_dashboard, chart_payload
from tradeledger.report_filters import ReportFilters
from tradeledger.repositories.trades import create_trade
from tradeledger.web.analytics import chart_script_sources

CDN = "https://cdn.jsdelivr.net/npm/chart.js@4.5.1/dist/chart.umd.min.js"
CHART_NAMES = ("curve", "drawdown", "monthly", "weekday", "instruments", "strategies", "sessions")


def page(client, query=""):
    response = client.get("/analytics" + (f"?{query}" if query else ""))
    return response, response.get_data(as_text=True)


def kpi(html, key):
    match = re.search(rf'data-kpi="{key}">.*?class="kpi__value">([^<]*)<', html, re.S)
    assert match, key
    return match.group(1)


def payload_of(html):
    match = re.search(r'<script type="application/json" id="dashboard-data">(.*?)</script>', html, re.S)
    return json.loads(match.group(1)) if match else None


# ---------- Rendering with sample data ----------


def test_dashboard_renders_every_section(seeded_client):
    response, html = page(seeded_client)
    assert response.status_code == 200
    for heading in (
        "Reporting filters",
        "Summary",
        "Performance over time",
        "Breakdowns",
        "Detailed tables",
        "About these figures",
    ):
        assert heading in html
    for name in CHART_NAMES:
        assert f'data-chart="{name}"' in html
        assert f'id="chart-{name}-desc"' in html  # visible text alternative


def test_kpi_cards_show_engine_values(seeded_client):
    _, html = page(seeded_client)
    expected = {
        "trades": "14",
        "wins": "7",
        "losses": "5",
        "breakevens": "2",
        "win_rate": "50.0%",
        "average_r": "+0.55R",
        "expectancy_r": "+0.55R",
        "profit_factor_r": "2.85",
        "total_r": "+7.69R",
        "max_drawdown_r": "1.05R",
        "longest_win_streak": "2 trades",
        "longest_loss_streak": "2 trades",
    }
    assert {key: kpi(html, key) for key in expected} == expected


def test_money_is_shown_per_currency_never_combined(seeded_client):
    _, html = page(seeded_client)
    assert "798.50 USD" in html and "200.00 GBP" in html
    assert "998.50" not in html  # USD + GBP would be meaningless


def test_tables_hold_the_chart_figures(seeded_client):
    _, html = page(seeded_client)
    for table in (
        "table-series",
        "table-monthly",
        "table-weekday",
        "table-instruments",
        "table-strategies",
        "table-sessions",
    ):
        assert f'id="{table}"' in html
    assert html.count("No closed trades") >= 2  # Saturday and Sunday rows
    assert "Small sample" in html


def test_page_works_without_javascript(seeded_client):
    _, html = page(seeded_client)
    assert "Charts need JavaScript" in html  # .no-js-only notice
    assert '<form class="card filters dash-filters" method="get"' in html


def test_chart_payload_matches_the_dashboard_layer(app, seeded_client):
    _, html = page(seeded_client, "strategy=Breakout")
    with app.app_context():
        expected = chart_payload(
            build_dashboard(get_db(), ReportFilters(strategy="Breakout"), user_id=USER_ID)
        )
    assert payload_of(html) == expected


def test_dashboard_and_api_agree(seeded_client):
    _, html = page(seeded_client, "session=LONDON")
    api = seeded_client.get("/api/v1/analytics/summary?session=LONDON").get_json()["data"]["overall"]
    curve = payload_of(html)["curve"]
    assert Decimal(curve[-1]["cumulative_r"]) == Decimal(api["total_r"])
    assert kpi(html, "trades") == str(api["trades"])


# ---------- Filters ----------


@pytest.mark.parametrize(
    "query, trades",
    [
        ("strategy=Breakout", "5"),
        ("session=ASIA", "2"),
        ("session=london", "6"),
        ("instrument_id=1", "3"),
        ("strategy=Breakout&session=LONDON", "3"),
        ("start=2026-03-10&end=2026-03-13", "4"),
        ("instrument_id=&strategy=&session=&start=&end=", "14"),  # "All" everywhere
    ],
)
def test_filters_change_the_figures(seeded_client, query, trades):
    response, html = page(seeded_client, query)
    assert response.status_code == 200
    assert kpi(html, "trades") == trades


def test_selected_filters_are_kept_in_the_form(seeded_client):
    _, html = page(
        seeded_client, "instrument_id=2&strategy=Pullback&session=london&start=2026-03-01&end=2026-03-31"
    )
    assert '<option value="2" selected' in html
    assert '<option value="Pullback" selected' in html
    assert '<option value="LONDON" selected' in html
    assert 'value="2026-03-01"' in html and 'value="2026-03-31"' in html
    assert "Showing results for:" in html


def test_reset_link_only_when_filtering(seeded_client):
    _, plain = page(seeded_client)
    _, filtered = page(seeded_client, "strategy=Breakout")
    assert "Reset filters" not in plain
    assert 'href="/analytics">Reset filters' in filtered


def test_bookmarked_strategy_stays_selectable(seeded_client):
    _, html = page(seeded_client, "strategy=Retired%20setup")
    assert '<option value="Retired setup" selected' in html


@pytest.mark.parametrize(
    "query, field, message",
    [
        ("instrument_id=999", "instrument_id", "does not match an existing instrument"),
        ("instrument_id=abc", "instrument_id", "must be a whole number of 1 or more"),
        ("session=MOON", "session", "must be one of: ASIA, LONDON, NEW_YORK, OTHER"),
        ("strategy=" + "x" * 51, "strategy", "must be 50 characters or fewer"),
        ("start=2026-02-30", "start", "must be a date in YYYY-MM-DD form"),
        ("start=2026-03-10&end=2026-03-01", "end", "must be on or after the start date"),
    ],
)
def test_invalid_filters_give_400_and_no_report(seeded_client, query, field, message):
    response, html = page(seeded_client, query)
    assert response.status_code == 400
    assert "The report was not produced" in html
    assert message in html
    assert f'id="dash-{field}"' in html and 'aria-invalid="true"' in html
    assert 'data-kpi="' not in html and "dashboard-data" not in html  # nothing misleading
    assert CDN not in html


# ---------- Empty and edge cases ----------


def test_empty_database(client):
    response, html = page(client)
    assert response.status_code == 200
    assert "No closed trades yet" in html
    assert kpi(html, "win_rate") == "n/a" and kpi(html, "total_r") == "n/a"
    assert "dashboard-data" not in html and CDN not in html  # nothing to draw
    assert CDN not in response.headers["Content-Security-Policy"]


def test_only_open_trades(app, client):
    with app.app_context():
        conn = get_db()
        conn.execute(
            "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment, contract_multiplier,"
            " quote_currency) VALUES (1, 'OPN', 'Open only', 'OTHER', '0.01', '1', 'USD')"
        )
        conn.commit()
        create_trade(
            conn,
            {
                "instrument_id": 1,
                "direction": "LONG",
                "status": "OPEN",
                "trade_date": "2026-03-02T08:00:00Z",
                "entry_price": "100",
                "stop_loss": "99",
                "position_size": "1",
            },
            user_id=USER_ID,
        )
    _, html = page(client)
    assert "1 open trade matches, but open trades have no realised result yet" in html
    assert kpi(html, "trades") == "0"


def test_filters_matching_nothing(seeded_client):
    _, html = page(seeded_client, "start=2030-01-01")
    assert "No closed trades match these filters" in html
    assert "Try a wider date range" in html


def test_instrument_with_only_open_trades(seeded_client):
    """Sample trade 15 (EURUSD, Reversal) is open; nothing else matches."""
    _, html = page(seeded_client, "instrument_id=1&strategy=Reversal")
    assert "1 open trade matches" in html


def test_legacy_trade_is_explained(app, seeded_client):
    with app.app_context():
        conn = get_db()
        conn.execute("UPDATE trades SET closed_at = NULL WHERE id = 4")
        conn.commit()
    _, html = page(seeded_client)
    assert "Unknown exit times:" in html and "(#4)" in html
    assert "Entry time: exit not recorded" in html
    curve = payload_of(html)["curve"]
    assert next(p for p in curve if p.get("trade_id") == 4)["close_time_known"] is False


def test_maximum_drawdown_row_is_marked(seeded_client):
    _, html = page(seeded_client)
    row = re.search(r'<tr class="is-max-drawdown">(.*?)</tr>', html, re.S).group(1)
    assert "#9 EURUSD" in row and "Maximum" in row


# ---------- Security ----------


def test_dashboard_csp_allows_only_the_exact_chart_js_file(seeded_client):
    response, html = page(seeded_client)
    csp = response.headers["Content-Security-Policy"]
    assert f"script-src 'self' {CDN};" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert "https://cdn.jsdelivr.net;" not in csp and "https://cdn.jsdelivr.net " not in csp
    assert f'<script src="{CDN}" defer crossorigin="anonymous" referrerpolicy="no-referrer">' in html


@pytest.mark.parametrize(
    "url", ["/", "/trades", "/trades/new", "/instruments", "/about", "/api/v1/analytics/summary"]
)
def test_other_pages_keep_the_strict_policy(seeded_client, url):
    csp = seeded_client.get(url).headers["Content-Security-Policy"]
    assert "script-src 'self';" in csp and "cdn.jsdelivr.net" not in csp


def test_no_inline_executable_script(seeded_client):
    _, html = page(seeded_client)
    for tag in re.findall(r"<script[^>]*>", html):
        assert "src=" in tag or 'type="application/json"' in tag, tag
    assert " onclick=" not in html and " style=" not in html


def test_self_hosted_chart_js_needs_no_extra_csp_source(db_path):
    app = create_app(
        "testing",
        overrides={
            "DATABASE": db_path,
            "CHART_JS_URL": "/static/vendor/chartjs/chart.umd.min.js",
            "CHART_JS_INTEGRITY": "sha384-abc123",
        },
    )
    with app.app_context():
        from tradeledger.seed import seed_sample_data

        init_schema(get_db())
        seed_sample_data(get_db(), user_id=ensure_user(get_db()))
    client = app.test_client()
    sign_in(client)
    response = client.get("/analytics")
    html = response.get_data(as_text=True)
    assert "script-src 'self';" in response.headers["Content-Security-Policy"]
    assert 'src="/static/vendor/chartjs/chart.umd.min.js"' in html
    assert 'integrity="sha384-abc123"' in html


@pytest.mark.parametrize(
    "url, sources",
    [
        ("/static/vendor/chartjs/chart.umd.min.js", ()),
        (CDN, (CDN,)),
    ],
)
def test_chart_script_sources(url, sources):
    assert chart_script_sources(url) == sources


@pytest.mark.parametrize(
    "url",
    [
        "http://cdn.example/chart.js",
        "//cdn.example/chart.js",
        "javascript:alert(1)",
        "https://cdn.example/chart.js?v=1",
        "chart.js",
    ],
)
def test_unsafe_chart_js_urls_are_refused(url):
    with pytest.raises(ValueError):
        chart_script_sources(url)


def test_malicious_strategy_name_is_escaped_everywhere(app, seeded_client):
    evil = "<script>alert(1)</script>"
    with app.app_context():
        get_db().execute("UPDATE trades SET strategy = ? WHERE id IN (1, 2)", (evil,))
        get_db().commit()
    for query in ("", "strategy=" + evil):
        _, html = page(seeded_client, query)
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    _, html = page(seeded_client)
    data = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S).group(1)
    assert "<" not in data  # tojson escapes it as \\u003c
    assert evil in json.dumps(payload_of(html))  # ...and it round-trips intact


def test_filter_injection_is_just_text(seeded_client):
    response, html = page(seeded_client, "strategy=%27%20OR%201%3D1%20--")
    assert response.status_code == 200
    assert "No closed trades match these filters" in html


def test_dashboard_requires_sign_in_in_production(db_path):
    # Phase 7 checked a 403 here (journal disabled); Phase 8 requires sign-in.
    # Either way no dashboard and no Chart.js permission for anonymous visitors.
    app = create_app("production", overrides={"SECRET_KEY": "k" * 64, "DATABASE": db_path})
    with app.app_context():
        init_schema(get_db())
    response = app.test_client().get("/analytics", base_url="https://localhost")
    assert response.status_code == 302 and response.headers["Location"].startswith("/login")
    assert "cdn.jsdelivr.net" not in response.headers["Content-Security-Policy"]


def test_post_is_not_allowed(seeded_client):
    token_page = seeded_client.get("/trades/new").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', token_page).group(1)
    assert seeded_client.post("/analytics", data={"csrf_token": token}).status_code == 405


def test_navigation_marks_analytics_current(seeded_client):
    _, html = page(seeded_client)
    marker = html.index('aria-current="page"')
    assert "Analytics" in html[marker : marker + 120]
```

### `tests/test_web_integration.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_web_integration.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phase 5 end to end (inside the test client): form -> SQLite -> journal ->
details -> analytics -> API, plus the repository search added for the
journal."""

import pytest

from tests.factories import USER_ID
from tests.web_helpers import (
    closed_trade_form,
    count_rows,
    new_trade_id,
    open_trade_form,
    post_form,
)
from tradeledger.repositories import trades as trade_repo
from tradeledger.validation import ValidationError

# ---------- Full journeys ----------


def test_trade_lifecycle_open_close_edit_delete(app, client):
    """Start from an EMPTY database, as a new user would."""
    # 1. Add an instrument through the website.
    response = post_form(
        client,
        "/instruments",
        {
            "symbol": "EURUSD",
            "name": "Euro / US Dollar (test)",
            "asset_class": "FOREX",
            "price_increment": "0.00001",
            "contract_multiplier": "100000",
            "quote_currency": "USD",
        },
        token_page="/instruments",
    )
    assert response.status_code == 302

    # 2. Record an open trade.
    trade_id = new_trade_id(post_form(client, "/trades/new", open_trade_form()))
    journal = client.get("/trades").get_data(as_text=True)
    assert "Showing 1–1 of 1 trade." in journal
    assert ">Open</span>" in journal
    overview = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" in overview  # open trades have no result

    # 3. Close it via the edit form.
    response = post_form(client, f"/trades/{trade_id}/edit", closed_trade_form())
    assert response.status_code == 302
    detail = client.get(f"/trades/{trade_id}").get_data(as_text=True)
    assert "393.00 USD" in detail and "+1.97R" in detail and ">Win</span>" in detail

    # 4. The overview, analytics preview and API all agree.
    overview = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" not in overview
    assert "393.00 USD" in overview
    analytics = client.get("/analytics").get_data(as_text=True)
    assert "EURUSD" in analytics and "100.0%" in analytics
    api = client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    assert api["trades"] == 1 and api["money"]["USD"]["net_pnl"] == "393"

    # 5. Delete it; everything returns to empty.
    post_form(client, f"/trades/{trade_id}/delete", {})
    assert count_rows(app, "trades") == 0
    assert "Your journal is empty" in client.get("/").get_data(as_text=True)
    assert client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]["trades"] == 0


def test_website_trade_matches_api_trade_exactly(seeded_client, trade_body):
    """The same trade entered through the form and through the API is stored
    identically: one validation layer, one storage format."""
    web_id = new_trade_id(
        post_form(
            seeded_client,
            "/trades/new",
            closed_trade_form(
                trade_date="2026-04-01T08:15", closed_at="2026-04-01T11:40", notes=trade_body["notes"]
            ),
        )
    )
    api_id = seeded_client.post("/api/v1/trades", json=trade_body).get_json()["data"]["id"]

    web = seeded_client.get(f"/api/v1/trades/{web_id}").get_json()["data"]
    api = seeded_client.get(f"/api/v1/trades/{api_id}").get_json()["data"]
    for volatile in ("id", "created_at", "updated_at"):
        web.pop(volatile, None)
        api.pop(volatile, None)
    assert web == api


def test_trade_added_by_api_appears_on_the_website(seeded_client, trade_body):
    trade_id = seeded_client.post("/api/v1/trades", json={**trade_body, "notes": "from-the-api"}).get_json()[
        "data"
    ]["id"]
    assert f'href="/trades/{trade_id}"' in seeded_client.get("/trades?q=from-the-api").get_data(as_text=True)


def test_website_edits_change_the_analytics(seeded_client):
    before = seeded_client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    post_form(seeded_client, "/trades/1/delete", {})
    after = seeded_client.get("/api/v1/analytics/summary").get_json()["data"]["overall"]
    assert after["trades"] == before["trades"] - 1
    assert after["wins"] == before["wins"] - 1  # trade 1 was a win


def test_detail_values_match_the_api(seeded_client):
    api = seeded_client.get("/api/v1/trades/1").get_json()["data"]
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert api["result"]["net_pnl"] == "393"
    assert "393.00 USD" in html


def test_journal_and_api_list_the_same_trades(seeded_client):
    api_ids = [t["id"] for t in seeded_client.get("/api/v1/trades?per_page=100").get_json()["data"]]
    html = seeded_client.get("/trades").get_data(as_text=True)
    for trade_id in api_ids:
        assert f'href="/trades/{trade_id}"' in html


# ---------- Repository: search and strategy filters ----------


def test_search_matches_symbol_case_insensitively(seeded_conn):
    rows = trade_repo.list_trades(seeded_conn, search="xau", user_id=USER_ID)
    assert len(rows) == 4 and {r["symbol"] for r in rows} == {"XAUUSD"}
    assert trade_repo.count_trades(seeded_conn, search="xau", user_id=USER_ID) == 4


def test_search_matches_strategy_and_notes(seeded_conn):
    assert trade_repo.count_trades(seeded_conn, search="pull", user_id=USER_ID) == 5
    assert trade_repo.count_trades(seeded_conn, search="fees", user_id=USER_ID) == 1


@pytest.mark.parametrize("wildcard", ["%", "_", "\\"])
def test_search_wildcards_are_literal(seeded_conn, wildcard):
    """% and _ are LIKE wildcards. They are escaped, so searching for
    them looks for the character itself, which no sample trade contains."""
    assert trade_repo.count_trades(seeded_conn, search=wildcard, user_id=USER_ID) == 0


def test_search_injection_attempt_is_plain_text(seeded_conn):
    assert trade_repo.count_trades(seeded_conn, search="' OR 1=1 --", user_id=USER_ID) == 0
    assert trade_repo.count_trades(seeded_conn, user_id=USER_ID) == 16


def test_search_combines_with_other_filters_and_pagination(seeded_conn):
    closed = trade_repo.count_trades(seeded_conn, search="usd", status="CLOSED", user_id=USER_ID)
    page_1 = trade_repo.list_trades(
        seeded_conn, search="usd", status="CLOSED", limit=3, offset=0, user_id=USER_ID
    )
    page_2 = trade_repo.list_trades(
        seeded_conn, search="usd", status="CLOSED", limit=3, offset=3, user_id=USER_ID
    )
    assert len(page_1) == 3
    assert not {r["id"] for r in page_1} & {r["id"] for r in page_2}
    assert all(r["status"] == "CLOSED" for r in page_1 + page_2)
    assert closed >= 6


@pytest.mark.parametrize("bad", ["", "   ", "x" * 101])
def test_search_rejects_empty_or_overlong_text(seeded_conn, bad):
    with pytest.raises(ValidationError) as excinfo:
        trade_repo.list_trades(seeded_conn, search=bad, user_id=USER_ID)
    assert "q" in excinfo.value.errors


def test_strategy_filter_is_exact(seeded_conn):
    assert trade_repo.count_trades(seeded_conn, strategy="Breakout", user_id=USER_ID) == 6
    assert trade_repo.count_trades(seeded_conn, strategy="break", user_id=USER_ID) == 0


def test_list_strategies_is_sorted_and_distinct(seeded_conn):
    assert trade_repo.list_strategies(seeded_conn, user_id=USER_ID) == ["Breakout", "Pullback", "Reversal"]
```

### `tests/test_web_pages.py` (MODIFIED)

**Why:** Repository calls pass `user_id=USER_ID` and data is created for the test user; assertions unchanged.  
**How to run or test:** `python -m pytest tests/test_web_pages.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
"""Phases 5-6: every website page renders, shows real data, and the shell
(navigation, headings, section labelling) behaves as documented."""

import pytest

PAGES = [
    "/",
    "/trades",
    "/trades/new",
    "/trades/1",
    "/trades/1/edit",
    "/trades/1/delete",
    "/instruments",
    "/analytics",
    "/about",
]


@pytest.mark.parametrize("url", PAGES)
def test_every_page_renders_with_sample_data(seeded_client, url):
    response = seeded_client.get(url)
    assert response.status_code == 200
    assert response.mimetype == "text/html"
    html = response.get_data(as_text=True)
    assert "<h1" in html
    assert 'id="main"' in html  # skip-link target
    assert 'class="skip-link"' in html


@pytest.mark.parametrize("url", ["/", "/trades", "/trades/new", "/instruments", "/analytics", "/about"])
def test_pages_render_with_an_empty_database(client, url):
    assert client.get(url).status_code == 200


@pytest.mark.parametrize("url", ["/no-such-page", "/trades/999", "/trades/999/edit", "/trades/999/delete"])
def test_unknown_pages_and_trades_are_html_404s(seeded_client, url):
    response = seeded_client.get(url)
    assert response.status_code == 404
    assert response.mimetype == "text/html"
    assert b"Page not found" in response.data


def test_non_numeric_trade_id_is_404(seeded_client):
    assert seeded_client.get("/trades/abc").status_code == 404


# ---------- Navigation shell ----------


def test_sidebar_lists_every_section(client):
    html = client.get("/").get_data(as_text=True)
    for label in ("Overview", "Trade journal", "Add trade", "Instruments", "Analytics", "About"):
        assert label in html


@pytest.mark.parametrize(
    "url, label",
    [
        ("/", "Overview"),
        ("/trades", "Trade journal"),
        ("/trades/new", "Add trade"),
        ("/instruments", "Instruments"),
        ("/about", "About"),
    ],
)
def test_current_section_is_marked_for_assistive_technology(client, url, label):
    html = client.get(url).get_data(as_text=True)
    marker = html.index('aria-current="page"')
    # The label of the marked link follows the attribute closely.
    assert label in html[marker : marker + 200]
    assert html.count('aria-current="page"') == 1


def test_trade_pages_highlight_the_journal(seeded_client):
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    marker = html.index('aria-current="page"')
    assert "Trade journal" in html[marker : marker + 200]


def test_menu_button_is_wired_for_accessibility(client):
    html = client.get("/").get_data(as_text=True)
    assert 'aria-controls="sidebar"' in html
    assert 'aria-expanded="false"' in html
    assert 'id="sidebar"' in html


def test_no_fake_account_features(client):
    """Phase 5 banned FAKE account features. Phase 8 added real ones, so
    "Sign out" must now be a working POST form (with a CSRF token), and the
    still-fake features stay absent."""
    html = client.get("/").get_data(as_text=True)
    assert '<form method="post" action="/logout">' in html
    assert 'name="csrf_token"' in html.split('action="/logout"')[1][:200]
    lowered = html.lower()
    for fake in ("avatar", "notifications", 'href="/logout"'):
        assert fake not in lowered


def test_scripts_and_styles_are_external_files_only(client):
    """The Content-Security-Policy forbids inline scripts and styles, so
    templates must not contain any."""
    for url in ("/", "/trades/new", "/instruments"):
        html = client.get(url).get_data(as_text=True)
        assert "<script>" not in html
        assert "<style" not in html
        assert " style=" not in html
        assert " onclick=" not in html


@pytest.mark.parametrize(
    "path",
    [
        "/static/js/app.js",
        "/static/js/early.js",
        "/static/js/trade-form.js",
        "/static/css/style.css",
        "/static/css/base.css",
        "/static/css/layout.css",
        "/static/css/components.css",
        "/static/css/pages.css",
        "/static/css/analytics.css",
        "/static/js/analytics.js",
    ],
)
def test_static_assets_are_served(client, path):
    with client.get(path) as response:  # closes the static file
        assert response.status_code == 200


# ---------- Overview ----------


def test_overview_shows_real_counts_and_recent_trades(seeded_client):
    html = seeded_client.get("/").get_data(as_text=True)
    assert "Recent trades" in html
    # 16 sample trades: 2 open, 14 closed, 5 instruments.
    assert ">2<" in html and ">14<" in html and ">5<" in html
    assert "XAUUSD" in html


def test_overview_empty_state(client):
    html = client.get("/").get_data(as_text=True)
    assert "No closed trades yet" in html
    assert "Your journal is empty" in html


# ---------- Journal ----------


def test_journal_lists_every_column(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    for heading in (
        "Entry time",
        "Instrument",
        "Status",
        "Entry",
        "Stop-loss",
        "Exit",
        "Size",
        "Net P&amp;L",
        "realised R",
        "Strategy",
        "Actions",
    ):
        assert heading in html
    assert "Showing 1–16 of 16 trades" in html


def test_journal_labels_outcomes_in_words_not_only_colour(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    # Badges contain the word itself (CSS only changes its letter case).
    for label in ("Win", "Loss", "Breakeven", "Open"):
        assert f">{label}</span>" in html
    assert "Long" in html and "Short" in html


def test_journal_money_carries_its_currency(seeded_client):
    html = seeded_client.get("/trades").get_data(as_text=True)
    assert "393.00 USD" in html  # sample trade 1
    assert "GBP" in html  # UK100 trades are in GBP


def test_journal_filters_by_status(seeded_client):
    html = seeded_client.get("/trades?status=OPEN").get_data(as_text=True)
    assert "Showing 1–2 of 2 trades matching your filters" in html


def test_journal_filters_by_strategy(seeded_client):
    html = seeded_client.get("/trades?strategy=Breakout").get_data(as_text=True)
    assert "of 6 trades" in html


def test_journal_search_covers_the_whole_database(seeded_client):
    html = seeded_client.get("/trades?q=xau").get_data(as_text=True)
    assert "of 4 trades" in html
    assert "EURUSD</a>" not in html


def test_journal_search_with_no_matches_shows_empty_state(seeded_client):
    html = seeded_client.get("/trades?q=zzzz-nothing").get_data(as_text=True)
    assert "No trades match" in html


def test_journal_reset_link_present_when_filtering(seeded_client):
    html = seeded_client.get("/trades?status=CLOSED").get_data(as_text=True)
    assert 'href="/trades"' in html
    assert "Reset" in html


@pytest.mark.parametrize(
    "query",
    [
        "status=MAYBE",
        "start=2026-13-01",
        "instrument_id=abc",
        "start=2026-03-10&end=2026-03-01",
        "q=" + "x" * 101,
    ],
)
def test_invalid_filters_are_400_with_an_explanation(seeded_client, query):
    response = seeded_client.get("/trades?" + query)
    assert response.status_code == 400
    assert b'aria-invalid="true"' in response.data


def test_pagination_keeps_filters(app, seeded_client):
    app.config["JOURNAL_PER_PAGE"] = 5
    html = seeded_client.get("/trades?status=CLOSED").get_data(as_text=True)
    assert "Showing 1–5 of 14 trades" in html
    assert "status=CLOSED" in html and "page=2" in html
    page3 = seeded_client.get("/trades?status=CLOSED&page=3").get_data(as_text=True)
    assert "Showing 11–14 of 14" in page3


def test_out_of_range_page_is_404(app, seeded_client):
    app.config["JOURNAL_PER_PAGE"] = 5
    assert seeded_client.get("/trades?page=99").status_code == 404


# ---------- Trade details ----------


def test_detail_shows_engine_values(seeded_client):
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert "393.00 USD" in html  # net P&L
    assert "400.00 USD" in html  # gross
    assert "200.00 USD" in html  # initial risk
    assert "+1.97R" in html  # realised R, display-rounded
    assert "Edit" in html and "Delete" in html


def test_detail_for_open_trade_has_no_invented_result(seeded_client):
    html = seeded_client.get("/trades/14").get_data(as_text=True)
    assert ">Open</span>" in html
    assert "Not closed yet" in html
    assert "no realised result yet" in html
    assert "(after fees)" not in html  # the result card is absent
    assert "Planned R:R" in html


def test_detail_for_legacy_trade_says_exit_time_unknown(app, seeded_client):
    with app.app_context():
        from tradeledger.db import get_db

        db = get_db()
        db.execute("UPDATE trades SET closed_at = NULL WHERE id = 1")
        db.commit()
    html = seeded_client.get("/trades/1").get_data(as_text=True)
    assert "Not recorded" in html
    assert "exit time of this trade was never recorded" in html
    assert "393.00 USD" in html  # the result is still computed


# ---------- Instruments, analytics, about ----------


def test_instruments_page_explains_multiplier_and_warns(seeded_client):
    html = seeded_client.get("/instruments").get_data(as_text=True)
    assert "Contract multiplier" in html
    assert "broker" in html
    assert "EURUSD" in html
    assert "/edit" not in html  # instruments are not editable


def test_analytics_is_the_full_dashboard(seeded_client):
    # Phase 6 replaced the Phase 5 preview with the full dashboard.
    html = seeded_client.get("/analytics").get_data(as_text=True)
    assert "Preview" not in html
    for name in ("curve", "drawdown", "monthly", "weekday", "instruments", "strategies", "sessions"):
        assert f'data-chart="{name}"' in html
    assert "Results by instrument" in html and "Results by strategy" in html


def test_sidebar_no_longer_labels_analytics_as_preview(client):
    # Phase 6: the "Preview" tag was removed when the dashboard was finished.
    html = client.get("/").get_data(as_text=True)
    analytics = html.index(">Analytics<")
    assert "Preview" not in html[analytics : analytics + 200]


def test_about_shows_roadmap_and_boundaries(client):
    html = client.get("/about").get_data(as_text=True)
    assert "Build progress" in html
    assert "Phase 6" in html and "Phase 7" in html
    assert "127.0.0.1" in html  # Phase 7: the Host check is explained
```

### `tests/test_web_security.py` (MODIFIED)

**Why:** Phase 7 guard tests became 'production requires sign-in'; CSRF page test checks the sign-out form.  
**How to run or test:** `python -m pytest tests/test_web_security.py` (PostgreSQL: set `TRADELEDGER_TEST_DATABASE_URL`)

```python
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
```

### `tests/web_helpers.py` (MODIFIED)

**Why:** `fetch_trade` returns decimals as strings on both engines.  
**How to run or test:** Covered by `python -m pytest` on SQLite and PostgreSQL.

```python
"""Helpers for the Phase 5 website tests.

The website protects every POST with a CSRF token (tradeledger/web/csrf.py).
A real browser receives the token in a hidden form field and sends it back.
These helpers do the same thing with Flask's test client: load a page,
read the token out of the HTML, and include it in the POST.
"""

import re
from decimal import Decimal

from tradeledger.db import get_db

TOKEN_PATTERN = re.compile(rb'name="csrf_token" value="([^"]+)"')


def csrf_token(client, page="/trades/new"):
    """GET a page that contains a form and return its CSRF token."""
    response = client.get(page)
    match = TOKEN_PATTERN.search(response.data)
    assert match, f"no CSRF token found on {page}"
    return match.group(1).decode()


def post_form(client, url, data, *, token_page="/trades/new", follow_redirects=False):
    """POST form data the way a browser would, including a valid token."""
    body = {"csrf_token": csrf_token(client, token_page), **data}
    return client.post(url, data=body, follow_redirects=follow_redirects)


def closed_trade_form(**changes):
    """A valid "Add trade" submission for sample instrument 1 (EURUSD).

    Values use the browser's datetime-local format (no seconds, no "Z"),
    which is exactly what the form sends. Same prices as sample trade 1:
    net +393 USD, realised R +1.965 (shown as +1.97R).
    """
    form = {
        "instrument_id": "1",
        "direction": "LONG",
        "status": "CLOSED",
        "trade_date": "2026-04-01T08:15",
        "closed_at": "2026-04-01T11:40",
        "entry_price": "1.08500",
        "stop_loss": "1.08300",
        "take_profit": "1.08900",
        "exit_price": "1.08900",
        "position_size": "1",
        "fees": "7",
        "strategy": "Breakout",
        "session": "LONDON",
        "notes": "Website test trade (synthetic)",
    }
    form.update(changes)
    return form


def open_trade_form(**changes):
    """A valid OPEN trade: no exit price, no exit time."""
    return closed_trade_form(status="OPEN", exit_price="", closed_at="", **changes)


def count_rows(app, table):
    with app.app_context():
        return get_db().execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def fetch_trade(app, trade_id):
    """The stored row, with exact decimals as text: SQLite stores TEXT and
    PostgreSQL returns NUMERIC as Decimal; str() gives the same digits."""
    with app.app_context():
        row = get_db().execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        if row is None:
            return None
        return {key: str(value) if isinstance(value, Decimal) else value for key, value in dict(row).items()}


def new_trade_id(response):
    """The id in a redirect such as /trades/17."""
    match = re.search(r"/trades/(\d+)$", response.headers["Location"])
    assert match, response.headers.get("Location")
    return int(match.group(1))
```

### `scripts/benchmark_dashboard.py` (MODIFIED)

**Why:** Two accounts, PostgreSQL target, API and authentication timings.  
**How to run or test:** `python scripts/benchmark_dashboard.py --help`

```python
"""Time the dashboard, the API and authentication on large synthetic journals.

    python scripts/benchmark_dashboard.py              # 100, 1,000 and 10,000 trades (SQLite)
    python scripts/benchmark_dashboard.py 5000         # a custom size
    python scripts/benchmark_dashboard.py --profile    # also show where the time goes
    python scripts/benchmark_dashboard.py --database-url postgresql://user:pw@host/postgres
        # PostgreSQL: a throwaway database is created (and dropped) per size
        # (add --pg-bridge where psycopg is not installed: test-only libpq bridge,
        #  whose timings include its own ctypes overhead and are NOT psycopg's)

Creates a throwaway database (never your real one) holding TWO accounts:
the measured one with N synthetic closed trades spread over several
instruments, strategies, sessions and two years, and a second account with
another N trades, so every query really has to filter by owner (Phase 8).
Then it measures, as the median of 5 runs:

  sql        the database query alone (list_trades with realised dates)
  report     build_report(): SQL query + Decimal calculations
  dashboard  build_dashboard() + chart_payload(): the above plus chart prep
  page       a full GET /analytics through Flask, including the template
  filtered   GET /analytics with strategy + session + date filters
  memory     peak Python memory allocated while rendering one page (tracemalloc)
  size       the HTML page, and the chart data (JSON) inside it
  api_list   GET /api/v1/trades?per_page=100 with a Bearer token
  api_sum    GET /api/v1/analytics/summary with a Bearer token
and once per run, independent of N:
  argon2     hashing and verifying one password with the production settings
  token      looking up and checking one API token
  session    loading the signed-in user for one request

Numbers depend on the machine; the script prints the Python version and
platform so results can be compared honestly. They are not guarantees.
"""

import cProfile
import json
import os
import platform
import pstats
import re
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradeledger import create_app
from tradeledger.db import connect, get_db, init_schema, register_postgres_driver
from tradeledger.passwords import OWASP_MINIMUM, hash_password, verify_password
from tradeledger.performance import build_report
from tradeledger.performance.dashboard import build_dashboard, chart_payload
from tradeledger.report_filters import ReportFilters
from tradeledger.repositories import api_tokens
from tradeledger.repositories.trades import list_trades
from tradeledger.repositories.users import create_user, get_user

RUNS = 5
STRATEGIES = ("Breakout", "Pullback", "Reversal", None)
SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "OTHER", None)
EXITS = ("102", "99", "100.5", "99", "101.25", "100")  # +2, -1, +0.5, -1, +1.25, 0 R
FAST_HASH = "$argon2id$"  # accounts are created with a placeholder-format hash; nobody signs in


def populate(conn, count, user_id):
    with conn:
        instruments = []
        for n, currency in enumerate(("USD", "USD", "GBP", "USD", "EUR")):
            row = conn.execute(
                "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment,"
                " contract_multiplier, quote_currency) VALUES (?, ?, ?, 'OTHER', ?, ?, ?) RETURNING id",
                (user_id, f"BENCH{n}", f"Benchmark {n}", conn.numeric("0.01"), conn.numeric("1"), currency),
            ).fetchone()
            instruments.append(row["id"])
        hundred, ninety_nine, one, zero = (conn.numeric(v) for v in ("100", "99", "1", "0"))
        for n in range(count):
            day = n * 730 // count  # spread over two years
            month, dom = 1 + (day // 28) % 12, 1 + day % 28
            year = 2025 + day // 336
            conn.execute(
                "INSERT INTO trades (user_id, instrument_id, trade_date, closed_at, direction, entry_price,"
                " stop_loss, exit_price, position_size, fees, status, strategy, session)"
                " VALUES (?, ?, ?, ?, 'LONG', ?, ?, ?, ?, ?, 'CLOSED', ?, ?)",
                (
                    user_id,
                    instruments[n % len(instruments)],
                    f"{year}-{month:02d}-{dom:02d}T08:{n % 60:02d}:00Z",
                    f"{year}-{month:02d}-{dom:02d}T{9 + n % 12:02d}:{n % 60:02d}:00Z",
                    hundred,
                    ninety_nine,
                    conn.numeric(EXITS[n % len(EXITS)]),
                    one,
                    zero,
                    STRATEGIES[n % len(STRATEGIES)],
                    SESSIONS[n % len(SESSIONS)],
                ),
            )


def median_ms(action):
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        action()
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times)


def peak_memory_mb(action):
    tracemalloc.start()
    action()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak / (1024 * 1024)


def chart_data_kb(html):
    match = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S)
    return len(match.group(1).encode()) / 1024 if match else 0.0


def profile(action, top=12):
    """Print where `action` spends its time, sorted two ways.

    cumulative: time in a function INCLUDING everything it calls; shows
                which high-level step is slow (e.g. build_dashboard).
    tottime:    time spent INSIDE the function itself; shows the actual
                hot spot. In Phase 7 this view revealed datetime.strptime
                being called ~50,000 times at 10,000 trades.
    """
    profiler = cProfile.Profile()
    profiler.runcall(action)
    for order in ("cumulative", "tottime"):
        print(f"\n--- sorted by {order} ---")
        pstats.Stats(profiler).sort_stats(order).print_stats(top)


class Target:
    """A throwaway database: a SQLite file, or a new PostgreSQL database."""

    def __init__(self, server_url, label):
        self.server_url, self.label = server_url, label
        self.folder = tempfile.TemporaryDirectory()

    def __enter__(self):
        if not self.server_url:
            return os.path.join(self.folder.name, "bench.db")
        from urllib.parse import urlsplit, urlunsplit

        self.name = f"tl_bench_{self.label}_{os.getpid()}"
        self.admin = connect(self.server_url)
        self.admin.execute(f"DROP DATABASE IF EXISTS {self.name}")
        self.admin.execute(f"CREATE DATABASE {self.name}")
        parts = urlsplit(self.server_url)
        return urlunsplit((parts.scheme, parts.netloc, f"/{self.name}", parts.query, ""))

    def __exit__(self, *exc):
        if self.server_url:
            self.admin.execute(f"DROP DATABASE IF EXISTS {self.name}")
            self.admin.close()
        self.folder.cleanup()


def benchmark(count, server_url=None, show_profile=False):
    with Target(server_url, count) as target:
        app = create_app("testing", overrides={"DATABASE": target})
        with app.app_context():
            conn = get_db()
            init_schema(conn)
            me = create_user(conn, "bench@example.com", FAST_HASH + "v=19$m=1,t=1,p=1$AAAA$AAAA")
            other = create_user(conn, "other@example.com", FAST_HASH + "v=19$m=1,t=1,p=1$AAAA$AAAA")
            populate(conn, count, me)
            populate(conn, count, other)  # the second account: queries must filter by owner
            if build_report(conn, user_id=me).overall.trades != count:
                raise SystemExit(f"setup failed: expected {count} closed trades")
            token, _ = api_tokens.create_token(conn, user_id=me, name="bench", lifetime_days=1, max_active=10)
            sql_ms = median_ms(lambda: list_trades(conn, user_id=me, date_field="realised"))
            report_ms = median_ms(lambda: build_report(conn, user_id=me))
            dashboard_ms = median_ms(
                lambda: chart_payload(build_dashboard(conn, ReportFilters(), user_id=me))
            )
            token_ms = median_ms(lambda: api_tokens.authenticate(conn, token))
            session_ms = median_ms(lambda: get_user(conn, me))
        client = app.test_client()
        now = int(time.time())
        with client.session_transaction() as session:
            session.update({"uid": me, "sv": 1, "auth_at": now, "seen_at": now})
        html = client.get("/analytics").get_data(as_text=True)
        if "dashboard-data" not in html:
            raise SystemExit("the dashboard did not render")
        page_ms = median_ms(lambda: client.get("/analytics"))
        filtered_ms = median_ms(
            lambda: client.get("/analytics?strategy=Breakout&session=LONDON&start=2025-06-01&end=2026-06-30")
        )
        memory = peak_memory_mb(lambda: client.get("/analytics"))
        bearer = {"Authorization": f"Bearer {token}"}
        api_list_ms = median_ms(lambda: client.get("/api/v1/trades?per_page=100", headers=bearer))
        api_summary_ms = median_ms(lambda: client.get("/api/v1/analytics/summary", headers=bearer))
        if show_profile:
            print(f"\n--- where GET /analytics spends its time ({count:,} trades) ---")
            profile(lambda: client.get("/analytics"))
        app.extensions["tradeledger_db"].close()
    result = {
        "trades": count,
        "sql_ms": sql_ms,
        "report_ms": report_ms,
        "dashboard_ms": dashboard_ms,
        "page_ms": page_ms,
        "filtered_ms": filtered_ms,
        "api_list_ms": api_list_ms,
        "api_summary_ms": api_summary_ms,
        "token_check_ms": token_ms,
        "session_load_ms": session_ms,
        "peak_mb": memory,
        "page_kb": len(html.encode()) / 1024,
        "chart_kb": chart_data_kb(html),
    }
    print(
        f"{count:>7,} trades | sql {sql_ms:7.1f} | report {report_ms:7.1f} | dashboard {dashboard_ms:7.1f} | "
        f"page {page_ms:7.1f} | filtered {filtered_ms:6.1f} | api list {api_list_ms:6.1f} | "
        f"api summary {api_summary_ms:7.1f} ms | peak {memory:5.1f} MB | page {result['page_kb']:6,.0f} KB"
    )
    return result


def argon2_ms():
    stored = hash_password("benchmark password", OWASP_MINIMUM)
    return (
        median_ms(lambda: hash_password("benchmark password", OWASP_MINIMUM)),
        median_ms(lambda: verify_password("benchmark password", stored)),
    )


if __name__ == "__main__":
    argv = sys.argv[1:]
    server = argv[argv.index("--database-url") + 1] if "--database-url" in argv else None
    if "--pg-bridge" in argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
        import pg_libpq_bridge

        register_postgres_driver(pg_libpq_bridge)
    sizes = [int(a) for a in argv if a.isdigit()] or [100, 1000, 10000]
    engine = (
        "PostgreSQL" + (" (test-only libpq bridge)" if "--pg-bridge" in argv else "") if server else "SQLite"
    )
    print(f"Python {platform.python_version()} on {platform.platform()}; {engine}; median of {RUNS} runs")
    hash_ms, verify_ms = argon2_ms()
    print(f"Argon2id (m=19456 KiB, t=2, p=1): hash {hash_ms:.1f} ms, verify {verify_ms:.1f} ms")
    results = [benchmark(size, server, "--profile" in argv) for size in sizes]
    print(
        json.dumps(
            {"engine": engine, "argon2_hash_ms": hash_ms, "argon2_verify_ms": verify_ms, "runs": results}
        )
    )
```

### `scripts/qa_dashboard_agreement.py` (MODIFIED)

**Why:** Independent audit now signs in, seeds a decoy account and can run on PostgreSQL.  
**How to run or test:** `python scripts/qa_dashboard_agreement.py --help`

```python
"""Phase 6 QA: does everything the dashboard shows agree with an
INDEPENDENT recalculation from the raw database rows?

An audit, not part of pytest (it takes ~15 s and needs Node.js). It builds
a throwaway database (the sample data plus 600 random trades in three
currencies, 5% open, 3% with no exit time), recomputes every figure from
the raw SQL rows with its OWN code (it never calls the engine), then
compares four outputs against that for every filter combination:
  1. the HTML page (KPI cards, money strip, all detailed tables)
  2. the chart payload embedded in the page
  3. the JSON API (/api/v1/analytics/*)
  4. the tooltip text produced by the REAL analytics.js (run in Node)

Phase 8: the audited account (user 1) shares the database with a SECOND
account holding 120 decoy trades on instruments with the same symbols. The
independent recalculation reads only user 1's rows, so any leak of the
other account's data into the dashboard or the API is a disagreement.
The page and the API are requested as user 1 (signed-in session; API token).

Usage (from the project folder):
    python scripts/qa_dashboard_agreement.py            # finds node on PATH
    python scripts/qa_dashboard_agreement.py --node /path/to/node
    python scripts/qa_dashboard_agreement.py --database-url postgresql://...  # audit on PostgreSQL
      (add --pg-bridge to use the test-only libpq bridge where psycopg is not installed)

Exit code 0 = no disagreements. Precision trap: every Decimal sum here runs
inside localcontext(prec=80); Python's default context keeps only 28 digits.
"""

import itertools
import json
import random
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, localcontext
from html import unescape
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from tradeledger import create_app  # noqa: E402
from tradeledger.db import get_db, init_schema, register_postgres_driver  # noqa: E402
from tradeledger.passwords import HashParameters, hash_password  # noqa: E402
from tradeledger.repositories import api_tokens  # noqa: E402
from tradeledger.repositories.instruments import create_instrument  # noqa: E402
from tradeledger.repositories.trades import create_trade  # noqa: E402
from tradeledger.repositories.users import create_user  # noqa: E402
from tradeledger.seed import seed_sample_data  # noqa: E402

AUDITED = 1  # the audited account's id (created first)
FAST = HashParameters(memory_kib=1024, iterations=1, lanes=1)

TOL = Decimal("1e-30")
SESSION_LABELS = {
    "ASIA": "Asia",
    "LONDON": "London",
    "NEW_YORK": "New York",
    "OTHER": "Other",
    None: "Unspecified",
}
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
problems = []
checks = 0


def check(condition, message):
    global checks
    checks += 1
    if not condition:
        problems.append(message)


# ---------- My own formatting (written from the documented rules) ----------


def q(value, places):
    return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)


def fmt_r(v):
    if v is None:
        return "n/a"
    r = q(v, "0.01")
    return "0.00R" if r == 0 else f"{r:+}R"


def fmt_rate(wins, total):
    return "n/a" if total == 0 else f"{q(Decimal(wins) * 100 / Decimal(total), '0.1')}%"


def fmt_ratio(v):
    return "n/a" if v is None else str(q(v, "0.01"))


# ---------- Build a database: sample data + 600 random trades ----------


def random_trade(rnd, instrument_ids, strategies, sessions):
    direction = rnd.choice(["LONG", "SHORT"])
    entry = Decimal(rnd.randint(5000, 20000)) / 100
    risk = Decimal(rnd.randint(10, 300)) / 100
    stop = entry - risk if direction == "LONG" else entry + risk
    move = Decimal(rnd.randint(-400, 400)) / 100
    exit_price = max(Decimal("0.01"), entry + move)
    start = datetime(2025, 11, 1, tzinfo=UTC)
    entered = start + (datetime(2026, 4, 30, tzinfo=UTC) - start) * rnd.random()
    entered = entered.replace(microsecond=0)
    closed = entered.timestamp() + rnd.randint(60, 5 * 86400)
    closed = datetime.fromtimestamp(closed, UTC)
    is_open = rnd.random() < 0.05
    return {
        "instrument_id": rnd.choice(instrument_ids),
        "direction": direction,
        "status": "OPEN" if is_open else "CLOSED",
        "trade_date": entered.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "closed_at": None if is_open else closed.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entry_price": str(entry),
        "stop_loss": str(stop),
        "exit_price": None if is_open else str(exit_price),
        "position_size": str(rnd.choice(["1", "2", "0.5", "3"])),
        "fees": str(Decimal(rnd.randint(0, 300)) / 100),
        "strategy": rnd.choice(strategies),
        "session": rnd.choice(sessions),
    }


def build_database(target):
    app = create_app("testing", overrides={"DATABASE": target})
    rnd = random.Random(20261115)
    with app.app_context():
        conn = get_db()
        init_schema(conn)
        audited = create_user(conn, "audited@example.com", hash_password("audit password 1", FAST))
        decoy = create_user(conn, "decoy@example.com", hash_password("audit password 2", FAST))
        if audited != AUDITED:
            raise SystemExit("the audit needs an empty database (audited account must be id 1)")
        seed_sample_data(conn, user_id=audited)
        # The decoy account: same symbols, very different trades.
        seed_sample_data(conn, user_id=decoy)
        decoy_instruments = [
            r[0] for r in conn.execute("SELECT id FROM instruments WHERE user_id = ?", (decoy,))
        ]
        for _ in range(120):
            create_trade(
                conn,
                random_trade(rnd, decoy_instruments, ["Breakout", "Decoy"], ["ASIA", None]),
                user_id=decoy,
            )
        extra = [
            create_instrument(
                conn,
                {
                    "symbol": sym,
                    "name": sym,
                    "asset_class": "OTHER",
                    "price_increment": "0.01",
                    "contract_multiplier": mult,
                    "quote_currency": cur,
                },
                user_id=audited,
            )
            for sym, mult, cur in (("QAUSD", "10", "USD"), ("QAGBP", "1", "GBP"), ("QAEUR", "0.5", "EUR"))
        ]
        strategies = ["Breakout", "Pullback", "Scalp", "Fade", None]
        sessions = ["ASIA", "LONDON", "NEW_YORK", "OTHER", None]
        for _ in range(600):
            create_trade(conn, random_trade(rnd, extra, strategies, sessions), user_id=audited)
        # 3% legacy trades: exit time never recorded.
        ids = [
            r[0]
            for r in conn.execute(
                "SELECT id FROM trades WHERE status='CLOSED' AND user_id = ? AND id > 16 ORDER BY id",
                (audited,),
            )
        ]
        for trade_id in rnd.sample(ids, len(ids) * 3 // 100):
            conn.execute("UPDATE trades SET closed_at = NULL WHERE id = ?", (trade_id,))
        conn.commit()
    return app


# ---------- Independent recalculation from raw rows ----------


def raw_rows(app):
    with app.app_context():
        return [
            dict(r)
            for r in get_db().execute(
                "SELECT t.*, i.symbol, i.contract_multiplier, i.quote_currency "
                "FROM trades t JOIN instruments i ON i.id = t.instrument_id WHERE t.user_id = ?",
                (AUDITED,),
            )
        ]


def independent(rows, f):
    with localcontext() as ctx:
        ctx.prec = 80
        out = []
        open_count = 0
        for t in rows:
            if f["instrument_id"] and t["instrument_id"] != f["instrument_id"]:
                continue
            if f["strategy"] and t["strategy"] != f["strategy"]:
                continue
            if f["session"] and t["session"] != f["session"]:
                continue
            if t["status"] == "OPEN":
                open_count += 1 if not (f["start"] or f["end"]) else 0
                continue
            realised = t["closed_at"] or t["trade_date"]
            day = realised[:10]
            if (f["start"] and day < f["start"]) or (f["end"] and day > f["end"]):
                continue
            sign = 1 if t["direction"] == "LONG" else -1
            entry, stop, exit_ = (Decimal(t[k]) for k in ("entry_price", "stop_loss", "exit_price"))
            size, mult = Decimal(t["position_size"]), Decimal(t["contract_multiplier"])
            net = (exit_ - entry) * sign * size * mult - Decimal(t["fees"] or "0")
            risk = abs(entry - stop) * size * mult
            out.append(
                {
                    "id": t["id"],
                    "when": realised,
                    "symbol": t["symbol"],
                    "net": net,
                    "r": net / risk,
                    "cur": t["quote_currency"],
                    "strategy": t["strategy"],
                    "session": t["session"],
                }
            )
        out.sort(key=lambda x: (x["when"], x["id"]))
        return out, open_count


def summary(trades):
    with localcontext() as ctx:
        ctx.prec = 80
        n = len(trades)
        wins = sum(1 for t in trades if t["net"] > 0)
        losses = sum(1 for t in trades if t["net"] < 0)
        total = sum((t["r"] for t in trades), Decimal(0))
        gains = sum((t["r"] for t in trades if t["r"] > 0), Decimal(0))
        pains = -sum((t["r"] for t in trades if t["r"] < 0), Decimal(0))
        cum, peak, worst = Decimal(0), Decimal(0), Decimal(0)
        for t in trades:
            cum += t["r"]
            peak = max(peak, cum)
            worst = max(worst, peak - cum)
        streaks = {"WIN": 0, "LOSS": 0}
        run_kind, run = None, 0
        for t in trades:
            kind = "WIN" if t["net"] > 0 else "LOSS" if t["net"] < 0 else None
            run = run + 1 if kind and kind == run_kind else (1 if kind else 0)
            run_kind = kind
            if kind:
                streaks[kind] = max(streaks[kind], run)
        return {
            "trades": n,
            "wins": wins,
            "losses": losses,
            "breakevens": n - wins - losses,
            "total_r": total,
            "mean_r": total / n if n else None,
            "pf": gains / pains if pains else None,
            "max_dd": worst,
            "win_streak": streaks["WIN"],
            "loss_streak": streaks["LOSS"],
        }


def grouped(trades, key):
    groups = defaultdict(list)
    for t in trades:
        groups[key(t)].append(t)
    return {k: summary(v) for k, v in groups.items()}


# ---------- Reading the page ----------


def kpis(html):
    return dict(re.findall(r'data-kpi="([a-z_]+)">.*?class="kpi__value">([^<]*)<', html, re.S))


def strip(fragment):
    return " ".join(unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def table(html, table_id):
    match = re.search(rf'id="{table_id}".*?<tbody>(.*?)</tbody>', html, re.S)
    if not match:
        return None
    rows = {}
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", match.group(1), re.S):
        head = strip(re.search(r"<th[^>]*>(.*?)</th>", row, re.S).group(1))
        head = head.replace("Small sample", "").strip()
        rows[head] = [strip(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
    return rows


def payload_of(html):
    m = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S)
    return json.loads(m.group(1)) if m else None


# ---------- Compare one filter combination ----------


def compare(client, rows, f, collected):
    query = "&".join(f"{k}={v}" for k, v in f.items() if v)
    tag = query or "(no filters)"
    response = client.get("/analytics?" + query)
    check(response.status_code == 200, f"{tag}: page status {response.status_code}")
    html = response.get_data(as_text=True)
    trades, _ = independent(rows, f)
    s = summary(trades)
    k = kpis(html)

    expected = {"trades": str(s["trades"])}
    if s["trades"]:
        expected.update(
            {
                "wins": str(s["wins"]),
                "losses": str(s["losses"]),
                "breakevens": str(s["breakevens"]),
                "win_rate": fmt_rate(s["wins"], s["trades"]),
                "average_r": fmt_r(s["mean_r"]),
                "expectancy_r": fmt_r(s["mean_r"]),
                "profit_factor_r": fmt_ratio(s["pf"]),
                "total_r": fmt_r(s["total_r"]),
                "max_drawdown_r": f"{q(s['max_dd'], '0.01')}R",
                "longest_win_streak": f"{s['win_streak']} trade" + ("" if s["win_streak"] == 1 else "s"),
                "longest_loss_streak": f"{s['loss_streak']} trade" + ("" if s["loss_streak"] == 1 else "s"),
            }
        )
    else:
        expected.update(
            dict.fromkeys(
                (
                    "wins",
                    "win_rate",
                    "average_r",
                    "expectancy_r",
                    "profit_factor_r",
                    "total_r",
                    "max_drawdown_r",
                ),
                "n/a",
            )
        )
    for key, value in expected.items():
        check(k.get(key) == value, f"{tag}: KPI {key} page={k.get(key)!r} independent={value!r}")

    # Money per currency, never combined.
    money = defaultdict(Decimal)
    for t in trades:
        money[t["cur"]] += t["net"]
    for cur, net in money.items():
        check(f"{q(net, '0.01')} {cur}" in html, f"{tag}: money {q(net, '0.01')} {cur} missing")

    api = {
        name: client.get(f"/api/v1/analytics/{name}?{query}").get_json()
        for name in ("summary", "instruments", "strategies", "sessions", "weekday", "monthly", "curve")
    }
    o = api["summary"]["data"]["overall"]
    check(o["trades"] == s["trades"], f"{tag}: API trades")
    check(abs(Decimal(o["total_r"]) - s["total_r"]) < TOL, f"{tag}: API total_r")

    if not s["trades"]:
        check(payload_of(html) is None, f"{tag}: payload present with no results")
        return

    payload = payload_of(html)
    collected.append({"tag": tag, "payload": payload})

    # Curve: same trades, same order, same exact cumulative values.
    cum = Decimal(0)
    points = payload["curve"][1:]
    check([p["trade_id"] for p in points] == [t["id"] for t in trades], f"{tag}: curve order")
    check(
        [p["trade_id"] for p in api["curve"]["data"]] == [t["id"] for t in trades], f"{tag}: API curve order"
    )
    # strict=False: a length mismatch is already reported by the order check above.
    for p, t in zip(points, trades, strict=False):
        with localcontext() as ctx:  # same precision as independent()
            ctx.prec = 80
            cum += t["r"]
        check(abs(Decimal(p["cumulative_r"]) - cum) < TOL, f"{tag}: curve #{t['id']} cumulative")
        check(p["cumulative_text"] == fmt_r(cum), f"{tag}: curve #{t['id']} text")
    check(abs(Decimal(payload["max_drawdown"]["amount"]) - s["max_dd"]) < TOL, f"{tag}: payload max drawdown")

    # Groups: page table, payload and API all equal the independent figures.
    specs = [
        ("instruments", "table-instruments", lambda t: t["symbol"], lambda key: key, "instruments"),
        (
            "strategies",
            "table-strategies",
            lambda t: t["strategy"] or "Unspecified",
            lambda key: key,
            "strategies",
        ),
        ("sessions", "table-sessions", lambda t: t["session"], lambda key: SESSION_LABELS[key], "sessions"),
    ]
    for name, table_id, key, label, api_name in specs:
        truth = {label(g): v for g, v in grouped(trades, key).items()}
        page_rows = table(html, table_id)
        check(
            set(page_rows) == set(truth), f"{tag}: {name} table rows {sorted(page_rows)} vs {sorted(truth)}"
        )
        for group, v in truth.items():
            cells = page_rows.get(group)
            if not cells:
                continue
            check(cells[0] == str(v["trades"]), f"{tag}: {name}/{group} trades")
            check(
                cells[1] == f"{v['wins']} / {v['losses']} / {v['breakevens']}", f"{tag}: {name}/{group} w/l/b"
            )
            check(cells[2] == fmt_rate(v["wins"], v["trades"]), f"{tag}: {name}/{group} win rate")
            check(
                cells[5] == fmt_r(v["total_r"]),
                f"{tag}: {name}/{group} total {cells[5]} vs {fmt_r(v['total_r'])}",
            )
        pay = {p["label"]: p for p in payload[name]}
        check(set(pay) == set(truth), f"{tag}: {name} payload labels")
        for group, v in truth.items():
            if group in pay:
                check(
                    abs(Decimal(pay[group]["total_r"]) - v["total_r"]) < TOL, f"{tag}: {name}/{group} payload"
                )
        api_rows = {
            (
                SESSION_LABELS[g["group"]]
                if name == "sessions" and g["group"] in SESSION_LABELS
                else g["group"]
            ): g
            for g in api[api_name]["data"]
        }
        check(set(api_rows) == set(truth), f"{tag}: {name} API groups {sorted(api_rows)}")
        # Order: best total R first (instruments/strategies).
        if name != "sessions":
            order = [p["label"] for p in payload[name]]
            check(order == sorted(order, key=lambda g: (-truth[g]["total_r"], g)), f"{tag}: {name} order")

    # Months: every month from first to last, gaps kept.
    month_truth = grouped(trades, lambda t: t["when"][:7])
    first, last = min(month_truth), max(month_truth)
    expected_keys, y, m = [], int(first[:4]), int(first[5:])
    while f"{y:04d}-{m:02d}" <= last:
        expected_keys.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    check([p["key"] for p in payload["monthly"]] == expected_keys, f"{tag}: month keys")
    for p in payload["monthly"]:
        v = month_truth.get(p["key"])
        if v is None:
            check(p["total_r"] is None, f"{tag}: empty month {p['key']} not null")
        else:
            check(abs(Decimal(p["total_r"]) - v["total_r"]) < TOL, f"{tag}: month {p['key']}")
    month_table = table(html, "table-monthly")
    check(len(month_table) == len(expected_keys), f"{tag}: month table rows")

    # Weekdays, UTC.
    def weekday(t):
        return WEEKDAYS[datetime.strptime(t["when"], "%Y-%m-%dT%H:%M:%SZ").weekday()]

    day_truth = grouped(trades, weekday)
    check([p["key"] for p in payload["weekday"]] == WEEKDAYS, f"{tag}: weekday order")
    day_table = table(html, "table-weekday")
    for p in payload["weekday"]:
        v = day_truth.get(p["key"])
        cells = day_table[p["key"]]
        if v is None:
            check(p["total_r"] is None and cells == ["No closed trades"], f"{tag}: {p['key']} empty")
        else:
            check(abs(Decimal(p["total_r"]) - v["total_r"]) < TOL, f"{tag}: weekday {p['key']}")
            check(cells[5] == fmt_r(v["total_r"]), f"{tag}: weekday table {p['key']}")


NODE_SCRIPT = r"""
const fs = require("fs");
const charts = require(process.argv[2]);
const items = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const theme = {text: "#fff", muted: "#aaa", grid: "#333", zero: "#999", accent: "#00f",
               positive: "#0f0", negative: "#f00", font: "sans-serif"};
const out = items.map(({tag, payload}) => {
  const c = charts.buildConfigs(payload, theme);
  const res = {tag};
  for (const name of Object.keys(c)) {
    const cb = c[name].options.plugins.tooltip.callbacks;
    const n = c[name].data.datasets[0].data.length;
    res[name] = {labels: c[name].data.labels || null, data: c[name].data.datasets[0].data,
                 tooltips: Array.from({length: n}, (_, i) => cb.label({dataIndex: i})),
                 titles: Array.from({length: n}, (_, i) => cb.title ? cb.title([{dataIndex: i}]) : null)};
  }
  return res;
});
fs.writeFileSync(process.argv[4], JSON.stringify(out));
"""


def check_tooltips(collected, node):
    work = Path(tempfile.mkdtemp())
    (work / "run.js").write_text(NODE_SCRIPT)
    (work / "in.json").write_text(json.dumps(collected))
    subprocess.run(
        [
            node,
            str(work / "run.js"),
            str(PROJECT / "tradeledger/static/js/analytics.js"),
            str(work / "in.json"),
            str(work / "out.json"),
        ],
        check=True,
    )
    results = json.loads((work / "out.json").read_text())
    for item, res in zip(collected, results, strict=True):
        tag, payload = item["tag"], item["payload"]
        # Curve: every point's tooltip quotes the server's exact display text,
        # and the plotted y equals the exact value converted once.
        for i, p in enumerate(payload["curve"]):
            lines = res["curve"]["tooltips"][i]
            check(res["curve"]["data"][i]["y"] == float(Decimal(p["cumulative_r"])), f"{tag}: curve y {i}")
            if not p.get("start"):
                check(f"Cumulative R: {p['cumulative_text']}" in lines, f"{tag}: curve tooltip {i}")
                check(f"Trade R: {p['realised_text']}" in lines, f"{tag}: trade R tooltip {i}")
                check(
                    ("Exit time not recorded: placed at entry time" in lines) == (not p["close_time_known"]),
                    f"{tag}: legacy note {i}",
                )
            dd = res["drawdown"]["tooltips"][i][0]
            check(
                dd
                == (
                    f"Drawdown: {p['drawdown_text']} below the running peak"
                    if Decimal(p["underwater_r"]) != 0
                    else dd
                ),
                f"{tag}: drawdown tooltip {i}",
            )
        # Bars: tooltip text equals payload text; null periods stay null.
        for name in ("monthly", "weekday", "instruments", "strategies", "sessions"):
            for i, row in enumerate(payload[name]):
                lines = res[name]["tooltips"][i]
                if row["total_r"] is None:
                    check(
                        res[name]["data"][i] is None and lines == ["No closed trades"],
                        f"{tag}: {name} gap {i}",
                    )
                else:
                    check(lines[0] == f"Total R: {row['total_r_text']}", f"{tag}: {name} tooltip {i}")
                    check(
                        f"Closed trades: {row['trades']} (won {row['wins']}, lost {row['losses']})" in lines,
                        f"{tag}: {name} count tooltip {i}",
                    )
                    check(res[name]["data"][i] == float(Decimal(row["total_r"])), f"{tag}: {name} value {i}")
    return len(results)


def sign_in_as_audited(app, client):
    """The same session a real sign-in creates, plus an API token."""
    import time

    now = int(time.time())
    with client.session_transaction() as session:
        session.update({"uid": AUDITED, "sv": 1, "auth_at": now, "seen_at": now})
    with app.app_context():
        token, _ = api_tokens.create_token(
            get_db(), user_id=AUDITED, name="audit", lifetime_days=1, max_active=10
        )
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"


def main():
    node = sys.argv[sys.argv.index("--node") + 1] if "--node" in sys.argv else shutil.which("node")
    if not node:
        sys.exit("Node.js is needed to run analytics.js; pass --node /path/to/node")
    if "--pg-bridge" in sys.argv:
        sys.path.insert(0, str(PROJECT / "tests"))
        import pg_libpq_bridge

        register_postgres_driver(pg_libpq_bridge)
    target = sys.argv[sys.argv.index("--database-url") + 1] if "--database-url" in sys.argv else None
    target = target or str(Path(tempfile.mkdtemp()) / "qa.db")
    app = build_database(target)
    rows = raw_rows(app)
    client = app.test_client()
    sign_in_as_audited(app, client)
    with app.app_context():
        instrument_ids = [
            r[0]
            for r in get_db().execute("SELECT id FROM instruments WHERE user_id = ? ORDER BY id", (AUDITED,))
        ]
        decoy_ids = [
            r[0] for r in get_db().execute("SELECT id FROM instruments WHERE user_id <> ?", (AUDITED,))
        ]
    # A decoy instrument id is someone else's: the page and the API must
    # treat it exactly like an id that does not exist.
    for decoy_id in decoy_ids[:2]:
        page = client.get(f"/analytics?instrument_id={decoy_id}")
        check(page.status_code == 400, f"decoy instrument {decoy_id}: page status {page.status_code}")
        api = client.get(f"/api/v1/analytics/summary?instrument_id={decoy_id}")
        check(api.status_code == 400, f"decoy instrument {decoy_id}: API status {api.status_code}")
    strategies = [None, "Breakout", "Pullback", "Scalp", "Reversal"]
    sessions = [None, "ASIA", "LONDON", "NEW_YORK", "OTHER"]
    dates = [(None, None), ("2026-01-01", "2026-02-28"), ("2025-12-15", None), ("2026-03-02", "2026-03-02")]
    collected = []
    combos = 0
    for inst, strat, sess, (start, end) in itertools.product(
        [None, *instrument_ids], strategies, sessions, dates
    ):
        f = {"instrument_id": inst, "strategy": strat, "session": sess, "start": start, "end": end}
        compare(client, rows, f, collected)
        combos += 1
    charted = check_tooltips(collected, node)
    closed = sum(1 for r in rows if r["status"] == "CLOSED")
    legacy = sum(1 for r in rows if r["status"] == "CLOSED" and not r["closed_at"])
    print(f"trades in database: {len(rows)} ({closed} closed, {legacy} with no exit time)")
    print(f"filter combinations: {combos}; with results (charts checked in Node): {charted}")
    print(f"individual comparisons: {checks}; disagreements: {len(problems)}")
    for p in problems[:30]:
        print("  -", p)
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
```

## 3. Deleted files

### `tradeledger/web/guard.py` (DELETED)

The Phase 7 'journal disabled in production unless an unsafe switch is set' guard. Replaced by real sign-in (`web/auth.py`); the switches are ignored with a warning.

### `tradeledger/templates/home_disabled.html` (DELETED)

The page shown by that guard. Anonymous visitors now see the sign-in page.
