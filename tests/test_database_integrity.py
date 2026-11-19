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
