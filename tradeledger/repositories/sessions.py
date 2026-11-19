"""Signed-in browsers ("sessions"), one database row each (Phase 8.5).

Plain English
-------------
Every time you sign in, TradeLedger writes down "this browser is signed in
to this account" and gives the browser a long random key to prove it. The
Account page lists your signed-in browsers and can sign out any one of
them. Signing out, or changing your password, crosses the row out on the
server, so even a stolen copy of the cookie stops working.

Technical summary
-----------------
Key      secrets.token_urlsafe(32): 256 random bits, kept inside Flask's
         signed session cookie. The table stores only SHA-256(key), like the
         API tokens: a database leak does not reveal usable session keys.
Limits   expires_at = sign-in time + SESSION_MAX_AGE_SECONDS (absolute) and
         last_seen_at for the idle limit, both checked on the SERVER.
         last_seen_at is written at most once a minute per session.
Clock    Whole seconds since 1970 (UTC) from web.auth.now_seconds(), the
         same clock the cookie checks use, so tests can move it.
Privacy  No IP address is stored. user_agent holds a short description such
         as "Firefox on Windows", never the full header.
Revoke   revoked_at is set; rows are never reused. Ending ALL of a user's
         sessions happens in repositories/users.py (password change,
         "sign out everywhere", deactivation), together with session_version. Expired and revoked rows
         older than a day are deleted when the same user signs in again.

Why not only the signed cookie (Phase 8): a signed cookie cannot be
"un-signed". Phase 8 could end ALL of a user's sessions (session_version)
but not one device, and a cookie copied before sign-out stayed valid until
it expired. A server-side row fixes both, at the cost of one indexed
lookup per request (well under a millisecond).
"""

import hashlib
import secrets

KEY_BYTES = 32
TOUCH_INTERVAL_SECONDS = 60
CLEANUP_AFTER_SECONDS = 24 * 3600

_COLUMNS = "id, user_id, created_at, last_seen_at, expires_at, user_agent, revoked_at"


def key_hash(key):
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def describe_user_agent(header):
    """A short, privacy-friendly label: "Firefox on Windows"."""
    text = header or ""
    if "Edg/" in text:
        browser = "Edge"
    elif "Firefox/" in text:
        browser = "Firefox"
    elif "Chrome/" in text or "Chromium/" in text or "CriOS/" in text:
        browser = "Chrome"
    elif "Safari/" in text:
        browser = "Safari"
    elif text.startswith("curl/") or "python" in text.lower():
        browser = "Script"
    else:
        browser = "Unknown browser"
    if "iPhone" in text or "iPad" in text:
        system = "iOS"
    elif "Android" in text:
        system = "Android"
    elif "Windows" in text:
        system = "Windows"
    elif "Mac OS X" in text or "Macintosh" in text:
        system = "macOS"
    elif "Linux" in text or "X11" in text:
        system = "Linux"
    else:
        system = "unknown system"
    return f"{browser} on {system}"


def create_session(conn, *, user_id, now, max_age_seconds, user_agent=""):
    """Record a new signed-in browser. Returns (key, session_id); the key
    goes into the cookie and is never stored."""
    key = secrets.token_urlsafe(KEY_BYTES)
    with conn:
        conn.execute(
            "DELETE FROM user_sessions WHERE user_id = ? AND (expires_at < ? OR revoked_at < ?)",
            (user_id, now - CLEANUP_AFTER_SECONDS, now - CLEANUP_AFTER_SECONDS),
        )
        row = conn.execute(
            "INSERT INTO user_sessions (user_id, key_hash, created_at, last_seen_at, expires_at, user_agent) "
            "VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
            (user_id, key_hash(key), now, now, now + max_age_seconds, describe_user_agent(user_agent)[:100]),
        ).fetchone()
    return key, row["id"]


def find_session(conn, key):
    """The session row for `key`, or None (also for a malformed key)."""
    if not isinstance(key, str) or not 20 <= len(key) <= 100:
        return None
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM user_sessions WHERE key_hash = ?",  # noqa: S608 - constant columns
        (key_hash(key),),
    ).fetchone()
    return None if row is None else dict(row)


def problem_with(row, *, user_id, now, idle_timeout_seconds):
    """Why this session can no longer be used ("revoked", "expired", "idle",
    "wrong_user"), or None if it is fine."""
    if row is None:
        return "unknown"
    if row["user_id"] != user_id:
        return "wrong_user"
    if row["revoked_at"] is not None:
        return "revoked"
    if now >= row["expires_at"]:
        return "expired"
    if now - row["last_seen_at"] > idle_timeout_seconds:
        return "idle"
    return None


def touch(conn, row, now):
    if now - row["last_seen_at"] >= TOUCH_INTERVAL_SECONDS:
        conn.execute("UPDATE user_sessions SET last_seen_at = ? WHERE id = ?", (now, row["id"]))


def list_active(conn, *, user_id, now, idle_timeout_seconds):
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM user_sessions "  # noqa: S608 - constant columns
        "WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ? AND last_seen_at >= ? "
        "ORDER BY last_seen_at DESC, id DESC",
        (user_id, now, now - idle_timeout_seconds),
    ).fetchall()
    return [dict(row) for row in rows]


def revoke(conn, session_id, *, user_id, now):
    """Sign out one browser of THIS user. False if there is no such active
    session (another user's id gives the same False)."""
    cursor = conn.execute(
        "UPDATE user_sessions SET revoked_at = ? WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
        (now, session_id, user_id),
    )
    return cursor.rowcount == 1
