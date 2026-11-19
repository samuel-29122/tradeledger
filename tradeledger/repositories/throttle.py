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
