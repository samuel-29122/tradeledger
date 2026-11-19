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
