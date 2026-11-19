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
