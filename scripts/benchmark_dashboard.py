"""Time the dashboard, the API and authentication on large synthetic journals.

    python scripts/benchmark_dashboard.py              # 100, 1,000 and 10,000 trades (SQLite)
    python scripts/benchmark_dashboard.py 5000         # a custom size
    python scripts/benchmark_dashboard.py --profile    # also show where the time goes
    python scripts/benchmark_dashboard.py --database-url postgresql://user:pw@host/postgres
        # PostgreSQL: a throwaway database is created (and dropped) per size
        # (add --pg-bridge where psycopg is not installed: test-only libpq bridge,
        #  whose timings include its own ctypes overhead and are NOT psycopg's)

Creates a throwaway database (never your real one) holding TWO accounts:
the measured one with N synthetic closed trades spread over several
instruments, strategies, sessions and two years, and a second account with
another N trades, so every query really has to filter by owner (Phase 8).
Then it measures, as the median of 5 runs:

  sql        the database query alone (list_trades with realised dates)
  report     build_report(): SQL query + Decimal calculations
  dashboard  build_dashboard() + chart_payload(): the above plus chart prep
  page       a full GET /analytics through Flask, including the template
  filtered   GET /analytics with strategy + session + date filters
  memory     peak Python memory allocated while rendering one page (tracemalloc)
  size       the HTML page, and the chart data (JSON) inside it
  api_list   GET /api/v1/trades?per_page=100 with a Bearer token
  api_sum    GET /api/v1/analytics/summary with a Bearer token
and once per run, independent of N:
  argon2     hashing and verifying one password with the production settings
  token      looking up and checking one API token
  session    loading the signed-in user for one request

Numbers depend on the machine; the script prints the Python version and
platform so results can be compared honestly. They are not guarantees.
"""

import cProfile
import json
import os
import platform
import pstats
import re
import statistics
import sys
import tempfile
import time
import tracemalloc
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradeledger import create_app
from tradeledger.db import connect, get_db, init_schema, register_postgres_driver
from tradeledger.passwords import OWASP_MINIMUM, hash_password, verify_password
from tradeledger.performance import build_report
from tradeledger.performance.dashboard import build_dashboard, chart_payload
from tradeledger.report_filters import ReportFilters
from tradeledger.repositories import api_tokens, sessions
from tradeledger.repositories.trades import list_trades
from tradeledger.repositories.users import create_user, get_user

RUNS = 5
STRATEGIES = ("Breakout", "Pullback", "Reversal", None)
SESSIONS = ("ASIA", "LONDON", "NEW_YORK", "OTHER", None)
EXITS = ("102", "99", "100.5", "99", "101.25", "100")  # +2, -1, +0.5, -1, +1.25, 0 R
FAST_HASH = "$argon2id$"  # accounts are created with a placeholder-format hash; nobody signs in


def populate(conn, count, user_id):
    with conn:
        instruments = []
        for n, currency in enumerate(("USD", "USD", "GBP", "USD", "EUR")):
            row = conn.execute(
                "INSERT INTO instruments (user_id, symbol, name, asset_class, price_increment,"
                " contract_multiplier, quote_currency) VALUES (?, ?, ?, 'OTHER', ?, ?, ?) RETURNING id",
                (user_id, f"BENCH{n}", f"Benchmark {n}", conn.numeric("0.01"), conn.numeric("1"), currency),
            ).fetchone()
            instruments.append(row["id"])
        hundred, ninety_nine, one, zero = (conn.numeric(v) for v in ("100", "99", "1", "0"))
        for n in range(count):
            day = n * 730 // count  # spread over two years
            month, dom = 1 + (day // 28) % 12, 1 + day % 28
            year = 2025 + day // 336
            conn.execute(
                "INSERT INTO trades (user_id, instrument_id, trade_date, closed_at, direction, entry_price,"
                " stop_loss, exit_price, position_size, fees, status, strategy, session)"
                " VALUES (?, ?, ?, ?, 'LONG', ?, ?, ?, ?, ?, 'CLOSED', ?, ?)",
                (
                    user_id,
                    instruments[n % len(instruments)],
                    f"{year}-{month:02d}-{dom:02d}T08:{n % 60:02d}:00Z",
                    f"{year}-{month:02d}-{dom:02d}T{9 + n % 12:02d}:{n % 60:02d}:00Z",
                    hundred,
                    ninety_nine,
                    conn.numeric(EXITS[n % len(EXITS)]),
                    one,
                    zero,
                    STRATEGIES[n % len(STRATEGIES)],
                    SESSIONS[n % len(SESSIONS)],
                ),
            )


def median_ms(action):
    times = []
    for _ in range(RUNS):
        start = time.perf_counter()
        action()
        times.append((time.perf_counter() - start) * 1000)
    return statistics.median(times)


def peak_memory_mb(action):
    tracemalloc.start()
    action()
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return peak / (1024 * 1024)


def chart_data_kb(html):
    match = re.search(r'id="dashboard-data">(.*?)</script>', html, re.S)
    return len(match.group(1).encode()) / 1024 if match else 0.0


def profile(action, top=12):
    """Print where `action` spends its time, sorted two ways.

    cumulative: time in a function INCLUDING everything it calls; shows
                which high-level step is slow (e.g. build_dashboard).
    tottime:    time spent INSIDE the function itself; shows the actual
                hot spot. In Phase 7 this view revealed datetime.strptime
                being called ~50,000 times at 10,000 trades.
    """
    profiler = cProfile.Profile()
    profiler.runcall(action)
    for order in ("cumulative", "tottime"):
        print(f"\n--- sorted by {order} ---")
        pstats.Stats(profiler).sort_stats(order).print_stats(top)


class Target:
    """A throwaway database: a SQLite file, or a new PostgreSQL database."""

    def __init__(self, server_url, label):
        self.server_url, self.label = server_url, label
        self.folder = tempfile.TemporaryDirectory()

    def __enter__(self):
        if not self.server_url:
            return os.path.join(self.folder.name, "bench.db")
        from urllib.parse import urlsplit, urlunsplit

        self.name = f"tl_bench_{self.label}_{os.getpid()}"
        self.admin = connect(self.server_url)
        self.admin.execute(f"DROP DATABASE IF EXISTS {self.name}")
        self.admin.execute(f"CREATE DATABASE {self.name}")
        parts = urlsplit(self.server_url)
        return urlunsplit((parts.scheme, parts.netloc, f"/{self.name}", parts.query, ""))

    def __exit__(self, *exc):
        if self.server_url:
            self.admin.execute(f"DROP DATABASE IF EXISTS {self.name}")
            self.admin.close()
        self.folder.cleanup()


def benchmark(count, server_url=None, show_profile=False):
    with Target(server_url, count) as target:
        app = create_app("testing", overrides={"DATABASE": target})
        with app.app_context():
            conn = get_db()
            init_schema(conn)
            me = create_user(conn, "bench@example.com", FAST_HASH + "v=19$m=1,t=1,p=1$AAAA$AAAA")
            other = create_user(conn, "other@example.com", FAST_HASH + "v=19$m=1,t=1,p=1$AAAA$AAAA")
            populate(conn, count, me)
            populate(conn, count, other)  # the second account: queries must filter by owner
            if build_report(conn, user_id=me).overall.trades != count:
                raise SystemExit(f"setup failed: expected {count} closed trades")
            token, _ = api_tokens.create_token(conn, user_id=me, name="bench", lifetime_days=1, max_active=10)
            sql_ms = median_ms(lambda: list_trades(conn, user_id=me, date_field="realised"))
            report_ms = median_ms(lambda: build_report(conn, user_id=me))
            dashboard_ms = median_ms(
                lambda: chart_payload(build_dashboard(conn, ReportFilters(), user_id=me))
            )
            token_ms = median_ms(lambda: api_tokens.authenticate(conn, token))
            now = int(time.time())
            # Phase 8.5: a signed-in request reads the user AND its session row.
            key, _ = sessions.create_session(
                conn,
                user_id=me,
                now=now,
                max_age_seconds=app.config["SESSION_MAX_AGE_SECONDS"],
                user_agent="bench",
            )
            session_ms = median_ms(lambda: (get_user(conn, me), sessions.find_session(conn, key)))
        client = app.test_client()
        with client.session_transaction() as session:
            session.update({"sid": key, "uid": me, "sv": 1, "auth_at": now, "seen_at": now})
        html = client.get("/analytics").get_data(as_text=True)
        if "dashboard-data" not in html:
            raise SystemExit("the dashboard did not render")
        page_ms = median_ms(lambda: client.get("/analytics"))
        filtered_ms = median_ms(
            lambda: client.get("/analytics?strategy=Breakout&session=LONDON&start=2025-06-01&end=2026-06-30")
        )
        memory = peak_memory_mb(lambda: client.get("/analytics"))
        bearer = {"Authorization": f"Bearer {token}"}
        api_list_ms = median_ms(lambda: client.get("/api/v1/trades?per_page=100", headers=bearer))
        api_summary_ms = median_ms(lambda: client.get("/api/v1/analytics/summary", headers=bearer))
        if show_profile:
            print(f"\n--- where GET /analytics spends its time ({count:,} trades) ---")
            profile(lambda: client.get("/analytics"))
        app.extensions["tradeledger_db"].close()
    result = {
        "trades": count,
        "sql_ms": sql_ms,
        "report_ms": report_ms,
        "dashboard_ms": dashboard_ms,
        "page_ms": page_ms,
        "filtered_ms": filtered_ms,
        "api_list_ms": api_list_ms,
        "api_summary_ms": api_summary_ms,
        "token_check_ms": token_ms,
        "session_load_ms": session_ms,
        "peak_mb": memory,
        "page_kb": len(html.encode()) / 1024,
        "chart_kb": chart_data_kb(html),
    }
    print(
        f"{count:>7,} trades | sql {sql_ms:7.1f} | report {report_ms:7.1f} | dashboard {dashboard_ms:7.1f} | "
        f"page {page_ms:7.1f} | filtered {filtered_ms:6.1f} | api list {api_list_ms:6.1f} | "
        f"api summary {api_summary_ms:7.1f} ms | peak {memory:5.1f} MB | page {result['page_kb']:6,.0f} KB"
    )
    return result


def argon2_ms():
    stored = hash_password("benchmark password", OWASP_MINIMUM)
    return (
        median_ms(lambda: hash_password("benchmark password", OWASP_MINIMUM)),
        median_ms(lambda: verify_password("benchmark password", stored)),
    )


if __name__ == "__main__":
    argv = sys.argv[1:]
    server = argv[argv.index("--database-url") + 1] if "--database-url" in argv else None
    if "--pg-bridge" in argv:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
        import pg_libpq_bridge

        register_postgres_driver(pg_libpq_bridge)
    sizes = [int(a) for a in argv if a.isdigit()] or [100, 1000, 10000]
    engine = (
        "PostgreSQL" + (" (test-only libpq bridge)" if "--pg-bridge" in argv else "") if server else "SQLite"
    )
    print(f"Python {platform.python_version()} on {platform.platform()}; {engine}; median of {RUNS} runs")
    hash_ms, verify_ms = argon2_ms()
    print(f"Argon2id (m=19456 KiB, t=2, p=1): hash {hash_ms:.1f} ms, verify {verify_ms:.1f} ms")
    results = [benchmark(size, server, "--profile" in argv) for size in sizes]
    print(
        json.dumps(
            {"engine": engine, "argon2_hash_ms": hash_ms, "argon2_verify_ms": verify_ms, "runs": results}
        )
    )
