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
