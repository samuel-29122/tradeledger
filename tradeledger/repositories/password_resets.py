"""One-time password-reset links, issued by an operator (Phase 8.5).

Plain English
-------------
TradeLedger has no email service, so it cannot email you a reset link on
its own. Instead the person who runs the server creates a link with one
command and passes it to you (in person, by phone, by a message you
trust). You open it and choose a new password yourself, so the operator
never learns or chooses your password. The link works once, for 30
minutes, and creating a new link cancels any older one.

Technical summary
-----------------
Token   "tlr_" + secrets.token_urlsafe(32): 256 random bits. The prefix lets
        the log filter recognise and hide it (observability.py).
Storage SHA-256 of the token only, like API tokens and session keys.
Use     consume() marks the link used and cancels every other open link for
        the account, in one transaction. Using it also ends every session
        and revokes every API token (web/reset.py).
Times   whole seconds since 1970 (UTC), the clock used for sessions.

Why not "security questions" or a self-service form that emails nobody:
both are weaker than they look (guessable answers; a form that cannot
deliver anything invites a fake feature). Why not reuse set-password:
with set-password the operator types the new password, so they know it.
"""

import hashlib
import secrets

PREFIX = "tlr_"

_COLUMNS = "id, user_id, created_at, expires_at, used_at"


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def looks_like_token(text):
    return isinstance(text, str) and text.startswith(PREFIX) and 40 <= len(text) <= 100 and text.isascii()


def create_link(conn, *, user_id, now, lifetime_seconds):
    """Issue a new link (cancelling older open ones). Returns the token,
    which is never stored and must be handed to the account holder."""
    token = PREFIX + secrets.token_urlsafe(32)
    with conn:
        conn.execute(
            "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (now, user_id)
        )
        conn.execute(
            "INSERT INTO password_resets (user_id, token_hash, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (user_id, token_hash(token), now, now + lifetime_seconds),
        )
    return token


def find_valid(conn, token, *, now):
    """The open, unexpired link for `token`, or None (also for a malformed,
    unknown, used or expired token: the caller cannot tell these apart)."""
    if not looks_like_token(token):
        return None
    row = conn.execute(
        f"SELECT {_COLUMNS} FROM password_resets WHERE token_hash = ?",  # noqa: S608 - constant columns
        (token_hash(token),),
    ).fetchone()
    if row is None or row["used_at"] is not None or now >= row["expires_at"]:
        return None
    return dict(row)


def consume(conn, link_id, *, user_id, now):
    """Mark this link used and cancel any other open link of the account.
    False if it had already been used (for example by a second tab)."""
    cursor = conn.execute(
        "UPDATE password_resets SET used_at = ? WHERE id = ? AND used_at IS NULL", (now, link_id)
    )
    if cursor.rowcount != 1:
        return False
    conn.execute(
        "UPDATE password_resets SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (now, user_id)
    )
    return True
