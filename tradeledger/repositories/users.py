"""User accounts (Phase 8).

Return values
  create_user           -> new id (int)
  get_user              -> dict or None (includes password_hash: never
                           send this dict to a template or the API; use
                           public_user())
  get_user_by_email     -> dict or None
  set_password_hash     -> None (optionally ends every other session)
  bump_session_version  -> the new version (ends every existing session and
                           revokes every user_sessions row)

Errors
  ValidationError  invalid email
  DuplicateError   the email is already registered

Emails are stored lower-case and trimmed, so "Trader@Example.com " and
"trader@example.com" are the same account.
"""

import re
import time

from ..db import IntegrityError
from ..timestamps import utc_now_text
from ..validation import ValidationError
from . import DuplicateError

MAX_EMAIL_LENGTH = 254
# Deliberately simple: something@something.something, no spaces. Full RFC
# 5322 validation is not worth it; only a confirmation email proves that an
# address works, and that needs an email service this phase does not have.
_EMAIL = re.compile(r"[^@\s]{1,64}@[^@\s]+\.[^@\s.]{2,}")

_COLUMNS = (
    "id, email, password_hash, is_active, session_version, created_at, updated_at, last_login_at, "
    "totp_secret, totp_enabled_at, totp_last_step"
)


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
    """The fields that are safe to show: never the password hash or the
    two-step secret."""
    shown = {key: user[key] for key in ("id", "email", "created_at", "last_login_at")}
    shown["two_step_enabled"] = bool(user.get("totp_enabled_at"))
    return shown


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


def _revoke_sessions(conn, user_id):
    """Cross out every signed-in browser of this user (Phase 8.5 rows)."""
    conn.execute(
        "UPDATE user_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (int(time.time()), user_id),
    )


def set_password_hash(conn, user_id, password_hash, *, end_other_sessions):
    """Store a new hash. end_other_sessions=True also raises the session
    version and revokes every user_sessions row, which signs out every
    browser that was logged in (the caller may then start a new session for
    the current browser)."""
    bump = ", session_version = session_version + 1" if end_other_sessions else ""
    with conn:
        conn.execute(
            f"UPDATE users SET password_hash = ?, updated_at = ?{bump} WHERE id = ?",  # noqa: S608 - fixed fragment
            (password_hash, utc_now_text(), user_id),
        )
        if end_other_sessions:
            _revoke_sessions(conn, user_id)


def bump_session_version(conn, user_id):
    with conn:
        row = conn.execute(
            "UPDATE users SET session_version = session_version + 1, updated_at = ? WHERE id = ? "
            "RETURNING session_version",
            (utc_now_text(), user_id),
        ).fetchone()
        _revoke_sessions(conn, user_id)
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
        _revoke_sessions(conn, user_id)
