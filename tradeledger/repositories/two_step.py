"""Two-step verification state in the database (Phase 8.5).

The cryptography lives in tradeledger/totp.py; this module only stores and
updates: the encrypted secret on the users row, the last accepted time
step, and the hashed single-use recovery codes. Every function takes the
user id explicitly; there is no "all users" mode.
"""

from ..timestamps import utc_now_text
from ..totp import recovery_hash


def enable(conn, *, user_id, encrypted_secret, accepted_step, recovery_codes):
    """Switch two-step on with a confirmed secret and a fresh set of codes."""
    with conn:
        conn.execute(
            "UPDATE users SET totp_secret = ?, totp_enabled_at = ?, totp_last_step = ?, updated_at = ? WHERE id = ?",
            (encrypted_secret, utc_now_text(), accepted_step, utc_now_text(), user_id),
        )
        _store_codes(conn, user_id, recovery_codes)


def disable(conn, *, user_id):
    with conn:
        conn.execute(
            "UPDATE users SET totp_secret = NULL, totp_enabled_at = NULL, totp_last_step = NULL, updated_at = ? "
            "WHERE id = ?",
            (utc_now_text(), user_id),
        )
        conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))


def accept_step(conn, *, user_id, step):
    """Record that the code for `step` was used. Atomic: if two requests
    race with the same code, only one of them gets True."""
    cursor = conn.execute(
        "UPDATE users SET totp_last_step = ? WHERE id = ? AND totp_enabled_at IS NOT NULL "
        "AND (totp_last_step IS NULL OR totp_last_step < ?)",
        (step, user_id, step),
    )
    return cursor.rowcount == 1


def _store_codes(conn, user_id, codes):
    conn.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
    for code in codes:
        conn.execute(
            "INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)", (user_id, recovery_hash(code))
        )


def replace_recovery_codes(conn, *, user_id, codes):
    with conn:
        _store_codes(conn, user_id, codes)


def use_recovery_code(conn, *, user_id, code, now):
    """Spend one recovery code. True only for an unused code of THIS user."""
    cursor = conn.execute(
        "UPDATE recovery_codes SET used_at = ? WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
        (now, user_id, recovery_hash(code)),
    )
    return cursor.rowcount == 1


def unused_recovery_codes(conn, *, user_id):
    return conn.execute(
        "SELECT COUNT(*) FROM recovery_codes WHERE user_id = ? AND used_at IS NULL", (user_id,)
    ).fetchone()[0]
