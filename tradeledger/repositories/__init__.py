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
